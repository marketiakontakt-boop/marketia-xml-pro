"""Assign model names to products, grouping color/size variants together.

Same series (e.g., BERGEN Niebieskie + BERGEN Szare) → shared fictional name:
  Krzesło Bergen Niebieskie  →  Holm Niebieskie   (pool name, not title-derived)
  Krzesło Bergen Szare       →  Holm Szare
  Krzesło Bergen Białe       →  Holm Białe

Pool names come from data/model_names.json per brand.
If the pool is exhausted, a random 5-letter uppercase code is used as fallback.
SKU→model_name is cached in SQLite so re-runs are idempotent.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import string
from collections import defaultdict
from pathlib import Path

from app.cache.sqlite_cache import get_sku_model_name, save_sku_model_name
from app.parser.normalizer import Product

DEFAULT_POOL_PATH = Path(__file__).resolve().parents[2] / "data" / "model_names.json"

# Brands where model names are NOT replaced — original name from supplier XML is kept.
SKIP_MODEL_RENAME_BRANDS: frozenset[str] = frozenset({"intex"})

_CONS = list("bdfgklmnprst")   # no v/z/x — softer, more name-friendly in Polish
_VOWELS = list("aeiou")
_CODAS = ["", "l", "n", "r"]    # short coda, no "s" at end (feels like abbreviation)


def _random_pronounceable() -> str:
    """Generate a 4-5 letter name using CV syllable patterns.

    Produces names like: Balon, Kenis, Toral, Mesin, Delos — easy to read aloud in Polish.
    Avoids double letters and harsh consonant clusters.
    """
    c1 = random.choice(_CONS).upper()
    v1 = random.choice(_VOWELS)
    # second consonant different from first
    c2 = random.choice([c for c in _CONS if c != c1.lower()])
    # second vowel different from first (avoids "aa", "ee" etc.)
    v2 = random.choice([v for v in _VOWELS if v != v1])
    coda = random.choice(_CODAS)
    return c1 + v1 + c2 + v2 + coda

# Words that indicate a color or size variant
_VARIANT_WORDS: frozenset[str] = frozenset({
    # Polish colors — nom/acc forms
    "biały", "białe", "biała", "białego",
    "czarny", "czarne", "czarna", "czarnego",
    "szary", "szare", "szara", "szarego",
    "granatowy", "granatowe", "granatowa",
    "niebieski", "niebieskie", "niebieska",
    "czerwony", "czerwone", "czerwona",
    "zielony", "zielone", "zielona",
    "żółty", "żółte", "żółta",
    "brązowy", "brązowe", "brązowa",
    "beżowy", "beżowe", "beżowa",
    "srebrny", "srebrne", "srebrna",
    "złoty", "złote", "złota",
    "fioletowy", "fioletowe", "fioletowa",
    "różowy", "różowe", "różowa",
    "kremowy", "kremowe", "kremowa",
    "bordowy", "bordowe", "bordowa",
    "turkusowy", "turkusowe",
    "ciemny", "ciemne", "ciemna",
    "jasny", "jasne", "jasna",
    "khaki", "beige", "ecru",
    # English colors
    "black", "white", "grey", "gray", "dark", "light",
    "brown", "green", "blue", "red", "yellow", "silver", "gold", "pink",
    # Size letters intentionally excluded: m, l, s, xl, xxl, xs etc.
    # This catalog (furniture, toys, pools) uses M/L/XL/XXL as measurement units
    # ("3X3 M", "1.8 L", "kuchnia XXL"), not clothing size variants.
    # Including them causes the measurement unit to be grabbed as the color suffix,
    # making all color variants collapse to the same model name ("Bari M").
})

# Generic furniture / category words stripped when finding the clean series name
_FURNITURE_WORDS: frozenset[str] = frozenset({
    "krzesło", "krzesła", "fotel", "fotele", "sofa", "sofą", "kanapa", "kanapy",
    "stół", "stolik", "ława", "łóżko", "regał", "komoda", "szafka", "szafki",
    "leżak", "huśtawka", "altana", "pergola", "ławka", "biurko",
    "zestaw", "meble", "mebel", "mebli", "narożnik", "narożna",
    "hoker", "taboret", "bujak", "szezlong",
})


def _strip_variant_words(name: str) -> str:
    """Remove ALL color/size words from name; result is the series key."""
    words = name.strip().lower().split()
    return " ".join(w for w in words if w not in _VARIANT_WORDS)


def _extract_variant_suffix(name: str) -> str:
    """Return color/size words starting from the first color found (Title Case)."""
    words = name.strip().split()
    first_idx = next((i for i, w in enumerate(words) if w.lower() in _VARIANT_WORDS), -1)
    if first_idx == -1:
        return ""
    suffix: list[str] = []
    for word in words[first_idx:]:
        if word.lower() in _VARIANT_WORDS:
            suffix.append(word.capitalize())
        else:
            break
    return " ".join(suffix)


def _series_from_stripped(stripped_lower: str, first_word_only: bool = True) -> str:
    """Extract clean series name: strip furniture words, return Title Case.

    first_word_only=True  → returns just the first remaining word (e.g. 'Bergen')
    first_word_only=False → returns all remaining words joined (for standalone products)
    """
    words = [w for w in stripped_lower.split() if w not in _FURNITURE_WORDS]
    if not words:
        words = stripped_lower.split()  # fallback: keep all words
    if first_word_only:
        return words[0].capitalize() if words else ""
    return " ".join(w.capitalize() for w in words) if words else ""


_WORD_BOUNDARY = r'(?<![A-Za-zÀ-ɏ0-9])'  # handles Latin + Polish extended chars


def _replace_series_in_name(product: Product, old_series: str, new_series: str) -> None:
    """Replace the original series base word with the fictional name in product.name.

    Called every run (also when returning cached model) so the substitution is idempotent:
    each run the XML-fresh product.name contains the original supplier word, which we swap.
    """
    if not old_series or not new_series or old_series.lower() == new_series.lower():
        return
    if old_series.lower() in _FURNITURE_WORDS:
        return  # never replace category words like "krzesło"
    pat = re.compile(
        _WORD_BOUNDARY + re.escape(old_series) + r'(?![A-Za-zÀ-ɏ0-9])',
        re.IGNORECASE,
    )
    if product.name:
        product.name = pat.sub(new_series, product.name)


def _used_base_names_for_brand(conn: sqlite3.Connection, brand: str) -> set[str]:
    """Return first words of all model names already assigned to this brand in DB."""
    rows = conn.execute(
        "SELECT DISTINCT model_name FROM sku_model_names WHERE brand = ?", (brand,)
    ).fetchall()
    return {r["model_name"].split()[0] for r in rows if r["model_name"]}


class ModelNameGenerator:
    """Assigns fictional model names from a per-brand pool (data/model_names.json).

    Color/size variants sharing the same series base get the same pool name:
      Krzesło Bergen Niebieskie → Holm Niebieskie
      Krzesło Bergen Szare      → Holm Szare
    Each series group across ALL products gets a unique pool name.
    Pool exhaustion → random 5-letter uppercase fallback.
    SKU→model_name cached in SQLite (idempotent across re-runs).
    """

    def __init__(self, conn: sqlite3.Connection, pool_path: Path | str | None = None):
        self.conn = conn
        # names used in this instance's session (prevents same-run duplicates)
        self._session_used: dict[str, set[str]] = {}

        path = Path(pool_path) if pool_path else DEFAULT_POOL_PATH
        try:
            with path.open(encoding="utf-8") as f:
                self._pool: dict[str, list[str]] = json.load(f)
        except Exception:
            self._pool = {}

    # ------------------------------------------------------------------
    # Pool helpers

    def _pick_pool_name(self, brand: str) -> str:
        """Pick a random unused pool name for brand. Falls back to a pronounceable name."""
        pool = self._pool.get(brand, [])
        db_used = _used_base_names_for_brand(self.conn, brand)
        session_used = self._session_used.setdefault(brand, set())
        all_used = db_used | session_used

        available = [n for n in pool if n not in all_used]
        if available:
            name = random.choice(available)
        else:
            # Pool exhausted — generate unique pronounceable fallback (CV syllable pattern)
            while True:
                name = _random_pronounceable()
                if name not in all_used:
                    break

        session_used.add(name)
        return name

    # ------------------------------------------------------------------
    # Cache helpers

    def _save(self, brand: str, model_name: str, sku: str) -> None:
        save_sku_model_name(self.conn, sku, brand, model_name)

    # ------------------------------------------------------------------
    # Single-product assignment (idempotent)

    def assign(self, product: Product) -> str:
        if (product.brand or "").lower() in SKIP_MODEL_RENAME_BRANDS:
            return product.model_name or ""

        raw = product.name or product.title or product.sku
        stripped = _strip_variant_words(raw)
        original_series = _series_from_stripped(stripped.lower())

        prior = get_sku_model_name(self.conn, product.sku)
        if prior:
            product.model_name = prior
            prior_base = prior.split()[0]
            _replace_series_in_name(product, original_series, prior_base)
            return prior

        series = self._pick_pool_name(product.brand or "")
        if not series:
            series = original_series or raw.split()[0].capitalize()

        suffix = _extract_variant_suffix(raw)
        full_model = f"{series} {suffix}".strip() if suffix else series
        self._save(product.brand or "", full_model, product.sku)
        product.model_name = full_model
        _replace_series_in_name(product, original_series, series)
        return full_model

    # ------------------------------------------------------------------
    # Batch assignment — groups color variants so they share the same pool name

    def assign_all(self, products: list[Product]) -> None:
        # Pass 1: load already-cached products; skip brands that keep original names
        to_assign: list[Product] = []
        for p in products:
            if (p.brand or "").lower() in SKIP_MODEL_RENAME_BRANDS:
                continue  # keep model_name as-is from XML
            prior = get_sku_model_name(self.conn, p.sku)
            if prior:
                p.model_name = prior
                # Replace original series name even on cache hit (XML-fresh name each run).
                # Use only the base word (first token) for substitution so the color suffix
                # in product.name is not duplicated.
                raw = p.name or p.title or p.sku
                original_series = _series_from_stripped(_strip_variant_words(raw).lower())
                prior_base = prior.split()[0]
                _replace_series_in_name(p, original_series, prior_base)
            else:
                to_assign.append(p)

        if not to_assign:
            return

        # Pass 2: group by (brand, stripped_series_key)
        # Products sharing the same non-color base → same series group → same pool name.
        # Products without a color suffix each get their own key (standalone).
        groups: dict[tuple[str, str], list[tuple[Product, str]]] = defaultdict(list)
        for p in to_assign:
            brand = p.brand or ""
            raw = p.name or p.title or p.sku
            suffix = _extract_variant_suffix(raw)
            stripped = _strip_variant_words(raw)
            series_key = (brand, stripped) if suffix else (brand, p.sku)
            groups[series_key].append((p, suffix))

        # Pass 3: assign one pool name per group (shared base across color variants).
        # model_name = "<PoolBase> <ColorSuffix>" so variants are distinct yet share the base.
        # The series base (not the full model_name) is substituted into product.name so the
        # color word in the original name is not duplicated.
        for (brand, stripped_key), items in groups.items():
            series = self._pick_pool_name(brand)
            original_series = _series_from_stripped(stripped_key)

            for p, suffix in items:
                full_model = f"{series} {suffix}".strip() if suffix else series
                self._save(brand, full_model, p.sku)
                p.model_name = full_model
                _replace_series_in_name(p, original_series, series)
