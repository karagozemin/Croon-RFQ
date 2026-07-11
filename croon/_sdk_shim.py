"""Minimal offline stand-ins for the parts of the real CROO SDK (`croo.types`)
that the provider delivery path constructs.

WHY THIS EXISTS
---------------
`croon.provider_worker._on_order_paid` builds a delivery payload using
`DeliverableType.TEXT` and `DeliverOrderRequest(...)` from `croo.types`. In
LIVE mode the real SDK is installed and used verbatim. In MOCK/test mode the
`croo` package is not present, yet we still want the entire paid-order
fulfilment path — handler dispatch, idempotency, and the deliver_order call
shape — to be exercisable offline (see tests/test_provider_worker.py, which
drives it with a FakeClient).

These shims mirror the SDK shapes CONFIRMED in the module docstring of
provider_worker.py:
    DeliverableType.TEXT == "text"
    DeliverOrderRequest(deliverable_type, deliverable_schema="", deliverable_text="")

They are intentionally tiny value objects with NO behaviour and NO network. If
the real SDK's field names ever change, the live import wins and this shim is
never reached — so it can never silently diverge in production. It only affects
offline/mock runs.

This is NOT a CapClient substitute and performs no CAP calls; all real CAP
interaction still goes exclusively through CapClient / the live SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeliverableType(str, Enum):
    """Mirror of croo.types.DeliverableType (only the values we emit)."""

    TEXT = "text"


@dataclass
class DeliverOrderRequest:
    """Mirror of croo.types.DeliverOrderRequest (delivery payload).

    Field names/defaults match the real SDK so the offline construction is
    byte-for-byte compatible with the live call site.
    """

    deliverable_type: DeliverableType
    deliverable_schema: str = ""
    deliverable_text: str = ""
