"""Tk controls for Copernicus Browser selections and live acquisition dates."""

import datetime as dt
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, colorchooser
import webbrowser

from marblescape_catalogue_activity import CatalogueActivity, reserve_text_lines
from marblescape_network import NETWORK_ACTIVITY
from marblescape_source_layout import COLOR_VALUE_WIDTH, SOURCE_COMBO_WIDTH, configure_source_columns
from marblescape_theme import (follow_wrap_width, on_theme_change, palette, set_scale_enabled, style_scale,
                               style_swatch)

from marblescape_copernicus import (
    AUTO_IMAGE_SIZE_LABEL,
    IMAGE_SIZES,
    image_size_label,
    tone_rule,
    ACCOUNT_SETTINGS_URL,
    CopernicusClient,
    DEFAULT_PROFILE,
    LOOKBACK_DAYS,
    MAX_QUARTER_OFFSET,
    MAX_MONTH_OFFSET,
    catalogue_revision,
    get_product,
    highlights,
    map_zooms,
    map_zooms_for_view,
    missions,
    normalize_profile,
    resolve_image_size,
    products,
    rolling_quarter_start,
    rolling_month_start,
    supports_cloud_filter,
    themes,
)


LATEST_LABEL = "Latest available"
# Use auto recommendation > Priority: the first one is Newest for mosaics.
AUTO_PRIORITY_LABELS = {"Fewest clouds": "fewest_clouds", "Newest": "newest",
                        "Data coverage": "full_coverage"}
LOADING_DATES_LABEL = "Loading dates…"
# Example scene: a Copernicus Browser highlight (saved as "highlight"); None is
# the own location. The row shows only for configurations that have scenes.
CUSTOM_HIGHLIGHT_LABEL = "None"
COVERAGE_LABELS = {
    "Single latest acquisition": "single",
    "Fill gaps with earlier imagery (use latest imagery of valid lookback)": "fill_gaps",
}
LOOKBACK_PERIODS = {
    30: "1 month", 45: "1.5 months", 60: "2 months", 90: "3 months",
    120: "4 months", 180: "6 months", 270: "9 months", 365: "1 year",
    550: "1.5 years", 730: "2 years", 920: "2.5 years", 1095: "3 years",
}
LOOKBACK_LABELS = {
    (f"{days} days ({LOOKBACK_PERIODS[days]})"
     if days in LOOKBACK_PERIODS else f"{days} days"): days
    for days in LOOKBACK_DAYS
}
# A fixed date: the limit filters the date list and leaves out that day's cloudier tiles.
FIXED_DATE_CLOUD_HINT = ("With a fixed date the limit filters the dates offered above and leaves "
                         "out cloudier tiles of that day.")
CLOUD_HINT = (
    "Inclusive tile limit: 20% accepts 20% or less; 0% may find no acquisition. "
    "The estimate is not for the exact map view. 100% allows all acquisitions with an "
    "estimate; Landsat night passes without one are skipped. "
    "Available only for supported optical layers."
)
QUARTER_MODE_LABELS = {
    "Specific quarter": "specific",
    "Relative to now": "relative_quarter",
    "Latest available": "latest",
}
QUARTER_OFFSET_LABELS = {
    ("Current quarter" if offset == 0 else "1 quarter ago" if offset == 1
     else f"{offset} quarters ago"): offset
    for offset in range(MAX_QUARTER_OFFSET + 1)
}
MONTH_OFFSET_LABELS = {
    ("Current month" if offset == 0 else "1 month ago" if offset == 1
     else f"{offset} months ago"): offset
    for offset in range(MAX_MONTH_OFFSET + 1)
}


def _natural_layer_rank(layer):
    name = re.sub(r"[^a-z0-9]+", "", str(layer.get("name", "")).casefold())
    if name == "truecolor" or name == "truecolour":
        return 0
    if "truecolor" in name or "truecolour" in name:
        return 1
    if "naturalcolor" in name or "naturalcolour" in name:
        return 2
    return 3


def _natural_product_rank(product):
    rank = min((_natural_layer_rank(layer) for layer in product["layers"]), default=3)
    data_types = {layer.get("data_type") for layer in product["layers"]}
    order = ("sentinel-2-l2a", "sentinel-2-l1c", "sentinel-3-olci", "landsat-ot-l1")
    return rank, min((order.index(item) for item in data_types if item in order), default=len(order))


# Shown like the buttons; saved in lower case.
NO_DATA_LABELS = {"transparent": "Transparent", "blur": "Blur", "blur_edge": "Edge Blur"}


class CopernicusSettings:
    def __init__(self, parent, profile=None, auth=None, timeout=90,
                 user_agent="MarbleScape", output_size=(1920, 1080), on_change=None,
                 catalogue_client=None, catalogue_retries=2,
                 reference_date=None, catalogue_parent=None, auth_parent=None, credits_parent=None,
                 rendering_parent=None, recommend_parent=None):
        self.frame = ttk.Frame(parent)
        # Image resolution and the tone corrections; the host may show them in
        # its own Rendering section.
        self.rendering_frame = ttk.Frame(rendering_parent if rendering_parent is not None else self.frame)
        configure_source_columns(self.rendering_frame)
        # The host may show the account section in its own place (the Image tab's end).
        self._auth_parent = auth_parent
        configure_source_columns(self.frame)
        # The host may show the catalogue controls in its own section.
        self.catalogue_frame = ttk.Frame(catalogue_parent if catalogue_parent is not None else self.frame)
        self.catalogue_frame.columnconfigure(1, weight=1)
        self._timeout = timeout
        self._user_agent = user_agent
        self._output_size = output_size
        self._reference_date = reference_date or dt.date.today
        self._on_change = on_change
        self._catalogue_client = catalogue_client
        self._catalogue_retries = max(1, min(9, int(catalogue_retries)))
        self._closed = False
        # The date and zoom lists depend on place, zoom, picture size and cloud
        # limit; a change reloads them after a short pause (_refresh_for_inputs).
        self._date_refresh_after = None
        self._last_date_inputs = None
        self._advice_window = None
        self._auto_controls_ready = False
        self._generation = 0
        self._results = queue.Queue()
        self._usage_results = queue.Queue()
        self._usage_generation = 0
        self._after_id = None
        self._usage_after_id = None
        self._updating = True
        auth = auth if isinstance(auth, dict) else {}

        self._mission_var = tk.StringVar(self.frame)
        self._configuration_var = tk.StringVar(self.frame)
        self._product_var = tk.StringVar(self.frame)
        self._layer_var = tk.StringVar(self.frame)
        self._highlight_var = tk.StringVar(self.frame)
        self._date_var = tk.StringVar(self.frame)
        self._quarter_mode_var = tk.StringVar(self.frame, "Latest available")
        self._quarter_offset_var = tk.StringVar(self.frame, "1 quarter ago")
        self._quarter_resolution_var = tk.StringVar(self.frame)
        self._latitude_var = tk.StringVar(self.frame)
        self._longitude_var = tk.StringVar(self.frame)
        self._zoom_var = tk.StringVar(self.frame)
        self._labels_var = tk.BooleanVar(self.frame)
        self._map_label_color_var = tk.StringVar(self.frame, "#FFFFFF")
        self._borders_var = tk.BooleanVar(self.frame)
        self._map_border_color_var = tk.StringVar(self.frame, "#FFFFFF")
        self._coverage_var = tk.StringVar(self.frame)
        self._lookback_var = tk.StringVar(self.frame)
        self._cloud_var = tk.IntVar(self.frame)
        self._cloud_value_var = tk.StringVar(self.frame)
        self._brightness_var = tk.IntVar(self.frame)
        self._brightness_value_var = tk.StringVar(self.frame)
        # Mosaic and regular layers keep their own No-data color; the row shows the applicable one.
        self._no_data_color_var = tk.StringVar(self.frame, DEFAULT_PROFILE["no_data_color"])
        self._scene_no_data_color_var = tk.StringVar(self.frame, DEFAULT_PROFILE["scene_no_data_color"])
        self._no_data_display_var = tk.StringVar(self.frame)
        self._contrast_var = tk.IntVar(self.frame, 100)
        self._contrast_value_var = tk.StringVar(self.frame, "100%")
        self._auto_brightness_var = tk.BooleanVar(self.frame, False)
        self._auto_contrast_var = tk.BooleanVar(self.frame, False)
        # With auto on, a slider shows what auto chose for the picture on screen
        # instead of the manual value, which stays saved.
        self._brightness_auto_var = tk.IntVar(self.frame, 100)
        self._contrast_auto_var = tk.IntVar(self.frame, 100)
        # A layer without a tone rule shows stock values; saved ones stay saved.
        self._stock_var = tk.IntVar(self.frame, 100)
        self._auto_shown = {"brightness": False, "contrast": False}
        self._shown_picture = None
        self._image_size_var = tk.StringVar(self.frame, "Auto (recommended)")
        self._client_id_var = tk.StringVar(self.frame, str(auth.get("client_id", "")))
        self._secret_var = tk.StringVar(self.frame, str(auth.get("client_secret", "")))
        self._status_var = tk.StringVar(self.frame)
        self._credits_status_var = tk.StringVar(self.frame, "Credits not loaded.")
        self._credits_role_var = tk.StringVar(self.frame, "Role: -")
        self._credits_since_var = tk.StringVar(self.frame)
        self._credits_values = {
            (category, field): tk.StringVar(self.frame, "-")
            for category in ("processingUnitsMonthly", "requestsMonthly")
            for field in ("configuration", "consumed", "remaining")
        }

        self._theme_by_label = {}
        self._product_by_label = {}
        self._layer_by_label = {}
        self._highlight_by_label = {}
        self._date_by_label = {LATEST_LABEL: "latest"}
        # Date granularity the date choices were built for, and a date kept
        # across a selection change until the new date list confirms it.
        self._date_unit = None
        self._kept_date = None
        self._dates = []
        self._date_label_before_loading = LATEST_LABEL

        self._mission_combo = self._combo(0, "Mission / dataset", self._mission_var)
        self._configuration_combo = self._combo(1, "Configuration", self._configuration_var)
        self._product_combo = self._combo(2, "Product", self._product_var)
        self._layer_combo = self._combo(3, "Layer", self._layer_var)
        self._highlight_combo = self._combo(4, "Example scene", self._highlight_var)
        date_controls = ttk.Frame(self.frame)
        date_controls.grid(row=5, column=0, columnspan=2, pady=3, sticky="ew")
        configure_source_columns(date_controls)
        self._date_label = ttk.Label(date_controls, text="Date / time")
        # The row's own padding spaces it; the label never makes the row taller.
        self._date_label.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="w")
        self._period_date_label = ttk.Label(date_controls)
        self._date_combo = ttk.Combobox(
            date_controls, textvariable=self._date_var, state="readonly",
            width=SOURCE_COMBO_WIDTH,
        )
        self._date_combo.grid(row=0, column=1, sticky="ew")
        self._quarter_mode_frame = ttk.Frame(date_controls)
        # Rows inside the period controls keep the 6 px gap of the other rows
        # (3 px above and below each); grid() later reuses these options.
        self._quarter_mode_frame.grid(
            row=1, column=0, columnspan=2, pady=0, sticky="ew"
        )
        self._quarter_mode_label = ttk.Label(self._quarter_mode_frame, text="Quarter selection")
        self._quarter_mode_label.grid(row=0, column=0, padx=(0, 6), sticky="w")
        configure_source_columns(self._quarter_mode_frame)
        self._quarter_mode_combo = ttk.Combobox(
            self._quarter_mode_frame, textvariable=self._quarter_mode_var,
            values=tuple(QUARTER_MODE_LABELS), state="readonly", width=SOURCE_COMBO_WIDTH,
        )
        self._quarter_mode_combo.grid(row=0, column=1, sticky="ew")
        self._quarter_offset_label = ttk.Label(self._quarter_mode_frame, text="Quarters back")
        self._quarter_offset_label.grid(row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="w")
        self._quarter_offset_combo = ttk.Combobox(
            self._quarter_mode_frame, textvariable=self._quarter_offset_var,
            values=tuple(QUARTER_OFFSET_LABELS), state="disabled", width=SOURCE_COMBO_WIDTH,
        )
        self._quarter_offset_combo.grid(row=1, column=1, pady=(6, 0), sticky="ew")
        self._quarter_resolution_label = ttk.Label(
            date_controls, textvariable=self._quarter_resolution_var, wraplength=500,
        )
        self._quarter_resolution_label.grid(
            row=2, column=0, columnspan=2, pady=(3, 0), sticky="w"
        )
        # One line while the section is wide enough; wraps only when it is not.
        follow_wrap_width(self._quarter_resolution_label)
        self._latitude_entry = self._entry(6, "Latitude", self._latitude_var)
        self._longitude_entry = self._entry(7, "Longitude", self._longitude_var)
        self._zoom_combo = self._combo(8, "Map zoom", self._zoom_var)
        self._coverage_combo = self._combo(
            9, "Gap fill", self._coverage_var, tuple(COVERAGE_LABELS)
        )
        self._lookback_combo = self._combo(
            10, "Maximum lookback", self._lookback_var, tuple(LOOKBACK_LABELS)
        )
        ttk.Label(self.frame, text="Maximum cloud cover").grid(
            row=11, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        # Slider rows: the auto checkbox (if any), the slider, its value.
        cloud_frame = ttk.Frame(self.frame)
        cloud_frame.grid(row=11, column=1, sticky="ew")
        cloud_frame.columnconfigure(1, weight=1)
        self._cloud_scale = tk.Scale(
            cloud_frame, from_=0, to=100, resolution=5, orient="horizontal",
            showvalue=False, variable=self._cloud_var, command=self._cloud_changed,
            highlightthickness=0,
        )
        self._cloud_scale.grid(row=0, column=1, sticky="ew")
        # The date list filters by the cloud limit: reload it once the slider is let go.
        self._cloud_scale.bind("<ButtonRelease-1>", self._schedule_inputs_refresh, add="+")
        self._cloud_scale.bind("<KeyRelease>", self._schedule_inputs_refresh, add="+")
        cloud_value = ttk.Label(cloud_frame, textvariable=self._cloud_value_var, width=5)
        cloud_value.grid(row=0, column=2, padx=(6, 0), sticky="e")
        style_scale(self._cloud_scale, cloud_value)
        self._cloud_hint = ttk.Label(
            self.frame, wraplength=560, justify="left", text=CLOUD_HINT,
        )
        self._cloud_hint.grid(row=12, column=0, columnspan=2, pady=(0, 3), sticky="w")
        # Wrap at the frame's width, so a short hint stays on one line.
        self._cloud_hint._marblescape_own_wrap = True
        self.frame.bind("<Configure>", lambda event: self._cloud_hint.configure(
            wraplength=max(300, event.width - 10)), add="+")
        # Shown as "3840 × 2160 (4K UHD)"; saved as "3840x2160".
        self._image_size_by_label = {image_size_label(value): value for value in ("auto", *IMAGE_SIZES)}
        self._image_size_combo = self._combo(0, "Image resolution", self._image_size_var,
            tuple(self._image_size_by_label),
            parent=self.rendering_frame)
        self._image_size_combo.bind("<<ComboboxSelected>>", self._image_size_changed)
        self._image_size_var.trace_add("write", lambda *_args: self.refresh_image_size_label())
        self.refresh_image_size_label()
        ttk.Label(self.rendering_frame, text="Brightness correction").grid(
            row=1, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        brightness_frame = ttk.Frame(self.rendering_frame)
        brightness_frame.grid(row=1, column=1, sticky="ew")
        brightness_frame.columnconfigure(1, weight=1)
        # The row's label says what the box corrects, so it reads just "auto".
        self._auto_brightness_button = ttk.Checkbutton(brightness_frame, text="auto",
            variable=self._auto_brightness_var, command=self._tone_mode_changed)
        self._auto_brightness_button.grid(row=0, column=0, padx=(0, 8), sticky="w")
        self._brightness_scale = tk.Scale(
            brightness_frame, from_=25, to=200, resolution=5, orient="horizontal",
            showvalue=False, variable=self._brightness_var, command=self._brightness_changed,
            highlightthickness=0,
        )
        self._brightness_scale.grid(row=0, column=1, sticky="ew")
        brightness_value = ttk.Label(brightness_frame, textvariable=self._brightness_value_var, width=5)
        brightness_value.grid(row=0, column=2, padx=(6, 0), sticky="e")
        style_scale(self._brightness_scale, brightness_value)
        ttk.Label(self.rendering_frame, text="Contrast correction").grid(row=2, column=0, sticky="w", pady=3)
        contrast_frame = ttk.Frame(self.rendering_frame)
        contrast_frame.grid(row=2, column=1, sticky="ew")
        contrast_frame.columnconfigure(1, weight=1)
        self._auto_contrast_button = ttk.Checkbutton(contrast_frame, text="auto",
            variable=self._auto_contrast_var, command=self._tone_mode_changed)
        self._auto_contrast_button.grid(row=0, column=0, padx=(0, 8), sticky="w")
        self._contrast_scale = tk.Scale(contrast_frame, from_=0, to=200, resolution=5,
            orient="horizontal", showvalue=False, variable=self._contrast_var,
            command=self._contrast_changed, highlightthickness=0)
        self._contrast_scale.grid(row=0, column=1, sticky="ew")
        contrast_value = ttk.Label(contrast_frame, textvariable=self._contrast_value_var, width=5)
        contrast_value.grid(row=0, column=2, padx=(6, 0), sticky="e")
        style_scale(self._contrast_scale, contrast_value)

        # The three sliders start at one line: each row reserves the width of
        # the wider auto checkbox, the cloud cover row too, where none is shown.
        def align_sliders():
            reserved = max(self._auto_brightness_button.winfo_reqwidth(),
                           self._auto_contrast_button.winfo_reqwidth()) + 8
            for frame in (cloud_frame, brightness_frame, contrast_frame):
                frame.columnconfigure(0, minsize=reserved)

        on_theme_change(self._auto_contrast_button, align_sliders)
        # The two map overlays of Copernicus Browser, each with a switch and a
        # color, and the No-data color close the Rendering rows.
        self._overlays = {}
        for row, key, text, toggle, color in (
            (3, "labels", "Labels (places, roads, POIs)", self._labels_var, self._map_label_color_var),
            (4, "borders", "Country borders", self._borders_var, self._map_border_color_var),
        ):
            ttk.Checkbutton(self.rendering_frame, text=text, variable=toggle,
                            command=self._map_labels_changed).grid(row=row, column=0, pady=3, sticky="w")
            color_frame = ttk.Frame(self.rendering_frame)
            color_frame.grid(row=row, column=1, sticky="ew")
            preview = tk.Label(color_frame, width=2, height=1)
            style_swatch(preview)
            preview.pack(side="left", padx=(0, 7))
            button = ttk.Button(color_frame, text="Choose color…",
                                command=lambda key=key: self._choose_overlay_color(key))
            button.pack(side="left")
            ttk.Label(color_frame, textvariable=color, width=COLOR_VALUE_WIDTH).pack(side="left", padx=8)
            self._overlays[key] = {"toggle": toggle, "color": color, "preview": preview,
                                   "button": button, "title": f"{text} color"}
            color.trace_add("write", lambda *_, key=key: self._refresh_overlay_preview(key))
            # Loading the theme resets plain tk colors; the swatch shows the chosen one again.
            on_theme_change(preview, lambda key=key: self._refresh_overlay_preview(key))
        self._map_label_color_button = self._overlays["labels"]["button"]
        self._map_border_color_button = self._overlays["borders"]["button"]

        auth_frame = ttk.LabelFrame(self._auth_parent if self._auth_parent is not None else self.frame,
                                    text="Copernicus Data Space access", padding=6)
        self.auth_frame = auth_frame
        ttk.Label(self.rendering_frame, text="No-data color").grid(row=5, column=0, sticky="w", pady=3)
        color_frame = ttk.Frame(self.rendering_frame)
        color_frame.grid(row=5, column=1, sticky="ew")
        self._no_data_preview = tk.Label(color_frame, width=2, height=1)
        style_swatch(self._no_data_preview)
        self._no_data_preview.pack(side="left", padx=(0, 7))
        self._no_data_color_button = ttk.Button(color_frame, text="Choose color…", command=self._choose_no_data_color)
        self._no_data_color_button.pack(side="left")
        # The value sits where the overlay rows above show theirs.
        ttk.Label(color_frame, textvariable=self._no_data_display_var,
                  width=COLOR_VALUE_WIDTH).pack(side="left", padx=(8, 4))
        # Transparent, Blur and Edge Blur equally wide.
        choices = ttk.Frame(color_frame)
        choices.pack(side="left")
        choices.columnconfigure((0, 1, 2), uniform="no_data_choices")
        self._transparent_button = ttk.Button(choices, text="Transparent", command=self._choose_transparent)
        self._blur_button = ttk.Button(choices, text="Blur", command=self._choose_blur)
        self._edge_blur_button = ttk.Button(choices, text="Edge Blur", command=self._choose_edge_blur)
        for column, button in enumerate((self._transparent_button, self._blur_button, self._edge_blur_button)):
            # The gap is split, so every column holds an equally wide button.
            button.grid(row=0, column=column, padx=3, sticky="ew")
        for variable in (self._no_data_color_var, self._scene_no_data_color_var):
            variable.trace_add("write", lambda *_: self._refresh_no_data_preview())
        on_theme_change(self._no_data_preview, self._refresh_no_data_preview)
        if rendering_parent is None:
            self.rendering_frame.grid(row=18, column=0, columnspan=2, sticky="ew")
        if self._auth_parent is None:
            auth_frame.grid(row=19, column=0, columnspan=2, pady=(7, 3), sticky="ew")
        else:
            auth_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        auth_frame.columnconfigure(1, weight=1)
        ttk.Label(auth_frame, text="OAuth Client ID").grid(
            row=0, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        ttk.Entry(auth_frame, textvariable=self._client_id_var).grid(
            row=0, column=1, pady=3, sticky="ew"
        )
        ttk.Label(auth_frame, text="OAuth Client secret").grid(
            row=1, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        ttk.Entry(auth_frame, textvariable=self._secret_var, show="●").grid(
            row=1, column=1, pady=3, sticky="ew"
        )
        # The host may show the credits as an own section (the Access tab's end).
        if credits_parent is None:
            credits_frame = ttk.LabelFrame(auth_frame, text="Credits", padding=6)
            credits_frame.grid(row=2, column=0, columnspan=2, pady=(7, 3), sticky="ew")
        else:
            credits_frame = ttk.LabelFrame(credits_parent, text="Credits", padding=8)
        self.credits_frame = credits_frame
        credits_frame.columnconfigure((1, 2, 3), weight=1)
        ttk.Label(credits_frame, textvariable=self._credits_role_var).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self._credits_refresh_button = ttk.Button(
            credits_frame, text="Refresh credits", command=self.refresh_usage
        )
        self._credits_refresh_button.grid(row=0, column=3, sticky="e")
        for column, variable in enumerate(("Configured", self._credits_since_var, "Remaining"), 1):
            if isinstance(variable, str):
                ttk.Label(credits_frame, text=variable).grid(row=1, column=column, padx=5)
            else:
                ttk.Label(credits_frame, textvariable=variable).grid(row=1, column=column, padx=5)
        for row, category, label in (
            (2, "processingUnitsMonthly", "Processing units"),
            (3, "requestsMonthly", "Requests"),
        ):
            ttk.Label(credits_frame, text=label).grid(row=row, column=0, sticky="w")
            for column, field in enumerate(("configuration", "consumed", "remaining"), 1):
                ttk.Label(credits_frame, textvariable=self._credits_values[(category, field)]).grid(
                    row=row, column=column, padx=5, sticky="e"
                )
        ttk.Label(credits_frame, textvariable=self._credits_status_var).grid(
            row=4, column=0, columnspan=4, pady=(4, 0), sticky="w"
        )
        self._credits_since_var.set("Consumed since " + dt.date.today().replace(day=1).strftime("%d-%m-%Y"))
        ttk.Label(
            auth_frame,
            text=("Create a free CDSE OAuth client under User Settings > OAuth clients. "
                  "No Planet subscription or Configuration Instance is required. On Windows "
                  "the secret is saved encrypted for the current user."),
            wraplength=560,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, pady=(3, 0), sticky="w")
        self._oauth_button = ttk.Button(
            auth_frame, text="Open free OAuth client settings", command=self._open_oauth_settings
        )
        self._oauth_button.grid(row=4, column=0, columnspan=2, pady=(6, 0), sticky="w")

        if catalogue_parent is None:
            self.catalogue_frame.grid(row=20, column=0, columnspan=2, pady=(5, 0), sticky="ew")
        self._refresh_button = ttk.Button(
            self.catalogue_frame, text="Refresh catalogue", command=self._refresh_clicked
        )
        # What the refresh loads is explained in Info and the image source guide.
        self._refresh_button.grid(row=0, column=0, padx=(0, 8), sticky="w")
        status_label = ttk.Label(
            self.catalogue_frame, textvariable=self._status_var, wraplength=570, justify="left"
        )
        status_label.grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="nw")
        reserve_text_lines(status_label)
        self._activity = CatalogueActivity(self.catalogue_frame, row=2)

        self._configuration_combo.bind("<<ComboboxSelected>>", self._select_configuration)
        self._mission_combo.bind("<<ComboboxSelected>>", self._select_mission)
        self._product_combo.bind("<<ComboboxSelected>>", self._select_product)
        self._layer_combo.bind("<<ComboboxSelected>>", self._select_layer)
        self._highlight_combo.bind("<<ComboboxSelected>>", self._select_highlight)
        self._date_combo.bind("<<ComboboxSelected>>", self._custom_date_changed)
        self._date_var.trace_add("write", lambda *_args: self._refresh_cloud_hint())
        self._quarter_mode_combo.bind("<<ComboboxSelected>>", self._select_quarter_mode)
        self._quarter_offset_combo.bind("<<ComboboxSelected>>", self._select_quarter_offset)
        self._zoom_combo.bind("<<ComboboxSelected>>", self._custom_location_changed)
        self._coverage_combo.bind("<<ComboboxSelected>>", self._select_coverage)
        self._lookback_combo.bind("<<ComboboxSelected>>", self._changed)
        self._latitude_var.trace_add("write", self._custom_location_changed)
        self._longitude_var.trace_add("write", self._custom_location_changed)
        self._client_id_var.trace_add("write", self._credentials_changed)
        self._secret_var.trace_add("write", self._credentials_changed)
        self.frame.bind("<Destroy>", self._destroyed, add="+")

        # Use auto recommendation, in the host's Recommendation section (rows 0-2).
        recommend = recommend_parent if recommend_parent is not None else ttk.Frame(self.frame)
        self._auto_var = tk.StringVar(self.frame, "No")
        self._auto_priority_var = tk.StringVar(self.frame, "Fewest clouds")
        self._auto_choice_var = tk.StringVar(self.frame)
        self._auto_precise_var = tk.BooleanVar(self.frame, False)
        ttk.Label(recommend, text="Use auto recommendation").grid(row=0, column=0, padx=(0, 10), pady=3,
                                                                  sticky="w")
        self._auto_combo = ttk.Combobox(recommend, textvariable=self._auto_var, values=("No", "Yes"),
                                        state="readonly", width=SOURCE_COMBO_WIDTH)
        self._auto_combo.grid(row=0, column=1, pady=3, sticky="ew")
        self._auto_priority_label = ttk.Label(recommend, text="Priority")
        self._auto_priority_label.grid(row=1, column=0, padx=(0, 10), pady=3, sticky="w")
        self._auto_priority_combo = ttk.Combobox(recommend, textvariable=self._auto_priority_var,
                                                 values=("Fewest clouds", "Data coverage"),
                                                 state="readonly", width=SOURCE_COMBO_WIDTH)
        self._auto_priority_combo.grid(row=1, column=1, pady=3, sticky="ew")
        # Regular layers only: mosaics measure what their priority needs anyway.
        self._auto_precise_check = ttk.Checkbutton(recommend, text="Always use precise check (consumes more credits)",
                                                   variable=self._auto_precise_var, command=self._changed)
        self._auto_precise_check.grid(row=2, column=1, pady=3, sticky="w")
        self._auto_choice_label = ttk.Label(recommend, textvariable=self._auto_choice_var, justify="left")
        self._auto_choice_label.grid(row=3, column=1, pady=(0, 3), sticky="w")
        self._auto_combo.bind("<<ComboboxSelected>>", self._select_auto)
        self._auto_priority_combo.bind("<<ComboboxSelected>>", self._select_auto_priority)
        # Each priority in its color in the Compare variants window: the first blue,
        # Data coverage green, in the field and in the open list.
        self._auto_priority_combo.configure(
            postcommand=lambda: self._auto_priority_combo.after_idle(self._color_priority_list))
        on_theme_change(self._auto_priority_combo, self._color_priority)
        self._auto_controls_ready = True

        self.set_profile(profile or DEFAULT_PROFILE)
        self._updating = False
        self._after_id = self.frame.after(100, self._poll)
        if self._client_id_var.get() and self._secret_var.get():
            self._usage_after_id = self.frame.after(250, self._auto_refresh_usage)

    def _auto_refresh_usage(self):
        self._usage_after_id = None
        self.refresh_usage()

    def _combo(self, row, label, variable, values=(), parent=None):
        parent = self.frame if parent is None else parent
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, padx=(0, 10), pady=3, sticky="w")
        widget = ttk.Combobox(
            parent, textvariable=variable, values=values,
            state="readonly", width=SOURCE_COMBO_WIDTH,
        )
        widget.grid(row=row, column=1, pady=3, sticky="ew")
        widget.label_widget = label_widget
        return widget

    def _entry(self, row, label, variable):
        ttk.Label(self.frame, text=label).grid(
            row=row, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        widget = ttk.Entry(self.frame, textvariable=variable)
        widget.grid(row=row, column=1, pady=3, sticky="ew")
        return widget

    @staticmethod
    def _unique_labels(items, name_key="name"):
        result = {}
        for item in items:
            base = str(item[name_key])
            label = base
            if label in result:
                label = f"{base} [{item['id']}]"
            result[label] = item
        return result

    def _label_for_id(self, mapping, item_id):
        return next((label for label, item in mapping.items() if item["id"] == item_id), "")

    def _build_theme_choices(self, selected_id):
        self._theme_by_label = self._unique_labels(themes())
        self._configuration_combo.configure(values=tuple(self._theme_by_label), state="readonly")
        self._configuration_var.set(self._label_for_id(self._theme_by_label, selected_id))

    def _selected_theme(self):
        return self._theme_by_label.get(self._configuration_var.get())

    def _selected_product(self):
        return self._product_by_label.get(self._product_var.get())

    def _selected_layer(self):
        return self._layer_by_label.get(self._layer_var.get())

    def _build_mission_choices(self, selected, prefer_natural=False):
        theme = self._selected_theme()
        choices = missions(theme["id"]) if theme else []
        if choices:
            natural = min(choices, key=lambda mission: min(
                (_natural_product_rank(item) for item in products(theme["id"], mission)),
                default=(3, 4),
            ))
            current_has_natural = selected in choices and min(
                (_natural_product_rank(item)[0] for item in products(theme["id"], selected)),
                default=3,
            ) < 3
            if selected not in choices or (prefer_natural and not current_has_natural):
                selected = natural
        self._mission_combo.configure(values=choices, state="readonly" if choices else "disabled")
        self._mission_var.set(selected if selected in choices else "")

    def _build_product_choices(self, selected_id):
        theme = self._selected_theme()
        items = products(theme["id"], self._mission_var.get()) if theme else []
        self._product_by_label = self._unique_labels(items)
        if selected_id not in {item["id"] for item in items} and items:
            preferred_id = DEFAULT_PROFILE["product"] \
                if theme["id"] == DEFAULT_PROFILE["configuration"] \
                and self._mission_var.get() == DEFAULT_PROFILE["mission"] else ""
            if self._mission_var.get() == "Sentinel-1 Mosaics":
                preferred_id = "MARBLESCAPE::S1-IW-MONTHLY"
            selected_id = preferred_id if preferred_id in {item["id"] for item in items} \
                else min(items, key=_natural_product_rank)["id"]
        self._product_combo.configure(
            values=tuple(self._product_by_label), state="readonly" if items else "disabled"
        )
        self._product_var.set(self._label_for_id(self._product_by_label, selected_id))
        return selected_id

    def _build_layer_choices(self, selected_id):
        product = self._selected_product()
        items = product["layers"] if product else []
        self._layer_by_label = self._unique_labels(items)
        if selected_id not in {item["id"] for item in items} and items:
            selected_id = min(items, key=_natural_layer_rank)["id"]
        self._layer_combo.configure(
            values=tuple(self._layer_by_label), state="readonly" if items else "disabled"
        )
        self._layer_var.set(self._label_for_id(self._layer_by_label, selected_id))
        self._refresh_cloud_control()
        self._refresh_coverage_controls()
        granularity = (self._selected_layer() or {}).get("date_granularity")
        self._date_label.configure(text={
            "year": "Year", "quarter": "Quarter", "month": "Month"
        }.get(granularity, "Date / time"))
        self._refresh_quarter_controls()
        return selected_id

    def _refresh_quarter_controls(self):
        self._refresh_quarter_controls_unlocked()
        self._apply_auto_lock()

    def _refresh_quarter_controls_unlocked(self):
        unit = (self._selected_layer() or {}).get("date_granularity")
        if unit not in {"quarter", "month"}:
            self._date_label.grid()
            self._date_combo.master.grid_configure(column=0, columnspan=2)
            self._quarter_mode_var.set("Latest available")
            # Hidden period controls in a fixed state, whatever the layer before had.
            self._quarter_mode_combo.configure(state="readonly")
            self._quarter_offset_combo.configure(state="disabled")
            self._quarter_mode_frame.grid_remove()
            self._quarter_resolution_label.grid_remove()
            self._period_date_label.grid_remove()
            configure_source_columns(self._date_combo.master)
            # grid() keeps earlier options: drop the period rows' top padding.
            self._date_combo.grid(row=0, column=1, columnspan=1, pady=0, sticky="ew")
            self._date_combo.configure(state="readonly")
            return
        offsets = QUARTER_OFFSET_LABELS if unit == "quarter" else MONTH_OFFSET_LABELS
        # Selectable again after Use auto recommendation is switched off;
        # _apply_auto_lock greys it out while the rule chooses.
        self._quarter_mode_combo.configure(values=(f"Specific {unit}", "Relative to now", LATEST_LABEL),
                                           state="readonly")
        if self._quarter_mode_var.get().startswith("Specific"):
            self._quarter_mode_var.set(f"Specific {unit}")
        self._quarter_mode_label.configure(text=f"{unit.title()} selection")
        self._quarter_offset_label.configure(text=f"{unit.title()}s back")
        self._quarter_offset_combo.configure(values=tuple(offsets))
        if self._quarter_offset_var.get() not in offsets:
            self._quarter_offset_var.set(next(label for label, value in offsets.items() if value == 1))
        self._date_combo.master.grid_configure(column=0, columnspan=2)
        self._date_label.grid_remove()
        self._quarter_mode_frame.grid(row=0, column=0, columnspan=2, pady=0, sticky="ew")
        configure_source_columns(self._date_combo.master)
        self._date_label.configure(text="")
        relative = self._quarter_mode_var.get() == "Relative to now"
        specific = self._quarter_mode_var.get() == f"Specific {unit}"
        # Selectable whether shown or not; _apply_auto_lock greys it out while the rule chooses.
        self._date_combo.configure(state="readonly")
        if specific:
            self._date_combo.grid(row=1, column=1, columnspan=1, pady=(6, 0), sticky="ew")
            self._date_combo.configure(state="readonly")
            self._period_date_label.configure(text=unit.title())
            self._period_date_label.grid(row=1, column=0, pady=(6, 0), sticky="w")
        else:
            self._date_combo.grid_remove()
            self._period_date_label.grid_remove()
        for widget in (self._quarter_offset_label, self._quarter_offset_combo):
            widget.grid() if relative else widget.grid_remove()
        self._quarter_offset_combo.configure(state="readonly" if relative else "disabled")
        if relative:
            offset = offsets.get(self._quarter_offset_var.get())
            if offset is not None:
                date = (rolling_quarter_start(self._reference_date(), offset) if unit == "quarter"
                        else rolling_month_start(self._reference_date(), offset))
                target = f"{date.year} Q{(date.month - 1) // 3 + 1}" if unit == "quarter" else f"{date:%Y-%m}"
                self._quarter_resolution_var.set(
                    f"Resolved {unit}: {target} | "
                    "The selected period must be published for this location."
                )
                self._quarter_resolution_label.grid(row=2, column=0, columnspan=2)
        else:
            self._quarter_resolution_label.grid_remove()

    def _refresh_cloud_control(self):
        self._refresh_cloud_control_unlocked()
        self._refresh_auto_controls()

    def _refresh_cloud_control_unlocked(self):
        layer = self._selected_layer() or {}
        self._refresh_no_data_preview()
        set_scale_enabled(self._cloud_scale, supports_cloud_filter(layer))
        # Brightness and contrast correction only where a tone rule exists.
        ruled = tone_rule(layer) is not None
        set_scale_enabled(self._brightness_scale, ruled and not self._auto_brightness_var.get())
        set_scale_enabled(self._contrast_scale, ruled and not self._auto_contrast_var.get())
        for button in (self._auto_brightness_button, self._auto_contrast_button):
            button.configure(state="normal" if ruled else "disabled")
        self._refresh_tone_values()
        self._refresh_cloud_hint()

    def _refresh_cloud_hint(self):
        """The note below Maximum cloud cover, for the layer and the date choice."""
        layer = self._selected_layer() or {}
        if layer.get("date_granularity") == "year":
            hint = (
                "The annual cloudless mosaic covers 2020 and 2021. Its source requires zoom 9 "
                "or higher; smaller Process requests do not remove that resolution limit."
            )
        elif layer.get("date_granularity") in {"month", "quarter"}:
            hint = (
                "This precomputed mosaic has no cloud filter. Wide views use the source's "
                "lower-resolution collection."
            )
        elif supports_cloud_filter(layer) and self._date_var.get() not in (LATEST_LABEL, LOADING_DATES_LABEL, ""):
            hint = CLOUD_HINT + " " + FIXED_DATE_CLOUD_HINT
        else:
            hint = CLOUD_HINT
        self._cloud_hint.configure(text=hint)

    def _active_no_data_var(self):
        """Mosaics and regular layers keep separate No-data colors."""
        if "date_granularity" in (self._selected_layer() or {}):
            return self._no_data_color_var
        return self._scene_no_data_color_var

    def _choose_no_data_color(self):
        variable = self._active_no_data_var()
        _rgb, color = colorchooser.askcolor(
            color=variable.get() if re.fullmatch(r"#[0-9A-Fa-f]{6}", variable.get()) else "#FFFFFF",
            parent=self.frame, title="No-data color")
        if color is not None:
            variable.set(color.upper())
            self._changed()

    def _refresh_no_data_preview(self):
        """Keep the compact swatch readable, including the transparent and blur choices."""
        variable = self._active_no_data_var()
        value = variable.get()
        # Shown like the buttons; saved in lower case.
        self._no_data_display_var.set(NO_DATA_LABELS.get(value, value))
        if value == "transparent":
            self._no_data_preview.configure(background="#FFFFFF", text="×", foreground="#666666")
        elif value in ("blur", "blur_edge"):
            self._no_data_preview.configure(background="#C8C8C8", text="≈", foreground="#444444")
        else:
            self._no_data_preview.configure(background=value if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) else "#000000",
                                            text="", foreground="#000000")

    def _map_labels_changed(self):
        self._refresh_map_label_controls()
        self._changed()

    def _refresh_map_label_controls(self):
        for overlay in self._overlays.values():
            overlay["button"].configure(state="normal" if overlay["toggle"].get() else "disabled")

    def _choose_overlay_color(self, key):
        overlay = self._overlays[key]
        if not overlay["toggle"].get():
            return
        current = overlay["color"].get()
        _rgb, color = colorchooser.askcolor(
            color="#FFFFFF" if current == "transparent" else current,
            parent=self.frame, title=overlay["title"],
        )
        if color is not None:
            overlay["color"].set(color.upper())
            self._changed()

    def _refresh_overlay_preview(self, key):
        overlay = self._overlays[key]
        value = overlay["color"].get()
        # "transparent" only comes from selections saved before the checkboxes.
        if value == "transparent":
            overlay["preview"].configure(background="#FFFFFF", text="×", foreground="#666666")
        else:
            overlay["preview"].configure(
                background=value if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) else "#000000",
                text="", foreground="#000000",
            )

    def _tone_mode_changed(self):
        self._refresh_cloud_control()
        self._changed()

    def _contrast_changed(self, value):
        if self._auto_shown["contrast"]:
            return
        self._contrast_value_var.set(f"{int(float(value))}%")
        self._changed()

    def _image_size_changed(self, _event=None):
        self._build_zoom_choices()
        self._changed()
        self._schedule_inputs_refresh()

    def refresh_image_size_label(self):
        """Image resolution, followed by the picture's pixel size, e.g. "(3840x2160)"."""
        try:
            width, height = self._effective_output_size()
            text = f"Image resolution ({width} × {height})"
        except (TypeError, ValueError):
            text = "Image resolution"
        self._image_size_combo.label_widget.configure(text=text)
        # A new monitor output size can change the zoom and date lists (Auto size).
        self._schedule_inputs_refresh()

    def _effective_output_size(self):
        fallback = self._output_size() if callable(self._output_size) else self._output_size
        return resolve_image_size({"image_size": self._image_size_value()}, fallback)

    def _image_size_value(self):
        """The saved value of the Image resolution shown ("auto" or "3840x2160")."""
        shown = self._image_size_var.get()
        return self._image_size_by_label.get(shown, "auto" if shown == AUTO_IMAGE_SIZE_LABEL else shown)

    def _choose_transparent(self):
        self._active_no_data_var().set("transparent")
        self._changed()

    def _choose_blur(self):
        self._active_no_data_var().set("blur")
        self._changed()

    def _choose_edge_blur(self):
        self._active_no_data_var().set("blur_edge")
        self._changed()

    def _cloud_changed(self, value):
        self._cloud_value_var.set(f"{int(float(value))}%")
        self._changed()

    def _brightness_changed(self, value):
        if self._auto_shown["brightness"]:
            return
        self._brightness_value_var.set(f"{int(float(value))}%")
        self._changed()

    def show_picture(self, profile, adjustments):
        """The Copernicus selection and tone adjustments of the picture on screen.

        ``None`` when no picture or no mosaic record is known. An auto slider
        shows the values only while the selection still equals that picture's.
        """
        self._shown_picture = ((profile, adjustments)
                               if isinstance(profile, dict) and isinstance(adjustments, dict) else None)
        self._refresh_tone_values()

    @staticmethod
    def _tone_key(profile):
        # A manual value has no effect while its auto option is on.
        key = normalize_profile(profile)
        if key["auto_brightness"]:
            key["brightness"] = None
        if key["auto_contrast"]:
            key["contrast"] = None
        return key

    def _shown_adjustments(self):
        if self._shown_picture is None:
            return None
        profile, adjustments = self._shown_picture
        try:
            if self._tone_key(profile) != self._tone_key(self.get_profile()):
                return None
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        return adjustments

    def _refresh_tone_values(self):
        """Manual sliders show the saved value; auto ones what auto chose for the picture on screen.

        Brightness shows the midtone gamma as the equal manual percentage (with
        auto contrast the manual brightness is that same gamma); contrast shows
        the stretch factor, since the manual contrast is another curve. Without
        a matching picture the value reads "auto" until the next picture.
        """
        if self._updating:
            return
        ruled = tone_rule(self._selected_layer()) is not None
        auto = {"brightness": ruled and bool(self._auto_brightness_var.get()),
                "contrast": ruled and bool(self._auto_contrast_var.get())}
        adjustments = self._shown_adjustments() if any(auto.values()) else None
        brightness_text, brightness_position = "auto", 100
        contrast_text = "auto"
        if adjustments is not None:
            gamma = adjustments.get("midtone_gamma")
            if adjustments.get("auto_brightness") and isinstance(gamma, (int, float)) and gamma > 0:
                percent = 100 / gamma
                brightness_text = f"{round(percent)}%"
                brightness_position = min(200, max(25, 5 * round(percent / 5)))
            low, high = adjustments.get("stretch_low"), adjustments.get("stretch_high")
            if (adjustments.get("auto_contrast") and isinstance(low, int) and isinstance(high, int)
                    and 0 <= low < high <= 255):
                contrast_text = f"×{255 / (high - low):.2f}"
        self._brightness_auto_var.set(brightness_position)
        self._contrast_auto_var.set(100)
        for key, scale, manual, shown, value_var, text in (
                ("brightness", self._brightness_scale, self._brightness_var, self._brightness_auto_var,
                 self._brightness_value_var, brightness_text),
                ("contrast", self._contrast_scale, self._contrast_var, self._contrast_auto_var,
                 self._contrast_value_var, contrast_text)):
            if not ruled:
                # Stock: the slider shows 100 % and moves nothing.
                self._auto_shown[key] = True
                scale.configure(variable=self._stock_var)
                value_var.set("100%")
                continue
            self._auto_shown[key] = auto[key]
            scale.configure(variable=shown if auto[key] else manual)
            value_var.set(text if auto[key] else f"{manual.get()}%")

    def _build_zoom_choices(self, selected=None):
        layer = self._selected_layer()
        values = map_zooms(layer)
        try:
            size = self._effective_output_size()
            values = map_zooms_for_view(layer, self._latitude_var.get(), size) or values
        except ValueError:
            pass
        try:
            selected = int(self._zoom_var.get() if selected is None else selected)
        except (TypeError, ValueError):
            selected = DEFAULT_PROFILE["map_zoom"]
        if selected not in values:
            selected = min(values, key=lambda value: abs(value - selected))
        self._zoom_combo.configure(values=tuple(str(value) for value in values), state="readonly")
        self._zoom_var.set(str(selected))

    def _build_highlight_choices(self, selected_id=""):
        theme = self._selected_theme()
        items = highlights(theme["id"]) if theme else []
        self._highlight_by_label = {CUSTOM_HIGHLIGHT_LABEL: None}
        self._highlight_by_label.update(self._unique_labels(items))
        self._highlight_combo.configure(values=tuple(self._highlight_by_label), state="readonly")
        label = next((label for label, item in self._highlight_by_label.items()
                      if item and item["id"] == selected_id), CUSTOM_HIGHLIGHT_LABEL)
        self._highlight_var.set(label)
        # Hidden while the configuration has no scenes (Default has none).
        for widget in (self._highlight_combo, self._highlight_combo.label_widget):
            widget.grid() if items else widget.grid_remove()

    def _set_date_choices(self, selected):
        self._update_date_choices([], selected)

    def _restore_cached_dates(self):
        cache = self._catalogue_client
        cached_dates = getattr(cache, "cached_copernicus_dates", None)
        if not callable(cached_dates):
            return False
        try:
            profile = self.get_profile(for_catalogue=True)
            size = self._effective_output_size()
            dates = cached_dates(profile, (int(size[0]), int(size[1])))
        except (OSError, TypeError, ValueError, tk.TclError):
            return False
        if dates is None:
            return False
        selected, notice = self._confirm_kept_date(
            dates, self._date_by_label.get(self._date_var.get(), "latest")
        )
        self._update_date_choices(dates, selected)
        if notice:
            self._status_var.set(notice)
        return True

    def _confirm_kept_date(self, dates, selected):
        """Fall back to Latest when a kept date is missing from the new list."""
        kept, self._kept_date = self._kept_date, None
        if kept is None or selected != kept or kept in dates:
            return selected, ""
        label = self._date_choice_label(kept)
        return "latest", f"{label} is not available for this selection; switched to Latest available."

    def _date_choice_label(self, value):
        granularity = (self._selected_layer() or {}).get("date_granularity")
        if granularity is None:
            return value
        date = dt.date.fromisoformat(value)
        if granularity == "year":
            return str(date.year)
        if granularity == "quarter":
            return f"{date.year} Q{(date.month - 1) // 3 + 1}"
        if granularity == "month":
            return date.strftime("%Y-%m")
        return value

    def _update_date_choices(self, dates, selected):
        self._dates = list(dates)
        available = list(dates)
        if selected != "latest" and selected not in available:
            available.insert(0, selected)
        self._date_by_label = {LATEST_LABEL: "latest"}
        for value in available:
            self._date_by_label[self._date_choice_label(value)] = value
        choices = tuple(self._date_by_label)
        if (self._selected_layer() or {}).get("date_granularity") in {"quarter", "month"}:
            choices = tuple(label for label in choices if label != LATEST_LABEL)
        self._date_combo.configure(values=choices, state="readonly")
        self._date_unit = (self._selected_layer() or {}).get("date_granularity")
        selected_label = LATEST_LABEL if selected == "latest" else self._date_choice_label(selected)
        if self._quarter_mode_var.get().startswith("Specific") and selected == "latest":
            selected_label = next(iter(choices), "")
        self._date_var.set(selected_label)
        self._refresh_quarter_controls()

    def _set_coverage_controls(self, mode, lookback_days):
        label = next((label for label, value in COVERAGE_LABELS.items() if value == mode), "")
        self._coverage_var.set(label)
        lookback_label = next(
            (label for label, value in LOOKBACK_LABELS.items() if value == lookback_days), ""
        )
        self._lookback_var.set(lookback_label)
        self._refresh_coverage_controls()

    def _refresh_coverage_controls(self):
        self._refresh_coverage_controls_unlocked()
        self._apply_auto_lock()

    def _auto_active(self):
        layer = self._selected_layer() or {}
        return self._auto_var.get() == "Yes" and layer.get("data_type") != "dem"

    def _apply_auto_lock(self):
        """While the rule chooses, date, Gap fill and cloud limit show their saved values greyed out."""
        if not self._auto_controls_ready or not self._auto_active():
            return
        for combo in (self._date_combo, self._quarter_mode_combo, self._quarter_offset_combo,
                      self._coverage_combo, self._lookback_combo):
            combo.configure(state="disabled")
        set_scale_enabled(self._cloud_scale, False)

    def _refresh_auto_controls(self):
        if not self._auto_controls_ready:
            return
        from marblescape_copernicus_advice import last_choice
        layer = self._selected_layer() or {}
        dem = layer.get("data_type") == "dem"
        # Regular layers add Newest; for mosaics Newest is the first priority.
        values = (("Newest", "Data coverage") if "date_granularity" in layer
                  else ("Fewest clouds", "Data coverage", "Newest"))
        self._auto_priority_combo.configure(values=values)
        if self._auto_priority_var.get() not in values:
            self._auto_priority_var.set(values[0])
        self._color_priority()
        if dem:
            self._auto_var.set("No")  # An elevation model has no dates to choose from.
        self._auto_combo.configure(state="disabled" if dem else "readonly")
        active = self._auto_active()
        for widget in (self._auto_priority_label, self._auto_priority_combo, self._auto_choice_label):
            widget.grid() if active else widget.grid_remove()
        if active and "date_granularity" not in layer:
            self._auto_precise_check.grid()
        else:
            self._auto_precise_check.grid_remove()
        if active:
            try:
                choice = last_choice(self.get_profile(for_catalogue=True))
            except (ValueError, tk.TclError):
                choice = None
            self._auto_choice_var.set(f"Last choice: {choice[0]} ({choice[1]:%H:%M})" if choice
                                      else "Chosen at the next image check.")
        self._apply_auto_lock()

    def _priority_color(self, label):
        key = {"Data coverage": "success", "Newest": "newest"}.get(label, "link")
        return palette(self._auto_priority_combo)[key]

    def _color_priority(self):
        self._auto_priority_combo.configure(foreground=self._priority_color(self._auto_priority_var.get()))

    def _color_priority_list(self):
        try:
            popdown = self._auto_priority_combo.tk.call("ttk::combobox::PopdownWindow", self._auto_priority_combo)
            listbox = f"{popdown}.f.l"
            for index, label in enumerate(self._auto_priority_combo.cget("values")):
                self._auto_priority_combo.tk.call(listbox, "itemconfigure", index,
                                                  "-foreground", self._priority_color(str(label)))
        except tk.TclError:
            pass

    def _select_auto_priority(self, _event=None):
        self._color_priority()
        self._changed()

    def _select_auto(self, _event=None):
        if self._updating:
            return
        if self._auto_var.get() == "Yes":
            # The rule picks among the newest images: Latest available.
            self._quarter_mode_var.set(LATEST_LABEL)
            self._update_date_choices(list(getattr(self, "_dates", [])), "latest")
        self._refresh_quarter_controls_unlocked()
        self._refresh_coverage_controls_unlocked()
        self._refresh_cloud_control()
        self._changed()

    def _refresh_coverage_controls_unlocked(self):
        layer = self._selected_layer() or {}
        if "date_granularity" in layer:
            self._coverage_var.set(next(label for label, value in COVERAGE_LABELS.items()
                                        if value == "single"))
            self._coverage_combo.configure(state="disabled")
            self._lookback_combo.configure(state="disabled")
        else:
            self._coverage_combo.configure(state="readonly")
            self._lookback_combo.configure(
                state="readonly" if COVERAGE_LABELS.get(self._coverage_var.get()) == "fill_gaps"
                else "disabled"
            )

    def set_profile(self, profile):
        profile = normalize_profile(profile)
        self._generation += 1
        self._activity.finish(False)
        self._refresh_button.configure(state="normal")
        self._updating = True
        try:
            self._build_theme_choices(profile["configuration"])
            self._build_mission_choices(profile["mission"])
            self._build_product_choices(profile["product"])
            self._build_layer_choices(profile["layer"])
            self._latitude_var.set(f"{profile['latitude']:.8f}".rstrip("0").rstrip("."))
            self._longitude_var.set(f"{profile['longitude']:.8f}".rstrip("0").rstrip("."))
            self._image_size_var.set(image_size_label(profile["image_size"]))
            self._build_zoom_choices(profile["map_zoom"])
            self._build_highlight_choices(profile["highlight"])
            self._kept_date = None
            self._set_date_choices(profile["date"])
            unit = (self._selected_layer() or {}).get("date_granularity", "quarter")
            mode_label = ("Relative to now" if profile["date_mode"].startswith("relative_")
                          else "Latest available" if profile["date"] == "latest"
                          else f"Specific {unit}")
            self._quarter_mode_var.set(mode_label)
            offset = profile["month_offset"] if unit == "month" else profile["quarter_offset"]
            offsets = MONTH_OFFSET_LABELS if unit == "month" else QUARTER_OFFSET_LABELS
            self._quarter_offset_var.set(next(label for label, value in offsets.items()
                                              if value == offset))
            self._refresh_quarter_controls()
            self._set_coverage_controls(profile["coverage_mode"], profile["lookback_days"])
            self._cloud_var.set(profile["max_cloud_cover"])
            self._cloud_value_var.set(f"{profile['max_cloud_cover']}%")
            self._brightness_var.set(profile["brightness"])
            self._contrast_var.set(profile["contrast"])
            self._contrast_value_var.set(f"{profile['contrast']}%")
            self._auto_brightness_var.set(profile["auto_brightness"])
            self._auto_contrast_var.set(profile["auto_contrast"])
            self._refresh_cloud_control()
            self._no_data_color_var.set(profile["no_data_color"])
            self._scene_no_data_color_var.set(profile["scene_no_data_color"])
            self._brightness_value_var.set(f"{profile['brightness']}%")
            self._labels_var.set(profile["map_labels"])
            self._map_label_color_var.set(profile["map_label_color"])
            self._borders_var.set(profile["map_borders"])
            self._map_border_color_var.set(profile["map_border_color"])
            self._refresh_map_label_controls()
            from marblescape_copernicus_advice import priority_label
            self._auto_var.set("Yes" if profile["auto_recommendation"] else "No")
            self._auto_priority_var.set(priority_label(
                profile["auto_priority"], "date_granularity" in (self._selected_layer() or {})))
            self._auto_precise_var.set(profile["auto_precise"])
            self._refresh_quarter_controls_unlocked()
            self._refresh_coverage_controls_unlocked()
            self._refresh_cloud_control()
            cached = self._restore_cached_dates()
            self._status_var.set(
                ("Cached Copernicus dates restored. " if cached else
                 "Bundled Copernicus Browser catalogue " + catalogue_revision()[:12]
                 + ". ")
                + "Latest available imagery is the default."
            )
        finally:
            self._updating = False
        self._refresh_tone_values()
        # The loaded profile's lists are current; only later edits reload them.
        self._last_date_inputs = self._date_inputs()

    def _date_inputs(self):
        """What the date and zoom lists depend on besides the selection."""
        try:
            size = tuple(int(value) for value in self._effective_output_size())
        except (TypeError, ValueError):
            size = None
        return (self._latitude_var.get().strip(), self._longitude_var.get().strip(),
                self._zoom_var.get(), int(float(self._cloud_var.get())), size)

    def _schedule_inputs_refresh(self, *_args):
        """Reload the date and zoom lists after a pause in typing or dragging."""
        if self._updating or self._closed or not hasattr(self, "_date_refresh_after"):
            return
        if self._date_refresh_after is not None:
            self.frame.after_cancel(self._date_refresh_after)
        self._date_refresh_after = self.frame.after(700, self._refresh_for_inputs)

    def _refresh_for_inputs(self):
        """Dates and zooms for the new place, zoom, size or cloud limit.

        A fixed date or zoom the new view does not offer becomes Latest available
        or the nearest zoom, with a note; nothing reloads while the inputs are
        unchanged, so a refresh never repeats itself.
        """
        self._date_refresh_after = None
        if self._closed:
            return
        inputs = self._date_inputs()
        if inputs == self._last_date_inputs:
            return
        self._last_date_inputs = inputs
        before_zoom, before_date = self._zoom_var.get(), self._date_var.get()
        self._build_zoom_choices()
        notes = []
        if self._zoom_var.get() != before_zoom:
            notes.append(f"Map zoom {before_zoom} does not fit this view; it is now {self._zoom_var.get()}.")
        layer = self._selected_layer() or {}
        if layer.get("data_type") != "dem":
            selected = self._date_by_label.get(self._date_var.get(), "latest")
            # A fixed date stays only when the new list still offers it.
            self._kept_date = selected if selected != "latest" else None
            if not self._restore_cached_dates():
                if self._client_id_var.get() and self._secret_var.get():
                    self.refresh_dates()
                else:
                    self._kept_date = None  # Without a list it cannot be checked; it stays.
        if notes:
            self._status_var.set(" ".join([*notes, self._status_var.get()]).strip())
        # "Last choice" of the auto recommendation belongs to the place and zoom.
        self._refresh_auto_controls()
        if self._zoom_var.get() != before_zoom or self._date_var.get() != before_date:
            self._last_date_inputs = self._date_inputs()
            self._changed()

    def _current_ids(self):
        theme = self._selected_theme()
        product = self._selected_product()
        layer = self._layer_by_label.get(self._layer_var.get())
        highlight = self._highlight_by_label.get(self._highlight_var.get())
        return theme, product, layer, highlight

    def get_profile(self, for_catalogue=False):
        theme, product, layer, highlight = self._current_ids()
        try:
            latitude = float(self._latitude_var.get().strip())
            longitude = float(self._longitude_var.get().strip())
            map_zoom = int(self._zoom_var.get().strip())
        except ValueError as exc:
            raise ValueError("Copernicus latitude, longitude and map zoom must be valid numbers.") from exc
        unit = (layer or {}).get("date_granularity")
        periodic = unit in {"quarter", "month"}
        selected_date = self._date_by_label.get(self._date_var.get(), self._date_var.get())
        if periodic:
            if for_catalogue or not self._quarter_mode_var.get().startswith("Specific"):
                selected_date = "latest"
            elif not selected_date or selected_date == "latest":
                raise ValueError(f"Select a specific {unit}; refresh the Copernicus catalogue if the list is empty.")
        value = {
            "configuration": theme["id"] if theme else "",
            "mission": self._mission_var.get(),
            "product": product["id"] if product else "",
            "layer": layer["id"] if layer else "",
            "highlight": highlight["id"] if highlight else "",
            "date": selected_date,
            "date_mode": (f"relative_{unit}" if periodic and self._quarter_mode_var.get() == "Relative to now"
                          else "catalogue"),
            "quarter_offset": (
                QUARTER_OFFSET_LABELS.get(self._quarter_offset_var.get(), -1)
                if unit == "quarter" and self._quarter_mode_var.get() == "Relative to now" else 0
            ),
            "month_offset": (MONTH_OFFSET_LABELS.get(self._quarter_offset_var.get(), -1)
                             if unit == "month" and self._quarter_mode_var.get() == "Relative to now" else 0),
            "latitude": latitude,
            "longitude": longitude,
            "map_zoom": map_zoom,
            "map_labels": bool(self._labels_var.get()),
            "map_label_color": self._map_label_color_var.get(),
            "map_borders": bool(self._borders_var.get()),
            "map_border_color": self._map_border_color_var.get(),
            "coverage_mode": COVERAGE_LABELS.get(self._coverage_var.get(), ""),
            "lookback_days": LOOKBACK_LABELS.get(self._lookback_var.get()),
            "max_cloud_cover": self._cloud_var.get(),
            "brightness": self._brightness_var.get(),
            "contrast": self._contrast_var.get(),
            "auto_brightness": self._auto_brightness_var.get(),
            "auto_contrast": self._auto_contrast_var.get(),
            "image_size": self._image_size_value(),
            "no_data_color": self._no_data_color_var.get(),
            "scene_no_data_color": self._scene_no_data_color_var.get(),
            "auto_recommendation": self._auto_active(),
            "auto_priority": AUTO_PRIORITY_LABELS.get(self._auto_priority_var.get(), "fewest_clouds"),
            "auto_precise": bool(self._auto_precise_var.get()),
        }
        value = normalize_profile(value)
        size = self._effective_output_size()
        allowed = map_zooms_for_view(layer, latitude, size)
        if map_zoom not in allowed:
            if not allowed:
                raise ValueError(
                    "The configured Copernicus output cannot fit at any zoom supported by this product."
                )
            raise ValueError(
                f"Copernicus map zoom {map_zoom} cannot contain the configured output at this latitude; "
                f"choose {allowed[0]} through {allowed[-1]}."
            )
        return value

    def _select_coverage(self, _event=None):
        if self._updating:
            return
        self._refresh_coverage_controls()
        self._changed()

    def _select_quarter_mode(self, _event=None):
        if self._updating:
            return
        if self._quarter_mode_var.get() == "Relative to now":
            self._date_var.set(LATEST_LABEL)
            self._highlight_var.set(CUSTOM_HIGHLIGHT_LABEL)
        elif self._quarter_mode_var.get().startswith("Specific"):
            choices = tuple(self._date_combo["values"])
            if self._date_var.get() not in choices:
                self._date_var.set(next(iter(choices), ""))
        else:
            self._date_var.set(LATEST_LABEL)
        self._refresh_quarter_controls()
        self._changed()

    def _select_quarter_offset(self, _event=None):
        if self._updating:
            return
        self._refresh_quarter_controls()
        self._changed()

    def get_auth(self, require=False):
        client_id = self._client_id_var.get().strip()
        secret = self._secret_var.get()
        if len(client_id) > 500 or len(secret) > 4000:
            raise ValueError("Copernicus OAuth credentials are too long.")
        if require and not (client_id and secret):
            raise ValueError(
                "Enter a Copernicus Sentinel Hub OAuth Client ID and Client secret under Access "
                "before selecting Copernicus."
            )
        return {"client_id": client_id, "client_secret": secret}

    def _keep_date_after_selection(self, previous, previous_unit, keep_list=False):
        # Keep a chosen date across layer/product changes of the same date
        # granularity; the next date list falls back to Latest if it is missing.
        unit = (self._selected_layer() or {}).get("date_granularity")
        keep = previous != "latest" and previous_unit == unit
        self._kept_date = previous if keep else None
        if keep_list and previous_unit == unit:
            # Layers of one product share their dates; show them until the refresh.
            self._update_date_choices(self._dates, previous if keep else "latest")
        else:
            self._set_date_choices(previous if keep else "latest")
        return self._restore_cached_dates()

    def _show_dates_loading(self):
        values = tuple(self._date_combo["values"])
        if LOADING_DATES_LABEL not in values:
            self._date_label_before_loading = self._date_var.get()
            self._date_combo.configure(values=values[:1] + (LOADING_DATES_LABEL,) + values[1:])

    def _hide_dates_loading(self):
        values = tuple(self._date_combo["values"])
        if LOADING_DATES_LABEL in values:
            self._date_combo.configure(
                values=tuple(value for value in values if value != LOADING_DATES_LABEL)
            )
        if self._date_var.get() == LOADING_DATES_LABEL:
            self._date_var.set(self._date_label_before_loading)

    def _repost_open_date_list(self):
        """Show newly loaded dates in a date dropdown that is already open."""
        try:
            popdown = self._date_combo.tk.call("ttk::combobox::PopdownWindow", self._date_combo)
            if int(self._date_combo.tk.call("winfo", "ismapped", popdown)):
                self._date_combo.tk.call("ttk::combobox::Unpost", self._date_combo)
                self._date_combo.tk.call("ttk::combobox::Post", self._date_combo)
        except tk.TclError:
            pass

    def _date_selection_changed(self, cached):
        self._changed()
        if not cached and self._client_id_var.get() and self._secret_var.get():
            self.refresh_dates()

    def _select_configuration(self, _event=None):
        if self._updating:
            return
        previous = self._date_by_label.get(self._date_var.get(), "latest")
        previous_unit = self._date_unit
        self._updating = True
        try:
            self._build_mission_choices(self._mission_var.get(), prefer_natural=True)
            self._build_product_choices("")
            self._build_layer_choices("")
            self._build_zoom_choices()
            self._build_highlight_choices()
            cached = self._keep_date_after_selection(previous, previous_unit)
        finally:
            self._updating = False
        self._date_selection_changed(cached)

    def _select_mission(self, _event=None):
        if self._updating:
            return
        previous = self._date_by_label.get(self._date_var.get(), "latest")
        previous_unit = self._date_unit
        self._updating = True
        try:
            self._build_product_choices("")
            self._build_layer_choices("")
            self._build_zoom_choices()
            self._build_highlight_choices()
            cached = self._keep_date_after_selection(previous, previous_unit)
        finally:
            self._updating = False
        self._date_selection_changed(cached)

    def _select_product(self, _event=None):
        if self._updating:
            return
        previous = self._date_by_label.get(self._date_var.get(), "latest")
        previous_unit = self._date_unit
        self._updating = True
        try:
            self._build_layer_choices("")
            self._build_zoom_choices()
            self._build_highlight_choices()
            cached = self._keep_date_after_selection(previous, previous_unit)
        finally:
            self._updating = False
        self._date_selection_changed(cached)

    def _select_layer(self, _event=None):
        if self._updating:
            return
        previous = self._date_by_label.get(self._date_var.get(), "latest")
        previous_unit = self._date_unit
        self._updating = True
        try:
            self._build_zoom_choices()
            self._build_highlight_choices()
            cached = self._keep_date_after_selection(previous, previous_unit, keep_list=True)
        finally:
            self._updating = False
        # Layers of one product can differ in their tone rule (True color, False color).
        self._refresh_cloud_control()
        self._date_selection_changed(cached)

    def _select_highlight(self, _event=None):
        if self._updating:
            return
        item = self._highlight_by_label.get(self._highlight_var.get())
        if item is None:
            self._changed()
            return
        theme = self._selected_theme()
        product = get_product(theme["id"], item["product"])
        if product is None:
            return
        self._updating = True
        try:
            self._build_mission_choices(product["missions"][0])
            self._build_product_choices(product["id"])
            self._build_layer_choices(item["layer"])
            self._build_zoom_choices(item["map_zoom"])
            self._latitude_var.set(str(item["latitude"]))
            self._longitude_var.set(str(item["longitude"]))
            self._quarter_mode_var.set("Specific quarter")
            self._kept_date = None
            self._set_date_choices(item["date"])
            self._refresh_quarter_controls()
            self._status_var.set("Example scene loaded with its own acquisition date. "
                                 "Choose Latest available under Date / time to keep it current.")
        finally:
            self._updating = False
        self._changed()

    def _custom_location_changed(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            self._highlight_var.set(CUSTOM_HIGHLIGHT_LABEL)
        finally:
            self._updating = False
        self._changed()
        self._schedule_inputs_refresh()

    def set_location(self, latitude, longitude):
        """Latitude and longitude from Find location; the highlight becomes custom."""
        self._latitude_var.set(f"{float(latitude):.8f}".rstrip("0").rstrip("."))
        self._longitude_var.set(f"{float(longitude):.8f}".rstrip("0").rstrip("."))
        # An open Recommend window takes the place too and waits for Check.
        if self._advice_window is not None and self._advice_window.is_open:
            self._advice_window.set_place(latitude, longitude)

    def _image_coordinates(self):
        """Latitude and longitude of the Image tab fields, or None while one is invalid."""
        from marblescape_location_search import parse_coordinates
        return parse_coordinates(f"{self._latitude_var.get()}, {self._longitude_var.get()}")

    def open_recommendation(self, find_coordinates=None, place_name=None):
        """Open the Recommend window for the current selection, or raise the open one."""
        from marblescape_copernicus_advice import current_text, fetch_advice, is_mosaic, variant_profile
        from marblescape_copernicus_advice_window import RecommendationWindow

        if self._advice_window is not None and self._advice_window.is_open:
            self._advice_window.window.lift()
            return self._advice_window
        try:
            profile = self.get_profile()
            size = tuple(map(int, self._effective_output_size()))
        except ValueError as exc:
            self._status_var.set(str(exc))
            return None
        layer = self._selected_layer() or {}
        auth = self.get_auth()
        unavailable = ""
        if layer.get("data_type") == "dem":
            unavailable = "This layer has no acquisition dates."
        elif not (auth["client_id"] and auth["client_secret"]):
            unavailable = "Enter the Copernicus OAuth client under Access first."
        timeout, user_agent = self._timeout, self._user_agent

        def run_check(latitude, longitude, zoom, progress, precise=False):
            # Runs in a worker thread: plain values only, no Tk objects.
            client = CopernicusClient(auth["client_id"], auth["client_secret"], timeout,
                                      user_agent, network_attempts=1)
            checked = dict(profile, latitude=latitude, longitude=longitude, map_zoom=zoom, highlight="")
            return fetch_advice(client, checked, size, progress=progress, precise=precise,
                                mask_progress=lambda page, pages: progress(page, pages, "masks"))

        def estimate(latitude, longitude, zoom):
            # Local arithmetic: processing units of one preview and one mask.
            checked = dict(profile, latitude=latitude, longitude=longitude, map_zoom=zoom, highlight="")
            try:
                return CopernicusClient().small_units(checked, size)
            except ValueError:
                return None

        def load_preview(variant, newest, latitude, longitude, zoom):
            # Runs in a worker thread: plain values only, no Tk objects.
            client = CopernicusClient(auth["client_id"], auth["client_secret"], timeout,
                                      user_agent, network_attempts=1)
            checked = dict(profile, latitude=latitude, longitude=longitude, map_zoom=zoom, highlight="")
            return client.render_preview(variant_profile(checked, variant), size, newest.isoformat())

        current = current_text(profile, layer)
        self._advice_window = RecommendationWindow(
            self.frame.winfo_toplevel(), title="Recommendation - Copernicus",
            selection=f"{self._mission_var.get()} · {self._layer_var.get()}", current=current,
            latitude=profile["latitude"], longitude=profile["longitude"], zoom=profile["map_zoom"],
            zoom_values=tuple(self._zoom_combo["values"]), run_check=run_check,
            apply=self.apply_recommendation, find_coordinates=find_coordinates, unavailable=unavailable,
            place_name=place_name, image_coordinates=self._image_coordinates,
            load_preview=None if unavailable else load_preview,
            estimate=None if unavailable else estimate, precise_available=not unavailable,
            precise_default=profile["auto_recommendation"] and profile["auto_precise"],
            mosaic=is_mosaic(layer),
        )
        return self._advice_window

    def apply_recommendation(self, variant, latitude, longitude, zoom):
        """Take a recommended row into the form: a draft until Save or OK."""
        self._updating = True
        try:
            if variant.period is not None:
                self._apply_period(variant)
            else:
                if variant.cloud_limit is not None:
                    self._cloud_var.set(int(variant.cloud_limit))
                    self._cloud_value_var.set(f"{int(variant.cloud_limit)}%")
                self._coverage_var.set(next(label for label, value in COVERAGE_LABELS.items()
                                            if value == variant.coverage_mode))
                if variant.lookback_days:
                    self._lookback_var.set(next(label for label, days in LOOKBACK_LABELS.items()
                                                if days == variant.lookback_days))
        finally:
            self._updating = False
        self._refresh_coverage_controls()
        self.set_location(latitude, longitude)
        self._build_zoom_choices(int(zoom))
        self._changed()

    def _apply_period(self, variant):
        """A mosaic period: the newest as Latest available, an older quarter or month
        relative to now, an older year as that year."""
        dates = list(getattr(self, "_dates", []))
        if variant.newest:
            self._quarter_mode_var.set(LATEST_LABEL)
            self._update_date_choices(dates, "latest")
        elif variant.offset is not None:
            offsets = QUARTER_OFFSET_LABELS if variant.granularity == "quarter" else MONTH_OFFSET_LABELS
            self._quarter_mode_var.set("Relative to now")
            self._quarter_offset_var.set(next(label for label, value in offsets.items()
                                              if value == variant.offset))
            self._update_date_choices(dates, "latest")
        else:
            self._update_date_choices(dates, variant.period.isoformat())

    def browser_preview(self, coordinates=None):
        """(url, status) for the Copernicus Browser at ``coordinates`` or the saved place."""
        from marblescape_location_search import copernicus_browser_url
        try:
            latitude, longitude = coordinates or (float(self._latitude_var.get()),
                                                  float(self._longitude_var.get()))
        except ValueError:
            return None
        try:
            zoom = int(self._zoom_var.get())
        except ValueError:
            zoom = DEFAULT_PROFILE["map_zoom"]
        return (copernicus_browser_url(latitude, longitude, zoom),
                "Opened the Copernicus Browser at this place and map zoom; choose the product and "
                "date there.")

    def _custom_date_changed(self, *_args):
        if self._updating:
            return
        if self._date_var.get() == LOADING_DATES_LABEL:
            self._date_var.set(self._date_label_before_loading)
            return
        self._updating = True
        try:
            self._highlight_var.set(CUSTOM_HIGHLIGHT_LABEL)
        finally:
            self._updating = False
        self._changed()

    def _changed(self, *_args):
        if self._updating:
            return
        if self._activity.active:
            self._generation += 1
            self._activity.finish(False)
            self._refresh_button.configure(state="normal")
            self._hide_dates_loading()
        if self._on_change:
            try:
                self._on_change()
            except (ValueError, tk.TclError):
                pass
        # Another selection no longer matches the picture on screen.
        self._refresh_tone_values()

    def _open_oauth_settings(self):
        try:
            opened = webbrowser.open_new_tab(ACCOUNT_SETTINGS_URL)
        except (OSError, webbrowser.Error) as exc:
            self._status_var.set("Could not open Copernicus OAuth settings: " + str(exc))
            return
        if opened is False:
            self._status_var.set(
                "Open " + ACCOUNT_SETTINGS_URL + " and choose User Settings > OAuth clients."
            )
        else:
            self._status_var.set("Opened the free Copernicus OAuth client settings in your browser.")

    def _clear_usage(self):
        self._credits_role_var.set("Role: -")
        for variable in self._credits_values.values():
            variable.set("-")

    def _credentials_changed(self, *_args):
        if self._closed:
            return
        self._usage_generation += 1
        self._credits_refresh_button.configure(state="normal")
        self._clear_usage()
        self._credits_status_var.set("Credentials changed. Refresh credits to update the account.")

    def refresh_usage(self):
        if self._closed:
            return
        self._usage_generation += 1
        generation = self._usage_generation
        self._clear_usage()
        try:
            auth = self.get_auth(require=True)
        except ValueError as exc:
            self._credits_status_var.set(str(exc))
            return
        self._credits_refresh_button.configure(state="disabled")
        self._credits_status_var.set("Loading account credits...")

        def worker():
            try:
                client = CopernicusClient(
                    auth["client_id"], auth["client_secret"], self._timeout,
                    self._user_agent, network_attempts=1,
                )
                result = client.account_usage()
                self._usage_results.put((generation, result, ""))
            except Exception as exc:
                self._usage_results.put((generation, None, str(exc)))

        threading.Thread(target=worker, name="MarbleScape-Copernicus-credits", daemon=True).start()

    def _refresh_clicked(self):
        """Refresh catalogue: like any date reload, but it reports how it went."""
        before = self._generation
        self.refresh_dates()
        if self._generation != before:
            self._manual_generation = self._generation
        else:
            # The selection could not be read: nothing was requested.
            self._manual_generation = before
            self._report_refresh(before, self._status_var.get() or "catalogue unavailable")

    def _report_refresh(self, generation, problem):
        if generation != getattr(self, "_manual_generation", None):
            return
        self._manual_generation = None
        callback = getattr(self, "on_catalogue_refreshed", None)
        if callable(callback):
            try:
                callback(str(problem or ""))
            except Exception:
                pass

    def refresh_dates(self):
        try:
            profile = self.get_profile(for_catalogue=True)
            auth = self.get_auth(require=False)
            size = self._effective_output_size()
            width, height = int(size[0]), int(size[1])
        except Exception as exc:
            self._activity.finish(issues=True)
            self._status_var.set(str(exc))
            return
        self._generation += 1
        generation = self._generation
        self._refresh_button.configure(state="disabled")
        self._status_var.set("Loading all available acquisition dates from Copernicus...")
        self._activity.start()
        self._show_dates_loading()

        def worker():
            dates = None
            problem = "no OAuth credentials; enter them under Access"
            if auth["client_id"] and auth["client_secret"]:
                client = CopernicusClient(
                    auth["client_id"], auth["client_secret"], self._timeout,
                    self._user_agent, network_attempts=1,
                )
                for _attempt in range(self._catalogue_retries + 1):
                    if NETWORK_ACTIVITY.event.is_set():
                        return
                    try:
                        dates = client.list_dates(profile, (width, height))
                        problem = ""
                        break
                    except Exception as exc:
                        problem = str(exc)
            cache = self._catalogue_client
            if NETWORK_ACTIVITY.event.is_set():
                return
            if dates is not None:
                if callable(getattr(cache, "store_copernicus_dates", None)):
                    cache.store_copernicus_dates(profile, (width, height), dates)
                self._results.put((generation, dates, None, False))
                return
            cached = (
                cache.cached_copernicus_dates(profile, (width, height))
                if callable(getattr(cache, "cached_copernicus_dates", None)) else None
            )
            if cached is not None:
                self._results.put((generation, cached, problem, True))
            else:
                self._results.put((generation, None, problem, False))

        threading.Thread(target=worker, name="MarbleScape-Copernicus-catalogue", daemon=True).start()

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        if self._quarter_mode_var.get() == "Relative to now":
            reference = self._reference_date()
            if reference != getattr(self, "_last_period_reference", None):
                self._last_period_reference = reference
                self._refresh_quarter_controls()
        try:
            while True:
                generation, dates, error, cached = self._results.get_nowait()
                if generation != self._generation:
                    continue
                self._refresh_button.configure(state="normal")
                if error and not cached:
                    # Without a date list the kept date cannot be checked; keep it.
                    self._kept_date = None
                    self._hide_dates_loading()
                    self._activity.finish(issues=True)
                    self._status_var.set("Copernicus catalogue unavailable: " + error[:260].rstrip(".") + ".")
                    self._report_refresh(generation, error)
                    continue
                self._hide_dates_loading()
                selected, notice = self._confirm_kept_date(
                    dates, self._date_by_label.get(self._date_var.get(), "latest")
                )
                self._update_date_choices(dates, selected)
                self._repost_open_date_list()
                self._status_var.set(
                    f"Copernicus catalogue loaded: {len(dates)} acquisition date"
                    + ("" if len(dates) == 1 else "s") + "."
                    + ("\n" + notice if notice else "")
                    + (("\nCatalogue notice: " + error[:260] + "; using cached catalogue data.")
                       if cached and error else "")
                )
                self._activity.finish(success=not cached, issues=bool(cached and error))
                self._report_refresh(generation, error if cached else "")
        except queue.Empty:
            pass
        try:
            while True:
                generation, usage, error = self._usage_results.get_nowait()
                if generation != self._usage_generation:
                    continue
                self._credits_refresh_button.configure(state="normal")
                if error:
                    self._credits_status_var.set("Credits unavailable: " + error[:240])
                    continue
                self._credits_role_var.set("Role: " + usage["role"])
                for category in ("processingUnitsMonthly", "requestsMonthly"):
                    for field in ("configuration", "consumed", "remaining"):
                        self._credits_values[(category, field)].set(usage[category][field])
                self._credits_since_var.set(
                    "Consumed since " + dt.date.today().replace(day=1).strftime("%d-%m-%Y")
                )
                self._credits_status_var.set("Credits updated.")
        except queue.Empty:
            pass
        if not self._closed:
            self._after_id = self.frame.after(100, self._poll)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        self._closed = True
        if self._date_refresh_after is not None:
            try:
                self.frame.after_cancel(self._date_refresh_after)
            except tk.TclError:
                pass
            self._date_refresh_after = None
        self._generation += 1
        self._usage_generation += 1
        if self._advice_window is not None and self._advice_window.is_open:
            self._advice_window.close()
        self._activity.close()
        if self._usage_after_id is not None:
            try:
                self.frame.after_cancel(self._usage_after_id)
            except tk.TclError:
                pass
            self._usage_after_id = None
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
