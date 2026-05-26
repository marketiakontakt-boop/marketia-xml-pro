"""Map BaseLinker categories to Allegro taxonomy paths."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parser.normalizer import Product

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "allegro_categories.json"


def load_category_map() -> dict[str, str]:
    """Load BaseLinker → Allegro category mapping from JSON."""
    if not _DATA_PATH.exists():
        return {}
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def map_category(baselinker_cat: str, category_map: dict[str, str]) -> str | None:
    """Return Allegro taxonomy path for a BaseLinker category, or None if unknown."""
    return category_map.get(baselinker_cat)


def map_all_products(products: list["Product"], category_map: dict[str, str]) -> None:
    """Set product.allegro_category for all products. Unknown categories → empty string."""
    for p in products:
        mapped = map_category(p.category_name, category_map)
        p.allegro_category = mapped or ""


def suggest_category_gemini(baselinker_cat: str) -> str:
    """Ask Gemini to suggest an Allegro category path for an unknown BaseLinker category."""
    from app.ai.claude_client import ClaudeClient
    client = ClaudeClient()
    system = (
        "Jesteś ekspertem od taksonomii Allegro.pl. "
        "Odpowiedz TYLKO ścieżką kategorii Allegro w formacie: "
        "'Nadkategoria > Kategoria > Podkategoria'. Bez żadnych dodatkowych słów."
    )
    prompt = (
        f"Kategoria z BaseLinker: '{baselinker_cat}'\n"
        "Podaj najlepiej pasującą ścieżkę kategorii Allegro.pl dla tej kategorii produktu."
    )
    result = client.call(system, prompt)
    return result.strip()
