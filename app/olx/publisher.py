"""Publisher — iterate selected products, validate → map → POST /adverts.

Runs single-threaded (OLX rate limit 4500/5min + intra-request client throttle).
Streams progress via `on_progress(done, total, sku)` callback and stores each
outcome to `olx_offers` for later inspection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from app.cache.sqlite_cache import open_cache, save_olx_offer
from app.olx.mapper import map_product_to_offer
from app.olx.validator import ValidationError, validate_product

if TYPE_CHECKING:
    from app.olx.api import OLXClient
    from app.parser.normalizer import Product


@dataclass
class PublishResult:
    total: int = 0
    published: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (sku, message)
    advert_ids: dict[str, str] = field(default_factory=dict)     # sku → advert_id


def _publish_one(
    client: "OLXClient",
    product: "Product",
    category_id: int,
    attribute_values: dict[str, str],
    contact_name: str,
    contact_phone: str,
    city_id: int,
) -> str:
    """Map + POST /adverts. Returns advert_id.

    STUB: intended to POST via `client.post('adverts', body)` — the underlying
    HTTP call is already implemented in `OLXClient`. If the response schema
    changes upstream, adapt the parsing here.
    """
    body = map_product_to_offer(
        product=product,
        category_id=category_id,
        attribute_values=attribute_values,
        contact_name=contact_name,
        contact_phone=contact_phone,
        city_id=city_id,
    )
    resp = client.post("adverts", body)
    # OLX returns {"data": {"id": ..., "url": ...}}
    data = resp.get("data", resp) if isinstance(resp, dict) else {}
    advert_id = str(data.get("id") or "")
    if not advert_id:
        raise RuntimeError(f"OLX response bez advert_id: {resp}")
    return advert_id


def publish_products(
    client: "OLXClient",
    products: list["Product"],
    category_mapping: dict[str, int],
    attributes_map: dict[str, dict],
    contact_name: str,
    contact_phone: str,
    city_id: int,
    on_progress: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> PublishResult:
    """Publish `products` sequentially. Stops early if `cancel_check()` returns True.

    Params:
        category_mapping: {sku: category_id}
        attributes_map: {sku: {attr_code: value}}

    Returns:
        `PublishResult` with counts + per-sku errors + advert_ids.
    """
    result = PublishResult(total=len(products))

    with open_cache() as conn:
        for idx, product in enumerate(products, start=1):
            if cancel_check and cancel_check():
                break

            sku = product.sku
            if on_progress:
                on_progress(idx, len(products), sku)

            category_id = category_mapping.get(sku)
            attribute_values = attributes_map.get(sku, {})

            if not category_id:
                msg = "brak przypisanej kategorii OLX"
                result.failed += 1
                result.errors.append((sku, msg))
                save_olx_offer(conn, sku, None, "validation_error", error=msg)
                continue

            # Pre-flight validation
            errors = validate_product(
                product=product,
                category_id=category_id,
                attribute_values=attribute_values,
                conn=conn,
                contact_name=contact_name,
                contact_phone=contact_phone,
            )
            if errors:
                msg = "; ".join(f"{e.field}: {e.message}" for e in errors)
                result.failed += 1
                result.errors.append((sku, msg))
                save_olx_offer(conn, sku, None, "validation_error", error=msg)
                continue

            # POST /adverts
            try:
                advert_id = _publish_one(
                    client=client,
                    product=product,
                    category_id=category_id,
                    attribute_values=attribute_values,
                    contact_name=contact_name,
                    contact_phone=contact_phone,
                    city_id=city_id,
                )
                result.published += 1
                result.advert_ids[sku] = advert_id
                save_olx_offer(
                    conn, sku, advert_id, "published",
                    external_url=f"https://www.olx.pl/oferta/{advert_id}",
                )
            except ValidationError as e:  # defensive — shouldn't reach here
                msg = str(e)
                result.failed += 1
                result.errors.append((sku, msg))
                save_olx_offer(conn, sku, None, "validation_error", error=msg)
            except Exception as e:  # noqa: BLE001 — capture per-sku, keep going
                msg = str(e)
                result.failed += 1
                result.errors.append((sku, msg))
                save_olx_offer(conn, sku, None, "api_error", error=msg)

    return result
