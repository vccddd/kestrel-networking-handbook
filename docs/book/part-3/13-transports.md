# 第十三章　从字节流包装理解传输链

读完本章，你应当能够沿着一条连接说明：网络字节在哪里被读取、哪个中间件替换了传输对象、下游看到的是哪一层的数据，以及资源何时归还。我们仍以工业遥测网关为贯穿场景，但先研究仓库已有的 Xor 与流量分析样例。它们适合解释机制，不能直接充当设备通信的安全方案。

## 13.1 从“收到了数据”追问到“哪一层的数据”

设备发送一条温度记录时，业务对象不会直接出现在服务器里。对象先编码成协议帧，再成为 TCP 字节流；如果启用 TLS，还要经过加密记录层。连接处理器的 `context.Transport` 是一个 `IDuplexPipe`，包含供应用读取的 `Input` 和供应用写入的 `Output`。这两个方向都从服务器角度命名：读是设备到服务器，写是服务器到设备。

TCP 保证连接内字节的有序传输，不保存应用消息边界。一次读可能只有半个帧，也可能包含多个帧。传输包装器可以观察或变换这些字节，却不会凭空获得帧边界。把“每次 ReadAsync”计为“一条遥测”会在本机测试中偶尔看起来正确，换一个网络或缓冲区大小就失效。

本书的 KG/1 教学协议会在[工业设计章](16-industrial-design.md)明确长度和类型。这里先记住职责：传输层负责提供字节，协议层负责找出完整消息，应用层决定消息能做什么。自定义 `ConnectionHandler` 不会自动获得 HTTP 请求体上限、HTTP 认证或 MVC 校验；连接数、帧长度、读写期限和设备授权都需要自己的设计。

## 13.2 顺着 Xor 的源码读一遍

打开 [XorMiddleware](../../../KestrelApp.Middleware/FlowXor/XorMiddleware.cs)。它先保存 `oldTransport`，构造 `XorDuplexPipe`，把 `context.Transport` 换成新对象，再 `await next(context)`。`finally` 恢复旧对象。这不是创建另一条网络连接，而是在当前连接上给下游提供一个新的读写入口。

恢复动作必须放在 `finally`，因为下游可能正常结束、取消或抛异常。否则外层清理逻辑可能继续持有已经完成的包装器。`await using` 则限定包装器的生命周期：下游还在读取时不能先完成它。阅读连接中间件时，可以固定问三个问题：谁创建对象、谁可以完成对象、发生异常后引用是否仍然正确。

[XorDuplexPipe](../../../KestrelApp.Middleware/FlowXor/XorDuplexPipe.cs) 把具体变换交给 [XorStream](../../../KestrelApp.Middleware/FlowXor/XorStream.cs)。后者的常量是 `xorByte = 9`，每个字节都执行 `b ^ 9`。同一个变换连续执行两次会恢复原值，因为 `(b ^ 9) ^ 9 == b`。这解释了为什么双方约定相同变换就能通信，也解释了它为什么不是安全加密：没有秘密密钥协商，没有身份认证，没有完整性保护；观察少量已知明文就能推知变换。不要把样例注释里的“加密/解密模拟”理解成可部署的密码方案。

写路径先租用数组，复制调用者提供的只读内存，再在副本上变换，最后异步写入。复制的原因不是协议要求，而是不能原地改写 `ReadOnlyMemory<byte>` 背后的调用者数据。查看 [ArrayOwner](../../../KestrelFramework/System.Buffers/ArrayOwner.cs) 可以确认：`AsMemory()` 只暴露请求的有效长度，即使池实际分配的数组更大，也不会把整个数组发送出去；`using` 结束时归还数组。池减少分配，但每次写仍然有复制成本。

读路径有一个值得主动发现的边界：`ReadAsync` 返回 `length` 后，当前实现对整个 `destination.Span` 做异或，而非只对 `destination.Span[..length]` 操作。按 Stream 契约，只有前 `length` 个字节属于这次有效读取，其余空间不应被当成输入数据处理。当前方法最终仍返回真实长度，但会无谓改动未读区域。这是源码审阅结论，不是本章已经修复的功能；教学实现不宜原样复制到生产。

## 13.3 Pipe、Stream、Pipe 的桥接有什么代价

[DelegatingDuplexPipe](../../../KestrelFramework/System.IO.Pipelines/DelegatingDuplexPipe.cs) 展示了桥接链：原始 `IDuplexPipe` 先包成 `DuplexPipeStream`，经委托工厂得到自定义 Stream，再用 `PipeReader.Create` 和 `PipeWriter.Create` 回到 Pipe 接口。这样可以复用 Stream 装饰器，业务代码也仍然读取 Pipe。

便利并不表示免费。桥接可能引入额外缓冲、复制和异步调度；读写调用次数也未必等于网络系统调用次数。包装器析构时完成它创建的 Input、Output，而构造参数使用 `leaveOpen: true`，表达保留底层流的意图。实际集成时仍须沿着整个拥有关系检查，不能以为所有对象都由同一个 `Dispose` 负责。

生产网关如果只需要读取一个固定二进制帧，通常先直接基于 Pipe 编写清晰的解析循环，确认边界正确后再优化。为了获得“零复制”的名声叠加几个包装层，反而会让内存生命周期更难证明。尤其不能把 Pipe 返回的切片放入后台队列后立即 `AdvanceTo`，那片内存随后可能被复用；跨读取周期保存数据，应复制为有明确所有者的对象。

## 13.4 交换顺序会改变计量含义

[FlowAnalyzeMiddleware](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzeMiddleware.cs) 与 Xor 使用相似的替换和恢复模式。它的 [FlowAnalyzeStream](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzeStream.cs) 在读完成之后报告实际读到的长度，却在写之前报告准备写出的长度。因此读计数近似“此层交付给应用的字节数”，写计数更接近“此层尝试写出的字节数”。底层写失败时，写计数已经增加，不能把它解释为对端已经收到了数据。

假设注册顺序是 `UseFlowAnalyze().UseFlowXor().UseHandler()`，分析器先包住原始 Transport，Xor 再包住分析器。业务读写经过 Xor 时会继续经过分析器。把顺序倒过来，计数位置就移动到了另一层。Xor 本身不改变长度，所以只看总字节数可能分辨不出差别；改成压缩或 TLS，线上字节和业务字节便可能明显不同。指标名必须带上计量位置，不能只叫“吞吐”。

还要区分“注册服务”和“把中间件接到端口”。[AddFlowAnalyze](../../../KestrelApp.Middleware/FlowAnalyze/ServiceCollectionExtensions.cs) 只把分析器注册成单例；没有在监听链上安装对应包装器，就不会自动统计所有端口。单例还意味着统计聚合到同一个服务实例，不天然按设备或连接隔离。

## 13.5 TCP、TLS、HTTP/3 的边界

TLS 是提供机密性、完整性和对端认证能力的标准协议，仍需正确的证书校验与授权。其服务端证书证明服务器身份，客户端证书能参与设备身份建立，但“握手成功”不等于设备有权写任意设备编号的数据。设备身份与载荷中的 `deviceId` 必须绑定。

HTTP/3 使用 QUIC；QUIC 具备多流和自身的握手、流控语义。不能把本章基于 TCP 双向字节流的任意 `ConnectionHandler` 改一个端口开关，就宣称实现了 HTTP/3 或任意自定义协议的 QUIC 版本。HTTP/3 是明确的 HTTP 协议栈，通用 QUIC 应用还涉及多路复用连接抽象、流的生命周期以及平台支持。初学阶段先把 TCP 帧协议、取消和背压做对，之后再单独评估传输替换。

## 13.6 动手练习：用字节证明自己的理解

本练习只需要本机 Python，不连接现场设备，也不启动会安装证书的 Fiddler 样例。先预测字节 `0x41`、`0x00` 经一次和两次异或后的结果，再运行：

```python
plain = bytes([0x41, 0x00])
wire = bytes(b ^ 9 for b in plain)
restored = bytes(b ^ 9 for b in wire)
print(plain.hex(), wire.hex(), restored.hex())
assert restored == plain
```

接着画一张纸面调用图：外层分析器、内层 Xor、最内层业务，给业务写入两个字节标出经过顺序。最后检查“分析器记录了 2 个写字节，所以设备已确认收到”这一推论，指出缺了哪些证据。

参考答案：输出为 `4100 4809 4100`；业务写入先进入 Xor 的复制和变换，再进入分析器，再到原始传输。分析器当前在底层写完成前计数，因此不能证明成功送达；即使底层写成功，也只说明本地传输接受了数据。设备应用是否处理，需要协议级 ACK，而 ACK 的业务含义还要另行定义。若要修正 Xor 的读边界，核心思路是仅变换返回长度内的切片，并覆盖短读、零字节 EOF 和取消场景；本任务没有修改旧源码。

继续阅读[Redis 伪服务器](14-redis.md)，把这里的字节流视角推进到完整请求、认证状态和命令分派。
