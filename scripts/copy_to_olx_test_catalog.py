"""Utwórz NOWY inventory "OLX Testy" w BaseLinker i skopiuj do niego
15 pierwszych (alfabetycznie) rodziców klonów z inventory 52173 MultiStore.

Nie modyfikuje żadnego istniejącego inventory ani produktu — script tylko tworzy nowe.
"""
from __future__ import annotations

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
SOURCE_INV_ID = 52173
NEW_INV_NAME = "OLX Testy"
NEW_INV_DESC = "Piaskownica do testów integracji OLX — kopie rodziców z MultiStore"
TARGET_WAREHOUSE = "bl_55230"
PRICE_GROUP_ID = 30157
SOURCE_STOCK_WAREHOUSE = "bl_58313"
LIMIT = 15

CLONE_RE = re.compile(r"^(.+)-(\d+)$")


def _first_price(prices_dict):
    """Return first value from `prices_dict` (dict {price_group_id: value}) or 0."""
    if not prices_dict or not isinstance(prices_dict, dict):
        return 0
    for v in prices_dict.values():
        return v
    return 0


def _extract_stock_from_source(stock_dict):
    """Pobierz stan z bl_58313 z source stock dict; fallback 0.

    BL zwraca stock w kilku możliwych kształtach:
      - `{bl_X: int}` (simple)
      - `{variant_id: {bl_X: int}}` (warianty — bierzemy jakikolwiek wariant)
    """
    if not stock_dict or not isinstance(stock_dict, dict):
        return 0
    if SOURCE_STOCK_WAREHOUSE in stock_dict:
        val = stock_dict[SOURCE_STOCK_WAREHOUSE]
        if isinstance(val, (int, float, str)):
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0
    # warianty
    for v in stock_dict.values():
        if isinstance(v, dict) and SOURCE_STOCK_WAREHOUSE in v:
            try:
                return int(v[SOURCE_STOCK_WAREHOUSE])
            except (TypeError, ValueError):
                return 0
    return 0


def main() -> int:
    if not TOKEN:
        print("BŁĄD: brak BASELINKER_TOKEN w .env")
        return 1

    # ---- KROK 1: Znajdź istniejący "OLX Testy" LUB stwórz nowy --------------
    print(f"[1] Szukam istniejącego inventory '{NEW_INV_NAME}'…")
    invs_resp = _call(TOKEN, "getInventories", {})
    existing = next(
        (i for i in invs_resp.get("inventories", []) if i.get("name") == NEW_INV_NAME),
        None,
    )
    if existing:
        new_inv_id = int(existing.get("inventory_id"))
        print(f"    ✓ Reuse: inventory_id = {new_inv_id}")
    else:
        print(f"    → addInventory '{NEW_INV_NAME}'…")
        try:
            resp = _call(TOKEN, "addInventory", {
                "inventory_id": None,
                "name": NEW_INV_NAME,
                "description": NEW_INV_DESC,
                "languages": ["pl"],
                "default_language": "pl",
                "price_groups": [PRICE_GROUP_ID],
                "default_price_group": PRICE_GROUP_ID,
                "warehouses": [TARGET_WAREHOUSE],
                "default_warehouse": TARGET_WAREHOUSE,
                "reservations": False,
                "is_default": False,
            })
        except Exception as e:
            print(f"    BŁĄD addInventory: {e}")
            return 1

        new_inv_id = resp.get("inventory_id")
        if not new_inv_id:
            print(f"    BŁĄD: response bez inventory_id → {resp}")
            return 1
        print(f"    ✓ Nowy inventory_id = {new_inv_id}")

    # ---- KROK 2: Wczytaj produkty z 52173 -----------------------------------
    print(f"\n[2] _list_products z inventory {SOURCE_INV_ID}…")
    sku_map = _list_products(TOKEN, SOURCE_INV_ID)
    print(f"    {len(sku_map)} produktów")

    # ---- KROK 3: Znajdź rodziców klonów -------------------------------------
    all_skus = set(sku_map.keys())
    parents_with_clones = set()
    for sku in all_skus:
        m = CLONE_RE.match(sku)
        if m and m.group(1) in all_skus:
            parents_with_clones.add(m.group(1))
    print(f"[3] Rodziców z klonami: {len(parents_with_clones)}")

    selected = sorted(parents_with_clones)[:LIMIT]
    print(f"    Wybrano pierwszych {len(selected)}: {selected}")

    # Pre-check: co już jest w targecie? (idempotencja)
    try:
        existing_target = _list_products(TOKEN, new_inv_id)
        print(f"    Target ma już {len(existing_target)} produktów — będę skipował duplikaty SKU.")
    except Exception:
        existing_target = {}

    # ---- KROK 4: Kopiuj po kolei --------------------------------------------
    print(f"\n[4] Kopiowanie {len(selected)} rodziców do inventory {new_inv_id}…")
    added = []
    failed = []
    skipped = []
    for parent_sku in selected:
        if parent_sku in existing_target:
            existing_pid = existing_target[parent_sku][0]
            print(f"  = {parent_sku} → PID {existing_pid} (już istnieje, skip)")
            skipped.append((parent_sku, existing_pid))
            continue
        pid, _qty = sku_map[parent_sku]
        try:
            data = _call(TOKEN, "getInventoryProductsData", {
                "inventory_id": SOURCE_INV_ID,
                "products": [str(pid)],
            })
            prod = (data.get("products") or {}).get(str(pid)) or {}
            if not prod:
                raise RuntimeError("getInventoryProductsData zwróciło pusty produkt")

            price_value = _first_price(prod.get("prices"))
            stock_from = _extract_stock_from_source(prod.get("stock"))

            # `getInventoryProductsData` zwraca `images` jako dict {slot: url},
            # ale `addInventoryProduct` wymaga URL-i z prefixem `url:` (inaczej
            # traktuje wartość jako base64 payload → "Invalid data format for images").
            raw_images = prod.get("images") or {}
            if isinstance(raw_images, dict):
                try:
                    urls = [raw_images[k] for k in sorted(raw_images.keys(), key=lambda x: int(x))]
                except (TypeError, ValueError):
                    urls = list(raw_images.values())
            elif isinstance(raw_images, list):
                urls = raw_images
            else:
                urls = []

            def _prefix(u: str) -> str:
                if not isinstance(u, str) or not u:
                    return ""
                return u if u.startswith("url:") else f"url:{u}"

            images_list = [_prefix(u) for u in urls if u]

            params = {
                "inventory_id": new_inv_id,
                "product_id": None,
                "sku": parent_sku,
                "ean": prod.get("ean") or "",
                "weight": prod.get("weight") or 0,
                "height": prod.get("height") or 0,
                "width": prod.get("width") or 0,
                "length": prod.get("length") or 0,
                "tax_rate": prod.get("tax_rate") if prod.get("tax_rate") is not None else 0,
                # UWAGA: category_id z 52173 nie istnieje w nowym inventory 111048
                # (kategorie BL są per-inventory). Dla piaskownicy zerujemy
                # kategorię i manufacturera — do testów OLX to nieistotne.
                "category_id": 0,
                "manufacturer_id": 0,
                "text_fields": prod.get("text_fields") or {},
                "images": images_list,
                "prices": {str(PRICE_GROUP_ID): price_value},
                "stock": {TARGET_WAREHOUSE: stock_from},
            }
            add_resp = _call(TOKEN, "addInventoryProduct", params)
            new_pid = add_resp.get("product_id")
            print(f"  ✓ {parent_sku} → PID {new_pid} (cena {price_value}, stock {stock_from})")
            added.append((parent_sku, new_pid))
        except Exception as e:
            msg = str(e)
            print(f"  ✗ {parent_sku}: {msg}")
            failed.append((parent_sku, msg))

    # ---- KROK 5: Raport -----------------------------------------------------
    print("\n=== RAPORT ===")
    print(f"Nowy inventory_id: {new_inv_id}")
    print(f"Skopiowano: {len(added)}/{len(selected)} (skipped istniejących: {len(skipped)})")
    if added:
        print("Added SKU → PID:")
        for sku, pid in added:
            print(f"  {sku} → {pid}")
    if skipped:
        print("Skipped (już były w targecie) SKU → PID:")
        for sku, pid in skipped:
            print(f"  {sku} → {pid}")
    if failed:
        print(f"Failed ({len(failed)}):")
        for sku, msg in failed:
            print(f"  {sku}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
