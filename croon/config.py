"""Configuration loaded from environment / .env file.

All secrets and tunables live here. Nothing is hardcoded elsewhere.
"""

from __future__ import annotations

import json
from functools import lru_cache

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

    # Scoring weights (documented in scoring.py)
    w_price: float = 0.4
    w_rep: float = 0.35
    w_speed: float = 0.25

    # --- LIVE mode only (CROO CAP SDK — confirmed against Python SDK Reference) ---
    # Auth: AgentClient(config, sdk_key). Key format `croo_sk_...`, from Dashboard.
    croo_sdk_key: str | None = None            # -> CROON_CROO_SDK_KEY
    croo_api_url: str = "https://api.croo.network"
    croo_ws_url: str = "wss://api.croo.network/ws"
    base_rpc_url: str = "https://mainnet.base.org"

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



@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
