"""Bulk ustaw deliveryPriceList dla wszystkich SKU wystawionych na Erli.

Domyślnie: 'Erli Kurier free' (id 1636005). User instrukcja "jak niepewny → kurier".

Fetch SKU z BL (default: 3 sync inventories), PATCH /products/batch-update chunks 100.

Usage:
    venv/bin/python scripts/erli_set_delivery_bulk.py [--pricelist-id ID] [--inventory-id N]

Env: BASELINKER_TOKEN, ERLI_API_KEY, BL_ERLI_SYNC_INVENTORIES
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
DEFAULT_PRICELIST = "1636005"  # Erli Kurier free
DEFAULT_INVENTORIES = [
    int(x) for x in (os.getenv("BL_ERLI_SYNC_INVENTORIES") or "45513,52173,111230").split(",")
]


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


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


def fetch_all_skus(inv_id: int) -> list[str]:
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

    skus = []
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": chunk})
        for pid, pd in (r.get("products") or {}).items():
            sku = pd.get("sku")
            if sku:
                skus.append(sku)
    return skus


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pricelist-id", default=DEFAULT_PRICELIST)
    parser.add_argument("--inventory-id", type=int, action="append", help="override inventories")
    args = parser.parse_args()

    if not BL_TOKEN or not ERLI_KEY:
        log("ERROR: brak BASELINKER_TOKEN / ERLI_API_KEY w env")
        return 2

    inventories = args.inventory_id or DEFAULT_INVENTORIES
    log(f"Inventories: {inventories}")
    log(f"Delivery price list ID: {args.pricelist_id}")

    # Fetch SKUs
    all_skus = []
    for inv_id in inventories:
        log(f"Fetching BL inv {inv_id}...")
        s = fetch_all_skus(inv_id)
        log(f"  → {len(s)} SKU")
        all_skus.extend(s)
    all_skus = list(dict.fromkeys(all_skus))
    log(f"Total unique SKU: {len(all_skus)}")

    # Bulk PATCH
    H = {"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"}
    ok = fail = 0
    for i in range(0, len(all_skus), 100):
        chunk = all_skus[i : i + 100]
        payload = [{"externalId": s, "deliveryPriceList": args.pricelist_id} for s in chunk]
        r = httpx.patch(
            "https://erli.pl/svc/shop-api/products/batch-update",
            headers=H, json=payload, timeout=60.0,
        )
        if r.status_code == 200:
            data = r.json()
            o = sum(1 for e in data if e.get("status") in (200, 202))
            f_ = len(data) - o
            ok += o
            fail += f_
            log(f"  batch [{i+1}-{i+len(chunk)}]: ok={o} fail={f_}")
        else:
            log(f"  batch [{i+1}-{i+len(chunk)}]: HTTP {r.status_code}: {r.text[:150]}")
            fail += len(chunk)
        time.sleep(0.5)

    log(f"SUMMARY: OK={ok}  FAIL={fail}  TOTAL={ok+fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
