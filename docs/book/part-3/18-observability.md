# 第十八章　可观测性：用证据回答网关为什么变慢

当设备说“没有收到 ACK”，网关日志说“已经处理”，健康接口说“ok”，三方可能都没有撒谎，只是在描述不同阶段。本章教你为阶段命名、选择指标、建立排查顺序，并用本机模拟实验核对推论。先区分已有实现和建议：样例已有少量 Meter 指标、结构化连接日志与 `/health`；仪表盘、指标导出、分布式追踪、生产告警和耐久对账均属于后续生产蓝图。

## 18.1 先给“成功”和“延迟”加一个终点

接收字节数不能代表遥测条数，入队条数不能代表数据库提交数，Flush 完成不能代表设备收到 ACK。指标如果省掉阶段，数值越精确，越可能让人误判。对 KG/1 基线，最有用的几个阶段是帧解码、入队、消费者处理、ACK 写入尝试与客户端校验 ACK。

从服务端能直接观察本地阶段，从客户端能确认它是否读到了匹配的 ACK，但二者不能凭空证明某个生产数据库发生过提交。当前 [TelemetryQueue](../../../samples/IndustrialGateway/TelemetryQueue.cs) 的消费者只是日志工作，`gateway.processed` 表示这段示范消费路径完成，不能写成“持久化条数”。Debug 日志被过滤时，消费者仍可能增加 processed；没有逐条日志不代表没有消费。

延迟同样要拆开。帧从第一个字节到完整解码的时间、等待入队时间、队列驻留时间、消费者工作时间和 ACK 写入时间分别受不同因素影响。基线的每帧预算覆盖读取、入队和 Flush，但没有分别导出这些直方图。生产蓝图应增加阶段耗时直方图，观察 p50、p95、p99 及超时数量；只看平均值会掩盖少量设备一直等待的尾部问题。

## 18.2 旧 FlowAnalyze 具体统计了什么

[FlowAnalyzer](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzer.cs) 维护读写两个队列，记录每次回调的长度和 `Environment.TickCount64`。速率是最近五秒内记录的字节和除以五，不是上一次调用之间的瞬时速率，也不是从连接开始到现在的平均速率。刚启动时即使只观察一秒，也仍除以五，图形会出现起始稀释。

总量用 `Interlocked.Add` 增加，队列使用并发结构，但查询与新回调并不是一个严格的全局原子快照。并发下不要要求总量、窗口和读写方向在同一纳秒完全对应。其每次流回调会建立一个记录，五秒窗口内记录数与调用次数相关；高频小读写会带来队列与求和成本，不能仅以五秒窗口断言内存开销恒定。

[FlowAnalyzeStream](../../../KestrelApp.Middleware/FlowAnalyze/FlowAnalyzeStream.cs) 在读之后统计真实返回长度，在写之前统计请求长度。写失败仍会增加总写字节，这应命名为对应包装层的写入尝试量。[注册](../../../KestrelApp.Middleware/FlowAnalyze/ServiceCollectionExtensions.cs) 是单例，所以聚合所有使用该实例的包装器；[安装扩展](../../../KestrelApp.Middleware/FlowAnalyze/ListenOptionsExtensions.cs) 才把它接入具体监听链。只调用 Add 并不会自动监视所有连接。

若在 TLS 外面统计，测量可能包含 TLS 记录开销；在解密之后统计则接近应用字节。工业运维中常见的“网卡流量与业务流量不一致”不必立即怀疑丢包，先确认观测位置、重传、协议头和采样窗口。第十三章讨论的包装顺序在这里直接影响指标语义。

## 18.3 读懂新样例的六个指标

[GatewayMetrics](../../../samples/IndustrialGateway/TelemetryQueue.cs) 创建名为 `IndustrialGateway` 的 Meter，包含以下仪器：

| 名称 | 类型 | 当前实现含义 |
| --- | --- | --- |
| `gateway.enqueued` | Counter | 成功进入易失 Channel 的记录 |
| `gateway.processed` | Counter | 日志消费者完成示范处理的记录 |
| `gateway.protocol_errors` | Counter | 因协议错误结束的连接路径 |
| `gateway.timeouts` | Counter | 帧总预算超时的连接路径 |
| `gateway.rejected` | Counter | 被应用连接配额拒绝的连接 |
| `gateway.active` | ObservableGauge | 当前获准进入协议处理的活动连接数 |

这里的同步 Counter 在事件发生时报告增量，由采集端按自己的观察窗口聚合；晚接入不会回放此前历史。进程重启或采集窗口变化后，监控平台应正确处理重置及窗口边界，再计算速率，不能把当前显示值直接叫作进程启动以来总量。Active 则是当前状态值：`ConnectionOpened`、`ConnectionEnded` 用 `Interlocked` 维护独立计数，ObservableGauge 在采集时通过 `Volatile.Read` 读取它。因此诊断工具即使晚于连接建立才附加，也能读到当前活动数，不需要看到此前的每一次增减事件。

选择 Gauge 的原因与问题有关：我们要回答“现在有几个连接”，只对附加之后的 `+1/-1` 事件求和会漏掉已有连接，甚至在已有连接关闭时得到误导结果。当前值应由服务自身维护，并在每次观测时报告。被拒绝的连接未进入 active，它也不覆盖 HTTP 健康端点、操作系统接受队列或处理器之前的瞬时资源，不能把该指标当成整个进程所有 TCP 状态的总和。

`enqueued - processed` 不能严格当作 Channel Count：消费者可以已经取出一条、尚未增加 processed，此时差额还包含在处理中的项；并发快照和重启也会影响比较。健康接口的 queued 是 Channel 当时的排队数，不包含已经取出的工作，更不包含 TCP 缓冲中的帧。

这些 Meter 仪器存在于进程内，不等于已经有 Prometheus 端点或 OpenTelemetry 导出器。可以在准备好的开发环境中安装兼容版本的诊断工具并观察 Meter，例如执行 `dotnet-counters monitor --process-id <PID> --counters IndustrialGateway`。工具版本与运行时兼容性需自己确认。若没有工具，仍可通过健康接口、测试输出和明确日志完成本章基础实验；本书不假设仓库已部署完整监控平台。

## 18.4 结构化日志与低基数标签

[连接处理器](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs) 记录连接标识和稳定原因，如 `frame_budget_exceeded`、`invalid_magic`、`truncated_frame`。稳定错误码让你能够聚合原因，不必从自然语言里猜异常类别。生产蓝图可在日志中加入部署版本、认证设备身份、协议版本及必要序号；不要记录密钥、认证头和整帧敏感载荷。

日志字段与指标标签的选择不同。设备编号、序号、连接 ID 适合有访问权限和保留策略的检索日志，直接作为指标标签则可能产生巨大时间序列数量。指标标签宜使用有界集合，例如消息类型、处理阶段、错误类别。按单个设备定位问题时，从聚合指标跳到日志或受控追踪。

追踪也不能自动跨自定义 TCP 协议传播。若要用 Activity 串起接收、存储和上行，需要定义本地父子关系及协议中的关联方式，限制采样和不可信关联字段。KG/1 没有 trace ID，不应在文档里展示已经存在的端到端链路图。对持续高频遥测，阶段指标加按需采样日志，往往比每条全部追踪更易控制成本。

旧 [HTTP 分析器](../../../KestrelApp.Fiddler/HttpAnalyzers/LoggingHttpAnalyzer.cs) 抓取整个请求响应是教学工具，生产观测不宜沿用默认全量模式。正文缓冲会改变延迟、占磁盘，还可能暴露密码与个人信息。必要的内容采集应有采样、尺寸上限、脱敏、访问控制和删除期限，不能把“便于排障”变成无限数据收集。

## 18.5 健康、就绪与业务正确性分开判断

当前 [健康接口](../../../samples/IndustrialGateway/Program.cs) 总是返回 status ok，并附带 queued 和 ACK 语义说明。它可以帮助确认 HTTP 服务还在响应，但没有检查设备认证、数据库、磁盘空间或持久日志，因为这些组件根本尚未实现。队列接近满时，接口仍可能返回 ok。

生产蓝图通常区分 liveness 与 readiness。前者判断进程是否陷入无法恢复的状态；后者判断是否应继续给它新业务。把短暂数据库抖动作为立即重启条件，可能导致所有实例反复重启并加剧故障。readiness 可结合积压、耐久存储可写性和停机状态，使用持续窗口与恢复阈值减少抖动。即使两个接口都健康，也仍需业务对账确认某个序号最终到了哪里。

排障顺序可以从外向内推进：客户端是否连到预期端口，连接是否被配额拒绝，帧是否被格式校验拒绝，是否到达绝对期限，是否进入队列，消费者是否追上，上行是否提交。每一步都找对应证据，避免看到 ACK 慢就直接扩线程。对于只发送一两个字节的设备，慢在帧未完成；对于队列满的设备，慢在消费能力；两者采取的措施完全不同。

## 18.6 动手：制造一个受控的慢消费者

先停止你自己启动的默认教学进程，保证 7810、7812 可用。从仓库根目录先进入 `samples`，再启动慢消费者实验：

```bash
cd samples
dotnet run --project IndustrialGateway/IndustrialGateway.csproj -- --Gateway:QueueCapacity=1 --Gateway:ConsumerDelayMilliseconds=900 --Gateway:FrameTimeoutMilliseconds=3000
```

SDK 配置查找取决于执行 `dotnet` 时的工作目录；`--project` 不会改变这个搜索起点。进入 `samples` 后会读取 [global.json](../../../samples/global.json) 的 .NET 10 约定，避免从仓库根直接运行时选到其他 SDK。若该终端已经位于 `samples`，直接执行第二行。

另一个终端保持工作目录为仓库根目录，执行：

```bash
python3 samples/IndustrialGateway/simulator.py --count 5 --coalesce
curl http://127.0.0.1:7812/health
```

消费者每条等待 900 毫秒，队列仅一个槽位。前几条可以因为消费者已取出一条、队列还能存一条而较快确认；继续发送后，ACK 会受到等待入队影响。这里的每帧期限是分别建立的，五条总时长超过三秒不必然超时，只要每一条的读取、入队与 Flush 都在自己的预算内完成。

需要可重复地验证“等待太久会断开”时，运行 [verify.py](../../../samples/IndustrialGateway/verify.py)，其中用独立进程设置更短预算和更慢消费者，并检查连接关闭。不要依赖手工恰好在一秒时查看 queued；并发运行时，某次快照错过峰值很正常。也不要把多次 send 写入当成必然的网络分片；确定性分段验证由 [Checks](../../../samples/IndustrialGateway.Checks/Program.cs) 完成。

## 18.7 练习与参考答案

练习一：五秒窗口中刚出现 1000 字节读流量，立刻查询旧分析器，速率是多少？参考答案：若没有其他有效记录，为 200 字节/秒。分母固定五秒，不是经过的那几毫秒。

练习二：enqueued 为 10、processed 为 8、queued 为 1，一定丢了一条吗？参考答案：不一定，消费者可能取出一条尚未完成，因此还有一条在处理中；快照也可能不在同一瞬间。当前没有持久化和对账，不能只凭这三个数证明端到端无丢失，但也不能立即推断丢失。

练习三：为什么不把 sequence 放到指标标签？参考答案：几乎每条消息都会创建新标签组合，时间序列数量随消息数增长。序号可用于受控日志或采样追踪，指标按有界错误类别与阶段聚合。

练习四：`/health` 为 ok，模拟器却超时，先收集哪些证据？参考答案：目标端口与服务实例、rejected、protocol_errors、timeouts、活动连接、队列快照、入队/消费速率、客户端 ACK 校验结果。health 只证明当前 HTTP 路径响应，不能证明遥测已经处理，更不能证明生产持久化。

练习五：慢消费者实验中总运行超过三秒却成功，是期限失效吗？参考答案：不是，每帧各有三秒预算；整个连接可处理多帧，持续时间可以更长。若业务需要连接总寿命上限，需要另行定义，基线没有给出该保证。
