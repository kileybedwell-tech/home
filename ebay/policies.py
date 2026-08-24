"""Creating the three business policies an offer must reference.

eBay will not publish an offer without a payment, return and fulfillment
policy, and a new seller account has none. These builders produce the minimal
valid payload for each, with the choices a small seller actually cares about
(handling time, shipping cost, return window) exposed as arguments.

Nothing here is guesswork about *structure* — the shapes come from the Account
API — but eBay validates enum values like shipping service codes server-side,
so `create_missing` reports rejections verbatim rather than swallowing them.
"""

from __future__ import annotations

from typing import Any, Callable

from .client import EbayClient
from .config import Config

#: Everything except vehicles; the only other value is MOTORS_VEHICLES.
CATEGORY_TYPE = "ALL_EXCLUDING_MOTORS_VEHICLES"

#: eBay never minted a "USPSGroundAdvantage" code — it kept USPSParcel and
#: remapped it when USPS renamed the service, so that is the working default.
DEFAULT_SHIPPING_SERVICE = "USPSParcel"
DEFAULT_CARRIER = "USPS"

#: Tried in order when eBay rejects a code as unknown. eBay retires codes
#: without warning and the authoritative list is only available from the
#: Trading API, so falling forward beats failing on the first guess.
SERVICE_FALLBACKS = (
    "USPSParcel",
    "USPSFirstClass",
    "USPSPriority",
    "ShippingMethodStandard",
    "Other",
)


def _is_unknown_service(error: Exception) -> bool:
    """True when eBay rejected the shipping service code specifically."""
    return "UNKNOWN_SHIPPING_SERVICE_CODE" in str(error)


def payment_policy(config: Config, *, name: str = "Immediate payment") -> dict[str, Any]:
    """Require payment at checkout, so stock is not held by non-payers."""
    return {
        "name": name,
        "marketplaceId": config.marketplace_id,
        "categoryTypes": [{"name": CATEGORY_TYPE}],
        "immediatePay": True,
    }


def return_policy(
    config: Config,
    *,
    name: str = "30 day returns",
    days: int = 30,
    buyer_pays_return: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "marketplaceId": config.marketplace_id,
        "categoryTypes": [{"name": CATEGORY_TYPE}],
        "returnsAccepted": True,
        "returnPeriod": {"value": days, "unit": "DAY"},
        "returnShippingCostPayer": "BUYER" if buyer_pays_return else "SELLER",
        "refundMethod": "MONEY_BACK",
    }


def fulfillment_policy(
    config: Config,
    *,
    name: str = "Standard shipping",
    handling_days: int = 1,
    cost: str = "5.00",
    free_shipping: bool = False,
    carrier: str = DEFAULT_CARRIER,
    service: str = DEFAULT_SHIPPING_SERVICE,
) -> dict[str, Any]:
    shipping_service: dict[str, Any] = {
        "sortOrder": 1,
        "shippingCarrierCode": carrier,
        "shippingServiceCode": service,
        "freeShipping": free_shipping,
    }
    if not free_shipping:
        shipping_service["shippingCost"] = {
            "value": cost,
            "currency": _currency_for(config.marketplace_id),
        }
    return {
        "name": name,
        "marketplaceId": config.marketplace_id,
        "categoryTypes": [{"name": CATEGORY_TYPE}],
        "handlingTime": {"value": handling_days, "unit": "DAY"},
        "shippingOptions": [
            {
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [shipping_service],
            }
        ],
        "shipToLocations": {
            "regionIncluded": [{"regionName": _region_for(config.marketplace_id)}]
        },
    }


_CURRENCIES = {"EBAY_US": "USD", "EBAY_GB": "GBP", "EBAY_DE": "EUR", "EBAY_AU": "AUD",
               "EBAY_CA": "CAD"}
_REGIONS = {"EBAY_US": "US", "EBAY_GB": "GB", "EBAY_DE": "DE", "EBAY_AU": "AU",
            "EBAY_CA": "CA"}


def _currency_for(marketplace: str) -> str:
    return _CURRENCIES.get(marketplace, "USD")


def _region_for(marketplace: str) -> str:
    return _REGIONS.get(marketplace, marketplace.replace("EBAY_", ""))


#: (label, id field, lister, creator, payload builder)
POLICY_KINDS = (
    ("payment", "paymentPolicyId", "payment_policies", "create_payment_policy",
     payment_policy),
    ("return", "returnPolicyId", "return_policies", "create_return_policy",
     return_policy),
    ("fulfillment", "fulfillmentPolicyId", "fulfillment_policies",
     "create_fulfillment_policy", fulfillment_policy),
)


def _create_with_fallback(
    client: EbayClient,
    label: str,
    creator: str,
    build: Callable[..., dict[str, Any]],
    options: dict[str, Any],
    on_event: Callable[[str], None],
) -> dict[str, Any]:
    """Create one policy, walking the service fallbacks if eBay rejects a code.

    Only the fulfillment policy carries a shipping service, and only an
    explicitly unknown code is worth retrying — any other rejection is a real
    problem and is raised straight away.
    """
    make = getattr(client, creator)
    if label != "fulfillment" or options.get("service"):
        return make(build(client.config, **options)) or {}

    last: Exception | None = None
    for candidate in SERVICE_FALLBACKS:
        try:
            return make(build(client.config, **dict(options, service=candidate))) or {}
        except Exception as exc:
            if not _is_unknown_service(exc):
                raise
            last = exc
            on_event(f"{label}: {candidate} rejected, trying next")
    raise last if last else RuntimeError("no shipping service candidates")


def create_missing(
    client: EbayClient,
    *,
    builder_options: dict[str, dict[str, Any]] | None = None,
    on_event: Callable[[str], None] = lambda message: None,
) -> dict[str, Any]:
    """Create whichever of the three policies the account is missing.

    Existing policies are left alone — this is safe to re-run. Each failure is
    recorded and the remaining kinds are still attempted, so one bad enum does
    not hide the other two results.
    """
    options = builder_options or {}
    created: dict[str, str] = {}
    existing: dict[str, str] = {}
    failed: dict[str, str] = {}

    for label, id_field, lister, creator, build in POLICY_KINDS:
        current = getattr(client, lister)()
        if current:
            existing[label] = current[0][id_field]
            on_event(f"{label}: already exists ({current[0].get('name', '')})")
            continue
        kind_options = dict(options.get(label, {}))
        try:
            result = _create_with_fallback(
                client, label, creator, build, kind_options, on_event
            )
        except Exception as exc:  # surface eBay's wording, keep going
            failed[label] = str(exc)
            on_event(f"{label}: FAILED - {exc}")
            continue
        policy_id = result.get(id_field, "")
        created[label] = policy_id
        on_event(f"{label}: created ({policy_id})")

    return {"created": created, "existing": existing, "failed": failed}


def inventory_location(
    *,
    name: str = "Home",
    postal_code: str,
    country: str = "US",
    address_line1: str = "",
    city: str = "",
    state: str = "",
) -> dict[str, Any]:
    """Build the payload for a warehouse location an offer can ship from.

    eBay accepts a warehouse with only a postal code and country, which is
    enough to rate shipping without publishing a full street address.
    """
    if not postal_code.strip():
        raise ValueError("a postal code is required to create a location")
    address: dict[str, Any] = {"postalCode": postal_code, "country": country}
    for field, value in (
        ("addressLine1", address_line1),
        ("city", city),
        ("stateOrProvince", state),
    ):
        if value:
            address[field] = value
    return {
        "location": {"address": address},
        "name": name,
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
    }
