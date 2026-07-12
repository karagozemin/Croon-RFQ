"""CROON base agents - standalone, CAP-callable provider services (spec sec.10).

Two real, hireable providers that accept USDC and settle on-chain:

  1. Listing Copy Agent  - repo URL/description -> Agent Store listing copy.
  2. Base Gas Oracle      - current Base gas + estimated USDC-transfer / CAP-call
                            cost.

They are independent products AND double as CROON RFQ's fallback providers (sec.7).

Design split (same discipline as the rest of the repo):
  - `*_core` functions are PURE LOGIC: deterministic, no network, unit-testable.
  - `provider.py` is the CAP wiring: registers the service, listens for orders,
     runs the core, delivers the result on-chain. ALL SDK uncertainty lives
     there (mirrors the CapClient boundary).
"""

from agents.listing_copy import ListingCopyResult, generate_listing_copy
from agents.gas_oracle import GasEstimate, estimate_base_gas

__all__ = [
    "generate_listing_copy",
    "ListingCopyResult",
    "estimate_base_gas",
    "GasEstimate",
]
