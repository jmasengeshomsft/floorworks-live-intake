"""Per-call tokens and shared-secret checks for the infra endpoints (/api/incoming, /api/callbacks, /ws).

Outside LOCAL_DEV the secrets and JWT enforcement are mandatory (callers pass require=True).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

log = logging.getLogger("gateway.security")


def new_call_id() -> str:
    return secrets.token_urlsafe(16)


def call_token(secret: str, call_id: str) -> str:
    return hmac.new(secret.encode(), f"call:{call_id}".encode(), hashlib.sha256).hexdigest()[:32]


def safe_equal(a: str, b: str) -> bool:
    """Constant-time compare on UTF-8 bytes; str inputs with non-ASCII chars would make compare_digest raise."""
    return hmac.compare_digest(a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass"))


def check_call_token(secret: str, call_id: str, token: str | None, require: bool = False) -> bool:
    """With no secret configured: allowed only when `require` is False (LOCAL_DEV). Otherwise the token must match."""
    if not secret:
        return not require
    return bool(token) and safe_equal(call_token(secret, call_id), token or "")


def check_shared_secret(secret: str, *candidates: str | None, require: bool = False) -> bool:
    """With no secret configured: allowed only when `require` is False (LOCAL_DEV). Otherwise any candidate must match."""
    if not secret:
        return not require
    return any(c and safe_equal(secret, c) for c in candidates)


def normalize_number(n: str) -> str:
    n = (n or "").strip()
    if ":" in n and not n.startswith("+"):
        return n.lower()  # ACS identifier (e.g. 28:orgid:<guid>), not a phone number
    return "".join(ch for ch in n if ch.isdigit())


ACS_ISSUER = "https://acscallautomation.communication.azure.com"
ACS_JWKS_URL = ACS_ISSUER + "/calling/keys"


class AcsJwtVerifier:
    """Validates the JWT Call Automation puts in the Authorization header of callbacks and media WebSocket upgrades.

    Modes: off (skip), warn (log failures, accept), enforce (reject failures).
    """

    def __init__(self, mode: str, resource_id: str, jwks_client=None):
        self.mode = mode if mode in ("warn", "enforce") else "off"
        self.resource_id = resource_id
        self._jwks = jwks_client

    def _client(self):
        if self._jwks is None:
            import jwt

            self._jwks = jwt.PyJWKClient(ACS_JWKS_URL, cache_keys=True, lifespan=3600)
        return self._jwks

    def _check(self, authorization: str | None) -> str:
        """Return '' when valid, else a short reason (never includes the token)."""
        import jwt

        parts = (authorization or "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return "missing bearer token"
        try:
            key = self._client().get_signing_key_from_jwt(parts[1]).key
            jwt.decode(parts[1], key, algorithms=["RS256"], audience=self.resource_id, issuer=ACS_ISSUER,
                       options={"require": ["exp", "iss", "aud"]})
        except Exception as e:  # noqa: BLE001
            return type(e).__name__
        return ""

    async def verify(self, authorization: str | None, what: str) -> bool:
        if self.mode == "off":
            return True
        import asyncio

        reason = await asyncio.to_thread(self._check, authorization)
        if not reason:
            return True
        log.warning("ACS JWT invalid on %s (%s)%s", what, reason, "" if self.mode == "enforce" else " - warn mode, accepted")
        return self.mode == "warn"
