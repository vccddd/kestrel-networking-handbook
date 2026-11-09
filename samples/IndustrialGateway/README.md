# KG/1 工业采集网关教学实验

本实验配合[完整教程](../../docs/book/README.md)。它独立于旧 .NET 6/7 解决方案，使用 .NET 10、ASP.NET Core 共享框架与 Python 3 标准库，无第三方 NuGet 包。只使用本机模拟设备。

## 启动和发送

从仓库根目录运行；命令先进入 `samples`，让 [.NET SDK 选择配置](../global.json) 生效。

```bash
cd samples
dotnet run --project IndustrialGateway/IndustrialGateway.csproj -c Release
```

另一个终端从仓库根目录执行：

```bash
python3 samples/IndustrialGateway/simulator.py --split
python3 samples/IndustrialGateway/simulator.py --coalesce --count 10
curl http://127.0.0.1:7812/health
```

默认监听 `127.0.0.1:7810`（KG/1 TCP）、`127.0.0.1:7812`（HTTP/1 健康端点）。没有 7811 监听、没有 TLS。Kestrel 启动日志把裸 TCP 端口也显示成 `http://...`，这不代表该端口能处理 HTTP。按 Ctrl+C 停止。

预期模拟器打印 `ACK 1: enqueued in volatile memory` 等输出。默认日志不打印每条数据，避免把控制台速度当网络性能；需要查看数据时加 `--Logging:LogLevel:IndustrialGateway=Debug`。

## 验证

从仓库根目录：

```bash
cd samples
dotnet run --project IndustrialGateway.Checks/IndustrialGateway.Checks.csproj -c Release
python3 IndustrialGateway/verify.py
```

前者为无测试框架依赖的确定性检查程序，失败以非零退出码结束，包含每个不完整长度、每个双段切分位置、逐字节多段、同缓冲多帧、非法头、EOF、ACK 和队列取消。后者构建并自动启动自己的临时本机端口服务，验证真实 socket、绝对帧预算、连接限制与慢消费者，结束后清理进程，不要求手工启动服务器。

注意：多次 `sendall` 不保证网络上形成同样数量的 TCP 包或服务端 Read；多段解析的确定性由检查程序构造 `ReadOnlySequence` 来保证。

## 调整实验参数

```bash
cd samples
dotnet run --project IndustrialGateway/IndustrialGateway.csproj -c Release -- --Gateway:QueueCapacity=1 --Gateway:ConsumerDelayMilliseconds=900 --Gateway:FrameTimeoutMilliseconds=3000
```

参数均可通过 .NET 配置提供：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `Gateway:TcpPort` | 7810 | TCP 协议端口 |
| `Gateway:HttpPort` | 7812 | HTTP 健康端口，须与 TCP 不同 |
| `Gateway:QueueCapacity` | 128 | 等待队列元素数上限，不含消费者正在处理的一个元素 |
| `Gateway:MaxConnections` | 64 | 自定义协议 handler 处理名额，超出即关闭 |
| `Gateway:FrameTimeoutMilliseconds` | 5000 | 一帧从开始等待到入队、Flush 的总预算；也限制帧间空闲 |
| `Gateway:ConsumerDelayMilliseconds` | 0 | 教学慢消费者延迟 |

## 线协议

所有多字节数值采用大端，帧头固定 16 字节：

| 偏移 | 字节数 | 字段 |
|---|---:|---|
| 0 | 2 | Magic `0x4B47`，ASCII `KG` |
| 2 | 1 | Version `1` |
| 3 | 1 | Type：`1` Telemetry，`2` Ack |
| 4 | 4 | PayloadLength，uint32 |
| 8 | 8 | Sequence，uint64 |

Telemetry 负载固定 16 字节，依次为 `DeviceId:uint32`、`UnixTimeMilliseconds:int64`、`ValueMilliUnits:int32`。23500 表示 23.500 个约定单位，单位须由设备点表另行定义。ACK 没有负载，回传相同 Sequence。

最大声明负载为 4096 字节，但 KG/1 当前唯一上行类型 Telemetry 只接受 16 字节，因此合法上行帧恒为 32 字节；4096 是协议上限，不能据此发送 4096 字节 Telemetry。不支持任意类型透传、重同步或协商版本。

## 代码分工与边界

- [Protocol.cs](Protocol.cs)：同步多段解析，先验头再等负载，仅成功才提交游标，返回独立标量值。
- [GatewayConnectionHandler.cs](GatewayConnectionHandler.cs)：一读一 `AdvanceTo`，每次返回一帧，入队和写响应在借用结束之后，单写者，异常与清理。
- [TelemetryQueue.cs](TelemetryQueue.cs)：有界 Channel、慢消费者、连接名额、低基数 Meter 指标。
- [Program.cs](Program.cs)：依赖注入、loopback 监听、SocketTransportOptions 读写水位与健康端点。

ACK **仅表示进入易失内存队列**；可能尚未被消费者处理。取消或发送失败时，客户端可能不知道入队是否已经发生。重发会重复，本例没有去重。日志不是持久化，不提供 WAL、数据库、鉴权、TLS、设备注册、会话恢复、故障转移或停机排空保证；健康端点也不是持久化链路就绪证明。生产演进见[工业架构章节](../../docs/book/part-3/16-industrial-design.md)。
