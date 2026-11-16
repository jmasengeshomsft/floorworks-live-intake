"""FastAPI app: /v1 consumer API + ACS/Event Grid infra endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import UUID

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from azure.core.exceptions import AzureError

from . import afterhours
from .auth import make_dependency
from .api_models import CustomerCreate, DraftEdit, FunctionResult, StatusResponse, SubmitDraft, TicketResponse
from .bridge import Gateway
from .config import Settings, load_settings
from .drafts import DraftConflict, DraftError, DraftStore
from .extractor import Extractor, TokenSource
from .functions import FunctionRegistry
from .journal import TranscriptRecorder
from .security import AcsJwtVerifier, check_call_token, check_shared_secret, new_call_id, normalize_number
from .state import CallStore, EventHub, MemoryStore, sse_frame
from .validate import report
from .webhooks import deliver_event

logging.getLogger("gateway").setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")


def create_app(settings: Settings | None = None, store: CallStore | None = None, acs=None, tokens=None, drafts_store=None) -> FastAPI:
    s = settings or load_settings()
    def _check_config():
        errors, warnings = report(s)
        if errors:
            raise RuntimeError("invalid configuration: " + "; ".join(errors))
        return errors, warnings

    cfg_errors, cfg_warnings = _check_config()
    hub = EventHub()
    store = store or MemoryStore(on_evict=hub.forget)
    owns_tokens = tokens is None and bool(s.foundry_project_endpoint or s.dataverse_url)
    if owns_tokens:
        tokens = TokenSource(s)
    owns_acs = acs is None and bool(s.acs_connection_string or s.acs_endpoint) and s.profile == "voice"
    if owns_acs:
        from .acs import AcsClient

        acs = AcsClient(s)
    extractor = Extractor(s, hub, tokens)
    drafts = drafts_store
    owns_drafts = drafts is None and bool(s.dataverse_url)
    if drafts is None and s.dataverse_url:
        drafts = DraftStore(s, tokens)
    if drafts is not None:
        extractor.on_result = drafts.save_notes
        extractor.on_validate = drafts.validate_ai
    webhook_http = httpx.AsyncClient(follow_redirects=False)
    functions = FunctionRegistry(s, webhook_http, hub.tasks)
    gw = Gateway(s, store, hub, extractor, functions, acs, tokens)
    recorder = TranscriptRecorder(drafts, store, hub, extractor) if drafts is not None else None
    gw.recorder = recorder
    acs_jwt = AcsJwtVerifier(s.acs_jwt_mode if s.local_dev else "enforce", s.acs_resource_id)
    require = not s.local_dev  # infra secrets are mandatory outside LOCAL_DEV

    if s.webhook_urls:

        async def fan_out(_cid: str, ev: dict) -> None:
            await deliver_event(s.webhook_urls, s.webhook_secret, ev, allow_insecure=s.local_dev, client=webhook_http)

        hub.listeners.append(fan_out)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with AsyncExitStack() as stack:
            if owns_tokens:
                stack.push_async_callback(tokens.aclose)
            if owns_acs:
                stack.push_async_callback(acs.aclose)
            if owns_drafts:
                stack.push_async_callback(drafts.aclose)
            stack.push_async_callback(extractor.aclose)
            stack.push_async_callback(webhook_http.aclose)
            if recorder is not None:
                stack.push_async_callback(recorder.close)
            stack.push_async_callback(gw.aclose)
            if recorder is not None:
                await recorder.recover()
            yield

    app = FastAPI(title="Intake Gateway", version="1.0", lifespan=lifespan)
    app.state.gateway = gw
    app.state.hub = hub
    app.state.store = store
    app.state.functions = functions
    app.state.extractor = extractor
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
        async def simulate(real: bool = False, phone: str = "+15550142"):
            if gw.draining:
                raise HTTPException(503, "application is draining")
            from . import simulator

            st = store.get_or_create(new_call_id())
            st.caller_phone = phone
            job = simulator.run_real(st, hub, extractor, recorder=recorder) if real else simulator.run(st, hub)
            hub.tasks.spawn(job, name="simulator")
            return st.summary()

    async def need(call_id: str):
        st = store.get(call_id)
        if st is None and recorder is not None:
            data = await dv(recorder.journal.get(call_id))
            if data is not None:
                from .state import CallState

                st = CallState.from_checkpoint(data)
                st.status = "ended"
                st.finalized = True
                store.put(st)
        if st is None:
            raise HTTPException(404, "call not found")
        return st

    # ---------------- /v1 ----------------

    @app.get("/v1/calls", dependencies=[auth])
    async def list_calls():
        return [c.summary() for c in store.list()]

    @app.get("/v1/calls/{call_id}", dependencies=[auth])
    async def get_call(call_id: str):
        st = await need(call_id)
        return {**st.summary(), "fields": st.fields, "enrichment": st.enrichment,
                "transcriptLines": len(st.transcript), "pendingFunctions": functions.pending(st)}

    @app.get("/v1/calls/{call_id}/transcript", dependencies=[auth])
    async def get_transcript(call_id: str):
        return (await need(call_id)).transcript

    @app.get("/v1/calls/{call_id}/extraction", dependencies=[auth])
    async def get_extraction(call_id: str):
        st = await need(call_id)
        return {"fields": st.fields, "enrichment": st.enrichment, "final": st.final_extraction,
                "status": st.extraction_status, "error": st.extraction_error,
                "extractedVersion": st.extracted_version, "persistedVersion": st.processed}

    @app.post("/v1/calls/{call_id}/extraction/retry", dependencies=[auth], response_model=StatusResponse)
    async def retry_extraction(call_id: str):
        if gw.draining or not extractor.enabled:
            raise HTTPException(503, "extraction unavailable")
        st = await need(call_id)
        if st.running:
            raise HTTPException(409, "extraction already running")
        if st.extraction_status != "failed":
            raise HTTPException(409, "only failed extraction can be retried explicitly")
        previous = st.extraction_status, st.extraction_error, st.extraction_attempts, st.final_requested
        st.extraction_status, st.extraction_error, st.extraction_attempts = "pending", None, 0
        st.final_requested = st.status == "ended"
        checkpointed = False
        try:
            await extractor._checkpoint(st)
            checkpointed = True
        finally:
            if not checkpointed:
                st.extraction_status, st.extraction_error, st.extraction_attempts, st.final_requested = previous
        extractor.trigger(st)
        return {"status": "accepted"}

    @app.get("/v1/calls/{call_id}/events", dependencies=[auth])
    async def events(call_id: str, request: Request):
        await need(call_id)
        try:
            last = int(request.headers.get("last-event-id") or request.query_params.get("afterEventId", "0"))
            if last < 0:
                raise ValueError("negative cursor")
        except ValueError as error:
            raise HTTPException(422, "event cursor must be a nonnegative integer") from error
        try:
            q = hub.subscribe(call_id, last)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

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
                    if ev["type"] == "resync.required":
                        return
            finally:
                hub.unsubscribe(call_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/v1/calls/{call_id}/transfer", dependencies=[auth])
    async def transfer(call_id: str):
        return await gw.transfer(await need(call_id))

    @app.post("/v1/calls/{call_id}/hangup", dependencies=[auth])
    async def hangup(call_id: str):
        return await gw.hangup(await need(call_id))

    @app.post("/v1/calls/{call_id}/functions/{fn_call_id}/result", dependencies=[auth], response_model=StatusResponse)
    async def function_result(call_id: str, fn_call_id: str, body: FunctionResult):
        if not functions.resolve(await need(call_id), fn_call_id, body.root):
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
            "drafts": drafts is not None,
            "dataverseConfig": drafts.schema.hash if drafts is not None else None,
            "equipment": drafts is None or drafts.schema.enabled("bdm_equipments"),
            "contacts": drafts is not None and drafts.schema.contacts,
            "liveNotes": gw.transcriber.name,
            "afterHoursMode": s.after_hours,
            "afterHoursNow": afterhours.is_active(s),
            "serviceTz": s.service_tz,
            "serviceDays": s.service_days,
            "serviceHours": s.service_hours,
            "transferTargets": len(s.transfer_teams_ids),
            "simulator": s.dev_simulator,
            "webhooks": len(s.webhook_urls),
            "speechAuth": s.speech_auth if gw.transcriber.name != "off" else None,
            "configWarnings": cfg_warnings,
            "schema": s.extraction_schema,
        }

    # ---------------- draft / rep actions (Dataverse) ----------------

    def need_drafts() -> DraftStore:
        if drafts is None:
            raise HTTPException(503, "drafts are not configured (set DATAVERSE_URL)")
        return drafts

    async def dv(coro):
        try:
            return await coro
        except LookupError as e:
            raise HTTPException(404, str(e))
        except DraftConflict as e:
            raise HTTPException(409, str(e))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 412:
                raise HTTPException(409, "draft changed; reload and retry") from e
            log.warning("Dataverse request failed: HTTP %s", e.response.status_code)
            raise HTTPException(502, "Dataverse request failed") from e
        except DraftError as e:
            raise HTTPException(400, str(e))
        except (httpx.HTTPError, AzureError, RuntimeError) as e:
            log.warning("draft op failed: %s", type(e).__name__)
            raise HTTPException(502, "Dataverse request failed")

    @app.get("/v1/drafts", dependencies=[auth])
    async def list_drafts():
        return await dv(need_drafts().list_drafts())

    @app.get("/v1/tickets", dependencies=[auth])
    async def list_tickets():
        return await dv(need_drafts().list_tickets())

    @app.get("/v1/customers", dependencies=[auth])
    async def search_customers(q: str = ""):
        return await dv(need_drafts().search_customers(q))

    @app.get("/v1/equipment", dependencies=[auth])
    async def list_equipment(customerId: UUID | None = None):
        return await dv(need_drafts().list_equipment(str(customerId) if customerId else None))

    @app.get("/v1/contacts", dependencies=[auth])
    async def list_contacts(customerId: UUID | None = None):
        return await dv(need_drafts().list_contacts(str(customerId) if customerId else None))

    @app.get("/v1/drafts/{call_id}", dependencies=[auth])
    async def get_draft(call_id: str):
        st = store.get(call_id)
        return await dv(need_drafts().get_detail(call_id, st.caller_phone if st else None))

    @app.post("/v1/drafts/{call_id}", dependencies=[auth], response_model=StatusResponse)
    async def edit_draft(call_id: str, body: DraftEdit):
        await dv(need_drafts().update(call_id, body.model_dump(mode="json", exclude_unset=True)))
        return {"status": "ok"}

    @app.post("/v1/drafts/{call_id}/customer", dependencies=[auth], response_model=StatusResponse)
    async def create_customer(call_id: str, body: CustomerCreate):
        await dv(need_drafts().create_customer(call_id, body.name, body.phone))
        return {"status": "ok"}

    @app.post("/v1/drafts/{call_id}/submit", dependencies=[auth], response_model=TicketResponse)
    async def submit_draft(call_id: str, body: SubmitDraft = Body(default=SubmitDraft())):
        return {"ticket": await dv(need_drafts().submit(call_id, body.submittedBy))}

    @app.get("/v1/stats", dependencies=[auth])
    async def stats():
        return {**gw.limits.stats(), "tracked": len(store.list()), "droppedWebhookEvents": hub.dropped_deliveries}

    # ---------------- infra (ACS / Event Grid) ----------------

    @app.get("/healthz")
    async def healthz():
        incomplete_local_voice = s.local_dev and s.profile == "voice" and not (s.public_base_url and s.foundry_project_endpoint and s.voice_agent)
        return {"status": "degraded" if cfg_errors or incomplete_local_voice else "ok", "errors": cfg_errors, "activeCalls": gw.limits.active}

    seen_events: dict[str, None] = {}

    @app.post("/api/incoming")
    async def incoming(request: Request):
        if gw.draining or s.profile != "voice":
            raise HTTPException(503, "voice admission unavailable")
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
            log.warning(
                "incoming call ignored: called=%r is not in LINE_NUMBERS (%s). Teams resource accounts arrive as "
                "28:orgid:<oid>, not a phone number; list that value or clear LINE_NUMBERS.",
                called,
                ",".join(s.line_numbers),
            )
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
            if recorder is not None:
                await recorder.flush(st)
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
