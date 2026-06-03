"""Tests for v2 JSON-based description generation pipeline."""
import pytest
from app.ai.prompts import (
    _extract_json,
    _spec_items,
    assemble_html_from_json,
    build_description_prompt_v2,
    SYSTEM_PROMPT_JSON,
)
from app.parser.normalizer import Product


def _p(brand="jumi", name="Krzesło barowe czarne", **kwargs) -> Product:
    defaults = dict(
        product_id="X1", sku="X1", ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=5.0, width=45.0, height=100.0, length=40.0,
        quantity=0, category_name="", manufacturer_name="",
        description="", description_extra_1="", description_extra_2="",
    )
    defaults.update(kwargs)
    return Product(name=name, brand=brand, **defaults)


IMAGES = [f"https://cdn.example.com/img{i}.jpg" for i in range(6)]

SAMPLE_JSON = {
    "sections": [
        {"type": "bullets", "heading": "WYGODNY HOKER DO BARU", "items": [
            "<b>Regulacja wysokości:</b> 60–80 cm — pasuje do każdego blatu",
            "Obrót <b>360°</b> — swoboda ruchu",
            "Produkt marki <b>JUMI</b>",
        ]},
        {"type": "paragraph", "heading": "STYL I KOMFORT", "text": "Elegancki czarny welur..."},
        {"type": "paragraph", "heading": "SOLIDNA KONSTRUKCJA", "text": "Stalowe nogi nośność 120 kg."},
        {"type": "paragraph", "heading": "ZASTOSOWANIE", "text": "Idealny do wyspy kuchennej."},
        {"type": "spec", "rows": [["Materiał siedziska", "welur czarny"], ["Kolor nóg", "czarny mat"]]},
    ]
}


# ── _extract_json ─────────────────────────────────────────────────────────────

def test_extract_json_clean():
    raw = '{"sections": []}'
    result = _extract_json(raw)
    assert result == {"sections": []}


def test_extract_json_with_fences():
    raw = '```json\n{"sections": []}\n```'
    result = _extract_json(raw)
    assert result == {"sections": []}


def test_extract_json_with_stray_text():
    raw = 'Oto JSON:\n{"sections": [{"type": "spec", "rows": []}]}\nDzięki!'
    result = _extract_json(raw)
    assert result is not None
    assert "sections" in result


def test_extract_json_invalid_returns_none():
    assert _extract_json("To nie jest JSON") is None
    assert _extract_json("") is None
    assert _extract_json("{broken json") is None


def test_extract_json_plain_fences_without_lang():
    raw = '```\n{"sections": []}\n```'
    result = _extract_json(raw)
    assert result == {"sections": []}


# ── assemble_html_from_json ───────────────────────────────────────────────────

def test_assemble_produces_wiersz_divs():
    html = assemble_html_from_json(SAMPLE_JSON, IMAGES, [])
    assert html.count('<div class="wiersz">') == 5  # 4 content + 1 spec


def test_assemble_images_assigned_sequentially():
    html = assemble_html_from_json(SAMPLE_JSON, IMAGES, [])
    for i in range(5):
        assert f'img{i}.jpg' in html


def test_assemble_last_image_reused_when_sections_exceed_images():
    short_images = ["https://cdn.example.com/only.jpg"]
    html = assemble_html_from_json(SAMPLE_JSON, short_images, [])
    # All sections should use the only available image
    assert html.count("only.jpg") == 5


def test_assemble_no_images_produces_empty_src():
    html = assemble_html_from_json(SAMPLE_JSON, [], [])
    assert 'src=""' in html


def test_assemble_spec_merges_pre_filled_items():
    pre = ['<b>Szerokość:</b> 45 cm', '<b>Marka:</b> JUMI']
    html = assemble_html_from_json(SAMPLE_JSON, IMAGES, pre)
    assert '<b>Szerokość:</b> 45 cm' in html
    assert '<b>Marka:</b> JUMI' in html
    assert '<b>Materiał siedziska:</b>' in html


def test_assemble_spec_deduplication():
    # Pre-filled has "Szerokość", Gemini also returns "szerokość" — should not duplicate
    pre = ['<b>Szerokość:</b> 45 cm']
    data = {
        "sections": [
            {"type": "spec", "rows": [["szerokość", "55 cm"], ["Nośność", "120 kg"]]},
        ]
    }
    html = assemble_html_from_json(data, IMAGES, pre)
    assert html.count("Szerokość") == 1  # not duplicated
    assert "Nośność" in html


def test_assemble_bullets_rendered_as_ul():
    html = assemble_html_from_json(SAMPLE_JSON, IMAGES, [])
    assert "<ul>" in html
    assert "<li>" in html


def test_assemble_paragraph_rendered_as_p():
    html = assemble_html_from_json(SAMPLE_JSON, IMAGES, [])
    assert "<p>Elegancki czarny welur..." in html


def test_assemble_h2_headings_present():
    html = assemble_html_from_json(SAMPLE_JSON, IMAGES, [])
    assert "<h2>WYGODNY HOKER DO BARU</h2>" in html
    assert "<h2>SPECYFIKACJA:</h2>" in html


def test_assemble_unknown_section_type_skipped():
    data = {"sections": [
        {"type": "unknown_type", "heading": "X"},
        {"type": "spec", "rows": []},
    ]}
    html = assemble_html_from_json(data, IMAGES, [])
    assert html.count('<div class="wiersz">') == 1  # only spec


# ── _spec_items ───────────────────────────────────────────────────────────────

def test_spec_items_includes_dimensions():
    p = _p(width=45.0, height=100.0, length=40.0, weight=5.0)
    items = _spec_items(p, "JUMI")
    keys = " ".join(items)
    assert "Szerokość" in keys
    assert "Wysokość" in keys
    assert "Głębokość" in keys
    assert "Waga" in keys
    assert "JUMI" in keys


def test_spec_items_skips_zero_dimensions():
    p = _p(width=0.0, height=0.0, length=0.0, weight=0.0)
    items = _spec_items(p, "JUMI")
    # Only Marka should remain (EAN empty, no attributes)
    assert len(items) == 1
    assert "JUMI" in items[0]


def test_spec_items_includes_attributes():
    p = _p()
    p.attributes = {"Kolor": "czarny", "Materiał": "welur"}
    items = _spec_items(p, "JUMI")
    combined = " ".join(items)
    assert "Kolor" in combined
    assert "Materiał" in combined


# ── build_description_prompt_v2 ───────────────────────────────────────────────

def test_prompt_v2_contains_product_title():
    p = _p(name="Krzesło barowe PIADO czarne")
    prompt = build_description_prompt_v2(p, {"name": "JUMI"}, "jumi")
    assert "PIADO" in prompt or "Krzesło barowe" in prompt


def test_prompt_v2_includes_brand_hints():
    p = _p(brand="gardenstein", name="Zestaw mebli ogrodowych")
    prompt = build_description_prompt_v2(p, {"name": "GARDENSTEIN"}, "gardenstein")
    assert "technorattan" in prompt.lower() or "TECHNORATTAN" in prompt


def test_prompt_v2_requests_json():
    p = _p()
    prompt = build_description_prompt_v2(p, {"name": "JUMI"}, "jumi")
    assert "JSON" in prompt


def test_prompt_v2_includes_safety_for_hopla():
    p = _p(brand="hopla_toys", name="Hulajnoga dla dzieci")
    prompt = build_description_prompt_v2(p, {"name": "HOPLA TOYS"}, "hopla_toys")
    assert "EN71" in prompt or "EN 71" in prompt or "BEZPIECZEŃSTWO" in prompt


def test_prompt_v2_includes_safety_for_intex():
    p = _p(brand="intex", name="Basen stelażowy 457x122")
    prompt = build_description_prompt_v2(p, {"name": "INTEX"}, "intex")
    assert "CE" in prompt or "BEZPIECZEŃSTWO" in prompt


def test_system_prompt_json_mentions_json_format():
    assert "JSON" in SYSTEM_PROMPT_JSON
    assert "section_1" in SYSTEM_PROMPT_JSON
    assert "section_7" in SYSTEM_PROMPT_JSON
    assert "spec_rows" in SYSTEM_PROMPT_JSON
