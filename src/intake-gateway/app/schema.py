"""Dataverse schema mapping.

The gateway code uses the FloorWorks `bdm_*` names as canonical keys. A `Schema` translates them at the
Dataverse boundary only (request path/body out, response rows in), so a deployment that stores customers and
tickets in other tables (for example the standard `account` and `incident` tables) is configuration, not code.

The mapping is one explicit JSON document, normally `dataverse/config.json` (edit its `roles` to change tables). Nothing is built in: omitted keys mean "canonical bdm_* names, unchanged".
Deployment reads the file and passes it in DATAVERSE_SCHEMA (or point DATAVERSE_SCHEMA_FILE at it for local runs).

    {
      "profile":  "acme",                      (label only)
      "roles":    {"customer": "account", "ticket": "incident", "contact": "contact"},   (discover the rest from the org, see discover.py; contact is optional)
      "describe": {"account": "Customers", "account.telephone1": "Primary phone"},   (for agent prompts)
      "names":    {"bdm_servicerequestdrafts": "acme_drafts", "bdm_callid": "acme_callref"},
      "columns":  {"bdm_customers": {"bdm_primaryphone": "mobilephone", "bdm_email": null}},
      "disabled": ["bdm_equipments"],
      "options":  {"status": {"draft": 1, "submitted": 2}},
      "tableOptions": {"bdm_servicetickets": {"priority": {"low": 3, "medium": 2, "high": 1, "urgent": 1}}},
      "logicalNames": {"bdm_customers": "account"},
      "phoneColumns": ["telephone2", "telephone3"],
      "contactPhoneColumns": ["telephone1", "telephone2"],
      "ticketDescriptionMax": 2000,
      "ticketNoteBind": "objectid_incident"
    }
`logicalNames` are the singular table names used by queries, preflight and the rendered agent prompts;
`phoneColumns` are extra customer phone columns searched besides bdm_primaryphone (`contactPhoneColumns` likewise
for the optional contact table); text longer than
`ticketDescriptionMax` (0 = no limit) is truncated and the full text is attached as a Note bound through
`ticketNoteBind`.

`names` maps a canonical table, column or lookup-navigation name to the real one for every table. `columns` is
scoped to one canonical table and wins over `names`; a null value means the column does not exist there (it is
dropped from selects and request bodies). Lookup value columns (`_bdm_customerid_value`) follow a renamed lookup
unless set explicitly. `tableOptions` maps option-set values per table; several canonical values may share one real
value (the first one wins when reading back).
"""

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

OPT_BASE = 105730000
CANON_STATUS = {"draft": OPT_BASE, "submitted": OPT_BASE + 1}
CANON_PRIORITY = {"low": OPT_BASE, "medium": OPT_BASE + 1, "high": OPT_BASE + 2, "urgent": OPT_BASE + 3}
OPTION_COLUMNS = {"status": "bdm_status", "priority": "bdm_priority"}
CANON_OPTIONS = {"status": CANON_STATUS, "priority": CANON_PRIORITY}

LOOKUPS = ("bdm_customerid", "bdm_equipmentid", "bdm_ticketid", "bdm_contactid")
TABLES = {"bdm_servicerequestdrafts", "bdm_servicetickets", "bdm_customers", "bdm_equipments", "bdm_contacts"}
COLUMNS = {
    "bdm_servicerequestdraftid", "bdm_serviceticketid", "bdm_name", "bdm_callid", "bdm_callerphone", "bdm_customername",
    "bdm_equipmenttext", "bdm_complaint", "bdm_priority", "bdm_summary", "bdm_transcript", "bdm_coaching", "bdm_status",
    "bdm_submittedby", "bdm_submittedon", "bdm_contactphone", "bdm_assetnumber", "bdm_location", "bdm_preferredtime",
    "bdm_editedfields", "bdm_enrichmentjson", "bdm_description", "bdm_openedon", "bdm_accountnumber", "bdm_firstname",
    "bdm_lastname", "bdm_primaryphone", "bdm_email", "bdm_street", "bdm_city", "bdm_state", "bdm_zip", "bdm_manufacturer",
    "bdm_plant", "bdm_customerid", "bdm_equipmentid", "bdm_ticketid", "bdm_contactid", "bdm_contactname",
}
LOOKUP_NAMES = {"bdm_CustomerId", "bdm_EquipmentId", "bdm_TicketId", "bdm_ContactId"}
LOOKUP_VALUES = {f"_{lk}_value" for lk in LOOKUPS}
KNOWN_NAMES = TABLES | COLUMNS | LOOKUP_NAMES | LOOKUP_VALUES
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUOTED = re.compile(r"('(?:[^']|'')*')")
_SELECT = re.compile(r"(\$select=)([^&]*)")

class SchemaError(ValueError):
    pass


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _logical(label: str, v: Any, nullable: bool = False) -> None:
    if v is None and nullable:
        return
    if not isinstance(v, str) or not _TOKEN.fullmatch(v):
        raise SchemaError(f"{label} must be a Dataverse logical name{' or null' if nullable else ''}, got {v!r}")


def _options(label: str, canon: dict, given: Any, distinct: bool) -> dict:
    out = dict(canon)
    if not isinstance(given or {}, dict):
        raise SchemaError(f"{label} must be an object")
    for k, v in (given or {}).items():
        if k not in canon or not isinstance(v, int) or isinstance(v, bool):
            raise SchemaError(f"{label}.{k} is not a valid key/integer")
        out[k] = v
    if distinct and len(set(out.values())) != len(out):
        raise SchemaError(f"{label} values must be distinct")
    return out


class Schema:
    def __init__(self, overrides: dict | None = None):
        o = overrides or {}
        unknown = set(o) - {"profile", "describe", "names", "columns", "disabled", "options", "tableOptions", "logicalNames",
                            "phoneColumns", "contactPhoneColumns", "ticketDescriptionMax", "ticketNoteBind", "roles"}
        if unknown:
            raise SchemaError(f"unknown top-level keys: {sorted(unknown)}")
        self.profile = str(o.get("profile") or "custom")
        self.describe = o.get("describe") or {}
        if not isinstance(self.describe, dict) or not all(isinstance(v, (str, dict)) for v in self.describe.values()):
            raise SchemaError("describe must be an object of table -> text or column -> text")
        self.raw = o
        self.roles = o.get("roles") or {}
        if not isinstance(self.roles, dict) or not all(v is None or isinstance(v, str) for v in self.roles.values()):
            raise SchemaError("roles must be an object of role -> logical table name")
        bad_roles = set(self.roles) - {"customer", "ticket", "equipment", "draft", "contact"}
        if bad_roles:
            raise SchemaError(f"roles has unknown keys: {sorted(bad_roles)}")
        cfg = {k: v for k, v in o.items() if k not in ("profile", "describe", "roles")}
        self.hash = hashlib.sha256(json.dumps(o, sort_keys=True).encode("utf-8")).hexdigest()[:12]

        names = cfg.get("names") or {}
        for k, v in names.items():
            if k not in KNOWN_NAMES:
                raise SchemaError(f"unknown canonical name in names: {k}")
            _logical(f"names.{k}", v)
        columns = cfg.get("columns") or {}
        for t, cm in columns.items():
            if t not in TABLES or not isinstance(cm, dict):
                raise SchemaError(f"columns.{t}: unknown canonical table")
            for k, v in cm.items():
                if k not in KNOWN_NAMES - TABLES:
                    raise SchemaError(f"columns.{t}: unknown canonical column {k}")
                _logical(f"columns.{t}.{k}", v, nullable=True)
        self.disabled = set(cfg.get("disabled") or []) | ({"bdm_equipments"} if "equipment" in self.roles and not self.roles["equipment"] else set())
        if not self.disabled <= TABLES:
            raise SchemaError(f"disabled has unknown tables: {sorted(self.disabled - TABLES)}")
        role_table = {"customer": "bdm_customers", "ticket": "bdm_servicetickets", "equipment": "bdm_equipments",
                      "draft": "bdm_servicerequestdrafts", "contact": "bdm_contacts"}
        self.logical = {role_table[r]: v for r, v in self.roles.items() if v}
        self.logical.update(cfg.get("logicalNames") or {})
        for t, v in self.logical.items():
            if t not in TABLES:
                raise SchemaError(f"logicalNames.{t}: unknown canonical table")
            _logical(f"logicalNames.{t}", v)
        self.phone_columns = list(cfg.get("phoneColumns") or [])
        self.contact_phone_columns = list(cfg.get("contactPhoneColumns") or [])
        for v in self.phone_columns + self.contact_phone_columns:
            _logical("phoneColumns[]", v)
        self.contacts = bool(self.logical.get("bdm_contacts")) and "bdm_contacts" not in self.disabled
        self.description_max = cfg.get("ticketDescriptionMax") or 0
        if not isinstance(self.description_max, int) or isinstance(self.description_max, bool) or self.description_max < 0:
            raise SchemaError("ticketDescriptionMax must be a non-negative integer")
        self.note_bind = cfg.get("ticketNoteBind") or None
        _logical("ticketNoteBind", self.note_bind, nullable=True)

        self.tables = {t: names[t] for t in TABLES if t in names and names[t] != t}
        self.fwd: dict[str, dict[str, str | None]] = {}
        self.rev: dict[str, dict[str, str]] = {}
        for t in TABLES:
            m: dict[str, str | None] = {k: v for k, v in names.items() if k not in TABLES}
            m.update(columns.get(t) or {})
            for lk in LOOKUPS:
                lv = f"_{lk}_value"
                if lk in m and lv not in m:
                    m[lv] = None if m[lk] is None else f"_{m[lk]}_value"
            m = {k: v for k, v in m.items() if k != v}
            real = [v for v in m.values() if v is not None]
            if len(set(real)) != len(real):
                raise SchemaError(f"two canonical names map to the same Dataverse name on {t}")
            self.fwd[t] = m
            self.rev[t] = {v: k for k, v in m.items() if v is not None}

        opts = cfg.get("options") or {}
        for k in opts:
            if k not in OPTION_COLUMNS:
                raise SchemaError(f"options.{k} is not status or priority")
        self.status = _options("options.status", CANON_STATUS, opts.get("status"), True)
        self.priority = _options("options.priority", CANON_PRIORITY, opts.get("priority"), True)
        base = {"status": self.status, "priority": self.priority}
        topts = cfg.get("tableOptions") or {}
        bad_t = set(topts) - TABLES
        if bad_t:
            raise SchemaError(f"tableOptions has unknown tables: {sorted(bad_t)}")
        self._out: dict[str, dict[str, dict[int, int]]] = {}
        self._in: dict[str, dict[str, dict[int, int]]] = {}
        for t in TABLES:
            given = topts.get(t) or {}
            if not isinstance(given, dict):
                raise SchemaError(f"tableOptions.{t} must be an object")
            self._out[t], self._in[t] = {}, {}
            for key, col in OPTION_COLUMNS.items():
                m = _options(f"tableOptions.{t}.{key}", CANON_OPTIONS[key], given.get(key) or base[key], False)
                inv: dict[int, int] = {}
                for k in CANON_OPTIONS[key]:
                    inv.setdefault(m[k], CANON_OPTIONS[key][k])
                self._out[t][col] = {CANON_OPTIONS[key][k]: v for k, v in m.items()}
                self._in[t][col] = inv
        self.identity = (
            not self.tables and not any(self.fwd.values()) and not self.disabled
            and self.status == CANON_STATUS and self.priority == CANON_PRIORITY and not topts
        )

    def logical_name(self, table: str) -> str:
        """Singular Dataverse table name (what MCP/SQL queries and metadata use), as opposed to the entity-set path."""
        return self.logical.get(table, table.rstrip("s"))

    def real_table(self, table: str) -> str:
        return self.tables.get(table, table)

    def real_column(self, table: str, column: str) -> str | None:
        return self.fwd.get(table, {}).get(column, column)

    @classmethod
    def load(cls, raw: str = "", path: str = "") -> "Schema":
        """Inline JSON (or `b64:<base64 JSON>`, which survives shell quoting) wins over a file."""
        try:
            data: Any = None
            if raw.strip():
                raw = raw.strip()
                if raw.startswith("b64:"):
                    raw = base64.b64decode(raw[4:]).decode("utf-8")
                data = json.loads(raw)
            elif path.strip():
                data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SchemaError(f"cannot load Dataverse schema: {e}") from e
        if data is not None and not isinstance(data, dict):
            raise SchemaError("Dataverse schema must be a JSON object")
        return cls(data)

    @staticmethod
    def table_of(path: str) -> str:
        m = _TOKEN.match(path)
        return m.group(0) if m else ""

    def enabled(self, table: str) -> bool:
        return table not in self.disabled

    def supports(self, table: str, column: str) -> bool:
        return self.enabled(table) and self.fwd.get(table, {}).get(column, column) is not None

    def path(self, p: str) -> str:
        """Translate names in a request path/query, leaving quoted literals untouched."""
        if self.identity:
            return p
        t = self.table_of(p)
        m = self.fwd.get(t, {})

        def keep(mm: re.Match) -> str:
            cols = [c for c in mm.group(2).split(",") if m.get(c, c) is not None]
            return mm.group(1) + ",".join(cols)

        def swap(mm: re.Match) -> str:
            tok = mm.group(0)
            if tok in self.tables:
                return self.tables[tok]
            return m.get(tok) or tok

        p = _SELECT.sub(keep, p)
        return "".join(s if s.startswith("'") else _TOKEN.sub(swap, s) for s in _QUOTED.split(p))

    def body(self, path: str, d: dict | None) -> dict | None:
        if d is None or self.identity:
            return d
        t = self.table_of(path)
        m = self.fwd.get(t, {})
        out = {}
        for k, v in d.items():
            base, sep, rest = k.partition("@")
            real = m.get(base, base)
            if real is None:
                continue
            if isinstance(v, int) and not isinstance(v, bool) and base in self._out.get(t, {}):
                v = self._out[t][base].get(v, v)
            elif isinstance(v, str) and v.startswith("/"):
                v = _TOKEN.sub(lambda mm: self.tables.get(mm.group(0), mm.group(0)), v)
            out[real + sep + rest] = v
        return out

    def row(self, path: str, d: Any) -> Any:
        if self.identity:
            return d
        return self._row(self.table_of(path), d)

    def _row(self, t: str, d: Any) -> Any:
        if isinstance(d, list):
            return [self._row(t, x) for x in d]
        if not isinstance(d, dict):
            return d
        rev = self.rev.get(t, {})
        out = {}
        for k, v in d.items():
            base, sep, rest = k.partition("@")
            ck = rev.get(base, base)
            if isinstance(v, int) and not isinstance(v, bool) and ck in self._in.get(t, {}):
                v = self._in[t][ck].get(v, v)
            out[ck + sep + rest] = self._row(t, v) if isinstance(v, (dict, list)) else v
        return out
