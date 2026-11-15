"""Bounded, process-local function execution; deferred work is never replayed on restart.

Live-call IDs are retained until call end, not evicted to make room for new work.
Admission fails closed at capacity. POST response loss is uncertain, not success,
and is never retried. Call finalization must call cancel_call before closing voice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from .config import FunctionDef, Settings
from .state import CallState
from .tasks import TaskSupervisor
from .webhooks import post_signed

log = logging.getLogger("gateway.functions")
Handler = Callable[[CallState, dict], Awaitable[dict]]
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,128}", re.ASCII)
MAX_JSON_BYTES = 262144
MAX_JSON_DEPTH = 32
MAX_INVOCATIONS = 4096
MAX_CALL_INVOCATIONS = 256
MAX_BACKGROUND_TASKS = 128
PENDING_TTL_S = 120.0


def _object(value: Any) -> dict:
    """Validate JSON without silently coercing keys, NaN, tuples, or custom types."""
    nodes = 0

    def check(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_BYTES:
            raise ValueError("JSON structure exceeds limits")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("value is not JSON")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("JSON numbers must be finite")

    if type(value) is not dict:
        raise ValueError("function arguments and results must be JSON objects")
    check(value, 0)
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("JSON object exceeds 256 KiB")
    return json.loads(encoded)


def _error(code: str, message: str, **extra: Any) -> dict:
    return {"status": "error", "code": code, "error": message, "retryable": False, **extra}


@dataclass
class _Invocation:
    st: CallState
    name: str
    call_id: str
    arguments: dict
    future: asyncio.Future
    result: dict | None = None
    timer: asyncio.TimerHandle | None = None
    runner: asyncio.Task | None = None
    delivery: asyncio.Task | None = None
    on_late: Callable[[dict], Awaitable[None]] | None = None
    deferred: bool = False


class FunctionRegistry:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None, tasks: TaskSupervisor | None = None):
        self.settings = settings
        self.builtins: dict[str, Handler] = {}
        self.consumer: dict[str, FunctionDef] = {f.name: f for f in settings.functions}
        self.publish: Callable[[str, str, Any], Any] = lambda *_: None
        self._http = client if client is not None else httpx.AsyncClient(follow_redirects=False)
        self._owns_http = client is None
        self.tasks = tasks if tasks is not None else TaskSupervisor()
        self._owns_tasks = tasks is None
        self._closed = False
        self._invocations: dict[tuple[str, str], _Invocation] = {}
        self._background: set[asyncio.Task] = set()

    def _ended(self, st: CallState) -> bool:
        return (st.status == "ended" or st.finalized or st.ended_at is not None
                or getattr(st, "_functions_ended", False))

    def _finish(self, rec: _Invocation, result: dict, event: str | None = None) -> bool:
        if rec.result is not None:
            return False
        rec.result = result
        if rec.timer is not None:
            rec.timer.cancel()
            rec.timer = None
        if rec.st.pending_functions.get(rec.call_id) is rec.future:
            rec.st.pending_functions.pop(rec.call_id)
        if not rec.future.done():
            rec.future.set_result(result)
        kind = event or ("error" if result.get("status") == "error" else "completed")
        self.publish(rec.st.call_id, f"function.{kind}", {
            "name": rec.name, "functionCallId": rec.call_id, "result": result,
        })
        if rec.deferred and rec.on_late is not None and kind != "cancelled":
            self._deliver(rec)
        return True

    def _deliver(self, rec: _Invocation) -> None:
        if self._closed or self.tasks.closing or self._ended(rec.st):
            return
        if sum(not task.done() for task in self._background) >= MAX_BACKGROUND_TASKS:
            log.error("function result delivery rejected: background capacity exhausted")
            return

        async def deliver() -> None:
            if not self._closed and not self.tasks.closing and not self._ended(rec.st):
                try:
                    await rec.on_late(_object(rec.result))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("function result delivery failed")

        work = deliver()
        try:
            task = self.tasks.spawn(work, name="function-result")
        except RuntimeError:
            work.close()
            log.exception("function result delivery rejected")
            return
        rec.delivery = task
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _expire(self, rec: _Invocation) -> None:
        self._finish(rec, _error("expired", "deferred function expired after 120 seconds", uncertain=True))

    def cancel_call(self, st: CallState, reason: str = "call_ended") -> None:
        """Stop owned work before voice teardown; borrowed clients/supervisors stay open."""
        st._functions_ended = True
        current = asyncio.current_task()
        for key, rec in list(self._invocations.items()):
            if key[0] != st.call_id:
                continue
            self._finish(rec, _error("cancelled", str(reason)[:128], uncertain=True), "cancelled")
            for task in (rec.runner, rec.delivery):
                if task is not None and task is not current and not task.done():
                    task.cancel()
            self._invocations.pop(key)
        for future in st.pending_functions.values():
            if not future.done():
                future.cancel()
        st.pending_functions.clear()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        running = {r.runner for r in self._invocations.values()
                   if r.runner is not None and r.runner is not asyncio.current_task() and not r.runner.done()}
        for st in {id(r.st): r.st for r in self._invocations.values()}.values():
            self.cancel_call(st, "shutdown")
        try:
            await asyncio.gather(*running, *self._background, return_exceptions=True)
            if self._owns_tasks:
                await self.tasks.close()
        finally:
            if self._owns_http:
                await self._http.aclose()

    def register_builtin(self, name: str, handler: Handler) -> None:
        if not isinstance(name, str) or not IDENTITY.fullmatch(name):
            raise ValueError("invalid function name")
        self.builtins[name] = handler

    def describe(self) -> list[dict]:
        return [{"name": n, "kind": "builtin"} for n in self.builtins] + [
            {"name": f.name, "kind": "webhook", "async": f.is_async, "timeoutMs": f.timeout_ms, "description": f.description}
            for f in self.consumer.values()
        ]

    def pending(self, st: CallState) -> list[dict]:
        return [
            {"name": rec.name, "functionCallId": rec.call_id, "arguments": _object(rec.arguments)}
            for rec in self._invocations.values()
            if rec.st is st and rec.result is None and rec.call_id in st.pending_functions
        ]

    async def invoke(
        self,
        st: CallState,
        name: str,
        fn_call_id: str,
        args: dict,
        on_late: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Return a terminal JSON object or pending; async pending expires in 120s."""
        if self._closed or self.tasks.closing:
            return {"status": "error", "error": "application is draining"}
        if self._ended(st):
            return _error("call_ended", "call has ended")
        if any(not isinstance(value, str) or not IDENTITY.fullmatch(value) for value in (name, fn_call_id)):
            return _error("invalid_identity", "function name and invocation ID require 1-128 ASCII letters/digits/_/-")
        try:
            arguments = _object(args)
        except (ValueError, RecursionError, OverflowError) as error:
            return _error("invalid_arguments", str(error))
        key = (st.call_id, fn_call_id)
        previous = self._invocations.get(key)
        if previous is not None:
            if previous.name != name or previous.arguments != arguments:
                return _error("invocation_conflict", "invocation ID already belongs to different arguments or function")
            return _object(previous.result) if previous.result is not None else {"status": "pending"}
        # Reserve delivery capacity before side effects: simultaneous callbacks
        # must not silently lose their voice delivery at the task limit.
        deliveries = sum(r.on_late is not None and (r.result is None or (
            r.delivery is not None and not r.delivery.done())) for r in self._invocations.values())
        if (len(self._invocations) >= MAX_INVOCATIONS
                or sum(k[0] == st.call_id for k in self._invocations) >= MAX_CALL_INVOCATIONS
                or (on_late is not None and deliveries >= MAX_BACKGROUND_TASKS)):
            return _error("capacity", "function invocation capacity exhausted")
        rec = _Invocation(st, name, fn_call_id, arguments, asyncio.get_running_loop().create_future(),
                          runner=asyncio.current_task(), on_late=on_late)
        self._invocations[key] = rec
        self.publish(st.call_id, "function.called", {"name": name, "functionCallId": fn_call_id, "arguments": arguments})
        try:
            if name in self.builtins:
                result = _object(await self.builtins[name](st, _object(arguments)))
                self._finish(rec, result)
            elif (fd := self.consumer.get(name)) is None:
                self._finish(rec, _error("unknown_function", f"unknown function '{name}'"))
            else:
                await self._webhook(rec, fd)
            return _object(rec.result) if rec.result is not None else {"status": "pending"}
        except asyncio.CancelledError:
            self._finish(rec, _error("cancelled", "function invocation cancelled", uncertain=True), "cancelled")
            raise
        except Exception as error:
            log.exception("function %s failed", name)
            self._finish(rec, _error("function_failed", f"function failed: {type(error).__name__}"))
            return _object(rec.result)
        finally:
            rec.runner = None

    async def _webhook(self, rec: _Invocation, fd: FunctionDef) -> None:
        st = rec.st
        timeout = fd.timeout_ms / 1000
        if fd.is_async:
            st.pending_functions[rec.call_id] = rec.future
            rec.timer = asyncio.get_running_loop().call_later(PENDING_TTL_S, self._expire, rec)
        deadline = asyncio.get_running_loop().time() + min(timeout, PENDING_TTL_S) if fd.is_async else 0
        payload = {
            "callId": st.call_id, "functionCallId": rec.call_id, "name": rec.name,
            "arguments": rec.arguments, "callerPhone": st.caller_phone, "fields": st.fields,
            "async": fd.is_async,
            "resultUrl": f"{self.settings.public_base_url}/v1/calls/{st.call_id}/functions/{rec.call_id}/result",
        }
        try:
            response = await post_signed(fd.webhook_url, payload, self.settings.webhook_secret,
                                         min(timeout, PENDING_TTL_S) if fd.is_async else timeout,
                                         f"{st.call_id}:{rec.call_id}", self.settings.local_dev, client=self._http,
                                         retry_safe=False, attempts=1)
        except (httpx.TimeoutException, asyncio.TimeoutError):
            if fd.is_async and rec.result is None:
                rec.deferred = True
                return
            self._finish(rec, _error("outcome_unknown", "webhook response lost; execution may have occurred", uncertain=True))
            return
        except httpx.HTTPError:
            self._finish(rec, _error("outcome_unknown", "webhook request failed; execution may have occurred", uncertain=True))
            return
        except ValueError as error:
            self._finish(rec, _error("webhook_rejected", str(error)))
            return
        if response.status_code >= 300:
            self._finish(rec, _error("webhook_rejected", f"webhook returned {response.status_code}",
                                    uncertain=response.status_code >= 500))
        elif fd.is_async:
            try:
                await asyncio.wait_for(asyncio.shield(rec.future),
                                       max(0, deadline - asyncio.get_running_loop().time()))
            except asyncio.TimeoutError:
                rec.deferred = True
        else:
            try:
                if len(response.content) > MAX_JSON_BYTES:
                    raise ValueError("webhook response exceeds 256 KiB")
                result = _object(response.json())
            except (ValueError, RecursionError, OverflowError) as error:
                self._finish(rec, _error("invalid_result", str(error), uncertain=True))
            else:
                self._finish(rec, result)

    def resolve(self, st: CallState, fn_call_id: str, result: dict) -> bool:
        if self._closed or self.tasks.closing or self._ended(st):
            self.cancel_call(st)
            return False
        if not isinstance(fn_call_id, str) or not IDENTITY.fullmatch(fn_call_id):
            return False
        rec = self._invocations.get((st.call_id, fn_call_id))
        if rec is None or rec.result is not None or st.pending_functions.get(fn_call_id) is not rec.future:
            return False
        if rec.future.cancelled():
            self._finish(rec, _error("cancelled", "function future cancelled", uncertain=True), "cancelled")
            return False
        try:
            validated = _object(result)
        except (ValueError, RecursionError, OverflowError) as error:
            self._finish(rec, _error("invalid_result", str(error)))
            return False
        return self._finish(rec, validated)
