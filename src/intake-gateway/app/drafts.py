"""Draft store: persists the live service-request draft in Dataverse and creates the ticket on rep Submit.

Port of the C# DraftStore. Agents only ever feed `save_notes`; `submit` (a human click) is the only
code path that creates a service ticket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from .config import Settings
from .schema import OPT_BASE, Schema
from .http import request

log = logging.getLogger("gateway.drafts")

DRAFT = "bdm_servicerequestdrafts"
DRAFT_INTEGRITY_COLUMNS = (
    "bdm_servicerequestdraftid", "bdm_callid", "bdm_status", "bdm_editedfields", "bdm_summary",
    "bdm_transcript", "bdm_transcriptversion", "bdm_TicketId", "_bdm_ticketid_value",
)
CONTACT_SELECT = "bdm_contactid,bdm_name,bdm_firstname,bdm_lastname,bdm_primaryphone,bdm_email,_bdm_customerid_value"
CUST_SELECT = "bdm_customerid,bdm_name,bdm_accountnumber,bdm_firstname,bdm_lastname,bdm_primaryphone,bdm_email,bdm_street,bdm_city,bdm_state,bdm_zip"
EDITABLE = {
    "customername": "bdm_customername",
    "contactphone": "bdm_contactphone",
    "equipmenttext": "bdm_equipmenttext",
    "assetnumber": "bdm_assetnumber",
    "location": "bdm_location",
    "preferredtime": "bdm_preferredtime",
    "summary": "bdm_summary",
}


class DraftError(Exception):
    """A problem the rep can act on (bad state or input); mapped to HTTP 400."""


class DraftConflict(DraftError):
    """Concurrent changes prevented a safe write; the caller can retry."""


def record_id(kind: str, identity: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"intake-gateway:{kind}:{identity}"))


def etag(row: dict) -> str:
    value = row.get("@odata.etag")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Dataverse did not return an ETag; refusing an unprotected update")
    return value


def _esc(s: str) -> str:
    return quote(s.replace("'", "''"), safe="")


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def priority_opt(p: Any) -> int:
    return OPT_BASE + {"low": 0, "medium": 1, "normal": 1, "high": 2, "critical": 3, "urgent": 3}.get(str(p or "").lower(), 1)


def transcript_text(st) -> str:
    return "\n".join(f"[{t['speaker']}] {t['text']}" for t in st.transcript)


def enrichment_text(raw: Any) -> str:
    """Plain-text rendering of the AI enrichment for the ticket notes."""
    try:
        e = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return ""
    if not isinstance(e, dict) or not e:
        return ""
    out = ["AI suggested (Foundry IQ):"]
    if e.get("likely_cause"):
        out.append(f"Likely cause: {e['likely_cause']}")
    if e.get("bulletin"):
        out.append(f"Referenced bulletin: {e['bulletin']}")
    parts = e.get("parts")
    if isinstance(parts, list) and parts:
        out.append(
            "Parts to check: "
            + "; ".join(
                f"{p.get('number', '')} {p.get('name', '')}".strip() + (f" (${p['unit_cost']})" if p.get("unit_cost") is not None else "")
                for p in parts
                if isinstance(p, dict)
            )
        )
    if e.get("suggested_priority"):
        out.append(f"Suggested priority: {e['suggested_priority']}" + (f" - {e['priority_reason']}" if e.get("priority_reason") else ""))
    steps = e.get("diagnostic_steps")
    if isinstance(steps, list):
        out += [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
    srcs = e.get("sources")
    if isinstance(srcs, list) and srcs:
        out.append("Sources: " + ", ".join(str(s) for s in srcs))
    return "\n".join(out) + "\n"


def _soundex(s: str) -> str:
    letters = re.sub(r"[^A-Z]", "", s.upper())
    if not letters:
        return ""
    codes = "01230120022455012623010202"  # A..Z
    out, last = letters[0], codes[ord(letters[0]) - 65]
    for ch in letters[1:]:
        c = codes[ord(ch) - 65]
        if c != "0" and c != last:
            out += c
        if ch not in "HW":
            last = c
    return out.ljust(4, "0")[:4]


def _sim(a: str, b: str) -> float:
    a, b = a.lower(), b.lower()
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    s = 1.0 - prev[len(b)] / max(len(a), len(b))
    return max(s, 0.85) if _soundex(a) == _soundex(b) else s


def rank_customers(customers: list[dict], name: str | None, phones: list[str | None], phone_cols=("bdm_primaryphone",)) -> list[dict]:
    """Rank customers against a possibly misheard name and any phone numbers from the call."""
    tokens = [t for t in re.split(r"[ ,.]+", name or "") if any(c.isalpha() for c in t)]
    tails = {d[-10:] for d in (_digits(p) for p in phones) if len(d) >= 10}
    scored: list[tuple[float, str, dict]] = []
    for c in customers:
        first, last = c.get("bdm_firstname") or "", c.get("bdm_lastname") or ""
        words = [first, last] if (first or last) else [w for w in re.split(r"[ ,.]+", c.get("bdm_name") or "") if w]
        ns = sum(max((_sim(t, w) for w in words), default=0.0) for t in tokens) / len(tokens) if tokens else 0.0
        hit = any(len(ph) >= 10 and ph[-10:] in tails for ph in (_digits(c.get(col)) for col in phone_cols))
        if ns >= 0.6 or hit:
            why = "name + phone match" if hit and ns >= 0.6 else "phone match" if hit else "similar name"
            scored.append((ns + (0.6 if hit else 0), why, c))
    scored.sort(key=lambda x: -x[0])
    return [{**c, "score": round(min(sc, 1.0), 2), "why": why} for sc, why, c in scored[:5]]


class DraftStore:
    def __init__(self, settings: Settings, tokens=None, client: httpx.AsyncClient | None = None, schema: Schema | None = None):
        self.org = settings.dataverse_url.rstrip("/")
        self.api = self.org + "/api/data/v9.2/"
        self.enrichment = settings.enrichment
        self._tokens = tokens
        self._http = client or httpx.AsyncClient(timeout=60)
        self._owns_http = client is None
        self._schema_lock = asyncio.Lock()
        self.brand = settings.brand_name
        self._resolved = schema is not None
        self._set_schema(schema or Schema.load(settings.dataverse_schema, settings.dataverse_schema_file))

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _set_schema(self, schema: Schema) -> None:
        self.schema = schema
        self.phone_cols = ["bdm_primaryphone"] + schema.phone_columns
        self.cust_select = CUST_SELECT + "".join("," + c for c in schema.phone_columns)
        self.contact_phone_cols = ["bdm_primaryphone"] + schema.contact_phone_columns
        self.contact_select = CONTACT_SELECT + "".join("," + c for c in schema.contact_phone_columns)

    async def _ensure_schema(self) -> None:
        if self._resolved or not self.schema.roles:
            return
        async with self._schema_lock:
            if not self._resolved:
                from .discover import resolve_schema

                tok = await self._tokens.get(self.org + "/.default")
                self._set_schema(await resolve_schema(self.schema, self._http, self.org, tok))
                self._resolved = True

    @property
    def schema_hash(self) -> str:
        return self.schema.hash

    def _draft_select(self) -> str:
        cols = ("bdm_servicerequestdraftid,bdm_name,bdm_callid,bdm_callerphone,bdm_customername,bdm_equipmenttext,bdm_complaint,"
                "bdm_priority,bdm_summary,bdm_transcript,bdm_coaching,bdm_status,bdm_submittedby,bdm_submittedon,bdm_contactphone,"
                "bdm_assetnumber,bdm_location,bdm_preferredtime,bdm_editedfields,createdon,_bdm_customerid_value,"
                "_bdm_equipmentid_value,_bdm_ticketid_value,bdm_transcriptversion")
        if self.schema.contacts:
            cols += ",_bdm_contactid_value"
        # the column only exists after re-running dataverse\provision-dataverse.ps1
        return cols + (",bdm_enrichmentjson" if self.enrichment else "")

    async def _send(self, method: str, path: str, body: dict | None = None, want_id: bool = False,
                    *, headers: dict[str, str] | None = None) -> Any:
        await self._ensure_schema()
        tok = await self._tokens.get(self.org + "/.default")
        r = await request(
            self._http,
            method,
            self.api + self.schema.path(path),
            retry_safe=method == "GET",
            json=self.schema.body(path, body),
            headers={
                "Authorization": f"Bearer {tok}",
                "OData-Version": "4.0",
                "Prefer": 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"',
                **(headers or {}),
            },
        )
        r.raise_for_status()
        if want_id:
            loc = r.headers.get("OData-EntityId", "")
            return loc[loc.rfind("(") + 1 : -1]
        return self.schema.row(path, r.json()) if r.content else None

    async def _rows(self, path: str) -> list[dict]:
        return ((await self._send("GET", path)) or {}).get("value", [])

    async def find_draft(self, call_id: str) -> dict | None:
        rows = await self._rows(f"{DRAFT}?$select={self._draft_select()}&$filter=bdm_callid eq '{_esc(call_id)}'&$top=2")
        if len(rows) > 1:
            raise DraftConflict("multiple drafts have this call ID; reconcile them before writing")
        return rows[0] if rows else None

    async def _write_draft(self, call_id: str, build: Callable[[dict | None], Awaitable[dict | None]]) -> None:
        for _ in range(5):
            draft = await self.find_draft(call_id)
            body = await build(draft)
            if not body:
                return
            did = draft["bdm_servicerequestdraftid"] if draft else record_id("draft", call_id)
            if draft is None:
                body = {"bdm_name": f"Call {call_id}", "bdm_callid": call_id,
                        "bdm_status": OPT_BASE, "bdm_priority": priority_opt(None), **body}
            try:
                await self._send("PATCH", f"{DRAFT}({did})", body,
                                 headers={"If-Match": etag(draft)} if draft else {"If-None-Match": "*"})
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 412:
                    raise
        raise DraftConflict("draft changed repeatedly; retry the operation")

    async def _changeset(self, operations: list[tuple[str, str, dict, dict[str, str]]]) -> None:
        """One atomic Dataverse transaction; inspect inner HTTP responses, not just the envelope."""
        await self._ensure_schema()
        boundary, change = f"batch_{uuid4().hex}", f"changeset_{uuid4().hex}"
        parts = [f"--{boundary}\r\nContent-Type: multipart/mixed; boundary={change}\r\n"]
        for i, (method, path, body, headers) in enumerate(operations, 1):
            lines = [f"--{change}", "Content-Type: application/http", "Content-Transfer-Encoding: binary",
                     f"Content-ID: {i}", "", f"{method} {self.api}{self.schema.path(path)} HTTP/1.1",
                     "Content-Type: application/json", "OData-Version: 4.0"]
            lines += [f"{k}: {v}" for k, v in headers.items()]
            parts.append("\r\n".join(lines) + "\r\n\r\n" + json.dumps(self.schema.body(path, body)) + "\r\n")
        parts.append(f"--{change}--\r\n--{boundary}--\r\n")
        token = await self._tokens.get(self.org + "/.default")
        r = await self._http.post(
            self.api + "$batch", content="\r\n".join(parts).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/mixed; boundary={boundary}",
                     "OData-Version": "4.0"},
        )
        r.raise_for_status()
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {r.headers.get('content-type', '')}\r\nMIME-Version: 1.0\r\n\r\n".encode() + r.content
        )
        statuses = []
        for part in message.walk():
            if part.get_content_type() != "application/http":
                continue
            raw = part.get_payload(decode=True) or b""
            match = re.match(rb"HTTP/1\.[01] (\d{3})", raw)
            if not match:
                raise RuntimeError("invalid Dataverse changeset response")
            status = int(match[1])
            statuses.append(status)
            if status >= 300:
                inner = httpx.Response(status, content=raw, request=r.request)
                inner.raise_for_status()
        if len(statuses) != len(operations):
            raise RuntimeError("incomplete Dataverse changeset response; verify the draft before retrying")

    async def match_customer(self, phone: str) -> tuple[str | None, str | None]:
        d = _digits(phone)[-10:]
        if len(d) < 7:
            return None, None
        rows = await self._rows(f"bdm_customers?$select={self.cust_select}&$filter={self._phone_filter(d[-4:])}&$top=50")
        hits = [r for r in rows if any(_digits(r.get(c)).endswith(d[-7:]) for c in self.phone_cols)]
        return (hits[0]["bdm_customerid"], hits[0].get("bdm_name")) if len(hits) == 1 else (None, None)

    async def match_contact(self, phone: str) -> dict | None:
        """The one contact whose mobile/direct line matches the caller's number (None when absent or ambiguous)."""
        d = _digits(phone)[-10:]
        if not self.schema.contacts or len(d) < 7:
            return None
        flt = "(" + " or ".join(f"contains({c},'{d[-4:]}')" for c in self.contact_phone_cols) + ")"
        rows = await self._rows(f"bdm_contacts?$select={self.contact_select}&$filter={flt}&$top=50")
        hits = [r for r in rows if any(_digits(r.get(c)).endswith(d[-7:]) for c in self.contact_phone_cols)]
        return hits[0] if len(hits) == 1 else None

    async def match_caller(self, phone: str) -> dict:
        """Who is calling: the matching contact (and its parent account) first, then an account's own phone."""
        contact = await self.match_contact(phone)
        parent = (contact or {}).get("_bdm_customerid_value")
        if contact and parent:
            cust = await self.get_customer(parent)
            if cust:
                return {"customerId": parent, "customerName": cust.get("bdm_name"), "contactId": contact["bdm_contactid"], "contactName": contact.get("bdm_name")}
        cid, cname = await self.match_customer(phone)
        return {"customerId": cid, "customerName": cname, "contactId": None, "contactName": None}

    async def get_contact(self, contact_id: str) -> dict | None:
        if not self.schema.contacts:
            return None
        return await self._send("GET", f"bdm_contacts({_esc(contact_id)})?$select={self.contact_select}")

    async def list_contacts(self, customer_id: str | None) -> list[dict]:
        if not self.schema.contacts or not customer_id:
            return []
        return await self._rows(f"bdm_contacts?$select={self.contact_select}&$filter=_bdm_customerid_value eq {_esc(customer_id)}&$top=50")

    async def validate_ai(self, st, result: dict) -> None:
        tables = {"bdm_equipment": "bdm_equipments", "bdm_customer": "bdm_customers",
                  "bdm_contact": "bdm_contacts", "bdm_serviceticket": "bdm_servicetickets"}
        draft = await self.find_draft(st.call_id) if result.get("matches") else None
        customer = (draft or {}).get("_bdm_customerid_value")
        for match in result.get("matches", []):
            table = tables.get(match["table"])
            if table is None or not self.schema.enabled(table):
                raise DraftError("unsupported AI match table")
            row = await self._record(f"{table}({_esc(match['id'])})", "AI match")
            if table != "bdm_customers" and customer and row.get("_bdm_customerid_value") != customer:
                raise DraftError("AI match does not belong to the selected customer")

    async def save_notes(self, st, result: dict | None) -> None:
        """Create the draft on first use, then refresh the agent-owned fields.

        Customer and equipment links are only set while empty and rep-edited fields are never overwritten.
        """
        async def build(draft: dict | None) -> dict | None:
            f = (result or {}).get("fields") or st.fields or {}
            if draft is not None and draft.get("bdm_status") != OPT_BASE:
                return  # already submitted
            edited = {x for x in ((draft or {}).get("bdm_editedfields") or "").split(",") if x}
            body: dict[str, Any] = {
                "bdm_callerphone": st.caller_phone,
                "bdm_coaching": (result or {}).get("next_question"),
            }

            def put(key: str, col: str, v: Any) -> None:
                if key not in edited and isinstance(v, str) and v.strip():
                    body[col] = v

            put("customername", "bdm_customername", f.get("customer_name"))
            put("contactphone", "bdm_contactphone", f.get("phone") or st.caller_phone)
            put("equipmenttext", "bdm_equipmenttext", f.get("equipment_model"))
            put("assetnumber", "bdm_assetnumber", f.get("equipment_serial_or_asset"))
            put("location", "bdm_location", f.get("location"))
            put("preferredtime", "bdm_preferredtime", f.get("preferred_time"))
            put("complaint", "bdm_complaint", f.get("complaint"))
            put("summary", "bdm_summary", f.get("summary"))
            enr = (result or {}).get("enrichment", st.enrichment)
            if self.enrichment and isinstance(enr, dict) and enr:
                body["bdm_enrichmentjson"] = json.dumps(enr)
            if "priority" not in edited and f.get("priority"):
                body["bdm_priority"] = priority_opt(f["priority"])
            elif draft is None:
                body["bdm_priority"] = priority_opt(None)

            if "customerId" not in edited and (draft is None or draft.get("_bdm_customerid_value") is None):
                who = await self.match_caller(st.caller_phone)
                cid, cname = who["customerId"], who["customerName"]
                if cid:
                    body["bdm_CustomerId@odata.bind"] = f"/bdm_customers({cid})"
                if "contactId" not in edited and who["contactId"] and not (draft or {}).get("_bdm_contactid_value"):
                    body["bdm_ContactId@odata.bind"] = f"/bdm_contacts({who['contactId']})"
                if (
                    cname
                    and "customername" not in edited
                    and not (draft or {}).get("bdm_customername")
                    and not body.get("bdm_customername")
                ):
                    body["bdm_customername"] = cname
            if ("equipmentId" not in edited and self.schema.enabled("bdm_equipments")
                    and (draft is None or draft.get("_bdm_equipmentid_value") is None)):
                eq = next(
                    (m.get("id") for m in (result or {}).get("matches") or [] if isinstance(m, dict) and m.get("table") == "bdm_equipment"),
                    None,
                )
                asset = f.get("equipment_serial_or_asset")
                if not eq and isinstance(asset, str) and asset.strip():
                    rows = await self._rows(f"bdm_equipments?$select=bdm_equipmentid&$filter=bdm_assetnumber eq '{_esc(asset.strip())}'&$top=2")
                    if len(rows) == 1:
                        eq = rows[0]["bdm_equipmentid"]
                customer_bind = body.get("bdm_CustomerId@odata.bind")
                customer = customer_bind.split("(")[1][:-1] if customer_bind else (draft or {}).get("_bdm_customerid_value")
                if eq and customer:
                    await self._validate_links(draft or {}, {"customerId": customer, "equipmentId": eq})
                    body["bdm_EquipmentId@odata.bind"] = f"/bdm_equipments({eq})"

            return body

        await self._write_draft(st.call_id, build)

    async def save_transcript(self, st) -> None:
        async def build(draft: dict | None) -> dict | None:
            if draft and (draft.get("bdm_transcriptversion") or 0) >= st.version:
                return None
            return {"bdm_callerphone": st.caller_phone, "bdm_transcript": transcript_text(st),
                    "bdm_transcriptversion": st.version}

        await self._write_draft(st.call_id, build)

    def _phone_filter(self, digits: str) -> str:
        return "(" + " or ".join(f"contains({c},'{digits}')" for c in self.phone_cols) + ")"

    def _name_filter(self, tokens: list[str]) -> str:
        cols = ["bdm_name"] + (["bdm_firstname", "bdm_lastname"] if self.schema.supports("bdm_customers", "bdm_firstname") else [])
        return "(" + " or ".join(f"contains({c},'{_esc(t)}')" for t in tokens for c in cols) + ")"

    async def suggest_customers(self, name: str | None, phones: list[str | None]) -> list[dict]:
        """Server-side candidate query (phone tails and name fragments), then fuzzy ranking in memory."""
        parts = [self._phone_filter(d[-4:]) for d in {_digits(p) for p in phones} if len(d) >= 10]
        tokens = [t[:3] for t in re.split(r"[ ,.]+", name or "") if len(t) >= 3 and any(c.isalpha() for c in t)]
        if tokens:
            parts.append(self._name_filter(tokens))
        if not parts:
            return []
        rows = await self._rows(f"bdm_customers?$select={self.cust_select}&$filter={' or '.join(parts)}&$top=100")
        return rank_customers(rows, name, phones, self.phone_cols)

    async def search_customers(self, q: str) -> list[dict]:
        q = q.strip()
        d = _digits(q)
        if len(d) < 4 and any(c.isalpha() for c in q):
            return await self.suggest_customers(q, [])
        if len(d) >= 4:
            rows = await self._rows(f"bdm_customers?$select={self.cust_select}&$filter={self._phone_filter(d[-4:])}&$top=50")
            return [r for r in rows if any(d in _digits(r.get(c)) for c in self.phone_cols)][:15]
        words = q.split()
        flt = " and ".join(self._name_filter([w]) for w in words) or "bdm_customerid ne null"
        return await self._rows(f"bdm_customers?$select={self.cust_select}&$filter={flt}&$top=15")

    async def get_customer(self, cid: str) -> dict | None:
        return await self._record(f"bdm_customers({_esc(cid)})?$select={self.cust_select}", "customer")

    async def _record(self, path: str, kind: str) -> dict:
        try:
            return await self._send("GET", path)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise LookupError(f"{kind} not found") from error
            raise

    async def _validate_links(self, draft: dict, edit: dict) -> None:
        if not {"customerId", "equipmentId", "contactId"} & edit.keys():
            return
        customer = edit.get("customerId") if "customerId" in edit else draft.get("_bdm_customerid_value")
        equipment = edit.get("equipmentId") if "equipmentId" in edit else draft.get("_bdm_equipmentid_value")
        contact = edit.get("contactId") if "contactId" in edit else draft.get("_bdm_contactid_value")
        if customer:
            await self.get_customer(customer)
        for identity, table, kind, enabled in (
            (equipment, "bdm_equipments", "equipment", self.schema.enabled("bdm_equipments")),
            (contact, "bdm_contacts", "contact", self.schema.contacts),
        ):
            if identity:
                if not enabled:
                    raise DraftError(f"{kind} is not enabled")
                row = await self._record(f"{table}({_esc(identity)})?$select=_bdm_customerid_value", kind)
                if not customer or row.get("_bdm_customerid_value") != customer:
                    raise DraftError(f"{kind} must belong to the selected customer; replace or explicitly clear the link")

    async def list_equipment(self, customer_id: str | None) -> list[dict]:
        if not self.schema.enabled("bdm_equipments"):
            return []
        flt = f"&$filter=_bdm_customerid_value eq {_esc(customer_id)}" if customer_id else ""
        return await self._rows(f"bdm_equipments?$select=bdm_equipmentid,bdm_name,bdm_assetnumber,bdm_manufacturer,bdm_plant{flt}&$top=50")

    async def list_drafts(self) -> list[dict]:
        return await self._rows(
            f"{DRAFT}?$select=bdm_callid,bdm_name,bdm_callerphone,bdm_customername,bdm_equipmenttext,bdm_complaint,bdm_priority,"
            "bdm_status,bdm_submittedby,createdon&$orderby=createdon desc&$top=100"
        )

    async def list_tickets(self) -> list[dict]:
        return await self._rows(
            "bdm_servicetickets?$select=bdm_name,bdm_priority,bdm_status,bdm_openedon,createdon,_bdm_customerid_value,"
            "_bdm_equipmentid_value&$orderby=createdon desc&$top=100"
        )

    async def ticket_name(self, tid: str) -> str | None:
        return ((await self._send("GET", f"bdm_servicetickets({_esc(tid)})?$select=bdm_name")) or {}).get("bdm_name")

    async def get_detail(self, call_id: str, live_phone: str | None = None) -> dict:
        d = await self.find_draft(call_id)
        ticket = await self.ticket_name(d["_bdm_ticketid_value"]) if d and d.get("_bdm_ticketid_value") else None
        customer = candidates = None
        cid = d.get("_bdm_customerid_value") if d else None
        if cid:
            customer = await self.get_customer(cid)
        else:
            candidates = await self.suggest_customers(
                (d or {}).get("bdm_customername"), [(d or {}).get("bdm_contactphone"), (d or {}).get("bdm_callerphone") or live_phone]
            )
        contact = None
        if d and d.get("_bdm_contactid_value"):
            contact = await self.get_contact(d["_bdm_contactid_value"])
        enr = None
        if self.enrichment and d and d.get("bdm_enrichmentjson"):
            try:
                enr = json.loads(d["bdm_enrichmentjson"])
            except ValueError:
                pass
        return {"callId": call_id, "draft": d, "customer": customer, "candidates": candidates, "contact": contact, "ticket": ticket, "enrichment": enr}

    async def _open_draft(self, call_id: str) -> dict:
        d = await self.find_draft(call_id)
        if d is None:
            raise LookupError("draft not found")
        if d.get("bdm_status") != OPT_BASE:
            raise DraftError("draft already submitted")
        return d

    async def update(self, call_id: str, edit: dict) -> None:
        async def build(d: dict | None) -> dict:
            if d is None:
                raise LookupError("draft not found")
            if d.get("bdm_status") != OPT_BASE:
                raise DraftError("draft already submitted")
            await self._validate_links(d, edit)
            b: dict[str, Any] = {}
            if "customerId" in edit:
                b["bdm_CustomerId@odata.bind"] = f"/bdm_customers({edit['customerId']})" if edit["customerId"] else None
                if "customername" not in edit and not (d.get("bdm_customername") or "").strip():
                    nm = ((await self.get_customer(edit["customerId"])) or {}).get("bdm_name") if edit["customerId"] else None
                    if nm:
                        b["bdm_customername"] = nm
            if "contactId" in edit and self.schema.contacts:
                b["bdm_ContactId@odata.bind"] = f"/bdm_contacts({edit['contactId']})" if edit["contactId"] else None
            if "equipmentId" in edit and self.schema.enabled("bdm_equipments"):
                b["bdm_EquipmentId@odata.bind"] = f"/bdm_equipments({edit['equipmentId']})" if edit["equipmentId"] else None
            edited = {x for x in (d.get("bdm_editedfields") or "").split(",") if x}
            edited.update({"customerId", "equipmentId", "contactId"} & edit.keys())
            for key, col in EDITABLE.items():
                if key in edit:
                    b[col] = edit[key]
                    edited.add(key)
            if "complaint" in edit:
                b["bdm_complaint"] = edit["complaint"]
                edited.add("complaint")
            if "priority" in edit:
                b["bdm_priority"] = priority_opt(edit["priority"])
                edited.add("priority")
            if b:
                b["bdm_editedfields"] = ",".join(sorted(edited))
            return b

        await self._write_draft(call_id, build)

    async def create_customer(self, call_id: str, full_name: str, phone: str | None) -> None:
        d = await self._open_draft(call_id)
        full_name = (full_name or "").strip()
        if not full_name:
            raise DraftError("enter the customer's name first")
        parts = full_name.split(None, 1)
        cid = record_id("customer", d["bdm_servicerequestdraftid"])
        if d.get("_bdm_customerid_value") == cid:
            return
        if d.get("_bdm_equipmentid_value") or d.get("_bdm_contactid_value"):
            raise DraftError("clear existing equipment and contact links before creating a new customer")
        await self._changeset([
            ("POST", "bdm_customers", {
                "bdm_customerid": cid,
                "bdm_name": full_name.upper(),
                "bdm_accountnumber": f"NEW-{datetime.now(timezone.utc):%y%m%d%H%M%S}",
                "bdm_firstname": parts[0],
                "bdm_lastname": parts[1] if len(parts) > 1 else "",
                "bdm_primaryphone": phone or "",
            }, {}),
            ("PATCH", f"{DRAFT}({d['bdm_servicerequestdraftid']})",
             {"bdm_CustomerId@odata.bind": f"/bdm_customers({cid})",
              "bdm_editedfields": ",".join(sorted(set((d.get("bdm_editedfields") or "").split(",")) - {""} | {"customerId"}))},
             {"If-Match": etag(d)}),
        ])

    async def submit(self, call_id: str, submitted_by: str) -> str:
        """The rep's Submit click: the only place a service ticket is created."""
        for _ in range(5):
            d = await self.find_draft(call_id)
            if d is None:
                raise LookupError("draft not found")
            if d.get("bdm_status") == OPT_BASE + 1 and d.get("_bdm_ticketid_value"):
                name = await self.ticket_name(d["_bdm_ticketid_value"])
                if not name:
                    raise RuntimeError("submitted draft has no readable ticket")
                return name
            if d.get("bdm_status") != OPT_BASE:
                raise DraftError("draft already submitted")
            equip = self.schema.enabled("bdm_equipments")
            cust, eq = d.get("_bdm_customerid_value"), d.get("_bdm_equipmentid_value")
            if not cust or (equip and not eq):
                raise DraftError("choose a customer and equipment first" if equip else "choose a customer first")
            await self._validate_links(d, {"customerId": cust})
            contact_id = d.get("_bdm_contactid_value")
            contact = (await self.get_contact(contact_id) if contact_id else None) or {}
            cinfo = (await self.get_customer(cust)) or {}
            acct = cinfo.get("bdm_accountnumber") or cinfo.get("bdm_name") or "NA"
            now = datetime.now(timezone.utc)
            title = f"{self.brand}-Voice-{acct}-{now:%Y%m%d%H%M}"
            lines = [
                f"Call ID: {call_id}",
                f"Customer: {d.get('bdm_customername') or ''}",
                *([f"Caller: {contact['bdm_name']}"] if contact.get("bdm_name") else []),
                f"Contact phone: {d.get('bdm_contactphone') or d.get('bdm_callerphone') or ''}",
                f"Equipment: {d.get('bdm_equipmenttext') or ''} {d.get('bdm_assetnumber') or ''}".rstrip(),
                f"Location: {d.get('bdm_location') or ''}",
                f"Preferred time: {d.get('bdm_preferredtime') or ''}",
                "",
                d.get("bdm_complaint") or "",
                "",
                "Call summary:",
                d.get("bdm_summary") or "",
            ]
            if self.enrichment:
                et = enrichment_text(d.get("bdm_enrichmentjson"))
                if et:
                    lines += ["", et]
            full = "\n".join(lines)
            cap = self.schema.description_max
            overflow = bool(cap) and len(full) > cap
            if overflow:
                tail = "\n[truncated - full notes in the attached note]" if self.schema.note_bind else "\n[truncated]"
                full_text, full = full, (full[:max(0, cap - len(tail))] + tail)[:cap]
            tid = record_id("ticket", d["bdm_servicerequestdraftid"])
            operations = [
                ("POST", "bdm_servicetickets", {
                    "bdm_serviceticketid": tid,
                    "bdm_name": title,
                    "bdm_description": full,
                    "bdm_priority": d.get("bdm_priority") or OPT_BASE + 1,
                    "bdm_status": OPT_BASE,
                    "bdm_openedon": now.isoformat(),
                    "bdm_EquipmentId@odata.bind": f"/bdm_equipments({eq})" if eq else None,
                    "bdm_CustomerId@odata.bind": f"/bdm_customers({cust})",
                    **({"bdm_ContactId@odata.bind": f"/bdm_contacts({contact_id})"} if contact_id else {}),
                }, {}),
            ]
            if overflow and self.schema.note_bind:
                operations.append(
                    ("POST", "annotations",
                     {"annotationid": record_id("note", tid), "subject": "Call notes", "notetext": full_text,
                      f"{self.schema.note_bind}@odata.bind": f"/{self.schema.real_table('bdm_servicetickets')}({tid})"}, {})
                )
            operations.append((
                "PATCH", f"{DRAFT}({d['bdm_servicerequestdraftid']})",
                {
                    "bdm_status": OPT_BASE + 1,
                    "bdm_submittedby": submitted_by,
                    "bdm_submittedon": now.isoformat(),
                    "bdm_TicketId@odata.bind": f"/bdm_servicetickets({tid})",
                }, {"If-Match": etag(d)},
            ))
            try:
                await self._changeset(operations)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 412:
                    continue
                if e.response.status_code in (400, 409):
                    # A competing transaction may have created the deterministic ticket first.
                    latest = await self.find_draft(call_id)
                    if (latest and latest.get("bdm_status") == OPT_BASE + 1
                            and latest.get("_bdm_ticketid_value") == tid):
                        name = await self.ticket_name(tid)
                        if name:
                            return name
                raise
            return title
        raise DraftConflict("draft changed repeatedly; retry Submit")
