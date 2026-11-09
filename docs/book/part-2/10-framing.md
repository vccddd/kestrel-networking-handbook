# 第 10 章：从字节流增量解析 KG/1 帧

设备一次 send，不等于网关一次 read。TCP 可以合并相邻发送，也可以把一条应用帧分成多个读取结果；TLS、代理和缓冲还会改变交付形状。我们平常说的“粘包”和“拆包”，是应用协议在字节流上识别边界的任务，不是 TCP 把消息弄乱了。正确解析器应只依赖报文字节和协议约定，不依赖某次读取的长度。

本章独立定义 KG/1 的布局，再沿着“完整、尚不足、非法”三种结果构建解析思路。这样既能读懂新实验，也能判断旧示例在哪些边界上不能直接用于设备网关。

## 10.1 先写清楚报文，而不是先写循环

KG/1 的所有多字节整数均采用大端。头部固定 16 字节，序号是无符号 64 位整数，不能缩窄成 int。

| 头部偏移 | 长度 | 字段 | 规则 |
| --- | ---: | --- | --- |
| 0 | 2 | magic | uint16，0x4B47，即 ASCII KG |
| 2 | 1 | version | 1 |
| 3 | 1 | type | 1 为 Telemetry，2 为 Ack |
| 4 | 4 | payloadLength | uint32，仅负载长度，不含头 |
| 8 | 8 | sequence | uint64，请求与 ACK 使用相同序号 |

Telemetry 负载严格为 16 字节：偏移 0 是 uint32 DeviceId，偏移 4 是 int64 UnixTimeMilliseconds，偏移 12 是 int32 ValueMilliUnits。负值可表达零点以下的温度等测量；数值单位由设备业务定义，整数千分单位避免直接把浮点本机布局作为协议。Ack 的负载长度严格为零。协议总体允许的声明负载上限是 4096，但 Telemetry 不能因为低于该上限就接受任意长度。

网关输入端只接受 Telemetry；设备输入端解析 Ack。类型存在于协议，并不意味着每一端每一种状态都应接受它。收到错误魔数、版本、方向上的非法类型或不合法长度，应在头部完整后立即拒绝，不能先照声明长度分配数组，或者一直等到攻击者声称的负载全部到齐。

## 10.2 解析结果有三个含义

“还没到齐”和“不是合法报文”必须区分。只有 6 字节头时，可能还需等待；完整头声明 Telemetry 长度为 15，则已经确定非法，等更多字节也不会变合法。新实验的 [FrameCodec](../../../samples/IndustrialGateway/Protocol.cs)用以下契约表达三态：

```csharp
bool TryReadTelemetry(
    ref ReadOnlySequence<byte> buffer,
    out Telemetry measurement);
```

返回 true 表示恰好取出一帧，measurement 是独立字段值，buffer 已切到下一帧起点；返回 false 表示不足，buffer 保持原样；非法则抛 InvalidDataException，同样不提交 buffer 的切片变化。这里所谓“不提交”只针对解析输入游标，不代表业务操作可以事务回滚。入队、持久化和 ACK 都在后续协调层处理。

实现时先用局部 SequenceReader 试读，在全部验证通过后才修改 ref buffer。即使已经读完头但负载只有 7 字节，也只需丢弃局部读取器，外部仍保存完整的半帧。这比先消费头、然后另存“还差多少负载”的状态更容易教学和测试。代价是等到下一批时会再次检查小头部；在固定 32 字节测量场景，这个成本通常比复杂状态更容易接受。

## 10.3 多段输入不需要复制整个缓冲

ReadOnlySequence 可以由多个段组成，第一段甚至可能很短或为空。`buffer.FirstSpan` 无法保证包含完整头；`buffer.ToArray()` 可以把所有段拼起来，却会复制全部已有数据，面对大批次会反复付出成本。仓库 [RedisRequest](../../../KestrelApp.Middleware/Redis/RedisRequest.cs)在多段时就是先 ToArray，适合用来认识从连续解析迁移到多段解析的机会，不能把它称为没有分配的解析器。

SequenceReader 可以跨段推进、试读整数、查找分隔符，仍然是同步 ref struct，不跨 await。新 KG/1 实验采取更易核对的办法：把固定小头和固定小负载分别复制到栈上，然后 BinaryPrimitives 解字段。下面是从源码提炼的头部步骤：

```csharp
if (buffer.Length < 16)
    return false;

var reader = new SequenceReader<byte>(buffer);
Span<byte> header = stackalloc byte[16];
if (!reader.TryCopyTo(header))
    return false;
reader.Advance(16); // TryCopyTo 只复制，不自动前进。

uint payloadLength = BinaryPrimitives.ReadUInt32BigEndian(header[4..]);
if (payloadLength > 4096 || payloadLength != 16)
    throw new InvalidDataException("非法 Telemetry 长度。");
if (reader.Remaining < payloadLength)
    return false;
```

片段省略了源码中更早执行的魔数、版本、类型验证，不可单独当完整解析器。实际 [Protocol 实现](../../../samples/IndustrialGateway/Protocol.cs)包含这些检查和负载解码。`TryCopyTo` 跨段复制但不推进，因此必须 Advance；Advance 也只移动局部读取器，不等于 PipeReader.AdvanceTo。最后 `buffer = buffer.Slice(reader.Position)` 才提交一帧的解析进度，读取循环再报告管道消费进度。

这里每帧只复制固定的 32 字节，不复制整个批次，不创建托管 byte[]，也没有把 Span 或 Sequence 放进异步状态。也可以用 TryReadBigEndian 逐字段读取以减少小复制，但常见扩展重载接收 short/int/long，处理无符号字段时要显式保持位模式、验证范围，不要误把高位为 1 当成负长度。两种方式应根据清晰度和测量选择，不能用“零复制”标签代替正确性。

## 10.4 整数验证顺序决定解析边界

payloadLength 是 uint32。若先强制转换成 int，0xFFFFFFFF 可能变成负数，随后长度比较和切片就偏离协议。应该先按无符号值与上限比较，再做任何窄化、分配、加法和等待。计算总长时可以用 long 或 checked 运算，避免“头长加负载长”溢出。KG/1 Telemetry 最终长度固定 32，更应在完整头到达后直接验证负载等于 16。

仓库 [EchoConnectionHandler](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs)写长度用 ushort，读时却 `TryReadBigEndian(out short length)`。当最高位为 1 时，读到负数；`Remaining >= length` 反而可能成立，然后负数进入租赁或 Slice。这不仅是性能问题，而是编码与解析类型不一致。合法范围应由协议定义，读写端统一为无符号语义，任何转换都在验证之后发生。

以分隔符为边界也有类似问题。RESP 的数字行尚未出现 LF 时，应报告不足而非立即非法；出现 LF 后还应检查前面的 CR、数字格式、长度范围和尾部 CRLF。[RedisRequest](../../../KestrelApp.Middleware/Redis/RedisRequest.cs)把 IndexOf 返回 -1 加一后变成 0，在部分情况下直接判错；读取某个参数前还可能访问空 span 的第零项。它接受零元素数组后又访问 values[0]，也没有完整验证负载结尾。这些源码展示了“正常输入能跑”与“增量协议解析完整”之间的差距，不能将所有失败都当作断线即可忽略。

## 10.5 解析正确还需要读取循环配合

一批包含 A、B 和半个 C 时，循环反复 TryRead，成功后 buffer 缩短，直到 C 返回 false。此时 AdvanceTo(C 起点, 当前末尾)，既保留 C 又等待更多字节。若一次只处理 A，应该把 consumed 和 examined 都停在 A 末尾，让 B 有机会在下一次读取立即返回。不能对一个 ReadResult 每解析一帧就 AdvanceTo；同一读取结果只推进一次。

IsCompleted 为真时仍要把所有完整帧解析完。剩下非空半帧应报告截断；剩下为空才是该方向的正常 EOF。如果生产端可半关闭写方向，网关能否继续发 ACK 由连接生命周期策略决定，不能在看到输入 EOF 时无条件把还未发送的响应丢弃。

为避免借用越界，Telemetry 只保存 sequence、DeviceId、时间和测量整数；它不保存原始 Sequence。若确需向后续业务转交原负载，必须在 AdvanceTo 之前制作受上限限制的独立副本，或使用另行租用并明确移交的 owner。解析函数返回值“看起来像对象”不代表它已拥有内存。

## 10.6 切分测试比一次正常发送更有价值

对一条 32 字节帧，遍历 0 到 32 的切分位置：先给前半，除完整帧外应返回 false 且不改输入，再补后半，应得到相同测量。进一步把头部和负载放入不同 ReadOnlySequence 段，甚至每个字节一段，结果也应一致。TCP 的两次 send 不保证接收端两段，因此多段单元检查与真实 Socket 检查应分开。

再准备两帧连发、完整帧加半帧、最后批次含多帧、错误魔数、错误版本、错误类型、负载 15/17/4097/uint.MaxValue、高位为 1 的序号和负测量。检查不仅是“不崩溃”，还包括失败时输入是否不变、成功是否只消费一帧、非法声明是否在完整头时立即拒绝。新实验的可执行检查由 [IndustrialGateway.Checks](../../../samples/IndustrialGateway.Checks/IndustrialGateway.Checks.csproj)提供，不能把抓包的一次成功视为边界验证完成。

## 10.7 动手练习与参考答案

练习一：已有完整头，声明长度 4097，但当前负载为空，应返回 false 还是抛错？参考答案：抛协议错误，上限已被违反，无需等待或分配。

练习二：TryCopyTo(header) 成功后直接 TryCopyTo(payload)，会发生什么？参考答案：复制仍从当前头部位置开始；TryCopyTo 不推进。必须明确 Advance(16)，并检查 Remaining。

练习三：设备发来两帧，服务端读取结果 IsCompleted=true，while 中第一次成功后是否可以直接 break？参考答案：不可以。应继续解析当前剩余全部完整帧，再区分空尾部与截断尾部。

练习四：为什么在失败返回前不应修改 ref buffer？参考答案：无状态增量解析约定让调用方保留整个半帧，下一次从同一帧起点重试；若提前丢头而不保存状态，后续会把负载误认成新头。若选择状态机方案，则必须显式保存头部验证结果和剩余长度，并单独测试取消、EOF 与重置。

关于多段序列、借用和读取推进，可查 [Pipelines 官方指南](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)；整数试读重载见 [SequenceReaderExtensions.TryReadBigEndian](https://learn.microsoft.com/en-us/dotnet/api/system.buffers.sequencereaderextensions.tryreadbigendian?view=net-10.0)。
