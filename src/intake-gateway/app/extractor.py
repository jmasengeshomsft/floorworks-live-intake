"""Extraction runner: calls any Foundry prompt agent via the Responses API."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from azure.identity import AzureCliCredential, DefaultAzureCredential

from .config import Settings
from .state import CallState, EventHub

log = logging.getLogger("gateway.extractor")
SCOPE = "https://ai.azure.com/.default"


class TokenSource:
    def __init__(self, local_dev: bool):
        self._cred = AzureCliCredential() if local_dev else DefaultAzureCredential()

    async def get(self) -> str:
        return (await asyncio.to_thread(self._cred.get_token, SCOPE)).token


def parse_agent_output(data: dict) -> dict:
    """Pull the JSON object out of a Responses API payload."""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                text = c.get("text")
                if text:
                    return parse_json_lenient(text)
    raise ValueError("no message output from extractor agent")


def parse_json_lenient(text: str) -> dict:
    """Parse the first JSON object in text; tolerate fences, trailing prose and truncation."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in extractor output")
    text = text[start:]
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
        return obj
    except json.JSONDecodeError:
        pass
    # Repair a truncated document by closing open strings/brackets.
    stack: list[str] = []
    in_str = esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    fixed = text + ('"' if in_str else "")
    fixed = fixed.rstrip().rstrip(",") + "".join(reversed(stack))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e:
        raise ValueError(f"unparseable extractor JSON: {e}") from e


class Extractor:
    def __init__(self, settings: Settings, hub: EventHub, tokens: TokenSource | None = None):
        self.s = settings
        self.hub = hub
        self.tokens = tokens

    async def _invoke(self, payload: dict, timeout_s: float) -> dict:
        tok = await self.tokens.get()  # type: ignore[union-attr]
        body = {
            "agent_reference": {"type": "agent_reference", "name": self.s.extractor_agent},
            "input": json.dumps(payload),
        }
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(
                f"{self.s.foundry_project_endpoint}/openai/v1/responses",
                json=body,
                headers={"Authorization": f"Bearer {tok}"},
            )
            r.raise_for_status()
            return parse_agent_output(r.json())

    async def _invoke_retry(self, payload: dict, timeout_s: float) -> dict:
        try:
            return await self._invoke(payload, timeout_s)
        except (ValueError, httpx.TransportError) as e:
            log.info("[%s] extractor retry after %s", payload.get("call_id"), type(e).__name__)
            return await self._invoke(payload, timeout_s)

    def build_payload(self, st: CallState, mode: str, enrich: bool) -> dict:
        p: dict[str, Any] = {
            "mode": mode,
            "call_id": st.call_id,
            "caller_phone": st.caller_phone,
            "transcript": [{"speaker": t["speaker"], "text": t["text"]} for t in st.transcript],
            "current": st.fields,
        }
        if self.s.extraction_schema:
            p["schema"] = self.s.extraction_schema
        if enrich:
            p["enrichment"] = "on"
        return p

    async def apply(self, st: CallState, result: dict, mode: str) -> None:
        fields = result.get("fields", result if "enrichment" not in result else {})
        if isinstance(fields, dict):
            st.fields = fields
        self.hub.publish(st.call_id, "extraction.updated", {"fields": st.fields, "version": st.version, "mode": mode})
        enr = result.get("enrichment")
        if self.s.enrichment and isinstance(enr, dict):
            st.enrichment = enr
            self.hub.publish(st.call_id, "enrichment.updated", enr)

    def trigger(self, st: CallState) -> None:
        if self.tokens is None or not self.s.extractor_agent:
            return
        if st.running:
            return  # the running loop re-checks version when it finishes
        st.running = True
        asyncio.get_running_loop().create_task(self._loop(st))

    async def _loop(self, st: CallState) -> None:
        try:
            while st.processed < st.version:
                target = st.version
                enrich = self.s.enrichment and (time.time() - st.last_enrich) >= self.s.enrichment_interval_s
                if enrich:
                    st.last_enrich = time.time()
                try:
                    res = await self._invoke_retry(self.build_payload(st, "extract", enrich), 60 if enrich else 30)
                    await self.apply(st, res, "extract")
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] extract failed: %s: %s", st.call_id, type(e).__name__, e)
                    self.hub.publish(st.call_id, "error", {"stage": "extract", "message": str(e)})
                st.processed = target
        finally:
            st.running = False

    async def finalize(self, st: CallState) -> None:
        if self.tokens is None or not self.s.extractor_agent or not st.transcript:
            return
        while st.running:
            await asyncio.sleep(0.25)
        try:
            res = await self._invoke_retry(self.build_payload(st, "finalize", self.s.enrichment), 90)
            await self.apply(st, res, "finalize")
            self.hub.publish(st.call_id, "extraction.final", {"fields": st.fields, "enrichment": st.enrichment})
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] finalize failed: %s: %s", st.call_id, type(e).__name__, e)
            self.hub.publish(st.call_id, "error", {"stage": "finalize", "message": str(e)})
