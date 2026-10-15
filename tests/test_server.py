import json
import socket
import urllib.error
import urllib.request

import pytest

from streamtv.server import MediaSession, StreamServer, file_session
from tests.conftest import FFMPEG, needs_ffmpeg


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    calls = []
    srv = StreamServer(free_port(), "geheim", on_cast=lambda d: calls.append(d) or "ok")
    srv.calls = calls
    srv.start()
    yield srv
    srv.shutdown()


def get(url, headers=None, method="GET", data=None):
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_file_full_and_range(server, tmp_path):
    f = tmp_path / "film.mkv"
    f.write_bytes(bytes(range(256)) * 40)
    s = server.add(file_session(f))
    url = server.url_for(s, "127.0.0.1")
    assert url.endswith(".mkv")
    code, h, body = get(url)
    assert code == 200 and len(body) == 10240 and h["Content-Type"] == "video/x-matroska"
    assert h["contentFeatures.dlna.org"].startswith("DLNA.ORG_OP=01")
    code, h, body = get(url, {"Range": "bytes=100-199"})
    assert code == 206 and body == f.read_bytes()[100:200] and h["Content-Range"] == "bytes 100-199/10240"
    code, h, body = get(url, {"Range": "bytes=-10"})
    assert code == 206 and body == f.read_bytes()[-10:]
    code, h, body = get(url, {"Range": "bytes=99999-"})
    assert code == 416
    code, h, body = get(url, method="HEAD")
    assert code == 200 and h["Content-Length"] == "10240" and body == b""


def test_unknown_session_404(server):
    assert get(f"http://127.0.0.1:{server.port}/media/abcdef.ts")[0] == 404


@needs_ffmpeg
def test_pipe_session_streams_ts(server):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
           "-c:v", "libx264", "-preset", "ultrafast", "-f", "mpegts", "pipe:1"]
    s = server.add(MediaSession(title="t", mime="video/mp2t", command=lambda: cmd))
    code, h, body = get(server.url_for(s, "127.0.0.1"))
    assert code == 200 and h["Content-Type"] == "video/mp2t"
    assert h["transferMode.dlna.org"] == "Streaming"
    assert body[0] == 0x47 and len(body) > 10_000
    # HEAD startet kein ffmpeg
    code, h, body = get(server.url_for(s, "127.0.0.1"), method="HEAD")
    assert code == 200 and body == b""


def test_new_session_replaces_old(server, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    s1 = server.add(file_session(f))
    s2 = server.add(file_session(f))
    assert list(server.sessions) == [s2.id]
    assert get(server.url_for(s1, "127.0.0.1"))[0] == 404


def test_bookmarklet_endpoint_requires_token(server):
    base = f"http://127.0.0.1:{server.port}/cast?url=https%3A%2F%2Fexample.org%2Fv&title=Hi"
    code, _, body = get(base + "&token=falsch")
    assert code == 403 and not server.calls
    code, _, body = get(base + "&token=geheim")
    assert code == 200 and "ok" in body.decode()
    assert server.calls == [{"url": "https://example.org/v", "media": "", "title": "Hi", "referer": ""}]


def test_api_cast_accepts_extension_origin_only(server):
    url = f"http://127.0.0.1:{server.port}/api/cast"
    payload = json.dumps({"url": "https://example.org/v", "media": "https://cdn/x.m3u8"}).encode()
    hdr = {"Content-Type": "application/json"}
    code, _, _ = get(url, {**hdr, "Origin": "https://evil.example"}, "POST", payload)
    assert code == 403
    code, h, body = get(url, {**hdr, "Origin": "chrome-extension://abc"}, "POST", payload)
    assert code == 200 and json.loads(body)["ok"]
    assert h["Access-Control-Allow-Origin"] == "chrome-extension://abc"
    assert server.calls[-1]["media"] == "https://cdn/x.m3u8"
    tok = json.dumps({"url": "https://e/v", "token": "geheim"}).encode()
    assert get(url, hdr, "POST", tok)[0] == 200


def test_ping(server):
    code, _, body = get(f"http://127.0.0.1:{server.port}/api/ping")
    assert code == 200 and json.loads(body)["app"] == "StreamTV"
