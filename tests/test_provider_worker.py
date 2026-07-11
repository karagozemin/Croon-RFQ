"""Tests for the live CAP provider worker (croon.provider_worker).

These exercise every network-free path:
  - service_map -> AgentSpec resolution (incl. unknown-spec warning)
  - status()/served_services introspection
  - start() safe no-op guards (disabled / mock mode / no key / empty map)
  - _field() id extraction from both the Event dataclass and its raw payload
  - _on_negotiation_created -> get_negotiation -> accept_negotiation dispatch
    (+ ignore-foreign, + fund-transfer refusal)
  - _on_order_paid -> get_order -> get_negotiation(prompt) -> handler ->
    deliver_order dispatch (+ service resolution + idempotency)
  - _on_order_terminal idempotency-marker cleanup

The real SDK is never imported here: start() bails out before importing `croo`
whenever we don't force it live, and the event handlers are driven directly with
a fake client, so these run fully offline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


import pytest

from agents.provider import ALL_AGENTS, BASE_AGENTS, CROON_RFQ_AGENT

from croon.config import Settings
from croon.provider_worker import ProviderWorker


# --- Fakes ------------------------------------------------------------------


@dataclass
class FakeEvent:
    """Mimics the SDK Event dataclass: typed id fields + a raw payload dict."""

    type: str = ""
    negotiation_id: str = ""
    order_id: str = ""
    service_id: str = ""
    raw: dict | None = None


@dataclass
class FakeNegotiation:
    service_id: str = ""
    requirements: str = ""
    fund_amount: str = ""


@dataclass
class FakeOrder:
    service_id: str = ""
    negotiation_id: str = ""


class FakeDeliverResult:
    def __init__(self, tx_hash: str = "0xdeadbeef") -> None:
        self.tx_hash = tx_hash


class FakeClient:
    """Records the provider-side SDK calls the worker makes."""

    def __init__(
        self,
        order: FakeOrder | None = None,
        negotiation: FakeNegotiation | None = None,
    ) -> None:
        self._order = order or FakeOrder()
        self._negotiation = negotiation or FakeNegotiation()
        self.accepted: list[str] = []
        self.delivered: list[tuple[str, str]] = []

    async def get_negotiation(self, negotiation_id: str):
        # Default the negotiation's service to the order's, so tests that only
        # set the order still validate ownership cleanly.
        if not self._negotiation.service_id and self._order.service_id:
            self._negotiation.service_id = self._order.service_id
        return self._negotiation

    async def accept_negotiation(self, negotiation_id: str):
        self.accepted.append(negotiation_id)
        return object()

    async def get_order(self, order_id: str):
        return self._order

    async def deliver_order(self, order_id: str, request):
        # request is a croo.types.DeliverOrderRequest; read its text field.
        self.delivered.append((order_id, request.deliverable_text))
        return FakeDeliverResult()


def _settings(**overrides) -> Settings:
    # Keep the suite hermetic: it must never read a real key from the
    # developer's .env / environment (which would otherwise let start() reach
    # the live WebSocket path during tests). `croo_sdk_key` uses a
    # validation_alias, so a constructor kwarg is ignored by pydantic-settings
    # (it loads from CROO_SDK_KEY instead); we therefore force it AFTER
    # construction unless a test explicitly opts into a key.
    sdk_key = overrides.pop("croo_sdk_key", None)
    base = dict(
        cap_mode="mock",
        provider_enabled=False,
        provider_service_map_json="{}",
    )
    base.update(overrides)
    settings = Settings(**base)
    object.__setattr__(settings, "croo_sdk_key", sdk_key)
    return settings


# --- Service resolution -----------------------------------------------------


def test_resolves_known_service_map():
    sid = "svc_listing_123"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "base_listing_copy"}}')
    )
    assert w.served_services == {sid: "base_listing_copy"}
    assert w._service_specs[sid] is BASE_AGENTS["base_listing_copy"]


def test_unknown_spec_in_map_is_skipped():
    w = ProviderWorker(
        _settings(provider_service_map_json='{"svc_x": "does_not_exist"}')
    )
    assert w.served_services == {}


def test_status_shape():
    w = ProviderWorker(_settings())
    status = w.status()
    assert status == {
        "enabled": False,
        "started": False,
        "ready": False,
        "served_services": {},
        "served_kinds": {},
        "serving_main": False,
    }


# --- Main CROON RFQ service (the product itself, sold on the Store) ---------


def test_main_service_registered_and_kind():
    # The main brokerage service must be servable and clearly marked "main".
    assert "croon_recurring_rfq" in ALL_AGENTS
    assert CROON_RFQ_AGENT.kind == "main"
    # Base agents remain "base" and are still part of the full registry.
    assert all(BASE_AGENTS[s].kind == "base" for s in BASE_AGENTS)
    assert set(ALL_AGENTS) == {"croon_recurring_rfq", *BASE_AGENTS}


def test_worker_resolves_main_service_and_reports_it():
    sid = "svc_croon_main"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "croon_recurring_rfq"}}')
    )
    assert w.served_services == {sid: "croon_recurring_rfq"}
    status = w.status()
    assert status["served_kinds"] == {sid: "main"}
    assert status["serving_main"] is True


def test_config_example_includes_main_service():
    from croon.provider_worker import _config_example

    text = _config_example()
    assert "croon_recurring_rfq" in text  # product itself is mappable
    assert "main" in text                 # kind is documented for the operator



# --- start() guards (never import the real SDK / open a socket) -------------


def test_start_noop_when_disabled():
    w = ProviderWorker(_settings(provider_enabled=False))
    asyncio.run(w.start())
    assert w.ready is False and w._started is False


def test_start_noop_in_mock_mode_even_if_enabled():
    w = ProviderWorker(
        _settings(
            provider_enabled=True,
            cap_mode="mock",
            provider_service_map_json='{"svc_x": "base_gas_oracle"}',
        )
    )
    asyncio.run(w.start())
    assert w.ready is False


def test_start_noop_when_live_but_no_key():
    w = ProviderWorker(
        _settings(
            provider_enabled=True,
            cap_mode="live",
            provider_service_map_json='{"svc_x": "base_gas_oracle"}',
        )
    )
    # No SDK key set -> must bail before importing croo.
    asyncio.run(w.start())
    assert w.ready is False


def test_start_noop_when_map_empty():
    w = ProviderWorker(
        _settings(provider_enabled=True, cap_mode="live", provider_service_map_json="{}")
    )
    asyncio.run(w.start())
    assert w.ready is False


# --- _field extraction ------------------------------------------------------


def test_field_prefers_typed_attr():
    ev = FakeEvent(order_id="ord_1", raw={"order_id": "ord_raw"})
    assert ProviderWorker._field(ev, "order_id") == "ord_1"


def test_field_falls_back_to_raw():
    ev = FakeEvent(order_id="", raw={"order_id": "ord_raw"})
    assert ProviderWorker._field(ev, "order_id") == "ord_raw"


def test_field_missing_returns_empty():
    ev = FakeEvent(raw=None)
    assert ProviderWorker._field(ev, "order_id") == ""


# --- Negotiation handling ---------------------------------------------------


def test_accept_negotiation_for_owned_service():
    sid = "svc_a"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "base_gas_oracle"}}')
    )
    client = FakeClient(negotiation=FakeNegotiation(service_id=sid))
    w._client = client
    ev = FakeEvent(negotiation_id="neg_1", service_id=sid)
    asyncio.run(w._on_negotiation_created(ev))
    assert client.accepted == ["neg_1"]


def test_ignore_negotiation_for_foreign_service():
    w = ProviderWorker(
        _settings(provider_service_map_json='{"svc_a": "base_gas_oracle"}')
    )
    client = FakeClient()
    w._client = client
    ev = FakeEvent(negotiation_id="neg_1", service_id="svc_not_ours")
    asyncio.run(w._on_negotiation_created(ev))
    assert client.accepted == []


def test_refuse_negotiation_with_unexpected_fund_amount():
    sid = "svc_a"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "base_gas_oracle"}}')
    )
    # Backend reports a fund transfer on a service we configured as non-fund.
    client = FakeClient(
        negotiation=FakeNegotiation(service_id=sid, fund_amount="5.00")
    )
    w._client = client
    ev = FakeEvent(negotiation_id="neg_1", service_id=sid)
    asyncio.run(w._on_negotiation_created(ev))
    assert client.accepted == []  # refused rather than guessing the accept variant


# --- Paid-order fulfilment --------------------------------------------------


def test_order_paid_runs_handler_and_delivers():
    sid = "svc_gas"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "base_gas_oracle"}}')
    )
    client = FakeClient(
        order=FakeOrder(service_id=sid, negotiation_id="neg_9"),
        negotiation=FakeNegotiation(service_id=sid, requirements=""),
    )
    w._client = client
    ev = FakeEvent(order_id="ord_9", service_id=sid)
    asyncio.run(w._on_order_paid(ev))
    assert len(client.delivered) == 1
    delivered_order_id, text = client.delivered[0]
    assert delivered_order_id == "ord_9"
    assert text  # gas oracle core always returns non-empty deliverable text


def test_order_paid_resolves_spec_from_order_when_event_omits_service():
    sid = "svc_gas"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "base_gas_oracle"}}')
    )
    # Event carries no service_id; worker must fall back to get_order().service_id.
    client = FakeClient(order=FakeOrder(service_id=sid, negotiation_id="neg_10"))
    w._client = client
    ev = FakeEvent(order_id="ord_10", service_id="")
    asyncio.run(w._on_order_paid(ev))
    assert len(client.delivered) == 1


def test_order_paid_ignored_for_foreign_service():
    w = ProviderWorker(
        _settings(provider_service_map_json='{"svc_gas": "base_gas_oracle"}')
    )
    client = FakeClient(order=FakeOrder(service_id="svc_not_ours"))
    w._client = client
    ev = FakeEvent(order_id="ord_11", service_id="svc_not_ours")
    asyncio.run(w._on_order_paid(ev))
    assert client.delivered == []


def test_order_paid_missing_order_id_is_noop():
    w = ProviderWorker(
        _settings(provider_service_map_json='{"svc_gas": "base_gas_oracle"}')
    )
    client = FakeClient()
    w._client = client
    ev = FakeEvent(order_id="", service_id="svc_gas")
    asyncio.run(w._on_order_paid(ev))
    assert client.delivered == []


def test_order_paid_is_idempotent_on_replay():
    sid = "svc_gas"
    w = ProviderWorker(
        _settings(provider_service_map_json=f'{{"{sid}": "base_gas_oracle"}}')
    )
    client = FakeClient(
        order=FakeOrder(service_id=sid, negotiation_id="neg_12"),
        negotiation=FakeNegotiation(service_id=sid),
    )
    w._client = client
    ev = FakeEvent(order_id="ord_12", service_id=sid)
    # Two ORDER_PAID for the same order (WS reconnect replay) -> deliver once.
    asyncio.run(w._on_order_paid(ev))
    asyncio.run(w._on_order_paid(ev))
    assert len(client.delivered) == 1


def test_order_terminal_clears_idempotency_marker():
    w = ProviderWorker(
        _settings(provider_service_map_json='{"svc_gas": "base_gas_oracle"}')
    )
    w._handled_orders.add("ord_13")
    w._on_order_terminal(FakeEvent(type="order_expired", order_id="ord_13"))
    assert "ord_13" not in w._handled_orders


# --- Config-example CLI helper (no secrets) ---------------------------------


def test_config_example_contains_real_spec_ids_and_no_real_key():
    from croon.provider_worker import _config_example

    text = _config_example()
    for spec_id in BASE_AGENTS:
        assert spec_id in text
    assert "<YOUR_SECRET_SDK_KEY>" in text  # placeholder, never a real secret


if __name__ == "__main__":  # allow `python tests/test_provider_worker.py`
    raise SystemExit(pytest.main([__file__, "-v"]))
