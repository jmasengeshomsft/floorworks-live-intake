"""Pluggable live-notes transcription for the part of the call the voice agent no longer hears.

After a transfer the voice agent is muted, so caller and rep speech would be lost. A transcriber
receives raw per-participant audio and emits transcript lines. Select with LIVE_NOTES=on|off.
Implement the `Transcriber` protocol to plug in another STT engine.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Protocol

from . import audio
from .config import Settings
from .state import CallState

log = logging.getLogger("gateway.transcribe")

REP_PREFIX = "8:orgid:"
LineSink = Callable[[CallState, str, str], None]  # (state, speaker, text)


class Transcriber(Protocol):
    name: str

    async def feed(self, st: CallState, participant: str, pcm24: bytes) -> None: ...

    async def close(self, st: CallState) -> None: ...


class NullTranscriber:
    name = "off"

    async def feed(self, st: CallState, participant: str, pcm24: bytes) -> None:
        return

    async def close(self, st: CallState) -> None:
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
            from azure.identity import AzureCliCredential, DefaultAzureCredential

            self._cred = AzureCliCredential() if settings.local_dev else DefaultAzureCredential()
        self._streams: dict[str, dict[str, tuple]] = {}
        self._starting: set[tuple[str, str]] = set()

    def _config(self, settings: Settings):
        sdk = self._sdk
        if settings.speech_auth == "aad":
            token = self._cred.get_token("https://cognitiveservices.azure.com/.default").token
            cfg = sdk.SpeechConfig(auth_token=f"aad#{settings.speech_resource_id}#{token}", region=settings.speech_region)
        else:
            cfg = sdk.SpeechConfig(subscription=settings.speech_key, region=settings.speech_region)
        cfg.speech_recognition_language = settings.speech_language
        cfg.set_property(self._sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "600")  # finalize sooner after a pause
        return cfg

    def _start(self, st: CallState, participant: str, loop: asyncio.AbstractEventLoop):
        sdk = self._sdk
        cfg = self._config(self.s)
        push = sdk.audio.PushAudioInputStream(sdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1))
        rec = sdk.SpeechRecognizer(speech_config=cfg, audio_config=sdk.audio.AudioConfig(stream=push))
        speaker = "rep" if participant.startswith(REP_PREFIX) else "customer"

        def recognized(evt) -> None:
            if evt.result.reason == sdk.ResultReason.RecognizedSpeech and evt.result.text:
                loop.call_soon_threadsafe(self.sink, st, speaker, evt.result.text)

        rec.recognized.connect(recognized)
        def canceled(e) -> None:
            d = e.cancellation_details
            lvl = logging.INFO if d.reason == sdk.CancellationReason.EndOfStream else logging.WARNING
            log.log(lvl, "[%s] STT canceled for %s: reason=%s code=%s %s", st.call_id, speaker, d.reason, d.error_code, d.error_details)

        rec.canceled.connect(canceled)
        rec.start_continuous_recognition()
        log.info("[%s] live notes started for %s", st.call_id, speaker)
        return push, rec

    async def feed(self, st: CallState, participant: str, pcm24: bytes) -> None:
        if not (st.rep_joined or st.transfer_started):  # live notes begin at transfer
            return
        streams = self._streams.setdefault(st.call_id, {})
        key = (st.call_id, participant)
        if participant not in streams:
            if key in self._starting:
                return
            self._starting.add(key)
            try:
                streams[participant] = await asyncio.to_thread(self._start, st, participant, asyncio.get_running_loop())
            except Exception:  # noqa: BLE001
                log.exception("[%s] could not start live notes", st.call_id)
                return
            finally:
                self._starting.discard(key)
        streams[participant][0].write(audio.resample_24_to_16(pcm24))

    async def close(self, st: CallState) -> None:
        for push, rec in self._streams.pop(st.call_id, {}).values():
            push.close()
            await asyncio.to_thread(rec.stop_continuous_recognition)


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
