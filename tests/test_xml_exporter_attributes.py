import tempfile
from pathlib import Path
from lxml import etree
from app.parser.normalizer import Product
from app.exporter.xml_exporter import export_xml


def _make_product(attrs: dict) -> Product:
    p = Product(
        product_id="1", sku="TEST-001", ean="5901234123457",
        price=99.99, purchase_price=50.0, tax_rate="23%",
        weight=2.5, width=0.0, height=0.0, length=0.0,
        quantity=10, name="Produkt testowy", category_name="Test",
        manufacturer_name="Brand", description="<p>Opis</p>",
        description_extra_1="", description_extra_2="",
    )
    p.attributes = attrs
    return p


def test_attributes_exported():
    p = _make_product({"Waga": "3.5", "Kolor": "Niebieski"})
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        path = f.name
    export_xml([p], path)
    tree = etree.parse(path)
    attrs_elem = tree.find(".//attributes")
    assert attrs_elem is not None
    names = {a.findtext("attribute_name") for a in attrs_elem.findall("attribute")}
    assert "Waga" in names
    assert "Kolor" in names


def test_empty_attributes_not_exported():
    p = _make_product({})
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        path = f.name
    export_xml([p], path)
    tree = etree.parse(path)
    attrs_elem = tree.find(".//attributes")
    assert attrs_elem is None
