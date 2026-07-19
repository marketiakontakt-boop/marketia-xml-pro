"""Tests for `app.olx.mapper` — title cleaning, HTML whitelist, offer body build."""
from __future__ import annotations

from app.olx.mapper import (
    _clean_description_html,
    _clean_title,
    map_product_to_offer,
)
from app.parser.normalizer import Product


def _mk_product(**kwargs) -> Product:
    defaults = dict(
        product_id="P1",
        sku="SKU-1",
        ean="5901234123457",
        price=199.0,
        purchase_price=100.0,
        tax_rate="23%",
        weight=1.0,
        width=0.0,
        height=0.0,
        length=0.0,
        quantity=10,
        name="Bujak dziecięcy Villago",
        category_name="Dziecko",
        manufacturer_name="Villago",
        description="<p>Testowy opis produktu dla dziecka.</p>",
        description_extra_1="",
        description_extra_2="",
        images=["https://cdn.example.com/img1.jpg"],
        title="Bujak Villago Milan Drewniany Zabawka",
        thumbnail_url="https://i.ibb.co/abc/thumb.jpg",
    )
    defaults.update(kwargs)
    return Product(**defaults)


def test_clean_title_trims_to_70() -> None:
    long_title = "A" * 80 + " tail"
    cleaned = _clean_title(long_title)
    assert len(cleaned) <= 70


def test_clean_title_no_all_caps() -> None:
    result = _clean_title("VILLAGO MILAN BUJAK DREWNIANY DLA DZIECKA")
    # Should not be all-uppercase after cleaning
    assert result != result.upper() or all(c.islower() or not c.isalpha() for c in result)
    # No 4+ consecutive uppercase runs
    import re
    assert not re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]{4,}", result)


def test_clean_description_strips_style_tags() -> None:
    html = (
        "<section><style>body{color:red}</style>"
        "<p>Realny opis produktu z wystarczającą liczbą znaków aby przejść walidację.</p>"
        "<dl><dt>klucz</dt><dd>wartość</dd></dl>"
        "<strong>Ważne</strong></section>"
    )
    cleaned = _clean_description_html(html)
    assert "<style>" not in cleaned
    assert "body{color:red}" not in cleaned
    assert "<section>" not in cleaned
    assert "<dl>" not in cleaned
    assert "<p>" in cleaned
    assert "<strong>" in cleaned


def test_map_produces_valid_offer_dict() -> None:
    p = _mk_product()
    body = map_product_to_offer(
        product=p,
        category_id=1234,
        attribute_values={"state": "new"},
        contact_name="Kowalski",
        contact_phone="+48123456789",
        city_id=5001,
    )
    assert body["category_id"] == 1234
    assert body["advertiser_type"] == "business"
    assert body["contact"] == {"name": "Kowalski", "phone": "+48123456789"}
    assert body["location"] == {"city_id": 5001}
    assert body["price"]["currency"] == "PLN"
    assert isinstance(body["price"]["value"], int)
    assert body["price"]["value"] == 199
    assert body["external_id"] == "SKU-1"
    assert body["attributes"] == [{"code": "state", "value": "new"}]
    assert body["images"] and body["images"][0]["url"].startswith("http")
    assert len(body["title"]) <= 70
