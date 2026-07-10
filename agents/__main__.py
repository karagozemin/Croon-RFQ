"""`python -m agents ...` entrypoint (see agents/provider.py for commands)."""

from __future__ import annotations

import asyncio
import sys

from agents.provider import _cli


def main() -> None:
    raise SystemExit(asyncio.run(_cli(sys.argv[1:])))


if __name__ == "__main__":
    main()
