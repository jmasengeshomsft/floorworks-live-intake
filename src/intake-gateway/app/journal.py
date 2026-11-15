"""Dataverse transcript checkpoints, independent of model availability."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

import httpx
from azure.core.exceptions import AzureError, HttpResponseError, ServiceRequestError, ServiceResponseError

from .drafts import DRAFT, DRAFT_INTEGRITY_COLUMNS, DraftConflict, DraftStore, etag, record_id
from .extractor import Extractor
from .state import CallState, CallStore, EventHub

log = logging.getLogger("gateway.journal")
JOURNAL = "bdm_calljournals"
RETRY_DELAY_S = 1.0


class DataverseJournal:
    def __init__(self, drafts: DraftStore):
        self.drafts = drafts

    async def get(self, call_id: str) -> dict | None:
        try:
            row = await self.drafts._send("GET", f"{JOURNAL}({record_id('journal', call_id)})")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return json.loads(row["bdm_checkpointjson"])

    async def verify(self) -> None:
        await self.drafts._ensure_schema()
        schema = self.drafts.schema
        for table, columns in (
            (DRAFT, DRAFT_INTEGRITY_COLUMNS),
            (JOURNAL, ("bdm_callid", "bdm_checkpointjson", "bdm_version", "bdm_complete")),
        ):
            if not schema.enabled(table) or any(not schema.supports(table, c) for c in columns):
                raise RuntimeError(f"{table}: required integrity columns cannot be disabled")
            meta = await self.drafts._send(
                "GET", f"EntityDefinitions(LogicalName='{schema.logical_name(table)}')"
                "?$select=IsOptimisticConcurrencyEnabled"
            )
            if meta.get("IsOptimisticConcurrencyEnabled") is not True:
                raise RuntimeError(f"{table}: optimistic concurrency is required")
        keys = await self.drafts._rows(
            f"EntityDefinitions(LogicalName='{schema.logical_name(DRAFT)}')/Keys"
            "?$select=KeyAttributes,EntityKeyIndexStatus"
        )
        call_column = schema.real_column(DRAFT, "bdm_callid")
        if not any(k.get("KeyAttributes") == [call_column] and k.get("EntityKeyIndexStatus") in ("Active", 2) for k in keys):
            raise RuntimeError("draft call-ID alternate key must be Active; rerun the Dataverse provisioner")
        if not schema.supports("bdm_servicetickets", "bdm_serviceticketid"):
            raise RuntimeError("ticket primary ID is required for idempotent submission")
        await self.drafts._rows(f"{DRAFT}?$select={self.drafts._draft_select()}&$top=1")
        await self.drafts._rows(f"{JOURNAL}?$select=bdm_callid,bdm_version,bdm_complete,bdm_checkpointjson&$top=1")

    async def save(self, data: dict, complete: bool = False) -> None:
        call_id = data["call_id"]
        path = f"{JOURNAL}({record_id('journal', call_id)})"
        text = json.dumps(data, ensure_ascii=True)
        if len(text) > 1_000_000:
            raise RuntimeError("call checkpoint exceeds the Dataverse journal column limit")
        for _ in range(5):
            try:
                row = await self.drafts._send("GET", path)
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                row = None
            if row and row["bdm_version"] > data["version"]:
                raise DraftConflict("a newer call checkpoint already exists; multiple call owners are unsupported")
            if (row and row.get("bdm_complete") and not complete
                    and row["bdm_checkpointjson"] == text):
                return
            body = {"bdm_name": f"Call {call_id}", "bdm_callid": call_id, "bdm_version": data["version"],
                    "bdm_checkpointjson": text, "bdm_complete": int(complete)}
            try:
                await self.drafts._send("PATCH", path, body,
                                        headers={"If-Match": etag(row)} if row else {"If-None-Match": "*"})
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 412:
                    raise
        raise DraftConflict("call checkpoint changed repeatedly")

    async def pending(self) -> list[dict]:
        path = f"{JOURNAL}?$select=bdm_checkpointjson&$filter=bdm_complete eq 0&$orderby=createdon"
        records = []
        while path:
            page = await self.drafts._send("GET", path)
            records.extend(json.loads(row["bdm_checkpointjson"]) for row in page.get("value", []))
            next_link = page.get("@odata.nextLink")
            if not next_link:
                break
            # Never forward a Dataverse token to a continuation host supplied by another service.
            if not next_link.startswith(self.drafts.api) or urlparse(next_link).netloc != urlparse(self.drafts.api).netloc:
                raise RuntimeError("invalid Dataverse journal continuation URL")
            path = next_link[len(self.drafts.api):]
        return records


class TranscriptRecorder:
    def __init__(self, drafts: DraftStore, store: CallStore, hub: EventHub, extractor: Extractor):
        self.drafts = drafts
        self.journal = DataverseJournal(drafts)
        self.store = store
        self.hub = hub
        self.extractor = extractor
        self.tasks: dict[str, asyncio.Task] = {}
        self.generations: dict[str, int] = {}
        self.closing = False
        extractor.checkpoint = self.flush

    def record(self, st: CallState) -> asyncio.Task:
        if self.closing:
            raise RuntimeError("transcript recorder is shutting down")
        self.generations[st.call_id] = self.generations.get(st.call_id, 0) + 1
        task = self.tasks.get(st.call_id)
        if task is None or task.done():
            task = asyncio.create_task(self._run(st))
            self.tasks[st.call_id] = task
            task.add_done_callback(lambda done: self._finished(st.call_id, done))
        return task

    def _finished(self, call_id: str, task: asyncio.Task) -> None:
        if self.tasks.get(call_id) is task:
            del self.tasks[call_id]
            self.generations.pop(call_id, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("[%s] transcript checkpoint worker failed", call_id,
                      exc_info=(type(error), error, error.__traceback__))

    async def _run(self, st: CallState) -> None:
        delay = RETRY_DELAY_S
        while True:
            generation = self.generations[st.call_id]
            data = st.checkpoint()
            try:
                await self.journal.save(data)
                st.checkpoint_version = data["version"]
                st.persistence_error = False
                for line in data["transcript"][st.published_version:]:
                    self.hub.publish(st.call_id, "transcript", line)
                    st.published_version += 1
                await self.drafts.save_transcript(CallState.from_checkpoint(data))
                self.hub.publish(st.call_id, "draft.updated", {"mode": "transcript"})
                if data["status"] == "ended" and (not self.extractor.enabled
                        or data.get("extraction_status") in ("complete", "failed") or not data["version"]):
                    await self.journal.save(data, complete=True)
                elif not self.closing and not self.hub.tasks.closing:
                    self.extractor.trigger(st)
            except (httpx.HTTPError, AzureError, RuntimeError, DraftConflict) as e:
                st.persistence_error = True
                log.warning("[%s] transcript checkpoint failed: %s", st.call_id, type(e).__name__)
                self.hub.publish(st.call_id, "error", {"stage": "checkpoint", "message": "transcript persistence failed"})
                status = (e.response.status_code if isinstance(e, httpx.HTTPStatusError)
                          else e.status_code if isinstance(e, HttpResponseError) else None)
                retryable = isinstance(e, (httpx.TransportError, ServiceRequestError, ServiceResponseError)) or (
                    status is not None and (status == 429 or status >= 500)
                )
                if self.closing or not retryable:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            delay = RETRY_DELAY_S
            if generation == self.generations[st.call_id]:
                return

    async def flush(self, st: CallState) -> None:
        await asyncio.wait_for(asyncio.shield(self.record(st)), timeout=30)

    async def recover(self) -> None:
        await self.journal.verify()
        for data in await self.journal.pending():
            st = CallState.from_checkpoint(data)
            # A checkpoint can restore notes, not a live socket or an ACS media session.
            st.status = "ended"
            st.finalized = True
            st.ended_at = st.ended_at or time.time()
            if self.extractor.enabled and st.version and st.extraction_status != "failed":
                st.final_requested = True
            self.store.put(st)
            await self.flush(st)
            self.extractor.trigger(st)
            self.hub.publish(st.call_id, "call.ended", {"recovered": True, "fields": st.fields})

    async def close(self) -> None:
        self.closing = True
        tasks = [task for task in self.tasks.values() if not task.done()]
        if tasks:
            message = "shutdown left uncheckpointed transcript data; Dataverse recovery includes only committed checkpoints"
            try:
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                log.exception(message)
                raise
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                log.error(message)
                raise BaseExceptionGroup(message, failures)


async def finalize_notes(st: CallState, hub: EventHub, extractor: Extractor,
                         recorder: TranscriptRecorder | None = None) -> None:
    if recorder is not None and not recorder.closing:
        recorder.record(st)
    task = asyncio.current_task()
    draining = hub.tasks.closing or bool(task and task.cancelling())
    try:
        if not draining:
            await extractor.finalize(st)
    finally:
        try:
            if recorder is not None:
                if hub.tasks.closing or bool(task and task.cancelling()):
                    recorder.record(st)
                else:
                    await recorder.flush(st)
        except (asyncio.TimeoutError, httpx.HTTPError, AzureError, RuntimeError, DraftConflict) as error:
            st.persistence_error = True
            log.warning("[%s] final checkpoint incomplete: %s", st.call_id, type(error).__name__)
            hub.publish(st.call_id, "error", {"stage": "checkpoint", "message": "final transcript persistence incomplete"})
        finally:
            hub.publish(st.call_id, "call.ended", {
                "fields": st.fields, "enrichment": st.enrichment, "transcript": st.transcript,
                "checkpointVersion": st.checkpoint_version, "persistenceError": st.persistence_error,
            })
