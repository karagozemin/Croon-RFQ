"""CapClient — the ONE isolation boundary for all CAP interactions.

ALL uncertainty about the real CROO CAP SDK lives HERE and nowhere else.
The engine depends only on this abstract interface + the schemas.

- `CapClient`     : abstract interface (contract the engine relies on).
- `MockCapClient` : deterministic fake agents; lets us build/test the entire
                    pipeline with NO network, NO keys, NO funded wallet.
- `LiveCapClient` : real SDK adapter. STUBBED for now — wired in build step 4
                    AFTER confirming real method names/signatures from the
                    official CROO CAP SDK docs. Do NOT invent methods.

Quote semantics note (spec §4): CAP may have no native "quote" primitive.
In that case a quote is DERIVED from the agent's listed price / SLA (+ any
negotiation signal). MockCapClient models exactly that shape so the scoring
engine is meaningful either way.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
from datetime import datetime, timezone

from decimal import Decimal

from croon.config import Settings, get_settings
from croon.schemas import AgentInfo, Delivery, Quote, Settlement, TaskSpec


class CapClient(abc.ABC):
    """Abstract CAP interface. The engine codes against THIS, not the SDK."""

    @abc.abstractmethod
    async def discover_agents(
        self, category: str | None, limit: int
    ) -> list[AgentInfo]:
        """Discover candidate supply agents (optionally filtered by category)."""

    @abc.abstractmethod
    async def request_quote(
        self, agent: AgentInfo, task: TaskSpec, timeout_s: int
    ) -> Quote | None:
        """Ask one agent for a quote. Returns None on timeout / no-bid."""

    @abc.abstractmethod
    async def hire_and_pay(
        self, agent: AgentInfo, task: TaskSpec, agreed_price_usdc: Decimal
    ) -> Settlement:
        """Hire the winning agent and settle payment in USDC on Base."""

    @abc.abstractmethod
    async def get_delivery(self, order_id: str) -> Delivery:
        """Fetch the delivered output for a settled order."""


# =============================================================================
# MockCapClient — deterministic fake market (no network required)
# =============================================================================

# A small, believable roster. Prices/ETAs/reputation are static so demos are
# reproducible; per-run jitter is derived deterministically from the order id.
_MOCK_ROSTER: list[dict] = [
    {
        "agent_id": "agent_alpha",
        "name": "Alpha Analytics",
        "category": "risk",
        "listed_price_usdc": Decimal("0.12"),
        "reputation": 0.72,
        "base_eta": 40,
        "is_base_agent": False,
    },
    {
        "agent_id": "agent_beta",
        "name": "Beta Insights",
        "category": "risk",
        "listed_price_usdc": Decimal("0.20"),
        "reputation": 0.91,
        "base_eta": 20,
        "is_base_agent": False,
    },
    {
        "agent_id": "agent_gamma",
        "name": "Gamma Research",
        "category": "research",
        "listed_price_usdc": Decimal("0.08"),
        "reputation": 0.65,
        "base_eta": 60,
        "is_base_agent": False,
    },
    # OUR base agents — real, hireable fallback providers (§7, §10). Modeled here
    # so the mock pipeline exercises the fallback path too.
    {
        "agent_id": "base_listing_copy",
        "name": "CROON Listing Copy Agent",
        "category": "research",
        "listed_price_usdc": Decimal("0.05"),
        "reputation": 0.60,
        "base_eta": 15,
        "is_base_agent": True,
    },
    {
        "agent_id": "base_gas_oracle",
        "name": "CROON Base Gas Oracle",
        "category": "infra",
        "listed_price_usdc": Decimal("0.01"),
        "reputation": 0.55,
        "base_eta": 5,
        "is_base_agent": True,
    },
]


class MockCapClient(CapClient):
    """Deterministic fake CAP network. Drives the full pipeline offline.

    Optional failure injection (for demoing the fallback path):
      - `fail_non_base_quotes=True` makes all non-base agents time out, forcing
        the engine down the fallback route (§7).
    """

    def __init__(
        self,
        *,
        simulate_latency: bool = True,
        fail_non_base_quotes: bool = False,
    ) -> None:
        self.simulate_latency = simulate_latency
        self.fail_non_base_quotes = fail_non_base_quotes

    async def discover_agents(
        self, category: str | None, limit: int
    ) -> list[AgentInfo]:
        candidates = [
            AgentInfo(
                agent_id=r["agent_id"],
                name=r["name"],
                category=r["category"],
                listed_price_usdc=r["listed_price_usdc"],
                reputation=r["reputation"],
                is_base_agent=r["is_base_agent"],
            )
            for r in _MOCK_ROSTER
            if not r["is_base_agent"]  # base agents are fallback-only, not discovered
        ]
        if category:
            filtered = [a for a in candidates if a.category == category]
            # If category filter is too narrow, fall back to all non-base agents
            # so the RFQ still has bidders (demo robustness).
            candidates = filtered or candidates
        return candidates[:limit]

    async def request_quote(
        self, agent: AgentInfo, task: TaskSpec, timeout_s: int
    ) -> Quote | None:
        row = self._row(agent.agent_id)
        if row is None:
            return None

        # Failure injection for the fallback demo.
        if self.fail_non_base_quotes and not row["is_base_agent"]:
            await asyncio.sleep(min(timeout_s + 1, 2))  # simulate a stall
            return None

        # Deterministic per-(agent,task) jitter so runs look "live" but reproduce.
        jitter = self._jitter(agent.agent_id + task.task_prompt)
        eta = max(1, int(row["base_eta"] + (jitter - 0.5) * 10))

        if self.simulate_latency:
            # A tiny, bounded response delay (never exceeds the RFQ timeout).
            await asyncio.sleep(min(0.2 + jitter * 0.3, timeout_s * 0.5))

        return Quote(
            agent_id=agent.agent_id,
            agent_name=agent.name,
            price_usdc=row["listed_price_usdc"],
            eta_seconds=eta,
            confidence=round(row["reputation"] + (jitter - 0.5) * 0.05, 3),
            is_base_agent=row["is_base_agent"],
        )

    async def hire_and_pay(
        self, agent: AgentInfo, task: TaskSpec, agreed_price_usdc: Decimal
    ) -> Settlement:
        if self.simulate_latency:
            await asyncio.sleep(0.3)
        order_id = "mock_order_" + hashlib.sha256(
            (agent.agent_id + task.task_prompt).encode()
        ).hexdigest()[:12]
        # Fake but BaseScan-shaped tx hash (0x + 64 hex chars).
        tx_hash = "0x" + hashlib.sha256(
            (order_id + datetime.now(timezone.utc).isoformat()).encode()
        ).hexdigest()
        return Settlement(
            order_id=order_id,
            agent_id=agent.agent_id,
            amount_paid_usdc=agreed_price_usdc,
            tx_hash=tx_hash,
            settled_at=datetime.now(timezone.utc),
        )

    async def get_delivery(self, order_id: str) -> Delivery:
        if self.simulate_latency:
            await asyncio.sleep(0.2)
        return Delivery(
            order_id=order_id,
            output_text=(
                f"[MOCK OUTPUT] Deliverable for {order_id}. "
                "In live mode this is the hired agent's real work product."
            ),
            delivered_at=datetime.now(timezone.utc),
        )

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _row(agent_id: str) -> dict | None:
        for r in _MOCK_ROSTER:
            if r["agent_id"] == agent_id:
                return r
        return None

    @staticmethod
    def _jitter(seed: str) -> float:
        """Deterministic 0..1 value from a seed string."""
        h = hashlib.sha256(seed.encode()).hexdigest()
        return (int(h[:8], 16) % 1000) / 1000.0


# =============================================================================
# LiveCapClient — real SDK adapter (STUBBED until build step 4)
# =============================================================================


class LiveCapClient(CapClient):
    """Adapter to the real CROO CAP SDK.

    TODO(step 4): Wire against the official CROO CAP SDK.
      - Confirm REAL method names/signatures, auth/key setup, wallet funding
        requirement, and event names from the official docs (link in README).
      - CROON's AA wallet must be funded with USDC on Base before hire_and_pay.
      - Map request_quote onto CAP's negotiation phase, or DERIVE a quote from
        the agent's listed price/SLA if no native quote primitive exists.
    Until then this raises clearly so nobody accidentally runs it half-wired.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # from croo_cap_sdk import CapSDK  # TODO(step 4): real import
        raise NotImplementedError(
            "LiveCapClient is not wired yet. Use CROON_CAP_MODE=mock. "
            "Wiring happens in build step 4 after confirming the CAP SDK docs."
        )

    async def discover_agents(self, category, limit):  # pragma: no cover
        raise NotImplementedError

    async def request_quote(self, agent, task, timeout_s):  # pragma: no cover
        raise NotImplementedError

    async def hire_and_pay(self, agent, task, agreed_price_usdc):  # pragma: no cover
        raise NotImplementedError

    async def get_delivery(self, order_id):  # pragma: no cover
        raise NotImplementedError


# =============================================================================
# Factory — flip the whole app with ONE env var (CROON_CAP_MODE)
# =============================================================================


def build_cap_client(settings: Settings | None = None) -> CapClient:
    """Return the CapClient implied by CROON_CAP_MODE (demo-day safety net)."""
    settings = settings or get_settings()
    if settings.is_live:
        return LiveCapClient(settings)
    return MockCapClient()
