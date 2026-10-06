#!/usr/bin/env python3
"""Self-contained X5 UDP receiver and RTSP publisher.

The web demo is deliberately not needed here.  The receiver code is reused
from x5_viewer, but frames and PCM audio go directly to FFmpeg pipes.
"""

import os
import subprocess
import threading
import time

from x5_viewer import state, udp_worker


FFMPEG = os.getenv("X5_FFMPEG", "/usr/local/bin/ffmpeg")
RTSP_URL = os.getenv("X5_RTSP_URL", "rtsp://127.0.0.1:8554/x5")
VIDEO_GRACE_SECONDS = float(os.getenv("X5_VIDEO_GRACE_SECONDS", "30"))


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def copy_video(fd: int, stop: threading.Event) -> None:
    # The camera can pause UDP video for several seconds while keeping the
    # session alive.  Feed the last complete JPEG at the measured 8 FPS so
    # FFmpeg/MediaMTX do not interpret a short pause as an ended RTSP source.
    # Once the frame is too old, stop duplicating it and let the normal retry
    # path take over.
    frame_period = 1.0 / 8.0
    next_write = time.monotonic()
    last_stamp = 0.0
    last_jpeg = None
    try:
        while not stop.is_set():
            with state.lock:
                jpeg, stamp = state.jpeg, state.updated
            if jpeg and stamp != last_stamp:
                last_jpeg, last_stamp = jpeg, stamp
            now = time.monotonic()
            if (last_jpeg and time.time() - last_stamp <= VIDEO_GRACE_SECONDS
                    and now >= next_write):
                write_all(fd, last_jpeg)
                next_write = now + frame_period
            else:
                stop.wait(0.01)
    except (BrokenPipeError, OSError):
        pass
    finally:
        os.close(fd)


def copy_audio(fd: int, stop: threading.Event) -> None:
    client = state.add_audio_client()
    try:
        while not stop.is_set():
            try:
                chunk = client.get(timeout=1)
            except Exception:
                continue
            write_all(fd, chunk)
    except (BrokenPipeError, OSError):
        pass
    finally:
        state.remove_audio_client(client)
        os.close(fd)


def publish_once() -> int:
    video_read, video_write = os.pipe()
    audio_read, audio_write = os.pipe()
    command = [
        FFMPEG, "-hide_banner", "-loglevel", "warning",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-thread_queue_size", "256", "-framerate", "8", "-f", "mjpeg",
        "-i", f"pipe:{video_read}",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-thread_queue_size", "256", "-f", "s16le", "-ar", "8000", "-ac", "1",
        "-i", f"pipe:{audio_read}", "-map", "0:v:0", "-map", "1:a:0",
        "-pix_fmt", "nv21", "-c:v", "h264_v4l2m2m", "-b:v", "800k", "-g", "8", "-bf", "0",
        "-c:a", "pcm_mulaw", "-ar", "8000", "-ac", "1",
        "-muxdelay", "0", "-muxpreload", "0", "-f", "rtsp", "-rtsp_transport", "tcp", RTSP_URL,
    ]
    process = subprocess.Popen(command, pass_fds=(video_read, audio_read))
    os.close(video_read)
    os.close(audio_read)
    stop = threading.Event()
    video = threading.Thread(target=copy_video, args=(video_write, stop), daemon=True)
    audio = threading.Thread(target=copy_audio, args=(audio_write, stop), daemon=True)
    video.start()
    audio.start()
    try:
        return process.wait()
    finally:
        stop.set()
        video.join(timeout=2)
        audio.join(timeout=2)
        if process.poll() is None:
            process.terminate()


def main() -> None:
    threading.Thread(target=udp_worker, name="x5-udp", daemon=True).start()
    while True:
        publish_once()
        time.sleep(15)


if __name__ == "__main__":
    main()
