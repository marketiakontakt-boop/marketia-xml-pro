# Parameters + Brand + GUI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add XML attribute extraction, inline brand editing, and GUI Light Modern redesign to marketia-xml-pro.

**Architecture:** Three independent feature slices: (1) data layer — parse+export attributes, (2) UX layer — inline brand dropdown, (3) visual layer — light theme + brand chips + logo.

**Tech Stack:** Python 3.14, customtkinter, lxml, Pillow (already installed), re (stdlib)

---

### Task 1: Add `attributes` field to Product + parse from XML

**Files:**
- Modify: `app/parser/normalizer.py`
- Test: `tests/test_normalizer_attributes.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_normalizer_attributes.py
import pytest
from lxml import etree
from app.parser.normalizer import normalize_product


def _make_product_elem(attrs: list[tuple[str, str]]) -> etree._Element:
    xml_str = "<product>"
    xml_str += "<product_id>1</product_id><sku>TEST-001</sku><ean></ean>"
    xml_str += "<price>0</price><purchase_price>0</purchase_price>"
    xml_str += "<tax_rate>23%</tax_rate><weight>0</weight>"
    xml_str += "<width>0</width><height>0</height><length>0</length>"
    xml_str += "<quantity>0</quantity><name>Test</name>"
    xml_str += "<category_name></category_name><manufacturer_name></manufacturer_name>"
    xml_str += "<description></description>"
    xml_str += "<description_extra_1></description_extra_1>"
    xml_str += "<description_extra_2></description_extra_2>"
    xml_str += "<attributes>"
    for name, value in attrs:
        xml_str += f"<attribute><attribute_name>{name}</attribute_name><attribute_value>{value}</attribute_value></attribute>"
    xml_str += "</attributes></product>"
    return etree.fromstring(xml_str)


def test_attributes_parsed_from_xml():
    elem = _make_product_elem([("Waga", "3.5"), ("Kolor", "Niebieski")])
    p = normalize_product(elem)
    assert p.attributes == {"Waga": "3.5", "Kolor": "Niebieski"}


def test_empty_attributes():
    elem = _make_product_elem([])
    p = normalize_product(elem)
    assert p.attributes == {}


def test_no_attributes_element():
    xml_str = "<product><product_id>1</product_id><sku>T</sku><ean></ean>"
    xml_str += "<price>0</price><purchase_price>0</purchase_price>"
    xml_str += "<tax_rate>23%</tax_rate><weight>0</weight>"
    xml_str += "<width>0</width><height>0</height><length>0</length>"
    xml_str += "<quantity>0</quantity><name>T</name>"
    xml_str += "<category_name></category_name><manufacturer_name></manufacturer_name>"
    xml_str += "<description></description>"
    xml_str += "<description_extra_1></description_extra_1>"
    xml_str += "<description_extra_2></description_extra_2>"
    xml_str += "</product>"
    elem = etree.fromstring(xml_str)
    p = normalize_product(elem)
    assert p.attributes == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_normalizer_attributes.py -v
```
Expected: FAIL — `Product` has no `attributes` field.

- [ ] **Step 3: Implement `_collect_attributes` and add `attributes` field to `Product`**

In `app/parser/normalizer.py`, after the `_int` function, add:

```python
def _collect_attributes(elem: Any) -> dict[str, str]:
    """Parse <attributes><attribute> children into a name→value dict."""
    attrs_elem = elem.find("attributes")
    if attrs_elem is None:
        return {}
    result: dict[str, str] = {}
    for attr in attrs_elem.findall("attribute"):
        name = _text(attr, "attribute_name")
        value = _text(attr, "attribute_value")
        if name and value:
            result[name] = value
    return result
```

In the `Product` dataclass, after `thumbnail_url: str = ""`, add:
```python
attributes: dict[str, str] = field(default_factory=dict)
```

In `normalize_product()`, add to the `Product(...)` constructor call:
```python
attributes=_collect_attributes(elem),
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_normalizer_attributes.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && git add app/parser/normalizer.py tests/test_normalizer_attributes.py && git commit -m "feat: add attributes field to Product, parse from XML <attributes>"
```

---

### Task 2: Attribute extractor — regex from HTML description

**Files:**
- Create: `app/transformer/attribute_extractor.py`
- Test: `tests/test_attribute_extractor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_attribute_extractor.py
import pytest
from app.transformer.attribute_extractor import extract_attributes_from_html, enrich_product_attributes
from app.parser.normalizer import Product


def _make_product(desc: str, existing_attrs: dict | None = None) -> Product:
    p = Product(
        product_id="1", sku="T", ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0,
        quantity=0, name="Test", category_name="", manufacturer_name="",
        description=desc, description_extra_1="", description_extra_2="",
    )
    if existing_attrs:
        p.attributes = existing_attrs
    return p


def test_extract_dimensions():
    html = "<p>Wymiary: 120 x 60 x 45 cm. Idealne do ogrodu.</p>"
    result = extract_attributes_from_html(html)
    assert "Wymiary" in result
    assert "120" in result["Wymiary"]


def test_extract_capacity():
    html = "<p>Pojemność: 3000 l. Basen prostokątny.</p>"
    result = extract_attributes_from_html(html)
    assert "Pojemność" in result
    assert "3000" in result["Pojemność"]


def test_extract_material():
    html = "<p>Materiał: tworzywo PVC wysokiej jakości.</p>"
    result = extract_attributes_from_html(html)
    assert "Materiał" in result


def test_extract_max_load():
    html = "<p>Maks. obciążenie: 120 kg na osobę.</p>"
    result = extract_attributes_from_html(html)
    assert "Maks. obciążenie" in result
    assert "120" in result["Maks. obciążenie"]


def test_enrich_does_not_overwrite_existing():
    p = _make_product("<p>Waga: 5 kg.</p>", existing_attrs={"Waga": "3.5"})
    enrich_product_attributes(p)
    assert p.attributes["Waga"] == "3.5"  # XML value preserved


def test_enrich_adds_missing():
    p = _make_product("<p>Materiał: aluminium.</p>", existing_attrs={})
    enrich_product_attributes(p)
    assert "Materiał" in p.attributes


def test_empty_html():
    result = extract_attributes_from_html("")
    assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_attribute_extractor.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `app/transformer/attribute_extractor.py`**

```python
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
    ), "single"),
    ("Wysokość", re.compile(
        r"wysoko[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*cm", re.IGNORECASE
    ), "single"),
    ("Głębokość", re.compile(
        r"g[łl][eę]boko[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*cm", re.IGNORECASE
    ), "single"),
    ("Pojemność", re.compile(
        r"pojemno[sś][cć]\s*[:\-]\s*(\d+[\.,]?\d*)\s*(?:l\b|litr)", re.IGNORECASE
    ), "single"),
    ("Materiał", re.compile(
        r"materia[łl]\s*[:\-]\s*([^<\n,.]{3,40})", re.IGNORECASE
    ), "text"),
    ("Kolor", re.compile(
        r"kolor\s*[:\-]\s*([^<\n,.]{3,30})", re.IGNORECASE
    ), "text"),
    ("Maks. obciążenie", re.compile(
        r"(?:maks?\.?\s*obci[aą][żz]enie|max\.?\s*load)\s*[:\-]?\s*(\d+[\.,]?\d*)\s*kg",
        re.IGNORECASE,
    ), "single"),
    ("Waga", re.compile(
        r"\bwaga\s*[:\-]\s*(\d+[\.,]?\d*)\s*kg", re.IGNORECASE
    ), "single"),
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
        elif kind == "single":
            result[attr_name] = m.group(1).replace(",", ".") + (" cm" if "Głębokość" in attr_name or "Szerokość" in attr_name or "Wysokość" in attr_name else "")
        elif kind == "text":
            result[attr_name] = m.group(1).strip()
    return result


def enrich_product_attributes(product: "Product") -> None:
    """Add regex-extracted attributes to product.attributes; XML values take precedence."""
    extracted = extract_attributes_from_html(product.description or "")
    for k, v in extracted.items():
        if k not in product.attributes:
            product.attributes[k] = v
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_attribute_extractor.py -v
```
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && git add app/transformer/attribute_extractor.py tests/test_attribute_extractor.py && git commit -m "feat: attribute_extractor — regex extraction from HTML descriptions"
```

---

### Task 3: Wire attribute enrichment into transforms + export XML

**Files:**
- Modify: `app/gui/main_window.py` (import + call in `_transform_worker`)
- Modify: `app/exporter/xml_exporter.py` (write `<attributes>` block)
- Test: `tests/test_xml_exporter_attributes.py`

- [ ] **Step 1: Write failing test for exporter**

```python
# tests/test_xml_exporter_attributes.py
import tempfile
from pathlib import Path
from lxml import etree
from app.parser.normalizer import Product
from app.exporter.xml_exporter import export_xml


def _make_product(attrs: dict) -> Product:
    p = Product(
        product_id="1", sku="TEST-001", ean="5901234123457",
        price=99.99, purchase_price=50.0, tax_rate="23%",
        weight=2.5, width=0.0, height=0.0, length=0.0,
        quantity=10, name="Produkt testowy", category_name="Test",
        manufacturer_name="Brand", description="<p>Opis</p>",
        description_extra_1="", description_extra_2="",
    )
    p.attributes = attrs
    return p


def test_attributes_exported():
    p = _make_product({"Waga": "3.5", "Kolor": "Niebieski"})
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        path = f.name
    export_xml([p], path)
    tree = etree.parse(path)
    attrs_elem = tree.find(".//attributes")
    assert attrs_elem is not None
    names = {a.findtext("attribute_name") for a in attrs_elem.findall("attribute")}
    assert "Waga" in names
    assert "Kolor" in names


def test_empty_attributes_not_exported():
    p = _make_product({})
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        path = f.name
    export_xml([p], path)
    tree = etree.parse(path)
    attrs_elem = tree.find(".//attributes")
    assert attrs_elem is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/test_xml_exporter_attributes.py -v
```
Expected: `test_attributes_exported` FAIL — no `<attributes>` in output.

- [ ] **Step 3: Update `xml_exporter.py` — add `<attributes>` block**

In `_product_to_element()`, after the images block, add:

```python
if getattr(p, "attributes", None):
    attrs_elem = etree.SubElement(e, "attributes")
    for name, value in p.attributes.items():
        attr = etree.SubElement(attrs_elem, "attribute")
        etree.SubElement(attr, "attribute_name").text = name
        etree.SubElement(attr, "attribute_value").text = str(value)
```

- [ ] **Step 4: Update `main_window.py` — call enrichment in transforms**

At top of file, add import (after existing transformer imports):
```python
from app.transformer.attribute_extractor import enrich_product_attributes
```

In `_transform_worker()`, after `load_cached_descriptions(self.products)`:
```python
for p in self.products:
    enrich_product_attributes(p)
```

- [ ] **Step 5: Run all tests**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && pytest tests/ -v
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && git add app/exporter/xml_exporter.py app/gui/main_window.py tests/test_xml_exporter_attributes.py && git commit -m "feat: export <attributes> to XML, enrich product attrs after transforms"
```

---

### Task 4: Inline brand dropdown in ProductRow

**Files:**
- Modify: `app/gui/main_window.py` (ProductRow + _render_table)

No new tests needed (GUI widget, not logic).

- [ ] **Step 1: Update `ProductRow.__init__` to accept `all_brands` and `on_brand_change`**

Change the `ProductRow.__init__` signature:
```python
def __init__(self, master, product: Product, on_click=None, all_brands: list[str] | None = None, on_brand_change=None, **kwargs):
```

Replace the brand label grid call:
```python
ctk.CTkLabel(self, text=product.brand or "—", anchor="w").grid(row=0, column=2, sticky="w", padx=4)
```
With:
```python
if all_brands and on_brand_change:
    _brand_var = ctk.StringVar(value=product.brand or "—")
    ctk.CTkOptionMenu(
        self,
        variable=_brand_var,
        values=all_brands,
        width=105, height=26,
        command=lambda v, p=product: on_brand_change(p, v),
    ).grid(row=0, column=2, sticky="w", padx=4, pady=2)
else:
    ctk.CTkLabel(self, text=product.brand or "—", anchor="w").grid(row=0, column=2, sticky="w", padx=4)
```

- [ ] **Step 2: Update `_render_table` in `App` to pass brands and callback**

Find the block in `_render_table()` that creates `ProductRow`:
```python
row = ProductRow(self.list_frame, p, on_click=lambda prod=p: self._on_row_click(prod))
```

Replace with:
```python
brands = sorted({q.brand for q in self.products if q.brand})
row = ProductRow(
    self.list_frame, p,
    on_click=lambda prod=p: self._on_row_click(prod),
    all_brands=brands if brands else None,
    on_brand_change=self._on_brand_change if brands else None,
)
```

Note: Move the `brands` computation outside the `enumerate` loop — compute once before the loop:

```python
cap = 300
filtered = self._filtered_products()
brands = sorted({p.brand for p in self.products if p.brand})
for idx, p in enumerate(filtered[:cap], 1):
    row = ProductRow(
        self.list_frame, p,
        on_click=lambda prod=p: self._on_row_click(prod),
        all_brands=brands if brands else None,
        on_brand_change=self._on_brand_change if brands else None,
    )
    row.grid(row=idx, column=0, sticky="ew", pady=1)
```

- [ ] **Step 3: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && git add app/gui/main_window.py && git commit -m "feat: inline brand dropdown in ProductRow for pre-AI brand correction"
```

---

### Task 5: GUI Light Modern — brand colors module + ProductRow card style

**Files:**
- Create: `app/gui/brand_colors.py`
- Modify: `app/gui/main_window.py` (theme, ProductRow redesign, sidebar, stats bar)

- [ ] **Step 1: Create `app/gui/brand_colors.py`**

```python
"""Brand-specific color chips for Light Modern theme."""
from __future__ import annotations

BRAND_COLORS: dict[str, tuple[str, str]] = {
    "intex":         ("#DBEAFE", "#1D4ED8"),
    "gardenstein":   ("#DCFCE7", "#15803D"),
    "villago":       ("#FFEDD5", "#C2410C"),
    "zoovera":       ("#EDE9FE", "#6D28D9"),
    "marketia_home": ("#E0F2FE", "#0369A1"),
    "hopla_toys":    ("#FCE7F3", "#9D174D"),
}
_DEFAULT = ("#F3F4F6", "#374151")


def get_brand_chip_colors(brand_key: str) -> tuple[str, str]:
    """Return (bg_color, text_color) for brand chip."""
    return BRAND_COLORS.get(brand_key.lower(), _DEFAULT)
```

- [ ] **Step 2: Switch to light mode in `main_window.py`**

Change at top of file (before App class):
```python
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")
```

- [ ] **Step 3: Update `ProductRow` for card style + brand chip colors**

Add import at top:
```python
from app.gui.brand_colors import get_brand_chip_colors
```

Change `ProductRow.__init__` super().__init__ call — replace:
```python
super().__init__(master, fg_color=bg or "transparent", **kwargs)
```
With:
```python
card_bg = "#FFFFFF"
super().__init__(master, fg_color=card_bg, border_width=1, border_color="#E5E7EB", corner_radius=6, **kwargs)
```

Update brand section in `ProductRow.__init__` — when showing the inline dropdown with brand colors:

In the `all_brands and on_brand_change` branch, add fg_color styling:
```python
if all_brands and on_brand_change:
    _brand_var = ctk.StringVar(value=product.brand or "—")
    bg_c, fg_c = get_brand_chip_colors(product.brand or "")
    ctk.CTkOptionMenu(
        self,
        variable=_brand_var,
        values=all_brands,
        width=105, height=26,
        fg_color=bg_c, text_color=fg_c,
        button_color=bg_c, button_hover_color="#E5E7EB",
        dropdown_fg_color="white",
        font=ctk.CTkFont(size=10, weight="bold"),
        command=lambda v, p=product: on_brand_change(p, v),
    ).grid(row=0, column=2, sticky="w", padx=4, pady=2)
else:
    bg_c, fg_c = get_brand_chip_colors(product.brand or "")
    ctk.CTkLabel(
        self, text=(product.brand or "—").upper()[:10],
        fg_color=bg_c, text_color=fg_c,
        corner_radius=4, font=ctk.CTkFont(size=10, weight="bold"),
    ).grid(row=0, column=2, sticky="w", padx=4, pady=4)
```

- [ ] **Step 4: Update list frame for light style**

Change `CTkScrollableFrame` creation:
```python
self.list_frame = ctk.CTkScrollableFrame(main, label_text="", fg_color="#FAFAFA")
```

Update header row:
```python
header_row = ctk.CTkFrame(self.list_frame, fg_color="#F3F4F6", corner_radius=4)
```
And header label text_color: `text_color="#6B7280"`.

- [ ] **Step 5: Update stats bar for chip style**

Replace `_build_stats_bar()` with a version that uses colored chips:
```python
def _build_stats_bar(self) -> None:
    self._stat_total = ctk.StringVar(value="Produkty: —")
    self._stat_ai    = ctk.StringVar(value="AI: —")
    self._stat_q     = ctk.StringVar(value="Q: —")
    self._stat_cost  = ctk.StringVar(value="~$0.00")
    self._stat_cache = ctk.StringVar(value="Cache: —")

    bar = ctk.CTkFrame(self, fg_color="transparent")
    bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))

    chip_specs = [
        (self._stat_total, "#DBEAFE", "#1D4ED8"),
        (self._stat_ai,    "#DCFCE7", "#15803D"),
        (self._stat_q,     "#FEF3C7", "#92400E"),
        (self._stat_cost,  "#F3F4F6", "#374151"),
        (self._stat_cache, "#EDE9FE", "#6D28D9"),
    ]
    for var, bg, fg in chip_specs:
        ctk.CTkLabel(
            bar, textvariable=var,
            fg_color=bg, text_color=fg,
            corner_radius=12,
            font=ctk.CTkFont(size=11, weight="bold"),
            padx=10, pady=4,
        ).pack(side="left", padx=4, pady=4)
```

- [ ] **Step 6: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && git add app/gui/brand_colors.py app/gui/main_window.py && git commit -m "feat: Light Modern theme — card rows, brand color chips, stats chips"
```

---

### Task 6: Sidebar logo + polish

**Files:**
- Modify: `app/gui/main_window.py` (sidebar logo, light sidebar style)
- Modify: `app/gui/product_detail.py` (light theme compatibility)

- [ ] **Step 1: Add logo to sidebar**

At top of `main_window.py`, add imports:
```python
from PIL import Image
```
(Pillow is already installed; if not: `pip install Pillow`)

In `_build_layout()`, replace the text label block in sidebar:
```python
ctk.CTkLabel(
    sidebar, text="MARKETIA\nXML PRO", font=ctk.CTkFont(size=18, weight="bold")
).pack(pady=(20, 4))
ctk.CTkLabel(
    sidebar, text="v2 — z Claude AI",
    text_color="#888", font=ctk.CTkFont(size=10),
).pack(pady=(0, 18))
```

With:
```python
_logo_path = Path("/Users/jakubknap/Documents/_meta/logo/LOGO MARKETIA.png")
try:
    _logo_img = ctk.CTkImage(Image.open(_logo_path), size=(155, 48))
    ctk.CTkLabel(sidebar, image=_logo_img, text="").pack(pady=(16, 2))
except Exception:
    ctk.CTkLabel(
        sidebar, text="MARKETIA", font=ctk.CTkFont(size=18, weight="bold")
    ).pack(pady=(20, 2))
ctk.CTkLabel(
    sidebar, text="XML PRO",
    text_color="#6B7280", font=ctk.CTkFont(size=11, weight="bold"),
).pack(pady=(0, 16))
```

- [ ] **Step 2: Light sidebar frame**

Change sidebar creation:
```python
sidebar = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color="#F9FAFB", border_width=0)
```

- [ ] **Step 3: Update sidebar buttons for light style**

The `fg_color` buttons already use explicit colors (blue, green, purple) — those look fine in light mode. No change needed.

Update the "2. Marka (auto)" button label to "2. Marka (inline)":
```python
ctk.CTkButton(sidebar, text="2. Marka (inline)", command=self._no_op).pack(
    fill="x", padx=12, pady=4
)
```

Update `_no_op` to explain inline brand editing:
```python
def _no_op(self):
    messagebox.showinfo(
        APP_NAME,
        "Marka jest liczona automatycznie podczas transformów (krok 3).\n\n"
        "Możesz zmienić markę inline — kliknij dropdown marki przy produkcie w liście.",
    )
```

- [ ] **Step 4: Update filter bar for light style**

Replace filter bar background:
```python
bar = ctk.CTkFrame(parent, fg_color="#F3F4F6", corner_radius=8)
bar.grid(row=1, column=0, sticky="ew", pady=(0, 6), padx=0)
```

Add padx to labels inside:
```python
ctk.CTkLabel(bar, text="Marka:", text_color="#374151").pack(side="left", padx=(12, 2))
```
```python
ctk.CTkLabel(bar, text="Status AI:", text_color="#374151").pack(side="left", padx=(8, 2))
```

- [ ] **Step 5: product_detail.py — no changes needed**

`ProductDetailWindow` uses CTkToplevel which inherits the global appearance mode. The light theme will apply automatically. Verify by inspection — no code changes required.

- [ ] **Step 6: Run the app to verify visually**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && source venv/bin/activate && python -m app.gui.main_window &
```
Check: light background, logo shows in sidebar, brand chips are colored.

- [ ] **Step 7: Commit**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro && git add app/gui/main_window.py app/gui/product_detail.py && git commit -m "feat: sidebar logo, light theme sidebar, filter bar polish"
```

---

## Summary

| Task | Files | Status |
|------|-------|--------|
| 1 | normalizer.py + test | ⬜ |
| 2 | attribute_extractor.py + test | ⬜ |
| 3 | xml_exporter.py + main_window.py + test | ⬜ |
| 4 | main_window.py (inline brand) | ⬜ |
| 5 | brand_colors.py + main_window.py | ⬜ |
| 6 | main_window.py (logo + polish) | ⬜ |
