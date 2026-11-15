"""API-key auth for /v1. Keys via X-Api-Key header, Authorization: Bearer, or ?key= (for SSE in browsers)."""
from __future__ import annotations

from fastapi import HTTPException, Request

from .config import Settings
from .security import safe_equal


def make_dependency(settings: Settings):
    async def require_key(request: Request) -> None:
        if not settings.api_keys:
            return
        supplied = (
            request.headers.get("x-api-key")
            or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            or request.query_params.get("key", "")
        )
        if not any(safe_equal(supplied, k) for k in settings.api_keys):
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    return require_key
