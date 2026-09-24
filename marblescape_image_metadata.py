"""Embed a small, explicit MarbleScape provenance record in PNG images."""

from __future__ import annotations

import json
import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEYWORD = b"MarbleScape"
SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 16_384


def embed_png_metadata(image_data: bytes, metadata: dict) -> bytes:
    """Add one UTF-8 iTXt record without recompressing or changing image pixels."""
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
    chunk_type = b"iTXt"
    new_chunk = (
        struct.pack(">I", len(payload)) + chunk_type + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xffffffff)
    )
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
        elif kind == b"IEND":
            if size != 0 or end != len(image_data):
                break
            kept.extend((new_chunk, chunk))
            return b"".join(kept)
        else:
            kept.append(chunk)
        offset = end
    raise ValueError("The PNG image has no valid final IEND chunk.")
