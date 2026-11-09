# 第 9 章：正确推进 PipeReader 与 PipeWriter

TCP 提供有序字节流，不提供应用消息边界。设备发出两条 32 字节的 KG/1 测量，服务端可能读到 5 字节、27 字节、64 字节，也可能一次读到一条半。Pipelines 把“从网络取得更多字节”和“从已有字节解析消息”分开，使代码不必反复移动数组中的残留数据。但内存复用和读写调度需要调用方准确报告进度。

本章先建立读写契约，再讨论结束、取消和异常。只要能回答每次读取后保留哪一段、为何等待更多数据、谁负责释放，就能避免大多数卡死、漏帧与内存损坏问题。

## 9.1 Pipe 与 IDuplexPipe 的方向不同

`new Pipe()` 创建一对 reader/writer，写入其 Writer 的字节可以由同一个 Pipe 的 Reader 取得。`IDuplexPipe` 不是 Pipe 的子类，而是只约定两个属性的接口：`PipeReader Input { get; }` 和 `PipeWriter Output { get; }`。它表示全双工连接的一侧，Input 接收对端的字节，Output 发向对端；Output 的内容不会自动从本机 Input 出来。Kestrel 的 `ConnectionContext.Transport` 就是这种连接抽象。

理解双向关系时，可以在纸上画两条单向通路：A.Output → B.Input，以及 B.Output → A.Input。自建内存实验可用两条 Pipe 交叉连接来实现这两条通路；但这是一个概念模型和可选实现，IDuplexPipe 接口并不保证底层一定创建两个 `new Pipe()`。真实连接还可以由 Socket、Stream 适配或其他传输实现提供这两个端点。

仓库 [DelegatingDuplexPipe](../../../KestrelFramework/System.IO.Pipelines/DelegatingDuplexPipe.cs)把 IDuplexPipe 包装为 Stream，再用装饰 Stream 创建新的 PipeReader/PipeWriter。它说明了 Stream 和 Pipeline 可以互相适配，但适配层会增加自己的缓冲、复制和生命周期规则。构造中的 `leaveOpen: true` 表示结束适配 reader/writer 时保留底层流，外层仍须明确由谁关闭真实连接，不能把“Complete 适配层”与“物理连接已关闭”混同。

## 9.2 ReadResult 是一次借用，不是永久快照

`await reader.ReadAsync(token)` 返回 ReadResult，Buffer 是 `ReadOnlySequence<byte>`。Sequence 可能包含多个不连续的 Memory 段；不要只处理 FirstSpan，也不要认为一个段就是一条消息。一次成功取得 ReadResult 后，必须且只应调用一次 AdvanceTo，才能开始下一次读取。若 ReadAsync 因 token 取消或 I/O 异常而没有返回结果，就没有这次结果需要推进。

`AdvanceTo(consumed, examined)` 的两个位置来自当前 Buffer：

- consumed 表示之前的字节已不再需要，管道可以复用相关存储。
- examined 表示这次解析已经检查到哪里，管道据此判断下一次 ReadAsync 是否应等待新数据。

这两个位置不是两个独立的任意偏移，必须处于当前序列的合法顺序，consumed 不得超过 examined。SequencePosition 是不透明位置，不能拿来自另一个序列的位置混用，也不能当整数相加；用 Slice、GetPosition 或 SequenceReader.Position 取得。

最常见的三种动作可以这样理解：整批全部处理完，`AdvanceTo(end, end)`；发现末尾只有半帧，`AdvanceTo(半帧起点, end)`，保留它但等待新数据；只处理第一帧就交还控制，`AdvanceTo(第一帧末尾, 第一帧末尾)`，让剩余已有数据尽快再次返回。单参数 `AdvanceTo(position)` 等价于把 consumed 与 examined 都设为 position，不是“自动检查到末尾”。

反例一：半帧时调用 `AdvanceTo(start, start)`。没有消费，也没有告诉管道已经看过全部现有数据，下次可能立即返回同一半帧，形成高 CPU 空转。反例二：只处理第一帧却调用 `AdvanceTo(第一帧末尾, buffer.End)`。后面明明还有完整帧，却声称已检查到末尾且需要更多数据，可能等到下一次网络输入才继续。两个错误都不是调大缓冲区可以解决的。

## 9.3 一次 Read 配一次 Advance 的循环

以下局部模式展示“尽量解析当前全部帧”的进度管理。TryReadTelemetry 是同步解析器：不足时返回 false 且不改变 remaining，非法时抛 InvalidDataException，成功后从 remaining 切掉一帧。ProcessValueAsync 只接收独立数值，不保留借用的 Sequence。这不是包含连接管理的完整服务器。

```csharp
while (true)
{
    ReadResult result = await input.ReadAsync(token);
    ReadOnlySequence<byte> remaining = result.Buffer;
    try
    {
        if (result.IsCanceled)
            break; // 本循环把 CancelPendingRead 定义为结束请求。

        while (FrameCodec.TryReadTelemetry(ref remaining, out var value))
            await ProcessValueAsync(value, token);

        if (result.IsCompleted)
        {
            if (!remaining.IsEmpty)
                throw new InvalidDataException("连接结束于半帧。");
            break;
        }
    }
    finally
    {
        input.AdvanceTo(remaining.Start, remaining.End);
    }
}
```

finally 使正常、取消结果和解析异常路径都结束当前借用。随后由拥有整个读取生命周期的上层完成 reader；不要在只读取一条消息的工具函数里随意 Complete，否则调用方无法继续读下一条。若上层需要把失败传播到自建 Pipe 的另一端，可以在 CompleteAsync 时传入异常；框架连接则要遵守连接处理器的资源约定，统一安排结束责任。

这个模式会在等待 ProcessValueAsync 时保留本次剩余管道缓冲，因而“类型独立”不等于“已经及时释放管道内存”。需要降低保留量时，可以采用一次只取一帧并立即 Advance 的模式，但 examined 必须停在那一帧末尾；同时每次 Read 后只 Advance 一次。不要在内层 while 每处理一帧就对同一个 ReadResult 多次 AdvanceTo。

## 9.4 IsCompleted 不等于 Buffer 为空

ReadResult.IsCompleted 表示不会再有后续输入。最后一次结果仍可能包含多帧，必须先解析全部完整帧，再判断余下是否为空。若只剩半帧，协议应报告截断，而不是一直等待永远不会到来的字节。若最后一批包含两条完整测量，只处理一条后看 IsCompleted 就 break，会丢掉第二条。

这正是仓库旧示例需要明确修正的地方。[EchoConnectionHandler](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs)和 [TelnetConnectionHandler](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs)每轮只解析一个请求，随后直接因 IsCompleted 结束，存在 EOF 批次内多条消息未处理的情况。它们有教学价值，但不能被复制为完整工业读取循环。`while (!ConnectionClosed.IsCancellationRequested)` 也不能替代处理最终读取结果：连接取消和应用读取缓冲中的有效字节是不同事实，退出策略必须按协议和服务关闭策略定义。

[RedisConnectionHandler](../../../KestrelApp.Middleware/Redis/RedisConnectionHandler.cs)会尝试从当前缓冲解析多条；有请求时使用单参数 AdvanceTo(consumed)，如果尾部留有半包，下一轮通常会先立即返回该残留，再走无请求分支把 examined 标到末尾，增加一次无收益唤醒。它还没有显式将 EOF 残留判定为截断。改写时应让解析器返回清楚的成功、不足、非法状态，再由读取循环统一更新进度。

## 9.5 取消结果与取消异常是两条路径

传给 ReadAsync 的 CancellationToken 在等待期间取消，通常使 await 抛 OperationCanceledException。调用 `CancelPendingRead()` 则会让当前或下一次 ReadAsync 返回 `IsCanceled == true`，通常不抛异常；它可以用来唤醒循环，让应用重新检查自己的停止条件。它不是对端 EOF，也不会自动意味着销毁连接。返回了取消结果，仍要结束该次读取的借用。

写端同理：CancelPendingFlush 对应 FlushResult.IsCanceled，token 取消通常走异常路径。FlushResult.IsCompleted 说明下游读取端结束，继续编码通常已无意义。应明确这些事件对应重试、停止还是关闭连接；取消 Flush 不保证已经发布的数据被撤销，不能无条件重发整条业务命令。

[DuplexPipeStream](../../../KestrelFramework/System.IO/DuplexPipeStream.cs)中的读取使用 finally 推进，并通过先 Slice 到实际复制范围来设置消费终点；非空内容优先于 IsCompleted，符合“EOF 仍可携带数据”的基本原则。它也有自定义 throwOnCancelled 及 cancelCalled 标志，因此取消表现应阅读适配实现，不能只看 Stream 接口猜测。该类是旧项目代码的研究对象，本章不宣称它覆盖所有 Stream 边界情况。

## 9.6 Writer 必须等待发布与背压

写端顺序为 GetMemory/GetSpan、填写、Advance、await FlushAsync。内存 Pipe 的 Flush 发布给 reader；Stream 适配 writer 则按 Stream 实现提交；Kestrel 输出由传输层实现处理。Flush 完成不构成网络 ACK 或 KG/1 业务 ACK，也不保证这一次调用把所有池数组归还。

同一个 PipeReader 和 PipeWriter 各自应只有一个执行上下文拥有。可以有一个任务读、另一个任务写，但不能多个任务同时往同一个 writer 编码帧。一个尚未完成的 Flush 期间不能再申请它的缓冲。也不能先等待 Flush 完成后才启动唯一能够消费数据的 reader：当达到背压阈值，Flush 等 reader 消费，而 reader 又等 Flush，便形成死锁。

## 9.7 用具体字节位置检查推进

设本次有 70 字节，其中前两帧各 32 字节，最后 6 字节是下一帧头。批量解析后 consumed 应位于偏移 64，examined 位于偏移 70。下一次读取会保留这 6 字节，再接上新到的数据；调用方不需要自己把它们移动到数组起点。若采用 ReadOneAsync，第一轮只取第一帧，则两个位置都在偏移 32；第二轮可以立即取得后面已有的 38 字节。无论哪种模式，偏移都只是教学表示，代码必须从当前 Sequence 取得 SequencePosition。

## 9.8 动手练习与参考答案

需要可运行的控制台入口时，先按[第20章的代码练习工作区](../part-4/20-exercises.md)创建带Microsoft.AspNetCore.App框架引用的NetworkScratch；那里有本实验的完整程序和命令，普通控制台项目不能假定已经引用Pipelines。先做一次自建 Pipe 观察：运行一个 producer，只写入字节 1、2 并 Flush；consumer 第一次读取后调用 `AdvanceTo(buffer.Start, buffer.End)`，再启动第二次 ReadAsync。参考现象是第二次等待，直到 producer 再写字节 3 并 Flush，此时 consumer 仍能读到 1、2、3，因为前两字节从未标为 consumed。实验必须给等待加取消超时，结束时完成两端；不要通过无限挂起判断程序是否正确。这直接展示“已经检查”和“已经消费”是两种进度。

练习一：缓冲为完整 A、完整 B、半个 C，循环已处理 A 和 B，应如何 Advance？参考答案：consumed 为 C 起点，examined 为当前末尾；保留 C 并等待后续输入。

练习二：只要读一条 A 就返回给调用方，后面仍有 B，examined 能否设为末尾？参考答案：应设为 A 末尾，保证下一次读取可及时拿到 B；没有解析 B，就不能把它视为需要等待更多输入的残留。

练习三：IsCompleted 为真且剩余 7 字节，解析器报告不足，下一步是什么？参考答案：这是截断帧，应终止并记录协议错误，完成相应资源；反复 Read 不能让已经结束的流补齐。

练习四：ReadAsync(token) 抛取消异常后是否需要对一个旧 buffer 再 Advance？参考答案：不需要，旧结果应已推进；本次未取得新 ReadResult。必须区分“await 未返回结果”和“返回 IsCanceled 结果”，避免重复 Advance。

完整契约、常见错误和取消方式见 [Microsoft Pipelines 使用指南](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)。
