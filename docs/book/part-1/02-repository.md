# 第 2 章 把仓库读成一张系统地图

本章解决“项目太多，从哪里开始读、哪个命令能运行”的问题。我们把原始示例当作理解设计的材料，把新增 .NET 10 工业网关当作可执行实验。读完后，你应能沿着注册、监听、连接处理、业务响应找到对应源码，并能解释环境问题与协议问题的区别。

## 2.1 先认清项目的职责

打开 [原始 README](../../../README.md)，可以看出仓库希望展示如何在 Kestrel 上组合协议、传输包装和应用中间件。这里的 `KestrelFramework` 是作者写的一组扩展，不等于微软 Kestrel 的源码，也不是使用原生 `ConnectionHandler` 的必需依赖。

| 阅读位置 | 负责什么 | 本篇重点 |
| --- | --- | --- |
| [KestrelApp 入口](../../../KestrelApp/Program.cs) | 创建 Host、注册服务、给端点安装协议、配置 HTTP 路由 | 从启动找到实际处理者 |
| [KestrelApp 配置](../../../KestrelApp/appsettings.json) | 给命名端点分配地址，设置日志与证书配置 | 把名字与端口对上 |
| [KestrelFramework 项目](../../../KestrelFramework/KestrelFramework.csproj) | 连接中间件接口、应用链、Buffers、Stream、Pipe 适配 | 理解组合方式 |
| [Middleware 项目](../../../KestrelApp.Middleware/KestrelApp.Middleware.csproj) | Echo、行协议、Redis 子集、代理、流量处理示例 | 理解具体协议 |
| [Fiddler 项目](../../../KestrelApp.Fiddler/KestrelApp.Fiddler.csproj) | HTTP 分析、转发与证书相关演示 | 留待代理篇研究 |
| [新网关项目](../../../samples/IndustrialGateway/IndustrialGateway.csproj) | .NET 10、KG/1、有限连接与队列、健康入口 | 本书贯穿实验 |

不要从生成目录里的文件猜设计。编译输出、恢复缓存和 IDE 索引可能来自旧环境；真正决定目标框架的是项目文件，真正决定分帧格式的是解析器。目录名叫 Echo 不代表它可以直接收普通文本，配置字符串含 `http://` 也不代表该端点最后一定由 HTTP 处理。

## 2.2 沿着一条连接读，而不是逐文件背诵

第一遍先读 [启动入口](../../../KestrelApp/Program.cs)。`WebApplication.CreateBuilder` 创建宿主构建器；`builder.Services` 注册依赖；`ConfigureKestrel` 配置服务器端点；`builder.Build()` 建成应用；`app.Run()` 才进入持续运行。它们都不是“收到每条设备消息后再执行”的代码。

接着在 [配置](../../../KestrelApp/appsettings.json) 中找到 `Telnet`，它的地址是 `http://localhost:5000`。回入口找到 `.Endpoint("Telnet", ...)`，那里调用 `UseTelnet()`。继续打开 [Telnet 注册扩展](../../../KestrelApp.Middleware/Telnet/ListenOptionsExtensions.cs)，看到它只把 `TelnetConnectionHandler` 安装为终端连接处理者。最后读 [处理者](../../../KestrelApp.Middleware/Telnet/TelnetConnectionHandler.cs)，才能知道欢迎语、CRLF 分帧和业务链。

这个顺序把四个问题分开了：在哪里监听？谁处理连接？字节怎样成消息？消息怎样成响应？排障也沿同一顺序。连接被拒绝时，尚未到协议解析；连接成功却没有预期响应时，才继续查帧格式、刷新、超时和业务分支。

再沿 Echo 走一遍：配置中的 `Echo` 是 `5006`，入口先安装 TLS 检测再 `UseEcho()`，[Echo 注册扩展](../../../KestrelApp.Middleware/Echo/ListenOptionsExtensions.cs) 安装 [Echo 处理者](../../../KestrelApp.Middleware/Echo/EchoConnectionHandler.cs)。TLS 检测与证书会引入额外前提，所以本书不以“直接启动整个旧宿主”作为第一个实验。

## 2.3 两代示例必须分清

原始 [KestrelApp 项目](../../../KestrelApp/KestrelApp.csproj)、[Middleware 项目](../../../KestrelApp.Middleware/KestrelApp.Middleware.csproj)、[Framework 项目](../../../KestrelFramework/KestrelFramework.csproj) 均以 `net6.0` 为目标；[Fiddler 项目](../../../KestrelApp.Fiddler/KestrelApp.Fiddler.csproj) 是 `net7.0`。本次写作环境安装 SDK 10.0.403 与 11.0.100-rc.2，运行时为 .NET/ASP.NET Core 10.0.14 与 11.0 预发行版，没有 6/7 共享运行时。

SDK 是构建工具，运行时是执行程序的环境；“装了更新的 SDK”不自动表示旧应用可按默认策略跨大版本启动。旧目标的恢复也可能需要额外目标包和网络。不能把编译与执行混为一谈，更不能把无法加载 net6 运行时误认为 Kestrel 协议有错误。

本书新建独立 net10 实验，避免在入门阶段同时处理旧依赖、证书、代理和业务协议。它不要求修改旧项目目标框架，也不把旧演示的端口搬过去。原始示例有助于看组合思想；新实验用于验证稳健边界。两者都有阅读价值，但命令、目标框架和协议必须成套使用。

## 2.4 原始端点地图：名字、地址、最终协议

下表来自 [端点配置](../../../KestrelApp/appsettings.json) 与 [入口注册](../../../KestrelApp/Program.cs) 的联合阅读。这里描述的是源码配置，并非宣称这些端点已在当前机器运行验证。

| 原始端点名 | 端口 | 注册后用途 |
| --- | --- | --- |
| Telnet | 5000 | CRLF 行文本演示 |
| XorTelnet | 5001 | XOR 包装后的同一行协议 |
| XorTelnetProxy | 5002 | 转发至 5001 的示例 |
| HttpProxy | 5003 | HTTP 代理相关处理 |
| HttpHttps | 5004 | TLS 检测后走 HTTP |
| TelnetWebSocket | 5005 | HTTP 路由 `/telnet` 的连接处理 |
| Echo | 5006 | TLS 检测后的两字节长度前缀 Echo |
| Redis | 5007 | RESP 风格命令演示子集 |

XOR 只是可逆变换教学，不提供现代密码学安全。行协议示例没有完整 Telnet 协商，Redis 示例也不代表完整 Redis 服务器。用熟悉的名字降低阅读门槛是有益的，但对外声明兼容性时必须逐项核对协议能力。

新网关则只使用 `127.0.0.1:7810` 的 KG/1 TCP 和 `127.0.0.1:7812` 的 HTTP `/health`，默认无 TLS、无 7811 监听。健康入口能回答，只说明相应 HTTP 服务可用，不证明某台设备已经接入，也不证明队列中的数据持久化完成。

## 2.5 一次可复现的环境检查

在仓库根目录执行：

```bash
dotnet --list-sdks
dotnet --list-runtimes
python3 --version
```

把实际输出记录在实验笔记中。每台机器结果可能不同，教材中的版本是本次环境事实。若缺 .NET 10 SDK，应先安装相应工具；不要通过修改原项目来掩盖环境差异。之后用两个终端：第一个持续运行新服务，第二个发起检查。

.NET CLI 按**当前工作目录向父目录**查找 SDK 配置，不会因为 `--project` 指向某项目就从该项目目录开始找。本书在 [samples/global.json](../../../samples/global.json) 中选择 .NET 10 正式版 SDK；若站在仓库根直接运行 `--project samples/...`，不会向下发现这份配置，装有更新预览 SDK 时可能选错版本。因此终端 A 先从仓库根进入 `samples`，再核对实际选中的 SDK 并启动：

```bash
# 终端 A：当前起点为仓库根，服务保持运行，结束时按 Ctrl+C
cd samples
dotnet --version
dotnet run --project IndustrialGateway/IndustrialGateway.csproj --no-launch-profile
```

`dotnet --version` 应显示符合配置的 10.0 正式版；`--list-sdks` 列出已安装版本，不能代替这个选择结果。这里使用的相对项目路径以当前 `samples` 为起点。终端 B 是另一个独立 shell，仍可停留在仓库根；一个终端执行 `cd` 不会改变另一个终端的工作目录。

```bash
# 终端 B：当前目录可保持仓库根，检查 HTTP 服务是否响应
curl --fail http://127.0.0.1:7812/health
```

`--no-launch-profile` 避免本地启动配置带来隐式 URL 或自动打开浏览器。收到健康响应后再运行 [模拟器](../../../samples/IndustrialGateway/simulator.py) 和 [验收脚本](../../../samples/IndustrialGateway/verify.py)，使用新实验自己的说明与参数。不要把 `curl` 指向 7810：它发送的是 HTTP，不是 KG/1。启动失败时先看终端 A 的异常；命令没有保持运行时，终端 B 连接被拒绝属于预期后果。

## 2.6 读源码时保存哪些证据

每发现一个行为，记下“文件、符号、输入、输出、约束”。例如：`TryReadRequest` 在 CRLF 出现后产生字符串；`EmptyMiddleware` 对空串写提示并短路；`TelnetResponse.WriteLine` 写 UTF-8 与 CRLF，真正刷新由 `FlushAsync` 完成。这样记录能帮助你从“看过代码”走向“能解释行为”。

个别原始文件采用非 UTF-8 编码。编辑器乱码时应先确认解码问题，用临时 UTF-8 副本阅读，避免为了读文档把原文件整份重写，产生无关差异。转换成功也要检查注释是否可读；仅仅没有解码异常不等于猜中的编码一定正确。

本篇会指出旧例的边界，包括 Echo 长度的有符号读取、EOF 残留处理、生命周期所有权和多任务收尾。它们不是要求你立刻修改旧实现，而是告诉你哪些思想可以借鉴、哪些地方必须在工业实现中补全。原文的作者观点应作为背景理解；性能结论需要独立测量。

## 2.7 练习、参考答案与交付标准

**练习一：入口里出现 `app.MapConnectionHandler<TelnetConnectionHandler>("/telnet")`，是否说明可以向 TCP 5000 发送 `GET /telnet`？** 参考答案：不可以。5000 已由 `UseTelnet` 接管；`/telnet` 属于 HTTP 路由路径，在原示例的 HTTP/WebSocket 端点上工作。监听端点和 HTTP 路由是不同层。

**练习二：原始 Echo 起不来，先把 net6 改成 net10 是否足够？** 参考答案：不够。运行时、依赖 API、TLS 检测、证书、监听冲突都可能有影响。迁移是独立任务，要有构建与行为验证。本书通过新 net10 项目提供实验路径，不默默改旧代码。

**练习三：`/health` 返回成功是否等于设备 42 的温度已保存？** 参考答案：不等于。健康检查与单条消息的接收、入队、持久化是不同证据；必须分别观察协议 Ack、消费结果和存储确认。

**本章交付标准：** 给出一张自己的项目职责表；能够沿 Telnet 或 Echo 从配置追踪到解析器和响应；记录本机 SDK/运行时，并启动新网关获得健康响应。记录失败也要包含命令、异常与判断层级。下一章阅读 [Kestrel 的连接模型](03-kestrel.md)，解释“连接”如何变成处理者的参数。
