import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from azure.core.credentials import AccessToken

from app import transcribe
from app.config import Settings
from app.state import CallState


class Signal:
    def connect(self, callback):
        self.callback = callback

    def emit(self, event):
        self.callback(event)


class FakeSpeech:
    ResultReason = SimpleNamespace(RecognizedSpeech="speech")
    CancellationReason = SimpleNamespace(EndOfStream="end")
    PropertyId = SimpleNamespace(Speech_SegmentationSilenceTimeoutMs="silence")

    def __init__(self):
        self.pushes = []
        self.recognizers = []
        self.configs = []
        self.start = lambda rec: None
        self.recognizer_error = None
        self.audio = SimpleNamespace(
            AudioStreamFormat=Mock(), PushAudioInputStream=self.push,
            AudioConfig=Mock(),
        )

    def SpeechConfig(self, **kwargs):
        self.configs.append(kwargs)
        return Mock()

    def push(self, *_):
        push = Mock()
        self.pushes.append(push)
        return push

    def SpeechRecognizer(self, **_):
        if self.recognizer_error:
            raise self.recognizer_error
        rec = SimpleNamespace(
            recognized=Signal(), canceled=Signal(),
            start_continuous_recognition=Mock(), stop_continuous_recognition=Mock(),
            authorization_token=None,
        )
        rec.start_continuous_recognition.side_effect = lambda: self.start(rec)
        self.recognizers.append(rec)
        return rec


def make_speech(monkeypatch, *, aad=False, credential=None):
    sdk = FakeSpeech()
    sink = Mock()
    if aad:
        monkeypatch.setattr(transcribe, "sync_credential", Mock(return_value=credential))
    speech = transcribe.SpeechTranscriber(Settings(
        speech_auth="aad" if aad else "key", speech_key="key",
        speech_region="region", speech_resource_id="resource",
    ), sink)
    speech._sdk = sdk
    return speech, sdk, sink


def call(call_id="call"):
    st = CallState(call_id)
    st.transfer_started = True
    return st


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


def recognized(text="hello"):
    return SimpleNamespace(result=SimpleNamespace(reason="speech", text=text))


def canceled():
    return SimpleNamespace(cancellation_details=SimpleNamespace(
        reason="error", error_code="connection", error_details="disconnected",
    ))


@pytest.mark.parametrize("action", ["cancel", "close", "shutdown"])
def test_blocked_startup_retains_worker_cleanup_ownership(monkeypatch, action):
    async def run():
        speech, sdk, sink = make_speech(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        monkeypatch.setattr(transcribe, "CLOSE_TIMEOUT_S", 0.02)

        def start(_):
            entered.set()
            release.wait()

        sdk.start = start
        st = call()
        feeding = asyncio.create_task(speech.feed(st, "caller", b"\0" * 48))
        try:
            await eventually(entered.is_set)
            stream = speech._streams[st.call_id]["caller"]
            if action == "cancel":
                feeding.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await feeding
            else:
                with pytest.raises(ExceptionGroup, match="cleanup") as errors:
                    await (speech.close(st) if action == "close" else speech.aclose())
                assert "incomplete" in str(errors.value.exceptions[0])
                assert stream in speech._workers
            sdk.recognizers[0].recognized.emit(recognized())
            await asyncio.sleep(0)
            sink.assert_not_called()
        finally:
            release.set()
            if not feeding.cancelled():
                await feeding
            await eventually(lambda: stream.done.done())
            await speech.aclose()
        sdk.pushes[0].close.assert_called_once()
        sdk.recognizers[0].stop_continuous_recognition.assert_called_once()
        sdk.pushes[0].write.assert_not_called()
        if action != "cancel":
            await speech.feed(st, "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 1
    asyncio.run(run())


def test_startup_deadline_does_not_spawn_per_frame_workers(monkeypatch):
    async def run():
        speech, sdk, _ = make_speech(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        monkeypatch.setattr(transcribe, "START_TIMEOUT_S", 0.02)
        sdk.start = lambda _: (entered.set(), release.wait())
        try:
            await speech.feed(call(), "caller", b"\0" * 48)
            assert entered.is_set()
            for _ in range(30):
                await speech.feed(call(), "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 1
        finally:
            release.set()
            await speech.aclose()
        sdk.recognizers[0].stop_continuous_recognition.assert_called_once()
    asyncio.run(run())


def test_callbacks_recheck_closed_state_on_event_loop(monkeypatch):
    async def run():
        speech, sdk, sink = make_speech(monkeypatch)
        st = call()
        await speech.feed(st, "8:orgid:rep", b"\0" * 48)
        rec = sdk.recognizers[0]
        rec.recognized.emit(recognized("before"))
        await asyncio.sleep(0)
        sink.assert_called_once_with(st, "rep", "before")
        rec.recognized.emit(recognized("queued"))
        await speech.close(st)
        rec.recognized.emit(recognized("late"))
        await asyncio.sleep(0)
        assert sink.call_count == 1
        await speech.aclose()
    asyncio.run(run())


def test_aad_renews_public_recognizer_token_before_expiry(monkeypatch):
    async def run():
        now = time.time()
        credential = Mock()
        credential.get_token.side_effect = [
            AccessToken("first", now + 0.5), AccessToken("second", now + 3600),
        ]
        speech, sdk, _ = make_speech(monkeypatch, aad=True, credential=credential)
        try:
            await speech.feed(call(), "caller", b"\0" * 48)
            assert sdk.configs[0]["auth_token"] == "aad#resource#first"
            await eventually(lambda: sdk.recognizers[0].authorization_token == "aad#resource#second")
            assert time.time() < now + 0.5
            assert credential.get_token.call_count == 2
            credential.get_token.assert_called_with(transcribe.SPEECH_SCOPE)
        finally:
            await speech.aclose()
        credential.close.assert_called_once()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["start", "canceled", "renew", "write"])
def test_failures_recover_with_bounded_backoff_without_frame_retry_storm(monkeypatch, failure):
    async def run():
        clock = [0.0]
        monkeypatch.setattr(transcribe, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
        monkeypatch.setattr(transcribe, "RETRY_INITIAL_S", 1.0)
        monkeypatch.setattr(transcribe, "RETRY_MAX_S", 2.0)
        credential = Mock()
        credential.get_token.side_effect = [
            AccessToken("first", time.time() + 0.3), RuntimeError("token unavailable"),
            AccessToken("recovered", time.time() + 3600),
        ]
        speech, sdk, _ = make_speech(monkeypatch, aad=failure == "renew", credential=credential)
        if failure == "start":
            sdk.start = Mock(side_effect=RuntimeError("startup failed"))
        st = call()
        try:
            await speech.feed(st, "caller", b"\0" * 48)
            stream = speech._streams[st.call_id]["caller"]
            if failure == "canceled":
                sdk.recognizers[0].canceled.emit(canceled())
            elif failure == "write":
                sdk.pushes[0].write.side_effect = RuntimeError("broken stream")
                await speech.feed(st, "caller", b"\0" * 48)
            await eventually(lambda: stream.done.done())
            for _ in range(30):
                await speech.feed(st, "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 1
            clock[0] = 0.99
            await speech.feed(st, "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 1
            clock[0] = 1.0
            await speech.feed(st, "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 2
            if failure == "start":
                await eventually(lambda: speech._streams[st.call_id]["caller"].done.done())
                await speech.feed(st, "caller", b"\0" * 48)
                clock[0] = 2.99
                await speech.feed(st, "caller", b"\0" * 48)
                assert len(sdk.recognizers) == 2
                sdk.start = lambda _: None
                clock[0] = 3.0
                await speech.feed(st, "caller", b"\0" * 48)
                assert len(sdk.recognizers) == 3
            sdk.pushes[-1].write.assert_called_once()
        finally:
            await speech.aclose()
    asyncio.run(run())


def test_constructor_failure_closes_already_created_push_stream(monkeypatch):
    async def run():
        speech, sdk, _ = make_speech(monkeypatch)
        sdk.recognizer_error = RuntimeError("constructor failed")
        await speech.feed(call(), "caller", b"\0" * 48)
        await speech.aclose()
        sdk.pushes[0].close.assert_called_once()
    asyncio.run(run())


def test_shutdown_defers_credential_close_until_blocked_token_acquisition_returns(monkeypatch):
    async def run():
        entered, release = threading.Event(), threading.Event()
        credential = Mock()
        monkeypatch.setattr(transcribe, "CLOSE_TIMEOUT_S", 0.02)

        def get_token(_):
            entered.set()
            release.wait()
            return AccessToken("late", time.time() + 3600)

        credential.get_token.side_effect = get_token
        speech, sdk, _ = make_speech(monkeypatch, aad=True, credential=credential)
        feeding = asyncio.create_task(speech.feed(call(), "caller", b"\0" * 48))
        try:
            await eventually(entered.is_set)
            with pytest.raises(ExceptionGroup):
                await speech.aclose()
            credential.close.assert_not_called()
        finally:
            release.set()
            await feeding
            await eventually(lambda: speech._credential_cleanup.done())
            await speech.aclose()
        credential.close.assert_called_once()
        assert not sdk.recognizers
    asyncio.run(run())


def test_cleanup_failure_still_attempts_every_stream_and_credential(monkeypatch):
    async def run():
        credential = Mock()
        credential.get_token.return_value = AccessToken("token", time.time() + 3600)
        speech, sdk, _ = make_speech(monkeypatch, aad=True, credential=credential)
        for st, participant in [(call("first"), "caller"), (call("first"), "rep"), (call("second"), "caller")]:
            await speech.feed(st, participant, b"\0" * 48)
        sdk.pushes[0].close.side_effect = RuntimeError("push")
        sdk.recognizers[0].stop_continuous_recognition.side_effect = RuntimeError("stop")
        credential.close.side_effect = RuntimeError("credential")
        with pytest.raises(ExceptionGroup) as errors:
            await speech.aclose()
        assert {str(error) for error in errors.value.exceptions[0].exceptions} == {"push", "stop"}
        assert str(errors.value.exceptions[1]) == "credential"
        for push, rec in zip(sdk.pushes, sdk.recognizers):
            push.close.assert_called_once()
            rec.stop_continuous_recognition.assert_called_once()
        credential.close.assert_called_once()
    asyncio.run(run())


@pytest.mark.parametrize("cancel_close", [False, True])
@pytest.mark.parametrize("operation", ["close", "aclose"])
def test_slow_cleanup_does_not_block_other_streams_or_lose_ownership(monkeypatch, cancel_close, operation):
    async def run():
        speech, sdk, sink = make_speech(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        monkeypatch.setattr(transcribe, "CLOSE_TIMEOUT_S", 1.0 if cancel_close else 0.02)
        st = call()
        for participant in ["caller", "rep"]:
            await speech.feed(st, participant, b"\0" * 48)
        sdk.recognizers[0].stop_continuous_recognition.side_effect = lambda: (entered.set(), release.wait())
        closing = asyncio.create_task(speech.close(st) if operation == "close" else speech.aclose())
        try:
            await eventually(entered.is_set)
            if cancel_close:
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
            else:
                with pytest.raises(ExceptionGroup) as errors:
                    await closing
                assert "incomplete" in str(errors.value.exceptions[-1])
            await eventually(lambda: sdk.recognizers[1].stop_continuous_recognition.called)
            for rec in sdk.recognizers:
                rec.recognized.emit(recognized())
            await asyncio.sleep(0)
            sink.assert_not_called()
            assert speech._workers
        finally:
            release.set()
            await eventually(lambda: all(stream.done.done() for stream in speech._workers))
            await speech.aclose()
        assert not speech._workers
    asyncio.run(run())


def test_canceling_feed_during_startup_allows_later_bounded_recovery(monkeypatch):
    async def run():
        clock = [0.0]
        monkeypatch.setattr(transcribe, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
        speech, sdk, _ = make_speech(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        sdk.start = lambda _: (entered.set(), release.wait())
        st = call()
        feeding = asyncio.create_task(speech.feed(st, "caller", b"\0" * 48))
        try:
            await eventually(entered.is_set)
            stream = speech._streams[st.call_id]["caller"]
            feeding.cancel()
            with pytest.raises(asyncio.CancelledError):
                await feeding
            release.set()
            await eventually(lambda: stream.done.done())
            for _ in range(20):
                await speech.feed(st, "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 1
            sdk.start = lambda _: None
            clock[0] = transcribe.RETRY_INITIAL_S
            await speech.feed(st, "caller", b"\0" * 48)
            assert len(sdk.recognizers) == 2
            sdk.pushes[1].write.assert_called_once()
        finally:
            release.set()
            await speech.aclose()
    asyncio.run(run())


def test_retired_recognizer_cleanup_errors_survive_until_final_close(monkeypatch):
    async def run():
        speech, sdk, _ = make_speech(monkeypatch)
        st = call()
        await speech.feed(st, "caller", b"\0" * 48)
        stream = speech._streams[st.call_id]["caller"]
        error = RuntimeError("retired cleanup failure")
        sdk.pushes[0].close.side_effect = error
        sdk.recognizers[0].canceled.emit(canceled())
        await eventually(lambda: stream.done.done())
        await speech.feed(st, "caller", b"\0" * 48)
        with pytest.raises(ExceptionGroup) as errors:
            await speech.close(st)
        assert errors.value.exceptions == (error,)
        await speech.aclose()
    asyncio.run(run())
