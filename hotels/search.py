"""Provider-neutral hotel quotes and the ranking that picks the cheapest.

Everything an API or a hand-collected price list produces is normalised to a
:class:`Quote` so the same ranking works whichever way the prices came in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping


class SearchError(RuntimeError):
    """Bad input: dates, quote files, or a stay that makes no sense."""


def parse_date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise SearchError(f"dates must look like 2026-10-03, not {text!r}") from None


def nights_between(check_in: str | date, check_out: str | date) -> int:
    """Number of nights in a stay; both ends may be strings or dates."""
    start = parse_date(check_in) if isinstance(check_in, str) else check_in
    end = parse_date(check_out) if isinstance(check_out, str) else check_out
    nights = (end - start).days
    if nights < 1:
        raise SearchError(f"check-out ({end}) must be after check-in ({start})")
    return nights


@dataclass(order=False)
class Quote:
    """One bookable price for one hotel over the whole stay.

    ``total`` is the price as quoted. Anything the quote leaves out but the
    hotel will charge anyway (resort fees, room tax) goes in ``fees`` and
    ``tax_pct``, and ``all_in`` is what actually leaves your account. Ranking
    and price filters use ``all_in``, never the headline number.
    """

    hotel: str
    total: float
    currency: str = "USD"
    nights: int = 1
    source: str = ""
    room: str = ""
    hotel_id: str = ""
    url: str = ""
    refundable: bool | None = None
    distance_km: float | None = None
    #: How good the on-site sportsbook is, 1 (a kiosk) to 5 (Circa-level); None if unknown.
    sportsbook: int | None = None
    #: Hotel class, 1-5 stars in half-star steps, as booking sites list it; None if unknown.
    stars: float | None = None
    #: Mandatory extras for the whole stay that the quoted total leaves out (resort fees...).
    fees: float = 0.0
    fee_note: str = ""
    #: Room tax not in the quote, as a percentage of room + fees; 0 if the quote includes it.
    tax_pct: float = 0.0
    #: Free in-room WiFi; None if unknown.
    wifi: bool | None = None
    #: How comfortable the hotel and its surroundings are for someone on their own,
    #: 1 (drive in, don't walk) to 5 (busy, lit, security at every door); None if unknown.
    safety: int | None = None
    #: Where it is and what the walk is like ("center Strip", "off-Strip on Koval Ln...").
    area: str = ""
    #: Loyalty programme the stay earns in ("Caesars Rewards", "MGM Rewards"...), "" if unknown.
    rewards: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def all_in(self) -> float:
        """Quoted total plus hidden fees plus tax on both."""
        return (self.total + self.fees) * (1 + self.tax_pct / 100)

    @property
    def extras(self) -> float:
        return self.all_in - self.total

    @property
    def per_night(self) -> float:
        """All-in cost per night."""
        return self.all_in / max(self.nights, 1)

    def extras_note(self) -> str:
        """Human description of what the quote left out, "" if nothing."""
        bits = []
        if self.fees:
            per_night = self.fees / max(self.nights, 1)
            label = self.fee_note or "fees"
            bits.append(f"{per_night:,.2f}/night {label}")
        if self.tax_pct:
            bits.append(f"{self.tax_pct:g}% tax")
        return " + ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "hotel": self.hotel,
            "total": round(self.all_in, 2),
            "per_night": round(self.per_night, 2),
            "currency": self.currency,
            "nights": self.nights,
        }
        if self.extras:
            data["quoted"] = round(self.total, 2)
            data["extras"] = round(self.extras, 2)
            data["extras_note"] = self.extras_note()
        if self.wifi is not None:
            data["wifi"] = self.wifi
        for key in ("source", "room", "hotel_id", "url", "rewards", "area"):
            value = getattr(self, key)
            if value:
                data[key] = value
        if self.refundable is not None:
            data["refundable"] = self.refundable
        if self.distance_km is not None:
            data["distance_km"] = round(self.distance_km, 1)
        if self.stars is not None:
            data["stars"] = self.stars
        if self.sportsbook is not None:
            data["sportsbook"] = self.sportsbook
        if self.safety is not None:
            data["safety"] = self.safety
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], default_nights: int = 1) -> "Quote":
        """Build a quote from a hand-written dict (see ``compare`` in the CLI).

        Accepts either ``total`` for the stay or ``per_night`` (multiplied by
        ``nights``), so a price list can be typed the way each site shows it.
        """
        if not isinstance(raw, Mapping):
            raise SearchError(f"each quote must be an object, got {raw!r}")
        hotel = str(raw.get("hotel") or raw.get("name") or "").strip()
        if not hotel:
            raise SearchError(f"quote is missing a hotel name: {dict(raw)}")
        nights = int(raw.get("nights") or default_nights)
        if nights < 1:
            raise SearchError(f"{hotel}: nights must be at least 1")
        try:
            if raw.get("total") not in (None, ""):
                total = float(raw["total"])
            elif raw.get("per_night") not in (None, ""):
                total = float(raw["per_night"]) * nights
            else:
                raise SearchError(f"{hotel}: needs a 'total' or 'per_night' price")
        except (TypeError, ValueError):
            raise SearchError(f"{hotel}: price is not a number") from None
        refundable = raw.get("refundable")
        sportsbook = parse_sportsbook(raw.get("sportsbook"), hotel)
        stars = parse_stars(raw.get("stars"), hotel)
        safety = parse_rating(raw.get("safety"), "safety", hotel)
        fees = _number(raw, "fees", hotel)
        fee_per_night = _number(raw, "fee_per_night", hotel)
        if fee_per_night:
            fees += fee_per_night * nights
        tax_pct = _number(raw, "tax_pct", hotel)
        if fees < 0 or tax_pct < 0:
            raise SearchError(f"{hotel}: fees and tax_pct cannot be negative")
        wifi = raw.get("wifi")
        if isinstance(wifi, str):
            wifi = {"true": True, "yes": True, "free": True, "1": True, "false": False, "no": False, "paid": False, "0": False}.get(wifi.strip().lower())
        return cls(
            hotel=hotel,
            total=total,
            currency=str(raw.get("currency") or "USD").upper(),
            nights=nights,
            source=str(raw.get("source") or ""),
            room=str(raw.get("room") or ""),
            hotel_id=str(raw.get("hotel_id") or ""),
            url=str(raw.get("url") or ""),
            refundable=None if refundable is None else bool(refundable),
            sportsbook=sportsbook,
            stars=stars,
            safety=safety,
            area=str(raw.get("area") or "").strip(),
            fees=fees,
            fee_note=str(raw.get("fee_note") or ("resort fee" if fees else "")),
            tax_pct=tax_pct,
            wifi=None if wifi is None else bool(wifi),
            rewards=str(raw.get("rewards") or "").strip(),
        )


SPORTSBOOK_MAX = 5
STARS_MAX = 5


def _number(raw: Mapping[str, Any], key: str, hotel: str) -> float:
    value = raw.get(key)
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).lstrip("$").rstrip("%"))
    except ValueError:
        raise SearchError(f"{hotel}: {key} is not a number: {value!r}") from None


def parse_stars(value: Any, hotel: str = "") -> float | None:
    """Validate a hotel class: 1-5 stars in half-star steps, or None/blank for unknown."""
    if value is None or value == "":
        return None
    try:
        stars = float(str(value).rstrip("*★ "))
    except ValueError:
        stars = -1.0
    if not 1 <= stars <= STARS_MAX or (stars * 2) != int(stars * 2):
        where = f"{hotel}: " if hotel else ""
        raise SearchError(f"{where}stars must be 1 to {STARS_MAX} in half steps (3, 3.5, 4...), not {value!r}")
    return stars


def parse_rating(value: Any, name: str, hotel: str = "") -> int | None:
    """Validate a 1-5 whole-number rating, or None/blank for unknown."""
    if value is None or value == "":
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        rating = -1
    if not 1 <= rating <= SPORTSBOOK_MAX:
        where = f"{hotel}: " if hotel else ""
        raise SearchError(f"{where}{name} must be a whole number from 1 to {SPORTSBOOK_MAX}, not {value!r}")
    return rating


def parse_sportsbook(value: Any, hotel: str = "") -> int | None:
    return parse_rating(value, "sportsbook", hotel)


def rank(
    quotes: Iterable[Quote],
    *,
    refundable_only: bool = False,
    max_total: float | None = None,
    min_sportsbook: int | None = None,
    rewards: str | None = None,
    min_stars: float | None = None,
    wifi_only: bool = False,
    min_safety: int | None = None,
) -> list[Quote]:
    """Cheapest all-in first. Ties break on per-night price, then hotel name.

    ``max_total`` and ``wifi_only`` filter on the all-in price and free WiFi
    (hotels with unknown WiFi are dropped by ``wifi_only``).

    ``min_stars``, ``min_sportsbook`` and ``min_safety`` drop hotels rated
    below them or not rated at all; ``rewards`` keeps only hotels whose programme name contains that text
    (case-insensitive, so ``"caesars"`` matches "Caesars Rewards").
    Quotes in different currencies are not converted; they are grouped by
    currency with the requested/most common one first, so "cheapest" is never
    a comparison of dollars against euros.
    """
    kept = [
        q
        for q in quotes
        if (not refundable_only or q.refundable)
        and (max_total is None or q.all_in <= max_total)
        and (min_sportsbook is None or (q.sportsbook or 0) >= min_sportsbook)
        and (rewards is None or rewards.strip().lower() in q.rewards.lower())
        and (min_stars is None or (q.stars or 0) >= min_stars)
        and (not wifi_only or q.wifi)
        and (min_safety is None or (q.safety or 0) >= min_safety)
    ]
    counts: dict[str, int] = {}
    for q in kept:
        counts[q.currency] = counts.get(q.currency, 0) + 1
    order = sorted(counts, key=lambda c: (-counts[c], c))
    return sorted(kept, key=lambda q: (order.index(q.currency), q.all_in, q.per_night, q.hotel.lower()))


def cheapest(quotes: Iterable[Quote], **filters: Any) -> Quote | None:
    ranked = rank(quotes, **filters)
    return ranked[0] if ranked else None
