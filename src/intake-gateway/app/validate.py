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
        warnings.append("API_KEYS not set: /v1 is unauthenticated (dev only).")
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
