import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.config import settings


BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
RFC2544_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    RFC2544_PROXY_NETWORK,
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]


def assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only http and https URLs are supported")
    if not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL host is required")
    host = parsed.hostname.lower()
    if host in BLOCKED_HOSTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Localhost URLs are not allowed")
    host_is_ip_literal = is_ip_literal(host)
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL host cannot be resolved") from exc
    for address in addresses:
        if is_blocked_address(address, host_is_ip_literal):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private or reserved network URLs are not allowed")


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_blocked_address(address: str, host_is_ip_literal: bool = False) -> bool:
    ip = ipaddress.ip_address(address)
    if settings.allow_rfc2544_proxy_network and not host_is_ip_literal and ip in RFC2544_PROXY_NETWORK:
        return False
    return any(ip in network for network in BLOCKED_NETWORKS)
