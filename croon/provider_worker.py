"""Live CAP provider worker for CROON's base agents (spec §10, supply side).

This is the SUPPLY counterpart to CapClient. Where CapClient is the BUYER
(negotiate -> pay -> collect delivery), this worker is the SELLER: it owns one
or more Store services, and for each incoming order it accepts the negotiation,
runs the deterministic core, and delivers the result on-chain.

SDK primitives used (all confirmed against the installed `croo` package —
see croo/agent_client.py and croo/ws.py):
  - AgentClient(config, sdk_key)
  - client.connect_websocket() -> EventStream        (croo/ws.py)
  - stream.on(EventType.X, handler)                   handler: (Event) -> None
  - client.accept_negotiation(negotiation_id)         -> AcceptNegotiationResult
  - client.get_order(order_id)                         -> Order
  - client.deliver_order(order_id, DeliverOrderRequest(deliverable_type=..,
        deliverable_text=..))                          -> DeliverOrderResult
  - EventType.NEGOTIATION_CREATED / ORDER_PAID         (croo/types.py)
  - DeliverableType.TEXT                               (croo/types.py)

Lifecycle we implement:
  NEGOTIATION_CREATED (for a service we own)  -> accept_negotiation
  ORDER_PAID          (buyer has funded)      -> run handler -> deliver_order

We deliberately deliver only AFTER ORDER_PAID so we never do unpaid work.

Uncertainty notes (do NOT block the demo on these; mock mode is unaffected):
  - TODO(verify): the WS event `type` strings and which id fields are populated
    for provider-side events. We read negotiation_id/order_id/service_id off the
    Event dataclass and fall back to the raw payload dict, so minor server-side
    naming differences degrade gracefully instead of crashing.
  - TODO(verify): whether provider negotiations for `require_fund_transfer`
    services must be accepted via accept_negotiation_with_fund_address. The two
    base agents are flat-priced (no fund transfer), so plain accept is correct;
    a fund-transfer branch is stubbed with a clear error.
"""

from __future__ import annotations

import asyncio
import logging

from agents.provider import BASE_AGENTS, AgentSpec
from croon.config import Settings, get_settings

logger = logging.getLogger("croon.provider")


class ProviderWorker:
    """Runs owned base-agent services against the live CAP WebSocket.

    One worker instance serves ALL owned services (mapped by service_id) over a
    single SDK-Key WebSocket connection — the SDK rejects duplicate-key
    connections (ws.py `_POLICY_VIOLATION`), so we must multiplex, not open one
    socket per agent.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # service_id -> AgentSpec (the local core that fulfils that service)
        self._service_specs: dict[str, AgentSpec] = {}
        self._client = None  # croo.AgentClient (live only)
        self._stream = None  # croo.ws.EventStream (live only)
        self._tasks: set[asyncio.Task] = set()
        self._started = False
        self._ready = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._resolve_services()

    # --- Introspection (used by /health) ------------------------------------

    @property
    def enabled(self) -> bool:
        return self._settings.provider_enabled

    @property
    def ready(self) -> bool:
        """True once the WebSocket is connected and handlers are registered."""
        return self._ready

    @property
    def served_services(self) -> dict[str, str]:
        """{service_id: agent_spec_id} actually being served."""
        return {sid: spec.agent_id for sid, spec in self._service_specs.items()}

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "started": self._started,
            "ready": self._ready,
            "served_services": self.served_services,
        }

    # --- Setup --------------------------------------------------------------

    def _resolve_services(self) -> None:
        """Map configured Store service_ids -> local AgentSpec cores."""
        for service_id, spec_id in self._settings.provider_service_map.items():
            spec = BASE_AGENTS.get(spec_id)
            if spec is None:
                logger.warning(
                    "provider: service_map references unknown agent spec '%s' "
                    "(service_id=%s); known specs=%s",
                    spec_id, service_id, list(BASE_AGENTS),
                )
                continue
            self._service_specs[service_id] = spec

    # --- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Connect the WebSocket and register event handlers.

        No-op (logs why) when disabled, in mock mode, or missing a key/map, so
        it is always safe to call from app startup.
        """
        if self._started:
            return
        if not self._settings.provider_enabled:
            logger.info("provider: disabled (CROON_PROVIDER_ENABLED not set)")
            return
        if not self._settings.is_live:
            logger.info("provider: skipped (CROON_CAP_MODE != live)")
            return
        if not self._settings.croo_sdk_key:
            logger.warning("provider: enabled but no CROO SDK key; not starting")
            return
        if not self._service_specs:
            logger.warning(
                "provider: enabled but CROON_PROVIDER_SERVICE_MAP_JSON is empty "
                "or unresolved; nothing to serve"
            )
            return

        from croo import AgentClient, Config  # type: ignore
        from croo.types import EventType  # type: ignore

        self._loop = asyncio.get_running_loop()
        config = Config(
            base_url=self._settings.croo_api_url,
            ws_url=self._settings.croo_ws_url,
            rpc_url=self._settings.base_rpc_url,
        )
        self._client = AgentClient(config, self._settings.croo_sdk_key)
        self._stream = await self._client.connect_websocket()

        # EventStream handlers are SYNC callbacks (ws.py). We bounce each event
        # onto the event loop as a task so we can await SDK calls safely.
        self._stream.on(
            EventType.NEGOTIATION_CREATED,
            lambda ev: self._spawn(self._on_negotiation_created(ev)),
        )
        self._stream.on(
            EventType.ORDER_PAID,
            lambda ev: self._spawn(self._on_order_paid(ev)),
        )

        self._started = True
        self._ready = True
        logger.info(
            "provider: started; serving services=%s",
            list(self._service_specs),
        )

    async def stop(self) -> None:
        self._ready = False
        if self._stream is not None:
            try:
                await self._stream.close()
            except Exception:  # noqa: BLE001
                logger.debug("provider: error closing stream", exc_info=True)
            self._stream = None
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                logger.debug("provider: error closing client", exc_info=True)
            self._client = None
        self._started = False

    # --- Event handling -----------------------------------------------------

    def _spawn(self, coro) -> None:
        """Schedule a coroutine from a sync WS callback, tracking the task.

        ws.py dispatches handlers from the read loop's thread/loop; using the
        loop captured at start() keeps this correct if that ever differs.
        """
        if self._loop is None:
            return
        task = self._loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _field(ev, name: str) -> str:
        """Read an id off the Event dataclass, falling back to its raw payload."""
        val = getattr(ev, name, "") or ""
        if not val and getattr(ev, "raw", None):
            val = ev.raw.get(name, "") or ""
        return val

    async def _on_negotiation_created(self, ev) -> None:
        service_id = self._field(ev, "service_id")
        negotiation_id = self._field(ev, "negotiation_id")
        spec = self._service_specs.get(service_id)
        if spec is None:
            # Not one of our services — ignore quietly (shared WS may see others).
            return
        if not negotiation_id:
            logger.warning("provider: negotiation event missing negotiation_id")
            return
        logger.info(
            "provider: accepting negotiation=%s service=%s (%s)",
            negotiation_id, service_id, spec.name,
        )
        try:
            # Base agents are flat-priced (no fund transfer) -> plain accept.
            # TODO(verify): route require_fund_transfer services through
            # accept_negotiation_with_fund_address once such a service exists.
            await self._client.accept_negotiation(negotiation_id)
        except Exception:  # noqa: BLE001 — keep serving other orders
            logger.exception(
                "provider: accept failed negotiation=%s", negotiation_id
            )

    async def _on_order_paid(self, ev) -> None:
        order_id = self._field(ev, "order_id")
        service_id = self._field(ev, "service_id")
        if not order_id:
            logger.warning("provider: order_paid event missing order_id")
            return

        from croo.types import (  # type: ignore
            DeliverableType,
            DeliverOrderRequest,
        )

        # Resolve which core fulfils this order. Prefer the event's service_id;
        # fall back to the order record when the event omits it.
        spec = self._service_specs.get(service_id) if service_id else None
        params: dict = {}
        prompt = ""
        try:
            order = await self._client.get_order(order_id)
            if spec is None:
                spec = self._service_specs.get(order.service_id)
            # Buyer requirements are carried on the negotiation; the order links
            # back to it. We keep params minimal — cores have safe defaults.
            prompt = getattr(order, "requirements", "") or ""
        except Exception:  # noqa: BLE001
            logger.exception("provider: get_order failed order=%s", order_id)

        if spec is None:
            # Order for a service we don't own (shared WS) — ignore.
            return

        logger.info(
            "provider: fulfilling paid order=%s service=%s (%s)",
            order_id, spec.agent_id, spec.name,
        )
        try:
            output = await spec.handler(prompt, params)
        except Exception:  # noqa: BLE001
            logger.exception("provider: handler failed order=%s", order_id)
            return

        try:
            result = await self._client.deliver_order(
                order_id,
                DeliverOrderRequest(
                    deliverable_type=DeliverableType.TEXT,
                    deliverable_text=output,
                ),
            )
            logger.info(
                "provider: delivered order=%s tx=%s (%d chars)",
                order_id, result.tx_hash, len(output),
            )
        except Exception:  # noqa: BLE001
            logger.exception("provider: deliver failed order=%s", order_id)
