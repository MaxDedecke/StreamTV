"""DLNA/UPnP: Fernseher finden (SSDP) und steuern (AVTransport, RenderingControl).

LG webOS-Fernseher (wie auch Samsung, Sony, Philips, Panasonic ...) melden sich
als ``urn:schemas-upnp-org:device:MediaRenderer:1``. Über den AVTransport-Dienst
bekommt der TV eine HTTP-URL, die er dann selbst abruft und abspielt.
"""

from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from streamtv.netutil import local_ipv4_addresses

SSDP_ADDR = ("239.255.255.250", 1900)
ST_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
ST_AVTRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"

NS_DEVICE = "{urn:schemas-upnp-org:device-1-0}"

# DLNA-Flags: Streaming-Transfer, Hintergrund-Transfer, DLNA 1.5
DLNA_FLAGS = "01700000000000000000000000000000"


class DLNAError(Exception):
    pass


def protocol_info(mime: str, seekable: bool) -> str:
    """protocolInfo-String für DIDL-Lite und den ``contentFeatures.dlna.org``-Header."""
    op = "01" if seekable else "00"  # 01 = Byte-Range-Seek möglich
    return f"http-get:*:{mime}:DLNA.ORG_OP={op};DLNA.ORG_CI=0;DLNA.ORG_FLAGS={DLNA_FLAGS}"


def content_features(mime: str, seekable: bool) -> str:
    return protocol_info(mime, seekable).split(":", 3)[3]


def build_didl(url: str, title: str, mime: str, seekable: bool, upnp_class: str = "object.item.videoItem") -> str:
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
        '<item id="streamtv-0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(title or 'StreamTV')}</dc:title>"
        f"<upnp:class>{upnp_class}</upnp:class>"
        f'<res protocolInfo="{escape(protocol_info(mime, seekable))}">{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


def parse_ssdp_response(data: bytes) -> dict[str, str]:
    """Wandelt eine SSDP-Antwort in ein Dict mit kleingeschriebenen Header-Namen."""
    headers: dict[str, str] = {}
    lines = data.decode("utf-8", "replace").split("\r\n")
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def _msearch(st: str, mx: int) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {st}\r\n"
        "USER-AGENT: Windows/10 UPnP/1.1 StreamTV/0.1\r\n\r\n"
    ).encode()


def ssdp_search(timeout: float = 3.0, unicast_host: str | None = None) -> list[str]:
    """Sucht MediaRenderer und gibt die LOCATION-URLs ihrer Gerätebeschreibungen zurück.

    Ohne ``unicast_host`` wird per Multicast auf jeder Netzwerkkarte gesucht,
    sonst gezielt an eine bekannte TV-IP (hilft, wenn Multicast blockiert ist).
    """
    sockets: list[socket.socket] = []
    bind_ips = local_ipv4_addresses() or ["0.0.0.0"]
    for ip in bind_ips:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.setblocking(False)
            sockets.append(s)
        except OSError:
            s.close()
        if unicast_host:
            break
    target = (unicast_host, 1900) if unicast_host else SSDP_ADDR
    for s in sockets:
        for st in (ST_RENDERER, ST_AVTRANSPORT):
            for _ in range(2):  # UDP kann verloren gehen
                try:
                    s.sendto(_msearch(st, 2), target)
                except OSError:
                    pass

    locations: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            got = False
            for s in sockets:
                try:
                    data, _ = s.recvfrom(65507)
                except (BlockingIOError, OSError):
                    continue
                got = True
                loc = parse_ssdp_response(data).get("location")
                if loc and loc not in locations:
                    locations.append(loc)
            if not got:
                time.sleep(0.05)
    finally:
        for s in sockets:
            s.close()
    return locations


@dataclass
class Renderer:
    location: str
    name: str
    manufacturer: str
    model: str
    avtransport_url: str
    avtransport_type: str
    rendering_url: str | None = None
    rendering_type: str | None = None

    @property
    def host(self) -> str:
        return urlparse(self.location).hostname or ""

    @property
    def label(self) -> str:
        extra = self.model if self.model and self.model not in self.name else ""
        return f"{self.name} ({extra}, {self.host})" if extra else f"{self.name} ({self.host})"

    # ---------------------------------------------------------------- SOAP
    def _soap(self, service: str, action: str, args: list[tuple[str, str]], timeout: float = 8.0) -> ET.Element:
        if service == "avt":
            url, stype = self.avtransport_url, self.avtransport_type
        else:
            if not self.rendering_url:
                raise DLNAError("Der Fernseher unterstützt keine Lautstärkeregelung per DLNA.")
            url, stype = self.rendering_url, self.rendering_type
        arg_xml = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{stype}">{arg_xml}</u:{action}></s:Body></s:Envelope>'
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{stype}#{action}"',
                "User-Agent": "StreamTV/0.1 UPnP/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return ET.fromstring(resp.read())
        except urllib.error.HTTPError as e:
            raise DLNAError(f"{action} abgelehnt: {_upnp_error(e.read())}") from None
        except (urllib.error.URLError, OSError) as e:
            raise DLNAError(f"Fernseher nicht erreichbar ({action}): {e}") from None

    # ------------------------------------------------------------ Aktionen
    def play_url(self, url: str, title: str, mime: str, seekable: bool) -> None:
        try:
            self.stop()
        except DLNAError:
            pass  # war nichts aktiv
        didl = build_didl(url, title, mime, seekable)
        self._soap("avt", "SetAVTransportURI", [("InstanceID", "0"), ("CurrentURI", url), ("CurrentURIMetaData", didl)])
        # Manche Geräte brauchen einen Moment, bevor Play akzeptiert wird.
        for attempt in range(3):
            try:
                self.play()
                return
            except DLNAError:
                if attempt == 2:
                    raise
                time.sleep(0.7)

    def play(self) -> None:
        self._soap("avt", "Play", [("InstanceID", "0"), ("Speed", "1")])

    def pause(self) -> None:
        self._soap("avt", "Pause", [("InstanceID", "0")])

    def stop(self) -> None:
        self._soap("avt", "Stop", [("InstanceID", "0")], timeout=4)

    def seek(self, seconds: int) -> None:
        self._soap("avt", "Seek", [("InstanceID", "0"), ("Unit", "REL_TIME"), ("Target", format_time(seconds))])

    def transport_state(self) -> str:
        root = self._soap("avt", "GetTransportInfo", [("InstanceID", "0")], timeout=4)
        return _find_text(root, "CurrentTransportState") or "UNKNOWN"

    def position(self) -> tuple[int, int]:
        """(Position, Dauer) in Sekunden; 0 wenn unbekannt."""
        root = self._soap("avt", "GetPositionInfo", [("InstanceID", "0")], timeout=4)
        return parse_time(_find_text(root, "RelTime")), parse_time(_find_text(root, "TrackDuration"))

    def get_volume(self) -> int:
        root = self._soap("rc", "GetVolume", [("InstanceID", "0"), ("Channel", "Master")], timeout=4)
        return int(_find_text(root, "CurrentVolume") or 0)

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        self._soap("rc", "SetVolume", [("InstanceID", "0"), ("Channel", "Master"), ("DesiredVolume", str(volume))])


def _find_text(root: ET.Element, tag: str) -> str | None:
    for el in root.iter():
        if el.tag == tag or el.tag.endswith("}" + tag):
            return (el.text or "").strip()
    return None


def _upnp_error(body: bytes) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return "unbekannter Fehler"
    code = _find_text(root, "errorCode")
    desc = _find_text(root, "errorDescription")
    return f"UPnP-Fehler {code}: {desc}" if desc else f"UPnP-Fehler {code}"


def parse_time(value: str | None) -> int:
    """'01:02:03' oder '0:02:03.000' -> Sekunden. 'NOT_IMPLEMENTED' -> 0."""
    if not value:
        return 0
    m = re.match(r"^(\d+):(\d{1,2}):(\d{1,2})", value)
    if not m:
        return 0
    h, mi, s = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s


def format_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def parse_description(location: str, xml_bytes: bytes) -> Renderer | None:
    """Liest die UPnP-Gerätebeschreibung und sucht AVTransport/RenderingControl."""
    root = ET.fromstring(xml_bytes)
    base = location
    url_base = root.find(f"{NS_DEVICE}URLBase")
    if url_base is not None and (url_base.text or "").strip():
        base = url_base.text.strip()

    devices = list(root.iter(f"{NS_DEVICE}device"))
    if not devices:
        return None
    top = devices[0]

    def text(dev: ET.Element, tag: str) -> str:
        el = dev.find(f"{NS_DEVICE}{tag}")
        return (el.text or "").strip() if el is not None else ""

    avt = rc = None
    for dev in devices:  # inkl. eingebetteter Geräte
        for svc in dev.iter(f"{NS_DEVICE}service"):
            stype = text(svc, "serviceType")
            ctrl = urljoin(base, text(svc, "controlURL"))
            if ":service:AVTransport:" in stype and avt is None:
                avt = (ctrl, stype)
            elif ":service:RenderingControl:" in stype and rc is None:
                rc = (ctrl, stype)
    if avt is None:
        return None
    return Renderer(
        location=location,
        name=text(top, "friendlyName") or "Fernseher",
        manufacturer=text(top, "manufacturer"),
        model=text(top, "modelName"),
        avtransport_url=avt[0],
        avtransport_type=avt[1],
        rendering_url=rc[0] if rc else None,
        rendering_type=rc[1] if rc else None,
    )


def load_renderer(location: str, timeout: float = 4.0) -> Renderer | None:
    try:
        req = urllib.request.Request(location, headers={"User-Agent": "StreamTV/0.1 UPnP/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_description(location, resp.read())
    except (OSError, ET.ParseError, ValueError):
        return None


def discover(timeout: float = 3.0, extra_hosts: list[str] | None = None) -> list[Renderer]:
    locations = ssdp_search(timeout)
    for host in extra_hosts or []:
        for loc in ssdp_search(min(timeout, 2.0), unicast_host=host):
            if loc not in locations:
                locations.append(loc)
    renderers: list[Renderer] = []
    seen: set[str] = set()
    for loc in locations:
        r = load_renderer(loc)
        if r and r.avtransport_url not in seen:
            seen.add(r.avtransport_url)
            renderers.append(r)
    return renderers
