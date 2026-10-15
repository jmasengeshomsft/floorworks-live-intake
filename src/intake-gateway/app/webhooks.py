"""HMAC-signed outbound webhooks."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger("gateway.webhooks")


def _blocked_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified


def url_problem(url: str, allow_insecure: bool = False) -> str:
    """Return why a webhook URL is unsafe (SSRF guard), or '' if acceptable."""
    u = urlparse(url)
    if u.scheme != "https" and not (allow_insecure and u.scheme == "http"):
        return "must use https"
    host = (u.hostname or "").lower()
    if not host:
        return "has no host"
    if allow_insecure:
        return ""
    if host == "localhost" or host.endswith((".local", ".internal")):
        return "points at a local host"
    try:
        if _blocked_ip(host):
            return "points at a private or reserved address"
    except ValueError:
        pass
    return ""


async def _resolves_public(url: str) -> bool:
    import asyncio

    host = urlparse(url).hostname or ""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        return False
    return all(not _blocked_ip(i[4][0]) for i in infos)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(secret: str, timestamp: str, body: bytes, signature: str, tolerance_s: int = 300) -> bool:
    if abs(time.time() - int(timestamp)) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def _headers(secret: str, body: bytes, event_id: str = "") -> dict[str, str]:
    ts = str(int(time.time()))
    h = {"content-type": "application/json", "x-gateway-timestamp": ts}
    if event_id:
        h["x-gateway-event-id"] = event_id
    if secret:
        h["x-gateway-signature"] = sign(secret, ts, body)
    return h


async def post_signed(url: str, payload: dict, secret: str, timeout_s: float = 5.0, event_id: str = "", allow_insecure: bool = False) -> httpx.Response:
    problem = url_problem(url, allow_insecure)
    if problem:
        raise ValueError(f"webhook URL {problem}")
    if not allow_insecure and not await _resolves_public(url):
        raise ValueError("webhook host does not resolve to a public address")
    body = json.dumps(payload).encode()
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as c:
        return await c.post(url, content=body, headers=_headers(secret, body, event_id))


async def deliver_event(urls: list[str], secret: str, event: dict, retries: int = 3, allow_insecure: bool = False) -> None:
    """Best-effort delivery with exponential backoff; one task per URL."""
    import asyncio

    async def one(url: str) -> None:
        for attempt in range(retries):
            try:
                r = await post_signed(url, event, secret, event_id=f"{event['callId']}:{event['id']}", allow_insecure=allow_insecure)
                if r.status_code < 300:
                    return
                log.warning("webhook %s -> %s", url, r.status_code)
            except Exception as e:  # noqa: BLE001
                log.warning("webhook %s failed: %s", url, e)
            await asyncio.sleep(2**attempt)

    await asyncio.gather(*(one(u) for u in urls))
