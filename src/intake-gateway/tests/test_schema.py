import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.drafts import DraftStore
from app.schema import OPT_BASE, Schema, SchemaError

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
    s = Schema()
    assert s.identity
    assert s.path("bdm_customers?$select=bdm_name") == "bdm_customers?$select=bdm_name"


def test_path_body_row_translation():
    s = Schema(OVERRIDE)
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

    st = Settings(dataverse_url="https://org.crm.dynamics.com", dataverse_schema=json.dumps(OVERRIDE))
    ds = DraftStore(st, Tokens(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    from app.state import CallState

    call = CallState("c-1", caller_phone="+15550142000")
    asyncio.run(ds.save_notes(call, {"fields": {"complaint": "leak"}}))
    post = [x for x in seen if x[0] == "POST"][0]
    assert post[1] == "acme_drafts"
    assert post[2]["acme_callref"] == "c-1" and post[2]["acme_state"] == 1
    assert "bdm_callid" not in post[2]


def test_cdm_profile_translation():
    s = Schema(profile="cdm-account-case")
    assert not s.identity and not s.enabled("bdm_equipments")
    assert s.path("bdm_customers?$select=bdm_customerid,bdm_name,bdm_firstname,bdm_primaryphone&$top=5") == \
        "accounts?$select=accountid,name,telephone1&$top=5"
    assert s.path("bdm_customers(a1)?$select=bdm_accountnumber") == "accounts(a1)?$select=accountnumber"
    t = s.body("bdm_servicetickets", {
        "bdm_name": "T", "bdm_priority": OPT_BASE + 3, "bdm_status": OPT_BASE, "bdm_openedon": "now",
        "bdm_EquipmentId@odata.bind": "/bdm_equipments(e1)", "bdm_CustomerId@odata.bind": "/bdm_customers(a1)",
    })
    assert t == {"title": "T", "prioritycode": 1, "customerid_account@odata.bind": "/accounts(a1)"}
    d = s.body("bdm_servicerequestdrafts", {"bdm_CustomerId@odata.bind": "/bdm_customers(a1)", "bdm_TicketId@odata.bind": "/bdm_servicetickets(i1)",
                                            "bdm_EquipmentId@odata.bind": "/bdm_equipments(e1)", "bdm_priority": OPT_BASE + 2})
    assert d == {"bdm_CustomerId@odata.bind": "/accounts(a1)", "bdm_TicketId@odata.bind": "/incidents(i1)", "bdm_priority": OPT_BASE + 2}
    assert s.row("bdm_customers", {"accountid": "a1", "name": "ACME", "telephone1": "5"}) == \
        {"bdm_customerid": "a1", "bdm_name": "ACME", "bdm_primaryphone": "5"}
    assert s.row("bdm_servicetickets", {"incidentid": "i1", "prioritycode": 1, "_customerid_value": "a1"}) == \
        {"bdm_serviceticketid": "i1", "bdm_priority": OPT_BASE + 2, "_bdm_customerid_value": "a1"}


def test_cdm_override_merges_over_profile():
    s = Schema({"columns": {"bdm_customers": {"bdm_primaryphone": "mobilephone"}}}, "cdm-account-case")
    assert s.path("bdm_customers?$select=bdm_primaryphone,bdm_name") == "accounts?$select=mobilephone,name"
    with pytest.raises(SchemaError):
        Schema(profile="nope")


def test_cdm_submit_on_the_wire():
    seen = []
    draft = {"bdm_servicerequestdraftid": "d1", "bdm_status": OPT_BASE, "_bdm_customerid_value": "a1", "bdm_priority": OPT_BASE + 3,
             "bdm_customername": "Sam", "bdm_equipmenttext": "Hydraulic Press", "bdm_assetnumber": "H1", "bdm_complaint": "leak"}

    def handler(req: httpx.Request) -> httpx.Response:
        rel = req.url.path.split("/v9.2/")[1]
        seen.append((req.method, rel, json.loads(req.content) if req.content else None))
        if req.method == "GET" and rel.startswith("bdm_servicerequestdrafts"):
            return httpx.Response(200, json={"value": [draft]})
        if req.method == "GET":
            return httpx.Response(200, json={"accountid": "a1", "name": "ACME"})
        return httpx.Response(204, headers={"OData-EntityId": "https://x/api/data/v9.2/incidents(i1)"})

    class Tokens:
        async def get(self, scope=None):
            return "t"

    st = Settings(dataverse_url="https://org.crm.dynamics.com", dataverse_profile="cdm-account-case")
    ds = DraftStore(st, Tokens(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    title = asyncio.run(ds.submit("c-1", "rep"))
    assert title.startswith("FloorWorks-Voice-ACME-")
    post = [x for x in seen if x[0] == "POST"][0]
    assert post[1] == "incidents"
    assert post[2]["prioritycode"] == 1 and post[2]["customerid_account@odata.bind"] == "/accounts(a1)"
    assert "Equipment: Hydraulic Press H1" in post[2]["description"]
    assert not any("equipment" in k.lower() for k in post[2])
    assert any(x[0] == "GET" and x[1].startswith("accounts(a1)") for x in seen)
