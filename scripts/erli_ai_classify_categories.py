"""AI classifier SKU→Erli category używający Gemini (google-genai).

Load /tmp/erli_cats_slim.json, fetch BL SKU (name), batch requests do Gemini
2.5 Flash (szybki + tani). Parse JSON response → PATCH externalCategories.

Usage: venv/bin/python scripts/erli_ai_classify_categories.py [--dry-run] [--batch-size N] [--limit N]

Env: BASELINKER_TOKEN, ERLI_API_KEY, GEMINI_API_KEYS (comma-sep)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

BL_TOKEN = os.getenv("BASELINKER_TOKEN")
ERLI_KEY = os.getenv("ERLI_API_KEY")  # override via --erli-key-var
GEMINI_KEYS = [k.strip() for k in (os.getenv("GEMINI_API_KEYS") or "").split(",") if k.strip()]
CATS_FILE = Path("/tmp/erli_cats_slim.json")
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


def build_leaf_context(cats: list[dict]) -> str:
    leafs = [c for c in cats if c.get("leaf")]
    lines = []
    for c in leafs:
        bc = c.get("breadcrumb") or []
        path = " > ".join(x.get("name", "?") for x in bc)
        lines.append(f"{c['id']}: {path}")
    return "\n".join(lines)


def fetch_bl_products() -> list[tuple[str, str]]:
    out = []
    seen = set()
    for inv_id in INVENTORIES:
        log(f"BL inv {inv_id}: fetching...")
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
            r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": ids[i : i + 100]})
            for pid, pd in (r.get("products") or {}).items():
                sku = pd.get("sku")
                if not sku or sku in seen:
                    continue
                tf = pd.get("text_fields") or {}
                name = tf.get("name") or pd.get("name") or ""
                if name:
                    out.append((sku, name))
                    seen.add(sku)
    return out


def classify_batch(client, model_name: str, cats_context: str, batch: list[tuple[str, str]]) -> dict:
    products_str = "\n".join(f"- {sku}: {name}" for sku, name in batch)

    prompt = f"""Klasyfikujesz produkty e-commerce do kategorii marketplace Erli.

Lista kategorii Erli (format `ID: PEŁNA_ŚCIEŻKA`, tylko LEAF):

```
{cats_context}
```

Dla każdego SKU dobierz NAJLEPSZĄ pasującą kategorię. Zwróć TYLKO poprawny JSON:
{{"SKU1": 123, "SKU2": 456, ...}}

Jeśli produkt naprawdę nie pasuje → użyj `null`. NIE dodawaj markdown code fence.

Produkty:
{products_str}
"""

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=8000,
                    response_mime_type="application/json",
                ),
            )
            text = resp.text or ""
            data = json.loads(text)
            return data
        except json.JSONDecodeError as e:
            log(f"  ⚠️  JSON fail (attempt {attempt+1}): {e}")
            time.sleep(2)
        except Exception as e:
            log(f"  ⚠️  API fail (attempt {attempt+1}): {type(e).__name__}: {str(e)[:150]}")
            time.sleep(5)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--only-skus-file", help="JSON file z listą SKU do klasyfikacji (retry)")
    parser.add_argument("--erli-key-var", default="ERLI_API_KEY", help="env var name for Erli key (ERLI_API_KEY / ERLI_GARDENSTEIN_KEY)")
    parser.add_argument("--fetch-missing-cat", action="store_true", help="Auto-fetch SKU z Erli z buyableProblems.category")
    args = parser.parse_args()

    if not CATS_FILE.exists():
        log(f"ERROR: brak {CATS_FILE}")
        return 2
    if not GEMINI_KEYS:
        log("ERROR: brak GEMINI_API_KEYS w .env")
        return 2

    # Override Erli key per --erli-key-var (dla GardenStein sub-sklep)
    global ERLI_KEY
    ERLI_KEY = os.getenv(args.erli_key_var)
    if not ERLI_KEY:
        log(f"ERROR: brak {args.erli_key_var} w .env")
        return 2
    log(f"Erli key: {args.erli_key_var}")

    # Auto-fetch SKU z missing category z Erli
    if args.fetch_missing_cat:
        log("Fetch SKU z buyableProblems.category z Erli...")
        H = {"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"}
        missing = []
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
                prob = p.get("buyableProblems") or []
                if "category" in prob and "archived" not in prob:
                    missing.append(p.get("externalId"))
            after = items[-1].get("externalId")
            if len(items) < 200:
                break
            time.sleep(0.3)
        log(f"  → {len(missing)} SKU z missing category")
        args.only_skus_file = f"/tmp/erli_missing_cat_{args.erli_key_var}.json"
        with open(args.only_skus_file, "w", encoding="utf-8") as f:
            json.dump(missing, f)

    cats = json.load(open(CATS_FILE, encoding="utf-8"))
    log(f"Loaded {len(cats)} categories, {sum(1 for c in cats if c.get('leaf'))} leafs")
    cats_context = build_leaf_context(cats)
    log(f"Context: {len(cats_context)} chars")

    products = fetch_bl_products()
    log(f"BL products: {len(products)}")
    if args.only_skus_file:
        only = set(json.load(open(args.only_skus_file, encoding="utf-8")))
        before = len(products)
        products = [(s, n) for s, n in products if s in only]
        log(f"--only-skus-file: filtered {before} → {len(products)}")
    if args.limit:
        products = products[: args.limit]
        log(f"--limit {args.limit} → {len(products)}")

    client = genai.Client(api_key=GEMINI_KEYS[0])

    all_mappings: dict[str, int | None] = {}
    total_batches = (len(products) + args.batch_size - 1) // args.batch_size
    for i in range(0, len(products), args.batch_size):
        batch = products[i : i + args.batch_size]
        bnum = i // args.batch_size + 1
        log(f"Batch {bnum}/{total_batches}: {len(batch)} SKU...")
        result = classify_batch(client, args.model, cats_context, batch)
        matched_in_batch = sum(1 for v in result.values() if v)
        for sku, cat_id in result.items():
            all_mappings[str(sku)] = cat_id
        log(f"  → {matched_in_batch}/{len(batch)} matched")

    matched = {k: v for k, v in all_mappings.items() if v}
    log(f"TOTAL matched: {len(matched)} / {len(products)}")

    # Save mapping
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / "output" / f"ai_classify_{ts}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(all_mappings, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")

    from collections import Counter
    counts = Counter(v for v in matched.values() if v)
    log("Top 15 kategorie:")
    for cid, cnt in counts.most_common(15):
        try:
            cid_int = int(cid)
        except (ValueError, TypeError):
            cid_int = None
        path = next((f"{c['id']}: " + " > ".join(x.get('name', '?') for x in c.get('breadcrumb', [])) for c in cats if c['id'] == cid_int), str(cid))
        log(f"  {cnt:4}× {path[:100]}")

    if args.dry_run:
        return 0

    # PATCH batch-update
    payload = [
        {
            "externalId": sku,
            "externalCategories": [{"source": "marketplace", "breadcrumb": [{"id": str(cat_id)}]}],
        }
        for sku, cat_id in matched.items()
    ]
    log(f"PATCH payload: {len(payload)}")

    H = {"Authorization": f"Bearer {ERLI_KEY}", "Content-Type": "application/json"}
    ok = fail = 0
    for i in range(0, len(payload), 100):
        chunk = payload[i : i + 100]
        r = httpx.patch("https://erli.pl/svc/shop-api/products/batch-update",
                       headers=H, json=chunk, timeout=60.0)
        if r.status_code == 200:
            data = r.json()
            o = sum(1 for e in data if e.get("status") in (200, 202))
            f_ = len(data) - o
            ok += o
            fail += f_
            log(f"  batch [{i+1}-{i+len(chunk)}]: ok={o} fail={f_}")
        else:
            log(f"  HTTP {r.status_code}: {r.text[:200]}")
            fail += len(chunk)
        time.sleep(0.5)

    log(f"CATEGORY SUMMARY: OK={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
