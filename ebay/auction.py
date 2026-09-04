"""Auction-format listings, via the classic Trading API's ``AddItem`` call.

eBay's modern Sell Inventory API (``listing.py``, used by ``create``) can only
build ``FIXED_PRICE`` offers — there is no auction format anywhere in that
API surface. A real auction ("Chinese" listing type, in Trading API terms)
has to go through the same classic XML API ``trading.py`` already uses for
``GetMyeBaySelling``, authenticated the same way (the OAuth user token via
``X-EBAY-API-IAF-TOKEN`` — no separate credential needed).

Unlike ``create_listing``, there is no draft/publish split here: a
successful ``AddItem`` call goes live immediately, for real. There is no
Trading API equivalent of "create the offer but don't publish" — always
``dry_run=True`` first and read the payload before calling for real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import EbayClient
from .listing import resolve_location, resolve_policies, upload_photos
from .trading import _NS, _call, _child, _text

#: eBay truncates beyond this, so reject rather than silently lose words.
MAX_TITLE = 80

#: Auction listings only allow these durations (Days_30/GTC are fixed-price-only).
DURATIONS = ("Days_1", "Days_3", "Days_5", "Days_7", "Days_10")

#: Trading API's classic numeric ConditionID values. Distinct from the
#: Inventory API's string CONDITIONS in listing.py — the two APIs use
#: unrelated condition vocabularies for the same idea.
CONDITION_NAME_TO_ID = {
    "NEW": "1000",
    "NEW_OTHER": "1500",
    "CERTIFIED_REFURBISHED": "2000",
    "SELLER_REFURBISHED": "2500",
    "USED_EXCELLENT": "3000",
    "USED_VERY_GOOD": "4000",
    "USED_GOOD": "5000",
    "USED_ACCEPTABLE": "6000",
    "FOR_PARTS_OR_NOT_WORKING": "7000",
}


class AuctionError(ValueError):
    """An auction draft could not be built or listed, with a reason worth reading."""


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@dataclass
class AuctionDraft:
    """The seller-supplied half of an auction listing."""

    sku: str
    title: str
    starting_bid: str
    category_id: str
    description: str = ""
    quantity: int = 1
    condition: str = "USED_GOOD"
    condition_description: str = ""
    duration: str = "Days_7"
    currency: str = "USD"
    dispatch_time_max: int = 3
    image_urls: list[str] = field(default_factory=list)
    aspects: dict[str, list[str]] = field(default_factory=dict)

    def validate(self) -> None:
        problems = []
        if not self.title.strip():
            problems.append("title is required")
        elif len(self.title) > MAX_TITLE:
            problems.append(
                f"title is {len(self.title)} characters; eBay allows {MAX_TITLE}"
            )
        if not self.category_id.strip():
            problems.append("category id is required (try `python -m ebay categories`)")
        if self.condition not in CONDITION_NAME_TO_ID:
            problems.append(
                f"condition {self.condition!r} is not one of: "
                f"{', '.join(CONDITION_NAME_TO_ID)}"
            )
        if self.duration not in DURATIONS:
            problems.append(f"duration {self.duration!r} is not one of: {', '.join(DURATIONS)}")
        try:
            if float(self.starting_bid) <= 0:
                problems.append("starting bid must be greater than zero")
        except (TypeError, ValueError):
            problems.append(f"starting bid {self.starting_bid!r} is not a number")
        if self.quantity < 1:
            problems.append("quantity must be at least 1")
        if not self.image_urls:
            problems.append(
                "at least one image is required "
                "(pass --photo for a local file, or --image for a URL)"
            )
        for url in self.image_urls:
            if url.startswith("<uploaded from "):
                continue  # dry-run placeholder standing in for a real upload
            if not url.startswith("https://"):
                problems.append(f"image URL must be https: {url!r}")
        if problems:
            raise AuctionError("; ".join(problems))

    def request_xml(
        self, *, policies: dict[str, str], country: str, postal_code: str, location: str
    ) -> str:
        """Build the ``AddItemRequest`` XML body for this draft."""
        pictures = "".join(
            f"<PictureURL>{_xml_escape(url)}</PictureURL>" for url in self.image_urls
        )
        specifics = "".join(
            f"<NameValueList><Name>{_xml_escape(name)}</Name>"
            + "".join(f"<Value>{_xml_escape(v)}</Value>" for v in values)
            + "</NameValueList>"
            for name, values in self.aspects.items()
        )
        condition_description = (
            f"<ConditionDescription>{_xml_escape(self.condition_description)}"
            "</ConditionDescription>"
            if self.condition_description
            else ""
        )
        return f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <Item>
    <SKU>{_xml_escape(self.sku)}</SKU>
    <Title>{_xml_escape(self.title)}</Title>
    <Description><![CDATA[{self.description or self.title}]]></Description>
    <PrimaryCategory><CategoryID>{_xml_escape(self.category_id)}</CategoryID></PrimaryCategory>
    <StartPrice currencyID="{self.currency}">{self.starting_bid}</StartPrice>
    <ConditionID>{CONDITION_NAME_TO_ID[self.condition]}</ConditionID>
    {condition_description}
    <Country>{_xml_escape(country)}</Country>
    <Currency>{self.currency}</Currency>
    <DispatchTimeMax>{self.dispatch_time_max}</DispatchTimeMax>
    <ListingDuration>{self.duration}</ListingDuration>
    <ListingType>Chinese</ListingType>
    <Location>{_xml_escape(location)}</Location>
    <PostalCode>{_xml_escape(postal_code)}</PostalCode>
    <Quantity>{self.quantity}</Quantity>
    <PictureDetails>{pictures}</PictureDetails>
    <ItemSpecifics>{specifics}</ItemSpecifics>
    <SellerProfiles>
      <SellerPaymentProfile><PaymentProfileID>{policies['paymentPolicyId']}</PaymentProfileID></SellerPaymentProfile>
      <SellerReturnProfile><ReturnProfileID>{policies['returnPolicyId']}</ReturnProfileID></SellerReturnProfile>
      <SellerShippingProfile><ShippingProfileID>{policies['fulfillmentPolicyId']}</ShippingProfileID></SellerShippingProfile>
    </SellerProfiles>
  </Item>
</AddItemRequest>"""


def _location_address(client: EbayClient, location_key: str) -> dict[str, str]:
    for loc in client.inventory_locations():
        if loc.get("merchantLocationKey") == location_key:
            address = loc.get("location", {}).get("address", {})
            return {
                "country": address.get("country", "") or client.config.marketplace_id.replace("EBAY_", ""),
                "postal_code": address.get("postalCode", ""),
                "city": address.get("city", ""),
            }
    raise AuctionError(f"no inventory location found for key {location_key!r}")


def create_auction(
    client: EbayClient,
    draft: AuctionDraft,
    *,
    policy_overrides: dict[str, str] | None = None,
    location: str | None = None,
    photos: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one ``AddItem`` call for an auction draft.

    There is no draft/publish split for this path: a non-dry-run call that
    succeeds is live on eBay immediately, for real money. Always try
    ``dry_run=True`` first.
    """
    if photos and not dry_run:
        draft.image_urls = list(draft.image_urls) + upload_photos(client, photos)
    elif photos:
        draft.image_urls = list(draft.image_urls) + [f"<uploaded from {p}>" for p in photos]

    draft.validate()

    if dry_run:
        return {
            "dryRun": True,
            "sku": draft.sku,
            "requestPreview": draft.request_xml(
                policies={
                    key: (policy_overrides or {}).get(key, "<resolved at run time>")
                    for key in ("fulfillmentPolicyId", "paymentPolicyId", "returnPolicyId")
                },
                country="<resolved at run time>",
                postal_code="<resolved at run time>",
                location="<resolved at run time>",
            ),
        }

    policies = resolve_policies(client, policy_overrides)
    location_key = resolve_location(client, location)
    address = _location_address(client, location_key)

    body = draft.request_xml(
        policies=policies,
        country=address["country"],
        postal_code=address["postal_code"],
        location=address["city"] or address["postal_code"],
    )
    root = _call(client.config, client.tokens, "AddItem", body)
    item_id = _text(_child(root, "ItemID"))
    return {
        "sku": draft.sku,
        "itemId": item_id,
        "fees": [
            {"name": _text(_child(fee, "Name")), "amount": _text(_child(fee, "Fee"))}
            for fee in root.iter(f"{{{_NS}}}Fee")
            if _child(fee, "Name") is not None
        ],
    }
