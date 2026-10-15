"""Programmlogik ohne Oberfläche: Fernseher, Streams, Steuerung."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from streamtv import dlna
from streamtv.config import Config
from streamtv.extractor import QUALITY_HEIGHT, ExtractError, StreamInfo, direct_stream, extract
from streamtv.ffmpeg import build_mirror_cmd, build_remux_cmd, find_ffmpeg, probe_file, video_needs_transcode
from streamtv.mirror import loopback_available, make_loopback_feeder
from streamtv.netutil import local_ip_for
from streamtv.server import MediaSession, StreamServer, file_session

TS_MIME = "video/mp2t"

Emit = Callable[[str, object], None]


@dataclass
class Playing:
    kind: str  # "stream" | "file" | "mirror"
    title: str
    session: MediaSession
    info: StreamInfo | None = None
    offset: int = 0  # Startpunkt (Sek.), wenn ein Stream neu gestartet wurde


class UserError(Exception):
    """Fehler mit verständlicher Meldung für die Oberfläche."""


class Controller:
    def __init__(self, cfg: Config, emit: Emit):
        self.cfg = cfg
        self.emit = emit
        self.ffmpeg = find_ffmpeg()
        self.devices: list[dlna.Renderer] = []
        self.device: dlna.Renderer | None = None
        self.playing: Playing | None = None
        self._lock = threading.RLock()
        self._poll_stop = threading.Event()
        self.server = StreamServer(cfg.port, cfg.token, on_cast=self._on_browser_cast, on_log=self.log)

    # -------------------------------------------------------------- Allgemein
    def log(self, msg: str) -> None:
        self.emit("log", msg)

    def start(self) -> None:
        self.server.start()
        threading.Thread(target=self._poll_loop, daemon=True, name="status").start()

    def shutdown(self) -> None:
        self._poll_stop.set()
        try:
            if self.playing and self.device:
                self.device.stop()
        except dlna.DLNAError:
            pass
        self.server.shutdown()

    def bookmarklet(self) -> str:
        return (
            "javascript:(()=>{const v=[...document.querySelectorAll('video,video source')]"
            ".map(x=>x.currentSrc||x.src).find(s=>/^https?:/.test(s))||'';"
            f"window.open('http://127.0.0.1:{self.cfg.port}/cast?token={self.cfg.token}"
            "&url='+encodeURIComponent(location.href)+'&media='+encodeURIComponent(v)"
            "+'&title='+encodeURIComponent(document.title),'streamtv','width=420,height=220')})()"
        )

    # -------------------------------------------------------------- Geräte
    def discover(self) -> list[dlna.Renderer]:
        self.log("Suche Fernseher im WLAN …")
        found = dlna.discover(timeout=3.0, extra_hosts=self.cfg.manual_devices)
        if self.cfg.last_device and not any(d.location == self.cfg.last_device for d in found):
            r = dlna.load_renderer(self.cfg.last_device, timeout=2.0)
            if r:
                found.append(r)
        # LG zuerst
        found.sort(key=lambda r: (0 if "lg" in (r.manufacturer + r.name).lower() else 1, r.name))
        with self._lock:
            self.devices = found
            if self.device and not any(d.avtransport_url == self.device.avtransport_url for d in found):
                self.device = None
            if not self.device and found:
                preferred = next((d for d in found if d.location == self.cfg.last_device), found[0])
                self.device = preferred
        if found:
            self.log(f"{len(found)} Gerät(e) gefunden.")
        else:
            self.log("Kein Fernseher gefunden. TV eingeschaltet und im selben WLAN? Ggf. „IP eingeben“ nutzen.")
        self.emit("devices", None)
        return found

    def add_manual(self, ip: str) -> None:
        ip = ip.strip()
        if not ip:
            return
        if ip not in self.cfg.manual_devices:
            self.cfg.manual_devices.append(ip)
            self.cfg.save()
        self.discover()

    def select(self, index: int) -> None:
        with self._lock:
            if 0 <= index < len(self.devices):
                self.device = self.devices[index]
                self.cfg.last_device = self.device.location
                self.cfg.save()

    def _need_device(self) -> dlna.Renderer:
        if not self.device:
            raise UserError("Bitte zuerst einen Fernseher auswählen (🔄 Suchen).")
        return self.device

    def _need_ffmpeg(self) -> str:
        if not self.ffmpeg:
            raise UserError("ffmpeg wurde nicht gefunden. Bitte ffmpeg.exe neben StreamTV.exe legen.")
        return self.ffmpeg

    # ------------------------------------------------------------ Abspielen
    def cast_url(self, url: str, media: str = "", referer: str = "", title: str = "") -> None:
        device = self._need_device()
        ffmpeg = self._need_ffmpeg()
        target = (media or url).strip()
        if not target:
            raise UserError("Bitte eine Adresse eingeben.")
        if not target.startswith(("http://", "https://")):
            target = "https://" + target
        page = (url or referer or "").strip() or None

        self.log(f"Lese Video aus: {target[:90]}")
        try:
            info = extract(target, self.cfg.quality, referer=page if media else None, cookies_browser=self.cfg.cookies_browser)
        except ExtractError as e:
            if not media.startswith("http"):
                raise UserError(f"Kein Video gefunden: {e}") from None
            self.log(f"yt-dlp: {e} – versuche die Stream-Adresse direkt.")
            info = direct_stream(media, title or "Video", referer=page)
        if title and info.title in ("Video", "", None):
            info.title = title
        for note in info.notes:
            self.log(note)
        with self._lock:
            self._play_stream(device, ffmpeg, info, offset=0)

    def _play_stream(self, device: dlna.Renderer, ffmpeg: str, info: StreamInfo, offset: int) -> None:
        max_h = QUALITY_HEIGHT.get(self.cfg.quality, 1080)
        session = MediaSession(
            title=info.title,
            mime=TS_MIME,
            command=lambda: build_remux_cmd(ffmpeg, info.inputs, start=offset, transcode_video=info.transcode_video,
                                            max_height=max_h),
            on_error=lambda m: self.emit("error", m),
        )
        self._send(device, session, seekable=False)
        self.playing = Playing("stream", info.title, session, info, offset)
        live = " (Live)" if info.is_live else ""
        self.log(f"▶ „{info.title}“{live} läuft auf {device.name}.")
        self.emit("playing", self.playing)

    def cast_file(self, path: str) -> None:
        device = self._need_device()
        session = file_session(path)
        with self._lock:
            self._send(device, session, seekable=True)
            self.playing = Playing("file", session.title, session)
        self.log(f"▶ Datei „{session.title}“ läuft auf {device.name}.")
        self.emit("playing", self.playing)

    def cast_file_compat(self, path: str) -> None:
        """Datei über ffmpeg schicken – falls der TV Codec/Format direkt nicht kann."""
        device = self._need_device()
        ffmpeg = self._need_ffmpeg()
        info = direct_stream(path, title=file_session(path).title)
        info.inputs[0].headers = {}
        vcodec, info.duration = probe_file(ffmpeg, path)
        info.transcode_video = video_needs_transcode(vcodec)
        self.log(f"Kompatibilitätsmodus: Video {vcodec or '?'} → "
                 f"{'H.264 (Umwandlung)' if info.transcode_video else 'kopiert'}, Ton → AAC")
        with self._lock:
            self._play_stream(device, ffmpeg, info, 0)

    def start_mirror(self, audio: str, dshow_device: str | None = None) -> None:
        device = self._need_device()
        ffmpeg = self._need_ffmpeg()
        feeder = None
        mode = "none"
        if audio == "Systemton":
            ok, detail = loopback_available()
            if ok:
                mode, feeder = "pcm", make_loopback_feeder(on_error=lambda m: self.emit("error", m))
                self.log(f"Ton von: {detail}")
            else:
                self.log(detail + " – spiegle ohne Ton.")
        elif audio not in ("Kein Ton", "") and dshow_device:
            mode = "dshow"
        fps = self.cfg.mirror_fps
        session = MediaSession(
            title="Bildschirm",
            mime=TS_MIME,
            command=lambda: build_mirror_cmd(ffmpeg, fps=fps, audio=mode, dshow_device=dshow_device),
            stdin_feeder=feeder,
            on_error=lambda m: self.emit("error", m),
        )
        with self._lock:
            self._send(device, session, seekable=False)
            self.playing = Playing("mirror", "Bildschirm", session)
        self.log(f"▶ Bildschirm wird auf {device.name} gespiegelt (Verzögerung ca. 2–5 s).")
        self.emit("playing", self.playing)

    def _send(self, device: dlna.Renderer, session: MediaSession, seekable: bool) -> None:
        self.server.add(session)
        host_ip = local_ip_for(device.host)
        url = self.server.url_for(session, host_ip)
        try:
            device.play_url(url, session.title, session.mime, seekable)
        except dlna.DLNAError as e:
            session.kill()
            raise UserError(str(e)) from None
        threading.Thread(target=self._check_fetched, args=(session, device), daemon=True).start()

    def _check_fetched(self, session: MediaSession, device: dlna.Renderer, wait: float = 15.0) -> None:
        """Häufigster Fehler: Die Windows-Firewall lässt den Fernseher nicht auf den Laptop zugreifen."""
        if self._poll_stop.wait(wait) or session.requests or self.server.sessions.get(session.id) is not session:
            return
        self.emit("error", f"{device.name} hat den Stream nicht abgerufen. Wahrscheinlich blockiert die "
                           f"Windows-Firewall: StreamTV für „Private Netzwerke“ zulassen und das WLAN in Windows "
                           f"als „Privates Netzwerk“ einstellen (siehe Anleitung).")

    # ------------------------------------------------------------ Steuerung
    def toggle_pause(self) -> None:
        device = self._need_device()
        try:
            state = device.transport_state()
            if state == "PLAYING":
                device.pause()
                self.log("⏸ Pause")
            else:
                device.play()
                self.log("▶ Weiter")
        except dlna.DLNAError as e:
            raise UserError(str(e)) from None

    def stop(self) -> None:
        device = self._need_device()
        with self._lock:
            try:
                device.stop()
            except dlna.DLNAError as e:
                self.log(str(e))
            self.server.stop_all()
            self.playing = None
        self.log("⏹ Gestoppt")
        self.emit("playing", None)

    def seek_relative(self, delta: int) -> None:
        state, pos, dur = self.status()
        self.seek_to(pos + delta)

    def seek_to(self, seconds: int) -> None:
        device = self._need_device()
        p = self.playing
        if not p:
            return
        seconds = max(0, int(seconds))
        try:
            if p.kind == "file":
                device.seek(seconds)
            elif p.kind == "stream" and p.info and not p.info.is_live:
                if p.info.duration:
                    seconds = min(seconds, max(0, p.info.duration - 5))
                self.log(f"Springe zu {dlna.format_time(seconds)} …")
                with self._lock:
                    self._play_stream(device, self._need_ffmpeg(), p.info, offset=seconds)
            else:
                raise UserError("Bei Live-Streams und beim Spiegeln kann nicht gespult werden.")
        except dlna.DLNAError as e:
            raise UserError(str(e)) from None

    def set_volume(self, volume: int) -> None:
        device = self._need_device()
        try:
            device.set_volume(volume)
        except dlna.DLNAError as e:
            raise UserError(str(e)) from None

    def get_volume(self) -> int | None:
        if not self.device:
            return None
        try:
            return self.device.get_volume()
        except dlna.DLNAError:
            return None

    def status(self) -> tuple[str, int, int]:
        device, p = self.device, self.playing
        if not device or not p:
            return "STOPPED", 0, 0
        state = device.transport_state()
        try:
            pos, dur = device.position()
        except dlna.DLNAError:
            pos, dur = 0, 0
        if p.kind == "stream":
            pos += p.offset
            dur = p.info.duration if p.info and p.info.duration else 0
        return state, pos, dur

    def _poll_loop(self) -> None:
        while not self._poll_stop.wait(2.0):
            if not self.playing:
                continue
            try:
                self.emit("status", self.status())
            except Exception:
                pass

    # ------------------------------------------------- Bookmarklet / Erweiterung
    def _on_browser_cast(self, data: dict) -> str:
        if not self.device:
            self.emit("error", "Vom Browser empfangen, aber kein Fernseher ausgewählt.")
            return "Kein Fernseher ausgewählt – bitte in StreamTV einen TV wählen."
        self.emit("browser", data)

        def run():
            try:
                self.cast_url(data.get("url", ""), media=data.get("media", ""), referer=data.get("referer", ""),
                              title=data.get("title", ""))
            except UserError as e:
                self.emit("error", str(e))
            except Exception as e:  # pragma: no cover
                self.emit("error", f"Unerwarteter Fehler: {e}")

        threading.Thread(target=run, daemon=True).start()
        return f"Wird an {self.device.name} gesendet …"

