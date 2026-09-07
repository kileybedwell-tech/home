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

# Browse refuses a search with no q/category_ids/charity_ids/epid/gtin
# (error 12001), so "everything this seller has listed" has to be walked
# one top-level category at a time - category_ids matches the whole subtree
# beneath the id, so these 34 cover the EBAY_US site. Verified against the
# Taxonomy API (category_tree/0); eBay Motors is deliberately absent, as it
# is a separate marketplace with its own tree.
_US_TOP_CATEGORIES = (
    "20081",   # Antiques
    "550",     # Art
    "2984",    # Baby
    "267",     # Books & Magazines
    "12576",   # Business & Industrial
    "625",     # Cameras & Photo
    "15032",   # Cell Phones & Accessories
    "11450",   # Clothing, Shoes & Accessories
    "11116",   # Coins & Paper Money
    "1",       # Collectibles
    "58058",   # Computers/Tablets & Networking
    "293",     # Consumer Electronics
    "14339",   # Crafts
    "237",     # Dolls & Bears
    "45100",   # Entertainment Memorabilia
    "172008",  # Gift Cards & Coupons
    "26395",   # Health & Beauty
    "11700",   # Home & Garden
    "281",     # Jewelry & Watches
    "11232",   # Movies & TV
    "11233",   # Music
    "619",     # Musical Instruments & Gear
    "1281",    # Pet Supplies
    "870",     # Pottery & Glass
    "10542",   # Real Estate
    "316",     # Specialty Services
    "888",     # Sporting Goods
    "64482",   # Sports Mem, Cards & Fan Shop
    "260",     # Stamps
    "1305",    # Tickets & Experiences
    "220",     # Toys & Hobbies
    "3252",    # Travel
    "1249",    # Video Games & Consoles
    "99",      # Everything Else
)


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
        # Browse hangs tracking parameters off the item URL - hundreds of
        # characters that wreck a terminal table. The bare /itm/<id> form
        # is what Trading's ViewItemURL gives and is what a person wants.
        "viewItemUrl": summary.get("itemWebUrl", "").split("?", 1)[0],
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
    query: str,
    max_items: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Active listings by ``seller`` matching ``query``.

    Browse ranks by relevance rather than matching every word, so callers
    should still filter the titles themselves. For an unfiltered sweep use
    :func:`all_seller_listings` - Browse rejects a search with no query at
    all.
    """
    if not query.strip():
        raise ValueError("Browse needs a query; use all_seller_listings() instead")
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
            "q": query,
        }
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


def all_seller_listings(
    config: Config, seller: str, *, max_items: int | None = None
) -> Iterator[dict[str, Any]]:
    """Every listing by ``seller``, swept one top-level category at a time.

    Browse has no "list everything by this seller" call, so this walks
    ``_US_TOP_CATEGORIES``. An item listed in two categories would come back
    twice, so item ids already seen are skipped.
    """
    token = application_token(config)
    seen: set[str] = set()
    yielded = 0
    for category in _US_TOP_CATEGORIES:
        offset = 0
        while True:
            if max_items is not None and yielded >= max_items:
                return
            payload = _search(
                config,
                token,
                {
                    "filter": "sellers:{%s}" % seller,
                    "category_ids": category,
                    "limit": str(_MAX_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            summaries = payload.get("itemSummaries") or []
            if not summaries:
                break
            for summary in summaries:
                item = _item_dict(summary)
                if item["itemId"] in seen:
                    continue
                seen.add(item["itemId"])
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            offset += len(summaries)
            if offset >= int(payload.get("total") or 0):
                break
