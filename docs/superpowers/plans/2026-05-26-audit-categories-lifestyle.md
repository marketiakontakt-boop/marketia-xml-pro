# Audit Preview, Category Mapper, Attribute Injection, Lifestyle Thumbnails — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 features to marketia-xml-pro: (D) inject extracted product attributes into Gemini prompt, (C) map BaseLinker categories to Allegro taxonomy with GUI editor, (F) HTML audit preview per product, (H) lifestyle PNG compositing on thumbnails.

**Architecture:** Feature D is a pure prompt change. Feature C adds a transformer + JSON data file + GUI window + exporter tweak. Feature F is a standalone HTML generator opened in browser. Feature H adds a PIL composer + GUI picker + updates ImgBB uploader. All features are additive — no existing functionality broken.

**Tech Stack:** Python 3.14, customtkinter, lxml, Pillow (PIL), re (stdlib), webbrowser (stdlib), app's existing ClaudeClient (wraps Gemini API)

**Implementation order:** D → C → F → H (F references `allegro_category` from C)

---

## File Structure

| File | Action | Feature |
|------|--------|---------|
| `app/ai/prompts.py` | Modify `build_description_prompt()` | D |
| `data/allegro_categories.json` | Create | C |
| `app/transformer/category_mapper.py` | Create | C |
| `app/parser/normalizer.py` | Add `allegro_category` field | C |
| `app/gui/category_mapper_window.py` | Create CTkToplevel editor | C |
| `app/gui/main_window.py` | Add column + buttons + methods | C + F + H |
| `app/exporter/xml_exporter.py` | Use `allegro_category` | C |
| `app/gui/audit_preview.py` | Create HTML generator | F |
| `app/images/lifestyle_composer.py` | Create PIL composer | H |
| `app/gui/lifestyle_picker.py` | Create CTkToplevel picker | H |
| `app/images/imgbb_uploader.py` | Prefer `_lifestyle.jpg` | H |
| `data/lifestyle/` | Create placeholder PNGs | H |
| `tests/test_category_mapper.py` | Tests for C | C |
| `tests/test_lifestyle_composer.py` | Tests for H | H |

---

### Task 1: Feature D — Inject product.attributes into Gemini prompt

**Files:**
- Modify: `app/ai/prompts.py` (function `build_description_prompt`, lines ~40-130)

**Context:** The function already builds a `spec_parts` list from explicit fields (width, height, weight, ean, brand). `product.attributes` is a `dict[str,str]` populated by `attribute_extractor.py` from XML + HTML regex. We inject these into both the spec_parts list and as a standalone block before the HTML skeleton.

- [ ] **Step 1: Locate the spec_parts block in `app/ai/prompts.py`**

Find this block (around line 40-55):
```python
    spec_parts = []
    if product.width and product.width > 0:
        spec_parts.append(f"<b>Szerokość:</b> {product.width} cm")
    ...
    spec_parts.append(f"<b>Marka:</b> {brand_display}")
```

- [ ] **Step 2: Add attributes injection after the spec_parts block**

After `spec_parts.append(f"<b>Marka:</b> {brand_display}")`, add:

```python
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
```

- [ ] **Step 3: Inject `_attrs_block` into the returned prompt string**

Find the `return f"""Napisz opis produktu...` at the end of the function. Change:

```python
    return f"""Napisz opis produktu BaseLinker/Allegro.

PRODUKT:
Tytuł: {product.title or product.name}
Kategoria: {product.category_name or '—'}
Oryginał (kontekst): {orig}
```

To:

```python
    return f"""Napisz opis produktu BaseLinker/Allegro.

PRODUKT:
Tytuł: {product.title or product.name}
Kategoria: {product.category_name or '—'}
Oryginał (kontekst): {orig}
{_attrs_block}
```

- [ ] **Step 4: Manual verification**

No automated test needed — tested by regenerating one product description with known attributes and checking that the SPECYFIKACJA section includes them. Verify by running the app, loading XML, running transforms, then regenerating a single product from the detail popup.

- [ ] **Step 5: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro
git add app/ai/prompts.py
git commit -m "feat: inject product.attributes into Gemini description prompt"
```

---

### Task 2: Feature C — Category mapper data + transformer

**Files:**
- Create: `data/allegro_categories.json`
- Create: `app/transformer/category_mapper.py`
- Modify: `app/parser/normalizer.py`
- Test: `tests/test_category_mapper.py`

- [ ] **Step 1: Create `data/allegro_categories.json`**

```json
{
  "DLA DOMU / Artykuły dla zwierząt / Klatki i kojce": "Zwierzęta > Psy > Kojce i klatki",
  "DLA DOMU / Artykuły dla zwierząt / Legowiska": "Zwierzęta > Psy > Posłania i legowiska",
  "DLA DOMU / Artykuły dla zwierząt / Pozostałe art. dla zwierząt": "Zwierzęta > Akcesoria dla zwierząt",
  "DLA DOMU / Artykuły dla zwierząt / Transportery": "Zwierzęta > Psy > Transportery",
  "DLA DOMU / Biurka i podstawki / Biurka gamingowe": "Dom i ogród > Meble > Biurka > Biurka gamingowe",
  "DLA DOMU / Biurka i podstawki / Biurka standardowe": "Dom i ogród > Meble > Biurka",
  "DLA DOMU / Biurka i podstawki / Biurka z regałem": "Dom i ogród > Meble > Biurka",
  "DLA DOMU / Biurka i podstawki / Podstawki pod laptopa": "Komputery > Akcesoria komputerowe > Podstawki pod laptopa",
  "DLA DOMU / Bramki i osłony": "Dziecko > Bezpieczeństwo > Bramki i osłony",
  "DLA DOMU / Daszki nad drzwi wejściowe": "Dom i ogród > Wyposażenie domu > Daszki",
  "DLA DOMU / Fitness / Rowerki treningowe": "Sport > Fitness > Rowery stacjonarne",
  "DLA DOMU / Fitness / Steppery": "Sport > Fitness > Steppery",
  "DLA DOMU / Fitness / Ławeczki i hantle": "Sport > Fitness > Hantle i sztangi",
  "DLA DOMU / Krzesła, fotele": "Dom i ogród > Meble > Krzesła",
  "DLA DOMU / Krzesła, fotele / Fotele biurowe - gamingowe": "Dom i ogród > Meble > Fotele > Fotele gamingowe",
  "DLA DOMU / Krzesła, fotele / Fotele bujane": "Dom i ogród > Meble > Fotele > Fotele bujane",
  "DLA DOMU / Krzesła, fotele / Krzesła": "Dom i ogród > Meble > Krzesła",
  "DLA DOMU / Krzesła, fotele / Krzesła barowe - hokery": "Dom i ogród > Meble > Krzesła > Hokery i krzesła barowe",
  "DLA DOMU / Pozostałe": "Dom i ogród > Wyposażenie domu",
  "DLA DOMU / RTV - AGD / Alkomaty": "Elektronika > Pomiary > Alkomaty",
  "DLA DOMU / RTV - AGD / Czajniki bezprzewodowe": "Dom i ogród > AGD małe > Czajniki elektryczne",
  "DLA DOMU / RTV - AGD / Nawilżacze, oczyszczacze powietrza": "Dom i ogród > AGD małe > Nawilżacze i oczyszczacze",
  "DLA DOMU / RTV - AGD / Pozostałe art. RTV-AGD": "Dom i ogród > AGD małe",
  "DLA DOMU / RTV - AGD / Roboty autonomiczne": "Dom i ogród > AGD małe > Odkurzacze > Odkurzacze autonomiczne",
  "DLA DOMU / RTV - AGD / Stacje pogodowe": "Elektronika > Pomiary > Stacje meteorologiczne",
  "DLA DOMU / Regały": "Dom i ogród > Meble > Regały i półki",
  "DLA DOMU / Rozrywka": "Dom i ogród > Wyposażenie domu",
  "DLA DOMU / STOŁY  STOLIKI KUCHENNE, ŁAWY  DO SALONU": "Dom i ogród > Meble > Stoły i stoliki",
  "DLA DOMU / Skrzynki na listy": "Dom i ogród > Wyposażenie domu > Skrzynki na listy",
  "DLA DOMU / Stoły do masażu": "Sport > Fitness > Stoły do masażu",
  "DLA OGRODU / Huśtawki i akcesoria": "Dom i ogród > Meble ogrodowe > Huśtawki ogrodowe",
  "DLA OGRODU / Leżaki": "Dom i ogród > Meble ogrodowe > Leżaki",
  "DLA OGRODU / Leżanki": "Dom i ogród > Meble ogrodowe > Leżaki",
  "DLA OGRODU / Markizy przeciwsłoneczne": "Dom i ogród > Meble ogrodowe > Markizy i rolety",
  "DLA OGRODU / Meble cateringowe": "Dom i ogród > Meble ogrodowe",
  "DLA OGRODU / Namioty na lato": "Dom i ogród > Meble ogrodowe > Altany i pergole",
  "DLA OGRODU / Opryskiwacze": "Dom i ogród > Ogród > Opryskiwacze",
  "DLA OGRODU / Osłony maskujące": "Dom i ogród > Meble ogrodowe > Osłony",
  "DLA OGRODU / Parasole gazowe": "Dom i ogród > Meble ogrodowe > Parasole ogrodowe",
  "DLA OGRODU / Parasole i podstawy": "Dom i ogród > Meble ogrodowe > Parasole ogrodowe",
  "DLA OGRODU / Pawilony": "Dom i ogród > Meble ogrodowe > Altany i pergole",
  "DLA OGRODU / Pozostałe": "Dom i ogród > Meble ogrodowe",
  "DLA OGRODU / Sport": "Sport > Sporty ogrodowe",
  "DLA OGRODU / Stoliki i krzesła": "Dom i ogród > Meble ogrodowe > Zestawy mebli ogrodowych",
  "DLA OGRODU / Suszarki": "Dom i ogród > Pranie i suszenie > Suszarki na pranie",
  "DLA OGRODU / Wózki transportowe": "Dom i ogród > Ogród > Taczki i wózki",
  "DLA OGRODU / Zestawy mebli": "Dom i ogród > Meble ogrodowe > Zestawy mebli ogrodowych",
  "DLA OGRODU / Żagle ogrodowe": "Dom i ogród > Meble ogrodowe > Żagle przeciwsłoneczne",
  "DLA OGRODU / Żeliwne kociołki": "Dom i ogród > Gotowanie > Kociołki",
  "INTEX / Baseny": "Dom i ogród > Basen i spa > Baseny ogrodowe",
  "INTEX / Materace dla domu": "Dom i ogród > Sypialnia > Materace > Materace dmuchane",
  "INTEX / Materace plażowe": "Sport > Sporty wodne > Materace i pływaki",
  "INTEX / Materace wodne dla dzieci": "Dziecko > Zabawki ogrodowe > Zabawy w wodzie",
  "INTEX / Pokrywy na basen": "Dom i ogród > Basen i spa > Akcesoria do basenów",
  "INTEX / Pompki": "Dom i ogród > Basen i spa > Akcesoria do basenów",
  "INTEX / Pompy i akcesoria do basenów": "Dom i ogród > Basen i spa > Akcesoria do basenów",
  "INTEX / Pontony i kajaki": "Sport > Sporty wodne > Pontony i kajaki",
  "INTEX / Pozostałe": "Dom i ogród > Basen i spa",
  "INTEX / SPA i hydromasaże": "Dom i ogród > Basen i spa > Jacuzzi i SPA",
  "INTEX / Wodne place zabaw": "Dziecko > Zabawki ogrodowe > Zabawy w wodzie"
}
```

- [ ] **Step 2: Write failing tests in `tests/test_category_mapper.py`**

```python
import json
import pytest
from app.transformer.category_mapper import load_category_map, map_category, map_all_products
from app.parser.normalizer import Product


def _make_product(cat: str) -> Product:
    return Product(
        product_id="1", sku="T", ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0,
        quantity=0, name="Test", category_name=cat, manufacturer_name="",
        description="", description_extra_1="", description_extra_2="",
    )


def test_load_category_map_returns_dict():
    m = load_category_map()
    assert isinstance(m, dict)
    assert len(m) > 0


def test_map_known_category():
    m = load_category_map()
    result = map_category("INTEX / Baseny", m)
    assert result == "Dom i ogród > Basen i spa > Baseny ogrodowe"


def test_map_unknown_category_returns_none():
    m = load_category_map()
    result = map_category("NIEZNANA KATEGORIA", m)
    assert result is None


def test_map_all_products_sets_allegro_category():
    m = load_category_map()
    p = _make_product("INTEX / Baseny")
    map_all_products([p], m)
    assert p.allegro_category == "Dom i ogród > Basen i spa > Baseny ogrodowe"


def test_map_all_products_leaves_empty_for_unknown():
    m = load_category_map()
    p = _make_product("UNKNOWN")
    map_all_products([p], m)
    assert p.allegro_category == ""
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_category_mapper.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.transformer.category_mapper'`

- [ ] **Step 4: Add `allegro_category` field to `Product` in `app/parser/normalizer.py`**

After `attributes: dict[str, str] = field(default_factory=dict)`, add:
```python
    allegro_category: str = ""   # populated by category_mapper transformer
```

- [ ] **Step 5: Create `app/transformer/category_mapper.py`**

```python
"""Map BaseLinker categories to Allegro taxonomy paths."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parser.normalizer import Product

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "allegro_categories.json"


def load_category_map() -> dict[str, str]:
    """Load BaseLinker → Allegro category mapping from JSON."""
    if not _DATA_PATH.exists():
        return {}
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def map_category(baselinker_cat: str, category_map: dict[str, str]) -> str | None:
    """Return Allegro taxonomy path for a BaseLinker category, or None if unknown."""
    return category_map.get(baselinker_cat)


def map_all_products(products: list["Product"], category_map: dict[str, str]) -> None:
    """Set product.allegro_category for all products. Unknown categories → empty string."""
    for p in products:
        mapped = map_category(p.category_name, category_map)
        p.allegro_category = mapped or ""


def suggest_category_gemini(baselinker_cat: str) -> str:
    """Ask Gemini to suggest an Allegro category path for an unknown BaseLinker category."""
    from app.ai.claude_client import ClaudeClient
    client = ClaudeClient()
    system = (
        "Jesteś ekspertem od taksonomii Allegro.pl. "
        "Odpowiedz TYLKO ścieżką kategorii Allegro w formacie: "
        "'Nadkategoria > Kategoria > Podkategoria'. Bez żadnych dodatkowych słów."
    )
    prompt = (
        f"Kategoria z BaseLinker: '{baselinker_cat}'\n"
        "Podaj najlepiej pasującą ścieżkę kategorii Allegro.pl dla tej kategorii produktu."
    )
    result = client.call(system, prompt)
    return result.strip()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_category_mapper.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 7: Wire into `_transform_worker` in `app/gui/main_window.py`**

Add import near top (after existing transformer imports):
```python
from app.transformer.category_mapper import load_category_map, map_all_products
```

In `_transform_worker()`, after `enrich_product_attributes(p)` loop, add:
```python
            _cat_map = load_category_map()
            map_all_products(self.products, _cat_map)
```

- [ ] **Step 8: Run full test suite**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add data/allegro_categories.json app/transformer/category_mapper.py app/parser/normalizer.py app/gui/main_window.py tests/test_category_mapper.py
git commit -m "feat: Allegro category mapper — JSON map + transformer + transform integration"
```

---

### Task 3: Feature C — CategoryMapperWindow + ProductRow column

**Files:**
- Create: `app/gui/category_mapper_window.py`
- Modify: `app/gui/main_window.py` (ProductRow + sidebar button + `_open_category_mapper` method)

- [ ] **Step 1: Create `app/gui/category_mapper_window.py`**

```python
"""Category Mapper Window — edit BaseLinker → Allegro category mapping."""
from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

from app.transformer.category_mapper import load_category_map, suggest_category_gemini

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "allegro_categories.json"


class CategoryMapperWindow(ctk.CTkToplevel):
    """Non-blocking window for editing BaseLinker → Allegro category mapping."""

    def __init__(self, parent, products, on_save=None):
        super().__init__(parent)
        self.title("Mapa kategorii Allegro")
        self.geometry("900x600")
        self.minsize(700, 400)
        self._on_save = on_save

        # Collect unique BaseLinker categories from loaded products
        self._bl_cats = sorted({p.category_name for p in products if p.category_name})
        self._cat_map = load_category_map()
        self._entries: dict[str, ctk.CTkEntry] = {}

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        ctk.CTkLabel(toolbar, text="BaseLinker kategoria → Allegro ścieżka",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(toolbar, text="Sugeruj brakujące (AI)", width=180,
                      command=self._suggest_missing,
                      fg_color="#1a6f3a", hover_color="#145c2f").pack(side="right", padx=(8, 0))
        ctk.CTkButton(toolbar, text="Zapisz mapę", width=120,
                      command=self._save).pack(side="right")

        # Scrollable table
        frame = ctk.CTkScrollableFrame(self, label_text="")
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, minsize=80)

        # Header
        for col, text in enumerate(["BaseLinker kategoria", "Allegro ścieżka", "Status"]):
            ctk.CTkLabel(frame, text=text, font=ctk.CTkFont(weight="bold"),
                         text_color="#6B7280").grid(row=0, column=col, sticky="w", padx=8, pady=4)

        for row_idx, bl_cat in enumerate(self._bl_cats, 1):
            current = self._cat_map.get(bl_cat, "")

            ctk.CTkLabel(frame, text=bl_cat, anchor="w", wraplength=380).grid(
                row=row_idx, column=0, sticky="w", padx=8, pady=2)

            entry = ctk.CTkEntry(frame, width=340)
            entry.insert(0, current)
            entry.grid(row=row_idx, column=1, sticky="ew", padx=8, pady=2)
            self._entries[bl_cat] = entry

            status_text = "✓" if current else "?"
            status_color = "#15803D" if current else "#EA580C"
            ctk.CTkLabel(frame, text=status_text, text_color=status_color,
                         font=ctk.CTkFont(weight="bold")).grid(
                row=row_idx, column=2, padx=8, pady=2)

    def _suggest_missing(self):
        for bl_cat, entry in self._entries.items():
            if not entry.get().strip():
                try:
                    suggestion = suggest_category_gemini(bl_cat)
                    entry.delete(0, "end")
                    entry.insert(0, suggestion)
                except Exception:
                    pass

    def _save(self):
        updated = dict(self._cat_map)
        for bl_cat, entry in self._entries.items():
            val = entry.get().strip()
            if val:
                updated[bl_cat] = val
            elif bl_cat in updated:
                del updated[bl_cat]
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(updated, f, ensure_ascii=False, indent=2)
        if self._on_save:
            self._on_save(updated)
        self.destroy()
```

- [ ] **Step 2: Add "KAT" column to `ProductRow` in `app/gui/main_window.py`**

Change `COL_WIDTHS` from:
```python
    COL_WIDTHS = (130, 340, 110, 100, 130, 40, 40, 50)
```
To:
```python
    COL_WIDTHS = (130, 310, 110, 80, 100, 130, 40, 40, 50)
```

After the brand dropdown/chip block (column 2) and before the model label (column 3), insert the category chip block. The current column 3 is model. Add a new column 3 for category, shift model to column 4, EAN to column 5, etc.

The full `ProductRow.__init__` column layout should be:

```python
        # Col 0: SKU
        ctk.CTkLabel(self, text=product.sku, anchor="w").grid(row=0, column=0, sticky="w", padx=4)
        # Col 1: Title
        ctk.CTkLabel(self, text=product.title or product.name, anchor="w", wraplength=300).grid(
            row=0, column=1, sticky="w", padx=4)
        # Col 2: Brand (existing dropdown/chip code — unchanged)
        # ... existing brand code ...

        # Col 3: Allegro category chip (NEW)
        allegro_cat = getattr(product, "allegro_category", "")
        if allegro_cat:
            cat_short = allegro_cat.split(" > ")[-1][:12]
            ctk.CTkLabel(self, text=cat_short, fg_color="#DCFCE7", text_color="#15803D",
                         corner_radius=4, font=ctk.CTkFont(size=9)).grid(
                row=0, column=3, sticky="w", padx=4, pady=4)
        else:
            ctk.CTkLabel(self, text="?", fg_color="#FFEDD5", text_color="#C2410C",
                         corner_radius=4, font=ctk.CTkFont(size=9, weight="bold")).grid(
                row=0, column=3, sticky="w", padx=4, pady=4)

        # Col 4: Model (was col 3)
        ctk.CTkLabel(self, text=product.model_name or "—", anchor="w").grid(row=0, column=4, sticky="w", padx=4)
```

Update EAN, title-ok, AI, Q columns to columns 5, 6, 7, 8 accordingly (increment each by 1).

Update the header row in `_render_table()`:
```python
        for i, (text, w) in enumerate(
            zip(("SKU", "TYTUŁ / NAZWA", "MARKA", "KAT.", "MODEL", "EAN", "OK", "AI", "Q"),
                ProductRow.COL_WIDTHS)
        ):
```

- [ ] **Step 3: Add import and sidebar button in `app/gui/main_window.py`**

Add import at top:
```python
from app.gui.category_mapper_window import CategoryMapperWindow
```

In `_build_layout()` sidebar, after the "2. Marka (inline)" button, add:
```python
        ctk.CTkButton(sidebar, text="Mapa kategorii", command=self._open_category_mapper,
                      fg_color="#374151", hover_color="#1f2937").pack(fill="x", padx=12, pady=(0, 4))
```

Add method to `App`:
```python
    def _open_category_mapper(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        def _on_save(updated_map):
            from app.transformer.category_mapper import map_all_products
            map_all_products(self.products, updated_map)
            self._render_table()
        CategoryMapperWindow(self, self.products, on_save=_on_save)
```

- [ ] **Step 4: Commit**

```bash
git add app/gui/category_mapper_window.py app/gui/main_window.py
git commit -m "feat: CategoryMapperWindow + category chip in ProductRow"
```

---

### Task 4: Feature C — Exporter uses allegro_category

**Files:**
- Modify: `app/exporter/xml_exporter.py`

- [ ] **Step 1: Update `_product_to_element()` to prefer `allegro_category`**

Find in `_product_to_element()`:
```python
    add("category_name", p.category_name)
```

Replace with:
```python
    add("category_name", getattr(p, "allegro_category", "") or p.category_name)
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add app/exporter/xml_exporter.py
git commit -m "feat: export allegro_category in XML, fallback to baselinker category"
```

---

### Task 5: Feature F — Audit Preview HTML generator

**Files:**
- Create: `app/gui/audit_preview.py`
- Modify: `app/gui/main_window.py` (+button +method)

- [ ] **Step 1: Create `app/gui/audit_preview.py`**

```python
"""Generate HTML audit report for all products and open in browser."""
from __future__ import annotations

import tempfile
import webbrowser
from pathlib import Path

from app.parser.normalizer import Product
from app.gui.brand_colors import get_brand_chip_colors
from app.validator.quality_scorer import get_label

_CSS = """
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #F9FAFB; margin: 0; padding: 16px; color: #374151; }
h1 { font-size: 20px; margin-bottom: 4px; color: #111827; }
.subtitle { color: #6B7280; margin-bottom: 16px; font-size: 13px; }
.filters { display: flex; gap: 8px; margin-bottom: 16px; }
.filter-btn { padding: 6px 14px; border: 1px solid #E5E7EB; border-radius: 20px;
              background: white; cursor: pointer; font-size: 12px; color: #374151; }
.filter-btn.active { background: #2563EB; color: white; border-color: #2563EB; }
.product-card { background: white; border-radius: 8px; border: 1px solid #E5E7EB;
                margin-bottom: 12px; overflow: hidden;
                border-left: 4px solid #16A34A; }
.product-card.has-issues { border-left-color: #DC2626; }
.card-header { display: flex; align-items: center; gap: 10px;
               padding: 10px 14px; background: #F9FAFB; border-bottom: 1px solid #E5E7EB; }
.brand-chip { padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; }
.card-title { flex: 1; font-size: 13px; font-weight: 600; color: #111827; }
.q-badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
.q-ok { background: #DCFCE7; color: #15803D; }
.q-warn { background: #FEF3C7; color: #92400E; }
.q-bad { background: #FEE2E2; color: #DC2626; }
.card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
.meta-block, .attrs-block { padding: 10px 14px; font-size: 12px; line-height: 1.7; }
.meta-block { border-right: 1px solid #F3F4F6; }
.block-title { font-size: 11px; font-weight: bold; color: #6B7280;
               text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.ok { color: #15803D; } .warn { color: #EA580C; } .bad { color: #DC2626; }
.desc-block { padding: 8px 14px 10px; border-top: 1px solid #F3F4F6; font-size: 12px; }
.desc-preview { color: #374151; line-height: 1.5; max-height: 60px; overflow: hidden; }
.no-desc { color: #9CA3AF; font-style: italic; }
</style>
"""

_JS = """
<script>
function filterCards(mode) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.product-card').forEach(card => {
    const isIssue = card.classList.contains('has-issues');
    const hasDesc = card.dataset.hasDesc === '1';
    if (mode === 'all') card.style.display = '';
    else if (mode === 'issues') card.style.display = isIssue ? '' : 'none';
    else if (mode === 'desc') card.style.display = hasDesc ? '' : 'none';
    else if (mode === 'nodesc') card.style.display = !hasDesc ? '' : 'none';
  });
}
</script>
"""


def _has_issues(p: Product) -> bool:
    if getattr(p, "quality_score", -1) < 6:
        return True
    if not getattr(p, "allegro_category", ""):
        return True
    if not getattr(p, "attributes", {}):
        return True
    if len(p.title or "") > 75 or not p.title:
        return True
    return False


def _q_class(score: int) -> str:
    if score < 0:
        return "q-bad"
    if score >= 7:
        return "q-ok"
    if score >= 5:
        return "q-warn"
    return "q-bad"


def _product_card(p: Product) -> str:
    issue_cls = "has-issues" if _has_issues(p) else ""
    has_desc = "1" if getattr(p, "ai_done", False) else "0"

    bg, fg = get_brand_chip_colors(p.brand or "")
    brand_label = (p.brand or "—").upper()[:10]

    score = getattr(p, "quality_score", -1)
    q_text = f"Q: {score}/10" if score >= 0 else "Q: —"
    q_cls = _q_class(score)

    title_len = len(p.title or "")
    title_ok = "✓" if 0 < title_len <= 75 else "✗"
    title_cls = "ok" if title_ok == "✓" else "bad"
    title_note = f"{title_len}/75 zn."

    ean_ok = "✓" if getattr(p, "ean_valid", True) and p.ean else "✗"
    ean_cls = "ok" if ean_ok == "✓" else "bad"

    allegro_cat = getattr(p, "allegro_category", "")
    cat_ok = "✓" if allegro_cat else "?"
    cat_cls = "ok" if allegro_cat else "warn"
    cat_display = allegro_cat[:50] if allegro_cat else "brak — uruchom transformy"

    attrs = getattr(p, "attributes", {})
    attrs_html = "<br>".join(f"<b>{k}:</b> {v}" for k, v in list(attrs.items())[:5])
    if not attrs_html:
        attrs_html = '<span class="no-desc">brak atrybutów</span>'

    desc = getattr(p, "description", "") or ""
    import re
    desc_text = re.sub(r"<[^>]+>", " ", desc)
    desc_text = re.sub(r"\s+", " ", desc_text).strip()[:200]
    desc_html = (f'<div class="desc-preview">{desc_text}…</div>'
                 if desc_text else '<div class="no-desc">brak opisu — uruchom krok 4</div>')

    return f"""
<div class="product-card {issue_cls}" data-has-desc="{has_desc}">
  <div class="card-header">
    <span class="brand-chip" style="background:{bg};color:{fg}">{brand_label}</span>
    <span class="card-title">{p.title or p.name}</span>
    <span class="q-badge {q_cls}">{q_text}</span>
  </div>
  <div class="card-grid">
    <div class="meta-block">
      <div class="block-title">📝 Meta</div>
      SKU: {p.sku}<br>
      EAN: <span class="{ean_cls}">{ean_ok}</span> {p.ean or '—'}<br>
      Tytuł: <span class="{title_cls}">{title_ok}</span> {title_note}<br>
      Marka: {p.brand or '—'}<br>
      Kat. Allegro: <span class="{cat_cls}">{cat_ok}</span> {cat_display}
    </div>
    <div class="attrs-block">
      <div class="block-title">📊 Atrybuty ({len(attrs)})</div>
      {attrs_html}
    </div>
  </div>
  <div class="desc-block">
    <div class="block-title">📄 Opis</div>
    {desc_html}
  </div>
</div>"""


def generate_audit_html(products: list[Product]) -> str:
    issues = sum(1 for p in products if _has_issues(p))
    cards = "\n".join(_product_card(p) for p in products)
    return f"""<!DOCTYPE html>
<html lang="pl">
<head><meta charset="UTF-8"><title>Audit — Marketia XML Pro</title>{_CSS}</head>
<body>
{_JS}
<h1>Audyt produktów — Marketia XML Pro</h1>
<p class="subtitle">Łącznie: {len(products)} produktów | Z problemami: {issues}</p>
<div class="filters">
  <button class="filter-btn active" onclick="filterCards('all')">Wszystkie ({len(products)})</button>
  <button class="filter-btn" onclick="filterCards('issues')">Z problemami ({issues})</button>
  <button class="filter-btn" onclick="filterCards('desc')">Z opisem</button>
  <button class="filter-btn" onclick="filterCards('nodesc')">Bez opisu</button>
</div>
{cards}
</body></html>"""


def open_audit_preview(products: list[Product]) -> int:
    """Generate audit HTML and open in browser. Returns count of products."""
    if not products:
        return 0
    html = generate_audit_html(products)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        path = f.name
    webbrowser.open(f"file://{path}")
    return len(products)
```

- [ ] **Step 2: Add import + button + method to `app/gui/main_window.py`**

Add import:
```python
from app.gui.audit_preview import open_audit_preview
```

In `_build_layout()` sidebar, after "Podgląd opisów HTML" button, add:
```python
        ctk.CTkButton(
            sidebar, text="Audyt produktów", command=self._open_audit,
            fg_color="#374151", hover_color="#1f2937",
        ).pack(fill="x", padx=12, pady=(0, 4))
```

Add method:
```python
    def _open_audit(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        count = open_audit_preview(self.products)
        self.status_var.set(f"Audyt otwarty w przeglądarce ({count} produktów).")
```

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/gui/audit_preview.py app/gui/main_window.py
git commit -m "feat: audit preview — HTML per-product cards with meta, attributes, category, description"
```

---

### Task 6: Feature H — Lifestyle PNG assets + PIL composer

**Files:**
- Create: `data/lifestyle/` (placeholder PNGs via PIL)
- Create: `app/images/lifestyle_composer.py`
- Test: `tests/test_lifestyle_composer.py`

- [ ] **Step 1: Create placeholder lifestyle PNG assets using PIL**

Run this script once to generate placeholder assets (colored rounded shapes with brand emoji):

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && python3 - <<'EOF'
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math

brands = {
    "intex": [("child_splash", "💦", (147, 197, 253)), ("pool_float", "🏊", (96, 165, 250))],
    "gardenstein": [("flower_pot", "🌸", (167, 243, 208)), ("butterfly", "🦋", (110, 231, 183))],
    "zoovera": [("dog_sitting", "🐶", (196, 181, 253)), ("cat_playing", "🐱", (167, 139, 250))],
    "hopla_toys": [("child_playing", "🧒", (251, 207, 232)), ("teddy_bear", "🧸", (249, 168, 212))],
    "villago": [("plant_decor", "🌿", (209, 213, 219)), ("coffee_cup", "☕", (156, 163, 175))],
    "marketia_home": [("cleaning_brush", "🧹", (186, 230, 253)), ("towel_folded", "🏠", (147, 197, 253))],
}

base = Path("data/lifestyle")
base.mkdir(parents=True, exist_ok=True)

SIZE = 400
for brand, assets in brands.items():
    brand_dir = base / brand
    brand_dir.mkdir(exist_ok=True)
    for name, emoji, color in assets:
        img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Soft circle background
        r = 180
        cx, cy = SIZE // 2, SIZE // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, 200))
        # Save
        path = brand_dir / f"{name}.png"
        img.save(str(path), "PNG")
        print(f"Created: {path}")

print("Done — placeholder lifestyle assets created.")
print("Replace these PNGs with real cutout photos for best results.")
EOF
```

Expected output: lists of created PNG files in `data/lifestyle/`.

- [ ] **Step 2: Write failing tests in `tests/test_lifestyle_composer.py`**

```python
import pytest
from pathlib import Path
from PIL import Image
from app.images.lifestyle_composer import list_lifestyle_assets, compose_lifestyle

LIFESTYLE_DIR = Path(__file__).resolve().parent.parent / "data" / "lifestyle"


def test_list_lifestyle_assets_returns_paths():
    assets = list_lifestyle_assets("zoovera")
    assert len(assets) > 0
    for a in assets:
        assert a.suffix == ".png"
        assert a.exists()


def test_list_lifestyle_assets_unknown_brand_returns_empty():
    assets = list_lifestyle_assets("nonexistent_brand")
    assert assets == []


def test_compose_lifestyle_returns_image():
    thumb = Image.new("RGB", (1200, 1200), (255, 255, 255))
    assets = list_lifestyle_assets("zoovera")
    assert assets, "No zoovera assets found — run Task 6 Step 1 first"
    result = compose_lifestyle(thumb, assets[0])
    assert result.size == (1200, 1200)
    assert result.mode == "RGB"


def test_compose_lifestyle_bottom_right_different_from_original():
    thumb = Image.new("RGB", (1200, 1200), (255, 255, 255))
    assets = list_lifestyle_assets("zoovera")
    result = compose_lifestyle(thumb, assets[0])
    # Bottom-right quadrant should have some non-white pixels
    crop = result.crop((600, 600, 1200, 1200))
    pixels = list(crop.getdata())
    non_white = [p for p in pixels if p != (255, 255, 255)]
    assert len(non_white) > 100  # lifestyle element added some pixels
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_lifestyle_composer.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Create `app/images/lifestyle_composer.py`**

```python
"""Composite lifestyle PNG elements onto product thumbnails."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

LIFESTYLE_DIR = Path(__file__).resolve().parents[2] / "data" / "lifestyle"


def list_lifestyle_assets(brand_key: str) -> list[Path]:
    """Return sorted list of lifestyle PNG paths for a brand. Empty list if none found."""
    brand_dir = LIFESTYLE_DIR / brand_key.lower()
    if not brand_dir.exists():
        return []
    return sorted(brand_dir.glob("*.png"))


def compose_lifestyle(
    thumbnail: Image.Image,
    lifestyle_png: Path,
    position: str = "bottom-right",
    scale: float = 0.32,
) -> Image.Image:
    """Composite a lifestyle PNG element onto a thumbnail. Returns new RGB Image.

    Args:
        thumbnail: Source thumbnail (RGB, typically 1200×1200).
        lifestyle_png: Path to RGBA PNG with transparent background.
        position: 'bottom-right' or 'bottom-left'.
        scale: Lifestyle element width as fraction of thumbnail width.
    """
    result = thumbnail.convert("RGBA").copy()
    w, h = result.size

    element = Image.open(lifestyle_png).convert("RGBA")

    target_size = int(w * scale)
    elem_w, elem_h = element.size
    ratio = min(target_size / elem_w, target_size / elem_h)
    new_w = int(elem_w * ratio)
    new_h = int(elem_h * ratio)
    element = element.resize((new_w, new_h), Image.LANCZOS)

    margin = 20
    if position == "bottom-right":
        x = w - new_w - margin
        y = h - new_h - margin
    else:  # bottom-left
        x = margin
        y = h - new_h - margin

    # Soft shadow under element
    shadow_layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    shadow_elem = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    shadow_elem.paste((30, 30, 30, 80), mask=element.split()[3])
    shadow_layer.paste(shadow_elem, (x + 4, y + 6))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=8))
    result = Image.alpha_composite(result, shadow_layer)

    # Paste lifestyle element
    result.paste(element, (x, y), mask=element.split()[3])

    return result.convert("RGB")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_lifestyle_composer.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 6: Update `app/images/imgbb_uploader.py` to prefer `_lifestyle.jpg`**

In `upload_thumbnails()`, find:
```python
        path = thumb_dir / f"{p.sku}.jpg"
        if not path.exists():
            continue
```

Replace with:
```python
        path = thumb_dir / f"{p.sku}_lifestyle.jpg"
        if not path.exists():
            path = thumb_dir / f"{p.sku}.jpg"
        if not path.exists():
            continue
```

- [ ] **Step 7: Run full test suite**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add data/lifestyle/ app/images/lifestyle_composer.py app/images/imgbb_uploader.py tests/test_lifestyle_composer.py
git commit -m "feat: lifestyle PNG composer — PIL compositing, placeholder assets, imgbb prefers _lifestyle.jpg"
```

---

### Task 7: Feature H — LifestylePickerWindow + sidebar button

**Files:**
- Create: `app/gui/lifestyle_picker.py`
- Modify: `app/gui/main_window.py` (+button +2 methods)

- [ ] **Step 1: Create `app/gui/lifestyle_picker.py`**

```python
"""Lifestyle Thumbnail Picker — choose lifestyle element per brand, preview, generate."""
from __future__ import annotations

import threading
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.images.lifestyle_composer import list_lifestyle_assets, compose_lifestyle, LIFESTYLE_DIR
from app.images.thumbnail_generator import THUMB_DIR
from app.gui.brand_colors import get_brand_chip_colors
from app.parser.normalizer import Product


class LifestylePickerWindow(ctk.CTkToplevel):
    """Non-blocking window to pick lifestyle element per brand and generate composited thumbnails."""

    def __init__(self, parent, products: list[Product], on_done=None):
        super().__init__(parent)
        self.title("Lifestyle Thumbnails — wybór elementów")
        self.geometry("720x540")
        self.minsize(600, 400)
        self._products = products
        self._on_done = on_done

        # Collect brands that have thumbnails AND lifestyle assets
        self._brands = sorted({
            p.brand for p in products
            if p.brand and (THUMB_DIR / f"{p.sku}.jpg").exists()
            and list_lifestyle_assets(p.brand)
        })

        self._selections: dict[str, ctk.StringVar] = {}  # brand → selected PNG name
        self._enabled: dict[str, ctk.BooleanVar] = {}    # brand → enabled

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        ctk.CTkLabel(toolbar, text="Wybierz element lifestyle per marka",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        self._gen_btn = ctk.CTkButton(
            toolbar, text="Generuj lifestyle thumbnails",
            fg_color="#0891B2", hover_color="#0e7490",
            command=self._generate,
        )
        self._gen_btn.pack(side="right")

        scroll = ctk.CTkScrollableFrame(self, label_text="")
        scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        scroll.grid_columnconfigure(1, weight=1)

        if not self._brands:
            ctk.CTkLabel(scroll, text=(
                "Brak miniaturek lub brak elementów lifestyle.\n"
                "Uruchom najpierw krok 4.5 (Generuj miniatury)."
            ), text_color="#6B7280").pack(pady=20)
            return

        for row_idx, brand in enumerate(self._brands):
            assets = list_lifestyle_assets(brand)
            asset_names = [a.stem for a in assets]

            bg, fg = get_brand_chip_colors(brand)
            ctk.CTkLabel(scroll, text=brand.upper(),
                         fg_color=bg, text_color=fg,
                         corner_radius=4, font=ctk.CTkFont(size=11, weight="bold"),
                         padx=8).grid(row=row_idx, column=0, sticky="w", padx=8, pady=6)

            sel_var = ctk.StringVar(value=asset_names[0] if asset_names else "")
            self._selections[brand] = sel_var
            ctk.CTkOptionMenu(scroll, variable=sel_var, values=asset_names, width=200).grid(
                row=row_idx, column=1, sticky="w", padx=8, pady=6)

            en_var = ctk.BooleanVar(value=True)
            self._enabled[brand] = en_var
            ctk.CTkCheckBox(scroll, text="aktywna", variable=en_var).grid(
                row=row_idx, column=2, padx=8, pady=6)

            ctk.CTkButton(scroll, text="Podgląd", width=70,
                          command=lambda b=brand, sv=sel_var: self._preview(b, sv.get())).grid(
                row=row_idx, column=3, padx=8, pady=6)

    def _preview(self, brand: str, asset_stem: str):
        asset_path = LIFESTYLE_DIR / brand / f"{asset_stem}.png"
        if not asset_path.exists():
            return
        # Find first product of this brand with a thumbnail
        sample = next(
            (p for p in self._products if p.brand == brand
             and (THUMB_DIR / f"{p.sku}.jpg").exists()), None
        )
        if not sample:
            return
        thumb = Image.open(THUMB_DIR / f"{sample.sku}.jpg")
        result = compose_lifestyle(thumb, asset_path)

        # Show side-by-side preview in a small CTkToplevel
        win = ctk.CTkToplevel(self)
        win.title(f"Podgląd — {brand} / {asset_stem}")
        win.geometry("640x340")
        display_size = (300, 300)

        orig_ctk = ctk.CTkImage(thumb.resize(display_size), size=display_size)
        result_ctk = ctk.CTkImage(result.resize(display_size), size=display_size)

        ctk.CTkLabel(win, text="Oryginał").grid(row=0, column=0, padx=8, pady=(8, 2))
        ctk.CTkLabel(win, text="Z lifestyle").grid(row=0, column=1, padx=8, pady=(8, 2))
        ctk.CTkLabel(win, image=orig_ctk, text="").grid(row=1, column=0, padx=8, pady=4)
        ctk.CTkLabel(win, image=result_ctk, text="").grid(row=1, column=1, padx=8, pady=4)

    def _generate(self):
        self._gen_btn.configure(state="disabled", text="Generuję…")
        threading.Thread(target=self._generate_worker, daemon=True).start()

    def _generate_worker(self):
        count = 0
        for brand in self._brands:
            if not self._enabled.get(brand, ctk.BooleanVar(value=False)).get():
                continue
            asset_stem = self._selections[brand].get()
            if not asset_stem:
                continue
            asset_path = LIFESTYLE_DIR / brand / f"{asset_stem}.png"
            if not asset_path.exists():
                continue
            for p in self._products:
                if p.brand != brand:
                    continue
                src = THUMB_DIR / f"{p.sku}.jpg"
                if not src.exists():
                    continue
                result = compose_lifestyle(Image.open(src), asset_path)
                out = THUMB_DIR / f"{p.sku}_lifestyle.jpg"
                result.save(str(out), "JPEG", quality=95)
                count += 1

        if self._on_done:
            self._on_done(count)
        self.destroy()
```

- [ ] **Step 2: Add import + button + methods in `app/gui/main_window.py`**

Add import:
```python
from app.gui.lifestyle_picker import LifestylePickerWindow
```

In `_build_layout()` sidebar, after "4.6 Upload ImgBB" button, add:
```python
        self.btn_lifestyle = ctk.CTkButton(
            sidebar, text="4.7 Lifestyle thumb.", command=self._run_lifestyle,
            fg_color="#0891B2", hover_color="#0e7490",
        )
        self.btn_lifestyle.pack(fill="x", padx=12, pady=4)
```

Add methods:
```python
    def _run_lifestyle(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        self.btn_lifestyle.configure(state="disabled")
        LifestylePickerWindow(self, self.products, on_done=self._lifestyle_done)

    def _lifestyle_done(self, count: int) -> None:
        self.btn_lifestyle.configure(state="normal")
        self.status_var.set(f"Lifestyle: {count} miniaturek zapisanych jako *_lifestyle.jpg.")
        messagebox.showinfo(APP_NAME,
            f"Lifestyle thumbnails gotowe!\n{count} plików zapisanych w output/thumbnails/\n"
            "Format: {{sku}}_lifestyle.jpg\nImgBB upload będzie preferować te pliki.")
```

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/gui/lifestyle_picker.py app/gui/main_window.py
git commit -m "feat: LifestylePickerWindow — per-brand selection, before/after preview, batch generate"
```

---

## Summary

| Task | Feature | Key deliverable |
|------|---------|----------------|
| 1 | D | Attributes injected into Gemini prompt |
| 2 | C | Category JSON + transformer + tests |
| 3 | C | CategoryMapperWindow + KAT column in table |
| 4 | C | Exporter writes Allegro category |
| 5 | F | Full audit preview HTML in browser |
| 6 | H | PIL lifestyle composer + placeholder assets + tests |
| 7 | H | LifestylePickerWindow + sidebar button |
