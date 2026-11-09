# 附录 D：可复现验证记录

本附录记录实际执行过的检查，区分确定性解析、真实socket、文档结构和人工审阅。它不是性能认证或现场设备兼容报告。检查日期为2026年11月9日，时区Asia/Shanghai。

## D.1 基准与环境

- 原项目源码基准：`ba06920608a416fd285c24538a87ad379c1bbd1b`。
- 操作系统：macOS，Darwin，osx-arm64。
- 稳定SDK：10.0.403；共享运行时Microsoft.NETCore.App / Microsoft.AspNetCore.App 10.0.14。
- 同机还有.NET 11 RC；仓库根执行dotnet实际选择11 RC，进入samples执行则选择10.0.403。
- Python：运行系统提供的Python 3，所有脚本只使用标准库。
- 原解决方案为net6/net7，本次没有安装旧运行时，也没有运行旧宿主或Fiddler。
- 新教学实现为net10.0，无第三方PackageReference，不依赖旧项目。

[SDK配置](../../../samples/global.json)位于samples，搜索从dotnet进程的当前工作目录向上进行。复现实验时遵循以下工作目录，不能只把--project指向子目录就假定配置生效。

## D.2 确定性检查：通过162项断言

从仓库根：

```bash
cd samples
dotnet run --project IndustrialGateway.Checks/IndustrialGateway.Checks.csproj -c Release
```

[检查程序](../../../samples/IndustrialGateway.Checks/Program.cs)实际输出：

```text
PASS 162 assertions: incomplete, multi-segment, incremental, coalesced, invalid, EOF, cancellation, ACK, bounded queue, metrics.
```

覆盖范围：

| 条件 | 验证的性质 |
|---|---|
| 合法32字节帧的每个不完整长度 | 不足时返回false，调用者输入不推进 |
| 每个双段切分点、逐字节多段及空首段 | 跨内存段仍解出相同字段 |
| 两帧在同一Sequence内 | 一次成功只切掉一帧，后续仍可解析 |
| 错误magic/version/type | 头校验失败，不进入业务处理 |
| 0、15、17、4096、4097、uint最大值长度 | 非法类型长度从头即可拒绝，不等待声称的正文 |
| 完成输入携带两帧、截断EOF | 完整帧全部取出，剩半帧报告错误 |
| ACK最大序号编码 | 字段位置、零负载和无符号序号保持一致 |
| 容量1队列，无消费者 | 第二次写入等待，取消后队列仍有界 |
| 实际Pipe分次写入，并用推进握手控制补写 | 先确认半帧已发生Read和Advance，再补写；检查不足时start/end、成功时consumed=examined及读/推进次数 |
| CancelPendingRead与token取消 | 两条取消路径都能终止；之后可再次读，未残留未结束借用 |
| 直接将活动状态计数设为1后启动MeterListener | ObservableGauge仍报告当前1，结束状态后报告0；此项是指标状态测试，不创建真实socket |

本程序是轻量控制台检查，不是测试框架自动发现的测试套件。失败会抛出异常，以非零退出码结束。断言条数只用于标识这次结果，不等于所有分支或组合都已覆盖。

## D.3 真实连接与过载验证：全部通过

从仓库根：

```bash
cd samples
python3 IndustrialGateway/verify.py
```

[验证脚本](../../../samples/IndustrialGateway/verify.py)内部在samples工作目录构建Release程序，选择临时loopback端口并启动独立进程。最终构建结果为0 warnings、0 errors，脚本实际输出的结果组为：

```text
PASS fragmented writes, coalesced frames, EOF with buffered data
PASS invalid magic/version/type/length and truncated EOF close connections
PASS server remains available after protocol errors
PASS absolute frame budget rejects slow trickle
PASS connection admission limit
PASS bounded queue propagates slow-consumer backpressure to ACK
PASS full queue wait is canceled by frame budget
PASS all loopback integration checks; temporary processes stopped
```

检查使用真实socket，覆盖设备分次发送、同次多帧、关闭发送方向后读ACK、错误连接隔离、持续滴流但超出绝对预算、两个名额占满后拒绝第三条连接、慢消费者引起ACK延迟，以及满队列等待被预算取消。

为了缩短回归时间，脚本通过参数使用1—3秒等测试预算与容量1队列；这不是默认5秒/128队列的改动。它不是负载基准，不输出QPS、吞吐评级或跨机延迟分布。默认手动实验参数以[样例说明](../../../samples/IndustrialGateway/README.md)为准。

脚本负责结束自己创建的进程，启动日志写临时文件以避免未读取的stdout阻塞。它不修改系统代理、不安装证书、不访问现场设备。

## D.4 文档结构与审校

从仓库根执行[结构检查](../../../scripts/check_book.py)：

```bash
python3 scripts/check_book.py
git diff --check
```

结构检查覆盖27书页、全书目录唯一收录、现有相对文件及标题片段链接、代码围栏、尾空格、占位符和SDK命令约束。新增文件也会被扫描，不仅依赖Git已跟踪差异。外部网页由撰写与技术核查选择性抓取，未声称全面外链爬取；GitHub个别blob URL曾返回504，但固定版本raw源码可读取，不能把暂时网络错误当成路径不存在。

独立技术审阅覆盖21章与新样例，核对连接handler复用、Pipe游标、FIN通知、取消、队列、ACK和指标契约；重点对照.NET 10固定标签源码。独立初学者审阅仅凭书籍正文回答八个问题：消息边界、管道方向、单帧游标、Memory所有权、Flush与ACK、背压资源、幂等和版本运行。

审阅发现并修正了：根目录启动会选错SDK、业务帧/TCP段/内存段未直接对照、Task与DI前置解释不足、切片练习无完整宿主、旧Redis选做环境引用不准确、ACK与消费的顺序表述过强、64名额的限制范围不清、晚附加指标的当前数错误。读者测试的价值是发现能运行却未必能学懂的地方。

### 完整教学片段的编译检查

新增的练习宿主也实际编译执行过。从仓库根运行：

```bash
python3 scripts/verify_book_examples.py
```

[片段验证脚本](../../../scripts/verify_book_examples.py)直接抽取正文代码块，在临时目录创建net10控制台，dotnet进程仍从samples工作目录启动。它验证第7章切片输出99/2、第20章完整Pipe程序输出长度2/等待True/010203，以及把四个旧Redis解析源文件直接编入net10离线宿主后能解析PING。脚本以真实路径替换临时目录中的相对引用，不改原文件；结束后移除临时工作区。这三组检查全部通过，没有创建读者练习的MemoryScratch或NetworkScratch目录。

该检查证明正文的完整入口与必要引用可运行，不代表已修复旧Redis解析器；畸形和截断输入仍是练习要研究的问题。部分说明片段和伪代码故意省略上下文，不在此编译范围。

## D.5 首次失败与修复依据

首次socket验证在“先发送多帧、再关闭发送方向、继续读取ACK”失败。内存解析检查此前已通过，说明问题位于连接生命周期集成。原实现把ConnectionClosed链接到整个帧处理，接收FIN后取消了剩余读取与ACK。

检查[.NET 10 SocketConnection源码](https://github.com/dotnet/aspnetcore/blob/v10.0.0/src/Servers/Kestrel/Transport.Sockets/src/Internal/SocketConnection.cs)后，改为读取管道EOF/错误并排完完整帧，主机停止主动取消，帧预算限制其他等待；修复后socket验证通过。这个记录解释了测试为什么存在，防止未来“简化代码”时重新引入同类丢帧。

指标修正来自另一条失败推理：同步增减事件无法为晚接入监控恢复过去的+1。改为独立状态+ObservableGauge，再补了真实MeterListener迟订阅检查。该检查验证的是状态仪器语义，未证明外部监控系统配置正确。

## D.6 未验证与下一阶段

本次没有覆盖：真实设备、跨主机和弱网链路、TLS/mTLS或证书撤销、数据库/WAL耐久性、重启重放/幂等、进程强杀恢复、长期高并发性能、Windows/Linux兼容、内核及容器资源上限、代理安全部署，以及在线GitBook导入渲染。

取消和错误也尚未穷举所有组合，例如输出Flush长时间阻塞、transport RST与停机竞争、后台消费者异常、管道Complete抛异常。现有代码提供收尾结构与实验边界，不代表这些组合已被实测证明。扩展需求时应按承诺补测试；不要为了增加“测试通过”数字去写只复述实现的断言。
