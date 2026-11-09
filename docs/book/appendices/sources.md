# 附录 C：源码地图与权威资料

阅读顺序不是文件名排序，而是沿一条数据路径前进。先看监听，再看连接处理，再看解析/响应，最后看包装与生产约束。原仓库导读基准提交为 `ba06920608a416fd285c24538a87ad379c1bbd1b`，本书新增内容由独立教程仓库维护，与上游示例的历史分开。

## C.1 从问题找到源码

| 想回答的问题 | 应读文件 | 对应章 |
|---|---|---|
| 一个程序怎样监听不同协议 | [原入口](../../../KestrelApp/Program.cs)、[端点配置](../../../KestrelApp/appsettings.json) | 2、3 |
| 怎样把对象中间件装进连接链 | [连接扩展](../../../KestrelFramework/Microsoft.AspNetCore.Connections/ConnectionBuilderExtensions.cs)、[中间件接口](../../../KestrelFramework/IKestrelMiddleware.cs) | 4 |
| 怎样把next组合成应用 | [ApplicationBuilder](../../../KestrelFramework/Application/ApplicationBuilder.cs) | 4、14 |
| 最短的二进制与文本示例 | [Echo](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs)、[Telnet](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs) | 6 |
| 如何租赁和封装缓冲 | [ArrayOwner](../../../KestrelFramework/System.Buffers/ArrayOwner.cs)、[ArrayPoolBufferWriter](../../../KestrelFramework/System.Buffers/ArrayPoolBufferWriter.cs) | 7、8 |
| Stream与管道怎么适配 | [DuplexPipeStream](../../../KestrelFramework/System.IO/DuplexPipeStream.cs)、[DelegatingDuplexPipe](../../../KestrelFramework/System.IO.Pipelines/DelegatingDuplexPipe.cs) | 9、13 |
| RESP怎样成为应用消息 | [RedisRequest](../../../KestrelApp.Middleware/Redis/RedisRequest.cs)、[RedisConnectionHandler](../../../KestrelApp.Middleware/Redis/RedisConnectionHandler.cs) | 10、14 |
| 包装流如何变换与计数 | [XorStream](../../../KestrelApp.Middleware/FlowXor/XorStream.cs)、[FlowAnalyzeStream](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzeStream.cs) | 13、18 |
| TLS与代理怎样编排 | [Fiddler监听扩展](../../../KestrelApp.Fiddler/ListenOptionsExtensions.cs)、[TLS探测扩展](../../../KestrelFramework/Middleware/TlsDetection/ListenOptionsExtensions.cs) | 15 |
| 改进后的解析与循环 | [KG/1解析器](../../../samples/IndustrialGateway/Protocol.cs)、[网关处理器](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs) | 10、11、16、19 |
| 有界队列和监控 | [队列/消费者/指标](../../../samples/IndustrialGateway/TelemetryQueue.cs) | 11、18、19 |
| 怎样证明边界行为 | [确定性检查](../../../samples/IndustrialGateway.Checks/Program.cs)、[socket验证](../../../samples/IndustrialGateway/verify.py) | 19、20 |

源码链接在本地与GitHub中可直接阅读。原文件在远程发布时也可以使用[锁定提交的仓库树](https://github.com/xljiulang/KestrelApp/tree/ba06920608a416fd285c24538a87ad379c1bbd1b)定位，避免以后默认分支变化使书中描述失去依据。新样例随本书发布在[独立教程仓库](https://github.com/vccddd/kestrel-networking-handbook/tree/main/samples)，Pages阅读站为被引用的源码生成可读页面；源码与书稿一起审阅和更新。

## C.2 适合查API契约的官方资料

- [System.IO.Pipelines指南](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)：reader/writer、两游标、取消、EOF与常见错误。
- [Memory与Span使用指南](https://learn.microsoft.com/en-us/dotnet/standard/memory-and-spans/memory-t-usage-guidelines)：所有者、消费者和租期。
- [System.Buffers介绍](https://learn.microsoft.com/en-us/dotnet/standard/io/buffers)：缓冲类型与SequenceReader。
- [高性能.NET I/O最初设计背景](https://devblogs.microsoft.com/dotnet/system-io-pipelines-high-performance-io-in-net/)：了解Pipelines解决的复杂度，避免把历史性能数字当今天的指标。
- [Kestrel端点配置，ASP.NET Core 10](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/servers/kestrel/endpoints?view=aspnetcore-10.0)：监听、协议、TLS配置的版本化说明。
- [ConnectionHandler API](https://learn.microsoft.com/en-us/dotnet/api/microsoft.aspnetcore.connections.connectionhandler?view=aspnetcore-10.0)：连接处理终端契约。
- [SocketTransportOptions API](https://learn.microsoft.com/en-us/dotnet/api/microsoft.aspnetcore.server.kestrel.transport.sockets.sockettransportoptions?view=aspnetcore-10.0)：裸TCP读写水位与传输选项。
- [Channels指南](https://learn.microsoft.com/en-us/dotnet/core/extensions/channels)：容量、满载模式、多读写者契约。
- [指标埋点指南](https://learn.microsoft.com/en-us/dotnet/core/diagnostics/metrics-instrumentation)：Counter、Gauge、Histogram的选择。
- [dotnet-counters](https://learn.microsoft.com/en-us/dotnet/core/diagnostics/dotnet-counters)：如何观察运行中进程。
- [global.json配置](https://learn.microsoft.com/en-us/dotnet/core/tools/global-json)：SDK搜索规则与rollForward。
- [.NET支持策略](https://dotnet.microsoft.com/en-us/platform/support/policy/dotnet-core)：判断旧运行时支持状态，项目启动前重新查看。

## C.3 需要版本源码才能解释的问题

高级调度细节不能只靠方法名猜测。本书使用固定标签的源码作为证据；实现细节可能随.NET版本改变。

- [.NET 10 Pipe实现](https://github.com/dotnet/runtime/blob/v10.0.0/src/libraries/System.IO.Pipelines/src/System/IO/Pipelines/Pipe.cs)：AdvanceReader中examined与背压计量，以及consumed内存释放。
- [.NET 10 SocketConnection](https://github.com/dotnet/aspnetcore/blob/v10.0.0/src/Servers/Kestrel/Transport.Sockets/src/Internal/SocketConnection.cs)：接收FIN、完成输入、触发ConnectionClosed与发送循环。
- [.NET 10 SocketTransportOptions](https://github.com/dotnet/aspnetcore/blob/v10.0.0/src/Servers/Kestrel/Transport.Sockets/src/SocketTransportOptions.cs)：流控水位的准确范围。
- [.NET 10 Instrument实现](https://github.com/dotnet/runtime/blob/v10.0.0/src/libraries/System.Diagnostics.DiagnosticSource/src/System/Diagnostics/Metrics/Instrument.common.cs)：同步测量发给当前订阅者，不回放历史。

阅读顺序建议先契约、后源码。先用公开API写正确程序，只有在解释契约没有覆盖的实现现象时才进入内部实现；不要把内部类名作为应用依赖。

GitBook的内容配置规则以[官方Git Sync配置文档](https://gitbook.com/docs/docs-as-code/git-sync/content-configuration)为准；本书使用仓库根配置和显式SUMMARY，不依赖已过时的全局gitbook-cli。
