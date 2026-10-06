# TXW817 / WiFiCam X5 protocol notes

These notes describe the currently implemented path from the mobile-app
capture to the local RTSP stream. The implementation is not a complete
replacement for the vendor cloud: the cloud API is still needed to request a
live session and to send camera controls.

The vendor mobile app, currently **Z-IOT CAM**, is required for the initial
camera provisioning: connecting the camera to the home WiFi, confirming its
LAN address and starting the first live session used to obtain the UID and API
token. It is not required for the always-on local receiver after that setup.

## End-to-end session

The deployed receiver uses this sequence:

```text
UID + bearer token
  -> vendor HTTP API: send-stun-addr
  -> vendor HTTP API: notify-live-event
  -> vendor HTTP API: stun-addr/<UID> returns camera UDP port
  -> local UDP: camera sends JPEG fragments and G.711 audio
  -> FFmpeg: H.264 hardware encoding + G.711 μ-law
  -> local RTSP: MediaMTX publishes the stream to Home Assistant
  -> MQTT: Home Assistant Discovery and LED/IR controls
```

The camera's media packets travel directly over the LAN after negotiation. The
server registers its local address as both the public and private endpoint,
because the receiver and camera are on the same LAN. The negotiated camera
port can change between sessions and must not be hard-coded.

Negotiation success only means that the cloud returned a UDP endpoint. It does
not prove that the camera has started sending media. The receiver therefore
tracks separately:

- cloud negotiation;
- received UDP datagrams;
- assembled video frames;
- the RTSP publisher session.

If negotiation succeeds but no UDP packets arrive, the camera has not started
or has paused the media flow. The receiver retries without restarting Home
Assistant.

The stock firmware does not answer the `iLnk/PPPP` LAN discovery packet. It
opens a TCP control session to a cloud server on port `8882`. In the capture,
the active session was:

```text
camera 192.168.2.158:50361
server 47.97.71.82:8882
```

The current receiver does not depend on that camera TCP control connection for
media. It uses the vendor HTTP API to request the live event, then receives
media on the negotiated UDP port. The vendor cloud is therefore still part of
session setup and command delivery, while the media path is local.

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

## Local UDP keepalive

The mobile-app capture showed the following short exchange on the negotiated
camera UDP socket:

```text
camera -> app: `d.msg from firmware`
app -> camera: `App send heart for stun`
```

The receiver answers the first message immediately. It also sends the observed
periodic messages below while a camera endpoint is known:

```text
`DrApp send hello`
`App send heart for stun`
```

The firmware heartbeat response is important: a receiver that only sends the
timer-based heartbeat can work briefly and then lose the camera media session.
The exact timeout and meaning of the camera-side message are not fully known.

The receiver does not renegotiate a healthy session on a fixed timer. It waits
for a real video timeout (currently 30 seconds), because a new live-event
request can itself cause a temporary media pause. During a shorter pause, the
RTSP publisher repeats the last complete JPEG at 8 FPS to keep the downstream
RTSP/HLS pipeline alive. MediaMTX tolerates up to 60 seconds without RTSP
traffic; after that the publisher and HA clients may need to reconnect.

While media is healthy, the receiver refreshes the vendor live event every 20
seconds, matching the mobile application's cloud-assisted session lifetime.
This is a keepalive, not a new UDP-port negotiation, so it does not
intentionally replace a working RTSP source.

## Media packet formats

Video is carried as an RTP/JPEG-like UDP payload. The receiver recognizes
video packets beginning with `80 1a` or `80 9a`. The packet layout used by the
current camera is:

```text
80 1a/9a <camera-specific fields>
<6-byte frame identifier>
<4-byte big-endian fragment offset>
<fragment payload>
```

The first fragment contains a 140-byte camera/JPEG header. Subsequent fragment
offsets count JPEG entropy bytes and have an 8-byte transport prefix. A frame
is accepted only when all offsets are contiguous and the reconstructed JPEG
ends with `ff d9`; incomplete frames are discarded to avoid green/corrupted
output. `rtp_jpeg.py` rebuilds the camera-specific header and quantization
tables into a regular JPEG interchange stream.

The camera's measured video rate is about 8 FPS and the captured dimensions
were `640x480` for all tested image profiles. The RTSP publisher explicitly
uses 8 FPS so FFmpeg does not assume 25 FPS and create accelerating buffered
playback. Its MJPEG and PCM pipe input queues are limited to one packet; this
prevents a short camera pause from becoming a long replay of stale data.

Audio is RTP payload type `128`. After the 12-byte RTP header, packets contain
252 bytes of G.711 μ-law samples at 8 kHz, mono. The receiver converts this to
signed little-endian PCM16 before passing it to FFmpeg, which publishes G.711
μ-law in the RTSP track.

The browser viewer exposes video and audio as separate tracks. The standalone
RTSP publisher sends the two tracks to FFmpeg, which encodes the video as H.264
using `h264_v4l2m2m` when available and carries audio as G.711 μ-law at 8 kHz.
MediaMTX serves the resulting RTSP path `rtsp://127.0.0.1:8554/x5` to Home
Assistant. HA's `stream` integration normally produces HLS for the browser;
HLS buffering is downstream of the camera protocol and cannot repair a missing
UDP media session.

The publisher repeats the last complete JPEG at 8 FPS for up to 30 seconds
during a short video pause. After the configured stale-video timeout it retries
session negotiation. FFmpeg output uses `-flush_packets 1`; the publisher and
MediaMTX are separate systemd services so HA can start even when the camera is
offline.

### MQTT control integration

The RTSP publisher also runs an optional MQTT bridge. It connects to the broker
configured by `X5_MQTT_BROKER` (default `iotlocal.lan:1883`) and publishes
retained Home Assistant MQTT Discovery definitions for LED and IR switches.
The camera UID is included in the MQTT client ID, device identifier, entity
`unique_id` and all command/state topics, so multiple cameras can share one HA:

```text
homeassistant/switch/wificam_x5_<UID>_led/config
homeassistant/switch/wificam_x5_<UID>_ir/config
wificam/x5/<UID>/led/set
wificam/x5/<UID>/ir/set
wificam/x5/<UID>/led/state
wificam/x5/<UID>/ir/state
wificam/x5/<UID>/availability
```

MQTT is used for control and Discovery, not for video. MQTT Camera would carry
individual JPEG messages rather than the low-latency RTSP stream. If the MQTT
broker or Python MQTT package is unavailable, camera negotiation and RTSP
continue normally.

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
the mobile app rather than a camera setting. The current RTSP publisher does
nevertheless mux the continuously received audio into the RTSP track.

The audio packets use RTP payload type `128`. After the 12-byte RTP header,
the stream has the same four-byte SSRC (`42 52 12 91`) seen in the video
packets, followed by 252 bytes of G.711 μ-law samples. Packet timing is about
31 packets per second, consistent with 8 kHz mono audio. `x5_viewer.py`
decodes those samples to signed little-endian PCM16 and publishes them at
`/audio.pcm` for the browser. Video and audio remain separate internal tracks
until the publisher feeds them to FFmpeg, which muxes them into the RTSP output
without changing the camera capture path.
