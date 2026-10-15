@echo off
rem Baut dist\StreamTV\StreamTV.exe auf einem Windows-PC (Python 3.11+ muss installiert sein).
setlocal
cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt || goto :error

powershell -NoProfile -ExecutionPolicy Bypass -File tools\get-ffmpeg.ps1 || goto :error

pyinstaller --noconfirm StreamTV.spec || goto :error
echo.
echo Fertig: dist\StreamTV\StreamTV.exe
exit /b 0

:error
echo Build fehlgeschlagen.
exit /b 1
