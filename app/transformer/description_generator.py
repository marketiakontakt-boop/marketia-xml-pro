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
from app.ai.prompts import (
    SYSTEM_PROMPT_JSON,
    _extract_json,
    _spec_items,
    assemble_html_from_json,
    build_description_prompt_v2,
)
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
    cancel_check: "Callable[[], bool] | None" = None,
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

    def on_key_cooling(key_idx: int, seconds: float):
        log(f"⏳ Klucz #{key_idx + 1} wchodzi w cooldown {int(seconds)}s — czekam na wolny klucz…")

    client = ClaudeClient(on_key_cooling=on_key_cooling)

    requests = []
    for p in pending:
        brand_key = p.brand or "unknown"
        brand_info = brand_data.get(brand_key, {"name": brand_key.upper(), "tagline": ""})
        user_msg = build_description_prompt_v2(p, brand_info, brand_key)
        requests.append({
            "custom_id": p.sku,
            "system": SYSTEM_PROMPT_JSON,
            "content": user_msg,
            "json_mode": True,
        })

    sku_map = {p.sku: p for p in pending}
    total = len(requests)
    generated = 0

    def on_progress(done: int, total_: int, sku: str, error: str | None = None, cooling_status: str = ""):
        nonlocal generated
        suffix = f" | {cooling_status}" if cooling_status else ""
        if error:
            log(f"[{done}/{total_}] BŁĄD {sku}: {error}{suffix}")
        else:
            generated += 1
            log(f"[{done}/{total_}] ✓ {sku}{suffix}")

    log(f"Generuję {total} opisów przez Gemini 2.5 Flash...")
    results = client.generate_all(requests, progress_callback=on_progress)

    with open_cache() as conn:
        for sku, raw in results.items():
            if cancel_check and cancel_check():
                break
            if raw is None:
                continue
            p = sku_map.get(sku)
            # v2: parse JSON → assemble HTML
            data = _extract_json(raw)
            if data and (data.get("section_1") or data.get("sections")):
                brand_key = (p.brand if p else None) or "unknown"
                brand_info = brand_data.get(brand_key, {"name": brand_key.upper()})
                brand_display = brand_info.get("name", "").upper()
                spec = _spec_items(p, brand_display) if p else []
                html = assemble_html_from_json(data, list(p.images) if p else [], spec)
            else:
                # Fallback: treat raw as HTML (e.g. if json_mode not supported)
                html = raw
            score = score_description(html)
            if p:
                p.description = html
                p.ai_done = True
                p.quality_score = score
            save_description(conn, sku, html, quality_score=score)

    errors = sum(1 for v in results.values() if v is None)
    log(f"Gotowe: {generated} wygenerowanych | {errors} błędów | {cached_count} z cache")
    return generated, cached_count


def generate_single_description(product: Product) -> str:
    """Generate description for one product synchronously (for popup regeneration).

    Updates product in-place and saves to cache. Returns HTML.
    """
    brand_data = _load_brand_data()
    brand_key = product.brand or "unknown"
    brand_info = brand_data.get(brand_key, {"name": brand_key.upper(), "tagline": ""})
    user_msg = build_description_prompt_v2(product, brand_info, brand_key)

    client = ClaudeClient()
    raw = client.call(SYSTEM_PROMPT_JSON, user_msg, json_mode=True)

    data = _extract_json(raw)
    if data and (data.get("section_1") or data.get("sections")):
        brand_display = brand_info.get("name", "").upper()
        spec = _spec_items(product, brand_display)
        html = assemble_html_from_json(data, list(product.images or []), spec)
    else:
        html = raw  # fallback if JSON parse fails

    score = score_description(html)
    with open_cache() as conn:
        save_description(conn, product.sku, html, quality_score=score)

    product.description = html
    product.ai_done = True
    product.quality_score = score
    return html
