"""Video-/Stream-Adresse aus einer Website holen (mit yt-dlp)."""

from __future__ import annotations

from dataclasses import dataclass, field

from streamtv.ffmpeg import InputSpec, video_needs_transcode

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

QUALITY_HEIGHT = {"720p": 720, "1080p": 1080, "Beste": None}
COOKIE_BROWSERS = {"Keine": None, "Firefox": "firefox", "Edge": "edge", "Chrome": "chrome"}


class ExtractError(Exception):
    pass


@dataclass
class StreamInfo:
    title: str
    inputs: list[InputSpec]
    is_live: bool = False
    duration: int = 0
    transcode_video: bool = False
    page_url: str = ""
    notes: list[str] = field(default_factory=list)


def format_selector(max_height: int | None) -> str:
    """Bevorzugt H.264 + AAC (läuft auf dem LG ohne Umwandlung), max. ``max_height``."""
    h = f"[height<={max_height}]" if max_height else ""
    return "/".join([
        f"bv*[vcodec^=avc1]{h}+ba[acodec^=mp4a]",
        f"b[vcodec^=avc1]{h}",
        f"bv*[vcodec^=avc1]{h}+ba",
        f"bv*{h}+ba",
        f"b{h}",
        "bv*+ba",
        "b",
    ])


def _headers_for(fmt: dict, info: dict, referer: str | None) -> dict[str, str]:
    headers = dict(fmt.get("http_headers") or info.get("http_headers") or {})
    headers.setdefault("User-Agent", DEFAULT_UA)
    if referer and not any(k.lower() == "referer" for k in headers):
        headers["Referer"] = referer
    # Accept/Sec-Fetch-* Header sind für ffmpeg überflüssig
    for k in list(headers):
        if k.lower() in ("accept", "accept-language", "sec-fetch-mode"):
            headers.pop(k)
    return headers


def _cookie_header(ydl, url: str) -> str | None:
    try:
        return ydl.cookiejar.get_cookie_header(url) or None
    except Exception:
        return None


def extract(url: str, quality: str = "1080p", referer: str | None = None, cookies_browser: str | None = None) -> StreamInfo:
    try:
        import yt_dlp
    except ImportError as e:
        raise ExtractError("yt-dlp ist nicht installiert (pip install yt-dlp).") from e

    max_height = QUALITY_HEIGHT.get(quality, 1080)
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": format_selector(max_height),
        "socket_timeout": 20,
    }
    if referer:
        opts["http_headers"] = {"Referer": referer}
    browser = COOKIE_BROWSERS.get(cookies_browser or "Keine", cookies_browser)
    if browser:
        opts["cookiesfrombrowser"] = (browser,)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise ExtractError("Auf der Seite wurde kein Video gefunden.")
            if info.get("_type") == "playlist":
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise ExtractError("Auf der Seite wurde kein Video gefunden.")
                info = entries[0]
            return _to_stream_info(ydl, info, url, referer)
    except ExtractError:
        raise
    except Exception as e:  # yt_dlp.utils.DownloadError & Co.
        msg = str(e).replace("ERROR: ", "").strip()
        if "DRM" in msg:
            msg = "Das Video ist DRM-geschützt (z. B. Netflix, Disney+) und kann nicht weitergeleitet werden. Nutze dafür die App direkt auf dem LG."
        raise ExtractError(msg or "Unbekannter Fehler beim Auslesen der Seite.") from None


def _to_stream_info(ydl, info: dict, page_url: str, referer: str | None) -> StreamInfo:
    requested = info.get("requested_formats") or [info]
    inputs: list[InputSpec] = []
    vcodec = None
    for fmt in requested:
        stream_url = fmt.get("url")
        if not stream_url:
            continue
        headers = _headers_for(fmt, info, referer)
        cookie = _cookie_header(ydl, stream_url)
        if cookie and not any(k.lower() == "cookie" for k in headers):
            headers["Cookie"] = cookie
        inputs.append(InputSpec(stream_url, headers))
        if fmt.get("vcodec") not in (None, "none"):
            vcodec = vcodec or fmt.get("vcodec")
    if not inputs:
        raise ExtractError("Es wurde keine abspielbare Stream-Adresse gefunden.")
    # Video zuerst, Audio danach (build_remux_cmd erwartet diese Reihenfolge)
    if len(requested) >= 2 and requested[0].get("vcodec") == "none":
        inputs.reverse()

    si = StreamInfo(
        title=info.get("title") or "Video",
        inputs=inputs,
        is_live=bool(info.get("is_live")),
        duration=int(info.get("duration") or 0),
        transcode_video=video_needs_transcode(vcodec),
        page_url=page_url,
    )
    height = info.get("height")
    if height:
        si.notes.append(f"{height}p, {vcodec or 'Codec unbekannt'}")
    if si.transcode_video:
        si.notes.append("Video wird für den TV nach H.264 umgewandelt (braucht mehr CPU).")
    return si


def direct_stream(media_url: str, title: str = "Video", referer: str | None = None) -> StreamInfo:
    """Fallback ohne yt-dlp: Die Adresse wird so wie sie ist an ffmpeg gegeben."""
    headers = {"User-Agent": DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    return StreamInfo(title=title, inputs=[InputSpec(media_url, headers)], page_url=referer or "")
