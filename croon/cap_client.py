"""CapClient - the ONE isolation boundary for all CAP interactions.

ALL uncertainty about the real CROO CAP SDK lives HERE and nowhere else.
The engine depends only on this abstract interface + the schemas.

- `CapClient`     : abstract interface (contract the engine relies on).
- `MockCapClient` : deterministic fake agents; lets us build/test the entire
                    pipeline with NO network, NO keys, NO funded wallet.
- `LiveCapClient` : real SDK adapter. STUBBED for now - wired in build step 4
                    AFTER confirming real method names/signatures from the
                    official CROO CAP SDK docs. Do NOT invent methods.

Quote semantics note (spec sec.4): CAP may have no native "quote" primitive.
In that case a quote is DERIVED from the agent's listed price / SLA (+ any
negotiation signal). MockCapClient models exactly that shape so the scoring
engine is meaningful either way.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from decimal import Decimal

import httpx


from croon.config import Settings, get_settings
from croon.schemas import AgentInfo, Delivery, Quote, Settlement, TaskSpec

_log = logging.getLogger("croon.cap_client")

# CAP delivery is async (provider fulfils AFTER on-chain payment). How long a
# live get_delivery() polls for the deliverable before returning a pending
# (empty) Delivery. A run whose payment already settled must NEVER fail just
# because the provider hasn't produced output yet.
DELIVERY_POLL_TIMEOUT_S = 15.0
DELIVERY_POLL_INTERVAL_S = 2.0



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
# MockCapClient - deterministic fake market (no network required)
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
    # OUR base agents - real, hireable fallback providers (sec.7, sec.10). Modeled here
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
        the engine down the fallback route (sec.7).
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
        # is what makes the base agents genuine supply, not dead hedges (sec.7/sec.10).
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
# LiveCapClient - real SDK adapter (STUBBED until build step 4)
# =============================================================================


class LiveCapClient(CapClient):
    """Adapter to the real CROO CAP SDK (`croo-sdk`, PyPI).

    Wired against the official Python SDK Reference. Mapping of our interface
    onto the real SDK (as REQUESTER):

      discover_agents  -> NOT an SDK primitive. The SDK has no search/discovery
                          (account & service setup live in the Agent Store). So
                          candidates come from a CONFIGURED roster of Store
                          service ids (CROON_LIVE_CANDIDATES_JSON). Honest and
                          documented (README sec.CAP mapping).

      request_quote    -> CAP has NO native quote primitive. We DERIVE a quote
                          from the candidate's listed price / SLA (spec sec.4).
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
    (is_insufficient_balance). See README sec.Wallet funding.

    Auth: AgentClient(config, "croo_sk_...") via X-SDK-Key header.
    """

    # How long to wait for the provider to accept the negotiation before we
    # give up (engine will then route to fallback). Matched to the CLI path
    # (scripts/live_order.py, 60s): external Store providers frequently take
    # 30-60s to move a negotiation to an on-chain-payable 'created' order, and
    # a too-short window makes the UI path time out and fall back to a mock
    # (SIMULATED) settlement even though a real settlement was achievable.
    _ACCEPT_TIMEOUT_S = 60
    _POLL_INTERVAL_S = 2.0


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
        # Our own requester agent id, stamped onto negotiations so the provider
        # (and our run history) can attribute the request. Optional per SDK.
        self._requester_agent_id = settings.croo_requester_agent_id or ""


    async def discover_agents(
        self, category: str | None, limit: int
    ) -> list[AgentInfo]:
        # SDK has no discovery - build candidates from the configured roster.
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
                    # Optional per-service requirements schema override (dict or
                    # str). See AgentInfo.requirements_template / hire_and_pay.
                    requirements_template=r.get("requirements_template"),
                )

            )
        if category:
            filtered = [a for a in agents if a.category == category]
            agents = filtered or agents
        return agents[:limit]

    async def request_quote(
        self, agent: AgentInfo, task: TaskSpec, timeout_s: int
    ) -> Quote | None:
        # DERIVED quote (spec sec.4): use the candidate's listed price/SLA as its
        # bid. No on-chain action here - quoting must be cheap and side-effect
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

        # Verified against croo-sdk: negotiate_order takes a typed
        # NegotiateOrderRequest (NOT a dict). Fields confirmed by introspection:
        #   service_id, requirements, metadata, requester_agent_id,
        #   fund_amount, fund_token.  There is NO `price` field - the price comes
        #   from the provider's listed service; `agreed_price_usdc` is enforced
        #   OFF-CHAIN by our budget rule (we only negotiate the winner, whose
        #   listed price already passed the budget gate in scoring).
        from croo import NegotiateOrderRequest  # type: ignore

        req = NegotiateOrderRequest(
            service_id=agent.service_id,
            requirements=self._build_requirements(agent, task),
            # Acceptance criteria also carried as metadata (SDK metadata is a string).
            metadata=json.dumps(
                {"acceptance_criteria": task.acceptance_criteria}
            ),
            requester_agent_id=self._requester_agent_id,
        )


        # 1) Requester initiates the negotiation for the winning service.
        neg = await self._client.negotiate_order(req)
        negotiation_id = _attr(neg, "negotiation_id", "id")

        # 2) Wait for the provider to accept -> on-chain Order is created.
        #    Negotiation has NO order_id field; we resolve the Order by matching
        #    negotiation_id via list_orders (see _await_order_created).
        order_id = await self._await_order_created(str(negotiation_id))

        # 3) Pay the order in USDC on Base (SDK auto-handles ERC20 approve).
        # TODO(verify): confirm the SDK settles in the NATIVE USDC configured at
        # settings.usdc_contract_address (0x8335...2913), NOT bridged USDbC,
        # before the first real payment. pay_order(order_id) takes no token arg.
        pay = await self._client.pay_order(order_id)
        # PayOrderResult exposes `tx_hash` and the updated `order`; prefer the
        # top-level tx_hash, fall back to the order's pay_tx_hash.
        tx_hash = _attr(pay, "tx_hash", default=None)
        if not tx_hash:
            order_obj = _attr(pay, "order", default=None)
            if order_obj is not None:
                tx_hash = _attr(order_obj, "pay_tx_hash", default=None)

        # INTEGRITY GATE: the SDK reporting a tx_hash is NOT proof of an on-chain
        # payment. We independently confirm the hash exists on the CONFIGURED
        # Base RPC via eth_getTransactionByHash. If it is absent (e.g. the SDK
        # returned an off-chain/optimistic id, or settled on a different chain
        # than base_rpc_url), we mark the settlement UNVERIFIED so the engine and
        # UI never present it as a real, BaseScan-linkable live payment.
        tx_verified = await self._verify_tx_on_chain(tx_hash) if tx_hash else None

        return Settlement(
            order_id=str(order_id),
            agent_id=agent.agent_id,
            amount_paid_usdc=agreed_price_usdc,
            tx_hash=tx_hash,
            tx_verified=tx_verified,
            settled_at=datetime.now(timezone.utc),
        )

    async def _verify_tx_on_chain(self, tx_hash: str) -> bool:
        """Return True iff `tx_hash` is found on the configured Base RPC.

        Uses a single eth_getTransactionByHash JSON-RPC call. A transaction that
        is broadcast but not yet mined still returns a non-null result (with a
        null blockNumber), which is enough to confirm it is a REAL tx that hit
        the network. A null result means the node has never seen this hash - we
        must NOT trust it as a live settlement. Network/RPC errors are treated as
        "unverified" (False) rather than raising, so a flaky RPC never crashes a
        run whose payment may well be valid - the run is simply labeled honestly.
        """
        try:
            async with httpx.AsyncClient(timeout=8.0) as http:
                resp = await http.post(
                    self.settings.base_rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_getTransactionByHash",
                        "params": [tx_hash],
                    },
                )
                resp.raise_for_status()
                result = resp.json().get("result")
                found = result is not None
                if not found:
                    _log.warning(
                        "tx_hash %s NOT found on Base RPC %s - settlement marked "
                        "UNVERIFIED (not a confirmable on-chain payment).",
                        tx_hash, self.settings.base_rpc_url,
                    )
                return found
        except Exception as exc:  # noqa: BLE001 - verification must never crash
            _log.warning(
                "on-chain verification of %s failed (%s: %s) - marking "
                "UNVERIFIED.", tx_hash, type(exc).__name__, exc,
            )
            return False


    async def get_delivery(self, order_id: str) -> Delivery:
        """Fetch the delivered output for a paid order.

        CAP delivery is ASYNCHRONOUS: pay_order() settles USDC on Base
        immediately, but the provider produces its deliverable afterwards. So a
        get_delivery() call right after payment routinely 404s
        (DELIVERY_NOT_FOUND) until the provider fulfils. We poll briefly, then
        degrade GRACEFULLY: a not-yet-ready delivery must NOT crash a run whose
        payment already succeeded on-chain. The run is still valid - the tx hash
        is the proof of spend - and the buyer can re-fetch the delivery later.
        """
        deadline = asyncio.get_event_loop().time() + DELIVERY_POLL_TIMEOUT_S
        last_exc: Exception | None = None
        while True:
            try:
                delivery = await self._client.get_delivery(order_id)
                text = _attr(
                    delivery, "deliverable_text", "output_text", "text",
                    default="",
                )
                if text:
                    return Delivery(
                        order_id=str(order_id),
                        output_text=str(text),
                        delivered_at=datetime.now(timezone.utc),
                    )
                # Delivery row exists but is empty/pending - keep polling.
            except Exception as exc:  # noqa: BLE001 - 404 while provider works
                last_exc = exc
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(DELIVERY_POLL_INTERVAL_S)

        # Payment already settled on-chain; deliverable simply isn't ready yet.
        # Return an empty-but-valid Delivery so the receipt still anchors the
        # tx hash. Caller records output_ref="" / a pending marker.
        _log.warning(
            "delivery not ready for order_id=%s within %ss (payment already "
            "settled; returning pending delivery). last_error=%s",
            order_id,
            DELIVERY_POLL_TIMEOUT_S,
            last_exc,
        )
        return Delivery(
            order_id=str(order_id),
            output_text="",
            delivered_at=datetime.now(timezone.utc),
        )

    async def close(self) -> None:
        """Release SDK HTTP/WebSocket connections."""
        try:
            await self._client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _build_requirements(agent: AgentInfo, task: TaskSpec) -> str:
        """Build the CAP `requirements` string for a negotiation.

        CAP enforces the requirements schema PROVIDER-SIDE and there is NO
        discovery/describe endpoint to introspect it (the SDK exposes only
        /orders/* and /objects/*). Sending an object with fields a service
        doesn't recognise gets rejected (INVALID_PARAMETERS: unsupported
        requirement field "<field>"). So we support three modes, in priority:

          1) Per-service OVERRIDE (agent.requirements_template):
             - dict -> merged with {task_prompt, acceptance_criteria} and JSON
               encoded (operator opts in to fields the service accepts).
             - str  -> used verbatim (operator supplies the exact payload).
          2) SCHEMA-AGNOSTIC DEFAULT (no template): encode the prompt as a bare
             JSON string, e.g. json.dumps("do X") == '"do X"'. This is valid
             JSON (passes "requirements must be valid JSON") yet has NO object
             fields, so there is nothing for a provider to reject as an
             unsupported field. Our own base/main providers read the raw
             requirements string as the prompt, so this stays compatible.
        """
        template = agent.requirements_template
        if isinstance(template, str):
            return template
        if isinstance(template, dict):
            # Verbatim: providers reject ANY unrecognised field (proven by
            # scripts/probe_requirements_shape.py), including our own
            # task_prompt/acceptance_criteria. Operator supplies EXACTLY the
            # object the service accepts, e.g. {"wallet_address": "0x..."} or {}
            # for a parameterless service. Never inject extra fields.
            return json.dumps(template)
        # Schema-agnostic default: bare JSON string. NOTE most third-party
        # providers require a JSON OBJECT and reject a bare string; give those
        # an explicit requirements_template. Our own providers accept this.
        return json.dumps(task.task_prompt)

    async def _await_order_created(self, negotiation_id: str) -> str:
        """Poll until the provider accepts AND the order is payable ('created').

        Order lifecycle (CONFIRMED live): an accepted negotiation spawns an
        on-chain order in status "creating" while its create_tx confirms on
        Base, THEN flips to "created" (payable). pay_order() 400s with
        INVALID_STATUS ("order can only be paid when status is created") if
        called during "creating", so we MUST poll past it.

        `Negotiation` has NO order_id field, so we resolve the Order via
        list_orders() by matching negotiation_id. Times out -> fallback (sec.7).
        """
        from croo import ListOptions  # type: ignore

        deadline = asyncio.get_event_loop().time() + self._ACCEPT_TIMEOUT_S
        accepted = False
        while asyncio.get_event_loop().time() < deadline:
            if not accepted:
                neg = await self._client.get_negotiation(negotiation_id)
                status = str(_attr(neg, "status", default="") or "").upper()
                if status == "ACCEPTED":
                    accepted = True
                elif status in {"REJECTED", "EXPIRED", "CANCELLED"}:
                    raise RuntimeError(
                        f"Negotiation {negotiation_id} {status} by provider."
                    )

            if accepted:
                order_id, ostatus = await self._find_order_for_negotiation(
                    negotiation_id, ListOptions
                )
                if order_id and ostatus == "created":
                    return order_id
                if ostatus in {"rejected", "expired", "cancelled"}:
                    raise RuntimeError(
                        f"Order for negotiation {negotiation_id} is "
                        f"'{ostatus}', not payable."
                    )
                # Order missing or still 'creating' -> keep polling.
            await asyncio.sleep(self._POLL_INTERVAL_S)
        raise TimeoutError(
            f"Provider did not accept / order not payable for negotiation "
            f"{negotiation_id} in {self._ACCEPT_TIMEOUT_S}s."
        )

    async def _find_order_for_negotiation(
        self, negotiation_id: str, list_options_cls: type
    ) -> tuple[str | None, str]:
        """Match the on-chain Order to its negotiation via list_orders.

        Returns (order_id, status_lowercase); (None, "") if not indexed yet.

        CONFIRMED live: list_orders REQUIRES role in {"buyer","provider"} and
        400s otherwise. As the purchaser we are the "buyer".
        """
        opts = list_options_cls(
            role="buyer",
            agent_id=self._requester_agent_id or None,
            page=1,
            page_size=50,
        )

        orders = await self._client.list_orders(opts)
        for order in orders or []:
            if str(_attr(order, "negotiation_id", default="")) == str(
                negotiation_id
            ):
                oid = str(_attr(order, "order_id", "id"))
                ostatus = str(_attr(order, "status", default="") or "").lower()
                return oid, ostatus
        return None, ""


def _attr(obj: object, *names: str, default: object = "__RAISE__") -> object:
    """Read the first present attribute (or dict key) from an SDK object.

    The SDK returns typed objects, but exact field names may vary slightly by
    version. This keeps the adapter resilient and confines that uncertainty to
    ONE place (per spec sec.4)."""
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
# FailoverCapClient - live first, automatic mock fallback (demo resilience)
# =============================================================================


class FailoverCapClient(CapClient):
    """Live-first CAP client with automatic mock fallback.

    Every call is attempted against the real LiveCapClient first. If the live
    SDK raises for ANY reason (network down, key revoked, provider offline,
    insufficient balance, SDK drift...), the same call is transparently
    retried against MockCapClient so a demo/run never hard-fails.

    `degraded` reflects whether the MOST RECENT live call failed over - the
    /health endpoint surfaces it so you can see at a glance that the app is
    running on mock data.
    """

    def __init__(self, live: CapClient, mock: CapClient) -> None:
        self._live = live
        self._mock = mock
        self.degraded = False
        # STICKY, per-run fallback tracking. `degraded` alone is unsafe: it is
        # overwritten by EVERY call, so a mock hire_and_pay (fake tx) followed by
        # a successful live get_delivery would reset it to False and the run
        # would be mislabeled `mode=live` with a BaseScan-invalid hash. These
        # flags record whether the CRITICAL settlement step actually failed over
        # so the engine can label the run truthfully. Reset via begin_run().
        self.paid_via_mock = False
        self.any_mock_fallback = False

    def begin_run(self) -> None:
        """Reset per-run fallback state at the start of a run."""
        self.paid_via_mock = False
        self.any_mock_fallback = False

    async def _call(self, method: str, *args, **kwargs):
        try:
            result = await getattr(self._live, method)(*args, **kwargs)
            self.degraded = False
            return result
        except Exception as exc:  # noqa: BLE001 - failover must catch everything
            self.degraded = True
            self.any_mock_fallback = True
            if method == "hire_and_pay":
                # The on-chain settlement itself fell back to mock: the tx_hash
                # returned is a FAKE, BaseScan-invalid hash. The engine MUST NOT
                # label such a run as a real live settlement.
                self.paid_via_mock = True
            _log.warning(
                "live CAP %s failed (%s: %s) - falling back to mock",
                method, type(exc).__name__, exc,
            )
            return await getattr(self._mock, method)(*args, **kwargs)


    async def discover_agents(
        self, category: str | None, limit: int
    ) -> list[AgentInfo]:
        return await self._call("discover_agents", category, limit)

    async def request_quote(
        self, agent: AgentInfo, task: TaskSpec, timeout_s: int
    ) -> Quote | None:
        return await self._call("request_quote", agent, task, timeout_s)

    async def hire_and_pay(
        self, agent: AgentInfo, task: TaskSpec, agreed_price_usdc: Decimal
    ) -> Settlement:
        return await self._call("hire_and_pay", agent, task, agreed_price_usdc)

    async def get_delivery(self, order_id: str) -> Delivery:
        return await self._call("get_delivery", order_id)


# =============================================================================
# Factory - flip the whole app with ONE env var (CROON_CAP_MODE)
# =============================================================================


def build_cap_client(settings: Settings | None = None) -> CapClient:
    """Return the CapClient implied by CROON_CAP_MODE.

    live mode is RESILIENT: the live client is wrapped in FailoverCapClient so
    any live failure (startup OR per-call) automatically falls back to mock.
    """
    settings = settings or get_settings()
    if settings.is_live:
        try:
            live = LiveCapClient(settings)
        except Exception as exc:  # missing key / SDK not installed / etc.
            _log.warning(
                "live CAP unavailable at startup (%s: %s) - running on MOCK",
                type(exc).__name__, exc,
            )
            return MockCapClient()
        return FailoverCapClient(live, MockCapClient())
    return MockCapClient()

