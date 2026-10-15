@echo off
rem Startet StreamTV direkt aus dem Quellcode (ohne EXE zu bauen).
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\get-ffmpeg.ps1
start "" pythonw -m streamtv
