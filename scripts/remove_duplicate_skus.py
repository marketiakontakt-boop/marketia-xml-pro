r"""Usuwa duplikaty SKU w katalogach BL.

Strategia per SKU:
- Zachowaj wersję z wyższym stockiem (suma z bl_* + warehouse_*)
- Jeśli remis (oba 0 lub oba równe) → zachowaj nowszy (wyższy product_id)
- Usuń pozostałe przez `deleteInventoryProduct`

Uruchom z argumentem `--apply` żeby faktycznie usunąć. Bez argumentu = dry-run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from app.sync.baselinker_sync import _call, _warehouse_breakdown  # noqa: E402

TOKEN = os.getenv("BASELINKER_TOKEN", "").strip()
APPLY = "--apply" in sys.argv

INVENTORIES = [52173, 36715]  # MultiStore + Marketia Katalog


def sku_pids_full(inv_id: int) -> dict[str, list[str]]:
    """Return {sku: [pid1, pid2, ...]} — nie aggregate, żeby wyłapać duplikaty."""
    out: dict[str, list[str]] = {}
    page = 1
    while True:
        data = _call(TOKEN, "getInventoryProductsList", {"inventory_id": inv_id, "page": page})
        products = data.get("products", {})
        if not products:
            break
        for pid, info in products.items():
            sku = info.get("sku", "")
            if sku:
                out.setdefault(sku, []).append(pid)
        if len(products) < 1000:
            break
        page += 1
    return out


def stock_of(inv_id: int, pid: str) -> int:
    """Total stock rzeczywisty (z getInventoryProductsData.stock)."""
    d = _call(TOKEN, "getInventoryProductsData", {"inventory_id": inv_id, "products": [pid]})
    prod = d["products"].get(pid, {})
    warehouses = _warehouse_breakdown(prod.get("stock", {}) or {})
    return sum(max(0, v) for v in warehouses.values())


for inv_id in INVENTORIES:
    print(f"\n=== Katalog {inv_id} ===")
    sku_map = sku_pids_full(inv_id)
    dups = {sku: pids for sku, pids in sku_map.items() if len(pids) > 1}
    print(f"Duplikatów: {len(dups)}")

    to_delete: list[tuple[str, str, str, int]] = []  # (sku, pid_delete, pid_keep, stock_deleted)
    for sku, pids in dups.items():
        stocks = {pid: stock_of(inv_id, pid) for pid in pids}
        # Zachowaj pid z max stockiem; tie-break: wyższy pid (nowszy)
        keep_pid = max(pids, key=lambda p: (stocks[p], int(p)))
        for pid in pids:
            if pid != keep_pid:
                to_delete.append((sku, pid, keep_pid, stocks[pid]))

    print(f"Do usunięcia: {len(to_delete)}")
    for sku, pid_del, pid_keep, s_del in to_delete[:10]:
        print(f"  SKU={sku!r}: usuń pid={pid_del} (stock={s_del}) — zachowaj pid={pid_keep}")
    if len(to_delete) > 10:
        print(f"  ... i {len(to_delete) - 10} więcej")

    if APPLY:
        deleted = 0
        for sku, pid_del, _, _ in to_delete:
            try:
                r = _call(TOKEN, "deleteInventoryProduct", {"inventory_id": inv_id, "product_id": pid_del})
                if r.get("status") == "SUCCESS":
                    deleted += 1
                else:
                    print(f"  ❌ {pid_del}: {r}")
            except Exception as e:
                print(f"  ❌ {pid_del}: {e}")
        print(f"Usunięto: {deleted}/{len(to_delete)}")
    else:
        print("(dry-run — uruchom z --apply żeby usunąć)")
