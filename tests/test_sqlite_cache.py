import pytest
from app.cache.sqlite_cache import (
    open_cache,
    save_description,
    get_cached_description,
    get_description_history,
    restore_description_version,
)

@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    with open_cache(db) as c:
        yield c

def test_save_description_creates_version(conn):
    save_description(conn, "SKU-001", "<p>v1</p>", quality_score=7)
    history = get_description_history(conn, "SKU-001")
    assert len(history) == 1
    assert history[0]["version"] == 1
    assert history[0]["quality_score"] == 7

def test_second_save_increments_version(conn):
    save_description(conn, "SKU-001", "<p>v1</p>", quality_score=6)
    save_description(conn, "SKU-001", "<p>v2</p>", quality_score=8)
    history = get_description_history(conn, "SKU-001")
    assert len(history) == 2
    assert history[0]["version"] == 2  # DESC order — newest first
    assert history[1]["version"] == 1

def test_restore_version_updates_current(conn):
    save_description(conn, "SKU-001", "<p>v1</p>", quality_score=5)
    save_description(conn, "SKU-001", "<p>v2</p>", quality_score=9)
    history = get_description_history(conn, "SKU-001")
    old_version_id = history[1]["id"]  # v1
    html = restore_description_version(conn, "SKU-001", old_version_id)
    assert html == "<p>v1</p>"
    assert get_cached_description(conn, "SKU-001") == "<p>v1</p>"

def test_get_description_history_empty(conn):
    assert get_description_history(conn, "UNKNOWN") == []
