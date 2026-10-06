#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=${MEDIAMTX_VERSION:-v1.21.1}
ARCH=linux_armv7
ASSET="mediamtx_${VERSION}_${ARCH}.tar.gz"
URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/${ASSET}"
PREFIX=/opt/x5-viewer
TMP=$(mktemp -d)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root or with sudo: sudo $0" >&2
    exit 1
fi
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is required" >&2; exit 1; }

if [ -x /usr/local/bin/mediamtx ] && [ "${MEDIAMTX_FORCE_DOWNLOAD:-0}" != 1 ]; then
    echo "Using existing /usr/local/bin/mediamtx"
else
    echo "Downloading MediaMTX ${VERSION} for ARMv7..."
    curl -fsSL "$URL" -o "$TMP/$ASSET"
    tar -xzf "$TMP/$ASSET" -C "$TMP"
    install -o root -g root -m 0755 "$TMP/mediamtx" /usr/local/bin/mediamtx
fi
chmod 0755 /usr/local/bin/mediamtx
install -o root -g root -m 0644 "$ROOT/mediamtx.yml" /etc/x5-mediamtx.yml
install -o root -g root -m 0755 "$ROOT/x5-rtsp-publish.sh" "$PREFIX/x5-rtsp-publish.sh"
install -o root -g root -m 0644 "$ROOT/x5-mediamtx.service" /etc/systemd/system/x5-mediamtx.service
install -o root -g root -m 0644 "$ROOT/x5-rtsp-publish.service" /etc/systemd/system/x5-rtsp-publish.service

if id homeassistant >/dev/null 2>&1; then
    HA_DIR=$(getent passwd homeassistant | cut -d: -f6)/.homeassistant
    HA_CONFIG="$HA_DIR/configuration.yaml"
    HA_CAMERA="$HA_DIR/x5_camera.yaml"
    cp -p "$HA_CONFIG" "$HA_CONFIG.x5-backup-$(date +%Y%m%d-%H%M%S)-$$"
    install -o homeassistant -g homeassistant -m 0644 "$ROOT/x5-ha-camera.yaml" "$HA_CAMERA"
    if ! grep -Eq '^[[:space:]]*camera:[[:space:]]*!include[[:space:]]+x5_camera.yaml' "$HA_CONFIG"; then
        printf 'camera: !include x5_camera.yaml\n' >> "$HA_CONFIG"
    fi
    chown homeassistant:homeassistant "$HA_CONFIG"
    if [ -x /home/homeassistant/homeassistant/bin/hass ]; then
        if timeout 90 sudo -u homeassistant /home/homeassistant/homeassistant/bin/hass \
            --script check_config -c "$HA_DIR"; then
            systemctl restart home-assistant@homeassistant.service
        else
            echo "Home Assistant config check did not finish; services were not restarted." >&2
        fi
    fi
else
    echo "homeassistant user not found; install RTSP only" >&2
fi

systemctl daemon-reload
systemctl enable --now x5-mediamtx.service x5-rtsp-publish.service
systemctl restart x5-mediamtx.service x5-rtsp-publish.service
systemctl --no-pager --full status x5-mediamtx.service x5-rtsp-publish.service || true
echo "RTSP URL: rtsp://<odroid-ip>:8554/x5"
