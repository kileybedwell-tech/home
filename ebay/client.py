"""A typed-enough wrapper over the eBay Sell APIs used for seller listings."""

from __future__ import annotations

import mimetypes
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator, Mapping

from .auth import TokenStore
from .config import Config
from .http import EbayError, encode_multipart, request

#: Namespace every Trading API (legacy XML) element lives in.
_TRADING_NS = "urn:ebay:apis:eBLBaseComponents"


class EbayClient:
    """Authenticated access to the Sell Inventory, Fulfillment and Account APIs."""

    def __init__(self, config: Config, tokens: TokenStore | None = None) -> None:
        self.config = config
        self.tokens = tokens or TokenStore(config)

    # ---- plumbing -------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        url = f"{self.config.api_host}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        headers = {
            "Authorization": f"Bearer {self.tokens.access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.config.marketplace_id,
        }
        if json_body is not None:
            # Required by the Inventory API on every write.
            headers["Content-Language"] = self.config.content_language
        if extra_headers:
            headers.update(extra_headers)
        return request(method, url, headers=headers, json_body=json_body)

    def _paginate(
        self,
        path: str,
        *,
        key: str,
        params: Mapping[str, Any] | None = None,
        limit: int = 100,
        max_items: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk an offset-paginated Sell API collection, yielding each record."""
        offset = 0
        seen = 0
        while True:
            page_size = limit
            if max_items is not None:
                page_size = min(limit, max_items - seen)
                if page_size <= 0:
                    return
            query = dict(params or {})
            query.update({"limit": page_size, "offset": offset})
            payload = self._call("GET", path, params=query) or {}
            records = payload.get(key) or []
            for record in records:
                yield record
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            total = payload.get("total")
            offset += len(records)
            if not records or (isinstance(total, int) and offset >= total):
                return

    # ---- account --------------------------------------------------------

    def privileges(self) -> dict[str, Any]:
        """Selling limits and whether the account is registered for payments."""
        return self._call("GET", "/sell/account/v1/privilege") or {}

    def opted_in_programs(self) -> list[str]:
        """Seller programs this account is enrolled in."""
        payload = self._call("GET", "/sell/account/v1/program/get_opted_in_programs") or {}
        return [
            p.get("programType", "")
            for p in payload.get("programs", [])
            if isinstance(p, dict)
        ]

    def opt_in(self, program: str = "SELLING_POLICY_MANAGEMENT") -> Any:
        """Enrol the seller in a program.

        Business policies are opt-in, and the Account API returns error 20403
        ("User is not eligible for Business Policy") on every policy call until
        the seller enrols. An offer cannot publish without policy ids, so this
        is a hard prerequisite rather than a nicety.
        """
        return self._call(
            "POST", "/sell/account/v1/program/opt_in", json_body={"programType": program}
        )

    def create_fulfillment_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST", "/sell/account/v1/fulfillment_policy", json_body=policy
        ) or {}

    def create_payment_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/sell/account/v1/payment_policy", json_body=policy) or {}

    def create_return_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/sell/account/v1/return_policy", json_body=policy) or {}

    def fulfillment_policies(self) -> list[dict[str, Any]]:
        payload = self._call(
            "GET",
            "/sell/account/v1/fulfillment_policy",
            params={"marketplace_id": self.config.marketplace_id},
        ) or {}
        return payload.get("fulfillmentPolicies", [])

    def payment_policies(self) -> list[dict[str, Any]]:
        payload = self._call(
            "GET",
            "/sell/account/v1/payment_policy",
            params={"marketplace_id": self.config.marketplace_id},
        ) or {}
        return payload.get("paymentPolicies", [])

    def return_policies(self) -> list[dict[str, Any]]:
        payload = self._call(
            "GET",
            "/sell/account/v1/return_policy",
            params={"marketplace_id": self.config.marketplace_id},
        ) or {}
        return payload.get("returnPolicies", [])

    def create_location(self, key: str, location: dict[str, Any]) -> Any:
        """Create an inventory location. eBay returns 204 with no body."""
        return self._call(
            "POST",
            f"/sell/inventory/v1/location/{urllib.parse.quote(key, safe='')}",
            json_body=location,
        )

    def inventory_locations(self) -> list[dict[str, Any]]:
        """Warehouses/stores. An offer cannot publish without one."""
        payload = self._call("GET", "/sell/inventory/v1/location", params={"limit": 100}) or {}
        return payload.get("locations", [])

    # ---- images ---------------------------------------------------------

    def upload_image(self, path: str | Path) -> str:
        """Upload a local picture to eBay Picture Services, return its URL.

        eBay's Inventory API takes image *URLs*, never file uploads, so local
        photos have to be hosted somewhere first. EPS is eBay's own host and
        is the path with no third party involved.

        Note EPS deletes pictures not attached to a listing after 30 days, so
        upload as part of listing rather than as a long-term store.
        """
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"no such image: {source}")
        content = source.read_bytes()
        if not content:
            raise ValueError(f"{source} is empty")
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            raise ValueError(f"{source} does not look like an image ({mime})")

        body, content_type = encode_multipart("image", source.name, content, mime)
        _, headers = request(
            "POST",
            f"{self.config.media_host}/commerce/media/v1_beta/image/create_image_from_file",
            headers={"Authorization": f"Bearer {self.tokens.access_token()}"},
            raw_body=body,
            content_type=content_type,
            with_headers=True,
        )
        location = headers.get("Location") or headers.get("location") or ""
        image_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not image_id:
            raise ValueError(
                f"eBay accepted {source.name} but returned no image id (Location: {location!r})"
            )
        return self.image_url(image_id)

    def image_url(self, image_id: str) -> str:
        """Resolve an EPS image id to the URL a listing can reference."""
        payload = request(
            "GET",
            f"{self.config.media_host}/commerce/media/v1_beta/image/{image_id}",
            headers={"Authorization": f"Bearer {self.tokens.access_token()}"},
        ) or {}
        url = payload.get("imageUrl", "")
        if not url:
            raise ValueError(f"eBay returned no imageUrl for image {image_id}")
        return url

    # ---- taxonomy -------------------------------------------------------

    def default_category_tree_id(self) -> str:
        """The category tree that applies to this marketplace."""
        payload = self._call(
            "GET",
            "/commerce/taxonomy/v1/get_default_category_tree_id",
            params={"marketplace_id": self.config.marketplace_id},
        ) or {}
        return payload.get("categoryTreeId", "")

    def item_condition_policies(self, category_id: str) -> list[dict[str, Any]]:
        """Condition rules for a category.

        Most categories accept the plain condition enum (NEW, USED_GOOD, ...).
        Some - trading cards, coins, stamps, and other collectibles - reject
        it and instead require a numeric conditionId (e.g. 4000 "Ungraded")
        plus conditionDescriptors, which this lists so a listing can supply
        the right ones instead of failing at publish time.
        """
        payload = self._call(
            "GET",
            f"/sell/metadata/v1/marketplace/{self.config.marketplace_id}"
            "/get_item_condition_policies",
            params={"filter": f"categoryIds:{{{category_id}}}"},
        ) or {}
        return payload.get("itemConditionPolicies", [])

    def suggest_categories(self, query: str, tree_id: str | None = None) -> list[dict[str, Any]]:
        """Ask eBay which leaf categories match a description.

        Listings must reference a *leaf* category id; this is the supported way
        to find one without hand-browsing the category tree.
        """
        tree = tree_id or self.default_category_tree_id()
        payload = self._call(
            "GET",
            f"/commerce/taxonomy/v1/category_tree/{tree}/get_category_suggestions",
            params={"q": query},
        ) or {}
        return payload.get("categorySuggestions", [])

    # ---- inventory ------------------------------------------------------

    def inventory_items(self, max_items: int | None = None) -> Iterator[dict[str, Any]]:
        """Every inventory item (SKU) in the seller's catalogue."""
        return self._paginate(
            "/sell/inventory/v1/inventory_item",
            key="inventoryItems",
            max_items=max_items,
        )

    def get_inventory_item(self, sku: str) -> dict[str, Any]:
        return self._call(
            "GET", f"/sell/inventory/v1/inventory_item/{urllib.parse.quote(sku, safe='')}"
        ) or {}

    def upsert_inventory_item(self, sku: str, item: dict[str, Any]) -> None:
        """Create or replace a SKU. eBay returns 204 with no body on success."""
        self._call(
            "PUT",
            f"/sell/inventory/v1/inventory_item/{urllib.parse.quote(sku, safe='')}",
            json_body=item,
        )

    def offers_for_sku(self, sku: str) -> list[dict[str, Any]]:
        """Offers (the listing side of a SKU) for one inventory item.

        eBay returns HTTP 404 "[25713] This Offer is not available" for a SKU
        with no offers yet, rather than 200 with an empty array - a widely
        reported quirk of getOffers, not a real error. A SKU with no offers is
        the normal state right before the first one is created, so that
        specific 404 is swallowed into an empty list rather than raised.
        """
        try:
            payload = self._call(
                "GET", "/sell/inventory/v1/offer", params={"sku": sku, "limit": 100}
            ) or {}
        except EbayError as exc:
            if exc.status == 404 and any(
                str(e.get("errorId")) == "25713" for e in exc.errors
            ):
                return []
            raise
        return payload.get("offers", [])

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        return self._call("GET", f"/sell/inventory/v1/offer/{offer_id}") or {}

    def create_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/sell/inventory/v1/offer", json_body=offer) or {}

    def update_offer(self, offer_id: str, offer: dict[str, Any]) -> Any:
        return self._call(
            "PUT", f"/sell/inventory/v1/offer/{offer_id}", json_body=offer
        )

    def publish_offer(self, offer_id: str) -> dict[str, Any]:
        """Push an offer live; the response carries the resulting listingId."""
        return self._call("POST", f"/sell/inventory/v1/offer/{offer_id}/publish") or {}

    def withdraw_offer(self, offer_id: str) -> dict[str, Any]:
        """End the live listing but keep the offer for re-publishing."""
        return self._call("POST", f"/sell/inventory/v1/offer/{offer_id}/withdraw") or {}

    def _trading_call(self, call_name: str, request_body_xml: str) -> ET.Element:
        """POST one Trading API (legacy XML) call, returning its root element.

        The REST Sell APIs cover inventory-item-based listings only; some
        seller-wide data (watch counts, and every active listing regardless
        of how it was created) only exists on this older API. It still takes
        the current OAuth user token, sent as X-EBAY-API-IAF-TOKEN instead of
        an Authorization bearer, so no extra scope or re-consent is needed.
        """
        ns = _TRADING_NS
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<{call_name}Request xmlns="{ns}">{request_body_xml}</{call_name}Request>'
        ).encode("utf-8")
        url = f"{self.config.api_host}/ws/api.dll"
        text = request(
            "POST",
            url,
            headers={
                "X-EBAY-API-SITEID": "0",
                "X-EBAY-API-COMPATIBILITY-LEVEL": "1155",
                "X-EBAY-API-CALL-NAME": call_name,
                "X-EBAY-API-IAF-TOKEN": self.tokens.access_token(),
            },
            raw_body=body,
            content_type="text/xml",
        )
        root = ET.fromstring(text)
        ns_prefix = f"{{{ns}}}"
        ack = root.findtext(f"{ns_prefix}Ack")
        if ack not in ("Success", "Warning"):
            message = "; ".join(
                e.findtext(f"{ns_prefix}LongMessage") or e.findtext(f"{ns_prefix}ShortMessage") or ""
                for e in root.findall(f"{ns_prefix}Errors")
            )
            raise EbayError(200, url, {}, message or text)
        return root

    def watch_count(self, listing_id: str) -> int | None:
        """Watchers on one live listing. None if eBay omits the count (e.g. ended)."""
        ns = _TRADING_NS
        root = self._trading_call(
            "GetItem",
            f"<ItemID>{listing_id}</ItemID><IncludeWatchCount>true</IncludeWatchCount>",
        )
        count = root.findtext(f"{{{ns}}}Item/{{{ns}}}WatchCount")
        return int(count) if count is not None else None

    def active_listings(
        self, entries_per_page: int = 200, max_items: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Every currently active listing on the account, watch count included.

        ``inventory_items()`` only sees SKU-tracked items created through the
        Inventory API itself - a listing made directly in Seller Hub (or any
        listing that predates using this tool) never appears there. This
        calls the Trading API's GetMyeBaySelling instead, which reflects
        every active listing regardless of how it was created.
        """
        ns = _TRADING_NS
        p = f"{{{ns}}}"
        page = 1
        seen = 0
        while True:
            root = self._trading_call(
                "GetMyeBaySelling",
                "<ActiveList><Include>true</Include>"
                "<IncludeWatchCount>true</IncludeWatchCount>"
                f"<Pagination><EntriesPerPage>{entries_per_page}</EntriesPerPage>"
                f"<PageNumber>{page}</PageNumber></Pagination></ActiveList>",
            )
            active = root.find(f"{p}ActiveList")
            if active is None:
                return
            for item in active.findall(f"{p}ItemArray/{p}Item"):
                current_price = item.find(f"{p}SellingStatus/{p}CurrentPrice")
                yield {
                    "itemId": item.findtext(f"{p}ItemID"),
                    "sku": item.findtext(f"{p}SKU"),
                    "title": item.findtext(f"{p}Title"),
                    "listingType": item.findtext(f"{p}ListingType"),
                    "price": current_price.text if current_price is not None else None,
                    "currency": current_price.get("currencyID") if current_price is not None else None,
                    "quantity": item.findtext(f"{p}QuantityAvailable") or item.findtext(f"{p}Quantity"),
                    "watchCount": item.findtext(f"{p}WatchCount"),
                    "timeLeft": item.findtext(f"{p}TimeLeft"),
                }
                seen += 1
                if max_items and seen >= max_items:
                    return
            pagination = active.find(f"{p}PaginationResult")
            total_pages = int(pagination.findtext(f"{p}TotalNumberOfPages", "1")) if pagination is not None else 1
            if page >= total_pages:
                return
            page += 1

    def update_price_quantity(
        self, sku: str, *, price: str | None = None, quantity: int | None = None
    ) -> Any:
        """Bulk endpoint that changes price and/or available quantity for a SKU.

        Price lives on the offer, quantity on the inventory item, so the offers
        for the SKU are looked up when a price change is requested.
        """
        entry: dict[str, Any] = {"sku": sku}
        if quantity is not None:
            entry["shipToLocationAvailability"] = {"quantity": quantity}
        if price is not None:
            offers = self.offers_for_sku(sku)
            if not offers:
                raise ValueError(f"no offer exists for SKU {sku!r}; cannot set a price")
            entry["offers"] = [
                {
                    "offerId": offer["offerId"],
                    "price": {"value": price, "currency": _currency(offer)},
                }
                for offer in offers
            ]
        if len(entry) == 1:
            raise ValueError("nothing to update: pass a price, a quantity, or both")
        return self._call(
            "POST",
            "/sell/inventory/v1/bulk_update_price_quantity",
            json_body={"requests": [entry]},
        )

    # ---- orders ---------------------------------------------------------

    def orders(
        self,
        *,
        order_filter: str | None = None,
        max_items: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Orders, newest first. ``order_filter`` is an eBay filter expression."""
        return self._paginate(
            "/sell/fulfillment/v1/order",
            key="orders",
            params={"filter": order_filter},
            limit=50,
            max_items=max_items,
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._call("GET", f"/sell/fulfillment/v1/order/{order_id}") or {}

    def create_shipping_fulfillment(
        self, order_id: str, fulfillment: dict[str, Any]
    ) -> dict[str, Any]:
        """Mark an order shipped, optionally with tracking details."""
        return self._call(
            "POST",
            f"/sell/fulfillment/v1/order/{order_id}/shipping_fulfillment",
            json_body=fulfillment,
        ) or {}


def _currency(offer: Mapping[str, Any]) -> str:
    price = offer.get("pricingSummary", {}).get("price", {})
    return price.get("currency", "USD")
