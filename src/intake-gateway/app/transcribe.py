"""Pluggable live-notes transcription for the part of the call the voice agent no longer hears.

After a transfer the voice agent is muted, so caller and rep speech would be lost. A transcriber
receives raw per-participant audio and emits transcript lines. Select with LIVE_NOTES=on|off.
Implement the `Transcriber` protocol to plug in another STT engine.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Callable, Protocol

from . import audio
from .config import Settings
from .state import CallState
from .identity import sync_credential

log = logging.getLogger("gateway.transcribe")

REP_PREFIX = "8:orgid:"
LineSink = Callable[[CallState, str, str], None]  # (state, speaker, text)

START_TIMEOUT_S = 15.0
CLOSE_TIMEOUT_S = 5.0
RETRY_INITIAL_S = 0.5
RETRY_MAX_S = 30.0
TOKEN_REFRESH_MARGIN_S = 120.0
SPEECH_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass(eq=False)
class _SpeechStream:
    call_id: str = ""
    stopped: threading.Event = field(default_factory=threading.Event)
    ready: Future = field(default_factory=Future)
    done: Future = field(default_factory=Future)
    push: object = None
    rec: object = None


class Transcriber(Protocol):
    name: str

    async def feed(self, st: CallState, participant: str, pcm24: bytes) -> None: ...

    async def close(self, st: CallState) -> None: ...

    async def aclose(self) -> None: ...


class NullTranscriber:
    name = "off"

    async def feed(self, st: CallState, participant: str, pcm24: bytes) -> None:
        return

    async def close(self, st: CallState) -> None:
        return

    async def aclose(self) -> None:
        return


class SpeechTranscriber:
    """Azure Speech continuous recognition, one push stream per participant, active after transfer."""

    name = "azure-speech"

    def __init__(self, settings: Settings, sink: LineSink):
        import azure.cognitiveservices.speech as speechsdk  # optional dependency

        self._sdk = speechsdk
        self.s = settings
        self.sink = sink
        self._cred = None
        if settings.speech_auth == "aad":
            self._cred = sync_credential(settings)
        self._streams: dict[str, dict[str, _SpeechStream]] = {}
        self._workers: set[_SpeechStream] = set()
        self._retry: dict[tuple[str, str], tuple[int, float]] = {}
        self._cleanup_failures: dict[str, list[Exception]] = {}
        self._closed_calls: set[str] = set()
        self._closed = False
        self._credential_cleanup: Future | None = None

    def _config(self, settings: Settings):
        sdk = self._sdk
        expires_on = None
        if settings.speech_auth == "aad":
            token = self._cred.get_token(SPEECH_SCOPE)
            self._refresh_delay(token.expires_on)
            expires_on = token.expires_on
            cfg = sdk.SpeechConfig(auth_token=self._authorization_token(token), region=settings.speech_region)
        else:
            cfg = sdk.SpeechConfig(subscription=settings.speech_key, region=settings.speech_region)
        cfg.speech_recognition_language = settings.speech_language
        cfg.set_property(self._sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "600")  # finalize sooner after a pause
        return cfg, expires_on

    def _authorization_token(self, token) -> str:
        return f"aad#{self.s.speech_resource_id}#{token.token}"

    @staticmethod
    def _refresh_delay(expires_on: float) -> float:
        remaining = expires_on - time.time()
        if remaining <= 0.1:
            raise RuntimeError("Speech credential returned an expired or imminently expiring token")
        return max(0.05, remaining - TOKEN_REFRESH_MARGIN_S
                   if remaining > TOKEN_REFRESH_MARGIN_S else remaining / 2)

    def _start(self, stream: _SpeechStream, st: CallState, participant: str, loop: asyncio.AbstractEventLoop):
        """One daemon worker owns the entire SDK lifetime, even if its awaiter disappears."""
        speaker = "rep" if participant.startswith(REP_PREFIX) else "customer"
        failures: list[Exception] = []

        def deliver(text: str) -> None:
            # Recheck on the event loop: a callback can already be queued when close begins.
            if not stream.stopped.is_set() and not self._closed:
                self.sink(st, speaker, text)

        def recognized(evt) -> None:
            if (not stream.stopped.is_set()
                    and evt.result.reason == self._sdk.ResultReason.RecognizedSpeech and evt.result.text):
                try:
                    loop.call_soon_threadsafe(deliver, evt.result.text)
                except RuntimeError:
                    stream.stopped.set()  # The event loop has already shut down.

        def canceled(evt) -> None:
            stream.stopped.set()
            d = evt.cancellation_details
            lvl = logging.INFO if d.reason == self._sdk.CancellationReason.EndOfStream else logging.WARNING
            log.log(lvl, "[%s] STT canceled for %s: reason=%s code=%s %s",
                    st.call_id, speaker, d.reason, d.error_code, d.error_details)

        try:
            if stream.stopped.is_set():
                return
            sdk = self._sdk
            cfg, expires_on = self._config(self.s)
            if stream.stopped.is_set():
                return
            stream.push = sdk.audio.PushAudioInputStream(sdk.audio.AudioStreamFormat(
                samples_per_second=16000, bits_per_sample=16, channels=1))
            stream.rec = sdk.SpeechRecognizer(speech_config=cfg, audio_config=sdk.audio.AudioConfig(stream=stream.push))
            stream.rec.recognized.connect(recognized)
            stream.rec.canceled.connect(canceled)
            stream.rec.start_continuous_recognition()
            if stream.stopped.is_set():
                return
            stream.ready.set_result(True)
            log.info("[%s] live notes started for %s", st.call_id, speaker)
            while not stream.stopped.wait(None if expires_on is None else self._refresh_delay(expires_on)):
                token = self._cred.get_token(SPEECH_SCOPE)
                self._refresh_delay(token.expires_on)
                if stream.stopped.is_set():
                    break
                # SpeechConfig tokens are copied at construction; renew the recognizer itself.
                stream.rec.authorization_token = self._authorization_token(token)
                expires_on = token.expires_on
        except Exception:
            log.exception("[%s] live notes failed for %s", st.call_id, speaker)
        finally:
            stream.stopped.set()
            if not stream.ready.done():
                stream.ready.set_result(False)
            if stream.push is not None:
                try:
                    stream.push.close()
                except Exception as error:
                    failures.append(error)
            if stream.rec is not None:
                try:
                    stream.rec.stop_continuous_recognition()
                except Exception as error:
                    failures.append(error)
            if failures:
                log.error("[%s] Speech recognizer cleanup failed: %r", st.call_id, failures)
            stream.done.set_result(failures)

    def _retire(self, key: tuple[str, str], stream: _SpeechStream) -> None:
        self._workers.discard(stream)
        errors = stream.done.result()
        if errors:
            self._cleanup_failures.setdefault(key[0], []).extend(errors)
        attempts = self._retry.get(key, (0, 0.0))[0] + 1
        delay = min(RETRY_MAX_S, RETRY_INITIAL_S * 2 ** min(attempts - 1, 16))
        self._retry[key] = attempts, time.monotonic() + delay

    async def feed(self, st: CallState, participant: str, pcm24: bytes) -> None:
        if self._closed or st.call_id in self._closed_calls or not (st.rep_joined or st.transfer_started):
            return
        streams = self._streams.setdefault(st.call_id, {})
        key = (st.call_id, participant)
        stream = streams.get(participant)
        if stream is not None and stream.done.done():
            del streams[participant]
            self._retire(key, stream)
            stream = None
        if stream is None:
            if time.monotonic() < self._retry.get(key, (0, 0.0))[1]:
                return
            stream = _SpeechStream(call_id=st.call_id)
            streams[participant] = stream
            self._workers.add(stream)
            try:
                threading.Thread(target=self._start, args=(stream, st, participant, asyncio.get_running_loop()),
                                 daemon=True, name="speech-recognizer").start()
            except Exception:
                stream.stopped.set()
                stream.ready.set_result(False)
                stream.done.set_result([])
                log.exception("[%s] could not launch Speech worker", st.call_id)
        elif not stream.ready.done() or stream.stopped.is_set():
            return
        try:
            ready = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(stream.ready)), START_TIMEOUT_S)
        except asyncio.CancelledError:
            stream.stopped.set()
            raise
        except TimeoutError:
            stream.stopped.set()
            log.error("[%s] Speech startup deadline exceeded; worker retains cleanup ownership", st.call_id)
            return
        if ready and not stream.stopped.is_set() and not self._closed and st.call_id not in self._closed_calls:
            try:
                stream.push.write(audio.resample_24_to_16(pcm24))
            except Exception:
                stream.stopped.set()
                log.exception("[%s] could not write live notes audio", st.call_id)

    async def _drain(self, workers: list[_SpeechStream]) -> list[Exception]:
        for stream in workers:
            stream.stopped.set()
        futures = {asyncio.wrap_future(stream.done): stream for stream in workers}
        if not futures:
            return []
        done, pending = await asyncio.wait(futures, timeout=CLOSE_TIMEOUT_S)
        failures = [error for future in done for error in future.result()]
        for future in done:
            self._workers.discard(futures[future])
        if pending:
            failures.append(TimeoutError(
                f"Speech cleanup incomplete for {len(pending)} worker(s); background workers retain cleanup ownership"))
        return failures

    async def close(self, st: CallState) -> None:
        self._closed_calls.add(st.call_id)
        self._streams.pop(st.call_id, None)
        workers = [stream for stream in self._workers if stream.call_id == st.call_id]
        for key in [key for key in self._retry if key[0] == st.call_id]:
            del self._retry[key]
        failures = await self._drain(workers)
        failures[:0] = self._cleanup_failures.pop(st.call_id, [])
        if failures:
            raise ExceptionGroup("Speech recognizer cleanup failed", failures)

    async def aclose(self) -> None:
        self._closed = True
        self._streams.clear()
        self._retry.clear()
        workers = list(self._workers)
        for stream in workers:
            stream.stopped.set()
        # A credential must not be closed underneath a still-running get_token call.
        # This worker also survives cancellation/timeout of aclose without losing ownership.
        if self._credential_cleanup is None:
            self._credential_cleanup = Future()
            prior_failures = self._cleanup_failures
            self._cleanup_failures = {}

            def cleanup_credential():
                failures: list[Exception] = []
                by_call = prior_failures
                for stream in workers:
                    by_call.setdefault(stream.call_id, []).extend(stream.done.result())
                for errors in by_call.values():
                    if errors:
                        failures.append(ExceptionGroup("Speech recognizer cleanup failed", errors))
                try:
                    if self._cred is not None:
                        self._cred.close()
                except Exception as error:
                    failures.append(error)
                self._credential_cleanup.set_result(failures)

            threading.Thread(target=cleanup_credential, daemon=True, name="speech-credential-cleanup").start()
        future = asyncio.wrap_future(self._credential_cleanup)
        done, _ = await asyncio.wait([future], timeout=CLOSE_TIMEOUT_S)
        if done:
            failures = future.result()
            self._workers.clear()
        else:
            failures = [TimeoutError("Speech transcriber cleanup incomplete; background workers retain cleanup ownership")]
        if failures:
            raise ExceptionGroup("Speech transcriber cleanup failed", failures)


def build_transcriber(settings: Settings, sink: LineSink) -> Transcriber:
    if not settings.live_notes:
        return NullTranscriber()
    if settings.speech_auth == "aad":
        if not settings.speech_resource_id:
            log.warning("SPEECH_AUTH=aad but SPEECH_RESOURCE_ID is not set; live notes after transfer are disabled")
            return NullTranscriber()
    elif settings.speech_auth != "key" or not settings.speech_key:
        log.warning("LIVE_NOTES=on but no SPEECH_KEY (or SPEECH_AUTH=aad); live notes after transfer are disabled")
        return NullTranscriber()
    try:
        return SpeechTranscriber(settings, sink)
    except ImportError:
        log.warning("azure-cognitiveservices-speech is not installed; live notes after transfer are disabled")
        return NullTranscriber()
