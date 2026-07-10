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
        # Remember order_id -> (agent_id, task) so get_delivery can produce the
        # winner's REAL work product (base agents run their actual cores).
        self._orders: dict[str, tuple[str, TaskSpec]] = {}


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
        # Remember who was hired so get_delivery can produce their real output.
        self._orders[order_id] = (agent.agent_id, task)
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

        agent_id, task = self._orders.get(order_id, (None, None))

        # If one of OUR base agents was hired (typically the fallback path),
        # produce its REAL work product by running the actual agent core. This
        # is what makes the base agents genuine supply, not dead hedges (§7/§10).
        if agent_id is not None:
            from agents.provider import BASE_AGENTS

            spec = BASE_AGENTS.get(agent_id)
            if spec is not None:
                params = dict(task.params or {}) if task else {}
                prompt = task.task_prompt if task else ""
                output = await spec.handler(prompt, params)
                return Delivery(
                    order_id=order_id,
                    output_text=output,
                    delivered_at=datetime.now(timezone.utc),
                )

        return Delivery(
            order_id=order_id,
            output_text=(
                f"[MOCK OUTPUT] Deliverable for {order_id}"
                + (f" by {agent_id}" if agent_id else "")
                + ". In live mode this is the hired agent's real work product."
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
    """Adapter to the real CROO CAP SDK (`croo-sdk`, PyPI).

    Wired against the official Python SDK Reference. Mapping of our interface
    onto the real SDK (as REQUESTER):

      discover_agents  -> NOT an SDK primitive. The SDK has no search/discovery
                          (account & service setup live in the Agent Store). So
                          candidates come from a CONFIGURED roster of Store
                          service ids (CROON_LIVE_CANDIDATES_JSON). Honest and
                          documented (README §CAP mapping).

      request_quote    -> CAP has NO native quote primitive. We DERIVE a quote
                          from the candidate's listed price / SLA (spec §4).
                          We deliberately do NOT open a negotiation just to
                          quote (that would create dangling on-chain state and
                          cost gas). The real negotiation happens in
                          hire_and_pay for the winner only.

      hire_and_pay     -> negotiate_order(req)  [requester initiates]
                          -> provider accept_negotiation -> ORDER_CREATED
                          -> pay_order(order_id)  [USDC on Base, auto-approve]
                          -> returns Settlement(order_id, tx_hash, ...)

      get_delivery     -> get_delivery(order_id) -> Delivery(deliverable_text)

    PRECONDITION: CROON's AA wallet must be funded with USDC on Base before
    pay_order, otherwise the SDK raises an insufficient-balance error
    (is_insufficient_balance). See README §Wallet funding.

    Auth: AgentClient(config, "croo_sk_...") via X-SDK-Key header.
    """

    # How long to wait for the provider to accept the negotiation before we
    # give up (engine will then route to fallback). Kept modest for demos.
    _ACCEPT_TIMEOUT_S = 30
    _POLL_INTERVAL_S = 1.5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.croo_sdk_key:
            raise RuntimeError(
                "CROON_CROO_SDK_KEY is required for live mode (croo_sk_... from "
                "the CROO Dashboard). Set it in .env or use CROON_CAP_MODE=mock."
            )
        # Imported lazily so mock mode never needs the SDK installed.
        from croo import AgentClient, Config  # type: ignore

        self._config = Config(
            base_url=settings.croo_api_url,
            ws_url=settings.croo_ws_url,
            rpc_url=settings.base_rpc_url,
        )
        self._client = AgentClient(self._config, settings.croo_sdk_key)

    async def discover_agents(
        self, category: str | None, limit: int
    ) -> list[AgentInfo]:
        # SDK has no discovery — build candidates from the configured roster.
        agents: list[AgentInfo] = []
        for r in self.settings.live_candidates:
            price = r.get("listed_price_usdc")
            agents.append(
                AgentInfo(
                    agent_id=r["agent_id"],
                    name=r.get("name", r["agent_id"]),
                    category=r.get("category"),
                    listed_price_usdc=Decimal(str(price)) if price is not None else None,
                    reputation=float(r.get("reputation", 0.5)),
                    is_base_agent=bool(r.get("is_base_agent", False)),
                    service_id=r.get("service_id"),
                    listed_eta_seconds=r.get("listed_eta_seconds"),
                )
            )
        if category:
            filtered = [a for a in agents if a.category == category]
            agents = filtered or agents
        return agents[:limit]

    async def request_quote(
        self, agent: AgentInfo, task: TaskSpec, timeout_s: int
    ) -> Quote | None:
        # DERIVED quote (spec §4): use the candidate's listed price/SLA as its
        # bid. No on-chain action here — quoting must be cheap and side-effect
        # free; only the winner is actually negotiated + paid.
        if agent.listed_price_usdc is None:
            return None
        return Quote(
            agent_id=agent.agent_id,
            agent_name=agent.name,
            price_usdc=agent.listed_price_usdc,
            eta_seconds=agent.listed_eta_seconds or 60,
            confidence=agent.reputation,
            is_base_agent=agent.is_base_agent,
        )

    async def hire_and_pay(
        self, agent: AgentInfo, task: TaskSpec, agreed_price_usdc: Decimal
    ) -> Settlement:
        if not agent.service_id:
            raise RuntimeError(
                f"Live candidate '{agent.agent_id}' has no service_id; cannot "
                "negotiate. Add it to CROON_LIVE_CANDIDATES_JSON."
            )

        # 1) Requester initiates the negotiation for the winning service.
        # TODO(verify): confirm the exact request dataclass/fields for
        # negotiate_order in the installed croo-sdk build (see provider.py /
        # requester.py examples). We pass a dict; the SDK accepts a request
        # object — adjust field names here if the SDK errors on unknown keys.
        neg = await self._client.negotiate_order(
            {
                "service_id": agent.service_id,
                "agent_id": agent.agent_id,
                "price": str(agreed_price_usdc),
                "requirement": task.task_prompt,
                "acceptance_criteria": task.acceptance_criteria,
            }
        )
        negotiation_id = _attr(neg, "negotiation_id", "id")

        # 2) Wait for the provider to accept -> on-chain Order is created.
        order_id = await self._await_order_created(negotiation_id)

        # 3) Pay the order in USDC on Base (SDK auto-handles ERC20 approve).
        # TODO(verify): confirm the SDK settles in the NATIVE USDC configured at
        # settings.usdc_contract_address (0x8335...2913), NOT bridged USDbC,
        # before the first real payment. pay_order takes no token arg today; if a
        # future SDK build exposes one, pass settings.usdc_contract_address here.
        pay = await self._client.pay_order(order_id)
        tx_hash = _attr(pay, "tx_hash", "transaction_hash", "hash", default=None)

        return Settlement(
            order_id=str(order_id),
            agent_id=agent.agent_id,
            amount_paid_usdc=agreed_price_usdc,
            tx_hash=tx_hash,
            settled_at=datetime.now(timezone.utc),
        )

    async def get_delivery(self, order_id: str) -> Delivery:
        delivery = await self._client.get_delivery(order_id)
        text = _attr(
            delivery, "deliverable_text", "output_text", "text", default=""
        )
        return Delivery(
            order_id=str(order_id),
            output_text=str(text or ""),
            delivered_at=datetime.now(timezone.utc),
        )

    async def close(self) -> None:
        """Release SDK HTTP/WebSocket connections."""
        try:
            await self._client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    # --- helpers -----------------------------------------------------------

    async def _await_order_created(self, negotiation_id: str) -> str:
        """Poll the negotiation until the provider accepts and an Order exists.

        We poll rather than rely on the WS callback to keep hire_and_pay a
        simple awaitable. Times out -> caller routes to fallback (§7).
        """
        deadline = asyncio.get_event_loop().time() + self._ACCEPT_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            neg = await self._client.get_negotiation(negotiation_id)
            order_id = _attr(neg, "order_id", default=None)
            status = str(_attr(neg, "status", default="") or "").lower()
            if order_id:
                return str(order_id)
            if status in {"rejected", "expired"}:
                raise RuntimeError(
                    f"Negotiation {negotiation_id} {status} by provider."
                )
            await asyncio.sleep(self._POLL_INTERVAL_S)
        raise TimeoutError(
            f"Provider did not accept negotiation {negotiation_id} in "
            f"{self._ACCEPT_TIMEOUT_S}s."
        )


def _attr(obj: object, *names: str, default: object = "__RAISE__") -> object:
    """Read the first present attribute (or dict key) from an SDK object.

    The SDK returns typed objects, but exact field names may vary slightly by
    version. This keeps the adapter resilient and confines that uncertainty to
    ONE place (per spec §4)."""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    if default != "__RAISE__":
        return default
    raise AttributeError(
        f"None of {names} found on {type(obj).__name__}; check SDK version."
    )



# =============================================================================
# Factory — flip the whole app with ONE env var (CROON_CAP_MODE)
# =============================================================================


def build_cap_client(settings: Settings | None = None) -> CapClient:
    """Return the CapClient implied by CROON_CAP_MODE (demo-day safety net)."""
    settings = settings or get_settings()
    if settings.is_live:
        return LiveCapClient(settings)
    return MockCapClient()
