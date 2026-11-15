import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError, ServiceResponseError

from app.config import Settings
from app.extractor import Extractor
from app.journal import DataverseJournal, TranscriptRecorder
from app.main import create_app
from app.state import CallState, EventHub, MemoryStore
from tests.test_drafts import FakeDataverse, call, mkstore


def recorder(drafts):
    hub, store = EventHub(), MemoryStore()
    extractor = Extractor(Settings(), hub)
    return TranscriptRecorder(drafts, store, hub, extractor)


def test_transcripts_checkpoint_without_any_extractor():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        rec = recorder(ds)
        st = call()
        st.add_line("rep", "We can help.")
        await rec.flush(st)
        saved = await rec.journal.get(st.call_id)
        assert saved["transcript"] == st.transcript
        assert st.checkpoint_version == 2
        assert dv.draft["bdm_transcriptversion"] == 2
        assert "We can help." in dv.draft["bdm_transcript"]
        assert [e["type"] for e in rec.hub._buf[st.call_id]].count("transcript") == 2
        assert not dv.posts  # No customer or ticket is created by checkpointing.
        await rec.close()
    asyncio.run(run())


def test_checkpoint_during_task_drain_does_not_attempt_extraction_or_report_failure():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        hub = EventHub()
        extractor = Extractor(Settings(extractor_agent="extractor"), hub, tokens=Mock())
        rec = TranscriptRecorder(ds, MemoryStore(), hub, extractor)
        st = call()
        await hub.tasks.close(timeout=0)
        with patch.object(extractor, "trigger", wraps=extractor.trigger) as trigger:
            await rec.flush(st)
            trigger.assert_not_called()
        assert st.checkpoint_version == st.version and not st.persistence_error and not st.running
        assert dv.draft["bdm_transcriptversion"] == st.version
        assert (await rec.journal.get(st.call_id))["transcript"] == st.transcript
        assert not any(event["type"] == "error" for event in hub._buf[st.call_id])
        await rec.close()
        await extractor.aclose()
        await ds._http.aclose()
    asyncio.run(run())


def test_journal_survives_failed_draft_mirror_and_process_restart():
    async def run():
        dv = FakeDataverse()
        first = mkstore(dv)
        rec = recorder(first)
        send = first._send

        async def fail_mirror(method, path, *args, **kwargs):
            if method == "PATCH" and path.startswith("bdm_servicerequestdrafts("):
                raise httpx.HTTPStatusError("mirror denied", request=httpx.Request(method, "https://example.invalid"),
                                            response=httpx.Response(403))
            return await send(method, path, *args, **kwargs)

        first._send = fail_mirror
        st = call()
        with pytest.raises(httpx.HTTPStatusError):
            await rec.flush(st)
        assert st.persistence_error
        assert st.checkpoint_version == 1
        assert dv.draft is None

        restarted = recorder(mkstore(dv))
        await restarted.recover()
        recovered = restarted.store.get(st.call_id)
        assert recovered.transcript == st.transcript
        assert recovered.status == "ended" and recovered.finalized
        assert recovered.active_ws == 0 and not recovered.conn_id
        assert dv.draft["bdm_callid"] == st.call_id
        assert dv.draft["bdm_transcriptversion"] == 1
        assert not await restarted.journal.pending()
        before = dv.draft["bdm_servicerequestdraftid"]
        await restarted.recover()
        assert dv.draft["bdm_servicerequestdraftid"] == before
        assert not dv.posts
        await restarted.close()
    asyncio.run(run())


def test_no_transcript_event_acknowledgement_before_durable_write(monkeypatch):
    async def run():
        monkeypatch.setattr("app.journal.RETRY_DELAY_S", 0)
        dv = FakeDataverse()
        ds = mkstore(dv)
        rec = recorder(ds)
        send = ds._send
        attempted, release = asyncio.Event(), asyncio.Event()
        failed = False

        async def outage(method, path, *args, **kwargs):
            nonlocal failed
            if method == "PATCH" and path.startswith("bdm_calljournals(") and not failed:
                failed = True
                attempted.set()
                await release.wait()
                raise httpx.ConnectError("Dataverse unavailable")
            return await send(method, path, *args, **kwargs)

        ds._send = outage
        st = call()
        task = rec.record(st)
        await asyncio.wait_for(attempted.wait(), 1)
        assert st.checkpoint_version == 0
        assert not rec.hub._buf.get(st.call_id)
        st.add_line("customer", "Another detail during the outage.")
        rec.record(st)
        release.set()
        await asyncio.wait_for(task, 2)
        saved = await rec.journal.get(st.call_id)
        assert saved["transcript"] == st.transcript
        events = rec.hub._buf[st.call_id]
        assert events[0]["type"] == "error"
        assert len([e for e in events if e["type"] == "transcript"]) == 2
        assert not st.persistence_error and st.checkpoint_version == 2
        await rec.close()
    asyncio.run(run())


@pytest.mark.parametrize("error_type", [ServiceRequestError, ServiceResponseError])
def test_identity_transport_failure_retries_without_new_speech(error_type, monkeypatch):
    async def run():
        monkeypatch.setattr("app.journal.RETRY_DELAY_S", 0)
        ds = mkstore(FakeDataverse())
        rec = recorder(ds)
        attempted, release = asyncio.Event(), asyncio.Event()

        class Tokens:
            async def get(self, scope):
                if not attempted.is_set():
                    attempted.set()
                    raise error_type("temporary identity transport failure")
                await release.wait()
                return "t"

        ds._tokens = Tokens()
        st = call()
        task = rec.record(st)
        await asyncio.wait_for(attempted.wait(), 1)
        assert st.persistence_error and st.checkpoint_version == 0
        assert rec.hub._buf[st.call_id][0]["data"]["stage"] == "checkpoint"
        release.set()
        await asyncio.wait_for(task, 1)
        assert not st.persistence_error and st.checkpoint_version == 1
        assert (await rec.journal.get(st.call_id))["transcript"] == st.transcript
        await rec.close()
    asyncio.run(run())


def test_permanent_identity_failure_is_visible_and_not_retried():
    async def run():
        ds = mkstore(FakeDataverse())
        rec = recorder(ds)
        ds._tokens.get = AsyncMock(side_effect=ClientAuthenticationError("identity not authorized"))
        st = call()
        with pytest.raises(ClientAuthenticationError):
            await rec.flush(st)
        assert st.persistence_error and st.checkpoint_version == 0
        assert rec.hub._buf[st.call_id][0]["data"]["stage"] == "checkpoint"
        assert ds._tokens.get.await_count == 1
        await rec.close()
    asyncio.run(run())


def test_model_failure_cannot_prevent_journal_or_draft_transcript_write():
    async def run():
        dv = FakeDataverse()
        rec = recorder(mkstore(dv))
        rec.extractor.s.extractor_agent = "fake"
        rec.extractor.tokens = object()
        attempted = asyncio.Event()

        async def fail(*args):
            attempted.set()
            raise RuntimeError("model unavailable")

        rec.extractor._invoke_retry = fail
        st = call()
        await rec.flush(st)
        await asyncio.wait_for(attempted.wait(), 1)
        assert (await rec.journal.get(st.call_id))["transcript"] == st.transcript
        assert "Hydraulic press" in dv.draft["bdm_transcript"]
        await rec.close()
    asyncio.run(run())


def test_old_transcript_checkpoint_cannot_overwrite_a_newer_mirror():
    async def run():
        ds = mkstore(FakeDataverse())
        st = call()
        old = CallState.from_checkpoint(st.checkpoint())
        st.add_line("customer", "latest")
        await ds.save_transcript(st)
        await ds.save_transcript(old)
        draft = await ds.find_draft(st.call_id)
        assert draft["bdm_transcriptversion"] == 2
        assert "latest" in draft["bdm_transcript"]
    asyncio.run(run())


def test_completed_journal_available_through_api_after_restart():
    async def run():
        dv = FakeDataverse()
        rec = recorder(mkstore(dv))
        st = call()
        st.status = "ended"
        st.ended_at = st.started_at + 10
        await rec.flush(st)
        assert not await rec.journal.pending()

        app = create_app(Settings(local_dev=True, live_notes=False), drafts_store=mkstore(dv))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/v1/calls/{st.call_id}/transcript")
                assert response.status_code == 200 and response.json() == st.transcript
                response = await client.get(f"/v1/calls/{st.call_id}")
                assert response.json()["status"] == "ended"
                assert response.json()["checkpointVersion"] == 1
        await rec.close()
    asyncio.run(run())


def test_startup_rejects_disabled_integrity_storage():
    async def run():
        ds = mkstore(FakeDataverse())
        ds.schema.disabled.add("bdm_calljournals")
        with pytest.raises(RuntimeError, match="cannot be disabled"):
            await DataverseJournal(ds).verify()
    asyncio.run(run())


def test_startup_rejects_tables_without_optimistic_concurrency():
    async def run():
        ds = mkstore(FakeDataverse())
        send = ds._send

        async def no_concurrency(method, path, *args, **kwargs):
            if "IsOptimisticConcurrencyEnabled" in path:
                return {"IsOptimisticConcurrencyEnabled": False}
            return await send(method, path, *args, **kwargs)

        ds._send = no_concurrency
        with pytest.raises(RuntimeError, match="optimistic concurrency"):
            await DataverseJournal(ds).verify()
    asyncio.run(run())


@pytest.mark.parametrize("column", ["bdm_summary", "bdm_transcriptversion", "bdm_TicketId"])
def test_startup_rejects_missing_integrity_mapping(column):
    async def run():
        ds = mkstore(FakeDataverse())
        ds.schema.fwd["bdm_servicerequestdrafts"][column] = None
        with pytest.raises(RuntimeError, match="required integrity columns"):
            await DataverseJournal(ds).verify()
    asyncio.run(run())


def test_startup_requires_active_unique_call_key():
    async def run():
        ds = mkstore(FakeDataverse())
        send = ds._send

        async def pending_key(method, path, *args, **kwargs):
            if "/Keys?" in path:
                return {"value": [{"KeyAttributes": ["bdm_callid"], "EntityKeyIndexStatus": "Pending"}]}
            return await send(method, path, *args, **kwargs)

        ds._send = pending_key
        with pytest.raises(RuntimeError, match="alternate key"):
            await DataverseJournal(ds).verify()
    asyncio.run(run())


def test_final_state_refreshes_completed_checkpoint_without_new_speech():
    async def run():
        dv = FakeDataverse()
        rec = recorder(mkstore(dv))
        st = call()
        st.status = "ended"
        await rec.flush(st)
        st.fields = {"summary": "final extraction"}
        st.ended_at = st.started_at + 20
        await rec.flush(st)
        saved = await rec.journal.get(st.call_id)
        assert saved["fields"] == st.fields and saved["ended_at"] == st.ended_at
        assert saved["version"] == 1
        assert not await rec.journal.pending()
        assert len([e for e in rec.hub._buf[st.call_id] if e["type"] == "transcript"]) == 1
        await rec.close()
    asyncio.run(run())


def test_recovery_does_not_depend_on_model_service():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        await DataverseJournal(ds).save(call().checkpoint())
        rec = recorder(ds)

        async def unexpected_model(*args):
            pytest.fail("startup recovery must not call a model")

        rec.extractor.finalize = unexpected_model
        await rec.recover()
        assert "Hydraulic press" in dv.draft["bdm_transcript"]
        await rec.close()
    asyncio.run(run())


def test_gateway_ingestion_checkpoints_before_transcript_events():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        app = create_app(Settings(local_dev=True, live_notes=False), drafts_store=ds)
        async with app.router.lifespan_context(app):
            gw = app.state.gateway
            st = app.state.store.get_or_create("real-ingestion")
            gw._add_line(st, "customer", "A new fault")
            assert not app.state.hub._buf.get(st.call_id)
            await gw.recorder.flush(st)
            assert (await gw.recorder.journal.get(st.call_id))["transcript"] == st.transcript
            assert app.state.hub._buf[st.call_id][0]["type"] == "transcript"
            assert dv.draft["bdm_transcript"] == "[customer] A new fault"
    asyncio.run(run())


def test_finalization_survives_checkpoint_timeout_and_persists_after_recovery():
    async def run():
        ds = mkstore(FakeDataverse())
        app = create_app(Settings(local_dev=True, live_notes=False), drafts_store=ds)
        gw = app.state.gateway
        rec = gw.recorder
        st = app.state.store.get_or_create("finalization-timeout")
        st.add_line("caller", "Last detail")
        release = asyncio.Event()
        save = rec.journal.save
        sleep, wait_for = asyncio.sleep, asyncio.wait_for

        async def stalled(data, complete=False):
            await release.wait()
            await save(data, complete=complete)

        async def finalize(state):
            await sleep(0)
            state.fields = {"summary": "Final summary"}

        async def fast_sleep(delay):
            await sleep(0 if delay == 4 else delay)

        async def short_flush(awaitable, timeout):
            return await wait_for(awaitable, timeout=0.01 if timeout == 30 else timeout)

        rec.journal.save = stalled
        gw.extractor.finalize = AsyncMock(side_effect=finalize)
        with patch("app.bridge.asyncio.sleep", fast_sleep), patch("app.journal.asyncio.wait_for", short_flush):
            await wait_for(gw._maybe_finalize(st), 1)
            assert st.finalized and st.persistence_error
            gw.extractor.finalize.assert_awaited_once_with(st)
            events = app.state.hub._buf[st.call_id]
            ended = [e for e in events if e["type"] == "call.ended"]
            assert len(ended) == 1 and ended[0]["data"]["persistenceError"]
            assert ended[0]["data"]["checkpointVersion"] == 0
            assert any(e["type"] == "error" and e["data"]["stage"] == "checkpoint" for e in events)
            await gw._maybe_finalize(st)
            assert gw.extractor.finalize.await_count == 1
            assert len([e for e in events if e["type"] == "call.ended"]) == 1
            task = rec.tasks[st.call_id]
            release.set()
            await wait_for(task, 1)
        saved = await rec.journal.get(st.call_id)
        assert saved["fields"] == {"summary": "Final summary"}
        assert saved["status"] == "ended" and saved["ended_at"] == st.ended_at
        assert not st.persistence_error and st.checkpoint_version == 1
        assert not await rec.journal.pending()
        await rec.close()
    asyncio.run(run())


def test_permanent_checkpoint_failure_does_not_skip_final_extraction_or_ended_event():
    async def run():
        ds = mkstore(FakeDataverse())
        app = create_app(Settings(local_dev=True, live_notes=False), drafts_store=ds)
        gw = app.state.gateway
        st = app.state.store.get_or_create("finalization-denied")
        st.add_line("caller", "Last detail")
        ds._tokens.get = AsyncMock(side_effect=ClientAuthenticationError("identity not authorized"))
        gw.extractor.finalize = AsyncMock()
        sleep = asyncio.sleep

        async def fast_sleep(delay):
            await sleep(0 if delay == 4 else delay)

        with patch("app.bridge.asyncio.sleep", fast_sleep):
            await gw._maybe_finalize(st)
        gw.extractor.finalize.assert_awaited_once_with(st)
        ended = [e for e in app.state.hub._buf[st.call_id] if e["type"] == "call.ended"]
        assert len(ended) == 1 and ended[0]["data"]["persistenceError"]
        assert st.persistence_error and st.finalized
        await gw.recorder.close()
    asyncio.run(run())


def test_real_simulator_uses_failure_safe_finalization():
    async def run():
        from app.simulator import run_real

        ds = mkstore(FakeDataverse())
        rec = recorder(ds)
        rec.flush = AsyncMock(side_effect=asyncio.TimeoutError())
        rec.extractor.finalize = AsyncMock()
        st = call()
        await run_real(st, rec.hub, rec.extractor, delay=0, recorder=rec)
        rec.extractor.finalize.assert_awaited_once_with(st)
        assert st.status == "ended" and st.finalized and st.ended_at is not None
        ended = [e for e in rec.hub._buf[st.call_id] if e["type"] == "call.ended"]
        assert len(ended) == 1 and ended[0]["data"]["persistenceError"]
        await rec.close()
    asyncio.run(run())


def test_shutdown_drains_other_workers_before_closing_client_and_aggregates_failures(monkeypatch):
    async def run():
        ds = mkstore(FakeDataverse())
        monkeypatch.setattr("app.main.DraftStore", lambda *_: ds)
        app = create_app(Settings(local_dev=True, live_notes=False, dataverse_url="https://org.crm.dynamics.com"),
                         tokens=ds._tokens)
        rec = app.state.gateway.recorder
        async def close_client():
            assert all(task.done() for task in tasks), "client closed before workers"

        ds.aclose = AsyncMock(side_effect=close_client)
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        started = set()
        all_started = asyncio.Event()
        fail, finish = asyncio.Event(), asyncio.Event()
        failures = [RuntimeError("first checkpoint failed"), ClientAuthenticationError("second checkpoint failed")]

        async def save(data, complete=False):
            started.add(data["call_id"])
            if len(started) == 3:
                all_started.set()
            if data["call_id"] in ("failed-0", "failed-1"):
                await fail.wait()
                raise failures[int(data["call_id"][-1])]
            await finish.wait()

        rec.journal.save = save
        ds.save_transcript = AsyncMock()
        tasks = [rec.record(CallState(identity, status="ended")) for identity in ("failed-0", "failed-1", "healthy")]

        await asyncio.wait_for(all_started.wait(), 1)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)
        fail.set()
        await asyncio.gather(*tasks[:2], return_exceptions=True)
        await asyncio.sleep(0)
        assert not shutdown.done() and not tasks[2].done()
        ds.aclose.assert_not_awaited()
        finish.set()
        with pytest.raises(ExceptionGroup) as error:
            await shutdown
        assert list(error.value.exceptions) == failures
        assert all(task.done() for task in tasks)
        ds.aclose.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("failed_peer", [False, True])
def test_shutdown_cancels_stalled_checkpoint_and_reports_failure(failed_peer):
    async def run():
        ds = mkstore(FakeDataverse())
        rec = recorder(ds)
        started = asyncio.Event()
        fail, cleaned_up = asyncio.Event(), asyncio.Event()

        async def stalled(data, **kwargs):
            if data["call_id"] == "failed-peer":
                await fail.wait()
                raise RuntimeError("another checkpoint failed")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned_up.set()

        rec.journal.save = stalled
        task = rec.record(call())
        await started.wait()
        if failed_peer:
            rec.record(CallState("failed-peer"))
            asyncio.get_running_loop().call_soon(fail.set)
        # Exercise the shutdown timeout path without waiting ten real seconds.
        original = asyncio.wait_for

        async def immediate_timeout(awaitable, timeout):
            return await original(awaitable, timeout=0.01)

        with patch("app.journal.asyncio.wait_for", immediate_timeout):
            with pytest.raises(asyncio.TimeoutError):
                await rec.close()
        assert task.cancelled()
        assert cleaned_up.is_set()
    asyncio.run(run())
