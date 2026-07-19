"""Map Marketia `Product` → OLX offer body (JSON for POST /adverts).

OLX constraints handled here:
- title: 3–70 chars, no CAPS-only, no consecutive UPPERCASE runs > 3 chars.
- description: 80–9000 chars, HTML whitelist <p><br><strong><b><ul><li>.
- price: integer PLN gross.
- images: [{"url": ...}] with min 1 publicly-accessible URL.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.parser.normalizer import Product


_ALLOWED_TAGS = {"p", "br", "strong", "b", "ul", "li"}
_TITLE_MAX = 70
_DESC_MIN = 80
_DESC_MAX = 9000


# ── Title cleanup ───────────────────────────────────────────────────────────

_CAPS_RUN_RE = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ]{4,}")


def _clean_title(title: str) -> str:
    """Strip whitespace, kill CAPS runs, trim to 70 chars on word boundary."""
    t = " ".join((title or "").split())
    if not t:
        return t

    # If the whole thing is CAPS → title-case it.
    letters = [c for c in t if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
        t = t.title()

    # Break any remaining runs of 4+ uppercase letters into Title case.
    def _fix_run(match: re.Match[str]) -> str:
        return match.group(0).capitalize()

    t = _CAPS_RUN_RE.sub(_fix_run, t)

    if len(t) <= _TITLE_MAX:
        return t
    # Trim on last space before limit.
    cut = t[:_TITLE_MAX].rsplit(" ", 1)[0] or t[:_TITLE_MAX]
    return cut


# ── Description cleanup ────────────────────────────────────────────────────


class _HTMLWhitelistCleaner(HTMLParser):
    """Keep only allowed tags/text. Drop attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0  # inside <style>/<script>/etc.

    _SKIP_TAGS = {"style", "script", "section", "dl", "dt", "dd", "head", "meta"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _ALLOWED_TAGS:
            self._out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag in _ALLOWED_TAGS:
            self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _ALLOWED_TAGS:
            self._out.append(f"<{tag}/>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self._out.append(data)

    def result(self) -> str:
        html = "".join(self._out)
        # Collapse whitespace runs but preserve tag boundaries.
        html = re.sub(r"[ \t]+", " ", html)
        html = re.sub(r"\n{3,}", "\n\n", html)
        return html.strip()


def _clean_description_html(html: str) -> str:
    """Strip disallowed tags, keep whitelist. Empty input → empty string."""
    if not html:
        return ""
    cleaner = _HTMLWhitelistCleaner()
    try:
        cleaner.feed(html)
        cleaner.close()
    except Exception:  # noqa: BLE001 — never let parser crash the pipeline
        return re.sub(r"<[^>]+>", "", html)
    return cleaner.result()


def _pad_description(text: str, product_name: str) -> str:
    """Make sure we hit min length (80). Pad with product name if too short."""
    if len(text) >= _DESC_MIN:
        return text
    filler = f"<p>{product_name}</p>" if product_name else "<p>Nowy produkt.</p>"
    padded = text
    while len(padded) < _DESC_MIN:
        padded = f"{padded}\n{filler}"
    return padded[:_DESC_MAX]


# ── Image extraction ────────────────────────────────────────────────────────


def _extract_image_urls(product: "Product") -> list[str]:
    """Return de-duped list of publicly-accessible image URLs.

    Priority: thumbnail_url (already CDN-uploaded), then p.images[], then
    infographic imgbb_urls if attached to product.
    """
    urls: list[str] = []

    def _add(u: str | None) -> None:
        if not u:
            return
        u = u.strip()
        if not u.startswith(("http://", "https://")):
            return
        if u not in urls:
            urls.append(u)

    _add(getattr(product, "thumbnail_url", ""))
    for u in getattr(product, "images", []) or []:
        _add(u)

    # Optional: infographic URLs saved on product (loaded by pipeline).
    for info in getattr(product, "infographics", []) or []:
        if isinstance(info, dict):
            _add(info.get("imgbb_url"))
    return urls


# ── Public API ──────────────────────────────────────────────────────────────


def _to_int_price(value: float) -> int:
    """OLX price wants integer PLN gross. Round up to avoid underpricing."""
    if value is None:
        return 0
    return int(round(float(value)))


def _build_attributes(attribute_values: dict[str, str]) -> list[dict]:
    """OLX expects: [{"code": "state", "value": "new"}, ...]."""
    return [
        {"code": code, "value": value}
        for code, value in (attribute_values or {}).items()
        if value not in (None, "")
    ]


def map_product_to_offer(
    product: "Product",
    category_id: int,
    attribute_values: dict[str, str],
    contact_name: str,
    contact_phone: str,
    city_id: int,
    advertiser_type: str = "business",
    negotiable: bool = False,
    trade: bool = False,
) -> dict:
    """Return dict ready for POST /adverts.

    Callers should run `validate_product` FIRST — this mapper trusts inputs.
    """
    title = _clean_title(getattr(product, "title", "") or getattr(product, "name", ""))
    description = _clean_description_html(
        getattr(product, "description", "") or ""
    )
    description = _pad_description(description, product.name)

    images = [{"url": u} for u in _extract_image_urls(product)]

    body = {
        "title": title,
        "description": description,
        "category_id": int(category_id),
        "advertiser_type": advertiser_type,
        "contact": {
            "name": contact_name,
            "phone": contact_phone,
        },
        "location": {
            "city_id": int(city_id),
        },
        "price": {
            "value": _to_int_price(product.price),
            "currency": "PLN",
            "negotiable": bool(negotiable),
            "trade": bool(trade),
        },
        "attributes": _build_attributes(attribute_values),
        "images": images,
        "external_id": product.sku,
    }
    return body
