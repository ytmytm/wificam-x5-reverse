#!/usr/bin/env python3
"""Small, dependency-free web viewer for the TXW817/X5 camera."""

import json
import ipaddress
import logging
import os
import queue
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from typing import Dict, List, Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from rtp_jpeg import rebuild_jpeg


UID = os.getenv("X5_UID", "276642521291")
CAMERA_IP = os.getenv("X5_CAMERA_IP", "192.168.2.158")
LISTEN_HOST = os.getenv("X5_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("X5_LISTEN_PORT", "50001"))
HTTP_HOST = os.getenv("X5_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("X5_HTTP_PORT", "5080"))
API_BASE = os.getenv("X5_API_BASE", "http://ipc.gps555.net")
APP_ID = os.getenv("X5_APP_ID", "863b979a7c9dedd5c85b3626b02c9bfe9c771583")
TOKEN = os.getenv("X5_API_TOKEN", "")
LOG_LEVEL = os.getenv("X5_LOG_LEVEL", "INFO").upper()
LOG_INTERVAL = float(os.getenv("X5_LOG_INTERVAL", "10"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("x5")

COMMANDS = {
    "led": 3,
    "ir": 7,
    "quality": 22,
}

PAGE = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WiFiCam X5</title>
<style>
:root{color-scheme:dark;--bg:#10141b;--card:#1b2330;--line:#344154;--accent:#5cc8ff;--muted:#9eacbd}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef4fb;font:16px system-ui,sans-serif}
main{max-width:1080px;margin:auto;padding:24px}h1{margin:0 0 4px;font-size:1.7rem}h2{font-size:1rem;margin:0 0 12px;color:var(--accent)}
.sub,.hint{color:var(--muted);font-size:.9rem}.layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:18px;margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}.video{padding:8px;background:#05070a}
img{display:block;width:100%;height:auto;background:#000;min-height:180px;object-fit:contain}.group{margin-bottom:20px}.buttons{display:flex;flex-wrap:wrap;gap:8px}
button{border:1px solid #4a5b70;border-radius:8px;background:#273548;color:#eef4fb;padding:9px 12px;font:inherit;cursor:pointer}
button:hover{border-color:var(--accent);background:#30445d}button:disabled{cursor:not-allowed;opacity:.45}.result{min-height:1.4em;margin-top:10px;color:var(--muted);font-size:.9rem}
.pill{display:inline-block;border-radius:999px;background:#37465b;padding:3px 9px;font-size:.82rem}.ok{color:#8ee6a1}.bad{color:#ff9e9e}
@media(max-width:760px){main{padding:12px}.layout{display:block}.controls{margin-top:14px}}
</style></head>
<body><main>
<h1>WiFiCam X5</h1><div class="sub">Lokalny podgląd i sterowanie kamerą TXW817</div>
<div class="layout"><section class="card video"><img src="/stream.mjpg" alt="Podgląd kamery"></section>
<aside class="card controls">
<div class="group"><h2>Kamerka</h2><label>IP w LAN<br><input id="cameraIp" inputmode="decimal" style="width:100%;margin:4px 0 8px;padding:8px;background:#111923;color:#eef4fb;border:1px solid #4a5b70;border-radius:6px"></label><button onclick="selectCamera()">Wybierz po IP</button><div class="hint">UID: <code id="cameraUid">nieznany</code><br>UID pochodzi z aplikacji/API; IP z DHCP/LAN.</div></div>
<div class="group"><h2>Stan</h2><span id="status" class="pill">sprawdzam…</span><div id="result" class="result"></div></div>
<div class="group"><h2>Dioda</h2><div class="buttons"><button onclick="cmd('led',1)">Włącz</button><button onclick="cmd('led',0)">Wyłącz</button></div></div>
<div class="group"><h2>IR</h2><div class="buttons"><button onclick="cmd('ir',1)">Włącz</button><button onclick="cmd('ir',0)">Wyłącz</button></div></div>
<div class="group"><h2>Profil obrazu</h2><div class="buttons"><button onclick="cmd('quality',0)">Profil 0</button><button onclick="cmd('quality',1)">Profil 1</button><button onclick="cmd('quality',2)">Profil 2</button></div><div class="hint">Mapowanie SD / HD / FHD nie jest jeszcze potwierdzone.</div></div>
<div class="group"><h2>Dźwięk</h2><button id="audioButton" onclick="toggleAudio()">Włącz dźwięk</button><div id="audioState" class="hint">Dźwięk jest osobnym strumieniem G.711 μ-law 8 kHz. Przeglądarka wymaga kliknięcia.</div></div>
</aside></div>
<script>
let cameraIpLoaded=false;
async function refresh(){try{let r=await fetch('/status');let x=await r.json();let e=document.getElementById('status');e.textContent=x.status+' · klatki: '+x.frames;e.className='pill '+(x.status==='streaming'?'ok':'bad');document.getElementById('cameraUid').textContent=x.uid||'nieznany';if(!cameraIpLoaded){document.getElementById('cameraIp').value=x.camera_ip||'';cameraIpLoaded=true;}}catch(e){}}
async function cmd(name,value){let out=document.getElementById('result');out.textContent='Wysyłanie…';try{let r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:name,value:value})});let x=await r.json();out.textContent=r.ok?'Wysłano: '+name+' = '+value:(x.error||'Błąd');}catch(e){out.textContent='Błąd połączenia';}}
async function selectCamera(){let out=document.getElementById('result');out.textContent='Przełączanie…';try{let r=await fetch('/api/camera',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({camera_ip:document.getElementById('cameraIp').value})});let x=await r.json();if(r.ok){document.getElementById('cameraIp').value=x.camera_ip;cameraIpLoaded=true;}out.textContent=r.ok?'Wybrano IP '+x.camera_ip:(x.error||'Błąd');}catch(e){out.textContent='Błąd połączenia';}}
let audioRun=false,audioContext=null,audioReader=null,audioNext=0;
function ulawByte(x){x=(~x)&255;let t=((x&15)<<3)+132;t<<=((x&112)>>4);return (x&128)?(132-t):(t-132);}
async function toggleAudio(){let b=document.getElementById('audioButton'),s=document.getElementById('audioState');
 if(audioRun){audioRun=false;if(audioReader)try{await audioReader.cancel()}catch(e){};if(audioContext)await audioContext.close();b.textContent='Włącz dźwięk';s.textContent='Dźwięk wyłączony';return;}
 try{audioContext=new (window.AudioContext||window.webkitAudioContext)();await audioContext.resume();let r=await fetch('/audio.pcm');audioReader=r.body.getReader();audioRun=true;audioNext=audioContext.currentTime+0.08;b.textContent='Wyłącz dźwięk';s.textContent='Dźwięk aktywny';
  let pending=new Uint8Array(0);while(audioRun){let q=await audioReader.read();if(q.done)break;let x=new Uint8Array(pending.length+q.value.length);x.set(pending);x.set(q.value,pending.length);pending=x;let n=pending.length&~1;if(!n)continue;let a=audioContext.createBuffer(1,n/2,8000),d=a.getChannelData(0);for(let i=0;i<n;i+=2)d[i/2]=new DataView(pending.buffer,pending.byteOffset+i,2).getInt16(0,true)/32768;pending=pending.slice(n);let src=audioContext.createBufferSource();src.buffer=a;src.connect(audioContext.destination);let now=audioContext.currentTime;audioNext=Math.max(audioNext,now+0.03);src.start(audioNext);audioNext+=a.duration;}
 }catch(e){audioRun=false;b.textContent='Włącz dźwięk';s.textContent='Błąd dźwięku: '+e;}
}
refresh();setInterval(refresh,3000);
</script></main></body></html>"""


class CameraConfig:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.uid = UID
        self.camera_ip = CAMERA_IP
        self.generation = 0

    def get(self):
        with self.lock:
            return self.uid, self.camera_ip, self.generation

    def set(self, uid: str, camera_ip: str) -> None:
        with self.lock:
            self.uid, self.camera_ip = uid, camera_ip
            self.generation += 1


camera_config = CameraConfig()


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jpeg = None  # type: Optional[bytes]
        self.updated = 0.0
        self.received = 0
        self.status = "starting"
        self.last_error = ""
        self.audio_clients = []  # type: List[queue.Queue]

    def set_status(self, status: str, error: str = "") -> None:
        with self.lock:
            self.status, self.last_error = status, error

    def add_audio_client(self):
        client = queue.Queue(maxsize=32)
        with self.lock:
            self.audio_clients.append(client)
        return client

    def remove_audio_client(self, client) -> None:
        with self.lock:
            if client in self.audio_clients:
                self.audio_clients.remove(client)

    def publish_audio(self, pcm: bytes) -> None:
        with self.lock:
            clients = list(self.audio_clients)
        for client in clients:
            try:
                client.put_nowait(pcm)
            except queue.Full:
                try:
                    client.get_nowait()
                    client.put_nowait(pcm)
                except queue.Empty:
                    pass


def mulaw_to_pcm16(data: bytes) -> bytes:
    """Decode the camera's RTP audio (G.711 μ-law, 8 kHz, mono)."""
    out = bytearray(len(data) * 2)
    for i, value in enumerate(data):
        value = (~value) & 0xff
        magnitude = ((value & 0x0f) << 3) + 132
        magnitude <<= (value & 0x70) >> 4
        sample = 132 - magnitude if value & 0x80 else magnitude - 132
        struct.pack_into("<h", out, i * 2, sample)
    return bytes(out)


state = State()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Cloud:
    def __init__(self, uid: Optional[str] = None, camera_ip: Optional[str] = None) -> None:
        self.uid = uid or camera_config.get()[0]
        self.camera_ip = camera_ip or camera_config.get()[1]
        self.private_ip = self._local_ip()

    def _local_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((self.camera_ip, 9))
            return sock.getsockname()[0]
        finally:
            sock.close()

    def get(self, path: str, **params: str) -> dict:
        params.update(appId=APP_ID)
        url = API_BASE + path + "?" + urllib.parse.urlencode(params)
        log.debug("cloud GET %s params=%s", path, ",".join(sorted(params)))
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode())
            log.debug("cloud GET %s -> HTTP %s", path, response.status)
            return result

    def send_command(self, command: int, value: int) -> dict:
        if not TOKEN:
            raise RuntimeError("X5_API_TOKEN is not configured")
        query = urllib.parse.urlencode({"appId": APP_ID})
        url = API_BASE + "/api/v1/cmd/send-cmd?" + query
        payload = json.dumps({"cmdType": str(command), "uid": self.uid,
                              "content": str(value)}).encode("ascii")
        request = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": "Bearer " + TOKEN,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode())

    def negotiate(self) -> int:
        # The app registers its reachable UDP endpoint, then asks the cloud
        # to notify the camera that a live event is wanted.
        log.info("negotiation start uid=%s camera=%s local_udp=%s:%d api=%s",
                 self.uid, self.camera_ip, self.private_ip, LISTEN_PORT, API_BASE)
        self.get("/api/v1/ipc/send-stun-addr", publicIp=self.private_ip,
                 publicPort=str(LISTEN_PORT), privateIp=self.private_ip,
                 privatePort=str(LISTEN_PORT), uid=self.uid)
        log.info("registered UDP endpoint for camera=%s", self.camera_ip)
        self.get("/api/v1/ipc/notify-live-event", eventType="1", uid=self.uid)
        log.info("requested live event for uid=%s", self.uid)
        response = self.get("/api/v1/ipc/stun-addr/" + self.uid)
        data = response.get("data", response)
        port = int(data.get("IpcPublicPort") or data.get("IpcPrivatePort"))
        log.info("camera UDP endpoint reported as %s:%d", self.camera_ip, port)
        return port


def assemble(groups, frame_id):
    fragments = groups.pop(frame_id, {})
    offsets = sorted(fragments)
    first = fragments.get(0)
    if offsets[0:1] != [0] or not first or len(first) < 140 or not first.startswith(b"\x41\xff"):
        return None
    body = first[:140] + first[140:]
    expected = len(first) - 140
    for offset in offsets[1:]:
        fragment = fragments[offset]
        # The transport offset counts JPEG entropy bytes. Never publish a
        # frame with a missing UDP fragment; a decoder would show green blocks
        # and the downstream RTSP buffer would keep accumulating damage.
        if offset != expected or len(fragment) < 8:
            return None
        body += fragment[8:]
        expected += len(fragment) - 8
    end = body.rfind(b"\xff\xd9")
    return body[:end + 2] if end >= 0 else None


def udp_worker() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LISTEN_HOST, LISTEN_PORT))
    sock.settimeout(1.0)
    groups = {}  # type: Dict[bytes, Dict[int, bytes]]
    next_negotiation = 0.0
    next_heartbeat = 0.0
    next_stats = time.monotonic() + LOG_INTERVAL
    packet_count = 0
    video_packet_count = 0
    audio_packet_count = 0
    frame_count = 0
    last_source = None
    camera_port = None
    config_generation = -1

    while True:
        now = time.monotonic()
        uid, camera_ip, generation = camera_config.get()
        if generation != config_generation:
            camera_port = None
            groups.clear()
            config_generation = generation
            next_negotiation = 0.0
        if now >= next_negotiation:
            try:
                if TOKEN:
                    camera_port = Cloud(uid, camera_ip).negotiate()
                    state.set_status("negotiated")
                    log.info("negotiation succeeded camera=%s:%d", camera_ip, camera_port)
                else:
                    state.set_status("listening", "X5_API_TOKEN is not configured")
                    log.warning("no API token; UDP negotiation disabled")
                next_negotiation = now + 20
            except Exception as exc:
                state.set_status("offline", str(exc)[:160])
                log.warning("negotiation failed camera=%s: %s", camera_ip, exc)
                next_negotiation = now + 10
        if camera_port and now >= next_heartbeat:
            try:
                sock.sendto(b"DrApp send hello", (camera_ip, camera_port))
                sock.sendto(b"App send heart for stun", (camera_ip, camera_port))
                log.debug("heartbeat sent to camera=%s:%d", camera_ip, camera_port)
            except OSError:
                log.warning("heartbeat failed to camera=%s:%d", camera_ip, camera_port)
            next_heartbeat = now + 4
        try:
            packet, source = sock.recvfrom(65535)
        except socket.timeout:
            if now >= next_stats:
                log.info("UDP stats packets=%d video_packets=%d audio_packets=%d frames=%d last_source=%s",
                         packet_count, video_packet_count, audio_packet_count,
                         frame_count, last_source or "none")
                next_stats = now + LOG_INTERVAL
            continue
        packet_count += 1
        last_source = "%s:%d" % source
        if packet_count == 1:
            log.info("first UDP packet received source=%s:%d len=%d header=%s",
                     source[0], source[1], len(packet), packet[:2].hex())
        if len(packet) < 16 or packet[:2] not in (b"\x80\x1a", b"\x80\x9a"):
            if len(packet) >= 16 and packet[:2] == b"\x80\x80":
                # RTP PT 128, SSRC 42521291, followed by G.711 μ-law bytes.
                audio_packet_count += 1
                state.publish_audio(mulaw_to_pcm16(packet[16:]))
            continue
        video_packet_count += 1
        frame_id = packet[6:12]
        offset = struct.unpack("!I", packet[12:16])[0]
        groups.setdefault(frame_id, {})[offset] = packet[16:]
        if packet[1] & 0x80:
            raw = assemble(groups, frame_id)
            if raw:
                try:
                    jpeg = rebuild_jpeg(raw)
                    with state.lock:
                        state.jpeg = jpeg
                        state.updated = time.time()
                        state.received += 1
                        state.status = "streaming"
                        frame_count += 1
                        if frame_count == 1:
                            log.info("first video frame assembled bytes=%d source=%s:%d",
                                     len(jpeg), source[0], source[1])
                except Exception as exc:
                    state.set_status("decode-error", str(exc)[:160])
                    log.warning("video frame decode failed: %s", exc)
        # Drop incomplete frames so a lost packet cannot grow memory forever.
        if len(groups) > 32:
            groups.pop(next(iter(groups)))
        if now >= next_stats:
            log.info("UDP stats packets=%d video_packets=%d audio_packets=%d frames=%d last_source=%s",
                     packet_count, video_packet_count, audio_packet_count,
                     frame_count, last_source or "none")
            next_stats = now + LOG_INTERVAL


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/" or self.path == "/status":
            with state.lock:
                uid, camera_ip, _ = camera_config.get()
                payload = {"status": state.status, "frames": state.received,
                           "last_frame": state.updated, "error": state.last_error,
                           "uid": uid, "camera_ip": camera_ip}
            data = (json.dumps(payload) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/audio.pcm":
            client = state.add_audio_client()
            self.send_response(200)
            # The body is signed little-endian PCM16; audio/L16 conventionally
            # means big-endian, so keep the MIME type generic.
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    chunk = client.get(timeout=30)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (queue.Empty, BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                state.remove_audio_client(client)
            return
        if self.path == "/snapshot.jpg":
            with state.lock:
                jpeg = state.jpeg
            if not jpeg:
                self.send_error(503, "No camera frame available")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)
            return
        if self.path != "/stream.mjpg":
            self.send_error(404)
            return
        boundary = b"--x5frame\r\n"
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=x5frame")
        self.end_headers()
        last = 0.0
        while True:
            with state.lock:
                jpeg, stamp = state.jpeg, state.updated
            if jpeg and stamp != last:
                try:
                    self.wfile.write(boundary)
                    self.wfile.write(f"Content-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg + b"\r\n")
                    self.wfile.flush()
                    last = stamp
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
            time.sleep(0.05)

    def do_POST(self) -> None:
        if self.path == "/api/camera":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                uid = str(request.get("uid", camera_config.get()[0])).strip()
                camera_ip = str(request["camera_ip"]).strip()
                if not uid.isdigit() or not 6 <= len(uid) <= 20:
                    raise ValueError("UID must contain 6-20 digits")
                camera_ip = str(ipaddress.ip_address(camera_ip))
                camera_config.set(uid, camera_ip)
                state.set_status("switching")
                body = json.dumps({"ok": True, "uid": uid,
                                   "camera_ip": camera_ip}).encode("utf-8")
                self.send_response(200)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                body = json.dumps({"ok": False, "error": str(exc)[:200]}).encode("utf-8")
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/api/command":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            name = request["command"]
            value = int(request["value"])
            command = COMMANDS[name]
            if value not in (0, 1, 2) or (name != "quality" and value not in (0, 1)):
                raise ValueError("unsupported command value")
            result = Cloud().send_command(command, value)
            body = json.dumps({"ok": True, "command": name, "value": value,
                               "response": result}).encode("utf-8")
            self.send_response(200)
        except (KeyError, ValueError, TypeError, RuntimeError, HTTPError, URLError, OSError) as exc:
            body = json.dumps({"ok": False, "error": str(exc)[:200]}).encode("utf-8")
            self.send_response(400 if isinstance(exc, (KeyError, ValueError, TypeError)) else 502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    threading.Thread(target=udp_worker, daemon=True).start()
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"x5 viewer http://0.0.0.0:{HTTP_PORT}/stream.mjpg", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
