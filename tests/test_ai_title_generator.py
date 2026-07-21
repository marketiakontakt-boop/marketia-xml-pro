"""AI title generator tests — mocked Gemini client, real SQLite cache."""
from __future__ import annotations

import sqlite3

import pytest

from app.ai.prompts import TITLE_PROMPT_VERSION
from app.ai.title_generator import AITitleGenerator, _clean_response
from app.cache.sqlite_cache import init_schema, get_ai_title
from app.parser.normalizer import Product


# ── helpers ─────────────────────────────────────────────────────────────────


def _p(sku: str, name: str, brand: str = "hopla_toys", model: str = "") -> Product:
    p = Product(
        product_id="1", sku=sku, ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0,
        quantity=0, name=name, category_name="Zabawki",
        manufacturer_name="", description="",
        description_extra_1="", description_extra_2="",
    )
    p.brand = brand
    p.model_name = model
    return p


class _FakeClient:
    """Single-call + batch-call fake. Returns canned responses by SKU."""

    def __init__(self, by_sku: dict[str, str] | None = None, single: str = ""):
        self.by_sku = by_sku or {}
        self.single = single
        self.calls = 0

    def call(self, system: str, content: str, json_mode: bool = False) -> str:
        self.calls += 1
        return self.single

    def generate_all(self, requests, progress_callback=None, wait_on_cooldown=True, cancel_check=None):
        out: dict[str, str | None] = {}
        for req in requests:
            cid = req["custom_id"]
            out[cid] = self.by_sku.get(cid, "")
            if progress_callback:
                progress_callback(len(out), len(requests), cid)
        return out


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def brand_data():
    return {
        "hopla_toys":    {"name": "HOPLA TOYS",    "keywords": []},
        "homestein":       {"name": "HOMESTEIN",       "keywords": []},
        "gardenstein":   {"name": "GARDENSTEIN",   "keywords": []},
        "intex":         {"name": "INTEX",         "keywords": []},
        "marketia_home": {"name": "MARKETIA HOME", "keywords": []},
        "lifekraft":     {"name": "LIFEKRAFT",     "keywords": []},
        "zoovera":       {"name": "ZOOVERA",       "keywords": []},
    }


# ── _clean_response unit tests ──────────────────────────────────────────────


def test_clean_response_uppercases():
    assert _clean_response("lalka drewniana") == "LALKA DREWNIANA"


def test_clean_response_strips_fences():
    assert "```" not in _clean_response("```\nLALKA DREWNIANA\n```")


def test_clean_response_strips_quotes():
    assert "\"" not in _clean_response('"LALKA DREWNIANA"')


def test_clean_response_trims_75_chars_on_word_boundary():
    long = " ".join(["LALKA"] * 30)
    out = _clean_response(long)
    assert len(out) <= 75
    assert not out.endswith(" ")


def test_clean_response_empty_safe():
    assert _clean_response("") == ""
    assert _clean_response(None) == ""


# ── generate_one (cache + fresh) ────────────────────────────────────────────


def test_generate_one_uses_cache(conn, brand_data):
    gen = AITitleGenerator(conn, brand_data=brand_data, client=_FakeClient(single="X"))
    from app.cache.sqlite_cache import save_ai_title
    save_ai_title(conn, "SKU-1", "CACHED HOPLA TOYS TITLE", TITLE_PROMPT_VERSION)
    p = _p("SKU-1", "anything")
    assert gen.generate_one(p) == "CACHED HOPLA TOYS TITLE"
    assert gen.client.calls == 0


def test_generate_one_fresh_call_persists(conn, brand_data):
    fake = _FakeClient(single="LALKA DREWNIANA HOPLA TOYS PREMIUM")
    gen = AITitleGenerator(conn, brand_data=brand_data, client=fake)
    p = _p("SKU-2", "drewniana lalka")
    out = gen.generate_one(p)
    assert out == "LALKA DREWNIANA HOPLA TOYS PREMIUM"
    assert get_ai_title(conn, "SKU-2") == out
    assert fake.calls == 1


def test_generate_one_force_bypasses_cache(conn, brand_data):
    fake = _FakeClient(single="NEW HOPLA TOYS TITLE")
    gen = AITitleGenerator(conn, brand_data=brand_data, client=fake)
    from app.cache.sqlite_cache import save_ai_title
    save_ai_title(conn, "SKU-3", "OLD CACHED TITLE", TITLE_PROMPT_VERSION)
    out = gen.generate_one(_p("SKU-3", "x"), force=True)
    assert out == "NEW HOPLA TOYS TITLE"


# ── generate_all (batch) ────────────────────────────────────────────────────


def test_generate_all_batch_mixes_cache_and_fresh(conn, brand_data):
    from app.cache.sqlite_cache import save_ai_title
    save_ai_title(conn, "A", "CACHED A TITLE", TITLE_PROMPT_VERSION)
    fake = _FakeClient(by_sku={"B": "FRESH B TITLE", "C": "FRESH C TITLE"})
    gen = AITitleGenerator(conn, brand_data=brand_data, client=fake)
    results = gen.generate_all([_p("A", "x"), _p("B", "y"), _p("C", "z")])
    assert results["A"] == "CACHED A TITLE"
    assert results["B"] == "FRESH B TITLE"
    assert results["C"] == "FRESH C TITLE"


def test_apply_to_products_writes_in_place(conn, brand_data):
    # Fix 2026-07-11: apply_to_products pisze do p.title, nie p.name.
    # GUI/eksport używa `p.title or p.name` — TitleTransformer pisze do p.title,
    # więc AI musi robić to samo, żeby wygrać z deterministycznym.
    fake = _FakeClient(by_sku={"A": "AI TITLE A", "B": "AI TITLE B"})
    gen = AITitleGenerator(conn, brand_data=brand_data, client=fake)
    ps = [_p("A", "old A"), _p("B", "old B")]
    updated = gen.apply_to_products(ps)
    assert updated == 2
    assert ps[0].title == "AI TITLE A"
    assert ps[1].title == "AI TITLE B"
    # p.name (oryginał XML) pozostaje nietknięty
    assert ps[0].name == "old A"
    assert ps[1].name == "old B"


def test_apply_skips_when_response_empty(conn, brand_data):
    fake = _FakeClient(by_sku={"A": ""})  # empty → keep original
    gen = AITitleGenerator(conn, brand_data=brand_data, client=fake)
    p = _p("A", "orig")
    gen.apply_to_products([p])
    assert p.title == ""  # nie ustawione bo AI zwrócił nic
    assert p.name == "orig"
