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
