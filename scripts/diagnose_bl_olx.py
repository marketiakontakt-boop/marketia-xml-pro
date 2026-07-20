"""Diagnoza: BL integracje + kompletność produktu w 36715 pod OLX publish."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.sync.baselinker_sync import _call, _list_products  # noqa: E402

TOKEN = os.getenv("BASELINKER_TOKEN", "").strip()

print("=" * 60)
print("1. Integracje BL Connect")
print("=" * 60)
try:
    data = _call(TOKEN, "getConnectIntegrations", {})
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
except Exception as e:
    print(f"BŁĄD: {e}")

print()
print("=" * 60)
print("2. External storages (Allegro, OLX?)")
print("=" * 60)
try:
    data = _call(TOKEN, "getInventoryIntegrations", {})
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
except Exception as e:
    print(f"getInventoryIntegrations BŁĄD: {e}")

print()
print("=" * 60)
print("3. External store mappings dla 36715")
print("=" * 60)
try:
    data = _call(TOKEN, "getInventoryAvailableTextFieldKeys", {"inventory_id": 36715})
    keys = data.get("text_field_keys", [])
    olx_keys = [k for k in keys if "olx" in str(k).lower()]
    print(f"Wszystkich text_field_keys: {len(keys)}")
    print(f"OLX-related: {olx_keys}")
    # pierwsze 20
    print("Sample:")
    for k in keys[:20]:
        print(f"  {k}")
except Exception as e:
    print(f"BŁĄD: {e}")

print()
print("=" * 60)
print("4. Sample produkt 36715 — pełen dump (klon 100-1)")
print("=" * 60)
sku_map = _list_products(TOKEN, 36715)
CLONE_RE = re.compile(r"^(.+)-(\d+)$")
sample = None
for sku in ["100-1", "100"]:
    if sku in sku_map:
        sample = (sku, sku_map[sku][0])
        break
if not sample:
    print("Nie znaleziono 100 ani 100-1")
    sys.exit(1)

sku, pid = sample
data = _call(TOKEN, "getInventoryProductsData", {
    "inventory_id": 36715,
    "products": [str(pid)],
})
prod = list(data.get("products", {}).values())[0]

print(f"SKU: {prod.get('sku')}")
print(f"EAN: {prod.get('ean')}")
print(f"Waga: {prod.get('weight')}")
print(f"Wymiary: {prod.get('width')}x{prod.get('length')}x{prod.get('height')}")
print(f"Category_id: {prod.get('category_id')}")
print(f"Manufacturer_id: {prod.get('manufacturer_id')}")
print(f"Prices: {prod.get('prices')}")
print(f"Stock: {prod.get('stock')}")

print("\n--- Text fields ---")
tf = prod.get("text_fields", {})
for k, v in tf.items():
    val = str(v)[:150]
    print(f"  {k!r}: {val!r}")

print(f"\n--- Images (len={len(prod.get('images', {}))}) ---")
imgs = prod.get("images", {})
for k, v in list(imgs.items())[:3]:
    print(f"  {k}: {str(v)[:100]}")

print("\n--- Wszystkie klucze produktu ---")
for k, v in prod.items():
    if isinstance(v, (dict, list)):
        print(f"  {k}: <{type(v).__name__} len={len(v)}>")
    else:
        print(f"  {k}: {str(v)[:80]}")
