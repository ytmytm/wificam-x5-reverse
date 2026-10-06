#!/usr/bin/env python3
"""Probe the TXW817 local media socket after cloud endpoint negotiation."""

from __future__ import annotations

import argparse
import socket
import struct
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-ip", default="192.168.2.158")
    parser.add_argument("--camera-port", type=int, required=True)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--out", help="write received datagrams as length-prefixed records")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.listen_port))
    sock.settimeout(0.5)
    output = open(args.out, "wb") if args.out else None
    target = (args.camera_ip, args.camera_port)
    print(f"listening={args.listen_port} target={target}", flush=True)

    sock.sendto(b"DrApp send hello", target)
    next_heartbeat = time.monotonic()
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        now = time.monotonic()
        if now >= next_heartbeat:
            sock.sendto(b"App send heart for stun", target)
            next_heartbeat = now + 4
        try:
            data, address = sock.recvfrom(65535)
        except socket.timeout:
            continue
        if output:
            output.write(struct.pack("!dI", time.time(), len(data)))
            output.write(data)
            output.flush()
        print(f"recv={address} bytes={len(data)} hex={data[:32].hex()}", flush=True)

    if output:
        output.close()


if __name__ == "__main__":
    main()
