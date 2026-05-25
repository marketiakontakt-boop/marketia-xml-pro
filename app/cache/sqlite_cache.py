"""SQLite cache — Phase 1 covers `products` and `used_model_names`.
Phase 2/3 will add `descriptions` (versions, tokens) and `ean_assignments`.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "cache" / "marketia.db"


DIFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_snapshots (
    sku         TEXT PRIMARY KEY,
    snapshot    TEXT NOT NULL,
    seen_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku             TEXT PRIMARY KEY,
    product_id      TEXT,
    brand           TEXT,
    model_name      TEXT,
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_version    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS used_model_names (
    brand           TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    used_for_sku    TEXT NOT NULL,
    assigned_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (brand, model_name)
);

CREATE INDEX IF NOT EXISTS idx_used_models_brand ON used_model_names(brand);

CREATE TABLE IF NOT EXISTS descriptions (
    sku             TEXT PRIMARY KEY,
    description_html TEXT NOT NULL,
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batch_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    batch_id        TEXT,
    submitted_count INTEGER DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS description_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sku              TEXT NOT NULL,
    version          INTEGER NOT NULL,
    description_html TEXT NOT NULL,
    quality_score    INTEGER DEFAULT -1,
    generated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_desc_ver_sku_ver
    ON description_versions(sku, version);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and lazily create) the cache DB; returns a tuned connection.

    `check_same_thread=False` lets the GUI thread inspect the same connection
    that a worker thread populated. WAL mode handles concurrent readers; for
    concurrent writes the caller must serialize (typically: only one worker
    thread writes at a time — the Phase 1 contract).
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA + DIFF_SCHEMA)


@contextmanager
def open_cache(db_path: Path | str | None = None):
    conn = connect(db_path)
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


# --- model-name dedup helpers (used by ModelNameGenerator) ---

def used_models_for_brand(conn: sqlite3.Connection, brand_key: str) -> set[str]:
    rows = conn.execute(
        "SELECT model_name FROM used_model_names WHERE brand = ?",
        (brand_key,),
    ).fetchall()
    return {r["model_name"] for r in rows}


def reserve_model_name(
    conn: sqlite3.Connection, brand_key: str, model_name: str, sku: str
) -> bool:
    """Try to claim `model_name` for `brand_key` + `sku`. Returns False if taken."""
    try:
        conn.execute(
            "INSERT INTO used_model_names (brand, model_name, used_for_sku) "
            "VALUES (?, ?, ?)",
            (brand_key, model_name, sku),
        )
        return True
    except sqlite3.IntegrityError:
        return False


# --- description cache helpers ---

def get_cached_description(conn: sqlite3.Connection, sku: str) -> str | None:
    row = conn.execute(
        "SELECT description_html FROM descriptions WHERE sku = ?", (sku,)
    ).fetchone()
    return row["description_html"] if row else None


def save_description(conn: sqlite3.Connection, sku: str, html: str, quality_score: int = -1) -> None:
    # Keep descriptions table as current/latest (backward compat)
    conn.execute(
        """
        INSERT INTO descriptions (sku, description_html)
        VALUES (?, ?)
        ON CONFLICT(sku) DO UPDATE SET
            description_html = excluded.description_html,
            generated_at = CURRENT_TIMESTAMP
        """,
        (sku, html),
    )
    # Write new version to description_versions
    next_ver = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM description_versions WHERE sku = ?",
        (sku,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO description_versions (sku, version, description_html, quality_score) "
        "VALUES (?, ?, ?, ?)",
        (sku, next_ver, html, quality_score),
    )


def get_description_history(conn: sqlite3.Connection, sku: str) -> list[dict]:
    """Return all saved versions for sku, newest first."""
    rows = conn.execute(
        "SELECT id, version, quality_score, generated_at "
        "FROM description_versions WHERE sku = ? ORDER BY version DESC",
        (sku,),
    ).fetchall()
    return [dict(r) for r in rows]


def restore_description_version(conn: sqlite3.Connection, sku: str, version_id: int) -> str:
    """Set descriptions[sku] to the HTML from description_versions[version_id]. Returns HTML."""
    row = conn.execute(
        "SELECT description_html FROM description_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Version id={version_id} not found for sku={sku}")
    html = row["description_html"]
    conn.execute(
        "UPDATE descriptions SET description_html = ?, generated_at = CURRENT_TIMESTAMP "
        "WHERE sku = ?",
        (html, sku),
    )
    return html


def save_batch_id(conn: sqlite3.Connection, batch_id: str | None, count: int) -> None:
    conn.execute(
        """
        INSERT INTO batch_state (id, batch_id, submitted_count)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            batch_id = excluded.batch_id,
            submitted_count = excluded.submitted_count,
            updated_at = CURRENT_TIMESTAMP
        """,
        (batch_id, count),
    )


def get_pending_batch_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT batch_id FROM batch_state WHERE id = 1").fetchone()
    return row["batch_id"] if row and row["batch_id"] else None


# --- product helpers ---

def upsert_product(
    conn: sqlite3.Connection,
    sku: str,
    product_id: str,
    brand: str,
    model_name: str,
) -> None:
    conn.execute(
        """
        INSERT INTO products (sku, product_id, brand, model_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(sku) DO UPDATE SET
            product_id  = excluded.product_id,
            brand       = excluded.brand,
            model_name  = excluded.model_name,
            last_version = last_version + 1
        """,
        (sku, product_id, brand, model_name),
    )
