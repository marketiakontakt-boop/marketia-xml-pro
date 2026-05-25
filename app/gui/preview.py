"""HTML preview — renders all descriptions with BaseLinker jumi CSS in system browser."""
from __future__ import annotations

import tempfile
import webbrowser
from pathlib import Path

from app.parser.normalizer import Product
from app.validator.quality_scorer import score_description, get_label

JUMI_CSS = """
body { font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
.product-card { background: white; border: 1px solid #ddd; border-radius: 8px; margin: 24px 0; padding: 24px; }
.product-header { border-bottom: 2px solid #0a5c99; padding-bottom: 12px; margin-bottom: 16px; }
.product-header h2 { margin: 0 0 4px; color: #0a5c99; font-size: 16px; }
.product-header .meta { color: #888; font-size: 12px; }
.score-badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; margin-left: 8px; }
.no-desc { color: #c0392b; font-style: italic; padding: 20px; }
/* Jumi / BaseLinker styles */
.wiersz { display: flex; gap: 20px; align-items: flex-start; margin: 16px 0; border-top: 1px solid #eee; padding-top: 16px; }
.wiersz:first-child { border-top: none; padding-top: 0; }
.tekst { flex: 1; }
.tekst h2 { font-size: 15px; color: #222; margin: 0 0 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.tekst p, .tekst ul, .tekst ol { font-size: 14px; line-height: 1.6; color: #333; margin: 0; }
.tekst ul { padding-left: 20px; }
.img { flex: 0 0 200px; }
.img img { width: 200px; height: 150px; object-fit: contain; border: 1px solid #eee; border-radius: 4px; }
"""


def open_preview(products: list[Product]) -> int:
    """Write all product descriptions to a temp HTML file and open in browser.

    Returns the count of products with descriptions shown.
    """
    with_desc = [p for p in products if p.description and p.description.strip()]

    if not with_desc:
        return 0

    rows_html = []
    for p in with_desc:
        score = p.quality_score if p.quality_score >= 0 else score_description(p.description)
        label, color = get_label(score)
        badge = f'<span class="score-badge" style="background:{color}">{score}/10 {label}</span>'
        ean_warn = "" if p.ean_valid else ' <span style="color:#c0392b;font-weight:bold">⚠ EAN błędny</span>'
        rows_html.append(f"""
<div class="product-card">
  <div class="product-header">
    <h2>{p.title or p.name}{badge}</h2>
    <div class="meta">SKU: {p.sku} | Marka: {p.brand or '—'} | EAN: {p.ean}{ean_warn} | Zdjęcia: {len(p.images)}</div>
  </div>
  {p.description}
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>Marketia XML Pro — Podgląd opisów ({len(with_desc)} prod.)</title>
<style>{JUMI_CSS}</style>
</head>
<body>
<h1 style="color:#0a5c99">Podgląd opisów — {len(with_desc)} produktów</h1>
{''.join(rows_html)}
</body>
</html>"""

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", encoding="utf-8", delete=False
    )
    tmp.write(html)
    tmp.close()
    webbrowser.open(f"file://{tmp.name}")
    return len(with_desc)
