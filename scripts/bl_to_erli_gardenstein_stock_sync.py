"""BL → Erli GardenStein stock+price sync (cron).

Analog do bl_to_erli_stock_sync.py ale:
- ERLI_GARDENSTEIN_KEY (sklep GardenStein id 103151)
- Stock: TYLKO warehouse bl_58313 (Allegro Asortyment), nie sum
- Price: BL retail group 96668 (hurtownia × 1.35)
- Inv: MultiStore + Kathay
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
ERLI_KEY = os.getenv("ERLI_GARDENSTEIN_KEY")
INVENTORIES = [52173, 45513]  # MultiStore, Kathay
ALLEGRO_WH = "bl_58313"


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}", flush=True)


def bl(method: str, params: dict, retries: int = 4) -> dict:
    for i in range(retries):
        r = httpx.post(
            "https://api.baselinker.com/connector.php",
            headers={"X-BLToken": BL_TOKEN},
            data={"method": method, "parameters": json.dumps(params)},
            timeout=60.0,
        )
        d = r.json()
        if d.get("status") == "SUCCESS" or d.get("error_code") != "CONNECTION":
            return d
        time.sleep(15 * (i + 1))
    return d


def fetch_bl(inv_id: int) -> dict[str, dict]:
    ids = []
    page = 1
    while True:
        r = bl("getInventoryProductsList", {"inventory_id": inv_id, "page": page})
        p = r.get("products") or {}
        if not p:
            break
        ids.extend(p.keys())
        if len(p) < 1000:
            break
        page += 1
        if page > 30:
            break
    out = {}
    for i in range(0, len(ids), 100):
        r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": ids[i:i+100]})
        for pid, pd in (r.get("products") or {}).items():
            sku = pd.get("sku")
            if not sku:
                continue
            stock_map = pd.get("stock") or {}
            allegro = int(stock_map.get(ALLEGRO_WH) or 0)
            prices = pd.get("prices") or {}
            retail = float(prices.get("96668") or 0)
            if retail <= 0:
                retail = float(prices.get("30157") or 0) * 1.35
            out[sku] = {"stock": max(allegro, 0), "price": int(round(retail * 100))}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not BL_TOKEN or not ERLI_KEY:
        log("ERROR: brak env")
        return 2

    sku_map = {}
    for inv_id in INVENTORIES:
        log(f"BL inv {inv_id}...")
        m = fetch_bl(inv_id)
        for sku, data in m.items():
            if sku not in sku_map:
                sku_map[sku] = data

    log(f"Total: {len(sku_map)} SKU")

    # Fetch existing GardenStein SKUs — sync only these
    log("Fetch GardenStein existing SKUs...")
    H = {"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"}
    existing = set()
    after = None
    while True:
        body = {"pagination": {"limit": 200, "sortField": "externalId", "order": "ASC"}}
        if after:
            body["pagination"]["after"] = after
        r = httpx.post("https://erli.pl/svc/shop-api/products/_search", headers=H, json=body, timeout=30.0)
        if r.status_code != 200:
            break
        items = r.json()
        if not items:
            break
        for p in items:
            existing.add(p.get("externalId"))
        after = items[-1].get("externalId")
        if len(items) < 200:
            break
        time.sleep(0.3)
    log(f"  → {len(existing)} SKU w GardenStein Erli")

    payload = [
        {"externalId": sku, "stock": data["stock"], "price": data["price"]}
        for sku, data in sku_map.items()
        if sku in existing and data["price"] > 0
    ]
    log(f"Payload: {len(payload)}")

    if args.dry_run:
        log("DRY-RUN")
        return 0

    ok = fail = 0
    for i in range(0, len(payload), 100):
        chunk = payload[i:i+100]
        r = httpx.patch("https://erli.pl/svc/shop-api/products/batch-update",
                       headers=H, json=chunk, timeout=60.0)
        if r.status_code == 200:
            data = r.json()
            o = sum(1 for e in data if e.get("status") in (200, 202))
            ok += o
            fail += len(data) - o
        else:
            fail += len(chunk)
        time.sleep(0.5)
    log(f"SYNC SUMMARY: ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
