"""Extract product attributes from HTML description text using regex patterns.

Handles two description formats found in BaseLinker XML:
- JUMI format: rich bullet-point specs (`Średnica zewnętrzna - 244 cm`)
- Plain prose: inline specs (`szerokość: 90 cm`, `waga: 5 kg`)
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parser.normalizer import Product

_TAG_RE = re.compile(r"<[^>]+>")
_NBSP = re.compile(r"&nbsp;|&#160;| ")
_MULTI_SP = re.compile(r"[ \t]+")

# ---------------------------------------------------------------------------
# Bullet-point style: "Label - value unit" or "Label: value unit"
# Common in JUMI descriptions after HTML stripping.
# ---------------------------------------------------------------------------
_BULLET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Szerokość", re.compile(
        r"szeroko[sś][cć](?:[ \t]+\w+){0,3}?[ \t]*[-:][ \t]*(\d+[\.,]?\d*(?:[ \t]*[-–][ \t]*\d+[\.,]?\d*)?)[ \t]*cm",
        re.IGNORECASE,
    )),
    ("Wysokość", re.compile(
        r"wysoko[sś][cć](?:[ \t]+\w+){0,3}?[ \t]*[-:][ \t]*(\d+[\.,]?\d*(?:[ \t]*[-–][ \t]*\d+[\.,]?\d*)?)[ \t]*cm",
        re.IGNORECASE,
    )),
    ("Głębokość", re.compile(
        r"g[łl][eę]boko[sś][cć](?:[ \t]+\w+){0,3}?[ \t]*[-:][ \t]*(\d+[\.,]?\d*(?:[ \t]*[-–][ \t]*\d+[\.,]?\d*)?)[ \t]*cm",
        re.IGNORECASE,
    )),
    ("Długość", re.compile(
        r"d[łl]ugo[sś][cć](?:[ \t]+\w+){0,3}?[ \t]*[-:][ \t]*(\d+[\.,]?\d*(?:[ \t]*[-–][ \t]*\d+[\.,]?\d*)?)[ \t]*cm",
        re.IGNORECASE,
    )),
    ("Średnica", re.compile(
        r"[sś]rednica(?:[ \t]+\w+){0,3}?[ \t]*[-:][ \t]*(\d+[\.,]?\d*(?:[ \t]*[-–][ \t]*\d+[\.,]?\d*)?)[ \t]*cm",
        re.IGNORECASE,
    )),
    ("Maks. obciążenie", re.compile(
        r"(?:maks(?:ymalne?)?\.?[ \t]*obci[aą][żz]enie|max\.?\s*load|no[sś]no[sś][cć])"
        r"[ \t]*[-:][ \t]*(\d+[\.,]?\d*)[ \t]*kg",
        re.IGNORECASE,
    )),
    ("Maks. obciążenie", re.compile(
        r"dopuszczalna[ \t]+waga(?:[ \t]+\w+){0,4}?[ \t]+(\d+[\.,]?\d*)[ \t]*kg",
        re.IGNORECASE,
    )),
    ("Waga", re.compile(
        r"\bwaga\s*[-:]\s*(\d+[\.,]?\d*)\s*kg",
        re.IGNORECASE,
    )),
    ("Pojemność", re.compile(
        r"pojemno[sś][cć]\s*[-:]\s*(\d+[\.,]?\d*)\s*(?:l\b|litr)",
        re.IGNORECASE,
    )),
    ("Moc", re.compile(
        r"\bmoc\s*[-:]\s*(\d+[\.,]?\d*)\s*[wW]\b",
        re.IGNORECASE,
    )),
    ("Napięcie", re.compile(
        r"napi[eę]cie\s*[-:]\s*(\d+[\.,]?\d*)\s*[vV]\b",
        re.IGNORECASE,
    )),
    ("Liczba sprężyn", re.compile(
        r"liczba\s+spr[eę][żz]yn\s*[-:]\s*(\d+)\s*szt",
        re.IGNORECASE,
    )),
    ("Kolor", re.compile(
        r"kolor(?:[ \t]+\w+){0,2}?[ \t]*[-:][ \t]*([^<\n.,;:]{2,30})",
        re.IGNORECASE,
    )),
    ("Materiał", re.compile(
        r"materia[łl](?:[ \t]+\w+){0,2}?[ \t]*[-:][ \t]*([^<\n.,;:]{3,50})",
        re.IGNORECASE,
    )),
    ("Rodzaj", re.compile(
        r"rodzaj\s*[-:]\s*([^<\n]{3,40})",
        re.IGNORECASE,
    )),
    ("Montaż", re.compile(
        r"monta[żz]\s*[-:]\s*([^<\n]{3,40})",
        re.IGNORECASE,
    )),
]

# ---------------------------------------------------------------------------
# Inline dimension patterns: "W x H x D cm" or "90 x 50 x 40 cm"
# ---------------------------------------------------------------------------
_DIM_PATTERN = re.compile(
    r"(\d+[\.,]?\d*)\s*[xX×]\s*(\d+[\.,]?\d*)\s*(?:[xX×]\s*(\d+[\.,]?\d*))?\s*cm",
    re.IGNORECASE,
)


def _clean(html: str) -> str:
    text = _NBSP.sub(" ", html)
    text = _TAG_RE.sub("\n", text)
    text = _MULTI_SP.sub(" ", text)
    return text


def extract_attributes_from_html(html: str) -> dict[str, str]:
    """Return attribute_name → value dict extracted from HTML description."""
    if not html:
        return {}
    text = _clean(html)
    result: dict[str, str] = {}

    # Bullet / structured patterns
    for attr_name, pattern in _BULLET_PATTERNS:
        if attr_name in result:
            continue
        m = pattern.search(text)
        if not m:
            continue
        val = m.group(1).strip().rstrip(",;.").strip()
        val = val.replace(",", ".")
        if attr_name in ("Kolor", "Materiał", "Rodzaj", "Montaż"):
            val = val.strip()
        elif any(unit in attr_name for unit in ("obciążenie", "Waga")):
            val = val + " kg"
        elif "Pojemność" in attr_name:
            val = val + " l"
        elif "Moc" in attr_name:
            val = val + " W"
        elif "Napięcie" in attr_name:
            val = val + " V"
        elif "sprężyn" in attr_name:
            val = val + " szt."
        elif attr_name not in ("Kolor", "Materiał", "Rodzaj", "Montaż"):
            val = val + " cm"
        if val:
            result[attr_name] = val

    # Inline dimensions (WxHxD cm) only if we don't have separate width/height
    if "Szerokość" not in result and "Wysokość" not in result:
        m = _DIM_PATTERN.search(text)
        if m:
            groups = [g for g in m.groups() if g is not None]
            result["Wymiary"] = " x ".join(g.replace(",", ".") for g in groups) + " cm"

    return result


def enrich_product_attributes(product: "Product") -> None:
    """Add regex-extracted attributes to product.attributes; XML values take precedence.

    Runs extraction on both description and description_extra_1 (plain text).
    """
    sources = [
        product.description or "",
        getattr(product, "description_extra_1", "") or "",
    ]
    for src in sources:
        extracted = extract_attributes_from_html(src)
        for k, v in extracted.items():
            if k not in product.attributes:
                product.attributes[k] = v
