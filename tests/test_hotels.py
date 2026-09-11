"""Hotel finder tests. No network: the Amadeus transport is stubbed."""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotels import amadeus, cli  # noqa: E402
from hotels.amadeus import AmadeusClient, AmadeusError, Config, ConfigError  # noqa: E402
from hotels.search import Quote, SearchError, cheapest, nights_between, rank  # noqa: E402
from hotels.travel import Bus, Drive, Fly, compare as compare_travel  # noqa: E402


def q(hotel, total, **kw):
    return Quote(hotel=hotel, total=total, **kw)


class RankingTests(unittest.TestCase):
    def test_cheapest_first_with_per_night_tiebreak(self):
        quotes = [q("B", 300, nights=3), q("A", 200, nights=1), q("C", 200, nights=2)]
        self.assertEqual([x.hotel for x in rank(quotes)], ["C", "A", "B"])
        self.assertEqual(cheapest(quotes).hotel, "C")

    def test_currencies_are_grouped_not_converted(self):
        quotes = [q("Euro", 50, currency="EUR"), q("Dollar", 90), q("Dollar2", 80)]
        self.assertEqual([x.hotel for x in rank(quotes)], ["Dollar2", "Dollar", "Euro"])

    def test_filters(self):
        quotes = [q("Cheap nonref", 100, refundable=False), q("Pricey ref", 150, refundable=True), q("Unknown", 120)]
        self.assertEqual([x.hotel for x in rank(quotes, refundable_only=True)], ["Pricey ref"])
        self.assertEqual([x.hotel for x in rank(quotes, max_total=120)], ["Cheap nonref", "Unknown"])
        self.assertIsNone(cheapest([], max_total=1))

    def test_sportsbook_rating_and_filter(self):
        quotes = [q("Kiosk", 90, sportsbook=1), q("Unrated", 95), q("Good", 120, sportsbook=4)]
        self.assertEqual([x.hotel for x in rank(quotes, min_sportsbook=3)], ["Good"])
        self.assertEqual([x.hotel for x in rank(quotes, min_sportsbook=1)], ["Kiosk", "Good"])
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "sportsbook": "4"}).sportsbook, 4)
        self.assertIsNone(Quote.from_dict({"hotel": "X", "total": 1, "sportsbook": ""}).sportsbook)
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "sportsbook": 4}).to_dict()["sportsbook"], 4)
        self.assertNotIn("sportsbook", Quote.from_dict({"hotel": "X", "total": 1}).to_dict())
        for bad in (0, 6, "great"):
            with self.assertRaises(SearchError):
                Quote.from_dict({"hotel": "X", "total": 1, "sportsbook": bad})

    def test_hidden_fees_change_the_ranking(self):
        cheap_headline = Quote.from_dict({"hotel": "Resort", "per_night": 50, "fee_per_night": 40, "tax_pct": 10}, default_nights=2)
        honest = Quote.from_dict({"hotel": "Honest", "total": 190, "wifi": "yes"}, default_nights=2)
        self.assertAlmostEqual(cheap_headline.all_in, (100 + 80) * 1.10)
        self.assertAlmostEqual(cheap_headline.extras, 98.0)
        self.assertEqual(cheap_headline.extras_note(), "40.00/night resort fee + 10% tax")
        self.assertEqual(cheap_headline.fee_note, "resort fee")
        self.assertEqual([x.hotel for x in rank([cheap_headline, honest])], ["Honest", "Resort"])
        # price filters look at what you pay, not the headline
        self.assertEqual([x.hotel for x in rank([cheap_headline, honest], max_total=195)], ["Honest"])
        self.assertEqual([x.hotel for x in rank([cheap_headline, honest], wifi_only=True)], ["Honest"])
        d = cheap_headline.to_dict()
        self.assertEqual((d["total"], d["quoted"], d["extras"]), (198.0, 100.0, 98.0))
        self.assertNotIn("quoted", honest.to_dict())
        self.assertTrue(honest.to_dict()["wifi"])
        self.assertIs(Quote.from_dict({"hotel": "X", "total": 1, "wifi": "paid"}).wifi, False)
        self.assertIsNone(Quote.from_dict({"hotel": "X", "total": 1, "wifi": ""}).wifi)
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "fees": "$12.50", "fee_note": "parking"}).extras_note(), "12.50/night parking")
        for bad in ({"fees": -1}, {"tax_pct": "lots"}, {"fee_per_night": "?"}):
            with self.assertRaises(SearchError):
                Quote.from_dict({"hotel": "X", "total": 1, **bad})

    def test_safety_rating_and_area(self):
        quotes = [q("Cheap dark", 90, safety=2), q("Unrated", 95), q("Lit", 120, safety=5, area="center Strip")]
        self.assertEqual([x.hotel for x in rank(quotes, min_safety=3)], ["Lit"])
        self.assertEqual([x.hotel for x in rank(quotes, min_safety=2)], ["Cheap dark", "Lit"])
        made = Quote.from_dict({"hotel": "X", "total": 1, "safety": "4", "area": " off-Strip "})
        self.assertEqual((made.safety, made.area), (4, "off-Strip"))
        self.assertEqual(made.to_dict()["safety"], 4)
        self.assertEqual(made.to_dict()["area"], "off-Strip")
        self.assertNotIn("safety", Quote.from_dict({"hotel": "X", "total": 1}).to_dict())
        with self.assertRaises(SearchError) as ctx:
            Quote.from_dict({"hotel": "X", "total": 1, "safety": 9})
        self.assertIn("safety", str(ctx.exception))

    def test_stars_rating_and_filter(self):
        quotes = [q("Budget", 90, stars=3), q("Unrated", 95), q("Nice", 150, stars=4.5)]
        self.assertEqual([x.hotel for x in rank(quotes, min_stars=3.5)], ["Nice"])
        self.assertEqual([x.hotel for x in rank(quotes, min_stars=3)], ["Budget", "Nice"])
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "stars": "3.5"}).stars, 3.5)
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "stars": "4*"}).stars, 4)
        self.assertIsNone(Quote.from_dict({"hotel": "X", "total": 1, "stars": ""}).stars)
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "stars": 5}).to_dict()["stars"], 5)
        for bad in (0, 5.5, 3.2, "luxury"):
            with self.assertRaises(SearchError):
                Quote.from_dict({"hotel": "X", "total": 1, "stars": bad})

    def test_rewards_filter_is_loose(self):
        quotes = [q("Excalibur", 116, rewards="MGM Rewards"), q("Flamingo", 132, rewards="Caesars Rewards"), q("Indie", 90)]
        self.assertEqual([x.hotel for x in rank(quotes, rewards="caesars")], ["Flamingo"])
        self.assertEqual([x.hotel for x in rank(quotes, rewards="Rewards")], ["Excalibur", "Flamingo"])
        self.assertEqual(rank(quotes, rewards="hilton"), [])
        self.assertEqual(Quote.from_dict({"hotel": "X", "total": 1, "rewards": " MGM Rewards "}).rewards, "MGM Rewards")
        self.assertNotIn("rewards", Quote.from_dict({"hotel": "X", "total": 1}).to_dict())

    def test_nights(self):
        self.assertEqual(nights_between("2026-10-03", "2026-10-05"), 2)
        with self.assertRaises(SearchError):
            nights_between("2026-10-05", "2026-10-05")
        with self.assertRaises(SearchError):
            nights_between("10/03/2026", "2026-10-05")

    def test_from_dict_accepts_total_or_per_night(self):
        self.assertEqual(Quote.from_dict({"hotel": "X", "per_night": "100", "nights": 3}).total, 300)
        self.assertEqual(Quote.from_dict({"name": "X", "total": 250}, default_nights=2).per_night, 125)
        with self.assertRaises(SearchError):
            Quote.from_dict({"hotel": "X"})
        with self.assertRaises(SearchError):
            Quote.from_dict({"total": 5})
        with self.assertRaises(SearchError):
            Quote.from_dict({"hotel": "X", "total": "lots"})


class ConfigTests(unittest.TestCase):
    def test_missing_credentials(self):
        with self.assertRaises(ConfigError) as ctx:
            Config.from_env({})
        self.assertIn("AMADEUS_CLIENT_ID", str(ctx.exception))

    def test_environment(self):
        env = {"AMADEUS_CLIENT_ID": "id", "AMADEUS_CLIENT_SECRET": "s"}
        self.assertEqual(Config.from_env(env).host, "https://test.api.amadeus.com")
        self.assertEqual(Config.from_env({**env, "AMADEUS_ENVIRONMENT": "production"}).host, "https://api.amadeus.com")
        with self.assertRaises(ConfigError):
            Config.from_env({**env, "AMADEUS_ENVIRONMENT": "staging"})

    def test_dotenv_does_not_override(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# c\nAMADEUS_CLIENT_ID='from-file'\nAMADEUS_CLIENT_SECRET=sec\n")
            with mock.patch.dict("os.environ", {"AMADEUS_CLIENT_ID": "from-env"}, clear=True):
                amadeus.load_dotenv(path)
                import os

                self.assertEqual(os.environ["AMADEUS_CLIENT_ID"], "from-env")
                self.assertEqual(os.environ["AMADEUS_CLIENT_SECRET"], "sec")


def offer(hotel_id, name, total, *, currency="USD", refundable=None, deadline=None, available=True, taxes=None):
    policies = {}
    if refundable is not None:
        policies["refundable"] = {"cancellationRefund": "REFUNDABLE_UP_TO_DEADLINE" if refundable else "NON_REFUNDABLE"}
    elif deadline is not None:
        policies["cancellations"] = [{"deadline": deadline} if deadline else {"type": "FULL_STAY"}]
    return {
        "hotel": {"hotelId": hotel_id, "name": name},
        "available": available,
        "offers": [
            {
                "id": f"{hotel_id}-1",
                "price": {"currency": currency, "total": str(total), **({"taxes": taxes} if taxes else {})},
                "room": {"typeEstimated": {"category": "STANDARD_ROOM", "beds": 1, "bedType": "KING"}},
                "policies": policies,
            }
        ],
    }


class FakeAmadeus:
    """Answers the three Amadeus endpoints and records what was asked."""

    def __init__(self, hotels, offers, *, fail_token=False):
        self.hotels = hotels
        self.offers = offers
        self.fail_token = fail_token
        self.requests = []
        self.offer_batches = []

    def __call__(self, request):
        url = urllib.parse.urlsplit(request.full_url)
        params = dict(urllib.parse.parse_qsl(url.query))
        self.requests.append((request.get_method(), url.path, params, dict(request.header_items())))
        if url.path.endswith("/oauth2/token"):
            if self.fail_token:
                return 401, json.dumps({"errors": [{"code": 38187, "title": "Invalid parameters"}]}).encode()
            return 200, json.dumps({"access_token": "tok", "expires_in": 1799}).encode()
        if url.path.endswith("/reference-data/locations"):
            if params["keyword"].lower() == "nowhere":
                return 200, b'{"data": []}'
            return 200, json.dumps({"data": [{"iataCode": "SEA", "name": "SEATTLE", "address": {"countryCode": "US"}}]}).encode()
        if url.path.endswith("/hotels/by-city"):
            return 200, json.dumps({"data": self.hotels}).encode()
        if url.path.endswith("/shopping/hotel-offers"):
            ids = params["hotelIds"].split(",")
            self.offer_batches.append(ids)
            data = [o for o in self.offers if o["hotel"]["hotelId"] in ids]
            if not data:
                return 400, json.dumps({"errors": [{"code": 3664, "title": "NO ROOMS AVAILABLE AT REQUESTED PROPERTY"}]}).encode()
            return 200, json.dumps({"data": data}).encode()
        return 404, b"{}"


def make_client(fake, **kw):
    return AmadeusClient(Config("id", "secret"), transport=fake, sleep=lambda _: None, **kw)


class AmadeusTests(unittest.TestCase):
    def test_search_ranks_and_annotates(self):
        hotels = [
            {"hotelId": "H2", "name": "Far", "distance": {"value": 4.2, "unit": "KM"}},
            {"hotelId": "H1", "name": "Near", "distance": {"value": 0.5, "unit": "KM"}, "rating": "4"},
            {"hotelId": "H3", "name": "Sold out", "distance": {"value": 1.0, "unit": "KM"}},
        ]
        offers = [
            offer("H1", "Near Hotel", 310.0, refundable=False),
            offer("H2", "Far Hotel", 220.5, deadline="2026-10-01T23:59:00"),
            offer("H3", "Sold out", 100.0, available=False),
        ]
        fake = FakeAmadeus(hotels, offers)
        label, quotes = amadeus.search(make_client(fake), "Seattle", "2026-10-03", "2026-10-05", adults=2)
        self.assertEqual(label, "Seattle, US")
        ranked = rank(quotes)
        self.assertEqual([x.hotel for x in ranked], ["Far Hotel", "Near Hotel"])
        best = ranked[0]
        self.assertEqual(best.nights, 2)
        self.assertAlmostEqual(best.per_night, 110.25)
        self.assertTrue(best.refundable)
        self.assertFalse(ranked[1].refundable)
        self.assertEqual(best.distance_km, 4.2)
        self.assertEqual(best.room, "Standard Room · 1 king")
        self.assertEqual(best.source, "amadeus-test")
        self.assertIsNone(best.stars)
        self.assertEqual(ranked[1].stars, 4)
        # nearest hotels are priced first, and the request carries the stay
        self.assertEqual(fake.offer_batches, [["H1", "H3", "H2"]])
        _, _, params, headers = fake.requests[-1]
        self.assertEqual(params["adults"], "2")
        self.assertEqual(params["checkInDate"], "2026-10-03")
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_amadeus_fees_not_in_the_rate_are_hidden_fees(self):
        hotels = [{"hotelId": "H1", "name": "Fee", "distance": {"value": 1.0}}, {"hotelId": "H2", "name": "None", "distance": {"value": 2.0}}]
        taxes = [
            {"code": "RESORT_FEE", "amount": "30.00", "included": False, "pricingFrequency": "PER_NIGHT"},
            {"code": "CITY_TAX", "amount": "5.00", "included": False},
            {"code": "VAT", "amount": "20.00", "included": True},
        ]
        fake = FakeAmadeus(hotels, [offer("H1", "Fee Hotel", 200.0, taxes=taxes), offer("H2", "No Fee Hotel", 250.0)])
        _, quotes = amadeus.search(make_client(fake), "SEA", "2026-10-03", "2026-10-05")
        fee = next(x for x in quotes if x.hotel == "Fee Hotel")
        self.assertEqual(fee.fees, 65.0)
        self.assertEqual(fee.all_in, 265.0)
        self.assertEqual(fee.fee_note, "payable at hotel: resort fee, city tax")
        self.assertEqual([x.hotel for x in rank(quotes)], ["No Fee Hotel", "Fee Hotel"])

    def test_offers_are_batched_and_empty_batches_skipped(self):
        hotels = [{"hotelId": f"H{i}", "name": f"H{i}", "distance": {"value": i}} for i in range(45)]
        fake = FakeAmadeus(hotels, [offer("H44", "Last", 99.0)])
        _, quotes = amadeus.search(make_client(fake), "SEA", "2026-10-03", "2026-10-04")
        self.assertEqual([len(b) for b in fake.offer_batches], [20, 20, 5])
        self.assertEqual([x.hotel for x in quotes], ["Last"])
        # a 3-letter code skips the city lookup entirely
        self.assertFalse(any(p.endswith("/reference-data/locations") for _, p, _, _ in fake.requests))

    def test_unknown_city_and_bad_credentials(self):
        fake = FakeAmadeus([], [])
        with self.assertRaises(SearchError):
            amadeus.search(make_client(fake), "Nowhere", "2026-10-03", "2026-10-04")
        with self.assertRaises(AmadeusError) as ctx:
            make_client(FakeAmadeus([], [], fail_token=True)).token()
        self.assertIn("38187", str(ctx.exception))

    def test_retries_then_gives_up(self):
        calls = []

        def flaky(request):
            calls.append(request.full_url)
            return 503, b'{"errors":[{"title":"down"}]}'

        client = AmadeusClient(Config("id", "s"), transport=flaky, sleep=lambda _: None, retries=2)
        with self.assertRaises(AmadeusError):
            client.token()
        self.assertEqual(len(calls), 3)


def run_cli(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict("os.environ", env or {}, clear=True), redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_compare_json_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotes.json"
            path.write_text(
                json.dumps(
                    [
                        {"hotel": "A", "source": "Booking", "total": 400},
                        {"hotel": "B", "source": "site", "per_night": 150, "refundable": True, "url": "https://b.example"},
                    ]
                )
            )
            code, out, _ = run_cli("compare", str(path), "--check-in", "2026-10-03", "--check-out", "2026-10-05")
            self.assertEqual(code, 0)
            self.assertIn("Cheapest: B at $300.00 all-in for 2 nights ($150.00/night).", out)
            self.assertIn("https://b.example", out)
            code, out, _ = run_cli("compare", str(path), "--nights", "2", "--json", "--max-price", "350")
            self.assertEqual(code, 0)
            self.assertEqual([r["hotel"] for r in json.loads(out)], ["B"])

    def test_sportsbook_column_only_when_rated(self):
        with TemporaryDirectory() as tmp:
            rated = Path(tmp) / "rated.csv"
            rated.write_text("hotel,per_night,sportsbook\nVenetian,207,4\nEllis Island,47,1\nMystery,60,\n")
            code, out, _ = run_cli("compare", str(rated), "--nights", "2")
            self.assertEqual(code, 0)
            header = out.splitlines()[1]
            self.assertIn("Sportsbook", header)
            self.assertIn("Ellis Island", out)
            self.assertRegex(out, r"Ellis Island\s+1/5")
            code, out, _ = run_cli("compare", str(rated), "--nights", "2", "--min-sportsbook", "3")
            self.assertEqual(code, 0)
            self.assertIn("Cheapest: Venetian", out)
            self.assertNotIn("Ellis Island", out)
            self.assertNotIn("Mystery", out)
            code, out, _ = run_cli("compare", str(rated), "--nights", "2", "--min-sportsbook", "5")
            self.assertEqual(code, 1)
            self.assertIn("--min-sportsbook", out)
            stars = Path(tmp) / "stars.csv"
            stars.write_text("hotel,per_night,stars\nVenetian,207,5\nFlamingo,66,3.5\nMotel,40,\n")
            code, out, _ = run_cli("compare", str(stars), "--nights", "2", "--min-stars", "3.5")
            self.assertEqual(code, 0)
            self.assertIn("Stars", out.splitlines()[1])
            self.assertRegex(out, r"Flamingo\s+3.5★")
            self.assertNotIn("Motel", out)
            fees = Path(tmp) / "fees.csv"
            fees.write_text("hotel,per_night,fee_per_night,tax_pct,wifi\nResort,50,40,10,yes\nHonest,95,,,\n")
            code, out, _ = run_cli("compare", str(fees), "--nights", "2")
            self.assertEqual(code, 0)
            header = out.splitlines()[1]
            self.assertIn("Quoted", header)
            self.assertIn("Hidden", header)
            self.assertRegex(out, r"\$198\.00\s+\$99\.00\s+\$100\.00\s+\+\$98\.00\s+Resort")
            self.assertIn("Cheapest: Honest at $190.00 all-in", out)
            self.assertIn("biggest gap is Resort, quoted $100.00 but $198.00 to pay", out)
            code, out, _ = run_cli("compare", str(fees), "--nights", "2", "--wifi")
            self.assertEqual(code, 0)
            self.assertIn("Cheapest: Resort", out)
            self.assertNotIn("Honest", out)
            safety = Path(tmp) / "safety.csv"
            safety.write_text("hotel,per_night,safety,area\nDark,50,2,north end\nLit,70,5,center Strip\n")
            code, out, _ = run_cli("compare", str(safety), "--nights", "1", "--min-safety", "4")
            self.assertEqual(code, 0)
            self.assertIn("Safety", out.splitlines()[1])
            self.assertRegex(out, r"Lit\s+5/5\s+center Strip")
            self.assertNotIn("Dark", out)
            rewards = Path(tmp) / "rewards.csv"
            rewards.write_text("hotel,per_night,rewards\nExcalibur,58,MGM Rewards\nFlamingo,66,Caesars Rewards\n")
            code, out, _ = run_cli("compare", str(rewards), "--nights", "2")
            self.assertEqual(code, 0)
            self.assertIn("Rewards", out.splitlines()[1])
            self.assertNotIn("Sportsbook", out)
            code, out, _ = run_cli("compare", str(rewards), "--nights", "2", "--rewards", "caesars", "--json")
            self.assertEqual(code, 0)
            self.assertEqual([r["hotel"] for r in json.loads(out)], ["Flamingo"])
            unrated = Path(tmp) / "unrated.csv"
            unrated.write_text("hotel,total\nA,100\n")
            code, out, _ = run_cli("compare", str(unrated))
            self.assertEqual(code, 0)
            self.assertNotIn("Sportsbook", out)

    def test_compare_csv_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotes.csv"
            path.write_text("hotel,total,currency,source\nX,120,USD,Expedia\nY,95.5,USD,Hotels.com\n")
            code, out, _ = run_cli("compare", str(path))
            self.assertEqual(code, 0)
            self.assertTrue(out.splitlines()[-1].startswith("Cheapest: Y at $95.50"))

    def test_bad_input_is_a_clean_error(self):
        code, _, err = run_cli("compare", "/nonexistent/quotes.json")
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)
        code, _, err = run_cli("search", "Seattle", "2026-10-03", "2026-10-05", "--env-file", "/nonexistent/.env")
        self.assertEqual(code, 2)
        self.assertIn("AMADEUS_CLIENT_ID", err)

    def test_search_end_to_end_with_stub(self):
        fake = FakeAmadeus(
            [{"hotelId": "H1", "name": "One", "distance": {"value": 1.0}}],
            [offer("H1", "One Hotel", 250.0, refundable=True)],
        )
        env = {"AMADEUS_CLIENT_ID": "id", "AMADEUS_CLIENT_SECRET": "s"}
        with mock.patch.object(amadeus, "_default_transport", fake):
            code, out, err = run_cli("search", "Seattle", "2026-10-03", "2026-10-05", "--env-file", "/nonexistent", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("Amadeus TEST data", out)
        self.assertIn("Cheapest: One Hotel at $250.00 all-in for 2 nights ($125.00/night).", out)
        with mock.patch.object(amadeus, "_default_transport", fake):
            code, out, _ = run_cli("search", "SEA", "2026-10-03", "2026-10-05", "--env-file", "/nonexistent", "--max-price", "100", env=env)
        self.assertEqual(code, 1)
        self.assertIn("filters", out)


if __name__ == "__main__":
    unittest.main()


class TravelTests(unittest.TestCase):
    def test_drive_costs(self):
        drive = Drive(one_way_miles=270, mpg=30, gas_price=5.0, parking_per_night=25, nights=2)
        self.assertEqual(drive.round_trip_miles, 540)
        self.assertAlmostEqual(drive.fuel, 90.0)
        self.assertEqual(drive.parking, 50)
        self.assertAlmostEqual(drive.total, 140.0)
        flat = Drive(one_way_miles=100, per_mile=0.70)
        self.assertAlmostEqual(flat.fuel, 140.0)

    def test_tickets(self):
        self.assertEqual(Fly(50, travelers=2, extras=20).total, 220)  # 50 each way, two people
        self.assertEqual(Fly(50, travelers=2, extras=20, fare_back=30).total, 180)
        self.assertEqual(Bus(45, extras=40, fare_back=0).total, 85)  # ride home with friends
        self.assertIsNone(Bus(None).total)
        self.assertEqual(Bus(None).name, "bus")

    def test_break_even_and_winner(self):
        drive = Drive(one_way_miles=270, mpg=30, gas_price=5.0, parking_per_night=25, nights=2)  # $140
        unknown = compare_travel(drive, Fly(None, travelers=2, extras=20))
        self.assertIsNone(unknown.cheaper)
        self.assertAlmostEqual(unknown.break_even_fare, 30.0)  # (140-20)/2 people /2 legs
        cheap = compare_travel(drive, Fly(25, travelers=2, extras=20), hotel=200)
        self.assertEqual(cheap.cheaper, "fly")
        self.assertAlmostEqual(cheap.saving, 20.0)
        self.assertAlmostEqual(cheap.fly_total, 320.0)
        self.assertAlmostEqual(cheap.drive_total, 340.0)
        self.assertEqual(compare_travel(drive, Fly(30, travelers=2, extras=20)).cheaper, "tie")
        self.assertEqual(compare_travel(drive, Fly(200, travelers=1)).cheaper, "drive")
        # extras larger than the drive: flying can never win, break-even floors at zero
        self.assertEqual(compare_travel(Drive(10, mpg=30, gas_price=3), Fly(None, extras=500)).break_even_fare, 0.0)

    def test_bus_with_free_ride_home(self):
        drive = Drive(one_way_miles=270, mpg=30, gas_price=5.0)  # $90
        bus = Bus(45, extras=40, fare_back=0)
        result = compare_travel(drive, [Fly(80, extras=60), bus], hotel=190)
        self.assertEqual(result.cheaper, "bus")
        self.assertAlmostEqual(result.saving, 5.0)
        self.assertAlmostEqual(result.break_even(bus), 50.0)  # 90 - 40 extras, nothing owed for the return
        self.assertEqual(result.trip_totals(), {"drive": 280.0, "fly": 410.0, "bus": 275.0})
        self.assertIsNone(compare_travel(drive, [Fly(None), Bus(None)]).cheaper)
        with self.assertRaises(SearchError):
            compare_travel(drive, [Bus(10), Bus(20)])

    def test_bad_input(self):
        for drive, fly in (
            (Drive(0), Fly(None)),
            (Drive(10, mpg=0), Fly(None)),
            (Drive(10), Fly(None, travelers=0)),
            (Drive(10), Fly(-5)),
            (Drive(10), Bus(5, fare_back=-1)),
            (Drive(10, nights=-1), Fly(None)),
        ):
            with self.assertRaises(SearchError):
                compare_travel(drive, fly)

    def test_cli(self):
        code, out, _ = run_cli("travel", "--miles", "270", "--gas", "5", "--parking", "25", "--nights", "2", "--travelers", "2")
        self.assertEqual(code, 0)
        self.assertIn("driving total               $140.00", out)
        self.assertIn("under $35.00 per person each way", out)
        code, out, _ = run_cli(
            "travel", "--miles", "270", "--gas", "5", "--parking", "25", "--nights", "2", "--travelers", "2",
            "--flight", "25", "--flight-extras", "20", "--hotel", "200", "--json",
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["cheaper"], "fly")
        self.assertEqual(data["trip_totals"], {"drive": 340.0, "fly": 320.0})
        self.assertEqual(data["break_even"], {"fly": 30.0})
        code, out, _ = run_cli("travel", "--miles", "270", "--gas", "5", "--bus", "45", "--bus-return", "0", "--bus-extras", "40", "--hotel", "190")
        self.assertEqual(code, 0)
        self.assertIn("$45.00 one way x 1 traveler, free ride back", out)
        self.assertIn("Bus is cheapest, $5.00 less than driving.", out)
        self.assertIn("Break-even bus fare: $50.00 per person one way.", out)
        self.assertIn("Trip total with the hotel ($190.00): drive $280.00, bus $275.00.", out)
        code, out, _ = run_cli("travel", "--miles", "330", "--hours", "5", "--per-mile", "0.70", "--flight", "89")
        self.assertEqual(code, 0)
        self.assertIn("Fly is cheapest, $284.00 less than driving.", out)
        self.assertIn("10 hours on the road", out)
        self.assertNotIn("wear and tear", out)
        code, _, err = run_cli("travel", "--miles", "0")
        self.assertEqual(code, 2)
        self.assertIn("miles", err)

    def test_compare_trip_total(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.csv"
            path.write_text("hotel,total\nA,190\nB,230\n")
            code, out, _ = run_cli("compare", str(path), "--travel", "85")
            self.assertEqual(code, 0)
            self.assertIn("Trip", out.splitlines()[1])
            self.assertRegex(out, r"\$275\.00\s+A")
            self.assertIn("Trip total with $85.00 of travel: $275.00.", out)
            code, out, _ = run_cli("compare", str(path), "--travel", "85", "--json")
            self.assertEqual(json.loads(out)[0]["trip_total"], 275.0)
            code, out, _ = run_cli("compare", str(path))
            self.assertNotIn("Trip", out)
