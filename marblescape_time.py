"""Display-time helpers; persisted provider and cache timestamps remain UTC."""

from __future__ import annotations

import datetime as dt


TIME_ZONE_SYSTEM = "system"
TIME_ZONE_UTC = "utc"
TIME_ZONE_CHOICES = (TIME_ZONE_SYSTEM, TIME_ZONE_UTC)
TIME_ZONE_MENU_CHOICES = (
    ("System time (recommended)", TIME_ZONE_SYSTEM),
    ("UTC", TIME_ZONE_UTC),
)


def normalize_time_zone(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Display time zone must be system or UTC.")
    value = value.strip().casefold()
    if value not in TIME_ZONE_CHOICES:
        raise ValueError("Display time zone must be system or UTC.")
    return value


def _parse_datetime(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _offset_label(value: dt.datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return "local time"
    total_minutes = int(offset.total_seconds() // 60)
    if total_minutes == 0:
        return "UTC"
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def format_display_datetime(value, time_zone="system", include_seconds=False) -> str:
    """Format an instant in UTC or the OS time zone with an explicit zone label."""
    parsed = _parse_datetime(value)
    mode = normalize_time_zone(time_zone)
    if mode == TIME_ZONE_UTC:
        displayed, label = parsed, "UTC"
    else:
        displayed = parsed.astimezone()
        label = _offset_label(displayed)
    pattern = "%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M"
    return f"{displayed.strftime(pattern)} {label}"


def format_utc_datetime(value, include_seconds=False) -> str:
    return format_display_datetime(value, TIME_ZONE_UTC, include_seconds)
