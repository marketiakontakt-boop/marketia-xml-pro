"""Generate HTML audit report for all products and open in browser."""
from __future__ import annotations

import re
import tempfile
import webbrowser

from app.parser.normalizer import Product
from app.gui.brand_colors import get_brand_chip_colors

_CSS = """
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #F9FAFB; margin: 0; padding: 16px; color: #374151; }
h1 { font-size: 20px; margin-bottom: 4px; color: #111827; }
.subtitle { color: #6B7280; margin-bottom: 16px; font-size: 13px; }
.filters { display: flex; gap: 8px; margin-bottom: 16px; }
.filter-btn { padding: 6px 14px; border: 1px solid #E5E7EB; border-radius: 20px;
              background: white; cursor: pointer; font-size: 12px; color: #374151; }
.filter-btn.active { background: #2563EB; color: white; border-color: #2563EB; }
.product-card { background: white; border-radius: 8px; border: 1px solid #E5E7EB;
                margin-bottom: 12px; overflow: hidden;
                border-left: 4px solid #16A34A; }
.product-card.has-issues { border-left-color: #DC2626; }
.card-header { display: flex; align-items: center; gap: 10px;
               padding: 10px 14px; background: #F9FAFB; border-bottom: 1px solid #E5E7EB; }
.brand-chip { padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; }
.card-title { flex: 1; font-size: 13px; font-weight: 600; color: #111827; }
.q-badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
.q-ok { background: #DCFCE7; color: #15803D; }
.q-warn { background: #FEF3C7; color: #92400E; }
.q-bad { background: #FEE2E2; color: #DC2626; }
.card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
.meta-block, .attrs-block { padding: 10px 14px; font-size: 12px; line-height: 1.7; }
.meta-block { border-right: 1px solid #F3F4F6; }
.block-title { font-size: 11px; font-weight: bold; color: #6B7280;
               text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.ok { color: #15803D; } .warn { color: #EA580C; } .bad { color: #DC2626; }
.desc-block { padding: 8px 14px 10px; border-top: 1px solid #F3F4F6; font-size: 12px; }
.desc-preview { color: #374151; line-height: 1.5; max-height: 60px; overflow: hidden; }
.no-desc { color: #9CA3AF; font-style: italic; }
</style>
"""

_JS = """
<script>
function filterCards(mode) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.product-card').forEach(card => {
    const isIssue = card.classList.contains('has-issues');
    const hasDesc = card.dataset.hasDesc === '1';
    if (mode === 'all') card.style.display = '';
    else if (mode === 'issues') card.style.display = isIssue ? '' : 'none';
    else if (mode === 'desc') card.style.display = hasDesc ? '' : 'none';
    else if (mode === 'nodesc') card.style.display = !hasDesc ? '' : 'none';
  });
}
</script>
"""


def _has_issues(p: Product) -> bool:
    if getattr(p, "quality_score", -1) < 6:
        return True
    if not getattr(p, "allegro_category", ""):
        return True
    if not getattr(p, "attributes", {}):
        return True
    if len(p.title or "") > 75 or not p.title:
        return True
    return False


def _q_class(score: int) -> str:
    if score < 0:
        return "q-bad"
    if score >= 7:
        return "q-ok"
    if score >= 5:
        return "q-warn"
    return "q-bad"


def _product_card(p: Product) -> str:
    issue_cls = "has-issues" if _has_issues(p) else ""
    has_desc = "1" if getattr(p, "ai_done", False) else "0"

    bg, fg = get_brand_chip_colors(p.brand or "")
    brand_label = (p.brand or "—").upper()[:10]

    score = getattr(p, "quality_score", -1)
    q_text = f"Q: {score}/10" if score >= 0 else "Q: —"
    q_cls = _q_class(score)

    title_len = len(p.title or "")
    title_ok = "✓" if 0 < title_len <= 75 else "✗"
    title_cls = "ok" if title_ok == "✓" else "bad"
    title_note = f"{title_len}/75 zn."

    ean_ok = "✓" if getattr(p, "ean_valid", True) and p.ean else "✗"
    ean_cls = "ok" if ean_ok == "✓" else "bad"

    allegro_cat = getattr(p, "allegro_category", "")
    cat_ok = "✓" if allegro_cat else "?"
    cat_cls = "ok" if allegro_cat else "warn"
    cat_display = allegro_cat[:50] if allegro_cat else "brak — uruchom transformy"

    attrs = getattr(p, "attributes", {})
    attrs_html = "<br>".join(f"<b>{k}:</b> {v}" for k, v in list(attrs.items())[:5])
    if not attrs_html:
        attrs_html = '<span class="no-desc">brak atrybutów</span>'

    desc = getattr(p, "description", "") or ""
    desc_text = re.sub(r"<[^>]+>", " ", desc)
    desc_text = re.sub(r"\s+", " ", desc_text).strip()[:200]
    desc_html = (f'<div class="desc-preview">{desc_text}…</div>'
                 if desc_text else '<div class="no-desc">brak opisu — uruchom krok 4</div>')

    return f"""
<div class="product-card {issue_cls}" data-has-desc="{has_desc}">
  <div class="card-header">
    <span class="brand-chip" style="background:{bg};color:{fg}">{brand_label}</span>
    <span class="card-title">{p.title or p.name}</span>
    <span class="q-badge {q_cls}">{q_text}</span>
  </div>
  <div class="card-grid">
    <div class="meta-block">
      <div class="block-title">📝 Meta</div>
      SKU: {p.sku}<br>
      EAN: <span class="{ean_cls}">{ean_ok}</span> {p.ean or '—'}<br>
      Tytuł: <span class="{title_cls}">{title_ok}</span> {title_note}<br>
      Marka: {p.brand or '—'}<br>
      Kat. Allegro: <span class="{cat_cls}">{cat_ok}</span> {cat_display}
    </div>
    <div class="attrs-block">
      <div class="block-title">📊 Atrybuty ({len(attrs)})</div>
      {attrs_html}
    </div>
  </div>
  <div class="desc-block">
    <div class="block-title">📄 Opis</div>
    {desc_html}
  </div>
</div>"""


def generate_audit_html(products: list[Product]) -> str:
    issues = sum(1 for p in products if _has_issues(p))
    cards = "\n".join(_product_card(p) for p in products)
    return f"""<!DOCTYPE html>
<html lang="pl">
<head><meta charset="UTF-8"><title>Audit — Marketia Produktyzator</title>{_CSS}</head>
<body>
{_JS}
<h1>Audyt produktów — Marketia Produktyzator</h1>
<p class="subtitle">Łącznie: {len(products)} produktów | Z problemami: {issues}</p>
<div class="filters">
  <button class="filter-btn active" onclick="filterCards('all')">Wszystkie ({len(products)})</button>
  <button class="filter-btn" onclick="filterCards('issues')">Z problemami ({issues})</button>
  <button class="filter-btn" onclick="filterCards('desc')">Z opisem</button>
  <button class="filter-btn" onclick="filterCards('nodesc')">Bez opisu</button>
</div>
{cards}
</body></html>"""


def open_audit_preview(products: list[Product]) -> int:
    """Generate audit HTML and open in browser. Returns count of products."""
    if not products:
        return 0
    html = generate_audit_html(products)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        path = f.name
    webbrowser.open(f"file://{path}")
    return len(products)
