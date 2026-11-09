using System.Buffers;
using System.Buffers.Binary;
using System.IO.Pipelines;

namespace IndustrialGateway;

// All fields own their values: none borrow the PipeReader's buffers.
public readonly record struct Telemetry(
    ulong Sequence, uint DeviceId, long UnixTimeMilliseconds, int ValueMilliUnits);

public static class FrameCodec
{
    public const ushort Magic = 0x4B47; // ASCII "KG"
    public const byte Version = 1;
    public const byte TelemetryType = 1;
    public const byte AckType = 2;
    public const int HeaderSize = 16;
    public const int TelemetryPayloadSize = 16;
    public const uint MaxPayloadSize = 4096;

    // false = incomplete, exception = invalid, true = one complete owned value.
    // On false/exception the caller's buffer is unchanged.
    public static bool TryReadTelemetry(ref ReadOnlySequence<byte> buffer, out Telemetry measurement)
    {
        measurement = default;
        if (buffer.Length < HeaderSize)
            return false;

        var reader = new SequenceReader<byte>(buffer);
        Span<byte> header = stackalloc byte[HeaderSize];
        if (!reader.TryCopyTo(header))
            return false;
        reader.Advance(HeaderSize);

        if (BinaryPrimitives.ReadUInt16BigEndian(header) != Magic)
            throw new InvalidDataException("invalid_magic");
        if (header[2] != Version)
            throw new InvalidDataException("unsupported_version");
        if (header[3] != TelemetryType)
            throw new InvalidDataException("unexpected_message_type");

        uint payloadLength = BinaryPrimitives.ReadUInt32BigEndian(header[4..]);
        // Validate before narrowing, allocating, or waiting for a claimed body.
        if (payloadLength > MaxPayloadSize)
            throw new InvalidDataException("payload_too_large");
        if (payloadLength != TelemetryPayloadSize)
            throw new InvalidDataException("invalid_telemetry_length");
        if (reader.Remaining < payloadLength)
            return false;

        Span<byte> payload = stackalloc byte[TelemetryPayloadSize];
        if (!reader.TryCopyTo(payload))
            return false;
        reader.Advance(TelemetryPayloadSize);

        measurement = new Telemetry(
            BinaryPrimitives.ReadUInt64BigEndian(header[8..]),
            BinaryPrimitives.ReadUInt32BigEndian(payload),
            BinaryPrimitives.ReadInt64BigEndian(payload[4..]),
            BinaryPrimitives.ReadInt32BigEndian(payload[12..]));
        buffer = buffer.Slice(reader.Position);
        return true;
    }

    // Synchronous serialization: the caller owns the subsequent async flush.
    public static void WriteAck(PipeWriter output, ulong sequence)
    {
        Span<byte> header = output.GetSpan(HeaderSize)[..HeaderSize];
        BinaryPrimitives.WriteUInt16BigEndian(header, Magic);
        header[2] = Version;
        header[3] = AckType;
        BinaryPrimitives.WriteUInt32BigEndian(header[4..], 0);
        BinaryPrimitives.WriteUInt64BigEndian(header[8..], sequence);
        output.Advance(HeaderSize);
    }
}
