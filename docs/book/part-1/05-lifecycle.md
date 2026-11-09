# 第 5 章 生命周期：读完最后一帧，也释放最后一个资源

能返回一次正确响应，只说明主路径跑通。设备断线、只发半帧、队列满、宿主停机时，服务器还要结束等待、释放资源并保留可解释的结果。本章以“谁拥有、何时结束、结束前还剩什么”为线索，讲清连接与内存的生命周期。读完后，你应能审查一个读取循环的退出条件，而不只看它有没有 `while`。

## 5.1 为每种对象找到主人

Kestrel 接受的 `ConnectionContext` 由 Kestrel 管理整体生命周期。处理者获得连接的使用权，并在终端完成自己使用的读写端；不要因此认为应该随手 `await using` 或 `DisposeAsync` 整个传入 context。应用自行创建的上游连接、作用域、包装器和租赁数组，则要由应用明确安排释放。

| 对象 | 通常谁创建 | 本书采用的责任边界 |
| --- | --- | --- |
| 传入 `ConnectionContext` | Kestrel | Kestrel 最终释放；处理者不主动 Dispose context |
| 终端读取/写入端 | Kestrel 传输提供 | 终端处理完毕后完成其应用使用，收尾不再读写 |
| `ReadResult.Buffer` | Pipe 提供 | 借用；`AdvanceTo` 后不再引用 |
| 每连接局部状态 | 处理者调用 | 对应连接处理结束后释放 |
| 显式 DI scope | 应用 | 在预定连接或消息工作完成后释放 |
| 上游代理连接 | 应用调用工厂 | 应用停止相关任务并最终释放 |

原始 [Redis 处理者](../../../KestrelApp.Middleware/Redis/RedisConnectionHandler.cs) 在 finally 调用了传入 context 的 Dispose。阅读时要识别这属于旧例的实现，不能照抄为 Kestrel handler 的通用所有权规则。新 [网关处理者](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs) 则明确完成 input/output，将 context 的释放交回 Kestrel。

“完成 Pipe 使用”和“销毁底层连接对象”也要分开。外层中间件若还要恢复 Transport，应等终端返回后收尾；终端不能随意完成自己并不拥有的外层资源。通用 Pipe 示例里创建者自行完成两端是合理的，移植到框架宿主时则必须重审责任。

## 5.2 关闭通知不证明输入缓冲已空

TCP 可以分别结束两个方向。设备发完所有数据后执行 `shutdown(SHUT_WR)`，表示它不再发送，但仍希望接收 Ack。这种客户端行为与立即销毁整个 socket 不同。服务器此时应如何响应，必须结合所用传输实现和协议契约判断。

在 [.NET 10 SocketConnection](https://github.com/dotnet/aspnetcore/blob/v10.0.0/src/Servers/Kestrel/Transport.Sockets/src/Internal/SocketConnection.cs) 的接收循环中，读到零字节视为 FIN；finally 先完成输入生产端，再触发 `ConnectionClosed`。此前写入 Pipe 的数据仍可能等待应用消费。因此 `ConnectionClosed.IsCancellationRequested` 不等于“应用已经读完所有输入”。

原始 [Echo](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs) 与 [Telnet](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs) 用 `while (!context.ConnectionClosed.IsCancellationRequested)` 包围读取循环。它们适合展示基本结构，但若关闭通知先于应用排空缓冲，循环可能漏掉末尾数据。把这个 token 直接链接进所有读、入队与刷新操作，也可能把仍可处理的最后一帧提前取消。

新网关的正常结束以 Pipe 的 EOF 为准：先尝试解析当前缓冲；解析出一帧就返回自有值，下次继续；没有完整帧且 `IsCompleted` 为真时，空缓冲表示干净结束，非空缓冲表示截断帧。宿主停止负责主动取消，帧预算负责约束等待，传输错误则走错误路径。它支持的具体半关闭场景是“客户端发完后关闭发送方向，仍读取缓冲完整帧对应的 Ack”；不能由此泛化为 Kestrel 支持任意服务端半关闭策略。

## 5.3 EOF 与最后一批数据可以同时出现

`ReadResult.IsCompleted` 为真，表示生产端不会再提供新数据，不表示这次 `Buffer` 为空。正确顺序是先处理完整数据，再判断剩余与 EOF。若收到 A、B 两条完整帧和半条 C，并同时 EOF，则 A、B 仍可处理，C 必须记为截断，不能悄悄当成功结束。

观察新 [ReadOneAsync](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs)：每次只返回一帧；成功时将 consumed 与 examined 都设到这帧后方。这样若同一批缓冲还含下一帧，下次读取可以立即获得它。数据不足时 consumed 保持起点，examined 到末尾，表示“这些我看过了，等待更多数据”。

这两种 Advance 策略对应不同事实。只处理一帧却声称已经 examined 到整批末尾，可能令下一帧等待不必要的新数据；完全不足时连 examined 都不前进，则可能反复立即读取同一片段，形成忙循环。每次成功获得 `ReadResult`，都应通过 finally 恰好调用一次 `AdvanceTo`，包括解析抛错和取消结果路径。

还要保持解析原子性：KG/1 的同步 [TryReadTelemetry](../../../samples/IndustrialGateway/Protocol.cs) 在不完整时返回 false，在非法时抛异常，只有完整且有效时才提交输入切片。它内部先用局部 reader，避免“头已吃掉、正文没到”后失去解析起点。同步解析和异步等待分开，也避免让栈上 span、reader 或借用内存跨过 await。

## 5.4 内存活多久，业务就只能借多久

Pipe 的 buffer 是借来的，释放依据是 consumed。不能先保存 `payload = buffer.Slice(...)` 到后台任务，再调用 AdvanceTo 消费该区域。即使测试时字符串看起来没变，池内存的下一次复用仍可能改写它，错误往往随负载才出现。

工业网关把四个数值解析为不可变的 `Telemetry` 值，入队的对象不引用 Pipe 内存。这样读取循环可以及时 Advance，后台消费者可以独立工作。对于变长原始负载，选择是复制到自有内存，或把明确的内存所有者交给消费者并在消费结束后释放；后者能减少复制，但会增加故障与取消路径的复杂度。

原始 Echo 解析器租出 `IArrayOwner<byte>`，复制正文，并在回显处理完成后通过 using 归还。这说明“使用池”仍需要清晰释放点。池不会自动防止越界、不合法长度或释放后使用。应先校验外部长度，再租借，使用实际长度，而非租出数组的物理容量。

## 5.5 作用域必须显式创建

HTTP 通常给请求提供作用域，但原始 TCP `ConnectionHandler` 不能据此假定存在相同的每连接 scope。需要 scoped 服务时，注入 `IServiceScopeFactory`，然后明确创建。例如下面是生命周期示意，并非新网关的真实数据库实现：

```csharp
public override async Task OnConnectedAsync(ConnectionContext connection)
{
    await using var scope = scopeFactory.CreateAsyncScope();
    var session = scope.ServiceProvider.GetRequiredService<DeviceSession>();
    await ProcessConnectionAsync(connection, session);
}
```

连接作用域意味着所有消息共用一次作用域，适合确实需要连接会话的对象；数据库工作单元可能更适合每消息一个短作用域。若把数据库上下文跟随长连接存活数天，可能持有大量追踪状态。决定依据是业务事务和资源寿命，不是“每连接一个对象听起来很自然”。

后台队列也不能保存一个即将释放作用域内的服务对象，等待消费者以后调用。应入队独立数据，再由消费者创建自己的工作作用域。否则生产者方法退出后，消费者可能拿到已释放的对象。异步并不改变所有权规则，只让错误更难在时间上观察。

## 5.6 超时要限制整项工作，而非奖励慢速输入

设备只发一个字节然后停住，会占据连接和缓冲。每读到一个字节就重新计时的空闲超时，允许对端持续少量发送而长期占用资源。新网关在准备读取一帧时创建默认 5 秒绝对预算，同一预算覆盖读完整帧、等待有界队列和刷新 Ack；读到新字节不会重新给满 5 秒。

[TelemetryQueue](../../../samples/IndustrialGateway/TelemetryQueue.cs) 默认容量 128，满时采用 Wait。连接上限默认 64，限制同时进入设备处理逻辑的调用。队列满后，入队会等待，Ack 也随之延后，这称为反压；预算到期则终止等待并关闭连接。没有 Ack 的设备不能据此断言数据绝未入队，因为超时也可能发生在入队成功后的 Ack 刷新阶段。

这些值共同提供实验的资源边界，但并非精确内存上限。传输缓冲、socket 缓冲、每连接状态、队列元素和运行时本身都要占用内存。HTTP 请求体大小和 HTTP 超时也不能替代原始 TCP 的帧与传输限制。源码把读写传输水位设为 16 KiB，是为反压留出完整帧空间，水位不等于整个进程的内存配额。

## 5.7 停机和多任务要一起收尾

宿主停止时应使读取、队列等待和输出刷新结束，释放已占用连接名额，让异常被观察到。新网关用宿主停止 token 与帧预算取消；finally 分别完成两端，并在嵌套 finally 中保证名额归还。释放顺序比“每个地方都写 catch”更重要。

代理场景还有两个方向的复制任务。原始 [XOR 代理](../../../KestrelApp.Middleware/TelnetProxy/XorTelnetProxyHandler.cs) 用 `Task.WhenAny` 等到一侧结束后返回，展示双向转发概念，但没有完整安排另一侧停止、等待和上游释放。工业实现不能把 WhenAny 当作两个任务都结束；应制定退出策略，取消或关闭以唤醒另一侧，await 两侧观察结果，再释放自建上游连接。

这也解释了“关闭”为什么没有一行通用代码：正常 EOF、协议错误、传输错误、预算超时、宿主停机各有不同含义。日志应记录连接 ID 与原因，敏感正文则不宜无节制输出。关闭不是为了隐藏异常，而是让每条路径最终回到可控状态。

## 5.8 实验、答案与交付标准

阅读 [集成验收脚本](../../../samples/IndustrialGateway/verify.py)，找到先合并发送多帧、再 `shutdown(SHUT_WR)`、最后逐个读取 Ack 的分支。它检验的是 EOF 时缓冲末尾仍得到处理。另一个分支少发最后一个字节后半关闭，应观察关闭而不是 Ack；慢速逐字节分支检验绝对预算。

使用新终端从仓库根进入 `samples`，再执行下面的确定性检查与网络验收。SDK 配置按当前工作目录向上查找，`--project` 不会改变查找起点；进入该目录才能使用 [SDK 配置](../../../samples/global.json) 选择 .NET 10 正式版，详见 [第 2 章的环境检查](02-repository.md#25-一次可复现的环境检查)。

```bash
# 当前起点为仓库根；cd 后下列相对路径均以 samples 为起点
cd samples
dotnet run --project IndustrialGateway.Checks/IndustrialGateway.Checks.csproj
python3 IndustrialGateway/verify.py
```

[Checks 项目](../../../samples/IndustrialGateway.Checks/IndustrialGateway.Checks.csproj) 验证可控的多段解析；Python 验收脚本自行构建并用临时端口启动、停止隔离进程，不需要预先启动 7810 服务。脚本内部也显式以 `samples` 为构建工作目录。若在另一个仍位于仓库根的终端单独运行 Python，路径应为 `python3 samples/IndustrialGateway/verify.py`。结果应以本机实际 PASS 或异常为准；TCP 分次发送不能保证每次对应一个网络包。

**练习一：EOF 时 buffer 还有 64 字节，能立即退出吗？** 参考答案：不能，应先按协议取完整帧；字节数本身也不能证明内容合法。

**练习二：队列已写入而 Ack 超时，设备重发会自动去重吗？** 参考答案：不会。实验没有持久去重承诺，真实系统要另行定义幂等与恢复策略。

**练习三：返回一条消息前为何先把数值变成自有值？** 参考答案：返回前 finally 会 Advance，后续异步代码不能再依赖借用缓冲；标量值使内存寿命与业务处理解耦。

**本章交付标准：** 能写出所有权表；能区分关闭通知、Pipe EOF 和缓冲排空；能说明每个 await 在停机和超时下如何结束；能解释有界队列与 Ack 的关系，并指出实验没有落库和完整去重能力。下一章通过 [Echo 与行协议](06-echo-telnet.md) 把这些检查落到两段短小的原始实现。
