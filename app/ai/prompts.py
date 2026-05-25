"""Prompts for Gemini description generation.

HTML format: BaseLinker "jumi" template — div.wiersz / div.tekst / div.img
"""
from __future__ import annotations

import re
from app.parser.normalizer import Product

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

    # Clean original description for context
    orig = re.sub(r"<[^>]+>", " ", product.description or "")
    orig = re.sub(r"\s+", " ", orig).strip()[:1200]

    n_images = len(images)

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

    # Row 5: contents/composition + img 4 (if available)
    if n_images >= 5:
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

INSTRUKCJA:
Wypełnij poniższy HTML — zastąp wszystkie komentarze <!-- ... --> rzeczywistą treścią.
Zachowaj WSZYSTKIE tagi HTML i URL-e zdjęć DOKŁADNIE tak jak są — nie modyfikuj src ani klas.
Nagłówki h2: krótkie, WIELKIE LITERY, opisują korzyść kupującego.
Specyfikacja: uzupełnij o parametry z opisu oryginalnego (materiał, kolor, pojemność itp.).

HTML DO WYPEŁNIENIA:
{skeleton}
"""
