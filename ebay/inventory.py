"""A local backlog tracker for physical items, independent of eBay.

This solves a different problem than the eBay APIs can: eBay only knows
about things that are already listed. It has no idea what's sitting
around unlisted, waiting to be photographed, drafted, or priced. This
module is a plain JSON file of items with a status, so a big backlog of
"stuff to list eventually" doesn't just live in someone's memory.

Deliberately not tied to eBay auth or network access - adding, listing, and
updating backlog items works offline and instantly, since it is pure local
bookkeeping. Linking an item to a live SKU/listing once it does go up is
just another field on the record, not a live lookup.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: In order, from "not touched yet" to "done". Not enforced as a strict
#: state machine - a seller can jump straight from unlisted to sold if a
#: listing gets made and sells before the tracker is updated in between.
STATUSES = ("unlisted", "drafted", "listed", "sold")

DEFAULT_PATH = Path("inventory.json")


class InventoryError(ValueError):
    """A backlog operation could not be completed, with a reason worth reading."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class InventoryItem:
    id: str
    description: str
    status: str = "unlisted"
    category: str = ""
    sku: str = ""
    ebay_item_id: str = ""
    notes: str = ""
    added: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InventoryStore:
    """Loads and saves the backlog as a JSON file, one record per item."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8") or "[]")

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")

    def all(self) -> list[InventoryItem]:
        return [InventoryItem(**item) for item in self._load()]

    def get(self, item_id: str) -> InventoryItem:
        for item in self.all():
            if item.id == item_id:
                return item
        raise InventoryError(f"no backlog item with id {item_id!r}")

    def add(
        self,
        description: str,
        *,
        category: str = "",
        notes: str = "",
        status: str = "unlisted",
    ) -> InventoryItem:
        if not description.strip():
            raise InventoryError("description is required")
        if status not in STATUSES:
            raise InventoryError(f"status {status!r} is not one of: {', '.join(STATUSES)}")
        items = self._load()
        next_id = str(max((int(i["id"]) for i in items), default=0) + 1)
        record = InventoryItem(
            id=next_id, description=description.strip(), category=category,
            notes=notes, status=status,
        )
        items.append(record.to_dict())
        self._save(items)
        return record

    def update(
        self,
        item_id: str,
        *,
        status: str | None = None,
        sku: str | None = None,
        ebay_item_id: str | None = None,
        notes: str | None = None,
    ) -> InventoryItem:
        if status is not None and status not in STATUSES:
            raise InventoryError(f"status {status!r} is not one of: {', '.join(STATUSES)}")
        items = self._load()
        for raw in items:
            if raw["id"] == item_id:
                if status is not None:
                    raw["status"] = status
                if sku is not None:
                    raw["sku"] = sku
                if ebay_item_id is not None:
                    raw["ebay_item_id"] = ebay_item_id
                if notes is not None:
                    raw["notes"] = notes
                raw["updated"] = _now()
                self._save(items)
                return InventoryItem(**raw)
        raise InventoryError(f"no backlog item with id {item_id!r}")

    def remove(self, item_id: str) -> None:
        items = self._load()
        remaining = [i for i in items if i["id"] != item_id]
        if len(remaining) == len(items):
            raise InventoryError(f"no backlog item with id {item_id!r}")
        self._save(remaining)
