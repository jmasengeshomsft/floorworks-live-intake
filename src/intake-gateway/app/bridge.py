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
from .state import CallState, CallStore, EventHub
from .transcribe import REP_PREFIX, build_transcriber

log = logging.getLogger("gateway.bridge")


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
        factory = transcriber_factory or build_transcriber
        self.transcriber = factory(settings, self._add_line)
        self.limits = CallLimits(settings.max_concurrent_calls, settings.max_call_duration_s, settings.idle_timeout_s)
        functions.publish = hub.publish
        functions.register_builtin("transfer_to_rep", lambda st, a: self.transfer(st))
        functions.register_builtin("hangup", lambda st, a: self.hangup(st))

    # ---------- actions (shared by REST and agent functions) ----------

    async def transfer(self, st: CallState) -> dict:
        if st.after_hours:
            return {"status": "unavailable", "reason": "after_hours"}
        if not self.s.transfer_teams_ids:
            return {"status": "unavailable", "reason": "no_transfer_targets"}
        if st.transfer_started or st.rep_joined:
            return {"status": "already_transferring"}
        if self.acs is None or not st.conn_id:
            return {"status": "unavailable", "reason": "call_not_connected"}
        st.transfer_started = True
        asyncio.get_running_loop().create_task(self._do_transfer(st))
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
                asyncio.get_running_loop().create_task(self._ringback(st))
            self.hub.publish(st.call_id, "transfer.started", {"targets": len(self.s.transfer_teams_ids)})
            await self.acs.add_teams_participants(st.conn_id, self.s.transfer_teams_ids)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            log.exception("[%s] transfer failed", st.call_id)
            st.muted = st.ringing = st.transfer_started = False
            self.hub.publish(st.call_id, "transfer.failed", {"message": str(e)})

    async def _ringback(self, st: CallState) -> None:
        cycle = audio.ring_cycle()
        end = asyncio.get_running_loop().time() + 45
        i = 0
        while st.ringing and st.ws_send and asyncio.get_running_loop().time() < end:
            await st.ws_send(json.dumps({"kind": "AudioData", "audioData": {"data": audio.b64e(cycle[i % len(cycle)])}, "stopAudio": None}))
            i += 1
            await asyncio.sleep(audio.FRAME_MS / 1000)
        st.ringing = False

    async def hangup(self, st: CallState) -> dict:
        if self.acs is None or not st.conn_id:
            return {"status": "unavailable", "reason": "call_not_connected"}

        async def later() -> None:
            try:
                await asyncio.wait_for(st.response_done.wait(), 10)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1.0)
            await self.acs.hang_up(st.conn_id)  # type: ignore[union-attr]

        asyncio.get_running_loop().create_task(later())
        return {"status": "ok"}

    # ---------- Voice Live ----------

    def _context_text(self, st: CallState, resume: bool) -> str:
        parts = [
            f"Call context: Call ID: {st.call_id}. Caller phone: {st.caller_phone or 'unknown'}. "
            "Include the call ID and phone in any record you create."
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
            max_size=None,
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
            await send({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": fn_call_id, "output": json.dumps(result)}})
            await send({"type": "response.create"})

        async def run_function(item: dict) -> None:
            fn_id = item.get("call_id", "")
            try:
                args = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = await self.functions.invoke(st, item.get("name", ""), fn_id, args, on_late=lambda r: late(fn_id, r))
            await late(fn_id, out)

        async for raw in vl:
            try:
                ev = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            t = ev.get("type", "")
            if t == "response.output_audio.delta":
                if not st.muted:
                    pcm = audio.b64d(ev.get("delta", ""))
                    ai_ref.extend(pcm)
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
                    asyncio.get_running_loop().create_task(run_function(item))

    def _add_line(self, st: CallState, speaker: str, text: str) -> None:
        line = st.add_line(speaker, text)
        self.hub.publish(st.call_id, "transcript", {"speaker": speaker, "text": text, "ts": line["ts"]})
        self.extractor.trigger(st)

    # ---------- ACS media socket ----------

    async def handle_ws(self, ws: WebSocket, call_id: str) -> None:
        await ws.accept()
        st = self.store.get_or_create(call_id)
        st.active_ws += 1
        st.connections += 1
        resume = st.connections > 1
        self.limits.acquire(call_id)  # no-op if registered at /api/incoming (also covers direct ws tests)
        st.status = "active"
        send_lock = asyncio.Lock()

        async def ws_send(text: str) -> None:
            async with send_lock:
                await ws.send_text(text)

        st.ws_send = ws_send
        ai_ref = bytearray()
        vl = None
        voice_task = None
        try:
            vl, vl_send = await self._connect_voice(st, resume)
            st.vl_send = vl_send
            voice_task = asyncio.get_running_loop().create_task(self._voice_loop(st, vl, vl_send, ai_ref))
            while True:
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
                try:
                    m = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if m.get("kind") != "AudioData":
                    continue
                ad = m.get("audioData") or {}
                if ad.get("silent") or not ad.get("data"):
                    continue
                pid = ad.get("participantRawID") or ""
                if st.transfer_started or st.rep_joined:
                    await self.transcriber.feed(st, pid, audio.b64d(ad["data"]))
                if pid.startswith(REP_PREFIX):
                    continue  # rep audio is not sent to the voice agent
                if st.muted:
                    continue
                pcm = audio.b64d(ad["data"])
                ref = bytes(ai_ref[: len(pcm)])
                del ai_ref[: len(pcm)]
                await vl_send({"type": "input_audio_buffer.append", "audio": audio.b64e(audio.interleave(pcm, ref))})
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("[%s] bridge error", call_id)
        finally:
            st.active_ws -= 1
            if voice_task:
                voice_task.cancel()
            if vl:
                await vl.close()
            asyncio.get_running_loop().create_task(self._maybe_finalize(st))

    async def _maybe_finalize(self, st: CallState) -> None:
        await asyncio.sleep(4)  # ACS may reopen the socket mid-call
        if st.active_ws > 0 or st.status == "ended":
            return
        self.limits.release(st.call_id)
        st.status = "ended"
        st.ringing = False
        await self.transcriber.close(st)
        import time

        st.ended_at = time.time()
        await self.extractor.finalize(st)
        self.hub.publish(st.call_id, "call.ended", {"fields": st.fields, "enrichment": st.enrichment, "transcript": st.transcript})
