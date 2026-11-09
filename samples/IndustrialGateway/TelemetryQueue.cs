using System.Diagnostics.Metrics;
using System.Threading.Channels;

namespace IndustrialGateway;

public sealed class GatewayOptions
{
    public int TcpPort { get; set; } = 7810;
    public int HttpPort { get; set; } = 7812;
    public int QueueCapacity { get; set; } = 128;
    public int MaxConnections { get; set; } = 64;
    public int FrameTimeoutMilliseconds { get; set; } = 5000;
    // A laboratory knob that makes a slow sink observable; not a durability feature.
    public int ConsumerDelayMilliseconds { get; set; }

    public void Validate()
    {
        if (TcpPort is < 1 or > 65535 || HttpPort is < 1 or > 65535 || TcpPort == HttpPort)
            throw new ArgumentException("Use distinct TCP/HTTP ports in 1..65535.");
        if (QueueCapacity is < 1 or > 65536 || MaxConnections is < 1 or > 4096)
            throw new ArgumentException("QueueCapacity must be 1..65536; MaxConnections 1..4096.");
        if (FrameTimeoutMilliseconds is < 100 or > 300000 || ConsumerDelayMilliseconds is < 0 or > 300000)
            throw new ArgumentException("Invalid frame timeout or consumer delay.");
    }
}

public sealed class GatewayMetrics : IDisposable
{
    private readonly Meter meter = new("IndustrialGateway", "1.0.0");
    public Counter<long> Enqueued { get; }
    public Counter<long> Processed { get; }
    public Counter<long> ProtocolErrors { get; }
    public Counter<long> Timeouts { get; }
    public Counter<long> Rejected { get; }
    private int active;
    public void ConnectionOpened() => Interlocked.Increment(ref active);
    public void ConnectionEnded() => Interlocked.Decrement(ref active);

    public GatewayMetrics()
    {
        Enqueued = meter.CreateCounter<long>("gateway.enqueued", "messages");
        Processed = meter.CreateCounter<long>("gateway.processed", "messages");
        ProtocolErrors = meter.CreateCounter<long>("gateway.protocol_errors", "connections");
        Timeouts = meter.CreateCounter<long>("gateway.timeouts", "connections");
        Rejected = meter.CreateCounter<long>("gateway.rejected", "connections");
        // A late-attaching metrics reader still sees the current connection count.
        meter.CreateObservableGauge("gateway.active", () => Volatile.Read(ref active), "connections");
    }

    public void Dispose() => meter.Dispose();
}

public sealed class TelemetryQueue
{
    private readonly Channel<Telemetry> channel;
    public TelemetryQueue(GatewayOptions options)
    {
        channel = Channel.CreateBounded<Telemetry>(new BoundedChannelOptions(options.QueueCapacity)
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
            SingleWriter = false,
            AllowSynchronousContinuations = false
        });
    }

    public int Count => channel.Reader.Count;
    public ValueTask EnqueueAsync(Telemetry value, CancellationToken cancellationToken) =>
        channel.Writer.WriteAsync(value, cancellationToken);
    public IAsyncEnumerable<Telemetry> ReadAllAsync(CancellationToken cancellationToken) =>
        channel.Reader.ReadAllAsync(cancellationToken);
}

public sealed class TelemetryConsumer(
    TelemetryQueue queue, GatewayOptions options, GatewayMetrics metrics,
    ILogger<TelemetryConsumer> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        try
        {
            await foreach (var value in queue.ReadAllAsync(stoppingToken))
            {
                if (options.ConsumerDelayMilliseconds > 0)
                    await Task.Delay(options.ConsumerDelayMilliseconds, stoppingToken);
                // Log is a sink for the lesson. It is not a database or durable journal.
                logger.LogDebug("Sample Device={DeviceId} Sequence={Sequence} ValueMilliUnits={Value}",
                    value.DeviceId, value.Sequence, value.ValueMilliUnits);
                metrics.Processed.Add(1);
            }
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            logger.LogInformation("Consumer stopped; queued samples are volatile.");
        }
    }
}

// Singleton connection admission counter, shared by all invocations of the handler.
public sealed class ConnectionSlots(GatewayOptions options) : IDisposable
{
    private readonly SemaphoreSlim slots = new(options.MaxConnections, options.MaxConnections);
    public bool TryEnter() => slots.Wait(0);
    public void Exit() => slots.Release();
    public void Dispose() => slots.Dispose();
}
