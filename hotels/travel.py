"""Drive, fly or bus? Cost of getting there, so the hotel is not the only number.

A round trip by car costs fuel (or a flat per-mile rate, e.g. the IRS
figure, if you want wear and tear counted) plus hotel parking. A ticketed
option (flight, bus) costs a fare each way per person plus whatever getting
to and from the station costs; the return fare can differ, and can be zero
when someone else is driving you home. :func:`compare` totals every option,
adds the hotel if given, names the cheapest, and works out the outbound
fare at which each ticketed option would break even with driving.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .search import SearchError

#: US federal mileage rate, dollars per mile, for the --per-mile shortcut.
IRS_RATE = 0.70


@dataclass(frozen=True)
class Drive:
    one_way_miles: float
    mpg: float = 30.0
    gas_price: float = 4.50
    per_mile: float | None = None  # overrides mpg/gas when set
    parking_per_night: float = 0.0
    nights: int = 1
    one_way_hours: float | None = None

    name = "drive"

    @property
    def round_trip_miles(self) -> float:
        return self.one_way_miles * 2

    @property
    def fuel(self) -> float:
        if self.per_mile is not None:
            return self.round_trip_miles * self.per_mile
        return self.round_trip_miles / self.mpg * self.gas_price

    @property
    def parking(self) -> float:
        return self.parking_per_night * self.nights

    @property
    def total(self) -> float:
        return self.fuel + self.parking


@dataclass(frozen=True)
class Ticket:
    """A flight or bus: a fare each way per person, plus station extras."""

    name: str
    fare_out: float | None  # per person; None if not known yet
    fare_back: float | None = None  # per person; None means same as fare_out, 0 means a free ride home
    travelers: int = 1
    extras: float = 0.0  # airport/station parking, rideshares, bags: whole trip

    @property
    def known(self) -> bool:
        return self.fare_out is not None

    @property
    def fare_round_trip(self) -> float | None:
        if self.fare_out is None:
            return None
        back = self.fare_out if self.fare_back is None else self.fare_back
        return self.fare_out + back

    @property
    def fares(self) -> float | None:
        rt = self.fare_round_trip
        return None if rt is None else rt * self.travelers

    @property
    def total(self) -> float | None:
        return None if self.fares is None else self.fares + self.extras


def Fly(fare_per_person: float | None, travelers: int = 1, extras: float = 0.0, fare_back: float | None = None) -> Ticket:
    return Ticket("fly", fare_per_person, fare_back, travelers, extras)


def Bus(fare_per_person: float | None, travelers: int = 1, extras: float = 0.0, fare_back: float | None = None) -> Ticket:
    return Ticket("bus", fare_per_person, fare_back, travelers, extras)


@dataclass(frozen=True)
class Comparison:
    drive: Drive
    tickets: tuple[Ticket, ...]
    hotel: float = 0.0
    notes: list[str] = field(default_factory=list)

    # ---- per option ---------------------------------------------------------

    def ticket(self, name: str) -> Ticket | None:
        return next((t for t in self.tickets if t.name == name), None)

    @property
    def fly(self) -> Ticket | None:
        return self.ticket("fly")

    @property
    def bus(self) -> Ticket | None:
        return self.ticket("bus")

    def totals(self) -> dict[str, float | None]:
        """Getting-there cost per option name, None where the fare is unknown."""
        out: dict[str, float | None] = {"drive": self.drive.total}
        for t in self.tickets:
            out[t.name] = t.total
        return out

    def trip_totals(self) -> dict[str, float | None]:
        return {k: None if v is None else v + self.hotel for k, v in self.totals().items()}

    @property
    def drive_total(self) -> float:
        return self.drive.total + self.hotel

    @property
    def fly_total(self) -> float | None:
        return self.trip_totals().get("fly")

    def break_even(self, ticket: Ticket) -> float:
        """One-way fare per person at which ``ticket`` costs the same as driving.

        With no return fare given the return is assumed to match, so the
        figure is half the round-trip budget; with one given (including a
        free ride back) it is what is left for the outbound leg.
        """
        per_person = (self.drive.total - ticket.extras) / max(ticket.travelers, 1)
        if ticket.fare_back is None:
            per_person /= 2
        else:
            per_person -= ticket.fare_back
        return max(per_person, 0.0)

    @property
    def break_even_fare(self) -> float:
        first = self.tickets[0] if self.tickets else Fly(None)
        return self.break_even(first)

    # ---- the verdict --------------------------------------------------------

    @property
    def cheaper(self) -> str | None:
        """Name of the cheapest option, "tie" if two share the low, None if no fare is known."""
        known = {k: v for k, v in self.totals().items() if v is not None}
        if len(known) < 2:
            return None
        low = min(known.values())
        winners = [k for k, v in known.items() if abs(v - low) < 0.005]
        return "tie" if len(winners) > 1 else winners[0]

    @property
    def saving(self) -> float:
        """How much the cheapest option beats driving by (0 when driving wins or nothing is known)."""
        known = [v for k, v in self.totals().items() if v is not None and k != "drive"]
        if not known:
            return 0.0
        return max(self.drive.total - min(known), 0.0) if self.cheaper != "drive" else min(known) - self.drive.total


def compare(drive: Drive, fly: Ticket | Sequence[Ticket] | None = None, hotel: float = 0.0, *, tickets: Sequence[Ticket] = ()) -> Comparison:
    """Compare driving with any number of ticketed options.

    ``fly`` may be one ticket (the old single-option call) or a sequence;
    ``tickets`` adds more. Every ticket needs a distinct name.
    """
    all_tickets: list[Ticket] = []
    if isinstance(fly, Ticket):
        all_tickets.append(fly)
    elif fly:
        all_tickets.extend(fly)
    all_tickets.extend(tickets)
    if drive.one_way_miles <= 0:
        raise SearchError("miles must be positive")
    if drive.per_mile is None and (drive.mpg <= 0 or drive.gas_price < 0):
        raise SearchError("mpg must be positive and gas price cannot be negative")
    if drive.nights < 0 or drive.parking_per_night < 0 or hotel < 0:
        raise SearchError("nights, parking and hotel cannot be negative")
    names = set()
    for t in all_tickets:
        if t.travelers < 1:
            raise SearchError(f"{t.name}: travelers must be at least 1")
        if (t.fare_out is not None and t.fare_out < 0) or (t.fare_back is not None and t.fare_back < 0) or t.extras < 0:
            raise SearchError(f"{t.name}: fares and extras cannot be negative")
        if t.name in names:
            raise SearchError(f"two options are both called {t.name!r}")
        names.add(t.name)
    notes = []
    if drive.per_mile is None:
        notes.append(f"Driving counts fuel only; add --per-mile {IRS_RATE} (the IRS rate) to include wear and tear.")
    if drive.one_way_hours:
        notes.append(f"Driving is about {drive.one_way_hours:g} hours each way, {drive.one_way_hours * 2:g} hours on the road in total.")
    return Comparison(drive=drive, tickets=tuple(all_tickets), hotel=hotel, notes=notes)
