"""AI title generator — Gemini-powered SEO Allegro titles.

Opt-in alternative to the deterministic simple-mode TitleTransformer.
Uses gemini-2.5-flash via the existing ClaudeClient (multi-key + paid priority).
Caches outputs in `ai_titles` table; subsequent runs hit cache unless force=True.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from app.ai.claude_client import ClaudeClient
from app.ai.prompts import TITLE_PROMPT_VERSION, TITLE_SEO_PROMPT_V1, build_title_system_prompt
from app.cache.sqlite_cache import get_ai_title, save_ai_title
from app.parser.normalizer import Product

MAX_TITLE_LEN = 75
MIN_TRIM_FLOOR = 40
DEFAULT_KEYWORDS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"
)

_FENCE_RE = re.compile(r"```(?:\w+)?")
_QUOTE_RE = re.compile(r"[\"“”„«»‘’']")
_WS_RE = re.compile(r"\s+")
_TRAIL = " -–—,.;:|+/\\"


def _clean_response(text: str) -> str:
    t = (text or "").strip()
    t = _FENCE_RE.sub("", t).replace("```", "")
    t = _QUOTE_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip(_TRAIL).upper()
    if len(t) > MAX_TITLE_LEN:
        cut = t[:MAX_TITLE_LEN]
        last = cut.rfind(" ")
        if last >= MIN_TRIM_FLOOR:
            cut = cut[:last]
        t = cut.rstrip(_TRAIL)
    return t


def _product_payload(p: Product, brand_display: str) -> str:
    return json.dumps(
        {
            "name": p.name or "",
            "brand_display": brand_display,
            "model_name": p.model_name or "",
            "category_name": getattr(p, "category_name", "") or "",
            "manufacturer_name": getattr(p, "manufacturer_name", "") or "",
            "attributes": dict(getattr(p, "attributes", {}) or {}),
        },
        ensure_ascii=False,
    )


def load_cached_ai_titles(products: list[Product]) -> int:
    """Load AI titles v4 from SQLite cache into `p.title`. Returns hit count.

    Analogous to `load_cached_descriptions` — called after `TitleTransformer` in the
    transform pipeline so that products with cached AI titles show the AI version
    instead of the deterministic one after session restart (user request 2026-07-12e).
    """
    from app.cache.sqlite_cache import open_cache
    hits = 0
    with open_cache() as conn:
        for p in products:
            ai = get_ai_title(conn, p.sku, prompt_version=TITLE_PROMPT_VERSION)
            if ai:
                p.title = ai
                hits += 1
    return hits


class AITitleGenerator:
    def __init__(
        self,
        conn: sqlite3.Connection,
        brand_data: dict[str, dict] | None = None,
        client: ClaudeClient | None = None,
    ):
        self.conn = conn
        self.client = client or ClaudeClient()
        if brand_data is None:
            with DEFAULT_KEYWORDS_PATH.open(encoding="utf-8") as f:
                brand_data = json.load(f)
        self.brand_display = {
            k: v.get("name", k.upper()) for k, v in brand_data.items()
        }

    def _brand_for(self, p: Product) -> str:
        return self.brand_display.get(p.brand or "", "")

    def generate_one(self, p: Product, force: bool = False, custom_instruction: str = "") -> str:
        # Custom instruction = jednorazowy batch → force regen, NIE zapisuj do cache.
        skip_cache = bool(custom_instruction.strip())
        if not force and not skip_cache:
            cached = get_ai_title(self.conn, p.sku, prompt_version=TITLE_PROMPT_VERSION)
            if cached:
                return cached
        payload = _product_payload(p, self._brand_for(p))
        system_prompt = build_title_system_prompt(custom_instruction)
        raw = self.client.call(system=system_prompt, content=payload)
        title = _clean_response(raw)
        if not title:
            return p.name or ""
        if not skip_cache:
            save_ai_title(self.conn, p.sku, title, TITLE_PROMPT_VERSION)
        return title

    def generate_all(
        self,
        products: list[Product],
        force: bool = False,
        progress_cb=None,
        cancel_check=None,
        custom_instruction: str = "",
    ) -> dict[str, str]:
        """Batch generate. Returns {sku: ai_title}.

        `custom_instruction` (opcjonalne): dodatkowa instrukcja user'a do promptu.
        Gdy podana: skip cache read, skip cache write, force regen wszystkiego.
        """
        skip_cache = bool(custom_instruction.strip())
        results: dict[str, str] = {}
        pending: list[Product] = []
        for p in products:
            if not force and not skip_cache:
                cached = get_ai_title(self.conn, p.sku, prompt_version=TITLE_PROMPT_VERSION)
                if cached:
                    results[p.sku] = cached
                    continue
            pending.append(p)

        if not pending:
            return results

        system_prompt = build_title_system_prompt(custom_instruction)
        requests = [
            {
                "custom_id": p.sku,
                "system": system_prompt,
                "content": _product_payload(p, self._brand_for(p)),
            }
            for p in pending
        ]

        def _adapter(done, total, custom_id, error=None):
            if progress_cb:
                progress_cb(done, total, custom_id, error=error)

        raw_results = self.client.generate_all(
            requests,
            progress_callback=_adapter,
            cancel_check=cancel_check,
        )

        for p in pending:
            raw = raw_results.get(p.sku)
            title = _clean_response(raw) if raw else ""
            if title:
                if not skip_cache:
                    save_ai_title(self.conn, p.sku, title, TITLE_PROMPT_VERSION)
                results[p.sku] = title
            else:
                results[p.sku] = p.name or ""
        return results

    def apply_to_products(
        self,
        products: list[Product],
        force: bool = False,
        progress_cb=None,
        cancel_check=None,
        custom_instruction: str = "",
    ) -> int:
        """Generate + write ai_title to product.title in-place. Returns updated count.

        `generate_all` może zwrócić `p.name` jako fallback gdy AI nic nie wygenerował.
        Standardowo źródło prawdy = cache SQLite. Ale gdy `custom_instruction` jest podana,
        cache NIE jest aktualizowany (jednorazowy batch), więc czytamy z powrotu `generate_all`.
        """
        titles = self.generate_all(
            products, force=force, progress_cb=progress_cb, cancel_check=cancel_check,
            custom_instruction=custom_instruction,
        )
        updated = 0
        skip_cache = bool(custom_instruction.strip())
        for p in products:
            if skip_cache:
                # Custom batch: read from returned dict directly (cache nie ma tego wpisu).
                new = titles.get(p.sku)
                # `generate_all` fallbackuje do p.name gdy AI failuje — ignoruj taki fallback.
                if new and new != p.name and new != p.title:
                    p.title = new
                    updated += 1
                continue
            ai_title = get_ai_title(self.conn, p.sku, prompt_version=TITLE_PROMPT_VERSION)
            if ai_title and ai_title != p.title:
                p.title = ai_title
                updated += 1
        return updated
