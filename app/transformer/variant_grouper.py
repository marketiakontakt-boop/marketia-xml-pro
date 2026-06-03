"""Detect and assign variant groups across products.

Products sharing the same model_name base (first word) and brand are considered
variants of each other (e.g. "Milan Białe", "Milan Żółte" → group 1).

Assigns:
  product.variant_group_id  — shared positive int per group (0 = standalone)
  product.variant_name      — the suffix part ("Białe", "Żółte", "XL"…)
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parser.normalizer import Product


def _base_and_variant(model_name: str) -> tuple[str, str]:
    """Split 'Milan Białe' → ('MILAN', 'Białe').  Single word → (word, '')."""
    parts = model_name.strip().split(None, 1)
    if len(parts) == 1:
        return parts[0].upper(), ""
    return parts[0].upper(), parts[1]


def detect_variant_groups(products: list["Product"]) -> dict[str, list["Product"]]:
    """Return groups dict: group_key → list of products (≥2 members only)."""
    groups: dict[str, list["Product"]] = defaultdict(list)
    for p in products:
        if not p.model_name:
            continue
        base, _ = _base_and_variant(p.model_name)
        key = f"{p.brand}::{base}"
        groups[key].append(p)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def assign_variant_groups(products: list["Product"]) -> int:
    """Assign variant_group_id and variant_name to all products.

    Returns the number of variant groups found.
    """
    # Reset
    for p in products:
        p.variant_group_id = 0
        p.variant_name = ""

    groups = detect_variant_groups(products)
    for group_id, (key, members) in enumerate(groups.items(), start=1):
        for p in members:
            _, suffix = _base_and_variant(p.model_name)
            p.variant_group_id = group_id
            p.variant_name = suffix or p.model_name

    return len(groups)
