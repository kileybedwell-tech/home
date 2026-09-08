"""Make a photo's pixels the single source of truth before it is uploaded.

A JPEG can say "these pixels are sideways, rotate them before display" in its
EXIF orientation tag, and eBay's image pipeline honours that tag. So does
Preview, and so does the phone that shot the photo. What does *not* honour it
is most quick-look tooling, which renders the raw pixels - which means a photo
can look correct while it is prepared and land sideways on the listing, or the
reverse.

The trap is rotating such a file with `sips -r`: that rewrites the pixels but
leaves the orientation tag untouched, so the tag's rotation gets applied a
second time on top of the one already baked in and the picture ends up 90 or
180 degrees out. That is exactly how two live card listings went out sideways.

Normalising before upload removes the ambiguity: bake whatever the tag asks
for into the pixels, then set the tag to 1 so it asks for nothing. After that
every viewer agrees, whether it reads EXIF or not.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

# EXIF orientation values that are a plain rotation, mapped to the clockwise
# degrees needed to bake them in. The other four legal values (2, 4, 5, 7) are
# mirror images, which cameras essentially never emit; those are left alone
# rather than half-handled, since eBay applies them correctly on its own.
_ROTATION_FOR_ORIENTATION = {3: 180, 6: 90, 8: 270}

_ORIENTATION_TAG = 0x0112


def _orientation_offset(data: bytes) -> tuple[int, str] | None:
    """Byte offset of the orientation value in `data`, plus its endianness."""
    start = data.find(b"Exif\x00\x00")
    if start < 0:
        return None
    tiff = start + 6
    if len(data) < tiff + 8:
        return None
    endian = ">" if data[tiff : tiff + 2] == b"MM" else "<"
    try:
        ifd = tiff + struct.unpack(endian + "I", data[tiff + 4 : tiff + 8])[0]
        count = struct.unpack(endian + "H", data[ifd : ifd + 2])[0]
    except (struct.error, IndexError):
        return None
    for index in range(count):
        entry = ifd + 2 + index * 12
        if len(data) < entry + 12:
            return None
        try:
            tag = struct.unpack(endian + "H", data[entry : entry + 2])[0]
        except struct.error:
            return None
        if tag == _ORIENTATION_TAG:
            return entry + 8, endian
    return None


def exif_orientation(data: bytes) -> int | None:
    """The EXIF orientation `data` declares, or None if it declares none."""
    found = _orientation_offset(data)
    if found is None:
        return None
    offset, endian = found
    try:
        return struct.unpack(endian + "H", data[offset : offset + 2])[0]
    except struct.error:
        return None


def clear_orientation(path: Path) -> bool:
    """Set the orientation tag on `path` to 1, in place. True if it changed.

    Only correct once the pixels themselves are already upright - it makes the
    file mean what its pixels say, it does not move any pixels.
    """
    data = bytearray(path.read_bytes())
    found = _orientation_offset(bytes(data))
    if found is None:
        return False
    offset, endian = found
    if struct.unpack(endian + "H", data[offset : offset + 2])[0] == 1:
        return False
    struct.pack_into(endian + "H", data, offset, 1)
    path.write_bytes(bytes(data))
    return True


def normalize_orientation(path: Path, *, workdir: Path | None = None) -> Path:
    """Return a photo whose pixels need no EXIF rotation to display correctly.

    Returns `path` itself when there is nothing to do, so the common case
    copies nothing. Otherwise the rotation is baked into a temporary copy and
    that path is returned; the caller's original file is never modified.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return path
    degrees = _ROTATION_FOR_ORIENTATION.get(exif_orientation(data) or 1)
    if degrees is None:
        return path

    target_dir = workdir or Path(tempfile.mkdtemp(prefix="ebay-photo-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.copy2(path, target)
    try:
        subprocess.run(
            ["sips", "-r", str(degrees), str(target)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # No sips, or it refused the file. eBay honours the tag correctly on
        # its own, so the untouched original is still the safer thing to send.
        return path
    clear_orientation(target)
    return target
