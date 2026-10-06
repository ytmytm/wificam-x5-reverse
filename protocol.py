#!/usr/bin/env python3
"""Small TXW817 frame encoder/decoder used by the local relay work."""

from __future__ import annotations


MAGIC = b"\x86\x86"
UID_MARKER = b"\x00\x0c"
END = b"\x0d"


def xor_checksum(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def build_frame(flags: int, sequence: int, uid: str, body: bytes) -> bytes:
    """Build one frame as observed on the camera's TCP control session."""
    uid_bytes = uid.encode("ascii")
    if len(uid_bytes) != 12:
        raise ValueError("TXW817 UID must be exactly 12 ASCII bytes")
    if not 0 <= flags <= 0xFFFF or not 0 <= sequence <= 0xFFFF:
        raise ValueError("flags and sequence must fit in uint16")
    if len(body) > 0xFFFF - 14:
        raise ValueError("body is too large")

    # length is the number of bytes after the first 12 bytes of the frame.
    # For the normal frame layout this is 16 for a 4-byte command body.
    length = 4 + len(body) + 8
    frame = (
        MAGIC
        + flags.to_bytes(2, "big")
        + length.to_bytes(2, "big")
        + sequence.to_bytes(2, "big")
        + UID_MARKER
        + uid_bytes
        + body
    )
    return frame + bytes([xor_checksum(frame)]) + END


def build_command(sequence: int, uid: str, command: int, content: str) -> bytes:
    if not 0 <= command <= 0xFF:
        raise ValueError("command must fit in one byte")
    content_bytes = content.encode("ascii")
    if len(content_bytes) != 1:
        raise ValueError("UI command content must be one ASCII byte")
    return build_frame(0x8301, sequence, uid, bytes([command, 0, 1]) + content_bytes)


def parse_frame(frame: bytes) -> dict:
    if len(frame) < 24 or frame[:2] != MAGIC or frame[-1:] != END:
        raise ValueError("not a complete TXW817 frame")
    length = int.from_bytes(frame[4:6], "big")
    if len(frame) != 12 + length:
        raise ValueError("length field does not match frame size")
    if xor_checksum(frame[:-2]) != frame[-2]:
        raise ValueError("invalid XOR checksum")
    uid = frame[10:22].decode("ascii")
    return {
        "flags": int.from_bytes(frame[2:4], "big"),
        "length": length,
        "sequence": int.from_bytes(frame[6:8], "big"),
        "uid": uid,
        "body": frame[22:-2],
    }


if __name__ == "__main__":
    sample = build_command(0x11, "276642521291", 3, "0")
    print(sample.hex())
    print(parse_frame(sample))
