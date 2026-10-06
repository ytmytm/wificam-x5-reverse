# TXW817 / WiFiCam X5 — local receiver, RTSP and Home Assistant

This project receives video and audio from the low-cost WiFi camera sold as
**X5**, identified by its firmware as **TXW817**. After the session is
established, media travels directly over the LAN to the Home Assistant server
and is published as RTSP. LED/IR control is exposed through MQTT Discovery.

This is an independent reverse-engineering project. Use it only with cameras
and networks that you own or are authorized to test.

## 1. Requirements

The target is an always-on Home Assistant server running Ubuntu/ARMv7 with
FFmpeg and LAN access. The tested installation is Home Assistant 2023.10.3 on
Python 3.11. The project does not require Docker, Node.js or npm.

You also need:

- an X5/TXW817 camera;
- a phone with **[Z-IOT CAM from Google Play](https://play.google.com/store/apps/details?id=com.flu.flutter_wifi_camera)**;
- a router providing DHCP and LAN access for the camera;
- one-time access to the vendor cloud through the mobile app while obtaining
  the API token;
- an MQTT broker, `iotlocal.lan:1883` by default.

## 2. Initial camera setup

The vendor app is effectively required at the beginning. It is used to:

1. power on and reset the camera if necessary;
2. connect the phone to the camera's setup hotspot;
3. provide the camera with the home WiFi credentials;
4. verify that the camera appears in the router and works in the app;
5. identify the UID and generate the traffic from which the API token can be
   extracted.

Instructions for this camera family indicate that the setup hotspot may start
with `ZIOT`. After setup, the camera should receive a DHCP address such as
`192.168.2.158`. The app is required for the initial provisioning; this server
does not replace the camera's WiFi setup procedure.

## 3. UID, IP address and token

The automatic service needs three values:

```dotenv
X5_UID=276642521291
X5_CAMERA_IP=192.168.2.158
X5_API_TOKEN=local-private-token
```

### UID

The UID is the numeric camera identifier used by the API. It is not the LAN IP
address or the WiFi MAC address. The most reliable source is a capture of one
live session from the mobile app; search it for `uid=` or a JSON field named
`uid`. The UID also appears in the body of LED/IR commands.

You can search a private capture locally:

```sh
strings /path/to/x5-full.pcap | rg -o \
  '[?&]uid=[0-9]+|"uid"[[:space:]]*:[[:space:]]*"?[0-9]+'
```

### Token

The bearer token is extracted once from the app's captured traffic. On the
router, while the phone opens the camera preview, start a private capture:

```sh
tcpdump -i br-lan -s 0 -U -w /tmp/x5-full.pcap \
  'host <camera-ip> or host <phone-ip>'
```

Sign in to the app, open the camera and wait until video is visible. Stop
`tcpdump`, copy the capture to a trusted development machine, then run:

```sh
./tools/extract_token.sh /path/to/x5-full.pcap
```

The script prints ready-to-copy `X5_API_TOKEN`, `X5_UID` and, when detected,
`X5_CAMERA_IP` lines. A capture may contain tokens, account data and device
identifiers; do not publish or commit it. If the app switches to HTTPS, a
passive capture may no longer be sufficient to recover the token.

## 4. Private configuration

Copy the example and fill in the values:

```sh
cp config.env.example config.env
$EDITOR config.env
```

Minimum configuration:

```dotenv
X5_UID=276642521291
X5_CAMERA_IP=192.168.2.158
X5_API_TOKEN=replace-with-a-local-token
```

Important MQTT options:

```dotenv
X5_MQTT_BROKER=iotlocal.lan
X5_MQTT_PORT=1883
X5_MQTT_PREFIX=wificam/x5
X5_MQTT_DISCOVERY_PREFIX=homeassistant
```

Do not put the token in Git-tracked files. `config.env` is ignored, and on the
server the configuration is installed as `/etc/x5-viewer.env` with mode `0600`.

## 5. Installation and startup

### Optional diagnostic viewer

The viewer is for testing only and is not needed for daily operation:

```sh
sudo ./install.sh config.env
systemctl status x5-viewer.service
curl http://127.0.0.1:5080/status
```

Open `http://<home-assistant-server-ip>:5080/`. The viewer provides
`/stream.mjpg`, `/audio.pcm`, `/status` and LED/IR buttons. Do not run it at the
same time as the RTSP publisher: both processes use UDP port `50001`.

### Normal RTSP mode

If the private environment has not been installed yet, first copy it to
`/etc/x5-viewer.env`:

```sh
sudo ./install.sh config.env
```

Then start the normal RTSP mode:

```sh
sudo ./install-rtsp.sh
```

The installer installs MediaMTX and `python3-paho-mqtt`, copies the receiver to
`/opt/x5-viewer`, disables the diagnostic viewer and enables:

- `x5-mediamtx.service` — RTSP server on TCP port `8554`;
- `x5-rtsp-publish.service` — camera negotiation, UDP receiver and FFmpeg;
- startup ordering relative to Home Assistant.

Stream URL:

```text
rtsp://<home-assistant-server-ip>:8554/x5
```

Service checks:

```sh
systemctl status x5-rtsp-publish x5-mediamtx home-assistant@homeassistant
journalctl -u x5-rtsp-publish.service -f
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/x5
```

## 6. Home Assistant

Home Assistant uses one Generic Camera entity with this source:

```text
rtsp://127.0.0.1:8554/x5
```

For camera UID `276642521291`, the entity is:

```text
camera.wificam_x5_276642521291
```

For another camera, create a separate publisher/configuration with a different
UID, IP address and UDP port. The UID is also used in MQTT entities, so several
cameras can coexist in one Home Assistant instance without collisions.

### MQTT LED/IR controls

The publisher advertises these MQTT Discovery entities:

```text
switch.wificam_x5_276642521291_led
switch.wificam_x5_276642521291_ir
```

Topics are scoped by UID:

```text
wificam/x5/276642521291/led/set
wificam/x5/276642521291/ir/set
wificam/x5/276642521291/led/state
wificam/x5/276642521291/ir/state
wificam/x5/276642521291/availability
```

The state is the last successfully requested state. The firmware does not
provide a reliable physical LED/IR state readback, so Home Assistant may be
wrong after a manual change in the vendor app. An unavailable MQTT broker does
not stop RTSP.

MQTT is used only for controls and Discovery. MQTT Camera is not used for
video because it sends individual JPEG images rather than a low-latency stream.

### Latency and HLS

The tested Home Assistant 2023.10.3 installation uses stable HLS:

```yaml
stream:
  ll_hls: false
```

This normally adds a few seconds of latency. On this particular HA/browser
combination, LL-HLS could play the initial playlist and then stop requesting
subsequent fragments, so it remains disabled.

The “No activity” overlay in the camera card is the state of the Generic Camera
entity. This integration does not report `streaming`/`recording` even while RTSP
is flowing. Hide it in the card with:

```yaml
type: picture-entity
entity: camera.wificam_x5_276642521291
camera_view: live
show_state: false
```

## 7. What is local and what still uses the cloud

The project does not completely replace the vendor cloud. At startup the
publisher uses the vendor HTTP API and token to register the Home Assistant
server's UDP endpoint, request a live event, refresh the session and send LED/IR
commands.

After the session is established, actual video and audio travel directly from
the camera to the Home Assistant server over UDP. The cloud does not relay the
media. This is a local media proxy with cloud-assisted setup, not a completely
cloud-free implementation.

The capture also showed the firmware opening TCP to a vendor server on port
`8882`. We have not established whether that hostname is compiled into the
firmware or obtained through another configuration/DNS mechanism.

## 8. Stability and offline behavior

The publisher retries negotiation when the camera disappears from the LAN,
does not restart Home Assistant because the camera is absent, repeats the last
complete frame for up to 30 seconds during a short pause, and refreshes a
healthy live session every 20 seconds without replacing the UDP port. FFmpeg
queues are limited to one packet so stale video is not replayed for tens of
seconds or minutes.

If the camera is powered off, the publisher can remain running without frames
and Home Assistant should still start. When the camera returns, the process
tries to resume the session automatically.

## 9. Troubleshooting

Check the layers in this order:

1. ```sh
   systemctl is-active x5-rtsp-publish x5-mediamtx home-assistant@homeassistant
   ```
2. ```sh
   journalctl -u x5-rtsp-publish.service -f
   ```
   `negotiation succeeded` does not guarantee frames; look for
   `first video frame assembled`.
3. ```sh
   ffprobe -v error -rtsp_transport tcp rtsp://127.0.0.1:8554/x5
   ```
4. ```sh
   ss -lunp | grep 50001
   ping <camera-ip>
   ```

No `x5` path usually means FFmpeg has not received the first frame, not that
Home Assistant authentication failed. UDP `50001` must belong to exactly one
receiver. If `ffprobe` works but HA is blank, check Generic Camera, HLS and the
dashboard card.

## 10. Specific to this Home Assistant installation

This workaround applies only to our HA 2023.10.3 server on Python 3.11,
ARMv7 and GCC 7.5; it is not a general HA or ARMv7 requirement. This server
tries to install `numpy==1.26.0`, has no matching wheel and cannot build it with
the local compiler. NumPy 1.23.2 is already installed, and the HA unit uses
`--skip-pip-packages numpy` so boot does not take many minutes.

## 11. Files and development

| File/service | Purpose |
| --- | --- |
| `x5_viewer.py` | shared negotiation, UDP receiver and optional HTTP viewer |
| `x5_rtsp_publish.py` | RTSP publisher, FFmpeg and local control HTTP |
| `x5_mqtt.py` | MQTT Discovery, LED/IR and availability |
| `rtp_jpeg.py` | JPEG fragment reassembly |
| `protocol.md` | reverse-engineered protocol description |
| `/etc/x5-viewer.env` | private UID, IP, token and settings |
| `x5-rtsp-publish.service` | always-on receiver and publisher |
| `x5-mediamtx.service` | local RTSP server |

```sh
./test.sh
python3 rtp_jpeg.py private-capture.pcap --out /tmp/x5-jpegs
```

`.gitignore` excludes captures, raw media, logs, secrets and Python cache
files.

## 12. Security

- The observed API endpoint uses HTTP; treat the token as a password.
- Do not publish `pcap`, `config.env`, `/etc/x5-viewer.env` or shell history.
- The MJPEG viewer has no authentication; keep it on a trusted LAN.
- Rotate/revoke the token if a capture or configuration leaves trusted hosts.

## 13. Sources

- [Z-IOT CAM on Google Play](https://play.google.com/store/apps/details?id=com.flu.flutter_wifi_camera) — app used for initial setup;
- [DavidVentura/cam-reverse](https://github.com/DavidVentura/cam-reverse);
- [Elektroda, topic 4150688](https://www.elektroda.pl/rtvforum/topic4150688.html);
- [Elektroda, topic 4033757](https://www.elektroda.com/rtvforum/topic4033757.html);
- [Elektroda, topic 4117962](https://www.elektroda.com/rtvforum/topic4117962.html);
- [Elektroda, topic 4147085](https://www.elektroda.pl/rtvforum/topic4147085.html);
- [TXW817 manufacturer datasheet](https://taixin-semi.com/zh/docs/txw817/latest/TXW817%20%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C?navigation=1&series=txw81x);
- [RFC 2435 RTP/JPEG](https://www.rfc-editor.org/rfc/rfc2435.html);
- [FFmpeg RTP/JPEG depacketizer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/rtpdec_jpeg.c);
- [ACTi Streaming SDK guide](https://fen.home.pl/pub/webFTP/sterowniki/Acti/SDK/ACTi_SDK-10000_C_SDK_Programming_Guide-v1.2.33.pdf);
- [MediaMTX releases](https://github.com/bluenviron/mediamtx/releases);
- [Home Assistant Generic Camera](https://www.home-assistant.io/integrations/generic/).

Private captures are the primary evidence for this particular camera and are
not part of the public repository.
