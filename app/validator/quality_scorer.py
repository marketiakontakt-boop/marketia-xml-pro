"""Description quality scorer — 0-10 scale.

Counts sections under both layouts:
  - legacy v1: <div class="wiersz">
  - v2 7-section: <section class="section">

Criteria (10 pts total):
  3 pts — sections:   7+ = 3 | 5-6 = 2 | 3-4 = 1 | <3 = 0
  2 pts — length:     >4000 chars = 2 | >2500 = 1 | else 0
  1 pt  — spec:       SPECYFIKACJA / DANE TECHNICZNE section present
  2 pts — numbers:    >6 numeric values = 2 | >2 = 1 | else 0
  1 pt  — bold:       ≥4 <b> tags
  1 pt  — h2 headers: ≥5 h2 tags
"""
from __future__ import annotations

import re

SCORE_LABELS = {
    range(9, 11): ("Świetny", "#1a6f3a"),
    range(7, 9):  ("Dobry",   "#2d7d46"),
    range(5, 7):  ("OK",      "#b08000"),
    range(0, 5):  ("Słaby",   "#c0392b"),
}


def score_description(html: str) -> int:
    if not html or not html.strip():
        return 0

    score = 0

    # v1 wiersz blocks OR v2 section.section blocks
    sections = len(re.findall(r'class=["\']wiersz["\']', html))
    if sections == 0:
        sections = len(re.findall(r'<section\s+class=["\']section["\']', html, re.IGNORECASE))
    if sections >= 7:
        score += 3
    elif sections >= 5:
        score += 2
    elif sections >= 3:
        score += 1

    length = len(html)
    if length > 4000:
        score += 2
    elif length > 2500:
        score += 1

    html_upper = html.upper()
    if "SPECYFIKACJA" in html_upper or "DANE TECHNICZNE" in html_upper:
        score += 1

    numbers = len(re.findall(r'\b\d+(?:[.,]\d+)?\s*(?:cm|kg|l|ltr|m|szt|pcs|mb|mm|w|v|rpm)?\b', html, re.IGNORECASE))
    if numbers > 6:
        score += 2
    elif numbers > 2:
        score += 1

    bolds = len(re.findall(r'<b>', html, re.IGNORECASE))
    if bolds >= 4:
        score += 1

    h2s = len(re.findall(r'<h2', html, re.IGNORECASE))
    if h2s >= 5:
        score += 1

    return min(score, 10)


def get_label(score: int) -> tuple[str, str]:
    for r, label in SCORE_LABELS.items():
        if score in r:
            return label
    return ("?", "#888888")
