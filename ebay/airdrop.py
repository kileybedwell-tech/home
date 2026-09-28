"""Pick up photos AirDropped to this Mac and stage them as new items.

AirDrop saves into ~/Downloads and delivers one send as a quick burst of
files, so photos that land close together are one item and a gap longer
than ``group_gap`` seconds starts the next. Each item gets its own folder
under the photo root (``~/Desktop/eBay Photos`` unless ``EBAY_PHOTOS_DIR``
says otherwise), named ``New <date> <time>`` until it is drafted and
renamed after the item, like the folders already there.

HEIC photos are converted to JPEG with macOS's ``sips``: Mercari's web
uploader and most viewers want JPEG. The untouched originals move into an
``originals/`` subfolder, out of Downloads, so a photo is never picked up
twice and nothing is lost.

Only files that *arrived* after the last scan count. AirDrop stamps both
mtime and birth time with the photo's capture time, so neither says when
it landed; the inode change time (ctime) does, since moving a file into
Downloads updates it - it matches Finder's "Date Added". The first scan
ever just records "now", so years of old Downloads are left alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")
CONVERT_SUFFIXES = (".heic", ".heif", ".webp", ".png")

DEFAULT_WATCH_DIR = Path.home() / "Downloads"
DEFAULT_PHOTO_ROOT = Path.home() / "Desktop" / "eBay Photos"
STATE_FILE = Path.home() / ".config" / "ebay-connect" / "airdrop-state.json"

#: Seconds between photos that still count as the same AirDrop send.
GROUP_GAP = 60
#: Wait until nothing new has arrived for this long, so a send that is
#: still copying is not split or grabbed half-finished.
SETTLE = 15
#: Long edge in pixels; plenty for both sites and keeps uploads quick.
MAX_EDGE = 2000


@dataclass
class StagedItem:
    folder: Path
    photos: list[Path] = field(default_factory=list)


def photo_root() -> Path:
    return Path(os.environ.get("EBAY_PHOTOS_DIR") or DEFAULT_PHOTO_ROOT).expanduser()


def arrived_at(path: Path) -> float:
    return path.stat().st_ctime


def group_by_gap(stamped: list[tuple[float, Path]], gap: float = GROUP_GAP) -> list[list[Path]]:
    """Split time-sorted (arrival, path) pairs wherever the gap exceeds ``gap``."""
    groups: list[list[Path]] = []
    last = None
    for when, path in sorted(stamped):
        if last is None or when - last > gap:
            groups.append([])
        groups[-1].append(path)
        last = when
    return groups


def _load_since(state_file: Path) -> float | None:
    try:
        return float(json.loads(state_file.read_text())["since"])
    except (OSError, ValueError, KeyError):
        return None


def _save_since(state_file: Path, since: float) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"since": since}))


def _new_folder(root: Path, when: float) -> Path:
    base = "New " + datetime.fromtimestamp(when).strftime("%Y-%m-%d %H%M")
    folder, n = root / base, 1
    while folder.exists():
        n += 1
        folder = root / f"{base} ({n})"
    folder.mkdir(parents=True)
    return folder


def _to_jpeg(src: Path, dest: Path) -> None:
    if src.suffix.lower() in CONVERT_SUFFIXES:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", str(src), "--out", str(dest)],
            check=True, capture_output=True,
        )
    else:
        shutil.copy2(src, dest)
    subprocess.run(["sips", "-Z", str(MAX_EDGE), str(dest)], capture_output=True)


def scan(
    watch_dir: Path = DEFAULT_WATCH_DIR,
    root: Path | None = None,
    state_file: Path = STATE_FILE,
    *,
    group_gap: float = GROUP_GAP,
    settle: float = SETTLE,
    now: float | None = None,
) -> list[StagedItem]:
    """Stage any settled new photos as item folders. Returns what was staged."""
    root = root or photo_root()
    now = time.time() if now is None else now
    since = _load_since(state_file)
    if since is None:
        _save_since(state_file, now)
        return []

    stamped = []
    for path in Path(watch_dir).iterdir():
        if path.is_file() and path.suffix.lower() in PHOTO_SUFFIXES:
            when = arrived_at(path)
            if when > since:
                stamped.append((when, path))
    if not stamped:
        return []
    newest = max(when for when, _ in stamped)
    if now - newest < settle:
        return []

    staged = []
    for group in group_by_gap(stamped, group_gap):
        folder = _new_folder(root, arrived_at(group[0]))
        originals = folder / "originals"
        originals.mkdir()
        item = StagedItem(folder)
        for i, src in enumerate(group, 1):
            dest = folder / f"{i:02d}.jpg"
            _to_jpeg(src, dest)
            shutil.move(str(src), str(originals / src.name))
            item.photos.append(dest)
        staged.append(item)
    _save_since(state_file, newest)
    return staged


def notify(title: str, message: str) -> None:
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True)
