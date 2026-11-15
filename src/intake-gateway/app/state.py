"""Call state, store interface and per-call event hub (SSE + webhooks fan-out)."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from collections.abc import Coroutine
from typing import Any, Awaitable, Callable, Protocol

from .tasks import TaskSupervisor


class TranscriptLimit(RuntimeError):
    pass


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
    checkpoint_version: int = 0
    published_version: int = 0
    persistence_error: bool = False
    extracted_version: int = 0
    extraction_status: str = "pending"
    extraction_error: str | None = None
    extraction_result: dict | None = None
    extraction_target: int = 0
    extraction_mode: str = "extract"
    extraction_attempts: int = 0
    final_requested: bool = False
    final_extraction: bool = False
    _transcript_bytes: int = field(default=0, init=False, repr=False)

    def __post_init__(self):
        self._transcript_bytes = sum(len(line["text"].encode("utf-8")) for line in self.transcript)

    def checkpoint(self) -> dict[str, Any]:
        keys = ("call_id", "caller_phone", "status", "started_at", "ended_at", "after_hours",
                "rep_joined", "transcript", "fields", "enrichment", "version", "marks", "processed",
                "extracted_version", "extraction_status", "extraction_error", "extraction_result",
                "extraction_target", "extraction_mode", "extraction_attempts", "final_requested", "final_extraction")
        return {"format": 1, **{k: copy.deepcopy(getattr(self, k)) for k in keys}}

    @classmethod
    def from_checkpoint(cls, data: dict[str, Any]) -> "CallState":
        if data.get("format") != 1:
            raise ValueError("unsupported call checkpoint format")
        values = {k: v for k, v in data.items() if k != "format"}
        st = cls(**values)
        if not st.call_id or st.version != len(st.transcript):
            raise ValueError("invalid call checkpoint identity or transcript version")
        st.checkpoint_version = st.version
        return st

    def mark(self, name: str) -> bool:
        """Record the first time a call milestone happens; returns True only on that first time."""
        if name in self.marks:
            return False
        self.marks[name] = time.time()
        return True

    def add_line(self, speaker: str, text: str) -> dict[str, Any]:
        size = len(text.encode("utf-8"))
        if self._transcript_bytes + size > 256000 or len(self.transcript) >= 2000:
            raise TranscriptLimit("transcript capacity reached")
        line = {"speaker": speaker, "text": text, "ts": time.time()}
        self.transcript.append(line)
        self._transcript_bytes += size
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
            "checkpointVersion": self.checkpoint_version,
            "persistenceError": self.persistence_error,
            "extractionStatus": self.extraction_status,
            "extractionError": self.extraction_error,
            "extractedVersion": self.extracted_version,
            "persistedExtractionVersion": self.processed,
            "extractionFinal": self.final_extraction,
        }


class CallStore(Protocol):
    def get(self, call_id: str) -> CallState | None: ...
    def get_or_create(self, call_id: str) -> CallState: ...
    def put(self, st: CallState) -> None: ...
    def list(self) -> list[CallState]: ...


class MemoryStore:
    def __init__(self, max_calls: int = 500, retention_s: float = 3600, on_evict: Callable[[str], None] | None = None):
        self._calls: dict[str, CallState] = {}
        self._max = max_calls
        self._retention_s = retention_s
        self._on_evict = on_evict

    def _prune(self) -> None:
        ended = sorted((st for st in self._calls.values()
                        if st.status == "ended" and not st.running and not st.pending_functions),
                       key=lambda st: st.ended_at or st.started_at)
        now = time.time()
        for st in ended:
            if len(self._calls) <= self._max and now - (st.ended_at or st.started_at) < self._retention_s:
                continue
            del self._calls[st.call_id]
            if self._on_evict is not None:
                self._on_evict(st.call_id)

    def get(self, call_id: str) -> CallState | None:
        self._prune()
        return self._calls.get(call_id)

    def get_or_create(self, call_id: str) -> CallState:
        st = self._calls.setdefault(call_id, CallState(call_id))
        self._prune()
        return st

    def put(self, st: CallState) -> None:
        self._calls[st.call_id] = st
        self._prune()

    def list(self) -> list[CallState]:
        self._prune()
        return sorted(self._calls.values(), key=lambda c: c.started_at, reverse=True)


class EventHub:
    """Per-call pub/sub with a replay buffer so late SSE subscribers catch up."""

    def __init__(self, replay: int = 200, tasks: TaskSupervisor | None = None, queue_size: int = 100,
                 delivery_concurrency: int = 32, delivery_capacity: int = 1024, max_calls: int = 500):
        if min(replay, queue_size, delivery_concurrency, delivery_capacity, max_calls) < 1:
            raise ValueError("event capacities must be positive")
        self.tasks = tasks or TaskSupervisor()
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._buf: dict[str, list[dict]] = {}
        self._seq: dict[str, int] = {}
        self._replay = replay
        self.listeners: list[Callable[[str, dict], Coroutine[Any, Any, None]]] = []
        self._queue_size = queue_size
        self._deliveries: deque = deque()
        self._workers: set[asyncio.Task] = set()
        self._delivery_concurrency = delivery_concurrency
        self._delivery_capacity = delivery_capacity
        self._max_calls = max_calls
        self.dropped_deliveries = 0

    def _resync(self, call_id: str, q: asyncio.Queue) -> None:
        while not q.empty():
            q.get_nowait()
        q.put_nowait({"id": self._seq.get(call_id, 0), "callId": call_id, "type": "resync.required",
                     "ts": time.time(), "data": {"reason": "event history unavailable; reload call snapshot"}})

    def forget(self, call_id: str) -> None:
        self._buf.pop(call_id, None)
        self._seq.pop(call_id, None)
        for q in self._subs.pop(call_id, set()):
            self._resync(call_id, q)

    async def _deliver(self) -> None:
        while self._deliveries:
            fn, call_id, event = self._deliveries.popleft()
            try:
                await fn(call_id, event)
            except asyncio.CancelledError:
                self.dropped_deliveries += 1
                raise
            except Exception as error:
                self.dropped_deliveries += 1
                logging.getLogger("gateway.events").warning("event delivery failed: %s", type(error).__name__)

    def discard_deliveries(self) -> None:
        if self._deliveries:
            self.dropped_deliveries += len(self._deliveries)
            logging.getLogger("gateway.events").warning("undelivered events discarded at shutdown: %d", len(self._deliveries))
            self._deliveries.clear()

    def publish(self, call_id: str, type_: str, data: Any) -> dict:
        seq = self._seq.pop(call_id, 0) + 1
        if len(self._seq) >= self._max_calls:
            self.forget(next(iter(self._seq)))
        self._seq[call_id] = seq
        ev = {"id": seq, "type": type_, "callId": call_id, "ts": time.time(), "data": data}
        buf = self._buf.setdefault(call_id, [])
        buf.append(ev)
        del buf[: -self._replay]
        for q in list(self._subs.get(call_id, ())):
            if q.full():
                self._resync(call_id, q)
                self.unsubscribe(call_id, q)
            else:
                q.put_nowait(ev)
        for fn in self.listeners:
            if self.tasks.closing:
                logging.getLogger("gateway.events").warning("event delivery skipped during shutdown: %s", type_)
            else:
                if len(self._deliveries) >= self._delivery_capacity:
                    self.dropped_deliveries += 1
                    logging.getLogger("gateway.events").warning("event delivery queue full: %s", type_)
                    continue
                self._deliveries.append((fn, call_id, ev))
                self._workers.difference_update({worker for worker in self._workers if worker.done()})
                if len(self._workers) < self._delivery_concurrency:
                    worker = self.tasks.spawn(self._deliver(), name="event-delivery")
                    self._workers.add(worker)
                    worker.add_done_callback(self._workers.discard)
        return ev

    def subscribe(self, call_id: str, last_id: int = 0) -> asyncio.Queue:
        if len(self._subs.get(call_id, ())) >= 32 or sum(len(subs) for subs in self._subs.values()) >= 512:
            raise RuntimeError("event subscriber capacity reached")
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        events = self._buf.get(call_id, [])
        replay = [ev for ev in events if ev["id"] > last_id]
        if (len(replay) > self._queue_size or last_id > self._seq.get(call_id, 0)
                or (last_id and events and last_id < events[0]["id"] - 1)):
            self._resync(call_id, q)
            return q
        for ev in replay:
            q.put_nowait(ev)
        self._subs.setdefault(call_id, set()).add(q)
        return q

    def unsubscribe(self, call_id: str, q: asyncio.Queue) -> None:
        self._subs.get(call_id, set()).discard(q)
        if not self._subs.get(call_id):
            self._subs.pop(call_id, None)


def sse_frame(ev: dict) -> str:
    return f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
