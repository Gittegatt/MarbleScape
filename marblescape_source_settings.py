"""Source-specific image settings with network catalogue work off the Tk thread."""

from copy import deepcopy
import math
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import colorchooser, ttk

from marblescape_catalogues import CatalogueClient
from marblescape_catalogue_activity import CatalogueActivity, reserve_text_lines
from marblescape_noaa import JPEG_DRAFT_LIMIT, STORM_CATEGORY, NOAAClient
from marblescape_copernicus import (
    DEFAULT_PROFILE as DEFAULT_COPERNICUS_PROFILE,
    MAX_WEB_MERCATOR_LATITUDE,
    normalize_profile as normalize_copernicus_profile,
)
from marblescape_copernicus_settings import CopernicusSettings
from marblescape_eumetsat import (
    EumetsatSettings,
    normalize_profile as normalize_eumetsat_profile,
)
from marblescape_himawari import (
    DEFAULT_SHORELINE_COLOR, STORM_PREFIX as HIMAWARI_STORM_PREFIX, VIEW_LATITUDE_LIMIT,
    disk_pixel,
)
from marblescape_location_search import DEFAULT_SEARCH_ENDPOINT, LocationSearch
from marblescape_slider import cached_content_box, effective_resolution
from marblescape_source_layout import COLOR_VALUE_WIDTH, SOURCE_COMBO_WIDTH, configure_source_columns, size_text
from marblescape_theme import entry_placeholder, on_theme_change, style_swatch
from marblescape_source_defaults import default_source_profiles


PROVIDER_LABELS = {
    "eumetsat": "EUMETSAT",
    "goes_east": "GOES-East",
    "goes_west": "GOES-West",
    "solar": "Solar / Sun (SUVI)",
    "himawari": "Himawari",
    "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser",
    "worldview": "NASA Worldview",
}
GOES_SATELLITES = {"goes_east": "GOES-East", "goes_west": "GOES-West"}
STORM_NOTE = ("Active storms are temporary. MarbleScape checks this list hourly; when a "
              "storm ends, a profile with it shows LOST in the Profiles table.")
# Himawari centres the full disk on the storm's latest position at each update.
HIMAWARI_STORM_NOTE = STORM_NOTE + (" The full disk is centred on the storm's latest position "
                                    "at each update; zoom in to see it larger.")
# The NICT areas whose full disk can be centred on coordinates.
HIMAWARI_CENTERED_AREAS = frozenset(("nict_full_disk", "nict_full_disk_bands"))
HIMAWARI_CENTER_NOTE = ("The full disk as Himawari sees it, with this place in the middle once "
                        "zoomed in. Decimal degrees; South and West are negative.")
# The Himawari settings these controls edit; area, product and size come from the lists.
HIMAWARI_OPTIONS = ("shorelines", "shoreline_color", "center", "latitude", "longitude")
IMAGE_SOURCE_CHOICES = {
    "eumetsat": "EUMETSAT",
    "goes": "NOAA GOES",
    "solar": "Solar / Sun (SUVI)",
    "himawari": "Himawari",
    "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser",
    "worldview": "NASA Worldview",
}


def image_source_label(provider):
    return IMAGE_SOURCE_CHOICES["goes"] if provider in GOES_SATELLITES else IMAGE_SOURCE_CHOICES[provider]


DEFAULT_PROFILES = default_source_profiles()

CATALOGUE_PROVIDERS = frozenset(
    ("goes_east", "goes_west", "solar", "himawari", "slider", "worldview")
)


def _natural_color_rank(item):
    """Rank natural-looking imagery before thematic composites in a new selection."""
    name = re.sub(r"[^a-z0-9]+", "", (str(item.get("label", "")) + " " + str(item.get("id", ""))).casefold())
    if "truecolorreproduction" in name:
        return 0
    if "truecolor" in name or "truecolour" in name or "geocolor" in name or "geocolour" in name:
        return 1
    if "naturalcolor" in name or "naturalcolour" in name:
        return 2
    return 3


def _complete(profile, provider=None):
    if provider == "eumetsat":
        try:
            normalize_eumetsat_profile(profile)
            return True
        except (TypeError, ValueError):
            return False
    if provider == "copernicus":
        try:
            normalize_copernicus_profile(profile)
            return True
        except (TypeError, ValueError):
            return False
    return (
        isinstance(profile, dict)
        and isinstance(profile.get("area"), str) and bool(profile["area"].strip())
        and isinstance(profile.get("product"), str) and bool(profile["product"].strip())
        and (profile.get("resolution") in {"auto", "largest"}
             or bool(re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", str(profile.get("resolution", "")))))
    )


# A failed SLIDER padding measurement (network) is retried after this pause,
# not on every switch back to the sector.
SLIDER_MEASURE_RETRY_SECONDS = 600
_SLIDER_MEASURE_FAILED = {}


def _with_slider_sizes(client, area_id, products, measure_now=True):
    """Add a SLIDER sector's visible image sizes; measure its padding once if asked."""
    slider_client = getattr(client, "slider", client)
    measure = getattr(slider_client, "content_box", None)
    failed = _SLIDER_MEASURE_FAILED.get(area_id)
    recently_failed = failed is not None and time.monotonic() - failed < SLIDER_MEASURE_RETRY_SECONDS
    if (measure_now and products and callable(measure) and not recently_failed
            and cached_content_box(area_id) is None):
        product = next((item for item in products if item.get("id") == "geocolor"), products[0])
        try:
            measure(area_id, product["id"])
            _SLIDER_MEASURE_FAILED.pop(area_id, None)
        except Exception:
            # Sizes stay the tile grid until a measurement succeeds.
            _SLIDER_MEASURE_FAILED[area_id] = time.monotonic()
    if cached_content_box(area_id) is None:
        return products
    result = []
    for item in products:
        item = dict(item)
        item["effective_resolutions"] = {
            value: effective_resolution(area_id, value) for value in item.get("resolutions", ())
        }
        result.append(item)
    return result


# Sources served by the NOAA still renderer, which decodes JPEGs reduced.
JPEG_DRAFT_PROVIDERS = frozenset({"goes_east", "goes_west", "solar"})


def _drafted_size(source_size, output_size, zoom):
    """The size Pillow's JPEG draft decodes for the NOAA still renderer."""
    width, height = source_size
    target = [max(1, min(JPEG_DRAFT_LIMIT, int(value * max(1, zoom)))) for value in output_size]
    scale = min(width // target[0], height // target[1])
    # As Pillow: the largest reduction that keeps both sides at the target, else none.
    divisor = next((value for value in (8, 4, 2) if scale >= value), 1)
    return -(-width // divisor), -(-height // divisor)


def enlargement(source_size, output_size, zoom, fit_mode="fit", draft=False, view_scale=1.0):
    """How many output pixels one source pixel covers at ``zoom`` (above 1: enlarged).

    ``view_scale`` is a view's own zoom on top, e.g. a Himawari storm view.
    """
    out_width, out_height = output_size
    pick = min if fit_mode == "fit" else max
    width, height = _drafted_size(source_size, output_size, zoom * view_scale) if draft else source_size
    return pick(out_width / width, out_height / height) * zoom * view_scale


def sharp_zoom_limit(source_size, output_size, fit_mode="fit", draft=False, view_scale=1.0):
    """Highest zoom (0.05-20) at which the still image is not enlarged beyond its pixels.

    Zoom enlarges the centre of the picture; ``draft`` models the reduced JPEG
    decoding of the NOAA renderer. 0 means it is enlarged even at the smallest zoom.
    """
    best = 0.0
    for step in range(5, 2001):
        zoom = step / 100
        if enlargement(source_size, output_size, zoom, fit_mode, draft, view_scale) <= 1 + 1e-9:
            best = zoom
    return best


def still_zoom_lines(source_image, output_size, fit_mode, zoom=None, storm_view_km=None, view_scale=1.0):
    """The two lines under Zoom for still-image sources: (result, facts, enlarged), or None.

    The result first: "Zoom 1.5: enlarged 1.9× (sharp up to zoom 0.78)", then the
    facts behind it. A storm view (``storm_view_km`` across at zoom 1, ``view_scale``
    its own zoom) is enlarged on top of Zoom.
    """
    if not source_image or not output_size:
        return None
    width, height, which, draft = source_image
    limit = sharp_zoom_limit((width, height), output_size, fit_mode, draft, view_scale)
    sharp = ("enlarged at every zoom" if limit <= 0
             else f"up to zoom {limit:.2g}" if limit < 10 else f"up to zoom {limit:.0f}")
    source = "Largest source" if which == "largest" else "Source"
    facts = f"{source} {width}×{height} on {output_size[0]}×{output_size[1]}"
    if storm_view_km:
        facts = f"Storm view, {storm_view_km} km across at zoom 1 · {facts[0].lower()}{facts[1:]}"
    try:
        zoom = float(zoom)
    except (TypeError, ValueError):
        return (f"Sharp {sharp}", facts, False)
    factor = enlargement((width, height), output_size, zoom, fit_mode, draft, view_scale)
    if zoom <= limit or factor < 1.05:
        return (f"Zoom {zoom:g}: sharp ({sharp})", facts, False)
    # Clouds stay smooth when enlarged; coasts and small islands look softer.
    sharp = "enlarged at every zoom" if limit <= 0 else f"sharp {sharp}"
    return (f"Zoom {zoom:g}: enlarged {factor:.1f}× ({sharp})", facts, True)


def still_zoom_hint(source_image, output_size, fit_mode, zoom=None, storm_view_km=None, view_scale=1.0):
    """The two lines of still_zoom_lines as one text, or ""."""
    lines = still_zoom_lines(source_image, output_size, fit_mode, zoom, storm_view_km, view_scale)
    return "\n".join(lines[:2]) if lines else ""


class SourceSettings:
    """Embed ``frame`` in the Image tab and call ``close`` when its dialog closes.

    ``get_selection`` returns (provider, copied profiles), or raises ValueError
    if a newly selected catalogue combination has not finished loading or Copernicus
    credentials are missing. The caller owns persistence. Bundled source
    selections remain usable offline.
    """

    def __init__(self, parent, provider, profiles, timeout=90,
                 user_agent="MarbleScape", on_change=None, client=None,
                 copernicus_auth=None, output_size=(1920, 1080),
                 eumetsat_layer=None, reference_date=None, account_parent=None, credits_parent=None,
                 location_search_endpoint=DEFAULT_SEARCH_ENDPOINT, location_search=None):
        self.frame = ttk.LabelFrame(parent, text="Source", padding=8)
        configure_source_columns(self.frame)
        self._on_change = on_change
        self._view_defaults_requested = False
        self._replace_profiles(profiles)
        self._provider = provider if provider in PROVIDER_LABELS else "eumetsat"
        self._client = client if client is not None else CatalogueClient(
            timeout=timeout, user_agent=user_agent,
            noaa=NOAAClient(timeout=timeout, user_agent=user_agent),
        )
        self._client_lock = threading.Lock()
        self._results = queue.Queue()
        self._selection_requests = queue.Queue()
        self._ui_thread = threading.get_ident()
        self._generation = 0
        self._worker_state = {
            "closed": False,
            "generation": self._generation,
            "provider": self._provider,
        }
        self._global_refresh_seen_running = False
        self._closed = False
        self._after_id = None
        self._areas = []
        self._products = []
        self._area_by_label = {}
        self._product_by_label = {}
        self._resolution_by_label = {}
        # Pixel sizes behind the resolution choices, for the Zoom hint.
        self._source_sizes = {}
        self._source_image_listeners = []
        # The host's rule for Automatic: (key, width, height) choices -> key.
        self._automatic_resolution = None
        self._loading = False
        self._goes_provider = self._provider if self._provider in GOES_SATELLITES else "goes_east"
        self._provider_var = tk.StringVar(self.frame, image_source_label(self._provider))
        self._goes_var = tk.StringVar(self.frame, GOES_SATELLITES[self._goes_provider])
        self._category_var = tk.StringVar(self.frame)
        self._filter_var = tk.StringVar(self.frame)
        self._area_var = tk.StringVar(self.frame)
        self._product_var = tk.StringVar(self.frame)
        self._resolution_var = tk.StringVar(self.frame)
        self._status_var = tk.StringVar(self.frame)
        # Own section beside the Source section; the host grids it. Only the
        # selected source's catalogue controls are shown.
        self.catalogue_frame = ttk.LabelFrame(parent, padding=8)
        self.catalogue_frame.columnconfigure(0, weight=1)
        # Own section below Source, gridded by the host: how the picture is
        # rendered (each source's resolution, Copernicus tone corrections).
        self.rendering_frame = ttk.LabelFrame(parent, text="Rendering", padding=8)
        configure_source_columns(self.rendering_frame)
        # EUMETSAT's render quality; the host adds its controls.
        self.eumetsat_rendering_frame = ttk.Frame(self.rendering_frame)
        self.eumetsat_rendering_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        configure_source_columns(self.eumetsat_rendering_frame)
        # NICT's coastlines (Plot shorelines on its website), built like Copernicus'
        # Country borders: a switch, the color swatch, Choose color... and the value.
        self.himawari_rendering_frame = ttk.Frame(self.rendering_frame)
        self.himawari_rendering_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        configure_source_columns(self.himawari_rendering_frame)
        self._shorelines_var = tk.BooleanVar(self.frame)
        self._shoreline_color_var = tk.StringVar(self.frame, DEFAULT_SHORELINE_COLOR)
        ttk.Checkbutton(self.himawari_rendering_frame, text="Plot shorelines",
                        variable=self._shorelines_var, command=self._himawari_option_changed,
                        ).grid(row=0, column=0, pady=3, sticky="w")
        shoreline_colors = ttk.Frame(self.himawari_rendering_frame)
        shoreline_colors.grid(row=0, column=1, sticky="ew")
        self._shoreline_preview = tk.Label(shoreline_colors, width=2, height=1)
        style_swatch(self._shoreline_preview)
        self._shoreline_preview.pack(side="left", padx=(0, 7))
        self._shoreline_button = ttk.Button(shoreline_colors, text="Choose color…",
                                            command=self._choose_shoreline_color)
        self._shoreline_button.pack(side="left")
        ttk.Label(shoreline_colors, textvariable=self._shoreline_color_var,
                  width=COLOR_VALUE_WIDTH).pack(side="left", padx=8)
        self._shoreline_color_var.trace_add("write", lambda *_args: self._refresh_shoreline_preview())
        # Loading the theme resets plain tk colors; the swatch shows the chosen one again.
        on_theme_change(self._shoreline_preview, self._refresh_shoreline_preview)
        self.himawari_rendering_frame.grid_remove()
        # Copernicus Data Space access. The host grids it: on its own Access tab
        # (account_parent) it always shows; otherwise it shows for Copernicus only.
        self._account_external = account_parent is not None
        self.account_frame = ttk.Frame(account_parent if account_parent is not None else parent)
        self.account_frame.columnconfigure(0, weight=1)
        self._provider_combo, _ = self._combo(
            0, "Image source", self._provider_var, tuple(IMAGE_SOURCE_CHOICES.values()))
        self._provider_combo.bind("<<ComboboxSelected>>", self._select_provider)
        self._goes_combo, self._goes_label = self._combo(
            1, "Satellite", self._goes_var, tuple(GOES_SATELLITES.values())
        )
        self._goes_combo.bind("<<ComboboxSelected>>", self._select_goes_satellite)
        # EUMETSAT owns a dependent catalogue; the host adds view controls below it.
        self.eumetsat_frame = ttk.Frame(self.frame)
        self.eumetsat_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        configure_source_columns(self.eumetsat_frame)
        self.eumetsat_settings = EumetsatSettings(
            self.eumetsat_frame,
            self._profiles["eumetsat"],
            timeout=timeout,
            user_agent=user_agent,
            on_change=self._eumetsat_changed,
            client=getattr(self._client, "eumetsat", None),
            auto_refresh=False,
            catalogue_parent=self.catalogue_frame,
        )
        self.eumetsat_settings.catalogue_frame.grid(row=0, column=0, sticky="ew")
        if eumetsat_layer:
            self.eumetsat_settings.set_profile(
                self._profiles["eumetsat"], layer=eumetsat_layer
            )
        self.eumetsat_settings.frame.grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        self.eumetsat_view_frame = self.eumetsat_settings.view_frame
        # NASA Worldview lists many layer categories: a filter for them above
        # the dropdown, laid out like the area filter below it.
        self._categories = []
        self._category_filter_var = tk.StringVar(self.frame)
        self._category_filter_label = ttk.Label(self.frame, text="Filter categories")
        self._category_filter_label.grid(row=2, column=0, padx=(0, 10), pady=3, sticky="w")
        self._category_filter_frame = ttk.Frame(self.frame)
        self._category_filter_frame.grid(row=2, column=1, pady=3, sticky="ew")
        self._category_filter_frame.columnconfigure(0, weight=1)
        self._category_filter_entry = ttk.Entry(
            self._category_filter_frame, textvariable=self._category_filter_var, width=1)
        self._category_filter_entry.grid(row=0, column=0, sticky="ew")
        self._category_filter_clear_button = ttk.Button(
            self._category_filter_frame, text="Clear", command=lambda: self._category_filter_var.set(""))
        self._category_filter_clear_button.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self._category_filter_var.trace_add("write", self._filter_categories)
        # The help text sits in the empty field, as in Find location.
        self._category_filter_placeholder = entry_placeholder(
            self._category_filter_entry, self._category_filter_var, "Filters the layer category names.")
        self._category_filter_widgets = (self._category_filter_label, self._category_filter_frame)
        self._category_combo, self._category_label = self._combo(
            4, "Area category", self._category_var)
        self._category_combo.bind("<<ComboboxSelected>>", self._select_category)
        self._filter_label = ttk.Label(self.frame, text="Filter areas")
        self._filter_label.grid(row=5, column=0, padx=(0, 10), pady=3, sticky="w")
        # Field and Clear as in the Profiles tab; together exactly as wide as the
        # other fields (the Copernicus entries and every dropdown).
        self._filter_frame = ttk.Frame(self.frame)
        self._filter_frame.grid(row=5, column=1, pady=3, sticky="ew")
        self._filter_frame.columnconfigure(0, weight=1)
        self._filter_entry = ttk.Entry(self._filter_frame, textvariable=self._filter_var, width=1)
        self._filter_entry.grid(row=0, column=0, sticky="ew")
        self._filter_clear_button = ttk.Button(self._filter_frame, text="Clear",
                                               command=lambda: self._filter_var.set(""))
        self._filter_clear_button.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self._filter_var.trace_add("write", self._filter_areas)
        self._filter_placeholder = entry_placeholder(
            self._filter_entry, self._filter_var, "Filters area names and IDs within the selected category.")
        self._area_combo, self._area_label = self._combo(7, "Area / location", self._area_var)
        self._area_combo.bind("<<ComboboxSelected>>", self._select_area)
        # Below the area, aligned with it like the Zoom note, while NOAA's Active storms are chosen.
        self._storm_note = ttk.Label(self.frame, text=STORM_NOTE, wraplength=420, justify="left")
        self._storm_note.grid(row=8, column=1, pady=(0, 3), sticky="w")
        self._storm_note.grid_remove()
        self._category_var.trace_add("write", lambda *_args: self._show_storm_note())
        self._product_combo, self._product_label = self._combo(9, "Product / layer", self._product_var)
        self._product_combo.bind("<<ComboboxSelected>>", self._select_product)
        self._resolution_combo, self._resolution_label = self._combo(
            0, "Source resolution", self._resolution_var, parent=self.rendering_frame)
        self._resolution_combo.bind("<<ComboboxSelected>>", self._select_resolution)
        self.generic_view_frame = ttk.Frame(self.frame)
        # No padding of its own: its rows keep the 6 px gap of the rows above.
        self.generic_view_frame.grid(
            row=10, column=0, columnspan=2, pady=0, sticky="ew"
        )
        configure_source_columns(self.generic_view_frame)
        self._slider_note = ttk.Label(
            self.frame,
            text="CIRA product tiles are loaded without map borders or latitude/longitude lines.",
            wraplength=570, justify="left",
        )
        self._slider_note.grid(row=11, column=0, columnspan=2, pady=(3, 3), sticky="w")
        # Himawari: NICT's full disk centred on coordinates; Find location fills them.
        self._options_loading = False
        # Refresh catalogue reports how it went (Settings shows it below its buttons).
        self._catalogue_listeners = []
        self._manual_refresh = False
        self.himawari_center_frame = ttk.Frame(self.frame)
        self.himawari_center_frame.grid(row=12, column=0, columnspan=2, sticky="ew")
        configure_source_columns(self.himawari_center_frame)
        self._center_var = tk.BooleanVar(self.frame)
        self._latitude_var = tk.StringVar(self.frame)
        self._longitude_var = tk.StringVar(self.frame)
        ttk.Checkbutton(self.himawari_center_frame, text="Center on coordinates",
                        variable=self._center_var, command=self._himawari_option_changed,
                        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        self._center_entries = []
        for row, text, variable in ((1, "Latitude", self._latitude_var),
                                    (2, "Longitude", self._longitude_var)):
            ttk.Label(self.himawari_center_frame, text=text).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w")
            entry = ttk.Entry(self.himawari_center_frame, textvariable=variable, width=1)
            entry.grid(row=row, column=1, pady=3, sticky="ew")
            variable.trace_add("write", lambda *_args: self._himawari_option_changed())
            self._center_entries.append(entry)
        ttk.Label(self.himawari_center_frame, text=HIMAWARI_CENTER_NOTE, wraplength=420,
                  justify="left").grid(row=3, column=1, pady=(0, 3), sticky="w")
        self.himawari_center_frame.grid_remove()
        self._actions = ttk.Frame(self.catalogue_frame)
        self._actions.grid(row=0, column=0, sticky="ew")
        self._actions.columnconfigure(1, weight=1)
        self._refresh_button = ttk.Button(self._actions, text="Refresh catalogue", command=self._refresh)
        self._refresh_button.grid(row=0, column=0, sticky="w")
        self._status_label = ttk.Label(self._actions, textvariable=self._status_var,
                                       wraplength=570, justify="left")
        self._status_label.grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="nw")
        reserve_text_lines(self._status_label)
        self._catalogue_activity = CatalogueActivity(self._actions, row=2)
        self._area_widgets = (self._category_label, self._category_combo,
                              self._filter_label, self._filter_frame,
                              self._area_label, self._area_combo)
        self._product_widgets = (self._product_label, self._product_combo,
                                 self._resolution_label, self._resolution_combo,
                                 self._status_label)
        self._noaa_widgets = (*self._area_widgets, *self._product_widgets, self._actions)
        # Own small section below Rendering, gridded by the host with
        # grid_recommend_section: Use auto recommendation and Compare variants,
        # for Copernicus.
        self.recommend_frame = ttk.LabelFrame(parent, text="Recommendation", padding=8)
        configure_source_columns(self.recommend_frame)
        self.copernicus_settings = CopernicusSettings(
            self.frame,
            self._profiles["copernicus"],
            auth=copernicus_auth,
            timeout=timeout,
            user_agent=user_agent,
            output_size=output_size,
            reference_date=reference_date,
            on_change=self._copernicus_changed,
            catalogue_client=self._client,
            catalogue_retries=getattr(self._client, "retries", 2),
            catalogue_parent=self.catalogue_frame,
            auth_parent=self.account_frame,
            credits_parent=credits_parent,
            rendering_parent=self.rendering_frame,
            recommend_parent=self.recommend_frame,
        )
        self.copernicus_settings.rendering_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.copernicus_settings.catalogue_frame.grid(row=0, column=0, sticky="ew")
        self.copernicus_settings.frame.grid(
            row=2, column=0, columnspan=2, sticky="ew"
        )
        # Own section above Imagery updates for sources with latitude/longitude; the host
        # places it with grid_location_section and adds further targets.
        # EUMETSAT's and Copernicus' own Refresh catalogue report like the others.
        self.eumetsat_settings.on_catalogue_refreshed = (
            lambda problem: self._report_catalogue("eumetsat", problem))
        self.copernicus_settings.on_catalogue_refreshed = (
            lambda problem: self._report_catalogue("copernicus", problem))
        self.location_frame = ttk.LabelFrame(parent, text="Find location", padding=8)
        self._location_placed = False
        self._location_targets = {
            "copernicus": (self.copernicus_settings.set_location, lambda: True,
                           MAX_WEB_MERCATOR_LATITUDE, self.copernicus_settings.browser_preview),
            "himawari": (self._set_himawari_center, self._himawari_centerable,
                         VIEW_LATITUDE_LIMIT, None),
        }
        self.location_search = LocationSearch(
            self.location_frame, self._transfer_location,
            endpoint=location_search_endpoint, user_agent=user_agent, timeout=timeout,
            search=location_search, preview=self._preview_location,
        )
        self._recommend_placed = False
        self.recommend_button = ttk.Button(self.recommend_frame, text="Compare variants...",
                                           command=self.open_recommendation)
        self.recommend_button.grid(row=4, column=0, padx=(0, 10), pady=(3, 0), sticky="w")
        ttk.Label(self.recommend_frame, justify="left", text=(
            "Compares cloud limits and Gap fill, or the periods of a mosaic, for this view.")).grid(
            row=4, column=1, pady=(3, 0), sticky="w")
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        self._display_provider()
        self._sync_global_refresh()
        self._after_id = self.frame.after(100, self._poll)

    def _replace_profiles(self, profiles):
        self._profiles = deepcopy(DEFAULT_PROFILES)
        self._usable = {key: False for key in DEFAULT_PROFILES}
        for key, profile in (profiles or {}).items():
            if key in self._profiles and isinstance(profile, dict):
                self._profiles[key].update(deepcopy(profile))
                self._usable[key] = _complete(profile, key)
        self._last_valid_profiles = deepcopy(DEFAULT_PROFILES)
        for key, profile in self._profiles.items():
            if self._usable[key]:
                self._last_valid_profiles[key] = deepcopy(profile)

    def set_selection(self, provider, profiles):
        """Load saved image settings; calls from workers are queued for Tk.

        On the UI thread the change is immediate. The caller's dictionaries are
        copied, and on_change runs on the UI thread after the form is updated.
        An ongoing full catalogue refresh is independent of this selection.
        """
        if provider not in PROVIDER_LABELS:
            raise ValueError("Unknown image source.")
        if profiles is not None and not isinstance(profiles, dict):
            raise ValueError("Source profiles must be a dictionary.")
        if self._closed:
            return
        profiles = deepcopy(profiles)
        if threading.get_ident() != self._ui_thread:
            self._selection_requests.put((provider, profiles))
            return
        self._replace_profiles(profiles)
        self._provider = provider
        if provider in GOES_SATELLITES:
            self._goes_provider = provider
            self._goes_var.set(GOES_SATELLITES[provider])
        self._provider_var.set(image_source_label(provider))
        self.eumetsat_settings.set_profile(self._profiles["eumetsat"])
        self._display_provider()
        if self._on_change:
            self._on_change(provider)

    @property
    def provider(self):
        return self._provider

    @property
    def view_defaults_requested(self):
        return self._view_defaults_requested

    def _combo(self, row, text, variable, choices=(), parent=None):
        parent = self.frame if parent is None else parent
        label = ttk.Label(parent, text=text)
        label.grid(row=row, column=0, padx=(0, 10), pady=3, sticky="w")
        combo = ttk.Combobox(parent, textvariable=variable, values=choices,
                             state="readonly", width=SOURCE_COMBO_WIDTH)
        combo.grid(row=row, column=1, pady=3, sticky="ew")
        return combo, label

    def grid_location_section(self, **options):
        """Place Find location; it shows only while a source's latitude/longitude do."""
        self.location_frame.grid(**options)
        self._location_placed = True
        self.refresh_location_section()

    def add_location_target(self, provider, transfer, available=None, latitude_limit=90.0,
                            preview=None):
        """Let Find location fill ``provider``'s latitude/longitude.

        ``transfer(latitude, longitude)`` sets them; ``available()`` says whether
        they are shown now. Call ``refresh_location_section`` when that changes.
        ``preview(coordinates or None)`` returns (url, status) for Preview, or None.
        """
        self._location_targets[provider] = (transfer, available or (lambda: True),
                                            float(latitude_limit), preview)
        self.refresh_location_section()

    def refresh_location_section(self, *_args):
        target = self._location_targets.get(self._provider)
        if target is not None:
            self.location_search.latitude_limit = target[2]
            # Only a viewer that takes the place and zoom is worth a Preview.
            self.location_search.set_preview_available(target[3] is not None)
        if self._location_placed:
            self._set_visible((self.location_frame,), target is not None and bool(target[1]()))

    def grid_recommend_section(self, **options):
        """Place Recommendation; it shows only for Copernicus."""
        self.recommend_frame.grid(**options)
        self._recommend_placed = True
        self._set_visible((self.recommend_frame,), self._provider == "copernicus")

    def open_recommendation(self):
        from marblescape_location_search import parse_coordinates
        return self.copernicus_settings.open_recommendation(
            find_coordinates=lambda: parse_coordinates(self.location_search.coordinates_var.get()),
            place_name=self.location_search.place_name)

    def _preview_location(self, coordinates):
        target = self._location_targets.get(self._provider)
        return target[3](coordinates) if target is not None and target[3] else None

    def _transfer_location(self, latitude, longitude):
        target = self._location_targets.get(self._provider)
        if target is not None:
            target[0](latitude, longitude)

    def add_source_image_listener(self, callback):
        """Call ``callback()`` when the still image Zoom enlarges may have changed."""
        self._source_image_listeners.append(callback)

    def _source_image_changed(self):
        self.refresh_resolution_label()
        for callback in tuple(self._source_image_listeners):
            callback()

    def selected_area(self):
        """The area (or sector, layer) chosen for the shown source, or ""."""
        return self._profiles.get(self._provider, {}).get("area", "")

    def source_image_size(self):
        """(width, height, "selected" or "largest", JPEG draft) of the still image, or None."""
        if self._provider not in CATALOGUE_PROVIDERS or not self._resolution_by_label:
            return None
        resolution = self._profiles[self._provider].get("resolution", "auto")
        which = "selected" if resolution not in {"auto", "largest"} else "largest"
        size = self._source_sizes.get(resolution if which == "selected" else "largest")
        if size is None:
            return None
        return size[0], size[1], which, self._provider in JPEG_DRAFT_PROVIDERS

    def set_automatic_resolution(self, callback):
        """``callback(choices)`` returns the key Automatic takes among (key, width, height)."""
        self._automatic_resolution = callback
        self.refresh_resolution_label()

    def resolved_source_size(self):
        """(width, height) the selected resolution gives with the current draft, or None."""
        if self._provider not in CATALOGUE_PROVIDERS or not self._resolution_by_label:
            return None
        resolution = self._profiles[self._provider].get("resolution", "auto")
        if resolution == "auto":
            if not callable(self._automatic_resolution):
                return None
            choices = [(key, *size) for key, size in self._source_sizes.items() if key != "largest"]
            try:
                resolution = self._automatic_resolution(choices) if choices else None
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                return None
        return self._source_sizes.get(resolution)

    def refresh_resolution_label(self):
        """Source (Render) resolution, followed by its pixel size, e.g. "(5424x5424)"."""
        text = "Render resolution" if self._provider == "worldview" else "Source resolution"
        size = self.resolved_source_size()
        self._resolution_label.configure(text=f"{text} ({size[0]} × {size[1]})" if size else text)

    def _set_visible(self, widgets, visible):
        for widget in widgets:
            widget.grid() if visible else widget.grid_remove()

    def _show_storm_note(self):
        self._storm_note.configure(text=HIMAWARI_STORM_NOTE if self._provider == "himawari" else STORM_NOTE)
        self._set_visible((self._storm_note,), (self._provider in GOES_SATELLITES or self._provider == "himawari")
                          and self._category_var.get() == STORM_CATEGORY)

    def _himawari_nict(self):
        """True for NICT's tiled areas (and the storms on them): shorelines exist there."""
        area = self._profiles["himawari"].get("area", "")
        return area.startswith("nict_") or area.startswith(HIMAWARI_STORM_PREFIX)

    def _himawari_centerable(self):
        return (self._provider == "himawari"
                and self._profiles["himawari"].get("area") in HIMAWARI_CENTERED_AREAS)

    def _show_himawari_options(self):
        """Fill the shoreline and centre controls from the Himawari selection."""
        profile = self._profiles["himawari"]
        self._options_loading = True
        try:
            self._shorelines_var.set(bool(profile.get("shorelines", False)))
            self._shoreline_color_var.set(profile.get("shoreline_color", DEFAULT_SHORELINE_COLOR))
            self._center_var.set(bool(profile.get("center", False)))
            self._latitude_var.set(f"{float(profile.get('latitude', 0.0)):g}")
            self._longitude_var.set(f"{float(profile.get('longitude', 0.0)):g}")
        finally:
            self._options_loading = False
        self._refresh_himawari_controls()

    def _refresh_himawari_controls(self):
        himawari = self._provider == "himawari"
        self._set_visible((self.himawari_rendering_frame,), himawari and self._himawari_nict())
        self._set_visible((self.himawari_center_frame,), self._himawari_centerable())
        self._shoreline_button.configure(state="normal" if self._shorelines_var.get() else "disabled")
        for entry in self._center_entries:
            entry.configure(state="normal" if self._center_var.get() else "disabled")
        self.refresh_location_section()

    def _himawari_option_changed(self):
        if self._options_loading:
            return
        self._apply_himawari_options()
        self._refresh_himawari_controls()

    def _apply_himawari_options(self, strict=False):
        """Copy the controls into the Himawari selection; ``strict`` rejects bad coordinates."""
        profile = self._profiles["himawari"]
        profile["shorelines"] = bool(self._shorelines_var.get())
        profile["shoreline_color"] = self._shoreline_color_var.get()
        profile["center"] = bool(self._center_var.get())
        for key, variable, limit in (("latitude", self._latitude_var, 90.0),
                                     ("longitude", self._longitude_var, 180.0)):
            try:
                value = float(variable.get().strip().replace(",", "."))
                if not math.isfinite(value) or abs(value) > limit:
                    raise ValueError
            except ValueError:
                if strict and profile["center"] and self._himawari_centerable():
                    raise ValueError(f"Enter the Himawari {key} in decimal degrees "
                                     f"from -{limit:g} to {limit:g}.") from None
                continue
            profile[key] = value
        if strict and profile["center"] and self._himawari_centerable():
            try:
                disk_pixel(profile["latitude"], profile["longitude"], 11000)
            except ValueError:
                raise ValueError("Himawari cannot see this place: it shows East Asia, Australia "
                                 "and the western Pacific.") from None
        if self._usable["himawari"]:
            self._last_valid_profiles["himawari"].update(
                {key: profile[key] for key in HIMAWARI_OPTIONS})

    def _set_himawari_center(self, latitude, longitude):
        """Find location's Transfer: center the full disk on the place."""
        self._options_loading = True
        try:
            self._latitude_var.set(f"{latitude:.5f}".rstrip("0").rstrip("."))
            self._longitude_var.set(f"{longitude:.5f}".rstrip("0").rstrip("."))
            self._center_var.set(True)
        finally:
            self._options_loading = False
        self._himawari_option_changed()

    def _choose_shoreline_color(self):
        if not self._shorelines_var.get():
            return
        _rgb, color = colorchooser.askcolor(color=self._shoreline_color_var.get(),
                                            parent=self.frame, title="Plot shorelines color")
        if color is not None:
            self._shoreline_color_var.set(color.upper())
            self._himawari_option_changed()

    def _refresh_shoreline_preview(self):
        value = self._shoreline_color_var.get()
        self._shoreline_preview.configure(
            background=value if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) else "#000000")

    def _select_provider(self, _event=None):
        label = self._provider_var.get()
        selected = next((key for key, value in IMAGE_SOURCE_CHOICES.items() if value == label), None)
        if selected == "goes":
            selected = self._goes_provider
        if selected is None or selected == self._provider:
            return
        self._provider = selected
        self._display_provider()
        if self._on_change:
            self._view_defaults_requested = True
            try:
                self._on_change(self._provider)
            finally:
                self._view_defaults_requested = False

    def _select_goes_satellite(self, _event=None):
        selected = next((key for key, value in GOES_SATELLITES.items()
                         if value == self._goes_var.get()), None)
        if selected is None:
            return
        self._goes_provider = selected
        if self._provider not in GOES_SATELLITES or selected == self._provider:
            return
        self._provider = selected
        self._display_provider()
        if self._on_change:
            self._view_defaults_requested = True
            try:
                self._on_change(selected)
            finally:
                self._view_defaults_requested = False

    def _display_provider(self):
        self._advance_generation()
        self._catalogue_activity.finish(False)
        self._areas = []
        self._products = []
        self._area_by_label = {}
        self._product_by_label = {}
        self._resolution_by_label = {}
        self._filter_var.set("")
        self._categories = []
        self._category_filter_var.set("")
        self._category_var.set("")
        is_catalogue = self._provider in CATALOGUE_PROVIDERS
        is_copernicus = self._provider == "copernicus"
        self._set_visible((self._goes_label, self._goes_combo), self._provider in GOES_SATELLITES)
        self._set_visible((self.eumetsat_frame,), self._provider == "eumetsat")
        self._set_visible(self._area_widgets, is_catalogue and self._provider != "solar")
        self._set_visible(self._category_filter_widgets, self._provider == "worldview")
        self._set_visible(self._product_widgets, is_catalogue)
        self._set_visible((self._refresh_button, self._status_label), is_catalogue)
        self.catalogue_frame.configure(text="Catalogue refresh - " + image_source_label(self._provider))
        self._set_visible((self._actions,), is_catalogue)
        self._set_visible((self.eumetsat_settings.catalogue_frame,), self._provider == "eumetsat")
        self._set_visible((self.copernicus_settings.catalogue_frame,), is_copernicus)
        self._set_visible((self._slider_note,), self._provider == "slider")
        self._set_visible(
            (self.generic_view_frame,),
            is_catalogue and not is_copernicus,
        )
        self._set_visible((self.copernicus_settings.frame,), is_copernicus)
        self.refresh_location_section()
        if self._recommend_placed:
            self._set_visible((self.recommend_frame,), is_copernicus)
        self._source_image_changed()
        self._set_visible((self.copernicus_settings.rendering_frame,), is_copernicus)
        self._set_visible((self.eumetsat_rendering_frame,), self._provider == "eumetsat")
        if not self._account_external:
            self._set_visible((self.copernicus_settings.auth_frame,), is_copernicus)
        self._product_label.configure(
            text=("Channel" if self._provider == "solar" else
                  "Date / time" if self._provider == "worldview" else "Product / layer")
        )
        self._category_label.configure(
            text=("Satellite" if self._provider == "slider" else
                  "Layer category" if self._provider == "worldview" else "Area category")
        )
        self._filter_label.configure(
            text=("Filter sectors" if self._provider == "slider" else
                  "Filter imagery layers" if self._provider == "worldview" else "Filter areas")
        )
        self._filter_placeholder.configure(
            text=("Filters sector names and IDs within the selected satellite."
                  if self._provider == "slider" else
                  "Filters NASA visualization names and IDs within the selected category."
                  if self._provider == "worldview" else
                  "Filters area names and IDs within the selected category.")
        )
        self._area_label.configure(
            text=("Sector" if self._provider == "slider" else
                  "Imagery layer" if self._provider == "worldview" else "Area / location")
        )
        self.refresh_resolution_label()
        self._show_himawari_options()
        if is_copernicus:
            self._loading = False
            self.copernicus_settings.set_profile(self._profiles["copernicus"])
            self.copernicus_settings.refresh_dates()
            return
        if self._provider == "eumetsat":
            self._loading = False
            status = getattr(self._client, "catalogue_refresh_status", {}) or {}
            use_cache = getattr(self._client, "catalogue_cached_for_automatic_use", None)
            cached = callable(use_cache) and use_cache("eumetsat")
            self.eumetsat_settings.refresh(not bool(status.get("running")) and not cached)
            return
        if not is_catalogue:
            self._loading = False
            self._refresh_button.configure(state="disabled")
            return
        self._show_saved_profile()
        if not self._show_cached_catalogue():
            status = getattr(self._client, "catalogue_refresh_status", {}) or {}
            self._load_areas(refresh=not bool(status.get("running")))

    def _show_cached_catalogue(self):
        cached_areas = getattr(self._client, "cached_areas", None)
        cached_products = getattr(self._client, "cached_products", None)
        if not callable(cached_areas) or not callable(cached_products):
            return False
        areas = cached_areas(self._provider)
        if not areas:
            return False
        self._receive_areas(areas, refresh=False, request_products=False)
        profile = self._profiles[self._provider]
        if not any(item["id"] == profile["area"] for item in areas):
            return True
        products = cached_products(self._provider, profile["area"])
        if products:
            self._receive_products(products)
            return True
        self._load_products(refresh=False)
        return True

    def _show_saved_profile(self):
        profile = self._profiles[self._provider]
        for combo, variable, value in (
            (self._area_combo, self._area_var, profile.get("area", "")),
            (self._product_combo, self._product_var, profile.get("product", "")),
            (self._resolution_combo, self._resolution_var, profile.get("resolution", "")),
        ):
            label = ("Automatic (recommended)" if value == "auto" else
                     "Largest available" if value == "largest" else size_text(value))
            variable.set(label)
            combo.configure(values=(label,) if label else (), state="disabled")
        self._category_combo.configure(values=(), state="disabled")
        self._set_filter_enabled(False)

    def _request(self, kind, refresh=False, area_id=None):
        self._advance_generation()
        generation, provider = self._generation, self._provider
        self._loading = True
        source = PROVIDER_LABELS.get(provider, provider)
        if provider == "worldview":
            message = ("Loading NASA Worldview layers..." if kind == "areas"
                       else "Loading dates and render sizes...")
        else:
            message = (f"Loading {source} areas..." if kind == "areas"
                       else "Loading products and image sizes...")
        self._status_var.set(message)
        self._catalogue_activity.start()
        self._refresh_button.configure(state="disabled", text="Refresh catalogue")
        client, client_lock, results = self._client, self._client_lock, self._results
        worker_state = self._worker_state

        def worker():
            try:
                # Do not queue an obsolete request behind a slow catalogue call.
                # The same check remains inside the lock to close the race
                # between this fast path and acquiring the shared client.
                if (worker_state["closed"] or generation != worker_state["generation"]
                        or provider != worker_state["provider"]):
                    return
                with client_lock:
                    # A newer selection may have superseded this request while
                    # it waited for the client. Only inspect plain Python state
                    # here; all widget access stays on the Tk thread.
                    if (worker_state["closed"] or generation != worker_state["generation"]
                            or provider != worker_state["provider"]):
                        return
                    value = (client.list_areas(provider, refresh=refresh) if kind == "areas"
                             else client.list_products(provider, area_id, refresh=refresh))
                    measure = (kind == "products" and provider == "slider"
                               and cached_content_box(area_id) is None)
                    if kind == "products" and provider == "slider":
                        value = _with_slider_sizes(client, area_id, value, measure_now=False)
                # The products show at once; a first SLIDER padding measurement
                # then only refines the image sizes.
                results.put((generation, provider, kind, value, None, refresh))
                if measure and value:
                    if (worker_state["closed"] or generation != worker_state["generation"]
                            or provider != worker_state["provider"]):
                        return
                    with client_lock:
                        sized = _with_slider_sizes(client, area_id, value)
                    if cached_content_box(area_id) is not None:
                        results.put((generation, provider, "slider_sizes", (area_id, sized), None, refresh))
            except Exception as exc:
                results.put((generation, provider, kind, None, str(exc), refresh))

        threading.Thread(target=worker, name="MarbleScape-catalogue", daemon=True).start()

    def _load_areas(self, refresh=False):
        if not self._areas:
            self._category_combo.configure(state="disabled")
            self._set_filter_enabled(False)
            self._area_combo.configure(state="disabled")
        if not self._products:
            self._product_combo.configure(state="disabled")
            self._resolution_combo.configure(state="disabled")
        self._request("areas", refresh=refresh)

    def _advance_generation(self):
        self._generation += 1
        self._worker_state.update(
            closed=self._closed,
            generation=self._generation,
            provider=self._provider,
        )

    def _load_products(self, refresh=False):
        profile = self._profiles[self._provider]
        if not self._products or not self._usable[self._provider]:
            self._products = []
            self._product_by_label = {}
            self._product_var.set(profile.get("product", ""))
            resolution = profile.get("resolution", "")
            self._resolution_var.set(
                "Automatic (recommended)" if resolution == "auto" else
                "Largest available" if resolution == "largest" else
                size_text(effective_resolution(profile.get("area", ""), resolution)
                          if self._provider == "slider" else resolution)
            )
            self._product_combo.configure(state="disabled")
            self._resolution_combo.configure(state="disabled")
        self._request("products", refresh=refresh, area_id=profile["area"])

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        try:
            while True:
                provider, profiles = self._selection_requests.get_nowait()
                self.set_selection(provider, profiles)
        except queue.Empty:
            pass
        self._sync_global_refresh()
        try:
            while True:
                generation, provider, kind, value, error, refresh = self._results.get_nowait()
                if generation != self._generation or provider != self._provider:
                    continue
                self._loading = False
                self._refresh_button.configure(state="normal")
                if error:
                    self._show_error(error)
                    continue
                try:
                    if kind == "areas":
                        self._receive_areas(value, refresh)
                    elif kind == "slider_sizes":
                        self._receive_slider_sizes(*value)
                    else:
                        self._receive_products(value)
                except (ValueError, KeyError, TypeError) as exc:
                    self._show_error(str(exc))
        except queue.Empty:
            pass
        if not self._closed:
            self._after_id = self.frame.after(100, self._poll)

    def add_catalogue_listener(self, callback):
        """Call ``callback(source label, problem text or "")`` when Refresh catalogue ends."""
        self._catalogue_listeners.append(callback)

    def _catalogue_refresh_done(self, problem=""):
        """Report a Refresh catalogue (not a plain catalogue load) once it ends."""
        if not self._manual_refresh:
            return
        self._manual_refresh = False
        self._report_catalogue(self._provider, problem)

    def _report_catalogue(self, provider, problem=""):
        for callback in tuple(self._catalogue_listeners):
            try:
                callback(image_source_label(provider), str(problem or ""))
            except Exception:
                pass

    def _show_error(self, error):
        self._catalogue_refresh_done(error)
        self._catalogue_activity.finish(issues=True)
        suffix = (" The saved selection stays usable." if self._usable.get(self._provider)
                  else " Retry before saving this selection.")
        reason = str(error)[:180].rstrip(".")
        self._status_var.set(f"{image_source_label(self._provider)} catalogue unavailable: {reason}." + suffix)
        self._refresh_button.configure(state="normal", text="Retry catalogue")

    @staticmethod
    def _labels(items):
        # Including IDs keeps duplicate location/product names distinguishable.
        return {f"{item['label']} [{item['id']}]": item for item in items}

    def _receive_areas(self, areas, refresh, request_products=True):
        if not areas:
            noun = "layers" if self._provider == "worldview" else "areas"
            raise ValueError(f"No {noun} are currently listed for this source.")
        self._areas = areas
        profile = self._profiles[self._provider]
        selected = next((item for item in areas if item["id"] == profile["area"]), None)
        categories = list(dict.fromkeys(str(item.get("category", "Areas")) for item in areas))
        self._categories = categories
        self._category_combo.configure(state="readonly")
        self._filter_categories()
        if selected is not None:
            self._category_var.set(str(selected.get("category", "Areas")))
        elif self._category_var.get() not in categories:
            self._category_var.set(categories[0])
        self._set_filter_enabled(True)
        self._filter_areas()
        if selected is None:
            # Moving/retired storm sectors can disappear. Keep the configured
            # source visible until the user deliberately chooses a replacement.
            suffix = (" You can keep the saved selection, but new images may be unavailable."
                      if self._usable[self._provider]
                      else " Select an available area before saving.")
            noun = "layer" if self._provider == "worldview" else "area"
            self._status_var.set(f"The selected {noun} is no longer listed by its provider." + suffix)
            self._catalogue_activity.finish(issues=True)
            self._refresh_button.configure(state="normal", text="Refresh catalogue")
            return
        if not request_products:
            return
        self._catalogue_activity.set_progress(1, 2)
        # CIRA and NASA publish areas and products in one catalogue document.
        # The area request above already refreshed it; rereading it here can
        # trigger a second slow network transfer for the same button click.
        offline = getattr(self._client, "catalogue_offline", None)
        if callable(offline) and offline(self._provider):
            cached_products = getattr(self._client, "cached_products", None)
            products = cached_products(self._provider, profile["area"]) \
                if callable(cached_products) else None
            if products:
                self._receive_products(products)
                return
        self._load_products(refresh=refresh and self._provider not in ("slider", "worldview"))

    def _set_filter_enabled(self, enabled):
        for entry, button in ((self._filter_entry, self._filter_clear_button),
                              (self._category_filter_entry, self._category_filter_clear_button)):
            entry.configure(state="normal" if enabled else "disabled")
            button.state(["!disabled"] if enabled else ["disabled"])

    def _filter_categories(self, *_args):
        """Narrow the category popup; the shown category stays until another is chosen."""
        if not self._categories:
            return
        needle = self._category_filter_var.get().strip().casefold()
        self._category_combo.configure(values=[
            category for category in self._categories if not needle or needle in category.casefold()])

    def _filter_areas(self, *_args):
        if not self._areas:
            return
        category = self._category_var.get()
        needle = self._filter_var.get().strip().casefold()
        matches = [item for item in self._areas
                   if str(item.get("category", "Areas")) == category
                   and (not needle or needle in (item["label"] + " " + item["id"]).casefold())]
        self._area_by_label = self._labels(matches)
        self._area_combo.configure(values=list(self._area_by_label), state="readonly")
        selected_id = self._profiles[self._provider]["area"]
        selected = next((item for item in self._areas if item["id"] == selected_id), None)
        # Filtering narrows the popup only; it must not silently change a saved area.
        if selected:
            self._area_var.set(f"{selected['label']} [{selected['id']}]")

    def _select_category(self, _event=None):
        self._filter_var.set("")
        self._filter_areas()
        if not self._area_by_label:
            self._advance_generation()
            self._loading = False
            self._profiles[self._provider].update(area="", product="", resolution="auto")
            self._usable[self._provider] = False
            self._products = []
            self._product_by_label = {}
            self._resolution_by_label = {}
            for variable in (self._area_var, self._product_var, self._resolution_var):
                variable.set("")
            for combo in (self._area_combo, self._product_combo, self._resolution_combo):
                combo.configure(values=(), state="disabled")
            self._status_var.set("No areas are currently available in this category. Select another category or refresh the catalogue.")
            self._catalogue_activity.finish(False)
            self._source_image_changed()
            self._refresh_button.configure(state="normal")
            return
        label = next(iter(self._area_by_label))
        if self._provider == "worldview":
            label = min(self._area_by_label, key=lambda value: _natural_color_rank(self._area_by_label[value]))
        self._area_var.set(label)
        self._select_area()

    def _select_area(self, _event=None):
        selected = self._area_by_label.get(self._area_var.get())
        if selected is None:
            return
        profile = self._profiles[self._provider]
        if selected["id"] == profile["area"]:
            return
        profile.update(area=selected["id"], product="", resolution="auto")
        self._usable[self._provider] = False
        self._refresh_himawari_controls()
        self._load_products()

    def _receive_slider_sizes(self, area_id, products):
        """The sector's padding is measured: show its visible image sizes."""
        if self._profiles[self._provider].get("area") != area_id or not self._products:
            return
        self._products = [item for item in products if item.get("resolutions")]
        self._product_by_label = self._labels(self._products)
        selected = self._product_by_label.get(self._product_var.get())
        if selected is not None:
            self._set_resolutions(selected)

    def _receive_products(self, products):
        products = [item for item in products if item.get("resolutions")]
        if not products:
            self._usable[self._provider] = False
            raise ValueError("No still images are currently listed for this area.")
        self._products = products
        self._product_by_label = self._labels(products)
        profile = self._profiles[self._provider]
        selected = next((item for item in products if item["id"] == profile["product"]), None)
        if selected is None:
            preferred = DEFAULT_PROFILES[self._provider]["product"]
            selected = next(
                (item for item in products if item["id"] == preferred),
                min(products, key=_natural_color_rank),
            )
            profile["resolution"] = "auto"
        profile["product"] = selected["id"]
        self._product_combo.configure(values=list(self._product_by_label), state="readonly")
        self._product_var.set(f"{selected['label']} [{selected['id']}]")
        self._set_resolutions(selected)

    def _select_product(self, _event=None):
        selected = self._product_by_label.get(self._product_var.get())
        if selected is None:
            return
        profile = self._profiles[self._provider]
        if profile["product"] != selected["id"]:
            profile["resolution"] = "auto"
        profile["product"] = selected["id"]
        self._set_resolutions(selected)

    def _set_resolutions(self, product):
        profile = self._profiles[self._provider]
        resolutions = [str(value) for value in product["resolutions"]
                       if re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", str(value))]
        if not resolutions:
            self._usable[self._provider] = False
            raise ValueError("No supported still-image sizes are listed for this product.")
        largest = max(resolutions, key=lambda value: (
            int(value.split("x")[0]) * int(value.split("x")[1]),
            int(value.split("x")[0]), int(value.split("x")[1])))
        # SLIDER lists square tile grids; show the visible size without padding.
        effective = product.get("effective_resolutions") or {}
        label_of = {value: size_text(effective.get(value, value)) for value in resolutions}
        largest_label = f"Largest available ({label_of[largest]})"
        automatic_label = "Automatic (recommended)"
        self._resolution_by_label = {
            automatic_label: "auto",
            largest_label: "largest",
            **{label_of[value]: value for value in resolutions},
        }
        resolution = profile.get("resolution", "auto")
        if resolution not in {"auto", "largest"} and resolution not in resolutions:
            resolution = "auto"
        self._source_sizes = {}
        for value in resolutions:
            match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", str(effective.get(value, value)))
            if match:
                self._source_sizes[value] = (int(match.group(1)), int(match.group(2)))
        if largest in self._source_sizes:
            self._source_sizes["largest"] = self._source_sizes[largest]
        self._resolution_combo.configure(values=list(self._resolution_by_label), state="readonly")
        self._resolution_var.set(
            automatic_label if resolution == "auto" else
            largest_label if resolution == "largest" else label_of[resolution]
        )
        profile["resolution"] = resolution
        self._usable[self._provider] = True
        self._last_valid_profiles[self._provider] = deepcopy(profile)
        # One pattern for every source: "<Source> catalogue loaded: <counts>."
        # How the latest image and Automatic resolution work: Info > Image quality.
        status = f"{image_source_label(self._provider)} catalogue loaded: {self._catalogue_counts()}."
        if self._provider == "worldview":
            if product["id"] == "timeless":
                status += "\nThis NASA GIBS visualization is not time-dependent."
            elif product["id"] == "latest":
                status += "\nNASA GIBS resolves the latest available acquisition when the image is checked."
            else:
                status += f"\nNASA GIBS uses the selected fixed acquisition: {product['id']}."
        warning_for = getattr(self._client, "catalogue_warning_for", None)
        catalogue_warning = (
            str(warning_for(self._provider) or "").strip()
            if callable(warning_for)
            else str(getattr(self._client, "catalogue_warning", "") or "").strip()
        )
        if catalogue_warning:
            status += "\nCatalogue notice: " + catalogue_warning
        self._status_var.set(status)
        self._catalogue_activity.finish(success=not catalogue_warning, issues=bool(catalogue_warning))
        self._refresh_button.configure(state="normal", text="Refresh catalogue")
        self._catalogue_refresh_done(catalogue_warning)
        self._source_image_changed()

    def _catalogue_counts(self):
        """What the loaded catalogue lists, e.g. "7 areas, 18 products for the selected area"."""
        def count(items, noun):
            return f"{len(items)} {noun}" + ("" if len(items) == 1 else "s")

        if self._provider == "worldview":
            return count(self._areas, "layer")
        if self._provider == "solar":
            return count(self._products, "channel")
        area = "sector" if self._provider == "slider" else "area"
        return f"{count(self._areas, area)}, {count(self._products, 'product')} for the selected {area}"

    def _select_resolution(self, _event=None):
        shown = self._resolution_var.get()
        # Also "5424x5424" for the label "5424 × 5424".
        resolution = self._resolution_by_label.get(shown, self._resolution_by_label.get(size_text(shown)))
        if resolution is not None:
            self._profiles[self._provider]["resolution"] = resolution
            self._last_valid_profiles[self._provider] = deepcopy(self._profiles[self._provider])
            self._source_image_changed()

    def _copernicus_changed(self):
        try:
            profile = self.copernicus_settings.get_profile()
        except ValueError:
            self._usable["copernicus"] = False
            return
        self._profiles["copernicus"] = deepcopy(profile)
        self._last_valid_profiles["copernicus"] = deepcopy(profile)
        self._usable["copernicus"] = True

    def _eumetsat_changed(self):
        try:
            profile = self.eumetsat_settings.get_profile()
        except ValueError:
            self._usable["eumetsat"] = False
            return
        self._profiles["eumetsat"] = deepcopy(profile)
        self._last_valid_profiles["eumetsat"] = deepcopy(profile)
        self._usable["eumetsat"] = True
        if self._on_change and self._provider == "eumetsat":
            self._on_change("eumetsat")

    def select_eumetsat_layer(self, layer):
        self.eumetsat_settings.select_layer(layer)

    def get_eumetsat_layer(self):
        return self.eumetsat_settings.get_profile()["layer"]

    def get_copernicus_auth(self, require=False):
        return self.copernicus_settings.get_auth(
            require=require or self._provider == "copernicus"
        )

    def _refresh(self):
        if self._provider in CATALOGUE_PROVIDERS:
            self._manual_refresh = True
            self._load_areas(refresh=True)

    def _sync_global_refresh(self):
        """Reload the shown catalogue after a refresh of all catalogues.

        That refresh runs from Downloads & Updates > Catalogue refresh, at startup or on
        its daily schedule; its progress is shown there.
        """
        status = getattr(self._client, "catalogue_refresh_status", None)
        if not isinstance(status, dict):
            return
        if status.get("running"):
            self._global_refresh_seen_running = True
            return
        if not self._global_refresh_seen_running:
            return
        self._global_refresh_seen_running = False
        if self._provider in CATALOGUE_PROVIDERS:
            if not self._show_cached_catalogue():
                self._load_areas(refresh=False)
        elif self._provider == "eumetsat":
            self.eumetsat_settings.refresh(False)

    def get_selection(self):
        if self._provider == "eumetsat":
            profile = self.eumetsat_settings.get_profile()
            self._profiles["eumetsat"] = deepcopy(profile)
            self._last_valid_profiles["eumetsat"] = deepcopy(profile)
            self._usable["eumetsat"] = True
        elif self._provider == "copernicus":
            profile = self.copernicus_settings.get_profile()
            self.get_copernicus_auth(require=True)
            self._profiles["copernicus"] = deepcopy(profile)
            self._last_valid_profiles["copernicus"] = deepcopy(profile)
            self._usable["copernicus"] = True
        elif self._provider != "eumetsat":
            profile = self._profiles[self._provider]
            if self._provider == "himawari":
                self._apply_himawari_options(strict=True)
            if not _complete(profile, self._provider) or not self._usable[self._provider]:
                if self._loading:
                    raise ValueError("Please wait for the selected area, product and image sizes to load.")
                raise ValueError("Select an area, product and source resolution, or retry the catalogue.")
        # A provider changed and then left while loading must not persist an
        # incomplete profile; retain its last usable selection in that case.
        return self._provider, deepcopy(self._last_valid_profiles)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        self._closed = True
        self._catalogue_activity.close()
        self._advance_generation()
        self.eumetsat_settings.close()
        self.copernicus_settings.close()
        self.location_search.close()
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
