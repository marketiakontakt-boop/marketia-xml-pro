"""Prompts for Gemini description generation.

HTML format: BaseLinker "jumi" template — div.wiersz / div.tekst / div.img

v1 (legacy): Gemini fills a full HTML skeleton → sometimes loses structure.
v2 (current): Gemini returns JSON sections → we assemble HTML (always correct).
"""
from __future__ import annotations

import json as _json
import re
from app.parser.normalizer import Product

# Brand-specific copy instructions injected into every description prompt.
_BRAND_COPY_HINTS: dict[str, str] = {
    "villago": """\
WSKAZÓWKI VILLAGO (meble do wnętrz):
- Zastosowanie: "do jadalni", "do salonu", "do kuchni"
- Materiał: dokładna faktura tkaniny/drewna/metalu
- Styl: "skandynawski", "nowoczesny", "glamour", "loft"
- Wymiary: szerokość × głębokość × wysokość w cm
""",
    "gardenstein": """\
WSKAZÓWKI GARDENSTEIN (meble ogrodowe/ogrody):
- SKŁAD ZESTAWU obowiązkowo: każdy element osobno z wymiarami (sofa SZ×GŁ×WYS, fotele, stolik)
- Technorattan: "odporny na UV, deszcz i mróz — nie nasiąka, nie pęka"
- Stelaż: "aluminium anodowane" lub "stal malowana proszkowo"
- Poduszki: "zdejmowane pokrowce, pranie w pralce 30°C" (jeśli są)
- Przeznaczenie min. 2 synonimy: "taras", "ogród", "balkon"
- Klucz. frazy SEO: meble ogrodowe technorattan, zestaw mebli ogrodowych
""",
    "intex": """\
WSKAZÓWKI INTEX (baseny/akcesoria):
- Wymiary ZAWSZE: Ø×H lub DŁ×SZ×WYS cm + pojemność w litrach (np. "16 805 l")
- Certyfikat CE obowiązkowo w opisie
- Materiał ścian: "trójwarstwowe PVC"
- Rama: "stalowa malowana proszkowo, antykorozyjna"
- Skład zestawu: lista każdego elementu osobno (łącznie Xw1)
- Czas montażu: "ok. 45 min bez narzędzi"
- Używaj "Model 2026" — sygnał świeżości ważny na Allegro
- Klucz. frazy SEO: basen stelażowy, basen ogrodowy rodzinny
""",
    "hopla_toys": """\
WSKAZÓWKI HOPLA TOYS (zabawki/hulajnogi/rowery biegowe):
- Certyfikaty OBOWIĄZKOWO: EN 71 (zabawki) lub EN 14619 (hulajnogi) + CE
- Wiek: "od X lat" lub "od X miesięcy"
- Maks. waga użytkownika: "do X kg"
- Materiał: "aluminium"/"stal" + "wolne od BPA", "bez ftalatów"
- Koła: rozmiar mm + materiał (PU/kauczuk) + łożyska (ABEC-7/ABEC-9)
- Regulacja kierownicy: zakres X–X cm
- Dodaj "polska instrukcja obsługi" — buduje zaufanie rodziców
- Klucz. frazy SEO: certyfikat EN 71, hulajnoga dla dzieci, aluminium ABEC-7
""",
    "marketia_home": """\
WSKAZÓWKI MARKETIA HOME (akcesoria domowe/fitness):
- Zastosowanie: "do łazienki", "do kuchni", "do domu"
- Materiał + trwałość: powłoka antykorozyjna, ABS, stal nierdzewna
- Konkretny problem który rozwiązuje produkt
""",
    "zoovera": """\
WSKAZÓWKI ZOOVERA (akcesoria dla zwierząt):
- Gatunek i rozmiar: "dla psów małych/dużych ras", "dla kotów"
- Materiał: bezpieczny, łatwy do czyszczenia
- Wymiary dopasowane do zwierzęcia
""",
}

_SET_KEYWORDS = frozenset(["zestaw", "komplet", "set", "combo"])
_SET_BRANDS = frozenset(["gardenstein", "intex"])


def _is_set_product(product: Product) -> bool:
    name_lower = (product.name or "").lower()
    if any(kw in name_lower for kw in _SET_KEYWORDS):
        return True
    return product.brand in _SET_BRANDS


# ── v2: JSON-based generation ─────────────────────────────────────────────────

SYSTEM_PROMPT_JSON = """\
Jesteś copywriterem e-commerce specjalizującym się w opisach produktów na Allegro i BaseLinker.
Styl: benefit-selling — każda cecha = konkretna korzyść dla kupującego.

ZASADY TREŚCI:
- Cecha → Korzyść: NIGDY "Produkt ma kółka". ZAWSZE "Kółka pozwalają przestawić leżak jedną ręką".
- Konkretne liczby z opisu oryginalnego (waga, wymiary, materiał, pojemność).
- <b>...</b> dozwolone wewnątrz text i intro — na kluczowych parametrach i korzyściach.
- Nagłówki: WIELKIE LITERY, 2-6 słów, opisują KORZYŚĆ kupującego (nie cechę).
- Polskie znaki i ortografia.

FORMAT ODPOWIEDZI — WYŁĄCZNIE JSON, bez żadnego tekstu poza JSON:
{
  "section_1": {"heading": "NAGŁÓWEK CAPS", "text": "akapit 3-4 zdania"},
  "section_2": {"heading": "NAGŁÓWEK CAPS", "text": "akapit 2-3 zdania"},
  "section_3": {"heading": "NAGŁÓWEK CAPS", "text": "akapit 2-3 zdania"},
  "section_4": {"heading": "NAGŁÓWEK CAPS", "text": "akapit 2-3 zdania"},
  "section_5": {"heading": "NAGŁÓWEK CAPS", "text": "akapit 2-3 zdania"},
  "section_6": {"heading": "NAGŁÓWEK CAPS", "text": "akapit 2-3 zdania"},
  "section_7": {"heading": "NAGŁÓWEK CAPS", "intro": "jedno zdanie zachęty", "spec_rows": [["Parametr", "Wartość"], ...]}
}

Zawsze dokładnie 7 kluczy: section_1 … section_7. Bez żadnych innych kluczy ani tekstu.
"""


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


# Attribute keys stripped from AI prompts and XML export.
# Note: after BrandMapper.sanitize_manufacturer_names(), keys like "Producent"
# already have the own brand display name as value — but we still strip them
# from the AI prompt since brand_display is already stated explicitly there.
_SUPPLIER_ATTR_KEYS: frozenset[str] = frozenset({
    "producent", "producer", "manufacturer", "marka producenta", "brand",
    "dostawca", "supplier", "vendor", "dystrybutor", "distributor",
    "country of origin", "kraj pochodzenia", "origin", "pochodzenie",
    "model producenta", "part number", "mpn", "numer katalogowy producenta",
    "import",
})


def _filter_supplier_attrs(attributes: dict) -> dict:
    """Return a copy of attributes with supplier-identifying keys removed."""
    return {k: v for k, v in attributes.items() if k.lower() not in _SUPPLIER_ATTR_KEYS}


def _spec_items(product: Product, brand_display: str) -> list[str]:
    """Build spec <li> HTML strings from product fields and attributes."""
    items: list[str] = []
    if product.width and product.width > 0:
        items.append(f"<b>Szerokość:</b> {_fmt(product.width)} cm")
    if product.height and product.height > 0:
        items.append(f"<b>Wysokość:</b> {_fmt(product.height)} cm")
    if product.length and product.length > 0:
        items.append(f"<b>Głębokość:</b> {_fmt(product.length)} cm")
    if product.weight and product.weight > 0:
        items.append(f"<b>Waga:</b> {_fmt(product.weight)} kg")
    if product.ean:
        items.append(f"<b>Kod produktu:</b> {product.ean}")
    items.append(f"<b>Marka:</b> {brand_display}")
    used = {"szerokość", "wysokość", "głębokość", "waga", "marka", "kod produktu"}
    clean_attrs = _filter_supplier_attrs(product.attributes or {})
    for k, v in clean_attrs.items():
        if k.lower() not in used:
            items.append(f"<b>{k}:</b> {v}")
    return items


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from Gemini response, handles code fences and stray text."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return _json.loads(text[start:end])
    except _json.JSONDecodeError:
        return None


def build_description_prompt_v2(
    product: Product,
    brand_info: dict,
    brand_key: str,
) -> str:
    """v2 prompt: AI returns 7-section JSON, caller assembles HTML.

    Images are NOT included — assemble_html_from_json injects src="1"…"7".
    """
    brand_display = brand_info.get("name", "").upper()
    tagline = brand_info.get("tagline", "")

    orig = re.sub(r"<[^>]+>", " ", product.description or "")
    orig = re.sub(r"\s+", " ", orig).strip()[:1200]

    attrs_block = ""
    clean_attrs = _filter_supplier_attrs(product.attributes or {})
    if clean_attrs:
        lines = "\n".join(f"• {k}: {v}" for k, v in clean_attrs.items())
        attrs_block = f"\nZNANE PARAMETRY PRODUKTU:\n{lines}\n"

    brand_hints = _BRAND_COPY_HINTS.get(brand_key, "")
    brand_hints_block = f"\n{brand_hints}" if brand_hints else ""

    tagline_part = f' i tagline "{tagline}"' if tagline else ""
    guide = (
        "WYMAGANE 7 SEKCJI — dokładnie te klucze JSON:\n\n"
        "section_1 — WSTEP + HOOK + TAGLINE MARKI\n"
        "  heading: slogan marketingowy (3-6 slow, CAPS)\n"
        f"  text: 3-4 zdania. Zacznij od emocjonalnego haka (Wyobraz sobie...). "
        f"Wspomnij marke {brand_display}{tagline_part}."
        " <b> na kluczowych pojeciach.\n\n"
        "section_2 — MATERIALY I ESTETYKA\n"
        "  heading: cecha materialu jako korzysc (CAPS)\n"
        "  text: 2-3 zdania. Material, kolor, estetyka, odpornosc. <b> na materiale i kolorze.\n\n"
        "section_3 — FUNKCJONALNOSC I WIELOFUNKCYJNOSC\n"
        "  heading: co produkt umozliwia (CAPS)\n"
        "  text: 2-3 zdania. Sklad zestawu lub zastosowania z liczbami (X elementow, Y osob, Z cm). <b> na liczbach.\n\n"
        "section_4 — KONSTRUKCJA I STABILNOSC\n"
        "  heading: trwalosc i solidnosc (CAPS)\n"
        "  text: 2-3 zdania. Material stelaza, maks. obciazenie, waga. <b> na parametrach technicznych.\n\n"
        "section_5 — ERGONOMIA I WYGODA\n"
        "  heading: komfort uzytkowania (CAPS)\n"
        "  text: 2-3 zdania. Poduszki, regulacje, ergonomia, detale uzytkowe. <b> na cechach.\n\n"
        "section_6 — LOGISTYKA, CZYSZCZENIE I MONTAZ\n"
        "  heading: latwosc obslugi (CAPS)\n"
        "  text: 2-3 zdania. Czyszczenie, konserwacja, czas montazu, pakowanie. <b> na waznych slowach.\n\n"
        f"section_7 — SPECYFIKACJA TECHNICZNA\n"
        f'  heading: "Dane techniczne {brand_display} [nazwa modelu z tytulu]"\n'
        f"  intro: jedno zdanie zachety, np. Postaw na jakosc marki {brand_display}.\n"
        '  spec_rows: tablica par ["Parametr", "Wartosc"] — wszystkie wymiary, materialy, '
        "kolory, wagi, obciazenia z opisu oryginalnego (min. 5 wierszy).\n"
    )

    return (
        "Napisz opis produktu BaseLinker/Allegro. Odpowiedź: WYŁĄCZNIE JSON.\n\n"
        f"MARKA WŁASNA: {brand_display}\n"
        f"ZAKAZ: Nigdy nie używaj nazw producenta, dostawcy ani marki z oryginalnego opisu. "
        f"Jedyna marka w opisie to {brand_display}.\n\n"
        f"PRODUKT:\n"
        f"Tytuł: {product.title or product.name}\n"
        f"Kategoria: {product.category_name or '—'}\n"
        f"Oryginał (kontekst — używaj tylko wymiarów/materiałów, ignoruj nazwy marek): {orig}\n"
        f"{attrs_block}{brand_hints_block}\n"
        f"{guide}"
    )


_COLOR_WARNING = (
    '<b>Uwaga: kolor produktu na zdjęciach może nieznacznie różnić się od '
    'rzeczywistości w zależności od ustawień monitora.</b>'
)

_SECTION_COMMENTS = [
    "SEKCJA 1: WSTĘP I KONCEPCJA DESIGNU",
    "SEKCJA 2: MATERIAŁ I ESTETYKA",
    "SEKCJA 3: FUNKCJONALNOŚĆ I WIELOFUNKCYJNOŚĆ",
    "SEKCJA 4: KONSTRUKCJA I STABILNOŚĆ",
    "SEKCJA 5: ERGONOMIA I WYGODA",
    "SEKCJA 6: LOGISTYKA, CZYSZCZENIE I MONTAŻ",
    "SEKCJA 7: SPECYFIKACJA TECHNICZNA",
]


def _render_section(
    n: int,
    comment: str,
    heading: str,
    content_html: str,
    img_url: str = "",
) -> str:
    """Render one section with alternating text/image layout."""
    text_block = (
        f'    <div class="item item-6">\n'
        f'        <section class="text-item">\n'
        f'            <h2>{heading}</h2>\n'
        f'            {content_html}\n'
        f'        </section>\n'
        f'    </div>'
    )
    img_block = (
        f'    <div class="item item-6">\n'
        f'        <section class="image-item">\n'
        f'            <img src="{img_url}">\n'
        f'        </section>\n'
        f'    </div>'
    )
    # Odd sections: text left, image right. Even: image left, text right.
    inner = f"{text_block}\n{img_block}" if n % 2 == 1 else f"{img_block}\n{text_block}"
    return (
        f'<!-- {comment} -->\n'
        f'<section class="section">\n'
        f'{inner}\n'
        f'</section>'
    )


def _merge_spec(spec_items: list[str], ai_rows: list) -> str:
    """Merge pre-filled spec_items with AI spec_rows, deduped by key."""
    merged = list(spec_items)
    used: set[str] = set()
    for li in spec_items:
        m = re.match(r"<b>([^<:]+):", li)
        if m:
            used.add(m.group(1).lower())
    for row in ai_rows:
        if len(row) == 2:
            key, val = str(row[0]), str(row[1])
            if key.lower() not in used:
                merged.append(f"<b>{key}:</b> {val}")
                used.add(key.lower())
    li_rows = "\n                ".join(f"<li>{item}</li>" for item in merged)
    return f"<ul>\n                {li_rows}\n            </ul>"


def assemble_html_from_json(
    data: dict,
    images: list[str],
    spec_items: list[str],
) -> str:
    """Assemble 7-section HTML from AI JSON.

    New format (section_1 … section_7): renders section.section / div.item-6
    grid with alternating layout. Image URLs taken directly from `images`.
    Legacy format (sections list): kept for backward-compat.
    """
    if data.get("section_1"):
        return _assemble_7section(data, images, spec_items)
    return _assemble_html_legacy(data, images, spec_items)


def _img_url(images: list[str], n: int) -> str:
    """Return image URL for 1-based section index n, clamping to last available."""
    if not images:
        return ""
    idx = max(0, min(n - 1, len(images) - 1))
    return images[idx]


def _assemble_7section(data: dict, images: list[str], spec_items: list[str]) -> str:
    parts: list[str] = []

    for n in range(1, 7):
        key = f"section_{n}"
        sec = data.get(key) or {}
        heading = sec.get("heading", f"SEKCJA {n}")
        text = sec.get("text", "")
        content = f"<p>{text}</p>"
        if n == 6:
            content = f"<p>{text}<br>{_COLOR_WARNING}</p>"
        parts.append(_render_section(n, _SECTION_COMMENTS[n - 1], heading, content, _img_url(images, n)))

    # Section 7: spec
    sec7 = data.get("section_7") or {}
    heading7 = sec7.get("heading", "DANE TECHNICZNE")
    intro7 = sec7.get("intro", "")
    spec_html = _merge_spec(spec_items, sec7.get("spec_rows", []))
    content7 = (f"<p>{intro7}</p>\n            " if intro7 else "") + spec_html
    parts.append(_render_section(7, _SECTION_COMMENTS[6], heading7, content7, _img_url(images, 7)))

    return "\n\n".join(parts)


def _assemble_html_legacy(
    data: dict,
    images: list[str],
    spec_items: list[str],
) -> str:
    """Legacy jumi-format renderer for old sections-list JSON."""

    def _img(idx: int) -> str:
        if not images:
            return ""
        return images[idx] if idx < len(images) else images[-1]

    sections = data.get("sections", [])
    html_parts: list[str] = []
    img_idx = 0

    for sec in sections:
        sec_type = sec.get("type", "paragraph")
        heading = sec.get("heading", "")

        if sec_type == "bullets":
            li_rows = "\n".join(f"<li>{item}</li>" for item in sec.get("items", []))
            content = f"<ul>\n{li_rows}\n</ul>"
        elif sec_type == "paragraph":
            content = f"<p>{sec.get('text', '')}</p>"
        elif sec_type == "spec":
            heading = heading or "SPECYFIKACJA:"
            merged = list(spec_items)
            used_keys: set[str] = set()
            for li in spec_items:
                m = re.match(r"<b>([^<:]+):", li)
                if m:
                    used_keys.add(m.group(1).lower())
            for row in sec.get("rows", []):
                if len(row) == 2:
                    key, val = str(row[0]), str(row[1])
                    if key.lower() not in used_keys:
                        merged.append(f"<b>{key}:</b> {val}")
                        used_keys.add(key.lower())
            li_rows = "\n".join(f"<li>{item}</li>" for item in merged)
            content = f"<ul>\n{li_rows}\n</ul>"
        else:
            continue

        html_parts.append(
            f'<div class="wiersz">\n'
            f'<div class="tekst">\n'
            f'<h2>{heading}</h2>\n'
            f'{content}\n'
            f'</div>\n'
            f'<div class="img"><img src="{_img(img_idx)}"></div>\n'
            f'</div>'
        )
        img_idx += 1

    return "\n\n".join(html_parts)


# ── v1: legacy HTML-skeleton generation (kept as fallback) ────────────────────

SYSTEM_PROMPT = """\
Jesteś copywriterem e-commerce specjalizującym się w opisach produktów na Allegro i BaseLinker.
Piszesz w stylu benefit-selling: każda cecha produktu = konkretna korzyść dla kupującego.

ZASADY:
- Cecha → Korzyść: NIGDY "Produkt ma kółka". ZAWSZE "Kółka pozwalają przestawić leżak jedną ręką".
- Konkretne liczby z opisu oryginałnego (waga, wymiary, materiał, pojemność).
- Bold (<b>) dla kluczowych parametrów i korzyści.
- Nagłówki h2: krótkie, WIELKIE LITERY, opisują korzyść (nie cechę).
- Polskie znaki i ortografia.
- Zwróć WYŁĄCZNIE HTML — bez XML, bez CDATA, bez markdown, bez komentarzy XML.
"""


def build_description_prompt(
    product: Product,
    brand_info: dict,
    brand_key: str,
) -> str:
    images = product.images or []

    def img(i: int) -> str:
        if i < len(images):
            return images[i]
        return images[0] if images else ""

    brand_display = brand_info.get("name", "").upper()

    # Spec rows pre-filled from product attributes
    spec_parts = []
    if product.width and product.width > 0:
        spec_parts.append(f"<b>Szerokość:</b> {product.width} cm")
    if product.height and product.height > 0:
        spec_parts.append(f"<b>Wysokość:</b> {product.height} cm")
    if product.length and product.length > 0:
        spec_parts.append(f"<b>Głębokość:</b> {product.length} cm")
    if product.weight and product.weight > 0:
        spec_parts.append(f"<b>Waga:</b> {product.weight} kg")
    if product.ean:
        spec_parts.append(f"<b>Kod produktu:</b> {product.ean}")
    spec_parts.append(f"<b>Marka:</b> {brand_display}")

    # Inject extracted attributes (skip keys already in spec_parts)
    _spec_keys_used = {"szerokość", "wysokość", "głębokość", "waga", "marka", "kod produktu"}
    for attr_name, attr_val in (product.attributes or {}).items():
        if attr_name.lower() not in _spec_keys_used:
            spec_parts.append(f"<b>{attr_name}:</b> {attr_val}")

    # Block injected before skeleton so Gemini can reference throughout description
    _attrs_block = ""
    if product.attributes:
        lines = "\n".join(f"• {k}: {v}" for k, v in product.attributes.items())
        _attrs_block = f"\nZNANE PARAMETRY PRODUKTU (uwzględnij w opisie i specyfikacji):\n{lines}\n"

    # Clean original description for context
    orig = re.sub(r"<[^>]+>", " ", product.description or "")
    orig = re.sub(r"\s+", " ", orig).strip()[:1200]

    n_images = len(images)

    # Brand-specific copy hints
    brand_hints = _BRAND_COPY_HINTS.get(brand_key, "")
    brand_hints_block = f"\n{brand_hints}" if brand_hints else ""

    # ── HTML skeleton: real URLs embedded, <!-- --> = instructions for Gemini ──
    # Row 1: bullet highlights + img 0
    skeleton = f"""<div class="wiersz">
<div class="tekst">
<h2><!-- KRÓTKI NAGŁÓWEK: 3-6 słów, CAPS, marketingowa nazwa produktu (nie pełny tytuł SEO) --></h2>
<!-- Napisz listę 5-7 bulletów: kluczowe cechy → korzyści, każda cecha z <b> na ważnych słowach. Ostatni bullet: "Produkt marki <b>{brand_display}</b>" -->
</div>
<div class="img"><img src="{img(0)}"></div>
</div>"""

    # Row 2: hook paragraph + img 1
    skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2><!-- NAGŁÓWEK: główna korzyść dla kupującego (2-3 słowa, caps) --></h2>
<p><!-- Hook: 2-3 zdania opisujące co produkt daje w codziennym życiu. Konkretnie, bez banałów. <b> na key phrases. --></p>
</div>
<div class="img"><img src="{img(1)}"></div>
</div>"""

    # Row 3: second benefit + img 2
    skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2><!-- NAGŁÓWEK: druga ważna korzyść (caps) --></h2>
<p><!-- 2-3 zdania rozwinięcia. Użyj liczb i konkretów z opisu oryginalnego. <b> na key phrases. --></p>
</div>
<div class="img"><img src="{img(2)}"></div>
</div>"""

    # Row 4: third benefit + img 3
    skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2><!-- NAGŁÓWEK: trzecia korzyść lub zastosowanie (caps) --></h2>
<p><!-- 2-3 zdania. Wymień co konkretnie otrzymuje kupujący, jak używa produktu. <b> na key phrases. --></p>
</div>
<div class="img"><img src="{img(3)}"></div>
</div>"""

    # Row 5: contents/composition + img 4 (if available or set product)
    if n_images >= 5 or _is_set_product(product):
        skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2><!-- SKŁAD ZESTAWU lub DODATKOWE CECHY — nagłówek caps --></h2>
<!-- Lista 4-6 bulletów: co wchodzi w skład zestawu lub dodatkowe cechy produktu. <b> na nazwach elementów/parametrów. -->
</div>
<div class="img"><img src="{img(4)}"></div>
</div>"""

    # Row 6: extra benefit + img 5 (if available)
    if n_images >= 6:
        skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2><!-- NAGŁÓWEK: czwarta korzyść / materiał / trwałość (caps) --></h2>
<p><!-- 2-3 zdania o materiale, trwałości lub gwarancji. <b> na key phrases. --></p>
</div>
<div class="img"><img src="{img(5)}"></div>
</div>"""

    # Extra image rows for products with many photos
    if n_images >= 8:
        skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2><!-- NAGŁÓWEK: piąta korzyść lub Q&A lub podsumowanie (caps) --></h2>
<p><!-- 2-3 zdania. Możesz tu umieścić najczęstsze pytania/odpowiedzi lub podsumowanie wartości produktu. --></p>
</div>
<div class="img"><img src="{img(6)}"></div>
</div>"""

    # Safety section for toys and pools (mandatory per Allegro research)
    if brand_key in ("hopla_toys", "intex"):
        safety_img = img(5) if n_images >= 6 else img(min(n_images - 1, 4))
        if brand_key == "hopla_toys":
            safety_hint = (
                "Wymień certyfikaty EN 71 i CE. "
                "Podaj wiek (od X lat), maks. wagę (do X kg), "
                "info o materiałach wolnych od BPA i bez ftalatów."
            )
        else:
            safety_hint = (
                "Wymień certyfikat CE. "
                "Podaj pojemność w litrach, materiał ścian (trójwarstwowe PVC), "
                "antykorozyjność ramy stalowej."
            )
        skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2>BEZPIECZEŃSTWO I CERTYFIKATY:</h2>
<!-- {safety_hint}
Format: <ul> z <li> dla każdego certyfikatu i parametru bezpieczeństwa. -->
</div>
<div class="img"><img src="{safety_img}"></div>
</div>"""

    # Spec section always last
    spec_li = "\n".join(f"<li>{s}</li>" for s in spec_parts)
    last_img = img(min(n_images - 1, 9))
    skeleton += f"""

<div class="wiersz">
<div class="tekst">
<h2>SPECYFIKACJA:</h2>
<ul>
{spec_li}
<!-- Dodaj 3-5 parametrów wyciągniętych z opisu oryginalnego: materiał, kolor, pojemność, wymiary itp. Format: <li><b>Parametr:</b> wartość</li> -->
</ul>
</div>
<div class="img"><img src="{last_img}"></div>
</div>"""

    return f"""Napisz opis produktu BaseLinker/Allegro.

PRODUKT:
Tytuł: {product.title or product.name}
Kategoria: {product.category_name or '—'}
Oryginał (kontekst): {orig}
{_attrs_block}{brand_hints_block}
INSTRUKCJA:
Wypełnij poniższy HTML — zastąp wszystkie komentarze <!-- ... --> rzeczywistą treścią.
Zachowaj WSZYSTKIE tagi HTML i URL-e zdjęć DOKŁADNIE tak jak są — nie modyfikuj src ani klas.
Nagłówki h2: krótkie, WIELKIE LITERY, opisują korzyść kupującego.
Specyfikacja: uzupełnij o parametry z opisu oryginalnego (materiał, kolor, pojemność itp.).

HTML DO WYPEŁNIENIA:
{skeleton}
"""
