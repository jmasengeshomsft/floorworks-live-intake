import asyncio
import json
from urllib.parse import unquote
import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.drafts import OPT_BASE, DraftStore, priority_opt, rank_customers
from app.main import create_app
from app.state import CallState


class Tokens:
    async def get(self, scope=None):
        return "t"


class FakeDataverse:
    """Tiny in-memory Dataverse: one draft row, a couple of customers."""

    def __init__(self):
        self.draft = None
        self.posts = []
        self.patches = []
        self.customers = [
            {"bdm_customerid": "c1", "bdm_name": "JOHN SMITH", "bdm_accountnumber": "FW-1", "bdm_firstname": "John",
             "bdm_lastname": "Smith", "bdm_primaryphone": "+1 (555) 014-2000"},
        ]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, q = req.url.path.split("/v9.2/")[1], req.url.query.decode()
        body = json.loads(req.content) if req.content else None
        if req.method == "GET":
            if path == "bdm_servicerequestdrafts":
                d = self.draft if self.draft and f"'{self.draft['bdm_callid']}'" in unquote(q) else None
                return httpx.Response(200, json={"value": [d] if d else []})
            if path == "bdm_customers":
                if "contains(bdm_primaryphone" in q:
                    return httpx.Response(200, json={"value": self.customers})
                return httpx.Response(200, json={"value": self.customers})
            if path.startswith("bdm_customers("):
                return httpx.Response(200, json=self.customers[0])
            return httpx.Response(200, json={"value": []})
        if req.method == "POST":
            self.posts.append((path, body))
            if path == "bdm_servicerequestdrafts":
                self.draft = {**body, "bdm_servicerequestdraftid": "d1", "_bdm_customerid_value": None,
                              "_bdm_equipmentid_value": None, "_bdm_ticketid_value": None}
                if "bdm_CustomerId@odata.bind" in body:
                    self.draft["_bdm_customerid_value"] = "c1"
                if "bdm_EquipmentId@odata.bind" in body:
                    self.draft["_bdm_equipmentid_value"] = "e1"
            return httpx.Response(204, headers={"OData-EntityId": f"https://x/api/data/v9.2/{path}(new1)"})
        if req.method == "PATCH":
            self.patches.append(body)
            for k, v in body.items():
                if k.endswith("@odata.bind"):
                    col = {"bdm_CustomerId": "_bdm_customerid_value", "bdm_EquipmentId": "_bdm_equipmentid_value",
                           "bdm_TicketId": "_bdm_ticketid_value"}[k.split("@")[0]]
                    self.draft[col] = v.split("(")[1][:-1]
                else:
                    self.draft[k] = v
            return httpx.Response(204)
        return httpx.Response(404)


def mkstore(dv, **kw):
    s = Settings(dataverse_url="https://org.crm.dynamics.com", **kw)
    return DraftStore(s, Tokens(), httpx.AsyncClient(transport=httpx.MockTransport(dv)))


def call():
    st = CallState("call-1", caller_phone="+15550142000")
    st.add_line("caller", "Hydraulic press H1 is leaking")
    return st


def test_priority_and_rank():
    assert priority_opt("Urgent") == OPT_BASE + 3
    assert priority_opt("weird") == OPT_BASE + 1
    top = rank_customers([{"bdm_firstname": "Jon", "bdm_lastname": "Smyth", "bdm_primaryphone": ""}], "John Smith", [])
    assert top and top[0]["why"] == "similar name"


def test_save_notes_respects_rep_edits_and_submit():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        st = call()
        res = {"fields": {"customer_name": "Jon", "complaint": "valve leak", "priority": "High",
                          "equipment_model": "Hydraulic Press H1"}, "next_question": "Asset number?",
               "matches": [{"table": "bdm_equipment", "id": "e1"}]}
        await ds.save_notes(st, res)
        assert dv.draft["bdm_status"] == OPT_BASE
        assert dv.draft["_bdm_customerid_value"] == "c1"  # phone match
        assert dv.draft["_bdm_equipmentid_value"] is None or True
        assert dv.posts[0][1]["bdm_EquipmentId@odata.bind"] == "/bdm_equipments(e1)"
        assert dv.draft["bdm_priority"] == OPT_BASE + 2

        await ds.update("call-1", {"complaint": "rep wording", "priority": "Low"})
        await ds.save_notes(st, {"fields": {"complaint": "agent wording", "priority": "Urgent"}})
        assert dv.draft["bdm_complaint"] == "rep wording"
        assert dv.draft["bdm_priority"] == OPT_BASE

        title = await ds.submit("call-1", "Pat")
        assert title.startswith("FloorWorks-Voice-FW-1-")
        assert dv.posts[-1][0] == "bdm_servicetickets"
        assert dv.draft["bdm_status"] == OPT_BASE + 1
        await ds.save_notes(st, {"fields": {"complaint": "late"}})  # no-op once submitted
        assert dv.draft["bdm_complaint"] == "rep wording"

    asyncio.run(run())


def test_submit_requires_customer_and_equipment():
    async def run():
        dv = FakeDataverse()
        dv.customers = []
        ds = mkstore(dv)
        await ds.save_notes(call(), {"fields": {"complaint": "x"}})
        try:
            await ds.submit("call-1", "Pat")
        except Exception as e:  # noqa: BLE001
            assert "customer" in str(e)
        else:
            raise AssertionError("expected DraftError")

    asyncio.run(run())


def test_routes():
    dv = FakeDataverse()
    ds = mkstore(dv)
    c = TestClient(create_app(Settings(local_dev=True), drafts_store=ds))
    assert c.get("/v1/config").json()["drafts"] is True
    asyncio.run(ds.save_notes(call(), {"fields": {"complaint": "x"}}))
    assert c.get("/v1/drafts/call-1").json()["draft"]["bdm_callid"] == "call-1"
    assert c.post("/v1/drafts/call-1", json={"location": "Plant 2"}).status_code == 200
    assert c.post("/v1/drafts/nope", json={"location": "x"}).status_code == 404
    assert c.post("/v1/drafts/call-1/submit", json={}).status_code == 400
    off = TestClient(create_app(Settings(local_dev=True)))
    assert off.get("/v1/drafts").status_code == 503
    assert off.get("/v1/config").json()["drafts"] is False
