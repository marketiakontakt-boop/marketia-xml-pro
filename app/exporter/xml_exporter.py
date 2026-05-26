"""Export transformed products back to BaseLinker-compatible XML."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lxml import etree

from app.parser.normalizer import Product


def export_xml(products: list[Product], output_path: Path | str) -> int:
    """Write transformed XML. Returns count of products written."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    root = etree.Element("products")

    for p in products:
        elem = _product_to_element(p)
        root.append(elem)

    tree = etree.ElementTree(root)
    tree.write(
        str(output_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="utf-8",
    )
    return len(products)


def _product_to_element(p: Product) -> etree._Element:
    e = etree.Element("product")

    def add(tag: str, value: str):
        child = etree.SubElement(e, tag)
        child.text = str(value) if value is not None else ""

    # Immutable fields
    add("product_id", p.product_id)
    add("name", p.title if p.title else p.name)
    add("quantity", p.quantity)
    add("ean", p.ean)
    add("sku", p.sku)
    add("category_name", getattr(p, "allegro_category", "") or p.category_name)
    add("manufacturer_name", p.manufacturer_name)
    add("price", p.price)
    add("purchase_price", "")
    add("tax_rate", p.tax_rate)
    add("weight", p.weight)
    add("width", p.width)
    add("height", p.height)
    add("length", p.length)

    # Description in CDATA if it contains HTML
    desc_elem = etree.SubElement(e, "description")
    desc_html = p.description or ""
    if desc_html.strip():
        desc_elem.text = etree.CDATA(desc_html)
    else:
        desc_elem.text = ""

    # Extra descriptions (unchanged)
    for field in ("description_extra_1", "description_extra_2"):
        val = getattr(p, field, "") or ""
        child = etree.SubElement(e, field)
        if val.strip():
            child.text = etree.CDATA(val)
        else:
            child.text = ""

    # Images — if thumbnail_url set, prepend it as images[0]
    all_images = list(p.images)
    thumb = getattr(p, "thumbnail_url", "")
    if thumb and thumb.startswith("http"):
        all_images = [thumb] + all_images

    if all_images:
        img_elem = etree.SubElement(e, "image")
        img_elem.text = all_images[0]
        for i, url in enumerate(all_images[1:], 1):
            extra = etree.SubElement(e, f"image_extra_{i}")
            extra.text = url

    if getattr(p, "attributes", None):
        attrs_elem = etree.SubElement(e, "attributes")
        for name, value in p.attributes.items():
            attr = etree.SubElement(attrs_elem, "attribute")
            etree.SubElement(attr, "attribute_name").text = name
            etree.SubElement(attr, "attribute_value").text = str(value)

    return e
