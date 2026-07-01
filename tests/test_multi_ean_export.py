"""Multi-EAN clones — XML export + SQLite persistence."""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from app.cache.sqlite_cache import (
    clear_extra_eans,
    get_extra_eans,
    init_schema,
    open_cache,
    set_extra_eans,
)
from app.exporter.xml_exporter import export_xml
from app.parser.normalizer import Product


# Pre-validated EAN-13 codes (GS1 checksum OK).
EAN_BASE = "5901234567893"
EAN_CLONE_1 = "5012345678900"
EAN_CLONE_2 = "4006381333931"


def _make_product(sku: str = "SKU001", extra: list[str] | None = None) -> Product:
    return Product(
        product_id="PID-001",
        sku=sku,
        ean=EAN_BASE,
        price=199.0,
        purchase_price=99.0,
        tax_rate="23%",
        weight=2.5,
        width=10.0,
        height=20.0,
        length=30.0,
        quantity=42,
        name="Test product",
        category_name="Cat",
        manufacturer_name="Mfg",
        description="<p>desc</p>",
        description_extra_1="",
        description_extra_2="",
        images=["https://img/1.jpg"],
        extra_eans=list(extra or []),
    )


# ── SQLite persistence ──────────────────────────────────────────────────────


def test_set_and_get_extra_eans_roundtrip(tmp_path):
    db = tmp_path / "c.db"
    with open_cache(db) as conn:
        init_schema(conn)
        set_extra_eans(conn, "SKU001", [EAN_CLONE_1, EAN_CLONE_2])
        assert get_extra_eans(conn, "SKU001") == [EAN_CLONE_1, EAN_CLONE_2]


def test_set_extra_eans_replaces_atomically(tmp_path):
    db = tmp_path / "c.db"
    with open_cache(db) as conn:
        set_extra_eans(conn, "SKU001", [EAN_CLONE_1, EAN_CLONE_2])
        set_extra_eans(conn, "SKU001", [EAN_CLONE_1])
        assert get_extra_eans(conn, "SKU001") == [EAN_CLONE_1]


def test_clear_extra_eans(tmp_path):
    db = tmp_path / "c.db"
    with open_cache(db) as conn:
        set_extra_eans(conn, "SKU001", [EAN_CLONE_1])
        assert clear_extra_eans(conn, "SKU001") == 1
        assert get_extra_eans(conn, "SKU001") == []


def test_extra_eans_isolated_per_sku(tmp_path):
    db = tmp_path / "c.db"
    with open_cache(db) as conn:
        set_extra_eans(conn, "SKU001", [EAN_CLONE_1])
        set_extra_eans(conn, "SKU002", [EAN_CLONE_2])
        assert get_extra_eans(conn, "SKU001") == [EAN_CLONE_1]
        assert get_extra_eans(conn, "SKU002") == [EAN_CLONE_2]


# ── XML export with clones ──────────────────────────────────────────────────


def _read_products(path: Path) -> list[etree._Element]:
    tree = etree.parse(str(path))
    return tree.getroot().findall("product")


def test_no_extra_eans_no_clones(tmp_path):
    p = _make_product(extra=[])
    out = tmp_path / "out.xml"
    written = export_xml([p], out)
    assert written == 1
    elems = _read_products(out)
    assert len(elems) == 1
    assert elems[0].findtext("sku") == "SKU001"
    assert elems[0].findtext("ean") == EAN_BASE


def test_extra_eans_create_clones_with_suffix_sku(tmp_path):
    p = _make_product(extra=[EAN_CLONE_1, EAN_CLONE_2])
    out = tmp_path / "out.xml"
    written = export_xml([p], out)
    assert written == 3  # original + 2 clones
    elems = _read_products(out)
    assert [e.findtext("sku") for e in elems] == ["SKU001", "SKU001-1", "SKU001-2"]
    assert [e.findtext("ean") for e in elems] == [EAN_BASE, EAN_CLONE_1, EAN_CLONE_2]
    # Original keeps product_id; clones DROP it so BaseLinker doesn't UPDATE
    # the original on import (it would match by product_id otherwise).
    assert elems[0].findtext("product_id") == "PID-001"
    assert elems[1].find("product_id") is None
    assert elems[2].find("product_id") is None


def test_clone_data_identical_except_keys(tmp_path):
    p = _make_product(extra=[EAN_CLONE_1])
    out = tmp_path / "out.xml"
    export_xml([p], out)
    orig, clone = _read_products(out)
    # Identical: name, quantity, price, description, images, category, mfg
    for tag in ("name", "quantity", "price", "category_name", "manufacturer_name",
                "weight", "width", "height", "length"):
        assert orig.findtext(tag) == clone.findtext(tag), tag
    # Description (CDATA wrapped) — text comparison after parse
    assert orig.find("description").text == clone.find("description").text
    # Images
    assert orig.findtext("image") == clone.findtext("image")


def test_clones_have_full_stock(tmp_path):
    p = _make_product(extra=[EAN_CLONE_1, EAN_CLONE_2])
    out = tmp_path / "out.xml"
    export_xml([p], out)
    qtys = [e.findtext("quantity") for e in _read_products(out)]
    assert qtys == ["42", "42", "42"]


def test_multiple_products_mixed_clones(tmp_path):
    p1 = _make_product(sku="A", extra=[EAN_CLONE_1])
    p2 = _make_product(sku="B", extra=[])
    p2.product_id = "PID-B"
    out = tmp_path / "out.xml"
    written = export_xml([p1, p2], out)
    assert written == 3
    skus = [e.findtext("sku") for e in _read_products(out)]
    assert skus == ["A", "A-1", "B"]


def test_clones_do_not_emit_variant_tags_by_default(tmp_path):
    """Clones are independent products — no variant_group_id without explicit flag."""
    p = _make_product(extra=[EAN_CLONE_1, EAN_CLONE_2])
    out = tmp_path / "out.xml"
    export_xml([p], out)
    for elem in _read_products(out):
        assert elem.find("variant_group_id") is None
        assert elem.find("variant_name") is None
