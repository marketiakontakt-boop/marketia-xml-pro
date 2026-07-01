"""Unit tests for app.sync.baselinker_sync — mocks urlopen at module level."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from app.sync import baselinker_sync as bls


def _mock_response(payload: dict):
    """Build a fake urlopen() context manager returning a BytesIO of payload JSON."""
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _Resp(json.dumps(payload).encode())


def _patched_urlopen(*responses):
    """Patch bls.urlopen to return responses in order."""
    queue = list(responses)
    def _side_effect(*args, **kwargs):
        if not queue:
            raise AssertionError("urlopen called more times than mocked responses")
        return _mock_response(queue.pop(0))
    return patch.object(bls, "urlopen", side_effect=_side_effect)


# Standardowy mock dla getInventoryWarehouses — zawsze zwraca 1 writable default
_WAREHOUSES_RESP = {
    "status": "SUCCESS",
    "warehouses": [
        {"warehouse_type": "bl", "warehouse_id": 1, "name": "Domyślny", "stock_edition": True, "is_default": True},
    ],
}


def test_call_raises_on_non_success():
    with _patched_urlopen({"status": "ERROR", "error_message": "bad token"}):
        with pytest.raises(bls.BaseLinkerError) as exc:
            bls._call("xxx", "getInventories", {})
    assert "bad token" in str(exc.value)


def test_test_connection_ok():
    payload = {
        "status": "SUCCESS",
        "inventories": [{"inventory_id": 12345, "name": "Główny katalog"}],
    }
    with _patched_urlopen(payload):
        msg = bls.test_connection("tok", 12345)
    assert "Główny katalog" in msg
    assert "12345" in msg


def test_test_connection_missing_inventory():
    payload = {
        "status": "SUCCESS",
        "inventories": [{"inventory_id": 999, "name": "Inny"}],
    }
    with _patched_urlopen(payload):
        with pytest.raises(bls.BaseLinkerError) as exc:
            bls.test_connection("tok", 12345)
    assert "999" in str(exc.value)


def test_test_connection_no_token():
    with pytest.raises(bls.BaseLinkerError):
        bls.test_connection("", 1)


def test_sync_clones_no_clones_present():
    list_resp = {
        "status": "SUCCESS",
        "products": {"100": {"sku": "ABC"}, "101": {"sku": "DEF"}},
    }
    with _patched_urlopen(list_resp):
        result = bls.sync_clones("tok", 1)
    assert result.total_products == 2
    assert result.clones_found == 0
    assert result.clones_synced == 0


def test_sync_clones_happy_path():
    # 1) list: 1 parent (quantity=8) + 2 clones (quantity=0)
    list_resp = {
        "status": "SUCCESS",
        "products": {
            "p100": {"sku": "100", "quantity": 8},
            "p101": {"sku": "100-1", "quantity": 0},
            "p102": {"sku": "100-2", "quantity": 0},
        },
    }
    # 2) get parent warehouse breakdown
    parent_stock_resp = {
        "status": "SUCCESS",
        "products": {"p100": {"bl_1": 5, "bl_2": 3}},  # sum=8
    }
    # 3) get clone warehouse breakdown (both clones mają bl_1=0)
    clone_stock_resp = {
        "status": "SUCCESS",
        "products": {"p101": {"bl_1": 0}, "p102": {"bl_1": 0}},
    }
    # 4) full product data (diagnostic dump)
    data_resp = {"status": "SUCCESS", "products": {"p100": {"sku": "100", "name": "Test"}}}
    # 5) getInventoryWarehouses (writable list)
    # 6) push update OK
    push_resp = {"status": "SUCCESS"}

    with _patched_urlopen(list_resp, parent_stock_resp, clone_stock_resp, data_resp, _WAREHOUSES_RESP, push_resp):
        result = bls.sync_clones("tok", 1)

    assert result.total_products == 3
    assert result.clones_found == 2
    assert result.parents_resolved == 1
    assert result.clones_synced == 2


def test_sync_clones_orphan_clone_skipped():
    """Clone whose parent SKU does not exist in inventory should be skipped."""
    list_resp = {
        "status": "SUCCESS",
        "products": {
            "p100": {"sku": "100", "quantity": 10},
            "p101": {"sku": "100-1", "quantity": 0},
            "p102": {"sku": "999-1", "quantity": 0},  # parent "999" does NOT exist
        },
    }
    parent_stock_resp = {"status": "SUCCESS", "products": {"p100": {"bl_1": 10}}}
    clone_stock_resp = {"status": "SUCCESS", "products": {"p101": {"bl_1": 0}}}
    data_resp = {"status": "SUCCESS", "products": {"p100": {}}}
    push_resp = {"status": "SUCCESS"}

    with _patched_urlopen(list_resp, parent_stock_resp, clone_stock_resp, data_resp, _WAREHOUSES_RESP, push_resp):
        result = bls.sync_clones("tok", 1)
    assert result.clones_found == 1
    assert result.clones_synced == 1


def test_sync_clones_log_callback_invoked():
    list_resp = {"status": "SUCCESS", "products": {"p100": {"sku": "ABC"}}}
    logs: list[str] = []
    with _patched_urlopen(list_resp):
        bls.sync_clones("tok", 1, log=logs.append)
    assert any("produktów" in m for m in logs)


def test_sync_clones_empty_token():
    with pytest.raises(bls.BaseLinkerError):
        bls.sync_clones("", 1)


def test_clone_regex():
    assert bls.CLONE_RE.match("100-1").group(1) == "100"
    assert bls.CLONE_RE.match("SKU-X-3").group(1) == "SKU-X"
    assert bls.CLONE_RE.match("ABC123") is None
    assert bls.CLONE_RE.match("ABC-X") is None  # X not a digit


def test_flatten_stock_simple_shape():
    """Plain {bl_X: int} — sum across warehouses."""
    assert bls._flatten_stock({"bl_1": 5, "bl_2": 3}) == 8
    assert bls._flatten_stock({}) == 0


def test_flatten_stock_variants_shape():
    """Product with variants: {variant_id: {bl_X: int}} — recurse and sum all."""
    warehouses = {
        "1001": {"bl_1": 4, "bl_2": 2},
        "1002": {"bl_1": 7},
    }
    assert bls._flatten_stock(warehouses) == 13


def test_flatten_stock_ignores_garbage():
    """Non-numeric / None values should be skipped without raising."""
    assert bls._flatten_stock({"bl_1": 5, "bl_2": None, "bl_3": "x"}) == 5


def test_warehouse_breakdown_simple():
    """{bl_X: int} → preserve per-warehouse."""
    assert bls._warehouse_breakdown({"bl_4": 155, "bl_5": 0}) == {"bl_4": 155, "bl_5": 0}


def test_warehouse_breakdown_variants_collapsed():
    """{variant_id: {bl_X: int}} → sum per warehouse across variants."""
    result = bls._warehouse_breakdown({
        "1001": {"bl_4": 10, "bl_5": 5},
        "1002": {"bl_4": 3},
    })
    assert result == {"bl_4": 13, "bl_5": 5}


def test_warehouse_breakdown_skips_non_bl_keys():
    """Non-`bl_*` keys (np. variant_id, extra_field) muszą być pominięte."""
    result = bls._warehouse_breakdown({"bl_4": 10, "variant_id": 999, "_extra": "foo"})
    assert result == {"bl_4": 10}


def test_warehouse_breakdown_accepts_warehouse_and_blconnect_prefixes():
    """Hurtownie BL zapisują stock pod `warehouse_*` (MultiStore) i `blconnect_*` (BLConnect),
    nie tylko `bl_*`. Wszystkie 3 prefiksy muszą być akceptowane."""
    result = bls._warehouse_breakdown({
        "bl_55230": 0,
        "bl_58313": -7,
        "warehouse_5010250": 122,    # hurtownia MultiStore
        "blconnect_6200": 5,         # BLConnect partner
        "_extra_field": "ignore",    # nie-warehouse, pomijać
        "variant_id": 999,
    })
    assert result == {"bl_55230": 0, "bl_58313": -7, "warehouse_5010250": 122, "blconnect_6200": 5}


def test_sync_pushes_warehouse_stock_to_clone_bl_keys_only():
    """Regression real-world: rodzic SKU 100 ma `warehouse_5010250=122` (z hurtowni), bl_55230=0, bl_58313=0.
    Klon ma bl_55230=0 (z importu XML). Sync MUSI:
    1. Zsumować warehouses rodzica (122 + 0 + 0 = 122) jako target.
    2. Pchnąć 122 do `bl_55230` klona (NIE do `warehouse_5010250` — BL ignoruje update na warehouse_* keys).
    """
    inv_id = 52173
    list_resp = {
        "status": "SUCCESS",
        "products": {
            "p100": {"sku": "100", "quantity": 0},
            "p101": {"sku": "100-1", "quantity": 0},
        },
    }
    parent_stock_resp = {
        "status": "SUCCESS",
        "products": {"p100": {
            "bl_55230": 0,
            "bl_58313": 0,
            "warehouse_5010250": 122,   # ← hurtownia trzyma tu 122
        }},
    }
    clone_stock_resp = {
        "status": "SUCCESS",
        "products": {"p101": {"bl_55230": 0}},
    }
    data_resp = {"status": "SUCCESS", "products": {"p100": {}}}

    captured = []
    original_call = bls._call

    def spy_call(token, method, parameters, timeout=30):
        if method == "updateInventoryProductsStock":
            captured.append(parameters)
            return {"status": "SUCCESS"}
        if method == "getInventoryWarehouses":
            return _WAREHOUSES_RESP
        return original_call(token, method, parameters, timeout)

    with _patched_urlopen(list_resp, parent_stock_resp, clone_stock_resp, data_resp):
        with patch.object(bls, "_call", side_effect=spy_call):
            result = bls.sync_clones("tok", inv_id)

    assert result.clones_synced == 1
    pushed_dict = captured[0]["products"]
    stock = pushed_dict["p101"]
    # Klon dostaje 122 (suma warehouses rodzica: 0+0+122) do target warehouse bl_1 (writable default)
    assert stock["bl_1"] == 122
    # Sync używa TYLKO target warehouse, nie kluczy z rodzica (warehouse_5010250 nie jest writable)
    assert "warehouse_5010250" not in stock
    assert "bl_55230" not in stock  # klon rodzica miał bl_55230 ale to nie writable target


def test_sync_pushes_to_clone_own_warehouse_with_parent_quantity():
    """Regression: klon dostaje stock w SWOIM warehouse z wartością quantity rodzica.

    Faktyczny BL setup user-a: rodzic 100 ma quantity=155 w panelu, ale warehouse breakdown
    `bl_55230=0, bl_58313=0` (bo hurtownia 50zł/msc zapisuje do pola quantity, nie do bl_*).
    Klon 100-1 ma swój warehouse bl_55230=0. Sync musi pchnąć 155 do bl_55230 klona.
    """
    inv_id = 52173
    list_resp = {
        "status": "SUCCESS",
        "products": {
            "p100": {"sku": "100", "quantity": 155},      # rodzic — panel pokazuje 155
            "p101": {"sku": "100-1", "quantity": 0},      # klon — pusty
        },
    }
    # Rodzic ma quantity=155 ale warehouse breakdown SAME ZERA (hurtownia nie zapisuje do bl_*)
    parent_stock_resp = {
        "status": "SUCCESS",
        "products": {"p100": {"bl_55230": 0, "bl_58313": 0}},
    }
    # Klon ma swój warehouse bl_55230 z 0 (z importu XML)
    clone_stock_resp = {
        "status": "SUCCESS",
        "products": {"p101": {"bl_55230": 0}},
    }

    captured_payloads = []
    original_call = bls._call

    def spy_call(token, method, parameters, timeout=30):
        if method == "updateInventoryProductsStock":
            captured_payloads.append(parameters)
            return {"status": "SUCCESS"}
        if method == "getInventoryWarehouses":
            return _WAREHOUSES_RESP
        return original_call(token, method, parameters, timeout)

    with _patched_urlopen(list_resp, parent_stock_resp, clone_stock_resp):
        with patch.object(bls, "_call", side_effect=spy_call):
            result = bls.sync_clones("tok", inv_id)

    assert result.clones_synced == 1
    assert len(captured_payloads) == 1
    pushed_dict = captured_payloads[0]["products"]
    stock = pushed_dict["p101"]
    # Target = bl_1 (writable default z _WAREHOUSES_RESP). 155 z quantity field.
    assert stock["bl_1"] == 155


def test_sync_pushes_zero_when_parent_quantity_zero():
    """Rodzic z quantity=0 → klon też dostaje 0 (sync = mirror rodzica)."""
    list_resp = {"status": "SUCCESS", "products": {"p100": {"sku": "100", "quantity": 0}, "p101": {"sku": "100-1", "quantity": 0}}}
    parent_stock_resp = {"status": "SUCCESS", "products": {"p100": {"bl_4": 0}}}
    clone_stock_resp = {"status": "SUCCESS", "products": {"p101": {"bl_4": 0}}}

    captured = []
    original_call = bls._call

    def spy_call(token, method, parameters, timeout=30):
        if method == "updateInventoryProductsStock":
            captured.append(parameters)
            return {"status": "SUCCESS"}
        if method == "getInventoryWarehouses":
            return _WAREHOUSES_RESP
        return original_call(token, method, parameters, timeout)

    with _patched_urlopen(list_resp, parent_stock_resp, clone_stock_resp):
        with patch.object(bls, "_call", side_effect=spy_call):
            result = bls.sync_clones("tok", 1)
    assert result.clones_synced == 1
    assert captured[0]["products"]["p101"] == {"bl_1": 0}


def test_sync_clones_handles_variant_parent():
    """Parent has variants — stock should be summed across all variant warehouses."""
    list_resp = {
        "status": "SUCCESS",
        "products": {"p100": {"sku": "100", "quantity": 15}, "p101": {"sku": "100-1", "quantity": 0}},
    }
    parent_stock_resp = {
        "status": "SUCCESS",
        "products": {"p100": {"1001": {"bl_1": 10}, "1002": {"bl_1": 5}}},
    }
    clone_stock_resp = {"status": "SUCCESS", "products": {"p101": {"bl_1": 0}}}
    data_resp = {"status": "SUCCESS", "products": {"p100": {}}}
    push_resp = {"status": "SUCCESS"}
    with _patched_urlopen(list_resp, parent_stock_resp, clone_stock_resp, data_resp, _WAREHOUSES_RESP, push_resp):
        result = bls.sync_clones("tok", 1)
    assert result.clones_synced == 1


def test_list_inventories_returns_normalized_list():
    payload = {
        "status": "SUCCESS",
        "inventories": [
            {"inventory_id": "34107", "name": "Hurtownia MultiStore"},
            {"inventory_id": 34108, "name": "JUMI"},
            {"inventory_id": "bad"},  # corrupted entry — should be skipped
        ],
    }
    with _patched_urlopen(payload):
        result = bls.list_inventories("tok")
    assert result == [
        {"inventory_id": 34107, "name": "Hurtownia MultiStore"},
        {"inventory_id": 34108, "name": "JUMI"},
    ]


def test_sync_all_clones_auto_discovers_all():
    """Cross-katalog: 2 katalogi, każdy ze swoim parent + klonem.
    Nowy flow: list_inventories → cache_list × 2 → global_lookup_stocks × 2 → sync_clones × 2 (parent_stock+clone_stock+data+push)."""
    inv_resp = {
        "status": "SUCCESS",
        "inventories": [
            {"inventory_id": 1, "name": "MultiStore"},
            {"inventory_id": 2, "name": "JUMI"},
        ],
    }
    # Faza 1: cache _list_products dla obu katalogów
    cache_list_1 = {"status": "SUCCESS", "products": {"a": {"sku": "100", "quantity": 5}, "b": {"sku": "100-1", "quantity": 0}}}
    cache_list_2 = {"status": "SUCCESS", "products": {"c": {"sku": "X", "quantity": 10}, "d": {"sku": "X-1", "quantity": 0}, "e": {"sku": "X-2", "quantity": 0}}}
    # Faza 3: global_lookup_stocks dla rodziców per katalog
    global_stock_1 = {"status": "SUCCESS", "products": {"a": {"bl_1": 5}}}
    global_stock_2 = {"status": "SUCCESS", "products": {"c": {"bl_2": 10}}}
    # Faza 4: sync_clones per target — używa pre-fetched list, tylko stocks/data/push
    parent_stock_1 = {"status": "SUCCESS", "products": {"a": {"bl_1": 5}}}
    clone_stock_1 = {"status": "SUCCESS", "products": {"b": {"bl_1": 0}}}
    data_resp_1 = {"status": "SUCCESS", "products": {"a": {}}}
    push_resp_1 = {"status": "SUCCESS"}
    parent_stock_2 = {"status": "SUCCESS", "products": {"c": {"bl_2": 10}}}
    clone_stock_2 = {"status": "SUCCESS", "products": {"d": {"bl_2": 0}, "e": {"bl_2": 0}}}
    data_resp_2 = {"status": "SUCCESS", "products": {"c": {}}}
    push_resp_2 = {"status": "SUCCESS"}

    with _patched_urlopen(
        inv_resp,
        cache_list_1, cache_list_2,                # faza 1
        _WAREHOUSES_RESP,                          # faza 2a (writable — MUSI być przed global lookup)
        global_stock_1, global_stock_2,            # faza 3 (global lookup)
        parent_stock_1, clone_stock_1, data_resp_1, push_resp_1,
        parent_stock_2, clone_stock_2, data_resp_2, push_resp_2,
    ):
        results = bls.sync_all_clones("tok")
    assert [(inv_id, name, r.clones_synced) for inv_id, name, r in results] == [
        (1, "MultiStore", 1),
        (2, "JUMI", 2),
    ]


def test_sync_all_clones_filters_by_explicit_ids():
    """3 katalogi na koncie, sync TYLKO katalogu 2. Global lookup nadal iteruje wszystkie 3."""
    inv_resp = {
        "status": "SUCCESS",
        "inventories": [
            {"inventory_id": 1, "name": "MultiStore"},
            {"inventory_id": 2, "name": "JUMI"},
            {"inventory_id": 3, "name": "Inny"},
        ],
    }
    # Faza 1: cache wszystkich 3 katalogów (puste oprócz JUMI)
    cache_list_1 = {"status": "SUCCESS", "products": {}}
    cache_list_2 = {"status": "SUCCESS", "products": {"c": {"sku": "X", "quantity": 7}, "d": {"sku": "X-1", "quantity": 0}}}
    cache_list_3 = {"status": "SUCCESS", "products": {}}
    # Faza 3: global lookup tylko dla X (jedyny parent SKU); szuka tylko w katalogach gdzie X istnieje (kat 2)
    global_stock_2 = {"status": "SUCCESS", "products": {"c": {"bl_2": 7}}}
    # Faza 4: sync JUMI
    parent_stock = {"status": "SUCCESS", "products": {"c": {"bl_2": 7}}}
    clone_stock = {"status": "SUCCESS", "products": {"d": {"bl_2": 0}}}
    data_resp = {"status": "SUCCESS", "products": {"c": {}}}
    push_resp = {"status": "SUCCESS"}

    with _patched_urlopen(
        inv_resp,
        cache_list_1, cache_list_2, cache_list_3,
        _WAREHOUSES_RESP,
        global_stock_2,
        parent_stock, clone_stock, data_resp, push_resp,
    ):
        results = bls.sync_all_clones("tok", inventory_ids=[2])
    assert len(results) == 1
    assert results[0][0] == 2
    assert results[0][1] == "JUMI"


def test_sync_all_clones_cross_catalog_parent_lookup():
    """Regression usera: rodzic SKU 620 w katalogu MultiStore (id=1) ma 0 (bo nie ma go w hurtowni),
    ale w katalogu "Magazyn" (id=2) ma 13 szt. (stock z innego źródła). Klon 620-1 jest w MultiStore.
    Po cross-katalog lookup klon dostanie 13 (z katalogu 2), nie 0 (z katalogu 1)."""
    inv_resp = {
        "status": "SUCCESS",
        "inventories": [
            {"inventory_id": 1, "name": "MultiStore"},
            {"inventory_id": 2, "name": "Magazyn"},
        ],
    }
    # Faza 1: cache list
    # MultiStore (target): rodzic 620 + klon 620-1
    cache_list_1 = {"status": "SUCCESS", "products": {
        "p620": {"sku": "620", "quantity": 0},          # rodzic 0 w MultiStore
        "p620_1": {"sku": "620-1", "quantity": 0},
    }}
    # Magazyn (not target ale ma rodzica 620 ze stockiem)
    cache_list_2 = {"status": "SUCCESS", "products": {
        "x620": {"sku": "620", "quantity": 13},         # rodzic 13 w Magazynie!
    }}
    # Faza 3: global lookup — szuka rodzica 620 we WSZYSTKICH katalogach
    global_stock_1 = {"status": "SUCCESS", "products": {"p620": {"bl_99": 0}}}
    global_stock_2 = {"status": "SUCCESS", "products": {"x620": {"bl_77": 13}}}
    # Faza 4: sync MultiStore (target)
    parent_stock_1 = {"status": "SUCCESS", "products": {"p620": {"bl_99": 0}}}
    clone_stock_1 = {"status": "SUCCESS", "products": {"p620_1": {"bl_99": 0}}}
    data_resp = {"status": "SUCCESS", "products": {"p620": {}}}
    push_resp = {"status": "SUCCESS"}

    captured = []
    original_call = bls._call
    def spy_call(token, method, parameters, timeout=30):
        if method == "updateInventoryProductsStock":
            captured.append(parameters)
            return {"status": "SUCCESS"}
        if method == "getInventoryWarehouses":
            return _WAREHOUSES_RESP
        return original_call(token, method, parameters, timeout)

    with _patched_urlopen(
        inv_resp,
        cache_list_1, cache_list_2,
        global_stock_1, global_stock_2,
        parent_stock_1, clone_stock_1, data_resp,
    ):
        # spy_call obsługuje getInventoryWarehouses osobno
        with patch.object(bls, "_call", side_effect=spy_call):
            results = bls.sync_all_clones("tok", inventory_ids=[1])

    # Klon 620-1 w MultiStore dostaje 13 szt. (cross-katalog z Magazynu) do bl_1 (target)
    assert len(captured) == 1
    pushed_dict = captured[0]["products"]
    assert pushed_dict["p620_1"]["bl_1"] == 13


def test_sync_all_clones_unknown_id_raises():
    inv_resp = {"status": "SUCCESS", "inventories": [{"inventory_id": 1, "name": "A"}]}
    with _patched_urlopen(inv_resp):
        with pytest.raises(bls.BaseLinkerError) as exc:
            bls.sync_all_clones("tok", inventory_ids=[99])
    assert "99" in str(exc.value)


def test_list_products_paginates_past_page_size():
    """1050 products → 2 pages (1000 + 50). Pagination must aggregate both."""
    # First page: 1000 products (sku_0001 .. sku_1000)
    page1 = {
        "status": "SUCCESS",
        "products": {f"p{i:04}": {"sku": f"sku_{i:04}"} for i in range(1, bls.PAGE_SIZE + 1)},
    }
    # Second page: 50 products (sku_1001 .. sku_1050)
    page2 = {
        "status": "SUCCESS",
        "products": {f"p{i:04}": {"sku": f"sku_{i:04}"} for i in range(bls.PAGE_SIZE + 1, bls.PAGE_SIZE + 51)},
    }
    with _patched_urlopen(page1, page2):
        result = bls.sync_clones("tok", 1)
    assert result.total_products == bls.PAGE_SIZE + 50  # both pages aggregated
