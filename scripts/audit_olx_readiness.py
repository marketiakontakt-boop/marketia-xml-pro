"""Audyt gotowości katalogu BL do wystawiania na OLX.

Weryfikuje 15 SKU (inventory 111048 "OLX Testy") pod kątem 9 kryteriów
publikacji na OLX. TYLKO odczyt — nie modyfikuje danych w BL.

Kryteria (per SKU):
  1. TITLE (name)             — 3-70 znaków, bez HTML
  2. DESCRIPTION              — 80-9000 znaków po strip HTML
  3. PRICE (prices[30157])    — > 0, sensowna
  4. EAN                      — 13 cyfr, GS1 check digit
  5. WYMIARY                  — weight, w/l/h > 0
  6. OBRAZKI                  — >=1 URL + HEAD 200 image/*
  7. KATEGORIA                — !=0 i istnieje w inventory
  8. STOCK (bl_55230)         — >=0 (WARN jeśli 0)
  9. OLX text_fields          — obecność `name|pl|olx_15037`, `description|pl|olx_15037`

Uruchomienie:
    ./venv/bin/python scripts/audit_olx_readiness.py

Wynik: markdown na stdout + `output/audit_olx_readiness_YYYY-MM-DD.md`.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.sync.baselinker_sync import _call, _list_products  # noqa: E402

TOKEN = os.getenv("BASELINKER_TOKEN", "").strip()
INVENTORY_ID = 111048
PRICE_GROUP_ID = 30157
STOCK_WAREHOUSE = "bl_55230"
OLX_NAME_KEY = "name|pl|olx_15037"
OLX_DESC_KEY = "description|pl|olx_15037"

SKUS = [
    "10", "100", "1004", "1005", "1162", "1168", "1192", "12",
    "1201", "1202", "1204", "1259", "1261", "1463", "1600",
]

# OLX-specific limits
TITLE_MIN = 3
TITLE_MAX = 70
DESC_MIN = 80
DESC_MAX = 9000
PRICE_MIN_SENSIBLE = 1.0
PRICE_MAX_SENSIBLE = 100_000.0

DANGEROUS_TAGS = re.compile(r"<\s*(script|iframe|object|embed|form)", re.IGNORECASE)


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(html: str) -> str:
    if not html:
        return ""
    p = _StripHTML()
    try:
        p.feed(html)
    except Exception:
        # malformed HTML — fallback: regex strip
        return re.sub(r"<[^>]+>", "", html).strip()
    return "".join(p.parts).strip()


def has_html_tags(s: str) -> bool:
    return bool(re.search(r"<[^>]+>", s or ""))


def is_malformed_or_dangerous(html: str) -> tuple[bool, str]:
    """Zwraca (True, powód) jeśli HTML wygląda podejrzanie."""
    if not html:
        return False, ""
    if DANGEROUS_TAGS.search(html):
        return True, "zawiera niedozwolony tag (script/iframe/object/embed/form)"
    # malformed check: liczba < vs > różni się znacząco
    open_c = html.count("<")
    close_c = html.count(">")
    if abs(open_c - close_c) > 3:
        return True, f"niezbalansowane nawiasy <>: {open_c} vs {close_c}"
    return False, ""


def ean_check(ean: str) -> tuple[bool, str]:
    """GS1 check digit dla EAN-13. Zwraca (valid, powód_błędu)."""
    if not ean:
        return False, "puste"
    if not ean.isdigit():
        return False, f"nie same cyfry: {ean!r}"
    if len(ean) != 13:
        return False, f"długość {len(ean)} zamiast 13"
    # GS1: cyfry na pozycjach nieparzystych (1,3,5...) razy 1, parzystych razy 3
    # Pozycje liczone od lewej, od 1. Suma mod 10, dopełnienie do 10 = check.
    digits = [int(c) for c in ean[:12]]
    # positions 1,3,5,7,9,11 (indexy 0,2,4,6,8,10) — waga 1
    # positions 2,4,6,8,10,12 (indexy 1,3,5,7,9,11) — waga 3
    s_odd = sum(digits[i] for i in range(0, 12, 2))
    s_even = sum(digits[i] for i in range(1, 12, 2))
    total = s_odd + 3 * s_even
    check = (10 - total % 10) % 10
    if check != int(ean[12]):
        return False, f"check digit {ean[12]} (oczekiwane {check})"
    return True, ""


def price_status(prices: dict) -> tuple[float | None, str]:
    """Zwraca (cena, status_msg). status_msg pusty = OK."""
    if not isinstance(prices, dict):
        return None, "brak prices w produkcie"
    raw = prices.get(str(PRICE_GROUP_ID)) if prices else None
    if raw is None:
        raw = prices.get(PRICE_GROUP_ID)
    if raw is None:
        return None, f"brak ceny w grupie {PRICE_GROUP_ID}"
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None, f"cena {raw!r} nie jest liczbą"
    if val <= 0:
        return val, f"cena <=0 ({val})"
    if val < PRICE_MIN_SENSIBLE:
        return val, f"cena podejrzanie niska ({val})"
    if val > PRICE_MAX_SENSIBLE:
        return val, f"cena podejrzanie wysoka ({val})"
    return val, ""


def stock_value(stock: dict) -> tuple[int | None, str]:
    """Zwraca (stock_bl_55230, msg)."""
    if not isinstance(stock, dict):
        return None, "brak stock w produkcie"
    raw = stock.get(STOCK_WAREHOUSE)
    if raw is None:
        return None, f"brak magazynu {STOCK_WAREHOUSE}"
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None, f"stock {raw!r} nie jest liczbą"
    return val, ""


def first_image_url(images) -> str | None:
    if not images:
        return None
    if isinstance(images, dict):
        try:
            keys_sorted = sorted(images.keys(), key=lambda x: int(x))
        except (TypeError, ValueError):
            keys_sorted = list(images.keys())
        for k in keys_sorted:
            v = images[k]
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(images, list):
        for v in images:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def image_head_check(url: str) -> tuple[bool, str]:
    """HEAD request. Zwraca (ok, msg)."""
    if not url:
        return False, "brak URL"
    try:
        resp = httpx.head(
            url,
            follow_redirects=True,
            timeout=5.0,
            headers={"User-Agent": "Mozilla/5.0 (audit)"},
        )
    except httpx.TimeoutException:
        return False, "timeout 5s"
    except Exception as e:
        return False, f"błąd HTTP: {type(e).__name__}: {e}"
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    ct = resp.headers.get("content-type", "")
    if not ct.startswith("image/"):
        # niektóre CDN nie zwracają content-type w HEAD — pozwól z warningiem
        return True, f"content-type={ct!r} (nietypowe, ale HTTP 200)"
    return True, ""


def load_categories(inv_id: int) -> set[int]:
    """Zwraca zbiór category_id dostępnych w inventory."""
    data = _call(TOKEN, "getInventoryCategories", {"inventory_id": inv_id})
    ids = set()
    for c in data.get("categories", []):
        try:
            ids.add(int(c.get("category_id", 0)))
        except (TypeError, ValueError):
            continue
    return ids


def audit_sku(sku: str, pid: str, categories: set[int]) -> dict:
    """Audyt jednego SKU. Zwraca dict z wynikami wszystkich kryteriów."""
    result: dict = {
        "sku": sku, "pid": pid,
        "blockers": [], "warnings": [], "infos": [],
    }
    try:
        data = _call(TOKEN, "getInventoryProductsData", {
            "inventory_id": INVENTORY_ID,
            "products": [str(pid)],
        })
    except Exception as e:
        result["blockers"].append(f"getInventoryProductsData failed: {e}")
        return result

    prod = (data.get("products") or {}).get(str(pid)) or {}
    if not prod:
        result["blockers"].append("Pusty produkt w response")
        return result

    result["prod"] = prod

    # ---- 1. TITLE ----
    tf = prod.get("text_fields") or {}
    title = (tf.get("name") or prod.get("name") or "").strip()
    result["title"] = title
    result["title_len"] = len(title)
    if not title:
        result["blockers"].append("TITLE: pusty")
    else:
        if len(title) < TITLE_MIN:
            result["blockers"].append(f"TITLE: za krótki ({len(title)} znaków, min {TITLE_MIN})")
        elif len(title) > TITLE_MAX:
            result["blockers"].append(f"TITLE: za długi ({len(title)} znaków, max {TITLE_MAX})")
        if has_html_tags(title):
            result["blockers"].append(f"TITLE: zawiera HTML — {title[:60]!r}")

    # ---- 2. DESCRIPTION ----
    desc_html = tf.get("description") or ""
    desc_plain = strip_html(desc_html)
    result["desc_html_len"] = len(desc_html)
    result["desc_plain_len"] = len(desc_plain)
    if not desc_plain:
        result["blockers"].append("DESC: pusty")
    else:
        if len(desc_plain) < DESC_MIN:
            result["blockers"].append(f"DESC: za krótki ({len(desc_plain)} znaków, min {DESC_MIN})")
        elif len(desc_plain) > DESC_MAX:
            result["blockers"].append(f"DESC: za długi ({len(desc_plain)} znaków, max {DESC_MAX})")
    bad, why = is_malformed_or_dangerous(desc_html)
    if bad:
        result["blockers"].append(f"DESC: HTML podejrzany — {why}")

    # ---- 3. PRICE ----
    price, msg = price_status(prod.get("prices") or {})
    result["price"] = price
    if msg:
        # rozróżnij fail vs warn
        if price is None or price <= 0:
            result["blockers"].append(f"PRICE: {msg}")
        else:
            result["warnings"].append(f"PRICE: {msg}")

    # ---- 4. EAN ----
    ean = str(prod.get("ean") or "").strip()
    result["ean"] = ean
    ok, why = ean_check(ean)
    if not ok:
        result["blockers"].append(f"EAN: {why}")

    # ---- 5. WYMIARY ----
    def _f(k: str) -> float:
        try:
            return float(prod.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    weight = _f("weight")
    width = _f("width")
    length = _f("length")
    height = _f("height")
    result["weight"] = weight
    result["width"] = width
    result["length"] = length
    result["height"] = height
    dim_bad = []
    if weight <= 0:
        dim_bad.append(f"weight={weight}")
    if width <= 0:
        dim_bad.append(f"width={width}")
    if length <= 0:
        dim_bad.append(f"length={length}")
    if height <= 0:
        dim_bad.append(f"height={height}")
    if dim_bad:
        result["blockers"].append("WYMIARY: " + ", ".join(dim_bad))
    if height == 1.0:
        result["warnings"].append("WYMIARY: height=1 (placeholder — basen okrągły?)")

    # ---- 6. OBRAZKI ----
    imgs = prod.get("images") or {}
    if isinstance(imgs, dict):
        img_count = sum(1 for v in imgs.values() if isinstance(v, str) and v)
    elif isinstance(imgs, list):
        img_count = sum(1 for v in imgs if isinstance(v, str) and v)
    else:
        img_count = 0
    result["img_count"] = img_count
    first_url = first_image_url(imgs)
    result["first_img"] = first_url
    if img_count == 0 or not first_url:
        result["blockers"].append("OBRAZKI: brak URL")
    else:
        ok, why = image_head_check(first_url)
        if not ok:
            result["blockers"].append(f"OBRAZKI: HEAD pierwszego URL — {why} ({first_url})")
        elif why:
            result["warnings"].append(f"OBRAZKI: {why}")

    # ---- 7. KATEGORIA ----
    try:
        cat_id = int(prod.get("category_id") or 0)
    except (TypeError, ValueError):
        cat_id = 0
    result["category_id"] = cat_id
    if cat_id == 0:
        result["blockers"].append("KATEGORIA: category_id=0")
    elif cat_id not in categories:
        result["blockers"].append(
            f"KATEGORIA: category_id={cat_id} nie istnieje w inventory {INVENTORY_ID}"
        )

    # ---- 8. STOCK ----
    stock, msg = stock_value(prod.get("stock") or {})
    result["stock"] = stock
    if msg:
        # brak magazynu — blocker (nie ma jak wystawić)
        result["blockers"].append(f"STOCK: {msg}")
    else:
        if stock is not None and stock < 0:
            result["blockers"].append(f"STOCK: ujemny ({stock})")
        elif stock == 0:
            result["warnings"].append("STOCK: 0 (oferta wystawi się ale nie do zakupu)")

    # ---- 9. OLX text_fields ----
    olx_name = tf.get(OLX_NAME_KEY)
    olx_desc = tf.get(OLX_DESC_KEY)
    result["olx_name_present"] = bool(olx_name and str(olx_name).strip())
    result["olx_desc_present"] = bool(olx_desc and str(olx_desc).strip())
    olx_missing = []
    if not result["olx_name_present"]:
        olx_missing.append(OLX_NAME_KEY)
    if not result["olx_desc_present"]:
        olx_missing.append(OLX_DESC_KEY)
    if olx_missing:
        result["infos"].append(
            "OLX-fields: brak " + ", ".join(olx_missing) + " (BL użyje domyślnych name/description)"
        )

    return result


def _fmt_cell(v, ok_val="OK") -> str:
    if isinstance(v, bool):
        return "OK" if v else "FAIL"
    return str(v)


def build_report(rows: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"# Audyt {INVENTORY_ID} OLX Testy — {date.today().isoformat()}")
    lines.append("")

    n = len(rows)
    ready = [r for r in rows if not r.get("blockers")]
    with_warn = [r for r in ready if r.get("warnings")]
    with_block = [r for r in rows if r.get("blockers")]
    pure_ready = len(ready) - len(with_warn)

    # gotowość weighted: pass=1.0, warn=0.7, blocker=0.0
    weight = pure_ready * 1.0 + len(with_warn) * 0.7
    readiness = int(round(100 * weight / n)) if n else 0

    lines.append("## Podsumowanie globalne")
    lines.append(f"- {pure_ready}/{n} SKU gotowe (pass all critical, no warns)")
    lines.append(f"- {len(with_warn)}/{n} gotowe z warningami")
    lines.append(f"- {len(with_block)}/{n} z blockerami")
    lines.append(f"- Gotowość: **{readiness}%** (weighted avg: pass=1.0, warn=0.7, blocker=0.0)")
    lines.append("")

    # Tabela
    lines.append("## Per-SKU tabela")
    lines.append(
        "| SKU | PID | Title | Desc | Price | EAN | Waga | Wym(WxLxH) | Img | Kat | Stock | OLX-fields | Status |"
    )
    lines.append(
        "|-----|-----|-------|------|-------|-----|------|------------|-----|-----|-------|------------|--------|"
    )
    for r in rows:
        sku = r["sku"]
        pid = r.get("pid", "-")

        # title
        tl = r.get("title_len", 0)
        title_cell = f"{tl}/70"
        if any(b.startswith("TITLE") for b in r.get("blockers", [])):
            title_cell += " ✗"
        else:
            title_cell += " ✓"

        # desc
        dl = r.get("desc_plain_len", 0)
        desc_cell = f"{dl}"
        if any(b.startswith("DESC") for b in r.get("blockers", [])):
            desc_cell += " ✗"
        else:
            desc_cell += " ✓"

        # price
        pr = r.get("price")
        price_cell = f"{pr}" if pr is not None else "?"
        if any(b.startswith("PRICE") for b in r.get("blockers", [])):
            price_cell += " ✗"
        elif any(w.startswith("PRICE") for w in r.get("warnings", [])):
            price_cell += " ⚠"
        else:
            price_cell += " ✓"

        # ean
        ean_cell = "✓" if not any(b.startswith("EAN") for b in r.get("blockers", [])) else "✗"

        # weight
        w = r.get("weight", 0)
        weight_cell = f"{w}"

        # dims
        dim_cell = f"{r.get('width', 0)}x{r.get('length', 0)}x{r.get('height', 0)}"
        if any(b.startswith("WYMIARY") for b in r.get("blockers", [])):
            dim_cell += " ✗"
        elif any(w.startswith("WYMIARY") for w in r.get("warnings", [])):
            dim_cell += " ⚠"
        else:
            dim_cell += " ✓"

        # img
        ic = r.get("img_count", 0)
        img_cell = f"{ic}"
        if any(b.startswith("OBRAZKI") for b in r.get("blockers", [])):
            img_cell += " ✗"
        elif any(w.startswith("OBRAZKI") for w in r.get("warnings", [])):
            img_cell += " ⚠"
        else:
            img_cell += " ✓"

        # kat
        cid = r.get("category_id", 0)
        kat_cell = f"{cid}"
        if any(b.startswith("KATEGORIA") for b in r.get("blockers", [])):
            kat_cell += " ✗"
        else:
            kat_cell += " ✓"

        # stock
        st = r.get("stock")
        stock_cell = f"{st}" if st is not None else "?"
        if any(b.startswith("STOCK") for b in r.get("blockers", [])):
            stock_cell += " ✗"
        elif any(w.startswith("STOCK") for w in r.get("warnings", [])):
            stock_cell += " ⚠"
        else:
            stock_cell += " ✓"

        # olx-fields
        both = r.get("olx_name_present") and r.get("olx_desc_present")
        if both:
            olx_cell = "✓"
        elif r.get("olx_name_present") or r.get("olx_desc_present"):
            olx_cell = "½ ℹ"
        else:
            olx_cell = "— ℹ"

        # status
        if r.get("blockers"):
            status = f"BLOCKER ({len(r['blockers'])})"
        elif r.get("warnings"):
            status = f"READY-warn ({len(r['warnings'])})"
        else:
            status = "READY"

        lines.append(
            f"| {sku} | {pid} | {title_cell} | {desc_cell} | {price_cell} | {ean_cell} | "
            f"{weight_cell} | {dim_cell} | {img_cell} | {kat_cell} | {stock_cell} | {olx_cell} | {status} |"
        )
    lines.append("")

    # Szczegóły per SKU (tylko z warn/fail)
    detailed = [r for r in rows if r.get("blockers") or r.get("warnings") or r.get("infos")]
    if detailed:
        lines.append("## Per-SKU szczegóły (WARN/FAIL/INFO)")
        for r in detailed:
            sku = r["sku"]
            if r.get("blockers"):
                head = f"### SKU {sku} — BLOCKER ({len(r['blockers'])} fail, {len(r.get('warnings', []))} warn)"
            elif r.get("warnings"):
                head = f"### SKU {sku} — READY-warn ({len(r['warnings'])} warn)"
            else:
                head = f"### SKU {sku} — READY (info-only)"
            lines.append(head)
            for b in r.get("blockers", []):
                lines.append(f"- **FAIL:** {b}")
            for w in r.get("warnings", []):
                lines.append(f"- **WARN:** {w}")
            for i in r.get("infos", []):
                lines.append(f"- INFO: {i}")
            lines.append("")

    # Wnioski
    lines.append("## Wnioski i rekomendacje")
    all_blockers: dict[str, list[str]] = {}
    for r in rows:
        for b in r.get("blockers", []):
            key = b.split(":", 1)[0]
            all_blockers.setdefault(key, []).append(r["sku"])
    all_warnings: dict[str, list[str]] = {}
    for r in rows:
        for w in r.get("warnings", []):
            key = w.split(":", 1)[0]
            all_warnings.setdefault(key, []).append(r["sku"])

    if all_blockers:
        lines.append("### Blockers (napraw PRZED konfiguracją BL panel):")
        for kind, skus in sorted(all_blockers.items()):
            lines.append(f"- **{kind}**: SKU {', '.join(skus)} ({len(skus)}/{n})")
    else:
        lines.append("### Blockers: brak")
    lines.append("")

    if all_warnings:
        lines.append("### Warnings (można żyć, ale doprecyzuj później):")
        for kind, skus in sorted(all_warnings.items()):
            lines.append(f"- **{kind}**: SKU {', '.join(skus)} ({len(skus)}/{n})")
    else:
        lines.append("### Warnings: brak")
    lines.append("")

    # Wszystko gotowe?
    if not with_block:
        if with_warn:
            lines.append(
                f"### Werdykt: **TAK** — wszystkie {n} SKU pass critical checks. "
                f"{len(with_warn)} ma warnings do przemyślenia, ale można konfigurować BL panel."
            )
        else:
            lines.append(f"### Werdykt: **TAK** — wszystkie {n} SKU idealne. Do konfiguracji BL panel.")
    else:
        lines.append(
            f"### Werdykt: **NIE** — {len(with_block)}/{n} SKU ma blockery. "
            f"Napraw ({', '.join(sorted(all_blockers.keys()))}) przed konfiguracją BL panel."
        )
    return "\n".join(lines)


def main() -> int:
    if not TOKEN:
        print("BŁĄD: BASELINKER_TOKEN nie ustawiony w .env", file=sys.stderr)
        return 2

    print(f"[1/4] Pobieram listę produktów z inventory {INVENTORY_ID}…", file=sys.stderr)
    sku_map = _list_products(TOKEN, INVENTORY_ID)
    missing = [s for s in SKUS if s not in sku_map]
    if missing:
        print(f"UWAGA: brak w inventory: {missing}", file=sys.stderr)

    print("[2/4] Pobieram kategorie inventory…", file=sys.stderr)
    categories = load_categories(INVENTORY_ID)
    print(f"    znaleziono {len(categories)} kategorii", file=sys.stderr)

    print(f"[3/4] Audytuję {len(SKUS)} SKU (1 HTTP HEAD per SKU)…", file=sys.stderr)
    rows: list[dict] = []
    for i, sku in enumerate(SKUS, 1):
        if sku not in sku_map:
            rows.append({
                "sku": sku, "pid": "-",
                "blockers": [f"SKU nie istnieje w inventory {INVENTORY_ID}"],
                "warnings": [], "infos": [],
            })
            print(f"  [{i}/{len(SKUS)}] {sku} — brak w inventory", file=sys.stderr)
            continue
        pid, _qty = sku_map[sku]
        r = audit_sku(sku, pid, categories)
        rows.append(r)
        status = (
            f"BLOCK({len(r['blockers'])})" if r.get("blockers")
            else f"warn({len(r['warnings'])})" if r.get("warnings")
            else "OK"
        )
        print(f"  [{i}/{len(SKUS)}] {sku} PID {pid} — {status}", file=sys.stderr)

    print("[4/4] Generuję raport…", file=sys.stderr)
    report = build_report(rows)
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"audit_olx_readiness_{date.today().isoformat()}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"Raport zapisany: {out_file}", file=sys.stderr)
    print()
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
