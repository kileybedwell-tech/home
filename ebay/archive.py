"""A local record of every listing on the account, however it was made.

Most of the account's listings were created outside this tool (Seller Hub,
File Exchange, crosslisters), so until now the only copy of their titles,
descriptions, item specifics and photos lived on eBay. Once a listing is
removed or ends, eBay stops serving it (GetItem on a policy-removed listing
fails outright), and that record is gone.

``archive_listings`` pulls everything through ``GetSellerList`` and writes
one JSON file per listing under ``listings/``, plus ``listings/index.csv``.
Rerunning it refreshes what changed and never deletes a file, so an ended
or removed listing keeps its last known record.

Photos are a separate step because eBay's image host is blocked in hosted
sessions: ``archive_photos`` downloads each listing's pictures into
``photos/item-<ID>/`` (``photos/<SKU>/`` for this tool's own listings,
matching ``import-photos``), skipping files already on disk. Run it from a
machine with normal network access, e.g. the Mac.
"""

from __future__ import annotations

import csv
import datetime
import html
import json
import re
from pathlib import Path
from typing import Any, Iterator

from . import trading
from .client import EbayClient

_NS = "{urn:ebay:apis:eBLBaseComponents}"
#: GetSellerList refuses an end-time window wider than 120 days.
_WINDOW_DAYS = 119


def _find(el, *names: str):
    for name in names:
        if el is None:
            return None
        el = el.find(_NS + name)
    return el


def _text(el, *names: str) -> str:
    found = _find(el, *names)
    return (found.text or "").strip() if found is not None and found.text else ""


def _plain(description: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", description)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _record(item) -> dict[str, Any]:
    specifics: dict[str, list[str]] = {}
    for nv in item.iter(_NS + "NameValueList"):
        name = _text(nv, "Name")
        if name:
            specifics[name] = [v.text or "" for v in nv.findall(_NS + "Value")]
    description = _text(item, "Description")
    return {
        "itemId": _text(item, "ItemID"),
        "sku": _text(item, "SKU"),
        "title": _text(item, "Title"),
        "url": _text(item, "ListingDetails", "ViewItemURL"),
        "status": _text(item, "SellingStatus", "ListingStatus"),
        "listingType": _text(item, "ListingType"),
        "price": _text(item, "SellingStatus", "CurrentPrice"),
        "quantity": _text(item, "Quantity"),
        "quantitySold": _text(item, "SellingStatus", "QuantitySold"),
        "categoryId": _text(item, "PrimaryCategory", "CategoryID"),
        "categoryName": _text(item, "PrimaryCategory", "CategoryName"),
        "condition": _text(item, "ConditionDisplayName"),
        "conditionDescription": _text(item, "ConditionDescription"),
        "startTime": _text(item, "ListingDetails", "StartTime"),
        "endTime": _text(item, "ListingDetails", "EndTime"),
        "shippingProfileId": _text(item, "SellerProfiles", "SellerShippingProfile", "ShippingProfileID"),
        "returnProfileId": _text(item, "SellerProfiles", "SellerReturnProfile", "ReturnProfileID"),
        "pictureUrls": [p.text for p in item.iter(_NS + "PictureURL") if p.text],
        "itemSpecifics": specifics,
        "descriptionText": _plain(description),
        "descriptionHtml": description,
    }


def _seller_list(client: EbayClient, start: datetime.datetime, end: datetime.datetime) -> Iterator[dict]:
    page = 1
    while True:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetSellerListRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <DetailLevel>ReturnAll</DetailLevel>
  <EndTimeFrom>{start:%Y-%m-%dT%H:%M:%S}.000Z</EndTimeFrom>
  <EndTimeTo>{end:%Y-%m-%dT%H:%M:%S}.000Z</EndTimeTo>
  <IncludeVariations>false</IncludeVariations>
  <Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination>
</GetSellerListRequest>"""
        root = trading._call(client.config, client.tokens, "GetSellerList", body)
        items = list(root.iter(_NS + "Item"))
        for item in items:
            yield _record(item)
        pages = int(_text(root, "PaginationResult", "TotalNumberOfPages") or 1)
        if page >= pages or not items:
            return
        page += 1


def archive_listings(client: EbayClient, root: str | Path = "listings", days_back: int = 119) -> dict[str, int]:
    """Save every listing ending between ``days_back`` ago and 120 days ahead.

    That window covers every active listing (fixed-price listings renew
    monthly, so their end time is never more than ~30 days out) plus
    everything that sold or ended recently.
    """
    out = Path(root)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    windows = []
    start = now - datetime.timedelta(days=days_back)
    stop = now + datetime.timedelta(days=_WINDOW_DAYS)
    while start < stop:
        end = min(start + datetime.timedelta(days=_WINDOW_DAYS), stop)
        windows.append((start, end))
        start = end
    seen: dict[str, dict] = {}
    for start, end in windows:
        for rec in _seller_list(client, start, end):
            seen[rec["itemId"]] = rec
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    new = changed = 0
    for item_id, rec in seen.items():
        path = out / f"{item_id}.json"
        old = json.loads(path.read_text()) if path.exists() else None
        rec["firstArchived"] = (old or {}).get("firstArchived", stamp)
        rec["lastSeen"] = stamp
        if old is None:
            new += 1
        elif {k: v for k, v in old.items() if k != "lastSeen"} != {k: v for k, v in rec.items() if k != "lastSeen"}:
            changed += 1
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    _write_index(out)
    return {"seen": len(seen), "new": new, "changed": changed, "total": len(list(out.glob("*.json")))}


def _write_index(out: Path) -> None:
    rows = []
    for path in sorted(out.glob("*.json")):
        r = json.loads(path.read_text())
        rows.append([r["itemId"], r.get("sku", ""), r["title"], r.get("price", ""), r.get("status", ""),
                     r.get("endTime", ""), len(r.get("pictureUrls", [])), r.get("lastSeen", "")])
    with (out / "index.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["itemId", "sku", "title", "price", "status", "endTime", "photos", "lastSeen"])
        w.writerows(rows)


def archive_photos(root: str | Path = "listings", photos_root: str | Path = "photos") -> dict[str, Any]:
    """Download every archived listing's photos that are not on disk yet.

    Stops at the first download failure that looks like a blocked host,
    rather than trying 10,000 URLs that will all fail the same way.
    """
    saved = skipped = 0
    for path in sorted(Path(root).glob("*.json")):
        rec = json.loads(path.read_text())
        dest = Path(photos_root) / (rec.get("sku") or f"item-{rec['itemId']}")
        for i, url in enumerate(rec.get("pictureUrls", []), start=1):
            if any(dest.glob(f"ebay-{i:02d}.*")):
                skipped += 1
                continue
            dest.mkdir(parents=True, exist_ok=True)
            try:
                trading._download_verified_image(url, dest / f"ebay-{i:02d}.jpg",
                                                 label=f"photo {i} for {rec['itemId']}")
            except trading.PhotoArchiveError as exc:
                return {"saved": saved, "skipped": skipped, "stoppedAt": rec["itemId"], "error": str(exc)}
            saved += 1
    return {"saved": saved, "skipped": skipped}
