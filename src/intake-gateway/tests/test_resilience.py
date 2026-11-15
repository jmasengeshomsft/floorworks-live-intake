import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.config import Settings
from app.drafts import DraftError
from app.extraction_models import check_schema, validate_result
from app.extractor import Extractor, parse_agent_output
from app.journal import DataverseJournal, TranscriptRecorder
from app.main import create_app
from app.state import CallState, EventHub, MemoryStore, TranscriptLimit
from tests.test_drafts import FakeDataverse, call, mkstore


@pytest.mark.parametrize("result", [
    {"fields": {"complaint": []}},
    {"fields": {"priority": "unknown"}},
    {"fields": {"summary": "x" * 4001}},
    {"fields": {}, "matches": [{"table": "bdm_equipment", "id": "bad"}]},
    {"fields": {}, "enrichment": {"parts": [{"number": "a", "name": "b", "unit_cost": -1}]}},
    {"fields": {}, "error": "refused"},
    {"fields": {}, "created": "ticket"},
    {"fields": {"custom": float("nan")}},
])
def test_invalid_extraction_preserves_last_good_state_without_writes(result):
    async def run():
        ex = Extractor(Settings(), EventHub())
        ex.on_result = AsyncMock()
        st = call()
        st.fields = {"complaint": "last valid"}
        with pytest.raises(ValueError):
            await ex.apply(st, result, "extract")
        assert st.fields == {"complaint": "last valid"}
        ex.on_result.assert_not_awaited()
        assert not ex.hub._buf
        await ex.aclose()
    asyncio.run(run())


def test_configured_schema_and_incomplete_api_response_are_rejected():
    with pytest.raises(ValueError):
        validate_result({"fields": {"custom": 1}}, {"properties": {"custom": {"type": "string"}}})
    with pytest.raises(ValueError):
        check_schema({"$ref": "https://example.invalid/schema"})
    with pytest.raises(ValueError):
        check_schema({"$dynamicRef": "https://example.invalid/schema"})
    with pytest.raises(ValueError):
        parse_agent_output({"status": "incomplete", "output": [{"type": "message", "content": [{"text": '{"fields":{}}'}]}]})


def test_ai_reference_validation_prevents_foreign_equipment_publication():
    async def run():
        ds = mkstore(FakeDataverse())
        ds.find_draft = AsyncMock(return_value={"_bdm_customerid_value": "customer"})
        ds._record = AsyncMock(return_value={"_bdm_customerid_value": "another-customer"})
        ex = Extractor(Settings(), EventHub())
        ex.on_validate, ex.on_result = ds.validate_ai, AsyncMock()
        st = call()
        st.fields = {"complaint": "last valid"}
        with pytest.raises(DraftError):
            await ex.apply(st, {"fields": {"complaint": "new"}, "matches": [
                {"table": "bdm_equipment", "id": "10000000-0000-0000-0000-000000000001"}]}, "extract")
        assert st.fields == {"complaint": "last valid"} and not ex.hub._buf
        ex.on_result.assert_not_awaited()
        await ex.aclose()
        await ds._http.aclose()
    asyncio.run(run())


def test_persistence_retry_without_new_speech_reuses_validated_model_output(monkeypatch):
    async def run():
        monkeypatch.setattr("app.extractor.RETRY_DELAY_S", 0)
        ds = mkstore(FakeDataverse())
        hub = EventHub()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())
        rec = TranscriptRecorder(ds, MemoryStore(), hub, ex)
        ex._invoke_retry = AsyncMock(return_value={"fields": {"complaint": "valid result"}})
        writes = 0

        async def write(st, result):
            nonlocal writes
            writes += 1
            if writes == 1:
                raise httpx.ConnectError("temporary")
            await ds.save_notes(st, result)

        ex.on_result = write
        st = call()
        await ex.trigger(st)
        assert writes == 2 and ex._invoke_retry.await_count == 1
        assert st.processed == st.extracted_version == st.version
        assert st.extraction_status == "complete" and not st.running
        saved = await rec.journal.get(st.call_id)
        assert saved["processed"] == st.version and saved["extraction_result"] is None
        await hub.tasks.close()
        await rec.close()
        await ex.aclose()
        await ds._http.aclose()
    asyncio.run(run())


def test_restart_resumes_cached_final_result_without_another_model_request():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        hub = EventHub()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())
        rec = TranscriptRecorder(ds, MemoryStore(), hub, ex)
        ex.trigger = Mock()
        st = call()
        st.status = "ended"
        st.extraction_result = {"fields": {"complaint": "durable pending result"}}
        st.extraction_target = st.extracted_version = st.version
        st.extraction_status = "persisting"
        st.extraction_mode = "finalize"
        st.final_requested = True
        await rec.flush(st)
        await rec.close()
        await ex.aclose()

        restarted_hub, store = EventHub(), MemoryStore()
        restarted = Extractor(Settings(extractor_agent="extractor"), restarted_hub, Mock())
        restarted.on_result = ds.save_notes
        restarted._invoke_retry = AsyncMock(side_effect=AssertionError("model must not run again"))
        recorder = TranscriptRecorder(ds, store, restarted_hub, restarted)
        await recorder.recover()
        await restarted_hub.tasks.close(timeout=2)
        recovered = store.get(st.call_id)
        assert recovered.final_extraction and recovered.extraction_status == "complete"
        assert recovered.processed == st.version and not await recorder.journal.pending()
        restarted._invoke_retry.assert_not_awaited()
        assert dv.draft["bdm_complaint"] == "durable pending result"
        await recorder.close()
        await restarted.aclose()
        await ds._http.aclose()
    asyncio.run(run())


def test_permanent_model_failure_is_terminal_and_does_not_advance_version():
    async def run():
        hub = EventHub()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())
        ex._invoke_retry = AsyncMock(return_value={"fields": {"priority": "invalid"}})
        st = call()
        await ex.trigger(st)
        assert st.extraction_status == "failed" and st.processed == 0 and not st.final_extraction
        assert ex.trigger(st) is None
        assert ex._invoke_retry.await_count == 1
        await hub.tasks.close()
        await ex.aclose()
    asyncio.run(run())


def test_transient_model_failure_exhausts_budget_and_new_speech_reopens_work(monkeypatch):
    async def run():
        monkeypatch.setattr("app.extractor.RETRY_DELAY_S", 0)
        hub = EventHub()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())
        ex._invoke_retry = AsyncMock(side_effect=httpx.ReadTimeout("temporary"))
        st = call()
        await ex.trigger(st)
        assert ex._invoke_retry.await_count == 3 and st.extraction_status == "failed"
        assert st.processed == 0
        st.add_line("caller", "Another detail")
        ex._invoke_retry.side_effect = [httpx.ReadTimeout("temporary"), {"fields": {"summary": "Recovered"}}]
        await ex.trigger(st)
        assert ex._invoke_retry.await_count == 5 and st.processed == st.version
        assert st.extraction_status == "complete" and st.extraction_error is None
        await hub.tasks.close()
        await ex.aclose()
    asyncio.run(run())


def test_queued_extraction_does_not_start_a_model_during_shutdown():
    async def run():
        hub = EventHub()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())
        ex._invoke_retry = AsyncMock()
        ex.checkpoint = AsyncMock()
        st = call()
        ex.trigger(st)
        await hub.tasks.close()
        ex._invoke_retry.assert_not_awaited()
        assert not st.running and st.processed == 0 and st.extraction_status == "pending"
        ex.checkpoint.assert_awaited_once_with(st)
        await ex.aclose()
    asyncio.run(run())


def test_finalization_deadline_is_visible_and_cancels_the_worker(monkeypatch):
    async def run():
        monkeypatch.setattr("app.extractor.FINALIZATION_TIMEOUT_S", 0.01)
        hub = EventHub()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())

        async def stalled(*_):
            await asyncio.Event().wait()

        ex._invoke_retry = stalled
        st = call()
        await ex.finalize(st)
        assert not st.running and not st.final_extraction and st.processed == 0
        assert st.extraction_status == "failed" and st.extraction_error == "finalization_deadline"
        assert hub._buf[st.call_id][-1]["data"]["status"] == "failed"
        await hub.tasks.close()
        await ex.aclose()
    asyncio.run(run())


def test_recovery_requests_final_model_output_when_only_incremental_output_was_cached():
    async def run():
        ds = mkstore(FakeDataverse())
        st = call()
        st.extraction_result = {"fields": {"summary": "Incremental"}}
        st.extraction_target = st.extracted_version = st.version
        st.extraction_mode = "extract"
        await DataverseJournal(ds).save(st.checkpoint())
        hub, store = EventHub(), MemoryStore()
        ex = Extractor(Settings(extractor_agent="extractor"), hub, Mock())
        ex.on_result = ds.save_notes
        ex._invoke_retry = AsyncMock(return_value={"fields": {"summary": "Final"}})
        rec = TranscriptRecorder(ds, store, hub, ex)
        await rec.recover()
        task = ex._jobs.get(st.call_id)
        if task is not None:
            await task
        recovered = store.get(st.call_id)
        assert recovered.final_extraction and recovered.fields == {"summary": "Final"}
        ex._invoke_retry.assert_awaited_once()
        assert ex._invoke_retry.await_args.args[0]["mode"] == "finalize"
        await hub.tasks.close()
        await rec.close()
        await ex.aclose()
        await ds._http.aclose()
    asyncio.run(run())


def test_finalization_cancels_functions_before_voice_cleanup_and_shutdown_still_drains(monkeypatch):
    async def run():
        app = create_app(Settings(local_dev=True, live_notes=False))
        gw = app.state.gateway
        sleep = asyncio.sleep

        async def fast_sleep(_):
            await sleep(0)

        monkeypatch.setattr("app.bridge.asyncio.sleep", fast_sleep)
        async with app.router.lifespan_context(app):
            st = app.state.store.get_or_create("end-functions")
            st.pending_functions["pending"] = asyncio.get_running_loop().create_future()
            future = st.pending_functions["pending"]

            async def close_voice(state):
                assert future.cancelled() and not state.pending_functions

            gw.transcriber.close = AsyncMock(side_effect=close_voice)
            await gw._maybe_finalize(st)
            assert st.finalized
            assert (await gw.functions.invoke(st, "hangup", "late", {}))["code"] == "call_ended"
        assert gw.tasks.closing

        app = create_app(Settings(local_dev=True, live_notes=False))
        gw = app.state.gateway
        gw.functions.aclose = AsyncMock(side_effect=RuntimeError("function cleanup failed"))
        gw.transcriber.aclose = AsyncMock()
        with pytest.raises(RuntimeError, match="function cleanup failed"):
            async with app.router.lifespan_context(app):
                pass
        assert gw.tasks.closing
        gw.transcriber.aclose.assert_awaited_once()
    asyncio.run(run())


def test_explicit_retry_stays_available_after_checkpoint_failure_and_reports_actual_final_status():
    async def run():
        app = create_app(Settings(local_dev=True, live_notes=False, extractor_agent="extractor"), tokens=Mock())
        ex = app.state.extractor
        st = app.state.store.get_or_create("retry")
        st.add_line("caller", "Please help")
        st.status, st.extraction_status, st.extraction_error = "ended", "failed", "previous_failure"
        ex._invoke_retry = AsyncMock(return_value={"fields": {"summary": "Recovered"}})
        ex.checkpoint = AsyncMock(side_effect=RuntimeError("checkpoint failed"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
            ) as client:
                assert (await client.get("/v1/calls/retry/extraction")).json()["final"] is False
                assert (await client.post("/v1/calls/retry/extraction/retry")).status_code == 500
                assert st.extraction_status == "failed" and st.extraction_error == "previous_failure"
                ex.checkpoint = AsyncMock()
                assert (await client.post("/v1/calls/retry/extraction/retry")).status_code == 200
                task = ex._jobs.get(st.call_id)
                if task is not None:
                    await task
                result = (await client.get("/v1/calls/retry/extraction")).json()
                assert result["final"] is True and result["status"] == "complete"
                assert result["persistedVersion"] == st.version
                assert (await client.post("/v1/calls/retry/extraction/retry")).status_code == 409
    asyncio.run(run())


def test_actual_memory_lifecycle_evicts_ended_calls_and_their_event_state():
    hub = EventHub()
    store = MemoryStore(max_calls=3, on_evict=hub.forget)
    active = store.get_or_create("active")
    for i in range(50):
        st = store.get_or_create(str(i))
        st.status = "ended"
        hub.publish(st.call_id, "call.ended", {})
    assert store.get("active") is active
    assert len(store.list()) == 3 and len(hub._buf) <= 2 and len(hub._seq) <= 2
    expired = CallState("expired", status="ended", started_at=1, ended_at=2)
    store.put(expired)
    assert store.get("expired") is None


def test_slow_subscriber_and_replay_gaps_require_explicit_resync():
    hub = EventHub(queue_size=2, replay=2)
    q = hub.subscribe("c")
    for _ in range(4):
        hub.publish("c", "transcript", {})
    assert q.qsize() == 1 and q.get_nowait()["type"] == "resync.required"
    assert not hub._subs
    assert hub.subscribe("c", 1).get_nowait()["type"] == "resync.required"
    assert hub.subscribe("c", 100).get_nowait()["type"] == "resync.required"


def test_events_published_after_call_eviction_still_have_a_global_bound():
    hub = EventHub(max_calls=2)
    hub.publish("old", "event", {})
    old = hub.subscribe("old", 1)
    for i in range(50):
        hub.publish(str(i), "event", {})
    assert len(hub._seq) == len(hub._buf) == 2
    assert old.get_nowait()["type"] == "resync.required" and not hub._subs


def test_webhook_work_is_bounded_even_when_receivers_stall():
    async def run():
        hub = EventHub(delivery_concurrency=2, delivery_capacity=3)
        release = asyncio.Event()
        active = 0
        peak = 0

        async def receive(*_):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await release.wait()
            active -= 1

        hub.listeners.append(receive)
        for i in range(10):
            hub.publish("c", "event", i)
        assert len(hub.tasks.pending) == 2 and len(hub._deliveries) == 3 and hub.dropped_deliveries == 7
        await asyncio.sleep(0)
        assert active == peak == 2
        release.set()
        await hub.tasks.close()
        assert not hub._deliveries and not hub.tasks.pending
    asyncio.run(run())


def test_webhook_work_restarts_before_completed_worker_callbacks_run():
    async def run():
        hub = EventHub(delivery_concurrency=1)
        received = []

        async def receive(_, event):
            received.append(event["data"])

        hub.listeners.append(receive)
        hub.publish("c", "event", "first")
        await asyncio.sleep(0)
        assert all(worker.done() for worker in hub._workers)
        hub.publish("c", "event", "second")
        await hub.tasks.close()
        assert received == ["first", "second"] and not hub._deliveries
    asyncio.run(run())


def test_transcript_limit_is_explicit_and_never_partially_appends():
    st = call()
    before = st.checkpoint()
    with pytest.raises(TranscriptLimit):
        st.add_line("customer", "x" * 256001)
    assert st.checkpoint() == before
