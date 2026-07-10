"""Local entrypoint: `python run.py` starts the CROON RFQ server + UI.

Reads host/port from .env (CROON_HOST / CROON_PORT). Equivalent to running
uvicorn directly, but gives us a single documented command (spec §12).
"""

from __future__ import annotations

import uvicorn

from croon.config import get_settings


def main() -> None:
    s = get_settings()
    print(f"CROON RFQ starting in '{s.cap_mode}' mode → http://{s.host}:{s.port}")
    uvicorn.run("croon.api:app", host=s.host, port=s.port, reload=False)


if __name__ == "__main__":
    main()
