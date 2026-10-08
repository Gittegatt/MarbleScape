"""Embedded Tk settings editor for the local image-profile list.

ProfilesSettings(parent, library, capture_settings, on_load, on_apply=None,
status=None, on_save=None) edits the profile list. With on_save, every change
(after any confirmation) is passed to on_save(library) at once; a failed save
restores the last saved state. get_library() validates and copies the list.
on_load receives a settings copy for the Image form.
on_force_load(profile_ids) loads new pictures without changing the active
profile and returns (newly queued IDs, whether the active profile reloads now).
close() cancels status polling and is safe to call more than once.
"""

from copy import deepcopy
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from marblescape_copernicus import (
    get_highlight, get_layer, get_product, get_theme, no_data_choice, rolling_quarter_start,
    rolling_month_start, tone_rule, image_size_label,
)
from marblescape_catalogue_activity import reserve_text_lines
from marblescape_download_progress import (
    ACTIVE_SYMBOL, ACTIVITY_SYMBOL, DOWNLOAD_SYMBOL, FAILURE_SYMBOLS, QUEUE_SYMBOL, RENDER_SYMBOL,
)
from marblescape_slider import effective_resolution as _slider_effective_resolution
from marblescape_theme import (
    entry_placeholder as _entry_placeholder,
    menu_check_images as _menu_check_images,
    menu_entry_enabled as _menu_entry_enabled,
    on_theme_change as _on_theme_change,
    palette as _theme_palette,
    set_menu_entry_enabled as _set_menu_entry_enabled,
)
from marblescape_eumetsat import THEME_LABELS as _EUMETSAT_THEME_LABELS
from marblescape_profiles import (
    CHECKBOX_COLUMN_MIN_WIDTH, CHECKBOX_COLUMNS, DEFAULT_PROFILE_COLUMN_WIDTHS, DEFAULT_VISIBLE_PROFILE_COLUMNS,
    NARROW_COLUMNS, STATUS_SYMBOL_COLUMN,
    DEFAULT_PROFILE_TABLE_ROWS, MAX_PROFILE_TABLE_ROWS, MIN_PROFILE_TABLE_ROWS,
    ROTATION_INTERVAL_CHOICES, ROTATION_INTERVAL_UNITS, clean_profile_name, new_profile_id,
    normalize_library, normalize_profile_column_widths, normalize_profile_column_order,
    normalize_profile_table_rows,
)
from marblescape_profile_transfer import export_profiles, import_profiles, ImportCancelled, profile_export_filename
from marblescape_import_dialog import confirm_import_repair, confirm_transfer_conflict, review_import_preflight
from marblescape_time import format_display_datetime, format_utc_datetime


class _ProfileNameDialog(simpledialog.Dialog):
    """Readable profile-name prompt for Rename and Create Profile from Image."""

    def __init__(self, parent, initial_value, title="Rename profile", prompt="New profile name:"):
        self._initial_value = initial_value
        self._prompt = prompt
        self._entry = None
        super().__init__(parent, title=title)

    def body(self, master):
        ttk.Label(master, text=self._prompt).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self._entry = ttk.Entry(master, width=52)
        self._entry.grid(row=1, column=0, sticky="ew")
        self._entry.insert(0, self._initial_value)
        self._entry.selection_range(0, "end")
        master.columnconfigure(0, weight=1)
        return self._entry

    def apply(self):
        self.result = self._entry.get()


def _ask_profile_name(parent, initial_value):
    return _ProfileNameDialog(parent, initial_value).result


def _ask_new_profile_name(parent, initial_value):
    """Name for Create Profile from Image; None when cancelled."""
    return _ProfileNameDialog(parent, initial_value, title="Create profile",
                              prompt="Profile name:").result


class _DeleteProfilesDialog(simpledialog.Dialog):
    """Yes/No delete confirmation with an opt-in to remove History images."""

    def __init__(self, parent, title, prompt, history_label, history_note=""):
        self._prompt = prompt
        self._history_label = history_label
        self._history_note = history_note
        self._history_var = None
        super().__init__(parent, title=title)

    def body(self, master):
        ttk.Label(master, text=self._prompt, wraplength=460, justify="left").grid(row=0, column=0, sticky="w")
        self._history_var = tk.BooleanVar(master=master, value=False)
        ttk.Checkbutton(master, text=self._history_label, variable=self._history_var).grid(
            row=1, column=0, sticky="w", pady=(10, 0))
        if self._history_note:
            ttk.Label(master, text=self._history_note, wraplength=440, justify="left").grid(
                row=2, column=0, sticky="w", padx=(20, 0), pady=(4, 0))
        return None

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text="Yes", width=10, command=self.ok).pack(side="left", padx=5, pady=5)
        no_button = ttk.Button(box, text="No", width=10, command=self.cancel)
        no_button.pack(side="left", padx=5, pady=5)
        # Like the plain delete prompt, No is the default answer.
        self.initial_focus = no_button
        self.bind("<Return>", self._invoke_focused)
        self.bind("<Escape>", self.cancel)
        box.pack()

    def _invoke_focused(self, _event=None):
        widget = self.focus_get()
        if isinstance(widget, ttk.Button):
            widget.invoke()
        else:
            self.cancel()

    def apply(self):
        self.result = bool(self._history_var.get())


def _confirm_profile_delete(parent, title, prompt, history_label, history_note=""):
    """None when cancelled, otherwise whether History folders should go too."""
    return _DeleteProfilesDialog(parent, title, prompt, history_label, history_note).result

_SOURCE_LABELS = {"eumetsat": "EUMETSAT", "goes_east": "GOES-East",
                  "goes_west": "GOES-West", "solar": "Solar / Sun",
                  "himawari": "Himawari", "slider": "CIRA SLIDER",
                  "copernicus": "Copernicus", "worldview": "NASA Worldview"}
_PRESET_LABELS = {"full_earth": "Full Earth", "europe": "Europe",
                  "mediterranean": "Mediterranean", "central_europe": "Central Europe",
                  "custom": "Custom area"}
PROFILE_LIST_COLUMNS = (
    "rotation_enabled", "name", "status_symbol", "active", "history", "image_updates", "source", "mission", "product",
    "layer", "time_selection", "time", "quarter_mode",
    "quarter_offset",
    "quarter_target", "location", "latitude", "longitude",
    "gap_fill", "cloud_coverage", "mosaic_brightness", "zoom", "maximum_lookback",
    "output_resolution", "resolution", "map_labels", "map_borders", "cache_status", "last_download", "id", "short_id",
    "no_data_color", "image_size", "mosaic_contrast", "auto_brightness", "auto_contrast", "data_coverage",
    "auto_recommendation", "auto_priority", "auto_precise", "auto_choice", "shorelines",
)
PROFILE_LIST_COLUMN_LABELS = {
    "rotation_enabled": "Rotation",
    "id": "Profile ID",
    "short_id": "Short ID",
    "name": "Profile name",
    # The column id stays "active" so saved layouts keep their position.
    "active": "Status",
    # Named in the Columns menu; its heading stays empty (_heading_text).
    "status_symbol": "Status symbol",
    "history": "History",
    # Imagery updates > Check for and download newer images, saved in the profile.
    "image_updates": "Updates",
    "source": "Source",
    # The former Selection column, split up; the area is in Area / location.
    "mission": "Satellite / mission",
    "product": "Product",
    "layer": "Layer",
    # How the date is chosen (Fixed, Latest, Rolling, Timeless); Time shows the date.
    "time_selection": "Time selection",
    "time": "Time",
    "quarter_mode": "Period selection",
    "quarter_offset": "Periods back",
    "quarter_target": "Resolved period",
    "location": "Area / location",
    "latitude": "Lat",
    "longitude": "Long",
    "gap_fill": "Gap fill",
    # The saved limit, named as in the Image tab and Compare variants.
    "cloud_coverage": "Max. cloud cover",
    "mosaic_brightness": "Brightness correction",
    "zoom": "Zoom",
    "maximum_lookback": "Maximum lookback",
    # The column id stays "output_resolution" so saved layouts keep it.
    "output_resolution": "Resolution selection",
    # The size the newest picture really had (see _picture_resolution).
    "resolution": "Resolution",
    "map_labels": "Labels",
    "map_borders": "Country borders",
    # Himawari's Plot shorelines, shown like the Copernicus overlays.
    "shorelines": "Shorelines",
    "cache_status": "Cache status",
    "last_download": "Last download",
    "no_data_color": "No-data color",
    "image_size": "Image size selection",
    "mosaic_contrast": "Contrast correction",
    "auto_brightness": "Auto brightness",
    "auto_contrast": "Auto contrast",
    "data_coverage": "Data coverage %",
    "auto_recommendation": "Auto recommendation",
    "auto_priority": "Auto priority",
    "auto_precise": "Precise check",
    "auto_choice": "Auto choice",
}

# The Columns menu in fixed areas, separated by lines; the table's own column
# order is changed by dragging the headings.
COLUMN_MENU_GROUPS = (
    ("rotation_enabled", "name", "status_symbol", "active", "history", "image_updates", "id", "short_id"),
    ("source", "mission", "product", "layer", "location", "latitude", "longitude", "zoom"),
    ("time_selection", "time", "quarter_mode", "quarter_offset", "quarter_target", "last_download"),
    ("output_resolution", "resolution", "image_size"),
    ("gap_fill", "cloud_coverage", "maximum_lookback", "no_data_color", "map_labels", "map_borders",
     "shorelines"),
    ("mosaic_brightness", "mosaic_contrast", "auto_brightness", "auto_contrast"),
    ("auto_recommendation", "auto_priority", "auto_precise", "auto_choice"),
    ("data_coverage", "cache_status"),
)

# Palette keys of the priorities, as in Compare variants: Fewest clouds blue,
# Data coverage green, Newest orange.
AUTO_PRIORITY_COLORS = {"Fewest clouds": "link", "Data coverage": "success", "Newest": "newest"}


# The profile table without the theme's field border; rows and the empty area
# keep the Treeview background. Headings use the regular Treeview.Heading style.
PROFILE_TREE_STYLE = "MarbleScapeProfiles.Treeview"
PROFILE_TREE_LAYOUT = [("Treeview.background", {"sticky": "nswe", "children": [
    ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]}),
]})]


# The Status column's default width: every status fits, e.g. "ACTIVE · NETWORK" or
# "999.9 MiB". Like any column it can be dragged narrower; the text is then cut.
STATUS_COLUMN_WIDTH = 130


def _image_updates_enabled(settings):
    """Imagery updates > Check for and download newer images of a profile's settings."""
    return (settings.get("source") or {}).get("check_for_updates", True) is not False


# Seconds of each rotation unit, and the hint below a rotation under 2 minutes.
_ROTATION_UNIT_SECONDS = {"minutes": 60, "hours": 3_600, "days": 86_400, "weeks": 7 * 86_400,
                          "months": 30 * 86_400}


def short_rotation_hint(interval, unit):
    """The hint below Change every, or "" for a rotation of 2 minutes or more."""
    try:
        seconds = int(str(interval).strip()) * _ROTATION_UNIT_SECONDS[str(unit)]
    except (KeyError, ValueError):
        return ""
    if seconds >= 120:
        return ""
    # The next picture loads one minute, or half a shorter interval, before the switch.
    lead = int(min(60, seconds / 2))
    return (f"Very short: the next picture is prepared only {lead} seconds before the switch; "
            "large pictures may switch late.")


def profile_status(identifier, runtime):
    """(symbol, text) of the symbol and Status columns: a running download of this
    profile, else CHECK, QUEUE, ACTIVE, a failure or blank.

    A running download shows only its progress (RENDER while Copernicus computes
    the image), so the column stays narrow; the footer names it in full.
    """
    download = runtime.get("download") or {}
    if download.get("profile_id") == identifier:
        percent = download.get("percent")
        expected = download.get("expected_requests")
        transferred = download.get("transferred") or 0
        if percent is not None:
            text = f"{percent:.0f}%"
        elif expected and expected > 1:
            text = f"{download.get('finished_requests', 0)}/{expected}"
        elif not transferred and download.get("source") == "copernicus":
            # The Process API renders before sending any byte.
            return RENDER_SYMBOL, "RENDER"
        else:
            text = _format_bytes(transferred) if transferred else "DOWNL"
        return DOWNLOAD_SYMBOL, text
    if identifier == runtime.get("checking"):
        # Asking the provider whether a newer picture exists.
        return ACTIVITY_SYMBOL, "CHECK"
    if identifier in runtime.get("queued", ()):
        return QUEUE_SYMBOL, "QUEUE"
    active = identifier == runtime.get("active_profile_id")
    # The last attempt failed (LOST, SOURCE, NETWORK, UNAVAIL), until the next success:
    # its symbol shows instead of the active one.
    failure = (runtime.get("failures") or {}).get(identifier)
    if failure:
        return FAILURE_SYMBOLS.get(failure, ""), f"ACTIVE · {failure}" if active else failure
    return (ACTIVE_SYMBOL, "ACTIVE") if active else ("", "")


def profile_status_text(identifier, runtime):
    """The Status column's text (see profile_status)."""
    return profile_status(identifier, runtime)[1]


def _heading_text(column):
    """A heading's text: the symbol column's stays empty."""
    return "" if column == STATUS_SYMBOL_COLUMN else PROFILE_LIST_COLUMN_LABELS[column]


SHORT_ID_LENGTH = 8


def _short_id(identifier):
    """The first characters of an ID, enough to tell profiles apart at a glance."""
    return identifier[:SHORT_ID_LENGTH]


def _text(value, fallback="-"):
    return str(value).strip() if value is not None and str(value).strip() else fallback


def _source_resolution(value):
    value = _text(value)
    return ({"auto": "Automatic", "largest": "Largest available"}.get(value, value))


def _copernicus_labels(profile):
    configuration = profile.get("configuration", "")
    product_id = profile.get("product", "")
    layer_id = profile.get("layer", "")
    highlight_id = profile.get("highlight", "")
    theme = get_theme(configuration)
    product = get_product(configuration, product_id)
    layer = get_layer(product, layer_id)
    highlight = get_highlight(configuration, highlight_id) if highlight_id else None
    return (
        _text(theme.get("name") if theme else configuration),
        _text(product.get("name") if product else product_id),
        _text(layer.get("name") if layer else layer_id),
        _text(highlight.get("name") if highlight else ""),
    )


# Selected profile details: what the picture shows (left), how it is sized and
# placed (right), then its time, cache and coverage across the full width. A
# row whose value is "" does not apply to the source and is hidden.
DETAIL_LEFT_ROWS = (
    ("image_source", "Image source"), ("configuration", "Configuration"),
    ("area", "Area / location"), ("product", "Product"), ("layer", "Layer"),
    ("highlight", "Example scene"), ("quarter_selection", "Period selection"),
)
DETAIL_RIGHT_ROWS = (
    ("source_resolution", "Source resolution"), ("output_resolution", "Output resolution"),
    ("fit_zoom", "Fit mode / zoom"), ("wallpaper_position", "Wallpaper position"),
    ("auto_recommendation", "Auto recommendation"), ("auto_priority", "Priority"),
    ("auto_precise", "Precise check"),
)
DETAIL_WIDE_ROWS = (
    ("time_utc", "Acquisition time (UTC)"), ("cache", "Cache status"),
    ("data_coverage", "Data coverage"), ("auto_choice", "Auto choice"),
)
# Shown with "-" while no profile is selected; the others only when they apply.
DETAIL_ALWAYS_SHOWN = {"image_source", "area", "product", "source_resolution", "output_resolution",
                       "wallpaper_position", "time_utc", "cache"}
DETAIL_SOURCE_NAMES = {
    "eumetsat": "EUMETSAT", "goes_east": "NOAA GOES-East", "goes_west": "NOAA GOES-West",
    "solar": "NOAA Solar / Sun (SUVI)", "himawari": "Himawari", "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser", "worldview": "NASA Worldview",
}


def _auto_recommendation_values(settings):
    """(Yes/No, priority, precise check) of a Copernicus profile's Use auto recommendation.

    None for other sources. Priority and precise check are "" while the rule is
    off; precise check is "" for mosaics, which have no such option.
    """
    if settings.get("source", {}).get("provider") != "copernicus":
        return None
    profile = settings.get("sources", {}).get("copernicus", {})
    if not profile.get("auto_recommendation", False):
        return "No", "", ""
    from marblescape_copernicus_advice import priority_label
    product = get_product(profile.get("configuration"), profile.get("product"))
    layer = get_layer(product, profile.get("layer")) or {}
    mosaic = "date_granularity" in layer
    precise = "" if mosaic else "Yes" if profile.get("auto_precise", False) else "No"
    return "Yes", priority_label(profile.get("auto_priority", "fewest_clouds"), mosaic), precise


def _capitalized(value):
    """"fit" -> "Fit"; keeps "-" and the rest of the text as it is."""
    text = _text(value)
    return text[:1].upper() + text[1:]


def _enabled_wms_layers(settings):
    return [entry for entry in settings.get("layers", [])
            if isinstance(entry, dict) and entry.get("kind") == "wms"
            and entry.get("enabled", True)]


def _profile_location(settings):
    provider = settings.get("source", {}).get("provider", "eumetsat")
    profile = settings.get("sources", {}).get(provider, {})
    if provider == "copernicus":
        highlight_id = profile.get("highlight")
        if highlight_id:
            highlight = get_highlight(profile.get("configuration", ""), highlight_id)
            if highlight:
                return _text(highlight.get("name"))
        return "Custom Lat/Long"
    if provider == "eumetsat":
        view = settings.get("view", {})
        preset_id = view.get("preset")
        if preset_id == "custom" and _custom_area_center(settings) is None and isinstance(
                view.get("bbox"), (list, tuple)):
            return "Custom area · " + ", ".join(_format_number(value) for value in view["bbox"])
        return _PRESET_LABELS.get(preset_id, _text(preset_id))
    if provider == "slider":
        satellite, separator, sector = str(profile.get("area", "")).partition("---")
        return f"{_text(satellite)} · {_text(sector)}" if separator else _text(profile.get("area"))
    if provider == "worldview":
        return "Global"
    if provider == "solar":
        return _text(profile.get("area"), "Sun")
    return _text(profile.get("area"))


def _custom_area_center(settings):
    """The (latitude, longitude) centre of an EUMETSAT Geographic Custom area, or None."""
    view = settings.get("view", {})
    bbox = view.get("bbox")
    if (settings.get("source", {}).get("provider", "eumetsat") != "eumetsat"
            or view.get("preset") != "custom" or view.get("projection") != "Geographic"
            or not isinstance(bbox, (list, tuple)) or len(bbox) != 4):
        return None
    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError, OverflowError):
        return None
    return round((south + north) / 2.0, 4), round((west + east) / 2.0, 4)


def _himawari_centered(settings, profile):
    """A Himawari full disk centred on its saved coordinates."""
    return (settings.get("source", {}).get("provider") == "himawari" and profile.get("center") is True
            and profile.get("area") in ("nict_full_disk", "nict_full_disk_bands"))


def _profile_latitude(settings):
    provider = settings.get("source", {}).get("provider", "eumetsat")
    center = _custom_area_center(settings)
    if center is not None:
        return _format_coordinate(center[0])
    profile = settings.get("sources", {}).get(provider, {})
    if provider != "copernicus" and not _himawari_centered(settings, profile):
        return "-"
    return _format_coordinate(profile.get("latitude"))


def _profile_longitude(settings):
    provider = settings.get("source", {}).get("provider", "eumetsat")
    center = _custom_area_center(settings)
    if center is not None:
        return _format_coordinate(center[1])
    profile = settings.get("sources", {}).get(provider, {})
    if provider != "copernicus" and not _himawari_centered(settings, profile):
        return "-"
    return _format_coordinate(profile.get("longitude"))


def _profile_coverage(settings):
    provider = settings.get("source", {}).get("provider", "eumetsat")
    profile = settings.get("sources", {}).get(provider, {})
    if provider == "copernicus":
        mode = profile.get("coverage_mode", "fill_gaps")
        # Gap fill "black" is now Single latest acquisition with a black No-data color.
        if mode in {"single", "black"}:
            return "Single latest acquisition"
        if mode == "fill_gaps":
            return f"Gap fill · {_text(profile.get('lookback_days', 14))} days"
    elif provider == "eumetsat" and profile.get("fill_gaps"):
        return f"Gap fill · {_text(profile.get('gap_fill_lookback_hours', 12))} h"
    return "-"


def _profile_copernicus_percentage(settings, field):
    if settings.get("source", {}).get("provider") != "copernicus":
        return "-"
    profile = settings.get("sources", {}).get("copernicus", {})
    if field == "brightness":
        # Brightness correction only applies to layers with a tone rule.
        product = get_product(profile.get("configuration"), profile.get("product"))
        if tone_rule(get_layer(product, profile.get("layer"))) is None:
            return "-"
    default = 30 if field == "max_cloud_cover" else 100
    return f"{profile.get(field, default)}%"


def _fixed_profile_date(settings):
    provider = settings.get("source", {}).get("provider", "eumetsat")
    if provider == "copernicus":
        value = settings.get("sources", {}).get(provider, {}).get("date", "latest")
        return None if str(value).casefold() == "latest" else str(value)[:10]
    if provider == "worldview":
        value = settings.get("sources", {}).get(provider, {}).get("product", "latest")
        return None if str(value).casefold() in {"latest", "timeless"} else str(value)[:10]
    if provider == "eumetsat":
        dates = []
        for layer in _enabled_wms_layers(settings):
            value = str(layer.get("time", "")).strip()
            if value and value.casefold() != "latest":
                dates.append(value[:10])
        if dates:
            return dates[0]
    return None


def _format_source_time(value, time_zone="utc"):
    if not value:
        return None
    try:
        return format_display_datetime(value, time_zone)
    except (TypeError, ValueError, OverflowError):
        return None


def _profile_time(settings, metadata, time_zone="utc"):
    """(Time selection, Time): how the date is chosen, and the date or time it gives."""
    provider = settings.get("source", {}).get("provider", "eumetsat")
    if provider == "copernicus":
        profile = settings.get("sources", {}).get("copernicus", {})
        if profile.get("date_mode") in {"relative_quarter", "relative_month"}:
            monthly = profile["date_mode"] == "relative_month"
            unit = "month" if monthly else "quarter"
            offset = profile.get("month_offset" if monthly else "quarter_offset", 0)
            today = (dt.datetime.now(dt.timezone.utc).date()
                     if time_zone == "utc" else dt.date.today())
            try:
                start = rolling_month_start(today, offset) if monthly else rolling_quarter_start(today, offset)
            except ValueError:
                return "Rolling", "invalid offset"
            back = (f"current {unit}" if offset == 0
                    else f"{offset} {unit}{'s' if offset != 1 else ''} back")
            return (f"Rolling · {back}",
                    f"{start:%Y-%m}" if monthly else f"{start.year} Q{(start.month - 1) // 3 + 1}")
    if (provider == "worldview" and str(
            settings.get("sources", {}).get(provider, {}).get("product", "")
    ).casefold() == "timeless"):
        return "Timeless", "-"
    fixed = _fixed_profile_date(settings)
    if fixed:
        return "Fixed", fixed
    loaded = _format_source_time((metadata or {}).get("source_time"), time_zone)
    return "Latest", loaded or "not loaded yet"


def _profile_quarter_values(settings, time_zone="utc"):
    if settings.get("source", {}).get("provider") != "copernicus":
        return "-", "-", "-"
    profile = settings.get("sources", {}).get("copernicus", {})
    product = get_product(profile.get("configuration", ""), profile.get("product", ""))
    layer = get_layer(product, profile.get("layer", "")) if product else None
    if not layer or layer.get("date_granularity") not in {"quarter", "month"}:
        return "-", "-", "-"
    monthly = layer["date_granularity"] == "month"
    unit = "month" if monthly else "quarter"
    if profile.get("date_mode") in {"relative_quarter", "relative_month"}:
        offset = profile.get("month_offset" if monthly else "quarter_offset", 0)
        today = (dt.datetime.now(dt.timezone.utc).date()
                 if time_zone == "utc" else dt.date.today())
        try:
            target = rolling_month_start(today, offset) if monthly else rolling_quarter_start(today, offset)
        except ValueError:
            return "Relative to now", f"-{offset}Q", "Invalid offset"
        label = f"Current {unit}" if offset == 0 else f"{offset} {unit}{'s' if offset != 1 else ''} ago"
        return ("Relative to now", label,
                f"{target:%Y-%m}" if monthly else f"{target.year} Q{(target.month - 1) // 3 + 1}")
    date = str(profile.get("date", "latest"))
    if date == "latest":
        return "Latest available", "-", "Latest available"
    try:
        selected = dt.date.fromisoformat(date[:10])
    except ValueError:
        return f"Specific {unit}", "-", "Invalid date"
    return f"Specific {unit}", "-", (f"{selected:%Y-%m}" if monthly else f"{selected.year} Q{(selected.month - 1) // 3 + 1}")


def _format_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return _text(value)
    return f"{number:g}" if math.isfinite(number) else _text(value)


def _format_coordinate(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return _text(value)
    if not math.isfinite(number):
        return _text(value)
    return f"{number:.8f}".rstrip("0").rstrip(".")


def _render_quality_text(scale):
    """EUMETSAT's render quality: "Default (General)", "auto" or a factor such as "1.5×"."""
    if scale == "default":
        return "Default (General)"
    return "auto" if scale == "auto" else f"{_format_number(scale)}×"


def _profile_resolution(settings):
    """The profile's own detail choice; the output size is a device setting."""
    provider = settings.get("source", {}).get("provider")
    if provider == "eumetsat":
        return "Render quality " + _render_quality_text(settings.get("output", {}).get("render_scale", "auto"))
    key = "image_size" if provider == "copernicus" else "resolution"
    selection = settings.get("sources", {}).get(provider, {})
    value = _source_resolution(selection.get(key, "auto"))
    if provider == "slider":
        # SLIDER sizes are square tile grids; show the visible size without padding.
        value = _slider_effective_resolution(selection.get("area", ""), value)
    return value.replace("x", " × ") if re.fullmatch(r"\d+x\d+", value) else value


def _format_bytes(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return "unknown size"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if number < 1024 or unit == "GiB":
            return f"{number} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024


def _cache_status(metadata, time_zone="system"):
    if not metadata:
        return "Not cached"
    dimensions = f"{metadata.get('width', '?')} × {metadata.get('height', '?')}"
    saved = _format_source_time(metadata.get("updated_at"), time_zone)
    result = f"Cached · {dimensions} · {_format_bytes(metadata.get('bytes'))}"
    return result + (f" · saved {saved}" if saved else "")


def _profile_zoom(settings):
    """Only the number: Copernicus' map zoom, otherwise the image zoom."""
    if settings.get("source", {}).get("provider") == "copernicus":
        value = settings.get("sources", {}).get("copernicus", {}).get("map_zoom")
    else:
        value = settings.get("view", {}).get("zoom")
    return _format_number(value) if value is not None else "-"


def _profile_lookback(settings):
    """Return display text and a comparable duration in hours (or None)."""
    provider = settings.get("source", {}).get("provider", "eumetsat")
    profile = settings.get("sources", {}).get(provider, {})
    if provider == "copernicus":
        product = get_product(profile.get("configuration", ""), profile.get("product", ""))
        layer = get_layer(product, profile.get("layer", "")) if product else None
        if layer and "date_granularity" in layer:
            return "-", None  # Precomputed mosaics do not use a lookback window.
        value, unit, factor = profile.get("lookback_days", 14), "days", 24
    elif provider == "eumetsat":
        value, unit, factor = profile.get("gap_fill_lookback_hours", 12), "h", 1
    else:
        return "-", None
    number = _numeric_value(value)
    return (f"{_format_number(value)} {unit}", number * factor) if number is not None else ("-", None)


def _profile_map_overlay(settings, key):
    """Copernicus map overlay as "On · #RRGGBB" or "Off"; older selections drew
    the country borders like their labels."""
    if settings.get("source", {}).get("provider") != "copernicus":
        return "-"
    profile = settings.get("sources", {}).get("copernicus", {})
    labels = profile.get("map_labels", True), profile.get("map_label_color", "#000000")
    enabled, color = (labels if key == "labels" else
                      (profile.get("map_borders", labels[0]), profile.get("map_border_color", labels[1])))
    return f"On · {_text(color).upper() if color != 'transparent' else 'transparent'}" if enabled else "Off"


def _profile_shorelines(settings):
    """Himawari's Plot shorelines as "On · #RRGGBB" or "Off"; a dash for other sources."""
    if settings.get("source", {}).get("provider") != "himawari":
        return "-"
    profile = settings.get("sources", {}).get("himawari", {})
    if not str(profile.get("area", "")).startswith(("nict_", "jma_storm_")):
        return "-"  # JMA's regional stills have no shoreline layer.
    return f"On · {_text(profile.get('shoreline_color', '#FFFF00')).upper()}" if profile.get("shorelines") else "Off"


def _native_menu_corner():
    """Screen (x, y) of the top left corner of the Windows menu under the pointer, or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        window = user32.WindowFromPoint(point)
        name = ctypes.create_unicode_buffer(16)
        # "#32768" is the window class of every Windows popup menu.
        if not window or not user32.GetClassNameW(window, name, 16) or name.value != "#32768":
            return None
        rect = wintypes.RECT()
        return (rect.left, rect.top) if user32.GetWindowRect(window, ctypes.byref(rect)) else None
    except (AttributeError, OSError, ValueError):
        return None


def _column_min_width(column):
    """The narrowest a column can be dragged; saved narrower widths show this wide."""
    return (80 if column == "id"
            else CHECKBOX_COLUMN_MIN_WIDTH if column in NARROW_COLUMNS else 45)


def _numeric_value(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _natural_key(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", str(value).casefold()))


def _period_month(value):
    match = re.fullmatch(r"(\d{4}) (?:Q)([1-4])", value)
    if match:
        return int(match[1]) * 12 + (int(match[2]) - 1) * 3
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if match and 1 <= int(match[2]) <= 12:
        return int(match[1]) * 12 + int(match[2]) - 1
    return None


def _timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


import marblescape_snapshot as latest_snapshot
from marblescape_data_coverage import label as coverage_label


class ProfilesSettings:
    def __init__(self, parent, library, capture_settings, on_load,
                 on_apply=None, status=None, visible_columns=None,
                 normalize_settings=None, import_defaults=None, export_locations=None,
                 sort_column="", sort_descending=False, column_widths=None, column_order=None,
                 system_snapshot=None, history_directory=None, on_profile_rename=None,
                 history_enabled=None, history_usage=None, on_history_delete=None, on_save=None,
                 on_history_toggle=None, on_layout_change=None, on_force_load=None,
                 table_rows=DEFAULT_PROFILE_TABLE_ROWS, on_check=None, on_saved=None,
                 on_image_updates=None, suggest_name=None, catalogue_entry=None):
        if sort_column not in ("", *PROFILE_LIST_COLUMNS) or type(sort_descending) is not bool:
            raise ValueError("Invalid profile table sorting settings.")
        self._sort_column = sort_column
        self._column_order = normalize_profile_column_order(column_order)
        self._table_rows = normalize_profile_table_rows(table_rows)
        self._heading_drag = None
        self._sort_descending = sort_descending if sort_column else False
        column_widths = normalize_profile_column_widths(column_widths)
        self._library = normalize_library(library)
        self._saved_history_names = {item["id"]: item["name"] for item in self._library["items"]}
        self._rotation_ids = set(self._library["rotation"]["order"])
        self._system_snapshot = system_snapshot
        self._snapshot = system_snapshot() if system_snapshot is not None else None
        self._capture_settings = capture_settings
        # catalogue_entry(provider, item_id, area_id=None) -> cached entry or None;
        # gives the details readable names instead of catalogue IDs.
        self._catalogue_entry = catalogue_entry
        self._row_entries = {}  # (provider, id, area) -> cached catalogue entry, per refresh
        self._on_load = on_load
        self._on_apply = on_apply
        self._on_force_load = on_force_load
        # on_check(identifiers) -> (queued IDs, whether the shown profile is checked).
        self._on_check = on_check
        # Serial of the last "Check for new image" summary already handled.
        self._check_summary_serial = None
        # Serial of the last rotation switch made outside this window (tray menu).
        self._rotation_switch_serial = None
        self._status = status
        self._normalize_settings = normalize_settings
        self._import_defaults = import_defaults
        self._export_locations = export_locations
        self._history_directory = history_directory
        self._on_profile_rename = on_profile_rename
        self._history_usage = history_usage
        self._on_history_delete = on_history_delete
        # Persists every profile-list change immediately (profiles.toml).
        self._on_save = on_save
        # on_history_toggle(identifiers, enabled) saves History switches at once.
        self._on_history_toggle = on_history_toggle
        # on_image_updates(identifiers, enabled, library) saves switched Updates
        # checkboxes; without it the profile list is saved as for other changes.
        self._on_image_updates = on_image_updates
        # suggest_name() proposes the name of a new profile: the one the Image tab shows.
        self._suggest_name = suggest_name
        # on_layout_change() saves the table's column layout at once.
        self._on_layout_change = on_layout_change
        # on_saved() confirms a saved profile-list change (the Settings footer's "✓ Saved").
        self._on_saved = on_saved
        # Describes the latest saved change; it is not shown, the footer confirms it.
        self.last_saved_change = ""
        # True while rotation inputs are set by code rather than by the user.
        self._quiet = False
        # Profile id -> name whose History images go once the deletion is saved.
        self._pending_history_deletes = {}
        # Local History switch per profile (the snapshot row uses no-profile History).
        self._history_enabled = history_enabled
        self._closed = False
        self._after_id = None
        self._runtime = {"active_profile_id": None, "profiles": {}, "download": None,
                         "wallpaper_position": "-", "output_resolution": "-",
                         "display_time_zone": "system", "checking": None}
        by_id = {item["id"]: item for item in self._library["items"]}
        order = self._library["rotation"]["order"]
        self._items = [by_id[identifier] for identifier in order]
        self._items += [item for item in self._library["items"] if item["id"] not in order]
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.bind("<Destroy>", self._on_destroy, add="+")
        rotation = self._library["rotation"]
        self.enabled_var = tk.BooleanVar(master=self.frame, value=rotation["enabled"])
        self.interval_var = tk.StringVar(master=self.frame, value=str(rotation["interval"]))
        self.unit_var = tk.StringVar(master=self.frame, value=rotation["unit"])
        self.random_shuffle_var = tk.BooleanVar(
            master=self.frame, value=rotation["random_shuffle"]
        )
        self.keep_last_position_var = tk.BooleanVar(
            master=self.frame, value=rotation["keep_last_position"]
        )
        self.preload_next_var = tk.BooleanVar(master=self.frame, value=rotation["preload_next"])
        self.status_var = tk.StringVar(master=self.frame)
        # Kept apart from status_var, which the rotation status poll rewrites.
        self.notice_var = tk.StringVar(master=self.frame)
        self.detail_vars = {
            key: tk.StringVar(master=self.frame, value="-")
            for key, _label in (*DETAIL_LEFT_ROWS, *DETAIL_RIGHT_ROWS, *DETAIL_WIDE_ROWS)
        }

        # The Settings footer says that this tab saves without Save.
        table_header = ttk.Frame(self.frame)
        table_header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        table_header.columnconfigure(4, weight=1)
        # Shows only rows whose Profile name contains the text; nothing is saved.
        self.filter_var = tk.StringVar(master=self.frame)
        self._hidden_rows = set()
        self.filter_var.trace_add("write", self._filter_changed)
        self.filter_label = ttk.Label(table_header, text="Filter")
        self.filter_label.grid(row=0, column=1, padx=(0, 6), sticky="w")
        self.filter_entry = ttk.Entry(table_header, textvariable=self.filter_var, width=1)
        self.filter_entry.grid(row=0, column=2, sticky="ew")
        self.filter_placeholder = _entry_placeholder(self.filter_entry, self.filter_var, "Filters profile names.")
        self.filter_clear_button = ttk.Button(table_header, text="Clear", command=lambda: self.filter_var.set(""))
        self.filter_clear_button.grid(row=0, column=3, padx=(6, 0), sticky="w")
        # Filter, field and Clear end at half the header's width at the minimum
        # window width, and keep that width in a wider window.
        self._table_header = table_header
        table_header.bind("<Configure>", lambda _event: self._fit_filter_width(), add="+")
        self.columns_button = ttk.Button(table_header, text="Columns", command=self._show_column_menu)
        self.columns_button.grid(row=0, column=5, sticky="e")
        list_frame = ttk.Frame(self.frame)
        list_frame.grid(row=1, column=0, sticky="nsew")
        # Now showing (profile on screen, rotation, preload) in a frame across
        # the window right below the table; three lines stay reserved, so
        # nothing below jumps.
        status_frame = ttk.LabelFrame(self.frame, text="Now showing", padding=8)
        status_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        status_frame.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, wraplength=650, justify="left")
        self.status_label.grid(row=0, column=0, sticky="nw")
        reserve_text_lines(self.status_label, lines=3)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_frame, columns=PROFILE_LIST_COLUMNS, show="headings",
                                 selectmode="extended", height=self._table_rows)
        # Layouts belong to one ttk theme: drop the border again after each appearance change.
        tree_style = ttk.Style(self.tree)
        _on_theme_change(self.tree, lambda: tree_style.layout(PROFILE_TREE_STYLE, PROFILE_TREE_LAYOUT))
        self.tree.configure(style=PROFILE_TREE_STYLE)
        for column in PROFILE_LIST_COLUMNS:
            self.tree.heading(column, text=_heading_text(column),
                              command=lambda selected=column: self.sort_by(selected))
            minimum = _column_min_width(column)
            self.tree.column(column, width=max(column_widths[column], minimum),
                             minwidth=minimum, stretch=False,
                             anchor="center" if column in {"status_symbol", "active", "history",
                                                           "image_updates"} else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self._visible_columns = self._normalize_visible_columns(visible_columns)
        self._visible_columns = tuple(key for key in self._column_order if key in self._visible_columns)
        self.tree.configure(displaycolumns=self._visible_columns)
        self._column_visibility_vars = {
            column: tk.BooleanVar(master=self.frame, value=column in self._visible_columns)
            for column in PROFILE_LIST_COLUMNS
        }
        self._column_menu = tk.Menu(self.tree, tearoff=False)
        # Tests switch columns without a menu on screen.
        self.reopen_column_menu = True
        # The menu's top left corner while it is open, so it opens again in its place.
        self._column_menu_corner = None
        self._column_menu.bind("<<MenuSelect>>", self._remember_column_menu_corner)
        self._rebuild_column_menu()
        # The check marks are images in the mode's text color, so they follow it.
        _on_theme_change(self.tree, self._rebuild_column_menu)
        # Frequent actions first, then Edit, copying, rotation and history, and
        # transfer and columns last (most also have buttons below the table or keys).
        self._cell_menu = tk.Menu(self.tree, tearoff=False)
        self._edit_menu = tk.Menu(self._cell_menu, tearoff=False)
        self._transfer_menu = tk.Menu(self._cell_menu, tearoff=False)
        self._submenus = {"Edit": self._edit_menu, "Import / Export": self._transfer_menu}
        self._menu_of = {}  # Entry label -> the menu that holds it.
        # The switches show the selected rows' value: checked when it is on for all of them.
        self._toggle_vars = {label: tk.BooleanVar(self.tree, False)
                             for label in ("Toggle updates", "Toggle history", "Toggle rotation")}
        for menu, entries in (
            (self._cell_menu, (
                ("Apply", self.apply_selected, None),
                ("Load", self.load_selected, None),
                ("Update", self.update_selected, None),
                ("Check for new image", self.check_selected, None),
                ("Force loading new image", self.force_load_selected, None),
                None,
                "Edit",
                None,
                ("Copy cell", self._copy_context_cell, None),
                ("Copy row", self._copy_selected_row, "Ctrl+C"),
                None,
                ("Toggle updates", self.toggle_image_updates_selected, None),
                ("Toggle history", self.toggle_history_selected, None),
                ("Toggle rotation", self.toggle_rotation_selected, None),
                None,
                ("Open profile history folder", self.open_history_folder, None),
                None,
                "Import / Export",
                "Columns",
            )),
            (self._edit_menu, (
                ("Rename", self.rename_selected_from_dialog, None),
                ("Duplicate", self.duplicate_selected, None),
                ("Delete", self.delete_selected, "Del"),
                None,
                ("Move up", lambda: self.move_selected(-1), "Ctrl+Up"),
                ("Move down", lambda: self.move_selected(1), "Ctrl+Down"),
            )),
            (self._transfer_menu, (
                ("Import profile", self.import_selected, None),
                ("Export profile", self.export_selected, None),
            )),
        ):
            for entry in entries:
                if entry is None:
                    menu.add_separator()
                    continue
                if isinstance(entry, str):
                    menu.add_cascade(label=entry, menu=self._submenus.get(entry, self._column_menu))
                    continue
                label, command, accelerator = entry
                if label in self._toggle_vars:
                    menu.add_checkbutton(label=label, command=command, variable=self._toggle_vars[label],
                                         accelerator=accelerator or "")
                else:
                    menu.add_command(label=label, command=command, accelerator=accelerator or "")
                self._menu_of[label] = menu
        self._style_cell_menu_checks()
        _on_theme_change(self.tree, self._style_cell_menu_checks)
        self._context_item = None
        self._context_column = None
        # Drag the grip below the table to change its height in whole rows.
        self.table_grip = ttk.Frame(list_frame, height=8, cursor="sb_v_double_arrow")
        self.table_grip.grid(row=2, column=0, columnspan=2, sticky="ew")
        # A subtle line marks the grip; it handles the mouse like the grip.
        self.table_grip_line = ttk.Separator(self.table_grip, orient="horizontal",
                                             cursor="sb_v_double_arrow")
        self.table_grip_line.place(relx=0, rely=0.5, relwidth=1)
        for widget in (self.table_grip, self.table_grip_line):
            widget.bind("<ButtonPress-1>", self._grip_press)
            widget.bind("<B1-Motion>", self._grip_motion)
            widget.bind("<ButtonRelease-1>", self._grip_release)
            widget.bind("<Double-1>", self._grip_reset)
        self._grip_drag = None
        self._list_frame, self._horizontal_scrollbar = list_frame, horizontal
        list_frame.grid_propagate(False)
        self._apply_table_rows()
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._apply_double_clicked)
        self.tree.bind("<Button-3>", self._show_tree_menu)
        self._row_drag = None
        self._drop_line = None
        self._column_line = None
        self._saved_widths = self.get_column_widths()
        # First in the release chain: later handlers may end it with "break".
        self.tree.bind("<ButtonRelease-1>", self._check_column_widths, add="+")
        self.tree.bind("<ButtonPress-1>", self._heading_press, add="+")
        self.tree.bind("<ButtonPress-1>", self._row_press, add="+")
        self.tree.bind("<B1-Motion>", self._heading_motion, add="+")
        self.tree.bind("<B1-Motion>", self._row_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._heading_release, add="+")
        # Before the Rotation checkbox handler, so a finished drag never toggles it.
        self.tree.bind("<ButtonRelease-1>", self._row_release, add="+")
        self.tree.bind("<ButtonRelease-1>", self._toggle_rotation_clicked, add="+")
        self.tree.bind("<Control-c>", self._copy_selected_row)
        self.tree.bind("<Control-C>", self._copy_selected_row)
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        self.tree.bind("<Delete>", self._delete_key)
        self.tree.bind("<Control-Up>", lambda _event: self._move_key(-1))
        self.tree.bind("<Control-Down>", lambda _event: self._move_key(1))
        self.tree.bind("<Escape>", self._escape_key)
        self._search_prefix = ""
        self._search_time = 0.0
        self.tree.bind("<KeyPress>", self._type_to_select)
        self.tree.bind("<FocusOut>", self._reset_type_search, add="+")
        self.tree.bind("<ButtonPress-1>", self._reset_type_search, add="+")

        details = ttk.LabelFrame(self.frame, text="Selected profile details", padding=7)
        details.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        # Two equal halves: the left rows, and the right rows in their own frame
        # so a hidden row leaves no gap; the wide rows span both below them.
        details.columnconfigure(1, weight=1, uniform="profile_detail_halves")
        details.columnconfigure(2, weight=1, uniform="profile_detail_halves")
        right = ttk.Frame(details)
        right.grid(row=0, column=2, rowspan=len(DETAIL_LEFT_ROWS), sticky="nw")
        self._detail_rows = {}
        self._detail_wrap = {}
        for parent, rows, first_row, column in ((details, DETAIL_LEFT_ROWS, 0, 0),
                                                (right, DETAIL_RIGHT_ROWS, 0, 0),
                                                (details, DETAIL_WIDE_ROWS, len(DETAIL_LEFT_ROWS), 0)):
            for offset, (key, text) in enumerate(rows):
                label = ttk.Label(parent, text=text)
                label.grid(row=first_row + offset, column=column, padx=(0, 6), pady=2, sticky="nw")
                value = ttk.Label(parent, textvariable=self.detail_vars[key], justify="left")
                wide = rows is DETAIL_WIDE_ROWS
                value.grid(row=first_row + offset, column=column + 1, columnspan=2 if wide else 1,
                           padx=(0, 0 if wide or parent is right else 14), pady=2, sticky="nw")
                self._detail_rows[key] = (label, value)
                self._detail_wrap[key] = "wide" if wide else "half"
        self.detail_value_labels = [value for _label, value in self._detail_rows.values()]

        button_frame = ttk.Frame(self.frame)
        button_frame.grid(row=4, column=0, sticky="ew", pady=(9, 8))
        # Create on the left in its natural width (one width for all would not
        # fit the minimum window width), then Apply, Load, Update, Rename and
        # Delete equally wide; Import and Export, equally wide, on the right.
        button_frame.columnconfigure(6, weight=1)
        for column in (1, 2, 3, 4, 5):
            button_frame.columnconfigure(column, uniform="profile_edit_buttons")
        for column in (7, 8):
            button_frame.columnconfigure(column, uniform="profile_transfer_buttons")
        self.buttons = {}
        # Move lives in the table's context menu; rows are reordered by dragging
        # or Ctrl+Up/Down. Apply, Load, Update and Rename are in both.
        actions = (
            ("Create Profile from Image", self.add_current, 0),
            ("Apply", self.apply_selected, 1),
            ("Load", self.load_selected, 2),
            ("Update", self.update_selected, 3),
            ("Rename", self.rename_selected_from_dialog, 4),
            ("Delete", self.delete_selected, 5),
            ("Import profile", self.import_selected, 7),
            ("Export profile", self.export_selected, 8),
        )
        for label, callback, column in actions:
            button = ttk.Button(button_frame, text=label, command=callback)
            button.grid(row=0, column=column, padx=3, pady=3, sticky="ew")
            self.buttons[label] = button
        # Describes the latest action (Force loading, Check for new image); the
        # green "✓ Saved" of a saved change is in the Settings footer (on_saved).
        notice_frame = ttk.Frame(button_frame)
        notice_frame.grid(row=1, column=0, columnspan=9, sticky="ew", padx=3, pady=(5, 0))
        notice_frame.columnconfigure(0, weight=1)
        self.notice_label = ttk.Label(notice_frame, textvariable=self.notice_var, wraplength=630, justify="left")
        self.notice_label.grid(row=0, column=0, sticky="w")
        self.notice_label.grid_remove()

        rotation_frame = ttk.LabelFrame(self.frame, text="Rotation", padding=8)
        rotation_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(rotation_frame, text="Rotate through the profiles in the saved rotation order",
                        variable=self.enabled_var).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        # Own row frame: the wide texts spanning the columns must not spread
        # the interval controls away from their label.
        interval_block = ttk.Frame(rotation_frame)
        interval_block.grid(row=1, column=0, columnspan=3, sticky="w")
        interval_frame = ttk.Frame(interval_block)
        interval_frame.grid(row=0, column=0, sticky="w")
        ttk.Label(interval_frame, text="Change every").grid(row=0, column=0, padx=(0, 10), sticky="w")
        ttk.Combobox(
            interval_frame, textvariable=self.interval_var,
            values=ROTATION_INTERVAL_CHOICES, state="readonly", width=7,
        ).grid(row=0, column=1, padx=(0, 6), sticky="w")
        ttk.Combobox(
            interval_frame, textvariable=self.unit_var,
            values=ROTATION_INTERVAL_UNITS, state="readonly", width=12,
        ).grid(row=0, column=2, sticky="w")
        # Below 2 minutes, in the warning color: little time to prepare the next picture.
        # A reserved line below the row: the orange hint below 2 minutes. Its own
        # place, so the hint never spreads the controls.
        self.short_rotation_label = ttk.Label(interval_block)
        self.short_rotation_label.grid(row=1, column=0, pady=(2, 0), sticky="w")
        interval_probe = ttk.Label(interval_block, text="Ag")
        interval_block.rowconfigure(1, minsize=interval_probe.winfo_reqheight() + 2)
        interval_probe.destroy()
        for variable in (self.interval_var, self.unit_var):
            variable.trace_add("write", lambda *_args: self._refresh_short_rotation_hint())
        _on_theme_change(self.short_rotation_label, self._refresh_short_rotation_hint)
        ttk.Checkbutton(
            rotation_frame, text="Random shuffle", variable=self.random_shuffle_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            rotation_frame, text="Keep last rotation position",
            variable=self.keep_last_position_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            rotation_frame, text="Load the next picture before the switch",
            variable=self.preload_next_var,
        ).grid(row=4, column=0, columnspan=3, sticky="w")
        for variable in (self.enabled_var, self.interval_var, self.unit_var,
                         self.random_shuffle_var, self.keep_last_position_var,
                         self.preload_next_var):
            variable.trace_add("write", lambda *_args: self._rotation_changed())
        # Table usage help lives in Info > Profile table.
        _on_theme_change(self.tree, self._style_active_row)
        _on_theme_change(self.tree, self._color_auto_priorities)
        self.frame.bind("<Configure>", self._resize_text, add="+")
        self._refresh(self._items[0]["id"] if self._items else None)
        # What profiles.toml holds; a failed save returns the table to it.
        self._saved_state = deepcopy(self._library)
        self._saved_state["items"] = deepcopy(self._items)
        if status is not None:
            self._poll_status()

    @staticmethod
    def _normalize_visible_columns(columns):
        if columns is None:
            return DEFAULT_VISIBLE_PROFILE_COLUMNS
        if not isinstance(columns, (list, tuple)):
            raise ValueError("Profile list columns must be a list.")
        result = tuple(column for column in PROFILE_LIST_COLUMNS if column in columns)
        return result

    def get_visible_columns(self):
        return self._visible_columns

    def get_column_order(self):
        return self._column_order

    def _heading_press(self, event):
        self._heading_drag = None
        if self.tree.identify_region(event.x, event.y) == "heading":
            number = int(self.tree.identify_column(event.x)[1:]) - 1
            if 0 <= number < len(self._visible_columns):
                self._heading_drag = (self._visible_columns[number], event.x, False)
                return "break"  # Own header clicks so a drag cannot also sort.

    def _heading_motion(self, event):
        if self._heading_drag:
            column, start, moved = self._heading_drag
            moved = moved or abs(event.x - start) >= 5
            self._heading_drag = (column, start, moved)
            if moved:
                self.tree.configure(cursor="sb_h_double_arrow")
                width = self.tree.winfo_width()
                first, last = self.tree.xview()
                step = (last - first) / 10
                if event.x < 15 and first > 0:
                    self.tree.xview_moveto(max(0.0, first - step))
                elif event.x > width - 15 and last < 1:
                    self.tree.xview_moveto(first + step)
                self._show_column_line(self._column_gap(event.x))
            return "break"

    def _column_edges(self):
        """Screen x of each visible column's left edge, plus the right edge of the last."""
        widths = [int(self.tree.column(column, "width")) for column in self._visible_columns]
        offset = self.tree.xview()[0] * max(1, sum(widths))
        edges = [-offset]
        for width in widths:
            edges.append(edges[-1] + width)
        return edges

    def _column_gap(self, x):
        """Gap index among the visible columns where a dragged heading lands."""
        edges = self._column_edges()
        for index in range(len(edges) - 1):
            if x < edges[index + 1]:
                return index + (1 if x >= (edges[index] + edges[index + 1]) / 2 else 0)
        return len(edges) - 1

    def _show_column_line(self, gap):
        if self._column_line is None:
            self._column_line = tk.Frame(self.tree, width=2, background="#1a73e8")
        x = self._column_edges()[gap]
        if not 0 <= x <= self.tree.winfo_width():
            self._column_line.place_forget()
            return
        self._column_line.place(x=max(0, x - 1), y=0, width=2, relheight=1)
        self._column_line.lift()

    def _end_column_drag(self):
        self.tree.configure(cursor="")
        if self._column_line is not None:
            self._column_line.place_forget()

    def _heading_release(self, event):
        if not self._heading_drag:
            return None
        column, _start, moved = self._heading_drag
        self._heading_drag = None
        if moved:
            self._end_column_drag()
            self._drop_column(column, self._column_gap(event.x))
        elif self.tree.identify_region(event.x, event.y) == "heading":
            self.sort_by(column)
        return "break"

    def _drop_column(self, column, gap):
        visible = list(self._visible_columns)
        if column not in visible or gap in (visible.index(column), visible.index(column) + 1):
            return
        order = list(self._column_order)
        order.remove(column)
        if gap < len(visible):
            order.insert(order.index(visible[gap]), column)
        else:
            order.insert(order.index(visible[-1]) + 1, column)
        self._column_order = tuple(order)
        self._visible_columns = tuple(key for key in order if key in self._visible_columns)
        self.tree.configure(displaycolumns=self._visible_columns)
        self._rebuild_column_menu()
        self._layout_changed()

    def get_column_widths(self):
        # Read all data columns, not just displaycolumns, so hidden widths survive.
        return normalize_profile_column_widths({
            column: int(self.tree.column(column, "width")) for column in PROFILE_LIST_COLUMNS
        })

    def _toggle_column(self, column):
        visible = bool(self._column_visibility_vars[column].get())
        selected = set(self._visible_columns)
        if visible:
            selected.add(column)
        else:
            selected.discard(column)
        self._visible_columns = tuple(
            candidate for candidate in self._column_order if candidate in selected
        )
        self.tree.configure(displaycolumns=self._visible_columns)
        self._layout_changed()

    def _column_clicked(self, column):
        """Switch a column from the menu; it opens again with that entry under the
        pointer, so several columns can be switched in a row."""
        self._toggle_column(column)
        if self.reopen_column_menu:
            index = self._column_menu_index[column]
            self.tree.after_idle(lambda: self._reopen_column_menu(index))

    def _remember_column_menu_corner(self, _event=None):
        corner = _native_menu_corner()
        if corner is not None:
            self._column_menu_corner = corner

    def _reopen_column_menu(self, index):
        try:
            # Windows puts the menu's top left corner at the given point; Tk's
            # placement of an entry under the pointer misses with native menus.
            if self._column_menu_corner is not None:
                self._column_menu.tk_popup(*self._column_menu_corner)
                return
            x, y = self.tree.winfo_pointerxy()
            self._column_menu.tk_popup(x, y, index)
        except tk.TclError:
            pass  # The window closed meanwhile.

    def _layout_changed(self):
        """Column visibility, order, widths, sorting or table height changed: save the view at once."""
        self._saved_widths = self.get_column_widths()
        if self._on_layout_change is None:
            return
        try:
            self._on_layout_change()
        except Exception as exc:
            self._error(exc)

    def _check_column_widths(self, _event=None):
        # Dragging a column border has no event of its own; compare on release.
        if self.get_column_widths() != self._saved_widths:
            self._layout_changed()

    def _rebuild_column_menu(self):
        """The Columns menu: its areas (COLUMN_MENU_GROUPS), then the two resets."""
        self._column_menu.delete(0, "end")
        self._column_menu_index = {}  # Column -> its entry index in the menu.
        # Windows draws Tk's own check mark black until highlighted; this one
        # always has the text color of the mode.
        unchecked, checked = _menu_check_images(self.tree)
        grouped = {column for group in COLUMN_MENU_GROUPS for column in group}
        groups = (*COLUMN_MENU_GROUPS, tuple(column for column in PROFILE_LIST_COLUMNS if column not in grouped))
        index = 0
        for group in (group for group in groups if group):
            if index:
                self._column_menu.add_separator()
                index += 1
            for column in group:
                self._column_menu.add_checkbutton(
                    label=PROFILE_LIST_COLUMN_LABELS[column],
                    variable=self._column_visibility_vars[column],
                    command=lambda selected=column: self._column_clicked(selected),
                    indicatoron=False, image=unchecked, selectimage=checked, compound="left",
                )
                self._column_menu_index[column] = index
                index += 1
        self._column_menu.add_separator()
        self._column_menu.add_command(label="Reset sorting", command=self.reset_sort)
        self._column_menu.add_command(label="Reset columns", command=self.reset_columns)

    def _style_cell_menu_checks(self):
        """Check marks in the text color of the mode (Windows draws its own black on a
        dark menu); every other entry gets the same blank space, so labels line up."""
        unchecked, checked = _menu_check_images(self.tree)
        menu = self._cell_menu
        for index in range(menu.index("end") + 1):
            kind = menu.type(index)
            if kind == "checkbutton":
                menu.entryconfigure(index, indicatoron=False, image=unchecked, selectimage=checked,
                                    compound="left")
            elif kind in ("command", "cascade"):
                menu.entryconfigure(index, image=unchecked, compound="left")

    def _show_column_menu(self):
        self._column_menu.tk_popup(self.columns_button.winfo_rootx(),
                                   self.columns_button.winfo_rooty() + self.columns_button.winfo_height())

    def get_sort_settings(self):
        return {"sort_column": self._sort_column, "sort_descending": self._sort_descending}

    def sort_by(self, column):
        if column not in PROFILE_LIST_COLUMNS:
            raise ValueError("Unknown profile table sort column.")
        self._sort_descending = not self._sort_descending if column == self._sort_column else False
        self._sort_column = column
        self._sort_rows()
        self._layout_changed()

    def reset_sort(self):
        """Rows in the rotation order again; the columns stay as they are."""
        self._sort_column, self._sort_descending = "", False
        self._sort_rows()
        self._layout_changed()

    def reset_columns(self):
        """After a confirmation: the default columns, their order and widths; sorting stays."""
        if not messagebox.askyesno(
                "Reset columns",
                "Show the default columns in their default order and widths? "
                "Hidden columns stay available under Columns.",
                parent=self.frame.winfo_toplevel()):
            return
        self._column_order = normalize_profile_column_order()
        self._visible_columns = tuple(key for key in self._column_order if key in DEFAULT_VISIBLE_PROFILE_COLUMNS)
        for column in PROFILE_LIST_COLUMNS:
            self.tree.column(column, width=max(DEFAULT_PROFILE_COLUMN_WIDTHS[column], _column_min_width(column)))
            self._column_visibility_vars[column].set(column in self._visible_columns)
        self.tree.configure(displaycolumns=self._visible_columns)
        self._rebuild_column_menu()
        self._layout_changed()

    def _sort_value(self, item, column):
        settings = item["settings"]
        provider = settings.get("source", {}).get("provider", "eumetsat")
        profile = settings.get("sources", {}).get(provider, {})
        metadata = self._runtime.get("profiles", {}).get(item["id"], {})
        values = dict(zip(PROFILE_LIST_COLUMNS, self._row_values(item)))
        if column == "name":
            return _natural_key(item["name"])  # The runtime Active marker is not part of the name.
        if column == "rotation_enabled":
            return item["id"] in self._rotation_ids
        if column in {"history", "image_updates"}:
            return values[column] == "☑"
        if column == "data_coverage":
            return _numeric_value(values[column]) if values[column] != "-" else None
        if column in {"latitude", "longitude", "cloud_coverage", "mosaic_brightness", "mosaic_contrast"}:
            if values[column] == "-":
                return None
            field = {"cloud_coverage": "max_cloud_cover", "mosaic_brightness": "brightness", "mosaic_contrast": "contrast"}.get(column, column)
            fallback = {"cloud_coverage": 30, "mosaic_brightness": 100, "mosaic_contrast": 100}.get(column)
            return _numeric_value(profile.get(field, fallback)) if provider == "copernicus" else None
        if column == "zoom":
            return _numeric_value(profile.get("map_zoom") if provider == "copernicus"
                                  else settings.get("view", {}).get("zoom"))
        if column == "maximum_lookback":
            return _profile_lookback(settings)[1]
        if column in {"output_resolution", "resolution"}:
            match = re.match(r"(\d+) × (\d+)", values[column])
            if match:
                width, height = map(int, match.groups())
                return width * height, width, height
            return None
        if column in {"map_labels", "map_borders", "shorelines"}:
            return (values[column].startswith("On"), values[column]) if values[column] != "-" else None
        if column == "quarter_offset":
            if values[column] == "-":
                return None
            monthly = profile.get("date_mode") == "relative_month"
            offset = _numeric_value(profile.get("month_offset" if monthly else "quarter_offset", 0))
            return offset * (1 if monthly else 3) if offset is not None else None
        if column == "quarter_target":
            return _period_month(values[column])
        if column == "time":
            if profile.get("date_mode") in {"relative_quarter", "relative_month"} and provider == "copernicus":
                period = _period_month(values["quarter_target"])
                return _timestamp(f"{period // 12:04d}-{period % 12 + 1:02d}-01") if period is not None else None
            return _timestamp(_fixed_profile_date(settings) or metadata.get("source_time"))
        if column == "cache_status":
            return bool(metadata), _timestamp(metadata.get("updated_at")) or 0
        if column == "last_download":
            return _timestamp(self._runtime.get("last_downloads", {}).get(item["id"]))
        if column == "gap_fill" and values[column].startswith("Gap fill"):
            return _natural_key("Gap fill"), _profile_lookback(settings)[1] or 0
        if column == "gap_fill":
            return (_natural_key(values[column]), 0) if values[column] != "-" else None
        return _natural_key(values[column]) if values[column] != "-" else None

    def _ordered_items(self):
        """All profiles in table order: sorted by the active column or manual."""
        items = self._items
        if self._sort_column:
            keyed = [(item, self._sort_value(item, self._sort_column)) for item in items]
            present = [(item, key) for item, key in keyed if key is not None]
            present.sort(key=lambda pair: pair[1], reverse=self._sort_descending)
            # Missing/inapplicable values stay last in either direction. Ties retain rotation order.
            items = [item for item, _key in present] + [item for item, key in keyed if key is None]
        return items

    def _matches_filter(self, name):
        text = self.filter_var.get().strip().casefold()
        return not text or text in name.casefold()

    def _sort_rows(self):
        for column in PROFILE_LIST_COLUMNS:
            suffix = (" ▼" if self._sort_descending else " ▲") if column == self._sort_column else ""
            self.tree.heading(column, text=_heading_text(column) + suffix)
        rows = [(item["id"], item["name"]) for item in self._ordered_items()]
        if self._system_snapshot is not None and self.tree.exists(latest_snapshot.SYSTEM_ID):
            rows.insert(0, (latest_snapshot.SYSTEM_ID, latest_snapshot.SYSTEM_NAME))
        # Filtered-out rows are detached, not deleted, and leave the selection
        # so no action can reach a row that is not on screen.
        self._hidden_rows = set()
        index = 0
        for identifier, name in rows:
            if self._matches_filter(name):
                self.tree.move(identifier, "", index)
                index += 1
            else:
                self.tree.detach(identifier)
                self._hidden_rows.add(identifier)
        if self._hidden_rows:
            self.tree.selection_remove(*self._hidden_rows)

    @staticmethod
    def filter_field_width(header_width, window_width, minimum_window_width, label_width, clear_width):
        """Pixel width of the filter field so Filter, field and Clear fill half the header at the minimum."""
        minimum_header = header_width - max(0, window_width - minimum_window_width)
        return max(80, minimum_header // 2 - label_width - 6 - clear_width - 6)

    def _fit_filter_width(self):
        header = self._table_header
        window = header.winfo_toplevel()
        minimum = window.wm_minsize()[0]
        if header.winfo_width() <= 1 or minimum <= 1:
            return
        width = self.filter_field_width(header.winfo_width(), window.winfo_width(), minimum,
                                        self.filter_label.winfo_reqwidth(),
                                        self.filter_clear_button.winfo_reqwidth())
        if int(header.columnconfigure(2)["minsize"]) != width:
            header.columnconfigure(2, minsize=width)

    def _filter_changed(self, *_args):
        self._sort_rows()
        self._selection_changed()

    def get_table_rows(self):
        return self._table_rows

    def set_table_rows(self, rows):
        """Show ``rows`` table rows, limited to the supported range; True when it changed."""
        rows = min(max(int(rows), MIN_PROFILE_TABLE_ROWS), MAX_PROFILE_TABLE_ROWS)
        if rows == self._table_rows:
            return False
        self._table_rows = rows
        self._apply_table_rows()
        return True

    def _apply_table_rows(self):
        self.tree.configure(height=self._table_rows)
        self._list_frame.configure(width=650, height=(
            self.tree.winfo_reqheight() + self._horizontal_scrollbar.winfo_reqheight()
            + int(self.table_grip.cget("height"))))

    def _row_pixels(self):
        """Height of one table row; the requested height grows by it per row."""
        before = self.tree.winfo_reqheight()
        self.tree.configure(height=self._table_rows + 1)
        pixels = self.tree.winfo_reqheight() - before
        self.tree.configure(height=self._table_rows)
        return max(1, pixels)

    def _grip_press(self, event):
        self._grip_drag = {"y": event.y_root, "rows": self._table_rows,
                           "row_pixels": self._row_pixels()}
        return "break"

    def _grip_motion(self, event):
        if self._grip_drag is not None:
            self._resize_from_drag(self._grip_drag, event)
        return "break"

    def _grip_release(self, event):
        drag, self._grip_drag = self._grip_drag, None
        if drag is not None:
            self._resize_from_drag(drag, event)
            if self._table_rows != drag["rows"]:
                self._layout_changed()
        return "break"

    def _resize_from_drag(self, drag, event):
        self.set_table_rows(drag["rows"] + round((event.y_root - drag["y"]) / drag["row_pixels"]))

    def _grip_reset(self, _event=None):
        if self.set_table_rows(DEFAULT_PROFILE_TABLE_ROWS):
            self._layout_changed()
        return "break"

    def _show_tree_menu(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            self._column_menu.tk_popup(event.x_root, event.y_root)
            return "break"
        item = self.tree.identify_row(event.y)
        display_column = self.tree.identify_column(event.x)
        if region not in {"cell", "tree"} or not item or not display_column:
            self._context_item = self._context_column = None
            self._selection_changed()
            self._cell_menu.tk_popup(event.x_root, event.y_root)
            return "break"
        index = int(display_column[1:]) - 1
        if not 0 <= index < len(self._visible_columns):
            return None
        self._context_item = item
        self._context_column = self._visible_columns[index]
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        self.tree.focus(item)
        self._selection_changed()
        self._cell_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _reset_type_search(self, _event=None):
        self._search_prefix = ""
        self._search_time = 0.0

    def _type_to_select(self, event):
        # Widget-local binding: never capture text from entries or Ctrl/Alt shortcuts.
        # On Windows 0x0008 is Num Lock, not Alt: it must not stop typing.
        if event.state & (0x0004 | 0x20000):
            return None
        if event.keysym == "Escape":
            self._reset_type_search()
            return "break"
        if not event.char or not event.char.isprintable():
            self._reset_type_search()
            return None
        now = time.monotonic()
        char = event.char.casefold()
        previous = self._search_prefix if now - self._search_time < 1.0 else ""
        prefix = previous + char
        names = {item["id"]: item["name"].casefold() for item in self._items}
        names[latest_snapshot.SYSTEM_ID] = latest_snapshot.SYSTEM_NAME.casefold()
        rows = list(self.tree.get_children())
        cycle = not previous
        if not any(names.get(row, "").startswith(prefix) for row in rows) and previous == char:
            prefix, cycle = char, True
        self._search_prefix, self._search_time = prefix, now
        focus = self.tree.focus()
        if cycle and focus in rows:
            index = rows.index(focus) + 1
            rows = rows[index:] + rows[:index]
        for row in rows:
            if names.get(row, "").startswith(prefix):
                self.tree.selection_set(row)
                self.tree.focus(row)
                self.tree.see(row)
                self._selection_changed()
                break
        return "break"

    def _select_all(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        self._selection_changed()
        return "break"

    def _delete_key(self, _event=None):
        # Same action and availability as the Delete button.
        if self.buttons["Delete"].instate(["!disabled"]):
            self.delete_selected()
        return "break"

    def _separator_column(self, x):
        """The displayed column whose right border is at ``x`` (within a few pixels), else None."""
        columns = [column for column in self._visible_columns]
        widths = [int(self.tree.column(column, "width")) for column in columns]
        left = -round(self.tree.xview()[0] * sum(widths))
        nearest, distance = None, 7
        for column, width in zip(columns, widths):
            left += width
            if abs(x - left) < distance:
                nearest, distance = column, abs(x - left)
        return nearest

    def autofit_column(self, column):
        """Fit ``column`` to its heading and the widest value of every row; saved at once."""
        style = ttk.Style(self.tree)
        heading_font = style.lookup("Treeview.Heading", "font") or "TkDefaultFont"
        body_font = style.lookup("Treeview", "font") or "TkDefaultFont"

        def measure(font, text):
            return int(self.tree.tk.call("font", "measure", font, str(text)))

        # Room for the heading's padding and sort mark, and the cells' padding.
        width = measure(heading_font, self.tree.heading(column, "text")) + 20
        for item in (*self.tree.get_children(), *self._hidden_rows):
            if self.tree.exists(item):
                width = max(width, measure(body_font, self.tree.set(item, column)) + 16)
        width = max(width, int(self.tree.column(column, "minwidth")))
        if width != int(self.tree.column(column, "width")):
            self.tree.column(column, width=width)
            self._layout_changed()

    def _apply_double_clicked(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "separator":
            column = self._separator_column(event.x)
            if column is not None:
                self.autofit_column(column)
            return "break"
        item = self.tree.identify_row(event.y)
        if region not in {"cell", "tree"} or not item:
            return None
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.apply_selected()
        return "break"

    def _copy_to_clipboard(self, value):
        self.frame.clipboard_clear()
        self.frame.clipboard_append(str(value))
        self.frame.update_idletasks()

    def _copy_context_cell(self):
        if self._context_item and self._context_column:
            self._copy_to_clipboard(
                self.tree.set(self._context_item, self._context_column)
            )

    def _copy_selected_row(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return None
        values = self.tree.item(selection[0], "values")
        self._copy_to_clipboard("\t".join(str(value) for value in values))
        return "break"

    def _style_active_row(self):
        """Highlight the active profile in the colors of the light or dark mode."""
        colors = _theme_palette(self.tree)
        self.tree.tag_configure("active", background=colors["active_row"],
                                foreground=colors["active_row_text"])

    def _resize_text(self, event):
        if event.widget is self.frame:
            width = max(160, event.width - 12)
            self.notice_label.configure(wraplength=max(120, width - 20))
            # Inside the Now showing frame: its padding and border take about 20 px.
            self.status_label.configure(wraplength=max(140, width - 20))
            # Values wrap at word boundaries inside their half, or the full width.
            half_width = max(90, (width - 270) // 2)
            wide_width = max(180, width - 200)
            for key, (_label, value) in self._detail_rows.items():
                value.configure(wraplength=wide_width if self._detail_wrap[key] == "wide" else half_width)

    def _row_values(self, item):
        if item["id"] == latest_snapshot.SYSTEM_ID and self._snapshot is None:
            values = {key: "-" for key in PROFILE_LIST_COLUMNS}
            values.update(name=latest_snapshot.SYSTEM_NAME, time="No image yet",
                          history=self._history_value(item["id"]), status_symbol="")
            return tuple(values[key] for key in PROFILE_LIST_COLUMNS)
        identifier = item["id"]
        settings = item["settings"]
        provider = settings.get("source", {}).get("provider", "eumetsat")
        name = item["name"]
        symbol, active = profile_status(identifier, self._runtime)
        metadata = self._runtime.get("profiles", {}).get(identifier, {})
        quarter_mode, quarter_offset, quarter_target = _profile_quarter_values(
            settings, self._runtime.get("display_time_zone", "system")
        )
        return ("☑" if identifier in self._rotation_ids else "☐", name, symbol, active,
                self._history_value(identifier), "☑" if _image_updates_enabled(settings) else "☐",
                _SOURCE_LABELS.get(provider, provider),
                *self._selection_columns(settings), *_profile_time(
                    settings, metadata, self._runtime.get("display_time_zone", "system")
                ), quarter_mode, quarter_offset, quarter_target,
                _profile_location(settings), _profile_latitude(settings),
                _profile_longitude(settings), _profile_coverage(settings),
                _profile_copernicus_percentage(settings, "max_cloud_cover"),
                _profile_copernicus_percentage(settings, "brightness"),
                _profile_zoom(settings), _profile_lookback(settings)[0],
                _profile_resolution(settings), self._picture_resolution(identifier, settings),
                _profile_map_overlay(settings, "labels"),
                _profile_map_overlay(settings, "borders"),
                _cache_status(metadata, self._runtime.get("display_time_zone", "system")),
                _format_source_time(self._runtime.get("last_downloads", {}).get(identifier),
                                    self._runtime.get("display_time_zone", "system")) or "-",
                self._snapshot["snapshot_id"] if identifier == latest_snapshot.SYSTEM_ID else identifier,
                _short_id(self._snapshot["snapshot_id"] if identifier == latest_snapshot.SYSTEM_ID else identifier),
                self._no_data_color(settings), *self._tone_columns(settings),
                self._coverage_value(identifier), *self._auto_columns(identifier, settings),
                _profile_shorelines(settings))

    def _history_value(self, identifier):
        if self._history_enabled is None:
            return ""
        return "☑" if self._history_enabled(identifier) else "☐"

    def refresh_history(self):
        """Show changed History switches; the draft is owned by the host."""
        for identifier in (*self.tree.get_children(), *self._hidden_rows):
            self.tree.set(identifier, "history", self._history_value(identifier))
        if self._sort_column == "history":
            self._sort_rows()

    def _coverage_value(self, identifier):
        record = self._runtime.get("image_records", {}).get(identifier)
        if not record:
            return "-"
        try:
            value = record.get("data_coverage_percent")
            return f"{value:.2f}" if type(value) in (int, float) else "-"
        except (AttributeError, TypeError, ValueError):
            return "-"

    def _selection_columns(self, settings):
        """(Satellite / mission, Product, Layer), with the cached catalogues' names."""
        provider = settings.get("source", {}).get("provider", "eumetsat")
        profile = settings.get("sources", {}).get(provider, {})
        area_id = str(profile.get("area", ""))
        if provider == "copernicus":
            _configuration, product, layer, _highlight = _copernicus_labels(profile)
            return _text(profile.get("mission")), product, layer
        if provider == "eumetsat":
            satellite, mission = _text(profile.get("satellite"), ""), _text(profile.get("mission"), "")
            # "MTG - 0 Degree" already names the MTG mission.
            names = [satellite] if mission and satellite.startswith(mission) else [satellite, mission]
            layers = ", ".join(self._row_label("eumetsat", entry.get("name"))
                               for entry in _enabled_wms_layers(settings))
            return " · ".join(name for name in names if name) or "-", _text(profile.get("product_type")), layers or "-"
        if provider == "worldview":
            # The imagery layer is the selection; its date is in Time.
            return "-", "-", self._row_label(provider, area_id)
        if provider not in _SOURCE_LABELS:
            return "-", "-", "-"
        product = self._row_label(provider, profile.get("product"), area_id)
        entry = self._row_entry(provider, area_id) or {}
        if provider == "slider":
            # The catalogue's satellite group, e.g. "GOES-19 (East; 75.2W)".
            satellite = area_id.partition("---")[0].upper()
            return _text(entry.get("category"), satellite or "-"), product, "-"
        if provider == "himawari":
            return "Himawari · " + ("NICT" if area_id.startswith("nict_") else "JMA"), product, "-"
        # NOAA GOES and SUVI: the area's satellite, e.g. G19 -> GOES-19.
        match = re.fullmatch(r"G(\d+)", str(entry.get("satellite", "")))
        return (f"GOES-{match.group(1)}" if match else _SOURCE_LABELS[provider]), product, "-"

    def _row_entry(self, provider, item_id, area_id=None):
        """A cached catalogue entry for the table, looked up once per refresh."""
        key = (provider, str(item_id or ""), area_id)
        if key not in self._row_entries:
            entry = None
            if self._catalogue_entry is not None and key[1].strip():
                try:
                    entry = self._catalogue_entry(provider, item_id, area_id)
                except Exception:
                    entry = None
            self._row_entries[key] = entry if isinstance(entry, dict) else None
        return self._row_entries[key]

    def _row_label(self, provider, item_id, area_id=None):
        """The catalogue name of a saved ID for the table, else the ID itself."""
        label = (self._row_entry(provider, item_id, area_id) or {}).get("label")
        return str(label).strip() if label and str(label).strip() else _text(item_id)

    def _picture_resolution(self, identifier, settings):
        """The newest picture's size: Copernicus' saved PNG, the source picture of the others.

        A dash before a picture records it (other sources only since the
        source_resolution field exists).
        """
        record = self._runtime.get("image_records", {}).get(identifier)
        if not isinstance(record, dict):
            return "-"
        if settings.get("source", {}).get("provider") == "copernicus":
            value = f"{record.get('width')}x{record.get('height')}"
        else:
            value = str(record.get("source_resolution", ""))
        match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", value)
        return f"{match.group(1)} × {match.group(2)}" if match else "-"

    def _auto_columns(self, identifier, settings):
        """Auto recommendation, Auto priority, Precise check and Auto choice."""
        values = _auto_recommendation_values(settings)
        if values is None:
            return ("-",) * 4
        enabled, priority, precise = values
        return enabled, priority or "-", precise or "-", self._auto_choice(identifier, settings) or "-"

    def _auto_choice(self, identifier, settings):
        """The settings the rule took for the profile's newest picture; "" without one."""
        values = _auto_recommendation_values(settings)
        record = self._runtime.get("image_records", {}).get(identifier)
        if (not values or values[0] != "Yes" or not isinstance(record, dict)
                or not record.get("auto_recommendation")):
            return ""
        return _text(record.get("auto_choice"), "")

    def _color_auto_priorities(self):
        """The priorities in their colors: the details row and the Auto priority cells."""
        colors = _theme_palette(self.tree)
        value = self._detail_rows["auto_priority"][1]
        key = AUTO_PRIORITY_COLORS.get(self.detail_vars["auto_priority"].get())
        value.configure(foreground=colors[key] if key else "")
        rows = (*self.tree.get_children(), *self._hidden_rows)
        for label, key in AUTO_PRIORITY_COLORS.items():
            tag = "auto_priority_" + key
            self.tree.tag_configure(tag, foreground=colors[key])
            cells = [(row, "auto_priority") for row in rows
                     if self.tree.exists(row) and self.tree.set(row, "auto_priority") == label]
            try:
                self.tree.tk.call(self.tree, "tag", "cell", "remove", tag)
                if cells:
                    self.tree.tk.call(self.tree, "tag", "cell", "add", tag, cells)
            except tk.TclError:
                return  # Tk before 9 has no cell tags: the column keeps the text color.

    @staticmethod
    def _tone_columns(settings):
        if settings.get("source", {}).get("provider") != "copernicus":
            return ("-",) * 4
        profile = settings.get("sources", {}).get("copernicus", {})
        product = get_product(profile.get("configuration"), profile.get("product"))
        size = image_size_label(profile.get("image_size", "auto"))
        # Brightness and contrast correction only apply to layers with a tone rule.
        if tone_rule(get_layer(product, profile.get("layer"))) is None:
            return size, "-", "-", "-"
        return (size, f"{profile.get('contrast', 100)}%",
                "On" if profile.get("auto_brightness", False) else "Off",
                "On" if profile.get("auto_contrast", False) else "Off")

    @staticmethod
    def _no_data_color(settings):
        if settings.get("source", {}).get("provider") == "copernicus":
            profile = settings.get("sources", {}).get("copernicus", {})
            product = get_product(profile.get("configuration"), profile.get("product"))
            choice = no_data_choice(profile, get_layer(product, profile.get("layer")))
            # Shown like the Image tab's buttons; saved in lower case.
            return {"blur": "Blur", "blur_edge": "Edge Blur", "transparent": "Transparent"}.get(choice, choice)
        return "-"

    def _catalogue_label(self, provider, item_id, area_id=None):
        """The readable catalogue name of a saved choice, or None when unknown."""
        if self._catalogue_entry is None or not str(item_id or "").strip():
            return None
        try:
            entry = self._catalogue_entry(provider, item_id, area_id)
        except Exception:
            return None
        label = entry.get("label") if isinstance(entry, dict) else None
        return str(label).strip() if label and str(label).strip() else None

    def _detail_values(self, item):
        if item["id"] == latest_snapshot.SYSTEM_ID and self._snapshot is None:
            return {key: ("No image yet" if key == "time_utc" else "-" if key in DETAIL_ALWAYS_SHOWN else "")
                    for key in self.detail_vars}
        settings = item["settings"]
        provider = settings.get("source", {}).get("provider", "eumetsat")
        profile = settings.get("sources", {}).get(provider, {})
        view = settings.get("view", {})
        output = settings.get("output", {})
        source = DETAIL_SOURCE_NAMES.get(provider, _text(provider))
        configuration = layer = highlight = ""
        fit_zoom = f"{_capitalized(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        source_resolution = _source_resolution(profile.get("resolution"))
        area_id = str(profile.get("area", ""))
        area = self._catalogue_label(provider, area_id) or _text(area_id)
        product = self._catalogue_label(provider, profile.get("product"), area_id) or _text(profile.get("product"))
        if provider == "copernicus":
            configuration, product, layer, highlight = _copernicus_labels(profile)
            source = f"{source} · {_text(profile.get('mission'))}"
            area = f"{_format_number(profile.get('latitude'))}, {_format_number(profile.get('longitude'))}"
            highlight = "" if highlight == "-" else highlight
            source_resolution = f"Map zoom {_text(profile.get('map_zoom'))}"
            # The map zoom is the source resolution; the picture always shows the map extent.
            fit_zoom = ""
        elif provider == "solar":
            # The only area is the Sun, which the source already names.
            area = ""
        elif provider == "himawari":
            source += " · NICT" if area_id.startswith("nict_") else " · JMA"
        elif provider == "slider":
            entry = None
            if self._catalogue_entry is not None:
                try:
                    entry = self._catalogue_entry(provider, area_id)
                except Exception:
                    entry = None
            satellite, separator, sector = area_id.partition("---")
            if isinstance(entry, dict) and entry.get("label"):
                area = " · ".join(_text(value) for value in (entry.get("category"), entry["label"])
                                  if _text(value, ""))
            elif separator:
                area = f"{satellite.upper()} · {_capitalized(sector.replace('_', ' '))}"
        elif provider == "worldview":
            area = "Global (EPSG:4326)"
            product = self._catalogue_label(provider, area_id) or _text(area_id)
        elif provider == "eumetsat":
            preset_value = view.get("preset")
            preset = _PRESET_LABELS.get(preset_value, _text(preset_value))
            # Themes dropped from the viewer (Climate, Emergency) now mean all themes.
            theme = _EUMETSAT_THEME_LABELS.get(profile.get("theme"), _EUMETSAT_THEME_LABELS["all"])
            configuration = f"{theme} · {preset}"
            if profile.get("fill_gaps"):
                configuration += f" · Gap fill {profile.get('gap_fill_lookback_hours', 12)} h"
            satellite, mission = _text(profile.get("satellite"), ""), _text(profile.get("mission"), "")
            # "MTG - 0 Degree" already names the MTG mission.
            names = [satellite] if mission and satellite.startswith(mission) else [satellite, mission]
            source = " · ".join([source, *(name for name in names if name)])
            center = _custom_area_center(settings)
            if center is not None:
                area = f"{preset} · {_format_coordinate(center[0])}, {_format_coordinate(center[1])}"
            elif preset_value == "custom" and isinstance(view.get("bbox"), (list, tuple)):
                area = ", ".join(_format_number(value) for value in view["bbox"])
            else:
                area = preset
            product = _text(profile.get("product_type"))
            layer = ", ".join(self._catalogue_label("eumetsat", entry.get("name")) or _text(entry.get("name"))
                              for entry in _enabled_wms_layers(settings)) or "-"
            scale = output.get("render_scale", "auto")
            source_resolution = "Render quality · " + ("Automatic" if scale == "auto" else _render_quality_text(scale))
        metadata = self._runtime.get("profiles", {}).get(item["id"], {})
        wallpaper = _capitalized(self._runtime.get("wallpaper_position"))
        fixed = _fixed_profile_date(settings)
        source_time = metadata.get("source_time")
        if fixed:
            utc_time = f"Fixed date · {fixed}"
        elif source_time:
            try:
                utc_time = format_utc_datetime(source_time)
            except (TypeError, ValueError, OverflowError):
                utc_time = "Not loaded yet"
        elif provider == "worldview" and str(profile.get("product", "")).casefold() == "timeless":
            utc_time = "Timeless (not time-dependent)"
        else:
            utc_time = "Not loaded yet"
        quarter_mode, quarter_offset, quarter_target = _profile_quarter_values(
            settings, self._runtime.get("display_time_zone", "system")
        )
        quarter_selection = (
            "" if quarter_mode == "-" else
            f"{quarter_mode} · {quarter_offset} · {quarter_target}"
            if quarter_offset != "-" else f"{quarter_mode} · {quarter_target}"
        )
        records = self._runtime.get("image_records", {})
        auto = _auto_recommendation_values(settings) or ("", "", "")
        return {
            "image_source": source,
            "configuration": configuration,
            "area": area,
            "product": product,
            "layer": layer,
            "highlight": highlight,
            "quarter_selection": quarter_selection,
            "source_resolution": source_resolution,
            "output_resolution": f"{_text(self._runtime.get('output_resolution'))} (global)",
            "fit_zoom": fit_zoom,
            "wallpaper_position": f"{wallpaper} (global)",
            "time_utc": utc_time,
            "cache": _cache_status(
                metadata, self._runtime.get("display_time_zone", "system")
            ),
            # Only once a picture records it.
            "data_coverage": coverage_label(records[item["id"]]) if item["id"] in records else "",
            # Copernicus only; priority and precise check only while the rule is on.
            "auto_recommendation": auto[0],
            "auto_priority": auto[1],
            "auto_precise": auto[2],
            "auto_choice": self._auto_choice(item["id"], settings),
        }

    def _update_details(self, item=None):
        if item is None:
            try:
                item = self._selected_item()
            except (ValueError, StopIteration):
                item = None
        values = ({key: "-" if key in DETAIL_ALWAYS_SHOWN else "" for key in self.detail_vars}
                  if item is None else self._detail_values(item))
        for key, variable in self.detail_vars.items():
            variable.set(values[key])
            # A row that does not apply to the source is hidden, not shown with "-".
            for widget in self._detail_rows[key]:
                widget.grid() if values[key] else widget.grid_remove()
        self._color_auto_priorities()

    def _candidate(self, items):
        candidate = deepcopy(self._library)
        candidate["items"] = deepcopy(items)
        candidate["rotation"]["order"] = [item["id"] for item in items if item["id"] in self._rotation_ids]
        return normalize_library(candidate)

    def _commit(self, items, selected=None):
        candidate = self._candidate(items)
        self._library = candidate
        self._items = candidate["items"]
        self._rotation_ids = set(candidate["rotation"]["order"])
        self._refresh(selected)

    def _toggle_rotation_clicked(self, event):
        """Toggle the Rotation or History checkbox without applying a profile."""
        column = self._checkbox_column(event)
        item = self.tree.identify_row(event.y) if column else ""
        if not item:
            return None
        if column == "rotation_enabled" and item == latest_snapshot.SYSTEM_ID:
            return None  # The snapshot row never rotates.
        if column == "history" and self._on_history_toggle is None:
            return None
        if column == "image_updates" and item == latest_snapshot.SYSTEM_ID:
            return None  # The snapshot's settings are not edited by hand.
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        if column == "rotation_enabled":
            self.toggle_rotation_selected()
        elif column == "image_updates":
            self.toggle_image_updates_selected()
        else:
            self.toggle_history_selected()
        return "break"

    def toggle_image_updates_selected(self):
        """Switch Check for and download newer images of the selected profiles; saved at once."""
        identifiers = [item["id"] for item in self._items if item["id"] in self.tree.selection()]
        if not identifiers:
            return
        enabled = not all(_image_updates_enabled(item["settings"]) for item in self._items
                          if item["id"] in identifiers)
        items = deepcopy(self._items)
        for item in items:
            if item["id"] in identifiers:
                item["settings"].setdefault("source", {})["check_for_updates"] = enabled
        self._commit(items, identifiers[0])
        for identifier in identifiers[1:]:
            if self.tree.exists(identifier):
                self.tree.selection_add(identifier)
        persist = None
        if self._on_image_updates is not None:
            def persist(library):
                self._on_image_updates(identifiers, enabled, library)
        self._save(f"Imagery updates {'on' if enabled else 'off'} for {len(identifiers)} profile(s).",
                   persist=persist)

    def toggle_history_selected(self):
        """Switch History for the selected rows; the host saves it at once.

        The Latest snapshot row stands for History (no profile).
        """
        normal = {item["id"] for item in self._items} | {latest_snapshot.SYSTEM_ID}
        identifiers = [identifier for identifier in self.tree.selection() if identifier in normal]
        if not identifiers or self._on_history_toggle is None:
            return
        enabled = not all(self._history_enabled(identifier) for identifier in identifiers)
        try:
            self._on_history_toggle(identifiers, enabled)
        except Exception as exc:
            self._error(exc)
            return
        self.refresh_history()
        self._show_notice(f"History {'on' if enabled else 'off'} for {len(identifiers)} profile(s).", saved=True)

    def toggle_rotation_selected(self):
        identifiers = [item["id"] for item in self._items if item["id"] in self.tree.selection()]
        if not identifiers:
            raise ValueError("Select one or more normal profiles to change rotation.")
        self._set_rotation_selected(
            identifiers,
            not all(identifier in self._rotation_ids for identifier in identifiers),
        )

    def _set_rotation_selected(self, identifiers, enabled):
        if enabled:
            self._rotation_ids.update(identifiers)
        else:
            self._rotation_ids.difference_update(identifiers)
        self._commit(self._items, identifiers[0])
        for identifier in identifiers[1:]:
            if self.tree.exists(identifier):
                self.tree.selection_add(identifier)
        self._save(("Enabled" if enabled else "Disabled") + f" {len(identifiers)} profile(s) for rotation.")

    def _refresh(self, selected=None):
        self._row_entries = {}  # Catalogue names may have been refreshed since.
        yview = self.tree.yview()
        self.tree.delete(*self.tree.get_children(),
                         *(identifier for identifier in self._hidden_rows if self.tree.exists(identifier)))
        if self._system_snapshot is not None:
            item = self._system_item()
            active = latest_snapshot.SYSTEM_ID == self._runtime.get("active_profile_id")
            self.tree.insert("", "end", iid=latest_snapshot.SYSTEM_ID, values=self._row_values(item),
                             tags=("active",) if active else ())
        for item in self._items:
            active = item["id"] == self._runtime.get("active_profile_id")
            self.tree.insert("", "end", iid=item["id"], values=self._row_values(item),
                             tags=("active",) if active else ())
        self._color_auto_priorities()
        self._sort_rows()
        if selected and self.tree.exists(selected) and selected not in self._hidden_rows:
            self.tree.selection_set(selected)
            self.tree.focus(selected)
            self.tree.see(selected)
        elif yview and self.tree.get_children():
            self.tree.yview_moveto(yview[0])
        self._selection_changed()

    def _system_item(self):
        return {"id": latest_snapshot.SYSTEM_ID, "name": latest_snapshot.SYSTEM_NAME,
                "settings": deepcopy(self._snapshot["settings"]) if self._snapshot else {}}

    def _selected_item(self):
        if self.tree.selection() == (latest_snapshot.SYSTEM_ID,):
            return self._system_item()
        return self._items[self._selected_index()]

    def _selected_transfer_item(self):
        if self.tree.selection() == (latest_snapshot.SYSTEM_ID,):
            return latest_snapshot.export_item(self._snapshot)
        return self._items[self._selected_index()]

    def _selected_index(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            raise ValueError("Select exactly one profile for this action.")
        return next(index for index, item in enumerate(self._items) if item["id"] == selected[0])

    def _selection_changed(self, _event=None):
        try:
            index = self._selected_index()
        except (ValueError, StopIteration):
            index = None
        if index is not None:
            self._update_details(self._items[index])
        else:
            self._update_details()
        system_selected = self.tree.selection() == (latest_snapshot.SYSTEM_ID,)
        normal_selected = any(item["id"] in self.tree.selection() for item in self._items)
        loadable = index is not None or bool(system_selected and self._snapshot)
        refreshable = normal_selected or bool(
            latest_snapshot.SYSTEM_ID in self.tree.selection() and self._snapshot)
        enabled = {
            "Apply": loadable and self._on_apply is not None,
            "Force loading new image": self._on_force_load is not None and refreshable,
            "Check for new image": self._on_check is not None and refreshable,
            "Load": loadable,
            "Update": index is not None,
            "Rename": index is not None,
            "Delete": normal_selected,
            "Move up": self._reordered(-1) is not None,
            "Move down": self._reordered(1) is not None,
            "Export profile": normal_selected or bool(system_selected and self._snapshot),
        }
        for label in ("Apply", "Load", "Update", "Rename", "Delete", "Export profile"):
            self.buttons[label].state(["!disabled"] if enabled[label] else ["disabled"])
        enabled.update({
            "Duplicate": index is not None or bool(system_selected and self._snapshot),
            "Toggle rotation": normal_selected,
            "Open profile history folder": (index is not None or system_selected)
                                           and self._history_directory is not None,
            "Copy cell": bool(self._context_item),
            "Copy row": bool(self.tree.selection()),
        })
        identifiers = set(self.tree.selection())
        normal = [item for item in self._items if item["id"] in identifiers]
        history_rows = [identifier for identifier in identifiers
                        if identifier == latest_snapshot.SYSTEM_ID or any(item["id"] == identifier for item in normal)]
        enabled.update({
            "Toggle updates": bool(normal),
            "Toggle history": bool(history_rows) and self._on_history_toggle is not None
                              and self._history_enabled is not None,
        })
        self._toggle_vars["Toggle updates"].set(
            bool(normal) and all(_image_updates_enabled(item["settings"]) for item in normal))
        self._toggle_vars["Toggle history"].set(
            enabled["Toggle history"] and all(self._history_enabled(identifier) for identifier in history_rows))
        self._toggle_vars["Toggle rotation"].set(
            bool(normal) and all(item["id"] in self._rotation_ids for item in normal))
        # Greyed out without Windows' white shadow of disabled entries.
        for label, state in enabled.items():
            _set_menu_entry_enabled(self._menu_of[label], label, bool(state))
        # A submenu greys out when none of its entries is available.
        for label, menu in self._submenus.items():
            _set_menu_entry_enabled(self._cell_menu, label, any(
                self.menu_entry_enabled(entry) for entry, owner in self._menu_of.items() if owner is menu))

    def menu_entry_enabled(self, label):
        """Whether the table's context menu (or one of its submenus) offers ``label`` now."""
        return _menu_entry_enabled(self._menu_of.get(label, self._cell_menu), label)

    def invoke_menu_entry(self, label):
        """Run a context menu entry wherever it sits, as a click on it would."""
        self._menu_of.get(label, self._cell_menu).invoke(label)

    def _error(self, error):
        messagebox.showerror("Image profiles", str(error), parent=self.frame.winfo_toplevel())

    def export_selected(self):
        try:
            if self._normalize_settings is None:
                raise ValueError("Profile transfer validation is unavailable.")
            selected = set(self.tree.selection())
            items = [item for item in self._items if item["id"] in selected]
            if latest_snapshot.SYSTEM_ID in selected and self._snapshot:
                items.insert(0, latest_snapshot.export_item(self._snapshot))
            if not items:
                raise ValueError("Select one or more profiles to export (Ctrl/Shift).")
            options = {"parent": self.frame.winfo_toplevel()}
            if self._export_locations is not None:
                options["initialdir"] = str(self._export_locations.initial_directory("profiles"))
            single = len(items) == 1
            if single:
                path = filedialog.asksaveasfilename(**options, title="Export profile",
                    initialfile=profile_export_filename(items[0]), defaultextension=".json",
                    filetypes=[("Profile JSON", "*.json")], confirmoverwrite=False)
            else:
                path = filedialog.askdirectory(**options,
                    title="Export selected profiles - one JSON per profile", mustexist=True)
            if not path:
                self.status_var.set("Export cancelled; no files were changed.")
                return
            folder = Path(path).parent if single else Path(path)
            written = export_profiles(folder, items, self._normalize_settings,
                destination=path if single else None,
                confirm_conflict=lambda context: confirm_transfer_conflict(self.frame.winfo_toplevel(), context),
                coverage_records=self._runtime.get("image_records", {}))
            skipped = len(items) - len(written)
            self.status_var.set(f"Exported {len(written)} profile(s); skipped {skipped}. Credentials and local paths were excluded.")
            summary = f"Successfully exported {len(written)} profile(s).\nSkipped: {skipped}." if written else (
                f"No profiles were exported.\nSkipped: {skipped}. Existing files were left unchanged.")
            if written:
                summary += "\n\nSaved to:\n" + (str(written[0]) if len(written) == 1 else str(folder))
            preference_warning = ""
            if written and self._export_locations is not None:
                try:
                    if not self._export_locations.remember("profiles", folder):
                        preference_warning = "The export folder could not be remembered for next time."
                except OSError as exc:
                    preference_warning = f"The export folder could not be remembered: {exc}"
            if preference_warning:
                messagebox.showwarning("Profile export complete", summary + "\n\n" + preference_warning,
                                       parent=self.frame.winfo_toplevel())
            else:
                messagebox.showinfo("Profile export complete" if written else "Profile export skipped", summary,
                                    parent=self.frame.winfo_toplevel())
        except ImportCancelled:
            self.status_var.set("Export cancelled; no files were changed.")
        except Exception as exc:
            self.status_var.set(f"Profile export failed: {exc}")
            messagebox.showerror("Profile export failed", f"The export could not be completed.\n\nReason: {exc}",
                                 parent=self.frame.winfo_toplevel())

    def import_selected(self):
        try:
            if self._normalize_settings is None or self._import_defaults is None:
                raise ValueError("Profile transfer validation is unavailable.")
            # Imports start where profiles are exported.
            options = {"parent": self.frame.winfo_toplevel()}
            if self._export_locations is not None:
                try:
                    options["initialdir"] = str(self._export_locations.initial_directory("profiles"))
                except OSError:
                    pass
            paths = filedialog.askopenfilenames(**options, title="Import profiles",
                filetypes=[("Profiles or MarbleScape PNG images", "*.json *.png"),
                           ("Profile JSON", "*.json"), ("PNG images", "*.png")])
            if not paths:
                return
            existing_ids = {item["id"] for item in self._items}
            result = import_profiles(paths, self._items, self._import_defaults, self._normalize_settings,
                confirm_repair=lambda label, changes: confirm_import_repair(self.frame.winfo_toplevel(), label, changes),
                confirm_conflict=lambda context: confirm_transfer_conflict(self.frame.winfo_toplevel(), context),
                review_preflight=lambda report: review_import_preflight(self.frame.winfo_toplevel(), report))
            imported, warnings = result
            if imported:
                # Rotation membership is local application state. New imported
                # profiles begin disabled even if their source library used
                # rotation; an overwritten profile keeps its local membership.
                self._rotation_ids.difference_update(
                    item["id"] for item in imported if item["id"] not in existing_ids)
                self._commit(result.items, imported[0]["id"])
                self.tree.selection_set([item["id"] for item in imported])
                self._selection_changed()
                if not self._save(f"Imported/replaced {len(imported)} profile(s)."):
                    return
            summary = f"Imported/replaced {len(imported)} profile(s)."
            if len(paths) == 1 and len(imported) == 1 and not warnings:
                summary += f"\n\nProfile: {imported[0]['name']}"
            if result.renamed:
                summary += ("\n\nA different profile with the same name already exists; "
                            "both were kept:\n" + "\n".join(result.renamed))
            if warnings:
                summary += "\n\nSkipped or could not import:\n" + "\n".join(warnings)
            messagebox.showinfo("Profile import", summary, parent=self.frame.winfo_toplevel())
        except ImportCancelled:
            self.status_var.set("Import cancelled; no profiles were changed.")
        except Exception as exc:
            self._error(exc)

    def add_current(self, name=None):
        """Create a profile from the Image settings.

        Without ``name`` a dialog asks for it, suggesting the profile name the
        Image tab shows; an invalid name is reported and asked for again.
        """
        asked = name is None
        suggestion = ""
        if asked and self._suggest_name is not None:
            try:
                suggestion = self._suggest_name() or ""
            except Exception:
                suggestion = ""
        while True:
            if asked:
                name = _ask_new_profile_name(self.frame.winfo_toplevel(), suggestion)
                if name is None:
                    return
            item = {"id": new_profile_id(), "name": name, "settings": {}}
            items = deepcopy(self._items) + [item]
            try:
                self._candidate(items)  # Check the name before capturing or changing data.
            except Exception as exc:
                self._error(exc)
                if not asked:
                    return
                suggestion = name
                continue
            break
        try:
            item["settings"] = deepcopy(self._capture_settings())
            self._commit(items, item["id"])
            added = next(entry["name"] for entry in self._items if entry["id"] == item["id"])
            self._save(f"Added profile '{added}'.")
        except Exception as exc:
            self._error(exc)

    def update_selected(self):
        try:
            index = self._selected_index()
            if not messagebox.askyesno("Update profile", f"Replace the saved image settings of '{self._items[index]['name']}' with the current Image settings?",
                                       parent=self.frame.winfo_toplevel(), icon="warning"):
                return
            items = deepcopy(self._items)
            items[index]["settings"] = deepcopy(self._capture_settings())
            self._commit(items, items[index]["id"])
            self._save(f"Updated the image settings of '{items[index]['name']}'.")
        except Exception as exc:
            self._error(exc)

    def load_selected(self):
        try:
            item = self._selected_transfer_item()
            self._on_load(deepcopy(item["settings"]))
        except Exception as exc:
            self._error(exc)

    def apply_selected(self):
        try:
            item = self._selected_transfer_item()
            if self._on_apply is None:
                raise ValueError("Applying a profile is unavailable in this window.")
            self._on_apply(deepcopy(item["settings"]))
        except Exception as exc:
            self._error(exc)

    def force_load_selected(self):
        """Load new pictures of the selected profiles without changing the active profile."""
        try:
            if self._on_force_load is None:
                raise ValueError("Loading new pictures is unavailable in this window.")
            identifiers = self._refresh_targets()
            if not identifiers:
                raise ValueError("Select one or more profiles to load new pictures.")
            queued, active = self._on_force_load(identifiers)
            parts = []
            if active:
                parts.append("Loading a new picture of the active profile.")
            if queued:
                parts.append(f"Loading new pictures of {len(queued)} profile(s) in the background; "
                             "the active profile stays.")
            self._show_notice(" ".join(parts) or
                              "The selected profiles are already waiting for new pictures.")
        except Exception as exc:
            self._error(exc)

    def _refresh_targets(self):
        """Selected rows for Force loading and Check: the Latest snapshot row (once it
        has an image) first, then saved profiles in list order."""
        selected = self.tree.selection()
        snapshot = [latest_snapshot.SYSTEM_ID] if latest_snapshot.SYSTEM_ID in selected and self._snapshot else []
        return snapshot + [item["id"] for item in self._items if item["id"] in selected]

    def check_selected(self):
        """Load a picture of the selected profiles only where the provider has a newer one."""
        try:
            if self._on_check is None:
                raise ValueError("Checking for new pictures is unavailable in this window.")
            identifiers = self._refresh_targets()
            if not identifiers:
                raise ValueError("Select one or more profiles to check for new pictures.")
            queued, active = self._on_check(identifiers)
            parts = []
            if active:
                parts.append("Checking the active profile now.")
            if queued:
                parts.append(f"Checking {len(queued)} profile(s) for new pictures in the background; "
                             "only newer pictures are downloaded.")
            self._show_notice(" ".join(parts) or
                              "The selected profiles are already waiting for new pictures.")
        except Exception as exc:
            self._error(exc)

    def rename_selected_from_dialog(self):
        """Prompt for a name when Rename is invoked (button or table context menu)."""
        try:
            index = self._selected_index()
            old_name = self._items[index]["name"]
            new_name = _ask_profile_name(self.frame.winfo_toplevel(), old_name)
            if new_name is not None:
                self._rename_to(index, old_name, new_name)
        except Exception as exc:
            self._error(exc)

    def _rename_to(self, index, old_name, new_name):
        # Invisible characters alone do not count as a new name.
        new_name = clean_profile_name(str(new_name))
        if not new_name or new_name == old_name:
            return
        probe = deepcopy(self._items)
        probe[index]["name"] = new_name
        self._candidate(probe)  # Validate name before confirmation or moving folders.
        if not messagebox.askyesno("Rename profile",
                                   f"Overwrite profile name '{old_name}' with '{new_name}'?",
                                   parent=self.frame.winfo_toplevel(), default="no"):
            return
        items = deepcopy(self._items)
        items[index]["name"] = new_name
        self._commit(items, items[index]["id"])
        self._save(f"Renamed '{old_name}' to '{new_name}'.")

    def delete_selected(self):
        try:
            identifiers = set(self.tree.selection())
            selected_items = [item for item in self._items if item["id"] in identifiers]
            if not selected_items:
                raise ValueError("Select one or more profiles to delete (Ctrl/Shift).")
            count = len(selected_items)
            prompt = (f"You are about to delete {selected_items[0]['name']}. Do you really want to proceed?" if count == 1 else
                      f"Are you sure you want to delete {count} selected profiles?\n\n"
                      + "\n".join(item["name"] for item in selected_items[:8])
                      + (f"\n... and {count - 8} more." if count > 8 else ""))
            removing_all = count == len(self._items)
            if removing_all and self.enabled_var.get():
                prompt += "\n\nAll profiles are selected, so profile rotation will also be disabled."
            if latest_snapshot.SYSTEM_ID in identifiers:
                prompt += "\n\nThe protected Latest snapshot (no profile) entry will be kept."
            title = "Delete profile" if count == 1 else "Delete profiles"
            history = {"files": 0, "bytes": 0, "other_files": 0, "other_bytes": 0}
            if self._history_usage is not None:
                for item in selected_items:
                    usage = self._history_usage(item["id"])
                    for key in history:
                        history[key] += usage.get(key, 0)
            delete_history = False
            if history["files"] or history["other_files"]:
                label = (f"Also delete History images ({history['files']} image{'s' if history['files'] != 1 else ''}, "
                         f"{_format_bytes(history['bytes'])})")
                note = ""
                if history["other_files"]:
                    other = history["other_files"]
                    note = (f"The History folder{'s' if count != 1 else ''} also contain{'' if count != 1 else 's'} "
                            f"{other} other file{'s' if other != 1 else ''} ({_format_bytes(history['other_bytes'])}) "
                            "not created by MarbleScape. They are deleted together with the folder.")
                delete_history = _confirm_profile_delete(self.frame.winfo_toplevel(), title, prompt, label, note)
                if delete_history is None:
                    return
            elif not messagebox.askyesno(title, prompt, parent=self.frame.winfo_toplevel(),
                                         icon="warning", default="no"):
                return
            index = next(index for index, item in enumerate(self._items) if item["id"] in identifiers)
            items = [deepcopy(item) for item in self._items if item["id"] not in identifiers]
            selected = items[min(index, len(items) - 1)]["id"] if items else None
            self._commit(items, selected)
            for item in selected_items:
                if delete_history:
                    self._pending_history_deletes[item["id"]] = item["name"]
                else:
                    self._pending_history_deletes.pop(item["id"], None)
            if not items:
                self._quiet = True
                try:
                    self.enabled_var.set(False)
                finally:
                    self._quiet = False
                self._library["rotation"]["enabled"] = False
            self._save(f"Deleted {count} profile(s)" + (" and their History folders." if delete_history else "."))
        except Exception as exc:
            self._error(exc)

    def open_history_folder(self):
        try:
            item = self._selected_item()
            if self._history_directory is None:
                raise ValueError("Select exactly one profile to open its history folder.")
            # The Latest snapshot row opens the "_no profile" folder.
            folder = self._history_directory(item["id"], self._saved_history_names.get(item["id"], item["name"]), True)
            if hasattr(os, "startfile"):
                os.startfile(str(folder))
            else:
                raise ValueError("Opening folders is available on Windows only.")
        except Exception as exc:
            self._error(exc)

    def commit_history_renames(self):
        """Rename archive folders only after the profile draft was saved."""
        errors = []
        for item in self._items:
            identifier, name = item["id"], item["name"]
            previous = self._saved_history_names.get(identifier)
            if previous and previous != name and self._on_profile_rename is not None:
                try:
                    self._on_profile_rename(identifier, previous, name)
                except (OSError, ValueError) as exc:
                    errors.append(f"{name}: {exc}")
                    continue
            self._saved_history_names[identifier] = name
        return errors

    def _save(self, message, persist=None):
        """Write the profile list at once; the tab keeps no unsaved draft.

        On failure the table returns to the last saved state, so what it shows
        always matches profiles.toml. ``persist`` replaces on_save for one change.
        """
        persist = persist or self._on_save
        if persist is not None:
            try:
                library = self.get_library()
                persist(library)
            except Exception as exc:
                self._restore_saved()
                self._error(exc)
                return False
            self._saved_state = library
            parent = self.frame.winfo_toplevel()
            rename_errors = self.commit_history_renames()
            if rename_errors:
                messagebox.showwarning("History folder rename incomplete",
                                       "The profiles were saved, but these History folders could not be renamed:\n"
                                       + "\n".join(rename_errors), parent=parent)
            delete_errors = self.commit_history_deletions()
            if delete_errors:
                messagebox.showwarning("History images not deleted",
                                       "The profiles were deleted, but History images of these profiles could not be removed:\n"
                                       + "\n".join(delete_errors), parent=parent)
        self._show_notice(message, saved=True)
        return True

    def _show_notice(self, message, saved=False):
        """Show ``message`` below the buttons.

        A saved change (``saved``) is only confirmed by the footer's green
        "✓ Saved"; the line below the buttons is then cleared and hidden.
        """
        if saved:
            self.last_saved_change = message
            if self._on_saved is not None:
                self._on_saved()
            self.notice_var.set("")
            self.notice_label.grid_remove()
            return
        self.notice_var.set(message)
        self.notice_label.grid()

    def _restore_saved(self):
        saved = deepcopy(self._saved_state)
        self._quiet = True
        try:
            self._library = saved
            self._items = saved["items"]
            self._rotation_ids = set(saved["rotation"]["order"])
            rotation = saved["rotation"]
            self.enabled_var.set(rotation["enabled"])
            self.interval_var.set(str(rotation["interval"]))
            self.unit_var.set(rotation["unit"])
            self.random_shuffle_var.set(rotation["random_shuffle"])
            self.keep_last_position_var.set(rotation["keep_last_position"])
            self.preload_next_var.set(rotation["preload_next"])
        finally:
            self._quiet = False
        self._pending_history_deletes.clear()
        selection = [identifier for identifier in self.tree.selection()
                     if any(item["id"] == identifier for item in self._items)]
        self._refresh(selection[0] if selection else None)

    def _refresh_short_rotation_hint(self):
        text = short_rotation_hint(self.interval_var.get(), self.unit_var.get())
        self.short_rotation_label.configure(
            text=text, foreground=_theme_palette(self.short_rotation_label)["warning"])
        # The line stays reserved; empty while the interval is long enough.

    def _rotation_changed(self):
        if not self._quiet:
            self._save("Saved rotation settings.")

    def commit_history_deletions(self):
        """Remove opted-in History folders once the deletion was saved."""
        errors = []
        kept = {item["id"] for item in self._items}
        pending, self._pending_history_deletes = self._pending_history_deletes, {}
        for identifier, name in pending.items():
            # Only a profile that is really gone loses its History.
            if identifier in kept or self._on_history_delete is None:
                continue
            try:
                self._on_history_delete(identifier)
            except (OSError, ValueError) as exc:
                errors.append(f"{name}: {exc}")
        return errors

    def duplicate_selected(self):
        try:
            system_selected = self.tree.selection() == (latest_snapshot.SYSTEM_ID,)
            index = -1 if system_selected else self._selected_index()
            item = deepcopy(self._selected_transfer_item())
            if system_selected:
                item["name"] = latest_snapshot.SYSTEM_NAME
            base = re.sub(r" \(Copy(?: \d+)?\)$", "", item["name"])
            names = {entry["name"].casefold() for entry in self._items}
            number = 0
            while True:
                suffix = " (Copy)" if number == 0 else f" (Copy {number})"
                name = base[:80 - len(suffix)] + suffix
                if name.casefold() not in names:
                    break
                number += 1
            item.update(id=new_profile_id(), name=name)
            items = deepcopy(self._items)
            items.insert(index + 1, item)
            self._commit(items, item["id"])
            self._save(f"Duplicated as '{name}'.")
        except Exception as exc:
            self._error(exc)

    def _displayed_ids(self):
        """Normal profile rows in on-screen order (sorted view included)."""
        normal = {item["id"] for item in self._items}
        return [identifier for identifier in self.tree.get_children() if identifier in normal]

    def _reordered(self, offset):
        """Order after moving every selected row one step, or None if nothing moves.

        Each selected row passes the unselected neighbour on that side, so a
        contiguous selection moves as one block.
        """
        order = self._displayed_ids()
        selected = set(self.tree.selection())
        indices = range(1, len(order)) if offset < 0 else range(len(order) - 2, -1, -1)
        moved = False
        for index in indices:
            neighbour = index + offset
            if order[index] in selected and order[neighbour] not in selected:
                order[index], order[neighbour] = order[neighbour], order[index]
                moved = True
        return order if moved else None

    def _apply_order(self, order, moving, message):
        """Store a new manual row order; it is also the rotation order.

        `order` covers the rows on screen. Rows hidden by the filter keep
        their places; the visible rows fill the slots they held before.
        """
        visible = set(order)
        reordered = iter(order)
        full = [next(reordered) if item["id"] in visible else item["id"] for item in self._ordered_items()]
        by_id = {item["id"]: item for item in self._items}
        items = [deepcopy(by_id[identifier]) for identifier in full]
        was_sorted = bool(self._sort_column)
        self._sort_column, self._sort_descending = "", False
        self._commit(items, moving[0])
        for identifier in moving[1:]:
            self.tree.selection_add(identifier)
        self.tree.focus(moving[0])
        self.tree.see(moving[0])
        self._save(message)
        if was_sorted:
            self._layout_changed()

    def _moved_label(self, moving):
        if len(moving) == 1:
            return f"'{next(item['name'] for item in self._items if item['id'] == moving[0])}'"
        return f"{len(moving)} profiles"

    def move_selected(self, offset):
        try:
            order = self._reordered(offset)
            if order is None:
                return
            moving = [identifier for identifier in order if identifier in self.tree.selection()]
            self._apply_order(order, moving,
                              f"Moved {self._moved_label(moving)} {'up' if offset < 0 else 'down'}.")
        except Exception as exc:
            self._error(exc)

    def _move_key(self, offset):
        self.move_selected(offset)
        return "break"

    def _escape_key(self, _event=None):
        self._reset_type_search()
        if self._heading_drag and self._heading_drag[2]:
            self._end_column_drag()
            self._heading_drag = None
        if self._row_drag and self._row_drag.get("active"):
            self._end_row_drag()
            # The button is still down; its release must do nothing.
            self._row_drag = {"cancelled": True}
        return "break"

    # Dragging rows: press on a row, move a few pixels, release at the line.
    def _row_press(self, event):
        self._row_drag = None
        if event.state & (0x0001 | 0x0004):
            return None  # Shift/Ctrl clicks only change the selection.
        if self.tree.identify_region(event.x, event.y) not in {"cell", "tree"}:
            return None
        row = self.tree.identify_row(event.y)
        if not row or row == latest_snapshot.SYSTEM_ID:
            return None
        selection = self.tree.selection()
        keep = row in selection and len(selection) > 1
        moving = ([identifier for identifier in self._displayed_ids() if identifier in selection]
                  if keep else [row])
        self._row_drag = {"row": row, "y": event.y, "moving": moving, "active": False,
                          "target": None, "keep": keep}
        if keep:
            # Keep the multiple selection so it can be dragged as a block;
            # a plain click still selects only this row on release.
            self._reset_type_search()
            self.tree.focus_set()
            return "break"
        return None

    def _row_motion(self, event):
        drag = self._row_drag
        if not drag or drag.get("cancelled"):
            return None
        if not drag["active"]:
            if abs(event.y - drag["y"]) < 5:
                return None
            drag["active"] = True
            self.tree.configure(cursor="sb_v_double_arrow")
        height = self.tree.winfo_height()
        if event.y < 12:
            self.tree.yview_scroll(-1, "units")
        elif event.y > height - 12:
            self.tree.yview_scroll(1, "units")
        drag["target"] = self._drop_position(event.y)
        self._show_drop_line(drag["target"])
        return "break"

    def _row_release(self, event):
        drag, self._row_drag = self._row_drag, None
        if not drag:
            return None
        if drag.get("cancelled"):
            return "break"
        if not drag["active"]:
            if drag["keep"] and not self._checkbox_column(event):
                self.tree.selection_set(drag["row"])
                self.tree.focus(drag["row"])
            return None
        self._end_row_drag()
        if drag["target"] is not None:
            self._drop_rows(drag["moving"], drag["target"])
        return "break"

    def _checkbox_column(self, event):
        """The checkbox column ('rotation_enabled', 'history', 'image_updates') under the event, else None."""
        display = self.tree.identify_column(event.x)
        if self.tree.identify_region(event.x, event.y) != "cell" or not display:
            return None
        index = int(display[1:]) - 1
        if (0 <= index < len(self._visible_columns)
                and self._visible_columns[index] in {"rotation_enabled", "history", "image_updates"}):
            return self._visible_columns[index]
        return None

    def _drop_position(self, y):
        """Gap index in the displayed normal rows where the dragged rows land."""
        rows = self._displayed_ids()
        if not rows:
            return None
        row = self.tree.identify_row(y)
        if row == latest_snapshot.SYSTEM_ID:
            return 0  # The pinned snapshot row stays first.
        if row in rows:
            _x, top, _width, height = self.tree.bbox(row) or (0, y, 0, 0)
            return rows.index(row) + (1 if y >= top + height / 2 else 0)
        visible = [identifier for identifier in rows if self.tree.bbox(identifier)]
        if visible and y < self.tree.bbox(visible[0])[1]:
            return rows.index(visible[0])
        return len(rows)

    def _show_drop_line(self, position):
        rows = self._displayed_ids()
        box = None
        if position is not None and rows:
            if position < len(rows):
                box = self.tree.bbox(rows[position])
                y = box[1] if box else None
            else:
                box = self.tree.bbox(rows[-1])
                y = box[1] + box[3] if box else None
        if not box:
            if self._drop_line is not None:
                self._drop_line.place_forget()
            return
        if self._drop_line is None:
            self._drop_line = tk.Frame(self.tree, height=2, background="#1a73e8")
        self._drop_line.place(x=0, y=max(0, y - 1), relwidth=1, height=2)
        self._drop_line.lift()

    def _end_row_drag(self):
        self.tree.configure(cursor="")
        if self._drop_line is not None:
            self._drop_line.place_forget()

    def _drop_rows(self, moving, position):
        try:
            rows = self._displayed_ids()
            order = ([row for row in rows[:position] if row not in moving] + list(moving)
                     + [row for row in rows[position:] if row not in moving])
            if order == rows:
                return
            self._apply_order(order, list(moving),
                              f"Moved {self._moved_label(moving)} to position {order.index(moving[0]) + 1}.")
        except Exception as exc:
            self._error(exc)

    def library_fingerprint(self):
        """A cheap text that changes whenever the profile list or its rotation inputs change."""
        return json.dumps([self._items, sorted(self._rotation_ids), self._library.get("rotation"),
                           self.enabled_var.get(), self.interval_var.get(), self.unit_var.get()],
                          sort_keys=True, default=str)

    def get_library(self):
        """Return a validated copy, including current rotation inputs and row order."""
        try:
            interval = int(self.interval_var.get().strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("Rotation interval must be a whole number.") from exc
        candidate = deepcopy(self._library)
        candidate["items"] = deepcopy(self._items)
        candidate["rotation"] = {
            "enabled": bool(self.enabled_var.get()),
            "interval": interval,
            "unit": self.unit_var.get(),
            "order": [item["id"] for item in self._items if item["id"] in self._rotation_ids],
            "random_shuffle": bool(self.random_shuffle_var.get()),
            "keep_last_position": bool(self.keep_last_position_var.get()),
            "preload_next": bool(self.preload_next_var.get()),
        }
        candidate = normalize_library(candidate)
        if candidate["rotation"]["enabled"] and not candidate["items"]:
            raise ValueError("Add at least one image profile before enabling rotation.")
        return candidate

    def _show_check_summary(self, summary):
        """Show a newly finished "Check for new image" batch below the buttons."""
        try:
            serial, text = summary
        except (TypeError, ValueError):
            return
        if self._check_summary_serial is None:
            # A batch that finished before this window opened is not news.
            self._check_summary_serial = serial
            return
        if serial != self._check_summary_serial:
            self._check_summary_serial = serial
            if text:
                self._show_notice(str(text))

    def _follow_rotation_switch(self, switch):
        """Show a rotation switch made in the tray menu, so a later save keeps it."""
        try:
            serial, enabled = switch
        except (TypeError, ValueError):
            return
        if self._rotation_switch_serial is None:
            # This window read profiles.toml after that switch.
            self._rotation_switch_serial = serial
            return
        if serial == self._rotation_switch_serial:
            return
        self._rotation_switch_serial = serial
        enabled = bool(enabled)
        if bool(self.enabled_var.get()) == enabled:
            return
        self._quiet = True
        try:
            self.enabled_var.set(enabled)
        finally:
            self._quiet = False
        self._library["rotation"]["enabled"] = enabled
        self._saved_state["rotation"]["enabled"] = enabled
        if self._on_save is not None:
            try:
                # profiles.toml already holds it; this only updates the host's saved state.
                self._on_save(self.get_library())
            except Exception:
                pass

    def _poll_status(self):
        self._after_id = None
        if self._closed:
            return
        try:
            self._apply_runtime_status(self._status())
        except Exception:
            self.status_var.set("Rotation status is currently unavailable.")
        self._after_id = self.frame.after(1000, self._poll_status)

    def _apply_runtime_status(self, status):
        if not isinstance(status, dict):
            self.status_var.set(str(status))
            return
        self.status_var.set(str(status.get("text", "")))
        runtime = {
            "active_profile_id": status.get("active_profile_id"),
            "download": status.get("download") if isinstance(status.get("download"), dict) else None,
            "queued": tuple(status.get("queued") or ()),
            "failures": dict(status.get("failures") or {}),
            "profiles": status.get("profiles", {}) if isinstance(status.get("profiles", {}), dict) else {},
            "last_downloads": status.get("last_downloads", {}),
            "image_records": status.get("image_records", {}),
            "latest_snapshot": status.get("latest_snapshot"),
            "wallpaper_position": status.get("wallpaper_position", "-"),
            "output_resolution": status.get("output_resolution", "-"),
            "display_time_zone": status.get("display_time_zone", "system"),
            "checking": status.get("checking"),
        }
        self._show_check_summary(status.get("check_summary"))
        self._follow_rotation_switch(status.get("rotation_switch"))
        period_reference = (dt.date.today(), dt.datetime.now(dt.timezone.utc).date())
        if runtime == self._runtime and period_reference == getattr(self, "_period_reference", None):
            return
        self._period_reference = period_reference
        self._runtime = runtime
        self._row_entries = {}
        if self._system_snapshot is not None:
            self._snapshot = status.get("latest_snapshot", self._system_snapshot())
            active = latest_snapshot.SYSTEM_ID == runtime["active_profile_id"]
            self.tree.item(latest_snapshot.SYSTEM_ID, values=self._row_values(self._system_item()),
                           tags=("active",) if active else ())
        for item in self._items:
            identifier = item["id"]
            if self.tree.exists(identifier):
                active = identifier == runtime["active_profile_id"]
                self.tree.item(identifier, values=self._row_values(item),
                               tags=("active",) if active else ())
        self._color_auto_priorities()
        self._sort_rows()
        if self.tree.selection() == (latest_snapshot.SYSTEM_ID,):
            # Refresh the snapshot row's actions.
            self._selection_changed()
        else:
            self._update_details()

    def _on_destroy(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
