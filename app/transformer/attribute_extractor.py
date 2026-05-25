"""Extract product attributes from HTML description text using regex patterns."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parser.normalizer import Product

_TAG_RE = re.compile(r"<[^>]+>")

_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("Wymiary", re.compile(
        r"(\d+[\.,]?\d*)\s*[xX×]\s*(\d+[\.,]?\d*)\s*(?:[xX×]\s*(\d+[\.,]?\d*))?\s*cm",
        re.IGNORECASE,
    ), "dims"),
    ("Szerokość", re.compile(
        r"szeroko[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*cm", re.IGNORECASE
    ), "single_cm"),
    ("Wysokość", re.compile(
        r"wysoko[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*cm", re.IGNORECASE
    ), "single_cm"),
    ("Głębokość", re.compile(
        r"g[łl][eę]boko[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*cm", re.IGNORECASE
    ), "single_cm"),
    ("Pojemność", re.compile(
        r"pojemno[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*(?:l\b|litr)", re.IGNORECASE
    ), "single_l"),
    ("Materiał", re.compile(
        r"materia[łl]\s*[:\-]\s*([^<\n,.]{3,40})", re.IGNORECASE
    ), "text"),
    ("Kolor", re.compile(
        r"kolor\s*[:\-]\s*([^<\n,.]{3,30})", re.IGNORECASE
    ), "text"),
    ("Maks. obciążenie", re.compile(
        r"(?:maks?\.?\s*obci[aą][żz]enie|max\.?\s*load)\s*[:\-]?\s*(\d+[\.,]?\d*)\s*kg",
        re.IGNORECASE,
    ), "single_kg"),
    ("Waga", re.compile(
        r"\bwaga\s*[:\-]\s*(\d+[\.,]?\d*)\s*kg", re.IGNORECASE
    ), "single_kg"),
]


def extract_attributes_from_html(html: str) -> dict[str, str]:
    """Return attribute_name → value dict extracted from HTML description."""
    if not html:
        return {}
    text = _TAG_RE.sub(" ", html)
    result: dict[str, str] = {}
    for attr_name, pattern, kind in _PATTERNS:
        if attr_name in result:
            continue
        m = pattern.search(text)
        if not m:
            continue
        if kind == "dims":
            groups = [g for g in m.groups() if g is not None]
            result[attr_name] = " x ".join(g.replace(",", ".") for g in groups) + " cm"
        elif kind == "single_cm":
            result[attr_name] = m.group(1).replace(",", ".") + " cm"
        elif kind == "single_l":
            result[attr_name] = m.group(1).replace(",", ".") + " l"
        elif kind == "single_kg":
            result[attr_name] = m.group(1).replace(",", ".") + " kg"
        elif kind == "text":
            result[attr_name] = m.group(1).strip()
    return result


def enrich_product_attributes(product: "Product") -> None:
    """Add regex-extracted attributes to product.attributes; XML values take precedence."""
    extracted = extract_attributes_from_html(product.description or "")
    for k, v in extracted.items():
        if k not in product.attributes:
            product.attributes[k] = v
