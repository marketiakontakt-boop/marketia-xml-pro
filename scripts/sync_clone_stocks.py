r"""CLI wrapper: BaseLinker stock sync for multi-EAN clones.

Reads BASELINKER_TOKEN + BASELINKER_INVENTORY_ID from .env, delegates to
`app.sync.baselinker_sync.sync_clones()`. For cron / launchd. GUI uses the
same function directly.

Exit codes: 0 success, 1 config missing, 2 API failure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make project root importable when run as `venv/bin/python scripts/sync_clone_stocks.py`
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.sync import BaseLinkerError, sync_all_clones  # noqa: E402


def main() -> int:
    token = os.getenv("BASELINKER_TOKEN", "").strip()
    if not token:
        print("ERROR: set BASELINKER_TOKEN in .env", file=sys.stderr)
        return 1
    inv_raw = os.getenv("BASELINKER_INVENTORY_ID", "").strip()
    inventory_ids = None
    if inv_raw:
        try:
            inventory_ids = [int(x.strip()) for x in inv_raw.split(",") if x.strip()]
        except ValueError:
            print("ERROR: BASELINKER_INVENTORY_ID must be empty or comma-separated integers", file=sys.stderr)
            return 1
    try:
        results = sync_all_clones(token, inventory_ids=inventory_ids, log=print)
    except BaseLinkerError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    for inv_id, name, result in results:
        print(f"[{name} ID={inv_id}] {result.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
