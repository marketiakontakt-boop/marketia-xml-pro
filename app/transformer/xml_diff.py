"""Incremental XML diff — compares current products against SQLite snapshots.

On first load: all products are NEW.
On subsequent loads: compares hash(ean|name|price) against saved snapshot.
  - NEW: SKU not seen before
  - CHANGED: SKU seen, but snapshot differs
  - UNCHANGED: SKU seen and snapshot matches

Saves current snapshot after comparison so next run has a baseline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.cache.sqlite_cache import open_cache
from app.parser.normalizer import Product

STATUS_NEW = "new"
STATUS_CHANGED = "changed"
STATUS_UNCHANGED = "unchanged"


def _snapshot(p: Product) -> str:
    raw = f"{p.ean}|{p.name}|{p.price:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()


@dataclass
class DiffResult:
    new: int = 0
    changed: int = 0
    unchanged: int = 0


def run_diff(products: list[Product]) -> DiffResult:
    """Tag each product with .diff_status and return summary counts.

    Also persists current snapshots for next run.
    """
    result = DiffResult()

    with open_cache() as conn:
        # Load all existing snapshots
        rows = conn.execute("SELECT sku, snapshot FROM product_snapshots").fetchall()
        stored: dict[str, str] = {r["sku"]: r["snapshot"] for r in rows}

        for p in products:
            snap = _snapshot(p)
            if p.sku not in stored:
                p.diff_status = STATUS_NEW
                result.new += 1
            elif stored[p.sku] != snap:
                p.diff_status = STATUS_CHANGED
                result.changed += 1
            else:
                p.diff_status = STATUS_UNCHANGED
                result.unchanged += 1

        # Persist current state
        conn.executemany(
            "INSERT OR REPLACE INTO product_snapshots (sku, snapshot) VALUES (?,?)",
            [(p.sku, _snapshot(p)) for p in products],
        )
        conn.commit()

    return result
