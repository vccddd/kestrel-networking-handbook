# 第十四章　拆解 Redis 伪服务器：解析、状态与命令链

本章目标是读懂一个自定义协议应用从字节到响应的完整路径，同时学会辨认“示范了结构”和“满足了协议”之间的距离。仓库中的 Redis 代码只实现少量命令，是伪服务器；它没有键值存储、事务、持久化、复制或完整 Redis 兼容性。不要让真实业务的 Redis 客户端指向它来验证数据安全。

## 14.1 先找到入口，再看业务

旧文档[开发 Redis 服务器](../../../docs/1.3%20kestrel网络编程--开发redis服务器.md)说明了原始教学目的。实际行为以现有源码为准。监听扩展把 Redis 端口交给 [RedisConnectionHandler](../../../KestrelApp.Middleware/Redis/RedisConnectionHandler.cs)，后者构造一条应用链：`AuthMiddleware → CmdMiddleware → FallbackMiddlware`。每次网络读取可能解析出多个请求，每个请求得到独立的 `RedisContext`，按顺序执行这条链。

这里存在两个生命周期。一个连接拥有一个 `RedisClient` 和一个输出包装器；连接可以处理很多个请求。每个请求的上下文引用同一个客户端，所以认证状态跨请求保留。若把 `IsAuthed` 放到单例命令处理器字段中，就会变成多个连接共用认证状态，构成严重隔离错误。源码把该状态放在客户端对象里，是值得保留的设计。

`RedisContext` 将请求、客户端、响应和 Features 组合起来，类似 HTTP 的上下文，但它不是 `HttpContext`。因此 ASP.NET Core 的 HTTP 授权中间件、请求体限制和模型验证不会自动作用于这里。自定义协议采用熟悉的命名方式，可以降低阅读成本；采用相似外形，并不等于继承了 HTTP 栈的全部保护。

## 14.2 用一个 PING 解释 RESP 的长度

[RedisRequest](../../../KestrelApp.Middleware/Redis/RedisRequest.cs) 识别的主要形式是“由 bulk string 构成的数组”。一个 PING 请求可写成：

```text
*1\r\n$4\r\nPING\r\n
```

`*1` 表示数组有一个成员；`$4` 表示下一段有效负载有四个字节；CRLF 是两个独立字节 `0D 0A`。这条消息总长 14 字节。ECHO 的命令名和参数是两个成员，例如 `*2\r\n$4\r\nECHO\r\n$3\r\nabc\r\n`。

长度针对字节，不是 .NET 字符数量。中文参数“温度”在 UTF-8 下是六个字节，不能填 `$2`。设备网关中同样要明确整数端序、负数编码和长度单位；“字符串长度大概等于字节数”是协议互通中很常见的隐患。

解析入口在多段序列时调用 `ToArray()`，先把当前缓冲区线性化，再逐个 `TryParse`。每成功一条，就累加 `Size`，最终用原始 `buffer.GetPosition(size)` 返回已消费位置。这种写法易于理解，并且复制后的值可以安全跨越 `AdvanceTo`；代价是多段缓冲和每个参数都可能产生数组分配。不要把“使用了 Pipe”直接等同于“所有路径零复制”。

连接循环有请求时消费到 `consumed`，没有完整请求时保留起点并把 examined 指到末尾，表达“这些字节已检查过，但还不能释放，需要更多字节”。正常的增量解析器应当允许任意网络分片。当前样例在这点上有明确缺陷。

## 14.3 为什么完整报文能通不代表解析器正确

逐行读 `TryParse` 可以找到以下实际边界，而不必先连接一个外部 Redis 客户端：

- 数组头完成后立刻进入参数循环并读取 `span[0]`。输入只有 `*1\r\n` 时，剩余 Span 为空，这里会越界，而不是返回“等待更多”。
- 对长度大于等于四、但尚未出现换行的头部，`IndexOf('\n') + 1` 为零，代码会抛协议异常。合法头部若恰好被网络拆开，也可能被误判。
- 代码根据换行位置切片，却没有全面检查 CRLF 的精确字节；bulk body 后面的两个字节只是被跳过。错误分隔符可能被接受。
- 没有完整的数组成员数、bulk 长度和总帧上限；负长度、巨大长度、算术溢出以及空数组都需要更严格处理。`*0\r\n` 最后仍访问 `values[0]`，并非一个安全的空请求。

这些问题意味着：把一次 `sendall` 的正常请求跑通，只能说明一条路径可工作。操作系统仍可能拆分那次发送，不能靠客户端“总是一次发送”修好服务器。生产解析器需要区分三种结果：完整帧、尚不完整、格式非法。前两者不能共用异常路径；非法报文也不能让服务器无限等待。

解析器还先收集一个批次再交给命令链。如果一份缓冲里前面有合法请求、后面紧跟非法请求，整个解析过程可能先抛异常，导致前面已收集的请求也未被执行。如何处理批次内错误，需要清晰约定。本书的 KG/1 基线选择非法帧关闭连接，且不扫描后续字节尝试重同步；这个选择可以让错误边界简单，但必须明确告知客户端。

## 14.4 认证链其实是一个三态状态机

[RedisClient](../../../KestrelApp.Middleware/Redis/RedisClient.cs) 的 `IsAuthed` 是 `bool?`：初始 `null` 表示尚未决策，`true` 表示已认证，`false` 表示失败。[AuthMiddleware](../../../KestrelApp.Middleware/Redis/Middlewares/AuthMiddleware.cs) 先检查失败状态并直接回复错误，再检查成功状态并放行；只有未决策状态才考虑当前是不是 AUTH。

如果未配置密码，第一次非 AUTH 请求会把状态设为成功。如果配置了密码，未认证的非 AUTH 请求会得到错误；AUTH 会交给 [AuthHandler](../../../KestrelApp.Middleware/Redis/CmdHandlers/AuthHandler.cs) 比较第一个参数与配置密码的 UTF-8 字节。这里有一个容易忽略的后果：一旦 AUTH 失败把状态置为 `false`，以后即使在同一连接发送正确 AUTH，也先被认证中间件拦住，无法重试。必须重新连接。它不是完整 Redis 认证行为的承诺，而是当前链路的直接结果。

还应思考配置轮换：已认证连接走 `true` 分支，不再逐次检查新密码，所以修改配置并不自动撤销现存连接。生产设备身份设计需要定义会话失效、证书吊销和轮换窗口，而不是只提供一个可变密码字符串。AUTH 若在明文 TCP 上发送，密码没有传输保密性；也不能把它当成工业设备授权方案。

## 14.5 命令分派值得学，兼容性不能夸大

[服务注册](../../../KestrelApp.Middleware/Redis/ServiceCollectionExtensions.cs) 扫描程序集中的 `IRedisCmdHanler` 实现并按单例加入容器。[CmdMiddleware](../../../KestrelApp.Middleware/Redis/Middlewares/CmdMiddleware.cs) 将其做成按枚举索引的字典。查到命令时调用处理器，查不到才执行后续兜底。因而新增命令主要涉及解析到枚举、实现处理器和保证注册，这是一个可复用的分派思路。

但是每个处理器的实际内容很有限：[PING](../../../KestrelApp.Middleware/Redis/CmdHandlers/PingHandler.cs) 固定回复 PONG；[ECHO](../../../KestrelApp.Middleware/Redis/CmdHandlers/EchoHandler.cs) 回显第一个参数，没有参数时回显空串；[INFO](../../../KestrelApp.Middleware/Redis/CmdHandlers/InfoHandler.cs) 固定输出 `redis_version: 9.9.9`；[SELECT](../../../KestrelApp.Middleware/Redis/CmdHandlers/SelectHandler.cs) 只回复 OK，不切换数据库；[QUIT](../../../KestrelApp.Middleware/Redis/CmdHandlers/QuitHandler.cs) 调用客户端关闭。它们不能证明完整参数检查、数据库存在或版本兼容。

响应包装器支持先写多个片段再 Flush，适合编码 bulk string。写入或 Flush 完成也不等于客户端业务已经消费。工业网关中的 ACK 必须用协议定义说明确认的阶段，不能用网络写成功推导数据库提交成功。

## 14.6 动手练习与参考答案

练习一无需运行服务器：写出 `ECHO 温度` 的 RESP 字节长度，解释为什么不能用字符长度。参考答案是 `*2\r\n$4\r\nECHO\r\n$6\r\n` 后跟 UTF-8 的“温度”和 CRLF；bulk 长度为 6。整个请求共 26 字节：数组头 4、命令段 10、参数段 12。

练习二在纸上模拟以下顺序，假定配置了密码：连接建立、错误 AUTH、正确 AUTH、PING。参考答案：初始为 null，错误 AUTH 把状态改为 false；后两条都被认证中间件的首个分支拒绝，正确 AUTH 不会进入处理器。若重连并先发送正确 AUTH，则变为 true，随后 PING 可到达命令处理器。

练习三检查半包 `*1\r\n`。参考答案：数组数量解析成功，剩余 Span 为空，进入首个成员时读取 `span[0]` 越界。一个改进解析器应返回“不完整”，保留全部输入，并在有界等待时间内等后续字节。不能把这个错误写成客户端必须规避的协议限制。

可选延伸实验需要自备隔离的旧项目运行环境：旧项目面向 .NET 6，本书没有提供其运行时安装与完整启动准备，前面三道离线练习已足以完成本章目标。若你已具备匹配的 SDK、运行时和依赖，并能在隔离环境检查、限制所有监听地址，再用 Python 客户端向本机回环地址 5007 发送 `b'*1\r\n$4\r\nPING\r\n'`，预期读取 `b'+PONG\r\n'`。旧项目还配置了其他端口及 TLS 检测，不能把只测 Redis 误认为启动时只暴露了 Redis。实验结果只覆盖这条正常路径，不要求为了完成本章另外安装旧运行时。接下来阅读[代理与 TLS](15-proxy-tls.md)，继续练习把示例行为和安全承诺分开。
