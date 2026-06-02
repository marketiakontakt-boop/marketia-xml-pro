"""Tests for ModelNameGenerator grouping — same series shares a Nordic base."""
import pytest
from app.cache.sqlite_cache import open_cache
from app.transformer.model_generator import (
    ModelNameGenerator,
    _strip_variant_words,
    _extract_variant_suffix,
)
from app.parser.normalizer import Product


def _p(sku, brand, name) -> Product:
    return Product(
        product_id=sku, sku=sku, ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0,
        quantity=0, name=name, category_name="", manufacturer_name="",
        description="", description_extra_1="", description_extra_2="",
        brand=brand,
    )


@pytest.fixture
def gen(tmp_path):
    db = tmp_path / "test.db"
    with open_cache(db) as conn:
        import json, pathlib
        pool_path = pathlib.Path(__file__).resolve().parents[1] / "data" / "model_names.json"
        yield ModelNameGenerator(conn, pool_path=pool_path)


# --- unit tests for helpers ---

def test_strip_color_from_end():
    assert _strip_variant_words("krzesło piado granatowe") == "krzesło piado"

def test_strip_multiple_color_words():
    assert _strip_variant_words("sofa comfort ciemna szara") == "sofa comfort"

def test_strip_no_color_unchanged():
    assert _strip_variant_words("stół ogrodowy komplet") == "stół ogrodowy komplet"

def test_extract_suffix_single():
    assert _extract_variant_suffix("KRZESŁO PIADO GRANATOWE") == "Granatowe"

def test_extract_suffix_none():
    assert _extract_variant_suffix("MEBLE OGRODOWE KOMPLET") == ""

def test_extract_suffix_skips_measurement_m():
    """'3X3 M' should not produce suffix 'M' — color at end should win."""
    result = _extract_variant_suffix("NAMIOT PAWILON 3X3 M ROZKŁADANY BIAŁY MULTIGARDEN")
    assert result == "Biały", f"Expected 'Biały', got {result!r}"

def test_extract_suffix_skips_measurement_m_no_color():
    """When name has only measurement M and no color, no suffix should be extracted."""
    result = _extract_variant_suffix("SUSZARKA OGRODOWA 60 M POWIERZCHNI GARDENSTEIN")
    assert result == "", f"Expected empty, got {result!r}"

def test_strip_keeps_measurement_m():
    """Measurement 'M' must stay in stripped series key (it's not a color word)."""
    assert "m" in _strip_variant_words("namiot pawilon 3x3 m rozkładany biały")

def test_variants_with_measurement_m_share_base(gen):
    """NAMIOT 3X3 M BIAŁY and NAMIOT 3X3 M SZARY must share same base model."""
    p1 = _p("N1", "gardenstein", "NAMIOT PAWILON 3X3 M ROZKŁADANY AUTOMATYCZNIE BIAŁY MULTIGARDEN")
    p2 = _p("N2", "gardenstein", "NAMIOT PAWILON 3X3 M ROZKŁADANY AUTOMATYCZNIE SZARY MULTIGARDEN")
    gen.assign_all([p1, p2])

    base1 = p1.model_name.split()[0].upper()
    base2 = p2.model_name.split()[0].upper()
    assert base1 == base2, f"Expected same base, got {p1.model_name!r} and {p2.model_name!r}"
    assert p1.model_name != p2.model_name, "Full model names must differ by color"
    assert "Biały" in p1.model_name or "biały" in p1.model_name.lower()
    assert "Szary" in p2.model_name or "szary" in p2.model_name.lower()

def test_extract_suffix_multi():
    # "ciemne szare" → both are variant words
    result = _extract_variant_suffix("SOFA COMFORT CIEMNE SZARE")
    assert "Ciemne" in result and "Szare" in result


# --- integration tests ---

def test_color_variants_share_base(gen):
    """PIADO Granatowe + PIADO Czarne must share the same Nordic base."""
    p1 = _p("SKU-001", "gardenstein", "KRZESŁO PIADO GRANATOWE")
    p2 = _p("SKU-002", "gardenstein", "KRZESŁO PIADO CZARNE")
    gen.assign_all([p1, p2])

    base1 = p1.model_name.split()[0].upper()
    base2 = p2.model_name.split()[0].upper()
    assert base1 == base2, f"Expected same base, got {p1.model_name!r} and {p2.model_name!r}"
    assert p1.model_name != p2.model_name, "Full model names must differ (different colors)"


def test_standalone_products_get_unique_bases(gen):
    """Products with no color suffix each get their own base name."""
    p1 = _p("SKU-010", "gardenstein", "STÓŁ OGRODOWY KOMPLET")
    p2 = _p("SKU-011", "gardenstein", "FOTEL BIUROWY PREMIUM")
    gen.assign_all([p1, p2])

    base1 = p1.model_name.split()[0].upper()
    base2 = p2.model_name.split()[0].upper()
    assert base1 != base2, "Standalone products should get distinct base names"


def test_three_variants_same_base(gen):
    p1 = _p("A1", "gardenstein", "KRZESŁO EVA BIAŁE")
    p2 = _p("A2", "gardenstein", "KRZESŁO EVA CZARNE")
    p3 = _p("A3", "gardenstein", "KRZESŁO EVA SZARE")
    gen.assign_all([p1, p2, p3])

    bases = {x.model_name.split()[0].upper() for x in [p1, p2, p3]}
    assert len(bases) == 1, f"All three should share one base, got {bases}"
    models = {x.model_name for x in [p1, p2, p3]}
    assert len(models) == 3, "Full model names must be distinct"


def test_two_different_series_get_different_bases(gen):
    """EVA chairs and MILAN chairs must get different Nordic bases."""
    eva_g = _p("E1", "gardenstein", "KRZESŁO EVA GRANATOWE")
    eva_c = _p("E2", "gardenstein", "KRZESŁO EVA CZARNE")
    mil_g = _p("M1", "gardenstein", "KRZESŁO MILAN GRANATOWE")
    mil_c = _p("M2", "gardenstein", "KRZESŁO MILAN CZARNE")
    gen.assign_all([eva_g, eva_c, mil_g, mil_c])

    eva_base = eva_g.model_name.split()[0].upper()
    mil_base = mil_g.model_name.split()[0].upper()
    assert eva_base == eva_c.model_name.split()[0].upper()
    assert mil_base == mil_c.model_name.split()[0].upper()
    assert eva_base != mil_base, f"Different series must get different bases, got {eva_base}"


def test_rerun_is_idempotent(gen):
    """Running assign_all twice gives the same model names."""
    p1 = _p("SKU-001", "gardenstein", "KRZESŁO PIADO GRANATOWE")
    p2 = _p("SKU-002", "gardenstein", "KRZESŁO PIADO CZARNE")
    gen.assign_all([p1, p2])
    name1_first = p1.model_name
    name2_first = p2.model_name

    gen.assign_all([p1, p2])
    assert p1.model_name == name1_first
    assert p2.model_name == name2_first


def test_model_rename_updates_description():
    """_apply_rename must replace old base name in product.description."""
    from app.gui.model_rename_window import _apply_rename
    from app.parser.normalizer import Product

    p = Product(
        product_id="X1", sku="X1", ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0,
        quantity=0, name="PIADO Granatowe", category_name="", manufacturer_name="",
        description="<p>Krzesło PIADO to elegancka seria. Model PIADO dostępny w wielu kolorach.</p>",
        description_extra_1="", description_extra_2="",
        brand="gardenstein", model_name="PIADO Granatowe",
    )

    affected = _apply_rename([p], old_base="PIADO", new_base="NORD")
    assert len(affected) == 1
    assert "PIADO" not in p.description
    assert "NORD" in p.description
    assert p.model_name.startswith("NORD")
