"""A narrow slice of eBay's classic Trading API (XML), used only for one
thing the newer REST Sell APIs cannot do: see every currently active
listing on the account.

``EbayClient.inventory_items`` (Sell Inventory API) only returns SKUs
created through that same API. A seller account can also carry listings
created through eBay's website, Seller Hub's bulk tools, File Exchange, or
third-party crosslisting tools - those never become "inventory items" and
are invisible to the REST API. ``GetMyeBaySelling`` reads the same backing
data My eBay's Active tab does, so it sees all of them regardless of how
they were created.

Authenticated with the same OAuth user token as the REST calls, via the
``X-EBAY-API-IAF-TOKEN`` header Trading API accepts in place of the classic
eBayAuthToken - no separate credential needed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree

from .auth import TokenStore
from .config import Config

_NS = "urn:ebay:apis:eBLBaseComponents"
_COMPATIBILITY_LEVEL = "1193"
_MAX_PAGE_SIZE = 200

# Magic-byte prefixes for the image formats eBay actually serves photos as,
# used to confirm a download is really a picture and not (say) an HTML error
# page saved with a .jpg name - a byte-count check alone would miss that.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def _image_extension(data: bytes) -> str | None:
    for magic, ext in _IMAGE_SIGNATURES:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


class TradingError(RuntimeError):
    """A Trading API call came back Ack=Failure, with eBay's own error list."""

    def __init__(self, call_name: str, errors: list[dict[str, str]]) -> None:
        self.call_name = call_name
        self.errors = errors
        summary = "; ".join(
            e.get("LongMessage") or e.get("ShortMessage", "") for e in errors
        ) or "no error detail returned"
        super().__init__(f"{call_name} failed: {summary}")

    @property
    def is_usage_limit(self) -> bool:
        """True when eBay refused the call for exceeding the app's quota.

        Error 218050 is the application-level call limit. eBay's advice in
        the message - call GetAPIAccessRules to see the usage - no longer
        works: that call was retired and answers HTTP 410.
        """
        return any(
            error.get("ErrorCode") == "218050"
            or "exceeded usage limit" in (error.get("LongMessage") or "").lower()
            for error in self.errors
        )


class PhotoArchiveError(RuntimeError):
    """A listing's photos could not be verified as saved to a local file.

    Raised instead of returning a partial result - a folder that looks like
    a complete archive but is silently missing a shot is worse than a clear
    failure, since nothing about it looks wrong until it's too late to matter.
    """


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return element.find(f"{{{_NS}}}{name}")


def _text(element: ElementTree.Element | None, default: str = "") -> str:
    return (element.text or default) if element is not None else default


def _call(config: Config, tokens: TokenStore, call_name: str, body: str) -> ElementTree.Element:
    """POST one Trading API call and return its parsed XML root."""
    headers = {
        "X-EBAY-API-IAF-TOKEN": tokens.access_token(),
        "X-EBAY-API-COMPATIBILITY-LEVEL": _COMPATIBILITY_LEVEL,
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "0",
        "Content-Type": "text/xml",
    }
    url = f"{config.api_host}/ws/api.dll"
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach {url}: {exc.reason}") from exc

    root = ElementTree.fromstring(raw)
    if _text(_child(root, "Ack")) not in ("Success", "Warning"):
        errors = [
            {
                "ShortMessage": _text(_child(error, "ShortMessage")),
                "LongMessage": _text(_child(error, "LongMessage")),
                "ErrorCode": _text(_child(error, "ErrorCode")),
            }
            for error in root.iter(f"{{{_NS}}}Errors")
        ]
        raise TradingError(call_name, errors)
    return root


def _item_dict(item: ElementTree.Element) -> dict[str, Any]:
    price = _child(item, "SellingStatus")
    price = _child(price, "CurrentPrice") if price is not None else None
    return {
        "itemId": _text(_child(item, "ItemID")),
        "sku": _text(_child(item, "SKU")),
        "title": _text(_child(item, "Title")),
        "price": _text(price),
        "currency": price.get("currencyID", "") if price is not None else "",
        "quantity": _text(_child(item, "QuantityAvailable")),
        "viewItemUrl": _text(_child(_child(item, "ListingDetails"), "ViewItemURL")),
    }


def active_listings(
    config: Config, tokens: TokenStore, max_items: int | None = None
) -> Iterator[dict[str, Any]]:
    """Every currently active listing on the account, most-recently-ended-first.

    Unlike the Sell Inventory API, this sees listings made any way at all -
    Seller Hub, File Exchange, third-party crosslisting tools included -
    since it is the same feed My eBay's Active tab reads. A seller with a
    few thousand listings costs a handful of requests at 200/page.
    """
    page = 1
    yielded = 0
    while True:
        if max_items is not None and yielded >= max_items:
            return
        entries = _MAX_PAGE_SIZE
        if max_items is not None:
            entries = min(entries, max_items - yielded)
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="{_NS}">
  <ActiveList>
    <Sort>TimeLeft</Sort>
    <Pagination>
      <EntriesPerPage>{entries}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
  <OutputSelector>ActiveList.ItemArray.Item.ItemID</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.Title</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.SKU</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.SellingStatus.CurrentPrice</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.QuantityAvailable</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.ListingDetails.ViewItemURL</OutputSelector>
  <OutputSelector>ActiveList.PaginationResult.TotalNumberOfPages</OutputSelector>
</GetMyeBaySellingRequest>"""
        root = _call(config, tokens, "GetMyeBaySelling", body)
        active = _child(root, "ActiveList")
        if active is None:
            return
        item_array = _child(active, "ItemArray")
        items = list(item_array.findall(f"{{{_NS}}}Item")) if item_array is not None else []
        for item in items:
            yield _item_dict(item)
            yielded += 1
            if max_items is not None and yielded >= max_items:
                return
        total_pages = int(_text(_child(_child(active, "PaginationResult"), "TotalNumberOfPages"), "1"))
        if not items or page >= total_pages:
            return
        page += 1


def get_item(config: Config, tokens: TokenStore, item_id: str) -> dict[str, Any]:
    """Full read-only details for one listing, by ItemID - classic GetItem.

    Works for any listing on the account regardless of how it was created
    (Seller Hub, File Exchange, this tool, ...), unlike the Sell Inventory
    API which only knows about its own SKUs.
    """
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="{_NS}">
  <ItemID>{item_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>"""
    root = _call(config, tokens, "GetItem", body)
    item = _child(root, "Item")
    pic_details = _child(item, "PictureDetails")
    picture_urls = (
        [_text(u) for u in pic_details.findall(f"{{{_NS}}}PictureURL")]
        if pic_details is not None
        else []
    )
    return {
        "itemId": item_id,
        "title": _text(_child(item, "Title")),
        "description": _text(_child(item, "Description")),
        "sku": _text(_child(item, "SKU")),
        "pictureUrls": picture_urls,
    }


def end_item(config: Config, tokens: TokenStore, item_id: str, reason: str = "NotAvailable") -> str:
    """End a live listing for good. Returns the EndTime eBay reports.

    Irreversible - a new listing must be created from scratch afterward.
    Callers planning to relist the same content should archive its photos
    first with ``archive_item_photos`` rather than calling this directly.
    """
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<EndItemRequest xmlns="{_NS}">
  <ItemID>{item_id}</ItemID>
  <EndingReason>{reason}</EndingReason>
</EndItemRequest>"""
    root = _call(config, tokens, "EndItem", body)
    return _text(_child(root, "EndTime"))


def _download_verified_image(url: str, dest: Path, *, label: str) -> Path:
    """Download one URL to ``dest``, verified as real image bytes on disk.

    Shared by ``archive_item_photos`` and ``import_item_photos`` - both need
    the identical "did this actually land as a readable picture" check, and
    a fix to one path (a new format, a stricter check) should not have to be
    made twice. ``label`` is just what the error names, e.g. "photo 2/4 for
    178131929484". ``dest``'s suffix is corrected to match the format
    actually downloaded (eBay's URLs don't reliably say), so the returned
    path - not the one passed in - is the one that was really written.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise PhotoArchiveError(f"could not download {label} ({url}): {exc}") from exc
    ext = _image_extension(data)
    if not data or ext is None:
        raise PhotoArchiveError(
            f"{label} did not download as a valid image ({url}) - got {len(data)} byte(s)"
        )
    dest = dest.with_suffix(ext)
    dest.write_bytes(data)
    if not dest.is_file() or dest.stat().st_size != len(data):
        raise PhotoArchiveError(f"could not verify {dest} was written to disk")
    return dest


def archive_item_photos(
    config: Config, tokens: TokenStore, item_id: str, dest_dir: str | Path
) -> list[Path]:
    """Download every photo of a live listing to ``dest_dir``, verified on disk.

    The one safeguard for ending a listing that is about to be split,
    merged, or otherwise recreated: its photos are usually the only copy,
    since most listings on this account were made outside this tool and it
    has no earlier local copy to fall back on. A failure here - a blocked
    network path, a bad URL, a truncated download - raises
    ``PhotoArchiveError`` naming exactly which photo failed, rather than
    silently saving whatever succeeded. Callers must treat that as "do not
    end the listing," not as a partial result to proceed with.

    Only reaches eBay's image host if the machine running it actually can -
    a hosted environment with that host blocked will fail here every time;
    run this from a machine with normal network access instead, e.g. via
    ``import_item_photos``/``import-photos`` from a local checkout.
    """
    item = get_item(config, tokens, item_id)
    urls = item["pictureUrls"]
    if not urls:
        raise PhotoArchiveError(f"listing {item_id} has no PictureURL to archive")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for i, url in enumerate(urls, start=1):
        path = dest / f"{item_id}-{i:02d}.jpg"
        saved.append(
            _download_verified_image(url, path, label=f"photo {i}/{len(urls)} for {item_id}")
        )
    return saved


def import_item_photos(
    config: Config, tokens: TokenStore, item_id: str, photos_root: str | Path = "photos"
) -> dict[str, Any]:
    """Download a listing's photos to a predictable local folder, with a manifest.

    For pulling an OLD listing's photos onto a machine with real network
    access to eBay (a local checkout, not this hosted environment), so they
    can be inspected and reused before the listing is ever touched. Meant to
    be run well ahead of any split/relist/end decision, not as part of one.

    The folder is keyed by SKU when the listing has one, else ``item-<ID>``
    (most pre-existing listings on this account have no SKU - they were
    never made through the Sell Inventory API). Layout:

        photos/<key>/manifest.json
        photos/<key>/01.jpg, 02.jpg, ...

    manifest.json carries itemId, sku, title, and each image's order,
    original eBay URL, and local filename - enough to know what a saved
    file actually shows without re-fetching anything. The same local paths
    work directly with ``create``/``create-auction --photo``, so nothing
    further is needed to reuse them in a new listing.

    Raises ``PhotoArchiveError`` (naming exactly which photo, same as
    ``archive_item_photos``) rather than writing a manifest that claims more
    than what is actually verified on disk.
    """
    item = get_item(config, tokens, item_id)
    urls = item["pictureUrls"]
    if not urls:
        raise PhotoArchiveError(f"listing {item_id} has no PictureURL to import")

    key = item["sku"] or f"item-{item_id}"
    dest = Path(photos_root) / key
    dest.mkdir(parents=True, exist_ok=True)

    images: list[dict[str, Any]] = []
    for i, url in enumerate(urls, start=1):
        path = dest / f"{i:02d}.jpg"
        path = _download_verified_image(url, path, label=f"photo {i}/{len(urls)} for {item_id}")
        images.append({"order": i, "url": url, "filename": path.name})

    manifest = {
        "itemId": item_id,
        "sku": item["sku"],
        "title": item["title"],
        "images": images,
    }
    manifest_path = dest / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest["localDir"] = str(dest)
    manifest["manifestPath"] = str(manifest_path)
    return manifest
