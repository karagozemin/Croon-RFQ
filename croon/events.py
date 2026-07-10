"""In-memory event bus — streams live run progress to the demo UI.

Deliberately tiny: a per-standing-order ring buffer of events. The UI polls
`GET /standing-orders/{id}/events?after=<seq>` (~1s) to render the mini-RFQ
moment live. No external broker, no WebSocket dependency — just enough to make
the money shot (spec §9) work reliably on demo day.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class EventBus:
    """Thread-safe, sequence-numbered event buffer keyed by standing_order_id."""

    def __init__(self, max_per_order: int = 500) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_per_order))
        self._seq = 0

    def publish(self, order_id: str, event: dict) -> None:
        with self._lock:
            self._seq += 1
            event = {**event, "seq": self._seq, "ts": time.time()}
            self._events[order_id].append(event)

    def get_since(self, order_id: str, after_seq: int = 0) -> list[dict]:
        """Return events for an order with seq > after_seq (chronological)."""
        with self._lock:
            return [e for e in self._events[order_id] if e["seq"] > after_seq]


# Module-level singleton shared by the engine (producer) and API (consumer).
EVENT_BUS = EventBus()
