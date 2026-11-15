import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import FunctionDef, Settings
from app.functions import FunctionRegistry
from app.state import CallState
from app.tasks import TaskSupervisor


def registry(client, *, asynchronous=True, tasks=None):
    return FunctionRegistry(Settings(local_dev=True, functions=[
        FunctionDef("lookup-name", "http://localhost/function", timeout_ms=1, is_async=asynchronous),
    ]), client, tasks)


def capture(reg):
    events = []
    reg.publish = lambda call, kind, data: events.append((kind, data))
    return events


def terminal(events):
    return [event for event in events if event[0] != "function.called"]


def test_timeout_without_callback_expires_and_cleans_future(monkeypatch):
    async def run():
        monkeypatch.setattr("app.functions.PENDING_TTL_S", 0.02)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client)
            events = capture(reg)
            st = CallState("call")
            assert await reg.invoke(st, "lookup-name", "sdk-1_a", {}) == {"status": "pending"}
            future = st.pending_functions["sdk-1_a"]
            await asyncio.sleep(0.04)
            assert not st.pending_functions
            assert future.result()["code"] == "expired"
            assert len(terminal(events)) == 1
            assert not reg.resolve(st, "sdk-1_a", {"ok": True})
            assert (await reg.invoke(st, "lookup-name", "sdk-1_a", {}))["code"] == "expired"
            await reg.aclose()
    asyncio.run(run())


def test_duplicate_ids_cannot_repeat_side_effects_and_are_scoped_to_call():
    async def run():
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: requests.append(req) or httpx.Response(200, json={"ok": True}))) as client:
            reg = registry(client, asynchronous=False)
            events = capture(reg)
            first, second = CallState("one"), CallState("two")
            result = await reg.invoke(first, "lookup-name", "same", {"a": 1})
            result["ok"] = False
            assert await reg.invoke(first, "lookup-name", "same", {"a": 1}) == {"ok": True}
            assert (await reg.invoke(first, "lookup-name", "same", {"a": 2}))["code"] == "invocation_conflict"
            assert await reg.invoke(second, "lookup-name", "same", {"a": 1}) == {"ok": True}
            assert len(requests) == 2 and len(terminal(events)) == 2
            await reg.aclose()
    asyncio.run(run())


def test_concurrent_builtin_duplicate_has_one_execution():
    async def run():
        async with httpx.AsyncClient() as client:
            reg = registry(client)
            entered, release = asyncio.Event(), asyncio.Event()
            handler = AsyncMock()

            async def perform(st, args):
                entered.set()
                await release.wait()
                return {"ok": True}

            handler.side_effect = perform
            reg.register_builtin("builtin", handler)
            st = CallState("call")
            task = asyncio.create_task(reg.invoke(st, "builtin", "id", {}))
            await entered.wait()
            assert await reg.invoke(st, "builtin", "id", {}) == {"status": "pending"}
            release.set()
            assert await task == {"ok": True}
            assert handler.await_count == 1
            await reg.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status", [302, 403, 503])
def test_rejected_webhook_cleans_pending_and_is_not_retried(status):
    async def run():
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: requests.append(req) or httpx.Response(status))) as client:
            reg = registry(client)
            events = capture(reg)
            late = AsyncMock()
            st = CallState("call")
            result = await reg.invoke(st, "lookup-name", "id", {}, late)
            assert result["code"] == "webhook_rejected"
            assert not st.pending_functions and not reg.resolve(st, "id", {"ok": True})
            assert await reg.invoke(st, "lookup-name", "id", {}, late) == result
            assert len(requests) == 1 and len(terminal(events)) == 1
            late.assert_not_called()
            await reg.aclose()
    asyncio.run(run())


def test_late_result_delivered_once_and_not_after_call_end():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client)
            events = capture(reg)
            late = AsyncMock()
            st = CallState("call")
            assert await reg.invoke(st, "lookup-name", "id", {}, late) == {"status": "pending"}
            assert reg.resolve(st, "id", {"ok": True})
            assert not reg.resolve(st, "id", {"ok": False})
            await asyncio.sleep(0)
            late.assert_awaited_once_with({"ok": True})
            assert len(terminal(events)) == 1
            assert await reg.invoke(st, "lookup-name", "id2", {}, late) == {"status": "pending"}
            reg.cancel_call(st)
            reg.cancel_call(st)
            assert not st.pending_functions and not reg.resolve(st, "id2", {"ok": True})
            assert (await reg.invoke(st, "lookup-name", "new", {}))["code"] == "call_ended"
            await asyncio.sleep(0)
            assert late.await_count == 1 and len(terminal(events)) == 2
            assert terminal(events)[-1][0] == "function.cancelled"
            await reg.aclose()
    asyncio.run(run())


def test_resolve_then_call_end_before_delivery_never_calls_closed_voice():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client)
            st, late = CallState("call"), AsyncMock()
            await reg.invoke(st, "lookup-name", "id", {}, late)
            assert reg.resolve(st, "id", {"ok": True})
            reg.cancel_call(st)
            await asyncio.sleep(0)
            late.assert_not_called()
            await reg.aclose()
    asyncio.run(run())


def test_shutdown_cancels_pending_and_preserves_borrowed_resources():
    async def run():
        shared = TaskSupervisor()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client, tasks=shared)
            st, late = CallState("call"), AsyncMock()
            events = capture(reg)
            await reg.invoke(st, "lookup-name", "id", {}, late)
            await reg.aclose()
            await reg.aclose()
            assert not st.pending_functions and not reg._background and not reg._invocations
            assert not client.is_closed and not shared.closing
            assert terminal(events)[0][0] == "function.cancelled"
            assert not reg.resolve(st, "id", {"ok": True})
            late.assert_not_called()
            await shared.close()
    asyncio.run(run())


def test_cancellation_of_inflight_work_is_terminal_and_cleans_pending():
    async def run():
        entered = asyncio.Event()

        async def send(req):
            entered.set()
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            reg = registry(client)
            events = capture(reg)
            st = CallState("call")
            task = asyncio.create_task(reg.invoke(st, "lookup-name", "id", {}))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not st.pending_functions
            assert len(terminal(events)) == 1
            assert (await reg.invoke(st, "lookup-name", "id", {}))["code"] == "cancelled"
            await reg.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_response_loss_is_not_retried_or_reported_as_success(asynchronous):
    async def run():
        requests = []

        def send(req):
            requests.append(req)
            raise httpx.ReadTimeout("response lost", request=req)

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            reg = registry(client, asynchronous=asynchronous)
            st = CallState("call")
            result = await reg.invoke(st, "lookup-name", "id", {})
            if asynchronous:
                assert result == {"status": "pending"}
                assert reg.resolve(st, "id", {"ok": True})
            else:
                assert result["code"] == "outcome_unknown" and result["uncertain"]
            await reg.invoke(st, "lookup-name", "id", {})
            assert len(requests) == 1
            await reg.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("name,identity,args,code", [
    ("bad/name", "id", {}, "invalid_identity"),
    ("lookup-name", "x" * 129, {}, "invalid_identity"),
    ("lookup-name", "é", {}, "invalid_identity"),
    ("lookup-name", "id", [], "invalid_arguments"),
    ("lookup-name", "id", {"x": float("nan")}, "invalid_arguments"),
    ("lookup-name", "id", {1: "x"}, "invalid_arguments"),
    ("lookup-name", "id", {"x": (1,)}, "invalid_arguments"),
    ("lookup-name", "id", {"x": "x" * 262144}, "invalid_arguments"),
])
def test_invalid_invocation_permanently_rejected_before_io(name, identity, args, code):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: pytest.fail("invalid invocation performed network IO"))) as client:
            reg = registry(client)
            result = await reg.invoke(CallState("call"), name, identity, args)
            assert result["code"] == code and result["retryable"] is False
            await reg.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("body", [b'[]', b'{"value":NaN}', b'not-json', b'{"x":"' + b'x' * 262144 + b'"}'],
                         ids=["array", "nan", "malformed", "oversized"])
def test_webhook_results_require_bounded_strict_json_objects(body):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
            reg = registry(client, asynchronous=False)
            assert (await reg.invoke(CallState("call"), "lookup-name", "id", {}))["code"] == "invalid_result"
            await reg.aclose()
    asyncio.run(run())


def test_invalid_callback_is_terminal_and_repeated_result_is_false():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client)
            st, events = CallState("call"), capture(reg)
            await reg.invoke(st, "lookup-name", "id", {})
            assert not reg.resolve(st, "id", {"x": object()})
            assert not reg.resolve(st, "id", {"ok": True})
            assert not st.pending_functions and len(terminal(events)) == 1
            assert (await reg.invoke(st, "lookup-name", "id", {}))["code"] == "invalid_result"
            await reg.aclose()
    asyncio.run(run())


def test_limits_fail_closed_without_evicting_side_effect_identity(monkeypatch):
    async def run():
        monkeypatch.setattr("app.functions.MAX_CALL_INVOCATIONS", 1)
        monkeypatch.setattr("app.functions.MAX_INVOCATIONS", 2)
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: requests.append(req) or httpx.Response(200, json={"ok": True}))) as client:
            reg = registry(client, asynchronous=False)
            one, two, three = CallState("one"), CallState("two"), CallState("three")
            await reg.invoke(one, "lookup-name", "first", {})
            assert (await reg.invoke(one, "lookup-name", "second", {}))["code"] == "capacity"
            await reg.invoke(two, "lookup-name", "first", {})
            assert (await reg.invoke(three, "lookup-name", "first", {}))["code"] == "capacity"
            assert await reg.invoke(one, "lookup-name", "first", {}) == {"ok": True}
            assert len(requests) == 2
            reg.cancel_call(one)
            assert await reg.invoke(three, "lookup-name", "first", {}) == {"ok": True}
            await reg.aclose()
    asyncio.run(run())


def test_expiry_delivers_one_terminal_error_and_blocks_later_result(monkeypatch):
    async def run():
        monkeypatch.setattr("app.functions.PENDING_TTL_S", 0.02)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client)
            st, late, events = CallState("call"), AsyncMock(), capture(reg)
            await reg.invoke(st, "lookup-name", "id", {}, late)
            await asyncio.sleep(0.04)
            late.assert_awaited_once()
            assert late.await_args.args[0]["code"] == "expired"
            assert not reg.resolve(st, "id", {"ok": True})
            assert len(terminal(events)) == 1
            await reg.aclose()
    asyncio.run(run())


def test_background_capacity_is_reserved_before_post_and_released_after_delivery(monkeypatch):
    async def run():
        monkeypatch.setattr("app.functions.MAX_BACKGROUND_TASKS", 1)
        requests = []
        release = asyncio.Event()

        async def late(result):
            await release.wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: requests.append(req) or httpx.Response(204))) as client:
            reg = registry(client)
            st = CallState("call")
            await reg.invoke(st, "lookup-name", "first", {}, late)
            assert (await reg.invoke(st, "lookup-name", "second", {}, late))["code"] == "capacity"
            assert reg.resolve(st, "first", {"ok": True})
            assert (await reg.invoke(st, "lookup-name", "second", {}, late))["code"] == "capacity"
            assert len(reg._background) == 1 and len(requests) == 1
            release.set()
            await asyncio.gather(*reg._background)
            assert await reg.invoke(st, "lookup-name", "second", {}, late) == {"status": "pending"}
            assert len(requests) == 2
            await reg.aclose()
    asyncio.run(run())


def test_ended_state_and_externally_cancelled_future_cannot_accept_callback():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            reg = registry(client)
            st, late = CallState("call"), AsyncMock()
            await reg.invoke(st, "lookup-name", "id", {}, late)
            st.pending_functions["id"].cancel()
            assert not reg.resolve(st, "id", {"ok": True})
            assert not st.pending_functions
            await reg.invoke(st, "lookup-name", "second", {}, late)
            st.status = "ended"
            assert not reg.resolve(st, "second", {"ok": True})
            await asyncio.sleep(0)
            late.assert_not_called()
            await reg.aclose()
    asyncio.run(run())


def test_json_depth_unknown_function_and_builtin_failure_are_explicit():
    async def run():
        async with httpx.AsyncClient() as client:
            reg = registry(client)
            events, st = capture(reg), CallState("call")
            deep = {}
            for _ in range(34):
                deep = {"nested": deep}
            assert (await reg.invoke(st, "unknown", "bad", deep))["code"] == "invalid_arguments"
            assert (await reg.invoke(st, "unknown", "unknown", {}))["code"] == "unknown_function"
            reg.register_builtin("failing", AsyncMock(side_effect=RuntimeError("boom")))
            assert (await reg.invoke(st, "failing", "builtin", {}))["code"] == "function_failed"
            assert len(terminal(events)) == 2
            await reg.aclose()
    asyncio.run(run())


def test_owned_http_client_and_supervisor_close():
    async def run():
        reg = FunctionRegistry(Settings())
        await reg.aclose()
        assert reg._http.is_closed and reg.tasks.closing
    asyncio.run(run())
