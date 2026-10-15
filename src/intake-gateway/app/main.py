"""FastAPI app: /v1 consumer API + ACS/Event Grid infra endpoints."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from . import afterhours
from .auth import make_dependency
from .bridge import Gateway
from .config import Settings, load_settings
from .extractor import Extractor, TokenSource
from .functions import FunctionRegistry
from .security import AcsJwtVerifier, check_call_token, check_shared_secret, new_call_id, normalize_number
from .state import CallStore, EventHub, MemoryStore, sse_frame
from .validate import report
from .webhooks import deliver_event

logging.getLogger("gateway").setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")


def create_app(settings: Settings | None = None, store: CallStore | None = None, acs=None, tokens=None) -> FastAPI:
    s = settings or load_settings()
    def _check_config():
        errors, warnings = report(s)
        if errors and settings is None and not s.local_dev:
            raise RuntimeError("invalid configuration: " + "; ".join(errors))
        return errors, warnings

    cfg_errors, cfg_warnings = _check_config()
    store = store or MemoryStore()
    hub = EventHub()
    if tokens is None and s.foundry_project_endpoint:
        tokens = TokenSource(s.local_dev)
    if acs is None and s.acs_connection_string:
        from .acs import AcsClient

        acs = AcsClient(s)
    extractor = Extractor(s, hub, tokens)
    functions = FunctionRegistry(s)
    gw = Gateway(s, store, hub, extractor, functions, acs, tokens)
    acs_jwt = AcsJwtVerifier(s.acs_jwt_mode if s.local_dev else "enforce", s.acs_resource_id)
    require = not s.local_dev  # infra secrets are mandatory outside LOCAL_DEV

    if s.webhook_urls:

        async def fan_out(_cid: str, ev: dict) -> None:
            await deliver_event(s.webhook_urls, s.webhook_secret, ev, allow_insecure=s.local_dev)

        hub.listeners.append(fan_out)

    app = FastAPI(title="Intake Gateway", version="1.0")
    app.state.gateway = gw
    app.state.hub = hub
    app.state.store = store
    auth = Depends(make_dependency(s))

    if s.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if s.dev_simulator:

        @app.post("/v1/dev/simulate", dependencies=[auth])
        async def simulate():
            from . import simulator

            st = store.get_or_create(new_call_id())
            st.caller_phone = "+15550142"
            asyncio.get_running_loop().create_task(simulator.run(st, hub))
            return st.summary()

    def need(call_id: str):
        st = store.get(call_id)
        if st is None:
            raise HTTPException(404, "call not found")
        return st

    # ---------------- /v1 ----------------

    @app.get("/v1/calls", dependencies=[auth])
    async def list_calls():
        return [c.summary() for c in store.list()]

    @app.get("/v1/calls/{call_id}", dependencies=[auth])
    async def get_call(call_id: str):
        st = need(call_id)
        return {**st.summary(), "fields": st.fields, "enrichment": st.enrichment, "transcriptLines": len(st.transcript)}

    @app.get("/v1/calls/{call_id}/transcript", dependencies=[auth])
    async def get_transcript(call_id: str):
        return need(call_id).transcript

    @app.get("/v1/calls/{call_id}/extraction", dependencies=[auth])
    async def get_extraction(call_id: str):
        st = need(call_id)
        return {"fields": st.fields, "enrichment": st.enrichment, "final": st.status == "ended"}

    @app.get("/v1/calls/{call_id}/events", dependencies=[auth])
    async def events(call_id: str, request: Request):
        need(call_id)
        last = int(request.headers.get("last-event-id", "0") or 0)
        q = hub.subscribe(call_id, last)

        async def gen():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        if await request.is_disconnected():
                            break
                        continue
                    yield sse_frame(ev)
            finally:
                hub.unsubscribe(call_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/v1/calls/{call_id}/transfer", dependencies=[auth])
    async def transfer(call_id: str):
        return await gw.transfer(need(call_id))

    @app.post("/v1/calls/{call_id}/hangup", dependencies=[auth])
    async def hangup(call_id: str):
        return await gw.hangup(need(call_id))

    @app.post("/v1/calls/{call_id}/functions/{fn_call_id}/result", dependencies=[auth])
    async def function_result(call_id: str, fn_call_id: str, body: dict = Body(...)):
        if not functions.resolve(need(call_id), fn_call_id, body):
            raise HTTPException(404, "no pending function call with that id")
        return {"status": "accepted"}

    @app.get("/v1/functions", dependencies=[auth])
    async def list_functions():
        return functions.describe()

    @app.get("/v1/config", dependencies=[auth])
    async def get_config():
        return {
            "voiceAgent": s.voice_agent,
            "extractorAgent": s.extractor_agent,
            "enrichment": s.enrichment,
            "liveNotes": gw.transcriber.name,
            "afterHoursMode": s.after_hours,
            "afterHoursNow": afterhours.is_active(s),
            "transferTargets": len(s.transfer_teams_ids),
            "simulator": s.dev_simulator,
            "webhooks": len(s.webhook_urls),
            "speechAuth": s.speech_auth if gw.transcriber.name != "off" else None,
            "configWarnings": cfg_warnings,
            "schema": s.extraction_schema,
        }

    @app.get("/v1/stats", dependencies=[auth])
    async def stats():
        return {**gw.limits.stats(), "tracked": len(store.list())}

    # ---------------- infra (ACS / Event Grid) ----------------

    @app.get("/healthz")
    async def healthz():
        return {"status": "degraded" if cfg_errors else "ok", "errors": cfg_errors, "activeCalls": gw.limits.active}

    seen_events: dict[str, None] = {}

    @app.post("/api/incoming")
    async def incoming(request: Request):
        if not check_shared_secret(
            s.eventgrid_secret, request.query_params.get("s"), request.headers.get("aeg-sas-key"), require=require
        ):
            log.warning("incoming rejected: bad event grid secret")
            raise HTTPException(401, "unauthorized")
        try:
            events_ = await request.json()
        except ValueError as e:
            raise HTTPException(400, "invalid json") from e
        if not isinstance(events_, list):
            events_ = [events_]
        for ev in events_:
            if not isinstance(ev, dict):
                continue
            et = ev.get("eventType", "")
            data = ev.get("data") or {}
            log.info("event received type=%s", et)
            if et == "Microsoft.EventGrid.SubscriptionValidationEvent":
                return {"validationResponse": data.get("validationCode")}
            if et != "Microsoft.Communication.IncomingCall":
                continue
            eid = str(ev.get("id") or "")
            if eid and eid in seen_events:
                log.info("duplicate event ignored")
                continue
            try:
                await _handle_incoming(data)
            except Exception as e:  # noqa: BLE001
                # Not marked seen: a 5xx makes Event Grid redeliver, and the retry is processed again.
                log.exception("incoming call handling failed")
                raise HTTPException(500, "incoming call handling failed") from e
            if eid:
                seen_events[eid] = None
                while len(seen_events) > 1000:
                    seen_events.pop(next(iter(seen_events)))
        return JSONResponse({"status": "ok"})

    async def _handle_incoming(data: dict) -> None:
        if acs is None:
            log.error("incoming call but ACS not configured")
            return
        frm = data.get("from") or {}
        phone = (frm.get("phoneNumber") or {}).get("value") or frm.get("rawId", "")
        to = data.get("to") or {}
        called = (to.get("phoneNumber") or {}).get("value") or to.get("rawId", "")
        context = data.get("incomingCallContext")
        log.info("IncomingCall to=%s", called)
        if not context:
            log.warning("incoming event without incomingCallContext")
            return
        if s.line_numbers and normalize_number(called) not in {normalize_number(n) for n in s.line_numbers}:
            log.warning("incoming call for unlisted number ignored")
            return
        if gw.limits.at_capacity():
            log.warning("incoming call rejected: at capacity (%d)", gw.limits.max_concurrent)
            try:
                await acs.reject(context)
            except Exception:  # noqa: BLE001
                log.exception("reject failed")
            return
        call_id = new_call_id()
        gw.limits.acquire(call_id)
        st = store.get_or_create(call_id)
        st.caller_phone = phone
        st.after_hours = afterhours.is_active(s)
        hub.publish(call_id, "call.incoming", {"callerPhone": phone, "afterHours": st.after_hours})
        try:
            await acs.answer(context, call_id)
        except Exception:  # noqa: BLE001
            log.exception("answer failed")
            gw.limits.release(call_id)
            st.status = "ended"
            st.finalized = True
            hub.publish(call_id, "error", {"stage": "answer", "message": "answer failed"})
            raise

    @app.post("/api/callbacks/{call_id}")
    async def callbacks(call_id: str, request: Request):
        if not check_call_token(s.gateway_secret, call_id, request.query_params.get("t"), require=require):
            raise HTTPException(401, "unauthorized")
        if not await acs_jwt.verify(request.headers.get("authorization"), "callback"):
            raise HTTPException(401, "unauthorized")
        if store.get(call_id) is None:
            raise HTTPException(404, "unknown call")
        try:
            body = await request.json()
        except ValueError as e:
            raise HTTPException(400, "invalid json") from e
        for ev in body if isinstance(body, list) else [body]:
            if not isinstance(ev, dict):
                continue
            et = (ev.get("type") or "").rsplit(".", 1)[-1]
            data = ev.get("data") or {}
            st = store.get(call_id)
            if st is None:
                continue
            if st.finalized or st.status == "ended":
                log.info("[%s] callback for ended call ignored", call_id)
                continue
            cc = data.get("callConnectionId", "")
            if et != "CallConnected" and not st.conn_id:
                log.debug("[%s] %s before CallConnected ignored", call_id, et)
                continue
            if st.conn_id and cc and cc != st.conn_id:
                log.warning("[%s] callback for foreign connection ignored", call_id)
                continue
            if et == "CallConnected":
                if not cc or st.conn_id:
                    continue  # duplicate delivery
                st.conn_id = data.get("callConnectionId", "")
                st.status = "active"
                if st.mark("connected"):
                    log.info("[%s] timeline: connected +%.1fs", call_id, st.marks["connected"] - st.started_at)
                hub.publish(call_id, "call.connected", {})
            elif et == "AddParticipantSucceeded":
                if st.rep_joined:
                    continue  # duplicate delivery
                st.rep_joined = True
                st.mark("rep_joined")
                log.info("[%s] timeline: rep joined +%.1fs", call_id, st.marks["rep_joined"] - st.started_at)
                st.ringing = False
                st.muted = True
                hub.publish(call_id, "transfer.succeeded", {})
            elif et == "AddParticipantFailed":
                st.muted = st.ringing = st.transfer_started = False
                hub.publish(call_id, "transfer.failed", {"message": str(data.get("resultInformation", ""))})
            elif et == "MediaStreamingFailed":
                hub.publish(call_id, "error", {"stage": "media", "message": json.dumps(data.get("resultInformation", ""))})
        return {"status": "ok"}

    @app.websocket("/ws/{call_id}")
    async def media_ws(ws: WebSocket, call_id: str):
        st = store.get(call_id)
        if not check_call_token(s.gateway_secret, call_id, ws.query_params.get("t"), require=require) or st is None or st.finalized:
            await ws.close(code=1008)
            return
        if not await acs_jwt.verify(ws.headers.get("authorization"), "ws"):
            await ws.close(code=1008)
            return
        await gw.handle_ws(ws, call_id)

    return app


def _default_app() -> FastAPI:
    return create_app()


app = _default_app() if __name__ != "__main__" and not __import__("os").environ.get("GATEWAY_LAZY") else None  # type: ignore[assignment]
