"""Embed a small, explicit MarbleScape provenance record in PNG images."""

from __future__ import annotations

import json
from copy import deepcopy
import struct
import zlib

from PIL import Image


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEYWORD = b"MarbleScape"
SCHEMA_VERSION = 4
MAX_METADATA_BYTES = 16_384
SOURCE_LABELS = {
    "eumetsat": "EUMETSAT", "goes_east": "GOES-East",
    "goes_west": "GOES-West", "solar": "Solar / Sun (SUVI)",
    "himawari": "Himawari", "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser", "worldview": "NASA Worldview",
}


def wallpaper_metadata(original, size, position):
    """Preserve the source profile while explicitly describing a display derivative."""
    record = deepcopy(original)
    record["image_role"] = "wallpaper_derivative"
    record["profile_image_size"] = original.get("profile_image_size", [original["width"], original["height"]])
    record["width"], record["height"] = map(int, size)
    record["wallpaper_position"] = position
    record.pop("rendered_image_sha256", None)  # That digest belongs to the source PNG.
    return record


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def embed_png_metadata(image_data: bytes, metadata: dict) -> bytes:
    """Add matching PNG text and EXIF provenance without changing image pixels."""
    if not isinstance(image_data, bytes) or not image_data.startswith(PNG_SIGNATURE):
        raise ValueError("MarbleScape image metadata requires a PNG image.")
    encoded = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError("MarbleScape image metadata is too large.")
    # PNG iTXt: keyword, NUL, compression flag/method, language NUL,
    # translated keyword NUL, and uncompressed UTF-8 text.
    payload = KEYWORD + b"\0\0\0\0\0" + encoded
    text_chunk = _png_chunk(b"iTXt", payload)
    exif = Image.Exif()
    # EXIF ImageDescription is ASCII: escape Unicode losslessly (e.g. profile names).
    exif[270] = json.dumps(metadata, ensure_ascii=True, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    exif[305] = "MarbleScape"
    exif_bytes = exif.tobytes()
    if not exif_bytes.startswith(b"Exif\0\0"):
        raise ValueError("Unable to create PNG EXIF metadata.")
    exif_chunk = _png_chunk(b"eXIf", exif_bytes[6:])
    offset = len(PNG_SIGNATURE)
    kept = [PNG_SIGNATURE]
    while offset + 12 <= len(image_data):
        size = struct.unpack_from(">I", image_data, offset)[0]
        end = offset + 12 + size
        if end > len(image_data):
            break
        kind = image_data[offset + 4:offset + 8]
        chunk = image_data[offset:end]
        if kind == b"iTXt" and chunk[8:8 + len(KEYWORD) + 1] == KEYWORD + b"\0":
            pass  # Replace an existing MarbleScape record, if present.
        elif kind == b"eXIf":
            pass  # Replace prior EXIF with the public MarbleScape record.
        elif kind == b"IEND":
            if size != 0 or end != len(image_data):
                break
            if struct.unpack(">I", chunk[-4:])[0] != zlib.crc32(b"IEND") & 0xffffffff:
                raise ValueError("Invalid PNG IEND checksum.")
            kept.extend((text_chunk, exif_chunk, chunk))
            return b"".join(kept)
        else:
            kept.append(chunk)
        offset = end
    raise ValueError("The PNG image has no valid final IEND chunk.")
