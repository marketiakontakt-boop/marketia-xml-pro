"""Sprawdź: 36715 Marketia Katalog — czy klony 100-N mają BL Connect linki?"""
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
INV_ID = 36715

sku_map = _list_products(TOKEN, INV_ID)
CLONE_RE = re.compile(r"^(.+)-(\d+)$")

# Znajdź 5 przykładowych klonów
clones = sorted([sku for sku in sku_map if CLONE_RE.match(sku)])[:5]
pids = [str(sku_map[sku][0]) for sku in clones]

print(f"Sample klony w Marketia Katalog (36715): {clones}")

data = _call(TOKEN, "getInventoryProductsData", {
    "inventory_id": INV_ID,
    "products": pids,
})
products = data.get("products") or {}
for pid, prod in products.items():
    sku = prod.get("sku")
    print(f"\n=== {sku} (PID {pid}) ===")
    print(f"  EAN: {prod.get('ean')}")
    print(f"  Stock: {json.dumps(prod.get('stock', {}), ensure_ascii=False)}")
    print(f"  Links: {json.dumps(prod.get('links', {}), ensure_ascii=False)}")

# Sprawdź czy rodzice też istnieją
print("\n=== Rodzice klonów (masz w 36715?) ===")
for clone in clones:
    parent = CLONE_RE.match(clone).group(1)
    print(f"  '{clone}' → parent '{parent}': {'✓ w 36715' if parent in sku_map else '✗ BRAK'}")

# Overlap z hurtowniami (rodzice)
print("\n=== Rodzice w 36715 vs SOURCE (52173 MultiStore + 45513 Kathay) ===")
all_parents = {CLONE_RE.match(s).group(1) for s in sku_map if CLONE_RE.match(s)}
print(f"  Unikalnych rodziców w klonach 36715: {len(all_parents)}")

ms = set(_list_products(TOKEN, 52173).keys())
ka = set(_list_products(TOKEN, 45513).keys())
print(f"  Rodzice też w MultiStore: {len(all_parents & ms)}")
print(f"  Rodzice też w Kathay:     {len(all_parents & ka)}")
print(f"  Rodzice w (MS ∪ KA):      {len(all_parents & (ms | ka))}")
