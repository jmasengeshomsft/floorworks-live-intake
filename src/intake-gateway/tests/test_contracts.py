import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from app.config import FunctionDef, Settings, load_settings
from app.main import create_app
from app.validate import validate
from tests.cfgfiles import raw
from tests.test_drafts import FakeDataverse, mkstore


@pytest.mark.parametrize("changes, expected", [
    ({"max_concurrent_calls": -1}, "MAX_CONCURRENT_CALLS"),
    ({"enrichment_interval_s": -1}, "ENRICHMENT_INTERVAL_S"),
    ({"service_tz": "Not/AZone"}, "SERVICE_TZ"),
    ({"service_hours": "18:00-07:00"}, "SERVICE_HOURS"),
    ({"service_days": "Nonday"}, "SERVICE_DAYS"),
    ({"dataverse_url": "https://org.invalid:bad"}, "DATAVERSE_URL"),
    ({"after_hours": "maybe"}, "AFTER_HOURS"),
    ({"functions": [FunctionDef("hangup", "https://example.com")]}, "built-in"),
])
def test_invalid_local_configuration_fails_before_admitting_requests(changes, expected):
    with pytest.raises(RuntimeError, match=expected):
        create_app(Settings(local_dev=True, **changes))


def test_settings_are_strict_and_api_only_production_profile_is_explicit():
    with pytest.raises(ValidationError):
        Settings(local_dev="false")
    settings = Settings(profile="api", api_keys=["k"], dataverse_url="https://org.crm.dynamics.com",
                        dataverse_schema=raw("floorworks"))
    assert not validate(settings)[0]
    with pytest.raises(RuntimeError, match="azure_cli"):
        create_app(Settings(profile="api", api_keys=["k"], dataverse_url="https://org.crm.dynamics.com",
                            dataverse_schema=raw("floorworks"), credential_mode="azure_cli"))


@pytest.mark.parametrize("name, value, expected", [
    ("EXTRACTION_SCHEMA", "[]", "JSON object"),
    ("FUNCTIONS", "{}", "JSON array"),
    ("FUNCTIONS", '[{"name":"fn","webhookUrl":"https://example.com","async":"false"}]', "boolean"),
    ("LOCAL_DEV", "yes", "0 or 1"),
    ("ENRICHMENT", "maybe", "on or off"),
    ("MAX_CONCURRENT_CALLS", "many", "integer"),
])
def test_environment_settings_reject_malformed_shapes_and_values(name, value, expected, monkeypatch):
    monkeypatch.delenv("EXTRACTION_SCHEMA_FILE", raising=False)
    monkeypatch.delenv("FUNCTIONS_FILE", raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=expected):
        load_settings()


def test_typed_api_rejects_bad_mutations_and_preserves_omitted_versus_cleared():
    async def run():
        ds = mkstore(FakeDataverse())
        ds.update = AsyncMock()
        ds.create_customer = AsyncMock()
        ds.submit = AsyncMock(return_value="ticket")
        app = create_app(Settings(local_dev=True, live_notes=False), drafts_store=ds)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for body in ({"summary": []}, {"surprise": True}, {"equipmentId": "not-a-guid"},
                         {"priority": None}, {"priority": "wrong"}, {"summary": "x" * 4001}):
                assert (await client.post("/v1/drafts/call", json=body)).status_code == 422
            ds.update.assert_not_awaited()
            response = await client.post("/v1/drafts/call", json={"summary": None, "equipmentId": None, "priority": "High"})
            assert response.status_code == 200
            ds.update.assert_awaited_once_with("call", {"summary": None, "equipmentId": None, "priority": "high"})
            for body in ({"name": ""}, {"name": 12}, {"name": "Pat", "phone": []}):
                assert (await client.post("/v1/drafts/call/customer", json=body)).status_code == 422
            ds.create_customer.assert_not_awaited()
            assert (await client.post("/v1/drafts/call/submit", json={"submittedBy": {}})).status_code == 422
            ds.submit.assert_not_awaited()
            assert (await client.post("/v1/drafts/call/submit")).json() == {"ticket": "ticket"}
            ds.submit.assert_awaited_once_with("call", "rep")
            assert (await client.get("/v1/equipment?customerId=bad")).status_code == 422
            assert (await client.post("/v1/calls/call/functions/fn/result", json=[])).status_code == 422
            assert (await client.post("/v1/calls/call/functions/fn/result", json={"result": "x" * 262145})).status_code == 422
        await ds.aclose()
    asyncio.run(run())


def test_lifespan_does_not_close_borrowed_dependencies_and_closes_owned_http():
    async def run():
        ds = mkstore(FakeDataverse())
        ds.aclose = AsyncMock()
        tokens, acs = Mock(), Mock()
        tokens.aclose = AsyncMock()
        acs.aclose = AsyncMock()
        app = create_app(Settings(local_dev=True, live_notes=False), drafts_store=ds, tokens=tokens, acs=acs)
        extractor_http = app.state.extractor._http
        webhook_http = app.state.functions._http
        async with app.router.lifespan_context(app):
            st = app.state.store.get_or_create("call")
            st.pending_functions["fn"] = asyncio.get_running_loop().create_future()
            future = st.pending_functions["fn"]
        assert future.cancelled() and not st.pending_functions
        assert extractor_http.is_closed and webhook_http.is_closed
        tokens.aclose.assert_not_awaited()
        acs.aclose.assert_not_awaited()
        ds.aclose.assert_not_awaited()
        await ds._http.aclose()
    asyncio.run(run())


def test_lifespan_closes_transcriber_and_clients_when_task_drain_fails():
    async def run():
        app = create_app(Settings(local_dev=True, live_notes=False))
        gateway = app.state.gateway
        gateway.tasks.close = AsyncMock(side_effect=RuntimeError("drain failed"))
        gateway.transcriber.aclose = AsyncMock()
        with pytest.raises(RuntimeError, match="drain failed"):
            async with app.router.lifespan_context(app):
                st = app.state.store.get_or_create("call")
                future = asyncio.get_running_loop().create_future()
                st.pending_functions["fn"] = future
        gateway.transcriber.aclose.assert_awaited_once()
        assert future.cancelled() and not st.pending_functions
        assert app.state.extractor._http.is_closed and app.state.functions._http.is_closed
    asyncio.run(run())
