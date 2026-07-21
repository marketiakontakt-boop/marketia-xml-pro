"""AI classifier SKU→Erli category używający Claude API.

Load /tmp/erli_cats_slim.json (pobrać osobno), fetch BL SKU (name), batch requests
do Claude Sonnet 4.6 z prompt caching (drzewo Erli = cached prefix, SKU names varying suffix).

Parse response → PATCH externalCategories batch-update.

Cost estimate: ~2758 SKU / 50 per request = 55 requests × Sonnet 4.6.
Z prompt caching drzewa (~50KB = 12K tokens cached, kolejne req read po 0.1×):
  First req: ~$0.05, kolejne ~$0.015 each = ~$1 total.

Usage: venv/bin/python scripts/erli_ai_classify_categories.py [--dry-run] [--batch-size 50]

Wymagane env: ANTHROPIC_API_KEY, BASELINKER_TOKEN, ERLI_API_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx
from dotenv import load_dotenv

load_dotenv()

BL_TOKEN = os.getenv("BASELINKER_TOKEN")
ERLI_KEY = os.getenv("ERLI_API_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
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
    """Zwraca kompaktowy format `ID: FULL_PATH` per leaf, jeden per linia."""
    leafs = [c for c in cats if c.get("leaf")]
    lines = []
    for c in leafs:
        bc = c.get("breadcrumb") or []
        path = " > ".join(x.get("name", "?") for x in bc)
        lines.append(f"{c['id']}: {path}")
    return "\n".join(lines)


def fetch_bl_products() -> list[tuple[str, str]]:
    """Zwraca [(sku, name)] dla wszystkich SKU z target inv."""
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


CLASSIFY_PROMPT = """Klasyfikujesz produkty e-commerce do kategorii marketplace Erli.

Poniżej lista dostępnych kategorii Erli (LEAF only — kategorie końcowe). Format `ID: PEŁNA_ŚCIEŻKA`:

```
{categories}
```

Dla każdego produktu poniżej dobierz NAJLEPSZĄ pasującą kategorię. Zwróć TYLKO JSON w formacie:
```json
{{"SKU_1": ID_KATEGORII, "SKU_2": ID_KATEGORII, ...}}
```

Wybieraj ID z listy powyżej. Jeśli produkt naprawdę nie pasuje do żadnej kategorii → użyj `null`.

Produkty do klasyfikacji:
{products}
"""


def classify_batch(client: anthropic.Anthropic, cats_context: str, batch: list[tuple[str, str]]) -> dict:
    """Zwraca {sku: category_id | None} dla batch."""
    products_str = "\n".join(f"- {sku}: {name}" for sku, name in batch)

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=[
            {
                "type": "text",
                "text": CLASSIFY_PROMPT.format(categories=cats_context, products="{products}"),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": f"Sklasyfikuj te produkty:\n{products_str}"}],
    )

    text = next((b.text for b in resp.content if b.type == "text"), "")
    # Extract JSON
    import re
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if not m:
        log(f"  ⚠️  brak JSON w response: {text[:150]}")
        return {}
    try:
        data = json.loads(m.group(0))
        return data
    except json.JSONDecodeError as e:
        log(f"  ⚠️  JSON parse fail: {e}. Raw: {text[:200]}")
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, help="max SKU (test)")
    args = parser.parse_args()

    if not CATS_FILE.exists():
        log(f"ERROR: brak {CATS_FILE} — uruchom najpierw fetch drzewa")
        return 2
    if not ANTHROPIC_KEY:
        log("ERROR: brak ANTHROPIC_API_KEY")
        return 2

    cats = json.load(open(CATS_FILE, encoding="utf-8"))
    log(f"Loaded {len(cats)} categories")
    cats_context = build_leaf_context(cats)
    log(f"Leaf context: {len(cats_context)} chars (~{len(cats_context) // 4} tokens)")

    products = fetch_bl_products()
    log(f"BL products: {len(products)}")
    if args.limit:
        products = products[: args.limit]
        log(f"Applied --limit {args.limit}")

    client = anthropic.Anthropic()

    # Classify in batches
    all_mappings: dict[str, str | None] = {}
    for i in range(0, len(products), args.batch_size):
        batch = products[i : i + args.batch_size]
        log(f"Batch {i//args.batch_size + 1}/{(len(products)+args.batch_size-1)//args.batch_size}: {len(batch)} SKU...")
        result = classify_batch(client, cats_context, batch)
        # Merge — normalize int/str keys
        for sku, cat_id in result.items():
            all_mappings[str(sku)] = cat_id
        log(f"  → classified {sum(1 for v in result.values() if v)} / {len(batch)}")

    matched = {k: v for k, v in all_mappings.items() if v}
    log(f"TOTAL matched: {len(matched)} / {len(products)}")

    if args.dry_run:
        # Save + top counts
        out = Path(__file__).resolve().parent.parent / "output" / f"ai_classify_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(all_mappings, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"DRY-RUN saved: {out}")
        from collections import Counter
        counts = Counter(v for v in matched.values())
        log(f"Top 15 categories:")
        for cid, cnt in counts.most_common(15):
            path = next((f"{c['id']}: " + " > ".join(x.get('name', '?') for x in c.get('breadcrumb', [])) for c in cats if c['id'] == cid), str(cid))
            log(f"  {cnt:4}× {path}")
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

    log(f"CATEGORY SUMMARY: OK={ok} FAIL={fail} SKIPPED (no match): {len(products) - len(matched)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
