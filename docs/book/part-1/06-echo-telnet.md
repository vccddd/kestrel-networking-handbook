# 第 6 章 从 Echo 与行协议走到工业帧

本章逐步拆开仓库中两个最小的应用协议。Echo 帮我们看长度前缀，Telnet 示例帮我们看分隔符与消息中间件。目标是能够解释每一个读取、消费和刷新步骤，同时识别教学代码尚未覆盖的输入边界。请先理解 [第 5 章的生命周期](05-lifecycle.md)，避免只抄主循环。

## 6.1 先问 Echo 到底回显什么

“Echo”只表达业务意思：收到什么，就返回什么。它没有自动规定线上的字节格式。仓库的 [EchoConnectionHandler](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs) 选择 `两字节大端长度 + 正文字节`。因此向它直接发送 ASCII 的 `hello`，前两个字节 `68 65` 会被当成长字段，而不是正文开头。

建立连接后，服务器主动发送一条欢迎帧，正文是 UTF-8 的 `Hello world`，长 11 字节。线上数据开头为 `00 0b`，后跟那 11 个字节。客户端必须先读并解析欢迎帧，随后再发送自己的请求并读对应响应。把欢迎语当作第一次请求的回显，会让后续所有请求响应错位。

这里的帧长度只包含正文，不包含两字节头。KG/1 的 payloadLength 同样不包含头，但头本身是 16 字节，两种协议不能混用。学习时每个协议都写一张字段表，比记住“它们都是 TCP”更有用。

## 6.2 沿 Echo 的主路径逐句读

处理者先从 `context.Transport` 获取 input/output，写入欢迎语长度和内容，然后 Flush。`WriteBigEndian` 和池数组不是 C# 内置语法；它们来自仓库的缓冲扩展。[BufferWriterExtensions](../../../KestrelFramework/System.Buffers/BufferWriterExtensions.cs) 值得沿方法定义查读，避免在另一个项目复制代码时只加 using 却找不到扩展。

每次 `ReadAsync` 得到一批暂借缓冲，`TryReadEcho` 用局部 `SequenceReader<byte>` 从起点尝试读长字段。正文足够时，它租出数组所有者，复制恰好 length 个字节，推进局部 reader，并返回 consumed。业务随后解 UTF-8 供日志观察，再把原始字节按相同格式写回，Flush 后归还数组并 Advance。

注意回显的是字节，不是把解码后的字符串重新编码。无效 UTF-8 的日志显示可能替换字符，但原始字节仍可被回显；“日志看起来一样”因此不适合作为精确字节一致性的唯一证据。源码日志写 `Received from server`，从此处理者视角实际收到的是客户端数据，教程中应按真实方向理解。

数据不足时，`AdvanceTo(result.Buffer.Start, result.Buffer.End)` 不消费任何字节，只声明当前内容已经检查过。下一次有更多数据时仍从帧头重试。这正是长度前缀协议抵御分次到达的基本结构。若只处理一帧且缓冲仍有第二帧，成功分支的一参数 AdvanceTo 会把 examined 设为 consumed，使下次读取能继续看到尾部。

## 6.3 短实现暴露的边界很有教学价值

第一处是有符号长度。输出用 `(ushort)` 写长度，解析却 `TryReadBigEndian(out short length)`。`short` 范围是 -32768 到 32767，所以 `80 00` 被读成负数。后面的剩余长度比较不能替代非负检查，租借或切片可能抛异常。正确协议应先明确无符号范围、业务上限，再安全转换；不能把外部声明长度直接当分配请求。

第二处是 EOF 排空。循环一轮只处理一帧，随后若 `IsCompleted` 就 break。若最后一次读取同时含多帧和 EOF，后面完整帧可能尚未处理；如果只有半帧，当前例子也没有明确报截断。再加上外层以 ConnectionClosed 作为循环条件，客户端半关闭可能触发前章讨论的末尾数据丢失。

第三处是完整的资源与失败路径。取消结果也要配对 Advance；Flush 结果要检查，对端停止读时不能无限写；长度、总预算与错误日志都需要明确策略。指出这些边界是为了学会审查，而不是否认短示例的价值。它已经清楚展示了“缓冲→帧→自有内存→响应”，工业版本要在此基础上补齐契约。

## 6.4 Telnet 示例其实是一种 CRLF 行协议

[TelnetConnectionHandler](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs) 先发两行欢迎语，随后用 `TryReadRequest` 寻找 `\r\n`，找到后将行正文解为 UTF-8 字符串。`\r` 是回车字节 `0d`，`\n` 是换行字节 `0a`；两个字节合起来才是本例的一条消息终点，分隔符不进入 Request。

这段代码适合用 telnet 或原始 TCP 工具手动交互，但没有实现完整 Telnet 标准中的 IAC 协商、选项和终端控制。真正的 Telnet 客户端可能发送协议控制字节；本例没有专门解析它们。因此我们在讲解时称它“Telnet 示例的行协议”，不把名字当成标准兼容保证。

为什么行协议适合入门？人可以直接看到请求和响应，分隔符也容易观察。为什么工业二进制采集改用 KG/1？固定字段减少文本转换，长度与类型规则便于验证，数值单位和 sequence 可明确编码。二进制也更难手工检查，所以必须配模拟器、十六进制观察和确定性解析测试。

## 6.5 一行怎样进入业务链

一旦解析成功，处理者先 Advance，再创建 `TelnetContext` 并调用 application。这样做成立，是因为请求已经变成独立字符串；若保留的是借用 span，则 Advance 后再使用会失效。`TelnetResponse` 则围绕 output 存在于该连接内部，每行上下文复用它。

应用链依次是空行、告别、回显。输入零长度的行时返回 `Please type something.`；输入大小写任意的 `bye` 时写 `Have a good day!` 并 Abort；其他行返回 `Did you say 'hello'?` 这样的文本。这些对应 [Empty](../../../KestrelApp.Middleware/Telnet/Middleware/EmptyMiddleware.cs)、[Bye](../../../KestrelApp.Middleware/Telnet/Middleware/ByeMiddlware.cs)、[Echo](../../../KestrelApp.Middleware/Telnet/Middleware/EchoMiddleware.cs) 三个中间件。

[TelnetResponse](../../../KestrelApp.Middleware/Telnet/TelnetResponse.cs) 的 `WriteLine` 写正文并补 CRLF，`WriteLineAsync` 再调用 Flush。先写两行欢迎语再刷新一次，是批量提交的演示。Flush 完成仍不证明对端应用已经读到，更不保证紧接 Abort 后告别语必然显示；“尽力写出”与“远端确认收到”应分开。

行解析同样需要最大长度和绝对预算。客户端一直不发 CRLF，缓冲可能持续增长；只发 LF 的工具则会一直等待，而不是触发空行。原例也一轮只处理一行并在 EOF 时退出，所以最后一批包含多行时应重审排空逻辑。不要因为文本容易看见，就忽略它与二进制协议拥有同样的流式问题。

## 6.6 实验一：离线验证两种分帧

这个实验不依赖旧 net6 宿主，可在当前环境直接运行：

```bash
python3 - <<'PY'
import struct
body = '温度'.encode('utf-8')
echo = struct.pack('>H', len(body)) + body
print('Echo正文字符数:', len('温度'))
print('Echo正文字节数:', len(body))
print('Echo完整帧:', echo.hex(' '))
raw = b'hello\r\nbye\r\n'
parts = raw.split(b'\r\n')
print('已完成的行:', [x.decode() for x in parts[:-1]])
print('末尾未成行字节:', parts[-1])
PY
```

参考结果：字符数 2，UTF-8 字节数 6，Echo 帧为 `00 06 e6 b8 a9 e5 ba a6`；两条完整行为 hello、bye，尾部为空。再把 raw 改为 `b'hello\r\nby'`，应只有 hello 一条完整行，尾部 `by` 必须保留等待。如果此刻 EOF，`by` 是否作为最后一条命令，要由协议明确；本书的完整 CRLF 契约下，它是不完整行。

如果你另行准备了匹配的 .NET 6 运行时、依赖和证书，可以在隔离环境观察旧例。它的原始端口为 Echo 5006、Telnet 5000，但 Echo 还经过 TLS 检测，完整宿主还有其他监听与证书前提。本书没有把旧例在当前只有 10/11 运行时的环境中成功运行作为验证结果，也不要求为了本实验修改旧项目。

## 6.7 实验二：用新网关验证同一种流式规律

终端 A 以仓库根为起点，先进入 `samples` 再启动服务。SDK 配置按当前工作目录向上查找，不按 `--project` 目标查找；这样才能使用 [samples/global.json](../../../samples/global.json) 选择 .NET 10 正式版 SDK。

```bash
# 终端 A：cd 后当前工作目录为 samples
cd samples
dotnet run --project IndustrialGateway/IndustrialGateway.csproj --no-launch-profile
```

然后在终端 B 执行以下两条命令，各建立一条独立连接。终端 B 保持在仓库根，因此 Python 路径仍以 `samples/` 开头；终端 A 的 `cd` 不会改变终端 B 的目录。如果你也将终端 B 切到 `samples`，则应去掉这两条路径中的 `samples/` 前缀。

```bash
# 终端 B：当前工作目录为仓库根
python3 samples/IndustrialGateway/simulator.py --count 3 --split
python3 samples/IndustrialGateway/simulator.py --count 3 --coalesce
```

[模拟器](../../../samples/IndustrialGateway/simulator.py) 的 split 模式逐字节写入并短暂停顿，coalesce 模式将多帧交给一次 sendall。每次预期得到 sequence 为 1、2、3 的 Ack，输出明确写 `enqueued in volatile memory`。这些写入方式不能保证 TCP 包边界，却能验证接收器没有依赖发送调用边界；多段内存的精确组合则由 [Checks 项目](../../../samples/IndustrialGateway.Checks/IndustrialGateway.Checks.csproj) 检查。

将值改为 `--value -1250`，表示 -1.250 个业务单位；它检验有符号数值编码，不能拿 Ack 证明温度合理或单位正确。DeviceId 是自报字段，实验不提供设备认证。别把用户名式字段与安全身份等同。

比较三个协议：旧 Echo 靠两字节长度，旧行协议靠 CRLF，新 KG/1 靠 16 字节头与类型约束。网络规律相同：不足就等待，足够就消费，有余就继续，EOF 检查残留。新实现还加入长度校验、默认 5 秒整帧预算、容量 128 的 Wait 队列、64 连接准入，并只在入队后发送相同 sequence 的 Ack。

## 6.8 常见错误、练习与交付标准

**错误：发送 `hello\n` 后没有响应，就认为服务器卡死。** 在 CRLF 协议里缺少 `\r`；应先观察十六进制，而不是盲目增加超时。终端工具在不同平台如何转换回车，也应以实际发送字节为准。

**错误：给 Echo 发送一个 0 长帧，就认为测试了空行。** Echo 的零正文帧和行协议的 CRLF 是两种不同格式。测试数据必须与端点使用的协议匹配。

**练习一：UTF-8 文本“温度”前写 `00 02` 会怎样？** 参考答案：接收端只按 2 字节正文消费，可能得到不完整 UTF-8，剩余 4 字节又被当成后续帧头，整个流可能失步。应写字节长度 6。

**练习二：一批缓冲含 `a\r\nb\r\n` 且 EOF，原行循环能保证处理两行吗？** 参考答案：不能，它一轮只解析一行后见 IsCompleted 就退出。稳健读取循环要排完完整行，最后再判断残留。

**练习三：为何 KG/1 不沿用 Echo 的任意正文？** 参考答案：工业消息要明确设备、时间、值、单位及响应关联，任意字节回显没有这些业务契约。固定 Telemetry 结构也让上限和测试更明确。

**本章交付标准：** 能手写 Echo 与 KG/1 的头；能解释欢迎语、请求与响应的顺序；能根据源码判断空行、bye 和普通文本的分支；能指出旧实现的长度、EOF 与资源边界；能在新实验中完成 split/coalesce 两种发送并核对 Ack。到这里，你已经具备继续学习缓冲、解析器、反压和工业网关设计的共同语言。返回 [全书入口](../README.md) 选择后续篇章。
