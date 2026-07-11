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

These shims mirror the SDK shapes VERIFIED against croo-sdk==0.2.1
(`.venv/.../croo/types.py`) on 2026-07-12:

    class DeliverableType:            # NOTE: plain class, NOT an Enum
        TEXT = "text"
        SCHEMA = "schema"

    @dataclass
    class DeliverOrderRequest:
        deliverable_type: str
        deliverable_schema: str = ""
        deliverable_text: str = ""

Run `python scripts/verify_shim.py` in an env with `croo-sdk` installed to
re-confirm these shapes; it exits non-zero on any drift.

They are intentionally tiny value objects with NO behaviour and NO network. If
the real SDK's field names ever change, the live import wins and this shim is
never reached — so it can never silently diverge in production. It only affects
offline/mock runs.

This is NOT a CapClient substitute and performs no CAP calls; all real CAP
interaction still goes exclusively through CapClient / the live SDK.
"""

from __future__ import annotations

from dataclasses import dataclass


class DeliverableType:
    """Mirror of croo.types.DeliverableType.

    IMPORTANT: the real SDK defines this as a PLAIN class holding bare string
    constants — NOT an Enum. We match that exactly so that a value emitted
    offline (``DeliverableType.TEXT``) is the literal ``"text"`` string, byte-
    identical to live. A ``str``-Enum member would serialize/repr differently
    and could silently diverge in the delivery payload.
    """

    TEXT = "text"
    SCHEMA = "schema"


@dataclass
class DeliverOrderRequest:
    """Mirror of croo.types.DeliverOrderRequest (delivery payload).

    Field names, order, types and defaults match the real SDK so the offline
    construction is byte-for-byte compatible with the live call site.
    """

    deliverable_type: str
    deliverable_schema: str = ""
    deliverable_text: str = ""

