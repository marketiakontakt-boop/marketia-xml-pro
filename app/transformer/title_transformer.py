"""Simple-mode title transformer (v4 — BRAND + MODEL + TYP + CECHY).

Pattern target (Allegro SEO, max 75 chars):

    [BRAND] [MODEL] [PRODUCT TYPE] [HARD FEATURES from attrs]

Sources are strictly factual:
- product_type from `data/product_types.json` keyword dictionary
- features from `attributes`: Materiał, Kolor, Wymiar, Wiek, Liczba osób, …
- audience from category root: DLA DZIECI / DLA NIEJ / DLA PSA / …

INTEX exception: original supplier titles are already good — we keep them
as-is and only UPPERCASE + 75-char trim.

The output is run through `validate_title()` to catch known nonsense
patterns (DO METAL, DO INNY, DO 8W1, NA DO, Z Z) that older versions of the
generator used to leak.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.parser.normalizer import Product
from app.transformer.product_type_detector import ProductTypeDetector

MAX_LEN = 75
MIN_TRIM_FLOOR = 40

DEFAULT_KEYWORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"

# ── OEM / supplier brand names to strip ──────────────────────────────────────

_OEM_BY_BRAND: dict[str, frozenset[str]] = {
    "hopla_toys":    frozenset({"ecotoys", "iplay"}),
    "marketia_home": frozenset({"modernhome", "multistore"}),
    "gardenstein":   frozenset({
        "multigames", "multistar", "multigarden",
        "bauerkraft", "molden",
    }),
    "villago":       frozenset(),
    "intex":         frozenset(),
    "lifekraft":     frozenset(),
    "zoovera":       frozenset(),
}
_OEM_GLOBAL: frozenset[str] = frozenset()
_ALL_OEM: frozenset[str] = frozenset().union(*_OEM_BY_BRAND.values()) | _OEM_GLOBAL

# ── feature attribute keys (priority order) ──────────────────────────────────

_FEATURE_ATTR_KEYS: tuple[tuple[str, ...], ...] = (
    ("Materiał dominujący", "Materiał", "Materiał wykonania", "materiał"),
    ("Kolor dominujący", "Kolor", "kolor"),
    ("Przeznaczenie", "przeznaczenie"),
    ("Linia", "Seria", "linia"),
    ("Wiek", "Wiek od", "Wiek do", "wiek"),
    ("Liczba osób", "liczba osób"),
    ("Liczba elementów w zestawie", "Liczba elementów"),
)

_AUDIENCE_PREFIXES: tuple[str, ...] = (
    "DLA DZIECI", "DLA NIEJ", "DLA NIEGO", "DLA PSA", "DLA KOTA",
)

# ── regexes ──────────────────────────────────────────────────────────────────

_QUOTE_CHARS_RE = re.compile(r'["“”„«»‘’\']')
_MULTI_WS_RE = re.compile(r"\s+")
_TRAILING_PUNCT = " -–—,.;:|+/\\"
_DIM_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(cm|mm|m)?",
    re.IGNORECASE,
)

# Forbidden bełkot patterns produced by older generators. Used by the
# `validate_title` helper — we don't auto-rewrite, we surface them.
FORBIDDEN_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bDO\s+(METAL|INNY|INNE|TYLNY|PRZEDNI|8W1|6W1|4W1)\b", re.IGNORECASE),
    re.compile(r"\bNA\s+DO\b", re.IGNORECASE),
    re.compile(r"\bDO\s+NA\b", re.IGNORECASE),
    re.compile(r"\bZ\s+Z\b", re.IGNORECASE),
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _strip_supplier(title: str, supplier: str) -> str:
    return re.compile(rf"\b{re.escape(supplier)}\b", re.IGNORECASE).sub("", title)


def _word_boundary_trim(title: str, max_len: int = MAX_LEN) -> str:
    if len(title) <= max_len:
        return title
    cut = title[:max_len]
    last_space = cut.rfind(" ")
    if last_space >= MIN_TRIM_FLOOR:
        cut = cut[:last_space]
    return cut.rstrip(_TRAILING_PUNCT)


def _format_dimensions(value: str | None) -> str:
    if not value:
        return ""
    m = _DIM_RE.search(value)
    if not m:
        return ""
    nums = [m.group(i) for i in (1, 2, 3) if m.group(i)]
    unit = (m.group(4) or "").upper()
    if not nums:
        return ""
    nums = [n.replace(",", ".").rstrip("0").rstrip(".") if "." in n else n for n in nums]
    return f"{'X'.join(nums)} {unit}".strip()


def _attr_value(attrs: dict, keys: tuple[str, ...]) -> str:
    if not attrs:
        return ""
    for k in keys:
        v = attrs.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _feature_token(value: str, max_words: int = 3) -> str:
    if not value:
        return ""
    raw = _QUOTE_CHARS_RE.sub("", str(value))
    raw = _MULTI_WS_RE.sub(" ", raw).strip(" -–—,.;:|+/\\()[]")
    if not raw or raw.lower() in {"nie", "brak", "n/d", "n/a"}:
        return ""
    words = raw.split()[:max_words]
    out = " ".join(words).upper()
    if re.fullmatch(r"[\d.,]+", out):
        return ""
    return out


def _audience_from_category(category_name: str | None) -> str:
    if not category_name:
        return ""
    head = category_name.split("/")[0].strip().upper()
    for marker in _AUDIENCE_PREFIXES:
        if marker in head:
            return marker
    return ""


def _contains_word(haystack: str, needle: str) -> bool:
    return bool(re.search(rf"\b{re.escape(needle)}\b", haystack))


def _append_bounded(title: str, token: str, budget: int) -> str:
    if not token or _contains_word(title, token):
        return title
    candidate = f"{title} {token}".strip() if title else token
    return candidate if len(candidate) <= budget else title


# ── validator ───────────────────────────────────────────────────────────────


def validate_title(title: str) -> list[str]:
    """Return a list of issues found in `title`. Empty list = clean."""
    issues: list[str] = []
    if not title:
        issues.append("empty")
        return issues
    if len(title) > MAX_LEN:
        issues.append(f"length>{MAX_LEN}")
    if title != title.upper():
        issues.append("not_uppercase")
    if len(title.split()) < 4:
        issues.append("min_4_words")
    for pat in FORBIDDEN_PATTERNS:
        m = pat.search(title)
        if m:
            issues.append(f"forbidden:{m.group(0).upper()}")
    if re.search(r"[a-z]", title.lower()) and not re.search(
        r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", title
    ):
        if any(p in title.lower() for p in ("dla dzieci", "dla niej", "krzeslo", "lozko")):
            # ASCII fallback detected where Polish glyphs were expected.
            issues.append("missing_polish_glyphs")
    return issues


# ── main class ──────────────────────────────────────────────────────────────


class TitleTransformer:
    """Deterministic SEO-Allegro title builder. Public API unchanged."""

    def __init__(
        self,
        brand_data: dict[str, dict] | None = None,
        type_detector: ProductTypeDetector | None = None,
    ):
        if brand_data is None:
            with DEFAULT_KEYWORDS_PATH.open(encoding="utf-8") as f:
                brand_data = json.load(f)
        self.brand_display: dict[str, str] = {
            key: meta.get("name", key.upper()) for key, meta in brand_data.items()
        }
        self.type_detector = type_detector or ProductTypeDetector()

    def _brand_display_for(self, brand_key: str | None) -> str:
        if not brand_key or brand_key == "unknown":
            return ""
        return self.brand_display.get(brand_key, "")

    # ── pipeline ────────────────────────────────────────────────────────────

    def transform(self, p: Product) -> None:
        original = (p.name or "").strip()
        if not original:
            return

        if (p.brand or "") == "intex":
            p.title = self._transform_intex(original)
            return

        brand_display = self._brand_display_for(p.brand)
        attrs = getattr(p, "attributes", None) or {}
        category = getattr(p, "category_name", "") or ""

        product_type = self.type_detector.detect(original, category, p.brand)
        model = (p.model_name or "").strip().upper()

        # Build title (rev. 2026-07-01 — user request: BRAND NIE pierwsza).
        # Order: TYP → CECHY(legacy) → BRAND MODEL → wymiary → atrybuty.
        # SEO Allegro: user szuka "fotel ogrodowy" nie "GardenStein" — typ na początku.
        title = ""
        if product_type:
            title = product_type

        # Descriptive words z original name (cechy kluczowe: REZYDENCJA MALIBU, ŚWIECĄCE KOŁA)
        # Zostaw miejsce na brand + model (~20 znaków rezerwy)
        legacy_desc = self._clean_legacy(original, brand_display, model)
        brand_model_reserve = len(brand_display or "") + len(model) + 4  # + separatory
        legacy_max = MAX_LEN - brand_model_reserve
        for tok in legacy_desc.split():
            if len(title) >= legacy_max - 3:
                break
            if product_type and _contains_word(product_type, tok):
                continue
            if _contains_word(title, tok):
                continue
            title = _append_bounded(title, tok, legacy_max)

        # BRAND + MODEL na KOŃCU (jako sygnatura marki, nie pierwsze słowo)
        if brand_display and not _contains_word(title, brand_display):
            title = _append_bounded(title, brand_display.upper(), MAX_LEN)
        if model and not _contains_word(title, model):
            title = _append_bounded(title, model, MAX_LEN)

        # Append hard features from attributes (Wymiar → Materiał → Kolor → …).
        dims = _format_dimensions(attrs.get("Wymiary") or attrs.get("wymiary"))
        if dims and not _DIM_RE.search(title):
            title = _append_bounded(title, dims, MAX_LEN)

        for keys in _FEATURE_ATTR_KEYS:
            if len(title) >= MAX_LEN - 6:
                break
            tok = _feature_token(_attr_value(attrs, keys))
            if tok and not _contains_word(title, tok.split()[0]):
                title = _append_bounded(title, tok, MAX_LEN)

        audience = _audience_from_category(category)
        if audience and not _contains_word(title, audience.split()[0]):
            title = _append_bounded(title, audience, MAX_LEN)

        title = _MULTI_WS_RE.sub(" ", title).strip(_TRAILING_PUNCT)
        p.title = _word_boundary_trim(title, MAX_LEN)

    def transform_all(self, products: list[Product]) -> None:
        for p in products:
            self.transform(p)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _transform_intex(self, title: str) -> str:
        """INTEX: keep original (it already has TYPE + DIMENSIONS + SKU)."""
        title = _QUOTE_CHARS_RE.sub(" ", title)
        title = _MULTI_WS_RE.sub(" ", title).strip(_TRAILING_PUNCT).upper()
        if not _contains_word(title, "INTEX"):
            title = f"INTEX {title}".strip()
        return _word_boundary_trim(title, MAX_LEN)

    @staticmethod
    def _clean_legacy(
        original: str, brand_display: str, model_upper: str
    ) -> str:
        """Strip OEM suppliers + brand + model from the original name and
        return whatever descriptive words remain (used only when the
        dictionary fails to identify a product type)."""
        cleaned = _QUOTE_CHARS_RE.sub(" ", original)
        cleaned = _MULTI_WS_RE.sub(" ", cleaned).strip(_TRAILING_PUNCT)
        for supplier in _ALL_OEM:
            cleaned = _strip_supplier(cleaned, supplier)
        cleaned = cleaned.upper()
        if brand_display:
            cleaned = re.sub(
                rf"\b{re.escape(brand_display.upper())}\b", "", cleaned
            )
        if model_upper:
            cleaned = re.sub(rf"\b{re.escape(model_upper)}\b", "", cleaned)
        # Drop opaque short tokens like 'XBODY1B', 'G70', 'SZALS17' — they are
        # supplier SKUs, not features. Keep tokens that are pure words or
        # contain Polish diacritics.
        cleaned = _MULTI_WS_RE.sub(" ", cleaned).strip(_TRAILING_PUNCT)
        kept: list[str] = []
        for tok in cleaned.split():
            if re.fullmatch(r"[A-Z]{1,4}\d+[A-Z0-9]*", tok):
                continue  # looks like supplier SKU
            if re.fullmatch(r"\d+[A-Z]{1,3}", tok):
                continue
            if len(tok) <= 1:
                continue
            kept.append(tok)
        return " ".join(kept)
