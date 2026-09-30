"""A narrow slice of eBay's classic Trading API (XML), used only for one
thing the newer REST Sell APIs cannot do: see every currently active
listing on the account.

``EbayClient.inventory_items`` (Sell Inventory API) only returns SKUs
created through that same API. A seller account can also carry listings
created through eBay's website, Seller Hub's bulk tools, File Exchange, or
third-party crosslisting tools - those never become "inventory items" and
are invisible to the REST API. ``GetMyeBaySelling`` reads the same backing
data My eBay's Active tab does, so it sees all of them regardless of how
they were created.

Authenticated with the same OAuth user token as the REST calls, via the
``X-EBAY-API-IAF-TOKEN`` header Trading API accepts in place of the classic
eBayAuthToken - no separate credential needed.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Iterator
from xml.etree import ElementTree

from .auth import TokenStore
from .config import Config

_NS = "urn:ebay:apis:eBLBaseComponents"
_COMPATIBILITY_LEVEL = "1193"
_MAX_PAGE_SIZE = 200


class TradingError(RuntimeError):
    """A Trading API call came back Ack=Failure, with eBay's own error list."""

    def __init__(self, call_name: str, errors: list[dict[str, str]]) -> None:
        self.call_name = call_name
        self.errors = errors
        summary = "; ".join(
            e.get("LongMessage") or e.get("ShortMessage", "") for e in errors
        ) or "no error detail returned"
        super().__init__(f"{call_name} failed: {summary}")


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return element.find(f"{{{_NS}}}{name}")


def _text(element: ElementTree.Element | None, default: str = "") -> str:
    return (element.text or default) if element is not None else default


def _call(config: Config, tokens: TokenStore, call_name: str, body: str) -> ElementTree.Element:
    """POST one Trading API call and return its parsed XML root."""
    headers = {
        "X-EBAY-API-IAF-TOKEN": tokens.access_token(),
        "X-EBAY-API-COMPATIBILITY-LEVEL": _COMPATIBILITY_LEVEL,
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "0",
        "Content-Type": "text/xml",
    }
    url = f"{config.api_host}/ws/api.dll"
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach {url}: {exc.reason}") from exc

    root = ElementTree.fromstring(raw)
    if _text(_child(root, "Ack")) not in ("Success", "Warning"):
        errors = [
            {
                "ShortMessage": _text(_child(error, "ShortMessage")),
                "LongMessage": _text(_child(error, "LongMessage")),
                "ErrorCode": _text(_child(error, "ErrorCode")),
            }
            for error in root.iter(f"{{{_NS}}}Errors")
        ]
        raise TradingError(call_name, errors)
    return root


def _item_dict(item: ElementTree.Element) -> dict[str, Any]:
    price = _child(item, "SellingStatus")
    price = _child(price, "CurrentPrice") if price is not None else None
    return {
        "itemId": _text(_child(item, "ItemID")),
        "sku": _text(_child(item, "SKU")),
        "title": _text(_child(item, "Title")),
        "price": _text(price),
        "currency": price.get("currencyID", "") if price is not None else "",
        "quantity": _text(_child(item, "QuantityAvailable")),
        "viewItemUrl": _text(_child(_child(item, "ListingDetails"), "ViewItemURL")),
    }


def active_listings(
    config: Config, tokens: TokenStore, max_items: int | None = None
) -> Iterator[dict[str, Any]]:
    """Every currently active listing on the account, most-recently-ended-first.

    Unlike the Sell Inventory API, this sees listings made any way at all -
    Seller Hub, File Exchange, third-party crosslisting tools included -
    since it is the same feed My eBay's Active tab reads. A seller with a
    few thousand listings costs a handful of requests at 200/page.
    """
    page = 1
    yielded = 0
    while True:
        if max_items is not None and yielded >= max_items:
            return
        entries = _MAX_PAGE_SIZE
        if max_items is not None:
            entries = min(entries, max_items - yielded)
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="{_NS}">
  <ActiveList>
    <Sort>TimeLeft</Sort>
    <Pagination>
      <EntriesPerPage>{entries}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
  <OutputSelector>ActiveList.ItemArray.Item.ItemID</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.Title</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.SKU</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.SellingStatus.CurrentPrice</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.QuantityAvailable</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.ListingDetails.ViewItemURL</OutputSelector>
  <OutputSelector>ActiveList.PaginationResult.TotalNumberOfPages</OutputSelector>
</GetMyeBaySellingRequest>"""
        root = _call(config, tokens, "GetMyeBaySelling", body)
        active = _child(root, "ActiveList")
        if active is None:
            return
        item_array = _child(active, "ItemArray")
        items = list(item_array.findall(f"{{{_NS}}}Item")) if item_array is not None else []
        for item in items:
            yield _item_dict(item)
            yielded += 1
            if max_items is not None and yielded >= max_items:
                return
        total_pages = int(_text(_child(_child(active, "PaginationResult"), "TotalNumberOfPages"), "1"))
        if not items or page >= total_pages:
            return
        page += 1


#: EndItem reasons eBay accepts; "NotAvailable" is the honest one for an
#: item that sold somewhere else.
END_REASONS = ("NotAvailable", "Incorrect", "LostOrBroken", "OtherListingError", "SellToHighBidder")


def end_item(
    config: Config, tokens: TokenStore, item_id: str, reason: str = "NotAvailable"
) -> str:
    """End one active listing, however it was created. Returns the end time.

    This is how an item that sold on Mercari comes down from eBay: most of
    the store was listed in Seller Hub, which the Sell Inventory API's
    ``withdraw`` cannot touch. Listings created through the Inventory API
    are the exception - eBay refuses Trading calls on those, so withdraw
    their offer instead.
    """
    if reason not in END_REASONS:
        raise ValueError(f"reason must be one of: {', '.join(END_REASONS)}")
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<EndItemRequest xmlns="{_NS}">
  <ItemID>{int(item_id)}</ItemID>
  <EndingReason>{reason}</EndingReason>
</EndItemRequest>"""
    root = _call(config, tokens, "EndItem", body)
    return _text(_child(root, "EndTime"))


def get_item(config: Config, tokens: TokenStore, item_id: str) -> dict[str, Any]:
    """The shipping-relevant facts of one listing, however it was created."""
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="{_NS}">
  <ItemID>{int(item_id)}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>"""
    item = _child(_call(config, tokens, "GetItem", body), "Item")
    pkg = _child(item, "ShippingPackageDetails")
    profiles = _child(item, "SellerProfiles")
    ship = _child(profiles, "SellerShippingProfile") if profiles is not None else None
    ounces = 0.0
    dims = {}
    if pkg is not None:
        ounces = float(_text(_child(pkg, "WeightMajor"), "0") or 0) * 16 + float(
            _text(_child(pkg, "WeightMinor"), "0") or 0
        )
        dims = {
            "length": float(_text(_child(pkg, "PackageLength"), "0") or 0),
            "width": float(_text(_child(pkg, "PackageWidth"), "0") or 0),
            "height": float(_text(_child(pkg, "PackageDepth"), "0") or 0),
        }
    return {
        "itemId": str(item_id),
        "sku": _text(_child(item, "SKU")),
        "categoryId": _text(_child(_child(item, "PrimaryCategory"), "CategoryID")),
        "shippingPolicyId": _text(_child(ship, "ShippingProfileID")) if ship is not None else "",
        "package": {"weight": {"value": ounces}, "dimensions": dims},
    }


def revise_shipping(
    config: Config, tokens: TokenStore, item_id: str, policy_id: str, package: dict[str, Any]
) -> None:
    """Put a Seller Hub listing on a shipping policy with a real package."""
    ounces = int(package["weight"]["value"])
    d = package["dimensions"]
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="{_NS}">
  <Item>
    <ItemID>{int(item_id)}</ItemID>
    <SellerProfiles><SellerShippingProfile><ShippingProfileID>{int(policy_id)}</ShippingProfileID></SellerShippingProfile></SellerProfiles>
    <ShippingPackageDetails>
      <MeasurementUnit>English</MeasurementUnit>
      <WeightMajor unit="lbs">{ounces // 16}</WeightMajor>
      <WeightMinor unit="oz">{ounces % 16}</WeightMinor>
      <PackageLength unit="in">{d["length"]}</PackageLength>
      <PackageWidth unit="in">{d["width"]}</PackageWidth>
      <PackageDepth unit="in">{d["height"]}</PackageDepth>
    </ShippingPackageDetails>
  </Item>
</ReviseItemRequest>"""
    _call(config, tokens, "ReviseItem", body)
