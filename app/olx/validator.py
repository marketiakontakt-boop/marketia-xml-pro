"""Pre-POST validator — catch schema violations BEFORE hitting OLX API.

Reasoning: OLX returns 400 with terse errors. Catching in the client keeps the
GUI responsive and gives users actionable feedback.
"""
from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

from app.olx.mapper import _clean_description_html, _clean_title, _extract_image_urls

if TYPE_CHECKING:
    from app.parser.normalizer import Product


class ValidationError(Exception):
    """Field-level error with pointer to the offending field."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")

    def __repr__(self) -> str:  # pragma: no cover — cosmetic
        return f"ValidationError({self.field!r}, {self.message!r})"


# Accepts +48123456789, 48123456789, or 123456789 (9 digits PL).
_PHONE_RE = re.compile(r"^(?:\+?48)?[0-9]{9}$")


def _validate_title(title: str, errors: list[ValidationError]) -> None:
    cleaned = _clean_title(title)
    if len(cleaned) < 3:
        errors.append(ValidationError("title", "min 3 znaki"))
    elif len(cleaned) > 70:
        errors.append(ValidationError("title", f"max 70 znaków (masz {len(cleaned)})"))


def _validate_description(desc_raw: str, errors: list[ValidationError]) -> None:
    cleaned = _clean_description_html(desc_raw or "")
    length = len(cleaned)
    if length < 80:
        errors.append(ValidationError(
            "description", f"min 80 znaków po oczyszczeniu HTML (masz {length})"
        ))
    elif length > 9000:
        errors.append(ValidationError("description", f"max 9000 znaków (masz {length})"))


def _validate_contact(name: str, phone: str, errors: list[ValidationError]) -> None:
    name_stripped = (name or "").strip()
    if not name_stripped:
        errors.append(ValidationError("contact.name", "wymagane"))
    elif len(name_stripped) > 30:
        errors.append(ValidationError("contact.name", "max 30 znaków"))

    phone_clean = (phone or "").replace(" ", "").replace("-", "")
    if not phone_clean:
        errors.append(ValidationError("contact.phone", "wymagane"))
    elif not _PHONE_RE.match(phone_clean):
        errors.append(ValidationError(
            "contact.phone", "format E.164 PL: +48123456789 lub 9 cyfr"
        ))


def _validate_category(
    category_id: int, conn: sqlite3.Connection, errors: list[ValidationError]
) -> None:
    if not category_id:
        errors.append(ValidationError("category_id", "wymagane"))
        return
    row = conn.execute(
        "SELECT 1 FROM olx_categories WHERE id = ?", (int(category_id),)
    ).fetchone()
    if not row:
        errors.append(ValidationError(
            "category_id", f"kategoria {category_id} nie znaleziona w cache — odśwież kategorie"
        ))


def _validate_attributes(
    category_id: int,
    provided: dict[str, str],
    conn: sqlite3.Connection,
    errors: list[ValidationError],
) -> None:
    from app.olx.categories import get_required_attributes

    if not category_id:
        return  # already flagged
    for attr in get_required_attributes(conn, int(category_id)):
        code = attr["code"]
        value = (provided or {}).get(code)
        if value in (None, ""):
            errors.append(ValidationError(
                f"attributes.{code}", f"wymagany atrybut: {attr['label']}"
            ))


def _validate_images(product: "Product", errors: list[ValidationError]) -> None:
    if not _extract_image_urls(product):
        errors.append(ValidationError(
            "images", "min 1 publicznie dostępny URL (uploaduj miniatury na ImgBB)"
        ))


def _validate_price(product: "Product", errors: list[ValidationError]) -> None:
    try:
        price = float(product.price)
    except (TypeError, ValueError):
        errors.append(ValidationError("price.value", "musi być liczbą"))
        return
    if price <= 0:
        errors.append(ValidationError("price.value", "musi być > 0"))


def validate_product(
    product: "Product",
    category_id: int,
    attribute_values: dict[str, str],
    conn: sqlite3.Connection,
    contact_name: str,
    contact_phone: str,
) -> list[ValidationError]:
    """Return list of ValidationError. Empty list = ready to POST."""
    errors: list[ValidationError] = []
    _validate_title(getattr(product, "title", "") or getattr(product, "name", ""), errors)
    _validate_description(getattr(product, "description", ""), errors)
    _validate_contact(contact_name, contact_phone, errors)
    _validate_category(category_id, conn, errors)
    _validate_attributes(category_id, attribute_values, conn, errors)
    _validate_images(product, errors)
    _validate_price(product, errors)
    return errors
