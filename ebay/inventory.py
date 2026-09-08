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

Each item tracks two marketplaces: ``status`` is the eBay side (with the
SKU and item id) and ``mercari`` is the Mercari side (with the listing
URL). Mercari has no API, so the Mercari fields can only ever be updated by
hand - the point of tracking them here is that "what still isn't on
Mercari" and "this sold on one site, is it still live on the other" become
questions the file can answer.
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
    #: Mercari side: one of STATUSES, independent of the eBay ``status``.
    mercari: str = "unlisted"
    mercari_url: str = ""
    added: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def mercari_item_url(value: str) -> str:
    """A Mercari listing URL from either a full URL or a bare item id.

    Mercari item ids look like ``m12345678901``; the app's share link and
    the web URL both carry it. Anything already starting with ``http`` is
    kept as given.
    """
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://www.mercari.com/us/item/{value.strip('/')}/"


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
        mercari_url: str = "",
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
            mercari="listed" if mercari_url else "unlisted",
            mercari_url=mercari_item_url(mercari_url),
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
        mercari: str | None = None,
        mercari_url: str | None = None,
    ) -> InventoryItem:
        """Change the given fields only.

        A ``mercari_url`` on its own also marks the Mercari side ``listed``
        (unless it was already listed or sold), since linking the listing is
        how a seller says it went up; pass ``mercari`` too to say otherwise.
        """
        if status is not None and status not in STATUSES:
            raise InventoryError(f"status {status!r} is not one of: {', '.join(STATUSES)}")
        if mercari is not None and mercari not in STATUSES:
            raise InventoryError(f"mercari status {mercari!r} is not one of: {', '.join(STATUSES)}")
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
                if mercari_url is not None:
                    raw["mercari_url"] = mercari_item_url(mercari_url)
                    if mercari is None and raw.get("mercari", "unlisted") in ("unlisted", "drafted"):
                        raw["mercari"] = "listed"
                if mercari is not None:
                    raw["mercari"] = mercari
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
