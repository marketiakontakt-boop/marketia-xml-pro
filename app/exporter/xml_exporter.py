"""Export transformed products back to BaseLinker-compatible XML."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from app.parser.normalizer import Product


def export_xml(
    products: list[Product],
    output_path: Path | str,
    include_variants: bool = False,
) -> int:
    """Write transformed XML. Returns count of products written.

    include_variants=True adds <variant_group_id> and <variant_name> tags
    for products that belong to a variant group (variant_group_id > 0).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    root = etree.Element("products")
    for p in products:
        root.append(_product_to_element(p, include_variants=include_variants))

    etree.ElementTree(root).write(
        str(output_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="utf-8",
    )
    return len(products)


def _product_to_element(p: Product, include_variants: bool = False) -> etree._Element:
    e = etree.Element("product")

    def add(tag: str, value) -> None:
        child = etree.SubElement(e, tag)
        child.text = str(value) if value is not None else ""

    add("product_id", p.product_id)
    add("name", p.title if p.title else p.name)
    add("quantity", p.quantity)
    add("ean", p.ean)
    add("sku", p.sku)
    add("category_name", getattr(p, "allegro_category", "") or p.category_name)
    # Use the program's assigned brand display name — never the original supplier name
    add("manufacturer_name", p.manufacturer_name)
    add("price", p.price)
    add("purchase_price", "")
    add("tax_rate", p.tax_rate)
    add("weight", p.weight)
    add("width", p.width)
    add("height", p.height)
    add("length", p.length)

    # Variant info (BaseLinker uses these for grouped variants)
    if include_variants:
        gid = getattr(p, "variant_group_id", 0)
        if gid:
            add("variant_group_id", gid)
            add("variant_name", getattr(p, "variant_name", "") or p.model_name)

    # Description in CDATA
    desc_elem = etree.SubElement(e, "description")
    desc_html = p.description or ""
    desc_elem.text = etree.CDATA(desc_html) if desc_html.strip() else ""

    for field in ("description_extra_1", "description_extra_2"):
        val = getattr(p, field, "") or ""
        child = etree.SubElement(e, field)
        child.text = etree.CDATA(val) if val.strip() else ""

    # Images — thumbnail first (if uploaded), then all originals from XML.
    thumb = getattr(p, "thumbnail_url", "")
    if thumb and thumb.startswith("http"):
        all_images = [thumb] + list(p.images)
    else:
        all_images = list(p.images)

    if all_images:
        etree.SubElement(e, "image").text = all_images[0]
        for i, url in enumerate(all_images[1:], 1):
            etree.SubElement(e, f"image_extra_{i}").text = url

    # Export all attributes as-is — BrandMapper.sanitize_manufacturer_names()
    # already replaced "Producent: JUMI" → "Producent: GARDENSTEIN" etc. in-place.
    attrs = getattr(p, "attributes", None) or {}
    if attrs:
        attrs_elem = etree.SubElement(e, "attributes")
        for name, value in attrs.items():
            attr = etree.SubElement(attrs_elem, "attribute")
            etree.SubElement(attr, "attribute_name").text = name
            etree.SubElement(attr, "attribute_value").text = str(value)

    return e
