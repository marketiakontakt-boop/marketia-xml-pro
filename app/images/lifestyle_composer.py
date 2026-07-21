"""AI Lifestyle Thumbnail Generator.

Pipeline per product:
  1. Download original image (or use cached thumbnail)
  2. rembg — remove background → RGBA cutout
  3. Flux Pro (fal.ai) — generate scene background matching brand / product type
  4. Composite product cutout onto AI background at natural floor position
  5. Save as output/thumbnails/{sku}_lifestyle.jpg

Background scenes include contextual elements so the product looks naturally
placed in a real-world setting. No persons in thumbnails — Allegro regulation.
"""
from __future__ import annotations

import io
import os
import sqlite3
from pathlib import Path
from typing import Callable

import aiohttp
import asyncio
from PIL import Image, ImageFilter, ImageOps

from app.parser.normalizer import Product

CANVAS = 1200
PRODUCT_FILL = 0.60  # product occupies 60 % of canvas height

THUMB_DIR = Path(__file__).resolve().parents[2] / "output" / "thumbnails"
CACHE_DB = Path(__file__).resolve().parents[2] / "cache" / "marketia.db"

# ---------------------------------------------------------------------------
# Brand → lifestyle scene prompts (Imagen 4 background — no product visible)
# ---------------------------------------------------------------------------

_BRAND_SCENES: dict[str, list[str]] = {
    "homestein": [
        (
            "Modern Scandinavian dining room interior. Warm light oak parquet floor, "
            "white walls, large window with soft afternoon daylight. Potted monstera "
            "plant in the corner, minimalist decor. Center floor area completely empty "
            "— space for furniture. No chairs or tables in center. "
            "Photorealistic, interior design editorial, clean and bright."
        ),
        (
            "Contemporary Nordic living room. Sage-green accent wall, light oak floor, "
            "thin-framed abstract art print. A small side table with a candle at the edge. "
            "Wide empty space in the center of the frame at floor level. No furniture in center. "
            "Warm afternoon light, professional interior lifestyle photography."
        ),
    ],
    "gardenstein": [
        (
            "Elegant garden terrace, late afternoon golden hour. Wooden decking floor, "
            "lush green manicured lawn in background with blurred bokeh. "
            "Two decorative cushions and a cup of coffee on a small side table at the edge. "
            "Wide center area on the decking is completely clear — space for outdoor furniture. "
            "No chairs or sofas in center. Photorealistic, warm sunlight, premium outdoor lifestyle."
        ),
        (
            "Stylish outdoor patio with smooth stone tiles and wooden pergola. "
            "Climbing roses on the pergola, blue sky, Mediterranean summer atmosphere. "
            "Terracotta flower pot and a bottle of wine at the side. "
            "Empty center area on the stone tiles ready for garden furniture placement. "
            "No products in center. Sharp, vibrant, high-end outdoor lifestyle editorial."
        ),
    ],
    "intex": [
        (
            "Large suburban backyard on a sunny summer day. Blue sky, lush green lawn. "
            "Colorful pool toys and towels visible at the edges of the frame. "
            "Large completely empty green lawn space in the center of the frame. "
            "No pool in the center. Photorealistic, bright, cheerful summer atmosphere."
        ),
        (
            "Bright garden with green grass and white fence in the background. "
            "Summer afternoon sunshine, pool accessories at the edge. "
            "Wide open grassy center area — no objects there. "
            "Photorealistic, warm and inviting, high resolution family garden."
        ),
    ],
    "zoovera": [
        (
            "Cozy modern living room, hardwood floor, cream colored sofa in background. "
            "A happy golden retriever is sitting on the sofa looking at camera. "
            "Warm natural window light. Clear open floor space in the center. "
            "No pet accessories in center. Photorealistic, homey and warm."
        ),
        (
            "Bright apartment interior, white walls, parquet floor. A fluffy tabby cat "
            "is grooming itself on a windowsill. Afternoon sunlight. "
            "Empty floor space in center of frame — space for a pet product. "
            "Photorealistic, calm and inviting lifestyle photography."
        ),
    ],
    "hopla_toys": [
        (
            "Bright white studio background, pure white seamless backdrop, "
            "soft even lighting with gentle shadow underneath. "
            "Professional product photography, no people, no props. "
            "Clean, minimal, white RGB 255 255 255 background. Photo studio."
        ),
    ],
    "marketia_home": [
        (
            "Minimalist Scandinavian home interior. White walls, light oak parquet floor, "
            "small potted plant and a stack of books on a shelf to the side. "
            "Empty center floor space, no clutter in center. "
            "Clean, contemporary, soft natural light. Professional lifestyle photography."
        ),
    ],
    "lifekraft": [
        (
            "Minimalist modern home office desk. Light marble surface, white walls, "
            "small succulent plant to the side, a notebook and pen at the edge. "
            "Clear empty center of the desk — space for a desk organizer product. "
            "Warm white light, clean Scandinavian aesthetic. Photorealistic product lifestyle."
        ),
        (
            "Bright modern bathroom with white ceramic tiles and wood accent shelf. "
            "Folded white towels and a small candle to the side. "
            "Empty center wall and counter space — spot for a bathroom organizer. "
            "Clean, minimalist, spa-like atmosphere. Photorealistic interior lifestyle."
        ),
    ],
}

_DEFAULT_SCENES = [
    (
        "Modern bright interior room. Clean white walls, light wooden floor. "
        "A person is visible in the background. Large clear empty space in center "
        "of the frame at floor level. No furniture in center. Professional lifestyle photo."
    ),
]


def _get_scene_prompt(brand: str, sku: str) -> str:
    import zlib
    scenes = _BRAND_SCENES.get(brand.lower(), _DEFAULT_SCENES)
    idx = zlib.crc32(sku.encode()) % len(scenes)
    return scenes[idx]


# ---------------------------------------------------------------------------
# rembg helpers (lazy import)
# ---------------------------------------------------------------------------

_rembg_session = None


def _get_rembg():
    global _rembg_session
    if _rembg_session is None:
        from rembg import new_session
        _rembg_session = new_session("u2net")
    return _rembg_session


def _remove_bg(img_bytes: bytes) -> Image.Image:
    from rembg import remove
    result = remove(img_bytes, session=_get_rembg())
    return Image.open(io.BytesIO(result)).convert("RGBA")


def _alpha_trim(rgba: Image.Image) -> Image.Image:
    bbox = rgba.getbbox()
    return rgba.crop(bbox) if bbox else rgba


# ---------------------------------------------------------------------------
# Flux Pro (fal.ai) background generation
# ---------------------------------------------------------------------------

def _generate_background(prompt: str) -> Image.Image:
    import fal_client

    api_key = os.getenv("FAL_KEY", "").strip() or os.getenv("FAL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Brak klucza fal.ai — ustaw FAL_KEY lub FAL_API_KEY w .env")

    os.environ["FAL_KEY"] = api_key

    result = fal_client.run(
        "fal-ai/flux-pro",
        arguments={
            "prompt": prompt,
            "image_size": "square_hd",
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "num_images": 1,
            "safety_tolerance": "2",
        },
    )
    images = result.get("images") or []
    if not images:
        raise RuntimeError("Flux Pro nie zwrócił żadnego obrazu")

    import urllib.request
    with urllib.request.urlopen(images[0]["url"]) as resp:
        data = resp.read()
    return Image.open(io.BytesIO(data)).convert("RGB").resize(
        (CANVAS, CANVAS), Image.LANCZOS
    )


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

def _add_drop_shadow(
    canvas: Image.Image, product_rgba: Image.Image, pos: tuple[int, int]
) -> Image.Image:
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    mask = product_rgba.split()[3]
    sm = Image.new("RGBA", product_rgba.size, (0, 0, 0, 55))
    sm.putalpha(mask)
    shadow.paste(sm, pos)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    return Image.alpha_composite(canvas, shadow)


def _composite(bg: Image.Image, product_rgba: Image.Image) -> Image.Image:
    """Scale product to PRODUCT_FILL of canvas, place bottom-center."""
    target = int(CANVAS * PRODUCT_FILL)
    w, h = product_rgba.size
    scale = target / max(w, h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    product_rgba = product_rgba.resize((new_w, new_h), Image.LANCZOS)

    canvas = bg.convert("RGBA")
    x = (CANVAS - new_w) // 2
    # Place at ~bottom of canvas with a small margin so floor looks natural
    y = CANVAS - new_h - int(CANVAS * 0.06)
    y = max(0, y)

    canvas = _add_drop_shadow(canvas, product_rgba, (x, y))
    canvas.alpha_composite(product_rgba, (x, y))
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Public batch entry point
# ---------------------------------------------------------------------------

async def _download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=30), ssl=False) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


def _is_done(conn: sqlite3.Connection, sku: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM lifestyle_thumbnails WHERE sku=?", (sku,)
    ).fetchone() is not None


def _mark_done(conn: sqlite3.Connection, sku: str, path: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO lifestyle_thumbnails (sku, path) VALUES (?,?)", (sku, path)
    )
    conn.commit()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lifestyle_thumbnails "
        "(sku TEXT PRIMARY KEY, path TEXT)"
    )
    conn.commit()


def generate_lifestyle_thumbnails(
    products: list[Product],
    brands: list[str] | None = None,
    force: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Generate AI lifestyle thumbnails. Returns (done, skipped)."""
    return asyncio.run(
        _generate_batch(products, brands, force, progress_callback)
    )


async def _generate_batch(
    products: list[Product],
    brands: list[str] | None,
    force: bool,
    progress_callback: Callable[[str], None] | None,
) -> tuple[int, int]:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    _ensure_table(conn)

    targets = [
        p for p in products
        if (brands is None or p.brand in brands) and getattr(p, "images", [])
    ]

    done = skipped = 0
    total = len(targets)

    async with aiohttp.ClientSession() as session:
        for i, p in enumerate(targets, 1):
            label = f"{i}/{total} — {p.sku}"
            if progress_callback:
                progress_callback(f"Lifestyle AI: {label}")

            if not force and _is_done(conn, p.sku):
                skipped += 1
                continue

            img_bytes = await _download(session, p.images[0])
            if img_bytes is None:
                if progress_callback:
                    progress_callback(f"Lifestyle AI: {label} (błąd pobierania)")
                continue

            try:
                # Step 1: cut out product
                rgba = _remove_bg(img_bytes)
                rgba = _alpha_trim(rgba)

                # Step 2: generate matching background
                prompt = _get_scene_prompt(p.brand or "", p.sku)
                bg = _generate_background(prompt)

                # Step 3: composite
                result = _composite(bg, rgba)

                out_path = THUMB_DIR / f"{p.sku}_lifestyle.jpg"
                result.save(str(out_path), "JPEG", quality=95, optimize=True)
                _mark_done(conn, p.sku, str(out_path))
                done += 1
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Lifestyle AI: {label} (błąd: {e})")

    conn.close()
    return done, skipped
