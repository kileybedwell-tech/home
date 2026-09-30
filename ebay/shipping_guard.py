"""Every listing must charge the buyer for shipping and carry a real package.

Kiley's standing rule. A listing with no package weight or size makes eBay
default the label to 1 oz and 1x1x1 in, and a non-card listing on the free
Standard Envelope policy means the buyer pays nothing for postage - both have
cost her money on items that sold. Only trading cards may ship free.

This module is the single place that knows the rule: ``create`` and
``publish`` refuse listings that break it, and ``shipping-audit`` sweeps every
active listing on the account (however it was made) and repairs them.
"""

from __future__ import annotations

from typing import Any

from .client import EbayClient

#: Trading card categories - the only ones allowed free Standard Envelope shipping.
CARD_CATEGORIES = frozenset({"261328", "261329", "183454", "183050"})

#: Buyer-paid calculated USPS Ground Advantage, 2 business days.
BUYER_PAID_POLICY = "253136828026"

MAGAZINE_CATEGORIES = frozenset({"280", "64488"})

#: Package used when repairing a listing that has none, by category.
REPAIR_PACKAGES: dict[str, dict[str, float]] = {
    "280": {"oz": 32, "l": 15, "w": 10, "h": 2},
    "64488": {"oz": 32, "l": 15, "w": 10, "h": 2},
}
FALLBACK_PACKAGE = {"oz": 32, "l": 10, "w": 8, "h": 4}


def free_policy_ids(client: EbayClient) -> set[str]:
    """Fulfillment policies a non-card listing must not use.

    Free policies make the buyer pay nothing, and flat-rate ones (like the
    $5 "Standard shipping") can undercharge a heavy package. Kiley's rule is
    calculated USPS on everything except cards, so both kinds count.
    """
    free = set()
    for policy in client.fulfillment_policies():
        services = [
            svc
            for option in policy.get("shippingOptions", [])
            if option.get("optionType", "DOMESTIC") == "DOMESTIC"
            for svc in option.get("shippingServices", [])
        ]
        costs = {o.get("costType") for o in policy.get("shippingOptions", [])}
        all_free = services and all(svc.get("freeShipping") for svc in services)
        if all_free or "FLAT_RATE" in costs:
            free.add(policy["fulfillmentPolicyId"])
    return free


def package_ok(package: dict[str, Any] | None) -> bool:
    """A weight above zero and all three dimensions above zero."""
    if not package:
        return False
    weight = (package.get("weight") or {}).get("value") or 0
    dims = package.get("dimensions") or {}
    return float(weight) > 0 and all(float(dims.get(k) or 0) > 0 for k in ("length", "width", "height"))


def problems(category_id: str, fulfillment_policy_id: str, package: dict | None,
             free_ids: set[str]) -> list[str]:
    """What is wrong with this listing's shipping, if anything."""
    out = []
    is_card = str(category_id) in CARD_CATEGORIES
    if not is_card and fulfillment_policy_id in free_ids:
        out.append("non-card item on a free or flat-rate shipping policy (must be calculated USPS)")
    if not is_card and not package_ok(package):
        out.append("no package weight/size (eBay would default the label to 1 oz, 1x1x1)")
    return out


def repair_package(category_id: str) -> dict[str, Any]:
    p = REPAIR_PACKAGES.get(str(category_id), FALLBACK_PACKAGE)
    return {
        "dimensions": {"length": p["l"], "width": p["w"], "height": p["h"], "unit": "INCH"},
        "weight": {"value": p["oz"], "unit": "OUNCE"},
        "packageType": "PACKAGE_THICK_ENVELOPE",
        "shippingIrregular": False,
    }


def check_offer(client: EbayClient, offer: dict[str, Any], free_ids: set[str]) -> list[str]:
    """Problems with an Inventory API offer and its inventory item."""
    item = client.get_inventory_item(offer["sku"])
    return problems(
        offer.get("categoryId", ""),
        (offer.get("listingPolicies") or {}).get("fulfillmentPolicyId", ""),
        item.get("packageWeightAndSize"),
        free_ids,
    )


def repair_inventory_listing(client: EbayClient, sku: str, category_id: str,
                             fix_policy: bool) -> None:
    """Give an Inventory API listing a package and, if needed, buyer-paid shipping."""
    item = client.get_inventory_item(sku)
    for key in ("sku", "locale", "groupIds", "inventoryItemGroupKeys"):
        item.pop(key, None)
    if not package_ok(item.get("packageWeightAndSize")):
        item["packageWeightAndSize"] = repair_package(category_id)
    else:
        item["packageWeightAndSize"].setdefault("packageType", "PACKAGE_THICK_ENVELOPE")
    client.upsert_inventory_item(sku, item)
    for offer in client.offers_for_sku(sku):
        body = {k: v for k, v in offer.items() if k not in ("offerId", "status", "listing")}
        if fix_policy or str(category_id) not in CARD_CATEGORIES:
            body.setdefault("listingPolicies", {})["fulfillmentPolicyId"] = BUYER_PAID_POLICY
        try:
            client.update_offer(offer["offerId"], body)
        except Exception as exc:  # noqa: BLE001 - eBay refuses price fields during a sale
            if "25019" not in str(exc):
                raise
            body.pop("pricingSummary", None)
            client.update_offer(offer["offerId"], body)
