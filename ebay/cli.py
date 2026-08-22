"""Command line interface: ``python -m ebay <command>``."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from typing import Any, Iterable, Sequence

from .auth import (
    AuthError,
    TokenStore,
    authorization_url,
    exchange_code,
    parse_authorization_code,
)
from .client import EbayClient
from .config import DEFAULT_SCOPES, READONLY_SCOPES, SANDBOX, Config, ConfigError, load_dotenv
from .http import EbayError


# ---- presentation -------------------------------------------------------


def _table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    if not rows:
        return "(none)"
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    rule = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = [
        "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in rows
    ]
    return "\n".join([line, rule, *body])


def _truncate(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + "…"


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _money(price: dict[str, Any] | None) -> str:
    if not price:
        return "-"
    return f"{price.get('value', '?')} {price.get('currency', '')}".strip()


# ---- context ------------------------------------------------------------


def _build(args: argparse.Namespace) -> tuple[Config, TokenStore]:
    load_dotenv(args.env_file)
    environment = SANDBOX if args.sandbox else None
    config = Config.from_env(environment)
    if args.marketplace:
        config = Config(
            client_id=config.client_id,
            client_secret=config.client_secret,
            redirect_uri=config.redirect_uri,
            environment=config.environment,
            marketplace_id=args.marketplace,
            content_language=config.content_language,
            scopes=config.scopes,
        )
    return config, TokenStore(config)


def _client(args: argparse.Namespace) -> EbayClient:
    config, store = _build(args)
    return EbayClient(config, store)


# ---- commands -----------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> int:
    config, store = _build(args)
    scopes = READONLY_SCOPES if args.readonly else DEFAULT_SCOPES
    state = secrets.token_urlsafe(16)
    url = authorization_url(config, scopes=scopes, state=state, prompt_login=args.force)

    code = args.code
    if not code:
        print(f"Connecting to eBay ({config.environment}).\n")
        print("1. Open this URL and sign in as the seller account:\n")
        print(f"   {url}\n")
        print("2. Approve the requested permissions.")
        print("3. eBay redirects to your RuName's URL. Copy that whole URL from")
        print("   the address bar (it contains ?code=...) and paste it below.\n")
        try:
            code = input("Redirect URL (or bare code): ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.", file=sys.stderr)
            return 1

    tokens = exchange_code(config, parse_authorization_code(code))
    store.save(tokens)
    hours = (tokens.access_expires_at - time.time()) / 3600
    days = (tokens.refresh_expires_at - time.time()) / 86400
    print(f"\nConnected. Tokens saved to {store.path} (mode 0600).")
    print(f"  access token valid for  ~{hours:.1f} hours (auto-refreshed)")
    print(f"  refresh token valid for ~{days:.0f} days")
    print(f"  scopes: {len(tokens.scopes)} granted")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config, store = _build(args)
    print(f"environment: {config.environment}")
    print(f"marketplace: {config.marketplace_id}")
    print(f"token file:  {store.path}")
    tokens = store.load()
    if tokens is None:
        print("state:       not connected — run `python -m ebay login`")
        return 1
    if tokens.refresh_expired:
        print("state:       refresh token expired — run `python -m ebay login` again")
        return 1
    print(
        "state:       connected "
        f"(access {'expired, will refresh' if tokens.access_expired else 'valid'}, "
        f"refresh valid {(tokens.refresh_expires_at - time.time()) / 86400:.0f} more days)"
    )
    privileges = EbayClient(config, store).privileges()
    limit = privileges.get("sellingLimit", {})
    print(f"payments:    registered={privileges.get('sellerRegistrationCompleted')}")
    if limit:
        print(f"selling cap: {limit.get('quantity')} items / {_money(limit.get('amount'))}")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    _, store = _build(args)
    print(f"Removed {store.path}." if store.clear() else "No saved tokens to remove.")
    return 0


def cmd_listings(args: argparse.Namespace) -> int:
    client = _client(args)
    items = list(client.inventory_items(max_items=args.limit))
    if args.json:
        if args.with_offers:
            for item in items:
                item["offers"] = client.offers_for_sku(item.get("sku", ""))
        _emit(items)
        return 0

    rows = []
    for item in items:
        sku = item.get("sku", "")
        title = item.get("product", {}).get("title", "")
        quantity = (
            item.get("availability", {})
            .get("shipToLocationAvailability", {})
            .get("quantity")
        )
        price, status = "-", "-"
        if args.with_offers:
            offers = client.offers_for_sku(sku)
            if offers:
                price = _money(offers[0].get("pricingSummary", {}).get("price"))
                status = offers[0].get("status", "-")
        rows.append([sku, _truncate(title, 48), str(quantity if quantity is not None else "-"), price, status])
    headers = ["SKU", "TITLE", "QTY", "PRICE", "STATUS"]
    print(_table(rows, headers))
    print(f"\n{len(rows)} inventory item(s).")
    if not args.with_offers and rows:
        print("Pass --with-offers for price and live/unpublished status.")
    return 0


def cmd_item(args: argparse.Namespace) -> int:
    client = _client(args)
    payload = client.get_inventory_item(args.sku)
    payload["offers"] = client.offers_for_sku(args.sku)
    _emit(payload)
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    client = _client(args)
    filters = []
    if args.unshipped:
        filters.append("orderfulfillmentstatus:{NOT_STARTED|IN_PROGRESS}")
    if args.since:
        filters.append(f"creationdate:[{args.since}..]")
    order_filter = ",".join(filters) or None

    orders = list(client.orders(order_filter=order_filter, max_items=args.limit))
    if args.json:
        _emit(orders)
        return 0

    rows = []
    for order in orders:
        titles = ", ".join(
            line.get("title", "") for line in order.get("lineItems", [])[:2]
        )
        rows.append(
            [
                order.get("orderId", ""),
                (order.get("creationDate", "") or "")[:10],
                _truncate(order.get("buyer", {}).get("username", ""), 18),
                _money(order.get("pricingSummary", {}).get("total")),
                order.get("orderFulfillmentStatus", ""),
                _truncate(titles, 36),
            ]
        )
    print(_table(rows, ["ORDER", "DATE", "BUYER", "TOTAL", "FULFILMENT", "ITEMS"]))
    print(f"\n{len(rows)} order(s).")
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    _emit(_client(args).get_order(args.order_id))
    return 0


def cmd_policies(args: argparse.Namespace) -> int:
    client = _client(args)
    groups = (
        ("fulfillment", client.fulfillment_policies(), "fulfillmentPolicyId"),
        ("payment", client.payment_policies(), "paymentPolicyId"),
        ("return", client.return_policies(), "returnPolicyId"),
    )
    if args.json:
        _emit({name: policies for name, policies, _ in groups})
        return 0
    for name, policies, id_key in groups:
        print(f"\n{name} policies")
        print(
            _table(
                [[p.get(id_key, ""), _truncate(p.get("name", ""), 40)] for p in policies],
                ["ID", "NAME"],
            )
        )
    print("\nThese IDs are what an offer's listingPolicies must reference.")
    return 0


def cmd_price(args: argparse.Namespace) -> int:
    client = _client(args)
    client.update_price_quantity(args.sku, price=args.price, quantity=args.quantity)
    changes = []
    if args.price:
        changes.append(f"price={args.price}")
    if args.quantity is not None:
        changes.append(f"quantity={args.quantity}")
    print(f"Updated {args.sku}: {', '.join(changes)}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    result = _client(args).publish_offer(args.offer_id)
    listing_id = result.get("listingId")
    print(f"Published offer {args.offer_id}" + (f" as listing {listing_id}." if listing_id else "."))
    return 0


def cmd_withdraw(args: argparse.Namespace) -> int:
    _client(args).withdraw_offer(args.offer_id)
    print(f"Withdrew offer {args.offer_id}; the offer is kept and can be re-published.")
    return 0


def cmd_ship(args: argparse.Namespace) -> int:
    client = _client(args)
    fulfillment: dict[str, Any] = {}
    if args.tracking:
        fulfillment["trackingNumber"] = args.tracking
    if args.carrier:
        fulfillment["shippingCarrierCode"] = args.carrier
    if args.line_item:
        order = client.get_order(args.order_id)
        wanted = set(args.line_item)
        fulfillment["lineItems"] = [
            {"lineItemId": line["lineItemId"], "quantity": line.get("quantity", 1)}
            for line in order.get("lineItems", [])
            if line.get("lineItemId") in wanted
        ]
    result = client.create_shipping_fulfillment(args.order_id, fulfillment)
    print(f"Marked {args.order_id} shipped.")
    if result:
        _emit(result)
    return 0


# ---- wiring -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ebay", description="Connect to and manage your eBay seller listings."
    )
    parser.add_argument("--sandbox", action="store_true", help="use the eBay sandbox")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load (default: .env)")
    parser.add_argument("--marketplace", help="override EBAY_MARKETPLACE_ID, e.g. EBAY_GB")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="authorize this app against your eBay account")
    p.add_argument("--code", help="authorization code or redirect URL (skips the prompt)")
    p.add_argument("--readonly", action="store_true", help="request read-only scopes only")
    p.add_argument("--force", action="store_true", help="force a fresh eBay sign-in")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", help="show connection state and selling privileges")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("logout", help="delete the saved tokens")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("listings", help="list your inventory items")
    p.add_argument("--limit", type=int, default=25, help="max items (default: 25)")
    p.add_argument("--with-offers", action="store_true", help="also fetch price/status per SKU")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_listings)

    p = sub.add_parser("item", help="show one SKU with its offers")
    p.add_argument("sku")
    p.set_defaults(func=cmd_item)

    p = sub.add_parser("orders", help="list recent orders")
    p.add_argument("--limit", type=int, default=25, help="max orders (default: 25)")
    p.add_argument("--unshipped", action="store_true", help="only orders awaiting fulfilment")
    p.add_argument("--since", help="ISO-8601 instant, e.g. 2026-01-01T00:00:00.000Z")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("order", help="show one order in full")
    p.add_argument("order_id")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("policies", help="list business policy IDs offers must reference")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_policies)

    p = sub.add_parser("price", help="change price and/or quantity for a SKU")
    p.add_argument("sku")
    p.add_argument("--price", help="new price, e.g. 24.99")
    p.add_argument("--quantity", type=int, help="new available quantity")
    p.set_defaults(func=cmd_price)

    p = sub.add_parser("publish", help="push an offer live")
    p.add_argument("offer_id")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("withdraw", help="end a live listing, keeping the offer")
    p.add_argument("offer_id")
    p.set_defaults(func=cmd_withdraw)

    p = sub.add_parser("ship", help="mark an order shipped")
    p.add_argument("order_id")
    p.add_argument("--tracking", help="tracking number")
    p.add_argument("--carrier", help="eBay carrier code, e.g. USPS, FEDEX, UPS")
    p.add_argument("--line-item", action="append", help="limit to this lineItemId (repeatable)")
    p.set_defaults(func=cmd_ship)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except (ConfigError, AuthError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except EbayError as exc:
        print(f"eBay API error: {exc}", file=sys.stderr)
        if exc.status in (401, 403):
            print(
                "hint: the token may lack the required scope — "
                "re-run `python -m ebay login` without --readonly.",
                file=sys.stderr,
            )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
