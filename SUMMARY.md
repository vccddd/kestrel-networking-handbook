# Summary

* [从零理解高性能 Kestrel 网络编程](docs/book/README.md)

## 第一篇：先认识网络，再认识 Kestrel

* [1. 从一条温度数据认识网络](docs/book/part-1/01-network.md)
* [2. 把仓库读成一张系统地图](docs/book/part-1/02-repository.md)
* [3. Kestrel 如何把连接交给你的协议](docs/book/part-1/03-kestrel.md)
* [4. 中间件不是魔法](docs/book/part-1/04-middleware.md)
* [5. 连接、内存与异步工作的生命周期](docs/book/part-1/05-lifecycle.md)
* [6. 从 Echo 与行协议走到工业帧](docs/book/part-1/06-echo-telnet.md)

## 第二篇：缓冲、分帧与性能

* [7. 内存视图、所有权与池](docs/book/part-2/07-memory.md)
* [8. 写入缓冲，不制造多余中间对象](docs/book/part-2/08-buffer-writer.md)
* [9. 正确推进 PipeReader 与 PipeWriter](docs/book/part-2/09-pipelines.md)
* [10. 多段、半帧与增量解析](docs/book/part-2/10-framing.md)
* [11. 把慢消费者的压力传回设备](docs/book/part-2/11-backpressure.md)
* [12. 用证据判断高性能](docs/book/part-2/12-performance.md)

## 第三篇：从仓库示例走向工业架构

* [13. 传输包装与协议套娃](docs/book/part-3/13-transports.md)
* [14. 从 Redis 伪服务器理解应用管道](docs/book/part-3/14-redis.md)
* [15. HTTP 代理、TLS 与 Fiddler](docs/book/part-3/15-proxy-tls.md)
* [16. 设计有边界的工业遥测网关](docs/book/part-3/16-industrial-design.md)
* [17. 定义生产故障之后的结果](docs/book/part-3/17-production.md)
* [18. 用可观测性定位瓶颈](docs/book/part-3/18-observability.md)

## 第四篇：动手实现并验证

* [19. 亲手运行工业采集网关](docs/book/part-4/19-lab.md)
* [20. 十二个练习与毕业设计](docs/book/part-4/20-exercises.md)
* [21. 把技巧变成日常判断](docs/book/part-4/21-field-guide.md)

## 附录

* [A. 网络与内存术语速查](docs/book/appendices/glossary.md)
* [B. 勘误、边界和保留理由](docs/book/appendices/corrections.md)
* [C. 源码地图与权威资料](docs/book/appendices/sources.md)
* [D. 可复现验证记录](docs/book/appendices/validation.md)
* [E. GitBook 发布与维护](docs/book/appendices/publishing.md)
