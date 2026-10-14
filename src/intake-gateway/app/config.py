"""Environment-driven settings. Everything business-specific lives here, not in code."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


@dataclass
class FunctionDef:
    name: str
    webhook_url: str
    timeout_ms: int = 4000
    is_async: bool = False
    description: str = ""


@dataclass
class Settings:
    public_base_url: str = ""
    foundry_project_endpoint: str = ""
    voice_agent: str = ""
    extractor_agent: str = ""
    acs_connection_string: str = ""
    local_dev: bool = False

    # auth: comma separated API keys; empty = auth disabled (dev only)
    api_keys: list[str] = field(default_factory=list)

    # extraction
    extraction_schema: dict = field(default_factory=dict)
    enrichment: bool = False
    enrichment_interval_s: int = 60

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
    schema = _load_json(_env("EXTRACTION_SCHEMA"), "EXTRACTION_SCHEMA_FILE") or {}
    funcs_raw = _load_json(_env("FUNCTIONS"), "FUNCTIONS_FILE") or []
    funcs = [
        FunctionDef(
            name=f["name"],
            webhook_url=f["webhookUrl"],
            timeout_ms=int(f.get("timeoutMs", 4000)),
            is_async=bool(f.get("async", False)),
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
        local_dev=_env("LOCAL_DEV") == "1",
        api_keys=_list("API_KEYS"),
        extraction_schema=schema,
        enrichment=_env("ENRICHMENT", "off").lower() == "on",
        enrichment_interval_s=int(_env("ENRICHMENT_INTERVAL_S", "60")),
        transfer_teams_ids=_list("TRANSFER_TEAMS_OBJECT_IDS"),
        after_hours=_env("AFTER_HOURS", "off").lower(),
        service_days=_env("SERVICE_DAYS", "Mon-Fri"),
        service_hours=_env("SERVICE_HOURS", "07:00-18:00"),
        service_tz=_env("SERVICE_TZ", "America/Chicago"),
        after_hours_note=_env("AFTER_HOURS_NOTE"),
        voice_session_tuning=_env("VOICE_SESSION_TUNING") == "on",
        ringback=_env("RINGBACK", "on").lower() != "off",
        live_notes=_env("LIVE_NOTES", "on").lower() != "off",
        speech_key=_env("SPEECH_KEY"),
        speech_region=_env("SPEECH_REGION", "eastus"),
        speech_language=_env("SPEECH_LANGUAGE", "en-US"),
        speech_auth=_env("SPEECH_AUTH") .lower() or ("aad" if not _env("SPEECH_KEY") and _env("SPEECH_RESOURCE_ID") else "key"),
        speech_resource_id=_env("SPEECH_RESOURCE_ID"),
        max_concurrent_calls=int(_env("MAX_CONCURRENT_CALLS", "50")),
        max_call_duration_s=int(_env("MAX_CALL_DURATION_SECONDS", "3600")),
        idle_timeout_s=int(_env("IDLE_TIMEOUT_SECONDS", "120")),
        greeting_context=_env("GREETING_CONTEXT"),
        webhook_urls=_list("WEBHOOK_URLS"),
        webhook_secret=_env("WEBHOOK_SECRET"),
        cors_origins=_list("CORS_ORIGINS"),
        dev_simulator=_env("DEV_SIMULATOR", "off").lower() == "on",
        functions=funcs,
    )
