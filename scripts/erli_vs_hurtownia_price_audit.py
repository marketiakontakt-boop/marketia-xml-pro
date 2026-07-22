"""Audyt cen: Erli vs BL retail vs MultiStore hurtownia — znajdź zaniżone.

Fetch:
1. MultiStore external storage (getExternalStorageProductsPrices) — ceny hurtowni brutto
2. BL inventories (45513 Kathay, 52173 MultiStore, 111230 AgdSelect) — BL retail prices
3. Erli /products/_search — Erli current prices

Compare per SKU:
- ratio_erli_vs_hurtownia = erli_price / multistore_price (< 1.0 = strata!)
- ratio_erli_vs_bl = erli_price / bl_retail_price (< 1.0 = zaniżone niż BL zdecydowała)
- flags:
  - LOSS: erli_price <= hurtownia_price (sprzedaż poniżej kosztu)
  - UNDERPRICED_10: erli_price < 0.9 * bl_price (10%+ niżej niż BL retail)
  - UNDERPRICED_50: erli_price < 0.5 * bl_price (50%+ niżej, potencjalny bug)

Output: output/price_audit_<ts>.csv + summary do stdout.

Usage: venv/bin/python scripts/erli_vs_hurtownia_price_audit.py
"""
from __future__ import annotations

import csv
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
INVENTORIES = [
    (45513, "Kathay"),
    (52173, "MultiStore"),
    (111230, "AgdSelect"),
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


def fetch_multistore_prices() -> dict[str, float]:
    """Zwraca {sku: price_brutto} z hurtowni MultiStore."""
    log("MultiStore hurtownia: fetch products list...")
    all_prices: dict[str, float] = {}
    page = 1
    while True:
        r = bl("getExternalStorageProductsList", {"storage_id": "warehouse_5010250", "page": page})
        products = r.get("products") or []
        if not products:
            break
        for p in products:
            sku = p.get("sku")
            price = p.get("price_brutto")
            if sku and price is not None:
                all_prices[str(sku)] = float(price)
        if len(products) < 1000:
            break
        page += 1
        if page > 30:
            break
    log(f"  → {len(all_prices)} MultiStore prices")
    return all_prices


def fetch_bl_retail() -> dict[str, dict]:
    """Zwraca {sku: {retail, inv_name, ean, name}} — nasze retail prices z BL."""
    log("BL inventories: fetch retail...")
    all_bl: dict[str, dict] = {}
    for inv_id, inv_name in INVENTORIES:
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
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": chunk})
            for pid, pd in (r.get("products") or {}).items():
                sku = pd.get("sku")
                if not sku or sku in all_bl:
                    continue
                prices = pd.get("prices") or {}
                retail = float(list(prices.values())[0]) if prices else 0.0
                tf = pd.get("text_fields") or {}
                all_bl[sku] = {
                    "retail": retail,
                    "inv": inv_name,
                    "ean": pd.get("ean") or "",
                    "name": (tf.get("name") or "")[:80],
                }
        log(f"  {inv_name}: {sum(1 for v in all_bl.values() if v['inv'] == inv_name)} SKU")
    return all_bl


def fetch_erli_prices() -> dict[str, dict]:
    """Zwraca {sku: {price_pln, stock, status}} — Erli aktualne."""
    log("Erli: fetch all products...")
    H = {"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"}
    all_erli: dict[str, dict] = {}
    after = None
    while True:
        body = {"pagination": {"limit": 200, "sortField": "externalId", "order": "ASC"}}
        if after:
            body["pagination"]["after"] = after
        r = httpx.post("https://erli.pl/svc/shop-api/products/_search", headers=H, json=body, timeout=30.0)
        if r.status_code != 200:
            log(f"  HTTP {r.status_code}: {r.text[:150]}")
            break
        items = r.json()
        if not items:
            break
        for p in items:
            sku = p.get("externalId")
            all_erli[sku] = {
                "price_pln": (p.get("price") or 0) / 100.0,  # grosze → PLN
                "stock": p.get("stock") or 0,
                "problems": p.get("buyableProblems") or [],
                "archived": bool(p.get("archived")),
            }
        after = items[-1].get("externalId")
        if len(items) < 200:
            break
        time.sleep(0.3)
    log(f"  → {len(all_erli)} Erli offers")
    return all_erli


def main() -> int:
    if not BL_TOKEN or not ERLI_KEY:
        log("ERROR: brak BASELINKER_TOKEN / ERLI_API_KEY")
        return 2

    multistore = fetch_multistore_prices()
    bl_retail = fetch_bl_retail()
    erli = fetch_erli_prices()

    log(f"Comparing {len(erli)} Erli offers...")
    rows = []
    losses = 0
    under_50 = 0
    under_10 = 0
    for sku, e in erli.items():
        if e["archived"]:
            continue
        bl = bl_retail.get(sku, {})
        bl_price = bl.get("retail", 0.0)
        ms_price = multistore.get(sku, 0.0)
        erli_price = e["price_pln"]

        # Flags
        loss = ms_price > 0 and erli_price > 0 and erli_price <= ms_price
        underpriced_50 = bl_price > 0 and erli_price > 0 and erli_price < 0.5 * bl_price
        underpriced_10 = bl_price > 0 and erli_price > 0 and erli_price < 0.9 * bl_price

        if loss:
            losses += 1
        if underpriced_50:
            under_50 += 1
        elif underpriced_10:
            under_10 += 1

        # Zapisz tylko problemowe
        if loss or underpriced_10:
            rows.append({
                "sku": sku,
                "name": bl.get("name", ""),
                "inv": bl.get("inv", "?"),
                "ean": bl.get("ean", ""),
                "erli_pln": round(erli_price, 2),
                "bl_retail_pln": round(bl_price, 2),
                "multistore_pln": round(ms_price, 2),
                "erli_vs_bl_pct": f"{(erli_price / bl_price * 100):.0f}%" if bl_price else "-",
                "erli_vs_multistore_pct": f"{(erli_price / ms_price * 100):.0f}%" if ms_price else "-",
                "flags": "|".join(f for f, v in [("LOSS", loss), ("UNDER_50", underpriced_50), ("UNDER_10", underpriced_10 and not underpriced_50)] if v),
                "stock": e["stock"],
            })

    # Save CSV
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / "output" / f"price_audit_{ts}.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            # sortuj: LOSS first, potem UNDER_50, UNDER_10
            def sort_key(r):
                if "LOSS" in r["flags"]: return (0, r["sku"])
                if "UNDER_50" in r["flags"]: return (1, r["sku"])
                return (2, r["sku"])
            for r in sorted(rows, key=sort_key):
                writer.writerow(r)

    log(f"SAVED: {out}  ({len(rows)} problematic rows)")
    log(f"SUMMARY:")
    log(f"  ⚠️  LOSS (erli ≤ hurtownia): {losses}")
    log(f"  ⚠️  UNDERPRICED 50%+ (erli < 50% BL): {under_50}")
    log(f"  ⚠️  UNDERPRICED 10-50% (erli < 90% BL): {under_10}")
    log(f"  Total analyzed: {sum(1 for e in erli.values() if not e['archived'])} Erli offers")
    log(f"  BL retail matched: {sum(1 for s in erli if s in bl_retail)}")
    log(f"  MultiStore price matched: {sum(1 for s in erli if s in multistore)}")

    # Top 10 loss (największa strata)
    loss_rows = [r for r in rows if "LOSS" in r["flags"]]
    if loss_rows:
        log(f"\n=== TOP 15 LOSSES (erli ≤ hurtownia) ===")
        for r in loss_rows[:15]:
            log(f"  {r['sku']:20} erli={r['erli_pln']:>7} vs hurt={r['multistore_pln']:>7} ({r['erli_vs_multistore_pct']:>4}) — {r['name'][:60]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
