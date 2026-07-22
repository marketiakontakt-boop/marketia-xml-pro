"""BL → Erli stock+price sync (cron script).

Pobiera aktualne stany + ceny z BL dla wystawionych na Erli SKU i pushuje
przez Erli PATCH /products/batch-update (chunks 100). Idempotentny.

Config w .env:
  BASELINKER_TOKEN
  ERLI_API_KEY
  BL_ERLI_SYNC_INVENTORIES=45513,52173,111230  (domyślnie: te 3)

Usage:
  venv/bin/python scripts/bl_to_erli_stock_sync.py [--dry-run] [--inventory-id N]

Cron (launchd/GH Actions) — uruchom co 30 min.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BL_TOKEN = os.getenv("BASELINKER_TOKEN")
ERLI_KEY = os.getenv("ERLI_API_KEY")
ERLI_BASE = "https://erli.pl/svc/shop-api"
DEFAULT_INVENTORIES = [
    int(x) for x in (os.getenv("BL_ERLI_SYNC_INVENTORIES") or "45513,52173,111230").split(",")
]


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def bl(method: str, params: dict | None = None, retries: int = 4) -> dict:
    for i in range(retries):
        r = httpx.post(
            "https://api.baselinker.com/connector.php",
            headers={"X-BLToken": BL_TOKEN},
            data={"method": method, "parameters": json.dumps(params or {})},
            timeout=60.0,
        )
        resp = r.json()
        if resp.get("status") != "ERROR" or resp.get("error_code") != "CONNECTION":
            return resp
        time.sleep(15 * (i + 1))
    return resp


def erli(method: str, path: str, json_body=None) -> tuple[int, dict]:
    r = httpx.request(
        method, f"{ERLI_BASE}{path}",
        headers={"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"},
        json=json_body, timeout=60.0,
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:500]}


def fetch_bl_inventory_stock_price(inv_id: int) -> dict[str, dict]:
    """Zwraca {sku: {stock, price_grosze, bl_id}} dla całego inv."""
    log(f"BL inv {inv_id}: fetching product list...")
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

    log(f"  → {len(all_ids)} products, fetching data in batches of 100...")
    sku_map: dict[str, dict] = {}
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i : i + 100]
        r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": chunk})
        for pid, pdata in (r.get("products") or {}).items():
            sku = pdata.get("sku")
            if not sku:
                continue
            # Sum stock po wszystkich warehouses
            stock_map = pdata.get("stock") or {}
            stock = sum(int(v or 0) for v in stock_map.values()) if isinstance(stock_map, dict) else int(stock_map or 0)
            # Cena: bierz z RETAIL price group (96668 = hurtownia × 1.35).
            # BL automatycznie oblicza tę grupę jako dependent_on_price_group.
            # Fallback: default group (30157) jeśli 96668 brak (nie powinno się zdarzyć).
            prices = pdata.get("prices") or {}
            price_pln = float(prices.get("96668") or prices.get("30157") or 0)
            price_grosze = int(round(price_pln * 100))
            sku_map[sku] = {"stock": max(stock, 0), "price": price_grosze, "bl_id": pid}
    log(f"  → mapped {len(sku_map)} unique SKU")
    return sku_map


def batch_patch_erli(items: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """PATCH /products/batch-update chunks po 100. Zwraca (ok, fail)."""
    total_ok = 0
    total_fail = 0
    for i in range(0, len(items), 100):
        chunk = items[i : i + 100]
        if dry_run:
            log(f"  DRY-RUN batch [{i+1}-{i+len(chunk)}]: {len(chunk)} items (skip PATCH)")
            total_ok += len(chunk)
            continue
        status, resp = erli("PATCH", "/products/batch-update", chunk)
        if status in (200, 202):
            log(f"  batch [{i+1}-{i+len(chunk)}]: ✅ HTTP {status}")
            total_ok += len(chunk)
        elif status == 429:
            log(f"  batch [{i+1}-{i+len(chunk)}]: ⏳ 429, sleep 10s + retry")
            time.sleep(10)
            status, resp = erli("PATCH", "/products/batch-update", chunk)
            if status in (200, 202):
                log(f"    ✅ retry OK ({status})")
                total_ok += len(chunk)
            else:
                log(f"    ❌ retry FAIL ({status}): {json.dumps(resp)[:200]}")
                total_fail += len(chunk)
        else:
            log(f"  batch [{i+1}-{i+len(chunk)}]: ❌ HTTP {status}: {json.dumps(resp)[:200]}")
            total_fail += len(chunk)
        time.sleep(0.5)
    return total_ok, total_fail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--inventory-id", type=int, action="append", help="override inventories (może być wiele razy)")
    args = parser.parse_args()

    if not BL_TOKEN or not ERLI_KEY:
        log("ERROR: brak BASELINKER_TOKEN / ERLI_API_KEY w env")
        return 2

    inventories = args.inventory_id or DEFAULT_INVENTORIES
    log(f"Sync inventories: {inventories}")

    # 1. Fetch BL stock+price
    log("=== FETCH BL ===")
    sku_map: dict[str, dict] = {}
    for inv_id in inventories:
        m = fetch_bl_inventory_stock_price(inv_id)
        # Konflikt SKU między inv → ostatni wygrywa (rare, log)
        for sku in m:
            if sku in sku_map:
                log(f"  ⚠️  duplicate SKU across inv: {sku} (last wins)")
        sku_map.update(m)

    log(f"Total unique SKU from BL: {len(sku_map)}")

    # 2. Prepare batch payload — stock + retail price (z grupy 96668, BL auto-liczy ×1.35)
    items = [
        {"externalId": sku, "stock": data["stock"], "price": data["price"]}
        for sku, data in sku_map.items()
        if data["price"] > 0  # skip SKU bez ceny
    ]
    log(f"Payload items (stock + retail price from group 96668): {len(items)}")

    # 3. Batch PATCH
    log("=== PATCH ERLI ===")
    ok, fail = batch_patch_erli(items, dry_run=args.dry_run)

    # 4. Save summary
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / "output" / f"stock_sync_{ts}.json"
    out.parent.mkdir(exist_ok=True)
    summary = {
        "timestamp": ts,
        "dry_run": args.dry_run,
        "inventories": inventories,
        "bl_sku_count": len(sku_map),
        "payload_items": len(items),
        "batch_ok": ok,
        "batch_fail": fail,
    }
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")
    log(f"SYNC SUMMARY: bl_sku={len(sku_map)}  payload={len(items)}  batch_ok={ok}  batch_fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
