# 来源与文件边界

本教程基于 [xljiulang/KestrelApp](https://github.com/xljiulang/KestrelApp)，上游源码基准为 `ba06920608a416fd285c24538a87ad379c1bbd1b`。原软件及文档版权声明为 Copyright (c) 2022 老九，采用 MIT License；原许可全文保留于 [LICENSE](LICENSE)。

本仓库保留用于教学的原 KestrelApp、KestrelApp.Middleware、KestrelFramework、KestrelApp.Fiddler 源码与原文档。新增内容包括完整中文教程、新 .NET 10 KG/1 工业网关实验、检查程序、阅读站与发布工作流，均按仓库 MIT 许可发布。

为使教学仓库便于公开阅读，未复制上游历史、IDE 本地设置、编译产物、证书和私钥资产。未对原 .NET 6/7 宿主宣称运行验证；Fiddler 原入口包含 CA 安装等系统影响操作，旧例只作为源码导读。需要完整原项目时请使用上游仓库，并自行审查证书及运行环境。

预设工业场景与 KG/1 协议属于本书教学设计，未连接实际产线，也不代表通过工业安全认证。新实验的 ACK 仅确认内存入队；生产持久化、身份、去重与故障恢复仍须另行实现与验证。
