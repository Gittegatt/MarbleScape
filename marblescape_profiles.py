"""Validated MarbleScape image-profile TOML documents.

No function in this module reads or writes files or changes wallpaper settings.
``replace_library`` and ``remove_library`` preserve unrelated TOML text.

RotationScheduler's caller owns applying/rendering an image and its retry delay:
* When ``due()`` is true and no profile is pending, call ``start_next()``.
* Apply its returned snapshot, render, and install before calling ``success()``.
* On any failure call ``failure()``. ``retry`` keeps the same pending snapshot;
  retry it directly without calling start_next. ``skip`` and ``wait`` release it.
* ``configure()`` returns True when a meaningful change resets the schedule; the
  caller must then discard a pending profile. Name-only changes return False.

``current`` / ``current_id`` identify the most recently started profile.
``active_profile`` is an independent snapshot while a profile is in flight.
``attempts`` counts failures of that profile; a third failure exhausts it.
``deadline`` uses the supplied monotonic clock (None while disabled/empty).
"""

from __future__ import annotations

import json
import math
import re
import time
import tomllib
import uuid
from copy import deepcopy
from typing import Callable

MAX_INPUT_BYTES = 1_000_000
MAX_PROFILES = 100
MAX_NAME_LENGTH = 80
MAX_INTERVAL = 525_600
MAX_TREE_DEPTH = 32
MAX_TREE_NODES = 100_000
_ALLOWED_SECTIONS = frozenset({"source", "sources", "view", "output", "layers", "service"})
_UNIT_SECONDS = {"minutes": 60, "days": 86_400, "weeks": 604_800}
_ID_PATTERN = re.compile(r"[0-9a-fA-F]{32}\Z")


def new_profile_id() -> str:
    """Return a stable identifier suitable for a new library item."""
    return uuid.uuid4().hex


def _text_size(value: str, label: str) -> None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"{label} contains invalid Unicode.") from exc
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"{label} exceeds {MAX_INPUT_BYTES} bytes.")


def _table(value, label: str, allowed: set | frozenset) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a table.")
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise ValueError(f"{label} contains unsupported keys.")
    return value


def _identifier(value, label: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a 32-character hexadecimal UUID.")
    return value.lower()


def _copy_value(value, budget: list[int], depth: int = 0):
    """Copy bounded TOML values without sharing mutable caller data."""
    budget[0] += 1
    if budget[0] > MAX_TREE_NODES or depth > MAX_TREE_DEPTH:
        raise ValueError("Profile settings are too deeply nested or too large.")
    if isinstance(value, str):
        _text_size(value, "Profile string")
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("Profile integers must fit signed 64-bit TOML values.")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Profile numbers must be finite.")
        return value
    if isinstance(value, list):
        return [_copy_value(item, budget, depth + 1) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("Profile setting keys must be nonempty strings.")
            _text_size(key, "Profile setting key")
            result[key] = _copy_value(item, budget, depth + 1)
        return result
    raise ValueError("Profile settings support only tables, lists, strings, booleans and finite numbers.")


def _inline(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_inline(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_inline(key)} = {_inline(item)}" for key, item in value.items()
        ) + " }"
    raise ValueError("Unsupported TOML value.")


def toml_value(value) -> str:
    """Serialize a bounded TOML value inline, rejecting None and nonfinite data.

    This helper accepts the same nested tables/lists/scalars as profile settings
    and is suitable for writing a complete layers list without filesystem I/O.
    """
    copied = _copy_value(value, [0])
    result = _inline(copied)
    _text_size(result, "TOML value")
    return result


def normalize_library(value) -> dict:
    """Validate the library and return a fully defaulted independent copy.

    At most 100 profiles are accepted. IDs and case-folded names are unique.
    Profile settings are tables drawn from source/sources/view/output/layers/
    service; layers is a list of tables. An explicit rotation order may select
    a subset of IDs, including no IDs; omitted order includes every item.
    """
    value = _table(value, "image_profiles", {"version", "items", "rotation"})
    version = value.get("version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("Unsupported image profile library version.")
    items = value.get("items", [])
    if not isinstance(items, list) or len(items) > MAX_PROFILES:
        raise ValueError(f"Profile items must be a list of at most {MAX_PROFILES} entries.")
    normalized_items = []
    seen_ids, seen_names = set(), set()
    budget = [0]
    for item in items:
        item = _table(item, "Profile", {"id", "name", "settings"})
        if set(item) != {"id", "name", "settings"}:
            raise ValueError("Each profile requires id, name and settings.")
        identifier = _identifier(item["id"], "Profile ID")
        name = item["name"]
        if not isinstance(name, str):
            raise ValueError("Profile names must be text.")
        name = name.strip()
        if not 1 <= len(name) <= MAX_NAME_LENGTH or any(ord(char) < 32 for char in name):
            raise ValueError(f"Profile names must contain 1 to {MAX_NAME_LENGTH} printable characters.")
        _text_size(name, "Profile name")
        if identifier in seen_ids or name.casefold() in seen_names:
            raise ValueError("Profile IDs and names must be unique.")
        seen_ids.add(identifier)
        seen_names.add(name.casefold())
        settings = _table(item["settings"], "Profile settings", _ALLOWED_SECTIONS)
        for section, section_value in settings.items():
            if section == "layers":
                if not isinstance(section_value, list) or any(not isinstance(layer, dict) for layer in section_value):
                    raise ValueError("Profile layers must be a list of tables.")
            elif not isinstance(section_value, dict):
                raise ValueError(f"Profile {section} settings must be a table.")
        normalized_items.append({"id": identifier, "name": name,
                                 "settings": _copy_value(settings, budget)})
    rotation = _table(value.get("rotation", {}), "Profile rotation",
                      {"enabled", "interval", "unit", "order"})
    enabled = rotation.get("enabled", False)
    interval = rotation.get("interval", 15)
    unit = rotation.get("unit", "minutes")
    if type(enabled) is not bool:
        raise ValueError("Rotation enabled must be a boolean.")
    if type(interval) is not int or not 1 <= interval <= MAX_INTERVAL:
        raise ValueError(f"Rotation interval must be an integer from 1 to {MAX_INTERVAL}.")
    if not isinstance(unit, str) or unit not in _UNIT_SECONDS:
        raise ValueError("Rotation unit must be minutes, days or weeks.")
    order = rotation.get("order", [item["id"] for item in normalized_items])
    if not isinstance(order, list) or len(order) > MAX_PROFILES:
        raise ValueError("Rotation order must be a list of profile IDs.")
    normalized_order = [_identifier(identifier, "Rotation ID") for identifier in order]
    if len(normalized_order) != len(set(normalized_order)) or not set(normalized_order) <= seen_ids:
        raise ValueError("Rotation order must contain unique IDs from the profile library.")
    result = {"version": 1, "items": normalized_items,
              "rotation": {"enabled": enabled, "interval": interval,
                           "unit": unit, "order": normalized_order}}
    _text_size(_inline(result), "Profile library")
    return result


def read_library(config: dict) -> dict:
    """Read image_profiles from an already parsed configuration."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a table.")
    return normalize_library(config.get("image_profiles", {}))


def _header_path(line: str) -> tuple[str, ...]:
    marker = "__marblescape_header_probe__"
    node = tomllib.loads(line + "\n" + marker + " = 1\n")
    path = []
    while not (isinstance(node, dict) and node == {marker: 1}):
        if isinstance(node, list):
            node = node[0]
        else:
            key, node = next(iter(node.items()))
            path.append(key)
    return tuple(path)


def _headers(text: str) -> list[tuple[int, tuple[str, ...]]]:
    """Find actual table headers, ignoring brackets in strings and arrays."""
    result = []
    quote = None
    depth = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if quote is None and depth == 0 and line.lstrip(" \t").startswith("["):
            result.append((offset, _header_path(line.rstrip("\r\n"))))
            offset += len(line)
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if quote is not None:
                if quote in ('"', '"""') and char == "\\":
                    index += 2
                    continue
                if line.startswith(quote, index):
                    if len(quote) == 3:
                        # TOML permits one or two content quotes immediately
                        # before a multiline string's closing triple quote.
                        end = index + 3
                        while end < len(line) and line[end] == quote[0]:
                            end += 1
                        index = end
                    else:
                        index += 1
                    quote = None
                    continue
            else:
                if char == "#":
                    break
                if char in ('"', "'"):
                    quote = char * 3 if line.startswith(char * 3, index) else char
                    index += len(quote)
                    continue
                if char in "[{":
                    depth += 1
                elif char in "]}":
                    depth -= 1
            index += 1
        offset += len(line)
    return result


def table_spans(text, table_name):
    """Return complete root/child table ranges, ignoring headers inside strings."""
    headers = _headers(text)
    return [(offset, headers[index + 1][0] if index + 1 < len(headers) else len(text))
            for index, (offset, path) in enumerate(headers)
            if path and path[0] == table_name]


def replace_library(text: str, library: dict) -> str:
    """Replace only image_profiles tables, preserving all other text verbatim.

    Generated settings and rotation use inline TOML tables. Existing nested
    image_profiles tables are supported; an existing top-level inline/dotted
    image_profiles assignment is rejected instead of silently changing it.
    Both input and output configuration text are limited to one megabyte.
    """
    if not isinstance(text, str):
        raise ValueError("Configuration text must be a string.")
    _text_size(text, "Configuration")
    try:
        original = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid configuration TOML: {exc}") from exc
    library = normalize_library(library)
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = ["[image_profiles]", "version = 1", "items = ["]
    rows.extend("    " + _inline(item) + "," for item in library["items"])
    rows.extend([
        "]", "rotation = " + _inline(library["rotation"]), "",
    ])
    block = newline.join(rows)
    headers = _headers(text)
    ranges = [(offset, headers[index + 1][0] if index + 1 < len(headers) else len(text))
              for index, (offset, path) in enumerate(headers)
              if path and path[0] == "image_profiles"]
    if ranges:
        parts = []
        cursor = 0
        for index, (start, end) in enumerate(ranges):
            parts.append(text[cursor:start])
            if index == 0:
                parts.append(block)
                if end < len(text):
                    parts.append(newline)
            cursor = end
        parts.append(text[cursor:])
        updated = "".join(parts)
    else:
        if "image_profiles" in original:
            raise ValueError("image_profiles must be stored as a TOML table header.")
        separator = "" if not text else (newline if text.endswith(("\n", "\r")) else newline * 2)
        updated = text + separator + block
    _text_size(updated, "Configuration")
    try:
        parsed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Unable to serialize profile TOML: {exc}") from exc
    if read_library(parsed) != library:
        raise ValueError("Serialized profile library did not round-trip correctly.")
    if {key: value for key, value in parsed.items() if key != "image_profiles"} != {
        key: value for key, value in original.items() if key != "image_profiles"
    }:
        raise ValueError("Replacing profiles would change unrelated configuration.")
    return updated


def serialize_library(library: dict, newline: str = "\n") -> str:
    """Return a complete standalone profiles.toml document."""
    if newline not in {"\n", "\r\n"}:
        raise ValueError("Profile TOML newline must be LF or CRLF.")
    text = replace_library("", library)
    if newline != "\n":
        text = text.replace("\n", newline)
    return text


def remove_library(text: str) -> str:
    """Remove legacy image_profiles tables without changing other settings."""
    if not isinstance(text, str):
        raise ValueError("Configuration text must be a string.")
    _text_size(text, "Configuration")
    try:
        original = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid configuration TOML: {exc}") from exc
    if "image_profiles" not in original:
        return text
    headers = _headers(text)
    ranges = [
        (offset, headers[index + 1][0] if index + 1 < len(headers) else len(text))
        for index, (offset, path) in enumerate(headers)
        if path and path[0] == "image_profiles"
    ]
    if not ranges:
        raise ValueError("image_profiles must be stored as a TOML table header.")
    parts = []
    cursor = 0
    for start, end in ranges:
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    updated = "".join(parts)
    try:
        parsed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Unable to remove legacy profile TOML: {exc}") from exc
    if "image_profiles" in parsed:
        raise ValueError("Unable to remove legacy image_profiles tables.")
    if parsed != {key: value for key, value in original.items() if key != "image_profiles"}:
        raise ValueError("Removing profiles would change unrelated configuration.")
    return updated


class RotationScheduler:
    """Pure scheduler; applying snapshots, retries and image I/O belong to caller."""

    def __init__(self, library: dict, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._fingerprint = None
        self._library = {}
        self.current_id = None
        self.attempts = 0
        self.deadline = None
        self._in_flight = False
        self._next_index = 0
        self._failed_ids = set()
        self.configure(library)

    @property
    def current(self):
        return self.current_id

    @property
    def library(self):
        return deepcopy(self._library)

    @property
    def active_profile(self):
        if not self._in_flight:
            return None
        return deepcopy(self._items[self.current_id])

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value):
            raise ValueError("Rotation clock must return a finite monotonic value.")
        return value

    def configure(self, library: dict) -> bool:
        """Return True when changes reset rotation; cosmetic edits retain timing."""
        normalized = normalize_library(library)
        items = {item["id"]: item for item in normalized["items"]}
        signature = json.dumps({
            "rotation": normalized["rotation"],
            "settings": {identifier: item["settings"] for identifier, item in items.items()},
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        changed = signature != self._fingerprint
        now = self._now() if changed else None
        self._library, self._items = normalized, items
        self._fingerprint = signature
        rotation = normalized["rotation"]
        self._enabled = rotation["enabled"]
        self._order = rotation["order"]
        self._interval = rotation["interval"] * _UNIT_SECONDS[rotation["unit"]]
        if changed:
            self.current_id = None
            self.attempts = 0
            self._in_flight = False
            self._next_index = 0
            self._failed_ids.clear()
            self.deadline = now if self._enabled and self._order else None
        return changed

    def due(self) -> bool:
        return (self._enabled and bool(self._order) and not self._in_flight
                and self.deadline is not None and self._now() >= self.deadline)

    def start_next(self) -> dict | None:
        """Start the next due profile; return None before its deadline/in flight."""
        if not self.due():
            return None
        self.current_id = self._order[self._next_index]
        self.attempts = 0
        self._in_flight = True
        return self.active_profile

    def _require_active(self):
        if not self._in_flight:
            raise RuntimeError("No rotation profile is awaiting a result.")

    def success(self) -> None:
        """A successfully installed image starts its full rotation interval."""
        self._require_active()
        now = self._now()
        self._in_flight = False
        self._failed_ids.clear()
        self.attempts = 0
        self._next_index = (self._next_index + 1) % len(self._order)
        self.deadline = now + self._interval

    def cancel(self) -> None:
        """Defer a user-cancelled profile without recording a failed attempt."""
        self._require_active()
        now = self._now()
        self._in_flight = False
        self.attempts = 0
        self.deadline = now + self._interval

    def failure(self) -> str:
        """Return retry twice, then skip; pause if every profile exhausted retries."""
        self._require_active()
        now = self._now()
        self.attempts += 1
        if self.attempts < 3:
            return "retry"
        self._in_flight = False
        self._failed_ids.add(self.current_id)
        self._next_index = (self._next_index + 1) % len(self._order)
        if self._failed_ids >= set(self._order):
            self._failed_ids.clear()
            self.deadline = now + self._interval
            return "wait"
        self.deadline = now
        return "skip"
