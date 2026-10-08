"""Readable names for published PNGs; cache keys and embedded records stay intact."""

import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import unicodedata
import zlib

from PIL import Image
from marblescape_image_metadata import PNG_SIGNATURE, MAX_METADATA_BYTES


def _slug(value, limit, fallback):
    if not isinstance(value, str) or not value.strip():
        value = fallback
    value = unicodedata.normalize("NFC", value)
    value = "".join(character if character.isalnum() or character in ".-" else "-" for character in value)
    value = re.sub(r"-+", "-", value).strip(".-") or fallback
    # Windows counts UTF-16 code units, not Python Unicode code points.
    result, units = [], 0
    for character in value:
        size = 2 if ord(character) > 0xffff else 1
        if units + size > limit:
            break
        result.append(character)
        units += size
    return "".join(result).rstrip(".-") or fallback


def _utc_token(value):
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return None
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        # These metadata fields explicitly represent UTC even in older records.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    except (ValueError, OverflowError):
        return None


def descriptive_image_filename(record, image_hash):
    """Return a bounded basename: creation time, profile or location, and image hash.

    Source, product, acquisition time and size stay in the embedded record only;
    MarbleScape itself never reads them back from the filename.
    """
    if not isinstance(record, dict) or record.get("software") != "MarbleScape":
        return None
    if not isinstance(image_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", image_hash):
        raise ValueError("Image filenames require a SHA-256 digest.")
    width, height = record.get("width"), record.get("height")
    if any(type(value) is not int or not 0 < value <= 100000 for value in (width, height)):
        return None
    settings = record.get("profile_settings", {})
    settings = settings if isinstance(settings, dict) else {}
    source_settings = settings.get("source", {})
    source_settings = source_settings if isinstance(source_settings, dict) else {}
    provider = source_settings.get("provider", "")
    sources = settings.get("sources", {})
    profile = sources.get(provider, {}) if isinstance(sources, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    view = settings.get("view", {})
    view = view if isinstance(view, dict) else {}
    place = record.get("profile_name")
    if not place:
        place = profile.get("highlight")
        if not place and all(type(record.get(key)) in (int, float) for key in ("latitude", "longitude")):
            place = f"lat{record['latitude']:.4f}-lon{record['longitude']:.4f}"
        if not place:
            place = ((record.get("area") or record.get("sector")) if provider != "worldview" else None)
        place = place or view.get("preset") or "Custom"
    created = _utc_token(record.get("generated_at_utc")) or "created-unknown"
    return "_".join(("MarbleScape", created, _slug(place, 32, "Custom"), image_hash[:12])) + ".png"


def has_no_image_data(path):
    """True when the PNG's own record says it holds no image data (0% data coverage).

    Such a picture (saved before MarbleScape refused them) is never reused from
    the cache or the Latest folder. Pictures without a record, or with coverage
    unavailable, count as usable.
    """
    try:
        with Path(path).open("rb") as handle:
            record = _read_record(handle)
    except (OSError, ValueError, struct.error, RecursionError):
        return False
    return isinstance(record, dict) and record.get("data_coverage_percent") == 0


def _read_record(handle):
    """Read bounded provenance chunks without decoding all PNG pixels."""
    if handle.read(8) != PNG_SIGNATURE:
        return None
    total = handle.seek(0, 2)
    handle.seek(8)
    records = []
    dimensions = None
    while handle.tell() + 12 <= total:
        size, kind = struct.unpack(">I4s", handle.read(8))
        if handle.tell() + size + 4 > total:
            return None
        if kind in {b"iTXt", b"eXIf", b"IHDR"} and size <= MAX_METADATA_BYTES * 8:
            payload = handle.read(size)
            crc = struct.unpack(">I", handle.read(4))[0]
            if zlib.crc32(kind + payload) & 0xffffffff != crc:
                return None
            text = None
            if kind == b"IHDR" and size == 13:
                dimensions = struct.unpack(">II", payload[:8])
            elif kind == b"iTXt" and payload.startswith(b"MarbleScape\0\0\0\0\0"):
                text = payload[len(b"MarbleScape\0\0\0\0\0"):].decode("utf-8")
            elif kind == b"eXIf":
                exif = Image.Exif()
                exif.load(b"Exif\0\0" + payload)
                if exif.get(305) == "MarbleScape":
                    text = exif.get(270)
            if isinstance(text, str):
                record = json.loads(text)
                if isinstance(record, dict) and record.get("software") == "MarbleScape":
                    records.append(record)
        else:
            handle.seek(size + 4, 1)
        if kind == b"IEND":
            if size != 0 or handle.tell() != total or not records or dimensions is None:
                return None
            if any(record != records[0] for record in records[1:]):
                return None
            if (records[0].get("width"), records[0].get("height")) != dimensions:
                return None
            return records[0]
    return None


def image_filename_from_png(source, image_hash=None):
    """Return None for legacy/invalid metadata; never rewrite pixels or EXIF."""
    try:
        with (io.BytesIO(source) if isinstance(source, bytes) else Path(source).open("rb")) as handle:
            record = _read_record(handle)
            if record is None:
                return None
            if image_hash is None:
                handle.seek(0)
                image_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            return descriptive_image_filename(record, image_hash)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, struct.error):
        return None


def unused_image_path(folder, filename):
    """Disambiguate with a suffix without inventing a later creation time."""
    folder = Path(folder)
    if Path(filename).name != filename:
        raise ValueError("Expected an image basename, not a path.")
    candidate = folder / filename
    counter = 0
    while candidate.exists() or candidate.is_symlink():
        counter += 1
        candidate = folder / f"{Path(filename).stem}_{counter}.png"
    return candidate
