"""Simple-mode TitleTransformer tests.

Old 6-step rule pipeline (with noun injection, padding, atuty) has been
replaced by a minimal cleaner: strip supplier brand names, UPPERCASE,
append our brand + model, 75-char word-boundary trim.
"""
from __future__ import annotations

import pytest

from app.parser.normalizer import Product
from app.transformer.title_transformer import (
    MAX_LEN,
    TitleTransformer,
    _ALL_OEM,
    _OEM_BY_BRAND,
)


def _p(name: str, brand: str = "hopla_toys", model: str = "") -> Product:
    p = Product(
        product_id="1", sku="TEST-001", ean="", price=99.0, purchase_price=0.0,
        tax_rate="23%", weight=1.0, width=0.0, height=0.0, length=0.0,
        quantity=5, name=name, category_name="Zabawki",
        manufacturer_name="TEST", description="",
        description_extra_1="", description_extra_2="",
    )
    p.brand = brand
    p.model_name = model
    return p


@pytest.fixture(scope="module")
def tt() -> TitleTransformer:
    return TitleTransformer()


# ── basic guarantees ────────────────────────────────────────────────────────


def test_empty_title_is_noop(tt):
    p = _p("")
    tt.transform(p)
    assert p.title == ""


def test_uppercases_title(tt):
    p = _p("drewniany domek dla lalek")
    tt.transform(p)
    assert p.title == p.title.upper()


def test_length_never_exceeds_max(tt):
    long = "A " + " ".join(["słowo"] * 40)
    p = _p(long)
    tt.transform(p)
    assert len(p.title) <= MAX_LEN


def test_word_boundary_trim(tt):
    long = "A " + " ".join(["bardzodlugislowo"] * 6)
    p = _p(long, brand="homestein", model="DEMU")
    tt.transform(p)
    assert " " not in p.title[-1]   # no trailing space
    assert not p.title.endswith("-")


# ── OEM/supplier strip ──────────────────────────────────────────────────────


def test_strips_modernhome(tt):
    p = _p("MODERNHOME HOMESTEIN LUGANO-39 2W1", brand="homestein", model="LUGANO-39")
    tt.transform(p)
    assert "MODERNHOME" not in p.title
    assert "HOMESTEIN" in p.title
    assert "LUGANO-39" in p.title


def test_strips_iplay_and_modernhome(tt):
    p = _p("IPLAY MODERNHOME drewniany domek dla lalek", brand="hopla_toys")
    tt.transform(p)
    assert "IPLAY" not in p.title
    assert "MODERNHOME" not in p.title
    assert "HOPLA TOYS" in p.title


def test_strips_ecotoys(tt):
    p = _p("ECOTOYS Drewniany Domek dla Lalek", brand="hopla_toys")
    tt.transform(p)
    assert "ECOTOYS" not in p.title
    assert "HOPLA TOYS" in p.title


def test_strips_bauerkraft(tt):
    p = _p("BAUERKRAFT trampolina ogrodowa 305 CM PREMIUM",
           brand="gardenstein", model="ASPEN")
    tt.transform(p)
    assert "BAUERKRAFT" not in p.title
    assert "GARDENSTEIN" in p.title
    assert "ASPEN" in p.title


def test_strips_all_known_oem_regardless_of_detected_brand(tt):
    # supplier names are global noise; strip MODERNHOME even from a homestein title
    p = _p("MODERNHOME COŚ", brand="homestein")
    tt.transform(p)
    assert "MODERNHOME" not in p.title


# ── brand display injection ─────────────────────────────────────────────────


def test_appends_brand_if_missing(tt):
    # 2026-07-01: rev — TYP + CECHY + BRAND + MODEL (user request: brand nie pierwsza)
    p = _p("Rowerek biegowy dla dzieci", brand="hopla_toys")
    tt.transform(p)
    assert "HOPLA TOYS" in p.title
    # Brand nie może być pierwszym słowem
    assert not p.title.startswith("HOPLA TOYS")


def test_does_not_duplicate_brand(tt):
    p = _p("HOPLA TOYS rowerek biegowy", brand="hopla_toys")
    tt.transform(p)
    assert p.title.count("HOPLA TOYS") == 1


def test_no_brand_for_unknown(tt):
    p = _p("Coś tam", brand="unknown")
    p.category_name = ""   # disable enrichment so we can isolate brand logic
    tt.transform(p)
    # unknown brand → no brand append; title still uppercased
    assert p.title == "COŚ TAM"


# ── model handling ──────────────────────────────────────────────────────────


def test_appends_model_if_missing(tt):
    p = _p("Drewniany domek", brand="hopla_toys", model="TOLA-2")
    tt.transform(p)
    assert "TOLA-2" in p.title


def test_does_not_duplicate_model(tt):
    p = _p("DREWNIANY DOMEK TOLA-2", brand="hopla_toys", model="TOLA-2")
    tt.transform(p)
    assert p.title.count("TOLA-2") == 1


# ── quote / whitespace normalization ────────────────────────────────────────


def test_strips_quotes(tt):
    p = _p('"FLAM rowerek biegowy dla dzieci eva"', brand="hopla_toys", model="FLAM")
    tt.transform(p)
    assert '"' not in p.title
    assert "“" not in p.title


def test_normalizes_multiple_spaces(tt):
    p = _p("drewniany    domek     dla   lalek", brand="hopla_toys")
    tt.transform(p)
    assert "  " not in p.title


# ── transform_all batch API ─────────────────────────────────────────────────


def test_transform_all_runs_in_place(tt):
    ps = [_p("drewniany domek dla lalek", brand="hopla_toys") for _ in range(3)]
    tt.transform_all(ps)
    for p in ps:
        assert "HOPLA TOYS" in p.title


# ── enrichment (short titles get category noun + dimensions) ───────────────


def test_short_title_gets_product_type(tt):
    # v4: product type comes from product_types.json, not category leaf.
    p = _p("Drewniana kuchnia dla dzieci", brand="hopla_toys")
    p.category_name = "Zabawki / Kuchnie"
    tt.transform(p)
    assert "DREWNIANA KUCHNIA" in p.title
    assert "HOPLA TOYS" in p.title


def test_short_title_gets_dimensions_from_attrs(tt):
    p = _p("Domek dla lalek", brand="hopla_toys")
    p.attributes = {"Wymiary": "87 x 32 x 114 cm"}
    tt.transform(p)
    assert "87X32X114 CM" in p.title


def test_product_type_matches_polish_plural(tt):
    # Detector token-set + stem-prefix match: "krzesła" matches "krzesło".
    p = _p("Lugano-39", brand="homestein", model="LUGANO-39")
    p.category_name = "meble/krzesła do jadalni"
    tt.transform(p)
    assert "KRZESŁO DO JADALNI" in p.title


def test_long_title_skips_enrichment(tt):
    long = "DOMEK Z WINDĄ DLA LALEK REZYDENCJA MALIBU ŚWIECĄCE KOŁA"
    p = _p(long, brand="hopla_toys")
    p.category_name = "DLA DZIECI / Domki dla lalek"
    tt.transform(p)
    # Already > ENRICH_BELOW chars → no category noun injected.
    assert "DOMKI DLA LALEK" not in p.title
    assert "DOMEK" in p.title


def test_generic_category_leaf_falls_through(tt):
    p = _p("Coś", brand="hopla_toys")
    p.category_name = "Wyposażenie"  # too generic → no noun injection
    tt.transform(p)
    # No category noun ("WYPOSAŻENIE" is in generic list); only brand appended.
    assert "WYPOSAŻENIE" not in p.title
    assert "HOPLA TOYS" in p.title


def test_audience_tag_added_from_category_root(tt):
    """`DLA DZIECI` at category root is a hard audience signal, not generic noise."""
    p = _p("Zegar", brand="hopla_toys")
    p.category_name = "DLA DZIECI / Zegary edukacyjne"
    tt.transform(p)
    assert "DLA DZIECI" in p.title


def test_feature_tokens_from_attributes(tt):
    """Materiał / Kolor / Przeznaczenie from attrs are injected when title is short."""
    p = _p("Zestaw mebli", brand="gardenstein", model="WENECJA")
    p.category_name = "Dom i Ogród/Ogród/Meble ogrodowe/Komplety mebli"
    p.attributes = {
        "Materiał dominujący": "technorattan",
        "Kolor dominujący": "brązowy",
        "Przeznaczenie": "komplet obiadowy",
    }
    tt.transform(p)
    assert "TECHNORATTAN" in p.title
    assert "BRĄZOWY" in p.title
    assert "GARDENSTEIN" in p.title
    assert "WENECJA" in p.title


def test_enrichment_stops_at_target(tt):
    """Once we reach ENRICH_TARGET, we stop adding feature tokens to leave room
    for brand + model."""
    p = _p("Krzesło tapicerowane", brand="homestein", model="DEMU-12")
    p.category_name = "Meble/Krzesła do jadalni"
    p.attributes = {
        "Materiał dominujący": "welurowe",
        "Kolor dominujący": "czarne",
        "Przeznaczenie": "do jadalni",
        "Linia": "Skandynawska",
        "Wiek": "dorośli",
        "Liczba osób": "1",
    }
    tt.transform(p)
    assert "HOMESTEIN" in p.title
    assert "DEMU-12" in p.title
    assert len(p.title) <= 75


def test_dimensions_not_duplicated_when_already_in_title(tt):
    p = _p("Basen 188x46 cm", brand="intex")
    p.attributes = {"Wymiary": "188 x 46 cm"}
    tt.transform(p)
    # The dim regex already matches "188x46 cm" in the title → don't re-add.
    assert p.title.upper().count("188") == 1


# ── OEM catalogue integrity ─────────────────────────────────────────────────


def test_oem_union_covers_all_per_brand():
    union = set().union(*_OEM_BY_BRAND.values())
    assert union.issubset(_ALL_OEM)


def test_known_oem_present():
    for name in ("ecotoys", "iplay", "modernhome", "multistore",
                 "multigames", "multistar", "multigarden",
                 "bauerkraft", "molden"):
        assert name in _ALL_OEM


# ── 2026-07-01 rev: TYP + CECHY + BRAND + MODEL pattern ─────────────────────


def test_type_first_then_brand_then_model(tt):
    p = _p("Domek z windą dla lalek REZYDENCJA MALIBU", brand="hopla_toys", model="GUDON")
    p.category_name = "DLA DZIECI / Domki dla lalek"
    tt.transform(p)
    # Order: DOMEK DLA LALEK → REZYDENCJA MALIBU → HOPLA TOYS → GUDON
    pos_type = p.title.find("DOMEK DLA LALEK")
    pos_brand = p.title.find("HOPLA TOYS")
    pos_model = p.title.find("GUDON")
    assert pos_type == 0  # TYP na początku
    assert pos_type < pos_brand < pos_model


def test_intex_exception_preserves_original(tt):
    p = _p("INTEX basen stelażowy ogrodowy 305x76 cm 56406", brand="intex")
    tt.transform(p)
    # INTEX original is good — only UPPERCASE, no rebuild.
    assert "305X76" in p.title or "305x76".upper() in p.title
    assert "56406" in p.title
    assert p.title.startswith("INTEX")


def test_intex_brand_prepended_if_missing(tt):
    p = _p("basen stelażowy 305x76", brand="intex")
    tt.transform(p)
    assert p.title.startswith("INTEX")


def test_legacy_descriptors_kept_when_type_matches(tt):
    """REZYDENCJA MALIBU, ŚWIECĄCE KOŁA — descriptive tokens stay in the title."""
    p = _p(
        "Domek z windą dla lalek - REZYDENCJA MALIBU ŚWIECĄCE KOŁA",
        brand="hopla_toys", model="GUDON",
    )
    p.category_name = "DLA DZIECI / Domki dla lalek"
    tt.transform(p)
    assert "REZYDENCJA" in p.title or "MALIBU" in p.title


def test_supplier_sku_token_dropped(tt):
    """Opaque codes like XBODY1B, G70, SZALS17 are supplier SKUs — drop them."""
    p = _p("VICE Z G70 LIFEKRAFT CODZIENNY LIFESTYLE", brand="lifekraft")
    p.category_name = "Biżuteria ze stali chirurgicznej/Biżuteria ślubna"
    tt.transform(p)
    assert "G70" not in p.title


# ── validator ───────────────────────────────────────────────────────────────


def test_validate_clean_title():
    from app.transformer.title_transformer import validate_title
    assert validate_title("HOPLA TOYS GUDON DOMEK DLA LALEK DREWNIANY") == []


def test_validate_catches_forbidden_do_metal():
    from app.transformer.title_transformer import validate_title
    issues = validate_title("HOMESTEIN MILAN STÓŁ DO METAL CZARNY")
    assert any(i.startswith("forbidden:") for i in issues)


def test_validate_catches_forbidden_do_inny():
    from app.transformer.title_transformer import validate_title
    issues = validate_title("ZESTAW MEBLI OGRODOWYCH NA DO INNY OGRÓD")
    assert any(i.startswith("forbidden:") for i in issues)


def test_validate_catches_do_8w1():
    from app.transformer.title_transformer import validate_title
    issues = validate_title("HOPLA TOYS DOMEK DLA LALEK DREWNIANY DO 8W1")
    assert any(i.startswith("forbidden:") for i in issues)


def test_validate_length_over_limit():
    from app.transformer.title_transformer import validate_title
    long = "HOPLA TOYS " + "X" * 90
    issues = validate_title(long)
    assert any("length" in i for i in issues)


def test_validate_min_4_words():
    from app.transformer.title_transformer import validate_title
    issues = validate_title("HOPLA TOYS DOMEK")
    assert "min_4_words" in issues


# ── detector ────────────────────────────────────────────────────────────────


def test_detector_finds_dollhouse():
    from app.transformer.product_type_detector import ProductTypeDetector
    d = ProductTypeDetector()
    assert d.detect("Domek z windą dla lalek", "Domki dla lalek", "hopla_toys") == "DOMEK DLA LALEK"


def test_detector_falls_back_across_brands():
    from app.transformer.product_type_detector import ProductTypeDetector
    d = ProductTypeDetector()
    # No brand context — still matches the right type.
    assert d.detect("Hamak ogrodowy duży", "Ogród", None) == "HAMAK OGRODOWY"


def test_detector_returns_empty_for_unknown():
    from app.transformer.product_type_detector import ProductTypeDetector
    d = ProductTypeDetector()
    assert d.detect("Coś tam nieznanego XYZ", "Inne", "hopla_toys") == ""


def test_detector_polish_plural_match():
    from app.transformer.product_type_detector import ProductTypeDetector
    d = ProductTypeDetector()
    # Category uses plural "krzesła"; dictionary key is singular "krzesło".
    assert d.detect("Lugano-39", "krzesła do jadalni", "homestein") == "KRZESŁO DO JADALNI"
