import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.drafts import DraftStore
from app.schema import OPT_BASE, Schema, SchemaError
from tests.cfgfiles import cfg, raw

OVERRIDE = {
    "names": {
        "bdm_servicerequestdrafts": "acme_drafts",
        "bdm_callid": "acme_callref",
        "bdm_status": "acme_state",
        "bdm_customerid": "acme_customerid",
        "bdm_CustomerId": "acme_Customer",
        "bdm_customers": "acme_clients",
    },
    "options": {"status": {"draft": 1, "submitted": 2}},
}


def test_defaults_are_identity():
    assert Schema().identity
    s = Schema(cfg("floorworks"))
    assert s.identity
    assert s.path("bdm_customers?$select=bdm_name") == "bdm_customers?$select=bdm_name"


def test_path_body_row_translation():
    s = Schema({**cfg("floorworks"), **OVERRIDE})
    assert s.path("bdm_servicerequestdrafts?$filter=bdm_callid eq 'bdm_callid'") == \
        "acme_drafts?$filter=acme_callref eq 'bdm_callid'"  # quoted literal untouched
    assert s.path("bdm_servicerequestdrafts?$select=_bdm_customerid_value") == "acme_drafts?$select=_acme_customerid_value"
    body = s.body("bdm_servicerequestdrafts", {"bdm_status": OPT_BASE + 1, "bdm_CustomerId@odata.bind": "/bdm_customers(c1)"})
    assert body == {"acme_state": 2, "acme_Customer@odata.bind": "/acme_clients(c1)"}
    row = s.row("bdm_servicerequestdrafts", {"value": [{"acme_state": 2, "_acme_customerid_value": "c1", "acme_callref": "x"}]})
    assert row["value"][0] == {"bdm_status": OPT_BASE + 1, "_bdm_customerid_value": "c1", "bdm_callid": "x"}


def test_validation():
    for bad in ({"names": {"nope": "x"}}, {"x": 1}, {"names": {"bdm_name": "1 bad"}},
                {"options": {"status": {"draft": "a"}}}, {"names": {"bdm_name": "z", "bdm_summary": "z"}}):
        with pytest.raises(SchemaError):
            Schema(bad)
    with pytest.raises(SchemaError):
        Schema.load(raw="{not json")


def test_store_uses_overrides_on_the_wire():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path.split("/v9.2/")[1], json.loads(req.content) if req.content else None))
        if req.method == "GET":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(204, headers={"OData-EntityId": "https://x/api/data/v9.2/acme_drafts(n1)"})

    class Tokens:
        async def get(self, scope=None):
            return "t"

    st = Settings(dataverse_url="https://org.crm.dynamics.com", dataverse_schema=json.dumps({**cfg("floorworks"), **OVERRIDE}))
    ds = DraftStore(st, Tokens(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    from app.state import CallState

    call = CallState("c-1", caller_phone="+15550142000")
    asyncio.run(ds.save_notes(call, {"fields": {"complaint": "leak"}}))
    post = [x for x in seen if x[0] == "PATCH"][0]
    assert post[1].startswith("acme_drafts(")
    assert post[2]["acme_callref"] == "c-1" and post[2]["acme_state"] == 1
    assert "bdm_callid" not in post[2]


def test_cdm_profile_translation():
    s = Schema(cfg("cdm"))
    assert not s.identity and s.enabled("bdm_equipments")
    assert s.path("bdm_customers?$select=bdm_customerid,bdm_name,bdm_firstname,bdm_primaryphone&$top=5") == \
        "accounts?$select=accountid,name,telephone1&$top=5"
    assert s.path("bdm_customers(a1)?$select=bdm_accountnumber") == "accounts(a1)?$select=accountnumber"
    t = s.body("bdm_servicetickets", {
        "bdm_name": "T", "bdm_priority": OPT_BASE + 3, "bdm_status": OPT_BASE, "bdm_openedon": "now",
        "bdm_EquipmentId@odata.bind": "/bdm_equipments(e1)", "bdm_CustomerId@odata.bind": "/bdm_customers(a1)",
    })
    assert t == {"title": "T", "prioritycode": 1, "customerid_account@odata.bind": "/accounts(a1)",
                 "bdm_EquipmentId@odata.bind": "/bdm_equipments(e1)"}
    d = s.body("bdm_servicerequestdrafts", {"bdm_CustomerId@odata.bind": "/bdm_customers(a1)", "bdm_TicketId@odata.bind": "/bdm_servicetickets(i1)",
                                            "bdm_EquipmentId@odata.bind": "/bdm_equipments(e1)", "bdm_priority": OPT_BASE + 2})
    assert d == {"bdm_CustomerId@odata.bind": "/accounts(a1)", "bdm_TicketId@odata.bind": "/incidents(i1)",
                 "bdm_EquipmentId@odata.bind": "/bdm_equipments(e1)", "bdm_priority": OPT_BASE + 2}
    assert s.row("bdm_customers", {"accountid": "a1", "name": "ACME", "telephone1": "5"}) == \
        {"bdm_customerid": "a1", "bdm_name": "ACME", "bdm_primaryphone": "5"}
    assert s.row("bdm_servicetickets", {"incidentid": "i1", "prioritycode": 1, "_customerid_value": "a1"}) == \
        {"bdm_serviceticketid": "i1", "bdm_priority": OPT_BASE + 2, "_bdm_customerid_value": "a1"}


def test_cdm_override_changes_mapping():
    c = cfg("cdm")
    c["columns"]["bdm_customers"]["bdm_primaryphone"] = "mobilephone"
    s = Schema(c)
    assert s.path("bdm_customers?$select=bdm_primaryphone,bdm_name") == "accounts?$select=mobilephone,name"
    with pytest.raises(SchemaError):
        Schema({"bogus": 1})


def test_cdm_submit_on_the_wire():
    seen = []
    draft = {"@odata.etag": 'W/"1"', "bdm_servicerequestdraftid": "d1", "bdm_status": OPT_BASE,     "_bdm_customerid_value": "a1", "_bdm_equipmentid_value": "e1", "bdm_priority": OPT_BASE + 3,
             "bdm_customername": "Sam", "bdm_equipmenttext": "Hydraulic Press", "bdm_assetnumber": "H1", "bdm_complaint": "leak"}

    def handler(req: httpx.Request) -> httpx.Response:
        rel = req.url.path.split("/v9.2/")[1]
        if rel == "$batch":
            from tests.test_drafts import batch_requests, batch_response
            requests = batch_requests(req)
            for operation in requests:
                seen.append((operation.method, operation.url.path.split("/v9.2/")[1], json.loads(operation.content)))
            return batch_response([204] * len(requests))
        seen.append((req.method, rel, json.loads(req.content) if req.content else None))
        if req.method == "GET" and rel.startswith("bdm_servicerequestdrafts"):
            return httpx.Response(200, json={"value": [draft]})
        if req.method == "GET" and rel == "bdm_equipments(e1)":
            return httpx.Response(200, json={"_bdm_customerid_value": "a1"})
        if req.method == "GET":
            return httpx.Response(200, json={"accountid": "a1", "name": "ACME"})
        return httpx.Response(204, headers={"OData-EntityId": "https://x/api/data/v9.2/incidents(i1)"})

    class Tokens:
        async def get(self, scope=None):
            return "t"

    st = Settings(dataverse_url="https://org.crm.dynamics.com",     dataverse_schema=raw("cdm"), brand_name="FloorWorks")
    ds = DraftStore(st, Tokens(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    title = asyncio.run(ds.submit("c-1", "rep"))
    assert title.startswith("FloorWorks-Voice-ACME-")
    post = [x for x in seen if x[0] == "POST"][0]
    assert post[1] == "incidents"
    assert post[2]["prioritycode"] == 1 and post[2]["customerid_account@odata.bind"] == "/accounts(a1)"
    assert "Equipment: Hydraulic Press H1" in post[2]["description"]
    assert post[2]["bdm_EquipmentId@odata.bind"] == "/bdm_equipments(e1)"
    assert any(x[0] == "GET" and x[1].startswith("accounts(a1)") for x in seen)


def test_repo_dataverse_config_file_loads():
    from pathlib import Path
    f = Path(__file__).resolve().parents[3] / "dataverse" / "config.json"
    assert Schema.load(path=str(f)).profile == json.loads(f.read_text())["profile"]
