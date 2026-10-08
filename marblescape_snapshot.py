"""Protected latest-without-profile state, outside the rotation library."""
import datetime as dt
import json
import re
from copy import deepcopy

SYSTEM_ID = "__latest_snapshot__"
SYSTEM_NAME = "Latest snapshot (no profile)"
IMPORTED_NAME = "Latest snapshot (Imported)"
SYSTEM_KIND = "latest_snapshot"
# Profile-cache slot of the last image downloaded without a profile. Generated
# profile IDs are random UUID4 values and never equal it.
CACHE_ID = "0" * 32


def validate_snapshot(value, validate_settings):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"kind", "snapshot_id", "profile_id", "generated_at_utc", "settings"}:
        raise ValueError("Invalid latest snapshot fields.")
    if value["kind"] != SYSTEM_KIND:
        raise ValueError("Invalid latest snapshot kind.")
    for key in ("snapshot_id", "profile_id"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{32}", value[key]):
            raise ValueError(f"Invalid latest snapshot {key}.")
    if not isinstance(value["generated_at_utc"], str):
        raise ValueError("Invalid latest snapshot time.")
    stamp = dt.datetime.fromisoformat(value["generated_at_utc"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Latest snapshot time requires a UTC offset.")
    result = deepcopy(value)
    result["settings"] = validate_settings(result["settings"])
    return result


def from_config(config, validate_settings):
    section = config.get("latest_snapshot", {})
    if not isinstance(section, dict) or set(section) - {"record_json"}:
        raise ValueError("Invalid latest_snapshot configuration section.")
    raw = section.get("record_json", "")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise ValueError("Invalid latest snapshot document.")
    if not raw:
        return None
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate latest snapshot field.")
            result[key] = value
        return result
    return validate_snapshot(json.loads(raw, object_pairs_hook=pairs), validate_settings)


def from_record(record):
    return {"kind": SYSTEM_KIND, "snapshot_id": record["snapshot_id"],
            "profile_id": record["profile_id"], "generated_at_utc": record["generated_at_utc"],
            "settings": deepcopy(record["profile_settings"])}


def export_item(snapshot):
    if snapshot is None:
        raise ValueError("No image yet. Download an image without an active profile first.")
    return {"id": snapshot["profile_id"], "name": IMPORTED_NAME,
            "settings": deepcopy(snapshot["settings"])}
