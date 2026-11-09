using System.Net;
using IndustrialGateway;
using Microsoft.AspNetCore.Connections;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.AspNetCore.Server.Kestrel.Transport.Sockets;

var builder = WebApplication.CreateBuilder(args);
var options = new GatewayOptions();
builder.Configuration.GetSection("Gateway").Bind(options);
options.Validate();
builder.Services.AddSingleton(options);
builder.Services.AddSingleton<TelemetryQueue>();
builder.Services.AddSingleton<GatewayMetrics>();
builder.Services.AddSingleton<ConnectionSlots>();
builder.Services.AddSingleton<GatewayConnectionHandler>();
builder.Services.AddHostedService<TelemetryConsumer>();
// HTTP limits are not a substitute for raw TCP transport limits.
builder.Services.Configure<SocketTransportOptions>(transport =>
{
    // Leave headroom for this small bounded protocol. Progress also depends on the
    // reader's examined position, not just message size versus the watermark.
    // Thresholds are flow-control watermarks, not an exact total-memory ceiling.
    transport.MaxReadBufferSize = 16 * 1024;
    transport.MaxWriteBufferSize = 16 * 1024;
});
builder.WebHost.ConfigureKestrel(server =>
{
    server.Listen(IPAddress.Loopback, options.TcpPort, endpoint =>
        endpoint.UseConnectionHandler<GatewayConnectionHandler>());
    server.Listen(IPAddress.Loopback, options.HttpPort, endpoint =>
        endpoint.Protocols = HttpProtocols.Http1);
});
var app = builder.Build();
app.MapGet("/health", (TelemetryQueue queue) => Results.Ok(new
{
    status = "ok", queued = queue.Count, ack = "volatile-memory-enqueue-only"
}));
app.Run();
