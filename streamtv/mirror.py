"""Bildschirm spiegeln: Desktop per ffmpeg (gdigrab) + Systemton per WASAPI-Loopback.

Der Systemton (das, was aus den Laptop-Lautsprechern käme) wird mit dem Paket
``soundcard`` abgegriffen und als rohes PCM in ffmpegs stdin geschrieben. Da
Windows bei Stille keine Audiodaten liefert, füllt der Schreib-Thread Lücken mit
Stille auf. So bleibt der Ton durchgehend und Bild und Ton bleiben synchron.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_FRAME = 2 * CHANNELS  # s16le
BLOCK_FRAMES = 960  # 20 ms


def loopback_available() -> tuple[bool, str]:
    try:
        import numpy  # noqa: F401
        import soundcard as sc
    except Exception as e:  # ImportError oder Treiberfehler
        return False, f"Systemton nicht verfügbar ({e.__class__.__name__}: {e})"
    try:
        spk = sc.default_speaker()
        return True, spk.name
    except Exception as e:
        return False, f"Kein Wiedergabegerät gefunden ({e})"


def make_loopback_feeder(on_error=None):
    """Liefert eine Funktion für ``MediaSession.stdin_feeder``."""

    def feeder(proc: subprocess.Popen, stop: threading.Event) -> None:
        q: queue.Queue[bytes] = queue.Queue(maxsize=200)

        def capture():
            try:
                import numpy as np
                import soundcard as sc

                spk = sc.default_speaker()
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
                with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_FRAMES) as rec:
                    while not stop.is_set():
                        data = rec.record(numframes=BLOCK_FRAMES)
                        pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                        try:
                            q.put(pcm, timeout=0.5)
                        except queue.Full:
                            pass
            except Exception as e:
                if on_error and not stop.is_set():
                    on_error(f"Systemton-Aufnahme fehlgeschlagen: {e}. Es wird Stille gesendet.")

        threading.Thread(target=capture, daemon=True, name="loopback").start()
        writer_loop(proc, stop, q)

    return feeder


def writer_loop(proc: subprocess.Popen, stop: threading.Event, q: "queue.Queue[bytes]") -> None:
    """Schreibt Audio im Echtzeit-Takt nach ffmpeg; fehlende Daten = Stille."""
    start = time.monotonic()
    frames_written = 0
    pending = b""
    silence_block = b"\x00" * (BLOCK_FRAMES * BYTES_PER_FRAME)
    try:
        while not stop.is_set() and proc.poll() is None:
            due_frames = int((time.monotonic() - start) * SAMPLE_RATE)
            # gesammelte Daten holen
            while True:
                try:
                    pending += q.get_nowait()
                except queue.Empty:
                    break
            missing = due_frames - frames_written
            if missing <= 0:
                time.sleep(0.005)
                continue
            out = b""
            if pending:
                take = min(len(pending), missing * BYTES_PER_FRAME)
                take -= take % BYTES_PER_FRAME
                out, pending = pending[:take], pending[take:]
            gap = missing * BYTES_PER_FRAME - len(out)
            # Stille nur auffüllen, wenn wirklich >40 ms nichts kam (sonst kurz warten)
            if gap > 0 and not pending and missing > BLOCK_FRAMES * 2:
                while gap > 0:
                    n = min(gap, len(silence_block))
                    out += silence_block[:n]
                    gap -= n
            # zu viel gepuffert (Uhr des Audiogeräts läuft schneller) -> verwerfen
            if len(pending) > SAMPLE_RATE * BYTES_PER_FRAME // 2:
                pending = b""
            if out:
                proc.stdin.write(out)
                frames_written += len(out) // BYTES_PER_FRAME
            else:
                time.sleep(0.005)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def make_silence_feeder():
    """Für Tests/ohne Soundkarte: nur Stille im Echtzeit-Takt."""

    def feeder(proc: subprocess.Popen, stop: threading.Event) -> None:
        writer_loop(proc, stop, queue.Queue())

    return feeder
