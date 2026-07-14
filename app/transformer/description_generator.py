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
) -> tuple[int, int, float]:
    """Generate descriptions for pending products via Gemini.

    Returns (generated_count, cached_count, cost_usd).
    """
    def log(msg: str):
        if progress_callback:
            progress_callback(msg)

    brand_data = _load_brand_data()

    cached_count = load_cached_descriptions(products)
    pending = [p for p in products if not getattr(p, "ai_done", False)]

    if not pending:
        log(f"Wszystkie {cached_count} opisy z cache — nic do generowania.")
        return 0, cached_count, 0.0

    log(f"Cache: {cached_count} | Do wygenerowania: {len(pending)}")

    # Safety: force brand detection dla produktów z brand=None/unknown.
    # Bug 2026-07-13c: user wgrał XML, kliknął "Generuj opisy AI" zanim Transformy skończyły
    # przypisać brand → 59/59 opisów wygenerowane z "UNKNOWN" zamiast "INTEX". Fix: sanity
    # check przed wygenerowaniem promptu — jeśli brand nie ustawiony, wołaj BrandMapper
    # natychmiast (deterministic, szybkie).
    from app.transformer.brand_mapper import BrandMapper
    need_brand = [p for p in pending if not p.brand or p.brand == "unknown"]
    if need_brand:
        bm = BrandMapper()
        bm.map_products(need_brand)
        fixed = sum(1 for p in need_brand if p.brand and p.brand != "unknown")
        log(f"⚠️ Auto-fix brand dla {fixed}/{len(need_brand)} produktów (brand był pusty/unknown)")

    client = ClaudeClient()

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

    def on_progress(done: int, total_: int, sku: str, error: str | None = None):
        nonlocal generated
        cost_str = f" | ${client.cost_usd:.4f}" if client.cost_usd > 0 else ""
        if error:
            log(f"[{done}/{total_}] BŁĄD {sku}: {error}")
        else:
            generated += 1
            log(f"[{done}/{total_}] ✓ {sku}{cost_str}")

    # Streaming save — natychmiast po każdym success żeby crash nie tracił postępu.
    # Fix 2026-07-04: user zgłosił "generowało wieczność, po zamknięciu nic nie ma".
    # Root cause: save był PO całym batch, więc kill/crash w środku = utrata.
    # + retry w claude_client dla transient errors (18/500 → ~500/500).
    MIN_DESC_LEN = 500
    empty_responses = [0]  # box do mutacji z closure

    def _save_one(sku: str, raw: str):
        """Streaming save callback — wołany przez generate_all natychmiast po każdym success."""
        if raw is None:
            return
        p = sku_map.get(sku)
        data = _extract_json(raw)
        if data and (data.get("section_1") or data.get("sections")):
            brand_key = (p.brand if p else None) or "unknown"
            brand_info = brand_data.get(brand_key, {"name": brand_key.upper()})
            brand_display = brand_info.get("name", "").upper()
            spec = _spec_items(p, brand_display) if p else []
            html = assemble_html_from_json(data, list(p.images) if p else [], spec)
        else:
            html = raw or ""

        if len(html) < MIN_DESC_LEN:
            empty_responses[0] += 1
            log(f"⚠️ {sku}: pusta/za krótka odpowiedź AI ({len(html)} zn.) — pomijam, nie zapisuję do cache")
            return

        score = score_description(html)
        if p:
            p.description = html
            p.ai_done = True
            p.quality_score = score
        # Każdy save w osobnej krótkiej conn — autocommit gwarantuje persist.
        # Alternatywa (shared conn) była też OK ale wymagała żywej conn przez cały batch.
        with open_cache() as conn:
            save_description(conn, sku, html, quality_score=score)

    log(f"Generuję {total} opisów przez Gemini 2.5 Flash...")
    results = client.generate_all(
        requests,
        progress_callback=on_progress,
        cancel_check=cancel_check,
        on_result=_save_one,
    )

    errors = sum(1 for v in results.values() if v is None) + empty_responses[0]
    cost_usd = client.cost_usd
    cost_str = f" | Koszt: ${cost_usd:.4f} ({client.usage_summary()})" if cost_usd > 0 else ""
    log(f"Gotowe: {generated} wygenerowanych | {errors} błędów | {cached_count} z cache{cost_str}")
    return generated, cached_count, cost_usd


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
