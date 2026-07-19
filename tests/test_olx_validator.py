"""Tests for `app.olx.validator` — phone regex, required attrs, length checks."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.cache.sqlite_cache import init_schema, save_olx_attribute, save_olx_category
from app.olx.validator import validate_product
from app.parser.normalizer import Product


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "olx.db")
    c.row_factory = sqlite3.Row
    init_schema(c)
    save_olx_category(c, 100, None, "Meble", "Meble")
    save_olx_attribute(c, 100, "state", "Stan", required=True, attr_type="select",
                       options=[{"code": "new"}, {"code": "used"}])
    return c


def _mk_product(**kwargs) -> Product:
    defaults = dict(
        product_id="P1",
        sku="SKU-1",
        ean="5901234123457",
        price=199.0,
        purchase_price=100.0,
        tax_rate="23%",
        weight=1.0,
        width=0.0, height=0.0, length=0.0,
        quantity=10,
        name="Bujak dziecięcy Villago Milan drewniana zabawka premium",
        category_name="Dziecko",
        manufacturer_name="Villago",
        description=(
            "<p>To jest opis testowy wystarczająco długi żeby przejść walidację "
            "minimalnej długości opisu na poziomie 80 znaków wymaganym przez OLX API.</p>"
        ),
        description_extra_1="",
        description_extra_2="",
        images=["https://cdn.example.com/img1.jpg"],
        title="Bujak Villago Milan drewniana zabawka premium 12m+",
        thumbnail_url="https://i.ibb.co/x/thumb.jpg",
    )
    defaults.update(kwargs)
    return Product(**defaults)


def test_valid_product_returns_empty_list(conn: sqlite3.Connection) -> None:
    p = _mk_product()
    errors = validate_product(
        product=p, category_id=100,
        attribute_values={"state": "new"},
        conn=conn,
        contact_name="Kowalski", contact_phone="+48123456789",
    )
    assert errors == []


def test_short_title_fails(conn: sqlite3.Connection) -> None:
    p = _mk_product(title="ab")
    errors = validate_product(
        product=p, category_id=100,
        attribute_values={"state": "new"},
        conn=conn,
        contact_name="Kowalski", contact_phone="+48123456789",
    )
    assert any(e.field == "title" for e in errors)


def test_phone_regex_accepts_9digit_and_e164(conn: sqlite3.Connection) -> None:
    p = _mk_product()
    for phone in ("123456789", "+48123456789", "48123456789"):
        errors = validate_product(
            product=p, category_id=100,
            attribute_values={"state": "new"},
            conn=conn,
            contact_name="Kowalski", contact_phone=phone,
        )
        assert not any(e.field == "contact.phone" for e in errors), f"phone {phone} rejected"

    # bad phone
    errors = validate_product(
        product=p, category_id=100,
        attribute_values={"state": "new"},
        conn=conn,
        contact_name="Kowalski", contact_phone="abc",
    )
    assert any(e.field == "contact.phone" for e in errors)


def test_missing_required_attribute_fails(conn: sqlite3.Connection) -> None:
    p = _mk_product()
    errors = validate_product(
        product=p, category_id=100,
        attribute_values={},  # missing "state"
        conn=conn,
        contact_name="Kowalski", contact_phone="+48123456789",
    )
    assert any(e.field.startswith("attributes.state") for e in errors)
