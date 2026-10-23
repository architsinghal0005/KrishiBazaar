"""
Agentverse Mailbox Authentication Adapter for KrishiBazaar.

Resolves HTTP 401 ("Could not validate credentials") on Agentverse v2 mailbox APIs
(https://agentverse.ai/v2/agents/{address}/mailbox) by injecting Bearer token
authentication for mailbox polling and envelope deletion while preserving
cryptographic attestation fallbacks. Never logs secrets or private keys.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import aiohttp
from aiohttp import ClientConnectorError

from uagents import Agent
from uagents.mailbox import MailboxClient, StoredEnvelope
from krishibazaar.config import settings

logger = logging.getLogger("krishibazaar.mailbox_auth")


def get_mailbox_auth_headers(
    api_key: str | None = None,
    attestation: str | None = None,
) -> dict[str, str]:
    """
    Construct HTTP headers for Agentverse v2 mailbox API.
    Uses Bearer and X-API-Key when API key is available; falls back to Agent attestation.
    """
    effective_key = api_key or settings.agentverse_api_key
    if effective_key:
        return {
            "Authorization": f"Bearer {effective_key}",
            "X-API-Key": effective_key,
        }
    if attestation:
        return {
            "Authorization": f"Agent {attestation}",
        }
    return {}


def patch_mailbox_client(client: MailboxClient, api_key: str | None = None) -> None:
    """
    Patches a uagents MailboxClient instance to use Bearer token authentication
    for both message retrieval and deletion against Agentverse v2 APIs.
    """
    if not client:
        return

    effective_key = api_key or settings.agentverse_api_key

    def get_headers(self) -> dict[str, str]:
        return get_mailbox_auth_headers(
            api_key=effective_key,
            attestation=self.attestation,
        )

    # Attach header provider
    client.get_headers = get_headers.__get__(client, MailboxClient)

    async def authenticated_check_mailbox_loop(self):
        """Retrieves envelopes from the mailbox server with authenticated headers."""
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    agents_url = self._agentverse.agents_api
                    async with session.get(
                        f"{agents_url}/{self._identity.address}/mailbox",
                        headers=self.get_headers(),
                    ) as resp:
                        success = resp.status == 200
                        if success:
                            items = await resp.json()
                            for item in items:
                                stored_env = StoredEnvelope.model_validate(item)
                                await self._handle_envelope(stored_env)
                        elif resp.status == 404:
                            if not self._missing_mailbox_warning_logged:
                                self._logger.warning(
                                    "Agent mailbox not found: create one using the agent inspector"
                                )
                                self._missing_mailbox_warning_logged = True
                        else:
                            # Log status safely without leaking payload or auth headers
                            self._logger.error(
                                f"Failed to retrieve messages from Agentverse mailbox (HTTP {resp.status})"
                            )
            except (ClientConnectorError, asyncio.TimeoutError) as ex:
                self._logger.warning(f"Failed to connect to mailbox server: {ex}")
            except Exception as ex:
                self._logger.exception(f"Got exception while checking mailbox: {ex}")

            await asyncio.sleep(self._poll_interval)

    async def authenticated_delete_envelope(self, envelope_uuid: Any):
        """Deletes an envelope from the mailbox server with authenticated headers."""
        try:
            async with aiohttp.ClientSession() as session:
                agents_url = self._agentverse.agents_api
                self._logger.debug(f"Deleting message from mailbox: {str(envelope_uuid)}")
                async with session.delete(
                    f"{agents_url}/{self._identity.address}/mailbox/{str(envelope_uuid)}",
                    headers=self.get_headers(),
                ) as resp:
                    if resp.status >= 300:
                        self._logger.error(
                            f"Failed to delete envelope from mailbox (HTTP {resp.status})"
                        )
                    else:
                        self._logger.debug(
                            f"Deleted envelope {envelope_uuid} from Agentverse mailbox (HTTP {resp.status})"
                        )
        except ClientConnectorError as ex:
            self._logger.warning(f"Failed to connect to mailbox server: {ex}")
        except Exception as ex:
            self._logger.exception(f"Got exception while deleting message: {ex}")

    client._check_mailbox_loop = authenticated_check_mailbox_loop.__get__(client, MailboxClient)
    client._delete_envelope = authenticated_delete_envelope.__get__(client, MailboxClient)


def patch_agent_mailbox(agent: Agent, api_key: str | None = None) -> None:
    """Patches an Agent's mailbox client if mailbox is enabled."""
    if getattr(agent, "_use_mailbox", False) and getattr(agent, "_mailbox_client", None):
        patch_mailbox_client(agent._mailbox_client, api_key=api_key)
