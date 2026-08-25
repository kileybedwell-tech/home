"""Creating a listing end to end.

eBay's Inventory API does not have a "create listing" call. A live listing is
three resources created in order:

1. an **inventory item**, keyed by SKU — what the thing *is* (title, photos,
   condition, stock);
2. an **offer** — what it *costs on one marketplace* (price, category,
   business policies, location);
3. a **publish** of that offer, which is what mints the listing id.

This module drives that sequence, resolves the pieces eBay requires but does
not infer for you (policy ids, a merchant location), and validates what it can
locally so a typo fails before a half-created listing exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import EbayClient
from .config import Config

#: Condition values the Inventory API accepts.
CONDITIONS = (
    "NEW",
    "LIKE_NEW",
    "NEW_OTHER",
    "NEW_WITH_DEFECTS",
    "CERTIFIED_REFURBISHED",
    "EXCELLENT_REFURBISHED",
    "VERY_GOOD_REFURBISHED",
    "GOOD_REFURBISHED",
    "SELLER_REFURBISHED",
    "USED_EXCELLENT",
    "USED_VERY_GOOD",
    "USED_GOOD",
    "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
)

#: eBay truncates beyond this, so reject rather than silently lose words.
MAX_TITLE = 80

_POLICY_KINDS = (
    ("fulfillment", "fulfillmentPolicyId", "fulfillment_policies"),
    ("payment", "paymentPolicyId", "payment_policies"),
    ("return", "returnPolicyId", "return_policies"),
)


class ListingError(ValueError):
    """A listing could not be built or published, with a reason worth reading."""


@dataclass
class ListingDraft:
    """The seller-supplied half of a listing, before eBay's ids are resolved."""

    sku: str
    title: str
    price: str
    category_id: str
    description: str = ""
    quantity: int = 1
    condition: str = "NEW"
    condition_description: str = ""
    #: Required by trading-card categories (e.g. CCG Individual Cards) in
    #: addition to `condition` - {descriptorId: [valueId, ...]}, ids from
    #: the Sell Metadata API's getItemConditionPolicies.
    condition_descriptors: dict[str, list[str]] = field(default_factory=dict)
    #: Weight/size of the shipped item, eBay's own shape:
    #: {"packageType": ..., "weight": {"value":.., "unit":..},
    #:  "dimensions": {"length":.., "width":.., "height":.., "unit":..}}.
    #: Needed for calculated shipping and for eligibility checks on
    #: low-cost flat-rate services (e.g. the eBay Standard Envelope, which
    #: requires packageType LETTER and fitting its weight/size limits).
    package_weight_and_size: dict[str, Any] = field(default_factory=dict)
    image_urls: list[str] = field(default_factory=list)
    aspects: dict[str, list[str]] = field(default_factory=dict)
    currency: str = "USD"

    def validate(self) -> None:
        """Catch locally everything that would otherwise cost a round trip."""
        problems = []
        if not self.sku.strip():
            problems.append("sku is required")
        if not self.title.strip():
            problems.append("title is required")
        elif len(self.title) > MAX_TITLE:
            problems.append(
                f"title is {len(self.title)} characters; eBay allows {MAX_TITLE}"
            )
        if not self.category_id.strip():
            problems.append("category id is required (try `python -m ebay categories`)")
        if self.condition not in CONDITIONS:
            problems.append(
                f"condition {self.condition!r} is not one of: {', '.join(CONDITIONS)}"
            )
        if self.quantity < 1:
            problems.append("quantity must be at least 1")
        try:
            if float(self.price) <= 0:
                problems.append("price must be greater than zero")
        except (TypeError, ValueError):
            problems.append(f"price {self.price!r} is not a number")
        if not self.image_urls:
            problems.append(
                "at least one image is required to publish "
                "(pass --photo for a local file, or --image for a URL)"
            )
        for url in self.image_urls:
            if url.startswith("<uploaded from "):
                continue  # dry-run placeholder standing in for a real upload
            if not url.startswith("https://"):
                problems.append(f"image URL must be https: {url!r}")
        if problems:
            raise ListingError("; ".join(problems))

    # ---- payload construction -------------------------------------------

    def inventory_item(self) -> dict[str, Any]:
        product: dict[str, Any] = {"title": self.title}
        if self.description:
            product["description"] = self.description
        if self.image_urls:
            product["imageUrls"] = list(self.image_urls)
        if self.aspects:
            product["aspects"] = self.aspects
        item: dict[str, Any] = {
            "product": product,
            "condition": self.condition,
            "availability": {"shipToLocationAvailability": {"quantity": self.quantity}},
        }
        if self.condition_description:
            item["conditionDescription"] = self.condition_description
        if self.condition_descriptors:
            item["conditionDescriptors"] = [
                {"name": name, "values": list(values)}
                for name, values in self.condition_descriptors.items()
            ]
        if self.package_weight_and_size:
            item["packageWeightAndSize"] = dict(self.package_weight_and_size)
        return item

    def offer(
        self, config: Config, policies: dict[str, str], location_key: str
    ) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "marketplaceId": config.marketplace_id,
            "format": "FIXED_PRICE",
            "availableQuantity": self.quantity,
            "categoryId": self.category_id,
            "listingDescription": self.description or self.title,
            "listingPolicies": dict(policies),
            "pricingSummary": {"price": {"value": self.price, "currency": self.currency}},
            "merchantLocationKey": location_key,
        }


def resolve_policies(
    client: EbayClient, overrides: dict[str, str] | None = None
) -> dict[str, str]:
    """Fill in the three business policy ids an offer must reference.

    An override wins. Otherwise a single policy of that kind is used
    automatically; several is ambiguous and none means the account is not set
    up to list yet, and both say so with the actual choices.
    """
    chosen = dict(overrides or {})
    for label, key, method in _POLICY_KINDS:
        if chosen.get(key):
            continue
        available = getattr(client, method)()
        if not available:
            raise ListingError(
                f"no {label} policy exists for {client.config.marketplace_id}. "
                "Create one in eBay Seller Hub > Account > Business policies."
            )
        if len(available) > 1:
            options = ", ".join(
                f"{p.get(key)} ({p.get('name', '')})" for p in available
            )
            raise ListingError(
                f"several {label} policies exist; pass --{label}-policy. Options: {options}"
            )
        chosen[key] = available[0][key]
    return chosen


def resolve_location(client: EbayClient, override: str | None = None) -> str:
    """Pick the merchant location an offer ships from."""
    if override:
        return override
    locations = client.inventory_locations()
    enabled = [
        loc
        for loc in locations
        if loc.get("merchantLocationStatus", "ENABLED") == "ENABLED"
    ]
    usable = enabled or locations
    if not usable:
        raise ListingError(
            "no inventory location exists. Create one in eBay Seller Hub, or via "
            "POST /sell/inventory/v1/location/{key} — an offer cannot publish without one."
        )
    if len(usable) > 1:
        options = ", ".join(
            f"{loc.get('merchantLocationKey')} ({loc.get('name', '')})" for loc in usable
        )
        raise ListingError(f"several locations exist; pass --location. Options: {options}")
    return usable[0]["merchantLocationKey"]


def upload_photos(client: EbayClient, paths: list[str]) -> list[str]:
    """Host local photos on eBay Picture Services and return their URLs.

    Every file is checked to exist before any upload starts, so a typo in the
    last path does not leave earlier photos already uploaded.
    """
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        raise ListingError(f"no such file(s): {', '.join(missing)}")
    return [client.upload_image(path) for path in paths]


def create_listing(
    client: EbayClient,
    draft: ListingDraft,
    *,
    policy_overrides: dict[str, str] | None = None,
    location: str | None = None,
    photos: list[str] | None = None,
    publish: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the inventory-item → offer → publish sequence for one draft.

    ``photos`` are local files; each is uploaded to eBay Picture Services and
    the resulting URL appended to the draft's images.

    Re-running for a SKU that already has an offer updates that offer rather
    than failing, so a corrected draft can simply be submitted again.
    """
    if photos and not dry_run:
        # Upload before validating images, since uploading is what supplies them.
        draft.image_urls = list(draft.image_urls) + upload_photos(client, photos)
    elif photos:
        draft.image_urls = list(draft.image_urls) + [
            f"<uploaded from {p}>" for p in photos
        ]

    draft.validate()

    if dry_run:
        # Resolution needs the network, so show the shape without pretending
        # to know ids we have not fetched.
        return {
            "dryRun": True,
            "sku": draft.sku,
            "inventoryItem": draft.inventory_item(),
            "offer": draft.offer(
                client.config,
                {
                    key: (policy_overrides or {}).get(key, "<resolved at run time>")
                    for _, key, _ in _POLICY_KINDS
                },
                location or "<resolved at run time>",
            ),
        }

    policies = resolve_policies(client, policy_overrides)
    location_key = resolve_location(client, location)

    client.upsert_inventory_item(draft.sku, draft.inventory_item())
    payload = draft.offer(client.config, policies, location_key)

    existing = client.offers_for_sku(draft.sku)
    if existing:
        offer_id = existing[0]["offerId"]
        client.update_offer(offer_id, payload)
        reused = True
    else:
        offer_id = client.create_offer(payload).get("offerId", "")
        reused = False
        if not offer_id:
            raise ListingError("eBay accepted the offer but returned no offerId")

    result = {
        "sku": draft.sku,
        "offerId": offer_id,
        "offerReused": reused,
        "policies": policies,
        "merchantLocationKey": location_key,
        "published": False,
    }
    if publish:
        published = client.publish_offer(offer_id)
        result["published"] = True
        result["listingId"] = published.get("listingId", "")
    return result
