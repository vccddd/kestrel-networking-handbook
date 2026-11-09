# 第十五章　代理与 TLS：先看信任边界，再看转发路径

本章帮助你分别读懂普通 HTTP 代理、CONNECT 隧道和会终止 TLS 的分析代理。工业网关常同时接触设备网、管理网和上行服务，一段“能连到目标”的代码因此可能跨越安全边界。学习目标不是把样例部署成通用代理，而是能够说明每一步谁信任谁、谁能选择目的地址、明文在哪一侧出现。

**本章采用源码阅读和离线练习，不启动 Fiddler 样例，不安装或信任其 CA，不使用仓库中的私钥。** 这是有具体原因的：样例的启动配置会调用根证书安装逻辑，一些平台实现会修改系统信任存储。只想学习控制流时，没有必要承担这个操作系统范围的副作用。

## 15.1 两种代理请求有不同的消费位置

普通 HTTP 代理请求通常以绝对 URI 为目标，例如 `GET http://example.invalid/a HTTP/1.1`。CONNECT 请求通常是 `CONNECT example.invalid:443 HTTP/1.1`，表达“请替我建立到这个主机端口的隧道”。CONNECT 之后可以承载 TLS，也可以承载其他 TCP 上层协议，不能因为外层握手像 HTTP，就把隧道负载都当成 HTTP 请求。

先读 [ProxyMiddleware](../../../KestrelApp.Middleware/HttpProxy/ProxyMiddleware.cs)。它使用 Kestrel 内部命名空间的 `HttpParser` 解析请求行与头，将目标和认证头放到 `IProxyFeature`。对普通代理请求，它把消费位置保留在缓冲起点，让后面的 HTTP 栈重新读取原始请求；对 CONNECT，它消费掉代理握手的头部，让下游从隧道负载开始读。这是同一个 Pipe 的位置协议，消费错一次，就可能把 TLS 握手交给 HTTP 解析器，或把 CONNECT 再解析一遍。

内部 `HttpParser` 能减少手写语法代码，但它是内部 API，不应被当成跨 .NET 版本稳定的公共扩展点。旧项目针对 .NET 6，新样例针对 .NET 10；不要只改目标框架就承诺这套内部调用继续兼容。自定义代理还需要自己设置头部长度、握手等待期限和连接配额，使用解析器本身不会自动继承完整 Kestrel HTTP 服务的全部限制。

## 15.2 默认认证并没有保护代理

解析后调用的 [HttpProxyAuthenticationHandler](../../../KestrelApp.Middleware/HttpProxy/HttpProxyAuthenticationHandler.cs) 当前始终返回 true，无论是否有 `Proxy-Authorization`。因此“存在认证接口”和“实际需要认证”是两回事。源码中的 407 分支只有在替换处理器并返回 false 时才有机会发挥作用；当前响应也不是完整认证挑战流程的实现。

CONNECT 在 [TunnelProxyMiddleware](../../../KestrelApp.Middleware/HttpProxy/TunnelProxyMiddleware.cs) 中连接到 Feature 指定的主机和端口。缺少端口或连接异常时返回 502，成功连接之后才回复 200，再启动两个相反方向的复制任务，等待其中一个结束。`Task.WhenAny` 不代表另一个任务已经收尾，也没有完整说明半关闭、取消和超时语义。生产转发器必须显式管理两边生命周期，观察剩余任务异常，并避免僵尸任务或丢掉尚未发送完的尾部。

普通 HTTP 请求交给 [HttpProxyMiddleware](../../../KestrelApp.Middleware/HttpProxy/HttpProxyMiddleware.cs) 的 YARP Forwarder。出站处理器禁用了系统代理、Cookie、自动重定向和自动解压，减少隐藏的 HTTP 状态变化。不过目的地址依然来自客户端，这些开关并不等于出站访问控制。

## 15.3 代理为什么容易成为 SSRF 入口

SSRF 是让服务器替请求者访问服务器本来能到达的地址。工业网关可能有权连接设备网段、内部管理端口和元数据服务；若任意代理使用者都能填写目标，就等于借用了网关的网络权限。认证解决“谁在使用”，目标策略解决“允许去哪里”，二者都需要。

生产蓝图应从明确的允许目标集合出发，例如“某身份只能连接指定服务名及端口”，而不是仅靠一个禁止列表。名称需要规范化，端口和协议需要验证；解析后的所有 IPv4、IPv6 地址都应接受策略检查。要考虑回环、链路本地、内网管理网段、IPv4 映射 IPv6 和 DNS 重绑定。只在解析前检查字符串，然后让另一次 DNS 解析连接，是存在时序空隙的。

真正的连接目标应与验证结果绑定，并结合出站防火墙形成第二层限制。HTTP 跳转如果允许跟随，也必须对每一跳应用目标策略；样例关闭了自动跳转，但不能因此省掉第一跳验证。日志只记录必要的目的服务与策略结果，不记录代理密码。管理面与设备遥测面也不应共用任意出站代理权限。

## 15.4 TLS 侦测是启发式路由，不是安全策略

[UseTlsDetection](../../../KestrelFramework/Middleware/TlsDetection/ListenOptionsExtensions.cs) 的注册顺序为 `TlsInvadeMiddleware → UseHttps → TlsRestoreMiddleware`。[侦测实现](../../../KestrelFramework/Middleware/TlsDetection/TlsInvadeMiddleware.cs) 先读取至少两个字节并保留它们，检查是否以 `0x16 0x03` 开始。不是该前缀时，它临时设置假的 `ITlsConnectionFeature`，让后面的 HTTPS 中间件跳过自身；[恢复中间件](../../../KestrelFramework/Middleware/TlsDetection/TlsRestoreMiddleware.cs) 再移除这个标记。

这只是识别典型 TLS 握手记录前缀，不能代替完整 TLS 解析或身份判断。异常还会被侦测函数转换成 false。同一端口允许明文和 TLS，意味着客户端可以选择明文，服务器并没有强制保密。生产设备入口若要求 TLS，应使用明确的 TLS 监听与失败关闭策略，而不是把自动侦测误写成“所有流量自动加密”。使用假的 Feature 影响框架分支，也意味着升级框架后需要重新验证这些假设。

## 15.5 Fiddler 为什么能够看到 HTTPS 内容

查看 [Fiddler 的监听编排](../../../KestrelApp.Fiddler/ListenOptionsExtensions.cs)：先处理代理握手，再 TLS 侦测，最后隧道分类。这里的 [KestrelProxyMiddleware](../../../KestrelApp.Fiddler/Middlewares/KestrelProxyMiddleware.cs) 与前面的通用代理不同，它在消费 CONNECT 后立即回复 200，并没有先建立目标连接。阅读名称相似的两个类时，不能把一个实现的保证套在另一个上。

[CertService](../../../KestrelApp.Fiddler/Certs/CertService.cs) 生成 CA，把私钥以 PEM 写到本地，并根据客户端握手的域名缓存动态签发的服务器证书。客户端如果信任该 CA，就会接受分析代理签发的目标站点证书。于是形成两条 TLS 会话：客户端到代理、代理到目标。代理中间能看到明文。这是中间人分析机制，不是“保持端到端 TLS 但还能解密”。证书固定、公钥固定或不同信任库会让这条链失败，也不能靠忽略校验来补救。

其[Windows 安装器](../../../KestrelApp.Fiddler/Certs/CaCertInstallers/CaCertInstallerOfWindows.cs) 打开 LocalMachine 根证书存储并可能移除同名不同指纹的证书；[Linux 安装器](../../../KestrelApp.Fiddler/Certs/CaCertInstallers/CaCertInstallerOfLinux.cs) 在具备 root 权限时复制证书并执行信任更新工具；macOS 实现只给手动安装提示。不同平台副作用并不相同。因此本教程不提供执行该启动流程的练习；仓库附带的示例 CA 与私钥也不能作为任何正式信任根。

[隧道分类](../../../KestrelApp.Fiddler/Middlewares/KestrelTunnelMiddleware.cs) 只检查当次读取中是否出现类似 HTTP 请求行的后缀。如果第一段只有一个字节，它可能判为非 HTTP；字符串含 `HTTP/2.0` 也不能证明实现了完整 HTTP/2 识别与 ALPN 协商。非 HTTP 分支会连接目标，并在入站已经 TLS 解密时再创建出站 `SslStream`。这些是展示套接关系的样例路径，不能夸大成透明兼容所有应用协议。

## 15.6 双向 TLS 还缺哪些设计

工业生产蓝图通常需要按设备签发客户端证书，并在服务器验证完整证书链、有效期、用途、信任根及设备身份映射。服务器证书仍要由设备验证名称与链。不能写一个始终返回 true 的证书验证回调来“解决联调问题”，也不能只凭 Subject 字符串授权。

撤销和轮换需要同时设计：设备丢失后如何撤销；CRL 或 OCSP 在隔离网络不可达时采取什么策略；如何提前分发新根并保留重叠窗口；证书到期前如何告警；旧连接是否需要重认证或断开；私钥存在哪里、由谁读取。这些都是生产要求，当前 KG/1 教学样例没有实现 TLS、客户端认证或证书轮换。TLS 也不能阻止拥有合法证书的设备重放旧序号，应用层仍需重放与幂等策略。

分析器自身也带来敏感数据风险：[HttpAnalyzeMiddleware](../../../KestrelApp.Fiddler/Middlewares/HttpAnalyzeMiddleware.cs) 缓冲请求，把响应写入临时文件，分析完成后才复制回原响应流；[LoggingHttpAnalyzer](../../../KestrelApp.Fiddler/HttpAnalyzers/LoggingHttpAnalyzer.cs) 会序列化请求响应到日志。大响应会占用磁盘，流式响应延迟改变，认证头和载荷可能泄露。生产观测宜采集结构化元数据与脱敏摘要，而不是默认抓取全部正文。

## 15.7 动手练习与参考答案

练习一：离线画出普通代理与 CONNECT 的消费位置。参考答案：普通代理保留请求起点，CONNECT 消费代理请求头，隧道负载保留。前者仍要由 HTTP 栈解析，后者下一层可能先处理 TLS。

练习二：列出“只允许已登录用户访问代理”仍无法阻止的一个攻击路径。参考答案：已登录账号请求 `127.0.0.1`、设备管理网段或云元数据地址；因此还需要绑定身份的目标允许策略、解析地址验证和网络出站限制。不要实际请求这些地址，本题只做策略推演。

练习三：为模拟设备写一段双向 TLS 验收条件。参考答案应同时包含服务器名称与证书链验证、客户端证书链与用途验证、设备身份绑定、撤销可达性及不可达策略、新旧证书重叠轮换、私钥保护和失败测试。只写“打开 UseHttps”不完整。

练习四：为什么不能运行 Fiddler 后再问是否需要安装根证书？参考答案：根证书安装在监听配置调用链内，启动本身就可能触发系统修改。应先阅读并隔离副作用。本章已用源码完成学习，不要求执行 CA 安装。下一章把这些信任与资源边界收束到一个明确受限的工业遥测教学架构。
