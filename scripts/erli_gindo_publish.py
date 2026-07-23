"""Wystaw produkty z Gindo (inv 101582) na Erli z AI opisami Produktyzatora.

Flow per SKU:
1. Fetch BL product data (name, desc, prices, images, features)
2. BrandMapper.detect → brand (Gindo produkty najczęściej homestein bo meble łazienkowe)
3. build_description_prompt_v2(product, brand, ...) → prompt Gemini
4. Gemini 2.5 Flash → JSON sekcji → assemble_html_from_json → HTML opis
5. Doklej banner "🛠 Meble produkcyjne — czas realizacji 2-3 dni robocze"
6. POST /products/{sku} do Erli:
   - description = HTML (string, Erli accepts anyOf)
   - price = retail w groszach (z BL group 96668, fallback: hurtownia × 1.35)
   - stock, weight, images, dispatchTime={period:3, unit:'day'}
   - deliveryPriceList = 'Erli Kurier free' (name, zawiera DPD 31.5kg + inne)

Usage: venv/bin/python scripts/erli_gindo_publish.py [--limit N] [--dry-run]
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
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.ai.prompts import (
    SYSTEM_PROMPT_JSON,
    build_description_prompt_v2,
    assemble_html_from_json,
    _extract_json,
)
from app.parser.normalizer import Product
from app.transformer.brand_mapper import BrandMapper

load_dotenv()

BL = os.getenv("BASELINKER_TOKEN")
ERLI = os.getenv("ERLI_API_KEY")
GEMINI_KEYS = [k.strip() for k in (os.getenv("GEMINI_API_KEYS") or "").split(",") if k.strip()]
GINDO_INV = 101582
MARKUP_FALLBACK = 1.35
DELIVERY_NAME = "Erli Kurier free"
PRODUCTION_BANNER = (
    "\n<div style='background:#FFF7ED;border-left:4px solid #F97316;padding:12px;margin:16px 0;'>"
    "<b>🛠 Meble produkcyjne</b> — realizacja zamówienia zajmuje <b>2-3 dni robocze</b>. "
    "Każdy mebel wykonujemy pod konkretne zamówienie z najwyższą starannością."
    "</div>"
)


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def bl(method: str, params: dict, retries: int = 4) -> dict:
    for i in range(retries):
        r = httpx.post(
            "https://api.baselinker.com/connector.php",
            headers={"X-BLToken": BL},
            data={"method": method, "parameters": json.dumps(params)},
            timeout=60.0,
        )
        d = r.json()
        if d.get("status") == "SUCCESS" or d.get("error_code") != "CONNECTION":
            return d
        time.sleep(15 * (i + 1))
    return d


def bl_to_product(pd: dict, bl_id: str) -> Product:
    """Konwersja BL product dict → app.parser.normalizer.Product."""
    tf = pd.get("text_fields") or {}
    prices = pd.get("prices") or {}
    stock_map = pd.get("stock") or {}
    stock = sum(int(v or 0) for v in stock_map.values()) if isinstance(stock_map, dict) else 0
    imgs = pd.get("images") or {}
    if isinstance(imgs, dict):
        img_urls = list(imgs.values())
    else:
        img_urls = list(imgs)

    return Product(
        product_id=str(bl_id),
        sku=pd.get("sku") or "",
        ean=pd.get("ean") or "",
        name=tf.get("name") or "",
        price=float(prices.get("30157") or 0),  # hurtownia
        purchase_price=0.0,
        tax_rate=int(pd.get("tax_rate") or 23),
        weight=float(pd.get("weight") or 0),
        width=float(pd.get("width") or 0),
        height=float(pd.get("height") or 0),
        length=float(pd.get("length") or 0),
        quantity=max(stock, 0),
        description=tf.get("description") or "",
        description_extra_1=tf.get("description_extra1") or "",
        description_extra_2=tf.get("description_extra2") or "",
        images=img_urls,
        attributes=pd.get("features") or {},
        category_name=tf.get("category_name") or "",
        manufacturer_name=str(pd.get("manufacturer_id") or ""),
    )


def generate_ai_desc(client, product: Product, brand_key: str, brand_info: dict) -> str:
    """Generuj AI opis przez Gemini z prompt Produktyzatora v2. Zwraca HTML.

    Retry na 503 (Gemini high demand) z backoff 15/30/60s.
    """
    prompt = build_description_prompt_v2(product, brand_info, brand_key)
    last_exc = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    max_output_tokens=16000,  # bumped z 8000 (JSON truncation fix)
                    system_instruction=SYSTEM_PROMPT_JSON,
                    response_mime_type="application/json",
                ),
            )
            text = resp.text or ""
            data = _extract_json(text) or json.loads(text)
            return assemble_html_from_json(data, product.images, [])
        except Exception as e:
            last_exc = e
            msg = str(e)
            if "503" in msg or "UNAVAILABLE" in msg or "429" in msg or "high demand" in msg.lower():
                wait = 15 * (attempt + 1)
                time.sleep(wait)
                continue
            raise
    raise last_exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", help="pomijaj SKU już w Erli")
    args = parser.parse_args()

    if not BL or not ERLI or not GEMINI_KEYS:
        log("ERROR: brak BASELINKER_TOKEN / ERLI_API_KEY / GEMINI_API_KEYS")
        return 2

    # 1. Fetch Gindo products
    log(f"Fetch Gindo (inv {GINDO_INV})...")
    ids = []
    page = 1
    while True:
        r = bl("getInventoryProductsList", {"inventory_id": GINDO_INV, "page": page})
        p = r.get("products") or {}
        if not p:
            break
        ids.extend(p.keys())
        if len(p) < 1000:
            break
        page += 1
        if page > 30:
            break
    log(f"  → {len(ids)} product IDs")

    # Pobierz batches żeby móc filtrować po SKU (nie bl_id)
    all_products = {}
    for i in range(0, len(ids), 100):
        r = bl("getInventoryProductsData", {"inventory_id": GINDO_INV, "products": ids[i:i+100]})
        all_products.update(r.get("products") or {})
    log(f"  Fetched data for {len(all_products)} products")

    # Skip existing on Erli
    existing_skus: set[str] = set()
    if args.skip_existing:
        log("Fetch existing Erli SKUs...")
        H = {"Authorization": f"Bearer {ERLI}", "Content-Type": "application/json"}
        after = None
        while True:
            body = {"pagination": {"limit": 200, "sortField": "externalId", "order": "ASC"}}
            if after: body["pagination"]["after"] = after
            rr = httpx.post("https://erli.pl/svc/shop-api/products/_search", headers=H, json=body, timeout=30.0)
            if rr.status_code != 200: break
            items = rr.json()
            if not items: break
            for p in items:
                existing_skus.add(p.get("externalId"))
            after = items[-1].get("externalId")
            if len(items) < 200: break
            time.sleep(0.3)
        log(f"  → {len(existing_skus)} SKU już w Erli")

    # Filter + limit
    products_raw = {}
    for bl_id, pd in all_products.items():
        sku = pd.get("sku")
        if not sku: continue
        if args.skip_existing and sku in existing_skus: continue
        products_raw[bl_id] = pd
        if len(products_raw) >= args.limit: break
    log(f"Pilot: {len(products_raw)} SKU do wystawienia")

    # 2. Setup
    gemini = genai.Client(api_key=GEMINI_KEYS[0])
    bm = BrandMapper()
    H = {"Authorization": f"Bearer {ERLI}", "Content-Type": "application/json"}

    results = []
    for i, (bl_id, pd) in enumerate(products_raw.items(), 1):
        sku = pd.get("sku") or ""
        log(f"[{i}/{len(products_raw)}] {sku}: {(pd.get('text_fields') or {}).get('name','')[:60]}")
        try:
            product = bl_to_product(pd, bl_id)
            if not product.images or product.price <= 0:
                log(f"    SKIP: brak images ({len(product.images)}) albo price ({product.price})")
                continue

            # Brand detect
            brand_key, conf = bm.detect(product)
            brand_info = bm.brands.get(brand_key) or {"name": "GINDO", "tagline": "Meble produkcyjne", "keywords": []}
            log(f"    brand={brand_key} (conf={conf}, display={brand_info.get('name')})")

            # AI desc
            log(f"    generuj AI opis...")
            html = generate_ai_desc(gemini, product, brand_key, brand_info)
            html_full = html + PRODUCTION_BANNER
            log(f"    HTML: {len(html_full)} zn")

            # Prices
            prices = pd.get("prices") or {}
            retail_pln = float(prices.get("96668") or 0)
            if retail_pln <= 0:
                retail_pln = round(float(prices.get("30157") or 0) * MARKUP_FALLBACK, 2)
                log(f"    fallback markup: retail={retail_pln}")

            payload = {
                "name": product.name[:200],
                "images": [{"url": u} for u in product.images[:20] if u.startswith("http")],
                "price": int(round(retail_pln * 100)),
                "stock": product.quantity,
                "dispatchTime": {"period": 3, "unit": "day"},
                "description": html_full[:80000],
                "deliveryPriceList": DELIVERY_NAME,
            }
            if product.ean:
                payload["ean"] = str(product.ean)
            if product.weight > 0:
                payload["weight"] = int(round(product.weight * 1000))

            if args.dry_run:
                log(f"    DRY-RUN: {payload['name'][:50]!r} price={retail_pln} stock={product.quantity}")
                results.append({"sku": sku, "dry_run": True, "html_len": len(html_full)})
                continue

            sku_enc = quote(str(sku), safe="")
            r = httpx.post(f"https://erli.pl/svc/shop-api/products/{sku_enc}",
                          headers=H, json=payload, timeout=45.0)
            status = r.status_code
            try:
                resp_body = r.json()
            except Exception:
                resp_body = {"raw": r.text[:300]}
            entry = {"sku": sku, "status": status, "response": resp_body}
            if status in (200, 201, 202):
                log(f"    ✅ OK ({status})")
            else:
                log(f"    ❌ FAIL ({status}): {json.dumps(resp_body)[:200]}")
            results.append(entry)
            time.sleep(1.0)  # powoli
        except Exception as e:
            import traceback
            log(f"    EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append({"sku": sku, "error": f"{type(e).__name__}: {e}"})

    # Save
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / "output" / f"gindo_publish_{ts}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")

    ok = sum(1 for r in results if r.get("status") in (200, 201, 202))
    log(f"SUMMARY: OK={ok} / {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
