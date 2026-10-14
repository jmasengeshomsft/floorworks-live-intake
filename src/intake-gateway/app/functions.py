"""Client-side function registry: built-ins plus consumer functions via signed webhook."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from .config import FunctionDef, Settings
from .state import CallState
from .webhooks import post_signed

log = logging.getLogger("gateway.functions")

Handler = Callable[[CallState, dict], Awaitable[dict]]


class FunctionRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.builtins: dict[str, Handler] = {}
        self.consumer: dict[str, FunctionDef] = {f.name: f for f in settings.functions}
        self.publish: Callable[[str, str, Any], Any] = lambda *_: None

    def register_builtin(self, name: str, handler: Handler) -> None:
        self.builtins[name] = handler

    def describe(self) -> list[dict]:
        return [{"name": n, "kind": "builtin"} for n in self.builtins] + [
            {"name": f.name, "kind": "webhook", "async": f.is_async, "timeoutMs": f.timeout_ms, "description": f.description}
            for f in self.consumer.values()
        ]

    async def invoke(
        self,
        st: CallState,
        name: str,
        fn_call_id: str,
        args: dict,
        on_late: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Always returns a dict suitable as function_call_output.

        For async functions that miss their timeout, returns {"status":"pending"} and later
        calls on_late(result) when the consumer posts the deferred result.
        """
        self.publish(st.call_id, "function.called", {"name": name, "functionCallId": fn_call_id, "arguments": args})
        if name in self.builtins:
            try:
                return await self.builtins[name](st, args)
            except Exception as e:  # noqa: BLE001
                log.exception("builtin %s failed", name)
                return {"status": "error", "error": str(e)}
        fd = self.consumer.get(name)
        if fd is None:
            return {"status": "error", "error": f"unknown function '{name}'"}

        payload = {
            "callId": st.call_id,
            "functionCallId": fn_call_id,
            "name": name,
            "arguments": args,
            "callerPhone": st.caller_phone,
            "fields": st.fields,
            "async": fd.is_async,
            "resultUrl": f"{self.settings.public_base_url}/v1/calls/{st.call_id}/functions/{fn_call_id}/result",
        }
        if fd.is_async:
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            st.pending_functions[fn_call_id] = fut
            try:
                r = await post_signed(fd.webhook_url, payload, self.settings.webhook_secret, fd.timeout_ms / 1000, fn_call_id)
                if r.status_code >= 300:
                    return {"status": "error", "error": f"webhook returned {r.status_code}"}
                return await asyncio.wait_for(asyncio.shield(fut), fd.timeout_ms / 1000)
            except asyncio.TimeoutError:
                if on_late is not None:
                    fut.add_done_callback(lambda f: asyncio.get_running_loop().create_task(on_late(f.result())))
                return {"status": "pending"}
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "error": str(e)}
        try:
            r = await post_signed(fd.webhook_url, payload, self.settings.webhook_secret, fd.timeout_ms / 1000, fn_call_id)
            if r.status_code >= 300:
                return {"status": "error", "error": f"webhook returned {r.status_code}"}
            try:
                out = r.json()
            except json.JSONDecodeError:
                out = {"result": r.text}
            return out if isinstance(out, dict) else {"result": out}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    def resolve(self, st: CallState, fn_call_id: str, result: dict) -> bool:
        fut = st.pending_functions.pop(fn_call_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(result)
        return True
