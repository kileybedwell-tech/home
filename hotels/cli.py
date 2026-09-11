"""Command line interface: ``python -m hotels <command>``."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from .amadeus import AmadeusClient, AmadeusError, Config, ConfigError, load_dotenv, search as amadeus_search
from .travel import IRS_RATE, Bus, Drive, Fly, Ticket, compare as compare_travel
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
    if q.area:
        notes.append(q.area)
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


def print_ranking(quotes: list[Quote], *, limit: int, as_json: bool, title: str = "", travel: float = 0.0) -> None:
    """``travel`` is a fixed getting-there cost added to every quote as a trip total."""
    if as_json:
        rows = []
        for q in quotes[:limit]:
            d = q.to_dict()
            if travel:
                d["travel"] = round(travel, 2)
                d["trip_total"] = round(q.all_in + travel, 2)
            rows.append(d)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
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
        ("Trip", lambda i, q: _money(q.all_in + travel, q.currency), True, bool(travel)),
        ("Hotel", lambda i, q: q.hotel, False, True),
        ("Stars", lambda i, q: _stars(q.stars), True, any(q.stars is not None for q in shown)),
        (
            "Sportsbook",
            lambda i, q: f"{q.sportsbook}/{SPORTSBOOK_MAX}" if q.sportsbook is not None else "",
            True,
            any(q.sportsbook is not None for q in shown),
        ),
        ("Safety", lambda i, q: f"{q.safety}/{SPORTSBOOK_MAX}" if q.safety is not None else "", True, any(q.safety is not None for q in shown)),
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
    if travel:
        print(f"Trip total with {_money(travel, best.currency)} of travel: {_money(best.all_in + travel, best.currency)}.")
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
    ranked = rank(quotes, refundable_only=args.refundable, max_total=args.max_price, min_sportsbook=args.min_sportsbook, rewards=args.rewards, min_stars=args.min_stars, wifi_only=args.wifi, min_safety=args.min_safety)
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
            print("Prices came back, but none passed your --refundable/--wifi/--max-price/--min-stars/--min-safety/--min-sportsbook/--rewards filters.")
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
    ranked = rank(quotes, refundable_only=args.refundable, max_total=args.max_price, min_sportsbook=args.min_sportsbook, rewards=args.rewards, min_stars=args.min_stars, wifi_only=args.wifi, min_safety=args.min_safety)
    if not ranked and not args.json:
        print("No quote passed your --refundable/--wifi/--max-price/--min-stars/--min-safety/--min-sportsbook/--rewards filters.")
        return 1
    print_ranking(ranked, limit=args.limit, as_json=args.json, title="" if args.json else f"{len(quotes)} quotes from {args.file}", travel=args.travel or 0.0)
    return 0


def cmd_travel(args: argparse.Namespace) -> int:
    drive = Drive(
        one_way_miles=args.miles,
        mpg=args.mpg,
        gas_price=args.gas,
        per_mile=args.per_mile,
        parking_per_night=args.parking,
        nights=args.nights,
        one_way_hours=args.hours,
    )
    tickets: list[Ticket] = []
    if args.flight is not None or args.flight_extras or args.flight_return is not None:
        tickets.append(Fly(args.flight, travelers=args.travelers, extras=args.flight_extras, fare_back=args.flight_return))
    if args.bus is not None or args.bus_extras or args.bus_return is not None:
        tickets.append(Bus(args.bus, travelers=args.travelers, extras=args.bus_extras, fare_back=args.bus_return))
    if not tickets:
        tickets.append(Fly(None, travelers=args.travelers))
    result = compare_travel(drive, tickets, hotel=args.hotel or 0.0)
    cur = args.currency.upper()
    if args.json:
        options = {"drive": {"fuel": round(drive.fuel, 2), "parking": round(drive.parking, 2), "total": round(drive.total, 2)}}
        for t in result.tickets:
            options[t.name] = None if t.total is None else {
                "fares": round(t.fares or 0, 2),
                "extras": round(t.extras, 2),
                "total": round(t.total, 2),
            }
        print(
            json.dumps(
                {
                    "options": options,
                    "hotel": round(result.hotel, 2),
                    "trip_totals": {k: None if v is None else round(v, 2) for k, v in result.trip_totals().items()},
                    "break_even": {t.name: round(result.break_even(t), 2) for t in result.tickets},
                    "cheaper": result.cheaper,
                    "saving": round(result.saving, 2),
                    "currency": cur,
                },
                indent=2,
            )
        )
        return 0

    travelers = result.tickets[0].travelers if result.tickets else 1
    people = f"{travelers} traveler{'s' if travelers != 1 else ''}"
    print(f"Round trip of {drive.round_trip_miles:g} miles, {people}, {drive.nights} night{'s' if drive.nights != 1 else ''}.")
    print()
    rows = []
    if drive.per_mile is not None:
        rows.append(("Drive", f"{drive.round_trip_miles:g} mi x {_money(drive.per_mile, cur)}/mi", _money(drive.fuel, cur)))
    else:
        gallons = drive.round_trip_miles / drive.mpg
        rows.append(("Drive", f"{gallons:.1f} gal at {_money(drive.gas_price, cur)} ({drive.mpg:g} mpg)", _money(drive.fuel, cur)))
    if drive.parking:
        rows.append(("", f"hotel parking {_money(drive.parking_per_night, cur)} x {drive.nights}", _money(drive.parking, cur)))
    rows.append(("", "driving total", _money(drive.total, cur)))
    for t in result.tickets:
        if t.total is None:
            continue
        label = t.name.capitalize()
        back = t.fare_out if t.fare_back is None else t.fare_back
        if t.fare_back is None:
            fare_text = f"{_money(t.fare_out or 0, cur)} round trip x {people}"
        elif back == 0:
            fare_text = f"{_money(t.fare_out or 0, cur)} one way x {people}, free ride back"
        else:
            fare_text = f"{_money(t.fare_out or 0, cur)} out + {_money(back, cur)} back x {people}"
        rows.append((label, fare_text, _money(t.fares or 0, cur)))
        if t.extras:
            what = "airport parking / rides / bags" if t.name == "fly" else "rides to and from the station"
            rows.append(("", what, _money(t.extras, cur)))
        rows.append(("", f"{t.name} total", _money(t.total, cur)))
    print(_table(rows, ("", "", "Cost"), numeric={2}))
    print()
    if result.cheaper is None:
        for t in result.tickets:
            print(
                f"No {t.name} fare given. {t.name.capitalize()} beats driving only if one-way fares come in under "
                f"{_money(result.break_even(t), cur)} per person"
                + (" each way" if t.fare_back is None else "")
                + (f" (after {_money(t.extras, cur)} of extras)." if t.extras else ".")
            )
        print("Re-run with --flight or --bus FARE once you have a quote.")
    elif result.cheaper == "tie":
        print("The cheapest options cost the same.")
    else:
        if result.cheaper == "drive":
            print(f"Driving is cheapest, by {_money(result.saving, cur)}.")
        else:
            print(f"{result.cheaper.capitalize()} is cheapest, {_money(result.saving, cur)} less than driving.")
        for t in result.tickets:
            if t.total is not None:
                print(f"Break-even {t.name} fare: {_money(result.break_even(t), cur)} per person one way.")
    if result.hotel:
        parts = [f"{k} {_money(v, cur)}" for k, v in result.trip_totals().items() if v is not None]
        print(f"Trip total with the hotel ({_money(result.hotel, cur)}): " + ", ".join(parts) + ".")
    for note in result.notes:
        print(note)
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
        "--min-safety",
        type=int,
        choices=range(1, SPORTSBOOK_MAX + 1),
        metavar="1-5",
        help="only hotels whose solo-safety rating is at least this (unrated hotels are dropped)",
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
            "  python -m hotels compare examples/vegas-quotes.json --nights 2 --wifi --min-safety 4 --min-sportsbook 3\n"
            "  python -m hotels travel --miles 270 --hours 4 --gas 5.86 --parking 25 --nights 2 --travelers 2 --flight 140\n"
            "  python -m hotels travel --miles 270 --gas 5.86 --nights 2 --bus 45 --bus-return 0 --bus-extras 40 --hotel 190\n"
            "  python -m hotels compare examples/vegas-quotes.json --nights 2 --travel 85 --min-safety 4\n"
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
            "optional: nights, currency, source, room, url, refundable, wifi, stars (1-5), safety (1-5), area, sportsbook (1-5), rewards, "
            "and for fees the quote leaves out: fee_per_night (or fees for the stay), fee_note, tax_pct. "
            "Ranking uses the all-in price with those added. "
            "JSON: a list of objects. CSV: a header row with those column names."
        ),
    )
    c.add_argument("file", help="quotes.json or quotes.csv")
    c.add_argument("--check-in", help="YYYY-MM-DD, used to work out nights for per-night quotes")
    c.add_argument("--check-out", help="YYYY-MM-DD")
    c.add_argument("--nights", type=int, default=1, help="nights for quotes that give a per-night price (default 1)")
    c.add_argument("--travel", type=float, metavar="COST", help="getting-there cost (bus fare, gas, rides) added to every hotel as a trip total")
    _add_filters(c)
    c.set_defaults(func=cmd_compare)

    t = sub.add_parser(
        "travel",
        help="drive or fly? total both ways of getting there and the fare at which flying wins",
        description=(
            "Give the one-way distance and your car's numbers; optionally a round-trip fare per person. "
            "Without a fare it prints the break-even fare, so you know what to look for."
        ),
    )
    t.add_argument("--miles", type=float, required=True, help="one-way driving distance")
    t.add_argument("--hours", type=float, help="one-way driving time, for the summary")
    t.add_argument("--mpg", type=float, default=30.0, help="your car's fuel economy (default 30)")
    t.add_argument("--gas", type=float, default=4.50, metavar="PRICE", help="price per gallon (default 4.50)")
    t.add_argument("--per-mile", type=float, metavar="RATE", help=f"flat cost per mile instead of mpg/gas, e.g. {IRS_RATE} (IRS rate) to count wear")
    t.add_argument("--parking", type=float, default=0.0, metavar="PER_NIGHT", help="hotel parking per night when you drive")
    t.add_argument("--nights", type=int, default=1)
    t.add_argument("--travelers", type=int, default=1, help="people flying (default 1)")
    t.add_argument("--flight", type=float, metavar="FARE", help="flight fare per person, one way; the return costs the same unless --flight-return says otherwise")
    t.add_argument("--flight-return", type=float, metavar="FARE", help="return fare per person if different (0 = riding home with someone)")
    t.add_argument("--flight-extras", type=float, default=0.0, metavar="TOTAL", help="airport parking, rideshares, bag fees for the whole trip")
    t.add_argument("--bus", type=float, metavar="FARE", help="bus fare per person, one way (Greyhound, FlixBus...)")
    t.add_argument("--bus-return", type=float, metavar="FARE", help="return bus fare per person if different (0 = riding home with someone)")
    t.add_argument("--bus-extras", type=float, default=0.0, metavar="TOTAL", help="rides to and from the bus stations for the whole trip")
    t.add_argument("--hotel", type=float, metavar="TOTAL", help="all-in hotel total, to print a trip total")
    t.add_argument("--currency", default="USD")
    t.add_argument("--json", action="store_true", help="machine-readable output")
    t.set_defaults(func=cmd_travel)
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
