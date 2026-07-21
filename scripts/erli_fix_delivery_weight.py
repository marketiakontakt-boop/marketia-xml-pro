"""Fix delivery: PATCH wszystkich SKU z weight (BL → grams) + deliveryPriceList=FREE.

Cel: usunąć `buyableProblems: ["delivery"]` dla ofert Erli.
Problem: mój bl_to_erli() szukał weight w features/{} zamiast top-level BL field.
Fix: pobierz `weight` z BL top-level (kg), * 1000 = gramach, wyślij do Erli.

Też przełącz na cennik FREE (478758) — ma więcej metod dostawy (paczkomat + kurier + DPD)
niż Kurier free (1636005 — tylko kurier).

Usage: venv/bin/python scripts/erli_fix_delivery_weight.py [--dry-run]
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
PRICELIST_ID = "478758"  # FREE — więcej metod dostawy niż Kurier free
INVENTORIES = [
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


def fetch_bl_weights(inv_id: int) -> dict[str, dict]:
    """Zwraca {sku: {weight_g, height, width, length}} dla inv."""
    log(f"BL inv {inv_id}: fetching product list...")
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

    log(f"  → {len(ids)} product IDs, fetching data chunks 100...")
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": chunk})
        for pid, pd in (r.get("products") or {}).items():
            sku = pd.get("sku")
            if not sku:
                continue
            try:
                weight_kg = float(pd.get("weight") or 0)
            except (ValueError, TypeError):
                weight_kg = 0.0
            weight_g = int(round(weight_kg * 1000)) if weight_kg > 0 else 0
            out[sku] = {
                "weight_g": weight_g,
                "height": float(pd.get("height") or 0),
                "width": float(pd.get("width") or 0),
                "length": float(pd.get("length") or 0),
            }
    log(f"  → mapped {len(out)} SKU")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-weight", type=int, default=1, help="Skip SKU z weight_g < N (default 1 = wszystkie z niezerowym)")
    args = parser.parse_args()

    if not BL_TOKEN or not ERLI_KEY:
        log("ERROR: brak env")
        return 2

    log(f"Sync inventories: {INVENTORIES}")
    log(f"Target pricelist: {PRICELIST_ID} (FREE)")

    sku_map: dict[str, dict] = {}
    for inv_id in INVENTORIES:
        m = fetch_bl_weights(inv_id)
        for sku in m:
            if sku not in sku_map:
                sku_map[sku] = m[sku]
    log(f"Total unique SKU: {len(sku_map)}")

    with_weight = sum(1 for v in sku_map.values() if v["weight_g"] >= args.min_weight)
    no_weight = len(sku_map) - with_weight
    log(f"  Z weight>{args.min_weight}g: {with_weight}, bez weight: {no_weight}")

    # Payload
    payload = []
    for sku, info in sku_map.items():
        item = {"externalId": sku, "deliveryPriceList": PRICELIST_ID}
        if info["weight_g"] >= args.min_weight:
            item["weight"] = info["weight_g"]
        payload.append(item)

    log(f"Payload items: {len(payload)}")
    if args.dry_run:
        log("DRY-RUN — sample first 3:")
        for it in payload[:3]:
            log(f"  {it}")
        return 0

    # Bulk PATCH
    H = {"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"}
    ok = fail = 0
    for i in range(0, len(payload), 100):
        chunk = payload[i : i + 100]
        r = httpx.patch(
            "https://erli.pl/svc/shop-api/products/batch-update",
            headers=H, json=chunk, timeout=60.0,
        )
        if r.status_code == 200:
            data = r.json()
            o = sum(1 for e in data if e.get("status") in (200, 202))
            f_ = len(data) - o
            ok += o
            fail += f_
            log(f"  batch [{i+1}-{i+len(chunk)}]: ok={o} fail={f_}")
        else:
            log(f"  batch [{i+1}-{i+len(chunk)}]: HTTP {r.status_code}: {r.text[:200]}")
            fail += len(chunk)
        time.sleep(0.5)

    log(f"SUMMARY: OK={ok} FAIL={fail} TOTAL={ok+fail} (bez weight: {no_weight})")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
