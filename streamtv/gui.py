"""Fenster der App (tkinter, ohne zusätzliche Abhängigkeiten)."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from streamtv import APP_NAME, __version__
from streamtv.config import Config
from streamtv.controller import Controller, UserError
from streamtv.dlna import format_time, parse_time
from streamtv.extractor import COOKIE_BROWSERS, QUALITY_HEIGHT
from streamtv.ffmpeg import list_dshow_audio_devices

VIDEO_TYPES = [
    ("Videos", "*.mp4 *.mkv *.avi *.mov *.m4v *.ts *.m2ts *.webm *.wmv *.mpg *.mpeg"),
    ("Alle Dateien", "*.*"),
]
STATE_DE = {
    "PLAYING": "Spielt", "PAUSED_PLAYBACK": "Pausiert", "STOPPED": "Gestoppt",
    "TRANSITIONING": "Lädt …", "NO_MEDIA_PRESENT": "Kein Medium", "UNKNOWN": "–",
}


class App:
    def __init__(self, root: tk.Tk, cfg: Config):
        self.root = root
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()
        self.ctl = Controller(cfg, lambda kind, data: self.events.put((kind, data)))
        self._volume_job = None
        self._busy = 0
        self.dshow_devices: list[str] = []

        root.title(f"{APP_NAME} – Laptop → Fernseher")
        icon = _resource("assets/streamtv.ico")
        if icon.exists():
            try:
                root.iconbitmap(str(icon))
            except tk.TclError:
                pass
        root.geometry("720x620")
        root.minsize(620, 520)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        try:
            self.ctl.start()
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"Port {cfg.port} ist belegt ({e}).\nLäuft StreamTV schon? Sonst in "
                f"der config.json einen anderen Port eintragen.",
            )
            root.destroy()
            return
        if not self.ctl.ffmpeg:
            self.log("⚠ ffmpeg nicht gefunden – Websites und Spiegeln funktionieren erst mit ffmpeg.exe.")
        self.root.after(100, self._pump)
        self.refresh_devices()
        threading.Thread(target=self._load_dshow, daemon=True).start()

    # ================================================================ Aufbau
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=6)

        # --- Fernseher
        top = ttk.LabelFrame(self.root, text="📺 Fernseher")
        top.pack(fill="x", **pad)
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(top, textvariable=self.device_var, state="readonly")
        self.device_box.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        self.device_box.bind("<<ComboboxSelected>>", lambda e: self.ctl.select(self.device_box.current()))
        ttk.Button(top, text="🔄 Suchen", command=self.refresh_devices).pack(side="left", padx=2)
        ttk.Button(top, text="IP eingeben …", command=self.manual_ip).pack(side="left", padx=(2, 6))

        # --- Registerkarten
        nb = ttk.Notebook(self.root)
        nb.pack(fill="x", **pad)

        web = ttk.Frame(nb, padding=8)
        nb.add(web, text="🌐 Website / Video-Link")
        ttk.Label(web, text="Adresse der Seite mit dem Video (oder direkter .m3u8/.mp4-Link):").grid(
            row=0, column=0, columnspan=4, sticky="w")
        self.url_var = tk.StringVar()
        entry = ttk.Entry(web, textvariable=self.url_var)
        entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        entry.bind("<Return>", lambda e: self.cast_url())
        ttk.Button(web, text="📋 Einfügen", command=self.paste_url).grid(row=1, column=3, padx=(4, 0))
        ttk.Label(web, text="Qualität:").grid(row=2, column=0, sticky="w")
        self.quality_var = tk.StringVar(value=self.cfg.quality)
        q = ttk.Combobox(web, textvariable=self.quality_var, values=list(QUALITY_HEIGHT), state="readonly", width=8)
        q.grid(row=2, column=1, sticky="w")
        q.bind("<<ComboboxSelected>>", lambda e: self._save_setting("quality", self.quality_var.get()))
        ttk.Label(web, text="Anmeldung (Cookies) aus Browser:").grid(row=3, column=0, sticky="w")
        self.cookies_var = tk.StringVar(value=self.cfg.cookies_browser)
        c = ttk.Combobox(web, textvariable=self.cookies_var, values=list(COOKIE_BROWSERS), state="readonly", width=8)
        c.grid(row=3, column=1, sticky="w")
        c.bind("<<ComboboxSelected>>", lambda e: self._save_setting("cookies_browser", self.cookies_var.get()))
        ttk.Button(web, text="▶ Auf dem TV abspielen", style="Big.TButton", command=self.cast_url).grid(
            row=2, column=2, rowspan=2, columnspan=2, sticky="e")
        web.columnconfigure(2, weight=1)

        files = ttk.Frame(nb, padding=8)
        nb.add(files, text="📁 Datei")
        ttk.Label(files, text="Ein Video von diesem Laptop auf dem Fernseher abspielen.").pack(anchor="w")
        row = ttk.Frame(files)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="📂 Video wählen und abspielen …", style="Big.TButton", command=self.cast_file).pack(
            side="left")
        self.compat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Kompatibilitätsmodus (wenn Ton/Bild fehlt)", variable=self.compat_var).pack(
            side="left", padx=12)

        mirror = ttk.Frame(nb, padding=8)
        nb.add(mirror, text="🖥 Bildschirm spiegeln")
        ttk.Label(
            mirror,
            text="Überträgt den ganzen Bildschirm – für Seiten, aus denen kein Video ausgelesen werden kann.\n"
                 "Hinweis: ca. 2–5 Sekunden Verzögerung. Video im Browser auf Vollbild (F11) stellen.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(mirror, text="Ton:").grid(row=1, column=0, sticky="w", pady=6)
        self.audio_var = tk.StringVar(value=self.cfg.mirror_audio)
        self.audio_box = ttk.Combobox(mirror, textvariable=self.audio_var, values=["Systemton", "Kein Ton"],
                                      state="readonly", width=40)
        self.audio_box.grid(row=1, column=1, sticky="w")
        self.audio_box.bind("<<ComboboxSelected>>",
                            lambda e: self._save_setting("mirror_audio", self.audio_var.get()))
        ttk.Button(mirror, text="▶ Spiegeln starten", style="Big.TButton", command=self.start_mirror).grid(
            row=1, column=2, sticky="e", padx=(12, 0))
        mirror.columnconfigure(2, weight=1)

        # --- Steuerung
        ctrl = ttk.LabelFrame(self.root, text="Steuerung")
        ctrl.pack(fill="x", **pad)
        ttk.Button(ctrl, text="⏪ 30 s", width=7, command=lambda: self.bg(self.ctl.seek_relative, -30)).pack(
            side="left", padx=(6, 2), pady=6)
        ttk.Button(ctrl, text="⏯", width=4, command=lambda: self.bg(self.ctl.toggle_pause)).pack(side="left", padx=2)
        ttk.Button(ctrl, text="⏹", width=4, command=lambda: self.bg(self.ctl.stop)).pack(side="left", padx=2)
        ttk.Button(ctrl, text="30 s ⏩", width=7, command=lambda: self.bg(self.ctl.seek_relative, 30)).pack(
            side="left", padx=2)
        ttk.Button(ctrl, text="Springen …", command=self.seek_dialog).pack(side="left", padx=2)
        ttk.Label(ctrl, text="🔊").pack(side="left", padx=(12, 0))
        self.volume = tk.IntVar(value=20)
        ttk.Scale(ctrl, from_=0, to=100, variable=self.volume, command=self._volume_changed, length=120).pack(
            side="left", padx=4)
        self.status_var = tk.StringVar(value="Bereit")
        ttk.Label(ctrl, textvariable=self.status_var, width=26, anchor="e").pack(side="right", padx=6)

        # --- Browser-Integration
        br = ttk.LabelFrame(self.root, text="Direkt aus dem Browser senden")
        br.pack(fill="x", **pad)
        ttk.Label(br, text="Lesezeichen „Auf TV“ anlegen: Code kopieren → neues Lesezeichen → als Adresse einfügen.",
                  wraplength=470, justify="left").pack(side="left", padx=6, pady=6)
        ttk.Button(br, text="Erweiterung …", command=self.open_extension_help).pack(side="right", padx=(2, 6))
        ttk.Button(br, text="📋 Code kopieren", command=self.copy_bookmarklet).pack(side="right", padx=2)

        # --- Protokoll
        self.log_box = scrolledtext.ScrolledText(self.root, height=10, state="disabled", wrap="word",
                                                 font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, **pad)
        self.log(f"{APP_NAME} {__version__} gestartet. Browser-Schnittstelle: http://127.0.0.1:{self.cfg.port}")

    # ============================================================== Helfer
    def log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S ") + msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def bg(self, fn, *args, done=None) -> None:
        """Führt ``fn`` im Hintergrund aus, damit das Fenster nicht einfriert."""
        self._busy += 1
        self.root.configure(cursor="watch")

        def run():
            try:
                result = fn(*args)
                self.events.put(("_done", (done, result)))
            except UserError as e:
                self.events.put(("error", str(e)))
            except Exception as e:
                self.events.put(("error", f"Fehler: {e.__class__.__name__}: {e}"))
            finally:
                self.events.put(("_idle", None))

        threading.Thread(target=run, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _handle(self, kind: str, data) -> None:
        if kind == "log":
            self.log(data)
        elif kind == "error":
            self.log("❌ " + data)
        elif kind == "devices":
            self._fill_devices()
        elif kind == "browser":
            self.log(f"Vom Browser: {data.get('title') or data.get('url')}")
            self.url_var.set(data.get("url") or data.get("media") or "")
        elif kind == "playing":
            self.status_var.set("Startet …" if data else "Gestoppt")
            if data:
                self.bg(self.ctl.get_volume, done=self._set_volume_display)
        elif kind == "status":
            state, pos, dur = data
            label = STATE_DE.get(state, state)
            t = format_time(pos) + (f" / {format_time(dur)}" if dur else "")
            self.status_var.set(f"{label}  {t}")
        elif kind == "_done":
            cb, result = data
            if cb:
                cb(result)
        elif kind == "_idle":
            self._busy = max(0, self._busy - 1)
            if not self._busy:
                self.root.configure(cursor="")

    def _save_setting(self, name: str, value) -> None:
        setattr(self.cfg, name, value)
        self.cfg.save()

    # ============================================================ Aktionen
    def refresh_devices(self) -> None:
        self.bg(self.ctl.discover)

    def _fill_devices(self) -> None:
        labels = [d.label for d in self.ctl.devices]
        self.device_box["values"] = labels
        if self.ctl.device in self.ctl.devices:
            self.device_box.current(self.ctl.devices.index(self.ctl.device))
        elif not labels:
            self.device_var.set("– kein Fernseher gefunden –")

    def manual_ip(self) -> None:
        ip = simpledialog.askstring(APP_NAME, "IP-Adresse des Fernsehers (steht am LG unter\n"
                                              "Einstellungen → Allgemein → Netzwerk → WLAN → Erweitert):",
                                    parent=self.root)
        if ip:
            self.bg(self.ctl.add_manual, ip)

    def paste_url(self) -> None:
        try:
            self.url_var.set(self.root.clipboard_get().strip())
        except tk.TclError:
            pass

    def cast_url(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.paste_url()
            url = self.url_var.get().strip()
        if url:
            self.bg(self.ctl.cast_url, url)

    def cast_file(self) -> None:
        path = filedialog.askopenfilename(title="Video wählen", filetypes=VIDEO_TYPES)
        if not path:
            return
        if self.compat_var.get():
            self.bg(self.ctl.cast_file_compat, path)
        else:
            self.bg(self.ctl.cast_file, path)

    def start_mirror(self) -> None:
        choice = self.audio_var.get()
        dshow = choice if choice in self.dshow_devices else None
        self.bg(self.ctl.start_mirror, choice, dshow)

    def _load_dshow(self) -> None:
        if not self.ctl.ffmpeg:
            return
        self.dshow_devices = list_dshow_audio_devices(self.ctl.ffmpeg)
        if self.dshow_devices:
            self.events.put(("_done", (lambda _: self.audio_box.configure(
                values=["Systemton", "Kein Ton"] + self.dshow_devices), None)))

    def seek_dialog(self) -> None:
        value = simpledialog.askstring(APP_NAME, "Zu welcher Stelle springen? (z. B. 1:23:45 oder 45:00)",
                                       parent=self.root)
        if not value:
            return
        parts = value.strip().split(":")
        if len(parts) == 2:
            value = "0:" + value.strip()
        seconds = parse_time(value)
        if seconds == 0 and value.strip() not in ("0", "0:00", "0:0:00"):
            try:
                seconds = int(float(value) * 60)  # nur Minuten eingegeben
            except ValueError:
                messagebox.showwarning(APP_NAME, "Bitte als Stunden:Minuten:Sekunden eingeben.")
                return
        self.bg(self.ctl.seek_to, seconds)

    def _set_volume_display(self, vol) -> None:
        if vol is not None:
            self.volume.set(vol)

    def _volume_changed(self, _value) -> None:
        if self._volume_job:
            self.root.after_cancel(self._volume_job)
        self._volume_job = self.root.after(300, lambda: self.bg(self.ctl.set_volume, self.volume.get()))

    def copy_bookmarklet(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.ctl.bookmarklet())
        messagebox.showinfo(
            APP_NAME,
            "Der Lesezeichen-Code ist kopiert.\n\n"
            "1. Im Browser Strg+Umschalt+O (Favoriten verwalten) öffnen\n"
            "2. „Favorit hinzufügen“ – Name: Auf TV\n"
            "3. Bei URL den Code mit Strg+V einfügen und speichern\n\n"
            "Danach auf einer Video-Seite einfach das Lesezeichen „Auf TV“ anklicken.",
        )

    def open_extension_help(self) -> None:
        folder = _extension_folder()
        msg = ("Die Browser-Erweiterung erkennt Video-Streams auf jeder Seite automatisch.\n\n"
               "Installation in Edge/Chrome:\n"
               "1. edge://extensions bzw. chrome://extensions öffnen\n"
               "2. „Entwicklermodus“ einschalten\n"
               "3. „Entpackt laden“ und diesen Ordner wählen:\n\n"
               f"{folder}")
        if messagebox.askokcancel(APP_NAME, msg + "\n\nOrdner jetzt öffnen?"):
            webbrowser.open(folder.as_uri() if folder.exists() else str(folder))

    def on_close(self) -> None:
        try:
            self.ctl.shutdown()
        finally:
            self.root.destroy()


def _resource(rel: str) -> Path:
    """Datei aus dem Programmordner (EXE) bzw. dem Projektordner (Quellcode)."""
    import sys

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def _extension_folder() -> Path:
    import sys

    beside_exe = Path(sys.executable).parent / "extension"  # dort legt der Build sie gut sichtbar ab
    if getattr(sys, "frozen", False) and beside_exe.exists():
        return beside_exe
    return _resource("extension")


def run(cfg: Config) -> None:
    import sys

    if sys.platform == "win32":  # scharfe Schrift auf hochauflösenden Displays
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root, cfg)
    try:
        root.mainloop()
    except tk.TclError:
        pass
