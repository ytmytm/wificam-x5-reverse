#!/usr/bin/env python3
"""Depacketize the TXW817 video packets and rebuild RFC 2435 JPEG frames."""

import argparse
import re
import struct
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


def extract_udp_payloads(pcap: str, source: str = "192.168.2.158") -> List[bytes]:
    """Extract camera UDP payloads without requiring scapy/tshark."""
    proc = subprocess.run(
        ["tcpdump", "-nn", "-r", pcap, "udp", "-xx"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    packets = []  # type: List[bytes]
    current = None  # type: Optional[bytearray]
    wanted = False
    for line in proc.stdout.splitlines():
        if line and not line.startswith("\t"):
            if current is not None and wanted:
                packets.append(bytes(current))
            current = bytearray()
            wanted = f"IP {source}." in line and " > " in line
        elif line.startswith("\t0x") and current is not None:
            fields = line.split()
            for word in fields[1:]:
                if re.fullmatch(r"[0-9a-fA-F]{2,4}", word):
                    current.extend(bytes.fromhex(word))
    if current is not None and wanted:
        packets.append(bytes(current))

    payloads = []  # type: List[bytes]
    for packet in packets:
        if len(packet) < 42 or packet[12:14] != b"\x08\x00":
            continue
        ip = 14
        ihl = (packet[ip] & 0x0f) * 4
        if packet[ip + 9] != 17:
            continue
        udp = ip + ihl
        payloads.append(packet[udp + 8:])
    return payloads


def depacketize(payloads: List[bytes]) -> List[bytes]:
    """Group video fragments by frame id and return raw RFC2435 JPEG bodies."""
    groups = defaultdict(dict)  # type: Dict[bytes, Dict[int, bytes]]
    for payload in payloads:
        if len(payload) < 16 or payload[:2] not in (b"\x80\x1a", b"\x80\x9a"):
            continue
        frame_id = payload[6:12]
        offset = struct.unpack("!I", payload[12:16])[0]
        # The firmware repeats the 8-byte JPEG/restart header in every fragment.
        # The first fragment additionally carries the 4-byte quantizer length
        # and 128-byte quantizer data.
        fragment = payload[16:]
        if len(fragment) < 12:
            continue
        groups[frame_id][offset] = fragment

    frames = []  # type: List[bytes]
    for fragments in groups.values():
        if not fragments or min(fragments) != 0:
            continue
        first = fragments[0]
        if len(first) < 140 or not first.startswith(b"\x41\xff"):
            continue
        header = first[:140]
        scan = [fragments[0][140:]]
        scan.extend(fragments[offset][8:] for offset in sorted(fragments) if offset != 0)
        body = header + b"".join(scan)
        if body.startswith(b"\x41\xff") and b"\xff\xd9" in body:
            frames.append(body[: body.rfind(b"\xff\xd9") + 2])
    return frames


def _segments(jpeg: bytes) -> List[bytes]:
    out = []
    pos = 2
    while pos + 4 <= len(jpeg):
        if jpeg[pos] != 0xff:
            break
        marker = jpeg[pos + 1]
        if marker == 0xda:
            out.append(jpeg[pos:])
            break
        if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            pos += 2
            continue
        length = int.from_bytes(jpeg[pos + 2:pos + 4], "big")
        out.append(jpeg[pos:pos + 2 + length])
        pos += 2 + length
    return out


def _dqt(table: bytes, zigzag: bool) -> bytes:
    # RFC 2435 carries quantizers in zig-zag order; JPEG DQT uses natural order.
    zigzag_order = (0, 1, 5, 6, 14, 15, 27, 28, 2, 4, 7, 13, 16, 26, 29,
                    42, 3, 8, 12, 17, 25, 30, 41, 43, 54, 9, 11, 18, 24,
                    31, 40, 44, 53, 55, 63, 10, 19, 23, 32, 39, 45, 52,
                    56, 62, 20, 22, 33, 38, 46, 51, 57, 61, 21, 34, 37,
                    47, 50, 58, 60, 35, 36, 48, 49, 59)
    values = bytearray(128)
    for table_no in range(2):
        src = table[table_no * 64:(table_no + 1) * 64]
        for i, value in enumerate(src):
            values[table_no * 64 + (zigzag_order[i] if zigzag else i)] = value
    return b"\xff\xdb\x00\x84\x00" + bytes(values[:64]) + b"\x01" + bytes(values[64:])


STANDARD_DHT = bytes.fromhex(
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffc400b5100002010303020403050504040000017d010203000411051221314106"
    "13516107227114328191a1082342b1c11552d1f02433627282090a161718191a25"
    "262728292a3435363738393a434445464748494a535455565758595a6364656667"
    "68696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6"
    "a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2"
    "e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"
    "ffc4001f0100030101010101010101010000000000000102030405060708090a0b"
    "ffc400b51100020102040403040705040400010277000102031104052131061241"
    "510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718"
    "191a262728292a35363738393a434445464748494a535455565758595a63646566"
    "6768696a737475767778797a82838485868788898a92939495969798999aa2a3a4"
    "a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2"
    "e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa"
)
STANDARD_SOS = bytes.fromhex("ffda000c03010002110311003f00")


def rebuild_jpeg(body: bytes, zigzag: bool = False, sampling: bytes = b"\x22") -> bytes:
    if len(body) < 140 or body[:4] != b"\x41\xff\x50\x3c":
        raise ValueError("not a TXW817 640x480 JPEG body")
    sof = bytearray.fromhex("ffc0001108")
    sof += struct.pack("!HHB", 480, 640, 3)
    sof += bytes((1, sampling[0], 0, 2, 0x11, 1, 3, 0x11, 1))
    entropy = body[140:]
    header = bytearray(b"\xff\xd8")
    header += _dqt(body[12:140], zigzag)
    # The extended RTP/JPEG header reports a restart interval of 40 MCUs.
    header += b"\xff\xdd\x00\x04\x00\x28"
    header += sof
    header += STANDARD_DHT
    header += STANDARD_SOS
    return bytes(header) + entropy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap")
    ap.add_argument("--out", default="/tmp/x5-jpegs")
    args = ap.parse_args()
    frames = depacketize(extract_udp_payloads(args.pcap))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, body in enumerate(frames):
        (out / f"raw-{i:04d}.bin").write_bytes(body)
        for zigzag in (False, True):
            for sampling in (b"\x22", b"\x21", b"\x11"):
                name = f"frame-{i:04d}-{'zz' if zigzag else 'nat'}-{sampling.hex()}.jpg"
                (out / name).write_bytes(rebuild_jpeg(body, zigzag, sampling))
    print(f"payloads={len(extract_udp_payloads(args.pcap))} frames={len(frames)} out={out}")


if __name__ == "__main__":
    main()
