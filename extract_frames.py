#!/usr/bin/env python3
"""Print TCP payloads from a pcap using tcpdump's hex output.

This intentionally uses the system tcpdump instead of a third-party pcap
library, so it also works on the old Home Assistant server/router toolchain.
"""

from __future__ import annotations

import argparse
import re
import struct
import subprocess
from collections import defaultdict


PACKET_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ IP ")
HEX_RE = re.compile(r"^\s*0x[0-9a-f]+:")


def packets(path: str):
    command = ["tcpdump", "-tttt", "-nn", "-XX", "-s", "0", "-r", path, "tcp"]
    output = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL)
    current = None
    for line in output.splitlines():
        if PACKET_RE.match(line):
            if current:
                yield current
            current = {"header": line, "chunks": []}
        elif current and HEX_RE.match(line):
            # tcpdump separates the hex columns from the printable ASCII with
            # two spaces; ignore the ASCII part to avoid false hex matches.
            columns = line.split(":", 1)[1].lstrip().split("  ", 1)[0]
            words = re.findall(r"\b[0-9a-f]{2}\b|\b[0-9a-f]{4}\b", columns)
            current["chunks"].append(bytes.fromhex("".join(words)))
    if current:
        yield current


def payload(packet):
    raw = b"".join(packet["chunks"])
    if len(raw) < 54 or raw[14 + 9] != 6:
        return None
    ip_offset = 14
    ip_header_len = (raw[ip_offset] & 0x0F) * 4
    tcp_offset = ip_offset + ip_header_len
    tcp_header_len = (raw[tcp_offset + 12] >> 4) * 4
    sequence = struct.unpack("!I", raw[tcp_offset + 4 : tcp_offset + 8])[0]
    return sequence, raw[tcp_offset + tcp_header_len :]


def decode(payload_bytes: bytes):
    if not payload_bytes.startswith(b"\x86\x86"):
        return None
    if len(payload_bytes) < 24:
        return {"kind": "short", "hex": payload_bytes.hex()}
    uid_len = int.from_bytes(payload_bytes[8:10], "big")
    uid_start = 10
    uid_end = uid_start + uid_len
    body = payload_bytes[uid_end:-2]
    result = {
        "flags": payload_bytes[2:4].hex(),
        "length": int.from_bytes(payload_bytes[4:6], "big"),
        "sequence": int.from_bytes(payload_bytes[6:8], "big"),
        "uid": payload_bytes[uid_start:uid_end].decode("ascii", "replace"),
        "body": body.hex(),
        "checksum": payload_bytes[-2],
    }
    if len(body) >= 4 and body[1:3] == b"\x00\x01":
        result["cmd_type"] = body[0]
        result["content"] = chr(body[3])
    return result


def frame_length(data: bytes):
    """Return one protocol frame's size, or None if it is incomplete."""
    if len(data) < 6 or data[:2] != b"\x86\x86":
        return None
    # The protocol's length field excludes the first 12 bytes.
    total = 12 + int.from_bytes(data[4:6], "big")
    if len(data) < total or data[total - 1] != 0x0D:
        return None
    # The last two bytes are XOR checksum and frame terminator.
    checksum = 0
    for byte in data[: total - 2]:
        checksum ^= byte
    return total if checksum == data[total - 2] else None


def split_frames(data: bytes):
    """Split a reassembled TCP byte stream into complete protocol frames."""
    offset = 0
    while offset + 2 <= len(data):
        start = data.find(b"\x86\x86", offset)
        if start < 0:
            return
        if start + 6 > len(data):
            return
        size = 12 + int.from_bytes(data[start + 4 : start + 6], "big")
        if start + size > len(data):
            return
        candidate = data[start : start + size]
        if candidate[-1] != 0x0D or __import__("functools").reduce(
            lambda a, b: a ^ b, candidate[:-2], 0
        ) != candidate[-2]:
            offset = start + 2
            continue
        yield data[start : start + size]
        offset = start + size


def streams(path: str):
    """Reassemble TCP payloads per direction, ignoring retransmissions."""
    segments = defaultdict(dict)
    for packet in packets(path):
        decoded_payload = payload(packet)
        if not decoded_payload:
            continue
        sequence, raw_payload = decoded_payload
        if not raw_payload:
            continue
        fields = packet["header"].split()
        if len(fields) < 6:
            continue
        flow = (fields[3], fields[5].rstrip(":"))
        segments[flow].setdefault(sequence, raw_payload)

    for flow, flow_segments in segments.items():
        stream = bytearray()
        cursor = None
        for sequence in sorted(flow_segments):
            chunk = flow_segments[sequence]
            if cursor is None:
                cursor = sequence
            if sequence < cursor:
                chunk = chunk[cursor - sequence :]
            if sequence >= cursor:
                stream.extend(chunk)
                cursor = sequence + len(flow_segments[sequence])
        yield flow, bytes(stream)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcap")
    args = parser.parse_args()
    for flow, stream in streams(args.pcap):
        for raw_frame in split_frames(stream):
            decoded = decode(raw_frame)
            if decoded:
                print(f"flow={flow} frame_hex={raw_frame.hex()}")
                print(f"frame={decoded}")


if __name__ == "__main__":
    main()
