"""Command line interface: ``python -m ebay <command>``."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
import time
from collections import defaultdict
from typing import Any, Iterable, Sequence

from .auth import (
    AuthError,
    TokenStore,
    authorization_url,
    exchange_code,
    parse_authorization_code,
)
from .client import EbayClient
from .config import (
    DEFAULT_SCOPES,
    PRODUCTION,
    READONLY_SCOPES,
    SANDBOX,
    Config,
    ConfigError,
    check_credentials,
    load_dotenv,
    write_env_file,
)
from .http import EbayError
from .policies import create_missing, inventory_location
from .listing import (
    CONDITIONS,
    MAX_TITLE,
    ListingDraft,
    ListingError,
    create_listing,
    upload_photos,
)


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


def _listing_url(listing_id: str) -> str:
    return f"https://www.ebay.com/itm/{listing_id}"


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


def _ask(prompt: str, *, default: str = "", secret: bool = False) -> str:
    """Prompt once, showing any default, and never echo a secret."""
    label = f"  {prompt}" + (f" [{default}]" if default else "") + ": "
    try:
        value = (getpass.getpass(label) if secret else input(label)).strip()
    except (EOFError, KeyboardInterrupt):
        raise ValueError("setup cancelled") from None
    return value or default


def cmd_setup(args: argparse.Namespace) -> int:
    target = args.env_file
    if os.path.exists(target) and not args.force:
        print(f"{target} already exists. Re-run with --force to replace it.", file=sys.stderr)
        return 2

    print("eBay credentials\n")
    print("  Get these from https://developer.ebay.com/my/keys")
    print("  Pick the Production keyset, or the Sandbox one for a trial run.\n")

    client_id = _ask("App ID (Client ID)")
    client_secret = _ask("Cert ID (Client Secret)", secret=True)
    print("\n  The RuName is on the User Tokens tab of that same page, under")
    print("  'Get a Token from eBay via Your Application'. It is a name, not a URL.\n")
    redirect = _ask("RuName")

    print()
    environment = _ask("Environment (production/sandbox)", default=SANDBOX if args.sandbox else PRODUCTION)
    marketplace = _ask("Marketplace", default="EBAY_US")
    language = _ask("Content language", default="en-US")

    missing = [
        label
        for label, value in (
            ("App ID", client_id),
            ("Cert ID", client_secret),
            ("RuName", redirect),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} cannot be blank")
    if environment not in (PRODUCTION, SANDBOX):
        raise ValueError(f"environment must be {PRODUCTION} or {SANDBOX}, got {environment!r}")

    values = {
        "EBAY_CLIENT_ID": client_id,
        "EBAY_CLIENT_SECRET": client_secret,
        "EBAY_REDIRECT_URI": redirect,
        "EBAY_ENVIRONMENT": environment,
        "EBAY_MARKETPLACE_ID": marketplace,
        "EBAY_CONTENT_LANGUAGE": language,
    }
    warnings = check_credentials(values)
    written = write_env_file(target, values)

    print(f"\nWrote {written} (mode 0600). It is gitignored — keep it that way.")
    for warning in warnings:
        print(f"\nheads up: {warning}")
    print("\nNext: python -m ebay login")
    return 0


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


def cmd_find(args: argparse.Namespace) -> int:
    """Search every active listing on the account for a possible duplicate.

    `listings` only sees SKUs created through the Sell Inventory API. Most
    accounts also carry listings made on eBay's website, through Seller Hub
    bulk tools, File Exchange, or third-party crosslisting tools - those are
    invisible to `listings` but do show up here, since this reads the same
    feed My eBay's Active tab does. Run this before drafting anything new.
    """
    client = _client(args)
    words = [w.lower() for w in args.query.split() if w]
    matches = []
    for item in client.active_listings():
        title = item.get("title", "").lower()
        if all(word in title for word in words):
            matches.append(item)
            if len(matches) >= args.limit:
                break

    if args.json:
        _emit(matches)
        return 0

    if not matches:
        print(f"No active listing matches {args.query!r}.")
        return 0
    rows = [
        [
            item.get("itemId", ""),
            item.get("sku", "") or "-",
            _truncate(item.get("title", ""), 48),
            _money({"value": item.get("price", ""), "currency": item.get("currency", "")}),
        ]
        for item in matches
    ]
    print(_table(rows, ["ITEM ID", "SKU", "TITLE", "PRICE"]))
    print(f"\n{len(matches)} possible match(es) for {args.query!r}.")
    if len(matches) >= args.limit:
        print(f"(stopped at --limit {args.limit}; there may be more)")
    return 0


def _normalize_title(title: str) -> str:
    """Fold case/punctuation/whitespace so near-identical titles group together."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def cmd_duplicates(args: argparse.Namespace) -> int:
    """Group active listings by near-identical title to surface likely dupes.

    Scans every active listing on the account (however it was made - see
    `find`), not just this tool's own SKUs. Grouping is deliberately strict
    (case/punctuation-insensitive exact match) rather than fuzzy, so it flags
    real accidental re-listings without drowning them in similar-but-
    different cards.
    """
    client = _client(args)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    for item in client.active_listings(max_items=args.limit):
        total += 1
        groups[_normalize_title(item.get("title", ""))].append(item)
    dupes = {key: items for key, items in groups.items() if len(items) > 1}

    if args.json:
        _emit(list(dupes.values()))
        return 0

    if not dupes:
        print(f"No duplicate titles found among {total} active listing(s).")
        return 0

    for items in dupes.values():
        print(items[0].get("title", ""))
        rows = [
            [
                item.get("itemId", ""),
                item.get("sku", "") or "-",
                _money({"value": item.get("price", ""), "currency": item.get("currency", "")}),
                item.get("viewItemUrl", ""),
            ]
            for item in items
        ]
        print(_table(rows, ["ITEM ID", "SKU", "PRICE", "URL"]))
        print()
    print(f"{len(dupes)} duplicate title group(s) among {total} active listing(s).")
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


#: Programs a seller needs before the Sell APIs will do anything useful.
REQUIRED_PROGRAMS = ("SELLING_POLICY_MANAGEMENT",)


def cmd_programs(args: argparse.Namespace) -> int:
    """Show program enrolment, and enrol with --opt-in."""
    client = _client(args)
    enrolled = client.opted_in_programs()

    if args.opt_in:
        for program in REQUIRED_PROGRAMS:
            if program in enrolled:
                print(f"{program}: already enrolled")
                continue
            client.opt_in(program)
            print(f"{program}: enrolled")
        enrolled = client.opted_in_programs()

    if args.json:
        _emit(enrolled)
        return 0

    print(_table([[p] for p in enrolled], ["ENROLLED PROGRAM"]))
    missing = [p for p in REQUIRED_PROGRAMS if p not in enrolled]
    if missing:
        print(f"\nMissing: {', '.join(missing)}")
        print("Enrol with: python -m ebay programs --opt-in")
        return 1
    print("\nAll required programs enrolled.")
    return 0


def cmd_policies(args: argparse.Namespace) -> int:
    client = _client(args)

    if args.create:
        options = {
            "fulfillment": {
                "handling_days": args.handling_days,
                "cost": args.ship_cost,
                "free_shipping": args.free_shipping,
                **({"service": args.shipping_service} if args.shipping_service else {}),
            },
            "return": {"days": args.return_days},
        }
        result = create_missing(client, builder_options=options, on_event=print)
        if result["failed"]:
            print(
                "\nSome policies were rejected. The message above is eBay's own "
                "wording — usually a shipping service code it does not accept.\n"
                "Try another with --shipping-service, e.g. USPSPriority or "
                "USPSFirstClass.",
                file=sys.stderr,
            )
            return 3
        print()

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
    """Publish one or many offers, reporting each independently.

    A failure on one offer must not strand the rest of an approved batch, so
    every id is attempted and the failures are summarised at the end.
    """
    client = _client(args)
    published: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for offer_id in args.offer_id:
        try:
            result = client.publish_offer(offer_id)
            listing_id = result.get("listingId", "")
            published.append((offer_id, listing_id))
            if listing_id:
                print(f"{offer_id}  ->  listing {listing_id}\n  {_listing_url(listing_id)}")
            else:
                print(f"{offer_id}  ->  listing (no id returned)")
        except EbayError as exc:
            failed.append((offer_id, str(exc)))
            print(f"{offer_id}  ->  FAILED: {exc}", file=sys.stderr)

    if len(args.offer_id) > 1:
        print(f"\n{len(published)} published, {len(failed)} failed.")
    if failed:
        print("Retry the failures once fixed:", file=sys.stderr)
        print(
            "  python -m ebay publish " + " ".join(o for o, _ in failed), file=sys.stderr
        )
        return 3
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    """Everything created but not yet live — the approval queue."""
    client = _client(args)
    rows = []
    offer_ids = []
    for item in client.inventory_items(max_items=args.limit):
        sku = item.get("sku", "")
        for offer in client.offers_for_sku(sku):
            if offer.get("status") == "PUBLISHED":
                continue
            offer_ids.append(offer.get("offerId", ""))
            rows.append(
                [
                    offer.get("offerId", ""),
                    _truncate(sku, 20),
                    _truncate(item.get("product", {}).get("title", ""), 40),
                    _money(offer.get("pricingSummary", {}).get("price")),
                    offer.get("status", "UNPUBLISHED"),
                ]
            )

    if args.json:
        _emit(rows)
        return 0

    print(_table(rows, ["OFFER", "SKU", "TITLE", "PRICE", "STATUS"]))
    if not rows:
        print("\nNothing awaiting approval.")
        return 0
    print(f"\n{len(rows)} awaiting approval. To publish them all:\n")
    print("  python -m ebay publish " + " ".join(offer_ids))
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


def _parse_aspects(pairs: Iterable[str] | None) -> dict[str, list[str]]:
    """Turn repeated --aspect Brand=Canon flags into eBay's {name: [values]}."""
    aspects: dict[str, list[str]] = {}
    for pair in pairs or ():
        name, sep, value = pair.partition("=")
        if not sep or not name.strip() or not value.strip():
            raise ValueError(f"--aspect expects NAME=VALUE, got {pair!r}")
        aspects.setdefault(name.strip(), []).append(value.strip())
    return aspects


def cmd_create(args: argparse.Namespace) -> int:
    client = _client(args)

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as handle:
            data = json.load(handle)
        known = {f for f in ListingDraft.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"unknown field(s) in {args.from_file}: {', '.join(sorted(unknown))}"
            )
        draft = ListingDraft(**data)
        # Flags override the file, so a value the file cannot know - a category
        # id looked up against the live account - does not require editing it.
        for attribute, value in (
            ("title", args.title),
            ("price", args.price),
            ("category_id", args.category),
            ("description", args.description),
            ("condition_description", args.condition_description),
        ):
            if value:
                setattr(draft, attribute, value)
        if args.quantity != 1:
            draft.quantity = args.quantity
        if args.image:
            draft.image_urls = list(draft.image_urls) + list(args.image)
    else:
        missing = [
            flag
            for flag, value in (
                ("--title", args.title),
                ("--price", args.price),
                ("--category", args.category),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"{', '.join(missing)} required (or use --from-file). "
                "Run `python -m ebay categories \'your item\'` to find a category id."
            )
        draft = ListingDraft(
            sku=args.sku,
            title=args.title,
            price=args.price,
            category_id=args.category,
            description=args.description or "",
            quantity=args.quantity,
            condition=args.condition,
            condition_description=args.condition_description or "",
            image_urls=list(args.image or []),
            aspects=_parse_aspects(args.aspect),
            currency=args.currency,
        )

    overrides = {
        key: value
        for key, value in (
            ("fulfillmentPolicyId", args.fulfillment_policy),
            ("paymentPolicyId", args.payment_policy),
            ("returnPolicyId", args.return_policy),
        )
        if value
    }
    result = create_listing(
        client,
        draft,
        policy_overrides=overrides or None,
        location=args.location,
        photos=list(args.photo or []),
        publish=not args.draft,
        dry_run=args.dry_run,
    )

    if args.json or args.dry_run:
        _emit(result)
        return 0
    verb = "Updated" if result["offerReused"] else "Created"
    print(f"{verb} offer {result['offerId']} for SKU {draft.sku}.")
    if result["published"]:
        print(f"Published as listing {result['listingId']}.")
        print(_listing_url(result["listingId"]))
    else:
        print(f"Left unpublished. Run `python -m ebay publish {result['offerId']}` when ready.")
    return 0


def cmd_images(args: argparse.Namespace) -> int:
    client = _client(args)
    urls = upload_photos(client, list(args.photo))
    if args.json:
        _emit(urls)
        return 0
    for path, url in zip(args.photo, urls):
        print(f"{path}\n  -> {url}")
    print(f"\n{len(urls)} image(s) hosted on eBay Picture Services.")
    print("Unused EPS images are deleted after 30 days.")
    return 0


def cmd_categories(args: argparse.Namespace) -> int:
    client = _client(args)
    suggestions = client.suggest_categories(args.query)
    if args.json:
        _emit(suggestions)
        return 0
    rows = []
    for suggestion in suggestions[: args.limit]:
        category = suggestion.get("category", {})
        ancestors = suggestion.get("categoryTreeNodeAncestors", []) or []
        path = " > ".join(
            a.get("categoryName", "")
            for a in reversed(ancestors)
            if a.get("categoryName")
        )
        rows.append(
            [
                category.get("categoryId", ""),
                _truncate(category.get("categoryName", ""), 30),
                _truncate(path, 52),
            ]
        )
    print(_table(rows, ["CATEGORY ID", "NAME", "PATH"]))
    print("\nPass the id to `create --category`.")
    return 0


def cmd_condition_policy(args: argparse.Namespace) -> int:
    client = _client(args)
    policies = client.item_condition_policies(args.category)
    if args.json:
        _emit(policies)
        return 0
    if not policies:
        print(f"No condition policy found for category {args.category}.")
        return 0
    for policy in policies:
        if not policy.get("itemConditionRequired", True):
            print("condition is optional for this category.")
        for condition in policy.get("itemConditions", []):
            print(f"\n{condition['conditionId']}  {condition.get('conditionDescription', '')}")
            help_text = condition.get("conditionHelpText")
            if help_text:
                print(f"  {help_text}")
            for descriptor in condition.get("conditionDescriptors", []):
                constraint = descriptor.get("conditionDescriptorConstraint", {})
                usage = constraint.get("usage", "")
                print(
                    f"  descriptor {descriptor['conditionDescriptorId']} "
                    f"{descriptor.get('conditionDescriptorName', '')} ({usage})"
                )
                for value in descriptor.get("conditionDescriptorValues", []):
                    print(
                        f"    {value['conditionDescriptorValueId']}  "
                        f"{value.get('conditionDescriptorValueName', '')}"
                    )
    print(
        "\nIn a --from-file draft, set condition_id and condition_descriptors "
        '(e.g. "condition_id": "4000", "condition_descriptors": {"40001": "400010"}).'
    )
    return 0


def cmd_locations(args: argparse.Namespace) -> int:
    client = _client(args)

    if args.create:
        if not args.postal_code:
            raise ValueError("--postal-code is required with --create")
        existing = {loc.get("merchantLocationKey") for loc in client.inventory_locations()}
        if args.key in existing:
            print(f"{args.key}: already exists")
        else:
            client.create_location(
                args.key,
                inventory_location(
                    name=args.name,
                    postal_code=args.postal_code,
                    country=args.country,
                    address_line1=args.address or "",
                    city=args.city or "",
                    state=args.state or "",
                ),
            )
            print(f"{args.key}: created\n")

    locations = client.inventory_locations()
    if args.json:
        _emit(locations)
        return 0
    rows = [
        [
            loc.get("merchantLocationKey", ""),
            _truncate(loc.get("name", ""), 28),
            loc.get("merchantLocationStatus", ""),
            _truncate(
                loc.get("location", {}).get("address", {}).get("postalCode", ""), 12
            ),
        ]
        for loc in locations
    ]
    print(_table(rows, ["KEY", "NAME", "STATUS", "POSTCODE"]))
    if not rows:
        print("\nNo locations. An offer cannot publish until one exists.")
    return 0


# ---- wiring -------------------------------------------------------------


#: Flags that apply to every command, and their defaults when unspecified.
GLOBAL_DEFAULTS = {"sandbox": False, "env_file": ".env", "marketplace": None}


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the global flags, leaving them unset unless actually passed.

    SUPPRESS matters: the same flags are attached to the top-level parser and
    to every subparser so that either order works, and without it whichever
    parser ran last would clobber a value the other had set.
    """
    parser.add_argument(
        "--sandbox", action="store_true", default=argparse.SUPPRESS,
        help="use the eBay sandbox",
    )
    parser.add_argument(
        "--env-file", default=argparse.SUPPRESS,
        help="dotenv file to load (default: .env)",
    )
    parser.add_argument(
        "--marketplace", default=argparse.SUPPRESS,
        help="override EBAY_MARKETPLACE_ID, e.g. EBAY_GB",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ebay", description="Connect to and manage your eBay seller listings."
    )
    _add_global_flags(parser)

    # Accepted on subcommands too, so `ebay --sandbox setup` and
    # `ebay setup --sandbox` both work rather than one being a confusing error.
    common = argparse.ArgumentParser(add_help=False)
    _add_global_flags(common)

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", parents=[common], help="prompt for your keys and write .env")
    p.add_argument("--force", action="store_true", help="replace an existing .env")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("login", parents=[common], help="authorize this app against your eBay account")
    p.add_argument("--code", help="authorization code or redirect URL (skips the prompt)")
    p.add_argument("--readonly", action="store_true", help="request read-only scopes only")
    p.add_argument("--force", action="store_true", help="force a fresh eBay sign-in")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", parents=[common], help="show connection state and selling privileges")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("logout", parents=[common], help="delete the saved tokens")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("listings", parents=[common], help="list your inventory items")
    p.add_argument("--limit", type=int, default=25, help="max items (default: 25)")
    p.add_argument("--with-offers", action="store_true", help="also fetch price/status per SKU")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_listings)

    p = sub.add_parser(
        "find", parents=[common],
        help="search ALL active listings (however they were made) for a possible duplicate",
    )
    p.add_argument("query", help="keywords to match against listing titles, e.g. 'chatot perap'")
    p.add_argument("--limit", type=int, default=20, help="max matches (default: 20)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser(
        "duplicates", parents=[common],
        help="scan ALL active listings for likely accidental re-listings",
    )
    p.add_argument("--limit", type=int, help="max listings to scan (default: all)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser("create", parents=[common], help="create a listing: inventory item, offer, publish")
    p.add_argument("sku")
    p.add_argument("--title", help=f"listing title, max {MAX_TITLE} characters")
    p.add_argument("--price", help="e.g. 189.00")
    p.add_argument("--category", help="leaf category id; see `ebay categories`")
    p.add_argument("--description", help="listing description (defaults to the title)")
    p.add_argument("--quantity", type=int, default=1, help="stock available (default: 1)")
    p.add_argument(
        "--condition", default="NEW", choices=CONDITIONS, metavar="CONDITION",
        help="item condition (default: NEW); NEW, USED_GOOD, FOR_PARTS_OR_NOT_WORKING, ...",
    )
    p.add_argument("--condition-description", help="free text about wear or defects")
    p.add_argument("--photo", action="append", help="local image file to upload (repeatable)")
    p.add_argument("--image", action="append", help="already-hosted https image URL (repeatable)")
    p.add_argument("--aspect", action="append", help="item specific, NAME=VALUE (repeatable)")
    p.add_argument("--currency", default="USD", help="price currency (default: USD)")
    p.add_argument("--location", help="merchantLocationKey to ship from")
    p.add_argument("--fulfillment-policy", help="fulfillmentPolicyId override")
    p.add_argument("--payment-policy", help="paymentPolicyId override")
    p.add_argument("--return-policy", help="returnPolicyId override")
    p.add_argument("--from-file", help="JSON file of ListingDraft fields instead of flags")
    p.add_argument("--draft", action="store_true", help="create the offer but do not publish")
    p.add_argument("--dry-run", action="store_true", help="print the payloads, call nothing")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("images", parents=[common], help="upload local photos, print their eBay URLs")
    p.add_argument("photo", nargs="+", help="local image file(s)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("categories", parents=[common], help="find a leaf category id for an item")
    p.add_argument("query", help="describe the item, e.g. '35mm film camera'")
    p.add_argument("--limit", type=int, default=10, help="max suggestions (default: 10)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_categories)

    p = sub.add_parser(
        "condition-policy", parents=[common],
        help="show valid condition ids/descriptors for a category (trading cards, coins, ...)",
    )
    p.add_argument("category", help="leaf category id; see `ebay categories`")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_condition_policy)

    p = sub.add_parser("locations", parents=[common], help="list or create inventory locations")
    p.add_argument("--create", action="store_true", help="create a location")
    p.add_argument("--key", default="home", help="merchantLocationKey (default: home)")
    p.add_argument("--name", default="Home", help="display name (default: Home)")
    p.add_argument("--postal-code", help="postal code you ship from (required with --create)")
    p.add_argument("--country", default="US", help="ISO country code (default: US)")
    p.add_argument("--address", help="street address (optional)")
    p.add_argument("--city", help="city (optional)")
    p.add_argument("--state", help="state or province (optional)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_locations)

    p = sub.add_parser("item", parents=[common], help="show one SKU with its offers")
    p.add_argument("sku")
    p.set_defaults(func=cmd_item)

    p = sub.add_parser("orders", parents=[common], help="list recent orders")
    p.add_argument("--limit", type=int, default=25, help="max orders (default: 25)")
    p.add_argument("--unshipped", action="store_true", help="only orders awaiting fulfilment")
    p.add_argument("--since", help="ISO-8601 instant, e.g. 2026-01-01T00:00:00.000Z")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("order", parents=[common], help="show one order in full")
    p.add_argument("order_id")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("programs", parents=[common], help="show or join eBay seller programs")
    p.add_argument("--opt-in", action="store_true", help="enrol in the required programs")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_programs)

    p = sub.add_parser("policies", parents=[common], help="list or create business policies")
    p.add_argument("--create", action="store_true", help="create any of the three that are missing")
    p.add_argument("--handling-days", type=int, default=1, help="handling time (default: 1)")
    p.add_argument("--ship-cost", default="5.00", help="flat domestic shipping cost (default: 5.00)")
    p.add_argument("--free-shipping", action="store_true", help="offer free domestic shipping")
    p.add_argument("--return-days", type=int, default=30, help="return window (default: 30)")
    p.add_argument(
        "--shipping-service", default=None,
        help="pin one eBay shipping service code instead of trying the known-good list",
    )
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_policies)

    p = sub.add_parser("price", parents=[common], help="change price and/or quantity for a SKU")
    p.add_argument("sku")
    p.add_argument("--price", help="new price, e.g. 24.99")
    p.add_argument("--quantity", type=int, help="new available quantity")
    p.set_defaults(func=cmd_price)

    p = sub.add_parser("publish", parents=[common], help="push one or more offers live")
    p.add_argument("offer_id", nargs="+", help="offer id(s) to publish")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("pending", parents=[common], help="offers created but not yet live (approval queue)")
    p.add_argument("--limit", type=int, default=200, help="max SKUs to scan (default: 200)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("withdraw", parents=[common], help="end a live listing, keeping the offer")
    p.add_argument("offer_id")
    p.set_defaults(func=cmd_withdraw)

    p = sub.add_parser("ship", parents=[common], help="mark an order shipped")
    p.add_argument("order_id")
    p.add_argument("--tracking", help="tracking number")
    p.add_argument("--carrier", help="eBay carrier code, e.g. USPS, FEDEX, UPS")
    p.add_argument("--line-item", action="append", help="limit to this lineItemId (repeatable)")
    p.set_defaults(func=cmd_ship)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    for name, default in GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    try:
        return args.func(args)
    except (ConfigError, AuthError, ListingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except EbayError as exc:
        print(f"eBay API error: {exc}", file=sys.stderr)
        if any(str(e.get("errorId")) == "20403" for e in exc.errors):
            print(
                "hint: this account is not opted in to eBay Business Policies, "
                "which offers need to publish. Fix it with:\n"
                "  python -m ebay programs --opt-in",
                file=sys.stderr,
            )
        elif exc.status in (401, 403):
            print(
                "hint: the token may lack the required scope — "
                "re-run `python -m ebay login` without --readonly.",
                file=sys.stderr,
            )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
