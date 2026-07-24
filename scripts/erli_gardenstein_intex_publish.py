"""Wystaw Intex-y z BL na Erli konto GardenStein.

Filter:
- Inv: MultiStore (52173) + Kathay (45513)
- Manufacturer ∈ Intex/KATHAY-INTEX
- Nazwa zawiera "intex" (case-insensitive) — wyklucza Gardenstein z Intex mfr_id
- Stock w warehouse `bl_58313` (Allegro Asortyment) ≥ 5

Config:
- ERLI_GARDENSTEIN_KEY (sklep id 103151)
- Cenniki: 32563977 Kurier / 32563978 Paczkomaty (per SKU po nazwie)
- Kurier keywords: pompa, basen stelaż, ogrodowy, dmuchany duży
- Ceny: BL group 96668 (×1.35)
- dispatchTime: 2 dni (standardowe Intex)
- AI opis: Produktyzator prompt v2 (Gemini 2.5 Flash) BEZ bannera meble produkcyjne
- Stock w Erli = allegro_stock (bl_58313), NIE sum warehouses

Usage: venv/bin/python scripts/erli_gardenstein_intex_publish.py [--limit N] [--skip-existing]
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
ERLI = os.getenv("ERLI_GARDENSTEIN_KEY")  # osobny klucz GardenStein
GEMINI_KEYS = [k.strip() for k in (os.getenv("GEMINI_API_KEYS") or "").split(",") if k.strip()]

INVENTORIES = [(52173, "MultiStore"), (45513, "Kathay")]
INTEX_MFRS = {1693337, 1694696}
ALLEGRO_WH = "bl_58313"
MIN_STOCK = 5
MARKUP_FALLBACK = 1.35

# Cenniki GardenStein Erli (fetched z API)
PL_KURIER = "Kurier"
PL_PACZKOMAT = "Paczkomaty"
# Keywords → Kurier (reszta Paczkomat)
KURIER_KEYWORDS = [
    "pompa", "basen stelaż", "basen stelazowy", "stelaż", "stelazowa", "stelazowe",
    "ogrodowy", "ogrodow", "huśtawka", "hustaw",
    "namiot", "dmuchany baseny", "dmuchany basen ogrodowy",
]


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


def is_kurier(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in KURIER_KEYWORDS)


def bl_to_product(pd: dict, bl_id: str) -> Product:
    tf = pd.get("text_fields") or {}
    prices = pd.get("prices") or {}
    stock_map = pd.get("stock") or {}
    # STOCK: bierz TYLKO allegro asortyment
    allegro_stock = int(stock_map.get(ALLEGRO_WH) or 0)
    imgs = pd.get("images") or {}
    img_urls = list(imgs.values()) if isinstance(imgs, dict) else list(imgs)

    return Product(
        product_id=str(bl_id),
        sku=pd.get("sku") or "",
        ean=pd.get("ean") or "",
        name=tf.get("name") or "",
        price=float(prices.get("30157") or 0),
        purchase_price=0.0,
        tax_rate=int(pd.get("tax_rate") or 23),
        weight=float(pd.get("weight") or 0),
        width=float(pd.get("width") or 0),
        height=float(pd.get("height") or 0),
        length=float(pd.get("length") or 0),
        quantity=max(allegro_stock, 0),
        description=tf.get("description") or "",
        description_extra_1=tf.get("description_extra1") or "",
        description_extra_2=tf.get("description_extra2") or "",
        images=img_urls,
        attributes=pd.get("features") or {},
        category_name=tf.get("category_name") or "",
        manufacturer_name=str(pd.get("manufacturer_id") or ""),
    )


def generate_ai_desc(client, product: Product, brand_key: str, brand_info: dict) -> str:
    prompt = build_description_prompt_v2(product, brand_info, brand_key)
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4, max_output_tokens=16000,
                    system_instruction=SYSTEM_PROMPT_JSON,
                    response_mime_type="application/json",
                ),
            )
            text = resp.text or ""
            data = _extract_json(text) or json.loads(text)
            return assemble_html_from_json(data, product.images, [])
        except Exception as e:
            msg = str(e)
            if "503" in msg or "UNAVAILABLE" in msg or "429" in msg or "high demand" in msg.lower():
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise RuntimeError("Gemini retry exhausted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not BL or not ERLI or not GEMINI_KEYS:
        log("ERROR: brak BASELINKER_TOKEN / ERLI_GARDENSTEIN_KEY / GEMINI_API_KEYS")
        return 2

    # 1. Fetch Intex-y z BL
    log("Fetch Intex-y z BL (MultiStore + Kathay)...")
    candidates: list[tuple[int, str, dict]] = []  # (inv_id, bl_id, pd)
    for inv_id, inv_name in INVENTORIES:
        ids = set()
        for mid in INTEX_MFRS:
            page = 1
            while True:
                r = bl("getInventoryProductsList", {"inventory_id": inv_id, "filter_manufacturer_id": mid, "page": page})
                p = r.get("products") or {}
                if not p:
                    break
                ids.update(p.keys())
                if len(p) < 1000:
                    break
                page += 1
                if page > 20:
                    break
        ids_list = list(ids)
        for i in range(0, len(ids_list), 100):
            r = bl("getInventoryProductsData", {"inventory_id": inv_id, "products": ids_list[i:i+100]})
            for pid, pd in (r.get("products") or {}).items():
                candidates.append((inv_id, pid, pd))
        log(f"  {inv_name}: {len(ids_list)} candidates")

    # 2. Filter
    filtered: list[tuple[int, str, dict]] = []
    for inv_id, bl_id, pd in candidates:
        name = (pd.get("text_fields") or {}).get("name") or ""
        if "intex" not in name.lower():
            continue
        stock_map = pd.get("stock") or {}
        allegro = int(stock_map.get(ALLEGRO_WH) or 0)
        if allegro < MIN_STOCK:
            continue
        filtered.append((inv_id, bl_id, pd))
    log(f"Filtered: {len(filtered)} Intex-y z 'intex' w nazwie i stock ≥ {MIN_STOCK}")

    # 3. Skip existing
    existing = set()
    if args.skip_existing:
        log("Fetch existing GardenStein Erli SKUs...")
        H = {"Authorization": f"Bearer {ERLI}", "Content-Type": "application/json"}
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
        log(f"  → {len(existing)} SKU już w GardenStein Erli")

    # 4. Pick N
    picked = []
    for inv_id, bl_id, pd in filtered:
        sku = pd.get("sku") or ""
        if args.skip_existing and sku in existing:
            continue
        picked.append((inv_id, bl_id, pd))
        if len(picked) >= args.limit:
            break
    log(f"Pilot: {len(picked)} SKU")

    # 5. Setup
    gemini = genai.Client(api_key=GEMINI_KEYS[0])
    bm = BrandMapper()
    H = {"Authorization": f"Bearer {ERLI}", "Content-Type": "application/json"}
    results = []

    for i, (inv_id, bl_id, pd) in enumerate(picked, 1):
        sku = pd.get("sku") or ""
        name = (pd.get("text_fields") or {}).get("name") or ""
        log(f"[{i}/{len(picked)}] {sku}: {name[:60]}")
        try:
            product = bl_to_product(pd, bl_id)
            if not product.images or product.price <= 0:
                log(f"    SKIP: brak images ({len(product.images)}) albo price ({product.price})")
                continue

            brand_key, conf = bm.detect(product)
            brand_info = bm.brands.get(brand_key) or {"name": "INTEX", "tagline": "", "keywords": []}
            log(f"    brand={brand_key} conf={conf}")

            log(f"    generuj AI opis...")
            html = generate_ai_desc(gemini, product, brand_key, brand_info)
            log(f"    HTML: {len(html)} zn")

            # Delivery
            delivery = PL_KURIER if is_kurier(name) else PL_PACZKOMAT
            log(f"    delivery: {delivery}")

            prices = pd.get("prices") or {}
            retail_pln = float(prices.get("96668") or 0)
            if retail_pln <= 0:
                retail_pln = round(float(prices.get("30157") or 0) * MARKUP_FALLBACK, 2)

            payload = {
                "name": product.name[:200],
                "images": [{"url": u} for u in product.images[:20] if u.startswith("http")],
                "price": int(round(retail_pln * 100)),
                "stock": product.quantity,  # allegro_stock
                "dispatchTime": {"period": 2, "unit": "day"},
                "description": html[:80000],
                "deliveryPriceList": delivery,
            }
            if product.ean:
                payload["ean"] = str(product.ean)
            if product.weight > 0:
                payload["weight"] = int(round(product.weight * 1000))

            if args.dry_run:
                log(f"    DRY-RUN: retail={retail_pln} stock={product.quantity} deliv={delivery}")
                results.append({"sku": sku, "dry_run": True})
                continue

            sku_enc = quote(str(sku), safe="")
            r = httpx.post(f"https://erli.pl/svc/shop-api/products/{sku_enc}",
                          headers=H, json=payload, timeout=45.0)
            entry = {"sku": sku, "status": r.status_code, "delivery": delivery, "stock": product.quantity, "price_pln": retail_pln}
            if r.status_code in (200, 201, 202):
                log(f"    ✅ OK ({r.status_code})")
            else:
                try:
                    entry["response"] = r.json()
                except Exception:
                    entry["response"] = r.text[:300]
                log(f"    ❌ FAIL ({r.status_code}): {r.text[:200]}")
            results.append(entry)
            time.sleep(1.0)
        except Exception as e:
            import traceback
            log(f"    EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append({"sku": sku, "error": f"{type(e).__name__}: {e}"})

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent.parent / "output" / f"gardenstein_intex_{ts}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")

    ok = sum(1 for r in results if r.get("status") in (200, 201, 202))
    log(f"SUMMARY: OK={ok} / {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
