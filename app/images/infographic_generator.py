"""Generate parameter infographic overlays on product packshots.

2026-07-12g: Refactor — jedna DUŻA infografika z tabelą DANE TECHNICZNE (packshot 35%
+ zielony pasek 65%) zamiast trzech małych. Generowana TYLKO gdy AI opis produktu
zawiera sekcję spec_rows (parametry w `<li><b>Klucz:</b> Wartość</li>`).
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.parser.normalizer import Product

CANVAS = 800
# GARDENSTEIN green (2026-07-12: #4D7021 alpha 235). Dark accent: #344E16.
ACCENT_COLOR = (77, 112, 33, 235)
ACCENT_DARK = (52, 78, 22)
KEY_COLOR = (220, 245, 200)  # jasny zielony dla klucza w tabeli

# --- Legacy regexes/maps (backward compat) ---
_DIM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[Xx×]\s*(\d+(?:[.,]\d+)?)(?:\s*[Xx×]\s*(\d+(?:[.,]\d+)?))?\s*(CM|MM|M)\b", re.IGNORECASE)
_COLOR_RE = re.compile(r"\b(CZARN[YAE]|BIAŁ[YAE]|SZAR[YAE]|BEŻOW[YAE]|BRĄZOW[YAE]|ZIELON[YAE]|NIEBIESK[IAE]|CZERWON[YAE]|ŻÓŁT[YAE])\b", re.IGNORECASE)
_MATERIAL_RE = re.compile(r"\b(ALUMINIOW[YAE]|DREWNIAN[YAE]|METALOW[YAE]|STALOW[YAE]|POLIWĘGLANOW[YAE]|PLASTIKOW[YAE]|BAMBUSOW[YAE]|RATTANOW[YAE]|TEKSTYLN[YAE])\b", re.IGNORECASE)
_COLOR_MAP = {"czarn": "Czarny", "biał": "Biały", "szar": "Szary", "beżow": "Beżowy", "brązow": "Brązowy", "zielon": "Zielony", "niebiesk": "Niebieski", "czerwon": "Czerwony", "żółt": "Żółty"}
_MAT_MAP = {"aluminiow": "Aluminium", "drewnian": "Drewno", "metalow": "Metal", "stalow": "Stal", "poliwęglanow": "Poliwęglan", "plastikow": "Plastik", "bambusow": "Bambus", "rattanow": "Rattan", "tekstyln": "Tekstylia"}

def _font(sz: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load a system font at size; fall back to default."""
    cands = ["/System/Library/Fonts/Helvetica.ttc",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
             else "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for c in cands:
        try:
            return ImageFont.truetype(c, sz, index=1 if bold and c.endswith(".ttc") else 0)
        except Exception:
            pass
    return ImageFont.load_default()

def _center_pack(src_path: Path, canvas_size: int) -> Image.Image:
    """Load packshot and center it on a white square canvas."""
    src = Image.open(src_path).convert("RGB")
    canvas = Image.new("RGB", (canvas_size, canvas_size), (255, 255, 255))
    r = min(canvas_size / src.width, canvas_size / src.height)
    nw, nh = int(src.width * r), int(src.height * r)
    canvas.paste(src.resize((nw, nh), Image.LANCZOS), ((canvas_size - nw) // 2, (canvas_size - nh) // 2))
    return canvas

def _rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners to an RGB image using an alpha mask. Returns RGBA."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    rgba = img.convert("RGBA"); rgba.putalpha(mask); return rgba

def _extract_dims(text: str) -> str:
    """LEGACY. Pull WxHxD dimensions from a free-text title; return formatted string or empty."""
    m = _DIM_RE.search(text or "")
    if not m: return ""
    a, b, c, unit = m.group(1), m.group(2), m.group(3), m.group(4).upper()
    parts = [a.replace(",", "."), b.replace(",", ".")]
    if c: parts.append(c.replace(",", "."))
    return f"{' × '.join(parts)} {unit}"

def _extract_by_map(text: str, regex: re.Pattern, mapping: dict[str, str]) -> str:
    """LEGACY. First-match extraction using a stem→label mapping."""
    m = regex.search(text or "")
    if not m: return ""
    token = m.group(1).lower()
    for stem, label in mapping.items():
        if token.startswith(stem): return label
    return ""

def generate_infographic(product_img_path: Path, brand: str, param_key: str, param_value: str) -> Image.Image:
    """LEGACY 2026-07-12g — use generate_spec_infographic instead. Backward compat."""
    _ = brand
    img = _center_pack(product_img_path, CANVAS).convert("RGBA")
    ov = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0)); od = ImageDraw.Draw(ov); y0 = CANVAS - 140
    od.rectangle([(0, y0), (CANVAS, CANVAS)], fill=ACCENT_COLOR)
    od.rectangle([(0, y0), (14, CANVAS)], fill=ACCENT_DARK + (255,))
    od.text((40, y0 + 24), param_key, font=_font(22, bold=True), fill=(255, 255, 255, 255))
    od.rectangle([(40, y0 + 55), (100, y0 + 58)], fill=(255, 255, 255, 255))
    od.text((40, y0 + 65), param_value, font=_font(42 if len(param_value) > 15 else 48, bold=True), fill=(255, 255, 255, 255))
    img = Image.alpha_composite(img, ov).convert("RGB")
    bg = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    rgba = _rounded_corners(img, 32); bg.paste(rgba, (0, 0), rgba); return bg

def extract_params(product: Product) -> list[tuple[str, str]]:
    """LEGACY 2026-07-12g — use _parse_spec_rows + _filter_spec_rows instead."""
    ai_title = ""
    try:
        from app.cache.sqlite_cache import open_cache, get_ai_title
        from app.ai.prompts import TITLE_PROMPT_VERSION
        with open_cache() as conn:
            ai_title = get_ai_title(conn, product.sku, prompt_version=TITLE_PROMPT_VERSION) or ""
    except Exception:
        pass
    src = f"{ai_title} {product.title} {product.name}".strip()
    out: list[tuple[str, str]] = []
    waga = (product.attributes or {}).get("Waga", "").strip()
    if waga:
        out.append(("WAGA", waga if any(u in waga.lower() for u in ("kg", "g")) else f"{waga} kg"))
    for label, rx, mp in (("KOLOR", _COLOR_RE, _COLOR_MAP), ("MATERIAŁ", _MATERIAL_RE, _MAT_MAP)):
        v = _extract_by_map(src, rx, mp)
        if v:
            out.append((label, v))
    return out

# --- NEW spec-table infographic (2026-07-12g) ---
_SPEC_ROW_RE = re.compile(r'<li><b>(.*?):</b>\s*(.*?)</li>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')
_NORM_SUFFIX_RE = re.compile(r"\s+(produktu|złożonego|zlozonego|całkowite|calkowite)\b.*$")
_SKIP_KEYS = {"kod", "kod ean", "ean", "sku", "marka", "producent", "model", "gwarancja", "typ"}
_SKIP_PREFIXES = ("kod ",)  # kod produktu / kod ean / itp.
_PRIORITY = ["wymiary", "waga", "materiał stelaża", "materiał poszycia", "materiał",
             "kolor", "maksymalne obciążenie", "zawartość zestawu", "funkcje", "zastosowanie"]

def _clean_html(s: str) -> str:
    """Strip tags + collapse whitespace."""
    return _WS_RE.sub(" ", _TAG_RE.sub("", s)).strip()

def _norm_key(k: str) -> str:
    """Normalize key: lowercase, strip, drop suffix 'produktu'/'złożonego'/'całkowite'."""
    return _NORM_SUFFIX_RE.sub("", k.lower().strip())

def _parse_spec_rows(desc_html: str) -> list[tuple[str, str]]:
    """Parse <li><b>Klucz:</b> Wartość</li> z AI description. Zwraca listę par."""
    out: list[tuple[str, str]] = []
    for m in _SPEC_ROW_RE.finditer(desc_html or ""):
        k, v = _clean_html(m.group(1)), _clean_html(m.group(2))
        if k and v:
            out.append((k, v))
    return out

def _filter_spec_rows(rows: list[tuple[str, str]], max_rows: int = 8) -> list[tuple[str, str]]:
    """Filter szumy + dedupe. Zwraca max_rows najlepszych wg priorytetu."""
    seen: set[str] = set(); kept: list[tuple[int, str, str]] = []
    for idx, (k, v) in enumerate(rows):
        nk = _norm_key(k)
        kl = k.lower().strip()
        if nk in _SKIP_KEYS or nk in seen or any(kl.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if nk.split()[:1] == ["wymiary"] and any(x.startswith("wymiary") for x in seen):
            continue
        seen.add(nk); kept.append((idx, k, v))
    def rank(it: tuple[int, str, str]) -> tuple[int, int]:
        nk = _norm_key(it[1])
        for i, p in enumerate(_PRIORITY):
            if nk.startswith(p): return (i, it[0])
        return (len(_PRIORITY), it[0])
    kept.sort(key=rank)
    return [(k, v) for _, k, v in kept[:max_rows]]

def generate_spec_infographic(product_img_path: Path, spec_rows: list[tuple[str, str]]) -> Image.Image:
    """Generuj DUŻĄ infografikę: packshot 35% (280px) + zielona tabela DANE TECHNICZNE 65%."""
    top_h = 280
    src = Image.open(product_img_path).convert("RGB")
    r = min(CANVAS / src.width, top_h / src.height); nw, nh = int(src.width * r), int(src.height * r)
    img = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    img.paste(src.resize((nw, nh), Image.LANCZOS), ((CANVAS - nw) // 2, (top_h - nh) // 2))
    img = img.convert("RGBA")
    ov = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    od.rectangle([(0, top_h), (CANVAS, CANVAS)], fill=ACCENT_COLOR)
    od.rectangle([(0, top_h), (14, CANVAS)], fill=ACCENT_DARK + (255,))
    # Header „DANE TECHNICZNE" — biały bold ~30px, wyśrodkowany + separator.
    f_head, header, hy = _font(30, bold=True), "DANE TECHNICZNE", top_h + 25
    hbb = od.textbbox((0, 0), header, font=f_head)
    od.text(((CANVAS - (hbb[2] - hbb[0])) // 2, hy), header, font=f_head, fill=(255, 255, 255, 255))
    sep_y = hy + 42
    od.rectangle([(60, sep_y), (CANVAS - 60, sep_y + 2)], fill=(255, 255, 255, 255))
    # Tabela — 2 kolumny, max 8 wierszy, key prawy-align, val lewy-align.
    f_row, rows_top = _font(22, bold=True), sep_y + 22
    row_h = min(44, (CANVAS - 20 - rows_top) // max(1, len(spec_rows)))
    pad, split_x = 30, int(CANVAS * 0.45); val_max = CANVAS - split_x - 12 - pad
    for i, (key, val) in enumerate(spec_rows):
        y = rows_top + i * row_h
        kw = od.textbbox((0, 0), key, font=f_row)[2]
        od.text((max(pad, split_x - 12 - kw), y), key, font=f_row, fill=KEY_COLOR + (255,))
        vs = val
        while od.textbbox((0, 0), vs, font=f_row)[2] > val_max and len(vs) > 3:
            vs = vs[:-2] + "…"
        od.text((split_x + 12, y), vs, font=f_row, fill=(255, 255, 255, 255))
    img = Image.alpha_composite(img, ov).convert("RGB")
    bg = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    rgba = _rounded_corners(img, 32); bg.paste(rgba, (0, 0), rgba); return bg

def generate_for_product(product: Product, thumb_path: Path, output_dir: Path) -> list[Path]:
    """Generate spec infographic (tylko gdy AI opis ma spec_rows ≥3). Save + cache."""
    from app.cache.sqlite_cache import open_cache, save_infographic
    filtered = _filter_spec_rows(_parse_spec_rows(product.description or ""), max_rows=8)
    if len(filtered) < 3:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{product.sku}_infographic_spec.jpg"
    generate_spec_infographic(thumb_path, filtered).save(out, "JPEG", quality=90)
    with open_cache() as conn:
        save_infographic(conn, product.sku, "SPEC", str(out))
    return [out]

def generate_all_infographics(products, thumb_dir: Path, output_dir: Path,
                              progress_cb=None, cancel_check=None) -> tuple[int, int]:
    """Batch-generate spec infographics. Skip brak thumb + brak/za mało spec_rows.

    Returns (generated, skipped_no_thumb).
    """
    from app.cache.sqlite_cache import open_cache, save_infographic
    thumb_dir, output_dir = Path(thumb_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    total, generated, skipped_no_thumb = len(products), 0, 0
    with open_cache() as conn:
        for idx, product in enumerate(products, start=1):
            if cancel_check and cancel_check():
                break
            thumb_path = thumb_dir / f"{product.sku}.jpg"
            if not thumb_path.exists():
                skipped_no_thumb += 1
            else:
                filtered = _filter_spec_rows(_parse_spec_rows(product.description or ""), max_rows=8)
                if len(filtered) >= 3:
                    out_path = output_dir / f"{product.sku}_infographic_spec.jpg"
                    generate_spec_infographic(thumb_path, filtered).save(out_path, "JPEG", quality=90)
                    save_infographic(conn, product.sku, "SPEC", str(out_path))
                    generated += 1
            if progress_cb:
                try: progress_cb(idx, total, product.sku)
                except Exception: pass
    return generated, skipped_no_thumb

if __name__ == "__main__":
    from lxml import etree
    from app.parser.normalizer import normalize_product
    from app.cache.sqlite_cache import open_cache
    wanted = ("2168", "833", "1463", "3387")
    targets: dict[str, Product] = {}
    for e in etree.parse("output/gardenstein-multistore.xml").getroot().iter("product"):
        se = e.find("sku")
        if se is not None and se.text in wanted:
            p = normalize_product(e); p.brand = "GARDENSTEIN"; targets[se.text] = p
    with open_cache() as conn:
        for sku in wanted:
            row = conn.execute("SELECT description_html FROM descriptions WHERE sku=?", (sku,)).fetchone()
            if sku not in targets or not row:
                print(f"SKU {sku}: brak danych"); continue
            targets[sku].description = row["description_html"]
            rows = _parse_spec_rows(targets[sku].description)
            filtered = _filter_spec_rows(rows, max_rows=8)
            print(f"\n=== SKU {sku}: {len(rows)} parsed → {len(filtered)} filtered ===")
            for k, v in filtered: print(f"  {k}: {v}")
            thumb = Path(f"output/thumbnails/{sku}.jpg")
            if not thumb.exists() or len(filtered) < 3:
                print(f"  SKIP: thumb={thumb.exists()} filtered={len(filtered)}"); continue
            out = Path(f"/tmp/spec_infographic_sample_{sku}.jpg")
            generate_spec_infographic(thumb, filtered).save(out, "JPEG", quality=90)
            print(f"  → {out}")
