"""Bounded, portable profile JSON and PNG provenance import/export."""

from copy import deepcopy
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import uuid
import tempfile
import zlib

from PIL import Image

from marblescape_profiles import MAX_INPUT_BYTES, MAX_PROFILES, MAX_NAME_LENGTH, normalize_library, new_profile_id
from marblescape_image_metadata import PNG_SIGNATURE, MAX_METADATA_BYTES, SCHEMA_VERSION, SOURCE_LABELS
from marblescape_copernicus import LEGACY_BLACK_COVERAGE_MODE, get_product, get_layer


FORMAT = "MarbleScape profiles"
VIEW_KEYS = ("projection", "preset", "bbox", "fit_mode", "zoom", "truecolor_black_night")
# Output size, background and the Latest folder are device settings, never exported.
OUTPUT_KEYS = ("render_scale",)
LAYER_KEYS = ("kind", "name", "enabled", "opacity", "style", "time")


def portable_settings(settings, validate):
    """Keep rendering choices; never export auth, paths, or unrelated source selections."""
    bounded = normalize_library({"items": [{"id": new_profile_id(), "name": "Profile",
                                           "settings": settings}]})["items"][0]["settings"]
    value = validate(bounded)
    provider = value["source"]["provider"]
    result = {
        "source": {"provider": provider, "check_for_updates": value["source"].get("check_for_updates", True)},
        "sources": {provider: deepcopy(value["sources"][provider])},
        "view": {key: deepcopy(value["view"][key]) for key in VIEW_KEYS},
        "output": {key: deepcopy(value["output"][key]) for key in OUTPUT_KEYS},
        "layers": [{key: deepcopy(layer[key]) for key in LAYER_KEYS if key in layer}
                   for layer in value["layers"]] if provider == "eumetsat" else [],
    }
    validate(result)
    return result


def strict_settings(settings, validate):
    """Reject missing fields or normalization that would silently repair input."""
    canonical = portable_settings(settings, validate)
    issues = []

    def check(raw, expected, path):
        if isinstance(expected, dict):
            if not isinstance(raw, dict):
                issues.append(f"{path} must be an object.")
                return
            for key, value in expected.items():
                if key not in raw:
                    # Added later: old profiles have the documented black default.
                    introduced = {"no_data_color": "#FFFFFF", "map_label_color": "#000000",
                                  "map_labels": True,
                                  "contrast": 100, "auto_brightness": False,
                                  "auto_contrast": False, "image_size": "auto",
                                  "auto_recommendation": False, "auto_priority": "fewest_clouds",
                                  "auto_precise": False}
                    # Country borders followed the labels' switch and color.
                    introduced.update(
                        map_borders=raw.get("map_labels", introduced["map_labels"]),
                        map_border_color=raw.get("map_label_color", introduced["map_label_color"]))
                    # Regular layers showed the map background, or black with Gap fill "black".
                    introduced["scene_no_data_color"] = (
                        "#000000" if raw.get("coverage_mode") == LEGACY_BLACK_COVERAGE_MODE else "transparent")
                    if path == "settings.sources.copernicus" and key in introduced and value == introduced[key]:
                        continue
                    issues.append(f"Incomplete profile: missing {path}.{key}.")
                    continue
                check(raw[key], value, f"{path}.{key}")
        elif isinstance(expected, list):
            if not isinstance(raw, list) or len(raw) != len(expected):
                issues.append(f"Invalid {path}.")
                return
            for index, value in enumerate(expected):
                check(raw[index], value, f"{path}[{index}]")
        else:
            same_type = type(raw) is type(expected) or (
                type(raw) in (int, float) and type(expected) in (int, float))
            # Gap fill "black" is now Single latest acquisition with a black No-data color.
            legacy_black = (path == "settings.sources.copernicus.coverage_mode"
                            and raw == LEGACY_BLACK_COVERAGE_MODE and expected == "single")
            if (not same_type or raw != expected) and not legacy_black:
                issues.append(f"Invalid or inconsistent value at {path}.")
    check(settings, canonical, "settings")
    if issues:
        raise ValueError("\n".join(issues))
    return canonical


class ImportCancelled(Exception):
    """The user declined the batch; no entries should be committed."""


def complete_legacy_period_fields(settings):
    """Only infer fields whose pre-period-selection semantics are unambiguous."""
    candidate = deepcopy(settings)
    changes = []
    if isinstance(candidate.get("source"), dict) and "check_for_updates" not in candidate["source"]:
        candidate["source"]["check_for_updates"] = True
        changes.append("settings.source.check_for_updates = True (default; confirm before importing)")
    if candidate.get("source", {}).get("provider") != "copernicus":
        return candidate, changes
    profile = candidate.get("sources", {}).get("copernicus")
    if not isinstance(profile, dict):
        return candidate, changes
    if "brightness" not in profile:
        profile["brightness"] = 100
        changes.append("settings.sources.copernicus.brightness = 100 (neutral default)")
    if "date_mode" not in profile:
        if any(type(profile.get(key, 0)) is not int or profile.get(key, 0) != 0
               for key in ("quarter_offset", "month_offset")):
            raise ValueError("Missing date_mode with a nonzero/invalid offset: the intended period is ambiguous.")
        profile["date_mode"] = "catalogue"
        changes.append("settings.sources.copernicus.date_mode = catalogue (keep the saved date selection)")
    for key, active_mode in (("quarter_offset", "relative_quarter"), ("month_offset", "relative_month")):
        if key not in profile and profile["date_mode"] != active_mode:
            profile[key] = 0
            changes.append(f"settings.sources.copernicus.{key} = 0 (inactive offset)")
    return candidate, changes


def prepare_profile_settings(settings, validate, preserve_full=False):
    candidate, changes = complete_legacy_period_fields(settings)
    validation = deepcopy(candidate)
    if preserve_full and validation.get("source", {}).get("provider") != "eumetsat":
        # Full backups retain inactive WMS layers and local paths.
        validation["layers"] = []
    canonical = strict_settings(validation, validate)
    return (candidate if preserve_full else canonical), changes


def resolve_import_repairs(prepared, confirm_repair=None, allow_skip=True):
    """Run only after preflight has finished; prepared = (label, value, changes)."""
    accepted, skipped = [], []
    accept_all = False
    skip_all = False
    for label, value, changes in prepared:
        if changes and skip_all:
            skipped.append(f"{label}: skipped by user.")
            continue
        if changes and not accept_all:
            if confirm_repair is None:
                raise ValueError(f"{label}: missing fields require confirmation: " + "; ".join(changes))
            decision = confirm_repair(label, changes)
            if decision == "no":
                raise ImportCancelled("Import cancelled; nothing was imported.")
            if decision in {"skip", "skip_all"} and allow_skip:
                skip_all = decision == "skip_all"
                skipped.append(f"{label}: skipped by user.")
                continue
            if decision not in {"yes", "yes_all"}:
                raise ImportCancelled("Import cancelled; nothing was imported.")
            accept_all = decision == "yes_all"
        accepted.append(value)
    return accepted, skipped


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}.")
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError(f"Invalid JSON number: {value}.")


def _loads(text):
    return json.loads(text, object_pairs_hook=_json_pairs, parse_constant=_invalid_number)


def _digest(entries):
    data = json.dumps(entries, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def profile_export_filename(item):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item["name"]).strip(" .") or "Profile"
    return re.sub(r"_+", "_", f"MarbleScape_Profile_{name}_{uuid.UUID(hex=item['id'])}.json")


def copy_name(name, occupied, limit=MAX_NAME_LENGTH):
    """Create an unambiguous Copy suffix, preserving room for its number."""
    base = re.sub(r" \(Copy(?: \d+)?\)$", "", name)
    number = 0
    while True:
        suffix = " (Copy)" if number == 0 else f" (Copy {number})"
        candidate = base[:limit - len(suffix)].rstrip() + suffix
        if not occupied(candidate):
            return candidate
        number += 1


def imported_name(name, occupied, limit=MAX_NAME_LENGTH):
    """Mark a different profile whose name is already taken: (Imported), (Imported 2), ..."""
    base = re.sub(r" \((?i:imported)(?: \d+)?\)$", "", name)
    number = 1
    while True:
        suffix = " (Imported)" if number == 1 else f" (Imported {number})"
        candidate = base[:limit - len(suffix)].rstrip() + suffix
        if not occupied(candidate):
            return candidate
        number += 1


class ConflictDecisions:
    """Batch-local choices; never remember overwrite permission between calls."""
    def __init__(self, callback):
        self.callback = callback
        self.all = None

    def choose(self, context):
        allowed = context.get("overwrite_allowed", True)
        if self.all == "skip" or (self.all == "overwrite" and allowed):
            return self.all
        if self.callback is None:
            raise ValueError(f"File or profile already exists; confirmation required: {context['label']}")
        choice = self.callback(context)
        if choice in {"overwrite_all", "skip_all"}:
            self.all = choice.removesuffix("_all")
            choice = self.all
        if choice == "cancel" or choice not in {"overwrite", "skip", "rename"}:
            raise ImportCancelled("Transfer cancelled; no changes were made.")
        if choice == "overwrite" and not allowed:
            raise ValueError("This conflict cannot be overwritten unambiguously. Skip it or save a copy.")
        return choice


def _file_marker(path):
    try:
        stat = path.lstat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


def _publish_new(temporary, destination):
    # Neither operation replaces a concurrently created destination.
    if os.name == "nt":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)
        temporary.unlink()


def _verify_export(path, expected, validate_bytes):
    """Read bounded bytes back from disk, compare exactly, then parse/validate."""
    with path.open("rb") as handle:
        saved = handle.read(len(expected) + 1)
    if saved != expected:
        raise ValueError(f"Export verification failed: saved contents differ ({path.name}).")
    try:
        validate_bytes(saved)
    except Exception as exc:
        raise ValueError(f"Export verification failed ({path.name}): {exc}") from exc


def write_verified_export_batch(planned, validate_bytes):
    """Verify staged and published bytes; retain originals until every check passes."""
    staged, written = [], []
    retain = set()
    try:
        for path, data, marker in planned:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".marblescape-profile-",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                staged.append((path, temporary, marker, data))
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _verify_export(temporary, data, validate_bytes)
        for path, temporary, marker, _data in staged:
            if _file_marker(path) != marker:
                raise ValueError(f"Destination changed during export; please retry: {path.name}")
            backup = None
            if marker is not None:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".marblescape-restore-",
                                                 suffix=".tmp", delete=False) as handle:
                    backup = Path(handle.name)
                retain.add(backup)
                try:
                    shutil.copy2(path, backup)
                except Exception:
                    backup.unlink(missing_ok=True)
                    retain.discard(backup)
                    raise
                if _file_marker(path) != marker:
                    backup.unlink()
                    retain.discard(backup)
                    raise ValueError(f"Destination changed during export; please retry: {path.name}")
            try:
                if backup is not None:
                    os.replace(temporary, path)
                else:
                    _publish_new(temporary, path)
            except Exception:
                if backup is not None:
                    backup.unlink(missing_ok=True)
                    retain.discard(backup)
                raise
            written.append((path, backup, _file_marker(path)))
        # Check the actual destinations, not just the staging files. Originals
        # remain available for rollback until the entire batch has been reread.
        for path, _temporary, _marker, data in staged:
            _verify_export(path, data, validate_bytes)
    except Exception as exc:
        recovery = []
        for path, backup, installed_marker in reversed(written):
            try:
                if _file_marker(path) != installed_marker:
                    raise OSError("Destination changed after publication.")
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(backup, path)
                    retain.discard(backup)
            except OSError:
                recovery.append(f"{path} (original retained at {backup})" if backup else str(path))
        if recovery or retain:
            raise OSError("Export failed; some files require recovery. Originals were retained: "
                          + "; ".join(recovery + [str(path) for path in retain])) from exc
        raise
    else:
        for backup in retain:
            backup.unlink(missing_ok=True)
    finally:
        for _path, temporary, _marker, _data in staged:
            temporary.unlink(missing_ok=True)
    return [path for path, _backup, _marker in written]


def export_profiles(folder, items, validate, confirm_conflict=None, destination=None, coverage_records=None):
    """One JSON per profile; preflight every conflict before atomic publication."""
    items = normalize_library({"items": items})["items"]
    if not items:
        raise ValueError("Select one or more profiles to export.")
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError("Choose an existing destination folder.")
    if destination is not None and len(items) != 1:
        raise ValueError("A Save As filename can only be used for a single profile.")
    prepared = []
    for item in items:
        checked, _changes = prepare_profile_settings(item["settings"], validate, preserve_full=True)
        entry = {"id": item["id"], "name": item["name"],
                 "settings": portable_settings(checked, validate)}
        record = (coverage_records or {}).get(item["id"], {})
        value = record.get("data_coverage_percent") if isinstance(record, dict) else None
        if type(value) in (int, float) and 0 <= value <= 100:
            # Snapshot value: profile settings themselves remain portable render settings.
            entry["data_coverage_percent"] = round(float(value), 2)
        entries = [entry]
        document = {"format": FORMAT, "version": 3, "profiles": entries,
                    "profiles_sha256": _digest(entries)}
        data = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError("The profile export exceeds the permitted size.")
        path = Path(destination).absolute() if destination is not None else folder / profile_export_filename(item)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        if path.parent.resolve() != folder.resolve():
            raise ValueError("The export filename must be in the selected folder.")
        prepared.append((path, data))
    decisions = ConflictDecisions(confirm_conflict)
    planned, reserved = [], set()
    for path, data in prepared:
        marker = _file_marker(path)
        if marker is not None:
            choice = decisions.choose({"kind": "export", "label": str(path), "multiple": len(items) > 1,
                                       "overwrite_allowed": path.is_file() and not path.is_symlink()})
            if choice == "skip":
                continue
            if choice == "rename":
                stem = copy_name(path.stem, lambda name: (path.with_name(name + path.suffix).exists()
                    or path.with_name(name + path.suffix).is_symlink()
                    or str(path.with_name(name + path.suffix)).casefold() in reserved), limit=240)
                path = path.with_name(stem + path.suffix)
                marker = None
        key = str(path).casefold()
        if key in reserved:
            raise ValueError(f"Multiple exports would use the same filename: {path.name}")
        reserved.add(key)
        planned.append((path, data, marker))
    def validate_saved(data):
        entries, _version = _read_profile_json(data)
        normalized = normalize_library({"items": [{key: value for key, value in entry.items()
                                                     if key != "data_coverage_percent"}
                                                   for entry in entries]})
        for entry in normalized["items"]:
            strict_settings(entry["settings"], validate)

    return write_verified_export_batch(planned, validate_saved)


def read_png_record(path):
    if Path(path).stat().st_size > 100 * 1024 * 1024:
        raise ValueError("PNG profile imports are limited to 100 MiB per image.")
    with Image.open(path) as picture:
        if picture.format != "PNG" or picture.width * picture.height > 100_000_000:
            raise ValueError("Invalid or oversized PNG image.")
        picture.verify()
    with Image.open(path) as picture:
        picture.load()
    records = []
    with Path(path).open("rb") as handle:
        if handle.read(8) != PNG_SIGNATURE:
            raise ValueError("This is not a PNG image.")
        finished = False
        while True:
            header = handle.read(8)
            if len(header) != 8:
                break
            length, kind = struct.unpack(">I4s", header)
            if length > 100 * 1024 * 1024:
                raise ValueError("Invalid PNG chunk length.")
            if kind == b"IEND":
                if length != 0:
                    raise ValueError("Invalid PNG IEND length.")
                crc = handle.read(4)
                if len(crc) != 4 or struct.unpack(">I", crc)[0] != zlib.crc32(b"IEND") & 0xffffffff:
                    raise ValueError("Invalid PNG IEND checksum.")
                if handle.read(1):
                    raise ValueError("Unexpected data after PNG IEND.")
                finished = True
                break
            if kind in {b"iTXt", b"eXIf"}:
                if length > MAX_METADATA_BYTES * 8:
                    raise ValueError("PNG metadata is too large.")
                payload = handle.read(length)
                crc = handle.read(4)
                if len(payload) != length or len(crc) != 4 or struct.unpack(">I", crc)[0] != zlib.crc32(kind + payload) & 0xffffffff:
                    raise ValueError("Invalid PNG metadata checksum.")
                text = None
                if kind == b"iTXt" and payload.startswith(b"MarbleScape\0\0\0\0\0"):
                    text = payload[len(b"MarbleScape\0\0\0\0\0"):].decode("utf-8")
                elif kind == b"eXIf":
                    exif = Image.Exif()
                    exif.load(b"Exif\0\0" + payload)
                    candidate = exif.get(270)
                    if exif.get(305) == "MarbleScape" and (not isinstance(candidate, str) or not candidate.startswith("{")):
                        raise ValueError("MarbleScape EXIF profile data is missing or unreadable.")
                    if isinstance(candidate, str) and candidate.startswith("{"):
                        text = candidate
                if text is not None:
                    if len(text.encode("utf-8")) > MAX_METADATA_BYTES * 6:
                        raise ValueError("MarbleScape PNG metadata is too large.")
                    record = _loads(text)
                    if isinstance(record, dict) and record.get("software") == "MarbleScape":
                        records.append(record)
            else:
                handle.seek(length, 1)
                if len(handle.read(4)) != 4:
                    break
    if not finished or not records:
        raise ValueError("No readable MarbleScape profile metadata was found in this PNG.")
    if any(record != records[0] for record in records[1:]):
        raise ValueError("PNG text and EXIF contain conflicting MarbleScape metadata.")
    return records[0]


def _check_png_profile_metadata(record, settings):
    """Compare like-for-like provenance; never resolve a relative date to today."""
    def equivalent(actual, expected):
        if isinstance(expected, list):
            return (isinstance(actual, list) and len(actual) == len(expected)
                    and all(equivalent(a, b) for a, b in zip(actual, expected)))
        same_type = type(actual) is type(expected) or (
            type(actual) in (int, float) and type(expected) in (int, float))
        return same_type and actual == expected

    def check(field, expected):
        if field in record and not equivalent(record[field], expected):
            raise ValueError(f"PNG metadata conflicts with profile setting {field}.")

    provider = settings.get("source", {}).get("provider")
    check("source", SOURCE_LABELS.get(provider))
    # Keep the original record intact: repair consent is still required later.
    comparison, _changes = complete_legacy_period_fields(settings)
    profile = comparison.get("sources", {}).get(provider, {})
    selectors = ("satellite", "mission", "area", "sector", "product", "layer", "resolution", "theme")
    if provider != "copernicus":
        if any(key in record for key in ("mosaic_adjustments", "mosaic_contrast_percent", "auto_brightness",
                                         "auto_contrast", "image_size_selection", "auto_recommendation")):
            raise ValueError("Copernicus adjustment metadata conflicts with the selected image source.")
        for field in selectors:
            check(field, profile.get(field))
        if provider == "himawari":
            check("shorelines", profile.get("shorelines", False))
            if profile.get("shorelines"):
                check("shoreline_color", profile.get("shoreline_color"))
            check("center", profile.get("center", False) or None)
            if profile.get("center"):
                check("latitude", profile.get("latitude"))
                check("longitude", profile.get("longitude"))
        elif any(key in record for key in ("shorelines", "shoreline_color", "center",
                                           "center_latitude", "center_longitude")):
            raise ValueError("Himawari view metadata conflicts with the selected image source.")
        if "layers" in record:
            expected = [layer["name"] for layer in settings.get("layers", [])
                        if layer.get("enabled", True) and layer.get("name")]
            check("layers", expected if provider == "eumetsat" else None)
        return

    product = get_product(profile.get("configuration"), profile.get("product"))
    layer = get_layer(product, profile.get("layer"))
    for field in selectors:
        # The PNG uses display names here; the saved profile uses catalogue IDs.
        expected = ((product or {}).get("name") if field == "product" else
                    (layer or {}).get("name") if field == "layer" else profile.get(field))
        check(field, expected)
    # With Use auto recommendation the rule chose the date and Gap fill fields of the
    # picture; the profile keeps its own saved values.
    auto = record.get("auto_recommendation") is True
    check("auto_recommendation", profile.get("auto_recommendation", False))
    if auto:
        check("auto_priority", profile.get("auto_priority"))
        check("auto_precise", profile.get("auto_precise", False))
    chosen = {"selected_date", "date_mode", "quarter_offset", "month_offset", "coverage_mode",
              "lookback_days", "max_cloud_cover_percent"} if auto else set()
    for field, key in (("latitude", "latitude"), ("longitude", "longitude"),
                       ("map_zoom", "map_zoom"), ("map_labels", "map_labels"),
                       ("map_label_color", "map_label_color"),
                       ("map_borders", "map_borders"), ("map_border_color", "map_border_color"),
                       ("selected_date", "date"), ("date_mode", "date_mode"),
                       ("quarter_offset", "quarter_offset"), ("month_offset", "month_offset"),
                       ("coverage_mode", "coverage_mode"), ("lookback_days", "lookback_days"),
                       ("mosaic_brightness_percent", "brightness"),
                       ("no_data_color", "no_data_color"),
                       ("scene_no_data_color", "scene_no_data_color"),
                       ("mosaic_contrast_percent", "contrast"),
                       ("auto_brightness", "auto_brightness"), ("auto_contrast", "auto_contrast"),
                       ("image_size_selection", "image_size"),
                       ("max_cloud_cover_percent", "max_cloud_cover")):
        if field not in chosen:
            check(field, profile.get(key))

    if "mosaic_adjustments" in record:
        from marblescape_copernicus import tone_rule
        # Mosaics carried tone metadata before the rules; regular layers only with one.
        if "date_granularity" not in (layer or {}) and tone_rule(layer) is None:
            raise ValueError("Tone metadata requires a mosaic layer or a layer with a tone rule.")
        from marblescape_mosaic_adjustments import validate as validate_adjustments
        validate_adjustments(record["mosaic_adjustments"], profile)

    granularity = (layer or {}).get("date_granularity")

    def period(value):
        if granularity == "quarter":
            return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
        if granularity == "month":
            return f"{value.year}-{value.month:02d}"
        return str(value.year)

    resolved = None
    if "resolved_date" in record:
        try:
            resolved = dt.date.fromisoformat(record["resolved_date"])
        except (ValueError, TypeError) as exc:
            raise ValueError("PNG resolved_date must be an ISO calendar date.") from exc
    if "mosaic_period" in record:
        if granularity not in {"quarter", "month", "year"} or resolved is None:
            raise ValueError("PNG mosaic_period requires a mosaic layer and resolved_date.")
        check("mosaic_period", period(resolved))
    if (resolved is not None and granularity in {"quarter", "month", "year"} and not auto
            and profile.get("date_mode") == "catalogue" and profile.get("date") != "latest"):
        if period(resolved) != period(dt.date.fromisoformat(profile["date"])):
            raise ValueError("PNG resolved_date conflicts with the fixed profile period.")


def _read_entries(path):
    path = Path(path)
    if path.name.startswith("_"):
        raise ValueError("Files whose name starts with '_' are reserved and cannot be imported.")
    if path.suffix.lower() == ".png":
        record = read_png_record(path)
        if type(record.get("schema_version")) is not int or record["schema_version"] not in (3, SCHEMA_VERSION):
            raise ValueError("Unsupported or missing PNG metadata version.")
        if not record.get("profile_name") or "profile_settings" not in record:
            raise ValueError("PNG has no complete named profile. Use a profile export or a new profile image.")
        settings = record["profile_settings"]
        if not isinstance(settings, dict):
            raise ValueError("PNG profile settings must be an object.")
        with Image.open(path) as picture:
            profile_size = list(picture.size)
            if record.get("image_role") == "wallpaper_derivative":
                profile_size = record.get("profile_image_size")
                if not isinstance(profile_size, list) or len(profile_size) != 2 or any(type(value) is not int or value <= 0 for value in profile_size):
                    raise ValueError("Wallpaper source-image dimensions are missing or invalid.")
            for field, actual in (("width", picture.width), ("height", picture.height)):
                if field in record and (type(record[field]) is not int or record[field] != actual):
                    raise ValueError(f"PNG {field} conflicts with the image dimensions.")
                # Older PNGs record the device output size, which import ignores;
                # it must still match the picture.
                legacy_output = settings.get("output", {})
                if (isinstance(legacy_output, dict) and field in legacy_output
                        and legacy_output[field] != profile_size[0 if field == "width" else 1]):
                    raise ValueError(f"PNG profile output.{field} conflicts with the image dimensions.")
            if settings.get("source", {}).get("provider") == "copernicus":
                selection = settings.get("sources", {}).get("copernicus", {}).get("image_size", "auto")
                if selection != "auto":
                    try:
                        selected_size = list(map(int, selection.split("x")))
                    except (ValueError, AttributeError) as exc:
                        raise ValueError("Invalid PNG image_size selection.") from exc
                    if selected_size != profile_size:
                        raise ValueError("PNG image_size selection conflicts with the source image dimensions.")
        _check_png_profile_metadata(record, settings)
        from marblescape_data_coverage import validate as validate_coverage
        from marblescape_snapshot import SYSTEM_KIND, SYSTEM_NAME, IMPORTED_NAME, from_record, validate_snapshot
        validate_coverage(record)
        if record.get("schema_version") == 4 and not {"data_coverage", "data_coverage_percent"} <= record.keys():
            raise ValueError("PNG data coverage fields are missing.")
        entry = {"name": record["profile_name"], "settings": record["profile_settings"]}
        if "profile_kind" in record:
            if record["profile_kind"] != SYSTEM_KIND or record["profile_name"] != SYSTEM_NAME:
                raise ValueError("Invalid system snapshot provenance.")
            validate_snapshot(from_record(record), lambda value: value)
            entry["name"] = IMPORTED_NAME
        if "profile_id" in record:
            entry["id"] = record["profile_id"]
        return [entry], 1
    if path.suffix.lower() != ".json":
        raise ValueError("Only .json and .png profile imports are supported.")
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("Profile JSON exceeds 1 MB.")
    return _read_profile_json(data)


def _read_profile_json(data):
    document = _loads(data.decode("utf-8-sig"))
    if not isinstance(document, dict) or document.get("format") != FORMAT or type(document.get("version")) is not int or document["version"] not in (1, 2, 3):
        raise ValueError("Unsupported MarbleScape profile JSON format.")
    entries = document.get("profiles")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_PROFILES:
        raise ValueError("Profile JSON must contain 1 to 100 profiles.")
    if document["version"] >= 2 and document.get("profiles_sha256") != _digest(entries):
        raise ValueError("Profile file checksum does not match its contents.")
    return entries, document["version"]


@dataclass
class ProfileImportResult:
    items: list
    imported: list
    warnings: list
    renamed: list = field(default_factory=list)

    def __iter__(self):
        # Keep read-only callers that unpack imported items/warnings working.
        yield self.imported
        yield self.warnings


def import_profiles(paths, existing, defaults, validate, confirm_repair=None, confirm_conflict=None, review_preflight=None):
    """Preflight all data, preserve supplied UUIDs, then build an independent draft."""
    if not paths or len(paths) > MAX_PROFILES:
        raise ValueError("Choose between 1 and 100 profile files.")
    prepared, failures = [], []
    for filename in paths:
        path = Path(filename)
        try:
            entries, version = _read_entries(path)
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")
            continue
        for index, entry in enumerate(entries, 1):
            label = f"{path.name} / profile {index}"
            try:
                if not isinstance(entry, dict) or not {"name", "settings"} <= entry.keys() or set(entry) - {"id", "name", "settings", "data_coverage_percent"}:
                    raise ValueError("Each profile requires name and settings.")
                label += f" ({entry['name']})"
                if version >= 2 and "id" not in entry:
                    raise ValueError("Profile ID is missing.")
                if "data_coverage_percent" in entry:
                    coverage = entry["data_coverage_percent"]
                    if type(coverage) not in (int, float) or not 0 <= coverage <= 100:
                        raise ValueError("Profile data_coverage_percent must be a number from 0 to 100.")
                item = normalize_library({"items": [{
                    "id": entry.get("id", new_profile_id()), "name": entry["name"],
                    "settings": entry["settings"],
                }]})["items"][0]
                item["settings"], changes = prepare_profile_settings(item["settings"], validate)
                prepared.append((label, (label, item, "id" in entry), changes))
            except Exception as exc:
                failures.append(f"{label}: {exc}")
    draft = normalize_library({"items": existing})["items"]
    if review_preflight is not None and (failures or any(changes for _label, _value, changes in prepared)):
        report = {"failures": list(failures), "valid_count": len(prepared),
                  "repairs": [(label, changes) for label, _value, changes in prepared if changes]}
        if not review_preflight(report):
            raise ImportCancelled("Import cancelled; no profiles were changed.")
    if confirm_repair is None:
        # Programmatic callers retain per-entry error reporting; no silent repair.
        failures.extend(f"{label}: missing fields require confirmation: " + "; ".join(changes)
                        for label, _value, changes in prepared if changes)
        prepared = [entry for entry in prepared if not entry[2]]
    accepted, skipped = resolve_import_repairs(prepared, confirm_repair)
    failures.extend(skipped)
    decisions = ConflictDecisions(confirm_conflict)
    touched, renamed = set(), []
    for label, item, has_id in accepted:
        # The UUID identifies a profile. Only legacy records without one are
        # recognized by name; a different UUID is a different profile.
        matches = [index for index, current in enumerate(draft) if current["id"] == item["id"]]
        reason = "UUID"
        if not matches and not has_id:
            matches = [index for index, current in enumerate(draft)
                       if current["name"].casefold() == item["name"].casefold()]
            reason = "name"
        try:
            choice = None
            rename_note = None
            if matches:
                choice = decisions.choose({"kind": "import", "label": label, "reason": reason,
                    "incoming": deepcopy(item), "existing": [deepcopy(draft[index]) for index in matches],
                    "has_id": has_id, "multiple": len(prepared) > 1, "overwrite_allowed": len(matches) == 1})
                if choice == "skip":
                    failures.append(f"{label}: skipped by user.")
                    continue
                if choice == "rename":
                    names = {current["name"].casefold() for current in draft}
                    item["name"] = copy_name(item["name"], lambda name: name.casefold() in names)
                    item["id"] = new_profile_id()
                elif not has_id:
                    # Legacy imports have no original ID to preserve.
                    item["id"] = draft[matches[0]]["id"]
            # An import never leaves two profiles with the same name: mark the
            # incoming one, whether it is added or overwrites another profile.
            others = [current for index, current in enumerate(draft)
                      if not (choice == "overwrite" and index == matches[0])]
            if choice != "rename" and any(current["name"].casefold() == item["name"].casefold()
                                          for current in others):
                names = {current["name"].casefold() for current in others}
                original_name = item["name"]
                item["name"] = imported_name(item["name"], lambda name: name.casefold() in names)
                rename_note = f"{original_name} -> {item['name']}"
            candidate = deepcopy(draft)
            if choice == "overwrite":
                candidate[matches[0]] = item
            else:
                candidate.append(item)
            checked = normalize_library({"items": candidate})["items"]
            if choice == "overwrite":
                touched.discard(draft[matches[0]]["id"])
            draft = checked
            touched.add(item["id"])
            if rename_note:
                renamed.append(rename_note)
        except (ValueError, TypeError, KeyError) as exc:
            failures.append(f"{label}: {exc}")
    return ProfileImportResult(draft, [item for item in draft if item["id"] in touched], failures, renamed)
