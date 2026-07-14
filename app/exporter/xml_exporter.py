"""Export transformed products back to BaseLinker-compatible XML."""
from __future__ import annotations

import copy
from pathlib import Path

from lxml import etree

from app.parser.normalizer import Product


def export_xml(
    products: list[Product],
    output_path: Path | str,
    include_variants: bool = False,
    infographics_map: dict[str, list[str]] | None = None,
) -> int:
    """Write transformed XML. Returns count of <product> elements written.

    include_variants=True adds <variant_group_id> + <variant_name> tags for
    products that belong to a variant group (variant_group_id > 0).

    Products carrying `extra_eans` are emitted once as the base entry and
    then cloned per extra EAN (suffixed SKU/product_id, swapped EAN) so each
    Allegro product card can be hit by a distinct listing.

    WARNING: clones are INDEPENDENT products in BaseLinker — each has its
    own stock counter. Stock is NOT synchronized between clones unless
    additional manual configuration (bundle / multi-EAN / API) is applied
    in the BaseLinker panel post-import. See INSTRUKCJA_MULTI_EAN.md.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load infographic URLs once (single SQLite scan) unless caller pre-supplied them.
    if infographics_map is None:
        infographics_map = _load_infographics_map([p.sku for p in products])

    root = etree.Element("products")
    total = 0
    for p in products:
        original_elem = _product_to_element(
            p,
            include_variants=include_variants,
            infographics_map=infographics_map,
        )
        root.append(original_elem)
        total += 1
        for idx, ean in enumerate(getattr(p, "extra_eans", []) or [], start=1):
            root.append(_make_clone_element(original_elem, p, idx, ean))
            total += 1

    etree.ElementTree(root).write(
        str(output_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="utf-8",
    )
    return total


def _make_clone_element(
    original: etree._Element, p: Product, idx: int, ean: str
) -> etree._Element:
    """Deep-copy base element, swap SKU + EAN, DROP product_id entirely.

    `product_id` is removed because BaseLinker uses it as the primary match key
    on XML import — keeping a suffixed value like `372674308_1` either matches
    the original (UPDATE instead of INSERT) or fails parsing. Without
    `<product_id>`, BaseLinker creates a NEW product keyed by the unique
    suffixed SKU (`SKU001-1`, `SKU001-2`, …).
    """
    clone = copy.deepcopy(original)
    sku_el = clone.find("sku")
    if sku_el is not None:
        sku_el.text = f"{p.sku}-{idx}"
    pid_el = clone.find("product_id")
    if pid_el is not None:
        clone.remove(pid_el)
    ean_el = clone.find("ean")
    if ean_el is not None:
        ean_el.text = ean
    return clone


def _load_infographics_map(skus: list[str]) -> dict[str, list[str]]:
    """Return {sku: [imgbb_url, ...]} for every sku with uploaded infographics."""
    from app.cache.sqlite_cache import get_infographics, open_cache

    out: dict[str, list[str]] = {}
    if not skus:
        return out
    with open_cache() as conn:
        for sku in skus:
            infos = get_infographics(conn, sku)
            urls = [i["imgbb_url"] for i in infos if i.get("imgbb_url")]
            if urls:
                out[sku] = urls
    return out


def _product_to_element(
    p: Product,
    include_variants: bool = False,
    infographics_map: dict[str, list[str]] | None = None,
) -> etree._Element:
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

    # Variant info (BaseLinker uses these for grouped variants — independent stocks)
    if include_variants:
        gid = int(getattr(p, "variant_group_id", 0) or 0)
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

    # Images — MINIATURA (packshot lub pierwszy dostawcy) ZAWSZE jako główne zdjęcie [0].
    # Kolejność: main → oryginały dostawcy → INFOGRAFIKI NA KOŃCU (user request 2026-07-12c —
    # użytkownik nie chce ryzykować że infografiki wejdą "między" prawdziwe zdjęcia produktu).
    thumb = getattr(p, "thumbnail_url", "")
    info_urls = list((infographics_map or {}).get(p.sku, []))
    origins = list(p.images)
    if thumb and thumb.startswith("http"):
        all_images = [thumb] + origins + info_urls
    elif origins:
        all_images = origins + info_urls
    else:
        all_images = info_urls

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
