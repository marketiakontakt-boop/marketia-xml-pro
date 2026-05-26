import json
import pytest
from app.transformer.category_mapper import load_category_map, map_category, map_all_products
from app.parser.normalizer import Product


def _make_product(cat: str) -> Product:
    return Product(
        product_id="1", sku="T", ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0,
        quantity=0, name="Test", category_name=cat, manufacturer_name="",
        description="", description_extra_1="", description_extra_2="",
    )


def test_load_category_map_returns_dict():
    m = load_category_map()
    assert isinstance(m, dict)
    assert len(m) > 0


def test_map_known_category():
    m = load_category_map()
    result = map_category("INTEX / Baseny", m)
    assert result == "Dom i ogród > Basen i spa > Baseny ogrodowe"


def test_map_unknown_category_returns_none():
    m = load_category_map()
    result = map_category("NIEZNANA KATEGORIA", m)
    assert result is None


def test_map_all_products_sets_allegro_category():
    m = load_category_map()
    p = _make_product("INTEX / Baseny")
    map_all_products([p], m)
    assert p.allegro_category == "Dom i ogród > Basen i spa > Baseny ogrodowe"


def test_map_all_products_leaves_empty_for_unknown():
    m = load_category_map()
    p = _make_product("UNKNOWN")
    map_all_products([p], m)
    assert p.allegro_category == ""
