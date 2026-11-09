# 从零理解高性能 Kestrel 网络编程

面向网络初学者的中文实战教程：从 TCP 字节流、Kestrel 连接模型与 Pipelines，到内存所有权、背压和工业采集网关设计。21 章正文、5 个附录，约 6.3 万汉字；配套源码导读、可运行的 .NET 10 实验和故障验证。

**[在线阅读](https://vccddd.github.io/kestrel-networking-handbook/)** · **[书籍入口](docs/book/README.md)** · **[全书目录](SUMMARY.md)** · **[实验说明](samples/IndustrialGateway/README.md)**

## 学习路线

1. 第 1—6 章：网络基础、Kestrel、连接与应用中间件、生命周期、Echo 与文本协议。
2. 第 7—12 章：Span/Memory、缓冲池、IBufferWriter、Pipelines、多段增量解析、背压与测量。
3. 第 13—18 章：传输包装、Redis、HTTP 代理和 TLS，工业网关、幂等、持久化、停机与可观测性。
4. 第 19—21 章：完整实验、十二个练习与毕业设计。

每章说明设计缘由、源码行为与失败边界，区分原示例、改进后的教学实现和生产蓝图。建议先运行第 19 章，再从第 1 章顺读。

## 运行实验

安装 .NET 10 SDK 与 Python 3。从仓库根执行：

```bash
cd samples
dotnet run --project IndustrialGateway/IndustrialGateway.csproj -c Release
```

另开终端，从仓库根发送模拟测量：

```bash
python3 samples/IndustrialGateway/simulator.py --split
python3 samples/IndustrialGateway/simulator.py --coalesce --count 10
```

实验只监听本机 TCP 7810 / HTTP 7812。ACK 仅确认进入易失内存队列，不代表持久化；不包含生产 TLS、认证或去重。原示例目标框架为 .NET 6/7，作为源码导读保留，新实验独立运行。

## 验证

从仓库根检查文档：

```bash
python3 scripts/check_book.py
python3 scripts/verify_book_examples.py
```

在 samples 目录验证代码与真实连接：

```bash
cd samples
dotnet run --project IndustrialGateway.Checks/IndustrialGateway.Checks.csproj -c Release
python3 IndustrialGateway/verify.py
```

确定性检查包含 162 项断言；socket 验证覆盖错误帧、EOF、超时、连接限制与慢消费者。具体覆盖及未覆盖条件见[验证附录](docs/book/appendices/validation.md)。

## 本地构建阅读站

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-docs.txt
.venv/bin/python scripts/prepare_docs.py
.venv/bin/python -m mkdocs build --strict
.venv/bin/python scripts/check_site.py
python3 -m http.server 8000 --directory _site
```

打开 `http://127.0.0.1:8000`。网站由 **MkDocs Material** 生成，使用其原生章节导航、中文搜索、页内目录、深浅主题、代码高亮与复制、移动端导航和 Mermaid 支持。内容预处理脚本只整理 Markdown 与源码链接，不手写 HTML 页面或浏览器交互。

推送 main 后，GitHub Actions 自动检查文档、构建静态站并部署 GitHub Pages。独立的实验检查工作流验证 .NET 示例。GitBook Git Sync 配置也予以保留；在线阅读默认使用 GitHub Pages，不要求 GitBook 账号。

## 来源与许可

原始 Kestrel 示例与七篇文章来自 [xljiulang/KestrelApp](https://github.com/xljiulang/KestrelApp)，基准提交 `ba06920608a416fd285c24538a87ad379c1bbd1b`。本仓库在其基础上新增系统教程、工业网关实验与阅读站，保留原 MIT 许可及作者署名，详见 [NOTICE](NOTICE.md) 和 [LICENSE](LICENSE)。

旧例的证书、私钥与系统信任资产未随书籍发布。Fiddler 部分用于源码学习，不应直接运行来修改系统信任；完整原项目请参见上游仓库。
