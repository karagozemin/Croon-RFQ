"""Configuration loaded from environment / .env file.

All secrets and tunables live here. Nothing is hardcoded elsewhere.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache

from pydantic import AliasChoices, Field

from pydantic_settings import BaseSettings, SettingsConfigDict




class Settings(BaseSettings):
    """App settings. Prefix `CROON_` maps env vars to fields."""

    model_config = SettingsConfigDict(
        env_prefix="CROON_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Demo-day safety net: "mock" | "live"
    cap_mode: str = "mock"

    # Persistence
    database_url: str = "sqlite:///croon.db"

    # HTTP server
    host: str = "127.0.0.1"
    port: int = 8000

    # Scheduler (demo-grade in-process loop; NOT production cron)
    scheduler_tick_seconds: int = 10

    # Mini-RFQ engine
    rfq_timeout_seconds: int = 10

    # Spend guard for the MAIN brokerage service (spec: CROON RFQ sold on the
    # Store). When a buyer hires CROON RFQ itself, CROON re-opens the market and
    # may HIRE+PAY a downstream (child) agent to fulfil the work. This caps the
    # USDC CROON will spend on that one child settlement, so a single paid order
    # can never drain the agent wallet regardless of the buyer-supplied budget.
    # -> CROON_MAX_CHILD_SPEND_USDC
    max_child_spend_usdc: Decimal = Decimal("0.50")


    # Scoring weights (documented in scoring.py)
    w_price: float = 0.4
    w_rep: float = 0.35
    w_speed: float = 0.25

    # --- LIVE mode only (CROO CAP SDK — confirmed against Python SDK Reference) ---
    # Auth: AgentClient(config, sdk_key). Key format `croo_sk_...`, from Dashboard.
    # Each field accepts BOTH our prefixed name (CROON_*) AND the SDK's own
    # native env name (e.g. CROO_SDK_KEY, BASE_RPC_URL) so a wallet/SDK already
    # configured for CROO works without duplicating vars.
    croo_sdk_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CROON_CROO_SDK_KEY", "CROO_SDK_KEY"),
    )
    # Our OWN requester agent id, stamped onto negotiations (optional per SDK).
    croo_requester_agent_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CROON_CROO_REQUESTER_AGENT_ID", "CROO_REQUESTER_AGENT_ID"
        ),
    )
    croo_api_url: str = Field(
        default="https://api.croo.network",
        validation_alias=AliasChoices("CROON_CROO_API_URL", "CROO_API_URL"),
    )
    croo_ws_url: str = Field(
        default="wss://api.croo.network/ws",
        validation_alias=AliasChoices("CROON_CROO_WS_URL", "CROO_WS_URL"),
    )
    base_rpc_url: str = Field(
        default="https://mainnet.base.org",
        validation_alias=AliasChoices("CROON_BASE_RPC_URL", "BASE_RPC_URL"),
    )


    # Canonical native USDC on Base (NOT bridged USDbC). Confirmed via Coinbase
    # CDP docs. TODO(verify): confirm CAP settles in THIS USDC, not USDbC
    # (0xd9aA...) before the first live payment.
    usdc_contract_address: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    # SDK has NO discovery primitive (account/service setup lives in the Store).
    # So live candidates are a configured roster of Store service IDs. Quotes are
    # DERIVED from each listed price/SLA (spec §4). JSON list of objects:
    #   [{"agent_id","name","service_id","category",
    #     "listed_price_usdc","listed_eta_seconds","reputation"}, ...]
    live_candidates_json: str = "[]"           # -> CROON_LIVE_CANDIDATES_JSON

    # OUR base agent used as the fallback provider (§7). Its Store service id.
    fallback_service_id: str | None = None     # -> CROON_FALLBACK_SERVICE_ID
    fallback_agent_id: str | None = None
    fallback_agent_name: str = "CROON Fallback Provider"

    # --- Base-agent PROVIDER worker (spec §10; supply side) ------------------
    # When enabled (and in live mode with a valid SDK key), CROON runs the two
    # base agents as CAP providers: it opens the SDK WebSocket, accepts
    # negotiations for its owned services, and delivers on ORDER_PAID.
    #
    # Services are created on the Store/dashboard (the SDK has NO register
    # primitive), so the worker only needs a map from each owned Store
    # service_id -> which local AgentSpec runs it. JSON object:
    #   {"<service_id>": "base_listing_copy", "<service_id>": "base_gas_oracle"}
    provider_enabled: bool = False                 # -> CROON_PROVIDER_ENABLED
    provider_service_map_json: str = "{}"          # -> CROON_PROVIDER_SERVICE_MAP_JSON

    # First-connection diagnostics. When true the worker logs a redacted,
    # non-secret summary of every inbound WS event (class name, type, present id
    # fields). NEVER logs SDK keys, buyer payload secrets, or credentials.
    provider_debug_events: bool = False            # -> CROON_PROVIDER_DEBUG_EVENTS



    @property
    def is_live(self) -> bool:
        return self.cap_mode.strip().lower() == "live"

    @property
    def live_candidates(self) -> list[dict]:
        """Parsed live candidate roster (see live_candidates_json)."""
        try:
            data = json.loads(self.live_candidates_json or "[]")
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    @property
    def provider_service_map(self) -> dict[str, str]:
        """Parsed {service_id: agent_spec_id} map (see provider_service_map_json)."""
        try:
            data = json.loads(self.provider_service_map_json or "{}")
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}




@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
