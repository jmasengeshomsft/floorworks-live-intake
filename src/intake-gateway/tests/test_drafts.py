import asyncio
import json
import copy
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import unquote
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.cfgfiles import cfg, raw
from app.drafts import OPT_BASE, DraftConflict, DraftStore, priority_opt, rank_customers
from app.main import create_app
from app.state import CallState


class Tokens:
    async def get(self, scope=None):
        return "t"


def batch_requests(req):
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {req.headers['content-type']}\r\n\r\n".encode() + req.content
    )
    requests = []
    for part in message.walk():
        if part.get_content_type() == "application/http":
            raw = part.get_payload(decode=True)
            first, rest = raw.split(b"\r\n", 1)
            method, url, _ = first.decode().split(" ")
            headers, body = rest.split(b"\r\n\r\n", 1)
            parsed = BytesParser(policy=policy.default).parsebytes(headers + b"\r\n\r\n")
            requests.append(httpx.Request(method, url, headers=dict(parsed.items()), content=body.strip()))
    return requests


def batch_response(statuses):
    text = "--batch_reply\r\nContent-Type: multipart/mixed; boundary=change_reply\r\n\r\n"
    for i, status in enumerate(statuses, 1):
        text += (f"--change_reply\r\nContent-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n"
                 f"Content-ID: {i}\r\n\r\nHTTP/1.1 {status} Result\r\nContent-Length: 0\r\n\r\n")
    text += "--change_reply--\r\n--batch_reply--\r\n"
    return httpx.Response(200, content=text.encode(), headers={"content-type": "multipart/mixed; boundary=batch_reply"})


class FakeDataverse:
    """Tiny in-memory Dataverse: one draft row, a couple of customers."""

    def __init__(self):
        self.draft = None
        self.posts = []
        self.patches = []
        self.rows = {}
        self.revision = 0
        self.fail_operation = None
        self.lose_response = False
        self.batch_count = 0
        self.customers = [
            {"bdm_customerid": "c1", "bdm_name": "JOHN SMITH", "bdm_accountnumber": "FW-1", "bdm_firstname": "John",
             "bdm_lastname": "Smith", "bdm_primaryphone": "+1 (555) 014-2000"},
        ]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, q = req.url.path.split("/v9.2/")[1], req.url.query.decode()
        if path == "$batch":
            self.batch_count += 1
            before = copy.deepcopy((self.draft, self.posts, self.patches, self.rows, self.revision))
            statuses = []
            for i, operation in enumerate(batch_requests(req), 1):
                if self.fail_operation == i:
                    self.fail_operation = None
                    response = httpx.Response(503)
                else:
                    response = self(operation)
                statuses.append(response.status_code)
                if response.status_code >= 300:
                    self.draft, self.posts, self.patches, self.rows, self.revision = before
                    return batch_response([response.status_code])
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("response lost after commit", request=req)
            return batch_response(statuses)
        body = json.loads(req.content) if req.content else None
        if req.method == "GET":
            if path.startswith("EntityDefinitions"):
                return httpx.Response(200, json={"value": [{"KeyAttributes": ["bdm_callid"], "EntityKeyIndexStatus": "Active"}]}
                                      if "/Keys" in path else {"IsOptimisticConcurrencyEnabled": True})
            if path == "bdm_servicerequestdrafts":
                d = self.draft if self.draft and f"'{self.draft['bdm_callid']}'" in unquote(q) else None
                return httpx.Response(200, json={"value": [d] if d else []})
            if path == "bdm_customers":
                if "contains(bdm_primaryphone" in q:
                    return httpx.Response(200, json={"value": self.customers})
                return httpx.Response(200, json={"value": self.customers})
            if path.startswith("bdm_customers("):
                identity = path.split("(")[1][:-1]
                row = self.rows.get(("bdm_customers", identity)) or next(
                    (c for c in self.customers if c["bdm_customerid"] == identity), None)
                return httpx.Response(200, json=row) if row else httpx.Response(404)
            if path.startswith("bdm_equipments("):
                identity = path.split("(")[1][:-1]
                row = self.rows.get(("bdm_equipments", identity))
                if row is None and identity == "e1":
                    row = {"bdm_equipmentid": "e1", "_bdm_customerid_value": "c1"}
                return httpx.Response(200, json=row) if row else httpx.Response(404)
            match = re.fullmatch(r"(\w+)\(([^)]+)\)", path)
            if match:
                row = self.rows.get((match[1], match[2]))
                return httpx.Response(200, json=row) if row else httpx.Response(404)
            if path == "bdm_calljournals":
                rows = [r for (table, _), r in self.rows.items() if table == path]
                if "bdm_complete eq 0" in unquote(q):
                    rows = [r for r in rows if r["bdm_complete"] == 0]
                return httpx.Response(200, json={"value": rows})
            return httpx.Response(200, json={"value": []})
        match = re.fullmatch(r"(\w+)\(([^)]+)\)", path)
        if req.method == "POST" or (req.method == "PATCH" and match):
            table = match[1] if match else path
            pk = {"annotations": "annotationid"}.get(table, table.rstrip("s") + "id")
            identity = match[2] if match else body.get(pk, str(uuid4()))
            row = self.draft if table == "bdm_servicerequestdrafts" else self.rows.get((table, identity))
            if row and row.get(pk) != identity:
                row = None
            if req.headers.get("if-none-match") == "*" and row:
                return httpx.Response(412)
            expected = req.headers.get("if-match")
            if expected and (not row or (expected != "*" and expected != row.get("@odata.etag"))):
                return httpx.Response(412 if row else 404)
            if req.method == "POST" and row:
                return httpx.Response(409)
            if row:
                assert expected, "existing rows must be conditionally updated"
            if row is None:
                row = {pk: identity, "_bdm_customerid_value": None,
                       "_bdm_equipmentid_value": None, "_bdm_ticketid_value": None}
            row.update(body)
            for k, v in body.items():
                if k.endswith("@odata.bind") and table != "annotations":
                    col = {"bdm_CustomerId": "_bdm_customerid_value", "bdm_EquipmentId": "_bdm_equipmentid_value",
                           "bdm_TicketId": "_bdm_ticketid_value"}[k.split("@")[0]]
                    row[col] = v.split("(")[1][:-1] if v else None
            self.revision += 1
            row["@odata.etag"] = f'W/"{self.revision}"'
            if table == "bdm_servicerequestdrafts":
                self.draft = row
            else:
                self.rows[(table, identity)] = row
            if req.method == "POST":
                self.posts.append((path, body))
            else:
                self.patches.append(body)
            return httpx.Response(204, headers={"OData-EntityId": f"https://x/api/data/v9.2/{table}({identity})"})
        return httpx.Response(404)


def mkstore(dv, **kw):
    s = Settings(dataverse_url="https://org.crm.dynamics.com", dataverse_schema=raw("floorworks"), brand_name="FloorWorks", **kw)
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
        assert dv.draft["_bdm_equipmentid_value"] == "e1"
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

@pytest.mark.parametrize("stored", ["4056029804", "(405) 602-9804", "405-602-9804", "+1 405 602 9804", "405.602.9804"])
def test_phone_formats_match(stored):
    async def run():
        dv = FakeDataverse()
        dv.customers[0]["bdm_primaryphone"] = stored
        ds = mkstore(dv)
        for caller in ("+14056029804", "(405) 602-9804", "4056029804"):
            cid, _name = await ds.match_customer(caller)
            assert cid == "c1", (stored, caller)

    asyncio.run(run())


def test_render_prompt_from_config():
    from app.render_prompts import render, values
    from app.schema import Schema

    v = values(Schema(cfg("cdm")), "Acme")
    out = render("{{ASSISTANT_NAME}}: {{CUSTOMER_TABLE}} {{PHONE_COLUMNS}}", v)
    assert out == "Acme Assistant: account telephone1, telephone2, telephone3"
    assert "account: Customers" in v["SCHEMA_NOTES"] and len(v["CONFIG_HASH"]) == 12
    import pytest as _p

    with _p.raises(SystemExit):
        render("{{NOPE}}", v)


@pytest.mark.parametrize("agent_fields", [{"summary": "AI replacement"}, {}, {"summary": None}])
@pytest.mark.parametrize("rep_summary", ["rep correction", "", None])
def test_summary_ownership_including_rep_clear(agent_fields, rep_summary):
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        st = call()
        await ds.save_notes(st, {"fields": {"summary": "initial"}})
        await ds.update(st.call_id, {"summary": rep_summary})
        await ds.save_notes(st, {"fields": agent_fields})
        assert dv.draft["bdm_summary"] == rep_summary
        await ds._http.aclose()
    asyncio.run(run())


def test_omitted_summary_does_not_clear_unedited_summary():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        await ds.save_notes(call(), {"fields": {"summary": "keep"}})
        await ds.save_notes(call(), {"fields": {"complaint": "new detail"}})
        assert dv.draft["bdm_summary"] == "keep"
    asyncio.run(run())


@pytest.mark.parametrize("ai_write", [True, False])
def test_overlapping_writers_merge_rep_ownership(ai_write):
    async def run():
        dv = FakeDataverse()
        first, second = mkstore(dv), mkstore(dv)
        st = call()
        await first.save_notes(st, {"fields": {"complaint": "initial"}})
        send = first._send
        interleaved = False

        async def overlap(method, path, body=None, *args, **kwargs):
            nonlocal interleaved
            if method == "PATCH" and path.startswith("bdm_servicerequestdrafts(") and not interleaved:
                interleaved = True
                await second.update(st.call_id, {"complaint": "rep wins", "summary": "rep summary"})
            return await send(method, path, body, *args, **kwargs)

        first._send = overlap
        if ai_write:
            await first.save_notes(st, {"fields": {"complaint": "AI stale", "summary": "AI stale"}})
        else:
            await first.update(st.call_id, {"location": "Plant 2"})
        assert dv.draft["bdm_complaint"] == "rep wins"
        assert dv.draft["bdm_summary"] == "rep summary"
        assert set(dv.draft["bdm_editedfields"].split(",")) >= {"complaint", "summary"}
        if not ai_write:
            assert dv.draft["bdm_location"] == "Plant 2"
            assert "location" in dv.draft["bdm_editedfields"].split(",")
    asyncio.run(run())


def test_concurrent_draft_creation_uses_one_identity():
    async def run():
        dv = FakeDataverse()
        stores = [mkstore(dv), mkstore(dv)]
        ready = asyncio.Event()
        reads = 0

        def intercept(ds):
            send = ds._send
            first = True
            async def wrapped(method, path, *args, **kwargs):
                nonlocal reads, first
                result = await send(method, path, *args, **kwargs)
                if first and method == "GET" and path.startswith("bdm_servicerequestdrafts?"):
                    first = False
                    reads += 1
                    if reads == 2:
                        ready.set()
                    await ready.wait()
                return result
            ds._send = wrapped
        for ds in stores:
            intercept(ds)
        await asyncio.gather(*(ds.save_notes(call(), {"fields": {"complaint": "leak"}}) for ds in stores))
        assert dv.draft["bdm_callid"] == "call-1"
        assert sum("bdm_callid" in b for b in dv.patches) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failed_operation", [1, 2, 3])
def test_submit_rolls_back_every_operation_and_retries(failed_operation):
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv, enrichment=True)
        ds.schema.description_max = 80
        ds.schema.note_bind = "objectid_bdm_serviceticket"
        await ds.save_notes(call(), {"fields": {"summary": "long summary " * 20},
                                    "matches": [{"table": "bdm_equipment", "id": "e1"}]})
        dv.fail_operation = failed_operation
        with pytest.raises(httpx.HTTPStatusError):
            await ds.submit("call-1", "Pat")
        assert dv.draft["bdm_status"] == OPT_BASE
        assert not dv.posts
        title = await ds.submit("call-1", "Pat")
        assert await ds.submit("call-1", "Pat") == title
        assert len([p for p in dv.posts if p[0] == "bdm_servicetickets"]) == 1
        assert len([p for p in dv.posts if p[0] == "annotations"]) == 1
        assert dv.draft["bdm_status"] == OPT_BASE + 1
    asyncio.run(run())


def test_submit_recovers_response_loss_and_double_click():
    async def run():
        dv = FakeDataverse()
        first, second = mkstore(dv), mkstore(dv)
        await first.save_notes(call(), {"fields": {"complaint": "leak"},
                                       "matches": [{"table": "bdm_equipment", "id": "e1"}]})
        dv.lose_response = True
        with pytest.raises(httpx.ReadTimeout):
            await first.submit("call-1", "Pat")
        results = await asyncio.gather(first.submit("call-1", "Pat"), second.submit("call-1", "Pat"))
        assert results[0] == results[1]
        assert len([p for p in dv.posts if p[0] == "bdm_servicetickets"]) == 1
    asyncio.run(run())


def test_concurrent_submit_rechecks_etag_before_creating_ticket():
    async def run():
        dv = FakeDataverse()
        first, second = mkstore(dv), mkstore(dv)
        await first.save_notes(call(), {"fields": {"complaint": "leak"},
                                       "matches": [{"table": "bdm_equipment", "id": "e1"}]})
        changeset = first._changeset
        once = False
        other_title = None

        async def overlap(operations):
            nonlocal once, other_title
            if not once:
                once = True
                other_title = await second.submit("call-1", "Pat")
            return await changeset(operations)

        first._changeset = overlap
        assert await first.submit("call-1", "Pat") == other_title
        assert len([p for p in dv.posts if p[0] == "bdm_servicetickets"]) == 1
    asyncio.run(run())


def test_missing_etag_fails_closed():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        await ds.save_notes(call(), {"fields": {"complaint": "keep"}})
        del dv.draft["@odata.etag"]
        with pytest.raises(RuntimeError, match="ETag"):
            await ds.update("call-1", {"complaint": "unsafe"})
        assert dv.draft["bdm_complaint"] == "keep"
    asyncio.run(run())


def test_customer_change_rejects_foreign_links_until_explicitly_cleared():
    async def run():
        from app.drafts import DraftError

        dv = FakeDataverse()
        ds = mkstore(dv)
        st = call()
        await ds.save_notes(st, {"fields": {}, "matches": [{"table": "bdm_equipment", "id": "e1"}]})
        dv.customers.append({"bdm_customerid": "c2", "bdm_name": "OTHER CUSTOMER"})
        revision = dv.revision
        with pytest.raises(DraftError, match="must belong"):
            await ds.update(st.call_id, {"customerId": "c2"})
        assert dv.revision == revision and dv.draft["_bdm_customerid_value"] == "c1"
        await ds.update(st.call_id, {"customerId": "c2", "equipmentId": None})
        await ds.save_notes(st, {"matches": [{"table": "bdm_equipment", "id": "e1"}]})
        assert dv.draft["_bdm_customerid_value"] == "c2"
        assert dv.draft["_bdm_equipmentid_value"] is None
        assert "equipmentId" in dv.draft["bdm_editedfields"]
        await ds.update(st.call_id, {"customerId": None})
        await ds.save_notes(st, {"fields": {"summary": "New details"}})
        assert dv.draft["_bdm_customerid_value"] is None
    asyncio.run(run())


def test_submit_rejects_existing_customer_equipment_mismatch():
    async def run():
        from app.drafts import DraftError

        dv = FakeDataverse()
        ds = mkstore(dv)
        await ds.save_notes(call(), {"matches": [{"table": "bdm_equipment", "id": "e1"}]})
        dv.rows[("bdm_equipments", "e1")] = {"_bdm_customerid_value": "other"}
        with pytest.raises(DraftError, match="must belong"):
            await ds.submit("call-1", "Pat")
        assert not dv.posts and dv.draft["bdm_status"] == OPT_BASE
    asyncio.run(run())


def test_submit_rebuilds_ticket_from_rep_edit_on_conflict():
    async def run():
        dv = FakeDataverse()
        first, second = mkstore(dv), mkstore(dv)
        await first.save_notes(call(), {"fields": {"summary": "AI summary"},
                                       "matches": [{"table": "bdm_equipment", "id": "e1"}]})
        changeset = first._changeset
        edited = False

        async def overlap(operations):
            nonlocal edited
            if not edited:
                edited = True
                await second.update("call-1", {"summary": "rep correction"})
            return await changeset(operations)

        first._changeset = overlap
        await first.submit("call-1", "Pat")
        tickets = [b for table, b in dv.posts if table == "bdm_servicetickets"]
        assert len(tickets) == 1
        assert "rep correction" in tickets[0]["bdm_description"]
        assert "AI summary" not in tickets[0]["bdm_description"]
    asyncio.run(run())


def test_create_customer_links_atomically_and_handles_response_loss():
    async def run():
        dv = FakeDataverse()
        ds = mkstore(dv)
        await ds.save_notes(call(), {"fields": {"complaint": "leak"}})
        dv.fail_operation = 2
        with pytest.raises(httpx.HTTPStatusError):
            await ds.create_customer("call-1", "New Customer", "+15550199")
        assert not dv.posts
        dv.lose_response = True
        with pytest.raises(httpx.ReadTimeout):
            await ds.create_customer("call-1", "New Customer", "+15550199")
        await ds.create_customer("call-1", "New Customer", "+15550199")
        assert len([b for table, b in dv.posts if table == "bdm_customers"]) == 1
        assert dv.draft["_bdm_customerid_value"]
    asyncio.run(run())


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"status": "ok"}),
    batch_response([204]),
    httpx.Response(200, content=b"--broken", headers={"content-type": "multipart/mixed; boundary=broken"}),
])
def test_incomplete_changeset_envelope_is_not_success(response):
    async def run():
        ds = mkstore(lambda req: response)
        with pytest.raises(RuntimeError, match="incomplete Dataverse changeset"):
            await ds._changeset([("POST", "bdm_servicetickets", {}, {}),
                                 ("PATCH", "bdm_servicerequestdrafts(d1)", {}, {"If-Match": 'W/"1"'})])
    asyncio.run(run())
