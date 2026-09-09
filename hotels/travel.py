"""Drive or fly? Cost of getting there, so the hotel is not the only number.

A round trip by car costs fuel (or a flat per-mile rate, e.g. the IRS
figure, if you want wear and tear counted) plus hotel parking. Flying costs
the fare per person plus whatever getting to and from airports costs.
:func:`compare` totals both, adds the hotel if given, and works out the fare
per person at which flying would break even with driving.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .search import SearchError

#: US federal mileage rate, cents per mile, for the --per-mile shortcut.
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
class Fly:
    fare_per_person: float | None  # round trip; None if not known yet
    travelers: int = 1
    extras: float = 0.0  # airport parking, rideshares, bags: whole trip

    @property
    def fares(self) -> float | None:
        return None if self.fare_per_person is None else self.fare_per_person * self.travelers

    @property
    def total(self) -> float | None:
        return None if self.fares is None else self.fares + self.extras


@dataclass(frozen=True)
class Comparison:
    drive: Drive
    fly: Fly
    hotel: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def drive_total(self) -> float:
        return self.drive.total + self.hotel

    @property
    def fly_total(self) -> float | None:
        return None if self.fly.total is None else self.fly.total + self.hotel

    @property
    def break_even_fare(self) -> float:
        """Round-trip fare per person at which flying costs the same as driving."""
        return max((self.drive.total - self.fly.extras) / max(self.fly.travelers, 1), 0.0)

    @property
    def cheaper(self) -> str | None:
        if self.fly_total is None:
            return None
        if abs(self.fly_total - self.drive_total) < 0.005:
            return "tie"
        return "fly" if self.fly_total < self.drive_total else "drive"

    @property
    def saving(self) -> float:
        return 0.0 if self.fly_total is None else abs(self.fly_total - self.drive_total)


def compare(drive: Drive, fly: Fly, hotel: float = 0.0) -> Comparison:
    if drive.one_way_miles <= 0:
        raise SearchError("miles must be positive")
    if drive.per_mile is None and (drive.mpg <= 0 or drive.gas_price < 0):
        raise SearchError("mpg must be positive and gas price cannot be negative")
    if fly.travelers < 1:
        raise SearchError("travelers must be at least 1")
    if fly.fare_per_person is not None and fly.fare_per_person < 0:
        raise SearchError("fare cannot be negative")
    if drive.nights < 0 or drive.parking_per_night < 0 or fly.extras < 0 or hotel < 0:
        raise SearchError("nights, parking, extras and hotel cannot be negative")
    notes = []
    if drive.per_mile is None:
        notes.append("Driving counts fuel only; add --per-mile 0.70 (the IRS rate) to include wear and tear.")
    if drive.one_way_hours:
        notes.append(f"Driving is about {drive.one_way_hours:g} hours each way, {drive.one_way_hours * 2:g} hours on the road in total.")
    return Comparison(drive=drive, fly=fly, hotel=hotel, notes=notes)
