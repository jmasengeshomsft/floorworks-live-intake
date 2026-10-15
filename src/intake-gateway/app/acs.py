"""Thin async wrapper over the ACS Call Automation SDK (sync SDK -> to_thread)."""
from __future__ import annotations

import asyncio
import logging

from azure.communication.callautomation import (
    AudioFormat,
    CallAutomationClient,
    CallInvite,
    MediaStreamingAudioChannelType,
    MediaStreamingContentType,
    MediaStreamingOptions,
    StreamingTransportType,
)
from azure.communication.callautomation import CommunicationIdentifier  # noqa: F401  (re-export for typing)
from azure.communication.callautomation._shared.models import MicrosoftTeamsUserIdentifier

from .config import Settings
from .security import call_token

log = logging.getLogger("gateway.acs")


class AcsClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = CallAutomationClient.from_connection_string(settings.acs_connection_string)

    async def answer(self, incoming_context: str, call_id: str):
        q = f"?t={call_token(self.s.gateway_secret, call_id)}" if self.s.gateway_secret else ""
        media = MediaStreamingOptions(
            transport_url=f"{self.s.ws_base}/ws/{call_id}{q}",
            transport_type=StreamingTransportType.WEBSOCKET,
            content_type=MediaStreamingContentType.AUDIO,
            audio_channel_type=MediaStreamingAudioChannelType.UNMIXED,
            start_media_streaming=True,
            enable_bidirectional=True,
            audio_format=AudioFormat.PCM24_K_MONO,
        )
        return await asyncio.to_thread(
            self.client.answer_call,
            incoming_context,
            f"{self.s.public_base_url}/api/callbacks/{call_id}{q}",
            media_streaming=media,
        )

    async def reject(self, incoming_context: str) -> None:
        await asyncio.to_thread(self.client.reject_call, incoming_context)

    async def add_teams_participants(self, conn_id: str, object_ids: list[str]) -> None:
        conn = self.client.get_call_connection(conn_id)
        for oid in object_ids:
            await asyncio.to_thread(conn.add_participant, CallInvite(target=MicrosoftTeamsUserIdentifier(oid)))

    async def hang_up(self, conn_id: str) -> None:
        conn = self.client.get_call_connection(conn_id)
        await asyncio.to_thread(conn.hang_up, True)
