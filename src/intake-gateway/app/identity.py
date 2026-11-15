"""Credential selection and token acquisition shared by Azure adapters."""
from __future__ import annotations

import asyncio

from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import ClientAuthenticationError, ServiceResponseError
from azure.identity import AzureCliCredential, DefaultAzureCredential, ManagedIdentityCredential
from azure.identity import aio

from .config import Settings

SCOPE = "https://ai.azure.com/.default"


def _mode(settings: Settings) -> str:
    mode = settings.credential_mode if settings.credential_mode != "auto" else ("azure_cli" if settings.local_dev else "default")
    if mode not in ("azure_cli", "managed_identity", "default"):
        raise ValueError("unsupported Azure credential mode")
    if mode == "azure_cli" and not settings.local_dev:
        raise ValueError("Azure CLI credentials require LOCAL_DEV=1")
    return mode


def _default_options(settings: Settings) -> dict:
    return {
        "managed_identity_client_id": settings.managed_identity_client_id or None,
        "exclude_cli_credential": True,
        "exclude_powershell_credential": True,
        "exclude_developer_cli_credential": True,
        "exclude_shared_token_cache_credential": True,
        "exclude_visual_studio_code_credential": True,
        "exclude_broker_credential": True,
        "exclude_interactive_browser_credential": True,
    }


def sync_credential(settings: Settings) -> TokenCredential:
    mode = _mode(settings)
    if mode == "azure_cli":
        return AzureCliCredential()
    if mode == "managed_identity":
        return ManagedIdentityCredential(client_id=settings.managed_identity_client_id or None)
    return DefaultAzureCredential(**_default_options(settings))


def async_credential(settings: Settings) -> AsyncTokenCredential:
    mode = _mode(settings)
    if mode == "azure_cli":
        return aio.AzureCliCredential()
    if mode == "managed_identity":
        return aio.ManagedIdentityCredential(client_id=settings.managed_identity_client_id or None)
    return aio.DefaultAzureCredential(**_default_options(settings))


class TokenSource:
    def __init__(self, settings: Settings | bool, credential: AsyncTokenCredential | None = None):
        settings = Settings(local_dev=settings) if isinstance(settings, bool) else settings
        self._cred = credential or async_credential(settings)
        self._owns_credential = credential is None

    async def __aenter__(self) -> "TokenSource":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()

    async def get(self, scope: str = SCOPE) -> str:
        status = None

        def capture_response(response) -> None:
            nonlocal status
            status = response.http_response.status_code

        try:
            async with asyncio.timeout(30):
                return (await self._cred.get_token(scope, raw_response_hook=capture_response)).token
        except asyncio.TimeoutError as error:
            raise ServiceResponseError("Azure identity request deadline exceeded") from error
        except ClientAuthenticationError as error:
            # Credential chains can discard the HTTP status when wrapping authentication errors.
            if status is not None and (status == 429 or status >= 500):
                raise ServiceResponseError("Azure identity endpoint temporarily unavailable") from error
            raise

    async def aclose(self) -> None:
        if self._owns_credential:
            await self._cred.close()
