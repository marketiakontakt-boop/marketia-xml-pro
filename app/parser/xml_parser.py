"""BaseLinker XML parser — defensive, streaming-friendly for large files (up to ~10MB)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

from lxml import etree

from .normalizer import Product, normalize_product


def _make_parser() -> etree.XMLParser:
    # recover=True: tolerate slightly malformed XML (BaseLinker exports sometimes have stray bytes / BOM)
    # remove_blank_text=False: keep whitespace inside <description> intact for later HTML re-render
    return etree.XMLParser(recover=True, remove_blank_text=False, encoding="utf-8")


def iter_products(xml_path: Path | str) -> Iterator[Product]:
    """Stream <product> elements from `xml_path`, yielding Product dataclasses.

    Memory-safe for large XML files: clears each element after yield so peak
    memory stays bounded regardless of input size.
    """
    path = Path(xml_path)
    if not path.exists():
        raise FileNotFoundError(path)

    context = etree.iterparse(
        str(path),
        events=("end",),
        tag="product",
        recover=True,
        encoding="utf-8",
    )
    try:
        for _event, elem in context:
            product = normalize_product(elem)
            yield product
            # Free memory: clear element + drop preceding siblings from the tree
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                while elem.getprevious() is not None:
                    del parent[0]
    finally:
        del context


def parse_xml(xml_path: Path | str) -> list[Product]:
    """Load all products into memory. Convenience wrapper around `iter_products`.
    For files >50MB prefer the streaming `iter_products`.
    """
    return list(iter_products(xml_path))


def _cli() -> int:
    """Quick CLI sanity check: `python -m app.parser.xml_parser <path>`."""
    if len(sys.argv) != 2:
        print("usage: python -m app.parser.xml_parser <path-to-xml>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    products = parse_xml(path)
    print(f"parsed {len(products)} products from {path}")
    for i, p in enumerate(products[:3], 1):
        print(
            f"  #{i}  sku={p.sku!r}  name={p.name[:60]!r}  "
            f"price={p.price}  ean={p.ean!r}  images={len(p.images)}"
        )
    if len(products) > 3:
        print(f"  ... and {len(products) - 3} more")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
