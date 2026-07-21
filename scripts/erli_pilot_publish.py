"""ERLI pilot publish — pierwsze 20 SKU Gardenstein/Intex z Kathay + MultiStore.

Flow:
  1. Sanity check: GET /me (weryfikacja klucza)
  2. Fetch produktów z BL (getInventoryProductsList + getInventoryProductsData)
  3. Filter: manufacturer_id ∈ {Gardenstein, Intex, KATHAY-INTEX, GARDENSTEIN}
  4. Sample: 10 z Kathay + 10 z MultiStore
  5. Map BL → Erli ProductCreate JSON
  6. POST /products/{sku} per SKU + collect wyniki
  7. Save do output/erli_pilot_<ts>.json + print podsumowanie

Rate limit: 500ms sleep między POST-ami (safe default, Erli może zwrócić 429).

Env vars:
  BASELINKER_TOKEN
  ERLI_API_KEY

Usage:
  venv/bin/python scripts/erli_pilot_publish.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv()

BL_TOKEN = os.getenv("BASELINKER_TOKEN")
ERLI_KEY = os.getenv("ERLI_API_KEY")
ERLI_BASE = "https://erli.pl/svc/shop-api"

INVENTORIES = [(45513, "Kathay"), (52173, "Hurtownia MultiStore")]
# manufacturer_ids z recon: 1693337=Intex, 1694696=KATHAY-INTEX, 4487436=GARDENSTEIN
TARGET_MANUFACTURERS = {1693337, 1694696, 4487436}


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# --- BaseLinker helpers ---------------------------------------------------

def bl(method: str, params: dict | None = None, max_retries: int = 4) -> dict:
    """BL call z retry+backoff dla CONNECTION errorow (rate limit / transient)."""
    last = {}
    for attempt in range(max_retries):
        r = httpx.post(
            "https://api.baselinker.com/connector.php",
            headers={"X-BLToken": BL_TOKEN},
            data={"method": method, "parameters": json.dumps(params or {})},
            timeout=60.0,
        )
        last = r.json()
        if last.get("status") != "ERROR" or last.get("error_code") != "CONNECTION":
            return last
        wait = 15 * (attempt + 1)  # 15, 30, 45, 60
        log(f"    BL CONNECTION error, retry in {wait}s (attempt {attempt+1}/{max_retries})...")
        time.sleep(wait)
    return last


def fetch_all_bl_products(inv_id: int) -> list[dict]:
    """Zwraca WSZYSTKIE produkty z inv (bez filter manufacturer). Paginate po 1000."""
    log(f"  Fetching ALL products from inv {inv_id}...")
    all_ids: list[str] = []
    page = 1
    while True:
        r = bl("getInventoryProductsList", {"inventory_id": inv_id, "page": page})
        products = r.get("products") or {}
        if not products:
            break
        all_ids.extend(products.keys())
        if len(products) < 1000:
            break
        page += 1
        if page > 30:
            break
    log(f"    → {len(all_ids)} total product IDs, fetching data in batches of 100...")
    matched = []
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i : i + 100]
        r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": chunk})
        for pid, pdata in (r.get("products") or {}).items():
            pdata["_bl_id"] = pid
            pdata["_bl_inventory"] = inv_id
            matched.append(pdata)
    log(f"  → {len(matched)} products fetched")
    return matched


def fetch_bl_products_for_manufacturers(inv_id: int) -> list[dict]:
    """Zwraca produkty z inv gdzie manufacturer_id ∈ TARGET_MANUFACTURERS.

    Iteruje po każdym manufacturer_id osobno (mniejsze query = brak OOM po stronie BL).
    """
    log(f"  Fetching products per manufacturer from inv {inv_id}...")
    all_ids: list[str] = []
    for mid in TARGET_MANUFACTURERS:
        page = 1
        mfr_count = 0
        while True:
            r = bl(
                "getInventoryProductsList",
                {"inventory_id": inv_id, "filter_manufacturer_id": mid, "page": page},
            )
            products = r.get("products") or {}
            if not products:
                break
            all_ids.extend(products.keys())
            mfr_count += len(products)
            if len(products) < 1000:
                break
            page += 1
            if page > 20:
                break
        log(f"    manufacturer {mid}: {mfr_count} products")

    if not all_ids:
        return []

    matched = []
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i : i + 100]
        r = bl(
            "getInventoryProductsData",
            {"inventory_id": inv_id, "products": chunk},
        )
        for pid, pdata in (r.get("products") or {}).items():
            pdata["_bl_id"] = pid
            pdata["_bl_inventory"] = inv_id
            matched.append(pdata)
    log(f"  → {len(matched)} matched")
    return matched


# --- Erli helpers ----------------------------------------------------------

def erli(method: str, path: str, json_body: dict | None = None) -> tuple[int, dict]:
    """Zwraca (status, response). NIE raise on error — caller decyduje."""
    headers = {
        "Authorization": f"Bearer {ERLI_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    r = httpx.request(
        method, f"{ERLI_BASE}{path}", headers=headers, json=json_body, timeout=30.0
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:500]}


# --- Mapping BL → Erli ProductCreate --------------------------------------

def bl_to_erli(bl_prod: dict) -> dict | None:
    """Zamienia BL product dict → Erli ProductCreate JSON. Zwraca None gdy braki."""
    tf = bl_prod.get("text_fields") or {}
    name = tf.get("name") or bl_prod.get("name") or ""
    desc = tf.get("description") or ""
    sku = bl_prod.get("sku") or bl_prod.get("_bl_id")
    ean = bl_prod.get("ean") or ""
    # BL cena: prices dict {price_group_id: value} — bierz pierwsze
    prices = bl_prod.get("prices") or {}
    price_pln = 0.0
    if prices:
        price_pln = float(list(prices.values())[0] or 0)
    if not price_pln:
        price_pln = float(bl_prod.get("price") or 0)
    price_grosze = int(round(price_pln * 100))

    # Stock — sumuj wszystkie warehouses
    stock_map = bl_prod.get("stock") or {}
    stock = sum(int(v or 0) for v in stock_map.values()) if isinstance(stock_map, dict) else int(stock_map or 0)

    # Images — BL zwraca dict {index: url} albo list
    imgs = bl_prod.get("images") or {}
    if isinstance(imgs, dict):
        image_urls = list(imgs.values())
    else:
        image_urls = list(imgs)
    image_urls = [u for u in image_urls if u and isinstance(u, str) and u.startswith("http")]
    # Dedup URL z zachowaniem kolejności (Erli odrzuca duplikaty)
    image_urls = list(dict.fromkeys(image_urls))

    # Weight (g)
    weight_g = None
    features = bl_prod.get("features") or {}
    for k in ("weight", "waga", "waga_g"):
        if k in features:
            try:
                weight_g = float(features[k])
                break
            except (ValueError, TypeError):
                pass

    # Walidacja minimalna
    if not name or not image_urls or price_grosze <= 0:
        return None

    payload = {
        "name": name[:200],
        "images": [{"url": u} for u in image_urls[:20]],
        "price": price_grosze,
        "stock": max(stock, 0),
        "dispatchTime": {"period": 3, "unit": "day"},  # 3 dni robocze default
    }
    if desc and len(desc) > 20:
        # Erli description = anyOf(object|string). Plain string jest wspierany.
        payload["description"] = desc[:80000]
    if ean:
        payload["ean"] = str(ean)
    if sku:
        payload["sku"] = str(sku)
    if weight_g:
        payload["weight"] = weight_g

    return payload


# --- Main ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="nie POST-uj, tylko dump mappings")
    parser.add_argument("--limit", type=int, default=20, help="ile SKU (default 20)")
    parser.add_argument("--per-inv", type=int, default=10, help="max per inventory")
    parser.add_argument("--full", action="store_true", help="wystaw wszystkie (nadpisuje --limit/--per-inv)")
    parser.add_argument("--inventory-id", type=int, help="override: single inventory ID, fetch WSZYSTKIE (bez filter manufacturer)")
    args = parser.parse_args()
    if args.full:
        args.limit = 100_000
        args.per_inv = 100_000

    if not BL_TOKEN:
        log("ERROR: brak BASELINKER_TOKEN w env")
        return 2
    if not ERLI_KEY and not args.dry_run:
        log("ERROR: brak ERLI_API_KEY w env (użyj --dry-run dla testu mappingu)")
        return 2

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent.parent / "output"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"erli_pilot_{ts}.json"

    # 1. Sanity check GET /me
    if not args.dry_run:
        log("=== SANITY CHECK: GET /me ===")
        status, resp = erli("GET", "/me")
        log(f"  status={status}, response={json.dumps(resp)[:200]}")
        if status != 200:
            log("❌ Auth failed — sprawdź ERLI_API_KEY")
            return 3
        log(f"✅ Zalogowany jako sklep: {resp.get('name') or resp.get('shopName') or resp}")

    # 2. Fetch BL products
    log("=== FETCH BL PRODUCTS ===")
    picked = []
    seen_sku: set[str] = set()

    # Override: --inventory-id → single inv, wszystkie produkty (bez filter mfr)
    if args.inventory_id:
        target_invs = [(args.inventory_id, f"inv-{args.inventory_id}")]
        fetch_fn = fetch_all_bl_products
    else:
        target_invs = INVENTORIES
        fetch_fn = fetch_bl_products_for_manufacturers

    for inv_id, inv_name in target_invs:
        log(f"Inventory: {inv_name} ({inv_id})")
        prods = fetch_fn(inv_id)
        # Deduplikacja po (inv, bl_id) — ten sam BL id może wyskoczyć w wielu manufacturer queries
        dedup: dict[str, dict] = {}
        for p in prods:
            key = f"{inv_id}:{p['_bl_id']}"
            if key not in dedup:
                dedup[key] = p
        prods = list(dedup.values())
        prods.sort(key=lambda p: p.get("sku") or "")
        # Deduplikacja po SKU globalnie — Erli externalId musi być unique
        added = 0
        for p in prods:
            sku = p.get("sku") or p.get("_bl_id")
            if sku in seen_sku:
                continue
            seen_sku.add(sku)
            picked.append(p)
            added += 1
            if added >= args.per_inv:
                break
        log(f"  → added {added} unique SKU from {inv_name}")

    picked = picked[: args.limit]
    log(f"=== SELECTED {len(picked)} SKU FOR PILOT ===")
    for p in picked:
        log(f"  {p.get('sku')}: {(p.get('text_fields',{}).get('name') or '')[:70]}")

    # 3. Map + POST
    log("=== MAP + POST ===")
    results = []
    for i, p in enumerate(picked, 1):
        sku = p.get("sku") or p.get("_bl_id")
        mapped = bl_to_erli(p)
        entry = {
            "sku": sku,
            "bl_id": p.get("_bl_id"),
            "inventory": p.get("_bl_inventory"),
            "name": (p.get("text_fields", {}).get("name") or "")[:100],
            "mapped": mapped is not None,
            "erli_status": None,
            "erli_response": None,
        }
        if mapped is None:
            entry["skip_reason"] = "missing name/images/price"
            log(f"  [{i}/{len(picked)}] {sku}: SKIP — {entry['skip_reason']}")
        elif args.dry_run:
            entry["payload"] = mapped
            log(f"  [{i}/{len(picked)}] {sku}: DRY-RUN — name={mapped['name'][:50]!r}, price={mapped['price']}gr, imgs={len(mapped['images'])}")
        else:
            sku_enc = quote(str(sku), safe="")
            log(f"  [{i}/{len(picked)}] {sku}: POST /products/{sku_enc}...")
            status, resp = erli("POST", f"/products/{sku_enc}", mapped)
            entry["erli_status"] = status
            entry["erli_response"] = resp
            if status in (200, 201, 202):
                log(f"    ✅ OK ({status})")
                time.sleep(0.5)
            elif status == 429:
                log(f"    ⏳ RATE LIMIT (429), sleep 5s + retry once")
                time.sleep(5.0)
                status, resp = erli("POST", f"/products/{sku_enc}", mapped)
                entry["erli_status"] = status
                entry["erli_response"] = resp
                if status in (200, 201, 202):
                    log(f"    ✅ OK ({status}) po retry")
                else:
                    log(f"    ❌ FAIL po retry ({status}): {json.dumps(resp)[:150]}")
                time.sleep(1.0)
            else:
                log(f"    ❌ FAIL ({status}): {json.dumps(resp)[:200]}")
                time.sleep(0.5)
        results.append(entry)

    # 4. Save + summary
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(
            {"timestamp": ts, "dry_run": args.dry_run, "results": results},
            f, ensure_ascii=False, indent=2,
        )
    log(f"=== SAVED: {out_file} ===")

    ok = sum(1 for r in results if r.get("erli_status") in (200, 201))
    fail = sum(1 for r in results if r.get("erli_status") and r["erli_status"] not in (200, 201))
    skip = sum(1 for r in results if not r["mapped"])
    dry = sum(1 for r in results if args.dry_run and r["mapped"])
    log(f"PODSUMOWANIE: OK={ok}  FAIL={fail}  SKIP={skip}  DRY={dry}  TOTAL={len(results)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
