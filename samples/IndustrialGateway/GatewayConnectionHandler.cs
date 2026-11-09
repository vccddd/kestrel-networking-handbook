using System.IO.Pipelines;
using Microsoft.AspNetCore.Connections;

namespace IndustrialGateway;

public sealed class GatewayConnectionHandler(
    TelemetryQueue queue, GatewayOptions options, ConnectionSlots slots,
    GatewayMetrics metrics, IHostApplicationLifetime lifetime,
    ILogger<GatewayConnectionHandler> logger) : ConnectionHandler
{
    public override async Task OnConnectedAsync(ConnectionContext connection)
    {
        if (!slots.TryEnter())
        {
            metrics.Rejected.Add(1);
            connection.Abort(new ConnectionAbortedException("connection_limit"));
            return;
        }

        metrics.ConnectionOpened();
        var input = connection.Transport.Input;
        var output = connection.Transport.Output;
        // Socket transport can signal ConnectionClosed on receive FIN while buffered
        // frames still need processing. Read EOF/errors from the pipe; only host stop
        // eagerly cancels the whole operation. The frame budget bounds queue/flush waits.
        using var closed = CancellationTokenSource.CreateLinkedTokenSource(lifetime.ApplicationStopping);
        Exception? completionError = null;
        try
        {
            while (!closed.IsCancellationRequested)
            {
                // One absolute budget per frame. Receiving another byte does not reset it.
                using var budget = CancellationTokenSource.CreateLinkedTokenSource(closed.Token);
                budget.CancelAfter(options.FrameTimeoutMilliseconds);
                Telemetry? value = await ReadOneAsync(input, budget.Token);
                if (value is null)
                    break; // clean EOF, after any complete buffered frames have been processed

                // ReadOneAsync has already advanced the input. Only owned scalar fields survive.
                await queue.EnqueueAsync(value.Value, budget.Token);
                metrics.Enqueued.Add(1);
                FrameCodec.WriteAck(output, value.Value.Sequence);
                FlushResult flush = await output.FlushAsync(budget.Token);
                if (flush.IsCanceled || flush.IsCompleted)
                    break;
            }
        }
        catch (OperationCanceledException) when (closed.IsCancellationRequested)
        {
            logger.LogDebug("Connection {ConnectionId} closed or host stopping", connection.ConnectionId);
        }
        catch (OperationCanceledException ex)
        {
            completionError = ex;
            metrics.Timeouts.Add(1);
            logger.LogInformation("Connection {ConnectionId}: frame_budget_exceeded", connection.ConnectionId);
            connection.Abort(new ConnectionAbortedException("frame_budget_exceeded", ex));
        }
        catch (InvalidDataException ex)
        {
            completionError = ex;
            metrics.ProtocolErrors.Add(1);
            logger.LogInformation("Connection {ConnectionId}: {Reason}", connection.ConnectionId, ex.Message);
            connection.Abort(new ConnectionAbortedException("invalid_frame", ex));
        }
        catch (IOException ex)
        {
            completionError = ex;
            logger.LogDebug(ex, "Connection {ConnectionId}: transport ended", connection.ConnectionId);
        }
        finally
        {
            // This terminal handler owns application use of both pipes; Kestrel owns the context.
            try
            {
                await input.CompleteAsync(completionError);
            }
            finally
            {
                try { await output.CompleteAsync(completionError); }
                finally
                {
                    slots.Exit();
                    metrics.ConnectionEnded();
                }
            }
        }
    }

    public static async ValueTask<Telemetry?> ReadOneAsync(PipeReader input, CancellationToken token)
    {
        while (true)
        {
            ReadResult result = await input.ReadAsync(token);
            var remaining = result.Buffer;
            var consumed = remaining.Start;
            var examined = remaining.End;
            try
            {
                if (result.IsCanceled)
                    throw new OperationCanceledException(token);
                if (FrameCodec.TryReadTelemetry(ref remaining, out var value))
                {
                    consumed = remaining.Start;
                    // One frame per call: do not claim to have examined a coalesced next frame.
                    examined = consumed;
                    return value;
                }
                if (result.IsCompleted)
                {
                    if (!remaining.IsEmpty)
                        throw new InvalidDataException("truncated_frame");
                    return null;
                }
            }
            finally
            {
                // Exactly one AdvanceTo per successful ReadAsync, including error/cancel paths.
                input.AdvanceTo(consumed, examined);
            }
        }
    }
}
