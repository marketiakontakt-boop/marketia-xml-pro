"""Rules-based title transformer per SPEC §3.

Compose {TYPE/NAME} {BRAND} → UPPERCASE, ≤75 chars.
Strips leading brand prefixes already in the source name (e.g. "VILLAGO ACCESSORIES ..."),
trims when over budget, pads with a brand-agnostic atut when far below.

model_name is NOT included in the title — it derives from the product's own name,
so adding it would create duplicate words in the title.
"""
from __future__ import annotations

import json
import re
import zlib
from pathlib import Path

from app.parser.normalizer import Product

MAX_LEN = 75
PAD_BELOW = 70
ATUTS = ["PREMIUM", "DESIGN", "JAKOŚĆ", "2W1", "LUX"]  # fallback for unknown brands

# intex intentionally omitted — it uses an early-return path that never reaches padding
_BRAND_ATUTS: dict[str, list[str]] = {
    "villago":       ["DO JADALNI", "DO SALONU", "NOWOCZESNE", "SKANDYNAWSKIE"],
    "gardenstein":   ["NA TARAS", "DO OGRODU", "NA BALKON", "OGRODOWE"],
    "hopla_toys":    ["DLA DZIECI", "CERTYFIKAT", "EN 71", "BEZPIECZNA"],
    "marketia_home": ["DO DOMU", "PREMIUM", "PRAKTYCZNY", "DESIGN"],
    "zoovera":       ["DLA PSA", "DLA KOTA", "PREMIUM", "TRWAŁY"],
}

DEFAULT_KEYWORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"

# Brand-display + optional sub-line (ACCESSORIES / HOME / PREMIUM) appearing at the start of source names
_PREFIX_TAIL = r"(?:\s+(?:ACCESSORIES|PREMIUM|HOME|TOYS))?"


class TitleTransformer:
    def __init__(self, brand_data: dict[str, dict] | None = None):
        if brand_data is None:
            with DEFAULT_KEYWORDS_PATH.open(encoding="utf-8") as f:
                brand_data = json.load(f)
        self.brand_data = brand_data
        # Display names sorted longest-first so e.g. "MARKETIA HOME" beats "MARKETIA".
        displays = sorted(
            [b["name"].upper() for b in brand_data.values()],
            key=len,
            reverse=True,
        )
        self._prefix_re = re.compile(
            rf"^(?:{'|'.join(re.escape(d) for d in displays)}){_PREFIX_TAIL}\s+",
            re.IGNORECASE,
        )

    def _strip_leading_brand(self, name_upper: str) -> str:
        return self._prefix_re.sub("", name_upper, count=1).strip()

    def _brand_display(self, brand_key: str) -> str:
        info = self.brand_data.get(brand_key)
        return info["name"].upper() if info else ""

    def transform(self, product: Product) -> str:
        base = re.sub(r"\s+", " ", (product.name or "").upper()).strip()
        if not base:
            return ""

        # INTEX: keep existing name format, just strip extra INTEX occurrences + cap
        if product.brand == "intex":
            # Remove any embedded INTEX so it appears once at the front
            cleaned = re.sub(r"\bINTEX\b\s*", "", base, flags=re.IGNORECASE).strip()
            title = re.sub(r"\s+", " ", f"INTEX {cleaned}").strip()[:MAX_LEN].rstrip()
            product.title = title
            return title

        base = self._strip_leading_brand(base)
        brand_disp = self._brand_display(product.brand)

        title = self._compose(base, brand_disp)

        if len(title) > MAX_LEN:
            # Trim base to fit remaining budget for brand
            budget = MAX_LEN - (len(brand_disp) + 1 if brand_disp else 0)
            trimmed = base[:max(0, budget)].rstrip()
            title = self._compose(trimmed, brand_disp, "")
        if len(title) > MAX_LEN:
            # Final hard cut
            title = base[:MAX_LEN].rstrip()

        if len(title) < PAD_BELOW:
            brand_atuts = _BRAND_ATUTS.get(product.brand, ATUTS)
            atut = brand_atuts[zlib.crc32(product.sku.encode("utf-8")) % len(brand_atuts)]
            candidate = self._compose(title, "", atut)
            if len(candidate) <= MAX_LEN:
                title = candidate

        product.title = title
        return title

    @staticmethod
    def _compose(*parts: str) -> str:
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def transform_all(self, products: list[Product]) -> None:
        for p in products:
            self.transform(p)
