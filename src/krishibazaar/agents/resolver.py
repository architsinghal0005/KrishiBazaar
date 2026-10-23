"""
Hierarchical and Resilient Address Resolver for KrishiBazaar.
Avoids slow Almanac API lookups and Almanac contract resolution errors
when Agentverse experiences transient network degradation (e.g. 503/522 timeouts).
Directly resolves known specialist agent addresses to active mailbox submit endpoints
(or local ASGI submit endpoints if configured).
"""

from __future__ import annotations
import asyncio
import logging
from uagents.resolver import GlobalResolver, Resolver

from krishibazaar.config import settings

logger = logging.getLogger("krishibazaar.resolver")


class KrishiBazaarResolver(Resolver):
    """
    Hierarchical address resolver for the KrishiBazaar multi-agent system.
    Resolves known specialist agent addresses directly to their mailbox submit endpoint
    (https://agentverse.ai/v2/agents/mailbox/submit) or configured endpoint,
    eliminating Almanac API lookups and on-chain contract resolution timeouts during network degradation.
    Delegates unknown addresses to GlobalResolver with a bounded timeout.
    """

    def __init__(
        self,
        fallback_resolver: Resolver | None = None,
        custom_endpoints: dict[str, list[str]] | None = None,
        lookup_timeout_seconds: float = 3.0,
    ) -> None:
        self._fallback = fallback_resolver or GlobalResolver()
        self._lookup_timeout = lookup_timeout_seconds
        self._custom_endpoints = custom_endpoints or {}

    def get_known_endpoints(self, destination: str) -> list[str] | None:
        """Return configured submit endpoints for known specialist agents and orchestrator."""
        if destination in self._custom_endpoints:
            return self._custom_endpoints[destination]

        mailbox_submit_url = f"{settings.agentverse_url.rstrip('/')}/v2/agents/mailbox/submit"
        known_map: dict[str, list[str]] = {}

        specialist_pairs = [
            (settings.market_agent_address, settings.market_agent_port),
            (settings.weather_agent_address, settings.weather_agent_port),
            (settings.buyer_agent_address, settings.buyer_agent_port),
            (settings.buyer_b_agent_address, settings.buyer_b_agent_port),
            (settings.transport_agent_address, settings.transport_agent_port),
        ]
        for addr, port in specialist_pairs:
            if addr and addr.strip():
                known_map[addr.strip()] = [
                    f"http://127.0.0.1:{port}/submit",
                    mailbox_submit_url,
                ]

        # Register Orchestrator agent address for bidirectional resolution
        try:
            from uagents.crypto import Identity

            orch_addr = Identity.from_seed(settings.orchestrator_seed, 0).address
            if orch_addr:
                known_map[orch_addr] = [
                    f"http://127.0.0.1:{settings.orchestrator_port}/submit",
                    mailbox_submit_url,
                ]

            # Register single canonical Payment specialist agent address on port 8006
            canonical_pay_addr = settings.payment_payee_agent_address
            if not canonical_pay_addr and settings.payment_payee_seed:
                canonical_pay_addr = Identity.from_seed(settings.payment_payee_seed, 0).address

            if canonical_pay_addr and canonical_pay_addr.strip():
                known_map[canonical_pay_addr.strip()] = [
                    f"http://127.0.0.1:{settings.payment_agent_port}/submit",
                    mailbox_submit_url,
                ]
        except Exception:
            pass

        if destination and destination in known_map:
            return known_map[destination]

        return None

    async def resolve(self, destination: str) -> tuple[str | None, list[str]]:
        """
        Resolve destination address to active endpoints.
        """
        if not destination or not destination.strip():
            return None, []

        # 1. Fast path: Direct resolution for known specialist agents
        known = self.get_known_endpoints(destination)
        if known:
            logger.debug(f"Direct resolution for specialist {destination} -> {known}")
            return destination, known

        # 2. Fallback to GlobalResolver with bounded timeout to avoid hanging contract lookups
        try:
            return await asyncio.wait_for(
                self._fallback.resolve(destination),
                timeout=self._lookup_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Address resolution for '{destination}' timed out after {self._lookup_timeout}s. "
                "Almanac API unreachable; skipping hanging contract lookup."
            )
            return None, []
        except Exception as ex:
            logger.warning(
                f"Address resolution failed for '{destination}': {type(ex).__name__} - {str(ex)}"
            )
            return None, []


_default_resolver: KrishiBazaarResolver | None = None


def get_krishibazaar_resolver() -> KrishiBazaarResolver:
    """Return shared KrishiBazaarResolver instance."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = KrishiBazaarResolver()
    return _default_resolver
