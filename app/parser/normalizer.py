"""Normalize a raw BaseLinker <product> XML element into a Product dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _text(elem: Any, tag: str, default: str = "") -> str:
    """Defensive text extractor for an lxml element child."""
    if elem is None:
        return default
    found = elem.find(tag)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def _float(elem: Any, tag: str, default: float = 0.0) -> float:
    raw = _text(elem, tag)
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return default


def _int(elem: Any, tag: str, default: int = 0) -> int:
    raw = _text(elem, tag)
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


@dataclass
class Product:
    # --- Immutable (per V4 rules: SKU/product_id/price/weight/stock never change) ---
    product_id: str
    sku: str
    ean: str
    price: float
    purchase_price: float
    tax_rate: str           # e.g. "23%"
    weight: float
    width: float
    height: float
    length: float
    quantity: int

    # --- Display / transformable ---
    name: str
    category_name: str
    manufacturer_name: str

    # --- Source content (kept verbatim until transformer rewrites it) ---
    description: str        # may be HTML, html-escaped in source XML
    description_extra_1: str
    description_extra_2: str

    # --- Images: list of non-empty URLs in declared order ---
    images: list[str] = field(default_factory=list)

    # --- Derived (populated by transformers; empty until then) ---
    brand: str = ""         # e.g. "villago" (matches data/brand_keywords.json key)
    model_name: str = ""    # e.g. "Milan"
    title: str = ""         # transformed final title (≤75 chars, UPPERCASE)

    # --- Optional reference to source element for round-trip XML output ---
    raw_element: Any = None

    # --- Validation / scoring (populated after transforms) ---
    ean_valid: bool = True
    quality_score: int = -1   # -1 = not yet scored
    ai_done: bool = False
    thumbnail_url: str = ""   # local path or ImgBB URL after Phase 4
    attributes: dict[str, str] = field(default_factory=dict)
    allegro_category: str = ""   # populated by category_mapper transformer
    variant_group_id: int = 0    # 0 = not grouped; same positive int = same variant group
    variant_name: str = ""       # color/size suffix, e.g. "Białe", "XL"


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


def _collect_images(elem: Any) -> list[str]:
    """Pull <image> + <image_extra_1> .. <image_extra_15> in declared order; skip empty."""
    urls: list[str] = []
    main = _text(elem, "image")
    if main:
        urls.append(main)
    for i in range(1, 16):
        url = _text(elem, f"image_extra_{i}")
        if url:
            urls.append(url)
    return urls


def normalize_product(elem: Any) -> Product:
    """Convert an lxml <product> element to a Product dataclass."""
    return Product(
        product_id=_text(elem, "product_id"),
        sku=_text(elem, "sku"),
        ean=_text(elem, "ean"),
        price=_float(elem, "price"),
        purchase_price=_float(elem, "purchase_price"),
        tax_rate=_text(elem, "tax_rate"),
        weight=_float(elem, "weight"),
        width=_float(elem, "width"),
        height=_float(elem, "height"),
        length=_float(elem, "length"),
        quantity=_int(elem, "quantity"),
        name=_text(elem, "name"),
        category_name=_text(elem, "category_name"),
        manufacturer_name=_text(elem, "manufacturer_name"),
        description=_text(elem, "description"),
        description_extra_1=_text(elem, "description_extra_1"),
        description_extra_2=_text(elem, "description_extra_2"),
        images=_collect_images(elem),
        raw_element=elem,
        attributes=_collect_attributes(elem),
    )
