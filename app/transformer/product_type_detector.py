"""Product-type detector — keyword dictionary lookup.

Reads `data/product_types.json` and matches keywords against the product's
`name + category_name`. Used by the title transformer to inject a canonical
product-type phrase (e.g. "DOMEK DLA LALEK", "ROWEREK BIEGOWY") at the SEO
position.

Match strategy: longest keyword first, case-insensitive, word-boundary aware.
When several types win, the one for the product's detected brand takes
precedence; otherwise the first match wins.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "product_types.json"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


class ProductTypeDetector:
    """Lookup table for keyword → product type (UPPERCASE PL)."""

    def __init__(self, types_path: Path | str | None = None):
        path = Path(types_path) if types_path else DEFAULT_PATH
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        raw.pop("_meta", None)
        # Flatten: brand_key → list[(keyword_lower, type_upper)]
        # We also keep the brand context to break ties consistently.
        self._by_brand: dict[str, list[tuple[str, str]]] = {}
        for brand_key, type_map in raw.items():
            pairs: list[tuple[str, str]] = []
            for type_name, keywords in type_map.items():
                for kw in keywords:
                    pairs.append((_normalize(kw), type_name))
            # Longest keyword first → more specific matches win.
            pairs.sort(key=lambda p: -len(p[0]))
            self._by_brand[brand_key] = pairs

        # Brand-agnostic flat list as final fallback.
        flat: list[tuple[str, str]] = []
        for pairs in self._by_brand.values():
            flat.extend(pairs)
        flat.sort(key=lambda p: -len(p[0]))
        self._flat = flat

    def detect(self, name: str, category: str, brand_key: str | None = None) -> str:
        """Return the canonical UPPERCASE product-type phrase or "".

        Looks first inside the brand's keyword set (most precise), then falls
        back to brand-agnostic search across all known types.
        """
        hay = f"{_normalize(name)} {_normalize(category)}"
        if not hay.strip():
            return ""

        if brand_key:
            for kw, type_name in self._by_brand.get(brand_key, ()):
                if self._matches(hay, kw):
                    return type_name

        for kw, type_name in self._flat:
            if self._matches(hay, kw):
                return type_name
        return ""

    @staticmethod
    def _matches(haystack: str, keyword: str) -> bool:
        """Token-set match: every word of `keyword` must appear in `haystack`
        as a whole word, regardless of order or distance. Falls back to a
        prefix-stem match (drop last char) so Polish plurals also match —
        e.g. keyword "krzesło" matches haystack token "krzesła"."""
        if not keyword:
            return False
        tokens = [t for t in keyword.split() if t]
        if not tokens:
            return False
        for t in tokens:
            if re.search(rf"\b{re.escape(t)}\b", haystack):
                continue
            # Polish morphology fallback: shared 4+ char stem.
            if len(t) >= 5:
                stem = t[:-1]
                if re.search(rf"\b{re.escape(stem)}[a-ząćęłńóśźż]?\b", haystack):
                    continue
            return False
        return True
