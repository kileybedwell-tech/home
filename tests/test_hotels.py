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


def offer(hotel_id, name, total, *, currency="USD", refundable=None, deadline=None, available=True):
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
                "price": {"currency": currency, "total": str(total)},
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
            {"hotelId": "H1", "name": "Near", "distance": {"value": 0.5, "unit": "KM"}},
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
        # nearest hotels are priced first, and the request carries the stay
        self.assertEqual(fake.offer_batches, [["H1", "H3", "H2"]])
        _, _, params, headers = fake.requests[-1]
        self.assertEqual(params["adults"], "2")
        self.assertEqual(params["checkInDate"], "2026-10-03")
        self.assertEqual(headers["Authorization"], "Bearer tok")

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
            self.assertIn("Cheapest: B at $300.00 for 2 nights ($150.00/night).", out)
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
        self.assertIn("Cheapest: One Hotel at $250.00 for 2 nights ($125.00/night).", out)
        with mock.patch.object(amadeus, "_default_transport", fake):
            code, out, _ = run_cli("search", "SEA", "2026-10-03", "2026-10-05", "--env-file", "/nonexistent", "--max-price", "100", env=env)
        self.assertEqual(code, 1)
        self.assertIn("filters", out)


if __name__ == "__main__":
    unittest.main()
