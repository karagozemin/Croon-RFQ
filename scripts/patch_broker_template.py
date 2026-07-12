"""Add the Broker's verified requirements_template to CROON_LIVE_CANDIDATES_JSON.

Idempotent: only sets {"market_id": "540817"} on the Broker service if missing.
Preserves every other line in .env byte-for-byte.
"""

from __future__ import annotations

import json
import pathlib

ENV = pathlib.Path(".env")
KEY = "CROON_LIVE_CANDIDATES_JSON"
BROKER_SERVICE_ID = "23632a1d-d232-4a4e-b928-da30a73f1dcf"
BROKER_TEMPLATE = {"market_id": "540817"}


def main() -> int:
    lines = ENV.read_text().splitlines()
    out: list[str] = []
    changed = False
    for line in lines:
        if line.startswith(KEY + "="):
            raw = line[len(KEY) + 1:].strip()
            if (raw.startswith("'") and raw.endswith("'")) or (
                raw.startswith('"') and raw.endswith('"')
            ):
                raw = raw[1:-1]
            data = json.loads(raw)
            for c in data:
                if c.get("service_id") == BROKER_SERVICE_ID:
                    if c.get("requirements_template") != BROKER_TEMPLATE:
                        c["requirements_template"] = BROKER_TEMPLATE
                        changed = True
            new = KEY + "=" + "'" + json.dumps(data, separators=(",", ":")) + "'"
            out.append(new)
        else:
            out.append(line)

    ENV.write_text("\n".join(out) + "\n")
    print("changed:", changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
