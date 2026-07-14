r"""BaseLinker stock sync — propagate parent stock to multi-EAN clones.

Public surface:
    test_connection(token, inventory_id) -> str       # human-readable status
    sync_clones(token, inventory_id, log=None) -> SyncResult

Both are pure functions — no env reading, no I/O setup. GUI and CLI wrap them.

Clones detected by SKU regex `^(.+)-(\d+)$` (e.g. `100-1`, `100-2`). For each
clone, parent SKU is the prefix; clone gets the parent's total stock pushed
via `updateInventoryProductsStock`.
"""
from __future__ import annotations

import json
import re
import ssl
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://api.baselinker.com/connector.php"
CLONE_RE = re.compile(r"^(.+)-(\d+)$")
PAGE_SIZE = 1000


def _make_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that works on macOS Python.

    Python on macOS does not consult the system Keychain for trusted CAs —
    `urlopen` falls back to whatever bundle the interpreter was built with,
    which is often missing intermediates → `CERTIFICATE_VERIFY_FAILED`.
    Pin to `certifi`'s bundle when available; otherwise system default.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _make_ssl_context()


class BaseLinkerError(RuntimeError):
    """Raised when BaseLinker API returns status != SUCCESS."""


@dataclass
class SyncResult:
    total_products: int
    clones_found: int
    parents_resolved: int
    clones_synced: int
    parents_synced: int = 0
    # Diagnostyka — sample dla user-a żeby zrozumieć co sync zobaczył w BL
    sample_skus: list[str] = field(default_factory=list)            # pierwsze 10 SKU z katalogu
    sample_clone_pairs: list[tuple[str, str]] = field(default_factory=list)  # (clone_sku, parent_sku) × 10
    sample_parent_stocks: list[tuple[str, dict[str, int]]] = field(default_factory=list)  # (sku, breakdown) × 5
    warnings: list[str] = field(default_factory=list)
    # Pełny JSON dump pierwszego rodzica z getInventoryProductsData — żeby zobaczyć WSZYSTKIE pola
    raw_parent_dump: str = ""

    def summary(self) -> str:
        if self.clones_found == 0 and self.parents_synced == 0:
            return f"Brak klonów do synchronizacji (przeszukano {self.total_products} produktów)."
        parts = []
        if self.clones_synced:
            parts.append(f"{self.clones_synced} klonów")
        if self.parents_synced:
            parts.append(f"{self.parents_synced} rodziców (upsize z hurtowni)")
        return "Zsynchronizowano " + " + ".join(parts) + f" (katalog: {self.total_products} produktów)."

    def diagnostic_report(self) -> str:
        """Pełny raport tekstowy dla user-a — co sync widział i co zrobił."""
        lines = [f"Katalog: {self.total_products} produktów"]
        if self.sample_skus:
            lines.append(f"\nPrzykładowe SKU w katalogu (pierwsze {len(self.sample_skus)}):")
            for s in self.sample_skus:
                lines.append(f"  • {s}")
        lines.append(f"\nWykrytych klonów (SKU pasujący do regex `^(.+)-(\\d+)$`): {self.clones_found}")
        if self.sample_clone_pairs:
            lines.append(f"Przykłady (klon → rodzic):")
            for clone, parent in self.sample_clone_pairs:
                lines.append(f"  • {clone!r} → {parent!r}")
        lines.append(f"\nRodziców znalezionych w katalogu: {self.parents_resolved}")
        if self.sample_parent_stocks:
            lines.append("Stocki rodziców (z `getInventoryProductsStock`):")
            for sku, breakdown in self.sample_parent_stocks:
                bd = ", ".join(f"{k}={v}" for k, v in breakdown.items()) or "(pusto)"
                lines.append(f"  • {sku}: {bd}")
        lines.append(f"\nKlonów zsynchronizowanych: {self.clones_synced}")
        if self.warnings:
            lines.append("\n⚠️ OSTRZEŻENIA:")
            for w in self.warnings:
                lines.append(f"  • {w}")
        if self.raw_parent_dump:
            lines.append("\n--- PEŁNY JSON pierwszego rodzica (z `getInventoryProductsData`) ---")
            lines.append(self.raw_parent_dump)
            lines.append("--- KONIEC dump ---")
        return "\n".join(lines)


def _call(token: str, method: str, parameters: dict, timeout: int = 30) -> dict:
    body = urlencode({"method": method, "parameters": json.dumps(parameters)}).encode()
    req = Request(API_URL, data=body, headers={"X-BLToken": token})
    with urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        data = json.loads(resp.read())
    if data.get("status") != "SUCCESS":
        raise BaseLinkerError(f"{method}: {data.get('error_message') or data}")
    return data


def test_connection(token: str, inventory_id: int) -> str:
    """Verify credentials by calling a cheap endpoint. Returns human-readable status."""
    if not token:
        raise BaseLinkerError("Brak tokenu BaseLinker")
    # `getInventories` lists all warehouses — confirms token works
    data = _call(token, "getInventories", {})
    inventories = data.get("inventories", [])
    if not inventories:
        raise BaseLinkerError("Token OK, ale brak skonfigurowanych katalogów w BaseLinker")
    matched = next((i for i in inventories if int(i.get("inventory_id", 0)) == inventory_id), None)
    if not matched:
        ids = ", ".join(str(i.get("inventory_id")) for i in inventories)
        raise BaseLinkerError(
            f"Inventory ID {inventory_id} nie znaleziony. Dostępne ID katalogów: {ids}"
        )
    name = matched.get("name") or f"#{inventory_id}"
    return f"OK — katalog '{name}' (ID {inventory_id}) dostępny."


def _list_products(token: str, inventory_id: int) -> dict[str, tuple[str, int]]:
    """Return {sku: (product_id, quantity_total)} for ALL products.

    `quantity` z `getInventoryProductsList` to suma z wszystkich źródeł stocku
    widoczna w panelu BL (włącznie z integracjami hurtowni jak MultiStore).
    To NIE to samo co `getInventoryProductsStock` które zwraca tylko
    wewnętrzne magazyny `bl_*`.
    """
    out: dict[str, tuple[str, int]] = {}
    page = 1
    while True:
        data = _call(token, "getInventoryProductsList", {
            "inventory_id": inventory_id,
            "page": page,
        })
        products = data.get("products", {})
        if not products:
            break
        for pid, info in products.items():
            sku = info.get("sku", "")
            if not sku:
                continue
            qty_raw = info.get("quantity", 0)
            try:
                qty = int(qty_raw) if qty_raw is not None else 0
            except (TypeError, ValueError):
                qty = 0
            out[sku] = (pid, qty)
        if len(products) < PAGE_SIZE:
            break
        page += 1
    return out


def _list_products_all_pids(token: str, inventory_id: int) -> dict[str, list[str]]:
    """Return {sku: [pid, pid, ...]} — WSZYSTKIE PIDs per SKU (BL pozwala na duplikaty).

    Odróżnia się od `_list_products` (który zwraca tylko ostatni PID per SKU).
    Używane w sync gdzie wszystkie duplikaty muszą zostać zaktualizowane.
    """
    out: dict[str, list[str]] = {}
    page = 1
    while True:
        data = _call(token, "getInventoryProductsList", {
            "inventory_id": inventory_id,
            "page": page,
        })
        products = data.get("products", {})
        if not products:
            break
        for pid, info in products.items():
            sku = info.get("sku", "")
            if not sku:
                continue
            out.setdefault(sku, []).append(pid)
        if len(products) < PAGE_SIZE:
            break
        page += 1
    return out


def _flatten_stock(warehouses) -> int:
    """Sum all warehouse counts. BL returns either `{bl_X: int}` (simple) or
    `{variant_id: {bl_X: int}}` (product has variants) — recurse the dict
    branch so both shapes work. Returns total stock across ALL warehouses.
    """
    total = 0
    for v in warehouses.values():
        if isinstance(v, dict):
            total += _flatten_stock(v)
        else:
            try:
                total += int(v)
            except (TypeError, ValueError):
                continue
    return total


# Prefiksy kluczy magazynów BL widziane w API:
# - `bl_*`       — własne magazyny BL użytkownika
# - `warehouse_*` — magazyny zewnętrzne mapowane z integracji hurtowni (np. MultiStore)
# - `blconnect_*` — magazyny z BLConnect (interface między partnerami BL)
_WAREHOUSE_PREFIXES = ("bl_", "warehouse_", "blconnect_")


def _warehouse_breakdown(warehouses) -> dict[str, int]:
    """Return {warehouse_key: total} — preserves per-warehouse split (sums variants).

    BL stock endpoint zwraca albo `{bl_4: 10, warehouse_5010250: 122}` (simple),
    albo `{variant_id: {bl_4: 3}, ...}` (warianty). Akceptuje wszystkie znane
    prefiksy magazynów BL — bl_*, warehouse_*, blconnect_*.
    """
    out: dict[str, int] = {}
    for k, v in (warehouses or {}).items():
        if isinstance(v, dict):
            # warianty — sumuj per warehouse_key
            for wk, qty in _warehouse_breakdown(v).items():
                out[wk] = out.get(wk, 0) + qty
        else:
            try:
                out[k] = out.get(k, 0) + int(v)
            except (TypeError, ValueError):
                continue
    return {k: v for k, v in out.items() if str(k).startswith(_WAREHOUSE_PREFIXES)}


def _get_stocks(token: str, inventory_id: int, product_ids: list[str]) -> dict[str, dict[str, int]]:
    """Return {product_id: {warehouse_key: stock}, ...} — preserves per-warehouse breakdown."""
    if not product_ids:
        return {}
    stocks: dict[str, dict[str, int]] = {}
    for i in range(0, len(product_ids), PAGE_SIZE):
        chunk = product_ids[i : i + PAGE_SIZE]
        data = _call(token, "getInventoryProductsStock", {
            "inventory_id": inventory_id,
            "products": chunk,
        })
        for pid, warehouses in data.get("products", {}).items():
            stocks[pid] = _warehouse_breakdown(warehouses or {})
    return stocks


def _push_stocks(token: str, inventory_id: int, updates: dict[str, dict[str, int]]) -> None:
    """`updates` = {product_id: {warehouse_key: stock, ...}}. Empty dict skips that product.

    KLUCZOWE (probe 2026-07-01, real API test): BL `updateInventoryProductsStock` wymaga
    struktury `products: {pid: {warehouse_key: qty}}` — PŁASKO, bez zagnieżdżenia
    `variant_id`/`stock`. Zweryfikowane read-after-write: response `warnings: {}` i
    read-back pokazuje zapisaną wartość. Wcześniejszy format `{"variant_id": 0, "stock": {..}}`
    powodował warnings że BL nie znajduje magazynów `variant_id` i `stock`
    (traktował klucze jako warehouse ID). Format {pid: {warehouse: qty}} działa.
    """
    if not updates:
        return
    # Filter puste dict-y stock (klony bez znanego warehouse-u)
    filtered = {pid: stock for pid, stock in updates.items() if stock}
    if not filtered:
        return
    # Chunk po PAGE_SIZE
    items = list(filtered.items())
    for i in range(0, len(items), PAGE_SIZE):
        chunk_items = items[i : i + PAGE_SIZE]
        # Płaska struktura: pid → {warehouse_key: qty}. Bez variant_id/stock zagnieżdżeń.
        products_dict = dict(chunk_items)
        _call(token, "updateInventoryProductsStock", {
            "inventory_id": inventory_id,
            "products": products_dict,
        })


def list_inventories(token: str) -> list[dict]:
    """Return [{inventory_id: int, name: str}, …] for all catalogs on the account."""
    if not token:
        raise BaseLinkerError("Brak tokenu BaseLinker")
    data = _call(token, "getInventories", {})
    out = []
    for i in data.get("inventories", []):
        try:
            out.append({"inventory_id": int(i["inventory_id"]), "name": i.get("name", "")})
        except (KeyError, ValueError, TypeError):
            continue
    return out


def list_writable_warehouses(token: str) -> list[dict]:
    """Return warehouses gdzie stock_edition=True (writable przez updateInventoryProductsStock).

    BL rozróżnia warehouses read-only (integracje hurtowni typu `warehouse_*`, `blconnect_*`)
    od writable (`bl_*` które user ma pod kontrolą). Sync musi używać writable.
    """
    data = _call(token, "getInventoryWarehouses", {})
    writable = []
    for w in data.get("warehouses", []):
        if not w.get("stock_edition"):
            continue
        wtype = w.get("warehouse_type", "bl")
        wid = w.get("warehouse_id")
        if wid is None:
            continue
        writable.append({
            "key": f"{wtype}_{wid}",
            "name": w.get("name", ""),
            "is_default": bool(w.get("is_default")),
        })
    return writable


def _pick_target_warehouse_key(writable: list[dict]) -> str | None:
    """Wybierz klucz warehouse do push — preferuj is_default, fallback pierwszy writable."""
    if not writable:
        return None
    for w in writable:
        if w["is_default"]:
            return w["key"]
    return writable[0]["key"]


def _external_stock_sum(warehouses: dict[str, int], exclude_key: str | None) -> int:
    """Suma stocków z warehouses które NIE są `exclude_key` (chroni przed feedback loop).

    Bez wykluczenia target warehouse: sync N-ty czyta bl_58313 (co my zapisaliśmy w sync N-1)
    + warehouse_5010250 (98 z hurtowni) → sum=196 → push 196 → sync N+1: sum=294 → push 294.
    Po 12 syncach: 98 × 12 = 1176. Real world bug wykryty u user-a 2026-07-01.
    """
    return sum(
        max(0, v) for k, v in warehouses.items() if k != exclude_key
    )


def sync_all_clones(
    token: str,
    inventory_ids: list[int] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[tuple[int, str, SyncResult]]:
    """Sync clones across multiple catalogs with cross-catalog parent stock lookup.

    `inventory_ids=None` → auto-discover all catalogs on the account via
    `getInventories`. Returns list of (inv_id, name, result) tuples.

    Cross-katalog: stocki rodziców są zbierane ze WSZYSTKICH katalogów na koncie,
    nie tylko z bieżącego. Jeśli rodzic 620 ma 0 w MultiStore ale 122 w innym
    katalogu, klon 620-1 w MultiStore dostanie 122.
    """
    _log = log or (lambda _msg: None)
    if not token:
        raise BaseLinkerError("Brak tokenu BaseLinker")

    all_inventories = list_inventories(token)
    if not all_inventories:
        raise BaseLinkerError("Konto nie ma skonfigurowanych katalogów BaseLinker")

    if inventory_ids:
        wanted = set(inventory_ids)
        target_inventories = [i for i in all_inventories if i["inventory_id"] in wanted]
        if not target_inventories:
            ids = ", ".join(str(i) for i in inventory_ids)
            raise BaseLinkerError(f"Żaden z podanych Inventory ID nie istnieje: {ids}")
    else:
        target_inventories = all_inventories

    # Faza 1: pre-fetch _list_products dla WSZYSTKICH katalogów raz (cache reuse).
    _log("Skanuję wszystkie katalogi konta…")
    list_cache: dict[int, dict[str, tuple[str, int]]] = {}
    for inv in all_inventories:
        try:
            list_cache[inv["inventory_id"]] = _list_products(token, inv["inventory_id"])
        except Exception as e:
            _log(f"Pomijam katalog {inv['name']}: {e}")
            list_cache[inv["inventory_id"]] = {}

    # Faza 2a: pobierz writable warehouses NAJPIERW — potrzebne w fazie 3 do wykluczenia
    # target_key ze sumy (fix feedback loop 98×12=1176).
    try:
        writable = list_writable_warehouses(token)
        target_key = _pick_target_warehouse_key(writable)
        if target_key:
            _log(f"Target warehouse dla push: {target_key} ({next((w['name'] for w in writable if w['key']==target_key), '?')!r})")
        else:
            _log("⚠️ Brak writable warehouses — sync nie będzie nic zapisywał")
    except Exception as e:
        _log(f"Nie można pobrać writable warehouses: {e}")
        target_key = None

    # Faza 2b: zidentyfikuj parent SKUs z TARGET inventories (te które trzeba sync-ować)
    parent_skus_needed: set[str] = set()
    for inv in target_inventories:
        for sku in list_cache.get(inv["inventory_id"], {}):
            m = CLONE_RE.match(sku)
            if m:
                parent_skus_needed.add(m.group(1))

    # Faza 3: cross-katalog max stock per parent (przeszukaj wszystkie katalogi konta).
    # WYKLUCZAMY target_key ze sumy — tam my piszemy przy poprzednich sync-ach, uwzględnienie
    # spowodowałoby feedback loop (98 + 98 poprzedni push = 196 → 294 → 1176 po 12 syncach).
    _log(f"Zbieram stocki {len(parent_skus_needed)} rodziców z {len(all_inventories)} katalogów…")
    global_parent_stock: dict[str, int] = {}
    for inv in all_inventories:
        inv_id = inv["inventory_id"]
        sku_to_info = list_cache.get(inv_id, {})
        pid_for_sku = {sku: sku_to_info[sku][0] for sku in parent_skus_needed if sku in sku_to_info}
        if not pid_for_sku:
            continue
        try:
            stocks = _get_stocks(token, inv_id, list(pid_for_sku.values()))
        except Exception as e:
            _log(f"[global lookup] stock fetch fail w katalogu {inv_id}: {e}")
            continue
        for parent_sku, pid in pid_for_sku.items():
            warehouses = stocks.get(pid, {})
            warehouse_sum = _external_stock_sum(warehouses, target_key)
            qty_field = sku_to_info[parent_sku][1]
            best = max(warehouse_sum, qty_field, 0)
            if best > global_parent_stock.get(parent_sku, 0):
                global_parent_stock[parent_sku] = best
    _log(f"Znaleziono globalne stocki dla {len(global_parent_stock)} rodziców.")

    # Faza 5: sync per target katalog z global lookup + pre-fetched list cache + target warehouse
    results: list[tuple[int, str, SyncResult]] = []
    for inv in target_inventories:
        inv_id, name = inv["inventory_id"], inv["name"]
        _log(f"=== Katalog '{name}' (ID {inv_id}) ===")
        results.append((inv_id, name, sync_clones(
            token, inv_id, log=_log,
            global_parent_stock=global_parent_stock,
            _prefetched_list=list_cache.get(inv_id),
            _target_warehouse_key=target_key,
        )))
    return results


def sync_stocks_from_source(
    token: str,
    source_inventory_id: int,
    target_inventory_ids: list[int] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[tuple[int, str, SyncResult]]:
    """Kopiuj warehouse breakdown z source katalogu do wszystkich SKU w target katalogach.

    Per SKU w target katalogu:
      - Jeśli SKU istnieje w source → skopiuj warehouse breakdown z source
      - Jeśli SKU NIE istnieje w source → pomijamy
      - Jeśli stan już zgodny → skip (bez noise'a API)

    `target_inventory_ids=None` → auto-discover wszystkie katalogi ≠ source_inventory_id.
    """
    _log = log or (lambda _msg: None)
    if not token:
        raise BaseLinkerError("Brak tokenu BaseLinker")

    all_inventories = list_inventories(token)
    if not all_inventories:
        raise BaseLinkerError("Konto nie ma skonfigurowanych katalogów BaseLinker")

    source_inv = next((i for i in all_inventories if i["inventory_id"] == source_inventory_id), None)
    if not source_inv:
        ids = ", ".join(str(i["inventory_id"]) for i in all_inventories)
        raise BaseLinkerError(
            f"Source Inventory ID {source_inventory_id} nie znaleziony. Dostępne: {ids}"
        )

    if target_inventory_ids:
        wanted = set(target_inventory_ids)
        wanted.discard(source_inventory_id)
        targets = [i for i in all_inventories if i["inventory_id"] in wanted]
        if not targets:
            raise BaseLinkerError(f"Żaden target ID nie istnieje: {target_inventory_ids}")
    else:
        targets = [i for i in all_inventories if i["inventory_id"] != source_inventory_id]

    _log(f"Pobieram source katalog '{source_inv['name']}'…")
    source_sku_to_info = _list_products(token, source_inventory_id)
    _log(f"Source: {len(source_sku_to_info)} produktów.")
    source_pids = [pid for pid, _ in source_sku_to_info.values()]
    _log(f"Pobieram warehouse breakdowns dla source ({len(source_pids)} produktów)…")
    source_stocks = _get_stocks(token, source_inventory_id, source_pids)

    results: list[tuple[int, str, SyncResult]] = []
    for tgt in targets:
        tgt_id, tgt_name = tgt["inventory_id"], tgt["name"]
        _log(f"=== Target '{tgt_name}' (ID {tgt_id}) ===")
        # Zbierz WSZYSTKIE PIDs per SKU w target (BL pozwala na duplikaty SKU — jeden SKU
        # może mieć N PIDs; sync musi zaktualizować wszystkie żeby fix dotarł do każdego).
        # Fix 2026-07-14: Kathay ma 53 SKU z duplikatami (X-242-1 → 2 PIDs itd.).
        tgt_sku_to_pids = _list_products_all_pids(token, tgt_id)
        total_pids = sum(len(pids) for pids in tgt_sku_to_pids.values())
        _log(f"  Target: {len(tgt_sku_to_pids)} SKU / {total_pids} PIDs (duplikaty: {total_pids - len(tgt_sku_to_pids)}).")

        matched: list[tuple[str, str, str]] = []  # (sku, tgt_pid, src_pid) — expanded per PID
        skipped_no_source: list[str] = []
        for sku, tgt_pids in tgt_sku_to_pids.items():
            src_info = source_sku_to_info.get(sku)
            if src_info:
                for tgt_pid in tgt_pids:
                    matched.append((sku, tgt_pid, src_info[0]))
            else:
                skipped_no_source.append(sku)
        _log(f"  Matched: {len(matched)} PIDs, skipped (brak w source): {len(skipped_no_source)} SKU")

        if not matched:
            results.append((tgt_id, tgt_name, SyncResult(
                total_products=len(tgt_sku_to_pids),
                clones_found=0, parents_resolved=0, clones_synced=0,
                sample_skus=list(tgt_sku_to_pids.keys())[:10],
                warnings=[f"Brak dopasowań SKU między source a '{tgt_name}'."],
            )))
            continue

        tgt_pids = [tgt_pid for _, tgt_pid, _ in matched]
        _log(f"  Pobieram breakdowns target ({len(tgt_pids)} pids)…")
        tgt_stocks = _get_stocks(token, tgt_id, tgt_pids)

        updates: dict[str, dict[str, int]] = {}
        unchanged = 0
        for sku, tgt_pid, src_pid in matched:
            src_flat = _flatten_warehouses(source_stocks.get(src_pid, {}))
            tgt_flat = _flatten_warehouses(tgt_stocks.get(tgt_pid, {}))
            if src_flat == tgt_flat:
                unchanged += 1
                continue
            updates[tgt_pid] = src_flat
        _log(f"  Do zmiany: {len(updates)}, bez zmian: {unchanged}")

        if updates:
            _log(f"  Push {len(updates)} update'ów do BaseLinker…")
            _push_stocks(token, tgt_id, updates)

        results.append((tgt_id, tgt_name, SyncResult(
            total_products=len(tgt_sku_to_pids),
            clones_found=0, parents_resolved=len(matched),
            clones_synced=len(updates),
            sample_skus=[sku for sku, _, _ in matched[:10]],
            warnings=[],
        )))
    return results


def _flatten_warehouses(warehouses) -> dict[str, int]:
    """Flatten warehouse breakdown do {warehouse_key: qty}. Sumuj po wariantach."""
    if not warehouses:
        return {}
    flat: dict[str, int] = {}
    for k, v in warehouses.items():
        if isinstance(v, dict):
            for wk, wv in _flatten_warehouses(v).items():
                flat[wk] = flat.get(wk, 0) + int(wv or 0)
        else:
            flat[k] = flat.get(k, 0) + int(v or 0)
    return flat


def _max_across_sources(
    sku: str,
    source_data: dict[int, dict[str, list[str]]],
    source_stocks: dict[int, dict[str, dict[str, int]]],
) -> dict[str, int]:
    """Zwróć MAX qty per warehouse_key across wszystkich source katalogów gdzie SKU występuje.

    Semantic: rodzic ma stan zsumowany w hurtowniach; różne hurtownie mogą raportować
    inaczej dla tego samego produktu — bierzemy najwyższy per klucz warehouse.
    """
    flat_per_key: dict[str, int] = {}
    for inv_id, sku_to_pids in source_data.items():
        for pid in sku_to_pids.get(sku, []):
            wh_flat = _flatten_warehouses(source_stocks.get(inv_id, {}).get(pid, {}))
            for wk, qty in wh_flat.items():
                flat_per_key[wk] = max(flat_per_key.get(wk, 0), int(qty or 0))
    return flat_per_key


def sync_from_wholesale_to_target(
    token: str,
    source_inventory_ids: list[int],
    target_inventory_id: int,
    log: Callable[[str], None] | None = None,
) -> SyncResult:
    """Sync stany z hurtowni source → target katalog (rodzice + klony).

    Workflow per SKU w target:
      1. Znajdź `parent_sku` (SKU bez -N suffix, dla klona; sam SKU dla regular).
      2. W source katalogach szukaj `parent_sku` → weź MAX warehouse breakdown ze wszystkich.
      3. Zapisz stan do target: rodzic PID (jeśli jest) + WSZYSTKIE PIDs klonów `parent_sku-N`.

    Target sync:
      - Regular SKU (bez klonów): kopiuj stan bezpośrednio z source.
      - Klon `PARENT-N`: kopiuj stan RODZICA (`PARENT` w source) do klona.
      - Rodzic `PARENT` (jeśli obecny w target obok klonów): kopiuj bezpośrednio.

    Duplikaty SKU w target: obsługiwane (używa `_list_products_all_pids`).
    Skip: SKU których nie ma w żadnym source katalogu.

    Zwraca SyncResult z liczbą synced PIDs.
    """
    _log = log or (lambda _msg: None)
    if not token:
        raise BaseLinkerError("Brak tokenu BaseLinker")
    if not source_inventory_ids:
        raise BaseLinkerError("Brak source_inventory_ids (hurtownie)")

    # 1. Zbierz source: {inv_id: {sku: [pids...]}}
    _log(f"Skanuję {len(source_inventory_ids)} source katalogów…")
    source_data: dict[int, dict[str, list[str]]] = {}
    for inv_id in source_inventory_ids:
        try:
            source_data[inv_id] = _list_products_all_pids(token, inv_id)
        except Exception as e:
            _log(f"[source {inv_id}] fail: {e}")
            source_data[inv_id] = {}
    total_source_skus = sum(len(m) for m in source_data.values())
    _log(f"Source SKU total (across katalogów): {total_source_skus}")

    # 2. Pobierz stocki source per inv
    source_stocks: dict[int, dict[str, dict[str, int]]] = {}
    for inv_id, sku_to_pids in source_data.items():
        all_pids = [pid for pids in sku_to_pids.values() for pid in pids]
        if not all_pids:
            source_stocks[inv_id] = {}
            continue
        try:
            source_stocks[inv_id] = _get_stocks(token, inv_id, all_pids)
        except Exception as e:
            _log(f"[source {inv_id}] stock fetch fail: {e}")
            source_stocks[inv_id] = {}

    # 3. Pobierz target: {sku: [pids...]}
    tgt_sku_to_pids = _list_products_all_pids(token, target_inventory_id)
    total_pids = sum(len(pids) for pids in tgt_sku_to_pids.values())
    dup_count = total_pids - len(tgt_sku_to_pids)
    _log(f"Target ma {len(tgt_sku_to_pids)} SKU / {total_pids} PIDs ({dup_count} duplikatów)")

    # 4. Match + build updates
    updates: dict[str, dict[str, int]] = {}
    skipped_no_source: list[str] = []
    parents_matched: set[str] = set()
    regulars_matched: set[str] = set()
    for sku, tgt_pids in tgt_sku_to_pids.items():
        m = CLONE_RE.match(sku)
        parent_sku = m.group(1) if m else sku
        stock = _max_across_sources(parent_sku, source_data, source_stocks)
        if not stock:
            skipped_no_source.append(sku)
            continue
        if m:
            parents_matched.add(parent_sku)
        else:
            regulars_matched.add(sku)
        for tgt_pid in tgt_pids:
            updates[tgt_pid] = stock

    matched_skus = len(parents_matched) + len(regulars_matched)
    _log(
        f"Matched: {len(parents_matched)} rodziców + {len(regulars_matched)} regular "
        f"= {matched_skus} SKU do sync"
    )
    _log(f"Skipped (brak w source): {len(skipped_no_source)} SKU")

    # 5. Compare vs target existing, skip no-ops
    all_tgt_pids = [pid for pids in tgt_sku_to_pids.values() for pid in pids]
    tgt_stocks = _get_stocks(token, target_inventory_id, all_tgt_pids) if all_tgt_pids else {}
    real_updates: dict[str, dict[str, int]] = {}
    unchanged = 0
    for pid, stock in updates.items():
        existing = _flatten_warehouses(tgt_stocks.get(pid, {}))
        if existing == stock:
            unchanged += 1
            continue
        real_updates[pid] = stock
    _log(f"Do zmiany: {len(real_updates)} PIDs, bez zmian: {unchanged} (już zgodne)")

    if real_updates:
        _log(f"Push {len(real_updates)} update'ów…")
        _push_stocks(token, target_inventory_id, real_updates)

    warnings: list[str] = []
    if not matched_skus:
        warnings.append("Żaden SKU w target nie ma odpowiednika w source katalogach.")

    return SyncResult(
        total_products=len(tgt_sku_to_pids),
        clones_found=len(parents_matched),
        parents_resolved=matched_skus,
        clones_synced=len(real_updates),
        sample_skus=list(tgt_sku_to_pids.keys())[:10],
        warnings=warnings,
    )


def sync_clones(
    token: str,
    inventory_id: int,
    log: Callable[[str], None] | None = None,
    global_parent_stock: dict[str, int] | None = None,
    _prefetched_list: dict[str, tuple[str, int]] | None = None,
    _target_warehouse_key: str | None = None,
) -> SyncResult:
    """Run one sync pass for ONE catalog. `log` callback receives progress messages.

    `global_parent_stock` (opcjonalne): {parent_sku: best_known_stock_across_catalogs} —
    używane jako fallback gdy lokalne źródła (warehouse breakdown, quantity field) dają 0.
    Sync_all_clones buduje to mapowanie wstępnie.

    `_prefetched_list` (private): cache `_list_products` z sync_all_clones — unika
    powtórnego wywołania API gdy wywoływane jako część batch sync.
    """
    _log = log or (lambda _msg: None)
    _global_parent = global_parent_stock or {}

    if not token:
        raise BaseLinkerError("Brak tokenu BaseLinker")

    if _prefetched_list is not None:
        sku_to_info = _prefetched_list
        _log(f"Używam pre-fetched listy ({len(sku_to_info)} produktów).")
    else:
        _log("Pobieram listę produktów…")
        sku_to_info = _list_products(token, inventory_id)
    total = len(sku_to_info)
    _log(f"Znaleziono {total} produktów w katalogu.")

    warnings: list[str] = []
    sample_skus = list(sku_to_info.keys())[:10]

    # Reverse map dla diagnostyki (pid → sku)
    pid_to_sku = {pid: sku for sku, (pid, _qty) in sku_to_info.items()}
    sku_to_pid = {sku: pid for sku, (pid, _qty) in sku_to_info.items()}
    sku_to_qty = {sku: qty for sku, (_pid, qty) in sku_to_info.items()}

    clone_pairs: list[tuple[str, str, str]] = []  # (clone_sku, parent_sku, parent_pid)
    clones_unmatched: list[tuple[str, str]] = []  # (clone_sku, expected_parent_sku) gdy rodzica brak
    for sku, (pid, _qty) in sku_to_info.items():
        m = CLONE_RE.match(sku)
        if not m:
            continue
        parent_sku = m.group(1)
        parent_pid = sku_to_pid.get(parent_sku)
        if parent_pid:
            clone_pairs.append((sku, parent_sku, parent_pid))
        else:
            clones_unmatched.append((sku, parent_sku))

    if clones_unmatched:
        warnings.append(
            f"{len(clones_unmatched)} klonów nie ma rodzica w katalogu "
            f"(np. {clones_unmatched[0][0]!r} → szukaliśmy {clones_unmatched[0][1]!r})."
        )

    clones_found = len(clone_pairs)
    sample_clone_pairs = [(c, p) for c, p, _ in clone_pairs[:10]]

    if not clones_found:
        if total == 0:
            warnings.append("Katalog jest pusty — wgrałeś już XML z klonami do BaseLinkera?")
        else:
            warnings.append(
                "Sprawdź czy SKU klonów w BL ma postać `SKU-1`, `SKU-2` itp. "
                "(myślnik + cyfra na końcu). Jeśli BL przemienił SKU przy imporcie, sync ich nie wykryje."
            )
        _log("Brak klonów — nic do synchronizacji.")
        return SyncResult(
            total_products=total, clones_found=0, parents_resolved=0, clones_synced=0,
            sample_skus=sample_skus, warnings=warnings,
        )

    clone_to_parent: dict[str, str] = {}
    for clone_sku, _parent_sku, parent_pid in clone_pairs:
        clone_pid = sku_to_pid[clone_sku]
        clone_to_parent[clone_pid] = parent_pid

    parent_pids = sorted(set(clone_to_parent.values()))
    clone_pids = sorted(clone_to_parent.keys())
    _log(f"Pobieram warehouse breakdowns: {len(parent_pids)} rodziców + {len(clone_pids)} klonów…")
    parent_stocks = _get_stocks(token, inventory_id, parent_pids)
    clone_stocks = _get_stocks(token, inventory_id, clone_pids)

    # DIAGNOSTYKA: pobierz PEŁNE dane pierwszego rodzica (wszystkie pola — szukamy gdzie hurtownia trzyma stock)
    raw_parent_dump = ""
    if parent_pids:
        try:
            data_resp = _call(token, "getInventoryProductsData", {
                "inventory_id": inventory_id,
                "products": [parent_pids[0]],
            })
            raw_parent_dump = json.dumps(data_resp, indent=2, ensure_ascii=False)[:5000]
        except Exception as e:
            raw_parent_dump = f"(błąd pobierania getInventoryProductsData: {e})"

    # Sample dla diagnostyki: sku, warehouse breakdown, quantity field
    sample_parent_stocks = [
        (
            pid_to_sku.get(pid, pid),
            {**parent_stocks.get(pid, {}), "_quantity_field": sku_to_qty.get(pid_to_sku.get(pid, ""), 0)},
        )
        for pid in parent_pids[:5]
    ]

    # Strategia rev. 3 (2026-07-01 — po probe real API):
    #   1. Wybierz TARGET warehouse: writable (stock_edition=True), zwykle default.
    #      NIE używaj warehouses rodzica z INNEGO katalogu — mogą nie istnieć w bieżącym
    #      (BL warning: "nie znaleziono magazynu bl_X"). Klucz TARGET otrzymujemy z
    #      `_target_warehouse_key` (przekazany z sync_all_clones albo pobrany teraz).
    #   2. Target stock = max(warehouse_total rodzica, quantity rodzica, global cross-katalog, 0).
    #   3. Push {clone_pid: {target_key: target_stock}}.
    if _target_warehouse_key is None:
        try:
            writable = list_writable_warehouses(token)
            _target_warehouse_key = _pick_target_warehouse_key(writable)
        except Exception:
            _target_warehouse_key = None
    if _target_warehouse_key is None:
        warnings.append(
            "Nie znaleziono writable warehouse (stock_edition=True) — sync nie wie gdzie pisać. "
            "Sprawdź BL → Magazyn → Magazyny — potrzebny co najmniej jeden bl_* z edycją stocku."
        )

    updates: dict[str, dict[str, int]] = {}
    for clone_pid, parent_pid in clone_to_parent.items():
        parent_sku = pid_to_sku.get(parent_pid, "")
        parent_qty_field = sku_to_qty.get(parent_sku, 0)
        parent_warehouses = parent_stocks.get(parent_pid, {})
        global_stock_for_parent = _global_parent.get(parent_sku, 0)

        warehouse_total = _external_stock_sum(parent_warehouses, _target_warehouse_key)
        target_stock = max(warehouse_total, parent_qty_field, global_stock_for_parent, 0)

        if _target_warehouse_key:
            updates[clone_pid] = {_target_warehouse_key: target_stock}

    clones_synced_count = len(updates)  # Count PRZED dodaniem rodziców

    # Sync stocku RODZICÓW — ZAWSZE nadpisuj wartością z hurtowni/global (bez porównania z existing).
    # Guard "target > existing" byłby błędny bo overinflated stocki z poprzednich buggy sync (98×12=1176)
    # by nigdy się nie zresetowały. Rodzic musi odzwierciedlać STAN ZEWNĘTRZNY, nie kumulować.
    parents_synced_count = 0
    if _target_warehouse_key:
        for parent_pid in parent_pids:
            parent_sku = pid_to_sku.get(parent_pid, "")
            parent_qty_field = sku_to_qty.get(parent_sku, 0)
            parent_warehouses = parent_stocks.get(parent_pid, {})
            global_stock_for_parent = _global_parent.get(parent_sku, 0)
            warehouse_total = _external_stock_sum(parent_warehouses, _target_warehouse_key)
            target_stock = max(warehouse_total, parent_qty_field, global_stock_for_parent, 0)
            updates[parent_pid] = {_target_warehouse_key: target_stock}
            parents_synced_count += 1

    actually_synced = clones_synced_count
    total_stock_synced = sum(sum(v.values()) for v in updates.values())

    if actually_synced == 0:
        warnings.append(
            "Żaden klon nie ma znanych warehouses w BL i rodzice też nie — sync nie wie do jakiego "
            "magazynu pchnąć stock. Sprawdź konfigurację katalogu w BL → Magazyn → Magazyny."
        )
    elif total_stock_synced == 0:
        warnings.append(
            "Wszystkie warehouses rodziców są zerowe — klony dostaną 0. "
            "Sprawdź czy hurtownia (50zł/msc) faktycznie zsynchronizowała stock do BL."
        )

    _log(f"Push stocków: {actually_synced} klonów + {parents_synced_count} rodziców (łącznie {total_stock_synced} szt.)…")
    _push_stocks(token, inventory_id, updates)
    _log("Sync zakończony.")
    return SyncResult(
        total_products=total,
        clones_found=clones_found,
        parents_resolved=len(parent_pids),
        clones_synced=actually_synced,
        parents_synced=parents_synced_count,
        sample_skus=sample_skus,
        sample_clone_pairs=sample_clone_pairs,
        sample_parent_stocks=sample_parent_stocks,
        warnings=warnings,
        raw_parent_dump=raw_parent_dump,
    )
