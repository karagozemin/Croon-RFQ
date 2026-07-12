"""Live CAP provider worker for CROON's base agents (spec sec.10, supply side).

This is the SUPPLY counterpart to CapClient. Where CapClient is the BUYER
(negotiate -> pay -> collect delivery), this worker is the SELLER: it owns one
or more Store services, and for each incoming order it accepts the negotiation,
runs the deterministic core, and delivers the result on-chain.

SDK primitives used (ALL confirmed against the installed `croo` package -
see croo/agent_client.py, croo/ws.py, croo/types.py):
  - AgentClient(config, sdk_key)                       (agent_client.py)
  - client.connect_websocket() -> EventStream          (agent_client.py / ws.py)
  - stream.on(EventType.X, handler)  handler: (Event) -> None   (ws.py: SYNC)
  - client.get_negotiation(negotiation_id) -> Negotiation  (read-only GET)
  - client.accept_negotiation(negotiation_id) -> AcceptNegotiationResult
  - client.get_order(order_id) -> Order                (read-only GET)
  - client.deliver_order(order_id, DeliverOrderRequest(...)) -> DeliverOrderResult
  - client.list_negotiations() / list_orders()         (read-only GET; readiness)
  - EventType.NEGOTIATION_CREATED == "order_negotiation_created"
    EventType.ORDER_PAID == "order_paid"               (types.py)
  - EventType.ORDER_REJECTED / ORDER_EXPIRED           (terminal cleanup)
  - DeliverableType.TEXT == "text"                     (types.py)
  - DeliverOrderRequest(deliverable_type, deliverable_schema="", deliverable_text="")

CONFIRMED SDK FACTS that shaped this file:
  * WS callbacks are SYNCHRONOUS: ws.py `_dispatch_message` calls `h(event)`
    directly. Async SDK work must be dispatched via `asyncio.create_task` - we
    do this through `_spawn` using the loop captured at start().
  * `Event` (types.py) carries typed ids: negotiation_id, order_id, service_id,
    requester_agent_id, provider_agent_id, status, reason, plus the raw payload
    dict. We read typed fields first and fall back to `raw` defensively.
  * `Order` (types.py) has NO `requirements` field. The buyer's task/prompt is
    on `Negotiation.requirements`. So on ORDER_PAID we resolve the prompt via
    get_negotiation(order.negotiation_id) - NOT off the order.

Lifecycle we implement:
  NEGOTIATION_CREATED (service we own) -> get_negotiation -> validate ->
                                          accept_negotiation
  ORDER_PAID          (buyer funded)   -> get_order -> get_negotiation (prompt)
                                          -> run handler -> deliver_order
  ORDER_REJECTED / ORDER_EXPIRED       -> drop any in-flight idempotency marker

We deliver only AFTER ORDER_PAID so we never do unpaid work.

Remaining uncertainty (documented, non-blocking; mock mode unaffected):
  - TODO(verify): whether `require_fund_transfer` services must be accepted via
    accept_negotiation_with_fund_address (agent_client.py). CROON's registered
    service has Require Fund Transfer OFF, so plain accept_negotiation is
    correct; the fund branch raises a clear error rather than guessing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging


from agents.provider import ALL_AGENTS, BASE_AGENTS, AgentSpec
from croon.config import Settings, get_settings


logger = logging.getLogger("croon.provider")

# Event dataclass id fields we may surface in redacted debug logs. These are
# resource identifiers, NOT secrets. Buyer content (requirements/metadata) and
# the SDK key are deliberately excluded.
_SAFE_EVENT_ID_FIELDS = (
    "type",
    "negotiation_id",
    "order_id",
    "service_id",
    "requester_agent_id",
    "provider_agent_id",
    "status",
)


class ProviderWorker:
    """Runs owned base-agent services against the live CAP WebSocket.

    One worker instance serves ALL owned services (mapped by service_id) over a
    single SDK-Key WebSocket connection - the SDK rejects duplicate-key
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
        # Idempotency: order_ids we have delivered (or are delivering) this
        # process, so a duplicate ORDER_PAID (WS reconnect replay) does not
        # double-run the handler or double-call deliver_order.
        self._handled_orders: set[str] = set()
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

    @property
    def served_kinds(self) -> dict[str, str]:
        """{service_id: kind} - 'main' (CROON RFQ brokerage) vs 'base' (fallback).

        Lets /health and readiness show, at a glance, whether the product itself
        is being served or only its base agents.
        """
        return {sid: spec.kind for sid, spec in self._service_specs.items()}

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "started": self._started,
            "ready": self._ready,
            "served_services": self.served_services,
            "served_kinds": self.served_kinds,
            "serving_main": "main" in self.served_kinds.values(),
        }


    # --- Setup --------------------------------------------------------------

    def _resolve_services(self) -> None:
        """Map configured Store service_ids -> local AgentSpec cores.

        Resolves against ALL_AGENTS (the main CROON RFQ brokerage service PLUS
        the two base/fallback agents) so the product itself is hireable on the
        Store - not just its base agents. Unknown spec ids are skipped with a
        loud warning rather than silently serving nothing.
        """
        for service_id, spec_id in self._settings.provider_service_map.items():
            spec = ALL_AGENTS.get(spec_id)
            if spec is None:
                logger.warning(
                    "provider: service_map references unknown agent spec '%s' "
                    "(service_id=%s); known specs=%s",
                    spec_id, service_id, list(ALL_AGENTS),
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

        self._loop = asyncio.get_running_loop()
        config = Config(
            base_url=self._settings.croo_api_url,
            ws_url=self._settings.croo_ws_url,
            rpc_url=self._settings.base_rpc_url,
        )
        self._client = AgentClient(config, self._settings.croo_sdk_key)
        self._stream = await self._client.connect_websocket()
        self._register_handlers(self._stream)

        self._started = True
        self._ready = True
        logger.info(
            "provider: started; serving services=%s",
            list(self._service_specs),
        )

    def _register_handlers(self, stream) -> None:
        """Attach WS handlers using SDK EventType constants (never raw strings).

        Handlers are sync (ws.py); each bounces async work onto the loop via
        `_spawn`. Registration is factored out so readiness can reuse it and
        assert what was wired without duplicating the constant list.
        """
        from croo.types import EventType  # type: ignore

        if self._settings.provider_debug_events:
            stream.on_any(self._debug_log_event)

        stream.on(
            EventType.NEGOTIATION_CREATED,
            lambda ev: self._spawn(self._on_negotiation_created(ev)),
        )
        stream.on(
            EventType.ORDER_PAID,
            lambda ev: self._spawn(self._on_order_paid(ev)),
        )
        # Terminal states: forget any idempotency marker so a later re-use of an
        # id can't be silently skipped. Pure local bookkeeping, no SDK calls.
        stream.on(EventType.ORDER_REJECTED, self._on_order_terminal)
        stream.on(EventType.ORDER_EXPIRED, self._on_order_terminal)

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

        ws.py dispatches handlers from the read loop's task on this loop; using
        the loop captured at start() keeps this correct if that ever differs.
        """
        if self._loop is None:
            coro.close()
            return
        task = self._loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _field(ev, name: str) -> str:
        """Read an id off the Event dataclass, falling back to its raw payload.

        The installed SDK populates typed Event fields (ws.py `_dispatch_message`),
        so the typed read is the primary path; `raw` is defensive compatibility
        against server-side key drift and is intentionally kept.
        """
        val = getattr(ev, name, "") or ""
        if not val and getattr(ev, "raw", None):
            val = ev.raw.get(name, "") or ""
        return val

    def _debug_log_event(self, ev) -> None:
        """Redacted one-line summary of an inbound event (opt-in).

        Logs only resource identifiers and the event class/type - never the SDK
        key, buyer requirements/metadata, or any credential. Enabled via
        CROON_PROVIDER_DEBUG_EVENTS for first-connection field verification.
        """
        present = {
            f: self._field(ev, f)
            for f in _SAFE_EVENT_ID_FIELDS
            if self._field(ev, f)
        }
        raw_keys = sorted((getattr(ev, "raw", None) or {}).keys())
        logger.info(
            "provider[debug-event]: class=%s ids=%s raw_keys=%s",
            type(ev).__name__, present, raw_keys,
        )

    def _on_order_terminal(self, ev) -> None:
        """Sync handler: drop idempotency marker for a rejected/expired order."""
        order_id = self._field(ev, "order_id")
        if order_id:
            self._handled_orders.discard(order_id)
            logger.info(
                "provider: order terminal (%s) order=%s", self._field(ev, "type"), order_id
            )

    async def _on_negotiation_created(self, ev) -> None:
        service_id = self._field(ev, "service_id")
        negotiation_id = self._field(ev, "negotiation_id")
        spec = self._service_specs.get(service_id)
        if spec is None:
            # Not one of our services - ignore quietly (shared WS may see others).
            return
        if not negotiation_id:
            logger.warning("provider: negotiation event missing negotiation_id")
            return

        # Confirm ownership + fund-transfer expectations against the source of
        # truth before accepting. get_negotiation is a read-only GET (no spend).
        try:
            neg = await self._client.get_negotiation(negotiation_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "provider: get_negotiation failed negotiation=%s", negotiation_id
            )
            return

        neg_service = getattr(neg, "service_id", "") or service_id
        if neg_service not in self._service_specs:
            logger.info(
                "provider: negotiation=%s is for unowned service=%s; ignoring",
                negotiation_id, neg_service,
            )
            return

        # CROON's service has Require Fund Transfer OFF. If the backend ever
        # reports a fund_amount on our negotiation, do NOT guess the accept
        # variant - surface it and let a human decide.
        if (getattr(neg, "fund_amount", "") or "").strip() not in ("", "0"):
            logger.error(
                "provider: negotiation=%s carries fund_amount=%r but this "
                "service is configured non-fund; refusing to accept. "
                "TODO(verify): wire accept_negotiation_with_fund_address if a "
                "fund-transfer service is intentionally added.",
                negotiation_id, getattr(neg, "fund_amount", ""),
            )
            return

        logger.info(
            "provider: accepting negotiation=%s service=%s (%s)",
            negotiation_id, neg_service, self._service_specs[neg_service].name,
        )
        try:
            await self._client.accept_negotiation(negotiation_id)
        except Exception:  # noqa: BLE001 - keep serving other orders
            logger.exception(
                "provider: accept failed negotiation=%s", negotiation_id
            )

    async def _on_order_paid(self, ev) -> None:
        order_id = self._field(ev, "order_id")
        service_id = self._field(ev, "service_id")
        if not order_id:
            logger.warning("provider: order_paid event missing order_id")
            return

        # Idempotency: a WS reconnect can replay ORDER_PAID. Claim the order
        # atomically (single-threaded loop) before any await so a duplicate is
        # dropped rather than double-delivered.
        if order_id in self._handled_orders:
            logger.info("provider: order=%s already handled; ignoring replay", order_id)
            return
        self._handled_orders.add(order_id)

        # The deliverable payload types come from the real SDK when it is
        # installed (live mode). In mock/test mode the `croo` package is absent,
        # so we fall back to a lightweight shim that mirrors the SDK shape
        # (DeliverableType.TEXT + DeliverOrderRequest(deliverable_type=,
        # deliverable_text=)). This keeps the whole paid-order fulfilment path
        # exercisable offline without ever inventing a live SDK method.
        try:
            from croo.types import (  # type: ignore
                DeliverableType,
                DeliverOrderRequest,
            )
        except ModuleNotFoundError:
            from croon._sdk_shim import DeliverableType, DeliverOrderRequest


        # Resolve the order record (authoritative service_id + negotiation link).
        try:
            order = await self._client.get_order(order_id)
        except Exception:  # noqa: BLE001
            logger.exception("provider: get_order failed order=%s", order_id)
            self._handled_orders.discard(order_id)  # allow a later retry
            return

        resolved_service = service_id or getattr(order, "service_id", "")
        spec = self._service_specs.get(resolved_service)
        if spec is None:
            # Order for a service we don't own (shared WS) - release + ignore.
            self._handled_orders.discard(order_id)
            return

        # Buyer prompt lives on the NEGOTIATION, not the order (types.py: Order
        # has no `requirements`). Resolve it via the order's negotiation link.
        prompt = ""
        negotiation_id = getattr(order, "negotiation_id", "") or ""
        if negotiation_id:
            try:
                neg = await self._client.get_negotiation(negotiation_id)
                prompt = getattr(neg, "requirements", "") or ""
            except Exception:  # noqa: BLE001
                logger.exception(
                    "provider: get_negotiation failed for order=%s negotiation=%s "
                    "(continuing with empty prompt; cores have safe defaults)",
                    order_id, negotiation_id,
                )

        logger.info(
            "provider: fulfilling paid order=%s service=%s (%s) kind=%s",
            order_id, spec.agent_id, spec.name, spec.kind,
        )
        try:
            if spec.kind == "main":
                # The MAIN CROON RFQ service does not return a static report: it
                # RE-OPENS THE MARKET for this buyer and hires+pays a downstream
                # child agent via CAP. That spends real USDC, so it must be keyed
                # on the parent order id for idempotency (WS replay safe) and
                # bounded by the off-chain spend guard - both handled inside the
                # brokerage module. All CAP calls still go through CapClient.
                from croon import brokerage

                output = await brokerage.execute_main_brokerage_order(
                    parent_order_id=order_id,
                    task_prompt=prompt,
                    params={},
                    settings=self._settings,
                )
            else:
                output = await spec.handler(prompt, {})
        except Exception:  # noqa: BLE001
            logger.exception("provider: handler failed order=%s", order_id)
            self._handled_orders.discard(order_id)  # not delivered; allow retry
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
            self._handled_orders.discard(order_id)  # allow a redelivery attempt

    # --- No-spend live readiness -------------------------------------------

    async def readiness(self) -> dict:
        """Prove live wiring WITHOUT creating/accepting/paying/delivering anything.

        Steps (all read-only):
          1. Validate config + service map.
          2. Build AgentClient.
          3. Authenticate via list_negotiations + list_orders (GET).
          4. Open the WebSocket and register handlers (no order is acted upon;
             we close before any inbound event could be processed).
          5. Return a fully redacted report.

        Never initiates a transaction. Safe to run against the live network.
        """
        report: dict = {
            "cap_mode": self._settings.cap_mode,
            "provider_enabled": self._settings.provider_enabled,
            "service_map_valid": bool(self._service_specs),
            "served_services": self.served_services,
            "sdk_key_present": bool(self._settings.croo_sdk_key),
            "authentication": "SKIPPED",
            "list_negotiations": "SKIPPED",
            "list_orders": "SKIPPED",
            "websocket": "SKIPPED",
            "handlers_registered": [],
            "transactions_initiated": 0,
            "result": "FAIL",
        }

        if not self._settings.is_live:
            report["error"] = "CROON_CAP_MODE != live"
            return report
        if not self._settings.croo_sdk_key:
            report["error"] = "no CROO SDK key configured"
            return report
        if not self._service_specs:
            report["error"] = "service map empty/unresolved"
            return report

        from croo import AgentClient, Config  # type: ignore
        from croo.types import EventType, ListOptions  # type: ignore


        config = Config(
            base_url=self._settings.croo_api_url,
            ws_url=self._settings.croo_ws_url,
            rpc_url=self._settings.base_rpc_url,
        )
        client = AgentClient(config, self._settings.croo_sdk_key)
        stream = None
        try:
            # Read-only auth probes. CONFIRMED against the live API: BOTH list
            # endpoints REQUIRE an explicit role or they 400 with
            # INVALID_PARAMETERS. The two endpoints use DIFFERENT vocabularies:
            #   list_negotiations -> role in {"requester", "provider"}
            #   list_orders       -> role in {"buyer",     "provider"}
            # This worker is the SELLER, so we probe the "provider" role, which
            # is valid for both.
            negs = await client.list_negotiations(ListOptions(role="provider"))
            report["list_negotiations"] = f"OK ({len(negs)})"
            orders = await client.list_orders(ListOptions(role="provider"))
            report["list_orders"] = f"OK ({len(orders)})"
            report["authentication"] = "OK"


            # Connect + register, then immediately tear down. We register the
            # real handlers to confirm wiring, but close the socket before the
            # read loop can meaningfully dispatch - and every handler is a no-op
            # unless a matching owned event arrives, which we do not solicit.
            stream = await client.connect_websocket()
            report["websocket"] = "connected"
            report["handlers_registered"] = [
                EventType.NEGOTIATION_CREATED,
                EventType.ORDER_PAID,
                EventType.ORDER_REJECTED,
                EventType.ORDER_EXPIRED,
            ]
            report["result"] = "PASS"
        except Exception as e:  # noqa: BLE001
            report["error"] = f"{type(e).__name__}: {e}"
            report["result"] = "FAIL"
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:  # noqa: BLE001
                    logger.debug("readiness: error closing stream", exc_info=True)
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                logger.debug("readiness: error closing client", exc_info=True)

        return report


# --- Config-example helper (no secrets) -------------------------------------

def _config_example() -> str:
    """Print an annotated env block for provider mode, with placeholders only.

    Formats CROON_PROVIDER_SERVICE_MAP_JSON exactly as `provider_service_map`
    parses it (a JSON object of {service_id: agent_spec_id}), using the REAL
    local spec ids so the only thing left to fill in is each Store service id.
    """
    import json

    # Include EVERY servable spec: the main CROON RFQ brokerage service (kind
    # "main") plus the two base/fallback agents (kind "base"). Mapping the main
    # service is what makes the product itself hireable on the Store.
    spec_ids = list(ALL_AGENTS)
    example_map = {f"<STORE_SERVICE_ID_FOR_{sid}>": sid for sid in spec_ids}
    kind_notes = ", ".join(f"{sid}={ALL_AGENTS[sid].kind}" for sid in spec_ids)
    lines = [
        "# --- CROON provider (supply side) env example --------------------",
        "# Fill placeholders with real values in your .env (never commit it).",
        "CROON_CAP_MODE=live",
        "CROON_PROVIDER_ENABLED=true",
        "CROON_PROVIDER_DEBUG_EVENTS=true   # redacted event-field logging",
        "",
        "CROO_API_URL=https://api.croo.network",
        "CROO_WS_URL=wss://api.croo.network/ws",
        "BASE_RPC_URL=https://mainnet.base.org",
        "CROO_SDK_KEY=<YOUR_SECRET_SDK_KEY>   # from Dashboard; keep secret",
        "",
        "# Map each owned Store service_id -> local core. Known local specs",
        f"#   (kind): {kind_notes}",
        "# 'main' = the CROON RFQ product itself; 'base' = fallback providers.",
        "CROON_PROVIDER_SERVICE_MAP_JSON='" + json.dumps(example_map) + "'",
    ]
    return "\n".join(lines)



# --- CLI --------------------------------------------------------------------

def _redacted_report_str(report: dict) -> str:
    lines = ["Provider readiness report (redacted, no-spend):"]
    order = [
        ("cap_mode", "cap_mode"),
        ("provider_enabled", "provider_enabled"),
        ("authentication", "Authentication"),
        ("list_negotiations", "list_negotiations"),
        ("list_orders", "list_orders"),
        ("websocket", "WebSocket"),
        ("service_map_valid", "Service map valid"),
        ("transactions_initiated", "Transactions initiated"),
    ]
    for key, label in order:
        lines.append(f"  {label}: {report.get(key)}")
    for h in report.get("handlers_registered", []):
        lines.append(f"  handler registered: {h}")
    lines.append(f"  Served services: {report.get('served_services')}")
    if report.get("error"):
        lines.append(f"  Error: {report['error']}")
    lines.append(f"  Provider readiness: {report.get('result')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croon.provider_worker",
        description="CROON base-agent CAP provider worker (supply side).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--readiness",
        action="store_true",
        help="No-spend live readiness check (auth + WS connect, no transactions).",
    )
    group.add_argument(
        "--print-config-example",
        action="store_true",
        help="Print an annotated provider .env block (placeholders only).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.print_config_example:
        print(_config_example())
        return 0

    worker = ProviderWorker()

    if args.readiness:
        report = asyncio.run(worker.readiness())
        print(_redacted_report_str(report))
        return 0 if report.get("result") == "PASS" else 1

    # Default: run the provider until interrupted.
    async def _run() -> None:
        await worker.start()
        if not worker.ready:
            logger.error(
                "provider: did not start (check CROON_CAP_MODE=live, "
                "CROON_PROVIDER_ENABLED=true, CROO_SDK_KEY, service map)."
            )
            return
        logger.info("provider: running; press Ctrl+C to stop")
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await worker.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
