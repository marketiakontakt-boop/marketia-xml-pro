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
# Brands that do NOT use real collection names — pool names are internal grouping only.
# For these brands: product.name is preserved as-is from the supplier (no pool_name substitution),
# and model_name stays empty so titles are built purely from the supplier description.
SKIP_MODEL_RENAME_BRANDS: frozenset[str] = frozenset({
    "intex",
    "hopla_toys",
    "marketia_home",
    "lifekraft",
    "zoovera",
})

_CONS = list("bdfgklmnprst")   # no v/z/x — softer, more name-friendly in Polish
_VOWELS = list("aeiou")
_CODAS = ["", "l", "n", "r"]    # short coda, no "s" at end (feels like abbreviation)


_PRONOUNCEABLE_BLOCKLIST: frozenset[str] = frozenset({
    "dupa", "pupa", "kupa", "suka", "baba", "pipa", "wino", "bida",
})


def _random_pronounceable() -> str:
    """Generate a 4-5 letter name using CV syllable patterns.

    Produces names like: Balon, Kenis, Toral, Mesin, Delos — easy to read aloud in Polish.
    Avoids double letters, harsh consonant clusters, and Polish profanity.
    """
    while True:
        c1 = random.choice(_CONS).upper()
        v1 = random.choice(_VOWELS)
        c2 = random.choice([c for c in _CONS if c != c1.lower()])
        v2 = random.choice([v for v in _VOWELS if v != v1])
        coda = random.choice(_CODAS)
        name = c1 + v1 + c2 + v2 + coda
        if name.lower() not in _PRONOUNCEABLE_BLOCKLIST:
            return name

# Words indicating material/fabric — excluded from series key selection.
# Present in many products but describe variant, not the collection.
_MATERIAL_WORDS: frozenset[str] = frozenset({
    "ekoskóra", "welur", "aksamit", "velvet", "tkanina", "skóra",
    "rattan", "technorattan", "aluminium", "metal", "plastik",
    "drewno", "bambus", "bambusz", "mikrofibra", "poliester",
    "nylon", "bawełna", "len", "sztuczna",
})

# Common functional adjectives in furniture names — not model identifiers.
_STYLE_ADJECTIVES: frozenset[str] = frozenset({
    "obrotowe", "obrotowy", "obrotowa",
    "regulowane", "regulowany", "regulowana",
    "pikowane", "pikowany", "pikowana",
    "składane", "składany", "składana",
    "rozkładane", "rozkładany", "rozkładana",
    "barowe", "barowy", "barowa",
    "biurowe", "biurowy", "biurowa",
    "ogrodowe", "ogrodowy", "ogrodowa",
    "dziecięce", "dziecięcy", "dziecięca",
    "nowoczesne", "nowoczesny", "nowoczesna",
    "skandynawskie", "skandynawski", "skandynawska",
})

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
    "turkusowy", "turkusowe", "turkusowa",
    "ciemny", "ciemne", "ciemna",
    "jasny", "jasne", "jasna",
    "khaki", "beige", "ecru",
    # Brakujące kolory mebli (XML dropshipping)
    "musztardowy", "musztardowe", "musztardowa",
    "kawowy", "kawowe", "kawowa",
    "naturalny", "naturalne", "naturalna",
    "oliwkowy", "oliwkowe", "oliwkowa",
    "miętowy", "miętowe", "miętowa",
    "miodowy", "miodowe", "miodowa",
    "piaskowy", "piaskowe", "piaskowa",
    "antracytowy", "antracytowe", "antracytowa", "antracyt",
    "grafitowy", "grafitowe", "grafitowa", "grafit",
    "błękitny", "błękitne", "błękitna",
    "dębowy", "dębowe", "dębowa", "dąb",
    "bukowy", "bukowe", "bukowa", "buk",
    "orzechowy", "orzechowe", "orzechowa", "orzech",
    "sosnowy", "sosnowe", "sosnowa", "sosna",
    "cappuccino", "espresso", "mocca",
    "drewno", "drewniany", "drewniane", "drewniana",
    "transparent", "transparentny", "przezroczysty", "przezroczyste",
    # Adverbial compound forms — used in "biało/czarne", "czarno-szare", "srebrno-złote"
    "biało", "czarno", "szaro", "brązowo", "beżowo", "granatowo", "niebiesko",
    "czerwono", "zielono", "żółto", "różowo", "srebrno", "złoto", "kremowo",
    "fioletowo", "bordowo", "ciemno", "jasno",
    # Compound Polish colors (ciemno/jasno + color — single word without hyphen)
    "ciemnoszary", "ciemnoszare", "ciemnoszara",
    "ciemnobrązowy", "ciemnobrązowe", "ciemnobrązowa",
    "ciemnoniebieskie", "ciemnoniebieska", "ciemnoniebieski",
    "ciemnozielony", "ciemnozielone", "ciemnozielona",
    "ciemnogranatowy", "ciemnogranatowe",
    "jasnoszary", "jasnoszare", "jasnoszara",
    "jasnobrzązowy", "jasnobrzązowe",
    "jasnoniebieskie", "jasnoniebieska",
    "jasnozielony", "jasnozielone",
    "jasnobezowy", "jasnobezowe",
    # English colors
    "black", "white", "grey", "gray", "dark", "light",
    "brown", "green", "blue", "red", "yellow", "silver", "gold", "pink",
    "navy", "mint", "mustard", "cream", "natural", "oak", "walnut", "beech",
    # Size letters intentionally excluded: m, l, s, xl, xxl, xs etc.
    # This catalog (furniture, toys, pools) uses M/L/XL/XXL as measurement units
    # ("3X3 M", "1.8 L", "kuchnia XXL"), not clothing size variants.
    # Including them causes the measurement unit to be grabbed as the color suffix,
    # making all color variants collapse to the same model name ("Bari M").
})

# Tokens treated as noise — stripped before variant detection.
# Supplier names sometimes carry channel tags like "(ikeabox)" or "(allegro)"
# which would otherwise be glued to the series name and break grouping.
_NOISE_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")

# Hurtmeblowy SKU pattern: model_NNNNN_V-MODELNAME-CODE
# e.g. "model_3544_1-AVOLA-DORY21" → "avola"
_HM_SKU_RE = re.compile(r"^model_\d+_\d+-([A-Za-z]{3,})-", re.IGNORECASE)


def _series_from_sku(sku: str) -> str | None:
    """Extract supplier model name from hurtmeblowy SKU. Returns lowercase or None."""
    m = _HM_SKU_RE.match(sku)
    return m.group(1).lower() if m else None


def _is_compound_color(token: str) -> bool:
    """True if `token` is a slash/dash-joined compound where every part is a known color.

    Examples that should match:
        'biało/czarne', 'czarno-szare', 'czarne/czarne', 'biało-czarne'
    Mixed-case is normalised. Single-color tokens are NOT compounds — caller already
    checks `_VARIANT_WORDS` directly for those.
    """
    parts = re.split(r"[-/]", token.strip().lower())
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return False
    return all(p in _VARIANT_WORDS for p in parts)


def _is_variant(token: str) -> bool:
    """Treat a token as a colour/size variant if it's in the set OR a compound colour."""
    low = token.strip().lower()
    return low in _VARIANT_WORDS or _is_compound_color(low)

# Generic furniture / category words stripped when finding the clean series name
_FURNITURE_WORDS: frozenset[str] = frozenset({
    # Meble
    "krzesło", "krzesła", "krzeseł", "fotel", "fotele", "foteli",
    "sofa", "sofą", "sofy", "kanapa", "kanapy", "stół", "stolik", "stolika",
    "ława", "ławka", "ławki", "łóżko", "regał", "komoda", "szafka", "szafki",
    "szafa", "leżak", "huśtawka", "altana", "pergola", "biurko",
    "zestaw", "meble", "mebel", "mebli", "narożnik", "narożna",
    "hoker", "taboret", "bujak", "szezlong", "wieszak", "pufa",
    # Ogród / outdoor
    "trampolina", "trampoliny", "trampolinek", "trampolinka",
    "parasol", "altanka", "donica", "doniczka", "hamak", "hamaki",
    "basen", "baseny", "leżaki", "taczka", "kosiarka",
    # Zabawki / dzieci
    "hulajnoga", "hulajnogi", "hulajnóg",
    "rower", "rowery", "rowerek", "rowerki",
    "zabawka", "zabawki", "lalka", "lalki",
    "klocki", "puzzle", "namiot", "namioty",
    "piłka", "piłki", "domek", "domki",
    "drabinka", "drabinki", "zjeżdżalnia", "zjeżdżalnie",
    "karuzela", "karuzelka", "jeździk", "chodzik", "pchacz",
    "skakanka", "grzyb", "piaskownica",
    # Dom / home
    "lampa", "lampka", "kinkiet", "żyrandol",
    "lustro", "lustra", "dywan", "dywany", "chodnik",
    "organizer", "dozownik", "pojemnik", "kosz", "koszyk",
    "wieszaki", "haczyk", "haczyki", "dekoracja",
    # Kuchnia
    "czajnik", "garnek", "garnki", "patelnia", "patelnie",
    "blender", "toster", "ekspres", "mikser", "sokowirówka", "frytkownica",
    # Elektro
    "odkurzacz", "wentylator", "oczyszczacz", "nawilżacz", "grzejnik",
    "żelazko", "lokówka", "prostownica",
    # Zwierzęta
    "legowisko", "klatka", "transporter", "miska", "miski",
    "drapak", "budka", "obroża", "smycz",
    # Inne typy produktów
    "wózek", "hulajnogi", "mata", "pojemniki", "komplet",
})


def _strip_noise(name: str) -> str:
    """Remove channel tags in parens ('(ikeabox)', '(allegro)') so they don't pollute the series key."""
    return _NOISE_PAREN_RE.sub(" ", name).strip()


def _strip_variant_words(name: str) -> str:
    """Remove ALL color/size words (including compound slash/dash colors) from name."""
    cleaned = _strip_noise(name)
    words = cleaned.strip().lower().split()
    return " ".join(w for w in words if not _is_variant(w))


def _strip_series_noise(name: str) -> str:
    """Remove colors, materials, and style adjectives — leaves series-candidate words."""
    cleaned = _strip_noise(name)
    words = cleaned.strip().lower().split()
    return " ".join(
        w for w in words
        if not _is_variant(w)
        and w not in _MATERIAL_WORDS
        and w not in _STYLE_ADJECTIVES
    )


def _extract_variant_suffix(name: str) -> str:
    """Return color/size words starting from the first color/compound found (Title Case)."""
    cleaned = _strip_noise(name)
    words = cleaned.strip().split()
    first_idx = next((i for i, w in enumerate(words) if _is_variant(w)), -1)
    if first_idx == -1:
        return ""
    suffix: list[str] = []
    for word in words[first_idx:]:
        if _is_variant(word):
            suffix.append(word.capitalize())
        else:
            break
    return " ".join(suffix)


def _pick_series_word(words: list[str], brand_freq: dict[str, int]) -> str | None:
    """Pick the most frequent non-furniture, non-noise word as the series identifier.

    Most frequent = appears in most products of the same brand = collection name.
    Ties broken alphabetically for determinism.
    """
    candidates = [w for w in words if w not in _FURNITURE_WORDS and len(w) >= 3 and w.isalpha()]
    if not candidates:
        return None
    return max(candidates, key=lambda w: (brand_freq.get(w, 0), w))


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


def _remove_descriptor_from_name(product: Product, word: str) -> None:
    """Remove a descriptor word (sub-model code like 'LERA', 'TAMU') from product.name."""
    if not word or not product.name:
        return
    pat = re.compile(
        _WORD_BOUNDARY + re.escape(word) + r'(?![A-Za-zÀ-ɏ0-9])',
        re.IGNORECASE,
    )
    if pat.search(product.name):
        product.name = re.sub(r'  +', ' ', pat.sub('', product.name)).strip()


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

        sku_series = _series_from_sku(product.sku)
        raw = product.name or product.title or product.sku
        stripped_noise = _strip_series_noise(raw)
        words = [
            w for w in stripped_noise.lower().split()
            if w not in _FURNITURE_WORDS and len(w) >= 3 and w.isalpha()
        ]
        # SKU encodes the series directly — no frequency analysis needed
        if sku_series:
            original_series = sku_series
        else:
            original_series = words[0] if words else _series_from_stripped(stripped_noise.lower())

        prior = get_sku_model_name(self.conn, product.sku)
        if prior:
            product.model_name = prior
            prior_base = prior.split()[0]
            for w in words:
                if w == prior_base.lower():
                    continue
                if sku_series:
                    # Replace the known series word; remove everything else
                    if w == sku_series:
                        _replace_series_in_name(product, w, prior_base)
                    else:
                        _remove_descriptor_from_name(product, w)
                else:
                    replaced = False
                    if not replaced:
                        _replace_series_in_name(product, w, prior_base)
                        replaced = True
                    else:
                        _remove_descriptor_from_name(product, w)
            return prior

        series = self._pick_pool_name(product.brand or "")
        if not series:
            series = original_series or raw.split()[0].capitalize()

        full_model = series  # single-word — color lives in product.name/title, not model_name
        self._save(product.brand or "", full_model, product.sku)
        product.model_name = full_model
        if sku_series:
            for w in words:
                if w == sku_series:
                    _replace_series_in_name(product, w, series)
                else:
                    _remove_descriptor_from_name(product, w)
        else:
            replaced = False
            for w in words:
                if not replaced:
                    _replace_series_in_name(product, w, series)
                    replaced = True
                else:
                    _remove_descriptor_from_name(product, w)
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
                prior_base = prior.split()[0]
                sku_series = _series_from_sku(p.sku)
                raw = p.name or p.title or p.sku
                stripped_noise = _strip_series_noise(raw)
                desc_words = [
                    w for w in stripped_noise.lower().split()
                    if w not in _FURNITURE_WORDS and len(w) >= 3 and w.isalpha()
                ]
                replaced = False
                for w in desc_words:
                    if w == prior_base.lower():
                        continue
                    if sku_series:
                        if w == sku_series:
                            _replace_series_in_name(p, w, prior_base)
                        else:
                            _remove_descriptor_from_name(p, w)
                    else:
                        if not replaced:
                            _replace_series_in_name(p, w, prior_base)
                            replaced = True
                        else:
                            _remove_descriptor_from_name(p, w)
            else:
                to_assign.append(p)

        if not to_assign:
            return

        # Pass 2a: compute per-brand word frequency for series detection.
        # Count how many products (not yet cached) contain each descriptor word.
        # The word appearing in MOST products of a brand = collection name (e.g., "NOTO").
        brand_word_freq: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        product_meta: list[tuple[Product, str, list[str], str | None]] = []  # (product, suffix, words, series_word)

        for p in to_assign:
            brand = p.brand or ""
            raw = p.name or p.title or p.sku
            suffix = _extract_variant_suffix(raw)
            stripped_noise = _strip_series_noise(raw)
            words = [
                w for w in stripped_noise.lower().split()
                if w not in _FURNITURE_WORDS and len(w) >= 3 and w.isalpha()
            ]
            product_meta.append((p, suffix, words, None))  # series_word filled in Pass 2b
            for w in set(words):  # set: count each word once per product
                brand_word_freq[brand][w] += 1

        # Pass 2b: build groups.
        # SKU-encoded model name (hurtmeblowy pattern) takes precedence over frequency analysis.
        # Fallback: frequency-based detection from product names.
        groups: dict[tuple[str, str], list[tuple[Product, str, str | None]]] = defaultdict(list)
        for p, suffix, words, _ in product_meta:
            brand = p.brand or ""
            sku_series = _series_from_sku(p.sku)
            if sku_series:
                series_word = sku_series
            else:
                freq = brand_word_freq[brand]
                series_word = _pick_series_word(words, freq)
            series_key = (brand, series_word) if series_word else (brand, p.sku)
            groups[series_key].append((p, suffix, series_word))

        # Pass 3: assign one pool name per group (shared base across color variants).
        # model_name = "<PoolBase> <ColorSuffix>" so variants are distinct yet share the base.
        # The series base (not the full model_name) is substituted into product.name so the
        # color word in the original name is not duplicated.
        for (brand, _group_key), items in groups.items():
            series = self._pick_pool_name(brand)
            first_series_word = items[0][2]  # frequency-picked word (e.g. "noto") or None

            for p, suffix, _sw in items:
                full_model = series  # single-word — color lives in product.name/title
                self._save(brand, full_model, p.sku)
                p.model_name = full_model
                # All descriptor words for this product (computed before any substitution)
                raw = p.name or p.title or p.sku
                stripped_noise = _strip_series_noise(raw)
                desc_words = [
                    w for w in stripped_noise.lower().split()
                    if w not in _FURNITURE_WORDS and len(w) >= 3 and w.isalpha()
                ]
                # Replace the series word first (e.g. NOTO → Bari), then remove others (LERA, TAMU)
                replaced = False
                for w in desc_words:
                    target = first_series_word if first_series_word else w
                    if w == target:
                        _replace_series_in_name(p, w, series)
                        replaced = True
                    elif replaced or w != target:
                        _remove_descriptor_from_name(p, w)
