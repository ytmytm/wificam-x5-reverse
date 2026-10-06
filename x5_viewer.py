#!/usr/bin/env python3
"""Small, dependency-free web viewer for the TXW817/X5 camera."""

import json
import os
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from typing import Dict, Optional
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

COMMANDS = {
    "led": 3,
    "ir": 7,
    "flip_v": 8,
    "flip_h": 9,
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
<div class="group"><h2>Stan</h2><span id="status" class="pill">sprawdzam…</span><div id="result" class="result"></div></div>
<div class="group"><h2>Dioda</h2><div class="buttons"><button onclick="cmd('led',1)">Włącz</button><button onclick="cmd('led',0)">Wyłącz</button></div></div>
<div class="group"><h2>IR</h2><div class="buttons"><button onclick="cmd('ir',1)">Włącz</button><button onclick="cmd('ir',0)">Wyłącz</button></div></div>
<div class="group"><h2>Odbicie</h2><div class="buttons"><button onclick="cmd('flip_h',1)">Poziomo</button><button onclick="cmd('flip_h',0)">Bez H</button><button onclick="cmd('flip_v',1)">Pionowo</button><button onclick="cmd('flip_v',0)">Bez V</button></div></div>
<div class="group"><h2>Profil obrazu</h2><div class="buttons"><button onclick="cmd('quality',0)">Profil 0</button><button onclick="cmd('quality',1)">Profil 1</button><button onclick="cmd('quality',2)">Profil 2</button></div><div class="hint">Mapowanie SD / HD / FHD nie jest jeszcze potwierdzone.</div></div>
<div class="group"><h2>Dźwięk</h2><button disabled title="Nie ma jeszcze rozpoznanej komendy kamery">Nierozpoznane</button><div class="hint">W dotychczasowym capture aplikacja nie wysłała komendy audio; MJPEG nie zawiera dźwięku.</div></div>
</aside></div>
<script>
async function refresh(){try{let r=await fetch('/status');let x=await r.json();let e=document.getElementById('status');e.textContent=x.status+' · klatki: '+x.frames;e.className='pill '+(x.status==='streaming'?'ok':'bad');}catch(e){}}
async function cmd(name,value){let out=document.getElementById('result');out.textContent='Wysyłanie…';try{let r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:name,value:value})});let x=await r.json();out.textContent=r.ok?'Wysłano: '+name+' = '+value:(x.error||'Błąd');}catch(e){out.textContent='Błąd połączenia';}}
refresh();setInterval(refresh,3000);
</script></main></body></html>"""


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jpeg = None  # type: Optional[bytes]
        self.updated = 0.0
        self.received = 0
        self.status = "starting"
        self.last_error = ""

    def set_status(self, status: str, error: str = "") -> None:
        with self.lock:
            self.status, self.last_error = status, error


state = State()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Cloud:
    def __init__(self) -> None:
        self.private_ip = self._local_ip()

    @staticmethod
    def _local_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((CAMERA_IP, 9))
            return sock.getsockname()[0]
        finally:
            sock.close()

    def get(self, path: str, **params: str) -> dict:
        params.update(appId=APP_ID)
        url = API_BASE + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.loads(response.read().decode())

    def send_command(self, command: int, value: int) -> dict:
        if not TOKEN:
            raise RuntimeError("X5_API_TOKEN is not configured")
        query = urllib.parse.urlencode({"appId": APP_ID})
        url = API_BASE + "/api/v1/cmd/send-cmd?" + query
        payload = json.dumps({"cmdType": str(command), "uid": UID,
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
        self.get("/api/v1/ipc/send-stun-addr", publicIp=self.private_ip,
                 publicPort=str(LISTEN_PORT), privateIp=self.private_ip,
                 privatePort=str(LISTEN_PORT), uid=UID)
        self.get("/api/v1/ipc/notify-live-event", eventType="1", uid=UID)
        response = self.get("/api/v1/ipc/stun-addr/" + UID)
        data = response.get("data", response)
        port = int(data.get("IpcPublicPort") or data.get("IpcPrivatePort"))
        return port


def assemble(groups, frame_id):
    fragments = groups.pop(frame_id, {})
    first = fragments.get(0)
    if not first or len(first) < 140 or not first.startswith(b"\x41\xff"):
        return None
    body = first[:140] + first[140:] + b"".join(
        fragments[offset][8:] for offset in sorted(fragments) if offset
    )
    end = body.rfind(b"\xff\xd9")
    return body[:end + 2] if end >= 0 else None


def udp_worker() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LISTEN_HOST, LISTEN_PORT))
    sock.settimeout(1.0)
    groups = {}  # type: Dict[bytes, Dict[int, bytes]]
    cloud = Cloud()
    next_negotiation = 0.0
    next_heartbeat = 0.0
    camera_port = None

    while True:
        now = time.monotonic()
        if now >= next_negotiation:
            try:
                if TOKEN:
                    camera_port = cloud.negotiate()
                    state.set_status("negotiated")
                else:
                    state.set_status("listening", "X5_API_TOKEN is not configured")
                next_negotiation = now + 20
            except Exception as exc:
                state.set_status("offline", str(exc)[:160])
                next_negotiation = now + 10
        if camera_port and now >= next_heartbeat:
            try:
                sock.sendto(b"DrApp send hello", (CAMERA_IP, camera_port))
                sock.sendto(b"App send heart for stun", (CAMERA_IP, camera_port))
            except OSError:
                pass
            next_heartbeat = now + 4
        try:
            packet, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        if len(packet) < 16 or packet[:2] not in (b"\x80\x1a", b"\x80\x9a"):
            continue
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
                except Exception as exc:
                    state.set_status("decode-error", str(exc)[:160])
        # Drop incomplete frames so a lost packet cannot grow memory forever.
        if len(groups) > 32:
            groups.pop(next(iter(groups)))


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
                payload = {"status": state.status, "frames": state.received,
                           "last_frame": state.updated, "error": state.last_error}
            data = (json.dumps(payload) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
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
