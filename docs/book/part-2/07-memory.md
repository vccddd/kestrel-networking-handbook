# 第 7 章：内存视图、所有权与租期

一台采集网关同时接收几十台设备的数据。每次收到几十个字节就创建数组、截取数组、转换字符串，程序通常也能正确运行；当采样频率提高时，分配和回收会占用更多 CPU，延迟也可能随垃圾回收抖动。解决问题的第一步不是把所有数组替换成 Span，而是分清三件事：字节存在哪里，当前代码可以看哪一段，谁决定这段存储何时可以复用。

本章以 KG/1 设备协议作为背景。它有 16 字节头，包含魔数、版本、类型、负载长度和序号；Telemetry 的负载也是 16 字节。即使一帧只有 32 字节，网络仍可能分两次交付，也可能一次交付几十帧。内存类型帮助我们表达这些局部视图，但不会替我们保证协议完整和业务可靠。

## 7.1 数组是存储，Span 是连续区域的视图

`byte[]` 是托管数组对象。`Span<byte>` 是包含起点和长度的轻量视图，可以指向数组的一部分，也可以指向栈上或适当管理的非托管内存。`AsSpan` 和 `Slice` 通常不复制内容。下面三个变量观察的是同一组字节：

```csharp
byte[] packet = { 0x4B, 0x47, 1, 1, 0, 0, 0, 16 };
Span<byte> whole = packet;
Span<byte> lengthField = whole.Slice(4, 4);
lengthField[3] = 32;
// packet[7] 也变为 32；这里没有生成第二份报文。
```

视图的好处在于，解析函数可以接收一段输入，而不用约定“数组、偏移、长度”三个参数。对只读取的函数，应优先表达为 `ReadOnlySpan<byte>`。只读约束作用于这条访问路径：它不能通过该视图写入，但持有原数组的其他代码仍可能修改数据。因此“只读”不等于不可变，更不等于线程安全。

原文[相关新类型](<../../../docs/2.1 高性能buffer--相关新类型.md>)把 Span 概括成性能优于数组，并用数组自赋值的微基准给出固定排序。这不能成为通用结论。JIT 可能消除无意义的读写，边界检查消除、运行时版本、循环形式都会影响结果。Span 的主要价值是统一连续内存访问、低成本切片和更清楚的 API；它并不为每一种循环保证更低耗时。实际热点应测量有结果依赖的真实操作，例如解析字段并累计校验值。

`Span<T>` 是 `ref struct`，不能作为普通类的实例字段，也不能被普通闭包捕获。它的引用不能跨越异步挂起点。原文笼统说“异步方法不能声明 Span”也需要加上语言版本边界：较新的 C# 可以在受限制的局部范围使用 ref struct，但仍不能让它跨 `await` 存活。本仓库旧项目使用的语言版本未必支持这些放宽。最稳妥的教程组织方式是同步解析函数接收 Span，异步协调函数保存 Memory 或 Sequence。

## 7.2 Memory 能跨异步调用，不能延长所有者的寿命

`Memory<byte>` 和 `ReadOnlyMemory<byte>` 同样描述连续内存，但可作为普通字段，也可传给异步 I/O。需要同步访问时获取 `.Span`。以下写法的关键是等待操作结束以后才归还数组：

```csharp
byte[] rented = ArrayPool<byte>.Shared.Rent(32);
try
{
    // EncodeFrame 是同步编码函数，返回真正写入的字节数。
    int count = EncodeFrame(rented.AsSpan());
    await destination.WriteAsync(rented.AsMemory(0, count), token);
}
finally
{
    ArrayPool<byte>.Shared.Return(rented);
}
```

这里 `destination` 是已有的 Stream，`EncodeFrame` 是调用方的编码逻辑，片段只说明租期，不是独立服务器。`WriteAsync` 的任务到达完成、失败或取消状态后，按照正常 Memory API 契约，该操作才结束对输入的借用。相反，先调用而不等待，立即 Return，再等待任务，就可能让下一个租用者覆盖仍在发送的数据。

Microsoft 的 [Memory 使用指南](https://learn.microsoft.com/en-us/dotnet/standard/memory-and-spans/memory-t-usage-guidelines)明确区分 owner、consumer、lease：所有者管理生命周期，使用者在租期内读写。同步函数返回即结束借用；返回 Task 或 ValueTask 的异步函数，借用通常持续到该任务终结。接收一个 Memory 并不表示接收所有权。把它放入后台任务也不会自动延长合法租期，必须让调用方等待，或者复制内容、显式转移所有权。

这同样适用于 PipeReader 返回的字节。即使把 `ReadOnlyMemory<byte>` 放进对象，甚至把对象放进线程安全队列，它仍然借用原管道的内存。调用 `AdvanceTo` 后，这一次读取取得的全部视图都应视为失效；即使只消费了一部分，下次也要通过新的 ReadResult 重新取得未消费部分。引用在 C# 层面存在，并不能阻止内存池重用存储。

## 7.3 池复用容量，不提供消息长度和保密保证

`ArrayPool<byte>.Shared.Rent(100)` 保证至少有 100 个元素，实际数组可能更大，而且内容不保证为零。必须分别保存容量与有效长度。若只写入 32 字节，就只能发送 `AsMemory(0, 32)`，不能发送整个数组，否则会把旧数据当成协议的一部分。

```csharp
byte[] rented = ArrayPool<byte>.Shared.Rent(100);
try
{
    rented.AsSpan(0, 32).Clear();
    // 只把这 32 字节视为当前有效区域。
    Consume(rented.AsSpan(0, 32));
}
finally
{
    ArrayPool<byte>.Shared.Return(rented);
}
```

Rent 和 Return 必须配对，归还后不能读写，也不能再次归还给池。`Return(array, clearArray: true)` 可要求池在保留数组用于复用时清除内容，但池本身不是安全擦除机制；敏感内容需要根据威胁模型，在仍拥有数组时明确清除相关区域，密码材料可考虑专用清零 API，并控制日志与复制。不能把“使用池”描述成“自动清零且不会泄漏”。

仓库的 [ArrayOwner](../../../KestrelFramework/System.Buffers/ArrayOwner.cs) 和 [IArrayOwner](../../../KestrelFramework/System.Buffers/IArrayOwner.cs)把租用包装成 IDisposable。实际属性是 `Length`，不是旧文示例的 `Count`。`Length` 保存请求的逻辑长度，`Array.Length` 是实际容量；`AsSpan()` 和 `AsMemory()` 已按逻辑长度切片。Dispose 有重复调用保护，但公开的 Array 引用不会被撤销，方法也没有全面检查 disposed。调用方必须自觉遵守“Dispose 后不访问”。终结器只是兜底，不能替代及时的 using；等待 GC 才归还会使池化失去意义。

## 7.4 MemoryPool 是抽象，IMemoryOwner 才表达释放责任

`MemoryPool<T>.Rent` 返回 `IMemoryOwner<T>`，通过 `owner.Memory` 取得区域，通过 `Dispose` 释放。原文说 MemoryPool “本质上还是 ArrayPool”，只适合描述某些默认实现，不能当作抽象契约。自定义 MemoryPool 可以管理其他存储。消费代码应该依赖 Memory、大小和所有权约定，而不假定能取得底层数组。

```csharp
using IMemoryOwner<byte> owner = MemoryPool<byte>.Shared.Rent(32);
Memory<byte> frame = owner.Memory.Slice(0, 32);
int count = EncodeFrame(frame.Span);
await destination.WriteAsync(frame.Slice(0, count), token);
```

如果把 owner 交给一个约定接管所有权的队列，就不能在当前作用域继续 using 后立即释放。需要定义成功入队、入队失败、取消、丢弃、消费者异常时分别由谁 Dispose。单纯把 `Memory<byte>` 入队没有传递这项责任，常常是泄漏和数据损坏的来源。小型工业测量更适合在读取租期内解析成四个数值字段，再把值类型测量入队；它既不依赖管道寿命，也不需要为每条测量租一个数组。

## 7.5 字节序必须来自协议

CPU 的内存布局不应决定网络字段布局。KG/1 的长度和序号采用大端，也就是高位字节在前。使用 `BinaryPrimitives.ReadUInt32BigEndian`、`WriteUInt64BigEndian` 直接表达协议，比先 BitConverter 再按主机字节序翻转更清楚。`MemoryMarshal.AsBytes` 只重新解释本机布局，既不做字节序转换，也不自动验证对齐后的协议含义。不能用它把任意 C# 结构体直接变成跨平台报文，结构体填充、字段布局和端序都可能不符合协议。

原文把 `MemoryMarshal.AsMemory` 描述成给 ReadOnlyMemory “移除只读功能”，也不能理解为任意修改许可。转换只是改变访问视图，不能改变数据所有者的契约。只有已知底层可写、且调用方明确有权修改时才可如此操作；字符串等不可变存储、约定只读的共享数据，不能因为取得可写视图就改动。一般解析器直接接受只读视图即可，不需要这种转换。

仓库的 [BufferReader](../../../KestrelFramework/System.Buffers/BufferReader.cs)封装了连续 Span 的字段读取，每次读后缩短剩余视图。它的 `Read`、`ReadBigEndian` 假设输入已经足够，不是处理网络半包的 TryParse；长度不足会抛异常。只有先验证完整长度，才应使用这类读取器。网络多段输入则应使用 SequenceReader 或在有明确上限时复制一个小头部。

## 7.6 动手练习与参考答案

先做一个可独立运行的切片实验。下面是可选的本地练习：创建命令会在 samples 下写入一个新的 MemoryScratch 目录，与网关项目分开，不需要修改网关源码。如果已有同名目录，请换一个尚未使用的名称，并同步调整后面的路径；不要用 `--force` 覆盖已有练习。从仓库根目录执行：

```bash
cd samples
dotnet new console --framework net10.0 --output MemoryScratch
```

必须先进入 samples，让 SDK 解析使用该目录中的 [global.json](../../../samples/global.json)。创建成功后，将生成的 `MemoryScratch/Program.cs` 全部替换为以下完整顶级程序，不需要额外的 Main 方法或 NuGet 包：

```csharp
using System;

byte[] bytes = { 1, 2, 3, 4 };
ReadOnlyMemory<byte> view = bytes.AsMemory(1, 2);
byte[] copy = view.ToArray();

bytes[1] = 99;

Console.WriteLine($"view[0] = {view.Span[0]}");
Console.WriteLine($"copy[0] = {copy[0]}");
```

保持工作目录为 samples，再执行：

```bash
dotnet run --project MemoryScratch/MemoryScratch.csproj -c Release
```

参考输出如下：

```text
view[0] = 99
copy[0] = 2
```

ReadOnlyMemory 不直接提供元素索引器，需在同步访问时通过 `view.Span[0]` 读取。修改原数组后，切片视图看见 99，而独立副本仍保留 2，说明 AsMemory/Slice 借用原存储，ToArray 建立独立副本。此实验使用普通数组，不要通过“归还后再读取”的错误实验验证池复用，因为错误行为没有稳定结果保证。

练习一：租到 128 字节数组，写入 20 字节后，把整个数组交给发送函数。指出两种风险。参考答案：接收方会看到多余字节，破坏下一帧边界；多余区域还可能包含池中旧内容。必须保存有效长度，只传前 20 字节，并等待发送任务终结后归还。

练习二：某函数接收 `ReadOnlyMemory<byte>`，内部启动 Task.Run 后直接返回 void。调用方随后复用缓冲区。这为何不因“只读”而安全？参考答案：只读禁止该函数通过此视图修改，不禁止调用方修改。后台读取超出同步函数租期。改成可等待任务并让调用方等待，或者在返回前复制，或者明确接收并管理 owner。

练习三：KG/1 负载已经解析成 DeviceId、时间戳和整数测量值，是否还要把原始 Memory 留在队列？参考答案：若业务只需要这些字段，保存独立数值即可；若审计确需原报文，必须在 AdvanceTo 前制作有上限的副本或转移独立 owner，并规定释放责任。性能优化从生命周期清楚开始，而不是从消除所有复制开始。

本章的类型选择与租期规则以 [Microsoft Memory/Span 指南](https://learn.microsoft.com/en-us/dotnet/standard/memory-and-spans/memory-t-usage-guidelines)为准；管道借用的边界还应遵守 [Pipelines 指南](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)。
