"""Description Generator — sequential Gemini calls + SQLite caching.

Flow:
  1. Load cached descriptions from SQLite (skip already done).
  2. Build prompts for each pending product.
  3. Call Gemini sequentially — progress_callback after each.
  4. Save HTML to product.description + SQLite cache.
  5. Re-run safe: only processes products without cached descriptions.
"""
from __future__ import annotations

from typing import Callable

from app.ai.claude_client import ClaudeClient
from app.ai.prompts import SYSTEM_PROMPT, build_description_prompt
from app.cache.sqlite_cache import (
    get_cached_description,
    open_cache,
    save_description,
)
from app.parser.normalizer import Product
from app.validator.quality_scorer import score_description


def _load_brand_data() -> dict:
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_cached_descriptions(products: list[Product]) -> int:
    """Fill product.description from SQLite cache. Returns cache hit count."""
    hits = 0
    with open_cache() as conn:
        for p in products:
            cached = get_cached_description(conn, p.sku)
            if cached:
                p.description = cached
                p.ai_done = True
                p.quality_score = score_description(cached)
                hits += 1
    return hits


def generate_descriptions(
    products: list[Product],
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Generate descriptions for pending products via Gemini.

    Returns (generated_count, cached_count).
    """
    def log(msg: str):
        if progress_callback:
            progress_callback(msg)

    brand_data = _load_brand_data()

    cached_count = load_cached_descriptions(products)
    pending = [p for p in products if not getattr(p, "ai_done", False)]

    if not pending:
        log(f"Wszystkie {cached_count} opisy z cache — nic do generowania.")
        return 0, cached_count

    log(f"Cache: {cached_count} | Do wygenerowania: {len(pending)}")

    client = ClaudeClient()

    requests = []
    for p in pending:
        brand_key = p.brand or "unknown"
        brand_info = brand_data.get(brand_key, {"name": brand_key.upper(), "tagline": ""})
        user_msg = build_description_prompt(p, brand_info, brand_key)
        requests.append({
            "custom_id": p.sku,
            "system": SYSTEM_PROMPT,
            "content": user_msg,
        })

    sku_map = {p.sku: p for p in pending}
    total = len(requests)
    generated = 0

    def on_progress(done: int, total_: int, sku: str, error: str | None = None):
        nonlocal generated
        if error:
            log(f"[{done}/{total_}] BŁĄD {sku}: {error}")
        else:
            generated += 1
            log(f"[{done}/{total_}] ✓ {sku}")

    log(f"Generuję {total} opisów przez Gemini 2.5 Flash...")
    results = client.generate_all(requests, progress_callback=on_progress)

    with open_cache() as conn:
        for sku, html in results.items():
            if html is None:
                continue
            if sku in sku_map:
                p = sku_map[sku]
                p.description = html
                p.ai_done = True
                p.quality_score = score_description(html)
            save_description(conn, sku, html)

    errors = sum(1 for v in results.values() if v is None)
    log(f"Gotowe: {generated} wygenerowanych | {errors} błędów | {cached_count} z cache")
    return generated, cached_count
