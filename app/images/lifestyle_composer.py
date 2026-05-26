"""Composite lifestyle PNG elements onto product thumbnails."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

LIFESTYLE_DIR = Path(__file__).resolve().parents[2] / "data" / "lifestyle"


def list_lifestyle_assets(brand_key: str) -> list[Path]:
    """Return sorted list of lifestyle PNG paths for a brand. Empty list if none found."""
    brand_dir = LIFESTYLE_DIR / brand_key.lower()
    if not brand_dir.exists():
        return []
    return sorted(brand_dir.glob("*.png"))


def compose_lifestyle(
    thumbnail: Image.Image,
    lifestyle_png: Path,
    position: str = "bottom-right",
    scale: float = 0.32,
) -> Image.Image:
    """Composite a lifestyle PNG element onto a thumbnail. Returns new RGB Image."""
    result = thumbnail.convert("RGBA").copy()
    w, h = result.size

    element = Image.open(lifestyle_png).convert("RGBA")

    target_size = int(w * scale)
    elem_w, elem_h = element.size
    ratio = min(target_size / elem_w, target_size / elem_h)
    new_w = int(elem_w * ratio)
    new_h = int(elem_h * ratio)
    element = element.resize((new_w, new_h), Image.LANCZOS)

    margin = 20
    if position == "bottom-right":
        x = w - new_w - margin
        y = h - new_h - margin
    else:
        x = margin
        y = h - new_h - margin

    shadow_layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    shadow_elem = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    shadow_elem.paste((30, 30, 30, 80), mask=element.split()[3])
    shadow_layer.paste(shadow_elem, (x + 4, y + 6))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=8))
    result = Image.alpha_composite(result, shadow_layer)

    result.paste(element, (x, y), mask=element.split()[3])

    return result.convert("RGB")
