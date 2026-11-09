# 第 4 章 中间件不是魔法：把处理顺序写清楚

当协议需要日志、计量、传输变换和业务分派时，把所有功能塞进一个读取循环，很快就难以修改。本章解释仓库怎样用委托组合这些职责，并区分连接中间件与消息中间件。读完后，你应能预测执行顺序、识别短路，并解释传输包装为何必须成对安装与恢复。

## 4.1 一条连接和一条消息是两个粒度

连接中间件的参数是 `ConnectionContext`，一次调用通常覆盖连接的整个存续期。例如统计连接收发字节、包装传输、记录断开原因。应用中间件的参数则是某个业务请求上下文：Telnet 示例里是一行文本，Redis 示例里是一条命令。一个连接可以产生几千条消息，因此“调用 next 一次”要先说清是在哪一层。

仓库分别定义 [IKestrelMiddleware](../../../KestrelFramework/IKestrelMiddleware.cs) 和 [IApplicationMiddleware](../../../KestrelFramework/Application/IApplicationMiddleware.cs)。前者的核心是 `InvokeAsync(ConnectionDelegate next, ConnectionContext context)`；后者处理的是 `ApplicationDelegate<TContext>` 与 `TContext`。两者都叫中间件，是因为都能包裹后续处理；它们并不共享同一种上下文，也不会自动共享依赖注入作用域。

工业网关里，“最多允许 64 条设备连接”适合在连接入口做准入；“Telemetry 长度必须为 16”属于帧解析；“设备是否有权上报”属于业务校验；“入队后才 Ack”属于消息处理顺序。分开这些职责，是为了让一个规则只在它真正掌握信息的位置执行。

## 4.2 `next` 是后续工作，不是下一条消息

可以把一个中间件看成函数：拿到后续委托 `next`，返回新的委托。假设依次注册 A、B、终端 H，组合结果类似 `A(B(H))`。请求进入时 A 先执行，A 调用 next 才进入 B，B 再进入 H；等待完成后从 B 返回到 A。

```text
A 进入 → B 进入 → H 处理
A 退出 ← B 退出 ← H 返回
```

这里的进入与退出之间可能隔了几个小时，因为连接终端会持续处理数据。不要看到日志中 A 的“退出”很晚，就误以为没执行；它可能正在等待整条连接结束。对每帧计时则应放在消息层，不能拿整连接的持续时间当每条消息延迟。

下面是连接层顺序示意，可在独立学习片段中理解；本书不要求修改原始入口：

```csharp
listen.Use(next => async connection =>
{
    logger.LogInformation("connection {Id} begin", connection.ConnectionId);
    try
    {
        await next(connection);
    }
    finally
    {
        logger.LogInformation("connection {Id} end", connection.ConnectionId);
    }
});
listen.UseConnectionHandler<MyHandler>();
```

`await` 很重要：不等待 next 就返回，会让外层计时、作用域和释放提前结束。终端处理者不需要 next，后面再注册的链通常不会被它调用。短路则是有意不进入后续处理，例如准入失败后中断连接，或空命令直接回复提示。

## 4.3 仓库的扩展怎样把接口接到 Kestrel

打开 [连接注册扩展](../../../KestrelFramework/Microsoft.AspNetCore.Connections/ConnectionBuilderExtensions.cs)。泛型 `Use<TMiddleware>()` 先通过应用服务解析或创建中间件，再调用另一个重载；后者将接口方法适配成 `builder.Use(next => context => middleware.InvokeAsync(next, context))`。

这段代码解释了为什么自定义接口可以“插进” Kestrel：真正的扩展点仍是 `IConnectionBuilder.Use`，接口只是作者提供的可读封装。它还再次提醒我们，中间件实例可能在构建时创建并复用。把当前连接写进中间件字段，和把它写进 handler 字段一样，可能发生串线。

依赖注入只负责构造依赖，并不会自动让每个 `InvokeAsync` 获得一个新对象。若中间件需要连接状态，使用调用的局部变量。若需要 scoped 服务，显式创建作用域；若共享流量总计，则使用设计过的并发数据结构。不要用一个无锁 `long += bytes` 就假定多连接计数准确。

## 4.4 传输包装：修改视图，也承担所有权

读 [FlowAnalyzeMiddleware](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzeMiddleware.cs)：它先保存 `oldTransport`，创建 [FlowAnalyzeDuplexPipe](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzeDuplexPipe.cs)，把 `context.Transport` 替换成包装对象，然后 `await next(context)`。`finally` 负责恢复原始 Transport。

为什么替换，而不是修改业务处理者？因为业务处理者只需要读写抽象通道，无需知道计量逻辑。为什么恢复？因为外层看到的连接对象仍会继续走退出链；让它永久指向已释放的包装通道，会破坏后续收尾。为什么要 `finally`？因为正常返回、解析异常和取消都应恢复。

[XorMiddleware](../../../KestrelApp.Middleware/FlowXor/XorMiddleware.cs) 使用相同结构，只是包装层把读写字节做 XOR 变换。它便于观察“同一行协议走不同传输视图”，不提供认证、完整性或机密性保证。工业数据要加密时，应选用经过验证的安全协议并正确配置信任，而不是沿用教学 XOR。

包装次序决定观测含义。计量在 TLS 外面，可能看到密文字节与握手开销；在 TLS 后面，看到的是解密后的数据。日志在 XOR 外面和里面也会看到不同内容。选择前先问“我要测网络成本、应用负载，还是业务条数”，不能把不同层的字节计数直接比较。

[DelegatingDuplexPipe](../../../KestrelFramework/System.IO.Pipelines/DelegatingDuplexPipe.cs) 还展示了 Pipe→Stream→包装 Stream→Pipe 的适配，创建时使用 `leaveOpen: true`。这表示关闭适配器时不直接关闭底层流，并非“无需释放任何资源”。包装对象自身拥有的 reader/writer 要完成，底层连接则仍由它的所有者管理。每跨一层适配，都要写清谁完成谁。

## 4.5 应用链怎样处理一行命令

[Telnet 处理者](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs) 在构造时注册 `EmptyMiddleware`、`ByeMiddlware`、`EchoMiddleware`，并用 [ApplicationBuilder](../../../KestrelFramework/Application/ApplicationBuilder.cs) 构建委托。`Build()` 从列表末尾向前包裹，因此运行顺序与注册顺序一致，而不是倒序执行。

一条空行到达时，[EmptyMiddleware](../../../KestrelApp.Middleware/Telnet/Middleware/EmptyMiddleware.cs) 写 `Please type something.`，没有调用 next；输入 `bye` 时，[ByeMiddlware](../../../KestrelApp.Middleware/Telnet/Middleware/ByeMiddlware.cs) 写告别语并请求 Abort；其他文本最终到 [EchoMiddleware](../../../KestrelApp.Middleware/Telnet/Middleware/EchoMiddleware.cs)，例如输入 `hello` 就写出 `Did you say 'hello'?`。

如果把无条件 Echo 放最前面且仍不调用 next，空行和 bye 就不会到达专用分支。顺序不是风格问题，而是协议行为。工业网关同样应先验证格式和身份，再写入队列；如果先 Ack 再校验，设备会把失败数据误认为已经接收。

## 4.6 条件分支与分派有何差别

`ApplicationBuilder.When` 的两个重载都是命中后执行分支、不再进入原来的后续链。它不是“先经过分支再回主链”。此外，接收配置回调的重载在每次命中时创建分支 builder 并调用配置，再 Build。不能仅凭名称猜它与 ASP.NET Core 的同名 API 行为或构建时机完全相同。

如果需要只给某些设备加一段检查并继续共同入队，应明确编写继续调用逻辑，或重新设计分支合流；不要想当然把 `When` 当成自动返回主链的装饰器。一个小型分派表可以比复杂分支更容易验证，尤其协议类型已经是有限枚举时。

## 4.7 实验、参考答案与交付标准

本实验只阅读和推演，不改原目录。准备一张表，列出输入、经过的中间件、写出的响应、是否调用下一层。依次填入空字符串、`hello`、`BYE`、`bye ` 四种输入，再对照上面的三个源码文件核对。

参考答案：空串在 Empty 短路；`hello` 依次进入三层并由 Echo 回应；`BYE` 因忽略大小写匹配而走 Bye；`bye ` 有尾空格，源码未 Trim，所以走 Echo。区别说明协议不仅有“概念命令”，还有精确匹配规则。若要支持前后空格，应明确写进协议和测试，而不是由教程擅自替源码补行为。

再推演连接中间件 A、B 都在 `try/finally` 记录 begin/end，H 抛出异常。参考顺序为 `A begin, B begin, H, B end, A end`，然后异常继续向外传播，除非某层明确捕获。若 B 不调用 next，则没有 H；这与网络根本未建立连接是不同情况。

**常见错误：** 记录了所有字节就认为统计等于业务流量；捕获异常后静默返回造成正常断开与协议故障无法区分；多次调用 next 使同一连接被两个终端竞争读取；在 next 尚未完成时释放包装或作用域。这些问题的共同点是没有明确调用次数和生命周期。

**本章交付标准：** 能准确预测四种行输入的分派；能画出 A、B、H 的进入退出顺序；能说明替换 Transport 时保存、恢复、释放分别负责什么；能为“连接上限、帧长验证、入队、Ack”选择合适层次。下一章继续研究 [生命周期与资源所有权](05-lifecycle.md)，把异常与取消纳入这个模型。
