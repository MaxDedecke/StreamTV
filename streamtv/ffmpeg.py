"""ffmpeg finden und Befehle zusammenbauen.

Alles, was nicht als lokale Datei direkt geschickt wird, läuft durch ffmpeg und
kommt als MPEG-TS beim Fernseher an. Das hat mehrere Vorteile:

* HLS/DASH-Streams (getrennte Video-/Audiospur) werden zu einem Stream zusammengefügt.
* Header wie Referer/Cookies, die viele Seiten verlangen, schickt ffmpeg mit –
  der Fernseher selbst könnte das nicht.
* Audio wird immer nach AAC gewandelt (Opus etc. spielt der LG im TS nicht ab),
  Video wird nur kopiert, wenn es H.264/H.265 ist, sonst nach H.264 gewandelt.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

COPYABLE_VIDEO = ("avc", "h264", "hev", "hvc", "h265", "hevc")
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def popen_kwargs() -> dict:
    """Unter Windows kein schwarzes Konsolenfenster für ffmpeg öffnen."""
    return {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def find_ffmpeg() -> str | None:
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / exe)
        candidates.append(Path(sys.executable).parent / exe)
    here = Path(__file__).resolve().parent
    candidates += [here / exe, here.parent / exe, here.parent / "ffmpeg" / exe, here.parent / "ffmpeg" / "bin" / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("ffmpeg")


@dataclass
class InputSpec:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


def video_needs_transcode(vcodec: str | None) -> bool:
    """True, wenn der Codec nicht ins MPEG-TS für den TV kopiert werden kann."""
    if not vcodec or vcodec == "none":
        return False  # unbekannt (häufig bei HLS) -> ist praktisch immer H.264
    return not vcodec.lower().startswith(COPYABLE_VIDEO)


def _header_args(headers: dict[str, str]) -> list[str]:
    args: list[str] = []
    headers = dict(headers)
    ua = headers.pop("User-Agent", None) or headers.pop("user-agent", None)
    if ua:
        args += ["-user_agent", ua]
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    return args


def build_remux_cmd(
    ffmpeg: str,
    inputs: list[InputSpec],
    start: int = 0,
    transcode_video: bool = False,
    max_height: int | None = None,
) -> list[str]:
    """ffmpeg-Befehl: 1–2 Eingänge (Video+Audio) -> MPEG-TS auf stdout."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin"]
    for spec in inputs:
        if spec.url.startswith(("http://", "https://")):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            cmd += _header_args(spec.headers)
        if start > 0:
            cmd += ["-ss", str(int(start))]
        cmd += ["-i", spec.url]

    if len(inputs) >= 2:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]

    if transcode_video:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
        if max_height:
            cmd += ["-vf", f"scale=-2:'min({max_height},ih)'"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += ["-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]
    return cmd


def build_mirror_cmd(
    ffmpeg: str,
    fps: int = 30,
    audio: str = "none",
    dshow_device: str | None = None,
    max_width: int = 1920,
    bitrate: str = "8M",
) -> list[str]:
    """Bildschirmaufnahme (gdigrab) + Ton -> H.264/AAC MPEG-TS auf stdout.

    ``audio``: ``"pcm"`` = rohes PCM (s16le, 48 kHz, Stereo) kommt über stdin
    (Systemton per WASAPI-Loopback), ``"dshow"`` = DirectShow-Gerät (z. B. Stereomix),
    ``"none"`` = ohne Ton.
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning"]
    # Kein -thread_queue_size: ffmpeg 8 lehnt die Option bei Eingängen ab.
    cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1", "-i", "desktop"]
    if audio == "pcm":
        cmd += ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0"]
    elif audio == "dshow" and dshow_device:
        cmd += ["-f", "dshow", "-i", f"audio={dshow_device}"]
    else:
        cmd += ["-nostdin"]
    has_audio = audio == "pcm" or (audio == "dshow" and bool(dshow_device))

    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0"]
    cmd += [
        "-vf", f"scale='min({max_width},iw)':-2,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-g", str(fps * 2), "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", "4M",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-af", "aresample=async=1"]
    cmd += ["-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]
    return cmd


def list_dshow_audio_devices(ffmpeg: str) -> list[str]:
    """Audiogeräte für DirectShow (nur Windows), z. B. 'Stereomix (Realtek Audio)'."""
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, **popen_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_dshow_devices(proc.stderr)


def parse_dshow_devices(output: str) -> list[str]:
    devices: list[str] = []
    in_audio = False
    for line in output.splitlines():
        if "DirectShow audio devices" in line:
            in_audio = True
            continue
        if "DirectShow video devices" in line:
            in_audio = False
            continue
        if "Alternative name" in line:
            continue
        m = re.search(r'"([^"]+)"(?:\s+\((audio|video|audio, video)\))?', line)
        if not m:
            continue
        kind = m.group(2)
        if kind == "audio" or (kind is None and in_audio):
            devices.append(m.group(1))
    return devices


def probe_file(ffmpeg: str, path: str) -> tuple[str | None, int]:
    """(Video-Codec, Dauer in Sekunden) einer lokalen Datei über ``ffmpeg -i``."""
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20, **popen_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None, 0
    return parse_probe(proc.stderr)


def parse_probe(output: str) -> tuple[str | None, int]:
    vcodec = None
    m = re.search(r"Stream #\S+.*?: Video: (\w+)", output)
    if m:
        vcodec = m.group(1)
    duration = 0
    d = re.search(r"Duration: (\d+):(\d+):(\d+)", output)
    if d:
        h, mi, se = (int(x) for x in d.groups())
        duration = h * 3600 + mi * 60 + se
    return vcodec, duration


def run_selftest_encode(ffmpeg: str, seconds: int = 2) -> bytes:
    """Erzeugt ein Testbild und schickt es durch die Remux-Pipeline (für ``--selftest``)."""
    src = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-c:a", "aac",
           "-shortest", "-y"]
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "streamtv-selftest.mp4"
    subprocess.run(src + [str(tmp)], check=True, capture_output=True, timeout=60, **popen_kwargs())
    out = subprocess.run(build_remux_cmd(ffmpeg, [InputSpec(str(tmp))]), check=True, capture_output=True, timeout=60,
                         **popen_kwargs())
    tmp.unlink(missing_ok=True)
    return out.stdout
