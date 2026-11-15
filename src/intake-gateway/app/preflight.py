"""Deployment preflight: `python -m app.preflight [--offline]`.

Validates the gateway settings, then checks the configured Dataverse mapping against the live org: every mapped
table exists with the expected entity-set name and every mapped column or lookup exists. Exit code 1 on any failure.
"""
from __future__ import annotations

import sys

import httpx

from .config import load_settings
from .drafts import DRAFT_INTEGRITY_COLUMNS
from .schema import Schema
from .validate import validate

DRAFT_COLUMNS = (
    "bdm_servicerequestdraftid bdm_name bdm_callid bdm_callerphone bdm_customername bdm_equipmenttext bdm_complaint "
    "bdm_priority bdm_summary bdm_transcript bdm_coaching bdm_status bdm_submittedby bdm_submittedon bdm_contactphone "
    "bdm_assetnumber bdm_location bdm_preferredtime bdm_editedfields bdm_transcriptversion"
).split()
NEEDED = {
    "bdm_calljournals": ("bdm_calljournalid bdm_name bdm_callid bdm_checkpointjson bdm_version bdm_complete".split(), []),
    "bdm_servicerequestdrafts": (DRAFT_COLUMNS, ["bdm_customerid", "bdm_equipmentid", "bdm_ticketid", "bdm_contactid"]),
    "bdm_customers": ("bdm_customerid bdm_name bdm_accountnumber bdm_primaryphone bdm_email bdm_street bdm_city bdm_state bdm_zip".split(), []),
    "bdm_servicetickets": (
        "bdm_serviceticketid bdm_name bdm_description bdm_priority bdm_status bdm_openedon".split(),
        ["bdm_customerid", "bdm_equipmentid", "bdm_contactid"],
    ),
    "bdm_contacts": ("bdm_contactid bdm_name bdm_primaryphone bdm_email".split(), ["bdm_customerid"]),
    "bdm_equipments": ("bdm_equipmentid bdm_name bdm_assetnumber bdm_manufacturer bdm_plant".split(), ["bdm_customerid"]),
}


def _lookup_attr(real_value_col: str) -> str:
    return real_value_col[1:-6] if real_value_col.startswith("_") and real_value_col.endswith("_value") else real_value_col


def expected_attributes(schema: Schema, table: str) -> list[str]:
    cols, lookups = NEEDED[table]
    out = [schema.real_column(table, c) for c in cols]
    for lk in lookups:
        if table == "bdm_equipments" and not schema.enabled("bdm_equipments"):
            continue
        if lk == "bdm_contactid" and not schema.contacts:
            continue
        real = schema.real_column(table, f"_{lk}_value")
        if real:
            out.append(_lookup_attr(real))
    if table == "bdm_customers":
        out += schema.phone_columns
    if table == "bdm_contacts":
        out += schema.contact_phone_columns
    return sorted({c for c in out if c})


def check_org(settings, schema: Schema, token: str, client: httpx.Client | None = None) -> list[tuple[bool, str]]:
    if client is None:
        with httpx.Client(timeout=60) as owned:
            return check_org(settings, schema, token, owned)
    api = settings.dataverse_url.rstrip("/") + "/api/data/v9.2/"
    http = client
    hdr = {"Authorization": f"Bearer {token}", "OData-Version": "4.0"}
    results: list[tuple[bool, str]] = []

    r = http.get(api + "WhoAmI", headers=hdr)
    if r.status_code >= 300:
        return [(False, f"Dataverse WhoAmI failed ({r.status_code}): the identity has no application user or role in this org")]
    results.append((True, "Dataverse reachable and the identity is an application user"))

    for table in NEEDED:
        if table in ("bdm_servicerequestdrafts", "bdm_calljournals") and not schema.enabled(table):
            results.append((False, f"{table}: integrity storage cannot be disabled"))
            continue
        if table == "bdm_contacts" and not schema.contacts:
            results.append((True, "bdm_contacts: no contact role configured (skipped)"))
            continue
        if not schema.enabled(table):
            results.append((True, f"{table}: disabled by config (skipped)"))
            continue
        logical, entity_set = schema.logical_name(table), schema.real_table(table)
        r = http.get(f"{api}EntityDefinitions(LogicalName='{logical}')?$select=LogicalName,EntitySetName,IsOptimisticConcurrencyEnabled", headers=hdr)
        if r.status_code >= 300:
            results.append((False, f"{table}: table '{logical}' not found (HTTP {r.status_code}); run provision-dataverse.ps1 or fix logicalNames"))
            continue
        actual_set = r.json().get("EntitySetName")
        if actual_set != entity_set:
            results.append((False, f"{table}: entity set is '{actual_set}', config expects '{entity_set}' (names)"))
            continue
        if table in ("bdm_servicerequestdrafts", "bdm_calljournals"):
            if r.json().get("IsOptimisticConcurrencyEnabled") is not True:
                results.append((False, f"{table}: optimistic concurrency is required"))
            if table == "bdm_servicerequestdrafts":
                keys = http.get(f"{api}EntityDefinitions(LogicalName='{logical}')/Keys?$select=KeyAttributes,EntityKeyIndexStatus", headers=hdr)
                keys.raise_for_status()
                col = schema.real_column(table, "bdm_callid")
                if not any(k.get("KeyAttributes") == [col] and k.get("EntityKeyIndexStatus") in ("Active", 2)
                           for k in keys.json().get("value", [])):
                    results.append((False, "draft call-ID uniqueness key is not Active; rerun provisioning"))
            required = DRAFT_INTEGRITY_COLUMNS if table == "bdm_servicerequestdrafts" else tuple(NEEDED[table][0])
            if any(not schema.supports(table, c) for c in required):
                results.append((False, f"{table}: required integrity columns cannot be mapped to null"))
        a = http.get(f"{api}EntityDefinitions(LogicalName='{logical}')/Attributes?$select=LogicalName", headers=hdr)
        if a.status_code >= 300:
            results.append((False, f"{table}: cannot read column metadata (HTTP {a.status_code})"))
            continue
        have = {x["LogicalName"] for x in a.json().get("value", [])}
        wanted = expected_attributes(schema, table) + (["bdm_enrichmentjson"] if settings.enrichment and table == "bdm_servicerequestdrafts" else [])
        missing = [c for c in wanted if c not in have]
        if missing:
            results.append((False, f"{table} ({logical}): missing columns {missing}; add them or map them under 'columns' (null = not used)"))
        else:
            results.append((True, f"{table} -> {logical}/{entity_set}: all mapped columns exist"))
    return results


def main(argv: list[str]) -> int:
    s = load_settings()
    errors, warnings = validate(s)
    lines: list[tuple[bool, str]] = [(False, f"config: {e}") for e in errors] + [(True, f"config warning: {w}") for w in warnings]
    schema = None
    try:
        from .discover import load_resolved

        schema = load_resolved(s, offline="--offline" in argv)
        lines.append((True, f"Dataverse config '{schema.profile}' parsed" + (", mapping derived from the org" if schema.roles and "--offline" not in argv else "")))
    except Exception as e:  # noqa: BLE001
        lines.append((False, f"Dataverse schema: {e}"))
    if "--offline" not in argv and schema is not None and s.dataverse_url:
        from .extractor import TokenSource
        import asyncio

        async def acquire_token() -> str:
            async with TokenSource(s) as tokens:
                return await tokens.get(s.dataverse_url.rstrip("/") + "/.default")

        token = asyncio.run(acquire_token())
        lines += check_org(s, schema, token)
    for ok, msg in lines:
        print(("[ OK ] " if ok else "[FAIL] ") + msg)
    failed = sum(1 for ok, _ in lines if not ok)
    print(f"\npreflight: {'FAILED' if failed else 'passed'} ({failed} failure(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
