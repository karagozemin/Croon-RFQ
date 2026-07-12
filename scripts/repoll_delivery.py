"""Re-poll get_delivery() for an already-PAID live order.

The provider's SLA delivery_window can be up to 1800s (30 min); our original
in-run poll was only ~15s, so a 404 right after payment does NOT mean the work
was never delivered. This script re-checks a settled order after time has passed.

Run:
  PYTHONPATH="$PWD" .venv/bin/python scripts/repoll_delivery.py <order_id>
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ["CROON_CAP_MODE"] = "live"

from croon.cap_client import LiveCapClient, _attr  # noqa: E402
from croon.config import get_settings  # noqa: E402


async def main(order_id: str) -> None:
    client = LiveCapClient(get_settings())

    print(f"[REPOLL] order_id={order_id}")
    try:
        delivery = await client.get_delivery(order_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[REPOLL] get_delivery raised: {type(exc).__name__}: {exc}")
        return

    text = _attr(delivery, "deliverable_text", "output", "text", default=None)
    status = _attr(delivery, "status", default=None)
    print(f"[REPOLL] status={status}")
    if text:
        print("[REPOLL] DELIVERED - full-cycle proof (negotiate -> pay -> deliver):")
        print("-" * 60)
        print(text)
        print("-" * 60)
    else:
        print(
            "[REPOLL] Still pending. Payment already settled on-chain; delivery "
            "remains within the provider's SLA window."
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: repoll_delivery.py <order_id>")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
