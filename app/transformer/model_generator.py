"""Assign a brand-specific model name to each product, dedup-safe across runs.

- Pool of names per brand from data/model_names.json (≈12 each).
- SQLite `used_model_names` enforces uniqueness — same SKU re-runs return the same model.
- When a pool is exhausted, append `-2`, `-3`, … (still unique per brand).
"""
from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path

from app.cache.sqlite_cache import reserve_model_name, used_models_for_brand
from app.parser.normalizer import Product

DEFAULT_POOL_PATH = Path(__file__).resolve().parents[2] / "data" / "model_names.json"


class ModelNameGenerator:
    def __init__(self, conn: sqlite3.Connection, pool_path: Path | str | None = None):
        path = Path(pool_path) if pool_path else DEFAULT_POOL_PATH
        with path.open(encoding="utf-8") as f:
            self.pools: dict[str, list[str]] = json.load(f)
        self.conn = conn

    def _existing_for_sku(self, sku: str) -> str | None:
        row = self.conn.execute(
            "SELECT model_name FROM used_model_names WHERE used_for_sku = ?",
            (sku,),
        ).fetchone()
        return row["model_name"] if row else None

    def assign(self, product: Product) -> str:
        """Return (and set) the model name for this product. Idempotent per SKU."""
        # 1. SKU already has an assignment — reuse it.
        prior = self._existing_for_sku(product.sku)
        if prior:
            product.model_name = prior
            return prior

        # 2. Brand without a pool → empty model (UI can let user pick later).
        pool = self.pools.get(product.brand)
        if not pool:
            product.model_name = ""
            return ""

        # 3. Pick first unused from pool — deterministic SKU-seeded starting index
        used = used_models_for_brand(self.conn, product.brand)
        start = zlib.crc32(product.sku.encode("utf-8")) % len(pool)
        for offset in range(len(pool)):
            candidate = pool[(start + offset) % len(pool)]
            if candidate in used:
                continue
            if reserve_model_name(self.conn, product.brand, candidate, product.sku):
                product.model_name = candidate
                return candidate
            # Race / concurrent insert: refresh `used` and keep trying
            used.add(candidate)

        # 4. Pool fully exhausted — append numeric suffix to a deterministic anchor
        anchor = pool[start]
        suffix = 2
        while True:
            candidate = f"{anchor}-{suffix}"
            if reserve_model_name(self.conn, product.brand, candidate, product.sku):
                product.model_name = candidate
                return candidate
            suffix += 1

    def assign_all(self, products: list[Product]) -> None:
        for p in products:
            self.assign(p)
