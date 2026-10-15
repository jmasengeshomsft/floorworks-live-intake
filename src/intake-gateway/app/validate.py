"""Startup configuration validation: fatal for what nothing can work without, warnings for the rest."""
from __future__ import annotations

import logging

from .config import Settings

log = logging.getLogger("gateway.config")


def validate(s: Settings) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors mean the gateway cannot do its core job."""
    errors: list[str] = []
    warnings: list[str] = []
    if not s.public_base_url:
        errors.append("PUBLIC_BASE_URL is required (ACS callbacks and the media WebSocket are built from it).")
    if not s.foundry_project_endpoint:
        errors.append("FOUNDRY_PROJECT_ENDPOINT is required (voice and extractor agents live in the Foundry project).")
    if not s.voice_agent:
        errors.append("VOICE_AGENT_NAME is required.")

    if not s.acs_connection_string:
        warnings.append("ACS_CONNECTION_STRING not set: incoming calls cannot be answered; REST API stays available.")
    if not s.extractor_agent:
        warnings.append("EXTRACTOR_AGENT_NAME not set: no structured extraction.")
    if not s.api_keys:
        if s.local_dev:
            warnings.append("API_KEYS not set: /v1 is unauthenticated (LOCAL_DEV).")
        else:
            errors.append("API_KEYS is required outside LOCAL_DEV (/v1 would be unauthenticated).")
    if not s.gateway_secret:
        if s.local_dev:
            warnings.append("GATEWAY_SECRET not set: /api/callbacks and /ws are unauthenticated (LOCAL_DEV).")
        else:
            errors.append("GATEWAY_SECRET is required outside LOCAL_DEV (per-call tokens for /api/callbacks and /ws).")
    if not s.eventgrid_secret:
        if s.local_dev:
            warnings.append("EVENTGRID_SECRET not set: /api/incoming is unauthenticated (LOCAL_DEV).")
        else:
            errors.append("EVENTGRID_SECRET is required outside LOCAL_DEV (authenticates Event Grid deliveries).")
    if s.acs_jwt_mode not in ("off", "warn", "enforce"):
        errors.append(f"ACS_JWT_MODE={s.acs_jwt_mode!r} must be off, warn or enforce.")
    elif s.acs_jwt_mode != "enforce" and not s.local_dev:
        errors.append("ACS_JWT_MODE must be enforce outside LOCAL_DEV.")
    elif s.acs_jwt_mode != "off" and not s.acs_resource_id:
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
