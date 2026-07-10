"""Base Gas Oracle Agent — core logic (spec §10.2).

Output: current Base L2 gas price + estimated cost (in ETH and USDC) of a plain
        USDC transfer and a typical CAP call.

Two modes:
  - LIVE:  query a Base RPC via eth_gasPrice (httpx). Requires a reachable RPC.
  - OFFLINE/DETERMINISTIC: if no RPC or the call fails, fall back to a sane
    static gas price so the provider ALWAYS delivers (a fallback provider that
    can itself fail is worthless). Clearly flagged in the output.

Gas-unit assumptions (documented, adjustable):
  - ERC20 USDC transfer  ≈ 55,000 gas
  - Typical CAP call/settle ≈ 120,000 gas (approve + transfer + protocol logic)

Price conversion uses a configurable ETH/USD reference. On Base, USDC ≈ USD, so
USDC cost ≈ USD cost. This is an ESTIMATE, labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_UP

# Gas-unit assumptions (see module docstring).
GAS_USDC_TRANSFER = 55_000
GAS_CAP_CALL = 120_000

# Deterministic fallback gas price when no RPC is available (Base is cheap; a
# few hundredths of a gwei is typical). Used only in offline mode.
_FALLBACK_GAS_PRICE_WEI = 30_000_000  # 0.03 gwei

_WEI_PER_ETH = Decimal(10) ** 18
_WEI_PER_GWEI = Decimal(10) ** 9


@dataclass
class GasEstimate:
    """A Base gas snapshot + derived cost estimates."""

    gas_price_wei: int
    gas_price_gwei: Decimal
    eth_usd: Decimal
    usdc_transfer_cost_usdc: Decimal
    cap_call_cost_usdc: Decimal
    source: str  # "rpc" | "fallback"
    warnings: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """Human-readable deliverable (what the buyer receives)."""
        lines = [
            "BASE GAS ORACLE",
            f"  source:            {self.source}",
            f"  gas price:         {self.gas_price_gwei} gwei",
            f"  ETH/USD ref:       ${self.eth_usd}",
            f"  USDC transfer:     ~{self.usdc_transfer_cost_usdc} USDC "
            f"({GAS_USDC_TRANSFER:,} gas)",
            f"  CAP call/settle:   ~{self.cap_call_cost_usdc} USDC "
            f"({GAS_CAP_CALL:,} gas)",
        ]
        if self.warnings:
            lines.append("  warnings:")
            lines += [f"    - {w}" for w in self.warnings]
        return "\n".join(lines)


def _cost_usdc(gas_units: int, gas_price_wei: int, eth_usd: Decimal) -> Decimal:
    """Cost of `gas_units` at `gas_price_wei`, converted to USDC (≈ USD)."""
    eth_cost = (Decimal(gas_units) * Decimal(gas_price_wei)) / _WEI_PER_ETH
    usd = eth_cost * eth_usd
    # Round up to 6 dp (USDC precision) so we never UNDER-quote gas.
    return usd.quantize(Decimal("0.000001"), rounding=ROUND_UP)


def estimate_from_gas_price(
    gas_price_wei: int,
    *,
    eth_usd: Decimal,
    source: str = "rpc",
    warnings: list[str] | None = None,
) -> GasEstimate:
    """Pure conversion from a gas price to a full estimate (unit-testable)."""
    return GasEstimate(
        gas_price_wei=gas_price_wei,
        gas_price_gwei=(Decimal(gas_price_wei) / _WEI_PER_GWEI).quantize(
            Decimal("0.0001")
        ),
        eth_usd=eth_usd,
        usdc_transfer_cost_usdc=_cost_usdc(
            GAS_USDC_TRANSFER, gas_price_wei, eth_usd
        ),
        cap_call_cost_usdc=_cost_usdc(GAS_CAP_CALL, gas_price_wei, eth_usd),
        source=source,
        warnings=warnings or [],
    )


async def estimate_base_gas(
    *,
    rpc_url: str | None = None,
    eth_usd: Decimal = Decimal("3000"),
    timeout_s: float = 5.0,
) -> GasEstimate:
    """Return a Base gas estimate.

    Tries the RPC first (eth_gasPrice); on ANY failure falls back to a
    deterministic estimate so the provider always delivers.
    """
    warnings: list[str] = []

    if rpc_url:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_gasPrice",
                        "params": [],
                    },
                )
                resp.raise_for_status()
                hex_price = resp.json()["result"]
                gas_price_wei = int(hex_price, 16)
                return estimate_from_gas_price(
                    gas_price_wei, eth_usd=eth_usd, source="rpc"
                )
        except Exception as exc:  # noqa: BLE001 — always deliver
            warnings.append(
                f"RPC gas lookup failed ({exc.__class__.__name__}); "
                "using deterministic fallback price."
            )
    else:
        warnings.append("No RPC configured; using deterministic fallback price.")

    return estimate_from_gas_price(
        _FALLBACK_GAS_PRICE_WEI,
        eth_usd=eth_usd,
        source="fallback",
        warnings=warnings,
    )
