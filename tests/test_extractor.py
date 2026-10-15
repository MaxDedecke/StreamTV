import pytest

from streamtv import extractor


def test_format_selector_prefers_h264():
    sel = extractor.format_selector(1080)
    assert sel.startswith("bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]")
    assert sel.endswith("/b")
    assert "[height" not in extractor.format_selector(None)


def test_extract_direct_mp4_via_ytdlp(web_server):
    pytest.importorskip("yt_dlp")
    info = extractor.extract(f"{web_server.base}/sample.mp4", referer="https://example.org/page")
    assert len(info.inputs) == 1
    assert info.inputs[0].url.endswith("/sample.mp4")
    assert info.inputs[0].headers["Referer"] == "https://example.org/page"
    assert not info.transcode_video


def test_extract_html_page_with_video_tag(web_server, sample_video):
    pytest.importorskip("yt_dlp")
    (sample_video.parent / "page.html").write_text(
        "<html><head><title>Mein Film</title></head><body>"
        '<video controls><source src="sample.mp4" type="video/mp4"></video></body></html>')
    info = extractor.extract(f"{web_server.base}/page.html")
    assert info.inputs[0].url.endswith("/sample.mp4")


def test_extract_error_message(web_server):
    pytest.importorskip("yt_dlp")
    with pytest.raises(extractor.ExtractError):
        extractor.extract(f"{web_server.base}/gibtsnicht.mp4")


def test_direct_stream():
    info = extractor.direct_stream("https://cdn/x.m3u8", "T", referer="https://site/p")
    assert info.inputs[0].headers["Referer"] == "https://site/p"
    assert "User-Agent" in info.inputs[0].headers
