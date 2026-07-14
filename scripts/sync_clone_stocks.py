r"""CLI wrapper: BaseLinker wholesale → target katalog stock sync.

Reads config z .env, delegates do `app.sync.sync_from_wholesale_to_target()`.
Sync stocki z hurtowni (MultiStore, Kathay, JUMI...) → jeden target katalog
(Allegro Asortyment) który zawiera rodzice + klony `PARENT-N`.

For cron / GitHub Actions. GUI używa tej samej funkcji bezpośrednio.

Wymagane env:
  BASELINKER_TOKEN                    (token BL)
  BASELINKER_SOURCE_INVENTORY_IDS     (hurtownie, comma: "52173,45513")
  BASELINKER_TARGET_INVENTORY_ID      (Allegro Asortyment, int: "36713")

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

from app.sync import BaseLinkerError, sync_from_wholesale_to_target  # noqa: E402


_CONFIG_HINT = (
    "ERROR: Ustaw w .env:\n"
    "  BASELINKER_TOKEN=<twój token>\n"
    "  BASELINKER_SOURCE_INVENTORY_IDS=52173,45513  (hurtownie)\n"
    "  BASELINKER_TARGET_INVENTORY_ID=36713          (Allegro Asortyment)"
)


def main() -> int:
    token = os.getenv("BASELINKER_TOKEN", "").strip()
    src_raw = os.getenv("BASELINKER_SOURCE_INVENTORY_IDS", "").strip()
    tgt_raw = os.getenv("BASELINKER_TARGET_INVENTORY_ID", "").strip()

    if not token or not src_raw or not tgt_raw:
        print(_CONFIG_HINT, file=sys.stderr)
        return 1

    try:
        source_ids = [int(x.strip()) for x in src_raw.split(",") if x.strip()]
    except ValueError:
        print("ERROR: BASELINKER_SOURCE_INVENTORY_IDS musi być comma-sep int-y (np. 52173,45513)", file=sys.stderr)
        return 1
    if not source_ids:
        print(_CONFIG_HINT, file=sys.stderr)
        return 1

    try:
        target_id = int(tgt_raw)
    except ValueError:
        print("ERROR: BASELINKER_TARGET_INVENTORY_ID musi być liczbą (np. 36713)", file=sys.stderr)
        return 1

    try:
        result = sync_from_wholesale_to_target(
            token=token,
            source_inventory_ids=source_ids,
            target_inventory_id=target_id,
            log=print,
        )
    except BaseLinkerError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    print(f"\n[target ID={target_id}] {result.summary()}")
    print(f"  matched={result.parents_resolved}, synced={result.clones_synced}, total={result.total_products}")
    for w in result.warnings:
        print(f"  WARN: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
