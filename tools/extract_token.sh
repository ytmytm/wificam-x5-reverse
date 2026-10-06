#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 capture.pcap" >&2
    exit 2
fi

pcap=$1
if [ ! -r "$pcap" ]; then
    echo "capture is not readable: $pcap" >&2
    exit 1
fi
command -v tcpdump >/dev/null 2>&1 || {
    echo "tcpdump is required" >&2
    exit 1
}

# The capture can contain private account/device data. Keep the output secret.
tcpdump -nn -A -s 0 -r "$pcap" 'tcp port 80' 2>/dev/null |
    sed -nE 's/^[Aa]uthorization:[[:space:]]+Bearer[[:space:]]+([^[:space:]]+).*/\1/p' |
    awk 'NF { print; exit }'
