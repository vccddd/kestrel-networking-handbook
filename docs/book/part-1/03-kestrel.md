# 第 3 章 Kestrel 如何把连接交给你的协议

Kestrel 常以 ASP.NET Core 的 Web 服务器出现，因此初学者容易以为“用了 Kestrel 就只能写 HTTP”。本章把服务器、传输、连接处理与 HTTP 请求拆开。读完后，你应能解释一个 TCP 连接如何到达 `OnConnectedAsync`，以及为什么 HTTP 健康检查和设备协议可以由同一进程提供。

## 3.1 服务器与协议处理不是同一件事

监听端口时，操作系统把新的连接交给服务器；服务器组织连接生命周期，并提供读写通道。接下来怎样理解读到的字节，是上层协议的工作。HTTP 解析器会找方法、路径和头；KG/1 解析器会找 magic、版本、类型和长度。两者都可以建立在 TCP 连接上，但不能拿彼此的解析器解释同一输入。

Kestrel 的连接层允许安装自定义连接处理者。仓库的 [Echo 注册扩展](../../../KestrelApp.Middleware/Echo/ListenOptionsExtensions.cs) 用 `UseConnectionHandler<EchoConnectionHandler>()` 接管 Echo 端点；[Telnet 注册扩展](../../../KestrelApp.Middleware/Telnet/ListenOptionsExtensions.cs) 做相同的事。这些扩展很短，因为重要工作发生在端点选择和处理者内部。

使用连接 API 的收益，是复用 Kestrel 的宿主集成、传输和管道能力，把精力放在协议上。它不会自动生成协议实现，也不意味着一定比任何 Socket 程序快。选择依据应包括开发复杂度、团队熟悉程度、部署约束和实测结果。

## 3.2 连接上下文是一张“当前连接工作台”

先认识异步代码里的两个词：`Task` 是一项工作完成状态的凭据，可以表达成功、失败或取消，它本身不是线程。`await` 等待尚未完成的异步操作时，会挂起当前方法，让执行线程可以处理其他工作，例如另一条设备连接；操作完成后，方法再从等待处继续，不保证仍使用原来那条线程。如果等待的操作已完成，方法也可能直接继续执行。

处理者继承 `ConnectionHandler`，实现 `Task OnConnectedAsync(ConnectionContext context)`。Kestrel 对每条进入该端点的连接调用这个方法。参数代表这一条连接；返回的 Task 应覆盖读取、响应以及启动的子工作。若内部启动两个复制任务，必须按退出策略等待它们收尾，使返回 Task 完成时，这条连接的应用工作也确实结束，而不是把子任务留在后台后立即返回。

常用成员的含义如下。

| 成员 | 作用 | 使用时要记住 |
| --- | --- | --- |
| `ConnectionId` | 为日志关联当前连接 | 不能当设备身份或持久业务主键 |
| `LocalEndPoint` / `RemoteEndPoint` | 观察本地与远端地址 | 远端地址不等于可信设备身份 |
| `Transport` | 当前层看到的双工读写通道 | 中间件可能包装并替换它 |
| `ConnectionClosed` | 连接关闭通知 | 挂起 I/O 仍需取消与收尾设计 |
| `Items` / `Features` | 当前连接附属数据与能力 | 约定 key、类型、并发访问和所有权 |
| `Abort()` | 请求中断连接 | 不保证最后响应被对方应用接收 |

`Features` 是按接口类型提供能力的集合，例如某层可以暴露 TLS 相关能力；不要假定所有连接都有所有 feature。`Items` 更像应用随连接携带的字典。两者都不是数据库，连接结束后不能继续依赖它们保存设备历史。

原始 [TelnetContext](../../../KestrelApp.Middleware/Telnet/TelnetContext.cs) 每解析出一行，就创建一个应用请求上下文，保存这行字符串、响应对象和底层连接引用。它继承的 [ApplicationContext](../../../KestrelFramework/Application/ApplicationContext.cs) 用连接的 feature 集合作为默认层。这里已经有两种生命周期：一个 TCP 连接可以产生许多行请求，每一行对应不同的业务上下文。

## 3.3 `IDuplexPipe` 的两个方向

`context.Transport.Input` 是 `PipeReader`，负责设备发来的字节；`Output` 是 `PipeWriter`，负责写回设备。双工表示两方向可以同时通信，并不表示你往 Output 写入的内容会自动出现在本机 Input。要测试回显，必须由远端读到后再发回。

读取不是“拿走一个数组”这么简单。`ReadAsync` 返回一段暂时借给应用观察的缓冲，它可能由多段内存组成。完成解析后调用 `AdvanceTo(consumed, examined)`，告诉管道哪些可以回收，哪些已经看过。写入则先填充缓冲，再 `FlushAsync` 提交给后续传输。刷新可等待，也可能因对端停止读取而受阻。

这两个方向各自应有明确的逻辑所有者。同一时刻启动多个任务竞争读取一个 `PipeReader`，无法保证哪个任务拿到哪条消息；多个任务直接往同一 `PipeWriter` 拼帧也可能交错。常见设计是单一读取循环负责分帧，单一写入路径负责响应，后台业务通过队列传递独立数据。

在工业网关中，接收循环解析出 `DeviceId`、时间和值后，应形成自有的值数据再入队。不要把借来的 `ReadOnlySequence<byte>` 放到队列后立刻 `AdvanceTo`：消费者开始工作时，内存可能已被回收重用。第 5 章会详细讨论这些所有权。

## 3.4 安装处理者发生在构建管道时

下面是说明注册结构的片段，应该结合 [新网关入口](../../../samples/IndustrialGateway/Program.cs) 阅读，不是完整可复制的程序：

```csharp
builder.WebHost.ConfigureKestrel(options =>
{
    options.Listen(IPAddress.Loopback, 7810, listen =>
    {
        listen.UseConnectionHandler<GatewayConnectionHandler>();
    });
    options.Listen(IPAddress.Loopback, 7812);
});
// 构建应用后将 /health 映射为 HTTP 路由。
```

7810 的连接以自定义处理者为终点；7812 保留 HTTP 管线，处理 `/health`。设备连接是按 IP 与端口进入 7810，不先发送 URL 路径。因此“把 `/device` 映射出来”并不能把原始 TCP 设备自动接入 HTTP 路由。

还需纠正一个关系到数据串线的误解：**每条连接调用一次 `OnConnectedAsync`，不等于每条连接创建一个处理者对象。** [.NET 10 官方实现](https://github.com/dotnet/aspnetcore/blob/v10.0.0/src/Servers/Connections.Abstractions/src/ConnectionBuilderExtensions.cs) 在 `UseConnectionHandler<T>` 中，从应用服务解析或创建 handler，随后把该对象的方法安装为终端委托。多个连接可能并发调用同一对象。

因此不能在处理者上放 `currentDeviceId`、当前输入缓冲、当前连接或认证结果等可变字段。一个设备写入字段后，另一个设备可能覆盖它。处理者可以保存日志器、线程安全的共享队列或作用域工厂；当前连接的状态应放在方法局部变量、显式连接状态对象或有明确约定的连接数据中。即使注册要求“每次解析服务时创建新对象”，也不能推导出每次连接调用都会重新解析；要看框架在哪里取得对象，而不能只看注册名称。

## 3.5 多层协议怎样共用一个应用处理者

[原始入口](../../../KestrelApp/Program.cs) 中 `UseFlowXor().UseTelnet()` 表示先用包装层改变传输视图，再让行协议处理者读取还原后的文本。TLS 上的协议也类似：外层处理握手与加解密，内层处理应用消息。应用层可以复用，因为它读到的是处理后的明文通道。

命名为 `UseHttps` 的监听扩展提供 TLS 层；在自定义连接处理链上使用它，不会让 KG/1 自动变成 HTTP。TLS、HTTP、WebSocket 和 KG/1 有不同职责，组合时必须说明内外关系。原例的 [TLS 检测注册](../../../KestrelFramework/Middleware/TlsDetection/ListenOptionsExtensions.cs) 还插入检测与恢复中间件，这是高级示例，不应与标准 TLS 端点直接等同。

本书新实验默认仅有明文 TCP 7810 和 HTTP 健康入口 7812，无 7811 监听。学完后加入 TLS，需同时配置证书、客户端信任和错误路径，重新验证连接上限与超时；不能只改端口号就宣称数据已加密。

## 3.6 实验：画出两条并行连接

先阅读 [Echo 处理者](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs)，列出字段和局部变量。字段是日志器和静态欢迎字节；`input`、`output`、当前读取结果位于方法内部。再读 [Telnet 处理者](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs)，其字段保存已构建的应用委托，而每条连接的 `TelnetResponse` 在方法中创建。

画出同一 handler 指向连接 A 和 B 的两次方法调用，再把“当前设备号”分别画进两次调用的局部状态框。假设把它移到 handler 字段：A 设置 42，等待 I/O；B 设置 99；A 恢复后读取字段。此时 A 会得到 99。这里不需要线程同时执行 CPU 指令，只需两次异步调用交错就能产生错误。

再启动新网关，分别检查 `http://127.0.0.1:7812/health`，并通过 [模拟器](../../../samples/IndustrialGateway/simulator.py) 建立设备连接。记录 HTTP 和设备日志中的连接行为。看到一个 HTTP 健康响应，不能据此声称 KG/1 已正确解码；应继续检查相同 sequence 的 Ack。

## 3.7 常见错误与参考答案

依赖注入（DI）是让容器按照注册规则创建对象、提供依赖的一种方式，常见的三种生命周期先记住以下含义：

- **singleton（单例）**：同一个服务容器通常共享一个实例；容器创建并管理的可释放单例在容器结束时释放，共享使用时仍需保证并发安全。
- **scoped（作用域）**：同一 scope 内重复解析得到同一实例，不同 scope 可得到不同实例；应用在原始 TCP 处理中显式创建 scope，并在对应连接或消息工作结束后释放它，容器随之释放其中受管理的资源。
- **transient（瞬时）**：每次向容器解析服务时创建一个实例；它不表示每次调用对象方法都重建实例，容器创建的可释放对象也要按所属作用域或容器的所有权规则收尾。

原始 TCP 连接不会因为使用 Kestrel 就自动获得一个 HTTP 请求式 scope。构建连接管道时解析一次 transient handler 后，仍可由同一对象处理后续许多连接；需要连接或消息作用域时，必须明确创建与释放的位置。

**错误：在构造函数中获取 scoped 数据库上下文并用于所有设备。** 构造发生在应用服务解析环境，未必有连接作用域。数据库对象也往往不支持并发共享。需要时应显式创建连接或消息作用域，并在对应工作完成后释放。

**错误：把 `OnConnectedAsync` 改成启动 `Task.Run` 后立即返回。** 返回意味着连接处理工作已结束，Kestrel 可以开始收尾；后台任务可能使用正在关闭的通道。应让返回的 Task 覆盖全部工作，并在内部管理并发子任务。

**练习一：同一 handler 收到两个连接，可否共享只读协议常量？** 参考答案：可以；不可变常量适合共享。可变集合或计数器要另外设计线程安全，而每连接状态应独立。

**练习二：为什么 `ConnectionId` 不能代替 `DeviceId`？** 参考答案：它标识一次连接；重连通常产生新的连接身份，一个连接也可能承载多个业务对象。设备身份还涉及认证，不能仅凭远端自报字段确认。

**练习三：7810 不支持浏览器，但 7812 支持，是不是启动了两台服务器？** 参考答案：同一个宿主可让 Kestrel 监听多个端点，每个端点使用不同的连接处理链。进程数、监听数、连接数和请求数是四个不同维度。

**本章交付标准：** 能画出“宿主→监听端点→连接中间件→终端处理者→分帧→业务”的链；能解释两条连接共享 handler 时状态仍怎样隔离；能说明 TCP、TLS 与 HTTP 的位置。随后阅读 [第 4 章：中间件](04-middleware.md)，把链中的组合顺序变为可预测的行为。

延伸阅读：[Kestrel 端点配置](https://learn.microsoft.com/zh-cn/aspnet/core/fundamentals/servers/kestrel/endpoints?view=aspnetcore-10.0) 和 [System.IO.Pipelines](https://learn.microsoft.com/zh-cn/dotnet/standard/io/pipelines)。阅读通用 Pipe 生命周期例子时，应核对自己是否拥有该 Pipe；从 Kestrel 借来的连接不等于应用自行创建的 Pipe。
