"""Startup configuration validation: fatal for what nothing can work without, warnings for the rest."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings

log = logging.getLogger("gateway.config")


def validate(s: Settings) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors mean the gateway cannot do its core job."""
    errors: list[str] = []
    warnings: list[str] = []
    if s.profile not in ("voice", "api"):
        errors.append("GATEWAY_PROFILE must be voice or api.")
    if s.profile == "voice":
        required = warnings if s.local_dev else errors
        for value, name in ((s.public_base_url, "PUBLIC_BASE_URL"), (s.foundry_project_endpoint, "FOUNDRY_PROJECT_ENDPOINT"),
                            (s.voice_agent, "VOICE_AGENT_NAME")):
            if not value:
                required.append(f"{name} is required for voice calls.")
    for name, value in (("PUBLIC_BASE_URL", s.public_base_url), ("FOUNDRY_PROJECT_ENDPOINT", s.foundry_project_endpoint),
                        ("DATAVERSE_URL", s.dataverse_url), ("ACS_ENDPOINT", s.acs_endpoint)):
        if value:
            try:
                parsed = urlparse(value)
                parsed.port
            except ValueError:
                errors.append(f"{name} has an invalid host or port.")
                continue
            if (not parsed.hostname or parsed.scheme not in (("http", "https") if s.local_dev else ("https",))
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                errors.append(f"{name} must be an absolute {'HTTP(S)' if s.local_dev else 'HTTPS'} URL without credentials, query or fragment.")
    for name, value in (("MAX_CONCURRENT_CALLS", s.max_concurrent_calls), ("MAX_CALL_DURATION_SECONDS", s.max_call_duration_s),
                        ("IDLE_TIMEOUT_SECONDS", s.idle_timeout_s), ("ENRICHMENT_INTERVAL_S", s.enrichment_interval_s)):
        if type(value) is not int or value < 0:
            errors.append(f"{name} must be a nonnegative integer (0 disables the limit).")
    if not isinstance(s.extraction_schema, dict):
        errors.append("EXTRACTION_SCHEMA must be an object.")
    else:
        from .extraction_models import check_schema
        from jsonschema.exceptions import SchemaError

        try:
            check_schema(s.extraction_schema)
        except (SchemaError, ValueError):
            errors.append("EXTRACTION_SCHEMA must be a valid JSON Schema with only local references.")
    if s.credential_mode not in ("auto", "default", "managed_identity", "azure_cli"):
        errors.append("AZURE_CREDENTIAL_MODE must be auto, default, managed_identity or azure_cli.")
    if s.credential_mode == "azure_cli" and not s.local_dev:
        errors.append("AZURE_CREDENTIAL_MODE=azure_cli is allowed only with LOCAL_DEV=1.")
    if s.managed_identity_client_id:
        try:
            UUID(s.managed_identity_client_id)
        except ValueError:
            errors.append("AZURE_CLIENT_ID must be a UUID.")
    if s.dev_simulator and not s.local_dev:
        errors.append("DEV_SIMULATOR is allowed only with LOCAL_DEV=1.")
    if s.after_hours not in ("on", "off", "auto"):
        errors.append("AFTER_HOURS must be on, off or auto.")
    try:
        from .afterhours import _day_set

        if not _day_set(s.service_days):
            raise ValueError("no service days")
        start, end = (datetime.strptime(part.strip(), "%H:%M").time() for part in s.service_hours.split("-"))
        if start >= end:
            raise ValueError("service hours must increase within one day")
        ZoneInfo(s.service_tz)
    except (ValueError, ZoneInfoNotFoundError):
        errors.append("SERVICE_DAYS, SERVICE_HOURS or SERVICE_TZ is invalid; use a valid day set, same-day HH:MM-HH:MM range and IANA timezone.")
    names = set()
    for function in s.functions:
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", function.name)
                or function.name in names | {"hangup", "transfer_to_rep"}):
            errors.append("FUNCTIONS names must be valid, unique and not replace built-in functions.")
        names.add(function.name)
        if type(function.timeout_ms) is not int or not 1 <= function.timeout_ms <= 300000:
            errors.append("FUNCTIONS timeoutMs must be between 1 and 300000.")
        if type(function.is_async) is not bool:
            errors.append("FUNCTIONS async must be a boolean.")

    if s.acs_connection_string and s.acs_endpoint:
        errors.append("Configure only one of ACS_CONNECTION_STRING or ACS_ENDPOINT.")
    if not s.acs_connection_string and not s.acs_endpoint:
        target = errors if s.profile == "voice" and not s.local_dev else warnings
        target.append("ACS_CONNECTION_STRING or ACS_ENDPOINT is required for voice calls; neither is configured.")
    if not s.extractor_agent and s.profile == "voice":
        target = errors if not s.local_dev else warnings
        target.append("EXTRACTOR_AGENT_NAME is required for structured voice intake.")
    if not s.api_keys:
        if s.local_dev:
            warnings.append("API_KEYS not set: /v1 is unauthenticated (LOCAL_DEV).")
        else:
            errors.append("API_KEYS is required outside LOCAL_DEV (/v1 would be unauthenticated).")
    if s.profile == "voice" and not s.gateway_secret:
        if s.local_dev:
            warnings.append("GATEWAY_SECRET not set: /api/callbacks and /ws are unauthenticated (LOCAL_DEV).")
        else:
            errors.append("GATEWAY_SECRET is required outside LOCAL_DEV (per-call tokens for /api/callbacks and /ws).")
    if s.profile == "voice" and not s.eventgrid_secret:
        if s.local_dev:
            warnings.append("EVENTGRID_SECRET not set: /api/incoming is unauthenticated (LOCAL_DEV).")
        else:
            errors.append("EVENTGRID_SECRET is required outside LOCAL_DEV (authenticates Event Grid deliveries).")
    if s.acs_jwt_mode not in ("off", "warn", "enforce"):
        errors.append(f"ACS_JWT_MODE={s.acs_jwt_mode!r} must be off, warn or enforce.")
    elif s.acs_jwt_mode != "enforce" and not s.local_dev and s.profile == "voice":
        errors.append("ACS_JWT_MODE must be enforce outside LOCAL_DEV.")
    elif s.acs_jwt_mode != "off" and not s.acs_resource_id and s.profile == "voice":
        errors.append("ACS_RESOURCE_ID is required when ACS_JWT_MODE is warn or enforce (it is the JWT audience).")
    elif s.acs_jwt_mode == "off":
        warnings.append("ACS_JWT_MODE=off: ACS-signed JWTs on /api/callbacks and /ws are not validated (LOCAL_DEV).")
    if s.webhook_urls and not s.webhook_secret:
        warnings.append("WEBHOOK_SECRET not set: outbound webhooks are unsigned.")
    from .webhooks import url_problem

    urls = list(s.webhook_urls) + [f.webhook_url for f in s.functions]
    for u in urls:
        p = url_problem(u, s.local_dev)
        if p:
            errors.append(f"Webhook URL {u.split('?')[0]} {p}.")
    if not s.transfer_teams_ids:
        warnings.append("TRANSFER_TEAMS_OBJECT_IDS not set: transfer to a rep is unavailable.")
    if not s.dataverse_url:
        if s.local_dev:
            warnings.append("DATAVERSE_URL not set: drafts and tickets are not stored (LOCAL_DEV).")
        else:
            errors.append("DATAVERSE_URL is required outside LOCAL_DEV (drafts and tickets are stored in Dataverse).")
    if s.dataverse_url and not (s.dataverse_schema or s.dataverse_schema_file) and not s.local_dev:
        errors.append("DATAVERSE_SCHEMA is required with DATAVERSE_URL: deployment sends the contents of dataverse/config.json.")
    if s.dataverse_schema or s.dataverse_schema_file:
        from .schema import Schema, SchemaError

        try:
            Schema.load(s.dataverse_schema, s.dataverse_schema_file)
        except SchemaError as e:
            errors.append(f"Dataverse schema: {e}")
    if s.live_notes:
        if s.speech_auth == "aad":
            if not s.speech_resource_id:
                warnings.append("SPEECH_AUTH=aad needs SPEECH_RESOURCE_ID: live notes disabled.")
        elif s.speech_auth != "key":
            warnings.append(f"SPEECH_AUTH={s.speech_auth!r} is not 'key' or 'aad': live notes disabled.")
        elif not s.speech_key:
            warnings.append("LIVE_NOTES=on but no SPEECH_KEY (or SPEECH_AUTH=aad): live notes disabled.")
    return errors, warnings


def report(s: Settings) -> tuple[list[str], list[str]]:
    errors, warnings = validate(s)
    for e in errors:
        log.error("config error: %s", e)
    for w in warnings:
        log.warning("config warning: %s", w)
    if not errors:
        log.info("configuration validated")
    return errors, warnings
