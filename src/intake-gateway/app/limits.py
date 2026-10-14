"""Call guardrails: concurrency cap, max duration and idle (zombie) detection."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("gateway.limits")


@dataclass
class _Entry:
    started: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)


class CallLimits:
    def __init__(self, max_concurrent: int = 50, max_duration_s: int = 3600, idle_timeout_s: int = 120):
        self.max_concurrent = max_concurrent
        self.max_duration_s = max_duration_s
        self.idle_timeout_s = idle_timeout_s
        self._calls: dict[str, _Entry] = {}

    @property
    def active(self) -> int:
        return len(self._calls)

    @property
    def receive_timeout(self) -> float:
        """Socket read timeout, so an idle call is noticed within one idle period."""
        return float(min(self.idle_timeout_s, 30)) if self.idle_timeout_s > 0 else 30.0

    def at_capacity(self) -> bool:
        return self.max_concurrent > 0 and len(self._calls) >= self.max_concurrent

    def acquire(self, call_id: str) -> bool:
        """Register a call (idempotent for reconnects). False when the gateway is full."""
        if call_id in self._calls:
            return True
        if self.at_capacity():
            log.warning("call rejected, at capacity %d/%d call_id=%s", len(self._calls), self.max_concurrent, call_id)
            return False
        self._calls[call_id] = _Entry()
        log.info("call registered call_id=%s active=%d/%d", call_id, len(self._calls), self.max_concurrent)
        return True

    def release(self, call_id: str) -> None:
        e = self._calls.pop(call_id, None)
        if e:
            log.info("call released call_id=%s duration=%.1fs active=%d", call_id, time.monotonic() - e.started, len(self._calls))

    def touch(self, call_id: str) -> None:
        e = self._calls.get(call_id)
        if e:
            e.last_activity = time.monotonic()

    def expired(self, call_id: str) -> str | None:
        """Return 'max_duration' or 'idle' when the call should be dropped, else None."""
        e = self._calls.get(call_id)
        if not e:
            return None
        now = time.monotonic()
        if self.max_duration_s > 0 and now - e.started > self.max_duration_s:
            return "max_duration"
        if self.idle_timeout_s > 0 and now - e.last_activity > self.idle_timeout_s:
            return "idle"
        return None

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            "active": len(self._calls),
            "maxConcurrent": self.max_concurrent,
            "maxDurationSeconds": self.max_duration_s,
            "idleTimeoutSeconds": self.idle_timeout_s,
            "calls": [
                {"callId": k, "durationSeconds": round(now - v.started, 1), "idleSeconds": round(now - v.last_activity, 1)}
                for k, v in self._calls.items()
            ],
        }
