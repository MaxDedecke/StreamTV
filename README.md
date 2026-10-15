# 📺 StreamTV – vom Laptop per WLAN auf den Fernseher

Windows-App, die Videos und Streams aus dem Browser **ohne HDMI-Kabel** auf den
Fernseher bringt. Gebaut und getestet für **LG-Smart-TVs (webOS)**. Sie
funktioniert auch mit anderen DLNA-fähigen Fernsehern (Samsung, Sony, Philips,
Panasonic, Hisense …).

## Was die App kann

| Funktion | Wofür |
|---|---|
| 🌐 **Website / Video-Link** | Adresse einer Seite mit Video einfügen → läuft auf dem TV. Funktioniert mit tausenden Seiten (Mediatheken, Vimeo, Twitch, Nachrichten-Seiten, eigene Streams, direkte `.m3u8`/`.mp4`-Links …). |
| 🔖 **Lesezeichen „Auf TV“** | Ein Klick im Browser schickt die gerade geöffnete Seite an den Fernseher. |
| 🧩 **Browser-Erweiterung** (Edge/Chrome) | Erkennt Video-Streams auf jeder Seite automatisch. Hilfreich, wenn die Seite selbst nicht ausgelesen werden kann. |
| 📁 **Datei** | Videos vom Laptop (MP4, MKV, AVI …) auf dem TV abspielen, mit Vorspulen. |
| 🖥 **Bildschirm spiegeln** | Notlösung für alles andere: überträgt den ganzen Bildschirm samt Ton (ca. 2–5 s Verzögerung). |
| ⏯ **Steuerung** | Pause/Weiter, Stopp, ±30 s, zu einer Stelle springen, TV-Lautstärke. |

## So funktioniert es

```
Browser ──(Lesezeichen / Erweiterung / Link)──▶ StreamTV auf dem Laptop
                                                  │  holt die Video-Adresse (yt-dlp)
                                                  │  bereitet den Stream auf (ffmpeg)
                                                  ▼
                       LG-Fernseher ◀── DLNA ── „Spiel diese Adresse ab“
                       (ruft den Stream per WLAN vom Laptop ab)
```

Der Fernseher lädt das Video über das WLAN vom Laptop. Der Laptop muss deshalb
währenddessen eingeschaltet bleiben. Im Browser kann man in der Zeit weiterarbeiten
oder ihn schließen.

## Installation

### Variante A: fertige EXE (empfohlen)
1. Unter **Actions → Windows-Build** den neuesten erfolgreichen Lauf öffnen und
   das Artefakt **StreamTV-Windows** herunterladen. Bei veröffentlichten Versionen
   liegt die ZIP unter **Releases**.
2. ZIP entpacken, z. B. nach `C:\Programme\StreamTV` oder auf den Desktop.
3. `StreamTV.exe` starten. ffmpeg ist schon dabei.
4. **Windows-Firewall-Abfrage: „Private Netzwerke“ erlauben!** (siehe unten)

> Windows SmartScreen meldet eventuell „Unbekannter Herausgeber“, weil die EXE
> nicht signiert ist: **Weitere Informationen → Trotzdem ausführen**.

### Variante B: aus dem Quellcode
Python 3.11 oder neuer installieren (bei der Installation „Add to PATH“ anhaken), dann:
- `start.bat` doppelklicken: richtet alles ein und startet die App, oder
- `build.bat` doppelklicken: baut `dist\StreamTV\StreamTV.exe`.

## Erste Schritte

1. LG-Fernseher einschalten. Laptop und TV müssen im **selben WLAN** sein.
2. StreamTV starten. Der Fernseher erscheint oben, z. B. `[LG] webOS TV OLED55C3`.
   Falls nicht: **🔄 Suchen** klicken oder **IP eingeben …**. Die IP steht am LG unter
   *Einstellungen → Allgemein → Netzwerk → WLAN-Verbindung → Erweiterte Einstellungen*.
3. Im Browser die Seite mit dem Video öffnen, die Adresse kopieren, in StreamTV
   **📋 Einfügen** → **▶ Auf dem TV abspielen**.

Beim ersten Mal fragt der LG eventuell, ob er die Verbindung zulassen soll: mit
**Zulassen** bestätigen.

### Lesezeichen „Auf TV“ einrichten (einmalig)
1. In StreamTV auf **📋 Code kopieren** klicken.
2. Im Browser `Strg+Umschalt+O` (Favoriten verwalten) → **Favorit hinzufügen**.
3. Name: `Auf TV`, bei URL den Code mit `Strg+V` einfügen, speichern.
4. Ab jetzt auf einer Video-Seite einfach **Auf TV** in der Favoritenleiste anklicken.

### Browser-Erweiterung installieren (optional, für schwierige Seiten)
1. `edge://extensions` (bzw. `chrome://extensions`) öffnen.
2. **Entwicklermodus** einschalten.
3. **Entpackt laden** → den Ordner `extension` im StreamTV-Ordner wählen.
4. Auf einer Video-Seite das Video kurz starten. Das StreamTV-Symbol zeigt eine Zahl
   (erkannte Streams). Symbol anklicken → beim gewünschten Stream **▶ TV**. Die
   **HLS**-Einträge (`.m3u8`) sind meist die besten.

## ⚠ Windows-Firewall (wichtigster Punkt!)

Der Fernseher muss den Laptop erreichen können. Wenn der TV „lädt“, aber nichts
kommt, meldet StreamTV nach 15 Sekunden: *„hat den Stream nicht abgerufen“*.

- Bei der Firewall-Abfrage beim ersten Start **„Private Netzwerke“** anhaken.
- Das WLAN muss in Windows als **privates Netzwerk** eingestellt sein:
  *Einstellungen → Netzwerk und Internet → WLAN → (dein WLAN) → Netzwerkprofiltyp: Privat*.
- Abfrage verpasst? *Windows-Sicherheit → Firewall- & Netzwerkschutz → Zugriff
  von App durch die Firewall zulassen* → StreamTV bei „Privat“ anhaken. Alternativ
  in einer Administrator-Eingabeaufforderung:
  ```
  netsh advfirewall firewall add rule name="StreamTV" dir=in action=allow protocol=TCP localport=8765 profile=private
  ```
- Gast-WLAN bzw. „Client-Isolation/AP-Isolation“ am Router verhindert, dass Geräte
  sich sehen. Dann das normale WLAN verwenden.

## Was geht – und was nicht

- ✅ Die meisten Seiten mit frei abspielbaren Videos und Livestreams (z. B. ARD/ZDF-Mediathek,
  arte, Vimeo, Twitch, Dailymotion, Nachrichtenseiten, IPTV-`.m3u8`-Links).
- ✅ Seiten mit Anmeldung: unter *Anmeldung (Cookies) aus Browser* den Browser wählen,
  in dem man eingeloggt ist. **Firefox** funktioniert am zuverlässigsten. Bei Chrome/Edge
  muss der Browser dafür ggf. geschlossen sein.
- ❌ **Netflix, Disney+, Amazon Prime, Sky/WOW, DAZN …** sind kopiergeschützt (DRM).
  Diese Streams kann keine App weiterleiten; auch beim Spiegeln bleibt das Bild schwarz.
  Dafür die **Apps direkt auf dem LG** verwenden.
- ℹ **YouTube**: Am besten die YouTube-App auf dem LG nutzen (Handy/Laptop → „Auf TV
  wiedergeben“). StreamTV kann YouTube auch, braucht dafür aber evtl. zusätzlich
  [Deno](https://deno.com) auf dem PC (Anforderung von yt-dlp).
- ℹ Wenn bei einer **Datei** Ton oder Bild fehlt (z. B. DTS-Ton, den neuere LGs nicht
  mehr abspielen): Häkchen **Kompatibilitätsmodus** setzen. Der Ton wird dann umgewandelt.

### Und die Windows-Funktion „Drahtlos verbinden“ (Win + K)?
Die gibt es noch: `Win + K` spiegelt den Bildschirm per **Miracast**. Bei LG-TVs heißt
das Gegenstück „Screen Share“. Das kann man ausprobieren, es ist aber oft wacklig
(Abbrüche, Verzögerung, WLAN-Chip muss Miracast können, funktioniert nicht über alle
Router). StreamTV schickt dagegen das **Video selbst** in voller Qualität an den TV.
Das ist stabiler, und der Laptop bleibt frei.

## Probleme lösen

| Problem | Lösung |
|---|---|
| Kein Fernseher gefunden | TV an? Gleiches WLAN? *IP eingeben …* probieren. Am LG: *Einstellungen → Allgemein → Geräte → TV-Verwaltung/Externe Geräte* bzw. „LG Connect Apps“ einschalten (je nach Modell). |
| TV zeigt „Datei wird nicht unterstützt“ | Bei Dateien den *Kompatibilitätsmodus* nutzen. Bei Websites die Qualität auf *1080p* oder *720p* stellen. |
| Stream startet nicht, Meldung zur Firewall | Siehe Abschnitt *Windows-Firewall*. |
| „Kein Video gefunden“ | Browser-Erweiterung nutzen (Video kurz starten, dann erkannten Stream senden) oder *Bildschirm spiegeln*. |
| Spiegeln ohne Ton | Unter *Ton* „Systemton“ wählen; falls nicht verfügbar, ein Gerät wie „Stereomix“ auswählen (muss in den Windows-Soundeinstellungen aktiviert sein). |
| Ruckeln | Qualität auf 720p stellen, Laptop näher an den Router, oder Laptop per LAN-Kabel an den Router. |

## Technik

- `streamtv/dlna.py`: SSDP-Suche und UPnP-AVTransport/RenderingControl (ohne externe Bibliotheken)
- `streamtv/extractor.py`: Video-Adresse aus Websites mit [yt-dlp](https://github.com/yt-dlp/yt-dlp), bevorzugt H.264/AAC
- `streamtv/ffmpeg.py`: Umwandlung nach MPEG-TS (fügt getrennte Video-/Audiospuren zusammen, schickt Referer/Cookies mit, Audio → AAC, VP9/AV1 → H.264)
- `streamtv/server.py`: HTTP-Server für den TV (DLNA-Header, Range-Requests) und lokale Schnittstelle für Lesezeichen/Erweiterung (nur `127.0.0.1`, mit Token bzw. Erweiterungs-Origin)
- `streamtv/mirror.py`: Systemton per WASAPI-Loopback für das Spiegeln
- `streamtv/gui.py`: Oberfläche (tkinter)
- `extension/`: Browser-Erweiterung (Manifest V3)

Tests: `pip install -r requirements-dev.txt && python -m pytest`. Die Tests
enthalten einen simulierten DLNA-Fernseher, der den Stream wie ein echter TV abruft.

Die mitgelieferte `ffmpeg.exe` steht unter der GPL ([FFmpeg](https://ffmpeg.org)).
