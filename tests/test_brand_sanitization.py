"""Tests verifying that original supplier/manufacturer brand names are never exposed.

Covers every leak path found in the audit:
  1. prompts._filter_supplier_attrs — strips supplier keys from attributes
  2. prompts._spec_items — spec HTML never contains supplier attribute keys
  3. prompts.build_description_prompt_v2 — attrs_block stripped + AI brand instruction present
  4. xml_exporter — manufacturer_name and attributes in export never reveal supplier
  5. brand_mapper.sanitize_manufacturer_names — overwrites manufacturer_name with own brand
"""
import tempfile
import pytest
from lxml import etree

from app.ai.prompts import _filter_supplier_attrs, _spec_items, build_description_prompt_v2
from app.exporter.xml_exporter import export_xml
from app.transformer.brand_mapper import BrandMapper
from app.parser.normalizer import Product


# ── helpers ──────────────────────────────────────────────────────────────────

def _product(
    sku: str = "T-001",
    brand: str = "villago",
    manufacturer_name: str = "JUMI",
    description: str = "",
    attributes: dict | None = None,
) -> Product:
    p = Product(
        product_id="1", sku=sku, ean="", price=99.0, purchase_price=0.0,
        tax_rate="23%", weight=1.0, width=0.0, height=0.0, length=0.0,
        quantity=5, name="Krzesło testowe", category_name="Meble",
        manufacturer_name=manufacturer_name,
        description=description,
        description_extra_1="", description_extra_2="",
    )
    p.brand = brand
    p.attributes = attributes or {}
    return p


# ── 1. _filter_supplier_attrs ─────────────────────────────────────────────────

SUPPLIER_CASES = [
    "Producent", "producent", "PRODUCENT",
    "Producer", "Manufacturer",
    "Dostawca", "Supplier", "Vendor",
    "Country of Origin", "Kraj pochodzenia", "Origin",
    "Dystrybutor",
    "Model producenta", "Part Number", "MPN",
    "Import",
    "Marka producenta",
]


@pytest.mark.parametrize("key", SUPPLIER_CASES)
def test_filter_removes_supplier_key(key: str):
    result = _filter_supplier_attrs({key: "JUMI"})
    assert result == {}, f"Key '{key}' should have been removed"


def test_filter_keeps_product_attrs():
    attrs = {"Kolor": "Niebieski", "Materiał": "Tkanina", "Waga": "5 kg"}
    result = _filter_supplier_attrs(attrs)
    assert result == attrs


def test_filter_mixed_attrs():
    attrs = {
        "Kolor": "Czarny",
        "Producent": "JUMI",
        "Materiał": "Metal",
        "Dostawca": "AliExpress",
    }
    result = _filter_supplier_attrs(attrs)
    assert "Kolor" in result
    assert "Materiał" in result
    assert "Producent" not in result
    assert "Dostawca" not in result


# ── 2. _spec_items — no supplier keys in HTML output ─────────────────────────

def test_spec_items_excludes_supplier_attrs():
    p = _product(attributes={
        "Kolor": "Biały",
        "Producent": "JUMI",
        "Dostawca": "Alibaba Factory",
        "Materiał": "Drewno",
    })
    brand_info = {"name": "VILLAGO"}
    html_items = _spec_items(p, "VILLAGO")
    combined = " ".join(html_items)
    assert "JUMI" not in combined
    assert "Alibaba" not in combined
    assert "Producent" not in combined
    assert "Dostawca" not in combined
    # Own brand must still be there
    assert "VILLAGO" in combined
    # Valid attrs must still be there
    assert "Biały" in combined
    assert "Drewno" in combined


# ── 3. build_description_prompt_v2 ───────────────────────────────────────────

def test_prompt_contains_brand_protection_instruction():
    p = _product(description="Świetny produkt marki JUMI")
    brand_info = {"name": "VILLAGO"}
    prompt = build_description_prompt_v2(p, brand_info, "villago")
    # Must instruct AI to not use supplier brand
    assert "ZAKAZ" in prompt or "ignoruj nazwy marek" in prompt


def test_prompt_does_not_send_supplier_attrs_to_ai():
    p = _product(attributes={"Producent": "JUMI", "Kolor": "Czarny"})
    brand_info = {"name": "VILLAGO"}
    prompt = build_description_prompt_v2(p, brand_info, "villago")
    # Supplier key and value must not appear in attrs_block
    assert "Producent" not in prompt
    # But valid attribute must still be present
    assert "Czarny" in prompt


def test_prompt_brand_display_name_present():
    p = _product()
    brand_info = {"name": "GARDENSTEIN"}
    prompt = build_description_prompt_v2(p, brand_info, "gardenstein")
    assert "GARDENSTEIN" in prompt


# ── 4. xml_exporter — supplier data never leaks into exported XML ─────────────

def _export_and_parse(products: list[Product]) -> etree._Element:
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        path = f.name
    export_xml(products, path)
    return etree.parse(path).getroot()


def test_export_shows_own_brand_in_producent_attr():
    """After sanitize, 'Producent: JUMI' becomes 'Producent: VILLAGO' in XML."""
    bm = BrandMapper()
    p = _product(attributes={"Kolor": "Szary", "Producent": "JUMI"})
    bm.sanitize_manufacturer_names([p])
    root = _export_and_parse([p])
    attr_map = {
        a.findtext("attribute_name"): a.findtext("attribute_value")
        for a in root.findall(".//attribute")
    }
    assert attr_map.get("Producent") == "VILLAGO"
    assert "JUMI" not in str(attr_map.values())
    assert attr_map.get("Kolor") == "Szary"


def test_export_manufacturer_name_is_own_brand():
    p = _product(manufacturer_name="JUMI")
    p.manufacturer_name = "VILLAGO"
    root = _export_and_parse([p])
    mfr = root.findtext(".//manufacturer_name")
    assert mfr == "VILLAGO"
    assert "JUMI" not in (mfr or "")


def test_export_all_producer_keys_replaced():
    """All producer/supplier attribute keys get value replaced, not removed."""
    bm = BrandMapper()
    p = _product(attributes={
        "Producent": "JUMI",
        "Marka": "JUMI",
        "Dostawca": "Alibaba",
        "Kolor": "Czarny",
    })
    bm.sanitize_manufacturer_names([p])
    root = _export_and_parse([p])
    attr_map = {
        a.findtext("attribute_name"): a.findtext("attribute_value")
        for a in root.findall(".//attribute")
    }
    assert "JUMI" not in str(attr_map.values())
    assert "Alibaba" not in str(attr_map.values())
    assert attr_map.get("Producent") == "VILLAGO"
    assert attr_map.get("Marka") == "VILLAGO"
    assert attr_map.get("Dostawca") == "VILLAGO"
    assert attr_map.get("Kolor") == "Czarny"


# ── 5. brand_mapper.sanitize_manufacturer_names ───────────────────────────────

def test_sanitize_replaces_jumi_manufacturer():
    bm = BrandMapper()
    p = _product(manufacturer_name="JUMI", brand="villago")
    bm.sanitize_manufacturer_names([p])
    assert p.manufacturer_name == "VILLAGO"
    assert "JUMI" not in p.manufacturer_name


def test_sanitize_replaces_alibaba_manufacturer():
    bm = BrandMapper()
    p = _product(manufacturer_name="Alibaba Shenzhen Co.", brand="gardenstein")
    bm.sanitize_manufacturer_names([p])
    assert p.manufacturer_name == "GARDENSTEIN"


def test_sanitize_unknown_brand_clears_manufacturer():
    bm = BrandMapper()
    p = _product(manufacturer_name="JUMI", brand="unknown")
    bm.sanitize_manufacturer_names([p])
    assert p.manufacturer_name == ""


@pytest.mark.parametrize("brand,expected", [
    ("villago", "VILLAGO"),
    ("gardenstein", "GARDENSTEIN"),
    ("intex", "INTEX"),
    ("hopla_toys", "HOPLA TOYS"),
    ("zoovera", "ZOOVERA"),
    ("marketia_home", "MARKETIA HOME"),
])
def test_sanitize_sets_correct_display_name(brand: str, expected: str):
    bm = BrandMapper()
    p = _product(brand=brand, manufacturer_name="JUMI")
    bm.sanitize_manufacturer_names([p])
    assert p.manufacturer_name == expected
