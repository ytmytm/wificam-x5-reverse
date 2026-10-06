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
# Print shell assignments so the result can be pasted into config.env.
http_dump=$(tcpdump -nn -A -s 0 -r "$pcap" 'tcp port 80' 2>/dev/null)
token=$(printf '%s\n' "$http_dump" |
    sed -nE 's/^[Aa]uthorization:[[:space:]]+Bearer[[:space:]]+([^[:space:]]+).*/\1/p' |
    awk 'NF { print; exit }')

uid=$(printf '%s\n' "$http_dump" |
    grep -Eio '(^|[^[:alnum:]_])uid[[:space:]]*[=:][[:space:]]*"?[0-9]{6,20}' |
    grep -Eo '[0-9]{6,20}' | awk 'NF { print; exit }' || true)

# The camera's audio RTP packets are 268-byte UDP datagrams in this protocol.
# Use their source address as a best-effort LAN-IP hint; leave it out if absent.
camera_ip=$(tcpdump -nn -r "$pcap" 'udp' 2>/dev/null |
    awk '/length 268$/ { ip=$3; sub(/\.[0-9]+$/, "", ip); print ip; exit }' || true)

if [ -z "$token" ]; then
    echo "no HTTP bearer token found" >&2
    exit 1
fi

printf '%s\n' "# Paste into config.env (keep this file private)"
printf 'X5_API_TOKEN=%s\n' "$token"
if [ -n "$uid" ]; then
    printf 'X5_UID=%s\n' "$uid"
else
    echo "# X5_UID not found; obtain it from the app/API capture"
fi
if [ -n "$camera_ip" ]; then
    printf 'X5_CAMERA_IP=%s\n' "$camera_ip"
else
    echo "# X5_CAMERA_IP not detected; set it from the router DHCP lease"
fi
