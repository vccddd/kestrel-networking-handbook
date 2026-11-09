# 附录 E：阅读站发布、GitBook 导入与维护

本书同时提供完整Markdown、GitBook Git Sync配置和GitHub Pages静态阅读站。默认在线入口为[GitHub Pages](https://vccddd.github.io/kestrel-networking-handbook/)，源码位于[独立教程仓库](https://github.com/vccddd/kestrel-networking-handbook)。Pages不需要GitBook账号；GitBook托管空间仍可按下文另行接入。

## E.0 GitHub Pages 的自动发布

[Pages工作流](../../../.github/workflows/pages.yml)在main推送后检查书稿、构建网站、验证所有本地页面链接，再使用官方Pages Actions上传和部署；pull request只检查构建，不部署。网站使用 **MkDocs Material** 文档工具栈，由[站点配置](../../../mkdocs.yml)定义导航、中文搜索、目录、主题和代码展示。[内容预处理](../../../scripts/prepare_docs.py)只整理Markdown与源码链接，不生成HTML、不实现浏览器交互；实际页面全部由MkDocs构建。源文件仍可通过GitHub查看。

本地构建从仓库根执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-docs.txt
.venv/bin/python scripts/prepare_docs.py
.venv/bin/python -m mkdocs build --strict
.venv/bin/python scripts/check_site.py
python3 -m http.server 8000 --directory _site
```

然后访问 `http://127.0.0.1:8000`。此命令仅用于预览书籍，不启动设备网关。静态产物位于固定的_site目录，每次构建会重建该输出，不要把手写文件放进去。Material原生提供中文搜索、移动目录、浅深色切换、代码复制和高亮，页面样式无需手写。Mermaid由Material与SuperFences集成，使用主题提供的渐进加载能力。

## E.1 内容布局与配置选择

仓库根的[GitBook配置](../../../.gitbook.yaml)内容为：

```yaml
root: ./

structure:
  readme: docs/book/README.md
  summary: SUMMARY.md
```

这里故意选择整个仓库根作为内容根，而不是只选docs/book。书内源码链接需要跳转到兄弟目录中的原项目与samples；若把GitBook的映射目录缩到docs/book，Git Sync看不到这些兄弟目录。官方文档也说明，root只在映射空间目录内起作用，不能让不可见的兄弟目录自动变为可见。

[SUMMARY](../../../SUMMARY.md)只收录书籍入口、21章、5个附录，各页面只出现一次。原始文章和项目源码不作为书籍章节重复导入。现代GitBook使用Git Sync配置，不需要安装旧版全局gitbook-cli，也不要求运行 `gitbook install`。

## E.2 在GitBook中接入仓库

当你决定在线发布时，在GitBook创建或选择空间并启用对应仓库的Git Sync，映射到仓库根，选择包含本书文件的分支。内容应由本配置指定的入口和SUMMARY导入。若已有站点或空间映射，先核对映射目录，不要误把另一本书的内容根替换掉。

这部分说明的是可选GitBook接入，不代表已经创建GitBook托管空间。修改书籍时先完成本地验证，再按正常仓库流程推送；Pages会自动部署，GitBook只有接通Git Sync后才会读取相应分支。

GitBook对非Markdown文件、相对源码链接和Mermaid的呈现可能随同步模式变化。首次导入后至少抽查：第一章到源码的跳转、第19章的实验文件链接、附录中的百分号编码中文路径、目录分页和一张Mermaid图。这里验证的是发布后的用户体验，不能用本地“文件存在”代替。

如果某种同步方式不提供源码文件浏览，将原项目链接改为固定提交的GitHub blob URL；新实验链接应指向你实际发布后的提交，不能预先杜撰一个远程地址。也可以将样例作为显式可下载附件，再在章节中保留关键代码解释。仅导入一个脱离源码的书籍子目录不是本配置的默认支持方式。

## E.3 本地质量检查

从仓库根执行：

```bash
python3 scripts/check_book.py
```

[检查脚本](../../../scripts/check_book.py)只依赖Python标准库，检查27个书页、SUMMARY唯一覆盖、相对链接目标、Markdown标题片段、代码围栏、尾空格、明显占位符以及绕过samples SDK配置的运行命令。它不爬取外部网站，不渲染GitBook，也不是完整Markdown解析器；链接存在不能证明内容正确。

然后验证真实代码：

```bash
cd samples
dotnet run --project IndustrialGateway.Checks/IndustrialGateway.Checks.csproj -c Release
python3 IndustrialGateway/verify.py
```

这两条命令在同一samples终端执行，稳定.NET10 SDK配置才会生效。完整教学代码块还可从仓库根运行 `python3 scripts/verify_book_examples.py`，在临时目录编译第7/20章的宿主与离线解析入口，见[片段验证脚本](../../../scripts/verify_book_examples.py)。若只检查Markdown结构，不需要启动网关或安装浏览器工具。

有可用Git工作区时还可从仓库根运行：

```bash
git diff --check
```

这只检查已纳入diff的内容；未跟踪的新Markdown仍由上面的Python脚本检查。不要因为空白检查通过，就声称所有文本已经审阅。

## E.4 一次修改需要同步的地方

改协议时，同步字段表、Protocol、模拟器、检查程序、错误示例和练习答案。改默认队列或超时时，同步运行命令、容量推演及验证记录。改ACK点时，应重审可靠性整章，而不是只换一个注释。

改运行时版本时，更新目标框架、SDK选择文件、支持策略说明和实际运行记录；不要简单把所有net10替换成未来版本名称，就声称已经兼容。改指标类型时，也要检查采集窗口、晚接入和进程重启后的含义。

保持三类信息分开：仓库事实、可执行实验结果、生产建议。新能力在代码与验证完成前只能作为蓝图描述。为了更好读而删掉所有失败边界，会让教程从工程训练退化成正常路径演示。

## E.5 让这本书随学习成长

你可以在每章练习旁记录自己的预测、故障输入和实际输出。发现无法回答的问题，先把它写成具体情景：例如“同一个Read里两帧、只要一帧、后面不再发送，为什么超时”，比“Pipelines不稳定”更容易定位。

下一轮扩展适合从一个明确保证开始，例如“ACK后进程重启不丢”，然后实现耐久日志和恢复测试；或者“设备身份可轮换”，然后实现TLS和认证演练。每次只加强一个可解释的承诺，读者就能知道新增复杂度换来了什么。

配置语法依据[GitBook官方内容配置](https://gitbook.com/docs/docs-as-code/git-sync/content-configuration.md)。在线预览未在本次会话执行，发布后的导航、附件与图形效果仍需在实际空间核验。
