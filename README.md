# TXW817 / WiFiCam X5 local viewer

This project provides a small, dependency-free Python server for the low-cost
WiFi camera sold as **X5** and identified by its firmware as **TXW817**. It
receives the camera's local UDP video/audio stream and exposes it as a browser
preview.

The implementation was tested on an ARMv7 Odroid running Ubuntu 18.04. It does
not require Docker, Node.js, npm, or Python packages.

> This is an independent reverse-engineering project. Use it only with cameras
> and networks you own or are authorized to test.

## Features

- local web preview at `/stream.mjpg`;
- browser audio at `/audio.pcm` (signed little-endian PCM16, 8 kHz mono);
- runtime camera selection in the web UI by UID and LAN IP;
- browser control page at `/` with LED, IR, and experimental quality controls;
- JSON health endpoint at `/status`;
- cloud-assisted UDP endpoint negotiation, followed by local camera traffic;
- automatic retry when the camera or cloud API is unavailable;
- systemd autostart and graceful offline behavior;
- optional local RTSP output at `rtsp://<odroid-ip>:8554/x5`;
- optional Home Assistant MJPEG Camera autoconfiguration;
- Python 3.6-compatible runtime code for older ARM systems.

## Current device assumptions

The defaults describe the camera used during development, but all values can
be overridden through the environment file:

| Setting | Default |
| --- | --- |
| Camera IP | `192.168.2.158` |
| Camera UID | `276642521291` |
| UDP listen port | `50001` |
| HTTP port | `5080` |
| API host | `http://ipc.gps555.net` |
| Application ID | see `config.env.example` |

The API currently uses an HTTP bearer token. The token is not included in this
repository and must be supplied locally as `X5_API_TOKEN`.

## Finding the camera UID

The UID is the numeric device identifier used by the cloud API; it is not the
camera's LAN IP address and not its Wi-Fi MAC address. The easiest method is
to capture one live-preview session from the phone and search the decoded
HTTP/API traffic for `uid=` or a JSON field named `uid`. The same value also
appears in the cloud command body sent when changing LED/IR settings.

For a private capture, use the token-extraction workflow below and then search
locally without uploading the file:

```sh
strings /path/to/x5-full.pcap | rg -o '[?&]uid=[0-9]+|"uid"[[:space:]]*:[[:space:]]*"?[0-9]+'
```

If the capture is opened in Wireshark, use **Find Packet → String** and search
for `uid`. Verify the result against the camera selected in the mobile app;
some accounts can contain more than one device. Put the result in the private
environment file:

```sh
X5_UID=your-camera-uid
```

The default in the example configuration is the development camera's value;
replace it when configuring another device. Do not commit the pcap, UID, or
bearer token.

## Quick start

Copy the repository to the target machine and run:

```sh
cd x5-reverse
cp config.env.example config.env
$EDITOR config.env
sudo ./install.sh config.env
```

The installer places the program in `/opt/x5-viewer`, installs the systemd
unit, enables it at boot, and restarts it. The environment file is copied to
`/etc/x5-viewer.env` with mode `0600`.

Check the service:

```sh
systemctl status x5-viewer.service
curl http://127.0.0.1:5080/status
```

Open the preview from a LAN browser:

```text
http://<odroid-ip>:5080/stream.mjpg
```

Open `http://<odroid-ip>:5080/` for the control page. Click **Włącz dźwięk**
once after opening the page; this is required by browser autoplay policy. The
video and audio are currently separate browser tracks, so a small amount of
drift can occur over a long session. The raw internal tracks are deliberately
kept separate to make a future FFmpeg/RTSP adapter straightforward.

The implemented command mapping is LED (`3`), IR (`7`), and image profile
(`22`, values `0`/`1`/`2`). The capture confirms that these three profile
values are sent, but all captured video headers remained
`640×480`, so their exact SD/HD/FHD meaning is intentionally not claimed yet.

The app capture did include two audio toggles. No camera command or separate
audio-control request appeared; 268-byte audio datagrams continued throughout
the session. The camera nevertheless sends a separate RTP audio track. The
viewer decodes the audio payload (G.711 μ-law at 8 kHz, mono) to signed
little-endian PCM16 for the browser. Audio playback mute/unmute belongs to the
mobile app, not to the camera-control API.

The viewer remains running when the camera is absent. It reports `offline` or
`negotiated` in `/status` and retries automatically.

## What is local and what still uses the vendor cloud

This server is not yet a complete replacement for the vendor's cloud service.
After startup it uses the vendor HTTP API, authenticated with the captured
bearer token, to register the Odroid's UDP endpoint and request a live session.
It repeats that negotiation periodically so the stream can recover after a
camera or network interruption. LED, IR, and image-profile commands also go
through the vendor API.

Once the session is negotiated, the actual video and audio packets travel
directly between the camera and the Odroid over the LAN; they are not relayed
through the vendor cloud. The current architecture is therefore a local media
proxy with cloud-assisted session setup, not a fully cloud-free implementation.
Removing the remaining cloud dependency would require reverse-engineering a
direct local session/discovery handshake and a local replacement for the
control API.

The likely reason is that the cloud/live-session protocol is built into the
camera firmware: the capture showed the camera opening its own TCP connection
to a vendor server on port `8882`, while the app used `ipc.gps555.net` and the
camera UID. We have not established whether the vendor hostname is compiled
into firmware or obtained from another configuration/DNS mechanism, so this
remains an evidence-based hypothesis rather than a confirmed firmware detail.

## RTSP and Home Assistant

On an ARMv7 Odroid with FFmpeg installed, install the optional RTSP layer:

```sh
sudo ./install-rtsp.sh
```

This installs MediaMTX, starts a local RTSP publisher, and creates the stream:

```text
rtsp://<odroid-ip>:8554/x5
```

The RTSP output normalizes the camera's JPEG frames to hardware-encoded H.264
(`640x480`) and carries audio as G.711 μ-law (`8 kHz`, mono). This avoids
green/corrupted frames that can occur when the camera-specific JPEG stream is
copied directly into RTSP.

The publisher consumes the viewer's local MJPEG and PCM endpoints, so the
camera negotiation remains in `x5-viewer.service` and the RTSP layer retries
gracefully when the camera is offline. On this older Home Assistant/ARMv7
installation, the autoconfig uses Generic Camera with both the local
single-frame `/snapshot.jpg` endpoint and `rtsp://127.0.0.1:8554/x5` as its
stream source. The RTSP stream is handled by HA's `stream` integration; the
snapshot remains a fallback when no camera frame is currently available.

After Home Assistant imports the Generic Camera entry, remove the temporary
`camera: !include x5_camera.yaml` line from `configuration.yaml`. The imported
config entry is persistent; leaving the YAML import in place can create a
second `WiFiCam X5` entity after a later restart.

The Odroid's HA service also starts with `--skip-pip-packages numpy`. HA
2023.10 requests exactly `numpy==1.26.0`, but ARMv7/Python 3.11 has no matching
binary wheel and the installed GCC 7.5 cannot build it (NumPy 1.26 requires
GCC 8.4+). NumPy 1.23.2 is already installed and is sufficient for the
currently used integrations; skipping only this package prevents a failed
source build from delaying every HA boot. Revisit this override when upgrading
Home Assistant or its stream integration.

### Boot order and camera offline behavior

The systemd units for the viewer, MediaMTX, and the RTSP publisher are enabled
before Home Assistant. Home Assistant has a `Wants`/`After` drop-in for this
stack, but those are ordering and soft dependencies: Home Assistant still
starts if the camera is powered off or absent from the LAN. The viewer keeps
its HTTP endpoints available and reports no frame until the camera returns;
the publisher retries without restarting Home Assistant.
after making a timestamped backup. The installer attempts to validate the
configuration before restarting Home Assistant; if the old HA environment
cannot finish its dependency check, it leaves HA running and reports that
condition. MediaMTX is used as the RTSP server because
FFmpeg alone is an encoder/publisher, not a persistent RTSP server.

## Selecting a camera in the web UI

The control page displays the known `X5_UID`; enter the camera's current LAN
address and click **Wybierz po IP**.
The running process switches target and renegotiates the stream; a service
restart is not needed. This selection is runtime-only and is not written back
to `/etc/x5-viewer.env`, so use that file for the boot-time default.

The router's DHCP log/lease table can provide the LAN IP and MAC address. In
the captures used for this project, the camera did not advertise its cloud
UID in a useful LAN broadcast; the UID came from the mobile-app/cloud API
traffic. Therefore the UI intentionally requires both values rather than
guessing a UID from an IP or MAC address.

## Obtaining the token once

The official mobile application is currently needed only to obtain the API
bearer token and to confirm that the camera can start a live session. The token
can be captured once on the local router; it is then stored only on the viewer
machine.

1. Find the phone's LAN address and the camera address.
2. On the router, start a private capture:

   ```sh
   tcpdump -i br-lan -s 0 -U -w /tmp/x5-full.pcap \
     'host <camera-ip> or host <phone-ip>'
   ```

3. Open the camera in the official app, sign in if necessary, and open live
   preview. Wait until video is visible, then stop `tcpdump`.
4. Copy the pcap to a trusted development machine. Do not commit it: it can
   contain bearer tokens, account data, and device identifiers.
5. Extract ready-to-paste private configuration lines locally:

   ```sh
   ./tools/extract_token.sh /path/to/x5-full.pcap
   ```

   The script prints a block such as:

   ```text
   X5_API_TOKEN=...
   X5_UID=276642521291
   X5_CAMERA_IP=192.168.2.158
   ```

   It includes UID and camera IP only when they can be identified from the
   capture. Paste the block into private `config.env` or
   `/etc/x5-viewer.env`; never put it in Git.

The extractor intentionally accepts only HTTP `Authorization: Bearer` headers
from the supplied capture. If the app changes to HTTPS, the token cannot be
recovered from a passive LAN capture; use the app's own logged-in session and
do not attempt to bypass TLS.

## Configuration

Start with [`config.env.example`](config.env.example). At minimum:

```dotenv
X5_UID=276642521291
X5_CAMERA_IP=192.168.2.158
X5_API_TOKEN=replace-with-a-local-token
```

Other useful variables are `X5_HTTP_PORT`, `X5_LISTEN_PORT`, `X5_API_BASE`,
`X5_HTTP_HOST`, and `X5_LISTEN_HOST`. Keep the resulting file outside the Git
working tree or name it `config.env`; both are ignored by `.gitignore`.

## Development and offline replay

Syntax-check the code without installing dependencies:

```sh
./test.sh
```

If a private UDP pcap is available, `rtp_jpeg.py` can depacketize it and write
JPEG frames for inspection:

```sh
python3 rtp_jpeg.py private-capture.pcap --out /tmp/x5-jpegs
```

The repository's `.gitignore` excludes pcap, raw media, logs, secrets, and
Python cache files. Existing local captures are research artefacts and must
remain private.

## Protocol notes

The media packets use an RFC 2435-style JPEG payload. The firmware repeats the
JPEG/restart header in fragments and places the quantization tables in the first
fragment. `rtp_jpeg.py` reconstructs the fragments and creates a regular JPEG
interchange stream for the HTTP multipart response.

The control protocol and observed command mappings are documented in:

- [`protocol.md`](protocol.md)
- [`protocol.py`](protocol.py)
- [`extract_frames.py`](extract_frames.py)

The original `cam-reverse` implementation is not required by this project; its
directory is kept separately in the parent workspace for historical reference.

## Security notes

- The API endpoint observed during reverse engineering uses plain HTTP. Treat
  the token as a password and keep the viewer on a trusted LAN.
- Rotate/revoke the token if the capture or environment file leaves the trusted
  machines.
- Do not publish `.pcap`, `.pcapng`, `config.env`, or shell history containing
  a token.
- This project currently exposes the MJPEG stream without authentication. Do
  not bind it to an untrusted interface or forward port `5080` to the Internet.

## Research sources

The investigation used the following public references and user-provided
materials:

- [DavidVentura/cam-reverse](https://github.com/DavidVentura/cam-reverse) — related camera reverse engineering and the initial comparison point;
- [Elektroda: WiFiCam X5, topic 4150688](https://www.elektroda.pl/rtvforum/topic4150688.html);
- [Elektroda: WiFiCam X5, topic 4033757](https://www.elektroda.com/rtvforum/topic4033757.html);
- [Elektroda: WiFiCam X5, topic 4117962](https://www.elektroda.com/rtvforum/topic4117962.html);
- [Elektroda: related TXW817 discussion, topic 4147085](https://www.elektroda.com/rtvforum/topic4147085.html);
- [TXW817 manufacturer datasheet](https://taixin-semi.com/zh/docs/txw817/latest/TXW817%20%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C?navigation=1&series=txw81x) — confirms the chip's MJPEG/YUV420 capabilities;
- [RFC 2435: RTP Payload Format for JPEG-compressed Video](https://www.rfc-editor.org/rfc/rfc2435.html);
- [FFmpeg RTP/JPEG depacketizer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/rtpdec_jpeg.c);
- [ACTi Streaming SDK guide](https://fen.home.pl/pub/webFTP/sterowniki/Acti/SDK/ACTi_SDK-10000_C_SDK_Programming_Guide-v1.2.33.pdf) — useful documentation of the same RTP/JPEG header layout;
- [MediaMTX releases](https://github.com/bluenviron/mediamtx/releases) — lightweight RTSP server used by the optional output layer;
- [Home Assistant Generic Camera](https://www.home-assistant.io/integrations/generic/) — RTSP configuration reference;
- [Home Assistant Generic Camera](https://www.home-assistant.io/integrations/generic/) — dashboard snapshot configuration used on the old HA host.

The private `.pcap` files in this working tree are the primary evidence for
this particular camera and are intentionally excluded from Git.
