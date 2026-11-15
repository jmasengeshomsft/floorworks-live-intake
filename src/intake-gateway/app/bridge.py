"""ACS media WebSocket <-> Voice Live WebSocket bridge, plus transfer / hangup actions."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from . import afterhours, audio
from .acs import AcsClient
from .config import Settings
from .extractor import Extractor, TokenSource
from .functions import FunctionRegistry
from .limits import CallLimits
from .journal import TranscriptRecorder, finalize_notes
from .state import CallState, CallStore, EventHub, TranscriptLimit
from .transcribe import REP_PREFIX, build_transcriber

log = logging.getLogger("gateway.bridge")
MAX_RECONNECTS = 20
MAX_MEDIA_MESSAGE = 1048576
MAX_AUDIO_REFERENCE = 192000
VOICE_CONNECT_ATTEMPTS = 3
VOICE_RECONNECTS = 3
VOICE_RETRY_DELAY_S = 1.0


class Gateway:
    def __init__(
        self,
        settings: Settings,
        store: CallStore,
        hub: EventHub,
        extractor: Extractor,
        functions: FunctionRegistry,
        acs: AcsClient | None,
        tokens: TokenSource | None,
        transcriber_factory=None,
    ):
        self.s = settings
        self.store = store
        self.hub = hub
        self.extractor = extractor
        self.functions = functions
        self.acs = acs
        self.tokens = tokens
        self.tasks = hub.tasks
        self.draining = False
        self.recorder: TranscriptRecorder | None = None
        factory = transcriber_factory or build_transcriber
        self.transcriber = factory(settings, self._add_line)
        self.limits = CallLimits(settings.max_concurrent_calls, settings.max_call_duration_s, settings.idle_timeout_s)
        functions.publish = hub.publish
        functions.register_builtin("transfer_to_rep", lambda st, a: self.transfer(st))
        functions.register_builtin("hangup", lambda st, a: self.hangup(st))

    # ---------- actions (shared by REST and agent functions) ----------

    async def transfer(self, st: CallState) -> dict:
        if self.draining:
            return {"status": "unavailable", "reason": "draining"}
        if st.after_hours:
            return {"status": "unavailable", "reason": "after_hours"}
        if not self.s.transfer_teams_ids:
            return {"status": "unavailable", "reason": "no_transfer_targets"}
        if st.transfer_started or st.rep_joined:
            return {"status": "already_transferring"}
        if self.acs is None or not st.conn_id:
            return {"status": "unavailable", "reason": "call_not_connected"}
        st.transfer_started = True
        self.tasks.spawn(self._do_transfer(st), name="transfer")
        return {"status": "connecting"}

    async def _do_transfer(self, st: CallState) -> None:
        try:
            try:
                await asyncio.wait_for(st.response_done.wait(), 10)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1.5)
            st.muted = True
            if self.s.ringback:
                st.ringing = True
                self.tasks.spawn(self._ringback(st), name="ringback")
            st.mark("transfer_started")
            log.info("[%s] timeline: transfer started +%.1fs", st.call_id, st.marks["transfer_started"] - st.started_at)
            self.hub.publish(st.call_id, "transfer.started", {"targets": len(self.s.transfer_teams_ids)})
            await self.acs.add_teams_participants(st.conn_id, self.s.transfer_teams_ids)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            log.exception("[%s] transfer failed", st.call_id)
            st.muted = st.ringing = st.transfer_started = False
            self.hub.publish(st.call_id, "transfer.failed", {"message": str(e)})

    async def _ringback(self, st: CallState) -> None:
        cycle = audio.ring_cycle()
        end = asyncio.get_running_loop().time() + 120  # keep ringing until the rep joins; callers must not hear dead air
        i = 0
        while st.ringing and st.ws_send and asyncio.get_running_loop().time() < end:
            await st.ws_send(json.dumps({"kind": "AudioData", "audioData": {"data": audio.b64e(cycle[i % len(cycle)])}, "stopAudio": None}))
            i += 1
            await asyncio.sleep(audio.FRAME_MS / 1000)
        st.ringing = False

    async def hangup(self, st: CallState) -> dict:
        if self.draining:
            return {"status": "unavailable", "reason": "draining"}
        if self.acs is None or not st.conn_id:
            return {"status": "unavailable", "reason": "call_not_connected"}

        async def later() -> None:
            try:
                await asyncio.wait_for(st.response_done.wait(), 10)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1.0)
            await self.acs.hang_up(st.conn_id)  # type: ignore[union-attr]

        self.tasks.spawn(later(), name="hangup")
        return {"status": "ok"}

    # ---------- Voice Live ----------

    def _context_text(self, st: CallState, resume: bool) -> str:
        parts = [
            f"Call context: Call ID: {st.call_id}. Caller phone: {st.caller_phone or 'unknown'}. "
            "Include the call ID and phone in any record you create. "
            "Never guess or infer the caller's name from the phone or call metadata; use only a name the caller says aloud. "
            "Speak at most one short sentence at a time and never repeat the list of things you can look up. "
            "Speech recognition may mishear equipment names (for example 'mail' for 'mill'); confirm if unsure."
        ]
        if self.s.greeting_context:
            parts.append(self.s.greeting_context)
        if st.after_hours:
            parts.append(afterhours.note(self.s))
        if resume:
            last = "\n".join(f"{t['speaker']}: {t['text']}" for t in st.transcript[-12:])
            parts.append("The audio connection was re-established mid-call. Do NOT greet again; continue naturally.\n" + last)
        return "\n".join(parts)

    async def _connect_voice(self, st: CallState, resume: bool):
        tok = await self.tokens.get()  # type: ignore[union-attr]
        ep = self.s.foundry_project_endpoint.replace("https://", "wss://")
        url = f"{ep}/agents/{self.s.voice_agent}/endpoint/protocols/voice?api-version=v1"
        vl = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {tok}", "foundry-features": "VoiceAgents=V1Preview"},
            subprotocols=["realtime"],
            max_size=MAX_MEDIA_MESSAGE,
            max_queue=16,
        )

        async def send(obj: dict) -> None:
            await vl.send(json.dumps(obj))

        if self.s.voice_session_tuning:
            for sess in (
                {"input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"}},
                {"input_audio_echo_cancellation": {"type": "server_echo_cancellation"}},
                {"turn_detection": {"type": "azure_semantic_vad"}},
            ):
                await send({"type": "session.update", "session": sess})
        await send(
            {
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "system", "content": [{"type": "input_text", "text": self._context_text(st, resume)}]},
            }
        )
        if not resume:
            await send({"type": "response.create"})
        return vl, send

    async def _voice_loop(self, st: CallState, vl, send, ai_ref: bytearray) -> None:
        async def acs_send(obj: dict) -> None:
            if st.ws_send:
                await st.ws_send(json.dumps(obj))

        async def late(fn_call_id: str, result: dict) -> None:
            if st.vl_send is not send or st.finalized or self.draining:
                log.info("[%s] function result retained without delivery to a closed voice session", st.call_id)
                return
            await send({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": fn_call_id, "output": json.dumps(result)}})
            await send({"type": "response.create"})

        async def run_function(item: dict) -> None:
            fn_id = item.get("call_id", "")
            try:
                args = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            out = await self.functions.invoke(st, item.get("name", ""), fn_id, args, on_late=lambda r: late(fn_id, r))
            await late(fn_id, out)

        async for raw in vl:
            try:
                ev = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            t = ev.get("type", "")
            if t == "response.output_audio.delta":
                if st.mark("first_agent_audio"):
                    log.info("[%s] timeline: first agent audio +%.1fs", st.call_id, st.marks["first_agent_audio"] - st.started_at)
                if not st.muted:
                    pcm = audio.b64d(ev.get("delta", ""))
                    ai_ref.extend(pcm)
                    if len(ai_ref) > MAX_AUDIO_REFERENCE:
                        del ai_ref[:-MAX_AUDIO_REFERENCE]
                        log.warning("[%s] echo reference overflow; retaining latest audio", st.call_id)
                    await acs_send({"kind": "AudioData", "audioData": {"data": audio.b64e(pcm)}, "stopAudio": None})
            elif t == "input_audio_buffer.speech_started":
                ai_ref.clear()
                await acs_send({"kind": "StopAudio", "audioData": None, "stopAudio": {}})
            elif t == "conversation.item.input_audio_transcription.completed":
                text = (ev.get("transcript") or "").strip()
                if text:
                    self._add_line(st, "customer", text)
            elif t == "response.done":
                st.response_done.set()
            elif t == "error":
                log.warning("[%s] voice error: %s", st.call_id, ev.get("error"))
                self.hub.publish(st.call_id, "error", {"stage": "voice", "message": str(ev.get("error"))})
            elif t == "response.output_audio_transcript.done":
                text = (ev.get("transcript") or "").strip()
                if text:
                    self._add_line(st, "assistant", text)
            elif t == "response.output_item.done":
                item = ev.get("item", {})
                if item.get("type") == "function_call":
                    st.response_done.clear()
                    self.tasks.spawn(run_function(item), name="voice-function")

    def _add_line(self, st: CallState, speaker: str, text: str) -> None:
        try:
            line = st.add_line(speaker, text)
        except TranscriptLimit:
            if st.mark("transcript_limit"):
                log.warning("[%s] transcript capacity reached; ending call", st.call_id)
                self.hub.publish(st.call_id, "error", {"stage": "limits", "message": "transcript capacity reached; ending call"})
                if not self.draining:
                    self.tasks.spawn(self._hangup_now(st), name="transcript-limit")
            return
        if self.recorder is not None:
            self.recorder.record(st)
            return
        self.hub.publish(st.call_id, "transcript", {"speaker": speaker, "text": text, "ts": line["ts"]})
        self.extractor.trigger(st)

    # ---------- ACS media socket ----------

    async def _voice_up(self, st: CallState, resume: bool, ai_ref: bytearray):
        """Connect to Voice Live with retries; return (vl, voice_task) or None."""
        for attempt in range(VOICE_CONNECT_ATTEMPTS):
            try:
                vl, vl_send = await self._connect_voice(st, resume)
            except Exception:  # noqa: BLE001
                log.exception("[%s] voice connect failed (attempt %d/%d)", st.call_id, attempt + 1, VOICE_CONNECT_ATTEMPTS)
                if attempt + 1 < VOICE_CONNECT_ATTEMPTS:
                    await asyncio.sleep(VOICE_RETRY_DELAY_S * (attempt + 1))
                continue
            st.vl_send = vl_send
            task = self.tasks.spawn(self._voice_loop(st, vl, vl_send, ai_ref), name="voice-session")
            return vl, task
        return None

    async def _hangup_now(self, st: CallState) -> None:
        if self.acs is None or not st.conn_id:
            return
        try:
            await self.acs.hang_up(st.conn_id)
        except Exception:  # noqa: BLE001
            log.debug("[%s] hang up skipped (call already gone)", st.call_id)

    async def handle_ws(self, ws: WebSocket, call_id: str) -> None:
        st = self.store.get_or_create(call_id)
        # exactly one media socket per call; reject extras (each would open a billed Voice Live session)
        if st.finalized or st.connections >= MAX_RECONNECTS or st.active_ws > 0:
            await ws.close(code=1008)
            return
        # counted before the await so _maybe_finalize cannot run between the check and the increment
        st.active_ws += 1
        st.connections += 1
        resume = st.connections > 1
        send_lock = asyncio.Lock()
        vl = None
        voice_task = None
        try:
            await ws.accept()
            if not self.limits.acquire(call_id):  # no-op if registered at /api/incoming (also covers direct ws tests)
                log.warning("[%s] over capacity", call_id)
                await ws.close(code=1013)
                return
            st.status = "active"

            async def ws_send(text: str) -> None:
                async with send_lock:
                    await ws.send_text(text)

            st.ws_send = ws_send
            ai_ref = bytearray()
            up = await self._voice_up(st, resume, ai_ref)
            if up is None:
                self.hub.publish(call_id, "error", {"stage": "voice", "message": "voice connect failed"})
                await self._hangup_now(st)
                return
            vl, voice_task = up
            reconnects = 0
            while True:
                if voice_task.done():
                    voice_task.cancel()
                    st.vl_send = None
                    if vl:
                        await vl.close()
                    reconnects += 1
                    up = await self._voice_up(st, True, ai_ref) if reconnects <= VOICE_RECONNECTS else None
                    if up is None:
                        self.hub.publish(call_id, "error", {"stage": "voice", "message": "voice connection lost"})
                        await self._hangup_now(st)
                        break
                    vl, voice_task = up
                reason = self.limits.expired(call_id)
                if reason:
                    log.warning("[%s] dropping call: %s", call_id, reason)
                    self.hub.publish(call_id, "error", {"stage": "limits", "message": reason})
                    await self.hangup(st)
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive_text(), self.limits.receive_timeout)
                except asyncio.TimeoutError:
                    continue
                self.limits.touch(call_id)
                if len(msg.encode("utf-8")) > MAX_MEDIA_MESSAGE:
                    self.hub.publish(call_id, "error", {"stage": "limits", "message": "media message too large"})
                    await self._hangup_now(st)
                    await ws.close(code=1009)
                    break
                try:
                    m = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if m.get("kind") != "AudioData":
                    continue
                ad = m.get("audioData") or {}
                pid = ad.get("participantRawID") or ""
                if ad.get("silent") or not ad.get("data"):
                    # Speech needs the pauses to end an utterance; dropping silence delays every line.
                    if (st.transfer_started or st.rep_joined) and pid:
                        await self.transcriber.feed(st, pid, bytes(960))  # 20 ms of 24 kHz silence
                    continue
                if st.transfer_started or st.rep_joined:
                    await self.transcriber.feed(st, pid, audio.b64d(ad["data"]))
                if pid.startswith(REP_PREFIX):
                    continue  # rep audio is not sent to the voice agent
                if st.muted:
                    continue
                pcm = audio.b64d(ad["data"])
                ref = bytes(ai_ref[: len(pcm)])
                del ai_ref[: len(pcm)]
                try:
                    await st.vl_send({"type": "input_audio_buffer.append", "audio": audio.b64e(audio.interleave(pcm, ref))})
                except websockets.ConnectionClosed:
                    continue  # voice_task ends with the socket; the loop top reconnects
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("[%s] bridge error", call_id)
        finally:
            st.active_ws -= 1
            st.ws_send = st.vl_send = None
            if voice_task:
                voice_task.cancel()
                await asyncio.gather(voice_task, return_exceptions=True)
            if vl:
                await vl.close()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass  # already closed by the peer
            st.ws_send = st.vl_send = None
            if not self.draining:
                self.tasks.spawn(self._maybe_finalize(st), name="call-finalization")

    async def _maybe_finalize(self, st: CallState) -> None:
        await asyncio.sleep(4)  # ACS may reopen the socket mid-call
        if st.active_ws > 0 or st.finalized:
            return
        st.finalized = True  # set before any await so concurrent finalizers and reconnects see it
        self.functions.cancel_call(st)
        self.limits.release(st.call_id)
        st.status = "ended"
        st.ringing = False
        await self._hangup_now(st)  # never leave an orphaned live ACS call behind
        await self.transcriber.close(st)
        await asyncio.sleep(0)
        import time

        st.ended_at = time.time()
        log.info("timeline: [%s] ended +%.1fs marks=%s", st.call_id, st.ended_at - st.started_at,
                 {k: round(v - st.started_at, 1) for k, v in st.marks.items()})
        await finalize_notes(st, self.hub, self.extractor, self.recorder)
        log.info("[%s] timeline: finalization finished +%.1fs after hang-up", st.call_id, time.time() - st.ended_at)

    async def aclose(self) -> None:
        self.draining = True
        try:
            try:
                try:
                    await self.functions.aclose()
                finally:
                    await self.tasks.close()
            finally:
                self.hub.discard_deliveries()
                await self.transcriber.aclose()
        finally:
            import time

            for st in self.store.list():
                for future in st.pending_functions.values():
                    future.cancel()
                st.pending_functions.clear()
                st.ws_send = st.vl_send = None
                st.ringing = False
                if st.status != "ended":
                    st.status, st.finalized = "ended", True
                    st.ended_at = time.time()
                if self.recorder is not None and not self.recorder.closing:
                    self.recorder.record(st)
