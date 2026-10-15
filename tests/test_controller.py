"""Ende-zu-Ende: Controller -> simulierter LG-TV -> TV lädt den Stream von unserem Server."""

import time

import pytest

from streamtv.config import Config
from streamtv.controller import Controller, UserError
from tests.conftest import needs_ffmpeg
from tests.test_server import free_port


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    events = []
    cfg = Config(port=free_port())
    ctl = Controller(cfg, lambda k, d: events.append((k, d)))
    ctl.events = events
    ctl.start()
    yield ctl
    ctl.shutdown()


def use_tv(ctl, tv):
    from streamtv import dlna

    ctl.devices = [dlna.load_renderer(tv.location)]
    ctl.select(0)


def test_requires_device(controller):
    with pytest.raises(UserError, match="Fernseher"):
        controller.cast_url("https://example.org")


@needs_ffmpeg
def test_cast_url_end_to_end(controller, fake_tv, web_server):
    use_tv(controller, fake_tv)
    controller.cast_url(f"{web_server.base}/sample.mp4")
    assert fake_tv.fetched.wait(30)
    assert fake_tv.uri.endswith(".ts")
    assert fake_tv.headers["Content-Type"] == "video/mp2t"
    assert len(fake_tv.received) > 50_000 and fake_tv.received[0] == 0x47
    assert controller.playing.kind == "stream"
    state, pos, dur = controller.status()
    assert state == "PLAYING" and pos == 42


@needs_ffmpeg
def test_cast_sniffed_media_with_referer(controller, fake_tv, web_server):
    """Wie von der Browser-Erweiterung: Stream-URL, die nur mit Referer ausgeliefert wird."""
    use_tv(controller, fake_tv)
    controller.cast_url("https://example.org/page", media=f"{web_server.base}/protected/sample.mp4",
                        title="Geschützt")
    assert fake_tv.fetched.wait(30)
    assert len(fake_tv.received) > 50_000
    assert any(h.get("Referer") == "https://example.org/page" for h in web_server.seen)


@needs_ffmpeg
def test_seek_restarts_stream_with_offset(controller, fake_tv, web_server):
    use_tv(controller, fake_tv)
    controller.cast_url(f"{web_server.base}/sample.mp4")
    fake_tv.fetched.wait(30)
    first_uri = fake_tv.uri
    fake_tv.fetched.clear()
    controller.seek_to(3)
    assert controller.playing.offset == 3
    assert fake_tv.uri != first_uri
    assert fake_tv.fetched.wait(30) and len(fake_tv.received) > 10_000


def test_cast_file_and_controls(controller, fake_tv, sample_video):
    use_tv(controller, fake_tv)
    controller.cast_file(str(sample_video))
    assert fake_tv.fetched.wait(30)
    assert fake_tv.headers["Content-Type"] == "video/mp4"
    assert fake_tv.received[:8] == sample_video.read_bytes()[:8]
    controller.toggle_pause()
    assert fake_tv.state == "PAUSED_PLAYBACK"
    controller.toggle_pause()
    assert fake_tv.state == "PLAYING"
    controller.seek_relative(30)
    assert "Seek" in fake_tv.actions
    controller.set_volume(33)
    assert fake_tv.volume == 33
    controller.stop()
    assert controller.playing is None


@needs_ffmpeg
def test_browser_cast_callback(controller, fake_tv, web_server):
    use_tv(controller, fake_tv)
    msg = controller._on_browser_cast({"url": f"{web_server.base}/sample.mp4", "media": "", "title": "", "referer": ""})
    assert "gesendet" in msg
    assert fake_tv.fetched.wait(30)


def test_bookmarklet_contains_token_and_port(controller):
    code = controller.bookmarklet()
    assert code.startswith("javascript:")
    assert controller.cfg.token in code and f":{controller.cfg.port}/cast" in code


def test_firewall_hint_when_tv_never_fetches(controller, fake_tv, sample_video):
    use_tv(controller, fake_tv)
    fake_tv._fetch = lambda: None  # TV ruft nichts ab (wie bei blockierter Firewall)
    controller.cast_file(str(sample_video))
    controller._check_fetched(controller.playing.session, controller.device, wait=0.1)
    assert any(k == "error" and "Firewall" in d for k, d in controller.events)
