"""Turn an eBay listing draft into a Mercari listing you can paste in.

Mercari US has no seller API. The only official Mercari API (Mercari Shops)
is for Japanese business sellers under contract, and crosslisting tools get
around that by driving a logged-in browser. So nothing in this module talks
to Mercari at all. It does the part that is actually worth automating:
producing the exact text and photo order Mercari's listing form wants, from
the same draft JSON that ``create --from-file`` takes, inside Mercari's
limits, so putting an item on Mercari is a paste rather than a retype.

Limits and condition names are Mercari's published ones (help center:
"Creating a listing", "Item conditions", "Mercari's price limit"). The eBay
condition ids and descriptor values come from eBay's own metadata API
(``python -m ebay condition-policy 183454``).
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Mercari's form limits.
MAX_TITLE = 80
MAX_DESCRIPTION = 1000
MAX_PHOTOS = 12
MIN_PRICE = 1.0
MAX_PRICE = 2000.0  # up to 5,000 for Authenticate-eligible luxury items

#: Mercari's five-step condition scale, as spelled in the app.
CONDITIONS = ("New", "Like New", "Good", "Fair", "Poor")

#: eBay Inventory API condition enum -> Mercari condition. Mercari has no
#: "refurbished" or "new with defects", so those pick the nearest step and
#: raise a warning to say so in the description.
EBAY_CONDITION_TO_MERCARI = {
    "NEW": "New",
    "NEW_OTHER": "New",
    "NEW_WITH_DEFECTS": "Good",
    "LIKE_NEW": "Like New",
    "CERTIFIED_REFURBISHED": "Like New",
    "EXCELLENT_REFURBISHED": "Like New",
    "VERY_GOOD_REFURBISHED": "Good",
    "GOOD_REFURBISHED": "Good",
    "SELLER_REFURBISHED": "Good",
    "USED_EXCELLENT": "Like New",
    "USED_VERY_GOOD": "Good",
    "USED_GOOD": "Good",
    "USED_ACCEPTABLE": "Fair",
    "FOR_PARTS_OR_NOT_WORKING": "Poor",
}

#: Trading cards: eBay conditionId 4000 (Ungraded) carries descriptor 40001
#: "Card Condition". Value ids and names per `condition-policy 183454`.
CARD_CONDITION_VALUES = {
    "400010": ("Near mint or better", "Like New"),
    "400015": ("Lightly played (Excellent)", "Good"),
    "400016": ("Moderately played (Very good)", "Fair"),
    "400017": ("Heavily played (Poor)", "Poor"),
}

#: Trading cards: eBay conditionId 2750 (Graded) carries descriptor 27502
#: "Grade". Numeric grades only; "Authentic"/"Sample" values are unmapped.
GRADE_VALUES = {
    "275020": 10.0, "275021": 9.5, "275022": 9.0, "275023": 8.5, "275024": 8.0,
    "275025": 7.5, "275026": 7.0, "275027": 6.5, "275028": 6.0, "275029": 5.5,
    "2750210": 5.0, "2750211": 4.5, "2750212": 4.0, "2750213": 3.5, "2750214": 3.0,
    "2750215": 2.5, "2750216": 2.0, "2750217": 1.5, "2750218": 1.0,
}

#: Free-text "Card Condition" aspect values, for drafts that carry the text
#: but not the descriptor id. Matched as lowercase substrings, first hit wins.
CARD_CONDITION_TEXT = (
    ("near mint", "Like New"),
    ("mint", "Like New"),
    ("lightly played", "Good"),
    ("excellent", "Good"),
    ("moderately played", "Fair"),
    ("very good", "Fair"),
    ("heavily played", "Poor"),
    ("damaged", "Poor"),
    ("poor", "Poor"),
)

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif")

#: Keys allowed in a draft's optional "mercari" block (and as CLI flags).
OVERRIDE_KEYS = ("title", "description", "condition", "price", "brand", "category", "hashtags")


class MercariError(ValueError):
    """A Mercari draft could not be built, with a reason worth reading."""


@dataclass
class MercariDraft:
    """Everything Mercari's listing form asks for, ready to paste."""

    title: str
    description: str
    condition: str
    price: str
    photos: list[str] = field(default_factory=list)
    brand: str = ""
    category: str = ""
    #: Where each value came from, for the human checking the draft.
    source: dict[str, Any] = field(default_factory=dict)
    #: Anything that did not carry across cleanly. Empty means paste as is.
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- text -----------------------------------------------------------------


def _fold(value: str) -> str:
    """Accent- and case-insensitive form, so 'Pokémon' matches 'Pokemon'."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


_LIST_MARKER = re.compile(r"^\s*([-*•]|\d+[.)])\s+")


def reflow(text: str) -> str:
    """Join hard-wrapped lines within a paragraph; keep list items on their own.

    Drafts written by hand are wrapped at ~78 columns for the editor. Mercari
    renders every newline as a line break, so on a phone that reads as ragged
    two-word lines. Blank lines still separate paragraphs and a line that
    starts with a list marker still starts a new line.
    """
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        if not lines:
            continue
        joined = lines[0]
        for line in lines[1:]:
            joined += ("\n" if _LIST_MARKER.match(line) else " ") + line
        out.append(joined)
    return "\n\n".join(out)


def plain_text(value: str) -> str:
    """eBay description (HTML or plain) -> plain text with paragraph breaks.

    HTML keeps its explicit breaks (``<br>``, ``</p>``). Plain text with no
    tags is assumed to be hand-wrapped and is reflowed.
    """
    had_tags = bool(re.search(r"<[a-zA-Z/][^>]*>", value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|ul|ol)\s*>", "\n\n", text)
    text = re.sub(r"(?i)</\s*(li|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if had_tags else reflow(text)


def fit(value: str, limit: int) -> tuple[str, int]:
    """Cut ``value`` to ``limit`` at a sentence, line or word boundary.

    Returns the text and how many characters were dropped (0 if it fit).
    """
    if len(value) <= limit:
        return value, 0
    cut = value[:limit]
    boundary = max(cut.rfind("\n"), cut.rfind(". ") + 1)
    if boundary < limit * 0.6:
        boundary = cut.rfind(" ")
    if boundary > 0:
        cut = cut[:boundary]
    cut = cut.rstrip(" \n,;:-")
    return cut, len(value) - len(cut)


# ---- pieces ---------------------------------------------------------------


def map_condition(data: dict[str, Any], warnings: list[str]) -> tuple[str, str]:
    """eBay condition (enum, or card conditionId + descriptors) -> Mercari.

    Returns the Mercari condition and a label describing the eBay side, so
    the person checking the draft can see what it was mapped from.
    """
    condition_id = str(data.get("condition_id") or "")
    descriptors = {str(k): str(v) for k, v in (data.get("condition_descriptors") or {}).items()}
    aspects = data.get("aspects") or {}

    if condition_id == "4000":  # Ungraded card
        value = descriptors.get("40001", "")
        if value in CARD_CONDITION_VALUES:
            name, mercari = CARD_CONDITION_VALUES[value]
            return mercari, f"Ungraded, {name}"
        text_hit = _card_condition_from_text(aspects)
        if text_hit:
            return text_hit, "Ungraded (from the Card Condition item specific)"
        warnings.append(
            "ungraded card has no recognised Card Condition descriptor; "
            "condition defaulted to Good - set it yourself"
        )
        return "Good", "Ungraded, condition unknown"

    if condition_id == "2750":  # Graded card
        grade = GRADE_VALUES.get(descriptors.get("27502", ""))
        if grade is None:
            warnings.append(
                "graded card has no numeric grade descriptor; condition "
                "defaulted to Like New - Mercari has no 'graded' condition, so "
                "keep the grader and grade in the title"
            )
            return "Like New", "Graded, grade unknown"
        label = f"Graded {grade:g}"
        if grade >= 9:
            return "Like New", label
        if grade >= 7:
            return "Good", label
        if grade >= 4:
            return "Fair", label
        return "Poor", label

    condition = str(data.get("condition") or "NEW")
    mercari = EBAY_CONDITION_TO_MERCARI.get(condition)
    if mercari is None:
        warnings.append(
            f"eBay condition {condition!r} is not one this tool knows; "
            "condition defaulted to Good - set it yourself"
        )
        return "Good", condition
    if condition == "NEW_WITH_DEFECTS":
        warnings.append(
            "Mercari has no 'new with defects'; mapped to Good - say it is "
            "unused but flawed in the description"
        )
    elif "REFURBISHED" in condition:
        warnings.append(
            f"Mercari has no refurbished condition; {condition} mapped to "
            f"{mercari} - say it is refurbished in the description"
        )
    return mercari, condition


def _card_condition_from_text(aspects: dict[str, Any]) -> str:
    for name, values in aspects.items():
        if _fold(name) != "card condition":
            continue
        text = " ".join(str(v) for v in (values if isinstance(values, list) else [values]))
        folded = text.casefold()
        for needle, mercari in CARD_CONDITION_TEXT:
            if needle in folded:
                return mercari
    return ""


def detail_lines(aspects: dict[str, Any], *already: str) -> list[str]:
    """Item specifics worth repeating in the description, as ``Name: value``.

    Mercari has no item specifics, and its search is over the text of the
    listing, so specifics carry real keywords. Skipped: values already in the
    title or description (no point saying it twice), and plain "No" answers.
    """
    haystack = _fold(" ".join(already))
    lines = []
    for name, values in aspects.items():
        if not isinstance(values, list):
            values = [values]
        values = [str(v).strip() for v in values if str(v).strip()]
        values = [v for v in values if v.casefold() not in ("no", "n/a", "none")]
        if not values:
            continue
        if all(_fold(v) in haystack for v in values):
            continue
        lines.append(f"{name}: {', '.join(values)}")
    return lines


def build_description(
    data: dict[str, Any], overrides: dict[str, Any], title: str, warnings: list[str]
) -> str:
    """Assemble the description inside the 1000-character budget.

    Priority order: the eBay description itself, then the condition note,
    then item specifics not already mentioned, then hashtags. Whatever does
    not fit is reported as a warning rather than silently dropped.
    """
    if overrides.get("description"):
        text, dropped = fit(plain_text(str(overrides["description"])), MAX_DESCRIPTION)
        if dropped:
            warnings.append(f"description override cut by {dropped} characters to fit {MAX_DESCRIPTION}")
        return text

    full = plain_text(str(data.get("description") or ""))
    if not full:
        warnings.append("no description in the draft; Mercari requires one")
    hashtags = [
        "#" + str(tag).lstrip("#").replace(" ", "")
        for tag in (overrides.get("hashtags") or [])
        if str(tag).strip("# ")
    ]
    hashtag_line = " ".join(hashtags)
    # Hashtags were asked for explicitly, so they get their room up front
    # rather than losing out to the tail of a long description.
    budget = MAX_DESCRIPTION - (len(hashtag_line) + 2 if hashtag_line else 0)
    budget = max(budget, MAX_DESCRIPTION // 2)  # hashtags never get more than half
    body, dropped = fit(full, budget)

    blocks: list[tuple[str, list[str]]] = []
    condition_note = str(data.get("condition_description") or "").strip()
    if condition_note and not _has_condition(body, condition_note):
        blocks.append(("condition note", [f"Condition: {condition_note}"]))
    details = detail_lines(data.get("aspects") or {}, title, body, condition_note)
    if details:
        blocks.append(("details", details))
    if hashtag_line:
        blocks.append(("hashtags", [hashtag_line]))

    if dropped:
        preview = full[len(body):].lstrip(" \n,;:-.")[:60]
        warning = (
            f"description cut by {dropped} characters to fit Mercari's {MAX_DESCRIPTION}, "
            f"from: {preview!r}... - to choose what stays, put a shorter one in the "
            'draft under "mercari": {"description": "..."}'
        )
        skipped = [
            f"{label} ({'; '.join(lines)})" for label, lines in blocks if label != "hashtags"
        ]
        if skipped:
            warning += ". Also left out: " + "; ".join(skipped)
        warnings.append(warning)
        blocks = [(label, lines) for label, lines in blocks if label == "hashtags"]

    text = body
    for label, lines in blocks:
        kept: list[str] = []
        left_out: list[str] = []
        for line in lines:
            joiner = "\n\n" if not kept else "\n"
            candidate = (text if text else "") + (joiner if text else "") + line
            if len(candidate) <= MAX_DESCRIPTION:
                text = candidate
                kept.append(line)
            else:
                left_out.append(line)
        if left_out:
            warnings.append(
                f"no room in the description for {label}: " + "; ".join(left_out)
            )
    return text


def _has_condition(body: str, note: str) -> bool:
    """Does the description already cover the condition note?

    Either the note's text is in there, or the description has its own
    "Condition:" paragraph (the drafts in this repo always do), in which case
    a second one just says the same thing twice.
    """
    if _fold(note) in _fold(body):
        return True
    return re.search(r"(?im)^condition\b", body) is not None


def photos_for(draft_file: str | Path, directory: str | Path | None = None) -> list[str]:
    """Photos for a draft, in filename order.

    With ``directory`` given, that folder is used and must exist. Otherwise
    the repo convention is tried: ``drafts/NAME.json`` pairs with
    ``photos/NAME/`` (next to the drafts folder, or under the current
    directory). No match means no photos, which the draft then warns about.
    """
    if directory is not None:
        folder = Path(directory)
        if not folder.is_dir():
            raise MercariError(f"no such photo folder: {directory}")
        return _images_in(folder)
    name = Path(draft_file).stem
    parent = Path(draft_file).resolve().parent
    for candidate in (parent.parent / "photos" / name, Path("photos") / name):
        if candidate.is_dir():
            return _images_in(candidate)
    return []


def _images_in(folder: Path) -> list[str]:
    files = sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES
    )
    try:
        return [str(p.relative_to(Path.cwd())) for p in files]
    except ValueError:
        return [str(p) for p in files]


# ---- the draft ------------------------------------------------------------


def from_listing(
    data: dict[str, Any],
    *,
    photos: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> MercariDraft:
    """Build a Mercari draft from ``create --from-file`` JSON fields.

    ``data`` may carry its own ``"mercari"`` block of overrides; ``overrides``
    (from CLI flags) win over that. Either may set any of OVERRIDE_KEYS.
    """
    merged: dict[str, Any] = dict(data.get("mercari") or {})
    merged.update({k: v for k, v in (overrides or {}).items() if v not in (None, "", [])})
    unknown = set(merged) - set(OVERRIDE_KEYS)
    if unknown:
        raise MercariError(
            f"unknown mercari field(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(OVERRIDE_KEYS)})"
        )
    warnings: list[str] = []
    source: dict[str, Any] = {"sku": str(data.get("sku") or "")}

    title = " ".join(str(merged.get("title") or data.get("title") or "").split())
    if not title:
        raise MercariError("the draft has no title")
    title, dropped = fit(title, MAX_TITLE)
    if dropped:
        warnings.append(f"title cut by {dropped} characters to fit Mercari's {MAX_TITLE}")

    description = build_description(data, merged, title, warnings)

    if merged.get("condition"):
        condition = normalise_condition(str(merged["condition"]))
        ebay_label = "set by hand"
    else:
        condition, ebay_label = map_condition(data, warnings)
    source["ebay_condition"] = ebay_label

    price = str(merged.get("price") or data.get("price") or "").strip()
    try:
        amount = float(price)
    except ValueError:
        raise MercariError(f"price {price!r} is not a number") from None
    price = f"{amount:.2f}"
    if amount < MIN_PRICE:
        warnings.append(f"Mercari's minimum price is ${MIN_PRICE:.0f}; {price} is below it")
    elif amount > MAX_PRICE:
        warnings.append(
            f"Mercari's price limit is ${MAX_PRICE:,.0f} ($5,000 for Authenticate items "
            f"after extra ID verification); {price} is above it"
        )

    aspects = data.get("aspects") or {}
    brand = str(merged.get("brand") or "").strip()
    source["brand_from"] = "mercari block or flag" if brand else ""
    if not brand:
        for wanted in ("brand", "manufacturer"):
            for name, values in aspects.items():
                if _fold(name) == wanted and values:
                    brand = str(values[0] if isinstance(values, list) else values).strip()
                    source["brand_from"] = f"{name} item specific"
                    break
            if brand:
                break
    category = str(merged.get("category") or "").strip()
    source["ebay_category_id"] = str(data.get("category_id") or "")
    if not category:
        warnings.append(
            "Mercari's categories are its own; pick one in the app"
            + (f" (eBay category was {source['ebay_category_id']})" if source["ebay_category_id"] else "")
        )

    chosen = list(photos or [])
    if len(chosen) > MAX_PHOTOS:
        warnings.append(
            f"Mercari takes {MAX_PHOTOS} photos; the last {len(chosen) - MAX_PHOTOS} "
            "were left off: " + ", ".join(chosen[MAX_PHOTOS:])
        )
        chosen = chosen[:MAX_PHOTOS]
    if not chosen:
        warnings.append("no photos found; pass --photo or --photos-dir")

    return MercariDraft(
        title=title,
        description=description,
        condition=condition,
        price=price,
        photos=chosen,
        brand=brand,
        category=category,
        source=source,
        warnings=warnings,
    )


def normalise_condition(value: str) -> str:
    wanted = value.strip().casefold().replace("_", " ")
    for condition in CONDITIONS:
        if condition.casefold() == wanted:
            return condition
    raise MercariError(f"condition {value!r} is not one of: {', '.join(CONDITIONS)}")


def render(draft: MercariDraft, source: str = "") -> str:
    """The paste-ready text, one labelled block per form field."""
    lines = []
    heading = "Mercari listing"
    if source:
        heading += f" from {source}"
    if draft.source.get("sku"):
        heading += f" (SKU {draft.source['sku']})"
    lines += [heading, ""]
    lines += [f"TITLE ({len(draft.title)}/{MAX_TITLE} characters)", draft.title, ""]
    lines += [f"DESCRIPTION ({len(draft.description)}/{MAX_DESCRIPTION} characters)", draft.description, ""]
    lines.append(f"CATEGORY   {draft.category or 'pick in the app'}")
    brand_note = ""
    if draft.brand and "specific" in draft.source.get("brand_from", ""):
        brand_note = f"   (from the {draft.source['brand_from']}; pick the closest brand the app offers)"
    lines.append(f"BRAND      {draft.brand or '-'}{brand_note}")
    lines.append(f"CONDITION  {draft.condition}   (eBay: {draft.source.get('ebay_condition', '?')})")
    lines.append(f"PRICE      ${draft.price}")
    lines.append("")
    lines.append(f"PHOTOS ({len(draft.photos)} of {MAX_PHOTOS} max, in this order)")
    lines += [f"  {i}. {path}" for i, path in enumerate(draft.photos, 1)] or ["  (none)"]
    if draft.warnings:
        lines += ["", "CHECK BEFORE POSTING"]
        lines += [f"  - {warning}" for warning in draft.warnings]
    return "\n".join(lines)
