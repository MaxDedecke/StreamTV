# PyInstaller-Konfiguration:  pyinstaller StreamTV.spec
# Ergebnis: dist\StreamTV\StreamTV.exe (+ ffmpeg.exe, extension\)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("extension", "extension"), ("assets", "assets")]
binaries = []
for candidate in ("ffmpeg/ffmpeg.exe", "ffmpeg/bin/ffmpeg.exe", "ffmpeg.exe"):
    if os.path.isfile(candidate):
        binaries.append((candidate, "."))
        break

hiddenimports = []
try:
    datas += collect_data_files("soundcard")  # enthält die WASAPI-Headerdateien
    hiddenimports += collect_submodules("soundcard")
except Exception:
    pass

a = Analysis(
    ["run_streamtv.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "matplotlib", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StreamTV",
    console=False,
    icon="assets/streamtv.ico" if os.path.isfile("assets/streamtv.ico") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="StreamTV")
