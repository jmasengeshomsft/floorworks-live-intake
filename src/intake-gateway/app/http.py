"""Bounded HTTP retries for explicitly retry-safe operations."""
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from azure.core.exceptions import HttpResponseError, ServiceRequestError, ServiceResponseError


def retryable_status(status: int) -> bool:
    return status == 429 or status in (500, 502, 503, 504)


def retryable_error(error: Exception) -> bool:
    if isinstance(error, (httpx.TransportError, ServiceRequestError, ServiceResponseError, TimeoutError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return retryable_status(error.response.status_code)
    if isinstance(error, HttpResponseError) and error.status_code is not None:
        return retryable_status(error.status_code)
    return False


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(0, seconds)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                return max(0, (date - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return random.uniform(0, min(2**attempt, 10))


async def request(client: httpx.AsyncClient, method: str, url: str, *, retry_safe: bool = False,
                  attempts: int = 3, timeout_s: float = 60, **kwargs) -> httpx.Response:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    try:
        async with asyncio.timeout(timeout_s):
            for attempt in range(attempts if retry_safe else 1):
                response = None
                try:
                    response = await client.request(method, url, timeout=httpx.Timeout(timeout_s, connect=min(10, timeout_s)), **kwargs)
                    if not retry_safe or not retryable_status(response.status_code) or attempt == attempts - 1:
                        return response
                except httpx.TransportError:
                    if not retry_safe or attempt == attempts - 1:
                        raise
                await asyncio.sleep(retry_delay(response, attempt))
    except asyncio.TimeoutError as error:
        raise httpx.TimeoutException("outbound request deadline exceeded") from error
    raise RuntimeError("outbound request attempts exhausted")
