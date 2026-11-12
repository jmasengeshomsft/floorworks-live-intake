"""Render a prompt template from the Dataverse config: `python -m app.render_prompts <template> [--brand X]`.

Placeholders ({{NAME}}) are filled from the same DATAVERSE_* settings the gateway uses, so prompts and gateway agree.
Prints the rendered prompt to stdout and fails on any unresolved placeholder.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import load_settings
from .schema import Schema




def notes(schema: Schema) -> str:
    """One line per table from the config's `describe` section (empty when the config has none)."""
    lines = []
    for table, d in schema.describe.items():
        if isinstance(d, str):
            lines.append(f"- {table}: {d}")
            continue
        cols = "; ".join(f"{k} = {v}" for k, v in d.items() if k != "table")
        lines.append(f"- {table}: {d.get('table', '')}" + (f" Columns: {cols}." if cols else ""))
    return "\n".join(lines)


def contact_step(schema: Schema, brand: str, account_where: str) -> str:
    """Extra identification text for the optional contact role (empty when the org has no contact table)."""
    if not schema.contacts:
        return ""
    t = schema.logical_name("bdm_contacts")
    idc, namec = schema.real_column("bdm_contacts", "bdm_contactid"), schema.real_column("bdm_contacts", "bdm_name")
    parent = schema.real_column("bdm_contacts", "bdm_customerid")
    phones = [p for p in [schema.real_column("bdm_contacts", "bdm_primaryphone")] + schema.contact_phone_columns if p]
    where = " OR ".join(f"{p} LIKE '%405%602%9804'" for p in phones)
    return (
        f" Callers are usually individual people, so search the {t} table first, the same way: SELECT TOP 5 {idc}, {namec}, {parent}, "
        f"{', '.join(phones)} FROM {t} WHERE {where}. If exactly one contact matches, look up its company with {parent} in the customer table, "
        f"then confirm both with one question, for example \"Am I speaking with <contact name> from <company name>?\". Stating a matched "
        f"contact's name only as that confirming question is allowed; if the caller says no, ask for their name and company as below. "
        f"If no contact matches, fall back to the account phone search above and confirm only the company."
    )


def values(schema: Schema, brand: str) -> dict[str, str]:
    cust = schema.logical_name("bdm_customers")
    idc = schema.real_column("bdm_customers", "bdm_customerid")
    namec = schema.real_column("bdm_customers", "bdm_name")
    phones = ([schema.real_column("bdm_customers", "bdm_primaryphone")] if schema.supports("bdm_customers", "bdm_primaryphone") else []) + schema.phone_columns
    phones = [p for p in phones if p]
    where = " OR ".join(f"{p} LIKE '%405%602%9804'" for p in phones)
    sources = [f"{cust} (customers)", f"{schema.logical_name('bdm_servicetickets')} (service tickets)"]
    domain = "customers and service tickets"
    if schema.enabled("bdm_equipments"):
        sources.insert(1, f"{schema.logical_name('bdm_equipments')} (equipment)")
        domain = "customers, equipment, and service tickets"
    return {
        "CONTACT_STEP": contact_step(schema, brand, where),
        "ASSISTANT_NAME": f"{brand} Assistant",
        "SCHEMA_NOTES": notes(schema),
        "CONFIG_HASH": schema.hash,
        "BRAND": brand,
        "DOMAIN": domain,
        "DATA_SOURCES": ", ".join(sources),
        "CUSTOMER_TABLE": cust,
        "CUSTOMER_ID_COLUMN": idc,
        "CUSTOMER_NAME_COLUMN": namec,
        "PHONE_COLUMNS": ", ".join(phones),
        "PHONE_WHERE_FULL": where,
        "EQUIPMENT_TABLE": schema.logical_name("bdm_equipments"),
        "TICKET_TABLE": schema.logical_name("bdm_servicetickets"),
    }


def render(text: str, vals: dict[str, str]) -> str:
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: vals.get(m.group(1), m.group(0)), text)
    left = sorted(set(re.findall(r"\{\{(\w+)\}\}", out)))
    if left:
        raise SystemExit(f"unresolved placeholders: {left}")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("--brand", default="")
    a = ap.parse_args(argv)
    s = load_settings()
    from .discover import load_resolved

    schema = load_resolved(s)
    text = Path(a.template).read_text(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(render(text, values(schema, a.brand or s.brand_name)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
