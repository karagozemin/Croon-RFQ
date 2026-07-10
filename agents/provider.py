"""CAP provider runner for the two base agents (spec §10).

This is the SUPPLY side of CAP: it registers a service on the Agent Store,
listens for incoming orders/negotiations, runs the pure-logic core, and delivers
the result on-chain. ALL SDK uncertainty is confined HERE (mirrors CapClient).

Design:
  - `AgentSpec` describes one hireable service (id, price, category, handler).
  - `run_provider(spec)` is the live loop against the real CROO SDK (provider
    role). STUBBED with TODO(verify) markers until confirmed against the SDK's
    provider examples in build step 6-live.
  - The handler is `async (task_prompt, params) -> str` and returns the
    deliverable text. It reuses the deterministic cores, so a provider NEVER
    fails to deliver (critical for a fallback provider).

Run standalone (no network) to smoke-test a core:
    python -m agents listing-copy --description "on-chain risk auditing agent"
    python -m agents gas-oracle --rpc https://mainnet.base.org
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable

from agents.gas_oracle import estimate_base_gas
from agents.listing_copy import generate_listing_copy

Handler = Callable[[str, dict], Awaitable[str]]


@dataclass
class AgentSpec:
    """A hireable CAP service definition."""

    agent_id: str
    name: str
    category: str
    price_usdc: Decimal
    handler: Handler
    description: str = ""
    eta_seconds: int = 15
    tags: list[str] = field(default_factory=list)


# --- Handlers (bridge order params -> pure cores) ---------------------------


async def _listing_copy_handler(task_prompt: str, params: dict) -> str:
    result = generate_listing_copy(
        repo_url=params.get("repo_url"),
        description=params.get("description") or task_prompt,
    )
    return result.to_text()


async def _gas_oracle_handler(task_prompt: str, params: dict) -> str:
    eth_usd = params.get("eth_usd")
    estimate = await estimate_base_gas(
        rpc_url=params.get("rpc_url"),
        eth_usd=Decimal(str(eth_usd)) if eth_usd is not None else Decimal("3000"),
    )
    return estimate.to_text()


# --- The two base-agent specs (also CROON's fallback providers, §7) ---------

LISTING_COPY_AGENT = AgentSpec(
    agent_id="base_listing_copy",
    name="CROON Listing Copy Agent",
    category="research",
    price_usdc=Decimal("0.05"),
    handler=_listing_copy_handler,
    description=(
        "Turns a repo URL or description into Agent Store listing copy: a "
        "tagline, three selling bullets, and a suggested category."
    ),
    eta_seconds=15,
    tags=["copywriting", "listing", "marketing"],
)

GAS_ORACLE_AGENT = AgentSpec(
    agent_id="base_gas_oracle",
    name="CROON Base Gas Oracle",
    category="infra",
    price_usdc=Decimal("0.01"),
    handler=_gas_oracle_handler,
    description=(
        "Reports current Base gas and the estimated USDC cost of a token "
        "transfer and a typical CAP call."
    ),
    eta_seconds=5,
    tags=["gas", "oracle", "base", "infra"],
)

BASE_AGENTS: dict[str, AgentSpec] = {
    LISTING_COPY_AGENT.agent_id: LISTING_COPY_AGENT,
    GAS_ORACLE_AGENT.agent_id: GAS_ORACLE_AGENT,
}


# --- Live provider loop (STUBBED until confirmed vs SDK provider examples) --


async def run_provider(spec: AgentSpec) -> None:  # pragma: no cover - live only
    """Register `spec` as a CAP service and serve orders until interrupted.

    TODO(verify, build step 6-live): confirm against the CROO SDK provider
    examples (provider.py) the exact calls for:
      - creating/registering a service (name, price, category, description)
      - subscribing to incoming negotiations/orders (WS callback or async iter)
      - accepting a negotiation -> triggers on-chain ORDER_CREATED
      - submitting the deliverable (submit_delivery(order_id, text))
    Do NOT invent method names; wire them once verified.
    """
    from croon.config import get_settings

    settings = get_settings()
    if not settings.croo_sdk_key:
        raise RuntimeError(
            "CROON_CROO_SDK_KEY required to run a live provider. "
            "Use the CLI in offline mode to test the core logic instead."
        )

    from croo import AgentClient, Config  # type: ignore

    config = Config(
        base_url=settings.croo_api_url,
        ws_url=settings.croo_ws_url,
        rpc_url=settings.base_rpc_url,
    )
    client = AgentClient(config, settings.croo_sdk_key)

    # TODO(verify): register_service signature.
    service = await client.register_service(
        {
            "name": spec.name,
            "category": spec.category,
            "price": str(spec.price_usdc),
            "description": spec.description,
            "tags": spec.tags,
        }
    )
    service_id = getattr(service, "service_id", None) or getattr(service, "id", None)
    print(f"[provider] '{spec.name}' registered as service_id={service_id}")

    # TODO(verify): the negotiation/order subscription primitive. This assumes
    # an async-iterator of incoming negotiations; adjust to the SDK's callback
    # model if that's what the provider examples use.
    async for negotiation in client.incoming_negotiations():  # type: ignore
        neg_id = getattr(negotiation, "negotiation_id", None) or getattr(
            negotiation, "id", None
        )
        params = getattr(negotiation, "params", {}) or {}
        prompt = getattr(negotiation, "requirement", "") or ""
        try:
            order = await client.accept_negotiation(neg_id)  # -> ORDER_CREATED
            order_id = getattr(order, "order_id", None) or getattr(order, "id", None)
            output = await spec.handler(prompt, params)
            await client.submit_delivery(order_id, output)
            print(f"[provider] delivered order={order_id} ({len(output)} chars)")
        except Exception as exc:  # noqa: BLE001 — keep serving other orders
            print(f"[provider] order {neg_id} failed: {exc}")


# --- Standalone CLI (offline core smoke test) -------------------------------


async def _cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agents", description="CROON base agents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lc = sub.add_parser("listing-copy", help="run the Listing Copy core")
    p_lc.add_argument("--repo-url", default=None)
    p_lc.add_argument("--description", default="")

    p_go = sub.add_parser("gas-oracle", help="run the Base Gas Oracle core")
    p_go.add_argument("--rpc", dest="rpc_url", default=None)
    p_go.add_argument("--eth-usd", default="3000")

    p_srv = sub.add_parser("serve", help="run a live CAP provider (needs SDK)")
    p_srv.add_argument("agent_id", choices=list(BASE_AGENTS))

    p_json = sub.add_parser("manifest", help="print the base-agent manifest JSON")

    args = parser.parse_args(argv)

    if args.cmd == "listing-copy":
        out = await _listing_copy_handler(
            args.description,
            {"repo_url": args.repo_url, "description": args.description},
        )
        print(out)
        return 0

    if args.cmd == "gas-oracle":
        out = await _gas_oracle_handler(
            "", {"rpc_url": args.rpc_url, "eth_usd": args.eth_usd}
        )
        print(out)
        return 0

    if args.cmd == "manifest":
        print(
            json.dumps(
                [
                    {
                        "agent_id": s.agent_id,
                        "name": s.name,
                        "category": s.category,
                        "price_usdc": str(s.price_usdc),
                        "eta_seconds": s.eta_seconds,
                        "description": s.description,
                        "tags": s.tags,
                    }
                    for s in BASE_AGENTS.values()
                ],
                indent=2,
            )
        )
        return 0

    if args.cmd == "serve":
        await run_provider(BASE_AGENTS[args.agent_id])
        return 0

    return 1
