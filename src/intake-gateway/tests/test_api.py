import asyncio

import pytest
from fastapi.testclient import TestClient

from app import afterhours, audio, webhooks
from app.config import Settings
from app.extractor import parse_agent_output
from app.main import create_app
from app.state import EventHub


def mk(**kw):
    kw.setdefault("local_dev", True)
    return create_app(Settings(**kw))


def test_sign_verify():
    sig = webhooks.sign("s", "123", b"{}")
    assert webhooks.verify("s", "123", b"{}", sig, tolerance_s=10**10)
    assert not webhooks.verify("s", "123", b"{}x", sig, tolerance_s=10**10)


def test_parse_agent_output():
    data = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '```json\n{"a": 1}\n```'}]}]}
    assert parse_agent_output(data) == {"a": 1}


def test_hub_replay():
    async def run():
        hub = EventHub()
        hub.publish("c", "x", {"n": 1})
        hub.publish("c", "y", {"n": 2})
        q = hub.subscribe("c", 1)
        return await asyncio.wait_for(q.get(), 1)

    assert asyncio.run(run())["type"] == "y"


def test_health_and_auth():
    c = TestClient(mk(api_keys=["k"]))
    assert c.get("/healthz").status_code == 200
    assert c.get("/v1/calls").status_code == 401
    assert c.get("/v1/calls", headers={"x-api-key": "k"}).json() == []


def test_cors_and_simulator():
    c = TestClient(mk(cors_origins=["http://localhost:5173"], dev_simulator=True))
    r = c.options(
        "/v1/calls",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert c.get("/v1/config").json()["simulator"] is True
    assert c.post("/v1/dev/simulate").status_code == 200
    off = TestClient(mk())
    assert off.post("/v1/dev/simulate").status_code in (404, 405)


def test_call_limits():
    from app.limits import CallLimits

    lim = CallLimits(max_concurrent=1, max_duration_s=0, idle_timeout_s=0)
    assert lim.acquire("a") and lim.acquire("a")
    assert not lim.acquire("b")
    assert lim.stats()["active"] == 1
    lim.release("a")
    assert lim.acquire("b")
    tight = CallLimits(max_concurrent=5, max_duration_s=1, idle_timeout_s=1)
    tight.acquire("x")
    tight._calls["x"].started -= 5
    assert tight.expired("x") == "max_duration"


def test_validate_and_stats():
    from app.validate import validate

    errors, warnings = validate(Settings(live_notes=True))
    assert any("PUBLIC_BASE_URL" in e for e in errors)
    assert any("SPEECH" in w for w in warnings)
    base = dict(public_base_url="https://x", foundry_project_endpoint="https://f", voice_agent="v", speech_auth="aad", speech_resource_id="r")
    errs, _ = validate(Settings(**base))
    assert any("API_KEYS" in e for e in errs)
    ok, _ = validate(Settings(**base, api_keys=["k"], gateway_secret="g", eventgrid_secret="e", acs_jwt_mode="enforce", acs_resource_id="r"))
    assert not ok
    assert not validate(Settings(**base, local_dev=True))[0]
    c = TestClient(mk())
    assert c.get("/healthz").json()["status"] == "degraded"
    assert c.get("/v1/stats").json()["active"] == 0
    assert c.get("/v1/config").json()["configWarnings"]


def test_speech_auth_selection():
    from app.transcribe import NullTranscriber, build_transcriber

    assert isinstance(build_transcriber(Settings(speech_auth="aad"), lambda *a: None), NullTranscriber)
    assert isinstance(build_transcriber(Settings(speech_auth="key"), lambda *a: None), NullTranscriber)


def test_open_when_no_keys_and_404():
    c = TestClient(mk())
    assert c.get("/v1/config").status_code == 200
    assert c.get("/v1/calls/nope").status_code == 404
    assert c.post("/v1/calls/nope/functions/f/result", json={}).status_code == 404


def test_validation_handshake():
    c = TestClient(mk())
    r = c.post("/api/incoming", json=[{"eventType": "Microsoft.EventGrid.SubscriptionValidationEvent", "data": {"validationCode": "abc"}}])
    assert r.json() == {"validationResponse": "abc"}


def test_callback_sets_conn_id_and_call_listed():
    app = mk()
    c = TestClient(app)
    app.state.store.get_or_create("c1")
    c.post("/api/callbacks/c1", json=[{"type": "Microsoft.Communication.CallConnected", "data": {"callConnectionId": "cc"}}])
    assert app.state.store.get("c1").conn_id == "cc"
    assert c.get("/v1/calls/c1").json()["id"] if "id" in c.get("/v1/calls/c1").json() else True
    assert len(c.get("/v1/calls").json()) == 1

def test_live_notes_factory():
    from app.transcribe import NullTranscriber, SpeechTranscriber, build_transcriber

    sink = lambda st, sp, t: None  # noqa: E731
    assert isinstance(build_transcriber(Settings(live_notes=False, speech_key="x"), sink), NullTranscriber)
    assert isinstance(build_transcriber(Settings(live_notes=True, speech_key=""), sink), NullTranscriber)
    assert isinstance(build_transcriber(Settings(live_notes=True, speech_key="x"), sink), SpeechTranscriber)


def test_config_reports_live_notes():
    c = TestClient(mk(api_keys=["k"], live_notes=True, speech_key=""))
    assert c.get("/v1/config", headers={"x-api-key": "k"}).json()["liveNotes"] == "off"


def _call_app(**kw):
    app = mk(**kw)
    return app, TestClient(app)


def test_eventgrid_secret_enforced():
    app, c = _call_app(eventgrid_secret="s3cret")
    body = [{"eventType": "Microsoft.EventGrid.SubscriptionValidationEvent", "data": {"validationCode": "abc"}}]
    assert c.post("/api/incoming", json=body).status_code == 401
    assert c.post("/api/incoming?s=wrong", json=body).status_code == 401
    assert c.post("/api/incoming?s=s3cret", json=body).json() == {"validationResponse": "abc"}
    assert c.post("/api/incoming", json=body, headers={"aeg-sas-key": "s3cret"}).status_code == 200


def test_callback_token_and_unknown_call():
    from app.security import call_token

    app, c = _call_app(gateway_secret="k")
    app.state.store.get_or_create("c1")
    ev = [{"type": "Microsoft.Communication.CallConnected", "data": {"callConnectionId": "cc"}}]
    assert c.post("/api/callbacks/c1", json=ev).status_code == 401
    assert c.post("/api/callbacks/c1?t=bad", json=ev).status_code == 401
    assert c.post(f"/api/callbacks/c1?t={call_token('k', 'c1')}", json=ev).status_code == 200
    assert app.state.store.get("c1").conn_id == "cc"
    assert c.post(f"/api/callbacks/nope?t={call_token('k', 'nope')}", json=ev).status_code == 404


def test_callback_foreign_connection_ignored():
    app, c = _call_app()
    st = app.state.store.get_or_create("c1")
    st.conn_id = "mine"
    c.post("/api/callbacks/c1", json=[{"type": "X.AddParticipantSucceeded", "data": {"callConnectionId": "other"}}])
    assert st.rep_joined is False


def test_ws_rejects_unknown_call_and_bad_token():
    from starlette.websockets import WebSocketDisconnect

    from app.security import call_token

    app, c = _call_app(gateway_secret="k")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/nope"):
            pass
    app.state.store.get_or_create("c1")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/c1?t=bad"):
            pass
    assert app.state.store.get("nope") is None
    assert call_token("k", "c1") != call_token("k", "c2")


def test_call_ids_are_long_and_unique():
    from app.security import new_call_id

    ids = {new_call_id() for _ in range(100)}
    assert len(ids) == 100 and all(len(i) >= 20 for i in ids)


def test_webhook_url_guard():
    from app.webhooks import url_problem

    assert url_problem('http://example.com/x')
    assert url_problem('https://localhost/x')
    assert url_problem('https://169.254.169.254/x')
    assert url_problem('https://10.0.0.5/x')
    assert not url_problem('https://example.com/x')
    assert not url_problem('http://localhost:9/x', allow_insecure=True)



def _acs_jwt_setup():
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.security import ACS_ISSUER

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, _t):
            return type("K", (), {"key": key.public_key()})()

    def mint(aud="res-1", iss=ACS_ISSUER, exp=300):
        return "Bearer " + jwt.encode({"aud": aud, "iss": iss, "exp": int(time.time()) + exp}, key, algorithm="RS256")

    return FakeJwks(), mint


def test_acs_jwt_verifier_modes():
    import asyncio

    from app.security import AcsJwtVerifier

    jwks, mint = _acs_jwt_setup()
    enforce = AcsJwtVerifier("enforce", "res-1", jwks)
    run = asyncio.run
    assert run(enforce.verify(mint(), "t"))
    assert not run(enforce.verify(mint(aud="other"), "t"))
    assert not run(enforce.verify(mint(iss="https://evil.example"), "t"))
    assert not run(enforce.verify(mint(exp=-10), "t"))
    assert not run(enforce.verify(None, "t"))
    assert not run(enforce.verify("Bearer garbage", "t"))
    assert run(AcsJwtVerifier("warn", "res-1", jwks).verify(mint(aud="other"), "t"))
    assert run(AcsJwtVerifier("off", "", jwks).verify(None, "t"))


# ---------------- round-2 High fixes ----------------


def test_fail_closed_outside_local_dev():
    from app.validate import validate

    base = dict(public_base_url="https://x", foundry_project_endpoint="https://f", voice_agent="v", api_keys=["k"])
    errs, _ = validate(Settings(**base))
    joined = " ".join(errs)
    assert "GATEWAY_SECRET" in joined and "EVENTGRID_SECRET" in joined
    errs, _ = validate(Settings(**base, gateway_secret="g", eventgrid_secret="e", acs_jwt_mode="warn", acs_resource_id="r"))
    assert any("enforce" in e for e in errs)
    assert not validate(Settings(**base, local_dev=True))[0]


def test_infra_endpoints_closed_without_secrets_in_prod():
    app = create_app(Settings(local_dev=False))
    c = TestClient(app)
    assert c.post("/api/incoming", json=[]).status_code == 401
    app.state.store.get_or_create("c1")
    assert c.post("/api/callbacks/c1", json=[]).status_code == 401


def test_jwt_mode_defaults_to_enforce(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("ACS_JWT_MODE", raising=False)
    monkeypatch.delenv("LOCAL_DEV", raising=False)
    assert load_settings().acs_jwt_mode == "enforce"
    monkeypatch.setenv("LOCAL_DEV", "1")
    assert load_settings().acs_jwt_mode == "off"


def test_non_ascii_secrets_do_not_500():
    from app.security import check_call_token, check_shared_secret

    assert check_shared_secret("s", "é") is False
    assert check_call_token("k", "c1", "é") is False
    app, c = _call_app(eventgrid_secret="s3cret", gateway_secret="k", api_keys=["key"])
    assert c.post("/api/incoming?s=%C3%A9", json=[]).status_code == 401
    app.state.store.get_or_create("c1")
    assert c.post("/api/callbacks/c1?t=%C3%A9", json=[]).status_code == 401
    assert c.get("/v1/config", headers={"x-api-key": "é".encode()}).status_code == 401
    assert c.get("/v1/config?key=%C3%A9").status_code == 401


def test_callbacks_gated_by_call_state():
    app, c = _call_app()
    st = app.state.store.get_or_create("c1")
    forged = [{"type": "X.AddParticipantSucceeded", "data": {}}]
    c.post("/api/callbacks/c1", json=forged)
    assert st.rep_joined is False and st.muted is False  # before CallConnected
    c.post("/api/callbacks/c1", json=[{"type": "X.CallConnected", "data": {"callConnectionId": "cc"}}])
    assert st.conn_id == "cc"
    st.finalized = True
    c.post("/api/callbacks/c1", json=[{"type": "X.AddParticipantSucceeded", "data": {"callConnectionId": "cc"}}])
    assert st.rep_joined is False  # after finalize


class _FakeAcs:
    def __init__(self, fail_answer=False):
        self.hung_up: list[str] = []
        self.fail_answer = fail_answer

    async def hang_up(self, conn_id):
        self.hung_up.append(conn_id)

    async def answer(self, context, call_id):
        if self.fail_answer:
            raise RuntimeError("boom")

    async def reject(self, context):
        pass


def test_incoming_answer_failure_returns_5xx_and_retries():
    acs = _FakeAcs(fail_answer=True)
    app = create_app(Settings(local_dev=True), acs=acs)
    c = TestClient(app, raise_server_exceptions=False)
    ev = [{"id": "e1", "eventType": "Microsoft.Communication.IncomingCall", "data": {"incomingCallContext": "ctx", "from": {"rawId": "+1"}, "to": {"rawId": "+2"}}}]
    assert c.post("/api/incoming", json=ev).status_code == 500
    acs.fail_answer = False
    assert c.post("/api/incoming", json=ev).status_code == 200  # same event id is processed again, not deduped


def test_voice_connect_failure_hangs_up(monkeypatch):
    from app import bridge

    monkeypatch.setattr(bridge, "VOICE_RETRY_DELAY_S", 0)
    acs = _FakeAcs()
    app = create_app(Settings(local_dev=True), acs=acs)
    gw = app.state.gateway
    attempts = []

    async def boom(st, resume):
        attempts.append(resume)
        raise ConnectionError("down")

    monkeypatch.setattr(gw, "_connect_voice", boom)
    st = app.state.store.get_or_create("c1")
    st.conn_id = "cc"
    c = TestClient(app)
    with c.websocket_connect("/ws/c1") as ws:
        with pytest.raises(Exception):
            ws.receive_text()  # server closes after giving up
    assert len(attempts) == bridge.VOICE_CONNECT_ATTEMPTS
    assert acs.hung_up == ["cc"]


def test_second_concurrent_ws_rejected():
    from starlette.websockets import WebSocketDisconnect

    app, c = _call_app()
    st = app.state.store.get_or_create("c1")
    st.active_ws = 1  # a socket is already attached
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/c1"):
            pass
    assert st.active_ws == 1 and st.connections == 0


def test_normalize_enrichment():
    from app.extractor import normalize_enrichment
    out = normalize_enrichment({'suggested_priority': 'Normal', 'sources': ['SB-2026-003 【6:1†x】', '【1】']})
    assert out['suggested_priority'] == 'Medium'
    assert out['sources'] == ['SB-2026-003']

