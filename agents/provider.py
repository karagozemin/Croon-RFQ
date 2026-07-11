"""CAP provider runner for the two base agents (spec §10).

This is the SUPPLY side of CAP: the service is created on the Agent Store, and
this runner listens for incoming negotiations/orders, runs the pure-logic core,
and delivers the result on-chain. ALL SDK uncertainty is confined to
croon.provider_worker.ProviderWorker (mirrors CapClient on the buyer side).


Design:
  - `AgentSpec` describes one hireable service (id, price, category, handler).
  - `run_provider(spec)` drives croon.provider_worker.ProviderWorker, the live
    loop against the real CROO SDK (accept negotiation -> deliver on ORDER_PAID).
    All SDK calls it makes are confirmed against the installed `croo` package.
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


# --- Live provider loop -----------------------------------------------------
#
# The real WebSocket serving logic (accept negotiation -> deliver on ORDER_PAID)
# lives in croon.provider_worker.ProviderWorker, which is wired ONLY to
# SDK methods confirmed against the installed `croo` package (see that module's
# docstring). This CLI entrypoint drives that worker for a single agent so a
# provider can be run standalone. Services are created on the Store/dashboard
# (the SDK has no register primitive), so we serve by service_id.


async def run_provider(
    spec: AgentSpec, service_id: str | None = None
) -> None:  # pragma: no cover - live only
    """Serve `spec` as a live CAP provider until interrupted.

    `service_id` is the Store service id that maps to this local core. If not
    given, it is read from CROON_PROVIDER_SERVICE_MAP_JSON (the first entry that
    points at this spec's agent_id).
    """
    import asyncio

    from croon.config import get_settings
    from croon.provider_worker import ProviderWorker

    settings = get_settings()
    if not settings.croo_sdk_key:
        raise RuntimeError(
            "CROON_CROO_SDK_KEY required to run a live provider. "
            "Use the CLI in offline mode to test the core logic instead."
        )

    # Resolve the service_id for this spec if not supplied explicitly.
    if service_id is None:
        for sid, spec_id in settings.provider_service_map.items():
            if spec_id == spec.agent_id:
                service_id = sid
                break
    if service_id is None:
        raise RuntimeError(
            f"No Store service_id mapped to agent '{spec.agent_id}'. Create the "
            "service on the Store, then set CROON_PROVIDER_SERVICE_MAP_JSON="
            f'{{"<service_id>": "{spec.agent_id}"}} (or pass it on the CLI).'
        )

    # Build a worker scoped to just this one service, forcing it enabled/live.
    worker = ProviderWorker(settings)
    worker._service_specs = {service_id: spec}  # single-service CLI scope
    worker._settings.provider_enabled = True

    print(f"[provider] serving '{spec.name}' as service_id={service_id}")
    await worker.start()
    if not worker.ready:
        raise RuntimeError(
            "provider worker failed to start (check CROON_CAP_MODE=live, SDK key, "
            "and WebSocket URL)"
        )
    try:
        # Keep the process alive; the EventStream runs its own read loop.
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await worker.stop()
        print("[provider] stopped")



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
    p_srv.add_argument(
        "--service-id",
        default=None,
        help="Store service id for this agent (else read from "
        "CROON_PROVIDER_SERVICE_MAP_JSON)",
    )


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
        await run_provider(BASE_AGENTS[args.agent_id], service_id=args.service_id)
        return 0


    return 1
