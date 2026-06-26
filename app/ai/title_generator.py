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
from app.ai.prompts import TITLE_PROMPT_VERSION, TITLE_SEO_PROMPT_V1
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

    def generate_one(self, p: Product, force: bool = False) -> str:
        if not force:
            cached = get_ai_title(self.conn, p.sku)
            if cached:
                return cached
        payload = _product_payload(p, self._brand_for(p))
        raw = self.client.call(system=TITLE_SEO_PROMPT_V1, content=payload)
        title = _clean_response(raw)
        if not title:
            return p.name or ""
        save_ai_title(self.conn, p.sku, title, TITLE_PROMPT_VERSION)
        return title

    def generate_all(
        self,
        products: list[Product],
        force: bool = False,
        progress_cb=None,
        cancel_check=None,
        wait_on_cooldown: bool = True,
    ) -> dict[str, str]:
        """Batch generate. Returns {sku: ai_title}."""
        results: dict[str, str] = {}
        pending: list[Product] = []
        for p in products:
            if not force:
                cached = get_ai_title(self.conn, p.sku)
                if cached:
                    results[p.sku] = cached
                    continue
            pending.append(p)

        if not pending:
            return results

        requests = [
            {
                "custom_id": p.sku,
                "system": TITLE_SEO_PROMPT_V1,
                "content": _product_payload(p, self._brand_for(p)),
            }
            for p in pending
        ]

        def _adapter(done, total, custom_id, error=None, cooling_status=""):
            if progress_cb:
                progress_cb(done, total, custom_id, error=error)

        raw_results = self.client.generate_all(
            requests,
            progress_callback=_adapter,
            wait_on_cooldown=wait_on_cooldown,
            cancel_check=cancel_check,
        )

        for p in pending:
            raw = raw_results.get(p.sku)
            title = _clean_response(raw) if raw else ""
            if title:
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
    ) -> int:
        """Generate + write ai_title to product.name in-place. Returns updated count."""
        titles = self.generate_all(
            products, force=force, progress_cb=progress_cb, cancel_check=cancel_check
        )
        updated = 0
        for p in products:
            new = titles.get(p.sku)
            if new and new != p.name:
                p.name = new
                updated += 1
        return updated
