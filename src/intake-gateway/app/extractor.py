"""Extraction runner: calls any Foundry prompt agent via the Responses API."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from .config import Settings
from .identity import TokenSource
from .http import request, retryable_error
from .drafts import DraftConflict
from .extraction_models import validate_result
from .state import CallState, EventHub

log = logging.getLogger("gateway.extractor")
RETRY_DELAY_S = 1.0
MAX_ATTEMPTS = 3
FINALIZATION_TIMEOUT_S = 180


def parse_agent_output(data: dict) -> dict:
    """Pull the JSON object out of a Responses API payload."""
    if data.get("status", "completed") != "completed" or data.get("error"):
        raise ValueError("extractor response is incomplete or failed")
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                text = c.get("text")
                if text:
                    return parse_json_lenient(text)
    items = [i.get("type") for i in data.get("output", [])]
    err = data.get("error") or (data.get("incomplete_details") or {})
    raise ValueError(
        f"no message output from extractor agent (status={data.get('status')}, output_types={items}, detail={err})"
    )


def parse_json_lenient(text: str) -> dict:
    """Accept a complete JSON object, including fenced output, but never repair truncation."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in extractor output")
    text = text[start:]
    obj, _ = json.JSONDecoder().raw_decode(text)
    if not isinstance(obj, dict):
        raise ValueError("extractor output must be an object")
    return obj


_CITE = re.compile(r"【[^】]*】")
_PRIORITY = {"normal": "Medium", "medium": "Medium", "standard": "Medium", "low": "Low", "minor": "Low",
             "high": "High", "urgent": "Urgent", "critical": "Urgent", "emergency": "Urgent"}


def _clean(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _CITE.sub("", text)).strip()


def normalize_enrichment(enr: dict) -> dict:
    """Align enrichment with ticket vocabulary and strip raw citation markers."""
    out = dict(enr)
    pr = out.get("suggested_priority")
    if isinstance(pr, str) and pr.strip():
        out["suggested_priority"] = _PRIORITY.get(pr.strip().lower(), pr.strip())
    srcs = out.get("sources")
    if isinstance(srcs, list):
        cleaned = [_clean(x) if isinstance(x, str) else x for x in srcs]
        out["sources"] = [x for x in cleaned if x]
    return out


class Extractor:
    def __init__(self, settings: Settings, hub: EventHub, tokens: TokenSource | None = None,
                 client: httpx.AsyncClient | None = None):
        self.s = settings
        self.hub = hub
        self.tokens = tokens
        self._http = client or httpx.AsyncClient()
        self._owns_http = client is None
        self.on_result = None  # async (st, result) -> None; persists the draft
        self.on_validate = None
        self.checkpoint = None
        self._jobs: dict[str, asyncio.Task] = {}

    @property
    def enabled(self) -> bool:
        return self.tokens is not None and bool(self.s.extractor_agent)

    def needs_work(self, st: CallState) -> bool:
        return bool(self.enabled and (st.extraction_result is not None or st.processed < st.version
                                     or (st.final_requested and not st.final_extraction)))

    async def _checkpoint(self, st: CallState) -> None:
        if self.checkpoint is not None:
            await self.checkpoint(st)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _invoke(self, payload: dict, timeout_s: float) -> dict:
        tok = await self.tokens.get()  # type: ignore[union-attr]
        body = {
            "agent_reference": {"type": "agent_reference", "name": self.s.extractor_agent},
            "input": json.dumps(payload),
        }
        r = await request(
            self._http, "POST", f"{self.s.foundry_project_endpoint}/openai/v1/responses", timeout_s=timeout_s,
            json=body, headers={"Authorization": f"Bearer {tok}"},
        )
        r.raise_for_status()
        return parse_agent_output(r.json())

    async def _invoke_retry(self, payload: dict, timeout_s: float) -> dict:
        # The durable worker owns the retry budget, including token acquisition.
        async with asyncio.timeout(timeout_s):
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
        result = validate_result(result, self.s.extraction_schema)
        if self.on_validate is not None:
            await self.on_validate(st, result)
        if result.get("enrichment") is not None:
            result["enrichment"] = normalize_enrichment(result["enrichment"])
        if self.on_result is not None:
            await self.on_result(st, result)
        st.fields = result["fields"]
        self.hub.publish(st.call_id, "extraction.updated", {"fields": st.fields, "version": st.version, "mode": mode})
        enr = result.get("enrichment")
        if self.s.enrichment and isinstance(enr, dict):
            enr = normalize_enrichment(enr)
            st.enrichment = enr
            self.hub.publish(st.call_id, "enrichment.updated", enr)
        if self.on_result is not None:
            self.hub.publish(st.call_id, "draft.updated", {"mode": mode})

    def trigger(self, st: CallState) -> asyncio.Task | None:
        if self.hub.tasks.closing or not self.enabled:
            return
        if st.running:
            return self._jobs.get(st.call_id)
        existing = self._jobs.get(st.call_id)
        if existing is not None and not existing.done():
            return existing
        if st.extraction_status == "failed":
            if st.version <= st.extraction_target:
                return
            st.extraction_attempts = 0
            st.extraction_status, st.extraction_error = "pending", None
        if not self.needs_work(st):
            return
        task = self.hub.tasks.spawn(self._loop(st), name="extraction")
        self._jobs[st.call_id] = task
        st.running = True
        task.add_done_callback(lambda done: self._jobs.pop(st.call_id, None)
                               if self._jobs.get(st.call_id) is done else None)
        return task

    async def _loop(self, st: CallState) -> None:
        try:
            while self.needs_work(st):
                if self.hub.tasks.closing and st.extraction_result is None:
                    st.extraction_status = "pending"
                    await self._checkpoint(st)
                    return
                if st.extraction_result is None and st.extraction_target != st.version:
                    st.extraction_target = st.version
                    st.extraction_attempts = 0
                target = st.extraction_target
                mode = st.extraction_mode if st.extraction_result is not None else ("finalize" if st.final_requested else "extract")
                st.extraction_mode = mode
                enrich = self.s.enrichment and (time.time() - st.last_enrich) >= self.s.enrichment_interval_s
                candidate = st.extraction_result
                st.extraction_attempts += 1
                try:
                    if st.extraction_attempts > MAX_ATTEMPTS:
                        raise RuntimeError("extraction retry budget exhausted")
                    st.extraction_status, st.extraction_error = "pending", None
                    self.hub.publish(st.call_id, "extraction.status", {"status": "pending", "error": None,
                                                                     "version": target})
                    await self._checkpoint(st)
                    if candidate is None:
                        candidate = validate_result(await self._invoke_retry(
                            self.build_payload(st, mode, enrich or st.final_requested), 60 if enrich else 30),
                            self.s.extraction_schema)
                        if self.on_validate is not None:
                            await self.on_validate(st, candidate)
                        st.extraction_result = candidate
                        st.extracted_version = target
                        st.extraction_status = "persisting"
                        await self._checkpoint(st)
                    await self.apply(st, candidate, mode)
                    st.processed = target
                    st.final_extraction = mode == "finalize" and target == st.version
                    st.extraction_status = "complete" if target == st.version else "pending"
                    st.extraction_result = None
                    await self._checkpoint(st)
                    st.extraction_attempts = 0
                    self.hub.publish(st.call_id, "extraction.status", {"status": st.extraction_status,
                                                                     "error": None, "version": target})
                    if enrich:
                        st.last_enrich = time.time()
                    if st.final_extraction:
                        self.hub.publish(st.call_id, "extraction.final", {"fields": st.fields, "enrichment": st.enrichment})
                except Exception as e:  # noqa: BLE001
                    st.extraction_result = candidate
                    retry = (retryable_error(e) or isinstance(e, DraftConflict)) and st.extraction_attempts < MAX_ATTEMPTS
                    st.extraction_status = "pending" if retry else "failed"
                    st.extraction_error = type(e).__name__
                    st.final_extraction = False
                    log.warning("[%s] extraction %s: %s", st.call_id, st.extraction_status, type(e).__name__)
                    self.hub.publish(st.call_id, "error", {"stage": "extract", "message": st.extraction_error,
                                                          "retrying": retry, "version": target})
                    self.hub.publish(st.call_id, "extraction.status", {"status": st.extraction_status,
                                                                     "error": st.extraction_error, "version": target})
                    try:
                        await self._checkpoint(st)
                    except Exception as checkpoint_error:
                        log.warning("[%s] extraction state checkpoint failed: %s", st.call_id, type(checkpoint_error).__name__)
                    if not retry:
                        return
                    await asyncio.sleep(RETRY_DELAY_S * 2 ** (st.extraction_attempts - 1))
        finally:
            st.running = False

    async def finalize(self, st: CallState) -> None:
        if not self.enabled or not st.transcript:
            return
        st.final_requested = True
        task = self.trigger(st)
        if task is None:
            await self._checkpoint(st)
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), FINALIZATION_TIMEOUT_S)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            st.extraction_status, st.extraction_error = "failed", "finalization_deadline"
            self.hub.publish(st.call_id, "error", {"stage": "finalize", "message": st.extraction_error})
            self.hub.publish(st.call_id, "extraction.status", {"status": "failed", "error": st.extraction_error,
                                                             "version": st.extraction_target})
            await self._checkpoint(st)
