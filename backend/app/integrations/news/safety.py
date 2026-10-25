"""Only the public internet for news fetching (ADR-036): sources added by an admin must not turn the collector
into a way into the internal network (minio, api, the cloud metadata service) — SSRF.

Checked before every request, redirects and robots.txt included: http(s) only, ports 80/443, no credentials in
the URL, and every address the host resolves to is a global one.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

ALLOWED_SCHEMES = {"http": 80, "https": 443}


class BlockedHostError(httpx.TransportError):
    """The URL leads outside the public internet."""


def check_url_shape(url: str) -> str:
    """Normalized URL or ValueError: scheme, host, port and no user:password@."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ALLOWED_SCHEMES:
        raise ValueError("only http:// and https:// URLs")
    if not parts.hostname:
        raise ValueError("URL without a host")
    if parts.username or parts.password:
        raise ValueError("credentials in the URL are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid port") from exc
    if port is not None and port not in ALLOWED_SCHEMES.values():
        raise ValueError("only the standard ports 80 and 443")
    return parts.geturl()


async def ensure_public_host(host: str) -> None:
    """BlockedHostError unless every address of the host is a global (public) one."""
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise BlockedHostError(f"cannot resolve {host}: {exc}") from exc
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    private = sorted(str(a) for a in addresses if not a.is_global)
    if not addresses or private:
        raise BlockedHostError(f"{host} resolves to a non-public address ({', '.join(private) or 'none'})")


async def guard_request(request: httpx.Request) -> None:
    """httpx request hook: runs for the first request and for every redirect."""
    if request.url.scheme not in ALLOWED_SCHEMES or (
        request.url.port is not None and request.url.port not in ALLOWED_SCHEMES.values()
    ):
        raise BlockedHostError(f"{request.url.scheme}://…:{request.url.port} is not allowed", request=request)
    await ensure_public_host(request.url.host)
