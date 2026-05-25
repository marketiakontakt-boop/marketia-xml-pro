"""Brand detection by keyword scoring + explicit brand-name prefix matching."""
from __future__ import annotations

import json
from pathlib import Path

from app.parser.normalizer import Product

DEFAULT_KEYWORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"

UNKNOWN = "unknown"


class BrandMapper:
    def __init__(self, keywords_path: Path | str | None = None):
        path = Path(keywords_path) if keywords_path else DEFAULT_KEYWORDS_PATH
        with path.open(encoding="utf-8") as f:
            self.brands: dict[str, dict] = json.load(f)
        # Precompute lowercase keywords + brand display names for cheap match.
        # Sort displays longest-first so e.g. "MARKETIA HOME" wins over "MARKETIA".
        unsorted = {k: v["name"].lower() for k, v in self.brands.items()}
        self._brand_display = dict(
            sorted(unsorted.items(), key=lambda kv: -len(kv[1]))
        )
        self._brand_keywords = {
            k: [kw.lower() for kw in v["keywords"]] for k, v in self.brands.items()
        }

    def detect(self, product: Product) -> tuple[str, float]:
        """Return (brand_key, confidence in 0..1). `unknown` + 0.0 if no signal."""
        text = " ".join(
            (product.name or "", product.category_name or "", product.manufacturer_name or "")
        ).lower()
        if not text.strip():
            return UNKNOWN, 0.0

        # 1. Explicit brand-name appearance (e.g. "VILLAGO ACCESSORIES ZAŚLEPKA...")
        #    Trust this over keyword scoring — high precision.
        for brand_key, brand_display in self._brand_display.items():
            if brand_display and brand_display in text:
                return brand_key, 1.0

        # 2. Keyword scoring across name + category + manufacturer
        scores: dict[str, int] = {}
        for brand_key, kws in self._brand_keywords.items():
            score = sum(1 for kw in kws if kw and kw in text)
            if score > 0:
                scores[brand_key] = score
        if not scores:
            return UNKNOWN, 0.0

        best = max(scores, key=lambda k: scores[k])
        confidence = scores[best] / max(1, len(self._brand_keywords[best]))
        return best, round(confidence, 3)

    def map_products(self, products: list[Product]) -> None:
        """In-place: set `product.brand` (the brand key) for each item."""
        for p in products:
            brand, _conf = self.detect(p)
            p.brand = brand
