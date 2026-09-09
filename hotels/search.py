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
    """One bookable price for one hotel over the whole stay."""

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
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def per_night(self) -> float:
        return self.total / max(self.nights, 1)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "hotel": self.hotel,
            "total": round(self.total, 2),
            "per_night": round(self.per_night, 2),
            "currency": self.currency,
            "nights": self.nights,
        }
        for key in ("source", "room", "hotel_id", "url"):
            value = getattr(self, key)
            if value:
                data[key] = value
        if self.refundable is not None:
            data["refundable"] = self.refundable
        if self.distance_km is not None:
            data["distance_km"] = round(self.distance_km, 1)
        if self.sportsbook is not None:
            data["sportsbook"] = self.sportsbook
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
        )


SPORTSBOOK_MAX = 5


def parse_sportsbook(value: Any, hotel: str = "") -> int | None:
    """Validate a sportsbook rating: an integer 1-5, or None/blank for unknown."""
    if value is None or value == "":
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        rating = -1
    if not 1 <= rating <= SPORTSBOOK_MAX:
        where = f"{hotel}: " if hotel else ""
        raise SearchError(f"{where}sportsbook must be a whole number from 1 to {SPORTSBOOK_MAX}, not {value!r}")
    return rating


def rank(
    quotes: Iterable[Quote],
    *,
    refundable_only: bool = False,
    max_total: float | None = None,
    min_sportsbook: int | None = None,
) -> list[Quote]:
    """Cheapest first. Ties break on per-night price, then hotel name.

    ``min_sportsbook`` drops hotels whose sportsbook is unrated or rated below
    it. Quotes in different currencies are not converted; they are grouped by
    currency with the requested/most common one first, so "cheapest" is never
    a comparison of dollars against euros.
    """
    kept = [
        q
        for q in quotes
        if (not refundable_only or q.refundable)
        and (max_total is None or q.total <= max_total)
        and (min_sportsbook is None or (q.sportsbook or 0) >= min_sportsbook)
    ]
    counts: dict[str, int] = {}
    for q in kept:
        counts[q.currency] = counts.get(q.currency, 0) + 1
    order = sorted(counts, key=lambda c: (-counts[c], c))
    return sorted(kept, key=lambda q: (order.index(q.currency), q.total, q.per_night, q.hotel.lower()))


def cheapest(quotes: Iterable[Quote], **filters: Any) -> Quote | None:
    ranked = rank(quotes, **filters)
    return ranked[0] if ranked else None
