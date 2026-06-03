# Marketia XML Pro — Parameters, Inline Brand, GUI Redesign

**Date:** 2026-05-26  
**Scope:** 3 features: attributes extraction, inline brand selection, GUI Light Modern redesign

---

## 1. Parameters / Attributes Extraction

**Problem:** BaseLinker shows "Ten produkt nie ma jeszcze żadnych parametrów" — the exporter does not write `<attributes>` to XML.

**Solution:** Parse existing `<attributes>` from source XML, supplement with regex-extracted params from description HTML, export the merged dict back as `<attributes>`.

### 1.1 Data model — `normalizer.py`

Add field to `Product`:
```python
attributes: dict[str, str] = field(default_factory=dict)
```

Add parser helper `_collect_attributes(elem) -> dict[str, str]`:
```python
attrs_elem = elem.find("attributes")
if attrs_elem is None:
    return {}
result = {}
for attr in attrs_elem.findall("attribute"):
    name = _text(attr, "attribute_name")
    value = _text(attr, "attribute_value")
    if name and value:
        result[name] = value
return result
```

Call it in `normalize_product()`:
```python
attributes=_collect_attributes(elem),
```

### 1.2 Extractor — `app/transformer/attribute_extractor.py` (NEW)

Regex patterns extracted from Polish HTML description text. Merges into existing `product.attributes` — existing keys (from XML) take precedence.

Patterns to detect (Polish):
| Attribute name (BaseLinker) | Regex patterns |
|---|---|
| `Wymiary` | `(\d+[\.,]?\d*)\s*[xX×]\s*(\d+[\.,]?\d*)\s*(?:[xX×]\s*(\d+[\.,]?\d*))?\s*cm` |
| `Szerokość` | `szerokość[:\s]+(\d+[\.,]?\d*)\s*cm` |
| `Wysokość` | `wysoko[sś][cć][:\s]+(\d+[\.,]?\d*)\s*cm` |
| `Głębokość` | `głęboko[sś][cć][:\s]+(\d+[\.,]?\d*)\s*cm` |
| `Pojemność` | `pojemno[sś][cć][:\s]+(\d+[\.,]?\d*)\s*(l\b|litr)` |
| `Materiał` | `materiał[:\s]+([^<\n,.]{3,40})` |
| `Kolor` | `kolor[:\s]+([^<\n,.]{3,30})` |
| `Maks. obciążenie` | `(?:maks?\.?\s*obci[aą]żenie|max\.?\s*load)[:\s]+(\d+[\.,]?\d*)\s*kg` |
| `Waga` | `waga[:\s]+(\d+[\.,]?\d*)\s*kg` |

Function signature:
```python
def extract_attributes_from_html(html: str) -> dict[str, str]:
    """Return dict of attribute_name → attribute_value extracted from HTML text."""
```

Strip HTML tags before regex matching: `re.sub(r'<[^>]+>', ' ', html)`.

Merge strategy in `enrich_product_attributes(product: Product) -> None`:
```python
extracted = extract_attributes_from_html(product.description or "")
for k, v in extracted.items():
    if k not in product.attributes:  # XML values take precedence
        product.attributes[k] = v
```

### 1.3 Transform integration

Call `enrich_product_attributes(p)` for each product at the end of `App._transform_worker()` in `main_window.py`.

### 1.4 Export — `xml_exporter.py`

Add `<attributes>` block in `_product_to_element()`:
```python
if p.attributes:
    attrs_elem = etree.SubElement(e, "attributes")
    for name, value in p.attributes.items():
        attr = etree.SubElement(attrs_elem, "attribute")
        etree.SubElement(attr, "attribute_name").text = name
        etree.SubElement(attr, "attribute_value").text = str(value)
```

---

## 2. Inline Brand Selection

**Problem:** Users need to correct brand BEFORE AI generation. Current brand column is read-only text. The detail popup works but requires an extra click and is only available after transforms.

**Solution:** Replace the brand label in `ProductRow` with a compact `CTkOptionMenu`. The widget is always visible in the product list.

### 2.1 ProductRow changes

`ProductRow.__init__` receives two new optional params:
- `all_brands: list[str] = []` — options for the dropdown
- `on_brand_change: Callable[[Product, str], None] | None = None`

Replace:
```python
ctk.CTkLabel(self, text=product.brand or "—", anchor="w").grid(...)
```
With:
```python
brand_var = ctk.StringVar(value=product.brand or "—")
ctk.CTkOptionMenu(
    self,
    variable=brand_var,
    values=all_brands or [product.brand or "—"],
    width=100,
    height=24,
    command=lambda v, p=product: on_brand_change(p, v) if on_brand_change else None,
).grid(row=0, column=2, sticky="w", padx=4)
```

### 2.2 App._render_table changes

Pass brands and callback to each `ProductRow`:
```python
brands = sorted({p.brand for p in self.products if p.brand})
row = ProductRow(
    self.list_frame, p,
    all_brands=brands,
    on_brand_change=self._on_brand_change,
    on_click=lambda prod=p: self._on_row_click(prod),
)
```

The `_on_brand_change` callback already exists from the detail popup work:
```python
def _on_brand_change(self, product: Product, new_brand: str) -> None:
    product.brand = new_brand
    self._render_table()
```

### 2.3 Click propagation

The brand `CTkOptionMenu` must NOT trigger the row click. Since CTkOptionMenu handles its own events, clicks on it will not propagate to the frame's `<Button-1>` binding. No extra changes needed.

---

## 3. GUI Redesign — Light Modern (B)

**Direction:** Light theme, card-style rows, brand color chips, Marketia logo in sidebar.

### 3.1 Global theme

```python
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")
```

### 3.2 Brand color map

New module `app/gui/brand_colors.py`:
```python
BRAND_COLORS: dict[str, tuple[str, str]] = {
    # brand_key: (bg_color, text_color)
    "intex":         ("#DBEAFE", "#1D4ED8"),  # blue
    "gardenstein":   ("#DCFCE7", "#15803D"),  # green
    "villago":       ("#FFEDD5", "#C2410C"),  # orange
    "zoovera":       ("#EDE9FE", "#6D28D9"),  # purple
    "marketia_home": ("#E0F2FE", "#0369A1"),  # cyan
    "hopla_toys":    ("#FCE7F3", "#9D174D"),  # pink
}
DEFAULT_BRAND_COLOR = ("#F3F4F6", "#374151")  # gray

def get_brand_chip_colors(brand_key: str) -> tuple[str, str]:
    return BRAND_COLORS.get(brand_key, DEFAULT_BRAND_COLOR)
```

### 3.3 ProductRow redesign

Card style: white bg + subtle border + rounded corners:
```python
super().__init__(master, fg_color="white", border_width=1, border_color="#E5E7EB", corner_radius=6, **kwargs)
```

Brand chip: replace brand column with colored chip label:
```python
bg, fg = get_brand_chip_colors(product.brand or "")
chip = ctk.CTkLabel(
    self,
    text=(product.brand or "—").upper()[:8],
    fg_color=bg, text_color=fg,
    corner_radius=4,
    font=ctk.CTkFont(size=10, weight="bold"),
)
chip.grid(row=0, column=2, sticky="w", padx=4, pady=4)
```

**Note:** The brand chip is READ-ONLY display. The inline brand dropdown (Feature 2) goes in column 2 instead, replacing the chip. Both can coexist: chip for display-only mode, dropdown for interactive mode.

**Decision:** Keep both. The `CTkOptionMenu` already shows the current brand and provides the chip-like look with the brand name visible. The chip styling can be applied via custom foreground color on the menu. Use a `CTkOptionMenu` styled with `fg_color=bg, text_color=fg` to get chip appearance with dropdown functionality:
```python
ctk.CTkOptionMenu(
    self,
    variable=brand_var,
    values=all_brands or [product.brand or "—"],
    width=110, height=26,
    fg_color=bg, text_color=fg,
    button_color=bg, button_hover_color="#E5E7EB",
    dropdown_fg_color="white",
    font=ctk.CTkFont(size=10, weight="bold"),
    command=...,
)
```

### 3.4 Sidebar redesign

Logo in sidebar:
```python
from PIL import Image
import customtkinter as ctk

logo_path = Path("/Users/jakubknap/Documents/_meta/logo/LOGO MARKETIA.png")
logo_img = ctk.CTkImage(Image.open(logo_path), size=(160, 50))
ctk.CTkLabel(sidebar, image=logo_img, text="").pack(pady=(16, 4))
ctk.CTkLabel(sidebar, text="XML PRO", font=ctk.CTkFont(size=12, weight="bold"), text_color="#6B7280").pack(pady=(0, 16))
```

Light sidebar: `fg_color="#F9FAFB"`, border on right side (not possible directly in CTK — use a 1px-wide frame as divider).

Sidebar button style (light):
```python
ctk.CTkButton(
    sidebar, text="1. Wczytaj XML",
    fg_color="#2563EB", hover_color="#1D4ED8", text_color="white",
    corner_radius=6, height=34,
    command=self._pick_xml
)
```

### 3.5 Header and filter bar

Remove the summary label from above the filter bar — move it into the filter bar row as a compact text. 

Filter bar background: `fg_color="#F3F4F6"`, rounded `corner_radius=8`.

### 3.6 Stats bar redesign

Stats as pill chips:
```python
for text, var, color in [
    ("Produkty", self._stat_total, "#2563EB"),
    ("AI", self._stat_ai, "#16A34A"),
    ("Q avg", self._stat_q, "#D97706"),
    ("Koszt", self._stat_cost, "#6B7280"),
    ("Cache", self._stat_cache, "#7C3AED"),
]:
    ctk.CTkLabel(bar, textvariable=var, fg_color=color+"22", text_color=color, corner_radius=12, ...)
```

### 3.7 List frame

`CTkScrollableFrame` with light label: `fg_color="#FAFAFA"`, `label_text=""`, `label_fg_color="#F3F4F6"`.

Header row: `fg_color="#F3F4F6"`, text_color `#6B7280`, border bottom effect via margin.

### 3.8 Logo fallback

If logo file missing, show text "MARKETIA" in bold (no crash). Use try/except around `Image.open()`.

---

## Files changed

| File | Type | Changes |
|------|------|---------|
| `app/parser/normalizer.py` | Modify | Add `attributes` field + `_collect_attributes()` |
| `app/transformer/attribute_extractor.py` | New | Regex extraction from HTML |
| `app/exporter/xml_exporter.py` | Modify | Export `<attributes>` block |
| `app/gui/brand_colors.py` | New | Brand color map |
| `app/gui/main_window.py` | Modify | Light theme, call attribute enrichment, pass brands to rows |
| `app/gui/product_detail.py` | Modify | Light theme compatibility |
| `tests/test_attribute_extractor.py` | New | Tests for regex extraction |
| `tests/test_xml_exporter_attributes.py` | New | Tests for attribute export |

**No database changes. No breaking changes to existing XML format (new `<attributes>` block added, all existing fields unchanged).**
