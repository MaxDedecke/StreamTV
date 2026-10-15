from xml.etree import ElementTree as ET

import pytest

from streamtv import dlna
from tests.conftest import DESCRIPTION


def test_parse_description_finds_services():
    r = dlna.parse_description("http://192.168.0.50:1663/desc.xml", DESCRIPTION.encode())
    assert r.name == "[LG] webOS TV OLED55C3"
    assert r.manufacturer == "LG Electronics"
    assert r.avtransport_url == "http://192.168.0.50:1663/upnp/control/AVTransport1"
    assert r.rendering_url == "http://192.168.0.50:1663/upnp/control/RenderingControl1"
    assert r.host == "192.168.0.50"
    assert "192.168.0.50" in r.label


def test_parse_description_embedded_device_and_urlbase():
    xml = """<root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>http://10.0.0.9:9000/base/</URLBase>
      <device><friendlyName>Box</friendlyName><deviceList><device><friendlyName>Inner</friendlyName>
      <serviceList><service><serviceType>urn:schemas-upnp-org:service:AVTransport:2</serviceType>
      <controlURL>ctl/avt</controlURL></service></serviceList></device></deviceList></device></root>"""
    r = dlna.parse_description("http://10.0.0.9:1/x.xml", xml.encode())
    assert r.name == "Box"
    assert r.avtransport_url == "http://10.0.0.9:9000/base/ctl/avt"
    assert r.avtransport_type.endswith("AVTransport:2")
    assert r.rendering_url is None


def test_no_avtransport_returns_none():
    xml = '<root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>NAS</friendlyName></device></root>'
    assert dlna.parse_description("http://x/", xml.encode()) is None


def test_didl_is_valid_xml_and_escaped():
    didl = dlna.build_didl("http://1.2.3.4:8765/media/ab.ts?a=1&b=2", "Tom & Jerry <HD>", "video/mp2t", False)
    root = ET.fromstring(didl)
    ns = {"d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/", "dc": "http://purl.org/dc/elements/1.1/"}
    assert root.find("d:item/dc:title", ns).text == "Tom & Jerry <HD>"
    res = root.find("d:item/d:res", ns)
    assert res.text == "http://1.2.3.4:8765/media/ab.ts?a=1&b=2"
    assert res.get("protocolInfo").startswith("http-get:*:video/mp2t:DLNA.ORG_OP=00")


def test_time_helpers():
    assert dlna.parse_time("1:02:03") == 3723
    assert dlna.parse_time("00:00:05.123") == 5
    assert dlna.parse_time("NOT_IMPLEMENTED") == 0
    assert dlna.parse_time(None) == 0
    assert dlna.format_time(3723) == "1:02:03"
    assert dlna.format_time(-5) == "0:00:00"


def test_parse_ssdp_response():
    data = (b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLocation: http://192.168.1.20:1234/d.xml\r\n"
            b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n")
    h = dlna.parse_ssdp_response(data)
    assert h["location"] == "http://192.168.1.20:1234/d.xml"
    assert h["st"].endswith("MediaRenderer:1")


def test_soap_roundtrip_with_fake_tv(fake_tv):
    r = dlna.load_renderer(fake_tv.location)
    assert r is not None
    fake_tv.fetch_bytes = 0
    r.play_url("http://127.0.0.1:1/none.ts", "Test & Co", "video/mp2t", False)
    assert fake_tv.actions[:3] == ["Stop", "SetAVTransportURI", "Play"]  # Stop-Fehler wird ignoriert
    assert fake_tv.uri == "http://127.0.0.1:1/none.ts"
    assert "Test &amp; Co" in fake_tv.metadata
    assert r.transport_state() == "PLAYING"
    r.pause()
    assert r.transport_state() == "PAUSED_PLAYBACK"
    assert r.position() == (42, 600)
    r.set_volume(150)
    assert fake_tv.volume == 100
    assert r.get_volume() == 100


def test_upnp_error_is_reported(fake_tv):
    r = dlna.load_renderer(fake_tv.location)
    with pytest.raises(dlna.DLNAError, match="701"):
        r.stop()


def test_unreachable_tv_raises():
    r = dlna.Renderer("http://127.0.0.1:9/d.xml", "TV", "", "", "http://127.0.0.1:9/avt", "urn:x:service:AVTransport:1")
    with pytest.raises(dlna.DLNAError, match="nicht erreichbar"):
        r.play()
