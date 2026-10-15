"""Speichert Einstellungen in %APPDATA%\\StreamTV\\config.json."""

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from streamtv import APP_NAME


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


@dataclass
class Config:
    port: int = 8765
    token: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    last_device: str = ""  # LOCATION-URL der Gerätebeschreibung
    manual_devices: list[str] = field(default_factory=list)
    quality: str = "1080p"
    cookies_browser: str = "Keine"
    mirror_audio: str = "Systemton"
    mirror_fps: int = 30

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_dir() / "config.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        if not data:
            cfg.save(path)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "config.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass
