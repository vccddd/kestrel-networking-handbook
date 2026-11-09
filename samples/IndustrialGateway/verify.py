#!/usr/bin/env python3
"""Build and exercise isolated loopback gateway processes. Does not use a running GUI/server."""
import contextlib
import json
import pathlib
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request

from simulator import HEADER, frame, read_ack

ROOT = pathlib.Path(__file__).resolve().parent
SAMPLES = ROOT.parent
DLL = ROOT / "bin/Release/net10.0/IndustrialGateway.dll"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def server(*, delay=0, budget=1500, capacity=128, connections=64):
    tcp, http = free_port(), free_port()
    while http == tcp:
        http = free_port()
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen([
            "dotnet", str(DLL), f"--Gateway:TcpPort={tcp}", f"--Gateway:HttpPort={http}",
            f"--Gateway:ConsumerDelayMilliseconds={delay}", f"--Gateway:FrameTimeoutMilliseconds={budget}",
            f"--Gateway:QueueCapacity={capacity}", f"--Gateway:MaxConnections={connections}",
        ], cwd=SAMPLES, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{http}/health", timeout=0.5) as response:
                        health = json.load(response)
                    assert health["ack"] == "volatile-memory-enqueue-only"
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("gateway startup")
                    time.sleep(0.05)
            yield tcp, http
        except Exception:
            log.seek(0)
            print(log.read())
            raise
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def connect(port):
    return socket.create_connection(("127.0.0.1", port), timeout=5)


def expect_closed(sock):
    try:
        assert sock.recv(1) == b"", "unexpected response instead of connection close"
    except (ConnectionResetError, ConnectionAbortedError):
        pass


def main():
    subprocess.run(["dotnet", "build", "IndustrialGateway/IndustrialGateway.csproj", "-c", "Release", "--nologo"], cwd=SAMPLES, check=True)
    with server(connections=2) as (port, _):
        with connect(port) as sock:
            # Deterministic parser segmentation is tested in Checks; TCP may coalesce these writes.
            for b in frame(1):
                sock.sendall(bytes([b]))
            read_ack(sock, 1)
            sock.sendall(frame(2) + frame(3) + frame(4))
            sock.shutdown(socket.SHUT_WR)
            for sequence in (2, 3, 4):
                read_ack(sock, sequence)
            expect_closed(sock)
        print("PASS fragmented writes, coalesced frames, EOF with buffered data")
        for packet in (
            HEADER.pack(0x4B47, 1, 1, 0xFFFFFFFF, 1),
            HEADER.pack(0, 1, 1, 16, 1),
            HEADER.pack(0x4B47, 2, 1, 16, 1),
            HEADER.pack(0x4B47, 1, 2, 0, 1),
            HEADER.pack(0x4B47, 1, 1, 15, 1),
        ):
            with connect(port) as sock:
                sock.sendall(packet)
                expect_closed(sock)
        with connect(port) as sock:
            sock.sendall(frame()[:-1])
            sock.shutdown(socket.SHUT_WR)
            expect_closed(sock)
        print("PASS invalid magic/version/type/length and truncated EOF close connections")
        with connect(port) as sock:
            sock.sendall(frame(99))
            read_ack(sock, 99)
        print("PASS server remains available after protocol errors")
        with connect(port) as sock:
            started = time.monotonic()
            # Every byte arrives inside the idle interval, yet the whole-frame budget expires.
            for b in frame()[:12]:
                try:
                    sock.sendall(bytes([b]))
                except (BrokenPipeError, ConnectionResetError):
                    break
                time.sleep(0.2)
            expect_closed(sock)
            assert time.monotonic() - started < 4
        print("PASS absolute frame budget rejects slow trickle")
        with connect(port) as one, connect(port) as two:
            for sock in (one, two):
                sock.sendall(frame())
                read_ack(sock, 1)  # proves both slots are occupied before opening the third
            with connect(port) as rejected:
                expect_closed(rejected)
        print("PASS connection admission limit")
    with server(delay=900, budget=3000, capacity=1) as (port, _):
        with connect(port) as sock:
            started = time.monotonic()
            sock.sendall(frame(1) + frame(2) + frame(3))
            for sequence in (1, 2, 3):
                read_ack(sock, sequence)
            assert time.monotonic() - started >= 0.65, "slow sink did not apply backpressure"
        print("PASS bounded queue propagates slow-consumer backpressure to ACK")
    with server(delay=2500, budget=1000, capacity=1) as (port, _):
        with connect(port) as sock:
            sock.sendall(frame(1) + frame(2) + frame(3))
            read_ack(sock, 1)
            read_ack(sock, 2)
            expect_closed(sock)
        print("PASS full queue wait is canceled by frame budget")
    print("PASS all loopback integration checks; temporary processes stopped")


if __name__ == "__main__":
    main()
