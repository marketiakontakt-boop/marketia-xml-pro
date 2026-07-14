"""ImgBB image upload — free tier, 32MB limit, returns permanent URL.

Requires IMGBB_API_KEY in .env.
Caches uploads in SQLite to avoid re-uploading.
"""
from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path

import httpx

from app.cache.sqlite_cache import open_cache

API_URL = "https://api.imgbb.com/1/upload"


def _ensure_table(conn: sqlite3.Connection):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS imgbb_uploads (
            sku  TEXT PRIMARY KEY,
            url  TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()


def get_cached_url(conn: sqlite3.Connection, sku: str) -> str | None:
    row = conn.execute("SELECT url FROM imgbb_uploads WHERE sku=?", (sku,)).fetchone()
    return row["url"] if row else None


def save_upload(conn: sqlite3.Connection, sku: str, url: str):
    conn.execute(
        "INSERT OR REPLACE INTO imgbb_uploads (sku, url) VALUES (?,?)", (sku, url)
    )
    conn.commit()


def upload_image(sku: str, image_path: Path, force: bool = False) -> str | None:
    """Upload image to ImgBB. Returns public URL or None on failure."""
    api_key = os.getenv("IMGBB_API_KEY", "").strip()
    if not api_key:
        return None

    with open_cache() as conn:
        _ensure_table(conn)
        if not force:
            cached = get_cached_url(conn, sku)
            if cached:
                return cached

        if not image_path.exists():
            return None

        img_b64 = base64.b64encode(image_path.read_bytes()).decode()
        try:
            resp = httpx.post(
                API_URL,
                data={"key": api_key, "image": img_b64, "name": sku},
                timeout=30,
            )
            data = resp.json()
            if data.get("success"):
                url = data["data"]["url"]
                save_upload(conn, sku, url)
                return url
        except Exception:
            pass
        return None


def _upload_file_raw(api_key: str, image_path: Path, name: str) -> str | None:
    """Upload a file to ImgBB without cache lookup; returns URL or None."""
    if not image_path.exists():
        return None
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()
    try:
        resp = httpx.post(
            API_URL,
            data={"key": api_key, "image": img_b64, "name": name},
            timeout=30,
        )
        data = resp.json()
        if data.get("success"):
            return data["data"]["url"]
    except Exception:
        pass
    return None


def upload_infographics(
    products,
    output_dir: Path,
    progress_callback=None,
    cancel_check=None,
) -> int:
    """Upload every cached infographic to ImgBB; save URL back into SQLite cache.

    Iterates products, queries `get_infographics(sku)` per product, uploads each
    row whose `imgbb_url` is empty, and persists the returned URL via
    `set_infographic_imgbb`. Returns number of successful uploads.
    """
    from app.cache.sqlite_cache import get_infographics, set_infographic_imgbb

    api_key = os.getenv("IMGBB_API_KEY", "").strip()
    if not api_key:
        if progress_callback:
            progress_callback("ImgBB: brak IMGBB_API_KEY w .env — pomijam upload.")
        return 0

    output_dir = Path(output_dir)
    uploaded = 0

    with open_cache() as conn:
        for i, p in enumerate(products, 1):
            if cancel_check and cancel_check():
                break
            infos = get_infographics(conn, p.sku)
            if not infos:
                continue
            for info in infos:
                if info.get("imgbb_url"):
                    continue  # already uploaded — skip
                path = Path(info["path"])
                if not path.exists():
                    # fall back to conventional location if stored path is stale
                    path = output_dir / path.name
                    if not path.exists():
                        continue
                name = f"{p.sku}_{info['param_key']}"
                if progress_callback:
                    progress_callback(f"ImgBB infografiki: {i} — {name}")
                url = _upload_file_raw(api_key, path, name)
                if url:
                    set_infographic_imgbb(conn, p.sku, info["param_key"], url)
                    uploaded += 1
    return uploaded


def upload_thumbnails(
    products,
    thumb_dir: Path,
    progress_callback=None,
    cancel_check=None,
) -> int:
    """Upload all generated thumbnails to ImgBB and set product.thumbnail_url.

    Returns count of successful uploads.
    """
    api_key = os.getenv("IMGBB_API_KEY", "").strip()
    if not api_key:
        if progress_callback:
            progress_callback("ImgBB: brak IMGBB_API_KEY w .env — pomijam upload.")
        return 0

    uploaded = 0
    for i, p in enumerate(products, 1):
        if cancel_check and cancel_check():
            break
        path = thumb_dir / f"{p.sku}_lifestyle.jpg"
        if not path.exists():
            path = thumb_dir / f"{p.sku}.jpg"
        if not path.exists():
            continue
        if progress_callback:
            progress_callback(f"ImgBB upload: {i} — {p.sku}")
        url = upload_image(p.sku, path)
        if url:
            p.thumbnail_url = url
            uploaded += 1
    return uploaded
