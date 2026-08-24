"""A typed-enough wrapper over the eBay Sell APIs used for seller listings."""

from __future__ import annotations

import mimetypes
import urllib.parse
from pathlib import Path
from typing import Any, Iterator, Mapping

from .auth import TokenStore
from .config import Config
from .http import encode_multipart, request


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
        """Offers (the listing side of a SKU) for one inventory item."""
        payload = self._call(
            "GET", "/sell/inventory/v1/offer", params={"sku": sku, "limit": 100}
        ) or {}
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
