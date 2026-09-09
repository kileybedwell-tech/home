"""Live prices from the Amadeus Self-Service Hotel Search API.

Amadeus is the one hotel-pricing API with a free tier that does not need a
travel-agency contract: register at developers.amadeus.com, create an app,
and copy its API Key / API Secret into ``.env`` as ``AMADEUS_CLIENT_ID`` /
``AMADEUS_CLIENT_SECRET``.  New apps start in the *test* environment, which
serves a cached subset of hotels (fine for trying it out, not for booking
decisions); ``AMADEUS_ENVIRONMENT=production`` switches to live rates once
the app has been moved to production in the developer portal.

Three calls are involved, in order:

1. ``/v1/reference-data/locations`` turns "Seattle" into the IATA city code
   ``SEA`` (skipped when a three-letter code is given directly).
2. ``/v1/reference-data/locations/hotels/by-city`` lists the hotels Amadeus
   knows in that city, with their distance from the centre.
3. ``/v3/shopping/hotel-offers`` prices those hotels for the stay, in
   batches, keeping only the best rate per hotel.

Only the standard library is used, so this runs anywhere ``ebay`` does.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .search import Quote, SearchError, nights_between

TEST = "test"
PRODUCTION = "production"
_HOSTS = {TEST: "https://test.api.amadeus.com", PRODUCTION: "https://api.amadeus.com"}

USER_AGENT = "hotel-finder/1.0 (+https://github.com/kileybedwell-tech/home)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Amadeus times out on long hotelIds lists; this size is reliably answered.
OFFER_BATCH = 20

#: ``(status, body_bytes)`` for a prepared request — the seam tests stub.
Transport = Callable[[urllib.request.Request], "tuple[int, bytes]"]


class ConfigError(RuntimeError):
    """Missing or malformed Amadeus credentials."""


class AmadeusError(RuntimeError):
    """An error response from Amadeus, with its ``errors`` array intact."""

    def __init__(self, status: int, url: str, payload: Any) -> None:
        self.status = status
        self.url = url
        self.payload = payload
        self.errors = payload.get("errors", []) if isinstance(payload, dict) else []
        super().__init__(self._describe())

    def _describe(self) -> str:
        parts = []
        for err in self.errors:
            if isinstance(err, dict):
                text = err.get("detail") or err.get("title") or ""
                code = err.get("code")
                parts.append(f"[{code}] {text}" if code else text)
        if parts:
            return f"HTTP {self.status}: " + "; ".join(parts)
        return f"HTTP {self.status} from {self.url}"


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Seed ``os.environ`` from a ``.env`` file. Existing variables win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    environment: str = TEST

    @classmethod
    def from_env(cls, environ: "os._Environ[str] | dict[str, str] | None" = None) -> "Config":
        env = os.environ if environ is None else environ
        client_id = env.get("AMADEUS_CLIENT_ID", "").strip()
        secret = env.get("AMADEUS_CLIENT_SECRET", "").strip()
        environment = env.get("AMADEUS_ENVIRONMENT", TEST).strip().lower() or TEST
        missing = [
            name
            for name, value in (("AMADEUS_CLIENT_ID", client_id), ("AMADEUS_CLIENT_SECRET", secret))
            if not value
        ]
        if missing:
            raise ConfigError(
                "missing " + " and ".join(missing) + ". Create a free app at "
                "https://developers.amadeus.com and put its API Key / API Secret in .env "
                "(see .env.example)."
            )
        if environment not in _HOSTS:
            raise ConfigError(f"AMADEUS_ENVIRONMENT must be 'test' or 'production', not {environment!r}")
        return cls(client_id, secret, environment)

    @property
    def host(self) -> str:
        return _HOSTS[self.environment]


def _default_transport(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _parse(body: bytes) -> Any:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text[:400]}


class AmadeusClient:
    def __init__(
        self,
        config: Config,
        transport: Transport | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self.config = config
        self._send = transport or _default_transport
        self._sleep = sleep
        self._retries = retries
        self._token: str | None = None
        self._token_expiry = 0.0

    # ---- transport --------------------------------------------------------

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, *, data: bytes | None = None, auth: bool = True) -> Any:
        url = self.config.host + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if auth:
            headers["Authorization"] = f"Bearer {self.token()}"
        delay = 1.0
        for attempt in range(self._retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            status, body = self._send(request)
            payload = _parse(body)
            if status < 400:
                return payload
            if status in RETRY_STATUSES and attempt < self._retries:
                self._sleep(delay)
                delay *= 2
                continue
            raise AmadeusError(status, url, payload)
        raise AssertionError("unreachable")

    def token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            }
        ).encode()
        payload = self._request("POST", "/v1/security/oauth2/token", data=form, auth=False)
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise AmadeusError(200, self.config.host + "/v1/security/oauth2/token", payload)
        self._token = payload["access_token"]
        self._token_expiry = time.time() + float(payload.get("expires_in", 1799))
        return self._token

    # ---- lookups ------------------------------------------------------------

    def city_code(self, place: str) -> tuple[str, str]:
        """Resolve a city name (or pass through a 3-letter code) to ``(code, label)``."""
        text = place.strip()
        if len(text) == 3 and text.isalpha():
            return text.upper(), text.upper()
        payload = self._request(
            "GET",
            "/v1/reference-data/locations",
            {"subType": "CITY", "keyword": text, "page[limit]": 5, "view": "LIGHT"},
        )
        matches = [m for m in (payload or {}).get("data", []) if m.get("iataCode")]
        if not matches:
            raise SearchError(
                f"Amadeus does not know a city called {text!r}; try the nearest big city "
                "or its 3-letter airport/city code (e.g. SEA, LAX, NYC)."
            )
        best = matches[0]
        name = best.get("name") or best["iataCode"]
        if name.isupper():
            name = name.title()
        country = (best.get("address") or {}).get("countryCode")
        return best["iataCode"], f"{name}, {country}" if country else name

    def hotels_in_city(self, city_code: str, *, radius_km: int = 10, limit: int = 60) -> list[dict[str, Any]]:
        """Hotels Amadeus knows near the city centre, nearest first."""
        payload = self._request(
            "GET",
            "/v1/reference-data/locations/hotels/by-city",
            {"cityCode": city_code, "radius": radius_km, "radiusUnit": "KM", "hotelSource": "ALL"},
        )
        hotels = [h for h in (payload or {}).get("data", []) if h.get("hotelId")]
        hotels.sort(key=lambda h: h.get("distance", {}).get("value", 0))
        return hotels[:limit]

    def offers(
        self,
        hotel_ids: Sequence[str],
        check_in: str,
        check_out: str,
        *,
        adults: int = 1,
        rooms: int = 1,
        currency: str = "USD",
    ) -> list[dict[str, Any]]:
        """Best available offer per hotel, batched to keep Amadeus responsive."""
        found: list[dict[str, Any]] = []
        for start in range(0, len(hotel_ids), OFFER_BATCH):
            batch = hotel_ids[start : start + OFFER_BATCH]
            try:
                payload = self._request(
                    "GET",
                    "/v3/shopping/hotel-offers",
                    {
                        "hotelIds": ",".join(batch),
                        "checkInDate": check_in,
                        "checkOutDate": check_out,
                        "adults": adults,
                        "roomQuantity": rooms,
                        "currency": currency,
                        "bestRateOnly": "true",
                        "includeClosed": "false",
                    },
                )
            except AmadeusError as exc:
                # A batch where *no* hotel has availability comes back as 400
                # "NO ROOMS AVAILABLE" (code 3664) rather than an empty list.
                if exc.status == 400 and any(str(e.get("code")) in {"3664", "1257"} for e in exc.errors if isinstance(e, dict)):
                    continue
                raise
            found.extend(h for h in (payload or {}).get("data", []) if h.get("available", True))
        return found


def _to_quote(hotel_offer: dict[str, Any], nights: int, distances: dict[str, float], environment: str = TEST) -> Quote | None:
    hotel = hotel_offer.get("hotel") or {}
    offers = hotel_offer.get("offers") or []
    if not offers:
        return None
    offer = min(offers, key=lambda o: float((o.get("price") or {}).get("total") or "inf"))
    price = offer.get("price") or {}
    try:
        total = float(price["total"])
    except (KeyError, TypeError, ValueError):
        return None
    room = offer.get("room") or {}
    estimated = room.get("typeEstimated") or {}
    room_bits = [
        str(estimated.get("category", "")).replace("_", " ").title(),
        f"{estimated['beds']} {estimated.get('bedType', 'bed').lower()}" if estimated.get("beds") else "",
    ]
    policies = offer.get("policies") or {}
    verdict = (policies.get("refundable") or {}).get("cancellationRefund")
    cancellations = policies.get("cancellations") or []
    refundable: bool | None
    if verdict:
        refundable = verdict == "REFUNDABLE_UP_TO_DEADLINE"
    elif cancellations:
        # A cancellation rule with a deadline means free cancellation until
        # then; a rule with no deadline is a penalty from the moment of booking.
        refundable = any(c.get("deadline") for c in cancellations if isinstance(c, dict))
    else:
        refundable = None
    hotel_id = hotel.get("hotelId", "")
    return Quote(
        hotel=hotel.get("name") or hotel_id or "Unnamed hotel",
        total=total,
        currency=str(price.get("currency") or "USD").upper(),
        nights=nights,
        source="amadeus-test" if environment == TEST else "amadeus",
        room=" · ".join(b for b in room_bits if b),
        hotel_id=hotel_id,
        refundable=refundable,
        distance_km=distances.get(hotel_id),
        extra={"offer_id": offer.get("id"), "board": offer.get("boardType"), "rate_code": offer.get("rateCode")},
    )


def search(
    client: AmadeusClient,
    place: str,
    check_in: str,
    check_out: str,
    *,
    adults: int = 1,
    rooms: int = 1,
    currency: str = "USD",
    radius_km: int = 10,
    max_hotels: int = 60,
) -> tuple[str, list[Quote]]:
    """Price every hotel Amadeus knows near ``place`` and return ``(label, quotes)``."""
    nights = nights_between(check_in, check_out)
    code, label = client.city_code(place)
    hotels = client.hotels_in_city(code, radius_km=radius_km, limit=max_hotels)
    if not hotels:
        raise SearchError(f"Amadeus lists no hotels within {radius_km} km of {label} ({code}).")
    distances = {
        h["hotelId"]: float(h["distance"]["value"])
        for h in hotels
        if isinstance(h.get("distance"), dict) and "value" in h["distance"]
    }
    quotes: list[Quote] = []
    for entry in client.offers([h["hotelId"] for h in hotels], check_in, check_out, adults=adults, rooms=rooms, currency=currency):
        quote = _to_quote(entry, nights, distances, client.config.environment)
        if quote:
            quotes.append(quote)
    return label, quotes
