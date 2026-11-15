import asyncio
import json
import os
from contextlib import nullcontext
from unittest.mock import AsyncMock, Mock, create_autospec, patch

import httpx
import pytest
from azure.communication.callautomation import MicrosoftTeamsUserIdentifier
from azure.communication.callautomation.aio import CallAutomationClient, CallConnectionClient
from azure.core.pipeline.transport import AsyncHttpTransport, HttpResponse
from azure.identity.aio import DefaultAzureCredential

from app.acs import AcsClient
from app.config import FunctionDef, Settings
from app.extractor import Extractor
from app.functions import FunctionRegistry
from app.http import request
from app.identity import TokenSource
from app.state import CallState, EventHub
from app.tasks import TaskSupervisor
from app.transcribe import SpeechTranscriber
from tests.test_drafts import FakeDataverse, call, mkstore
from tests.test_journal import recorder


def test_public_async_acs_calls_and_borrowed_client_ownership():
    async def run():
        sdk = create_autospec(CallAutomationClient, instance=True)
        connection = create_autospec(CallConnectionClient, instance=True)
        sdk.get_call_connection.return_value = connection
        client = AcsClient(Settings(public_base_url="https://gateway", gateway_secret="secret"), sdk)
        await client.answer("context", "call")
        args = sdk.answer_call.await_args
        assert args.args[0] == "context" and args.args[1].startswith("https://gateway/api/callbacks/call?t=")
        assert args.kwargs["media_streaming"].transport_url.startswith("wss://gateway/ws/call?t=")
        await client.add_teams_participants("connection", ["user"])
        target = connection.add_participant.await_args.kwargs["target_participant"]
        assert isinstance(target, MicrosoftTeamsUserIdentifier)
        await client.reject("context")
        sdk.reject_call.assert_awaited_once_with("context")
        await client.hang_up("connection")
        connection.hang_up.assert_awaited_once_with(is_for_everyone=True)
        await client.aclose()
        sdk.close.assert_not_awaited()
    asyncio.run(run())


def test_owned_acs_and_identity_clients_are_closed():
    async def run():
        sdk = create_autospec(CallAutomationClient, instance=True)
        credential = Mock()
        credential.close = AsyncMock()
        with patch("app.acs.CallAutomationClient.from_connection_string", return_value=sdk):
            client = AcsClient(Settings(acs_connection_string="test"))
            await client.aclose()
        sdk.close.assert_awaited_once()
        with patch("app.identity.async_credential", return_value=credential):
            async with TokenSource(Settings()):
                pass
        credential.close.assert_awaited_once()
        await TokenSource(Settings(), credential).aclose()
        assert credential.close.await_count == 1
        sdk.close.side_effect = RuntimeError("SDK close failed")
        with patch("app.acs.async_credential", return_value=credential), patch(
                "app.acs.CallAutomationClient", return_value=sdk) as factory:
            client = AcsClient(Settings(acs_endpoint="https://acs"))
            factory.assert_called_once_with("https://acs", credential)
            with pytest.raises(RuntimeError, match="SDK close failed"):
                await client.aclose()
        assert credential.close.await_count == 2
    asyncio.run(run())


def test_real_azure_credential_chain_503_retries_after_sdk_wrapping(monkeypatch):
    class Response(HttpResponse):
        def __init__(self, req, status, payload):
            super().__init__(req, None)
            self.status_code = status
            self.headers = {"Content-Type": "application/json"}
            self.content_type = "application/json"
            self.reason = "Result"
            self.payload = json.dumps(payload).encode()

        def body(self):
            return self.payload

    class Transport(AsyncHttpTransport):
        def __init__(self):
            self.calls = 0

        async def open(self):
            pass

        async def close(self):
            pass

        async def __aexit__(self, *_):
            await self.close()

        async def send(self, req, **kwargs):
            assert req.url.startswith("https://identity.invalid/")
            self.calls += 1
            if self.calls == 1:
                return Response(req, 503, {"error": "temporarily_unavailable"})
            return Response(req, 200, {"access_token": "fake", "expires_in": 3600, "resource": "https://org.crm.dynamics.com"})

    async def run():
        monkeypatch.setattr("app.journal.RETRY_DELAY_S", 0)
        transport = Transport()
        env = {"IDENTITY_ENDPOINT": "https://identity.invalid/token", "IDENTITY_HEADER": "test"}
        with patch.dict(os.environ, env, clear=True):
            async with DefaultAzureCredential(
                transport=transport, retry_total=0, exclude_environment_credential=True,
                exclude_workload_identity_credential=True, exclude_cli_credential=True,
                exclude_powershell_credential=True, exclude_developer_cli_credential=True,
                exclude_visual_studio_code_credential=True, exclude_shared_token_cache_credential=True,
                exclude_broker_credential=True,
            ) as credential:
                ds = mkstore(FakeDataverse())
                ds._tokens = TokenSource(Settings(), credential)
                rec = recorder(ds)
                st = call()
                await asyncio.wait_for(rec.flush(st), 2)
                assert transport.calls == 2 and st.checkpoint_version == 1 and not st.persistence_error
                assert rec.hub._buf[st.call_id][0]["data"]["stage"] == "checkpoint"
                await rec.close()
    asyncio.run(run())


def test_http_retry_after_permanent_failure_and_non_retry_safe_write():
    async def run():
        responses = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200)]
        calls = []

        def send(req):
            calls.append(req)
            return responses.pop(0)

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            response = await request(client, "GET", "https://example.invalid", retry_safe=True)
            assert response.status_code == 200 and len(calls) == 2
            responses.append(httpx.Response(403))
            assert (await request(client, "GET", "https://example.invalid", retry_safe=True)).status_code == 403
            assert len(calls) == 3
            responses.append(httpx.Response(503))
            assert (await request(client, "POST", "https://example.invalid")).status_code == 503
            assert len(calls) == 4
            responses.extend([httpx.Response(503, headers={"Retry-After": "Infinity"}), httpx.Response(200)])
            with patch("app.http.random.uniform", return_value=0):
                assert (await request(client, "GET", "https://example.invalid", retry_safe=True)).status_code == 200
            assert len(calls) == 6
    asyncio.run(run())


def test_http_overall_deadline_includes_retry_after():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "60"}))) as client:
            with pytest.raises(httpx.TimeoutException, match="deadline"):
                await request(client, "GET", "https://example.invalid", retry_safe=True, timeout_s=0.01)
    asyncio.run(run())


def test_extractor_reuses_borrowed_http_client_and_does_not_retry_forbidden():
    async def run():
        send = Mock(return_value=httpx.Response(403))
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            source = Mock()
            source.get = AsyncMock(return_value="t")
            extractor = Extractor(Settings(foundry_project_endpoint="https://foundry"), EventHub(), source, client)
            with pytest.raises(httpx.HTTPStatusError):
                await extractor._invoke_retry({}, 1)
            await extractor.aclose()
            assert send.call_count == 1 and not client.is_closed
    asyncio.run(run())


def test_supervised_cancellation_and_deferred_function_cleanup():
    async def run():
        tasks = TaskSupervisor()
        started = asyncio.Event()
        st = CallState("call")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as client:
            registry = FunctionRegistry(Settings(local_dev=True, functions=[
                FunctionDef("lookup", "http://localhost/function", timeout_ms=10000, is_async=True),
            ]), client, tasks)

            async def invocation():
                started.set()
                return await registry.invoke(st, "lookup", "fn", {})

            task = tasks.spawn(invocation())
            await started.wait()
            await asyncio.sleep(0)
            await tasks.close(timeout=0)
            assert task.cancelled() and not st.pending_functions and not tasks.pending
            await registry.aclose()
            assert not client.is_closed
    asyncio.run(run())


def test_function_registry_drains_owned_tasks_and_borrows_shared_resources():
    async def run():
        async with httpx.AsyncClient() as client:
            registry = FunctionRegistry(Settings(local_dev=True), client)
            release = asyncio.Event()
            task = registry.tasks.spawn(release.wait())
            closing = asyncio.create_task(registry.aclose())
            await asyncio.sleep(0)
            assert not closing.done()
            result = await registry.invoke(CallState("call"), "lookup", "fn", {})
            assert result == {"status": "error", "error": "application is draining"}
            release.set()
            await closing
            assert task.done() and not registry.tasks.pending and not client.is_closed
            shared = TaskSupervisor()
            borrowed = FunctionRegistry(Settings(local_dev=True), client, shared)
            await borrowed.aclose()
            assert not shared.closing and not client.is_closed
            await shared.close()
    asyncio.run(run())


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_speech_cleanup_attempts_every_stream_before_closing_credential(fail_cleanup, monkeypatch):
    import time
    from azure.core.credentials import AccessToken
    from tests.test_speech import call as speech_call, make_speech

    async def run():
        credential = Mock()
        credential.get_token.return_value = AccessToken("token", time.time() + 3600)
        speech, sdk, _ = make_speech(monkeypatch, aad=True, credential=credential)
        for call_id, participant in [("first", "caller"), ("first", "rep"), ("second", "caller")]:
            await speech.feed(speech_call(call_id), participant, b"\0" * 48)
        streams = list(zip(sdk.pushes, sdk.recognizers))
        push_error, stop_error, credential_error = [RuntimeError(message) for message in ("push", "stop", "credential")]
        if fail_cleanup:
            streams[0][0].close.side_effect = push_error
            streams[0][1].stop_continuous_recognition.side_effect = stop_error

        def close_credential():
            assert all(push.close.called and rec.stop_continuous_recognition.called for push, rec in streams)
            if fail_cleanup:
                raise credential_error

        speech._cred.close.side_effect = close_credential
        with pytest.raises(ExceptionGroup) if fail_cleanup else nullcontext() as error:
            await speech.aclose()
        assert not speech._streams
        for push, rec in streams:
            push.close.assert_called_once()
            rec.stop_continuous_recognition.assert_called_once()
        speech._cred.close.assert_called_once()
        if fail_cleanup:
            assert error.value.exceptions[0].exceptions == (push_error, stop_error)
            assert error.value.exceptions[1] is credential_error
    asyncio.run(run())


def test_extractor_trigger_does_not_leave_running_flag_when_spawn_is_rejected(monkeypatch):
    async def run():
        hub = EventHub()
        extractor = Extractor(Settings(extractor_agent="extractor"), hub, tokens=Mock())
        st = CallState("call")
        st.add_line("caller", "Please help")

        def reject(work, **kwargs):
            work.close()
            raise RuntimeError("spawn failed")

        monkeypatch.setattr(hub.tasks, "spawn", reject)
        with pytest.raises(RuntimeError, match="spawn failed"):
            extractor.trigger(st)
        assert not st.running
        await hub.tasks.close()
        extractor.trigger(st)
        assert not st.running
        await extractor.aclose()
    asyncio.run(run())
