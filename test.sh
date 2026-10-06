#!/bin/sh
set -eu

cd "$(dirname "$0")"
python3 -m py_compile rtp_jpeg.py x5_viewer.py protocol.py extract_frames.py udp_probe.py
printf '%s\n' "Python syntax check passed."

if command -v tcpdump >/dev/null 2>&1 && [ -f x5-live-udp.pcap ]; then
    python3 rtp_jpeg.py x5-live-udp.pcap --out "${TMPDIR:-/tmp}/x5-test-jpegs" >/dev/null
    printf '%s\n' "UDP replay/depacketization check passed."
else
    printf '%s\n' "No local pcap/tcpdump; skipped UDP replay check."
fi
