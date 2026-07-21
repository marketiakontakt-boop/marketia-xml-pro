"""Retry Erli publish dla failed SKU z poprzedniego pilot output.

Filtruje entries z erli_status ∈ {400, 429} (pomija 409 = already exists).
Re-fetch z BL, re-map z FIXED image dedup, POST /products/{sku}.

Usage:
    venv/bin/python scripts/erli_retry_failed.py <path/to/erli_pilot_*.json>
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

# Reużyj helpers z głównego skryptu
sys.path.insert(0, str(Path(__file__).resolve().parent))
from erli_pilot_publish import bl, bl_to_erli, erli, log  # noqa: E402

load_dotenv()

RETRYABLE_STATUSES = {400, 429, 500, 502, 503, 504}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: erli_retry_failed.py <pilot_output.json>")
        return 2

    src = Path(sys.argv[1])
    if not src.is_file():
        print(f"ERROR: {src} nie istnieje")
        return 2

    data = json.loads(src.read_text(encoding="utf-8"))
    all_results = data.get("results", [])
    failed = [r for r in all_results if r.get("erli_status") in RETRYABLE_STATUSES]
    log(f"Loaded {len(all_results)} entries, {len(failed)} failed to retry")

    # Group by inventory dla efektywnego re-fetch z BL
    by_inv: dict[int, list[str]] = {}
    for r in failed:
        inv = r.get("inventory")
        if inv:
            by_inv.setdefault(inv, []).append(r["bl_id"])

    # Re-fetch aktualne dane z BL (chunk 100)
    log("Re-fetching product data z BL...")
    fresh_products: dict[str, dict] = {}
    for inv_id, bl_ids in by_inv.items():
        log(f"  inv {inv_id}: {len(bl_ids)} SKU do re-fetch")
        for i in range(0, len(bl_ids), 100):
            chunk = bl_ids[i : i + 100]
            r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": chunk})
            for pid, pdata in (r.get("products") or {}).items():
                pdata["_bl_id"] = pid
                pdata["_bl_inventory"] = inv_id
                fresh_products[f"{inv_id}:{pid}"] = pdata

    log(f"Fetched {len(fresh_products)} products fresh from BL")

    # Retry POST
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / "output" / f"erli_retry_{ts}.json"
    results = []

    for i, entry in enumerate(failed, 1):
        sku = entry["sku"]
        inv = entry["inventory"]
        bl_id = entry["bl_id"]
        prod = fresh_products.get(f"{inv}:{bl_id}")
        if not prod:
            log(f"  [{i}/{len(failed)}] {sku}: MISSING w fresh BL fetch — skip")
            entry["retry_status"] = "missing_bl"
            results.append(entry)
            continue

        mapped = bl_to_erli(prod)
        if not mapped:
            log(f"  [{i}/{len(failed)}] {sku}: mapping SKIP — brak name/images/price")
            entry["retry_status"] = "skip_mapping"
            results.append(entry)
            continue

        sku_enc = quote(str(sku), safe="")
        log(f"  [{i}/{len(failed)}] {sku}: POST /products/{sku_enc} (imgs={len(mapped['images'])})...")
        status, resp = erli("POST", f"/products/{sku_enc}", mapped)
        entry["retry_status"] = status
        entry["retry_response"] = resp
        if status in (200, 201, 202):
            log(f"    ✅ OK ({status})")
            time.sleep(0.5)
        elif status == 429:
            log(f"    ⏳ 429, sleep 10s + retry once")
            time.sleep(10)
            status, resp = erli("POST", f"/products/{sku_enc}", mapped)
            entry["retry_status"] = status
            entry["retry_response"] = resp
            log(f"    → {status}: {json.dumps(resp)[:120]}")
            time.sleep(1)
        elif status == 409:
            log(f"    ⚠️  409 already exists (OK, pomijam)")
            time.sleep(0.3)
        else:
            log(f"    ❌ still FAIL ({status}): {json.dumps(resp)[:150]}")
            time.sleep(0.5)
        results.append(entry)

    # Save + summary
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"timestamp": ts, "src": str(src), "results": results}, f,
                  ensure_ascii=False, indent=2)
    log(f"Saved: {out}")

    ok = sum(1 for r in results if r.get("retry_status") in (200, 201, 202))
    conflict = sum(1 for r in results if r.get("retry_status") == 409)
    still_fail = sum(1 for r in results if isinstance(r.get("retry_status"), int)
                     and r["retry_status"] not in (200, 201, 202, 409))
    skip = sum(1 for r in results if isinstance(r.get("retry_status"), str))
    log(f"RETRY SUMMARY: OK={ok}  409_exists={conflict}  still_fail={still_fail}  skip={skip}  total={len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
