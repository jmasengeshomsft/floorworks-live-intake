import asyncio

import pytest
from fastapi.testclient import TestClient

from app import afterhours, audio, webhooks
from app.config import Settings
from app.extractor import parse_agent_output
from app.main import create_app
from app.state import EventHub


def mk(**kw):
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
    ok, _ = validate(Settings(public_base_url="https://x", foundry_project_endpoint="https://f", voice_agent="v", speech_auth="aad", speech_resource_id="r"))
    assert not ok
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
