"""Skopiuj kategorie z inventory 52173 (MultiStore) → 111048 (OLX Testy) i
przypisz je do 15 rodziców klonów, które zostały wcześniej skopiowane.

Powód: `getInventoryCategories` w BL jest per-inventory. Klon inventory nie
dziedziczy kategorii. Bez `category_id` produkt nie może dostać mapingu na
kategorię OLX (integracja OLX wymaga BL category → OLX category mapping).

Kroki:
  1. Fetch source categories z 52173 (flat, wszystkie parent_id=0 w tym setupie).
  2. Fetch target categories z 111048 (idempotencja — matchujemy po nazwie).
  3. Dla 15 SKU: fetch getInventoryProductsData → weź `category_id` (source).
     Buduj set potrzebnych cat IDs.
  4. Dla każdego brakującego cat_id w target: addInventoryCategory + zapamiętaj
     mapping old_cat_id → new_cat_id.
  5. Dla każdego z 15 SKU w target (111048): znajdź product_id → updateInventoryProduct
     z nowym category_id.

Idempotent: reuse istniejących kategorii w 111048 po nazwie; skip SKU które
już mają category_id != 0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.sync.baselinker_sync import _call, _list_products  # noqa: E402

TOKEN = os.getenv("BASELINKER_TOKEN", "").strip()
SOURCE_INV_ID = 52173
TARGET_INV_ID = 111048

# 15 SKU rodziców klonów w piaskownicy OLX Testy (nazwy identyczne w source i target).
TARGET_SKUS = [
    "10", "100", "1004", "1005", "1162", "1168", "1192", "12",
    "1201", "1202", "1204", "1259", "1261", "1463", "1600",
]


def _fetch_categories(inv_id: int) -> dict[int, dict]:
    """Return {category_id: {name, parent_id}} dla danego inventory."""
    resp = _call(TOKEN, "getInventoryCategories", {"inventory_id": inv_id})
    out: dict[int, dict] = {}
    for c in resp.get("categories", []):
        try:
            cid = int(c["category_id"])
        except (KeyError, TypeError, ValueError):
            continue
        out[cid] = {
            "name": c.get("name", "").strip(),
            "parent_id": int(c.get("parent_id", 0) or 0),
        }
    return out


def _get_product_data(inv_id: int, pid: str) -> dict:
    resp = _call(TOKEN, "getInventoryProductsData", {
        "inventory_id": inv_id,
        "products": [str(pid)],
    })
    return (resp.get("products") or {}).get(str(pid)) or {}


def main() -> int:
    if not TOKEN:
        print("BŁĄD: brak BASELINKER_TOKEN w .env")
        return 1

    # KROK 1: pobierz kategorie z 52173 i 111048 (dla idempotencji) -----------
    print(f"[1] Pobieram kategorie z inventory {SOURCE_INV_ID}…")
    src_cats = _fetch_categories(SOURCE_INV_ID)
    print(f"    Source: {len(src_cats)} kategorii")

    print(f"[1] Pobieram kategorie z inventory {TARGET_INV_ID}…")
    tgt_cats = _fetch_categories(TARGET_INV_ID)
    print(f"    Target: {len(tgt_cats)} kategorii (idempotency baseline)")

    # Reverse mapping po nazwie dla existing target cats
    tgt_name_to_id: dict[str, int] = {c["name"].lower(): cid for cid, c in tgt_cats.items()}

    # KROK 2: mapowanie SKU → source PID + source category_id -----------------
    print(f"\n[2] Pobieram SKU→PID z source {SOURCE_INV_ID}…")
    src_sku_to_info = _list_products(TOKEN, SOURCE_INV_ID)
    src_missing = [s for s in TARGET_SKUS if s not in src_sku_to_info]
    if src_missing:
        print(f"    ⚠️  brakuje w source: {src_missing}")

    print(f"[2] Pobieram SKU→PID z target {TARGET_INV_ID}…")
    tgt_sku_to_info = _list_products(TOKEN, TARGET_INV_ID)
    tgt_missing = [s for s in TARGET_SKUS if s not in tgt_sku_to_info]
    if tgt_missing:
        print(f"    ⚠️  brakuje w target: {tgt_missing}")

    print("\n[3] Pobieram category_id per SKU z source…")
    sku_to_src_catid: dict[str, int] = {}
    for sku in TARGET_SKUS:
        info = src_sku_to_info.get(sku)
        if not info:
            continue
        src_pid = info[0]
        try:
            prod = _get_product_data(SOURCE_INV_ID, src_pid)
        except Exception as e:
            print(f"    ✗ SKU {sku}: fetch data fail: {e}")
            continue
        cat_id_raw = prod.get("category_id") or 0
        try:
            cat_id = int(cat_id_raw)
        except (TypeError, ValueError):
            cat_id = 0
        sku_to_src_catid[sku] = cat_id
        cat_name = src_cats.get(cat_id, {}).get("name", "(nieznana)") if cat_id else "(brak)"
        print(f"    SKU {sku}: source cat={cat_id} '{cat_name}'")

    # KROK 3: identyfikuj wymagane kategorie ---------------------------------
    used_cat_ids = {cid for cid in sku_to_src_catid.values() if cid > 0}
    print(f"\n[4] Wymagane kategorie: {len(used_cat_ids)} unikalnych → {sorted(used_cat_ids)}")

    # KROK 4: dodaj brakujące kategorie do target ----------------------------
    old_to_new_catid: dict[int, int] = {}
    added_cnt = 0
    reused_cnt = 0
    for old_cid in sorted(used_cat_ids):
        src_meta = src_cats.get(old_cid)
        if not src_meta:
            print(f"    ✗ cat {old_cid} nie istnieje w source — skip")
            continue
        name = src_meta["name"]
        # idempotency: reuse jeśli już jest w target po nazwie (case-insensitive)
        existing_cid = tgt_name_to_id.get(name.lower())
        if existing_cid:
            old_to_new_catid[old_cid] = existing_cid
            reused_cnt += 1
            print(f"    = cat '{name}': reuse target cat_id={existing_cid}")
            continue
        try:
            resp = _call(TOKEN, "addInventoryCategory", {
                "inventory_id": TARGET_INV_ID,
                "category_id": None,
                "name": name,
                "parent_id": 0,  # source też ma parent_id=0 dla tych 131 kategorii (flat)
            })
        except Exception as e:
            print(f"    ✗ addInventoryCategory '{name}': {e}")
            continue
        new_cid_raw = resp.get("category_id")
        try:
            new_cid = int(new_cid_raw)
        except (TypeError, ValueError):
            print(f"    ✗ addInventoryCategory '{name}': response bez category_id → {resp}")
            continue
        old_to_new_catid[old_cid] = new_cid
        tgt_name_to_id[name.lower()] = new_cid
        added_cnt += 1
        print(f"    ✓ cat '{name}': added target cat_id={new_cid}")

    print(f"\n    Kategorie: dodano {added_cnt}, reused {reused_cnt} (target has now {len(tgt_name_to_id)})")

    # KROK 5: update produktów w target --------------------------------------
    print(f"\n[5] Update category_id dla {len(TARGET_SKUS)} SKU w target {TARGET_INV_ID}…")
    ok_cnt = 0
    fail_cnt = 0
    skip_cnt = 0
    for sku in TARGET_SKUS:
        info = tgt_sku_to_info.get(sku)
        if not info:
            print(f"    ✗ SKU {sku}: brak w target — skip")
            fail_cnt += 1
            continue
        tgt_pid = info[0]
        old_cat_id = sku_to_src_catid.get(sku, 0)
        new_cat_id = old_to_new_catid.get(old_cat_id) if old_cat_id else None
        if not new_cat_id:
            print(f"    = SKU {sku}: brak mapowania (source cat={old_cat_id}) — skip")
            skip_cnt += 1
            continue
        # Idempotency: sprawdź czy target już ma tę kategorię
        try:
            tgt_prod = _get_product_data(TARGET_INV_ID, tgt_pid)
            existing_cat = int(tgt_prod.get("category_id") or 0)
        except Exception:
            existing_cat = 0
        if existing_cat == new_cat_id:
            print(f"    = SKU {sku} → cat {new_cat_id} (już ustawione, skip)")
            skip_cnt += 1
            continue
        try:
            # BL nie ma `updateInventoryProduct` — upsert przez `addInventoryProduct`
            # z `product_id` powoduje edycję istniejącego produktu.
            _call(TOKEN, "addInventoryProduct", {
                "inventory_id": TARGET_INV_ID,
                "product_id": tgt_pid,
                "category_id": new_cat_id,
            })
            print(f"    ✓ SKU {sku} → cat {new_cat_id} (target PID {tgt_pid})")
            ok_cnt += 1
        except Exception as e:
            print(f"    ✗ SKU {sku}: {e}")
            fail_cnt += 1

    # RAPORT ------------------------------------------------------------------
    print("\n=== RAPORT ===")
    print(f"Kategorii skopiowanych: {added_cnt} (reused {reused_cnt})")
    print(f"SKU updated: {ok_cnt}/{len(TARGET_SKUS)} (skipped: {skip_cnt}, failed: {fail_cnt})")
    return 0 if fail_cnt == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
