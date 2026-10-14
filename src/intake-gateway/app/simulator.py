"""Scripted fake call for UI development without a phone line (DEV_SIMULATOR=on)."""
from __future__ import annotations

import asyncio
import time

from .state import CallState, EventHub

SCRIPT = [
    ("agent", "Thanks for calling FloorWorks service. How can I help?", {}),
    ("caller", "Hi, the hydraulic press H1 is leaking at the valve.", {"equipment": "Hydraulic Press H1", "issue": "valve leaking"}),
    ("agent", "Sorry to hear that. Is the machine still running?", {}),
    ("caller", "We shut it down. It's losing pressure, maybe 3000 psi.", {"severity": "machine down"}),
    ("agent", "Understood. Can I get your name and a callback number?", {}),
    ("caller", "Sam Rivera, 555-0142.", {"contactName": "Sam Rivera", "contactPhone": "555-0142"}),
]

ENRICHMENT = {
    "likelyCause": "HV-220 seat seal extrusion under pressure (lot 4417).",
    "bulletin": "SB-2026-014",
    "parts": [{"partNumber": "P-HV220", "name": "Valve HV-220", "unitCost": 412.0}],
    "suggestedPriority": "High",
    "priorityReason": "Machine down; known quality alert.",
    "diagnosticSteps": ["Lock out / tag out press H1", "Check HV-220 lot number", "Inspect seat seal for extrusion"],
    "sources": ["SB-2026-014", "Hydraulic Press H1 manual"],
}


async def run(st: CallState, hub: EventHub, delay: float = 2.0) -> None:
    hub.publish(st.call_id, "call.incoming", {"callerPhone": st.caller_phone, "afterHours": False})
    await asyncio.sleep(delay)
    st.status = "active"
    hub.publish(st.call_id, "call.connected", {})
    for speaker, text, fields in SCRIPT:
        await asyncio.sleep(delay)
        line = st.add_line(speaker, text)
        hub.publish(st.call_id, "transcript", {"speaker": speaker, "text": text, "ts": line["ts"]})
        if fields:
            st.fields.update(fields)
            hub.publish(st.call_id, "extraction.updated", {"fields": st.fields, "version": st.version, "mode": "simulated"})
        if fields.get("equipment"):
            st.enrichment = ENRICHMENT
            hub.publish(st.call_id, "enrichment.updated", ENRICHMENT)
    await asyncio.sleep(delay)
    st.status = "ended"
    st.ended_at = time.time()
    hub.publish(st.call_id, "extraction.final", {"fields": st.fields, "enrichment": st.enrichment})
    hub.publish(st.call_id, "call.ended", {"fields": st.fields, "enrichment": st.enrichment, "transcript": st.transcript})
