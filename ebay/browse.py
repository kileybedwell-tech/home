"""A fallback view of the account's own active listings, via the Browse API.

``trading.active_listings`` (GetMyeBaySelling) is the authoritative feed -
it sees every listing however it was made. But it runs on the Trading API's
per-application call quota, and a freshly created App ID sits under a low
default cap until eBay's growth check raises it. When that cap is hit the
call fails with "exceeded usage limit on this call, please make call to
GetAPIAccessRules" - and GetAPIAccessRules itself was retired by eBay
(HTTP 410), so the remaining quota cannot even be read.

The Buy Browse API answers a narrower question - "what does this seller
have publicly listed matching these words" - but it runs on a *separate*
quota and authenticates with a client-credentials application token, so it
keeps the duplicate check working when Trading is throttled. It sees only
what eBay has publicly indexed, which is why it is a fallback and not the
primary source.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Iterator

from .auth import application_token
from .config import Config
from .http import request

_MAX_PAGE_SIZE = 200


def _search(
    config: Config, token: str, params: dict[str, str]
) -> dict[str, Any]:
    url = (
        f"{config.api_host}/buy/browse/v1/item_summary/search"
        f"?{urllib.parse.urlencode(params)}"
    )
    return request(
        "GET",
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": config.marketplace_id,
        },
    ) or {}


def _item_dict(summary: dict[str, Any]) -> dict[str, Any]:
    """Shape a Browse item summary like ``trading._item_dict`` output."""
    price = summary.get("price") or {}
    return {
        "itemId": summary.get("legacyItemId", "") or summary.get("itemId", ""),
        # Browse never exposes the seller's own SKU.
        "sku": "",
        "title": summary.get("title", ""),
        "price": price.get("value", ""),
        "currency": price.get("currency", ""),
        "quantity": "",
        "viewItemUrl": summary.get("itemWebUrl", ""),
    }


def seller_username(config: Config, client: Any) -> str:
    """The account's eBay username, needed to filter a Browse search.

    ``EBAY_SELLER_USERNAME`` wins if set. Otherwise it is discovered the
    only way the granted scopes allow: take a listing this tool published,
    look it up through Browse, and read the seller off it.
    """
    override = os.environ.get("EBAY_SELLER_USERNAME", "").strip()
    if override:
        return override

    token = application_token(config)
    for item in client.inventory_items(max_items=25):
        sku = item.get("sku")
        if not sku:
            continue
        for offer in client.offers_for_sku(sku):
            listing_id = (offer.get("listing") or {}).get("listingId")
            if not listing_id:
                continue
            url = (
                f"{config.api_host}/buy/browse/v1/item/get_item_by_legacy_id"
                f"?legacy_item_id={urllib.parse.quote(listing_id)}"
            )
            payload = request(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": config.marketplace_id,
                },
            ) or {}
            username = (payload.get("seller") or {}).get("username", "")
            if username:
                return username
    raise RuntimeError(
        "could not work out this account's eBay username from its own "
        "listings; set EBAY_SELLER_USERNAME in .env to use the Browse "
        "fallback"
    )


def seller_listings(
    config: Config,
    seller: str,
    *,
    query: str = "",
    max_items: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Active listings by ``seller``, narrowed to ``query`` when given.

    Browse ranks by relevance rather than matching every word, so callers
    should still filter the titles themselves.
    """
    token = application_token(config)
    yielded = 0
    offset = 0
    while True:
        if max_items is not None and yielded >= max_items:
            return
        params = {
            "filter": "sellers:{%s}" % seller,
            "limit": str(_MAX_PAGE_SIZE),
            "offset": str(offset),
        }
        if query:
            params["q"] = query
        payload = _search(config, token, params)
        summaries = payload.get("itemSummaries") or []
        if not summaries:
            return
        for summary in summaries:
            yield _item_dict(summary)
            yielded += 1
            if max_items is not None and yielded >= max_items:
                return
        offset += len(summaries)
        if offset >= int(payload.get("total") or 0):
            return
