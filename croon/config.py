"""Configuration loaded from environment / .env file.

All secrets and tunables live here. Nothing is hardcoded elsewhere.
"""

from __future__ import annotations

from decimal import Decimal
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

    # LIVE mode only — confirm exact names against CAP SDK docs (step 4).
    cap_api_key: str | None = None
    agent_wallet_private_key: str | None = None
    base_rpc_url: str | None = None
    usdc_contract_address: str | None = None

    @property
    def is_live(self) -> bool:
        return self.cap_mode.strip().lower() == "live"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
