"""Tests for ModelNameGenerator grouping — same series shares a Nordic base."""
import pytest
from app.cache.sqlite_cache import open_cache
from app.transformer.model_generator import (
    ModelNameGenerator,
    _strip_variant_words,
    _extract_variant_suffix,
    _series_from_sku,
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


# --- compound colors (slash / dash) ---

def test_strip_compound_slash_color():
    """'biało/czarne' = compound color, should be stripped → same series key as 'białe'."""
    assert _strip_variant_words("krzesło iger biało/czarne") == "krzesło iger"

def test_strip_compound_dash_color():
    """'czarno-szare' = compound color, should be stripped."""
    assert _strip_variant_words("krzesło joy czarno-szare") == "krzesło joy"

def test_extract_suffix_compound_slash():
    assert _extract_variant_suffix("KRZESŁO IGER biało/czarne") == "Biało/czarne"

def test_extract_suffix_compound_dash():
    assert _extract_variant_suffix("KRZESŁO JOY czarno-szare") == "Czarno-szare"

def test_strip_doesnt_break_random_dashes():
    """Non-color compounds (e.g. measurement '3x3-pawilon') must NOT be treated as variants."""
    assert "3x3-pawilon" in _strip_variant_words("namiot 3x3-pawilon biały")


# --- noise tokens (channel tags in parens) ---

def test_strip_noise_paren_tag():
    """'(ikeabox)' is a channel tag, not part of the series — strip it."""
    assert _strip_variant_words("Krzesło EVA szare (ikeabox)") == "krzesło eva"

def test_extract_suffix_ignores_paren_tag():
    assert _extract_variant_suffix("Krzesło EVA szare (ikeabox)") == "Szare"


# --- new colors from real XML ---

def test_strip_musztardowe():
    """'musztardowe' was missing from VARIANT_WORDS — caused Otranto-musztardowe singletons."""
    assert _strip_variant_words("krzesło otranto musztardowe") == "krzesło otranto"

def test_strip_kawowe():
    assert _strip_variant_words("sofa lounge kawowa") == "sofa lounge"

def test_strip_naturalny_drewno():
    """Wood-finish colors common in furniture catalog."""
    assert _strip_variant_words("stolik nico naturalny") == "stolik nico"
    assert _strip_variant_words("regał loft dębowy") == "regał loft"

def test_variants_with_measurement_m_share_base(gen):
    """NAMIOT 3X3 M BIAŁY and NAMIOT 3X3 M SZARY must share same base model."""
    p1 = _p("N1", "gardenstein", "NAMIOT PAWILON 3X3 M ROZKŁADANY AUTOMATYCZNIE BIAŁY")
    p2 = _p("N2", "gardenstein", "NAMIOT PAWILON 3X3 M ROZKŁADANY AUTOMATYCZNIE SZARY")
    gen.assign_all([p1, p2])

    assert p1.model_name == p2.model_name, f"Variants must share single-word model_name, got {p1.model_name!r} and {p2.model_name!r}"

def test_extract_suffix_multi():
    # "ciemne szare" → both are variant words
    result = _extract_variant_suffix("SOFA COMFORT CIEMNE SZARE")
    assert "Ciemne" in result and "Szare" in result


# --- integration tests ---

def test_color_variants_share_base(gen):
    """PIADO Granatowe + PIADO Czarne must share the same single-word model name."""
    p1 = _p("SKU-001", "gardenstein", "KRZESŁO PIADO GRANATOWE")
    p2 = _p("SKU-002", "gardenstein", "KRZESŁO PIADO CZARNE")
    gen.assign_all([p1, p2])

    assert p1.model_name == p2.model_name, f"Variants must share model_name, got {p1.model_name!r} and {p2.model_name!r}"
    assert " " not in p1.model_name, f"Model name must be single-word, got {p1.model_name!r}"


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
    # Single-word model: all three share identical model_name
    models = {x.model_name for x in [p1, p2, p3]}
    assert len(models) == 1, f"Single-word model_name must be identical for all variants, got {models}"


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


# --- SKU-encoded series (hurtmeblowy pattern) ---

def test_series_from_sku_extracts_model():
    assert _series_from_sku("model_3544_1-AVOLA-DORY21") == "avola"
    assert _series_from_sku("model_3329_1-ALBA-FEMY24") == "alba"
    assert _series_from_sku("model_3328_1-ADRIA-TRX12080") == "adria"

def test_series_from_sku_none_without_suffix():
    assert _series_from_sku("model_3671_1") is None
    assert _series_from_sku("LDFDCN4Y16") is None

def test_sku_series_groups_variants_together(gen):
    """Products with same model in SKU must share one pool name regardless of name analysis."""
    p1 = _p("model_3544_1-AVOLA-DORY21", "homestein", "KRZESŁO BOKU AVOLA - CZARNE")
    p2 = _p("model_3545_1-AVOLA-DORY21", "homestein", "KRZESŁO BOKU AVOLA - SZARE")
    p3 = _p("model_3543_1-AVOLA-DORY21", "homestein", "KRZESŁO BOKU AVOLA - SZARO-BIAŁE")
    gen.assign_all([p1, p2, p3])

    assert p1.model_name == p2.model_name == p3.model_name, (
        f"SKU-based series must share model_name, got {p1.model_name!r}, {p2.model_name!r}, {p3.model_name!r}"
    )

def test_sku_series_different_models_get_different_pools(gen):
    """AVOLA and ALBA are different series — must get different pool names."""
    avola1 = _p("model_3544_1-AVOLA-DORY21", "homestein", "KRZESŁO BOKU AVOLA - CZARNE")
    avola2 = _p("model_3545_1-AVOLA-DORY21", "homestein", "KRZESŁO BOKU AVOLA - SZARE")
    alba1 = _p("model_3329_1-ALBA-FEMY24", "homestein", "KRZESŁO KONUN ALBA - BIAŁE")
    alba2 = _p("model_3330_1-ALBA-FEMY24", "homestein", "KRZESŁO KONUN ALBA - CZARNE")
    gen.assign_all([avola1, avola2, alba1, alba2])

    assert avola1.model_name == avola2.model_name
    assert alba1.model_name == alba2.model_name
    assert avola1.model_name != alba1.model_name, (
        f"AVOLA and ALBA must get different pools, both got {avola1.model_name!r}"
    )

def test_sku_series_replaces_supplier_word_in_name(gen):
    """AVOLA must be replaced by pool name in product.name."""
    p = _p("model_3544_1-AVOLA-DORY21", "homestein", "KRZESŁO BOKU AVOLA - CZARNE")
    gen.assign_all([p])

    pool = p.model_name
    assert "AVOLA" not in p.name.upper(), f"AVOLA should be replaced, got {p.name!r}"
    assert pool.upper() in p.name.upper(), f"Pool name {pool!r} not found in {p.name!r}"


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
