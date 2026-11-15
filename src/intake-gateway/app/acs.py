"""ACS Call Automation adapter using the public asynchronous SDK."""
from __future__ import annotations

import logging

from azure.communication.callautomation import (
    AudioFormat,
    MediaStreamingAudioChannelType,
    MediaStreamingContentType,
    MediaStreamingOptions,
    StreamingTransportType,
    MicrosoftTeamsUserIdentifier,
)
from azure.communication.callautomation.aio import CallAutomationClient

from .config import Settings
from .security import call_token
from .identity import async_credential

log = logging.getLogger("gateway.acs")


class AcsClient:
    def __init__(self, settings: Settings, client: CallAutomationClient | None = None):
        self.s = settings
        self._owns_client = client is None
        self._credential = None
        if client is not None:
            self.client = client
        elif settings.acs_connection_string:
            self.client = CallAutomationClient.from_connection_string(settings.acs_connection_string)
        else:
            self._credential = async_credential(settings)
            self.client = CallAutomationClient(settings.acs_endpoint, self._credential)

    async def aclose(self) -> None:
        if self._owns_client:
            try:
                await self.client.close()
            finally:
                if self._credential is not None:
                    await self._credential.close()

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
        return await self.client.answer_call(
            incoming_context,
            f"{self.s.public_base_url}/api/callbacks/{call_id}{q}",
            media_streaming=media,
        )

    async def reject(self, incoming_context: str) -> None:
        await self.client.reject_call(incoming_context)

    async def add_teams_participants(self, conn_id: str, object_ids: list[str]) -> None:
        conn = self.client.get_call_connection(conn_id)
        for oid in object_ids:
            await conn.add_participant(target_participant=MicrosoftTeamsUserIdentifier(oid))

    async def hang_up(self, conn_id: str) -> None:
        conn = self.client.get_call_connection(conn_id)
        await conn.hang_up(is_for_everyone=True)
