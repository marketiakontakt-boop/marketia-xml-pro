# Marketia XML Pro — Audit Preview, Category Mapper, Attribute Injection, Lifestyle Thumbnails

**Date:** 2026-05-26  
**Scope:** 4 features: D (atrybuty → prompt), C (category mapper), F (audit preview), H (lifestyle PNG)

---

## Feature D — Atrybuty → Prompt AI

### Problem
Gemini generuje sekcję Specyfikacja na podstawie tekstu opisu. Po dodaniu `attribute_extractor.py` mamy już wyciągnięte atrybuty w `product.attributes`, ale prompt ich nie wykorzystuje — AI zgaduje wartości zamiast używać znanych danych.

### Rozwiązanie
Wstrzyknij `product.attributes` do prompta przed HTML skeleton.

### Implementacja — `app/ai/prompts.py`

W funkcji `build_description_prompt(product, brand_info, brand_key)` dodaj blok przed HTML skeleton:

```python
if product.attributes:
    lines = [f"• {k}: {v}" for k, v in product.attributes.items()]
    attrs_block = "Znane parametry produktu (uwzględnij w sekcji specyfikacji):\n" + "\n".join(lines)
else:
    attrs_block = ""
```

Inject `attrs_block` do stringa promptu bezpośrednio przed sekcją z HTML skeleton.

### Pliki
- Modify: `app/ai/prompts.py` — `build_description_prompt()` (+10 linii)

### Test
Uruchom regenerację dla produktu z atrybutami, sprawdź że sekcja Specyfikacja zawiera wartości z `product.attributes`.

---

## Feature C — Allegro Category Mapper

### Problem
BaseLinker eksportuje kategorię w formacie swojej własnej taksonomii (np. "Baseny i akcesoria"). Allegro ma swoją taksonomię (np. "Dom i ogród > Basen i spa > Baseny ogrodowe"). Błędna kategoria = niższe pozycje w wyszukiwarce.

### Rozwiązanie
Statyczna mapa JSON (BaseLinker → Allegro) + Gemini fallback dla nieznanych. GUI: kolumna kategorii w tabeli + okno edytora mapy.

### 1. Dane — `data/allegro_categories.json`

Słownik `{baselinker_category: allegro_path}`. Wstępna mapa na podstawie bieżącego XML:

```json
{
  "Baseny i akcesoria basenowe": "Dom i ogród > Basen i spa > Baseny ogrodowe",
  "Altany i namioty ogrodowe": "Dom i ogród > Meble ogrodowe > Altany i pergole",
  "Meble ogrodowe": "Dom i ogród > Meble ogrodowe > Zestawy mebli",
  "Zabawki": "Zabawki > Zabawki dla dzieci",
  "Akcesoria dla zwierząt": "Zwierzęta > Psy > Akcesoria",
  "Akcesoria domowe": "Dom i ogród > Wyposażenie domu"
}
```

### 2. Transformer — `app/transformer/category_mapper.py` (NEW)

```python
def load_category_map() -> dict[str, str]:
    """Load BaseLinker → Allegro category mapping from JSON."""

def map_category(baselinker_cat: str, category_map: dict) -> str | None:
    """Return Allegro path for BaseLinker category, or None if unknown."""

def map_all_products(products: list[Product], category_map: dict) -> None:
    """Set product.allegro_category for all products. Unknown → None."""

def suggest_category_gemini(baselinker_cat: str, client: ClaudeClient) -> str:
    """Ask Gemini to suggest Allegro category path for unknown category."""
```

### 3. Product dataclass — `app/parser/normalizer.py`

Nowe pole w `Product`:
```python
allegro_category: str = ""  # populated by category_mapper
```

### 4. GUI — kolumna w ProductRow

Dodaj kolumnę "KAT" (szerokość 80px) po kolumnie MODEL:
- Zielony chip (corner_radius=4): jeśli `product.allegro_category`
- Pomarańczowy chip "?" : jeśli puste
- `COL_WIDTHS` rozszerzony o 80

### 5. GUI — `CategoryMapperWindow(ctk.CTkToplevel)`

Nowe okno (nie blokujące, 900×600):
- Tabela: BaseLinker kat | Allegro kat (edytowalne CTkEntry) | status chip
- Przycisk "Sugeruj brakujące (AI)" — wywołuje `suggest_category_gemini` dla pustych
- Przycisk "Zapisz mapę" — zapisuje JSON
- Otwierany przyciskiem "Mapa kategorii" w sidebarze

### 6. _transform_worker — wywołanie

W `App._transform_worker()`, po istniejących transformach, dodaj:
```python
from app.transformer.category_mapper import load_category_map, map_all_products
cat_map = load_category_map()
map_all_products(self.products, cat_map)
```

### 7. Eksport — `xml_exporter.py`

W `_product_to_element()`, zmień pole `category_name`:
```python
cat = getattr(p, "allegro_category", "") or p.category_name
add("category_name", cat)
```

### Pliki
- New: `data/allegro_categories.json`
- New: `app/transformer/category_mapper.py`
- Modify: `app/parser/normalizer.py` (nowe pole)
- Modify: `app/gui/main_window.py` (kolumna + przycisk + window)
- Modify: `app/exporter/xml_exporter.py` (użyj allegro_category)
- Test: `tests/test_category_mapper.py`

---

## Feature F — Audit Preview (Panel Audytowy)

### Problem
Obecny `open_preview()` pokazuje tylko opisy HTML. Przed eksportem nie ma szybkiego widoku który produkty mają problemy: brak atrybutów, brak kategorii Allegro, niski Q score, tytuł za długi.

### Rozwiązanie
Nowy plik HTML generowany per sesja. Każdy produkt jako karta: meta + atrybuty + kategoria + Q score + opis.

### 1. Generator — `app/gui/audit_preview.py` (NEW)

```python
def generate_audit_html(products: list[Product]) -> str:
    """Generate full HTML audit report for all products."""

def open_audit_preview(products: list[Product]) -> int:
    """Write HTML to temp file and open in browser. Return count."""
```

### 2. Struktura HTML karty produktu

```html
<div class="product-card [has-issues]">
  <div class="card-header">
    <span class="brand-chip brand-{brand}">INTEX</span>
    <span class="title">INTEX ZESTAW DO CZYSZCZENIA BASENÓW DELUXE 28003</span>
    <span class="q-badge q-{level}">Q: 8</span>
  </div>
  <div class="card-grid">
    <div class="meta-block">
      <h4>📝 Meta</h4>
      SKU: LDFDCN4Y16<br>
      EAN: ✓ 5906006085003<br>
      Tytuł: ✓ 62/75 zn.<br>
      Marka: intex<br>
      Kat. Allegro: ✓ Dom i ogród > Basen...
    </div>
    <div class="attrs-block">
      <h4>📊 Atrybuty</h4>
      Waga: 3.5 kg<br>
      Wymiary: 120 x 60 cm<br>
      Materiał: PVC
    </div>
  </div>
  <div class="desc-block">
    <h4>📄 Opis (fragment)</h4>
    <div class="desc-preview">Krystalicznie czysta woda — Zestaw Intex 28003 sprawdzi się...</div>
  </div>
</div>
```

### 3. CSS dla audytu

Karta `.has-issues` ma czerwoną lewą krawędź (`border-left: 4px solid #DC2626`).
Karta normalna: zielona lewa krawędź (`border-left: 4px solid #16A34A`).

Warunki `has-issues`:
- `product.quality_score < 6` (lub -1)
- `not product.allegro_category`
- `not product.attributes`
- `len(product.title or "") > 75`

### 4. Filtr na górze strony

Przyciski na górze: "Wszystkie | Tylko z problemami | Z opisem | Bez opisu"

### 5. GUI — przycisk w sidebarze

Po "Podgląd opisów HTML" dodaj:
```python
ctk.CTkButton(sidebar, text="Audyt produktów", command=self._open_audit,
    fg_color="#374151", hover_color="#1f2937").pack(fill="x", padx=12, pady=(0, 4))
```

`_open_audit()` wywołuje `open_audit_preview(self.products)`.

### Pliki
- New: `app/gui/audit_preview.py`
- Modify: `app/gui/main_window.py` (+przycisk +1 metoda)

---

## Feature H — Lifestyle PNG Library

### Problem
Miniatury (1200×1200, białe tło) wyglądają sterylnie. KanzaSklep osiąga wyższy CTR przez dodanie lifestyle elementów (psy, koty, kwiaty) kompozytowanych na miniaturkę.

### Rozwiązanie
Biblioteka pre-wyciętych PNG (przezroczyste tło) per marka. PIL compositing w prawym dolnym rogu. Zero kosztu API.

### 1. Struktura katalogów

```
data/lifestyle/
├── intex/
│   ├── child_splash.png      # dziecko bawiące się w wodzie, wycięte
│   ├── pool_float.png        # nadmuchiwana zabawka
│   └── swimmer.png           # pływak
├── gardenstein/
│   ├── flower_pot.png        # doniczka z kwiatem
│   ├── butterfly.png         # motyl
│   └── garden_gloves.png     # rękawice ogrodowe
├── zoovera/
│   ├── dog_sitting.png       # pies siedzący (jak KanzaSklep)
│   └── cat_playing.png       # kot z zabawką
├── hopla_toys/
│   ├── child_playing.png     # dziecko bawiące się
│   └── teddy_bear.png        # miś pluszowy
├── villago/
│   ├── plant_decor.png       # roślina dekoracyjna
│   └── coffee_cup.png        # filiżanka kawy
└── marketia_home/
    ├── cleaning_brush.png    # szczotka
    └── towel_folded.png      # złożony ręcznik
```

**Źródło PNG**: Unsplash/Pexels (licencja CC0), usuwanie tła przez `rembg`. Dostarczane razem z kodem jako assets.

### 2. Composer — `app/images/lifestyle_composer.py` (NEW)

```python
LIFESTYLE_DIR = Path(__file__).resolve().parents[2] / "data" / "lifestyle"

def list_lifestyle_assets(brand_key: str) -> list[Path]:
    """Return sorted list of PNG paths for brand."""

def compose_lifestyle(
    thumbnail: Image.Image,
    lifestyle_png: Path,
    position: str = "bottom-right",  # bottom-right | bottom-left
    scale: float = 0.32,             # lifestyle element = 32% szerokości thumbnail
) -> Image.Image:
    """Composite lifestyle PNG onto thumbnail. Returns new Image."""
```

**Algorytm compositingu:**
1. Otwórz lifestyle PNG (RGBA)
2. Oblicz target_size = `int(thumbnail.width * scale)`
3. Resize lifestyle PNG zachowując proporcje: `lifestyle.resize((target_size, target_size), LANCZOS)`
4. Pozycja `bottom-right`: `x = thumbnail.width - target_size - 20`, `y = thumbnail.height - target_size - 20`
5. Dodaj shadow pod elementem: Gaussian blur na alpha channel (radius=8, opacity=0.3) przesunięty o (4,4)px
6. `thumbnail.paste(lifestyle, (x, y), mask=lifestyle)` (alpha composite)
7. Zwróć zmodyfikowany thumbnail

### 3. GUI — nowy przycisk "4.7 Lifestyle"

W sidebarze po "4.6 Upload ImgBB":
```python
self.btn_lifestyle = ctk.CTkButton(
    sidebar, text="4.7 Lifestyle thumb.", command=self._run_lifestyle,
    fg_color="#0891B2", hover_color="#0e7490",
)
```

### 4. Okno wyboru — `LifestylePickerWindow(ctk.CTkToplevel)`

Rozmiar: 700×500. Layout:
```
┌─────────────────────────────────────────────────────┐
│  Lifestyle Thumbnails — wybierz element per marka   │
├─────────────────────────────────────────────────────┤
│  INTEX     [child_splash.png ▼]  [Podgląd]  [✓]    │
│  GARDENSTEIN [flower_pot.png ▼]  [Podgląd]  [✓]    │
│  ZOOVERA   [dog_sitting.png ▼]   [Podgląd]  [✓]    │
│  ...                                                │
│                                                     │
│  [Podgląd przed/po] ← kliknięcie pokazuje split   │
│                                                     │
│  [Generuj lifestyle thumbnails dla X produktów]     │
└─────────────────────────────────────────────────────┘
```

- Dropdown per marka: lista PNG-ek z `list_lifestyle_assets(brand)`
- "Podgląd" otwiera split okno: lewy = oryginał, prawy = z lifestyle
- Checkbox "✓" = marka aktywna (lifestyle będzie nałożony)
- Generuj: nadpisuje `output/thumbnails/{sku}.jpg` (jeśli istnieje) lub tworzy nowe

### 5. Zapis i integracja

Po złożeniu: zawsze zapisz jako `output/thumbnails/{sku}_lifestyle.jpg` (osobny plik — nigdy nie nadpisuj oryginalnego `{sku}.jpg`).

`upload_thumbnails()` preferuje `_lifestyle.jpg` jeśli istnieje, fallback na `{sku}.jpg`. Logika w `imgbb_uploader.py`:
```python
thumb_path = (THUMB_DIR / f"{p.sku}_lifestyle.jpg")
if not thumb_path.exists():
    thumb_path = THUMB_DIR / f"{p.sku}.jpg"
```

`Product` nie zmienia się — istniejące `thumbnail_url` działa bez zmian.

### Pliki
- New: `app/images/lifestyle_composer.py`
- New: `app/gui/lifestyle_picker.py`
- New: `data/lifestyle/` (PNG assets)
- Modify: `app/gui/main_window.py` (+przycisk +2 metody)
- Test: `tests/test_lifestyle_composer.py`

---

## Podsumowanie plików

| Plik | Typ | Feature |
|------|-----|---------|
| `app/ai/prompts.py` | Modify | D |
| `data/allegro_categories.json` | New | C |
| `app/transformer/category_mapper.py` | New | C |
| `app/parser/normalizer.py` | Modify | C (nowe pole) |
| `app/gui/main_window.py` | Modify | C + F + H |
| `app/exporter/xml_exporter.py` | Modify | C |
| `tests/test_category_mapper.py` | New | C |
| `app/gui/audit_preview.py` | New | F |
| `app/images/lifestyle_composer.py` | New | H |
| `app/gui/lifestyle_picker.py` | New | H |
| `data/lifestyle/` | New | H (PNG assets) |
| `tests/test_lifestyle_composer.py` | New | H |

**Brak breaking changes.** Nowe pola w Product mają domyślne wartości. Istniejące XML i cache nienaruszone.

**Kolejność implementacji:** D (5 min) → C (najbardziej złożone) → F → H
