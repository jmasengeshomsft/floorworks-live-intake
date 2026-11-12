"""Derive the Dataverse mapping from the live org's metadata.

The config names only which table plays which role:

    "roles": {"customer": "account", "ticket": "incident", "equipment": "bdm_equipment", "draft": "bdm_servicerequestdraft"}

`resolve` turns the org metadata of those tables into the same explicit mapping `Schema` already understands
(entity sets, columns, lookups, priority option values); anything written explicitly in the config wins over it.
A canonical column that exists on the real table keeps its name; otherwise well-known standard columns are tried
(accountnumber, telephone1, prioritycode, ...); otherwise the column is dropped (null).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

import httpx

from .schema import CANON_PRIORITY, Schema, SchemaError, _merge

ROLE_TABLE = {"customer": "bdm_customers", "ticket": "bdm_servicetickets", "equipment": "bdm_equipments", "draft": "bdm_servicerequestdrafts", "contact": "bdm_contacts"}
ROLES = set(ROLE_TABLE)
PREFERRED_LOOKUP = {("bdm_contacts", "bdm_customerid"): "parentcustomerid"}
LOOKUP_ROLE = {"bdm_customerid": "customer", "bdm_equipmentid": "equipment", "bdm_ticketid": "ticket", "bdm_contactid": "contact"}
LOOKUP_NAV = {"bdm_customerid": "bdm_CustomerId", "bdm_equipmentid": "bdm_EquipmentId", "bdm_ticketid": "bdm_TicketId", "bdm_contactid": "bdm_ContactId"}
TABLE_LOOKUPS = {
    "bdm_servicetickets": ("bdm_customerid", "bdm_equipmentid", "bdm_contactid"),
    "bdm_contacts": ("bdm_customerid",),
    "bdm_equipments": ("bdm_customerid",),
    "bdm_servicerequestdrafts": ("bdm_customerid", "bdm_equipmentid", "bdm_ticketid", "bdm_contactid"),
}
# canonical column -> standard columns to try when it is missing ($id/$name = primary id/name attribute)
FALLBACKS: dict[str, dict[str, list[str]]] = {
    "bdm_customers": {
        "bdm_customerid": ["$id"], "bdm_name": ["$name"], "bdm_accountnumber": ["accountnumber"],
        "bdm_primaryphone": ["telephone1", "mobilephone"], "bdm_email": ["emailaddress1"],
        "bdm_street": ["address1_line1"], "bdm_city": ["address1_city"], "bdm_state": ["address1_stateorprovince"],
        "bdm_zip": ["address1_postalcode"], "bdm_firstname": ["firstname"], "bdm_lastname": ["lastname"],
    },
    "bdm_servicetickets": {
        "bdm_serviceticketid": ["$id"], "bdm_name": ["$name"], "bdm_description": ["description"],
        "bdm_priority": ["prioritycode"], "bdm_status": [], "bdm_openedon": [],
    },
    "bdm_equipments": {"bdm_equipmentid": ["$id"], "bdm_name": ["$name"]},
    "bdm_contacts": {
        "bdm_contactid": ["$id"], "bdm_name": ["$name"], "bdm_primaryphone": ["mobilephone", "telephone1"],
        "bdm_email": ["emailaddress1"], "bdm_firstname": ["firstname"], "bdm_lastname": ["lastname"],
    },
    "bdm_servicerequestdrafts": {"bdm_servicerequestdraftid": ["$id"], "bdm_name": ["$name"]},
}
CANON_COLUMNS = {
    "bdm_customers": "bdm_customerid bdm_name bdm_accountnumber bdm_primaryphone bdm_email bdm_street bdm_city bdm_state bdm_zip bdm_firstname bdm_lastname".split(),
    "bdm_servicetickets": "bdm_serviceticketid bdm_name bdm_description bdm_priority bdm_status bdm_openedon".split(),
    "bdm_equipments": "bdm_equipmentid bdm_name bdm_assetnumber bdm_manufacturer bdm_plant".split(),
    "bdm_contacts": "bdm_contactid bdm_name bdm_primaryphone bdm_email bdm_firstname bdm_lastname".split(),
    "bdm_servicerequestdrafts": (
        "bdm_servicerequestdraftid bdm_name bdm_callid bdm_callerphone bdm_customername bdm_equipmenttext bdm_complaint bdm_priority "
        "bdm_summary bdm_transcript bdm_coaching bdm_status bdm_submittedby bdm_submittedon bdm_contactphone bdm_assetnumber "
        "bdm_location bdm_preferredtime bdm_editedfields bdm_enrichmentjson"
    ).split(),
}
PRIORITY_WORDS = {"low": ("low",), "medium": ("medium", "normal", "moderate"), "high": ("high", "important"), "urgent": ("urgent", "critical", "highest")}
_PHONE = re.compile(r"^(telephone\d|mobilephone)$")


def _real_logicals(roles: dict) -> dict[str, str | None]:
    out = {}
    for role, table in ROLE_TABLE.items():
        out[table] = roles[role] if role in roles else (None if role == "contact" else table.rstrip("s"))
    return out


def resolve(roles: dict, meta: dict[str, dict]) -> dict:
    """roles: role -> logical name; meta: logical name -> {entitySet, primaryId, primaryName, attrs, lookups, options}."""
    unknown = set(roles) - ROLES
    if unknown:
        raise SchemaError(f"roles has unknown keys: {sorted(unknown)}")
    logical = _real_logicals(roles)
    out: dict[str, Any] = {"logicalNames": {}, "names": {}, "columns": {}, "disabled": []}
    problems: list[str] = []
    for table, lg in logical.items():
        if lg is None or lg not in meta:
            if table == "bdm_contacts":
                if lg is not None:
                    out["disabled"].append(table)
                continue
            if table == "bdm_equipments":
                out["disabled"].append(table)
                continue
            problems.append(f"{table}: table '{lg}' not found in the org")
            continue
        m = meta[lg]
        attrs = m["attrs"]
        out["logicalNames"][table] = lg
        if m["entitySet"] != table:
            out["names"][table] = m["entitySet"]
        cols: dict[str, str | None] = {}
        for c in CANON_COLUMNS[table]:
            if c in attrs:
                continue
            real = None
            for cand in FALLBACKS.get(table, {}).get(c, []):
                cand = m["primaryId"] if cand == "$id" else m["primaryName"] if cand == "$name" else cand
                if cand in attrs:
                    real = cand
                    break
            cols[c] = real
        for lk in TABLE_LOOKUPS.get(table, ()):
            target = logical.get(ROLE_TABLE[LOOKUP_ROLE[lk]])
            found = [x for x in m.get("lookups", []) if target and x["referenced"] == target]
            prefer = PREFERRED_LOOKUP.get((table, lk))
            pick = next((x for x in found if x["attr"] == lk), None) or next((x for x in found if x["attr"] == prefer), None) or (found[0] if found else None)
            if pick is not None and lk == "bdm_contactid":
                cust_t = logical.get(ROLE_TABLE["customer"])
                # incident.customerid is one polymorphic column shared with the account lookup; no separate contact lookup
                if any(x["attr"] == pick["attr"] and x["referenced"] == cust_t for x in m.get("lookups", [])):
                    pick = None
            if pick is None:
                if table != "bdm_equipments" or lk == "bdm_customerid":
                    cols[LOOKUP_NAV[lk]] = None
                    cols[f"_{lk}_value"] = None
                    cols[lk] = None
                continue
            if pick["attr"] != lk or pick["nav"] != LOOKUP_NAV[lk]:
                cols[LOOKUP_NAV[lk]] = pick["nav"]
                cols[f"_{lk}_value"] = f"_{pick['attr']}_value"
                cols[lk] = pick["attr"]
        if cols:
            out["columns"][table] = cols

    cust = meta.get(logical.get("bdm_customers") or "")
    if cust:
        prim = out["columns"].get("bdm_customers", {}).get("bdm_primaryphone", "bdm_primaryphone")
        extra = sorted(a for a in cust["attrs"] if _PHONE.match(a) and a != prim)
        if extra:
            out["phoneColumns"] = extra

    ct = meta.get(logical.get("bdm_contacts") or "")
    if ct:
        prim = out["columns"].get("bdm_contacts", {}).get("bdm_primaryphone", "bdm_primaryphone")
        extra = sorted(a for a in ct["attrs"] if _PHONE.match(a) and a != prim)
        if extra:
            out["contactPhoneColumns"] = extra

    tk = meta.get(logical.get("bdm_servicetickets") or "")
    if tk:
        t_cols = out["columns"].get("bdm_servicetickets", {})
        desc = t_cols.get("bdm_description", "bdm_description")
        a = tk["attrs"].get(desc) if desc else None
        if a and a.get("max") and a["max"] < 100000:
            out["ticketDescriptionMax"] = a["max"]
            out["ticketNoteBind"] = f"objectid_{logical['bdm_servicetickets']}"
        pr = t_cols.get("bdm_priority", "bdm_priority")
        opts = tk.get("options", {}).get(pr) if pr else None
        if opts:
            mapped = _map_priority(opts)
            if mapped != CANON_PRIORITY:
                out["tableOptions"] = {"bdm_servicetickets": {"priority": mapped}}
    if problems:
        raise SchemaError("cannot resolve the Dataverse mapping: " + "; ".join(problems))
    return {k: v for k, v in out.items() if v not in ({}, [], None)}


def _map_priority(opts: dict[str, int]) -> dict[str, int]:
    found = {}
    for key, words in PRIORITY_WORDS.items():
        v = next((opts[w] for w in words if w in opts), None)
        if v is not None:
            found[key] = v
    if "high" not in found or "low" not in found:
        raise SchemaError(f"priority options {sorted(opts)} do not include low and high; set tableOptions explicitly")
    found.setdefault("medium", found["low"])
    found.setdefault("urgent", found["high"])
    return found


def combine(raw: dict, discovered: dict) -> dict:
    """Explicit config wins over discovery; `roles` is consumed."""
    explicit = {k: v for k, v in raw.items() if k != "roles"}
    return _merge(discovered, explicit)


async def fetch_meta(http: httpx.AsyncClient, org: str, token: str, roles: dict) -> dict[str, dict]:
    api = org.rstrip("/") + "/api/data/v9.2/"
    hdr = {"Authorization": "Bearer " + token, "OData-Version": "4.0"}
    out: dict[str, dict] = {}
    for lg in {v for v in _real_logicals(roles).values() if v}:
        base = f"{api}EntityDefinitions(LogicalName='{lg}')"
        r = await http.get(
            base + "?$select=LogicalName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute"
            "&$expand=Attributes($select=LogicalName,AttributeType),"
            "ManyToOneRelationships($select=ReferencingAttribute,ReferencedEntity,ReferencingEntityNavigationPropertyName)",
            headers=hdr,
        )
        if r.status_code == 404:
            continue
        if r.status_code >= 300:
            raise RuntimeError(f"Dataverse metadata for '{lg}' failed ({r.status_code}): {r.text[:200]}")
        d = r.json()
        attrs = {x["LogicalName"]: {"type": x.get("AttributeType"), "max": None} for x in d["Attributes"]}
        lookups = [
            {"attr": x["ReferencingAttribute"], "referenced": x["ReferencedEntity"], "nav": x["ReferencingEntityNavigationPropertyName"]}
            for x in d["ManyToOneRelationships"]
        ]
        options: dict[str, dict[str, int]] = {}
        for name, a in attrs.items():
            if a["type"] == "Picklist" and ("priority" in name):
                o = await http.get(
                    f"{base}/Attributes(LogicalName='{name}')/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
                    "?$select=LogicalName&$expand=OptionSet($select=Options)",
                    headers=hdr,
                )
                if o.status_code < 300:
                    options[name] = {
                        ((x.get("Label") or {}).get("UserLocalizedLabel") or {}).get("Label", "").lower(): x["Value"]
                        for x in o.json()["OptionSet"]["Options"]
                    }
        out[lg] = {"entitySet": d["EntitySetName"], "primaryId": d["PrimaryIdAttribute"], "primaryName": d["PrimaryNameAttribute"],
                   "attrs": attrs, "lookups": lookups, "options": options}
    return out


async def resolve_schema(schema: Schema, http: httpx.AsyncClient, org: str, token: str) -> Schema:
    """Return the schema with live metadata merged under the explicit config (a no-op without `roles`)."""
    if not schema.roles:
        return schema
    meta = await fetch_meta(http, org, token, schema.roles)
    return Schema(combine(schema.raw, resolve(schema.roles, meta)))


def load_resolved(settings, offline: bool = False) -> Schema:
    """Synchronous variant for the CLI tools (preflight, prompt rendering)."""
    schema = Schema.load(settings.dataverse_schema, settings.dataverse_schema_file)
    if not schema.roles or offline or not settings.dataverse_url:
        return schema
    from .extractor import TokenSource

    async def go() -> Schema:
        token = await TokenSource(settings.local_dev).get(settings.dataverse_url.rstrip("/") + "/.default")
        async with httpx.AsyncClient(timeout=60) as http:
            return await resolve_schema(schema, http, settings.dataverse_url, token)

    return asyncio.run(go())
