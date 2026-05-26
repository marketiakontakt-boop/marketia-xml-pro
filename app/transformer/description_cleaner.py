"""Strip legacy JUMI-format HTML from product descriptions.

JUMI is a BaseLinker HTML template plugin that uses section/item-6 layout.
Products with JUMI descriptions need fresh AI descriptions in .wiersz/.tekst format.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parser.normalizer import Product

_JUMI_MARKERS = re.compile(
    r'class=["\'](?:section|item item-\d+|text-item|image-item)["\']'
)


def _is_jumi(html: str) -> bool:
    return bool(_JUMI_MARKERS.search(html))


def strip_jumi_descriptions(products: list["Product"]) -> int:
    """Clear JUMI-format descriptions from products not yet processed by AI.

    Products with JUMI descriptions will be regenerated in the AI step.
    Also clears description_extra_1/2 if they contain JUMI format.
    Returns count of products whose descriptions were cleared.
    """
    count = 0
    for p in products:
        if p.ai_done:
            continue
        changed = False
        if p.description and _is_jumi(p.description):
            p.description = ""
            changed = True
        if p.description_extra_1 and _is_jumi(p.description_extra_1):
            p.description_extra_1 = ""
            changed = True
        if p.description_extra_2 and _is_jumi(p.description_extra_2):
            p.description_extra_2 = ""
            changed = True
        if changed:
            count += 1
    return count
