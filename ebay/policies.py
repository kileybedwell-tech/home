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

#: USPS's current ground service. Overridable because eBay retires codes.
DEFAULT_SHIPPING_SERVICE = "USPSGroundAdvantage"
DEFAULT_CARRIER = "USPS"


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
        payload = build(client.config, **options.get(label, {}))
        try:
            result = getattr(client, creator)(payload) or {}
        except Exception as exc:  # surface eBay's wording, keep going
            failed[label] = str(exc)
            on_event(f"{label}: FAILED - {exc}")
            continue
        policy_id = result.get(id_field, "")
        created[label] = policy_id
        on_event(f"{label}: created ({policy_id})")

    return {"created": created, "existing": existing, "failed": failed}
