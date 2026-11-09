#!/usr/bin/env python3
"""KG/1 device simulator. Python standard library only; defaults to loopback."""
import argparse
import socket
import struct
import time

HEADER = struct.Struct("!HBBIQ")
PAYLOAD = struct.Struct("!Iqi")


def frame(sequence=1, device=42, value=23500, timestamp=None):
    body = PAYLOAD.pack(device, int(time.time() * 1000) if timestamp is None else timestamp, value)
    return HEADER.pack(0x4B47, 1, 1, len(body), sequence) + body


def recv_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise EOFError(f"expected {count} bytes, received {len(result)}")
        result.extend(chunk)
    return bytes(result)


def read_ack(sock, expected):
    actual = HEADER.unpack(recv_exact(sock, HEADER.size))
    if actual != (0x4B47, 1, 2, 0, expected):
        raise ValueError(f"unexpected ACK {actual}; expected sequence {expected}")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7810)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--device", type=int, default=42)
    parser.add_argument("--value", type=int, default=23500, help="milli-units; 23500 means 23.500")
    parser.add_argument("--split", action="store_true", help="send one byte at a time (not guaranteed TCP packet boundaries)")
    parser.add_argument("--coalesce", action="store_true", help="send all frames in one sendall")
    args = parser.parse_args()
    if args.count < 1 or args.split and args.coalesce:
        parser.error("count must be positive; split and coalesce are mutually exclusive")
    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        if args.coalesce:
            sock.sendall(b"".join(frame(i, args.device, args.value) for i in range(1, args.count + 1)))
            for i in range(1, args.count + 1):
                read_ack(sock, i)
                print(f"ACK {i}: enqueued in volatile memory")
        else:
            for i in range(1, args.count + 1):
                packet = frame(i, args.device, args.value)
                if args.split:
                    for byte in packet:
                        sock.sendall(bytes([byte]))
                        time.sleep(0.005)
                else:
                    sock.sendall(packet)
                read_ack(sock, i)
                print(f"ACK {i}: enqueued in volatile memory")


if __name__ == "__main__":
    main()
