"""Phase 4 — Thumbnail Generator.

Opcja A (Pro White):
  - rembg background removal → white 1200×1200
  - Alpha-trim → scale to 75% fill → center-top
  - Scale copy at 30% → bottom-right corner  (KanzaSklep "scale trick")
  - Soft drop shadow on final canvas
  - Output: output/thumbnails/{sku}.jpg @ q=95

Async download via aiohttp; SQLite-backed skip for already-done SKUs.
"""
from __future__ import annotations

import asyncio
import io
import sqlite3
from pathlib import Path
from typing import Callable

import aiohttp
from PIL import Image, ImageFilter

from app.parser import Product

CANVAS = 1200
FILL_MAIN = 0.75       # main product occupies this fraction of canvas
FILL_MINI = 0.28       # small copy fraction of canvas
SHADOW_RADIUS = 20
SHADOW_OPACITY = 35    # 0-255 — subtle, like KanzaSklep

THUMB_DIR = Path(__file__).resolve().parents[2] / "output" / "thumbnails"
CACHE_DB = Path(__file__).resolve().parents[2] / "cache" / "marketia.db"

_rembg_session = None


def _get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        from rembg import new_session
        _rembg_session = new_session("u2net")
    return _rembg_session


def _remove_bg(img_bytes: bytes) -> Image.Image:
    from rembg import remove
    result = remove(img_bytes, session=_get_rembg_session())
    return Image.open(io.BytesIO(result)).convert("RGBA")


def _alpha_trim(rgba: Image.Image) -> Image.Image:
    bbox = rgba.getbbox()
    if bbox is None:
        return rgba
    return rgba.crop(bbox)


def _scale_to_fill(rgba: Image.Image, fill: float) -> Image.Image:
    target_px = int(CANVAS * fill)
    w, h = rgba.size
    scale = target_px / max(w, h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return rgba.resize((new_w, new_h), Image.LANCZOS)


def _add_drop_shadow(canvas: Image.Image, product_rgba: Image.Image, pos: tuple[int, int]) -> Image.Image:
    """Draw soft shadow on canvas at pos before compositing product."""
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    # Extract alpha mask of product
    alpha_mask = product_rgba.split()[3]
    shadow_mask = Image.new("RGBA", product_rgba.size, (0, 0, 0, SHADOW_OPACITY))
    shadow_mask.putalpha(alpha_mask)
    shadow_layer.paste(shadow_mask, pos)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(SHADOW_RADIUS))
    canvas = Image.alpha_composite(canvas, shadow_layer)
    return canvas


def _build_thumbnail_a(img_bytes: bytes) -> Image.Image:
    rgba = _remove_bg(img_bytes)
    rgba = _alpha_trim(rgba)

    main = _scale_to_fill(rgba, FILL_MAIN)
    mini = _scale_to_fill(rgba, FILL_MINI)

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 255))

    # Main product: center-x, slightly above center-y
    mx = (CANVAS - main.width) // 2
    my = max(20, (CANVAS - main.height) // 2 - 40)
    canvas = _add_drop_shadow(canvas, main, (mx, my))
    canvas.alpha_composite(main, (mx, my))

    # Mini copy: bottom-right with padding
    pad = 30
    sx = CANVAS - mini.width - pad
    sy = CANVAS - mini.height - pad
    canvas = _add_drop_shadow(canvas, mini, (sx, sy))
    canvas.alpha_composite(mini, (sx, sy))

    return canvas.convert("RGB")


async def _download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    try:
        # ssl=False: some product CDNs use self-signed certs
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30), ssl=False
        ) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


def _is_done(conn: sqlite3.Connection, sku: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM thumbnails WHERE sku=?", (sku,)
    ).fetchone()
    return row is not None


def _mark_done(conn: sqlite3.Connection, sku: str, path: str):
    conn.execute(
        "INSERT OR REPLACE INTO thumbnails (sku, path) VALUES (?,?)",
        (sku, path),
    )
    conn.commit()


def _ensure_table(conn: sqlite3.Connection):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS thumbnails (sku TEXT PRIMARY KEY, path TEXT)"
    )
    conn.commit()


async def _generate_batch_async(
    products: list[Product],
    output_dir: Path,
    progress_callback: Callable[[str], None] | None,
    force: bool,
) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    _ensure_table(conn)

    done = 0
    skipped = 0
    total = len(products)

    async with aiohttp.ClientSession() as session:
        for i, product in enumerate(products, 1):
            sku = product.sku
            if progress_callback:
                progress_callback(f"Miniatury: {i}/{total} — {sku}")

            if not force and _is_done(conn, sku):
                skipped += 1
                continue

            images = getattr(product, "images", [])
            if not images:
                continue

            url = images[0]
            img_bytes = await _download(session, url)
            if img_bytes is None:
                if progress_callback:
                    progress_callback(f"Miniatury: {i}/{total} — {sku} (błąd pobierania)")
                continue

            try:
                thumb = _build_thumbnail_a(img_bytes)
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
) -> tuple[int, int]:
    """Generate Option A thumbnails for all products with images.

    Returns:
        (generated, skipped) counts
    """
    out = output_dir or THUMB_DIR
    return asyncio.run(
        _generate_batch_async(products, out, progress_callback, force)
    )
