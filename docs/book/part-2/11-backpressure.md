# 第 11 章：把慢消费者的压力传回设备

设备每秒生成一千条测量，数据库只能每秒处理六百条。无论内存池和 Span 多快，多出来的四百条都必须有去处：等待、丢弃、拒绝，或者进入容量明确且可靠性匹配的外部存储。若系统只是不断 Task.Run 或放入无界队列，它并没有解决吞吐差距，只是把失败延后到内存耗尽或延迟失控。

背压就是让下游的处理能力限制上游继续推进。它不创造吞吐，不保证数据不丢，也不等同于 TCP 流控；它是整个系统在容量有限时维持可预测行为的设计。本章使用 KG/1 网关说明管道、业务队列、连接数、时间预算和 ACK 如何一起工作。

## 11.1 Pipe 阈值控制等待，不是内存硬上限

自建 Pipe 的 `PauseWriterThreshold` 和 `ResumeWriterThreshold` 形成高低水位。写入并 Flush 后，积压达到暂停条件时，FlushAsync 可以保持未完成，等待读取端进度；读取推进让压力降到恢复条件后，writer 才继续。使用两个水位可以避免在同一点附近频繁暂停、恢复。

这里的等待依赖生产者真正 await Flush。忽略返回值、持续 GetMemory 和 Advance，会破坏预期的节流。一个 Flush 尚未完成时继续操作相同 writer 也不符合使用契约。reader 与 writer 应各自只有一个拥有者；需要多个业务生产者时，先通过受限队列汇聚到单个写循环，而不是让多个任务交错编码报文。

水位不能被解释为精确的进程内存上限。一次写入可以越过水位，底层按段或池桶分配，读端可能保留尚未消费的数据，输出、Socket、TLS 和队列都有额外存储。`examined` 参与读写唤醒的协调，而 `consumed` 表示哪些内存可以释放；把末尾标成 examined 不等于丢弃旧数据。规划容量时仍必须计算未消费数据和所有缓冲层。

这里还需纠正一个容易传播的推断：“完整帧大于暂停水位，就必然拆帧死锁。”在 [.NET 10 Pipe 源码](https://github.com/dotnet/runtime/blob/v10.0.0/src/libraries/System.IO.Pipelines/src/System/IO/Pipelines/Pipe.cs)的 AdvanceReader 中，背压计量随 examined 的前进更新，释放存储则依赖 consumed。正确报告 `AdvanceTo(start, end)` 可以让 writer 继续补齐半帧，即使尚未消费头部；代价是保留内存仍可能继续增长。因此水位不是消息长度保护，严格上限不能省略。真正的等待环包括未报告读取进度就等待生产者完成，或唯一 reader 要等 Flush 完成才启动。大负载仍宜逐块消费并保存状态，但不能用一个不准确的“水位必然死锁”解释代替实现依据。

## 11.2 Kestrel 裸 TCP 使用传输层设置

新实验在 [Program](../../../samples/IndustrialGateway/Program.cs)通过 `SocketTransportOptions.MaxReadBufferSize` 和 `MaxWriteBufferSize` 将输入、输出水位设置为 16 KiB。KG/1 声明负载上限为 4096，加 16 字节头仍小于该水位；当前输入只接受固定 32 字节的 Telemetry。这个量级为受限帧提供足够空间，但仍不是每连接总内存的精确保证。

不要用 HTTP 的请求体大小、请求缓冲配置推断裸 TCP 连接也受到同样约束。监听端点通过 UseConnectionHandler 执行自定义字节协议，应用必须自己验证 KG/1 长度、控制解析时间和业务队列。HTTP 健康端口与设备端口是两条不同的协议处理路径，健康检查返回成功也不代表测量已经落库。

压力传播的实际链路是：业务入队等待，连接处理器暂停继续读下一帧，输入缓冲逐渐积压，传输层减少继续接收，最终 TCP 接收窗口和发送缓冲使设备发送变慢。这个过程存在多级缓冲和时间差；“暂停 Read”不会立刻阻止设备再发送一个字节。因此设备也要限制自身发送窗口、未确认数量和重试缓冲。

## 11.3 有界 Channel 只是其中一段

[TelemetryQueue](../../../samples/IndustrialGateway/TelemetryQueue.cs)使用容量默认 128 的 `Channel<Telemetry>`，FullMode 为 Wait，单消费者、多生产者。入队在满时异步等待，取消可以中断等待。它保存解析后的四个数值字段，不持有 PipeReader 借用内存，也不会每条测量创建一份原始报文数组。

```csharp
Telemetry? value = await ReadOneAsync(input, budget.Token);
if (value is null)
    return;

await queue.EnqueueAsync(value.Value, budget.Token);
FrameCodec.WriteAck(output, value.Value.Sequence);
FlushResult result = await output.FlushAsync(budget.Token);
```

这是连接处理路径的局部示意，完整的异常与资源结束以 [GatewayConnectionHandler](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs)为准。ReadOneAsync 在返回值之前已经 AdvanceTo，后续排队等待不会保留这一帧的借用。一次只处理一帧，成功时 examined 与 consumed 都停在该帧末尾，因此合并到同一次读取中的下一帧不会被错误地标为“全部检查、等待新输入”。

错误写法是 `_ = queue.EnqueueAsync(value, token)`，或为每条测量创建 Task.Run 去等待入队。队列虽然仍是 128，队列外的等待任务却可以无限增长，背压失效。若采用 DropOldest、DropWrite 等丢弃策略，则必须把丢弃作为业务行为记录并告知设备，不能沿用“ACK 表示接受”的说法。工业报警和普通温度采样往往需要不同策略，不能只因吞吐测试好看就统一丢弃。

## 11.4 等待必须受时间和连接数量约束

如果所有连接都在等队列，内存仍可能被大量连接占用。实验额外设置默认 64 个活动连接的准入槽；无可用槽时拒绝新连接。队列有界约束消息数，连接准入约束正在持有状态和 I/O 资源的会话数，两者解决不同问题。

每帧采用同一个绝对 5 秒预算，覆盖读取完整帧、等待入队以及 ACK Flush。收到一个字节不重置预算，否则慢客户端可以每隔几秒发一个字节，永久占用连接。对于已经入队但 Flush 超时的消息，服务端不能简单撤回已被消费者拿走的数据；设备未收到 ACK 后可能重试，因此可靠去重是另一个独立需求。

预算也意味着吞吐降低时系统会有明确的超时拒绝，而不是无限等待。这是实验的策略，不是所有工厂都应该固定为 5 秒。远程蜂窝网络、批量补传和控制指令应根据延迟分布、设备缓存和业务期限设计。可调整配置必须做范围验证；将超时设为无限大或者随意增大队列，都可能掩盖长期吞吐不足。

输入停止与连接取消还有一个容易遗漏的边界：在 [.NET 10 SocketConnection](https://github.com/dotnet/aspnetcore/blob/v10.0.0/src/Servers/Kestrel/Transport.Sockets/src/Internal/SocketConnection.cs)中，接收方向遇到 FIN 后会进入 finally，完成输入 Pipe 并触发 ConnectionClosed 通知，而 Pipe 中仍可能有完整帧待处理。实验因此以管道 EOF/IO 错误结束读取，只把 ApplicationStopping 作为提前取消整个处理的信号，保持最后批次能完成解析与 ACK；不能把 ConnectionClosed 一触发就取消所有工作当作通用正确做法。FIN、RST、服务关闭和应用超时应分别记录和验证。

## 11.5 ACK 的位置定义业务承诺

KG/1 ACK 的序号与 Telemetry 相同，负载为空。当前实验先成功入易失内存队列，再编码 ACK 并 Flush。因此它只表示“进入本进程的易失队列”，不表示消费者已处理、数据库已提交、消息已复制，也不保证服务重启后保留。后台消费者以日志作为教学 sink，不能被描述成可靠存储。

如果业务需要持久确认，应把 ACK 的产生位置放到满足持久性要求之后。例如事务提交或可靠消息日志确认之后，再向设备确认；还必须定义设备标识和序号的唯一性、重复请求处理、重启后的去重状态、失败重试及存储满时行为。仅仅把 ACK 移后但不做幂等，仍可能因为“提交成功而 ACK 丢失”产生重复。

Flush 完成只代表当前 writer 的提交操作按其契约完成。TCP ACK 由传输层自动处理，KG/1 ACK 由应用生成，两者都不能被 Flush 的返回值替代。取消也不是回滚：发生超时前，部分数据可能已经发出或消息已经入队。日志应记录阶段和 sequence，帮助区分“未接受”“已接受但确认不明”“已持久化但设备未确认”等状态。

## 11.6 容量估计与观察

估算应包含活动连接乘以每连接输入、输出和解析状态，再加队列消息、消费者批次、任务、池保留和内核缓冲。不能只用 128×32 字节计算总内存，因为队列存的是结构值且有容器开销，连接和传输缓冲也远大于一帧。另一方面，也不要把 16 KiB 水位直接当作每连接固定分配量，它是流控配置，需要运行时观察实际占用。

至少同时观察当前连接数、拒绝数、队列深度、入队/处理速率、入队等待时间、帧超时和协议错误。队列长期接近满且入队速率高于处理速率，说明长期能力不匹配；短暂尖峰后回落则可能是合理吸收。健康检查的排队数量只是瞬时快照，不能单独说明系统没有积压。

## 11.7 动手练习与参考答案

先做一个受限容量实验：创建 QueueCapacity=1 的 TelemetryQueue，等待第一条入队，随后用可取消 token 尝试第二条。参考答案是第二次 ValueTask 在没有消费者时保持未完成，取消后抛 OperationCanceledException，队列数量仍为 1；读取第一条后再入队则能继续。现有检查程序已有这个场景，动手时重点观察等待位置，不要在主线程阻塞等待而让唯一消费者没有机会运行。

练习一：队列容量 128，但每次入队都 Task.Run，是否已经限制内存？参考答案：没有。待入队任务和它们捕获的消息构成另一个无界队列，应在接收路径直接 await，让压力传回连接。

练习二：设备收到 ACK 后服务崩溃，测量未写数据库，是否违反本实验语义？参考答案：没有违反“易失入队确认”，但若产品要求不丢则当前语义不足，必须实现可靠存储、重试和幂等，不能改一个日志说明来补足。

练习三：输入水位 1 KiB，解析器保留完整 8 KiB 帧才消费，是否能据此断言死锁或内存最多 1 KiB？参考答案：都不能。.NET 10 Pipe 可根据 examined 的前进恢复 writer，完整帧仍可能补齐；未消费的内存也可能超过水位。必须正确推进、限制消息长度，并检查有无循环等待；大负载可采用流式消费。

练习四：ConnectionClosed 通知到达，是否说明所有已接收字节都已处理？参考答案：不是。接收 EOF 与应用排空是不同进度；读取最终 Buffer 中完整帧，并检查半帧，才知道应用层是否结束。

管道水位和等待规则见 [Pipelines 官方指南](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)。生产设计还需以所使用版本的 Socket 传输实现和业务可靠性契约为依据。
