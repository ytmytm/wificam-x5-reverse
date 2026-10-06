# TXW817 cloud-control protocol notes

The stock firmware does not answer the `iLnk/PPPP` LAN discovery packet. It
opens a TCP control session to a cloud server on port `8882`. In the capture,
the active session was:

```text
camera 192.168.2.158:50361
server 47.97.71.82:8882
```

## Frame shape

Frames observed from the server to the camera start with:

```text
86 86 83 01  <length:u16> <sequence:u16> 00 0c
<12-byte ASCII UID>
<command body>
<checksum> 0d
```

For UI commands, the body is four bytes:

```text
<cmdType> 00 01 <ASCII content>
```

Examples captured from the camera's real cloud session:

```text
03 00 01 30    cmdType 3, content "0"
03 00 01 31    cmdType 3, content "1"
07 00 01 31    cmdType 7, content "1"
16 00 01 32    cmdType 22, content "2"
08 00 01 31    cmdType 8, content "1"
09 00 01 30    cmdType 9, content "0"
```

The final byte before `0d` changes with the frame and is not yet understood;
it is an XOR checksum: XOR of every byte from the first `86` through the last
body byte (excluding the checksum itself and the final `0d`).  For example,
the command frame ending in `03 00 01 30 b2 0d` has checksum `b2`.

The length field gives the number of bytes after the first 12 bytes, so a full
frame occupies `12 + length` bytes.  This matters because several TCP packets
contain multiple concatenated frames.

`protocol.py` contains a small encoder.  A captured LED-off command can be
recreated with:

```sh
./x5-reverse/protocol.py
```

## Command correlation

The HTTP API request and the binary frame are separated by roughly 1–2 seconds:

```text
phone -> ipc.gps555.net: POST /api/v1/cmd/send-cmd
phone -> cloud:         {"cmdType":"3", ...}
cloud -> camera:        86 86 83 01 ... 03 00 01 30 ... 0d
```

The camera sends a short response frame after each command. It also sends
periodic status/heartbeat frames, so command correlation must use timestamps
and the command body rather than frame count alone.

## Quality and audio caveat

The controlled capture contains the following quality sequence:

```text
cmdType 22: 2, 1, 0, 1
```

The application action order suggests these are image-profile changes, but the
captured media packets all advertise `640x480`. The values are therefore
implemented as neutral profiles `0`, `1`, and `2` until a new capture confirms
which one means SD, HD, or FHD.

Audio was toggled twice during the same capture. No cloud command or separate
audio-control HTTP request was observed; audio-sized UDP packets continued
throughout. The current evidence points to a local mute/unmute operation in
the mobile app rather than a camera setting.
