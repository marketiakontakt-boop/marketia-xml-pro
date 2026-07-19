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

CREATE TABLE IF NOT EXISTS sku_model_names (
    used_for_sku    TEXT PRIMARY KEY,
    brand           TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    assigned_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS ai_titles (
    sku             TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_eans (
    sku             TEXT NOT NULL,
    ean             TEXT NOT NULL,
    position        INTEGER NOT NULL,
    added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sku, ean)
);

CREATE INDEX IF NOT EXISTS idx_product_eans_sku
    ON product_eans(sku, position);

CREATE TABLE IF NOT EXISTS product_infographics (
    sku          TEXT NOT NULL,
    param_key    TEXT NOT NULL,
    path         TEXT NOT NULL,
    imgbb_url    TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(sku, param_key)
);

CREATE INDEX IF NOT EXISTS idx_product_infographics_sku
    ON product_infographics(sku);

CREATE TABLE IF NOT EXISTS session_state (
    xml_hash TEXT PRIMARY KEY,
    xml_path TEXT NOT NULL,
    state_json TEXT NOT NULL,
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_session_state_saved_at ON session_state(saved_at DESC);

-- OLX integration tables (Faza 1: OAuth + kategorie + oferty)
CREATE TABLE IF NOT EXISTS olx_oauth_tokens (
    client_id     TEXT PRIMARY KEY,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    TIMESTAMP NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS olx_categories (
    id         INTEGER PRIMARY KEY,
    parent_id  INTEGER,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_olx_categories_path ON olx_categories(path);

CREATE TABLE IF NOT EXISTS olx_category_attributes (
    cat_id       INTEGER NOT NULL,
    code         TEXT NOT NULL,
    label        TEXT NOT NULL,
    required     INTEGER NOT NULL DEFAULT 0,
    attr_type    TEXT,
    options_json TEXT,
    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cat_id, code)
);

CREATE TABLE IF NOT EXISTS olx_offers (
    sku          TEXT PRIMARY KEY,
    advert_id    TEXT,
    status       TEXT,
    external_url TEXT,
    error        TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
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
    # Single atomic INSERT...SELECT — version counter computed inside one statement
    conn.execute(
        "INSERT INTO description_versions (sku, version, description_html, quality_score) "
        "SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ? "
        "FROM description_versions WHERE sku = ?",
        (sku, html, quality_score, sku),
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
        "SELECT description_html FROM description_versions WHERE id = ? AND sku = ?",
        (version_id, sku),
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


# --- sku→model_name helpers (no collision suffixes) ---

def get_sku_model_name(conn: sqlite3.Connection, sku: str) -> str | None:
    """Look up model_name for sku — new table first, old table as fallback."""
    row = conn.execute(
        "SELECT model_name FROM sku_model_names WHERE used_for_sku = ?", (sku,)
    ).fetchone()
    if row:
        return row["model_name"]
    row = conn.execute(
        "SELECT model_name FROM used_model_names WHERE used_for_sku = ?", (sku,)
    ).fetchone()
    return row["model_name"] if row else None


def save_sku_model_name(conn: sqlite3.Connection, sku: str, brand: str, model_name: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sku_model_names (used_for_sku, brand, model_name) VALUES (?, ?, ?)",
        (sku, brand, model_name),
    )


# --- AI title helpers ---

def get_ai_title(
    conn: sqlite3.Connection, sku: str, prompt_version: str | None = None
) -> str | None:
    """Zwraca cache'owany tytuł. Jeśli `prompt_version` podana i nie zgadza się z zapisaną,
    zwraca None (traktuje jako stale — cache został wygenerowany starszą wersją prompta).
    """
    row = conn.execute(
        "SELECT title, prompt_version FROM ai_titles WHERE sku = ?", (sku,)
    ).fetchone()
    if not row:
        return None
    if prompt_version and row["prompt_version"] != prompt_version:
        return None
    return row["title"]


def save_ai_title(
    conn: sqlite3.Connection, sku: str, title: str, prompt_version: str = "v1"
) -> None:
    conn.execute(
        """INSERT INTO ai_titles (sku, title, prompt_version)
           VALUES (?, ?, ?)
           ON CONFLICT(sku) DO UPDATE SET
               title          = excluded.title,
               prompt_version = excluded.prompt_version,
               generated_at   = CURRENT_TIMESTAMP""",
        (sku, title, prompt_version),
    )


def clear_ai_titles(conn: sqlite3.Connection, skus: list[str] | None = None) -> int:
    if skus is None:
        cur = conn.execute("DELETE FROM ai_titles")
        return cur.rowcount
    if not skus:
        return 0
    placeholders = ",".join("?" * len(skus))
    cur = conn.execute(
        f"DELETE FROM ai_titles WHERE sku IN ({placeholders})", skus  # noqa: S608
    )
    return cur.rowcount


# --- multi-EAN helpers (for Allegro product matching via clones) ---

def get_extra_eans(conn: sqlite3.Connection, sku: str) -> list[str]:
    """Return additional EANs for a SKU, ordered by position (1, 2, 3, …)."""
    rows = conn.execute(
        "SELECT ean FROM product_eans WHERE sku = ? ORDER BY position",
        (sku,),
    ).fetchall()
    return [r["ean"] for r in rows]


def set_extra_eans(conn: sqlite3.Connection, sku: str, eans: list[str]) -> None:
    """Replace all extra EANs for `sku` atomically. Empty list deletes all."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM product_eans WHERE sku = ?", (sku,))
        for pos, ean in enumerate(eans, start=1):
            conn.execute(
                "INSERT INTO product_eans (sku, ean, position) VALUES (?, ?, ?)",
                (sku, ean, pos),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def clear_extra_eans(conn: sqlite3.Connection, sku: str) -> int:
    cur = conn.execute("DELETE FROM product_eans WHERE sku = ?", (sku,))
    return cur.rowcount


# --- product helpers ---

_CLEARABLE_TABLES = {
    "descriptions":         "Opisy HTML",
    "description_versions": "Historia wersji opisów",
    "products":             "Produkty (metadata)",
    "used_model_names":     "Nazwy modeli (stara tabela)",
    "sku_model_names":      "Nazwy modeli (SKU→model)",
    "batch_state":          "Stan Batch API",
    "product_snapshots":    "Snapshots (diff)",
    "thumbnails":           "Miniatury (cache)",
    "lifestyle_thumbnails": "Lifestyle AI (cache)",
    "ai_titles":            "Tytuły AI (cache)",
    "product_eans":         "Dodatkowe EAN-y (klony)",
    "product_infographics": "Infografiki parametrów (cache)",
    "session_state":        "Sesje GUI (filtry + selekcja per plik XML)",
    "olx_oauth_tokens":     "OLX OAuth tokens",
    "olx_categories":       "OLX kategorie (cache 7 dni)",
    "olx_category_attributes": "OLX atrybuty kategorii (cache)",
    "olx_offers":           "OLX oferty (advert_id + status)",
}


def clear_cache(conn: sqlite3.Connection, tables: list[str] | None = None) -> dict[str, int]:
    """Delete rows from the given tables (default: all clearable tables).

    Returns {table: rows_deleted}.
    """
    targets = tables if tables is not None else list(_CLEARABLE_TABLES.keys())
    result: dict[str, int] = {}
    for tbl in targets:
        if tbl not in _CLEARABLE_TABLES:
            continue
        try:
            cur = conn.execute(f"DELETE FROM {tbl}")  # noqa: S608 — table name from controlled list
            result[tbl] = cur.rowcount
        except Exception:
            result[tbl] = 0
    return result


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


# --- product infographic helpers ---

def save_infographic(
    conn: sqlite3.Connection, sku: str, param_key: str, path: str
) -> None:
    """Upsert path for (sku, param_key); resets imgbb_url so stale URLs don't linger."""
    conn.execute(
        "INSERT OR REPLACE INTO product_infographics (sku, param_key, path, imgbb_url) "
        "VALUES (?, ?, ?, NULL)",
        (sku, param_key, path),
    )


def set_infographic_imgbb(
    conn: sqlite3.Connection, sku: str, param_key: str, url: str
) -> None:
    """Update imgbb_url for an existing (sku, param_key) infographic row."""
    conn.execute(
        "UPDATE product_infographics SET imgbb_url = ? "
        "WHERE sku = ? AND param_key = ?",
        (url, sku, param_key),
    )


def get_infographics(conn: sqlite3.Connection, sku: str) -> list[dict]:
    """Return all infographic rows for `sku` as dicts (param_key, path, imgbb_url)."""
    rows = conn.execute(
        "SELECT param_key, path, imgbb_url FROM product_infographics "
        "WHERE sku = ? ORDER BY param_key",
        (sku,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- session state persistence helpers ---

import hashlib
import json as _json_module


def hash_xml_file(path: str | Path) -> str:
    """Return SHA256 hex of first 100KB + file size — stable identifier for XML file."""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256()
    with p.open("rb") as f:
        h.update(f.read(100_000))
    h.update(str(size).encode())
    return h.hexdigest()[:32]  # 32 chars sufficient


def save_session_state(
    conn: sqlite3.Connection, xml_hash: str, xml_path: str, state: dict
) -> None:
    """Upsert session state as JSON keyed by XML file hash."""
    conn.execute(
        "INSERT INTO session_state (xml_hash, xml_path, state_json, saved_at) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(xml_hash) DO UPDATE SET xml_path=excluded.xml_path, "
        "state_json=excluded.state_json, saved_at=CURRENT_TIMESTAMP",
        (xml_hash, str(xml_path), _json_module.dumps(state, ensure_ascii=False)),
    )


def load_session_state(conn: sqlite3.Connection, xml_hash: str) -> dict | None:
    """Load state dict for given XML hash, or None if not found / corrupt."""
    row = conn.execute(
        "SELECT state_json FROM session_state WHERE xml_hash = ?",
        (xml_hash,),
    ).fetchone()
    if not row:
        return None
    try:
        raw = row["state_json"] if hasattr(row, "keys") else row[0]
        return _json_module.loads(raw)
    except (_json_module.JSONDecodeError, KeyError, IndexError):
        return None


# --- OLX integration helpers ---

def save_olx_token(
    conn: sqlite3.Connection,
    client_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: str,
) -> None:
    """Upsert OLX OAuth token bundle keyed by client_id.

    `expires_at` should be ISO8601 UTC (str) so SQLite CURRENT_TIMESTAMP
    comparisons work naturally.
    """
    conn.execute(
        """
        INSERT INTO olx_oauth_tokens (client_id, access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(client_id) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at    = excluded.expires_at,
            updated_at    = CURRENT_TIMESTAMP
        """,
        (client_id, access_token, refresh_token, expires_at),
    )


def get_olx_token(conn: sqlite3.Connection, client_id: str) -> dict | None:
    """Return {access_token, refresh_token, expires_at} or None."""
    row = conn.execute(
        "SELECT access_token, refresh_token, expires_at FROM olx_oauth_tokens "
        "WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "access_token": row["access_token"],
        "refresh_token": row["refresh_token"],
        "expires_at": row["expires_at"],
    }


def save_olx_category(
    conn: sqlite3.Connection,
    cat_id: int,
    parent_id: int | None,
    name: str,
    path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO olx_categories (id, parent_id, name, path, fetched_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            parent_id  = excluded.parent_id,
            name       = excluded.name,
            path       = excluded.path,
            fetched_at = CURRENT_TIMESTAMP
        """,
        (cat_id, parent_id, name, path),
    )


def save_olx_attribute(
    conn: sqlite3.Connection,
    cat_id: int,
    code: str,
    label: str,
    required: bool,
    attr_type: str | None,
    options: list[dict] | None,
) -> None:
    """Upsert attribute definition. `options` serialized as JSON in options_json."""
    options_json = _json_module.dumps(options or [], ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO olx_category_attributes
            (cat_id, code, label, required, attr_type, options_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cat_id, code) DO UPDATE SET
            label        = excluded.label,
            required     = excluded.required,
            attr_type    = excluded.attr_type,
            options_json = excluded.options_json,
            fetched_at   = CURRENT_TIMESTAMP
        """,
        (cat_id, code, label, 1 if required else 0, attr_type, options_json),
    )


def get_olx_category_attributes(
    conn: sqlite3.Connection, cat_id: int
) -> list[dict]:
    """Return list of attribute rows (dicts) for a category."""
    rows = conn.execute(
        "SELECT code, label, required, attr_type, options_json "
        "FROM olx_category_attributes WHERE cat_id = ? ORDER BY required DESC, code",
        (cat_id,),
    ).fetchall()
    result: list[dict] = []
    for r in rows:
        try:
            opts = _json_module.loads(r["options_json"] or "[]")
        except (_json_module.JSONDecodeError, TypeError):
            opts = []
        result.append({
            "code": r["code"],
            "label": r["label"],
            "required": bool(r["required"]),
            "attr_type": r["attr_type"],
            "options": opts,
        })
    return result


def save_olx_offer(
    conn: sqlite3.Connection,
    sku: str,
    advert_id: str | None,
    status: str,
    external_url: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO olx_offers (sku, advert_id, status, external_url, error, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(sku) DO UPDATE SET
            advert_id    = excluded.advert_id,
            status       = excluded.status,
            external_url = excluded.external_url,
            error        = excluded.error,
            updated_at   = CURRENT_TIMESTAMP
        """,
        (sku, advert_id, status, external_url, error),
    )


def get_olx_offer(conn: sqlite3.Connection, sku: str) -> dict | None:
    row = conn.execute(
        "SELECT sku, advert_id, status, external_url, error, created_at, updated_at "
        "FROM olx_offers WHERE sku = ?",
        (sku,),
    ).fetchone()
    return dict(row) if row else None


def list_recent_sessions(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """List recent sessions [{xml_hash, xml_path, saved_at}, ...] ordered by saved_at DESC."""
    rows = conn.execute(
        "SELECT xml_hash, xml_path, saved_at FROM session_state "
        "ORDER BY saved_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    result: list[dict] = []
    for r in rows:
        if hasattr(r, "keys"):
            result.append({
                "xml_hash": r["xml_hash"],
                "xml_path": r["xml_path"],
                "saved_at": r["saved_at"],
            })
        else:
            result.append({"xml_hash": r[0], "xml_path": r[1], "saved_at": r[2]})
    return result
