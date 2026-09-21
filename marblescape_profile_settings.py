"""Embedded Tk settings editor for local image-profile drafts.

ProfilesSettings(parent, library, capture_settings, on_load, on_apply=None,
status=None) owns
only a local draft. get_library() validates and copies that draft; its caller
persists it on Apply. on_load receives a settings copy for the Image form.
close() cancels status polling and is safe to call more than once.
"""

from copy import deepcopy
import math
import tkinter as tk
from tkinter import messagebox, ttk

from marblescape_copernicus import get_highlight, get_layer, get_product, get_theme
from marblescape_eumetsat import THEME_LABELS as _EUMETSAT_THEME_LABELS
from marblescape_profiles import new_profile_id, normalize_library
from marblescape_time import format_display_datetime, format_utc_datetime

_SOURCE_LABELS = {"eumetsat": "EUMETSAT", "goes_east": "GOES-East",
                  "goes_west": "GOES-West", "solar": "Solar / Sun",
                  "himawari": "Himawari", "slider": "CIRA SLIDER",
                  "copernicus": "Copernicus", "worldview": "NASA Worldview"}
_PRESET_LABELS = {"full_earth": "Full Earth", "europe": "Europe",
                  "mediterranean": "Mediterranean", "central_europe": "Central Europe",
                  "custom": "Custom"}


def _text(value, fallback="—"):
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


def _enabled_wms_layers(settings):
    return [entry for entry in settings.get("layers", [])
            if isinstance(entry, dict) and entry.get("kind") == "wms"
            and entry.get("enabled", True)]


def _profile_selection(settings):
    provider = settings.get("source", {}).get("provider", "eumetsat")
    profile = settings.get("sources", {}).get(provider, {})
    if provider == "copernicus":
        _configuration, product, layer, _highlight = _copernicus_labels(profile)
        return f"{product} · {layer}"
    if provider == "slider":
        satellite, separator, sector = str(profile.get("area", "")).partition("---")
        area = f"{_text(satellite)} · {_text(sector)}" if separator else _text(profile.get("area"))
        return f"{area} · {_text(profile.get('product'))}"
    if provider == "worldview":
        return _text(profile.get("area"))
    if provider in {"goes_east", "goes_west", "himawari"}:
        return f"{_text(profile.get('area'))} · {_text(profile.get('product'))}"
    if provider == "solar":
        return _text(profile.get("product"))
    if provider == "eumetsat":
        return (
            f"{_text(profile.get('satellite'))} · {_text(profile.get('mission'))} · "
            f"{_text(profile.get('product_type'))}"
        )
    preset = _PRESET_LABELS.get(settings.get("view", {}).get("preset"),
                                _text(settings.get("view", {}).get("preset")))
    layers = _enabled_wms_layers(settings)
    layer = _text(layers[0].get("name")) if layers else "—"
    return f"{preset} · {layer}"


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
    provider = settings.get("source", {}).get("provider", "eumetsat")
    if (provider == "worldview" and str(
            settings.get("sources", {}).get(provider, {}).get("product", "")
    ).casefold() == "timeless"):
        return "Timeless"
    fixed = _fixed_profile_date(settings)
    if fixed:
        return f"Fixed · {fixed}"
    loaded = _format_source_time((metadata or {}).get("source_time"), time_zone)
    return f"Latest · {loaded}" if loaded else "Latest · not loaded yet"


def _format_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return _text(value)
    return f"{number:g}" if math.isfinite(number) else _text(value)


def _output_resolution(settings):
    output = settings.get("output", {})
    width = output.get("width")
    height = output.get("height")
    if width in (None, ""):
        return "—"
    if height not in (None, "", 0, "0"):
        return f"{width} × {height}"
    ratio_text = _text(output.get("aspect_ratio"), "auto")
    try:
        if ":" in ratio_text or "/" in ratio_text:
            separator = ":" if ":" in ratio_text else "/"
            left, right = ratio_text.split(separator, 1)
            ratio = float(left) / float(right)
        else:
            ratio = float(ratio_text)
        calculated_height = round(int(width) / ratio)
        if not math.isfinite(ratio) or ratio <= 0 or calculated_height <= 0:
            raise ValueError
        return f"{width} × {calculated_height} ({ratio_text})"
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return f"{width} × auto ({ratio_text})"


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


class ProfilesSettings:
    def __init__(self, parent, library, capture_settings, on_load,
                 on_apply=None, status=None):
        self._library = normalize_library(library)
        self._capture_settings = capture_settings
        self._on_load = on_load
        self._on_apply = on_apply
        self._status = status
        self._closed = False
        self._after_id = None
        self._runtime = {"active_profile_id": None, "profiles": {},
                         "wallpaper_position": "—", "display_time_zone": "system"}
        by_id = {item["id"]: item for item in self._library["items"]}
        order = self._library["rotation"]["order"]
        self._items = [by_id[identifier] for identifier in order]
        self._items += [item for item in self._library["items"] if item["id"] not in order]
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.bind("<Destroy>", self._on_destroy, add="+")
        rotation = self._library["rotation"]
        self.name_var = tk.StringVar(master=self.frame)
        self.enabled_var = tk.BooleanVar(master=self.frame, value=rotation["enabled"])
        self.interval_var = tk.StringVar(master=self.frame, value=str(rotation["interval"]))
        self.unit_var = tk.StringVar(master=self.frame, value=rotation["unit"])
        self.status_var = tk.StringVar(master=self.frame)
        self.detail_vars = {
            key: tk.StringVar(master=self.frame, value="—")
            for key in ("configuration", "highlight", "area", "product", "source_resolution",
                        "output_resolution", "fit_zoom", "wallpaper_position", "time_utc", "cache")
        }

        ttk.Label(self.frame, text="Saved image profiles").grid(row=0, column=0, sticky="w", pady=(0, 5))
        list_frame = ttk.Frame(self.frame)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_frame, columns=("name", "source", "selection", "time"), show="headings",
                                 selectmode="browse", height=7)
        self.tree.heading("name", text="Profile name")
        self.tree.heading("source", text="Source")
        self.tree.heading("selection", text="Selection")
        self.tree.heading("time", text="Time")
        self.tree.column("name", width=150, minwidth=100, stretch=True)
        self.tree.column("source", width=95, minwidth=75, stretch=True)
        self.tree.column("selection", width=205, minwidth=130, stretch=True)
        self.tree.column("time", width=175, minwidth=145, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

        details = ttk.LabelFrame(self.frame, text="Selected profile details", padding=7)
        details.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        details.columnconfigure(1, weight=1)
        details.columnconfigure(3, weight=1)
        detail_rows = (
            ("Configuration", "configuration", "Highlight", "highlight"),
            ("Area / location", "area", "Product / layer", "product"),
            ("Source resolution", "source_resolution", "Output resolution", "output_resolution"),
            ("Fit mode / zoom", "fit_zoom", "Wallpaper position", "wallpaper_position"),
        )
        self.detail_value_labels = []
        for row, (left_label, left_key, right_label, right_key) in enumerate(detail_rows):
            ttk.Label(details, text=left_label).grid(row=row, column=0, padx=(0, 6), pady=2, sticky="nw")
            left_value = ttk.Label(details, textvariable=self.detail_vars[left_key], justify="left")
            left_value.grid(row=row, column=1, padx=(0, 14), pady=2, sticky="nw")
            ttk.Label(details, text=right_label).grid(row=row, column=2, padx=(0, 6), pady=2, sticky="nw")
            right_value = ttk.Label(details, textvariable=self.detail_vars[right_key], justify="left")
            right_value.grid(row=row, column=3, pady=2, sticky="nw")
            self.detail_value_labels.extend((left_value, right_value))
        ttk.Label(details, text="Acquisition time (UTC)").grid(
            row=4, column=0, padx=(0, 6), pady=2, sticky="nw"
        )
        utc_value = ttk.Label(details, textvariable=self.detail_vars["time_utc"], justify="left")
        utc_value.grid(row=4, column=1, columnspan=3, pady=2, sticky="nw")
        self.detail_value_labels.append(utc_value)
        ttk.Label(details, text="Cache status").grid(row=5, column=0, padx=(0, 6), pady=2, sticky="nw")
        cache_value = ttk.Label(details, textvariable=self.detail_vars["cache"], justify="left")
        cache_value.grid(row=5, column=1, columnspan=3, pady=2, sticky="nw")
        self.detail_value_labels.append(cache_value)

        name_frame = ttk.Frame(self.frame)
        name_frame.grid(row=3, column=0, sticky="ew", pady=(9, 5))
        name_frame.columnconfigure(1, weight=1)
        ttk.Label(name_frame, text="Profile name").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.name_entry = ttk.Entry(name_frame, textvariable=self.name_var, width=32)
        self.name_entry.grid(row=0, column=1, sticky="ew")

        button_frame = ttk.Frame(self.frame)
        button_frame.grid(row=4, column=0, sticky="w", pady=(0, 8))
        self.buttons = {}
        actions = (
            ("Add current image", self.add_current, 0, 0),
            ("Update selected", self.update_selected, 0, 1),
            ("Load into Image", self.load_selected, 0, 2),
            ("Apply profile", self.apply_selected, 0, 3),
            ("Rename", self.rename_selected, 1, 0),
            ("Delete", self.delete_selected, 1, 1),
            ("Move up", lambda: self.move_selected(-1), 1, 2),
            ("Move down", lambda: self.move_selected(1), 1, 3),
        )
        for label, callback, row, column in actions:
            button = ttk.Button(button_frame, text=label, command=callback)
            button.grid(row=row, column=column, padx=(0, 6), pady=3, sticky="ew")
            self.buttons[label] = button

        rotation_frame = ttk.LabelFrame(self.frame, text="Rotation", padding=8)
        rotation_frame.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(rotation_frame, text="Rotate through the profiles in the listed order",
                        variable=self.enabled_var).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(rotation_frame, text="Change every").grid(row=1, column=0, padx=(0, 10), sticky="w")
        ttk.Entry(rotation_frame, textvariable=self.interval_var, width=9).grid(row=1, column=1, padx=(0, 6), sticky="w")
        ttk.Combobox(rotation_frame, textvariable=self.unit_var, values=("minutes", "days", "weeks"),
                     state="readonly", width=10).grid(row=1, column=2, sticky="w")
        ttk.Label(rotation_frame, text=(
            "Three attempts per profile, then skip. Restart begins with the first profile.\n"
            "Enable General > Set wallpaper automatically to update the desktop."
        ), wraplength=620, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.hint = ttk.Label(self.frame, text=(
            "Apply saves profiles and rotation. Load copies a profile to the Image tab; "
            "Apply loads its picture."), wraplength=650, justify="left")
        self.hint.grid(row=6, column=0, sticky="ew", pady=(0, 6))
        self.status_label = ttk.Label(self.frame, textvariable=self.status_var, wraplength=650, justify="left")
        self.status_label.grid(row=7, column=0, sticky="ew")
        self.tree.tag_configure("active", background="#dceeff")
        self.frame.bind("<Configure>", self._resize_text, add="+")
        self._refresh(self._items[0]["id"] if self._items else None)
        if status is not None:
            self._poll_status()

    def _resize_text(self, event):
        if event.widget is self.frame:
            width = max(160, event.width - 12)
            self.hint.configure(wraplength=width)
            self.status_label.configure(wraplength=width)
            detail_width = max(90, (width - 270) // 2)
            for label in self.detail_value_labels:
                label.configure(wraplength=detail_width)

    def _row_values(self, item):
        identifier = item["id"]
        settings = item["settings"]
        provider = settings.get("source", {}).get("provider", "eumetsat")
        name = item["name"]
        if identifier == self._runtime.get("active_profile_id"):
            name += " · Active"
        metadata = self._runtime.get("profiles", {}).get(identifier, {})
        return (name, _SOURCE_LABELS.get(provider, provider),
                _profile_selection(settings), _profile_time(
                    settings, metadata, self._runtime.get("display_time_zone", "system")
                ))

    def _detail_values(self, item):
        settings = item["settings"]
        provider = settings.get("source", {}).get("provider", "eumetsat")
        profile = settings.get("sources", {}).get(provider, {})
        view = settings.get("view", {})
        output = settings.get("output", {})
        if provider == "copernicus":
            configuration, product, layer, highlight = _copernicus_labels(profile)
            location = f"{_format_number(profile.get('latitude'))}, {_format_number(profile.get('longitude'))}"
            source_resolution = f"Map zoom {_text(profile.get('map_zoom'))}"
            fit_zoom = f"Map extent · zoom {_text(profile.get('map_zoom'))}"
        elif provider in {"goes_east", "goes_west"}:
            configuration, highlight = "NOAA STAR", "—"
            location = _text(profile.get("area"))
            product = _text(profile.get("product"))
            layer = "—"
            source_resolution = _source_resolution(profile.get("resolution"))
            fit_zoom = f"{_text(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        elif provider == "solar":
            configuration, highlight = "NOAA STAR SUVI", "—"
            location = _text(profile.get("area"), "Sun")
            product = _text(profile.get("product"))
            layer = "—"
            source_resolution = _source_resolution(profile.get("resolution"))
            fit_zoom = f"{_text(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        elif provider == "himawari":
            area_id = str(profile.get("area", ""))
            configuration = ("NICT Himawari Viewer" if area_id.startswith("nict_")
                             else "JMA Himawari Real-Time Image")
            highlight = "—"
            location = _text(profile.get("area"))
            product = _text(profile.get("product"))
            layer = "—"
            source_resolution = _source_resolution(profile.get("resolution"))
            fit_zoom = f"{_text(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        elif provider == "slider":
            area_id = str(profile.get("area", ""))
            satellite, separator, sector = area_id.partition("---")
            configuration, highlight = "CIRA SLIDER", "No map overlays"
            location = (f"{_text(satellite)} · {_text(sector)}" if separator
                        else _text(area_id))
            product = _text(profile.get("product"))
            layer = "—"
            source_resolution = _source_resolution(profile.get("resolution"))
            fit_zoom = f"{_text(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        elif provider == "worldview":
            configuration, highlight = "NASA GIBS / Worldview", "No map overlays"
            location = "Global (EPSG:4326)"
            date_value = _text(profile.get("product"), "latest")
            product = ("Latest available" if date_value.casefold() == "latest" else
                       "Timeless" if date_value.casefold() == "timeless" else f"Fixed · {date_value}")
            layer = _text(profile.get("area"))
            source_resolution = _source_resolution(profile.get("resolution"))
            fit_zoom = f"{_text(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        else:
            preset_value = view.get("preset")
            preset = _PRESET_LABELS.get(preset_value, _text(preset_value))
            theme = _EUMETSAT_THEME_LABELS.get(
                profile.get("theme"), _text(profile.get("theme"))
            )
            configuration = f"EUMETView · {theme} · {preset}"
            if profile.get("fill_gaps"):
                configuration += (
                    " · Gap fill "
                    f"{profile.get('gap_fill_lookback_hours', 12)} h"
                )
            highlight = (
                f"{_text(profile.get('satellite'))} · "
                f"{_text(profile.get('mission'))}"
            )
            if preset_value == "custom" and isinstance(view.get("bbox"), (list, tuple)):
                location = ", ".join(_format_number(value) for value in view["bbox"])
            else:
                location = preset
            layers = _enabled_wms_layers(settings)
            product = _text(profile.get("product_type"))
            layer = ", ".join(_text(entry.get("name")) for entry in layers) or "—"
            render_scale = output.get("render_scale", "auto")
            source_resolution = f"WMS render scale {_text(render_scale)}"
            fit_zoom = f"{_text(view.get('fit_mode'))} · {_format_number(view.get('zoom'))}x"
        metadata = self._runtime.get("profiles", {}).get(item["id"], {})
        wallpaper = _text(self._runtime.get("wallpaper_position"))
        fixed = _fixed_profile_date(settings)
        source_time = metadata.get("source_time")
        if fixed:
            utc_time = f"Fixed date · {fixed}"
        elif source_time:
            try:
                utc_time = format_utc_datetime(source_time)
            except (TypeError, ValueError, OverflowError):
                utc_time = "Not loaded yet"
        else:
            utc_time = "Not loaded yet"
        return {
            "configuration": configuration,
            "highlight": highlight,
            "area": location,
            "product": f"{product} · {layer}",
            "source_resolution": source_resolution,
            "output_resolution": _output_resolution(settings),
            "fit_zoom": fit_zoom,
            "wallpaper_position": f"{wallpaper} (global)",
            "time_utc": utc_time,
            "cache": _cache_status(
                metadata, self._runtime.get("display_time_zone", "system")
            ),
        }

    def _update_details(self, item=None):
        if item is None:
            try:
                item = self._items[self._selected_index()]
            except (ValueError, StopIteration):
                item = None
        values = ({key: "—" for key in self.detail_vars} if item is None
                  else self._detail_values(item))
        for key, variable in self.detail_vars.items():
            variable.set(values[key])

    def _candidate(self, items):
        candidate = deepcopy(self._library)
        candidate["items"] = deepcopy(items)
        candidate["rotation"]["order"] = [item["id"] for item in items]
        return normalize_library(candidate)

    def _commit(self, items, selected=None):
        candidate = self._candidate(items)
        self._library = candidate
        self._items = candidate["items"]
        self._refresh(selected)

    def _refresh(self, selected=None):
        yview = self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        for item in self._items:
            active = item["id"] == self._runtime.get("active_profile_id")
            self.tree.insert("", "end", iid=item["id"], values=self._row_values(item),
                             tags=("active",) if active else ())
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.focus(selected)
            self.tree.see(selected)
        elif yview and self.tree.get_children():
            self.tree.yview_moveto(yview[0])
        self._selection_changed()

    def _selected_index(self):
        selected = self.tree.selection()
        if not selected:
            raise ValueError("Select a profile first.")
        return next(index for index, item in enumerate(self._items) if item["id"] == selected[0])

    def _selection_changed(self, _event=None):
        try:
            index = self._selected_index()
        except (ValueError, StopIteration):
            index = None
        if index is not None:
            self.name_var.set(self._items[index]["name"])
            self._update_details(self._items[index])
        else:
            self._update_details()
        for label in (
            "Update selected", "Load into Image", "Apply profile", "Rename", "Delete"
        ):
            self.buttons[label].state(["!disabled"] if index is not None else ["disabled"])
        if self._on_apply is None:
            self.buttons["Apply profile"].state(["disabled"])
        self.buttons["Move up"].state(["!disabled"] if index is not None and index > 0 else ["disabled"])
        self.buttons["Move down"].state(["!disabled"] if index is not None and index + 1 < len(self._items) else ["disabled"])

    def _error(self, error):
        messagebox.showerror("Image profiles", str(error), parent=self.frame.winfo_toplevel())

    def add_current(self):
        try:
            item = {"id": new_profile_id(), "name": self.name_var.get(), "settings": {}}
            items = deepcopy(self._items) + [item]
            self._candidate(items)  # Check the name before capturing or changing data.
            item["settings"] = deepcopy(self._capture_settings())
            self._commit(items, item["id"])
        except Exception as exc:
            self._error(exc)

    def update_selected(self):
        try:
            index = self._selected_index()
            items = deepcopy(self._items)
            items[index]["settings"] = deepcopy(self._capture_settings())
            self._commit(items, items[index]["id"])
        except Exception as exc:
            self._error(exc)

    def load_selected(self):
        try:
            item = self._items[self._selected_index()]
            self._on_load(deepcopy(item["settings"]))
        except Exception as exc:
            self._error(exc)

    def apply_selected(self):
        try:
            item = self._items[self._selected_index()]
            if self._on_apply is None:
                raise ValueError("Applying a profile is unavailable in this window.")
            self._on_apply(deepcopy(item["settings"]))
        except Exception as exc:
            self._error(exc)

    def rename_selected(self):
        try:
            index = self._selected_index()
            items = deepcopy(self._items)
            items[index]["name"] = self.name_var.get()
            self._commit(items, items[index]["id"])
        except Exception as exc:
            self._error(exc)

    def delete_selected(self):
        try:
            index = self._selected_index()
            items = deepcopy(self._items)
            del items[index]
            selected = items[min(index, len(items) - 1)]["id"] if items else None
            self._commit(items, selected)
            if not items:
                self.name_var.set("")
        except Exception as exc:
            self._error(exc)

    def move_selected(self, offset):
        try:
            index = self._selected_index()
            target = index + offset
            if not 0 <= target < len(self._items):
                return
            items = deepcopy(self._items)
            selected = items[index]["id"]
            items[index], items[target] = items[target], items[index]
            self._commit(items, selected)
        except Exception as exc:
            self._error(exc)

    def get_library(self):
        """Return a validated copy, including current rotation inputs and row order."""
        try:
            interval = int(self.interval_var.get().strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("Rotation interval must be a whole number.") from exc
        candidate = deepcopy(self._library)
        candidate["items"] = deepcopy(self._items)
        candidate["rotation"] = {"enabled": bool(self.enabled_var.get()), "interval": interval,
                                 "unit": self.unit_var.get(), "order": [item["id"] for item in self._items]}
        candidate = normalize_library(candidate)
        if candidate["rotation"]["enabled"] and not candidate["items"]:
            raise ValueError("Add at least one image profile before enabling rotation.")
        return candidate

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
            "profiles": status.get("profiles", {}) if isinstance(status.get("profiles", {}), dict) else {},
            "wallpaper_position": status.get("wallpaper_position", "—"),
            "display_time_zone": status.get("display_time_zone", "system"),
        }
        if runtime == self._runtime:
            return
        self._runtime = runtime
        for item in self._items:
            identifier = item["id"]
            if self.tree.exists(identifier):
                active = identifier == runtime["active_profile_id"]
                self.tree.item(identifier, values=self._row_values(item),
                               tags=("active",) if active else ())
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
