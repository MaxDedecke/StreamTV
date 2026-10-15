"""Kleiner HTTP-Server im Heimnetz.

* ``/media/<id>.ts``  – live von ffmpeg erzeugter MPEG-TS-Stream (Websites, Spiegeln)
* ``/media/<id>.<ext>`` – lokale Datei mit Range-Unterstützung (Vorspulen am TV)
* ``/cast`` und ``/api/*`` – Schnittstelle für Bookmarklet und Browser-Erweiterung,
  nur von diesem PC (127.0.0.1) erreichbar.
"""

from __future__ import annotations

import collections
import hmac
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse

from streamtv.dlna import content_features
from streamtv.ffmpeg import popen_kwargs

CHUNK = 64 * 1024

FILE_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo", ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".mts": "video/mp2t",
    ".webm": "video/webm", ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac", ".jpg": "image/jpeg", ".png": "image/png",
}


@dataclass
class MediaSession:
    title: str
    mime: str
    # Pipe-Sitzung: Funktion, die zum Startzeitpunkt den ffmpeg-Befehl liefert
    command: Callable[[], list[str]] | None = None
    # optional: schreibt in ffmpeg-stdin (z. B. Systemton beim Spiegeln)
    stdin_feeder: Callable[[subprocess.Popen, threading.Event], None] | None = None
    path: Path | None = None
    id: str = field(default_factory=lambda: secrets.token_hex(6))
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=40))
    requests: int = 0  # wie oft der TV den Stream abgerufen hat
    _proc: subprocess.Popen | None = None
    _stop: threading.Event | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    on_error: Callable[[str], None] | None = None

    @property
    def is_file(self) -> bool:
        return self.path is not None

    @property
    def ext(self) -> str:
        return self.path.suffix.lower() if self.path else ".ts"

    def start_process(self) -> subprocess.Popen:
        """Startet ffmpeg neu; eine eventuell laufende Instanz wird beendet.

        Fernseher öffnen den Stream gern mehrmals hintereinander (Probe, dann
        richtig). Nur die jeweils letzte Verbindung bekommt Daten.
        """
        with self._lock:
            self.kill()
            cmd = self.command()
            stop = threading.Event()
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if self.stdin_feeder else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **popen_kwargs(),
            )
            self._proc, self._stop = proc, stop
            threading.Thread(target=self._read_stderr, args=(proc, stop), daemon=True).start()
            if self.stdin_feeder:
                threading.Thread(target=self.stdin_feeder, args=(proc, stop), daemon=True).start()
            return proc

    def _read_stderr(self, proc: subprocess.Popen, stop: threading.Event) -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.log.append(line)
        code = proc.wait()
        if code not in (0, None) and self.on_error and not stop.is_set():
            last = self.log[-1] if self.log else f"Exit-Code {code}"
            self.on_error(f"ffmpeg: {last}")

    def kill(self) -> None:
        proc, stop = self._proc, self._stop
        if stop:
            stop.set()
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        self._proc = None


class StreamServer:
    def __init__(self, port: int, token: str, on_cast: Callable[[dict], str] | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.port = port
        self.token = token
        self.on_cast = on_cast
        self.on_log = on_log or (lambda msg: None)
        self.sessions: dict[str, MediaSession] = {}
        self._httpd: ThreadingHTTPServer | None = None

    # ----------------------------------------------------------- Sitzungen
    def add(self, session: MediaSession) -> MediaSession:
        for old in list(self.sessions.values()):
            old.kill()
        self.sessions = {session.id: session}  # immer nur ein aktiver Stream
        return session

    def url_for(self, session: MediaSession, host_ip: str) -> str:
        name = quote(f"{session.id}{session.ext}")
        return f"http://{host_ip}:{self.port}/media/{name}"

    def stop_all(self) -> None:
        for s in self.sessions.values():
            s.kill()

    # -------------------------------------------------------------- Server
    def start(self) -> None:
        server = self

        class Handler(StreamHandler):
            srv = server

        self._httpd = _HTTPServer(("0.0.0.0", self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="http").start()

    def shutdown(self) -> None:
        self.stop_all()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()


class _HTTPServer(ThreadingHTTPServer):
    # Unter Windows erlaubt SO_REUSEADDR, dass ein zweites Programm denselben Port
    # belegt – dann würde eine zweite StreamTV-Instanz Anfragen „stehlen“.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def _is_local(client_ip: str) -> bool:
    return client_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


class StreamHandler(BaseHTTPRequestHandler):
    srv: StreamServer
    server_version = "StreamTV/0.1 DLNADOC/1.50 UPnP/1.0"
    # HTTP/1.0: Ende des Streams = Verbindungsende (mögen DLNA-Geräte lieber als chunked)
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # kein Konsolen-Spam
        pass

    # ------------------------------------------------------------ Routing
    def do_HEAD(self):
        self._route(head=True)

    def do_GET(self):
        self._route(head=False)

    def do_POST(self):
        if urlparse(self.path).path == "/api/cast":
            self._api_cast()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        if not _is_local(self.client_address[0]):
            self.send_error(403)
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _route(self, head: bool):
        path = urlparse(self.path).path
        m = re.match(r"^/media/([0-9a-f]+)(\.[A-Za-z0-9]+)?$", path)
        if m:
            session = self.srv.sessions.get(m.group(1))
            if session:
                session.requests += 1
            if not session:
                self.send_error(404, "Stream beendet")
            elif session.is_file:
                self._serve_file(session, head)
            else:
                self._serve_pipe(session, head)
        elif path == "/cast":
            self._bookmarklet_cast()
        elif path == "/api/ping":
            self._json(200, {"ok": True, "app": "StreamTV"}) if _is_local(self.client_address[0]) else self.send_error(403)
        elif path == "/":
            self._html(200, "StreamTV läuft.")
        else:
            self.send_error(404)

    # ----------------------------------------------------------- Streams
    def _dlna_headers(self, session: MediaSession, seekable: bool):
        self.send_header("Content-Type", session.mime)
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("contentFeatures.dlna.org", content_features(session.mime, seekable))
        self.send_header("Cache-Control", "no-cache")

    def _serve_pipe(self, session: MediaSession, head: bool):
        self.send_response(200)
        self._dlna_headers(session, seekable=False)
        self.send_header("Accept-Ranges", "none")
        self.send_header("Connection", "close")
        self.end_headers()
        if head:
            return
        self.srv.on_log(f"Fernseher {self.client_address[0]} ruft den Stream ab.")
        proc = session.start_process()
        try:
            while True:
                chunk = proc.stdout.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (ConnectionError, OSError):
            pass  # TV hat die Verbindung geschlossen
        finally:
            # Nur beenden, wenn ffmpeg noch läuft (TV hat getrennt). Ist ffmpeg von
            # selbst beendet (Fehler/Ende), bleibt der Fehlerbericht erhalten.
            if proc.poll() is None:
                if session._proc is proc:
                    session.kill()
                else:  # eine neuere Verbindung hat übernommen
                    try:
                        proc.kill()
                    except OSError:
                        pass

    def _serve_file(self, session: MediaSession, head: bool):
        try:
            size = os.path.getsize(session.path)
        except OSError:
            self.send_error(404)
            return
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:  # bytes=-500 -> die letzten 500 Bytes
                    start = max(0, size - int(m.group(2)))
                partial = True
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self._dlna_headers(session, seekable=True)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        try:
            with open(session.path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK * 4, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionError, OSError):
            pass

    # --------------------------------------------------- Browser-Schnittstelle
    def _cors(self):
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _token_ok(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(str(token), self.srv.token)

    def _bookmarklet_cast(self):
        if not _is_local(self.client_address[0]):
            self.send_error(403)
            return
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if not self._token_ok(q.get("token")):
            self._html(403, "Ungültiges Bookmarklet. Bitte in StreamTV neu kopieren.")
            return
        msg = self._dispatch(q)
        self._html(200, msg, autoclose=True)

    def _api_cast(self):
        if not _is_local(self.client_address[0]):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
        except (ValueError, OSError):
            self._json(400, {"ok": False, "message": "Ungültige Anfrage"})
            return
        origin = self.headers.get("Origin") or ""
        # Webseiten können diesen Origin nicht fälschen – nur echte Erweiterungen.
        from_extension = origin.startswith(("chrome-extension://", "moz-extension://", "extension://"))
        if not (from_extension or self._token_ok(data.get("token"))):
            self._json(403, {"ok": False, "message": "Nicht erlaubt"})
            return
        msg = self._dispatch(data)
        self._json(200, {"ok": True, "message": msg})

    def _dispatch(self, data: dict) -> str:
        if not self.srv.on_cast:
            return "StreamTV ist nicht bereit."
        clean = {k: str(data.get(k) or "") for k in ("url", "media", "title", "referer")}
        if not (clean["url"] or clean["media"]):
            return "Keine Adresse übergeben."
        return self.srv.on_cast(clean)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, message: str, autoclose: bool = False):
        script = "<script>setTimeout(()=>window.close(),2500)</script>" if autoclose else ""
        body = (
            "<!doctype html><html lang='de'><head><meta charset='utf-8'><title>StreamTV</title>"
            "<style>body{font:16px system-ui,sans-serif;background:#111;color:#eee;display:flex;"
            "align-items:center;justify-content:center;height:90vh;margin:0}div{max-width:360px;text-align:center}</style>"
            f"</head><body><div><b>📺 StreamTV</b><p>{html_escape(message)}</p></div>{script}</body></html>"
        ).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def file_session(path: str | Path, title: str | None = None) -> MediaSession:
    p = Path(path)
    mime = FILE_MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "video/mp4"
    return MediaSession(title=title or p.stem, mime=mime, path=p)
