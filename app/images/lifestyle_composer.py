"""AI Lifestyle Thumbnail Generator.

Pipeline per product:
  1. Download original image (or use cached thumbnail)
  2. rembg — remove background → RGBA cutout
  3. Imagen 4 — generate scene background matching brand / product type
  4. Composite product cutout onto AI background at natural floor position
  5. Save as output/thumbnails/{sku}_lifestyle.jpg

Background scenes include people and contextual elements so the product
looks naturally placed in a real-world setting.
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
    "villago": [
        (
            "Modern Scandinavian dining room interior. Warm wooden parquet floor, white walls, "
            "large windows with soft natural daylight. A smiling woman in casual clothes is "
            "sitting at the far edge of frame. Center floor area is empty — space for furniture. "
            "No chairs or tables in the center. Photorealistic, interior design editorial style."
        ),
        (
            "Contemporary Nordic living room. Light oak floor, sage-green accent wall, potted "
            "monstera plant in corner. A young couple relaxing in background, laughing softly. "
            "Clear empty space in the center of the frame at floor level. "
            "No furniture in center. Warm afternoon light. Professional lifestyle photography."
        ),
    ],
    "gardenstein": [
        (
            "Lush sunny garden terrace, late afternoon golden hour. Green manicured lawn, "
            "colorful flower beds, wooden decking. A woman in a summer dress holds an iced drink "
            "and smiles in the background. Center of the terrace is clear — space for outdoor "
            "furniture. No chairs or tables in center. Photorealistic, bright and cheerful."
        ),
        (
            "Elegant outdoor patio with stone tiles and pergola covered in climbing roses. "
            "Blue sky, Mediterranean atmosphere. A man and a child play in the background. "
            "Empty center area on the tiles ready for garden furniture. "
            "No products in center. Sharp, vibrant, lifestyle editorial."
        ),
    ],
    "intex": [
        (
            "Large green suburban backyard on a sunny summer day. Blue sky, lush lawn. "
            "Two children in bright swimwear are laughing and running on the grass nearby. "
            "Parents are watching from a shaded area. Large clear space in the center of "
            "the yard — area where an inflatable pool will be placed. No pool present. "
            "Photorealistic, cheerful family atmosphere."
        ),
        (
            "Backyard garden party, summer afternoon. Families relaxing, cold drinks on a "
            "side table, colorful towels on the grass. Bright sunshine. Wide open grassy "
            "space in the center of the frame, no objects there. "
            "Photorealistic, warm and inviting, high resolution."
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
            "Bright children's bedroom, pastel blue walls, soft colorful rug on the floor. "
            "Two children aged 4-6 are playing in the background, laughing. Wooden toy "
            "shelves visible on the side. Clear open space in center of room on the rug. "
            "No toys in center. Soft natural light. Photorealistic, playful and safe."
        ),
        (
            "Cheerful playroom, yellow and white walls, wooden floor with a colorful mat. "
            "A child in overalls is drawing at a table in the background. "
            "Open empty space in the center of the frame on the floor. "
            "Photorealistic, warm, family-friendly, lifestyle photography."
        ),
    ],
    "marketia_home": [
        (
            "Minimalist modern home interior. White walls, light oak parquet floor, "
            "subtle shelf with plants and books on the side. A woman reads a book on "
            "a sofa in the background. Empty center floor space. "
            "Clean, contemporary, no clutter in center. Professional lifestyle photography."
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
# Imagen 4 background generation
# ---------------------------------------------------------------------------

def _generate_background(prompt: str) -> Image.Image:
    import google.genai as genai
    from google.genai import types

    keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
    if not keys:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Brak klucza Gemini — ustaw GEMINI_API_KEYS lub GEMINI_API_KEY")
        keys = [key]

    last_err: Exception | None = None
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_images(
                model="imagen-4.0-fast-generate-001",
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="1:1",
                    output_mime_type="image/jpeg",
                ),
            )
            data = response.generated_images[0].image.image_bytes
            return Image.open(io.BytesIO(data)).convert("RGB").resize(
                (CANVAS, CANVAS), Image.LANCZOS
            )
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Imagen 4 failed on all keys: {last_err}")


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
