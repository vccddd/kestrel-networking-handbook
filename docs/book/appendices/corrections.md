# 附录 B：原文与示例的勘误、边界和保留理由

本书以实际源码与当前API契约为依据。下列条目不是否定原示例的教学价值，而是区分“展示一个机制”和“可以直接部署”。原 .NET 6/7 文件保持原状，修正后的可执行教学实现放在[新实验](../../../samples/IndustrialGateway/README.md)。

## B.1 对原文章表述的修正

| 原表述或容易得出的推断 | 本书采用的准确理解 | 为什么影响实践 |
|---|---|---|
| Span 的性能固定高于指针、数组 | 性能由访问模式、JIT、边界检查消除、平台和版本决定 | 单个旧微基准不能替代真实测量 |
| Span/Memory 都是栈上内存 | Span是ref struct视图，Memory是可保存的视图；底层可能为堆数组、池或其他存储 | 类型约束、底层存储位置和所有权是三件事 |
| 小于1KiB必定new更快，大于必定池化更快 | 只能作为待验证经验，不能泛化固定分界 | 池租还、清零与并发开销会改变结果 |
| MemoryPool必然经ArrayPool获取数组 | 具体MemoryPool可使用不同存储策略 | 不应依赖未承诺的实现细节 |
| Flush就是写Stream并立即归还池数组 | 内存Pipe、Stream适配器、Kestrel输出的契约不同 | Flush不普遍释放内存，也不证明业务接收 |
| 高性能等于完全零拷贝 | 有界小复制常换来简单可靠的所有权 | 错误借用比一次复制更昂贵 |
| 不必再用Socket或第三方框架 | Kestrel是优秀选项，不是所有网络场景的唯一选择 | UDP、设备生态和现成协议库可能有不同需求 |
| TCP/TLS/HTTP/QUIC可随意按端口替换 | 需区分传输实现、连接模型、TLS与HTTP/3协议支持 | 任意ConnectionHandler不能靠改一个HTTP选项变成QUIC服务 |

[原缓冲文章](../../2.1%20高性能buffer--相关新类型.md)和[原PipeWriter文章](../../2.3%20高性能buffer--PipeWriter.md)包含上述历史语境。书中已给出新的契约解释，不建议先背原结论再打补丁。

## B.2 对具体源码的导读边界

| 源码 | 可以学到什么 | 复制到生产前必须解决 |
|---|---|---|
| [EchoConnectionHandler](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs) | 长度前缀、池化正文、异步回显 | ushort写/short读不一致；EOF同批多帧和ConnectionClosed前置退出；消息限制与异常路径 |
| [TelnetConnectionHandler](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs) | 行分隔文本处理和应用链 | 不是完整Telnet协商实现；长行上限、EOF多消息与取消 |
| [RedisRequest](../../../KestrelApp.Middleware/Redis/RedisRequest.cs) | RESP结构、命令上下文 | 空span访问、不完整换行判断、零元素及数字边界；整批ToArray和多处复制；并非完整Redis |
| [RedisConnectionHandler](../../../KestrelApp.Middleware/Redis/RedisConnectionHandler.cs) | 每连接循环与每消息中间件 | 边界解析、EOF残留、资源所有权和结构化错误分类；不照抄context.Dispose |
| [AuthMiddleware](../../../KestrelApp.Middleware/Redis/Middlewares/AuthMiddleware.cs) | 会话认证状态如何进入命令链 | 认证失败状态变化及后续恢复策略；不能当成完整安全设计 |
| [XorStream](../../../KestrelApp.Middleware/FlowXor/XorStream.cs) | 包装传输、读解码写编码 | 只处理实际读到范围；取消与内存所有权；XOR没有TLS安全性 |
| [FlowAnalyzer](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzer.cs) | 分方向计数与周期窗口 | 计数时点、窗口时长、读写失败及业务吞吐含义要分别定义 |
| [XorTelnetProxyHandler](../../../KestrelApp.Middleware/TelnetProxy/XorTelnetProxyHandler.cs) | 双向复制 | WhenAny后停止并等待另一任务，释放上游，定义半关闭策略 |
| [HTTP代理认证](../../../KestrelApp.Middleware/HttpProxy/HttpProxyAuthenticationHandler.cs) | 可扩展认证入口 | 默认放行不能暴露为开放代理；目标地址校验、SSRF与配额 |
| [Fiddler入口](../../../KestrelApp.Fiddler/Program.cs) | HTTP代理、TLS与分析组合 | CA安装与系统信任的副作用、证书生命周期、敏感流量和目标权限 |

本书没有执行CA安装，也没有连接现场设备。Fiddler是理解分层的案例，不是要求你为了学会Pipelines而修改系统信任库。

## B.3 本书编写与验证中主动修正的问题

首次真实socket验证发现：链接ConnectionClosed取消令牌，会在接收FIN后过早停止处理，丢掉已缓冲的多帧。新handler改用Pipe EOF/错误作为正常输入结束依据，主机停止主动取消，绝对预算约束等待。这是当前实验验证的具体输入半关闭场景，不承诺通用服务端半关闭支持。

技术审校进一步核对了.NET 10 Pipe源码：背压计量随examined推进，存储释放由consumed决定。因此不采用“完整帧比水位大就必然死锁”的简化解释。真正的循环等待需要结合Flush、reader启动与推进顺序分析，详见[第11章](../part-2/11-backpressure.md)。

读者审校发现根目录执行 `dotnet run --project samples/...` 不会自动应用子目录的SDK配置。全书统一先进入samples执行dotnet，Python脚本内部也固定正确cwd。仅安装.NET10 SDK不是所有终端都会选中它的充分条件。

指标审校发现同步UpDownCounter不能向晚接入采集器回放既有连接的增量。本实验用Interlocked保存独立当前数，再以ObservableGauge读取，检查程序验证先建立状态、后附加MeterListener仍能得到正确值。事件Counter仍是采集窗口内增量，不宣称回放进程启动以来历史。

ACK顺序也经过统一：必须先成功入队才生成ACK，但不等待消费者完成；消费者可能在ACK前完成，也可能在ACK后完成。所谓“仅入队确认”是保证的下限，不是保证消费者还没动手。

## B.4 如何继续更新勘误

发现问题时，记录具体版本、输入字节、实际结果、预期结果和最小复现。优先写能失败的确定性检查；涉及连接生命周期再补真实socket验证。修改代码后同步协议表、练习答案、默认参数和[验证记录](validation.md)。若只是新环境中的性能下降，先保留原始数据，再决定是否修改教学结论。
