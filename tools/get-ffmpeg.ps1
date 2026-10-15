# Lädt ffmpeg (Windows, 64 Bit) nach .\ffmpeg\ffmpeg.exe
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root 'ffmpeg\ffmpeg.exe'
if (Test-Path $target) { exit 0 }
$url = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
$zip = Join-Path $env:TEMP 'streamtv-ffmpeg.zip'
$tmp = Join-Path $env:TEMP 'streamtv-ffmpeg'
Write-Host "Lade ffmpeg von $url ..."
Invoke-WebRequest -Uri $url -OutFile $zip
Expand-Archive $zip -DestinationPath $tmp -Force
New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
Copy-Item (Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName $target
Remove-Item $zip
Remove-Item $tmp -Recurse
Write-Host "ffmpeg liegt jetzt in $target"
