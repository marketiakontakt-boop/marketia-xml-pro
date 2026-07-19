"""OLX categories + attributes cache (SQLite, TTL 7 days).

Typical flow:
    1. `refresh_categories(client, conn)` — populate `olx_categories`.
    2. `find_category_by_name(conn, "Meble ogrodowe")` — help user pick cat_id.
    3. `refresh_attributes(client, conn, cat_id)` — fetch per-category attributes.
    4. `get_required_attributes(conn, cat_id)` — show as form fields in GUI.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.cache.sqlite_cache import (
    get_olx_category_attributes,
    save_olx_attribute,
    save_olx_category,
)

if TYPE_CHECKING:
    from app.olx.api import OLXClient


CACHE_TTL_DAYS = 7


def _walk_categories(nodes: list[dict], parent_id: int | None, path_prefix: str) -> list[dict]:
    """Flatten OLX category tree into rows {id, parent_id, name, path}."""
    rows: list[dict] = []
    for node in nodes:
        cid = int(node["id"])
        name = str(node.get("name", "")).strip()
        path = f"{path_prefix} > {name}" if path_prefix else name
        rows.append({"id": cid, "parent_id": parent_id, "name": name, "path": path})
        children = node.get("children") or []
        if children:
            rows.extend(_walk_categories(children, cid, path))
    return rows


def refresh_categories(client: "OLXClient", conn: sqlite3.Connection) -> int:
    """GET /categories, walk the tree, upsert every node. Returns row count."""
    payload = client.get("categories")
    # Response shape: {"data": [{"id": .., "name": .., "children": [...]}]}
    roots = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(roots, list):
        raise ValueError(f"Unexpected /categories payload: {type(roots)}")

    rows = _walk_categories(roots, parent_id=None, path_prefix="")
    for r in rows:
        save_olx_category(conn, r["id"], r["parent_id"], r["name"], r["path"])
    return len(rows)


def refresh_attributes(
    client: "OLXClient", conn: sqlite3.Connection, cat_id: int
) -> int:
    """GET /categories/{id}/attributes, upsert to cache. Returns row count."""
    payload = client.get(f"categories/{cat_id}/attributes")
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError(f"Unexpected /attributes payload: {type(items)}")

    for item in items:
        code = str(item.get("code", "")).strip()
        if not code:
            continue
        save_olx_attribute(
            conn,
            cat_id=cat_id,
            code=code,
            label=str(item.get("label", code)),
            required=bool(item.get("required", False)),
            attr_type=item.get("type"),
            options=item.get("values") or item.get("options") or [],
        )
    return len(items)


def get_category_by_path(conn: sqlite3.Connection, path: str) -> int | None:
    """Exact path lookup, e.g. 'Dom > Meble ogrodowe'."""
    row = conn.execute(
        "SELECT id FROM olx_categories WHERE path = ?", (path,)
    ).fetchone()
    return int(row["id"]) if row else None


def find_category_by_name(conn: sqlite3.Connection, name: str, limit: int = 20) -> list[dict]:
    """LIKE search on name (case-insensitive)."""
    pattern = f"%{name.strip()}%"
    rows = conn.execute(
        "SELECT id, parent_id, name, path FROM olx_categories "
        "WHERE name LIKE ? COLLATE NOCASE ORDER BY LENGTH(path) LIMIT ?",
        (pattern, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_required_attributes(conn: sqlite3.Connection, cat_id: int) -> list[dict]:
    """Return only required attributes for a category."""
    return [a for a in get_olx_category_attributes(conn, cat_id) if a["required"]]


def cache_is_stale(conn: sqlite3.Connection, cat_id: int | None = None) -> bool:
    """True if newest fetched_at is older than CACHE_TTL_DAYS. cat_id=None → categories."""
    if cat_id is None:
        row = conn.execute(
            "SELECT MAX(fetched_at) AS ts FROM olx_categories"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT MAX(fetched_at) AS ts FROM olx_category_attributes WHERE cat_id = ?",
            (cat_id,),
        ).fetchone()
    if not row or not row["ts"]:
        return True
    try:
        ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(ts.tzinfo) - ts) > timedelta(days=CACHE_TTL_DAYS)
