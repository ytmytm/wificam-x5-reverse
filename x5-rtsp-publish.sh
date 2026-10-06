#!/bin/sh
set -eu

# Keep the RTSP layer independent from camera negotiation. x5-viewer owns the
# camera session and exposes the two local elementary tracks; FFmpeg muxes
# them into one RTSP publisher for MediaMTX.
exec /usr/local/bin/ffmpeg \
    -hide_banner -loglevel warning \
    -fflags nobuffer -flags low_delay -thread_queue_size 256 -framerate 8 -f mjpeg \
    -i http://127.0.0.1:5080/stream.mjpg \
    -fflags nobuffer -flags low_delay -thread_queue_size 256 -f s16le -ar 8000 -ac 1 \
    -i http://127.0.0.1:5080/audio.pcm \
    -map 0:v:0 -map 1:a:0 \
    -pix_fmt nv21 -c:v h264_v4l2m2m -b:v 800k -g 8 -bf 0 \
    -c:a pcm_mulaw -ar 8000 -ac 1 \
    -muxdelay 0 -muxpreload 0 -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/x5
