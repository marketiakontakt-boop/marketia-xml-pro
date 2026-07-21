"""Tests for the AI lifestyle composer (rembg + Imagen 4 pipeline)."""
import io
import pytest
from PIL import Image
from app.images.lifestyle_composer import (
    _get_scene_prompt,
    _alpha_trim,
    _composite,
    _BRAND_SCENES,
    generate_lifestyle_thumbnails,
)
from app.parser.normalizer import Product


def _rgba_circle(size: int = 400) -> Image.Image:
    """Create a small RGBA image with a transparent background for testing."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Draw a simple filled square in center
    for y in range(100, 300):
        for x in range(100, 300):
            img.putpixel((x, y), (200, 100, 50, 255))
    return img


def test_scene_prompts_cover_main_brands():
    for brand in ("homestein", "gardenstein", "intex", "zoovera", "hopla_toys"):
        assert brand in _BRAND_SCENES, f"Missing scene for brand: {brand}"
        assert len(_BRAND_SCENES[brand]) >= 1


def test_get_scene_prompt_returns_string_for_known_brand():
    prompt = _get_scene_prompt("homestein", "SKU-001")
    assert isinstance(prompt, str)
    assert len(prompt) > 20


def test_get_scene_prompt_fallback_for_unknown_brand():
    prompt = _get_scene_prompt("unknownbrand_xyz", "SKU-999")
    assert isinstance(prompt, str)
    assert len(prompt) > 10


def test_get_scene_prompt_deterministic():
    p1 = _get_scene_prompt("gardenstein", "G-100")
    p2 = _get_scene_prompt("gardenstein", "G-100")
    assert p1 == p2


def test_alpha_trim_removes_transparent_border():
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    # Opaque center region
    for y in range(50, 150):
        for x in range(50, 150):
            img.putpixel((x, y), (255, 0, 0, 255))
    trimmed = _alpha_trim(img)
    assert trimmed.size == (100, 100)


def test_alpha_trim_fully_opaque_image_unchanged():
    img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    trimmed = _alpha_trim(img)
    assert trimmed.size == (100, 100)


def test_composite_output_size_is_canvas():
    from app.images.lifestyle_composer import CANVAS
    bg = Image.new("RGB", (CANVAS, CANVAS), (180, 200, 220))
    product = _rgba_circle(400)
    result = _composite(bg, product)
    assert result.size == (CANVAS, CANVAS)
    assert result.mode == "RGB"


def test_composite_modifies_background():
    from app.images.lifestyle_composer import CANVAS
    bg = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    product = _rgba_circle(400)
    result = _composite(bg, product)
    # At least some pixels must differ from pure white
    pixels = list(result.getdata())
    non_white = [p for p in pixels if p != (255, 255, 255)]
    assert len(non_white) > 500


def test_generate_lifestyle_thumbnails_skips_products_without_images():
    """No API calls should be made for products lacking images."""
    p = Product(
        product_id="1", sku="TEST-001", ean="", price=0.0, purchase_price=0.0,
        tax_rate="23%", weight=0.0, width=0.0, height=0.0, length=0.0, quantity=0,
        name="Test", category_name="", manufacturer_name="",
        description="", description_extra_1="", description_extra_2="",
    )
    p.brand = "homestein"
    p.images = []
    done, skipped = generate_lifestyle_thumbnails([p], brands=["homestein"])
    assert done == 0
