"""Environment-driven settings. Everything business-specific lives here, not in code."""
from __future__ import annotations

import json
import os
from dataclasses import field

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


def _integer(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _toggle(name: str, default: str = "off") -> bool:
    value = _env(name, default).lower()
    if value not in ("on", "off"):
        raise ValueError(f"{name} must be on or off")
    return value == "on"


@dataclass(config=ConfigDict(strict=True))
class FunctionDef:
    name: str
    webhook_url: str
    timeout_ms: int = 4000
    is_async: bool = False
    description: str = ""


@dataclass(config=ConfigDict(strict=True))
class Settings:
    public_base_url: str = ""
    foundry_project_endpoint: str = ""
    voice_agent: str = ""
    extractor_agent: str = ""
    acs_connection_string: str = ""
    acs_endpoint: str = ""
    local_dev: bool = False
    credential_mode: str = "auto"  # auto | default | managed_identity | azure_cli
    managed_identity_client_id: str = ""
    profile: str = "voice"  # voice | api

    # infra endpoint protection (empty = not enforced)
    gateway_secret: str = ""  # HMAC key for per-call callback / media URL tokens
    eventgrid_secret: str = ""  # shared secret expected on /api/incoming (?s= or aeg-sas-key header)
    acs_jwt_mode: str = "off"  # off | warn | enforce: validate the ACS-signed JWT on callbacks and /ws
    acs_resource_id: str = ""  # expected JWT audience (ACS immutable resource id)
    line_numbers: list[str] = field(default_factory=list)  # accepted called numbers; empty = any

    # auth: comma separated API keys; empty = auth disabled (dev only)
    api_keys: list[str] = field(default_factory=list)

    # extraction
    extraction_schema: dict = field(default_factory=dict)
    enrichment: bool = False
    enrichment_interval_s: int = 60
    dataverse_url: str = ""
    dataverse_schema: str = ""
    dataverse_schema_file: str = ""
    brand_name: str = "Intake"  # prefix of generated ticket titles
    # transfer / after hours
    transfer_teams_ids: list[str] = field(default_factory=list)
    after_hours: str = "off"  # on | off | auto
    service_days: str = "Mon-Fri"
    service_hours: str = "07:00-18:00"
    service_tz: str = "America/Chicago"
    after_hours_note: str = ""

    # voice session
    voice_session_tuning: bool = False
    ringback: bool = True
    greeting_context: str = ""

    # live notes after transfer (pluggable STT)
    live_notes: bool = True
    speech_key: str = ""
    speech_region: str = "eastus"
    speech_language: str = "en-US"
    speech_auth: str = "key"  # key | aad (managed identity / Entra, needs SPEECH_RESOURCE_ID)
    speech_resource_id: str = ""

    # guardrails (0 disables a limit)
    max_concurrent_calls: int = 50
    max_call_duration_s: int = 3600
    idle_timeout_s: int = 120

    # webhooks and functions
    webhook_urls: list[str] = field(default_factory=list)
    webhook_secret: str = ""
    cors_origins: list[str] = field(default_factory=list)
    dev_simulator: bool = False
    functions: list[FunctionDef] = field(default_factory=list)

    @property
    def ws_base(self) -> str:
        return self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")


def _load_json(value: str, file_var: str) -> object:
    path = _env(file_var)
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(value) if value else None


def load_settings() -> Settings:
    schema = _load_json(_env("EXTRACTION_SCHEMA"), "EXTRACTION_SCHEMA_FILE")
    schema = {} if schema is None else schema
    if not isinstance(schema, dict):
        raise ValueError("EXTRACTION_SCHEMA must be a JSON object")
    funcs_raw = _load_json(_env("FUNCTIONS"), "FUNCTIONS_FILE")
    funcs_raw = [] if funcs_raw is None else funcs_raw
    if not isinstance(funcs_raw, list) or not all(isinstance(f, dict) for f in funcs_raw):
        raise ValueError("FUNCTIONS must be a JSON array of objects")
    for f in funcs_raw:
        if set(f) - {"name", "webhookUrl", "timeoutMs", "async", "description"}:
            raise ValueError("FUNCTIONS contains unknown properties")
        if (not isinstance(f.get("name"), str) or not isinstance(f.get("webhookUrl"), str)
                or type(f.get("timeoutMs", 4000)) is not int or type(f.get("async", False)) is not bool
                or not isinstance(f.get("description", ""), str)):
            raise ValueError("FUNCTIONS requires string name/webhookUrl/description, integer timeoutMs and boolean async")
    funcs = [
        FunctionDef(
            name=f["name"],
            webhook_url=f["webhookUrl"],
            timeout_ms=f.get("timeoutMs", 4000),
            is_async=f.get("async", False),
            description=f.get("description", ""),
        )
        for f in funcs_raw
    ]
    return Settings(
        public_base_url=_env("PUBLIC_BASE_URL").rstrip("/"),
        foundry_project_endpoint=_env("FOUNDRY_PROJECT_ENDPOINT").rstrip("/"),
        voice_agent=_env("VOICE_AGENT_NAME"),
        extractor_agent=_env("EXTRACTOR_AGENT_NAME"),
        acs_connection_string=_env("ACS_CONNECTION_STRING"),
        acs_endpoint=_env("ACS_ENDPOINT").rstrip("/"),
        local_dev=_local_dev(),
        credential_mode=_env("AZURE_CREDENTIAL_MODE", "auto").lower(),
        managed_identity_client_id=_env("AZURE_CLIENT_ID"),
        profile=_env("GATEWAY_PROFILE", "voice").lower(),
        gateway_secret=_env("GATEWAY_SECRET"),
        eventgrid_secret=_env("EVENTGRID_SECRET"),
        acs_jwt_mode=(_env("ACS_JWT_MODE") or ("off" if _env("LOCAL_DEV") == "1" else "enforce")).lower(),
        acs_resource_id=_env("ACS_RESOURCE_ID"),
        line_numbers=_list("LINE_NUMBERS"),
        api_keys=_list("API_KEYS"),
        extraction_schema=schema,
        enrichment=_toggle("ENRICHMENT"),
        enrichment_interval_s=_integer("ENRICHMENT_INTERVAL_S", 60),
        dataverse_url=_env("DATAVERSE_URL"),
        dataverse_schema=_env("DATAVERSE_SCHEMA"),
        dataverse_schema_file=_env("DATAVERSE_SCHEMA_FILE"),
        brand_name=_env("BRAND_NAME", "Intake"),
        transfer_teams_ids=_list("TRANSFER_TEAMS_OBJECT_IDS"),
        after_hours=_env("AFTER_HOURS", "off").lower(),
        service_days=_env("SERVICE_DAYS", "Mon-Fri"),
        service_hours=_env("SERVICE_HOURS", "07:00-18:00"),
        service_tz=_env("SERVICE_TZ", "America/Chicago"),
        after_hours_note=_env("AFTER_HOURS_NOTE"),
        voice_session_tuning=_toggle("VOICE_SESSION_TUNING"),
        ringback=_toggle("RINGBACK", "on"),
        live_notes=_toggle("LIVE_NOTES", "on"),
        speech_key=_env("SPEECH_KEY"),
        speech_region=_env("SPEECH_REGION", "eastus"),
        speech_language=_env("SPEECH_LANGUAGE", "en-US"),
        speech_auth=_env("SPEECH_AUTH").lower() or ("aad" if not _env("SPEECH_KEY") and _env("SPEECH_RESOURCE_ID") else "key"),
        speech_resource_id=_env("SPEECH_RESOURCE_ID"),
        max_concurrent_calls=_integer("MAX_CONCURRENT_CALLS", 50),
        max_call_duration_s=_integer("MAX_CALL_DURATION_SECONDS", 3600),
        idle_timeout_s=_integer("IDLE_TIMEOUT_SECONDS", 120),
        greeting_context=_env("GREETING_CONTEXT"),
        webhook_urls=_list("WEBHOOK_URLS"),
        webhook_secret=_env("WEBHOOK_SECRET"),
        cors_origins=_list("CORS_ORIGINS"),
        dev_simulator=_toggle("DEV_SIMULATOR"),
        functions=funcs,
    )


def _local_dev() -> bool:
    value = _env("LOCAL_DEV", "0")
    if value not in ("0", "1"):
        raise ValueError("LOCAL_DEV must be 0 or 1")
    return value == "1"
