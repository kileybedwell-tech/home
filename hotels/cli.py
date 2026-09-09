"""Command line interface: ``python -m hotels <command>``."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from .amadeus import AmadeusClient, AmadeusError, Config, ConfigError, load_dotenv, search as amadeus_search
from .search import SPORTSBOOK_MAX, STARS_MAX, Quote, SearchError, nights_between, rank


# ---- presentation -------------------------------------------------------


def _money(amount: float, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "JPY": "¥"}.get(currency)
    text = f"{amount:,.0f}" if currency == "JPY" else f"{amount:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency}"


def _table(rows: Iterable[Sequence[str]], headers: Sequence[str], *, numeric: set[int] = frozenset()) -> str:
    """Fixed-width table; columns in ``numeric`` are right-aligned."""
    rows = [list(r) for r in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: Sequence[str]) -> str:
        return "  ".join(
            (cell.rjust(widths[i]) if i in numeric else cell.ljust(widths[i])) for i, cell in enumerate(row)
        ).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


def _stars(stars: float | None) -> str:
    if stars is None:
        return ""
    return f"{stars:g}★"


def _notes(q: Quote) -> str:
    notes = []
    if q.room:
        notes.append(q.room)
    if q.extras:
        notes.append(q.extras_note())
    if q.wifi is True:
        notes.append("free WiFi")
    elif q.wifi is False:
        notes.append("WiFi costs extra")
    if q.refundable is True:
        notes.append("free cancellation")
    elif q.refundable is False:
        notes.append("non-refundable")
    if q.distance_km is not None:
        notes.append(f"{q.distance_km:.1f} km from centre")
    if q.source:
        notes.append(q.source)
    return "; ".join(notes)


def print_ranking(quotes: list[Quote], *, limit: int, as_json: bool, title: str = "") -> None:
    if as_json:
        print(json.dumps([q.to_dict() for q in quotes[:limit]], indent=2, ensure_ascii=False))
        return
    if not quotes:
        print("No priced rooms found.")
        return
    if title:
        print(title)
    shown = quotes[:limit]
    # (header, cell, right-align, shown?) -- optional columns appear only when
    # some quote fills them, so a live search or a city with no casinos is not
    # padded with empty Sportsbook / Rewards columns.
    columns = [
        ("#", lambda i, q: f"{i}.", False, True),
        ("All-in", lambda i, q: _money(q.all_in, q.currency), True, True),
        ("Per night", lambda i, q: _money(q.per_night, q.currency), True, True),
        ("Quoted", lambda i, q: _money(q.total, q.currency), True, any(q.extras for q in shown)),
        ("Hidden", lambda i, q: f"+{_money(q.extras, q.currency)}" if q.extras else "", True, any(q.extras for q in shown)),
        ("Hotel", lambda i, q: q.hotel, False, True),
        ("Stars", lambda i, q: _stars(q.stars), True, any(q.stars is not None for q in shown)),
        (
            "Sportsbook",
            lambda i, q: f"{q.sportsbook}/{SPORTSBOOK_MAX}" if q.sportsbook is not None else "",
            True,
            any(q.sportsbook is not None for q in shown),
        ),
        ("Rewards", lambda i, q: q.rewards, False, any(q.rewards for q in shown)),
        ("Notes", lambda i, q: _notes(q), False, True),
    ]
    columns = [c for c in columns if c[3]]
    rows = [[cell(i, q) for _, cell, _, _ in columns] for i, q in enumerate(shown, 1)]
    print(_table(rows, [c[0] for c in columns], numeric={i for i, c in enumerate(columns) if c[2]}))
    best = quotes[0]
    print()
    print(
        f"Cheapest: {best.hotel} at {_money(best.all_in, best.currency)} all-in for "
        f"{best.nights} night{'s' if best.nights != 1 else ''} ({_money(best.per_night, best.currency)}/night)."
    )
    hidden = [q for q in shown if q.extras]
    if hidden:
        worst = max(hidden, key=lambda q: q.extras)
        print(
            f"Hidden fees: {len(hidden)} of {len(shown)} quotes leave out fees or tax; "
            f"the biggest gap is {worst.hotel}, quoted {_money(worst.total, worst.currency)} "
            f"but {_money(worst.all_in, worst.currency)} to pay."
        )
    if best.url:
        print(best.url)
    others = {q.currency for q in quotes} - {best.currency}
    if others:
        print(f"Note: quotes in {', '.join(sorted(others))} are listed after {best.currency} ones, not converted.")
    if len(quotes) > limit:
        print(f"({len(quotes) - limit} more; raise --limit to see them)")


# ---- commands -----------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file)
    config = Config.from_env()
    client = AmadeusClient(config)
    nights = nights_between(args.check_in, args.check_out)
    label, quotes = amadeus_search(
        client,
        args.place,
        args.check_in,
        args.check_out,
        adults=args.adults,
        rooms=args.rooms,
        currency=args.currency.upper(),
        radius_km=args.radius,
        max_hotels=args.max_hotels,
    )
    ranked = rank(quotes, refundable_only=args.refundable, max_total=args.max_price, min_sportsbook=args.min_sportsbook, rewards=args.rewards, min_stars=args.min_stars, wifi_only=args.wifi)
    title = (
        f"{label}: {args.check_in} to {args.check_out} ({nights} night{'s' if nights != 1 else ''}), "
        f"{args.adults} adult{'s' if args.adults != 1 else ''}, {args.rooms} room{'s' if args.rooms != 1 else ''}"
    )
    if not args.json:
        if config.environment == "test":
            title += "\n(Amadeus TEST data: a cached sample of hotels, not live rates. "
            title += "Set AMADEUS_ENVIRONMENT=production for real prices.)"
        if not quotes:
            print(title)
            print("No hotel returned a price for those dates. Try a wider --radius or different dates.")
            return 1
        if not ranked:
            print(title)
            print("Prices came back, but none passed your --refundable/--wifi/--max-price/--min-stars/--min-sportsbook/--rewards filters.")
            return 1
    print_ranking(ranked, limit=args.limit, as_json=args.json, title=title)
    return 0


def _read_quotes(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SearchError(f"no such file: {path}")
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SearchError(f"{path} is not valid JSON: {exc}") from None
    if isinstance(data, dict):
        data = data.get("quotes", data.get("hotels"))
    if not isinstance(data, list):
        raise SearchError(f"{path} should hold a JSON list of quotes (or {{\"quotes\": [...]}})")
    return data


def cmd_compare(args: argparse.Namespace) -> int:
    default_nights = nights_between(args.check_in, args.check_out) if args.check_in and args.check_out else args.nights
    raw = _read_quotes(Path(args.file))
    quotes = [Quote.from_dict(item, default_nights=default_nights) for item in raw]
    if not quotes:
        raise SearchError(f"{args.file} has no quotes in it")
    ranked = rank(quotes, refundable_only=args.refundable, max_total=args.max_price, min_sportsbook=args.min_sportsbook, rewards=args.rewards, min_stars=args.min_stars, wifi_only=args.wifi)
    if not ranked and not args.json:
        print("No quote passed your --refundable/--wifi/--max-price/--min-stars/--min-sportsbook/--rewards filters.")
        return 1
    print_ranking(ranked, limit=args.limit, as_json=args.json, title="" if args.json else f"{len(quotes)} quotes from {args.file}")
    return 0


# ---- parser -------------------------------------------------------------


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--refundable", action="store_true", help="only rooms with free cancellation")
    parser.add_argument("--wifi", action="store_true", help="only hotels with free WiFi (unknown is dropped)")
    parser.add_argument("--max-price", type=float, metavar="TOTAL", help="drop anything above this all-in total for the stay")
    parser.add_argument(
        "--min-stars",
        type=float,
        metavar="N",
        help="only hotels of at least this class, e.g. 3.5 (unrated hotels are dropped)",
    )
    parser.add_argument(
        "--min-sportsbook",
        type=int,
        choices=range(1, SPORTSBOOK_MAX + 1),
        metavar="1-5",
        help="only hotels whose sportsbook is rated at least this (unrated hotels are dropped)",
    )
    parser.add_argument(
        "--rewards",
        metavar="NAME",
        help="only hotels in this loyalty programme, matched loosely: 'caesars', 'mgm', 'bonvoy'",
    )
    parser.add_argument("--limit", type=int, default=10, help="how many to show (default 10)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hotels",
        description="Find the cheapest hotel for a stay.",
        epilog=(
            "examples:\n"
            "  python -m hotels search Seattle 2026-10-03 2026-10-05 --adults 2\n"
            "  python -m hotels search LAS 2026-11-20 2026-11-23 --refundable --max-price 400\n"
            "  python -m hotels compare quotes.json --check-in 2026-10-03 --check-out 2026-10-05\n"
            "  python -m hotels compare examples/vegas-quotes.json --nights 2 --wifi --min-stars 3.5 --min-sportsbook 3\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="live prices from Amadeus for every hotel near a city")
    s.add_argument("place", help="city name or 3-letter city/airport code (Seattle, SEA, NYC)")
    s.add_argument("check_in", metavar="CHECK_IN", help="YYYY-MM-DD")
    s.add_argument("check_out", metavar="CHECK_OUT", help="YYYY-MM-DD")
    s.add_argument("--adults", type=int, default=1)
    s.add_argument("--rooms", type=int, default=1)
    s.add_argument("--currency", default="USD")
    s.add_argument("--radius", type=int, default=10, metavar="KM", help="search radius from the city centre (default 10)")
    s.add_argument("--max-hotels", type=int, default=60, help="how many hotels to price, nearest first (default 60)")
    s.add_argument("--env-file", default=".env", help=argparse.SUPPRESS)
    _add_filters(s)
    s.set_defaults(func=cmd_search)

    c = sub.add_parser(
        "compare",
        help="rank prices you collected yourself (Booking, Expedia, the hotel's own site...) from a JSON or CSV file",
        description=(
            "Each quote needs a hotel name and either a total or per_night price; "
            "optional: nights, currency, source, room, url, refundable, wifi, stars (1-5), sportsbook (1-5), rewards, "
            "and for fees the quote leaves out: fee_per_night (or fees for the stay), fee_note, tax_pct. "
            "Ranking uses the all-in price with those added. "
            "JSON: a list of objects. CSV: a header row with those column names."
        ),
    )
    c.add_argument("file", help="quotes.json or quotes.csv")
    c.add_argument("--check-in", help="YYYY-MM-DD, used to work out nights for per-night quotes")
    c.add_argument("--check-out", help="YYYY-MM-DD")
    c.add_argument("--nights", type=int, default=1, help="nights for quotes that give a per-night price (default 1)")
    _add_filters(c)
    c.set_defaults(func=cmd_compare)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SearchError, ConfigError, AmadeusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
