#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${X5_INSTALL_PREFIX:-/opt/x5-viewer}
ENV_SOURCE=${1:-}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root or with sudo: sudo $0 [config.env]" >&2
    exit 1
fi

install -d -o pi -g pi "$PREFIX"
install -o pi -g pi -m 0755 "$ROOT/rtp_jpeg.py" "$PREFIX/rtp_jpeg.py"
install -o pi -g pi -m 0755 "$ROOT/x5_viewer.py" "$PREFIX/x5_viewer.py"
install -o root -g root -m 0644 "$ROOT/x5-viewer.service" \
    /etc/systemd/system/x5-viewer.service
if id homeassistant >/dev/null 2>&1; then
    install -d -o root -g root /etc/systemd/system/home-assistant@homeassistant.service.d
    install -o root -g root -m 0644 "$ROOT/home-assistant-x5.conf" \
        /etc/systemd/system/home-assistant@homeassistant.service.d/x5-camera.conf
fi

if [ -n "$ENV_SOURCE" ]; then
    test -r "$ENV_SOURCE" || { echo "cannot read $ENV_SOURCE" >&2; exit 1; }
    install -o root -g root -m 0600 "$ENV_SOURCE" /etc/x5-viewer.env
elif [ ! -f /etc/x5-viewer.env ]; then
    install -o root -g root -m 0600 "$ROOT/config.env.example" /etc/x5-viewer.env
    echo "Created /etc/x5-viewer.env; set X5_API_TOKEN before expecting a stream." >&2
fi

systemctl daemon-reload
systemctl enable --now x5-viewer.service
systemctl restart x5-viewer.service
systemctl --no-pager --full status x5-viewer.service || true
