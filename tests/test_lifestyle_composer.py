import pytest
from pathlib import Path
from PIL import Image
from app.images.lifestyle_composer import list_lifestyle_assets, compose_lifestyle

LIFESTYLE_DIR = Path(__file__).resolve().parent.parent / "data" / "lifestyle"


def test_list_lifestyle_assets_returns_paths():
    assets = list_lifestyle_assets("zoovera")
    assert len(assets) > 0
    for a in assets:
        assert a.suffix == ".png"
        assert a.exists()


def test_list_lifestyle_assets_unknown_brand_returns_empty():
    assets = list_lifestyle_assets("nonexistent_brand")
    assert assets == []


def test_compose_lifestyle_returns_image():
    thumb = Image.new("RGB", (1200, 1200), (255, 255, 255))
    assets = list_lifestyle_assets("zoovera")
    assert assets, "No zoovera assets found — run placeholder generation first"
    result = compose_lifestyle(thumb, assets[0])
    assert result.size == (1200, 1200)
    assert result.mode == "RGB"


def test_compose_lifestyle_bottom_right_different_from_original():
    thumb = Image.new("RGB", (1200, 1200), (255, 255, 255))
    assets = list_lifestyle_assets("zoovera")
    result = compose_lifestyle(thumb, assets[0])
    crop = result.crop((600, 600, 1200, 1200))
    pixels = list(crop.getdata())
    non_white = [p for p in pixels if p != (255, 255, 255)]
    assert len(non_white) > 100
