"""OLX API integration (Faza 1 SHELL).

Public API — thin re-exports so importers write::

    from app.olx import OLXAuth, OLXClient, publish_products
"""
from __future__ import annotations

from app.olx.auth import OLXAuth, OLXAuthError
from app.olx.api import OLXClient, OLXAPIError
from app.olx.categories import (
    refresh_categories,
    refresh_attributes,
    get_category_by_path,
    get_required_attributes,
    find_category_by_name,
)
from app.olx.mapper import map_product_to_offer
from app.olx.validator import validate_product, ValidationError
from app.olx.publisher import publish_products, PublishResult

__all__ = [
    "OLXAuth",
    "OLXAuthError",
    "OLXClient",
    "OLXAPIError",
    "refresh_categories",
    "refresh_attributes",
    "get_category_by_path",
    "get_required_attributes",
    "find_category_by_name",
    "map_product_to_offer",
    "validate_product",
    "ValidationError",
    "publish_products",
    "PublishResult",
]
