"""Brand-specific color chips for Light Modern theme."""
from __future__ import annotations

BRAND_COLORS: dict[str, tuple[str, str]] = {
    "intex":         ("#DBEAFE", "#1D4ED8"),
    "gardenstein":   ("#DCFCE7", "#15803D"),
    "villago":       ("#FFEDD5", "#C2410C"),
    "zoovera":       ("#EDE9FE", "#6D28D9"),
    "marketia_home": ("#E0F2FE", "#0369A1"),
    "hopla_toys":    ("#FCE7F3", "#9D174D"),
}
_DEFAULT = ("#F3F4F6", "#374151")


def get_brand_chip_colors(brand_key: str) -> tuple[str, str]:
    """Return (bg_color, text_color) for brand chip."""
    return BRAND_COLORS.get(brand_key.lower(), _DEFAULT)
