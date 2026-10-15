"""Einstiegspunkt: ``python -m streamtv`` bzw. StreamTV.exe."""

from __future__ import annotations

import argparse
import sys

from streamtv import APP_NAME, __version__


def _setup_streams() -> None:
    """In der Fenster-EXE gibt es keine Konsole (stdout/stderr = None) – auf Logdatei umleiten."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from streamtv.config import config_dir

    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        log = open(config_dir() / "streamtv.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        import os

        log = open(os.devnull, "w")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def selftest() -> int:
    """Prüft die gebaute App ohne Fernseher (wird im Windows-Build ausgeführt).

    Die Ausgabe landet zusätzlich in %TEMP%\\streamtv-selftest.txt, weil die
    Fenster-EXE keine Konsole hat.
    """
    import os
    import subprocess
    from pathlib import Path

    report = Path(os.environ.get("TEMP", "/tmp")) / "streamtv-selftest.txt"
    lines: list[str] = []

    def print(*parts):  # noqa: A001 - bewusst überschrieben
        line = " ".join(str(p) for p in parts)
        lines.append(line)
        sys.stdout.write(line + "\n")
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    from streamtv import dlna, extractor, gui, mirror, server  # noqa: F401  (Imports prüfen)
    from streamtv.ffmpeg import build_mirror_cmd, find_ffmpeg, popen_kwargs, run_selftest_encode

    ffmpeg = find_ffmpeg()
    print(f"{APP_NAME} {__version__} – Selbsttest")
    print("ffmpeg:", ffmpeg)
    if not ffmpeg:
        print("FEHLER: ffmpeg nicht gefunden")
        return 1
    ts = run_selftest_encode(ffmpeg)
    if len(ts) < 10_000 or ts[0] != 0x47:
        print("FEHLER: MPEG-TS-Ausgabe ungültig", len(ts))
        return 1
    print(f"Remux OK ({len(ts)} Bytes MPEG-TS)")
    import yt_dlp

    print("yt-dlp:", yt_dlp.version.__version__)
    ok, detail = mirror.loopback_available()
    print("Systemton-Loopback:", "OK – " + detail if ok else detail)
    if sys.platform == "win32":
        cmd = build_mirror_cmd(ffmpeg, fps=10)
        cmd = cmd[: cmd.index("-f", len(cmd) - 6)] + ["-t", "2", "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=60, **popen_kwargs())
        if r.returncode != 0:
            print("FEHLER Bildschirmaufnahme (gdigrab):", r.stderr.decode(errors="replace")[-400:])
            return 1
        print("Bildschirmaufnahme (gdigrab): OK")
    print("Selbsttest erfolgreich.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Videos per WLAN auf den Fernseher streamen")
    parser.add_argument("--selftest", action="store_true", help="Komponenten prüfen und beenden")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    _setup_streams()
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()

    from streamtv.config import Config
    from streamtv.gui import run

    run(Config.load())
    return 0
