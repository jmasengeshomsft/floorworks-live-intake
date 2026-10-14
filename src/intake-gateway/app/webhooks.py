"""HMAC-signed outbound webhooks."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import httpx

log = logging.getLogger("gateway.webhooks")


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


async def post_signed(url: str, payload: dict, secret: str, timeout_s: float = 5.0, event_id: str = "") -> httpx.Response:
    body = json.dumps(payload).encode()
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        return await c.post(url, content=body, headers=_headers(secret, body, event_id))


async def deliver_event(urls: list[str], secret: str, event: dict, retries: int = 3) -> None:
    """Best-effort delivery with exponential backoff; one task per URL."""
    import asyncio

    async def one(url: str) -> None:
        for attempt in range(retries):
            try:
                r = await post_signed(url, event, secret, event_id=f"{event['callId']}:{event['id']}")
                if r.status_code < 300:
                    return
                log.warning("webhook %s -> %s", url, r.status_code)
            except Exception as e:  # noqa: BLE001
                log.warning("webhook %s failed: %s", url, e)
            await asyncio.sleep(2**attempt)

    await asyncio.gather(*(one(u) for u in urls))
