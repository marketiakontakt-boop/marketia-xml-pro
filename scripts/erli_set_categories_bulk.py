"""Heurystyczne mapowanie SKU → Erli category po słowach kluczowych w nazwie.

Algorytm:
  1. Load /tmp/erli_cats_slim.json (1250 leafs pobranych z Erli)
  2. Dla każdego LEAF category zbuduj tokens z name (lowercase, bez stopwords)
  3. Dla każdego SKU z BL (3 inv) tokenize name + znajdź category z max match score
  4. Bulk PATCH externalCategories = [{source: 'marketplace', breadcrumb: [...]}]

"Na oko" — nie perfekcja. Skip gdy score < threshold (Erli auto-suggest).

Usage: venv/bin/python scripts/erli_set_categories_bulk.py [--dry-run] [--min-score N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BL_TOKEN = os.getenv("BASELINKER_TOKEN")
ERLI_KEY = os.getenv("ERLI_API_KEY")
CATS_FILE = Path("/tmp/erli_cats_slim.json")
DEFAULT_INVENTORIES = [
    int(x) for x in (os.getenv("BL_ERLI_SYNC_INVENTORIES") or "45513,52173,111230").split(",")
]

# Stopwords polskie + typowe dla produktów
STOPWORDS = {
    "do", "z", "na", "w", "i", "dla", "od", "po", "za", "przy", "przed",
    "bez", "cm", "mm", "kg", "szt", "ml", "l", "x", "-", "the", "and",
    "ok", "nowy", "nowa", "nowe", "nowy", "duży", "duża", "mała", "małe",
}


def tokenize(text: str) -> list[str]:
    text = re.sub(r"[^\wąćęłńóśźż\s]", " ", text.lower())
    tokens = [t for t in text.split() if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()]
    return tokens


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


def build_category_index(cats: list[dict]) -> list[tuple[dict, set[str]]]:
    """Zwraca [(category_dict, token_set)] tylko dla LEAF categories."""
    idx = []
    for c in cats:
        if not c.get("leaf"):
            continue
        tokens = set(tokenize(c["name"]))
        # Dodaj też tokens z przedostatniego poziomu (kontekst)
        bc = c.get("breadcrumb") or []
        for level in bc[-2:]:
            tokens.update(tokenize(level.get("name", "")))
        idx.append((c, tokens))
    return idx


def classify(product_name: str, cat_index: list[tuple[dict, set[str]]]) -> tuple[dict | None, int]:
    """Zwraca (best_category, score) albo (None, 0)."""
    p_tokens = set(tokenize(product_name))
    if not p_tokens:
        return None, 0

    best = None
    best_score = 0
    for cat, c_tokens in cat_index:
        # Match = liczba wspólnych tokenów, z bonusem za name match słowo w słowo
        common = p_tokens & c_tokens
        if not common:
            continue
        score = len(common)
        # Bonus jeśli nazwa kategorii występuje jako fragment nazwy produktu
        cat_name_lower = cat["name"].lower()
        if cat_name_lower in product_name.lower():
            score += 5
        if score > best_score:
            best = cat
            best_score = score
    return best, best_score


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-score", type=int, default=2, help="min match score (default 2)")
    parser.add_argument("--limit", type=int, help="max SKU do przetworzenia (test)")
    args = parser.parse_args()

    if not CATS_FILE.exists():
        log(f"ERROR: brak {CATS_FILE}, uruchom najpierw fetch categories")
        return 2

    cats = json.load(open(CATS_FILE, encoding="utf-8"))
    log(f"Loaded {len(cats)} categories, building leaf index...")
    cat_index = build_category_index(cats)
    log(f"  → {len(cat_index)} leaf categories indexed")

    # Fetch BL products z 3 inv
    log("Fetching BL products from 3 inventories...")
    all_products: dict[str, dict] = {}  # sku -> {name}
    for inv_id in DEFAULT_INVENTORIES:
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
                if not sku or sku in all_products:
                    continue
                tf = pd.get("text_fields") or {}
                name = tf.get("name") or pd.get("name") or ""
                if name:
                    all_products[sku] = {"name": name}
    log(f"Total unique SKU with names: {len(all_products)}")

    # Classify
    log("Classifying products...")
    payload = []
    skip = 0
    cat_hits: Counter = Counter()
    for sku, info in all_products.items():
        cat, score = classify(info["name"], cat_index)
        if cat and score >= args.min_score:
            payload.append({
                "externalId": sku,
                "externalCategories": [{
                    "source": "marketplace",
                    "breadcrumb": [{"id": b["id"], "name": b["name"]} for b in cat["breadcrumb"]],
                }],
            })
            cat_hits[cat["name"]] += 1
        else:
            skip += 1
    log(f"  matched: {len(payload)}, skipped (below score {args.min_score}): {skip}")
    log("  top 15 kategorie:")
    for cn, count in cat_hits.most_common(15):
        log(f"    {count:4}× {cn}")

    if args.limit:
        payload = payload[: args.limit]
        log(f"  (--limit {args.limit} applied → {len(payload)} to send)")

    if args.dry_run:
        log(f"DRY-RUN: {len(payload)} would be PATCHed")
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

    log(f"CATEGORIES SUMMARY: OK={ok} FAIL={fail} SKIPPED (no match): {skip}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
