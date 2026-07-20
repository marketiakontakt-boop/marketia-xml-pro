"""Uzupełnij `weight`/`width`/`length`/`height` dla 15 rodziców klonów w
inventory 111048 (OLX Testy).

Odkryte podczas probingu: BL już trzyma wymiary w `text_fields.features` w
formacie {'Waga': '3.5', 'Wymiary': '113 x 30 x 13 cm'} — MultiStore je tam
zapisuje przez integrację hurtowni. Fast path: fetch z BL, parse features
i zapisz do dedykowanych pól. Fallback (na wypadek gdyby BL nie miał danych):
scrape https://www.multistore.pl/?szukaj={EAN} → follow do produktu → parse
sekcję "SPECYFIKACJA" w opisie.

Format `Wymiary` w BL features:
  - "113 x 30 x 13 cm"       → width x length x height
  - "305 x 183 cm"           → średnica x wysokość (basen) → h=1, jako fallback dla wymogu OLX 3D
  - "180 x 74 cm"            → 2D
  - "30.5 x 27 x 7.5 cm"     → float allowed

BL `updateInventoryProduct` (właściwie `addInventoryProduct` z `product_id`) —
`weight` w kg, `width`/`length`/`height` w cm.

STOP condition: jeśli >5 SKU fail w rzędzie z powodu remote (HTTP/blokada),
przerwij (hurtownia mogła zablokować bot).
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.sync.baselinker_sync import _call, _list_products  # noqa: E402

TOKEN = os.getenv("BASELINKER_TOKEN", "").strip()
TARGET_INV_ID = 111048
SOURCE_INV_ID = 52173

TARGET_SKUS = [
    "10", "100", "1004", "1005", "1162", "1168", "1192", "12",
    "1201", "1202", "1204", "1259", "1261", "1463", "1600",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Dimension patterns — z BL features["Wymiary"] lub z HTML MultiStore.
# Akceptuj kropkę LUB przecinek jako separator dziesiętny.
_NUM = r"(\d+(?:[.,]\d+)?)"
_DIM_3D_RE = re.compile(rf"{_NUM}\s*[x×]\s*{_NUM}\s*[x×]\s*{_NUM}", re.IGNORECASE)
_DIM_2D_RE = re.compile(rf"{_NUM}\s*[x×]\s*{_NUM}", re.IGNORECASE)
_WEIGHT_KG_RE = re.compile(rf"{_NUM}\s*kg", re.IGNORECASE)


def _to_float(v: str) -> float:
    return float(v.replace(",", "."))


def _parse_dimensions(txt: str) -> tuple[float, float, float] | None:
    """Zwróć (width, length, height) cm z tekstu typu '113 x 30 x 13 cm' albo
    '305 x 183 cm' (2D → height=1 jako placeholder wymaganego pola OLX).
    """
    if not txt:
        return None
    m = _DIM_3D_RE.search(txt)
    if m:
        return (_to_float(m.group(1)), _to_float(m.group(2)), _to_float(m.group(3)))
    m = _DIM_2D_RE.search(txt)
    if m:
        # 2D → height placeholder=1 (OLX wymaga 3 wymiarów; user może dopolerować później)
        return (_to_float(m.group(1)), _to_float(m.group(2)), 1.0)
    return None


def _parse_weight(txt: str) -> float | None:
    """Zwróć wagę w kg z tekstu ('2.4', '2,4 kg', '2.4 kg'). Same liczby też."""
    if not txt:
        return None
    txt = str(txt).strip()
    m = _WEIGHT_KG_RE.search(txt)
    if m:
        return _to_float(m.group(1))
    # sam liczbowy string
    try:
        return _to_float(txt)
    except ValueError:
        return None


def _fetch_from_bl(inv_id: int, pid: str) -> dict:
    resp = _call(TOKEN, "getInventoryProductsData", {
        "inventory_id": inv_id,
        "products": [str(pid)],
    })
    return (resp.get("products") or {}).get(str(pid)) or {}


def _extract_from_bl_features(prod: dict) -> tuple[float | None, tuple[float, float, float] | None]:
    """Fast path: BL text_fields.features ma { 'Waga': '3.5', 'Wymiary': '...' } bo
    integracja MultiStore hurtownia je tam zapisuje.
    """
    tf = prod.get("text_fields") or {}
    feats = tf.get("features") or {}
    if not isinstance(feats, dict):
        return None, None
    weight = _parse_weight(feats.get("Waga") or feats.get("waga") or "")
    dims = _parse_dimensions(feats.get("Wymiary") or feats.get("wymiary") or "")
    return weight, dims


def _scrape_multistore_by_ean(client: httpx.Client, ean: str) -> tuple[float | None, tuple[float, float, float] | None]:
    """Fallback: szukaj produktu na hurtowniamultistore.pl (przekierowanie z multistore.pl)
    po EAN → follow pierwszy product link → parse sekcję SPECYFIKACJA.

    Zwraca (weight_kg, (w,l,h)_cm) albo (None, None) jeśli nie znaleziono.
    """
    if not ean:
        return None, None
    try:
        r = client.get(f"https://www.multistore.pl/?szukaj={ean}")
    except httpx.HTTPError:
        return None, None
    if r.status_code >= 400:
        return None, None
    # Szukamy linku do konkretnego produktu (nie carousel/related)
    # Format: <a href="slug,idNNN.html"> lub w atrybucie itemprop
    links = re.findall(r'href=[\"\'](?:https?://[^\"\']*)?(/[a-z0-9-]+,id\d+\.html)[\"\']', r.text)
    if not links:
        return None, None
    # Filtrujemy powtórki (na wielu stronach ten sam link jest w headerze i main)
    seen: set[str] = set()
    unique_links = [l for l in links if not (l in seen or seen.add(l))]
    # Bierzemy pierwszy — search rank multistore.pl jest deterministyczny.
    prod_url = "https://www.multistore.pl" + unique_links[0]
    try:
        pr = client.get(prod_url)
    except httpx.HTTPError:
        return None, None
    if pr.status_code >= 400:
        return None, None
    # Sekcja SPECYFIKACJA HTML zawiera:
    #   <li><b>Waga:</b> 2.4 kg</li>
    #   <li><b>Wymiar opakowania</b>: 30,5x27x7,5 cm</li>
    html = pr.text
    # Uproszczenie: szukamy kluczowych słów.
    weight = None
    wmatch = re.search(r"Waga\s*:?</?[^>]*>\s*([0-9]+[.,]?[0-9]*)\s*kg", html, re.IGNORECASE)
    if wmatch:
        weight = _to_float(wmatch.group(1))
    dims = None
    for pat in (
        r"Wymiar[^:<]*[:<][^<]*?" + _DIM_3D_RE.pattern,
        r"Wymiary[^:<]*[:<][^<]*?" + _DIM_3D_RE.pattern,
        _DIM_3D_RE.pattern,  # last resort — pierwsze 3D w opisie
    ):
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            # ostatnie 3 groups z pełnego matcha
            groups = m.groups()
            dims = (_to_float(groups[-3]), _to_float(groups[-2]), _to_float(groups[-1]))
            break
    return weight, dims


def main() -> int:
    if not TOKEN:
        print("BŁĄD: brak BASELINKER_TOKEN w .env")
        return 1

    print(f"[1] Pobieram SKU→PID z target {TARGET_INV_ID}…")
    tgt_info = _list_products(TOKEN, TARGET_INV_ID)
    missing_tgt = [s for s in TARGET_SKUS if s not in tgt_info]
    if missing_tgt:
        print(f"    ⚠️  brakuje w target: {missing_tgt}")

    print(f"[1] Pobieram SKU→PID z source {SOURCE_INV_ID} (dla EAN + fast path features)…")
    src_info = _list_products(TOKEN, SOURCE_INV_ID)

    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=20.0,
    )
    consecutive_scrape_fails = 0
    STOP_AFTER_CONSECUTIVE_FAILS = 5

    ok_cnt = 0
    fail_cnt = 0
    skip_cnt = 0

    print(f"\n[2] Update wymiarów dla {len(TARGET_SKUS)} SKU…")
    for sku in TARGET_SKUS:
        tgt = tgt_info.get(sku)
        if not tgt:
            print(f"  ✗ SKU {sku}: brak w target")
            fail_cnt += 1
            continue
        tgt_pid = tgt[0]

        # Fetch source (features + EAN) — używamy source bo target po klonowaniu może być pusty
        src = src_info.get(sku)
        weight: float | None = None
        dims: tuple[float, float, float] | None = None
        ean = ""
        if src:
            try:
                src_prod = _fetch_from_bl(SOURCE_INV_ID, src[0])
                ean = str(src_prod.get("ean") or "")
                # Fast path — features
                w1, d1 = _extract_from_bl_features(src_prod)
                # `weight` na poziomie root też trzymamy jako priorytet (już liczba)
                try:
                    root_weight = float(src_prod.get("weight") or 0)
                except (TypeError, ValueError):
                    root_weight = 0.0
                weight = root_weight if root_weight > 0 else w1
                dims = d1
            except Exception as e:
                print(f"  ⚠  SKU {sku}: source fetch fail: {e}")

        source_used = "bl-features"
        # Fallback do scrape jeśli fast path nie dał wag albo wymiarów
        if (weight is None or weight == 0 or dims is None) and ean:
            try:
                w2, d2 = _scrape_multistore_by_ean(client, ean)
                if weight in (None, 0) and w2:
                    weight = w2
                if dims is None and d2:
                    dims = d2
                source_used = "multistore-scrape"
                consecutive_scrape_fails = 0
            except Exception as e:
                consecutive_scrape_fails += 1
                print(f"  ⚠  SKU {sku}: scrape fail: {e}")
                if consecutive_scrape_fails >= STOP_AFTER_CONSECUTIVE_FAILS:
                    print(f"  ⛔ STOP: {STOP_AFTER_CONSECUTIVE_FAILS}× scrape fail w rzędzie — hurtownia może blokować.")
                    break
            time.sleep(0.3)  # rate limit (nieinwazyjny)

        if dims is None and (weight in (None, 0)):
            print(f"  = SKU {sku}: nie znaleziono danych — skip")
            skip_cnt += 1
            continue

        w = weight or 0
        wi, le, he = dims if dims else (0.0, 0.0, 0.0)

        # OLX wymaga cm jako int; BL akceptuje float — konwersja do int (zaokrąglenie w górę)
        # gdy wymiar < 1 cm, dolewamy do 1.
        def _cm(v: float) -> int:
            iv = int(round(v))
            return max(iv, 1) if v > 0 else 0

        payload = {
            "inventory_id": TARGET_INV_ID,
            "product_id": tgt_pid,
            "weight": round(w, 2),
            "width": _cm(wi),
            "length": _cm(le),
            "height": _cm(he),
        }
        try:
            # BL nie ma `updateInventoryProduct` — upsert przez addInventoryProduct
            _call(TOKEN, "addInventoryProduct", payload)
            print(
                f"  ✓ SKU {sku}: {payload['weight']}kg "
                f"{payload['width']}x{payload['length']}x{payload['height']}  ({source_used})"
            )
            ok_cnt += 1
        except Exception as e:
            print(f"  ✗ SKU {sku}: update fail: {e}")
            fail_cnt += 1

    print("\n=== RAPORT ===")
    print(f"Ok: {ok_cnt}, skipped: {skip_cnt}, failed: {fail_cnt} (z {len(TARGET_SKUS)})")
    return 0 if fail_cnt == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
