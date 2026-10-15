"""Call state, store interface and per-call event hub (SSE + webhooks fan-out)."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class CallState:
    call_id: str
    caller_phone: str = ""
    conn_id: str = ""
    status: str = "ringing"  # ringing | active | ended
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    after_hours: bool = False
    muted: bool = False
    rep_joined: bool = False
    transfer_started: bool = False
    ringing: bool = False
    active_ws: int = 0
    finalized: bool = False
    connections: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    enrichment: dict[str, Any] | None = None
    version: int = 0
    processed: int = 0
    running: bool = False
    last_enrich: float = 0.0
    pending_functions: dict[str, asyncio.Future] = field(default_factory=dict)
    response_done: asyncio.Event = field(default_factory=asyncio.Event)
    ws_send: Callable[[str], Awaitable[None]] | None = None
    vl_send: Callable[[dict], Awaitable[None]] | None = None
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> bool:
        """Record the first time a call milestone happens; returns True only on that first time."""
        if name in self.marks:
            return False
        self.marks[name] = time.time()
        return True

    def add_line(self, speaker: str, text: str) -> dict[str, Any]:
        line = {"speaker": speaker, "text": text, "ts": time.time()}
        self.transcript.append(line)
        self.version += 1
        return line

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "callerPhone": self.caller_phone,
            "status": self.status,
            "afterHours": self.after_hours,
            "repJoined": self.rep_joined,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
        }


class CallStore(Protocol):
    def get(self, call_id: str) -> CallState | None: ...
    def get_or_create(self, call_id: str) -> CallState: ...
    def put(self, st: CallState) -> None: ...
    def list(self) -> list[CallState]: ...


class MemoryStore:
    def __init__(self, max_calls: int = 500):
        self._calls: dict[str, CallState] = {}
        self._max = max_calls

    def get(self, call_id: str) -> CallState | None:
        return self._calls.get(call_id)

    def get_or_create(self, call_id: str) -> CallState:
        return self._calls.setdefault(call_id, CallState(call_id))

    def put(self, st: CallState) -> None:
        self._calls[st.call_id] = st
        if len(self._calls) > self._max:
            for k in [k for k, v in self._calls.items() if v.status == "ended"][: len(self._calls) - self._max]:
                del self._calls[k]

    def list(self) -> list[CallState]:
        return sorted(self._calls.values(), key=lambda c: c.started_at, reverse=True)


class EventHub:
    """Per-call pub/sub with a replay buffer so late SSE subscribers catch up."""

    def __init__(self, replay: int = 200):
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._buf: dict[str, list[dict]] = {}
        self._seq: dict[str, int] = {}
        self._replay = replay
        self.listeners: list[Callable[[str, dict], Awaitable[None]]] = []

    def publish(self, call_id: str, type_: str, data: Any) -> dict:
        seq = self._seq.get(call_id, 0) + 1
        self._seq[call_id] = seq
        ev = {"id": seq, "type": type_, "callId": call_id, "ts": time.time(), "data": data}
        buf = self._buf.setdefault(call_id, [])
        buf.append(ev)
        del buf[: -self._replay]
        for q in list(self._subs.get(call_id, ())):
            q.put_nowait(ev)
        for fn in self.listeners:
            asyncio.get_running_loop().create_task(fn(call_id, ev))
        return ev

    def subscribe(self, call_id: str, last_id: int = 0) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for ev in self._buf.get(call_id, []):
            if ev["id"] > last_id:
                q.put_nowait(ev)
        self._subs.setdefault(call_id, set()).add(q)
        return q

    def unsubscribe(self, call_id: str, q: asyncio.Queue) -> None:
        self._subs.get(call_id, set()).discard(q)


def sse_frame(ev: dict) -> str:
    return f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
