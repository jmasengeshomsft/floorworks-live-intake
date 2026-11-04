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
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .schema import OPT_BASE, Schema

log = logging.getLogger("gateway.drafts")

DRAFT = "bdm_servicerequestdrafts"
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


def rank_customers(customers: list[dict], name: str | None, phones: list[str | None]) -> list[dict]:
    """Rank customers against a possibly misheard name and any phone numbers from the call."""
    tokens = [t for t in re.split(r"[ ,.]+", name or "") if any(c.isalpha() for c in t)]
    tails = {d[-10:] for d in (_digits(p) for p in phones) if len(d) >= 10}
    scored: list[tuple[float, str, dict]] = []
    for c in customers:
        first, last = c.get("bdm_firstname") or "", c.get("bdm_lastname") or ""
        words = [first, last] if (first or last) else [w for w in re.split(r"[ ,.]+", c.get("bdm_name") or "") if w]
        ns = sum(max((_sim(t, w) for w in words), default=0.0) for t in tokens) / len(tokens) if tokens else 0.0
        ph = _digits(c.get("bdm_primaryphone"))
        hit = len(ph) >= 10 and ph[-10:] in tails
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
        self._lock = asyncio.Lock()
        self.schema = schema or         Schema.load(settings.dataverse_schema, settings.dataverse_schema_file, settings.dataverse_profile)
        self._cust: tuple[float, list[dict]] | None = None

    def _draft_select(self) -> str:
        cols = ("bdm_servicerequestdraftid,bdm_name,bdm_callid,bdm_callerphone,bdm_customername,bdm_equipmenttext,bdm_complaint,"
                "bdm_priority,bdm_summary,bdm_transcript,bdm_coaching,bdm_status,bdm_submittedby,bdm_submittedon,bdm_contactphone,"
                "bdm_assetnumber,bdm_location,bdm_preferredtime,bdm_editedfields,createdon,_bdm_customerid_value,"
                "_bdm_equipmentid_value,_bdm_ticketid_value")
        # the column only exists after re-running dataverse\provision-dataverse.ps1
        return cols + (",bdm_enrichmentjson" if self.enrichment else "")

    async def _send(self, method: str, path: str, body: dict | None = None, want_id: bool = False) -> Any:
        tok = await self._tokens.get(self.org + "/.default")
        r = await self._http.request(
            method,
            self.api + self.schema.path(path),
            json=self.schema.body(path, body),
            headers={
                "Authorization": f"Bearer {tok}",
                "OData-Version": "4.0",
                "Prefer": 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"',
            },
        )
        if r.status_code >= 300:
            raise RuntimeError(f"dataverse {r.status_code} {method} {path}: {r.text[:300]}")
        if want_id:
            loc = r.headers.get("OData-EntityId", "")
            return loc[loc.rfind("(") + 1 : -1]
        return self.schema.row(path, r.json()) if r.content else None

    async def _rows(self, path: str) -> list[dict]:
        return ((await self._send("GET", path)) or {}).get("value", [])

    async def find_draft(self, call_id: str) -> dict | None:
        rows = await self._rows(f"{DRAFT}?$select={self._draft_select()}&$filter=bdm_callid eq '{_esc(call_id)}'&$top=1")
        return rows[0] if rows else None

    async def match_customer(self, phone: str) -> tuple[str | None, str | None]:
        d = _digits(phone)[-10:]
        if len(d) < 7:
            return None, None
        rows = await self._rows(
            f"bdm_customers?$select=bdm_customerid,bdm_name,bdm_primaryphone&$filter=contains(bdm_primaryphone,'{d[-4:]}')&$top=50"
        )
        hits = [r for r in rows if _digits(r.get("bdm_primaryphone")).endswith(d[-7:])]
        return (hits[0]["bdm_customerid"], hits[0].get("bdm_name")) if len(hits) == 1 else (None, None)

    async def save_notes(self, st, result: dict | None) -> None:
        """Create the draft on first use, then refresh the agent-owned fields.

        Customer and equipment links are only set while empty and rep-edited fields are never overwritten.
        """
        async with self._lock:
            f = (result or {}).get("fields") or st.fields or {}
            draft = await self.find_draft(st.call_id)
            if draft is not None and draft.get("bdm_status") != OPT_BASE:
                return  # already submitted
            edited = {x for x in ((draft or {}).get("bdm_editedfields") or "").split(",") if x}
            body: dict[str, Any] = {
                "bdm_callerphone": st.caller_phone,
                "bdm_transcript": transcript_text(st),
                "bdm_summary": f.get("summary"),
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
            enr = st.enrichment
            if self.enrichment and isinstance(enr, dict) and enr:
                body["bdm_enrichmentjson"] = json.dumps(enr)
            if "priority" not in edited and f.get("priority"):
                body["bdm_priority"] = priority_opt(f["priority"])
            elif draft is None:
                body["bdm_priority"] = priority_opt(None)

            if draft is None or draft.get("_bdm_customerid_value") is None:
                cid, cname = await self.match_customer(st.caller_phone)
                if cid:
                    body["bdm_CustomerId@odata.bind"] = f"/bdm_customers({cid})"
                if (
                    cname
                    and "customername" not in edited
                    and not (draft or {}).get("bdm_customername")
                    and not body.get("bdm_customername")
                ):
                    body["bdm_customername"] = cname
            if self.schema.enabled("bdm_equipments") and (draft is None or draft.get("_bdm_equipmentid_value") is None):
                eq = next(
                    (m.get("id") for m in (result or {}).get("matches") or [] if isinstance(m, dict) and m.get("table") == "bdm_equipment"),
                    None,
                )
                asset = f.get("equipment_serial_or_asset")
                if not eq and isinstance(asset, str) and asset.strip():
                    rows = await self._rows(f"bdm_equipments?$select=bdm_equipmentid&$filter=bdm_assetnumber eq '{_esc(asset.strip())}'&$top=2")
                    if len(rows) == 1:
                        eq = rows[0]["bdm_equipmentid"]
                if eq:
                    body["bdm_EquipmentId@odata.bind"] = f"/bdm_equipments({eq})"

            if draft is None:
                body["bdm_name"] = f"Call {st.caller_phone} {datetime.now(timezone.utc):%Y-%m-%d %H:%M}"
                body["bdm_callid"] = st.call_id
                body["bdm_status"] = OPT_BASE
                await self._send("POST", DRAFT, body, want_id=True)
            else:
                await self._send("PATCH", f"{DRAFT}({draft['bdm_servicerequestdraftid']})", body)

    async def _all_customers(self) -> list[dict]:
        if self._cust is None or time.time() - self._cust[0] > 30:
            self._cust = (time.time(), await self._rows(f"bdm_customers?$select={CUST_SELECT}&$top=500"))
        return self._cust[1]

    async def suggest_customers(self, name: str | None, phones: list[str | None]) -> list[dict]:
        return rank_customers(await self._all_customers(), name, phones)

    async def search_customers(self, q: str) -> list[dict]:
        q = q.strip()
        d = _digits(q)
        if len(d) < 4 and any(c.isalpha() for c in q):
            return await self.suggest_customers(q, [])
        if len(d) >= 4:
            flt = f"contains(bdm_primaryphone,'{d}')"
        elif self.schema.supports("bdm_customers", "bdm_firstname"):
            flt = " and ".join(f"(contains(bdm_firstname,'{_esc(w)}') or contains(bdm_lastname,'{_esc(w)}'))" for w in q.split()) or "bdm_customerid ne null"
        else:
            flt = " and ".join(f"contains(bdm_name,'{_esc(w)}')" for w in q.split()) or "bdm_customerid ne null"
        return await self._rows(f"bdm_customers?$select={CUST_SELECT}&$filter={flt}&$top=15")

    async def get_customer(self, cid: str) -> dict | None:
        return await self._send("GET", f"bdm_customers({_esc(cid)})?$select={CUST_SELECT}")

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
        enr = None
        if self.enrichment and d and d.get("bdm_enrichmentjson"):
            try:
                enr = json.loads(d["bdm_enrichmentjson"])
            except ValueError:
                pass
        return {"callId": call_id, "draft": d, "customer": customer, "candidates": candidates, "ticket": ticket, "enrichment": enr}

    async def _open_draft(self, call_id: str) -> dict:
        d = await self.find_draft(call_id)
        if d is None:
            raise LookupError("draft not found")
        if d.get("bdm_status") != OPT_BASE:
            raise DraftError("draft already submitted")
        return d

    async def update(self, call_id: str, edit: dict) -> None:
        d = await self._open_draft(call_id)
        b: dict[str, Any] = {}
        if edit.get("customerId"):
            b["bdm_CustomerId@odata.bind"] = f"/bdm_customers({edit['customerId']})"
            if "customername" not in edit and not (d.get("bdm_customername") or "").strip():
                nm = ((await self.get_customer(edit["customerId"])) or {}).get("bdm_name")
                if nm:
                    b["bdm_customername"] = nm
        if edit.get("equipmentId"):
            b["bdm_EquipmentId@odata.bind"] = f"/bdm_equipments({edit['equipmentId']})"
        edited = {x for x in (d.get("bdm_editedfields") or "").split(",") if x}
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
            await self._send("PATCH", f"{DRAFT}({d['bdm_servicerequestdraftid']})", b)

    async def create_customer(self, call_id: str, full_name: str, phone: str | None) -> None:
        d = await self._open_draft(call_id)
        full_name = (full_name or "").strip()
        if not full_name:
            raise DraftError("enter the customer's name first")
        parts = full_name.split(None, 1)
        cid = await self._send(
            "POST",
            "bdm_customers",
            {
                "bdm_name": full_name.upper(),
                "bdm_accountnumber": f"FW-{datetime.now(timezone.utc):%y%m%d%H%M%S}",
                "bdm_firstname": parts[0],
                "bdm_lastname": parts[1] if len(parts) > 1 else "",
                "bdm_primaryphone": phone or "",
            },
            want_id=True,
        )
        self._cust = None
        await self._send("PATCH", f"{DRAFT}({d['bdm_servicerequestdraftid']})", {"bdm_CustomerId@odata.bind": f"/bdm_customers({cid})"})

    async def submit(self, call_id: str, submitted_by: str) -> str:
        """The rep's Submit click: the only place a service ticket is created."""
        async with self._lock:
            d = await self._open_draft(call_id)
            equip = self.schema.enabled("bdm_equipments")
            cust, eq = d.get("_bdm_customerid_value"), d.get("_bdm_equipmentid_value")
            if not cust or (equip and not eq):
                raise DraftError("choose a customer and equipment first" if equip else "choose a customer first")
            cinfo = (await self.get_customer(cust)) or {}
            acct = cinfo.get("bdm_accountnumber") or cinfo.get("bdm_name") or "NA"
            now = datetime.now(timezone.utc)
            title = f"FloorWorks-Voice-{acct}-{now:%Y%m%d%H%M}"
            lines = [
                f"Call ID: {call_id}",
                f"Customer: {d.get('bdm_customername') or ''}",
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
            tid = await self._send(
                "POST",
                "bdm_servicetickets",
                {
                    "bdm_name": title,
                    "bdm_description": "\n".join(lines),
                    "bdm_priority": d.get("bdm_priority") or OPT_BASE + 1,
                    "bdm_status": OPT_BASE,
                    "bdm_openedon": now.isoformat(),
                    "bdm_EquipmentId@odata.bind": f"/bdm_equipments({eq})" if eq else None,
                    "bdm_CustomerId@odata.bind": f"/bdm_customers({cust})",
                },
                want_id=True,
            )
            await self._send(
                "PATCH",
                f"{DRAFT}({d['bdm_servicerequestdraftid']})",
                {
                    "bdm_status": OPT_BASE + 1,
                    "bdm_submittedby": submitted_by,
                    "bdm_submittedon": now.isoformat(),
                    "bdm_TicketId@odata.bind": f"/bdm_servicetickets({tid})",
                },
            )
            return title
