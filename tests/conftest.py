"""Gemeinsame Test-Helfer: Testvideo und ein simulierter DLNA-Fernseher."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import unescape

import pytest

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg nicht installiert")

DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>[LG] webOS TV OLED55C3</friendlyName>
    <manufacturer>LG Electronics</manufacturer>
    <modelName>OLED55C3</modelName>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <controlURL>/upnp/control/RenderingControl1</controlURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>/upnp/control/AVTransport1</controlURL>
      </service>
    </serviceList>
  </device>
</root>"""


class FakeTV:
    """Antwortet auf SOAP wie ein Renderer und lädt die übergebene URL wie ein TV."""

    def __init__(self, fetch_bytes: int = 300_000):
        self.actions: list[str] = []
        self.uri: str | None = None
        self.metadata: str | None = None
        self.state = "NO_MEDIA_PRESENT"
        self.volume = 15
        self.fetch_bytes = fetch_bytes
        self.received = b""
        self.headers: dict = {}
        self.fetched = threading.Event()
        tv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = DESCRIPTION.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"])).decode()
                action = self.headers["SOAPAction"].strip('"').split("#")[1]
                tv.actions.append(action)
                out = tv.handle(action, data)
                if out is None:
                    body = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
                            '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>701</errorCode>'
                            '<errorDescription>Transition not available</errorDescription></UPnPError></detail>'
                            '</s:Fault></s:Body></s:Envelope>').encode()
                    self.send_response(500)
                else:
                    body = (f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                            f'<u:{action}Response xmlns:u="x">{out}</u:{action}Response></s:Body></s:Envelope>').encode()
                    self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.location = f"http://127.0.0.1:{self.httpd.server_port}/desc.xml"

    def handle(self, action: str, data: str):
        def arg(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", data, re.S)
            return unescape(m.group(1)) if m else None

        if action == "SetAVTransportURI":
            self.uri, self.metadata = arg("CurrentURI"), arg("CurrentURIMetaData")
            self.state = "STOPPED"
            return ""
        if action == "Play":
            self.state = "PLAYING"
            threading.Thread(target=self._fetch, daemon=True).start()
            return ""
        if action == "Pause":
            self.state = "PAUSED_PLAYBACK"
            return ""
        if action == "Stop":
            if self.state == "NO_MEDIA_PRESENT":
                return None  # wie echte Geräte: Fehler 701
            self.state = "STOPPED"
            return ""
        if action == "GetTransportInfo":
            return f"<CurrentTransportState>{self.state}</CurrentTransportState>"
        if action == "GetPositionInfo":
            return "<RelTime>0:00:42</RelTime><TrackDuration>0:10:00</TrackDuration>"
        if action == "Seek":
            return ""
        if action == "GetVolume":
            return f"<CurrentVolume>{self.volume}</CurrentVolume>"
        if action == "SetVolume":
            self.volume = int(arg("DesiredVolume"))
            return ""
        return None

    def _fetch(self):
        self.received = b""
        try:
            with urllib.request.urlopen(self.uri, timeout=30) as r:
                self.headers = dict(r.headers)
                while len(self.received) < self.fetch_bytes:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    self.received += chunk
        except OSError:
            pass
        self.fetched.set()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake_tv():
    tv = FakeTV()
    yield tv
    tv.close()


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg nicht installiert")
    d = tmp_path_factory.mktemp("media")
    out = d / "sample.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=25:duration=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                    "-y", str(out)], check=True)
    return out


@pytest.fixture
def web_server(sample_video):
    """Liefert das Testvideo wie eine Website aus; merkt sich Request-Header."""
    seen: list[dict] = []
    root = sample_video.parent

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(dict(self.headers))
            if self.path.startswith("/protected/"):
                if self.headers.get("Referer") != "https://example.org/page":
                    self.send_error(403)
                    return
                self.path = self.path[len("/protected"):]
            super().do_GET()

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # TV-Simulation trennt absichtlich vorzeitig

    httpd = QuietServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.seen = seen
    httpd.base = f"http://127.0.0.1:{httpd.server_port}"
    yield httpd
    httpd.shutdown()
    httpd.server_close()
