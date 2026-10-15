"""Hilfsfunktionen rund um Netzwerkadressen."""

import socket


def local_ip_for(remote_host: str = "8.8.8.8") -> str:
    """Gibt die lokale IP zurück, über die ``remote_host`` erreicht wird.

    Es wird kein Paket verschickt; ``connect`` auf einem UDP-Socket wählt nur
    die passende Route bzw. Netzwerkkarte aus (wichtig bei VPN/Hyper-V).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_ipv4_addresses() -> list[str]:
    """Alle lokalen IPv4-Adressen (ohne Loopback), Standardroute zuerst."""
    addrs: list[str] = []
    default = local_ip_for()
    if default != "127.0.0.1":
        addrs.append(default)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    return addrs
