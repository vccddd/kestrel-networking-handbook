# 第 20 章：从读懂到独立实现的十二个练习

跟着示例运行，和自己面对陌生故障，是两种能力。这一章把“我觉得懂了”变成可检查的证据。练习只使用本机模拟设备；不要向真实产线注入畸形报文。除了特别注明，原示例保持原状，改动放到你自己的实验分支或新文件里。

建议准备一个实验记录：先写预测，再写观测，最后写原因。预测错误很有价值，因为它指出哪一层模型还不稳定。代码没有报错，不足以算完成；你必须说明它拒绝了什么、什么时候释放内存、失败后设备应怎么做。

## 代码练习工作区：先给代码一个运行入口

纸面推演不需要新项目。需要改服务器行为的题目，请在你自己的实验分支修改已经能启动的[网关处理器](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs)，再按[第19章](19-lab.md)启动；不要只把一个处理器文件复制到空目录，原注册不会因此改用副本。如果想完整隔离，应复制整个网关项目并保留其所有依赖文件，再使用独立端口启动。

小Pipe和离线解析练习则使用独立控制台，不必改网关入口。从仓库根执行一次（若同名目录已存在，先复用或另选名字，不要强制覆盖）：

```bash
cd samples
dotnet new console --framework net10.0 --output NetworkScratch
```

用编辑器把新建的 `NetworkScratch/NetworkScratch.csproj` 替换为下面配置。FrameworkReference提供Pipelines；ProjectReference只引用新net10实验，方便练习调用FrameCodec和ReadOneAsync，不引用旧net6宿主：

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <FrameworkReference Include="Microsoft.AspNetCore.App" />
    <ProjectReference Include="../IndustrialGateway/IndustrialGateway.csproj" />
  </ItemGroup>
</Project>
```

先用下面完整顶级程序替换新建的 `NetworkScratch/Program.cs`，验证练习宿主。它也完成[第9章](../part-2/09-pipelines.md)的基础Pipe观察：

```csharp
using System.Buffers;
using System.IO.Pipelines;

var pipe = new Pipe();
using var budget = new CancellationTokenSource(TimeSpan.FromSeconds(2));
try
{
    await pipe.Writer.WriteAsync(new byte[] { 1, 2 }, budget.Token);
    var first = await pipe.Reader.ReadAsync(budget.Token);
    Console.WriteLine($"第一次可读长度：{first.Buffer.Length}");
    pipe.Reader.AdvanceTo(first.Buffer.Start, first.Buffer.End);

    var pending = pipe.Reader.ReadAsync(budget.Token);
    Console.WriteLine($"等待更多输入：{!pending.IsCompleted}");
    await pipe.Writer.WriteAsync(new byte[] { 3 }, budget.Token);
    var second = await pending;
    Console.WriteLine(Convert.ToHexString(second.Buffer.ToArray()));
    pipe.Reader.AdvanceTo(second.Buffer.End);
}
finally
{
    await pipe.Writer.CompleteAsync();
    await pipe.Reader.CompleteAsync();
}
```

保持终端位于samples运行：

```bash
dotnet run --project NetworkScratch/NetworkScratch.csproj -c Release
```

预期依次为长度2、等待更多输入True、`010203`。前两字节被examined但未consumed，因此仍在下一结果中。后续题目把实验逻辑替换到这个Program；用 `using IndustrialGateway;` 访问新协议。第7章的MemoryScratch是更简单的内存练习宿主，没有配置Pipelines，二者不能在未补引用时直接互换。

练习9还需研究旧Redis解析器。可以在NetworkScratch的项目内额外加入以下编译链接，使四个原文件直接编入当前net10离线实验；原文件不会被复制或修改，内部类型在同一实验程序集内可访问：

```xml
<ItemGroup>
  <Compile Include="../../KestrelApp.Middleware/Redis/RedisRequest.cs" Link="Legacy/RedisRequest.cs" />
  <Compile Include="../../KestrelApp.Middleware/Redis/RedisValue.cs" Link="Legacy/RedisValue.cs" />
  <Compile Include="../../KestrelApp.Middleware/Redis/RedisCmd.cs" Link="Legacy/RedisCmd.cs" />
  <Compile Include="../../KestrelApp.Middleware/Redis/RedisProtocolException.cs" Link="Legacy/RedisProtocolException.cs" />
</ItemGroup>
```

此ItemGroup放在Project元素内部、已有ItemGroup之后。在Program中引入 `using KestrelApp.Middleware.Redis;`，即可用 `RedisRequest.Parse(new ReadOnlySequence<byte>(bytes), out var consumed)` 测离线输入。不启动旧Redis服务器，也不ProjectReference旧项目。修改解析算法时，应复制到你自己的实验代码并去掉对应Compile链接，避免练习误改原仓库文件。旧解析器可能产生空引用可空分析警告；题目要研究的是它对输入的实际行为，不能靠关闭运行时异常来“通过”。

## 练习 1：同一条消息，三种到达方式

把两条 32 字节上报记为 A 和 B。请画出下面三种输入中每次 Read 的剩余数据，以及 consumed/examined：

1. 一次得到 A+B；`ReadOneAsync` 只取 A。
2. 先得到 A 的前 10 字节，再得到 A 余部+B 前 7 字节。
3. 一次得到 A+B，并且 IsCompleted 为 true。

**验收**：不能用整数偏移代替真实 SequencePosition 编写生产代码，但可以用偏移画图；要说明下一次 Read 是否应该立刻返回。

**参考答案**：第一种成功后 consumed=examined=A 末尾，下次立即读 B。第二种首次不消费、examined 到当前末尾，等待更多；第二次成功后两游标只到 A 末尾，B 的 7 字节尚未被本次 parser 检查，下次取得后判断不足再等待。第三种先返回 A，下次继续取 B，最后才返回干净 EOF，不能因第一次看到 IsCompleted 就跳过 B。

## 练习 2：故意把 examined 写错

在你自己的实验分支修改[已注册的实验处理器](../../../samples/IndustrialGateway/GatewayConnectionHandler.cs)，按第19章重启网关；把成功一帧的 `examined = consumed` 改为 `examined = result.Buffer.End`。一次发送 A+B，不关闭写方向，也不再发送第三帧。

**预测**：A 得到 ACK 后，B 会发生什么？

**参考答案**：你声称已经检查过整批缓冲，下一次 Read 可能等待新输入；B 即使已经在内存中也不能及时处理，最后触发预算。发送 C 后似乎“恢复”的现象，会误导你以为网络在攒包。真正问题是游标契约。

**验收**：使用一个小 Pipe 测试重复该条件，用带超时的取消代替让测试永久挂住；恢复正确游标后测试应稳定通过。

## 练习 3：长度先校验，还是先分配

修改 Python 构帧，只发送头，令 PayloadLength 为 `0xFFFFFFFF`。比较以下设计：先 `new byte[length]`；先转 `int` 再相加；先以 uint 校验上限和类型，再等待负载。

**参考答案**：第一种可造成异常或极端分配，第二种可能发生有符号溢出，第三种从头即可拒绝而不等待大量数据。合法最大长度应该先在足够宽的类型中校验，再做受控计算。本例 Telemetry 的负载必须正好 16，而不仅是小于4096。

**验收**：错误头关闭的是该连接，进程仍能接收下一条正常消息；检查日志只有有限原因码，没有把任意负载全文写进去。

## 练习 4：把时间从“空闲计时”改成“帧预算”

让模拟器每 400ms 发 1 字节，完整帧需要约 12.8 秒。业务约定一帧最多占用 2 秒。请解释为什么“每读到一批就重置2秒”无法满足要求。

**参考答案**：这种重置只限制字节间空闲，不限制一帧从开始到完成的总时间。慢发送者只要持续送一个字节，就能一直占用连接。应从开始等一帧时建立绝对截止时间，所有补读共享预算；本实验还把入队和 Flush 纳入同一预算。这是教学策略，真实产品可拆分握手、空闲、组帧、业务和发送预算，但必须定义各自含义。

**验收**：用单调时钟测持续时间，不用设备传来的墙钟；记录超时发生在哪个阶段，而不是把一切都叫“网络超时”。

## 练习 5：找出内存逃逸

下面是**故意错误的伪代码**，不要部署：

```csharp
// 错：payload借用的是输入管道内存。
var payload = result.Buffer.Slice(headerLength, length);
input.AdvanceTo(payload.End);
await workQueue.EnqueueAsync(payload);
```

如果在最后一步入队的是 ReadOnlyMemory，情况是否改变？

**参考答案**：不改变。只读限制的是当前视图的写入能力，不会延长所有者的生命周期。Advance 后，底层存储可能归还池并重新使用。修正选择是：借用期间同步完成处理；复制需要的数据到自己的对象；或显式转移独立内存所有权。不能把一个指向借用数据的视图改名为“消息”就算拥有它。

**验收**：画出生产者与消费者之间的所有权边界，为每块池化内存指定唯一归还者，并说明取消和队列拒绝时由谁归还。

## 练习 6：计算业务积压

假设 1000 台设备，每台每秒上报 10 个值，数据库维护 20 秒；每条消息连同对象开销按 128 字节估算。生产速率是每秒10000条，若完全不消费，20秒积压约200000条，即25.6MB十进制估算，不包含管道、Socket、连接和索引。

**问题**：把队列设为200000是否就保证不丢？

**参考答案**：不保证。它只覆盖给定持续时间和平均速率下的估算，突发、恢复时消费能力、内存额外开销、进程崩溃都会改变结论。若恢复后消费速率只有生产速率，积压永远排不完。需要限流、耐久缓冲或明确丢弃策略，并测量 P99 等待时间与最高积压。对于必须保留的质量追溯事件，应设计持久化与上游重试，而不是赌一个大数组。

**验收**：同时给出正常、维护、恢复三个阶段的生产/消费速率，并画出队列随时间的斜率。

## 练习 7：重发与幂等的协议改造

网关把 sequence=42 的消息持久化了，但 ACK 在网络中丢失。设备重连并重发42。只用设备ID+序号作为唯一键有什么问题？

**参考答案**：设备重启、恢复备份或序号回绕可能让新数据复用旧序号；单用连接ID又会把重连后的重发当新消息。可以引入持久设备标识、启动/会话代号和序号组成幂等键，定义代号生成及保存方式，再由数据库唯一约束或原子事务保证“记录数据”和“记录已处理键”的一致性。允许重复接收与只产生一次业务效果是不同层次。

**验收**：写明重启、ACK丢失、重复并发到达、键保留期过期后的行为。不接受“加个 Redis Set”而没有解释它与业务落库事务关系的答案。

## 练习 8：两个任务同时写同一个 PipeWriter

一个任务定时发心跳，另一个任务发 ACK。两者分别调用 GetSpan/Advance/Flush。是否可以只对 Flush 加锁？

**参考答案**：不可以。编码、Advance、Flush 是同一条输出状态机的一部分，GetSpan 到提交之间也可能交错。可让所有响应进入一个有界发送队列，由单一写循环编码并发送；或者对整个异步发送操作使用合适的互斥。还须处理发送队列满、优先级、超时、取消和停机。普通 `lock` 内不能 await，不要用 `.Result` 绕过。

**验收**：增加ACK与心跳交错压力测试，按线协议逐帧解码，保证不存在头体串接错误；输出循环异常必须取消或通知生产者。

## 练习 9：给现有 Redis parser 写失败证据

阅读[RedisRequest.cs](../../../KestrelApp.Middleware/Redis/RedisRequest.cs)，把一个合法 RESP 命令在每一个字节位置截断；再测试 `*1\r\n`、零元素数组、负数bulk长度、缺失CRLF和很大元素计数。先写预测，再在本章开头配置好的NetworkScratch中运行离线解析测试，不向任何真实 Redis 实例发送畸形流量。

**参考答案**：parser 中存在直接访问 `span[0]`、不完整换行当异常等边界；这些不是 TCP 会自动修复的问题。修复时应先定义“不足/非法/成功”三态，临时游标成功才提交，设置数字、元素数、单体和总帧长上限，再看如何减少整段 ToArray。仅补一个 try/catch 不会让半包变成可重试解析。

**验收**：合法报文在所有截断点均返回不足而非越界；完整畸形报文快速失败，测试不得通过吞掉所有异常来蒙混过关。

## 练习 10：性能回归要测哪一段

比较 A：每条测量转成字符串再写日志；B：标量解析并关闭逐条日志。B快10倍，能否宣称 SequenceReader 比数组解析快10倍？

**参考答案**：不能。你同时改变了字符串分配、格式化、日志和下游 I/O，测量无法归因。应在相同输入分布、相同业务工作与日志级别下只改变一个因素，区分解析微基准与端到端吞吐、延迟，收集分配率、GC、CPU、积压和错误。Debug与Release结果、单段与多段、生产与测试设备都不能混为一张榜单。

**验收**：提交包含机器、运行时、输入分布、预热、持续时间、并发和原始结果的报告；没有实测时写“待测假设”，不要填想象中的百万QPS。

## 练习 11：停机承诺的反例

当前基线收到主机停止信号时取消连接和后台消费者。假设已 ACK 的队列里还剩20条，它们一定被处理吗？

**参考答案**：不一定，基线明确没有排空和耐久承诺。改造成优雅停机，需要先停止接纳/标记不就绪，拒绝新业务，等待连接在期限内结束，完成队列写端，让消费者排空，最后在总期限内退出。每一步超时后如何记录或重放，依赖持久化设计；内存队列无法在进程被强杀后恢复。

**验收**：用 `SIGINT` 和强制结束分别测试，列出已入队、已ACK、已持久化、已处理的计数差异，不把两种退出结果混在一起。

## 练习 12：毕业设计——一个能说明边界的采集服务

完成一个独立项目，至少包含：版本化协议、分帧与大小限制、绝对帧预算、连接名额、有界业务/输出队列、可取消处理、低基数指标，以及一项真实持久化和明确ACK定义。使用模拟设备，不接实际生产网络。

提交四件东西：协议文档、可运行程序、故障验证报告、设计决策。设计决策必须回答：为什么选择这种帧格式；为什么队列是这个容量；为什么ACK在这个时点发；为什么持有/复制某段内存；为什么采用这种重试和去重键。

最低故障集包括：每个头部切分点、负载切分、同批多帧、EOF剩半帧、非法长度、慢发送、慢接收、慢数据库、重复消息、设备重启、进程崩溃与停机。生产认证与TLS可以作为下一阶段，但文档必须明确哪些环境目前允许运行。

评判标准不是代码行数或用了多少高级API，而是每个异常条件都有可解释、可验证、有界的行为。能把一次失败说清楚，常比跑出一个漂亮的平均吞吐量更接近工程能力。
