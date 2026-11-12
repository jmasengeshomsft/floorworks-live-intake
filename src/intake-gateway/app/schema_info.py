"""Print the resolved Dataverse config as JSON: `python -m app.schema_info`.

The seeder, provisioner and deploy scripts call this so they use the same config file as the gateway.
"""
from __future__ import annotations

import json
import sys

from .config import load_settings
from .schema import Schema


def info(schema: Schema) -> dict:
    return {
        "profile": schema.profile,
        "standardTables": schema.logical_name("bdm_customers") == "account",
        "tables": {t: schema.logical_name(t) for t in sorted(schema.logical)},
        "equipmentEnabled": schema.enabled("bdm_equipments"),
        "contactsEnabled": schema.contacts,
    }


def main() -> int:
    s = load_settings()
    print(json.dumps(info(Schema.load(s.dataverse_schema, s.dataverse_schema_file))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
