# 第 8 章：用 IBufferWriter 编码报文

假设网关要向设备回复一个 KG/1 ACK。ACK 只有 16 字节头，没有负载。最直接的写法是创建 16 字节数组、填写字段，再写入网络输出。每条消息创建一个数组容易理解，但序列化器若能直接填写输出组件提供的缓冲区，就可以少一份中间存储。`IBufferWriter<T>` 正是这类协作的接口：存储由 writer 管理，编码器只申请可写区域、写入并报告实际数量。

它不是网络接口，也没有“发送成功”的含义。内存数组 writer、分段 writer、PipeWriter 都可以实现它。理解这个边界，才能把编码函数写成可复用、可测试且不依赖 TCP 的小函数。

## 8.1 三个动作不能混为一谈

`GetSpan(sizeHint)` 和 `GetMemory(sizeHint)` 取得至少包含所请求数量的可写区域。`sizeHint` 不是写入数量；即使请求 16，得到的长度也可能是 256。`Advance(count)` 告诉 writer 前 count 个元素已经写好，将逻辑写入位置前移。`FlushAsync` 不属于 IBufferWriter，而属于 PipeWriter 等更具体的抽象，用来按实现约定发布或提交已经写入的数据。

取得区域后，只能在合法范围内写入，Advance 的数量不能超过所取得的区域。完成 Advance 后，要继续写必须重新 GetSpan/GetMemory，不能继续使用旧视图；下一次申请也不保证返回相同存储。不要先申请头、再申请负载，最后回到旧头视图补长度，因为第二次申请可能触发扩容或更换段，旧视图的有效性无法保证。已知固定头长度时，可以先算出负载长度，一次写好头，再按需写负载。

```csharp
static void WriteAck(IBufferWriter<byte> writer, ulong sequence)
{
    Span<byte> header = writer.GetSpan(16).Slice(0, 16);
    BinaryPrimitives.WriteUInt16BigEndian(header, 0x4B47);
    header[2] = 1; // version
    header[3] = 2; // Ack
    BinaryPrimitives.WriteUInt32BigEndian(header.Slice(4, 4), 0);
    BinaryPrimitives.WriteUInt64BigEndian(header.Slice(8, 8), sequence);
    writer.Advance(16);
}
```

这个同步片段需要 `System.Buffers` 和 `System.Buffers.Binary`。它填写全部 16 字节，不依赖池内存的初始值；不分配中间数组，也不调用 Flush。若调用方传入 PipeWriter，调用方决定何时 `await FlushAsync(token)`；若传入 ArrayBufferWriter，调用方检查 WrittenSpan 即可。正式实验里的编码接口以源码为准，这里用更一般的 IBufferWriter 展示编码职责。

典型反例是 `writer.Advance(header.Length)`。如果忘记 Slice，header.Length 可能远大于 16，等于把尚未初始化的容量声明成有效报文。另一个反例是写完 16 字节却漏 Advance：调试时内存里确实有字节，但 writer 的逻辑长度未更新，后续写入可以覆盖它，Flush 也没有这 16 字节可提交。

## 8.2 从仓库实现理解容量和已写长度

仓库的 [ArrayPoolBufferWriter](../../../KestrelFramework/System.Buffers/ArrayPoolBufferWriter.cs)用一个池数组保存全部内容。`index` 是已写入长度，`Capacity` 是数组容量，`FreeCapacity` 是两者之差。GetSpan 调用 CheckAndResizeBuffer，返回从 index 起的剩余区域；Advance 只增加 index。它没有网络连接，也没有 Flush。

扩容时，代码以当前容量和 sizeHint 中较大的值作为增量，checked 计算新容量，租新数组，把已写部分复制过去，再归还旧数组。池化减少反复创建新数组的机会，但没有消除扩容复制。大报文反复扩容仍然消耗内存带宽，租到的物理容量也可能超过逻辑需求。协议有明确上限时，应基于典型大小选择初始容量，并在调用层限制最大消息长度。

`WrittenSpan`、`WrittenMemory` 和 `WrittenSegment` 都引用当前数组，不是快照。扩容后旧数组可能被归还，Clear 后内容会变，Dispose 后也不可再用。因此以下生命周期是错误的：先取得 WrittenMemory，把 writer Dispose，再在后台发送该 Memory。正确顺序是保持 writer 有效直到消费结束，或在释放前复制成独立数组。需要跨组件长期保存时，也可以重新设计成明确转移 owner 的接口。

这份实现的 Dispose 有重复归还保护，但其他成员没有普遍检查 disposed，不能依赖它抛 ObjectDisposedException 来替调用方发现错误。Clear 清空已写范围并把 index 归零，不等于清空整个物理数组；扩容和 Dispose 的 Return 没有请求清零。这里描述的是现有源码行为，不应将它当成敏感数据清理的完整方案。

## 8.3 接口设计应让编码器只负责格式

[BufferWriterExtensions](../../../KestrelFramework/System.Buffers/BufferWriterExtensions.cs)中的 WriteBigEndian 重载体现了一个清楚模式：请求 `sizeof(T)`、用 BinaryPrimitives 写字段、Advance 相同数量。WriteCRLF 同理。若使用已经替你 Advance 的 Write 扩展，不要在外面再 Advance 一次，否则会跳过一段未写区域。

原文[封装与扩展](<../../../docs/2.2 高性能buffer--封装与扩展.md>)提到 RecyclableBufferWriter、ResizableBufferWriter、FixedBufferWriter，这是当时外部 MemoryExtensions 库的介绍，并非当前仓库都有这些类型。本仓库应以 ArrayPoolBufferWriter 和接口定义为阅读入口。旧文还说 MemoryStream 写一个整数“不得不先转换成 4 字节数组”，这也是旧 API 使用方式的限制，不是现代 .NET 的必然限制：可以先写入固定小 Span，再调用支持 Span 的 Stream.Write；也可以选择 IBufferWriter 来进一步让编码直接进入目标区域。

编码器接收 IBufferWriter 而不接收具体池类型，有两个实际收益。第一，单元检查可把输出留在内存，逐字节核对魔数、长度和序号。第二，相同编码器可以写入不同目的地，传输层再统一负责 Flush、超时、并发和资源结束。接口本身没有最大容量或线程安全保证，协议上限仍要由调用方守住，多个生产者也不能同时向同一个 writer 交错写头和负载。

## 8.4 文本编码也应明确字节数

字符串 Length 是 UTF-16 代码单元数量，不是 UTF-8 字节数。若以后为 KG/1 增加文本状态字段，不能把字符串字符数直接写到 payloadLength。小而受限的文本可先用 Encoding.UTF8.GetByteCount 计算字节数，再 GetSpan、GetBytes、Advance 实际写入字节数。大文本可以使用 Encoder 分段编码，避免一次请求巨大连续空间，但要正确处理跨段字符状态和最终 flush 参数。

仓库扩展中的文本 Write 使用 unsafe 指针转换。理解现有实现有助于了解历史优化方式，但新代码不必照搬；现代 Encoding 提供 Span 重载，通常能在更清楚的安全代码中完成同样任务。协议字段也不应先把数字转十进制字符串、再转 UTF-8，除非协议就是文本格式。KG/1 的序号是八字节无符号整数，直接大端写入即可。

所谓“直接写入”也需要限定：如果输入本来就是一份 byte[]，调用 Write 往 writer 填充通常仍然会复制。它省掉的是不必要的临时缓冲，而不是从业务对象到网卡之间的所有复制。TLS 加密、内核缓冲和设备驱动还有自己的处理，不能由 IBufferWriter 接口推出端到端零复制。

## 8.5 PipeWriter 的 Flush 是下一层责任

原文[PipeWriter](<../../../docs/2.3 高性能buffer--PipeWriter.md>)把所有 PipeWriter 统一描述为“向 MemoryPool→ArrayPool 租赁、Flush 写 Stream、随后归还数组”。这把一种 Stream 适配实现扩展成了抽象定义。实际要区分三种情况：内存 `Pipe` 的 writer 把数据发布给对应 reader；`PipeWriter.Create(stream)` 适配 Stream；Kestrel 连接的 Transport.Output 由服务器传输实现负责。具体存储是否为数组、何时回收，不能由抽象类名字确定。

`FlushAsync` 完成也不表示对端 TCP 已确认，更不表示设备业务已经执行。KG/1 的 ACK 帧要由服务端业务路径明确生成；实验约定它只确认测量进入易失队列。如果连接在 Flush 后断开，发送方仍可能不知道 ACK 是否收到，需要利用 sequence 和业务去重规则处理重试，不能用“Flush 没抛异常”推断整个业务链路成功。

Flush 挂起期间，不应继续获取或修改同一个 PipeWriter 的区域。调用方必须等待返回的 ValueTask，并检查 IsCanceled 和 IsCompleted。前者可由 CancelPendingFlush 引起；后者表示读取端已结束，不再继续写。CancellationToken 取消则通常经 OperationCanceledException 路径结束。每个返回的 ValueTask 通常只等待一次，不要保存后反复等待。

## 8.6 动手练习与参考答案

先验证本章编码片段：创建 `var writer = new ArrayBufferWriter<byte>()`，调用本章的 `WriteAck(writer, 1)`，再用 `Convert.ToHexString(writer.WrittenSpan)` 输出。参考答案为 `4B470102000000000000000000000001`，WrittenCount 应为 16。将初始容量改大，结果仍应只有这 16 字节；这个检查验证的是逻辑长度不随物理容量变化。它使用本章的一般化编码函数，正式 FrameCodec.WriteAck 则接收 PipeWriter。

练习一：GetSpan(16) 返回长度 256，编码器填写前 16 字节后 Advance(256)，为什么抓包多出 240 字节？参考答案：Advance 的单位是实际写入元素数，不是预留容量。应只 Advance(16)，并确保每个有效字节都已填写。

练习二：连续调用两次 WriteBigEndian，再手工 Advance(8)，是否正确？参考答案：仓库扩展内部已经推进，外部再次推进会制造未初始化间隙；用接口时应明确每个函数是否自行 Advance，不能混用两种约定。

练习三：要在头中写负载长度，能否先缓存 GetSpan(16) 返回值，写几次任意长度负载后再回填头？参考答案：通用 IBufferWriter 不保证这些调用保留原区域。已知长度时先计算长度再写；长度未知时可以先在有上限的独立 writer 中编码负载，或采用协议明确允许的分块格式。不能依赖某次测试中数组恰好没有扩容。

练习四：ACK 编码函数是否应在末尾调用 FlushAsync？参考答案：若函数的职责只是编码，保持同步并只 Advance，调用方可选择逐帧 Flush 或有限批量 Flush；若提供“编码并发送”API，则应明确它返回的任务、取消和结果检查责任。两者都可以设计，不能把发送语义偷偷塞进一个看似纯编码的方法。

进一步的契约以 [Microsoft Pipelines 指南](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)和 [Memory 使用指南](https://learn.microsoft.com/en-us/dotnet/standard/memory-and-spans/memory-t-usage-guidelines)为准。
