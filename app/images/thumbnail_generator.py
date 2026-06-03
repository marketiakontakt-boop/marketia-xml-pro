"""Thumbnail Generator — download first product image, apply mirror, save JPEG."""
from __future__ import annotations

import asyncio
import io
import sqlite3
from pathlib import Path
from typing import Callable

import aiohttp
from PIL import Image, ImageOps

from app.parser import Product

THUMB_DIR = Path(__file__).resolve().parents[2] / "output" / "thumbnails"
CACHE_DB  = Path(__file__).resolve().parents[2] / "cache" / "marketia.db"


def _build_thumbnail(img_bytes: bytes, mirror: bool) -> Image.Image:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if mirror:
        img = ImageOps.mirror(img)
    return img


async def _download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30), ssl=False
        ) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


def _is_done(conn: sqlite3.Connection, sku: str) -> bool:
    return conn.execute("SELECT 1 FROM thumbnails WHERE sku=?", (sku,)).fetchone() is not None


def _mark_done(conn: sqlite3.Connection, sku: str, path: str) -> None:
    conn.execute("INSERT OR REPLACE INTO thumbnails (sku, path) VALUES (?,?)", (sku, path))
    conn.commit()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS thumbnails (sku TEXT PRIMARY KEY, path TEXT)")
    conn.commit()


async def _generate_batch_async(
    products: list[Product],
    output_dir: Path,
    progress_callback: Callable[[str], None] | None,
    force: bool,
    mirror: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    _ensure_table(conn)

    done = skipped = 0
    total = len(products)

    async with aiohttp.ClientSession() as session:
        for i, product in enumerate(products, 1):
            if cancel_check and cancel_check():
                break
            sku = product.sku
            if progress_callback:
                progress_callback(f"Miniatury: {i}/{total} — {sku}")

            if not force and _is_done(conn, sku):
                skipped += 1
                continue

            images = getattr(product, "images", [])
            if not images:
                continue

            img_bytes = await _download(session, images[0])
            if img_bytes is None:
                if progress_callback:
                    progress_callback(f"Miniatury: {i}/{total} — {sku} (błąd pobierania)")
                continue

            try:
                thumb = _build_thumbnail(img_bytes, mirror)
                out_path = output_dir / f"{sku}.jpg"
                thumb.save(str(out_path), "JPEG", quality=95, optimize=True)
                _mark_done(conn, sku, str(out_path))
                done += 1
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Miniatury: {i}/{total} — {sku} (błąd: {e})")

    conn.close()
    return done, skipped


def generate_thumbnails(
    products: list[Product],
    output_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
    force: bool = False,
    mirror: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    # kept for backward-compat, ignored:
    use_ai: bool = False,
    bg_preset: str = "Biały",
) -> tuple[int, int]:
    """Generate thumbnails (mirror only). Returns (generated, skipped)."""
    out = output_dir or THUMB_DIR
    return asyncio.run(
        _generate_batch_async(products, out, progress_callback, force, mirror, cancel_check)
    )
