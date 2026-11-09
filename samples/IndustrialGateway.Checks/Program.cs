using System.Buffers;
using System.Buffers.Binary;
using System.IO.Pipelines;
using System.Diagnostics.Metrics;
using IndustrialGateway;

var count = 0;
void Check(bool condition, string message)
{
    if (!condition) throw new Exception(message);
    count++;
}
byte[] Encode(ulong sequence = 7)
{
    var bytes = new byte[32];
    BinaryPrimitives.WriteUInt16BigEndian(bytes, FrameCodec.Magic);
    bytes[2] = 1;
    bytes[3] = 1;
    BinaryPrimitives.WriteUInt32BigEndian(bytes.AsSpan(4), 16);
    BinaryPrimitives.WriteUInt64BigEndian(bytes.AsSpan(8), sequence);
    BinaryPrimitives.WriteUInt32BigEndian(bytes.AsSpan(16), uint.MaxValue);
    BinaryPrimitives.WriteInt64BigEndian(bytes.AsSpan(20), 1720000000123);
    BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(28), -12345);
    return bytes;
}
var expected = new Telemetry(7, uint.MaxValue, 1720000000123, -12345);
var packet = Encode();
for (int length = 0; length < packet.Length; length++)
{
    var partial = new ReadOnlySequence<byte>(packet.AsMemory(0, length));
    var original = partial;
    Check(!FrameCodec.TryReadTelemetry(ref partial, out _), $"partial {length} was accepted");
    Check(partial.Start.Equals(original.Start) && partial.Length == length, "incomplete input advanced");
}
for (int split = 1; split < packet.Length; split++)
{
    var first = new Segment(packet.AsMemory(0, split));
    var last = first.Append(packet.AsMemory(split));
    var sequence = new ReadOnlySequence<byte>(first, 0, last, last.Memory.Length);
    Check(FrameCodec.TryReadTelemetry(ref sequence, out var value), $"multi-segment split {split}");
    Check(value == expected && sequence.IsEmpty, $"decoded fields at split {split}");
}
{
    var first = new Segment(ReadOnlyMemory<byte>.Empty);
    var last = first;
    foreach (byte b in packet) last = last.Append(new byte[] { b });
    var sequence = new ReadOnlySequence<byte>(first, 0, last, last.Memory.Length);
    Check(FrameCodec.TryReadTelemetry(ref sequence, out var value) && value == expected, "one byte segments");
}
{
    var sequence = new ReadOnlySequence<byte>(packet.Concat(Encode(8)).ToArray());
    Check(FrameCodec.TryReadTelemetry(ref sequence, out var first) && first.Sequence == 7, "first coalesced");
    Check(FrameCodec.TryReadTelemetry(ref sequence, out var second) && second.Sequence == 8 && sequence.IsEmpty, "second coalesced");
}
foreach (var (offset, value, reason) in new[] { (0, (byte)0, "magic"), (2, (byte)2, "version"), (3, (byte)2, "type") })
{
    var invalid = (byte[])packet.Clone();
    invalid[offset] = value;
    var sequence = new ReadOnlySequence<byte>(invalid);
    bool rejected = false;
    try { FrameCodec.TryReadTelemetry(ref sequence, out _); }
    catch (InvalidDataException) { rejected = true; }
    Check(rejected && sequence.Length == 32, $"reject {reason} without consuming");
}
foreach (uint length in new uint[] { 0, 15, 17, 4096, 4097, uint.MaxValue })
{
    var header = packet[..16];
    BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(4), length);
    var sequence = new ReadOnlySequence<byte>(header);
    bool rejected = false;
    try { FrameCodec.TryReadTelemetry(ref sequence, out _); }
    catch (InvalidDataException) { rejected = true; }
    Check(rejected, $"length {length} must be rejected from header alone");
}
// EOF may accompany multiple complete frames; ReadOne must not wait for a new byte.
{
    var pipe = new Pipe();
    await pipe.Writer.WriteAsync(packet.Concat(Encode(8)).ToArray());
    await pipe.Writer.CompleteAsync();
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    Check(await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, timeout.Token) == expected, "EOF first");
    Check((await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, timeout.Token))?.Sequence == 8, "EOF second");
    Check(await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, timeout.Token) is null, "clean EOF");
    await pipe.Reader.CompleteAsync();
}
{
    var pipe = new Pipe();
    await pipe.Writer.WriteAsync(packet[..31]);
    await pipe.Writer.CompleteAsync();
    bool rejected = false;
    try { await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, CancellationToken.None); }
    catch (InvalidDataException ex) when (ex.Message == "truncated_frame") { rejected = true; }
    Check(rejected, "truncated EOF");
    await pipe.Reader.CompleteAsync();
}
{
    var pipe = new Pipe();
    FrameCodec.WriteAck(pipe.Writer, ulong.MaxValue);
    await pipe.Writer.FlushAsync();
    var result = await pipe.Reader.ReadAsync();
    var ack = result.Buffer.ToArray();
    Check(ack.Length == 16 && ack[3] == 2, "ACK header");
    Check(BinaryPrimitives.ReadUInt32BigEndian(ack.AsSpan(4)) == 0 &&
          BinaryPrimitives.ReadUInt64BigEndian(ack.AsSpan(8)) == ulong.MaxValue, "ACK length/sequence");
    pipe.Reader.AdvanceTo(result.Buffer.End);
    await pipe.Writer.CompleteAsync();
    await pipe.Reader.CompleteAsync();
}
// Bounded Wait means a producer really waits, and cancellation must release it.
{
    var queue = new TelemetryQueue(new GatewayOptions { QueueCapacity = 1 });
    await queue.EnqueueAsync(expected, CancellationToken.None);
    using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
    var pending = queue.EnqueueAsync(expected, timeout.Token);
    Check(!pending.IsCompleted, "full queue must suspend producer");
    bool cancelled = false;
    try { await pending; }
    catch (OperationCanceledException) { cancelled = true; }
    Check(cancelled && queue.Count == 1, "full queue cancellation");
}
// Distinguish network arrival over multiple reads from memory segmentation.
foreach (int cut in new[] { 1, 15, 16, 20, 31 })
{
    var pipe = new Pipe();
    var observed = new ObservedReader(pipe.Reader);
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    var pending = GatewayConnectionHandler.ReadOneAsync(observed, timeout.Token).AsTask();
    await pipe.Writer.WriteAsync(packet.AsMemory(0, cut));
    // Handshake: the parser must observe and advance the partial input BEFORE we
    // provide the rest. Otherwise scheduling could coalesce both writes into one read.
    await observed.FirstAdvance.Task.WaitAsync(timeout.Token);
    Check(!pending.IsCompleted && observed.Reads == 1 && observed.Advances == 1 &&
        observed.ConsumedAtStart && observed.ExaminedAtEnd, $"incremental read must wait at {cut}");
    await pipe.Writer.WriteAsync(packet.AsMemory(cut));
    Check(await pending == expected && observed.Reads == 2 && observed.Advances == 2 &&
        observed.ConsumedEqualsExamined, $"incremental read completes at {cut}");
    await pipe.Writer.CompleteAsync();
    await observed.CompleteAsync();
}
// CancelPendingRead returns a result; token cancellation may throw before a result exists.
{
    var pipe = new Pipe();
    pipe.Reader.CancelPendingRead();
    bool cancelled = false;
    try { await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, CancellationToken.None); }
    catch (OperationCanceledException) { cancelled = true; }
    Check(cancelled, "IsCanceled result is handled");
    // Another read proves that the canceled result's borrow was ended with AdvanceTo.
    await pipe.Writer.WriteAsync(packet);
    Check(await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, CancellationToken.None) == expected,
        "read after cancellation result");
    await pipe.Writer.CompleteAsync();
    await pipe.Reader.CompleteAsync();
}
{
    var pipe = new Pipe();
    using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
    bool cancelled = false;
    try { await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, timeout.Token); }
    catch (OperationCanceledException) { cancelled = true; }
    Check(cancelled, "ReadAsync token cancellation");
    await pipe.Writer.WriteAsync(packet);
    Check(await GatewayConnectionHandler.ReadOneAsync(pipe.Reader, CancellationToken.None) == expected,
        "read after token cancellation");
    await pipe.Writer.CompleteAsync();
    await pipe.Reader.CompleteAsync();
}
// An observable current-state metric must include connections opened before attachment.
{
    using var metrics = new GatewayMetrics();
    metrics.ConnectionOpened();
    using var listener = new MeterListener();
    int observed = -99;
    listener.InstrumentPublished = (instrument, owner) =>
    {
        if (instrument.Meter.Name == "IndustrialGateway" && instrument.Name == "gateway.active")
            owner.EnableMeasurementEvents(instrument);
    };
    listener.SetMeasurementEventCallback<int>((_, value, _, _) => observed = value);
    listener.Start();
    listener.RecordObservableInstruments();
    Check(observed == 1, "late metrics listener sees existing connection");
    metrics.ConnectionEnded();
    listener.RecordObservableInstruments();
    Check(observed == 0, "current connection gauge returns to zero");
}
Console.WriteLine($"PASS {count} assertions: incomplete, multi-segment, incremental, coalesced, invalid, EOF, cancellation, ACK, bounded queue, metrics.");

sealed class ObservedReader(PipeReader inner) : PipeReader
{
    private SequencePosition start;
    private SequencePosition end;
    public int Reads { get; private set; }
    public int Advances { get; private set; }
    public bool ConsumedAtStart { get; private set; }
    public bool ExaminedAtEnd { get; private set; }
    public bool ConsumedEqualsExamined { get; private set; }
    public TaskCompletionSource FirstAdvance { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    private void Observe(ReadResult result)
    {
        Reads++;
        start = result.Buffer.Start;
        end = result.Buffer.End;
    }
    public override async ValueTask<ReadResult> ReadAsync(CancellationToken cancellationToken = default)
    {
        var result = await inner.ReadAsync(cancellationToken);
        Observe(result);
        return result;
    }
    public override bool TryRead(out ReadResult result)
    {
        if (!inner.TryRead(out result)) return false;
        Observe(result);
        return true;
    }
    public override void AdvanceTo(SequencePosition consumed) => AdvanceTo(consumed, consumed);
    public override void AdvanceTo(SequencePosition consumed, SequencePosition examined)
    {
        ConsumedAtStart = consumed.Equals(start);
        ExaminedAtEnd = examined.Equals(end);
        ConsumedEqualsExamined = consumed.Equals(examined);
        inner.AdvanceTo(consumed, examined);
        Advances++;
        FirstAdvance.TrySetResult();
    }
    public override void CancelPendingRead() => inner.CancelPendingRead();
    public override void Complete(Exception? exception = null) => inner.Complete(exception);
    public override ValueTask CompleteAsync(Exception? exception = null) => inner.CompleteAsync(exception);
}

sealed class Segment : ReadOnlySequenceSegment<byte>
{
    public Segment(ReadOnlyMemory<byte> memory) => Memory = memory;
    public Segment Append(ReadOnlyMemory<byte> memory)
    {
        var next = new Segment(memory) { RunningIndex = RunningIndex + Memory.Length };
        Next = next;
        return next;
    }
}
