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

    # Faza 2: zidentyfikuj parent SKUs z TARGET inventories (te które trzeba sync-ować)
    parent_skus_needed: set[str] = set()
    for inv in target_inventories:
        for sku in list_cache.get(inv["inventory_id"], {}):
            m = CLONE_RE.match(sku)
            if m:
                parent_skus_needed.add(m.group(1))

    # Faza 3: cross-katalog max stock per parent (przeszukaj wszystkie katalogi konta)
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
            warehouse_sum = sum(max(0, v) for v in warehouses.values())
            qty_field = sku_to_info[parent_sku][1]
            best = max(warehouse_sum, qty_field, 0)
            if best > global_parent_stock.get(parent_sku, 0):
                global_parent_stock[parent_sku] = best
    _log(f"Znaleziono globalne stocki dla {len(global_parent_stock)} rodziców.")

    # Faza 4: pobierz writable warehouses — target dla wszystkich push-ów
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

        warehouse_total = sum(max(0, v) for v in parent_warehouses.values())
        target_stock = max(warehouse_total, parent_qty_field, global_stock_for_parent, 0)

        if _target_warehouse_key:
            updates[clone_pid] = {_target_warehouse_key: target_stock}

    clones_synced_count = len(updates)  # Count PRZED dodaniem rodziców

    # Sync stocku RODZICÓW też — z global cross-katalog do writable warehouse w tym katalogu.
    # Bez tego rodzic w Marketia Katalog / Allegro Asortyment miałby 0 mimo że hurtownia
    # ma stock w MultiStore. Global lookup wybiera najwyższą wartość ze wszystkich katalogów.
    parents_synced_count = 0
    if _target_warehouse_key:
        for parent_pid in parent_pids:
            parent_sku = pid_to_sku.get(parent_pid, "")
            parent_qty_field = sku_to_qty.get(parent_sku, 0)
            parent_warehouses = parent_stocks.get(parent_pid, {})
            global_stock_for_parent = _global_parent.get(parent_sku, 0)
            warehouse_total = sum(max(0, v) for v in parent_warehouses.values())
            target_stock = max(warehouse_total, parent_qty_field, global_stock_for_parent, 0)
            # Nie nadpisuj rodzica jeśli w bieżącym katalogu MA już stock w target warehouse większy
            # od global (żeby nie nadpisać hurtowni jej samej wartością).
            existing_in_target = parent_warehouses.get(_target_warehouse_key, 0)
            if target_stock > existing_in_target:
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
