"""Copernicus Browser catalogue, Sentinel Hub rendering, and map composition.

The bundled catalogue is derived from the official MIT-licensed Copernicus
Browser metadata cache.  Image rendering uses the documented Copernicus Data
Space Sentinel Hub Process API.  OAuth client credentials belong to the user;
no Browser application credentials are embedded.  The account usage request
uses the read-only endpoint called by the Copernicus Dashboard.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import datetime as dt
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageMath
import certifi

from marblescape_download_progress import (
    DOWNLOAD_PROGRESS,
    DownloadCancelledError,
    ResponseTooLargeError,
    read_response,
)
from marblescape_copernicus_mosaics import (
    MOSAIC_PRODUCTS, QUARTERLY as QUARTERLY_DATA_TYPE, WORLDCOVER_ANNUAL as WORLDCOVER_DATA_TYPE,
    evalscript_for_brightness,
)
from marblescape_mosaic_adjustments import evalscript_brightness
from marblescape_network import NETWORK_ACTIVITY, open_response


TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"
ACCOUNT_SETTINGS_URL = "https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings"
ACCOUNT_USAGE_URL = "https://sh.dataspace.copernicus.eu/api/v1/accounting/usage"
OSM_BACKGROUND_URL = (
    "https://gisco-services.ec.europa.eu/maps/tiles/"
    "OSMCartoBackground/EPSG3857/{z}/{x}/{y}.png"
)
OSM_LABELS_URL = (
    "https://gisco-services.ec.europa.eu/maps/tiles/"
    "OSMCartoLabelsEN/EPSG3857/{z}/{x}/{y}.png"
)
GISCO_BORDERS_URL = (
    "https://gisco-services.ec.europa.eu/vectortiles/"
    "gisco.countries_bn_2024/{z}/{x}/{y}.pbf"
)
CATALOGUE_FILENAME = "marblescape_copernicus_catalog.json"
MAX_WEB_MERCATOR_LATITUDE = 85.05112878
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
PROCESS_TILE_LIMIT = 2500
GAP_REFINEMENT_TILE_SIZE = 512
# Recommendation: previews render the view within PREVIEW_BOX like the wallpaper;
# Precise check reads a data and cloud mask of the view within MASK_BOX.
PREVIEW_BOX = (384, 216)
# Fixed Image resolution choices: landscape, then portrait for a vertical view.
IMAGE_SIZES = ("1920x1080", "2560x1440", "3840x2160", "7680x4320",
               "1080x1920", "1440x2560", "2160x3840", "4320x7680")
IMAGE_SIZE_NAMES = {"1920x1080": "Full HD", "2560x1440": "QHD", "3840x2160": "4K UHD", "7680x4320": "8K UHD",
                    "1080x1920": "Full HD, portrait", "1440x2560": "QHD, portrait",
                    "2160x3840": "4K UHD, portrait", "4320x7680": "8K UHD, portrait"}
AUTO_IMAGE_SIZE_LABEL = "Auto (recommended)"


def image_size_label(value):
    """A saved Image resolution as shown: "3840 × 2160 (4K UHD)" or Auto (recommended)."""
    if value == "auto":
        return AUTO_IMAGE_SIZE_LABEL
    width, _x, height = str(value).partition("x")
    name = IMAGE_SIZE_NAMES.get(value)
    return f"{width} × {height}" + (f" ({name})" if name else "")
MASK_BOX = (160, 90)
# The coarsest ground pixel (metres) the Process API accepts per collection. A small
# request of a wide view renders larger and shrinks. Collections not listed use the
# default until a refusal states their limit (learnt for the session).
PIXEL_LIMITS = {"sentinel-2-l1c": 200.0, "sentinel-2-l2a": 1500.0}
DEFAULT_PIXEL_LIMIT = 1500.0
PIXEL_LIMIT_MARGIN = 0.93
_PIXEL_LIMIT_REFUSAL = re.compile(r"exceeds the limit ([0-9]+(?:\.[0-9]+)?) meters per pixel")
# Cloud masks per collection: the band and the test for a cloudy pixel. Others give
# the coverage only (radar sees through clouds).
CLOUD_MASKS = {
    "sentinel-2-l2a": ("SCL", "s.SCL == 8 || s.SCL == 9 || s.SCL == 10"),
    "sentinel-2-l1c": ("CLM", "s.CLM == 1"),
    "landsat-ot-l1": ("BQA", "((s.BQA >> 3) & 1) == 1"),
}
# Processing units per 512 x 512 pixels, from measurements (true color preview about
# 0.4 for 384 x 216; one mask band).
PREVIEW_UNITS_PER_TILE = 4 / 3
MASK_UNITS_PER_TILE = 1 / 3
MINIMUM_REQUEST_UNITS = 0.005
MAX_RESPONSE_BYTES = 192 * 1024 * 1024
MAX_CATALOGUE_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_CATALOGUE_DATES = 5000
TILE_SIZE = 256
TILE_CACHE_LIMIT = 256
GISCO_MAX_NATIVE_ZOOM = 18
NETWORK_ATTEMPTS = 3
SUPPORTED_MAP_ZOOMS = tuple(range(2, 26))
COVERAGE_MODES = ("single", "fill_gaps")
# Gap fill "black" became Single latest acquisition with a black No-data color.
LEGACY_BLACK_COVERAGE_MODE = "black"
LOOKBACK_DAYS = (3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 365, 550, 730, 920, 1095)
DATA_TYPE_ZOOM_RANGES = {
    "sentinel-1-grd": (7, 18),
    "sentinel-2-l1c": (10, 18),
    "sentinel-2-l2a": (7, 18),
    "sentinel-3-olci": (6, 18),
    "sentinel-3-olci-l2": (6, 18),
    "sentinel-3-slstr": (6, 18),
    "sentinel-3-slstr-l2": (5, 18),
    "sentinel-3-synergy-l2": (6, 18),
    "sentinel-5p-l2": (3, 19),
    "landsat-ot-l1": (7, 18),
    "dem": (7, 25),
}
SECRET_ENVIRONMENT_VARIABLE = "MARBLESCAPE_COPERNICUS_CLIENT_SECRET"
CLIENT_ID_ENVIRONMENT_VARIABLE = "MARBLESCAPE_COPERNICUS_CLIENT_ID"

DEFAULT_PROFILE = {
    "configuration": "DEFAULT-THEME",
    "mission": "Sentinel-2 Mosaics",
    "product": "MARBLESCAPE::S2-QUARTERLY",
    "layer": "TRUE_COLOR_CLOUDLESS",
    "highlight": "",
    "date": "latest",
    "date_mode": "catalogue",
    "quarter_offset": 0,
    "month_offset": 0,
    "latitude": 51.1657,
    "longitude": 10.4515,
    "map_zoom": 7,
    "map_labels": False,
    "map_label_color": "#FFFFFF",
    "map_borders": False,
    "map_border_color": "#FFFFFF",
    "coverage_mode": "single",
    "lookback_days": 14,
    "max_cloud_cover": 100,
    "brightness": 100,
    "contrast": 100,
    # New selections tone layers with a tone rule automatically; saved ones keep
    # their choice. Layers without a rule ignore these values.
    "auto_brightness": True,
    "auto_contrast": True,
    "image_size": "auto",
    # No-data color of mosaic layers, and of regular (scene) layers, where
    # "transparent" keeps the map background visible.
    "no_data_color": "blur",
    "scene_no_data_color": "blur",
    # Recommendation: render with the recommended cloud limit and Gap fill (or a
    # mosaic's period) at every check; the saved values stay as the fallback.
    "auto_recommendation": False,
    "auto_priority": "fewest_clouds",
    # Regular layers: the rule measures the view (Precise check) at every check.
    "auto_precise": False,
}
# The first priority is Fewest clouds for scenes and Newest for mosaics; either name
# means it, so a layer change keeps the choice.
AUTO_PRIORITIES = ("fewest_clouds", "newest", "full_coverage")

# Selections saved before map_label_color existed rendered black labels; a saved
# selection without the key keeps that look instead of the white default.
LEGACY_MAP_LABEL_COLOR = "#000000"
# Selections saved before the No-data colors existed rendered white mosaic gaps
# and showed the map background behind regular layers.
LEGACY_NO_DATA_COLOR = "#FFFFFF"
LEGACY_SCENE_NO_DATA_COLOR = "transparent"
# Map labels were on by default before they could be chosen per overlay.
LEGACY_MAP_LABELS = True
# New selections allow every cloud estimate; one saved before the key existed
# rendered with the former default.
LEGACY_MAX_CLOUD_COVER = 30

# Besides #RRGGBB: keep missing pixels transparent, or blur the nearby image
# into them. Far from real pixels Blur fades to the average of the whole image,
# Edge Blur to that of the gap's border (open sea next to a coast stays sea
# colored instead of taking the land's colors).
NO_DATA_CHOICES = ("transparent", "blur", "blur_edge")
BLUR_CHOICES = ("blur", "blur_edge")
# Edge Blur's border: real pixels this close to a gap, as a share of the
# picture's longer side (about 12 px at 2987 px).
NO_DATA_EDGE_SHARE = 0.004
# Blur fill: nearest-pixel growth per pyramid level, the light blur on top, and
# how far from real pixels it gives way to a smooth fill (avoids long streaks).
NO_DATA_BLUR_STEPS = 8
NO_DATA_BLUR_RADIUS = 6
NO_DATA_BLUR_REACH = 24

MAX_QUARTER_OFFSET = 40
MAX_MONTH_OFFSET = 120


def rolling_month_start(reference_date, offset):
    if not isinstance(reference_date, dt.date) or isinstance(reference_date, dt.datetime):
        raise ValueError("Rolling month reference must be a date.")
    if type(offset) is not int or not 0 <= offset <= MAX_MONTH_OFFSET:
        raise ValueError(f"Rolling month offset must be 0-{MAX_MONTH_OFFSET} months.")
    year, month = divmod(reference_date.year * 12 + reference_date.month - 1 - offset, 12)
    if not 1 <= year <= 9999:
        raise ValueError("Rolling month is outside the supported calendar range.")
    return dt.date(year, month + 1, 1)


def rolling_quarter_start(reference_date, offset):
    """Return the calendar quarter that is ``offset`` quarters before today."""
    if not isinstance(reference_date, dt.date) or isinstance(reference_date, dt.datetime):
        raise ValueError("Rolling quarter reference must be a date.")
    if type(offset) is not int or not 0 <= offset <= MAX_QUARTER_OFFSET:
        raise ValueError(f"Rolling quarter offset must be 0-{MAX_QUARTER_OFFSET} quarters.")
    quarter_index = reference_date.year * 4 + (reference_date.month - 1) // 3 - offset
    year, quarter = divmod(quarter_index, 4)
    if not 1 <= year <= 9999:
        raise ValueError("Rolling quarter is outside the supported calendar range.")
    return dt.date(year, quarter * 3 + 1, 1)

_CATALOGUE = None
_CATALOGUE_LOCK = threading.Lock()


def _verified_ssl_context():
    """Combine current Mozilla roots with the operating-system trust store."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_default_certs()
    return context


_HTTPS_CONTEXT = _verified_ssl_context()


def _verified_urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_HTTPS_CONTEXT)


def _resource_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent


def load_catalogue():
    """Return the immutable-by-convention bundled Browser catalogue."""
    global _CATALOGUE
    with _CATALOGUE_LOCK:
        if _CATALOGUE is None:
            path = _resource_directory() / CATALOGUE_FILENAME
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if value.get("schema_version") != 1 or not isinstance(value.get("themes"), list):
                raise ValueError("The bundled Copernicus catalogue has an unsupported format.")
            default_theme = next((item for item in value["themes"]
                                  if item.get("id") == "DEFAULT-THEME"), None)
            if default_theme is not None:
                default_theme["products"].extend(MOSAIC_PRODUCTS)
            _CATALOGUE = value
        return _CATALOGUE


def catalogue_revision() -> str:
    return str(load_catalogue().get("source_revision") or "unknown")


def themes():
    return load_catalogue()["themes"]


def get_theme(theme_id):
    return next((item for item in themes() if item["id"] == theme_id), None)


def missions(theme_id=None):
    selected_themes = [get_theme(theme_id)] if theme_id else themes()
    result = []
    for theme in selected_themes:
        if not theme:
            continue
        for product in theme["products"]:
            for mission in product["missions"]:
                if mission not in result:
                    result.append(mission)
    preferred = ["Sentinel-1", "Sentinel-1 Mosaics", "Sentinel-2", "Sentinel-2 Mosaics",
                 "Sentinel-3", "Sentinel-5P",
                 "Copernicus DEM", "Landsat 8/9"]
    return sorted(result, key=lambda value: (
        preferred.index(value) if value in preferred else len(preferred), value.casefold()
    ))


def products(theme_id, mission=None):
    theme = get_theme(theme_id)
    if not theme:
        return []
    return [item for item in theme["products"]
            if mission is None or mission in item["missions"]]


def get_product(theme_id, product_id):
    return next((item for item in products(theme_id) if item["id"] == product_id), None)


def get_layer(product, layer_id):
    if not product:
        return None
    return next((item for item in product["layers"] if item["id"] == layer_id), None)


def map_zooms(layer):
    """Return the zoom choices published by Copernicus Browser for a layer."""
    if not isinstance(layer, dict):
        return SUPPORTED_MAP_ZOOMS
    if "minimum_zoom" in layer:
        return tuple(range(layer["minimum_zoom"], layer["maximum_zoom"] + 1))
    low, high = DATA_TYPE_ZOOM_RANGES.get(layer.get("data_type"),
                                           (SUPPORTED_MAP_ZOOMS[0], SUPPORTED_MAP_ZOOMS[-1]))
    return tuple(range(low, high + 1))


def map_zooms_for_view(layer, latitude, output_size):
    """Return Browser-supported zooms that can contain the requested viewport."""
    try:
        latitude = float(latitude)
        width, height = (int(value) for value in output_size)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Copernicus output size and latitude must be valid numbers.") from exc
    if (not math.isfinite(latitude) or not -MAX_WEB_MERCATOR_LATITUDE <= latitude
            <= MAX_WEB_MERCATOR_LATITUDE or width <= 0 or height <= 0):
        raise ValueError("Copernicus output size or latitude is outside the supported range.")
    result = []
    for zoom in map_zooms(layer):
        _cx, cy, world_size = _world_pixel_center(latitude, 0.0, zoom)
        if layer and "low_resolution_data_type" in layer:
            if 0 < cy < world_size:
                result.append(zoom)
        elif width <= world_size and cy - height / 2 >= 0 and cy + height / 2 <= world_size:
            result.append(zoom)
    return tuple(result)


def highlights(theme_id):
    theme = get_theme(theme_id)
    return [] if not theme else theme.get("highlights", [])


def get_highlight(theme_id, highlight_id):
    return next((item for item in highlights(theme_id) if item["id"] == highlight_id), None)


def resolve_image_size(profile, fallback, targets=()):
    """Explicit image pixels, or the fallback aspect ratio enlarged for display needs."""
    selection = profile.get("image_size", "auto")
    if selection == "auto":
        width, height = fallback
        factor = max([1.0] + [max(w / width, h / height) for w, h in targets])
        # Auto never asks for more than Copernicus allows: a display that needs
        # more (a tall portrait output of a landscape picture) gets the largest.
        limit = min(32768 / max(width, height), math.sqrt(33_554_432 / (width * height)))
        enlarged = math.ceil(width * factor), math.ceil(height * factor)
        if factor >= limit or max(enlarged) > 32768 or enlarged[0] * enlarged[1] > 33_554_432:
            enlarged = math.floor(width * limit), math.floor(height * limit)
        width, height = enlarged
    else:
        width, height = map(int, selection.split("x"))
    if min(width, height) <= 0 or max(width, height) > 32768 or width * height > 33_554_432:
        raise ValueError("Copernicus image size exceeds the 32-megapixel limit; choose a smaller Image size.")
    return width, height


def normalize_profile(profile):
    """Validate one saved Copernicus selection without network access."""
    if profile is None:
        profile = {}
    if not isinstance(profile, dict):
        raise ValueError("[sources.copernicus] must be a table.")
    result = dict(DEFAULT_PROFILE)
    result.update({key: profile[key] for key in DEFAULT_PROFILE if key in profile})
    if profile and "max_cloud_cover" not in profile:
        result["max_cloud_cover"] = LEGACY_MAX_CLOUD_COVER
    if profile and "map_labels" not in profile:
        result["map_labels"] = LEGACY_MAP_LABELS
    if profile and "map_label_color" not in profile:
        result["map_label_color"] = LEGACY_MAP_LABEL_COLOR
    # Labels and country borders shared one switch and one color before.
    if profile and "map_borders" not in profile:
        result["map_borders"] = result["map_labels"]
    if profile and "map_border_color" not in profile:
        result["map_border_color"] = result["map_label_color"]
    if profile and "no_data_color" not in profile:
        result["no_data_color"] = LEGACY_NO_DATA_COLOR
    if profile and "scene_no_data_color" not in profile:
        result["scene_no_data_color"] = LEGACY_SCENE_NO_DATA_COLOR
    # Selections saved before the Auto options existed rendered without them.
    for key in ("auto_brightness", "auto_contrast"):
        if profile and key not in profile:
            result[key] = False
    if result["coverage_mode"] == LEGACY_BLACK_COVERAGE_MODE:
        result["coverage_mode"] = "single"
        if "scene_no_data_color" not in profile:
            result["scene_no_data_color"] = "#000000"

    for key in ("configuration", "mission", "product", "layer", "highlight", "date",
                "date_mode"):
        if not isinstance(result[key], str):
            raise ValueError(f"Invalid Copernicus {key}.")
        result[key] = result[key].strip()
        if key not in {"highlight"} and not result[key]:
            raise ValueError(f"Copernicus {key} cannot be empty.")
        if len(result[key]) > 400:
            raise ValueError(f"Copernicus {key} is too long.")

    theme = get_theme(result["configuration"])
    if theme is None:
        raise ValueError("The selected Copernicus configuration is unavailable.")
    if result["mission"] not in missions(theme["id"]):
        raise ValueError("The selected mission is unavailable in this Copernicus configuration.")
    product = get_product(theme["id"], result["product"])
    if product is None or result["mission"] not in product["missions"]:
        raise ValueError("The selected Copernicus product is unavailable.")
    layer = get_layer(product, result["layer"])
    if layer is None:
        raise ValueError("The selected Copernicus layer is unavailable.")
    if result["highlight"] and get_highlight(theme["id"], result["highlight"]) is None:
        raise ValueError("The selected Copernicus highlight is unavailable.")

    if result["date"].casefold() == "latest":
        result["date"] = "latest"
    else:
        try:
            parsed_date = dt.date.fromisoformat(result["date"])
        except ValueError as exc:
            raise ValueError("Copernicus date must be 'latest' or YYYY-MM-DD.") from exc
        result["date"] = parsed_date.isoformat()

    if result["date_mode"] not in {"catalogue", "relative_quarter", "relative_month"}:
        raise ValueError("Copernicus date mode must be catalogue, relative_quarter, or relative_month.")
    if type(result["quarter_offset"]) is not int:
        raise ValueError("Copernicus quarter offset must be an integer.")
    if result["date_mode"] == "relative_quarter":
        if layer.get("date_granularity") != "quarter":
            raise ValueError("Rolling Quarter is available only for quarterly mosaics.")
        if result["date"] != "latest":
            raise ValueError("Rolling Quarter cannot also select a fixed quarter.")
        if not 0 <= result["quarter_offset"] <= MAX_QUARTER_OFFSET:
            raise ValueError(
                f"Rolling quarter offset must be 0-{MAX_QUARTER_OFFSET} quarters."
            )
    elif result["quarter_offset"] != 0:
        raise ValueError("Quarter offset is only available in Rolling Quarter mode.")
    if type(result["month_offset"]) is not int:
        raise ValueError("Copernicus month offset must be an integer.")
    if result["date_mode"] == "relative_month":
        if layer.get("date_granularity") != "month" or result["date"] != "latest":
            raise ValueError("Rolling Month requires a monthly mosaic and no fixed date.")
        if not 0 <= result["month_offset"] <= MAX_MONTH_OFFSET:
            raise ValueError(f"Rolling month offset must be 0-{MAX_MONTH_OFFSET} months.")
    elif result["month_offset"] != 0:
        raise ValueError("Month offset is only available in Rolling Month mode.")

    for key, low, high in (
        ("latitude", -MAX_WEB_MERCATOR_LATITUDE, MAX_WEB_MERCATOR_LATITUDE),
        ("longitude", -180.0, 180.0),
    ):
        if type(result[key]) not in (int, float):
            raise ValueError(f"Copernicus {key} must be a number.")
        result[key] = float(result[key])
        if not math.isfinite(result[key]) or not low <= result[key] <= high:
            raise ValueError(f"Copernicus {key} must be between {low:g} and {high:g}.")

    allowed_zooms = map_zooms(layer)
    if type(result["map_zoom"]) is not int or result["map_zoom"] not in allowed_zooms:
        raise ValueError(
            f"Copernicus map zoom for this product must be an integer from "
            f"{allowed_zooms[0]} through {allowed_zooms[-1]}."
        )
    for key in ("map_labels", "map_borders", "auto_brightness", "auto_contrast", "auto_recommendation",
                "auto_precise"):
        if type(result[key]) is not bool:
            raise ValueError(f"Copernicus {key} must be true or false.")
    if result["auto_priority"] not in AUTO_PRIORITIES:
        raise ValueError("Copernicus auto priority must be " + ", ".join(AUTO_PRIORITIES) + ".")
    if result["coverage_mode"] not in COVERAGE_MODES:
        raise ValueError("Copernicus coverage mode is invalid.")
    if "date_granularity" in layer:
        result["coverage_mode"] = "single"
    if type(result["lookback_days"]) is not int or result["lookback_days"] not in LOOKBACK_DAYS:
        raise ValueError(
            "Copernicus maximum lookback must be one of: "
            + ", ".join(str(days) for days in LOOKBACK_DAYS) + " days."
        )
    if (type(result["max_cloud_cover"]) is not int
            or not 0 <= result["max_cloud_cover"] <= 100
            or result["max_cloud_cover"] % 5):
        raise ValueError("Copernicus maximum cloud cover must be 0-100% in 5% steps.")
    if (type(result["brightness"]) is not int or not 25 <= result["brightness"] <= 200
            or result["brightness"] % 5):
        raise ValueError("Copernicus brightness correction must be 25-200% in 5% steps.")
    if type(result["contrast"]) is not int or not 0 <= result["contrast"] <= 200 or result["contrast"] % 5:
        raise ValueError("Copernicus contrast correction must be 0-200% in 5% steps.")
    if result["image_size"] not in ("auto", *IMAGE_SIZES):
        raise ValueError("Copernicus image size must be auto or a supported HD/QHD/4K/8K size.")
    for key in ("no_data_color", "scene_no_data_color"):
        if (not isinstance(result[key], str)
                or (result[key] not in NO_DATA_CHOICES
                    and re.fullmatch(r"#[0-9A-Fa-f]{6}", result[key]) is None)):
            raise ValueError("Copernicus no-data color must be #RRGGBB, transparent, blur or blur_edge.")
    # "transparent" stays valid for selections saved before the checkboxes.
    for key, name in (("map_label_color", "map-label"), ("map_border_color", "country-border")):
        if (not isinstance(result[key], str)
                or (result[key] != "transparent"
                    and re.fullmatch(r"#[0-9A-Fa-f]{6}", result[key]) is None)):
            raise ValueError(f"Copernicus {name} color must be #RRGGBB or transparent.")
    return result


def map_overlay_signature(profile):
    """Overlay choices for image keys: unchanged for a selection that draws the
    country borders like its labels, as before the two could differ.

    A switched-off overlay draws nothing, so its color does not count: the
    legacy black stands in for the labels' color and the labels' signed color
    for the borders'. Changing it never invalidates a cached picture.
    """
    labels_shown = profile["map_labels"]
    label_color = profile.get("map_label_color", LEGACY_MAP_LABEL_COLOR)
    labels = (labels_shown, label_color if labels_shown else LEGACY_MAP_LABEL_COLOR)
    borders_shown = profile.get("map_borders", labels_shown)
    borders = (borders_shown,
               profile.get("map_border_color", label_color) if borders_shown else labels[1])
    return labels if borders == labels else labels + borders


# Evalscripts that return a single RGB or grey output and read dataMask without
# returning it (Landsat Wildfires) deliver missing data as opaque black.
_OUTPUT_WITHOUT_ALPHA = re.compile(r"output\s*:\s*\{\s*bands\s*:\s*[13]\s*\}")
_DATA_MASK_INPUT = re.compile(r"""input\s*:[^}]*["']dataMask["']""", re.S)
_DATA_MASK_ALPHA_WRAPPER = """
// MarbleScape: append dataMask as alpha so missing data stays transparent.
var marblescapeSetup = setup;
var marblescapeEvaluatePixel = evaluatePixel;
setup = function () {
  var config = marblescapeSetup();
  config.output.bands += 1;
  return config;
};
evaluatePixel = function (samples, scenes, inputMetadata, customData, outputMetadata) {
  var value = marblescapeEvaluatePixel(samples, scenes, inputMetadata, customData, outputMetadata);
  return value.concat([samples.dataMask]);
};
"""
# Ways the supported evalscripts put their binary dataMask into the alpha band.
_DATA_MASK_ALPHA_PATTERNS = (
    re.compile(r",\s*\w+\.dataMask\s*\]"),
    re.compile(r"\.(?:push|concat)\(\s*\w+\.dataMask\s*\)"),
    re.compile(r",\s*\w+\.dataMask\s*\)"),
    re.compile(r"dataMask\s*===?\s*0\s*\)\s*return\s*\[\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\]"),
)


def needs_data_mask_alpha(layer):
    script = (layer or {}).get("evalscript", "")
    return bool(_OUTPUT_WITHOUT_ALPHA.search(script) and _DATA_MASK_INPUT.search(script))


def evalscript_with_data_mask_alpha(layer, script):
    return script + _DATA_MASK_ALPHA_WRAPPER if needs_data_mask_alpha(layer) else script


# Tone rule per collection (data type) and layer id, from comparing example
# pictures (docs/IMAGE_SOURCES.md, "Tone rules"). A layer is matched only while
# its evalscript equals the Default theme's, the one the rule was tuned for.
TONE_RULE_LAYERS = {
    ("sentinel-2-l2a", "1_TRUE_COLOR"): "e",
    ("sentinel-2-l2a", "2_TONEMAPPED_NATURAL_COLOR"): "b",
    ("sentinel-2-l1c", "1_TRUE_COLOR"): "b",
    ("sentinel-2-l1c", "2_TONEMAPPED_NATURAL_COLOR"): "b",
    (QUARTERLY_DATA_TYPE, "TRUE_COLOR_CLOUDLESS"): "c",
    (WORLDCOVER_DATA_TYPE, "TRUE_COLOR_CLOUDLESS"): "c",
    ("landsat-ot-l1", "1_TRUE_COLOR"): "f",
    ("landsat-ot-l1", "2_TRUE_COLOR_PANSHARPENED"): "f",
    ("landsat-ot-l1", "3_TONEMAPPED_NATURAL_COLOR"): "a",
    ("sentinel-3-olci", "1_TRUE_COLOR"): "b",
    ("sentinel-3-olci", "1_TRUE_COLOR_ENHANCED"): "b",
    ("sentinel-3-olci", "6_TRUE-COLOR-HIGLIGHT-OPTIMIZED"): "d",
}
_TONE_REFERENCE = {"revision": None, "scripts": {}}


def _tone_reference_scripts():
    revision = catalogue_revision()
    if _TONE_REFERENCE["revision"] != revision:
        scripts = {}
        for product in products("DEFAULT-THEME"):
            for layer in product.get("layers", ()):
                key = (layer.get("data_type"), layer.get("id"))
                if key in TONE_RULE_LAYERS:
                    scripts.setdefault(key, layer.get("evalscript"))
        _TONE_REFERENCE.update(revision=revision, scripts=scripts)
    return _TONE_REFERENCE["scripts"]


def tone_rule(layer):
    """The tone rule letter (a-f) of a layer, or None: no brightness/contrast correction."""
    layer = layer or {}
    key = (layer.get("data_type"), layer.get("id"))
    rule = TONE_RULE_LAYERS.get(key)
    if rule is None:
        return None
    reference = _tone_reference_scripts().get(key)
    return rule if reference is None or layer.get("evalscript") == reference else None


def tone_profile(profile, layer):
    """The tone settings that apply: a layer without a rule renders stock.

    Saved values stay in the profile; they only take effect on a layer with a rule.
    """
    if tone_rule(layer) is not None:
        return profile
    return dict(profile, brightness=100, contrast=100, auto_brightness=False, auto_contrast=False)


# Rule f: instead of the Browser's hard clip at reflectance 0.4, linear up to
# output 0.7 (reflectance 0.28), then an exponential shoulder that reaches
# white only at reflectance 1.0, so bright surfaces arrive unclipped.
_SOFT_HIGHLIGHT_FUNCTION = """
function marblescapeSoft(value) {
  let x = value / maxVal;
  let knee = 0.7, room = 0.3, k = 0.9, top = 1.0 / maxVal;
  if (x <= knee) return Math.max(0, x);
  return Math.min(1, knee + room * (1 - Math.exp(-(x - knee) / k)) / (1 - Math.exp(-(top - knee) / k)));
}
"""
_SOFT_VISUALIZER = "let viz = new DefaultVisualizer(minVal, maxVal);"
_SOFT_RETURN = "return viz.processList(val);"


def soft_highlight_script(script):
    """The evalscript with rule f's soft highlights; unchanged when it is not the expected one."""
    if script.count(_SOFT_VISUALIZER) != 1 or script.count(_SOFT_RETURN) != 1:
        return script
    return script.replace(_SOFT_VISUALIZER, _SOFT_HIGHLIGHT_FUNCTION, 1).replace(
        _SOFT_RETURN,
        "return [marblescapeSoft(val[0]), marblescapeSoft(val[1]), marblescapeSoft(val[2]), val[3]];", 1)


def uses_soft_highlights(profile, layer):
    from marblescape_mosaic_adjustments import TONE_RULES
    rule = tone_rule(layer)
    return bool(rule and TONE_RULES[rule]["soft_script"] and profile.get("auto_contrast", False))


# Sentinel-3 OLCI Enhanced Natural Color passes dataMask through its
# HighlightCompressVisualizer, so valid pixels arrived with alpha 254 and
# counted as gaps. The colours are processed alone and the mask kept exact.
_MASKED_RETURN = "return viz.processList(values);"


def evalscript_with_exact_data_mask(layer, script):
    if ((layer or {}).get("data_type"), (layer or {}).get("id")) != ("sentinel-3-olci", "1_TRUE_COLOR_ENHANCED"):
        return script
    if script.count(_MASKED_RETURN) != 1:
        return script
    return script.replace(_MASKED_RETURN, "let rgb = viz.processList(values.slice(0, 3));\n"
                          "        return [rgb[0], rgb[1], rgb[2], samples.dataMask];", 1)


def needs_exact_data_mask(layer):
    script = (layer or {}).get("evalscript", "")
    return evalscript_with_exact_data_mask(layer, script) != script


def has_data_mask_alpha(layer):
    script = (layer or {}).get("evalscript", "")
    return needs_data_mask_alpha(layer) or any(
        pattern.search(script) for pattern in _DATA_MASK_ALPHA_PATTERNS
    )


def no_data_choice(profile, layer):
    """The No-data color that applies: mosaics and regular layers keep their own."""
    if "date_granularity" in (layer or {}):
        return profile.get("no_data_color", "#FFFFFF")
    if "scene_no_data_color" not in profile and profile.get("coverage_mode") == LEGACY_BLACK_COVERAGE_MODE:
        return "#000000"
    return profile.get("scene_no_data_color", "transparent")


def _opaque(image):
    return image.getchannel("A").getextrema()[0] == 255


def _grow_pixels(image, steps):
    """Copy real pixels into missing neighbours, one ring per step, without blending."""
    current = image
    for _ in range(steps):
        if _opaque(current):
            break
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        grown = Image.new("RGBA", current.size, (0, 0, 0, 0))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            grown.alpha_composite(current, (max(dx, 0), max(dy, 0)), (max(-dx, 0), max(-dy, 0)))
        grown.alpha_composite(current)
        current = grown
    return current


def _nearest_fill(image):
    """Fill gaps from the nearest real pixels; distant gaps use coarser levels."""
    current = _grow_pixels(image, NO_DATA_BLUR_STEPS)
    if _opaque(current) or current.getchannel("A").getextrema()[1] == 0:
        return current
    smaller = current.resize(((current.width + 1) // 2, (current.height + 1) // 2), Image.BOX)
    smaller.putalpha(smaller.getchannel("A").point(lambda value: 255 if value else 0))
    result = _nearest_fill(smaller).resize(current.size, Image.NEAREST)
    result.alpha_composite(current)
    return result


def _smooth_fill(image):
    """Fill gaps with a smooth average of the surrounding image (pull-push).

    Works in floating point: 8-bit colors lose precision where only a few real
    pixels contribute, which shifts the color of large filled areas.
    """
    alpha = image.getchannel("A").convert("F").point(lambda value: value / 255)
    bands = [ImageMath.lambda_eval(lambda args: args["c"] * args["a"],
                                   c=band.convert("F"), a=alpha)
             for band in image.convert("RGB").split()]
    levels = [(alpha, bands)]
    while max(levels[-1][0].size) > 1:
        width, height = levels[-1][0].size
        size = ((width + 1) // 2, (height + 1) // 2)
        levels.append((levels[-1][0].resize(size, Image.BOX),
                       [band.resize(size, Image.BOX) for band in levels[-1][1]]))
    alpha, bands = levels[-1]
    colors = [ImageMath.lambda_eval(lambda args: args["p"] / args["a"], p=band, a=alpha)
              for band in bands]
    for alpha, bands in reversed(levels[:-1]):
        colors = [ImageMath.lambda_eval(lambda args: args["p"] + args["u"] * (1 - args["a"]),
                                        p=band, u=color.resize(alpha.size, Image.BILINEAR), a=alpha)
                  for band, color in zip(bands, colors)]
    result = Image.merge("RGB", [color.convert("L") for color in colors]).convert("RGBA")
    result.alpha_composite(image)
    return result


def fill_no_data_blur(image, edge=False):
    """Blur the nearby image into missing pixels; real pixels stay unchanged.

    Near real pixels the gap takes their colors, lightly blurred; farther away
    it gives way to a smooth fill, so large areas such as open sea stay calm.
    That fill averages the whole image, or with ``edge`` (Edge Blur) only the
    real pixels along the gap's border. An image without any real pixel is
    returned unchanged.
    """
    image = image.convert("RGBA")
    real = image.getchannel("A").point(lambda value: 255 if value == 255 else 0)
    if real.getextrema() != (0, 255):
        return image
    base = image.copy()
    base.putalpha(real)
    near = _nearest_fill(base).filter(ImageFilter.GaussianBlur(NO_DATA_BLUR_RADIUS))
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    if edge:
        reach = max(3, round(max(image.size) * NO_DATA_EDGE_SHARE)) | 1
        gap = ImageChops.invert(real)
        border = image.copy()
        border.putalpha(ImageChops.multiply(real, gap.filter(ImageFilter.MaxFilter(reach))))
        far = _smooth_fill(border)
    else:
        far = _smooth_fill(base)
    weight = real.filter(ImageFilter.GaussianBlur(NO_DATA_BLUR_REACH)).point(
        lambda value: min(255, value * 4))
    result = Image.composite(near, far, weight)
    result.putalpha(255)
    result.alpha_composite(image)
    return result


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob_from_bytes(value: bytes):
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def protect_client_secret(secret: str) -> str:
    """Protect a secret for the current Windows user using DPAPI."""
    if not secret:
        return ""
    if os.name != "nt":
        raise ValueError(
            f"On this operating system set {SECRET_ENVIRONMENT_VARIABLE} instead of saving a secret."
        )
    raw = secret.encode("utf-8")
    input_blob, input_buffer = _blob_from_bytes(raw)
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob), "MarbleScape Copernicus OAuth", None, None, None,
        0x01, ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
    del input_buffer
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_client_secret(value: str) -> str:
    if not value:
        return ""
    if not isinstance(value, str) or not value.startswith("dpapi:"):
        raise ValueError("Copernicus client secrets must be stored as a DPAPI value.")
    if os.name != "nt":
        return ""
    try:
        encrypted = base64.b64decode(value[6:], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("The protected Copernicus client secret is invalid.") from exc
    input_blob, input_buffer = _blob_from_bytes(encrypted)
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0x01, ctypes.byref(output_blob)
    ):
        raise ValueError("The Copernicus client secret cannot be decrypted for this Windows user.")
    try:
        raw = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
    del input_buffer
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("The protected Copernicus client secret is invalid.") from exc


def normalize_auth_configuration(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("[copernicus] must be a table.")
    configured_id = value.get("client_id", "")
    protected = value.get("client_secret_protected", "")
    if not isinstance(configured_id, str) or len(configured_id.strip()) > 500:
        raise ValueError("Copernicus client_id is invalid.")
    if not isinstance(protected, str) or len(protected) > 16384:
        raise ValueError("Copernicus client_secret_protected is invalid.")
    client_id = os.environ.get(CLIENT_ID_ENVIRONMENT_VARIABLE, configured_id).strip()
    environment_secret = os.environ.get(SECRET_ENVIRONMENT_VARIABLE)
    secret = environment_secret if environment_secret is not None else unprotect_client_secret(protected)
    return client_id, secret, protected


def _utc_iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    value = value.astimezone(dt.timezone.utc)
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _parse_catalogue_datetime(value):
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _catalogue_dates(result):
    """Yield dates from both normal STAC and ``distinct: date`` responses."""
    features = result.get("features", []) if isinstance(result, dict) else []
    for feature in features:
        if isinstance(feature, str):
            value = feature
        elif isinstance(feature, dict):
            properties = feature.get("properties", {})
            value = properties.get("datetime") or properties.get("date") \
                if isinstance(properties, dict) else None
        else:
            value = None
        parsed = _parse_catalogue_datetime(value)
        if parsed is not None:
            yield parsed


def _world_pixel_center(latitude, longitude, zoom):
    world_size = TILE_SIZE * (2 ** zoom)
    x = (longitude + 180.0) / 360.0 * world_size
    sine = math.sin(math.radians(latitude))
    y = (0.5 - math.log((1 + sine) / (1 - sine)) / (4 * math.pi)) * world_size
    return x, y, world_size


def _scaled_view_center(profile, width, height):
    cx, cy, world_size = _world_pixel_center(
        profile["latitude"], profile["longitude"], profile["map_zoom"]
    )
    product = get_product(profile["configuration"], profile["product"])
    layer = get_layer(product, profile["layer"])
    if not layer or "low_resolution_data_type" not in layer:
        return cx, cy, world_size
    vertical_room = 2 * min(cy, world_size - cy)
    if vertical_room <= 0:
        raise ValueError("The Copernicus view crosses a Web Mercator pole; move the latitude.")
    scale = max(1.0, width / world_size, height / vertical_room)
    return cx * scale, cy * scale, world_size * scale


def geographic_bbox(profile, width, height):
    """Return the WGS84 bbox of the requested slippy-map view."""
    profile = normalize_profile(profile)
    cx, cy, world_size = _scaled_view_center(profile, width, height)
    top, bottom = cy - height / 2, cy + height / 2
    if top < 0 or bottom > world_size:
        raise ValueError("The Copernicus view crosses a Web Mercator pole; increase map zoom or move the latitude.")
    left, right = cx - width / 2, cx + width / 2
    if right - left > world_size:
        raise ValueError("The Copernicus view is wider than the world; increase map zoom.")

    def lon(px):
        return (px / world_size) * 360.0 - 180.0

    def lat(py):
        n = math.pi - 2 * math.pi * py / world_size
        return math.degrees(math.atan(math.sinh(n)))

    # Catalog searches cannot express a wrapped bbox.  Use the whole longitude
    # range when a view crosses the date line; rendering itself remains wrapped.
    west, east = lon(left), lon(right)
    if west < -180 or east > 180:
        west, east = -180.0, 180.0
    return [west, lat(bottom), east, lat(top)]


def ground_meters_per_pixel(profile, width, height):
    """Ground size of one output pixel at the view centre."""
    _cx, _cy, world_size = _scaled_view_center(profile, width, height)
    return (2 * WEB_MERCATOR_HALF_WORLD / world_size
            * math.cos(math.radians(profile["latitude"])))


def mask_evalscript(collection):
    """Red: data in the view; green: cloudy pixels with data (Precise check)."""
    band, test = CLOUD_MASKS.get(collection, (None, None))
    inputs = '"dataMask"' + (f', "{band}"' if band else "")
    cloud = f"(({test}) ? 255 : 0)" if band else "0"
    return (
        "//VERSION=3\n"
        "function setup() {\n"
        f"  return {{input: [{inputs}], output: {{bands: 3, sampleType: \"UINT8\"}}}};\n"
        "}\n"
        "function evaluatePixel(s) {\n"
        f"  return [s.dataMask * 255, s.dataMask * {cloud}, 0];\n"
        "}\n"
    )


def request_units(width, height, per_tile):
    return max(MINIMUM_REQUEST_UNITS, width * height / (512 * 512) * per_tile)


def supports_cloud_filter(layer):
    """Only collections advertising this Process filter expose cloud control."""
    return "maxCloudCoverage" in layer.get("data_filter", {})


def _collection_for_resolution(layer, meters_per_pixel):
    alternate = layer.get("low_resolution_data_type")
    threshold = layer.get("low_resolution_threshold_m", 320)
    if alternate and meters_per_pixel > threshold:
        return alternate
    return layer["data_type"]


def _catalog_filter(layer, max_cloud_cover=None):
    values = dict(layer.get("data_filter", {}))
    if max_cloud_cover is not None and supports_cloud_filter(layer):
        values["maxCloudCoverage"] = max_cloud_cover
    clauses = []
    mapping = {
        "acquisitionMode": "sar:instrument_mode",
        "polarization": "s1:polarization",
        "resolution": "s1:resolution",
        "orbitDirection": "sat:orbit_state",
        "timeliness": "s1:timeliness",
    }
    for key, property_name in mapping.items():
        if key in values:
            item = str(values[key]).replace("'", "''")
            if key == "orbitDirection":
                item = item.lower()
            clauses.append(f"{property_name}='{item}'")
    if "maxCloudCoverage" in values:
        # Landsat night passes report an unknown cloud cover of -1, which would
        # otherwise satisfy every limit and make a black frame the "latest" one.
        clauses.append("eo:cloud_cover>=0")
        clauses.append(f"eo:cloud_cover<={float(values['maxCloudCoverage']):g}")
    return " and ".join(clauses)


def _read_varint(data, position):
    value = 0
    shift = 0
    while position < len(data) and shift <= 63:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7
    raise ValueError("Invalid vector-tile integer.")


def _protobuf_fields(data):
    position = 0
    while position < len(data):
        tag, position = _read_varint(data, position)
        number, wire_type = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid vector-tile field.")
        if wire_type == 0:
            value, position = _read_varint(data, position)
        elif wire_type == 1:
            if position + 8 > len(data):
                raise ValueError("Truncated vector tile.")
            value, position = data[position:position + 8], position + 8
        elif wire_type == 2:
            length, position = _read_varint(data, position)
            if length > len(data) - position:
                raise ValueError("Truncated vector tile.")
            value, position = data[position:position + length], position + length
        elif wire_type == 5:
            if position + 4 > len(data):
                raise ValueError("Truncated vector tile.")
            value, position = data[position:position + 4], position + 4
        else:
            raise ValueError("Unsupported vector-tile field type.")
        yield number, wire_type, value


def _packed_varints(data):
    position = 0
    while position < len(data):
        value, position = _read_varint(data, position)
        yield value


def _decode_geometry(data):
    values = list(_packed_varints(data))
    position = 0
    x = y = 0
    paths = []
    current = None
    while position < len(values):
        command = values[position]
        position += 1
        command_id, count = command & 7, command >> 3
        if count <= 0 or command_id not in {1, 2, 7}:
            raise ValueError("Invalid vector-tile geometry command.")
        if command_id in {1, 2}:
            if position + count * 2 > len(values):
                raise ValueError("Truncated vector-tile geometry.")
            for _ in range(count):
                dx, dy = values[position], values[position + 1]
                position += 2
                x += (dx >> 1) ^ -(dx & 1)
                y += (dy >> 1) ^ -(dy & 1)
                if command_id == 1:
                    current = [(x, y)]
                    paths.append(current)
                elif current is not None:
                    current.append((x, y))
        elif current and len(current) > 1:
            current.append(current[0])
    return [path for path in paths if len(path) > 1]


def _decode_vector_tile_lines(data):
    """Return (extent, paths) for all line features in a Mapbox vector tile."""
    result = []
    for number, wire_type, layer_data in _protobuf_fields(data):
        if number != 3 or wire_type != 2:
            continue
        extent = 4096
        features = []
        for field, layer_wire, value in _protobuf_fields(layer_data):
            if field == 2 and layer_wire == 2:
                features.append(value)
            elif field == 5 and layer_wire == 0:
                extent = value
        if not 1 <= extent <= 1_000_000:
            raise ValueError("Invalid vector-tile extent.")
        for feature in features:
            geometry_type = 0
            geometry = None
            for field, feature_wire, value in _protobuf_fields(feature):
                if field == 3 and feature_wire == 0:
                    geometry_type = value
                elif field == 4 and feature_wire == 2:
                    geometry = value
            if geometry_type == 2 and geometry is not None:
                result.extend((extent, path) for path in _decode_geometry(geometry))
    return result


class CopernicusClient:
    def __init__(self, client_id="", client_secret="", timeout=90,
                 user_agent="MarbleScape", opener=None, network_attempts=NETWORK_ATTEMPTS):
        self.client_id = str(client_id).strip()
        self.client_secret = str(client_secret)
        self.timeout = float(timeout)
        self.user_agent = str(user_agent)
        self._opener = opener or _verified_urlopen
        if type(network_attempts) is not int or not 1 <= network_attempts <= 10:
            raise ValueError("Copernicus network attempts must be between 1 and 10.")
        self.network_attempts = network_attempts
        self._token = ""
        self._token_deadline = 0.0
        self._token_lock = threading.Lock()
        self._tile_cache = {}
        self._tile_cache_order = []
        self._tile_cache_lock = threading.Lock()
        self.last_render_warnings = []

    @property
    def credentials_available(self):
        return bool(self.client_id and self.client_secret)

    def _read_response(self, response, maximum, track=False):
        try:
            data = read_response(response, maximum, track=track)
        except ResponseTooLargeError:
            raise RuntimeError("Copernicus response exceeds the supported size.")
        return data

    @staticmethod
    def _http_error(exc, service="Copernicus service"):
        try:
            body = exc.read(65537)[:65536]
            value = json.loads(body.decode("utf-8", "replace"))
            detail = value.get("error_description") or value.get("message") \
                or value.get("description") or value.get("error")
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("description") or detail.get("reason")
            if isinstance(detail, list):
                detail = "; ".join(str(item) for item in detail[:3])
        except Exception:
            detail = None
        suffix = f": {detail}" if detail else ""
        if service == "Copernicus service" and exc.code == 401:
            return RuntimeError("Copernicus authentication failed; check Client ID and Client secret" + suffix)
        if service == "Copernicus service" and exc.code == 429:
            return RuntimeError("Copernicus rate limit reached; try again later" + suffix)
        return RuntimeError(f"{service} returned HTTP {exc.code}{suffix}")

    def _open(self, request, maximum, track=False):
        request_url = getattr(request, "full_url", "")
        service = (
            "GISCO map service"
            if urllib.parse.urlparse(request_url).hostname == "gisco-services.ec.europa.eu"
            else "Copernicus service"
        )
        for attempt in range(self.network_attempts):
            NETWORK_ACTIVITY.check()
            if track:
                DOWNLOAD_PROGRESS.raise_if_cancelled()
            try:
                with open_response(self._opener, request, timeout=self.timeout) as response:
                    return self._read_response(response, maximum, track=track), response.headers.get("Content-Type", "")
            except urllib.error.HTTPError as exc:
                retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
                if retryable and attempt + 1 < self.network_attempts:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = min(10.0, max(0.0, float(retry_after)))
                    except (TypeError, ValueError):
                        delay = 0.5 * (2 ** attempt)
                    exc.close()
                    if track:
                        DOWNLOAD_PROGRESS.wait_or_raise(delay)
                    else:
                        NETWORK_ACTIVITY.wait(delay)
                    continue
                error = self._http_error(exc, service)
                exc.close()
                raise error from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                    http.client.HTTPException) as exc:
                if attempt + 1 < self.network_attempts:
                    delay = 0.5 * (2 ** attempt)
                    if track:
                        DOWNLOAD_PROGRESS.wait_or_raise(delay)
                    else:
                        NETWORK_ACTIVITY.wait(delay)
                    continue
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                raise RuntimeError(
                    f"{service} network request failed after {self.network_attempts} attempts: {reason}"
                ) from exc

    def access_token(self):
        if not self.credentials_available:
            raise ValueError(
                "Copernicus requires a free Sentinel Hub OAuth Client ID and Client secret. "
                "Create them under User Settings > OAuth clients at "
                "shapps.dataspace.copernicus.eu and enter them in Settings > Image."
            )
        with self._token_lock:
            if self._token and time.monotonic() < self._token_deadline:
                return self._token
            payload = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }).encode("utf-8")
            request = urllib.request.Request(
                TOKEN_URL, data=payload, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": self.user_agent},
            )
            raw, _ = self._open(request, MAX_CATALOGUE_RESPONSE_BYTES)
            try:
                value = json.loads(raw.decode("utf-8"))
                token = value["access_token"]
                expires = float(value.get("expires_in", 600))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Copernicus authentication returned an invalid response.") from exc
            if not isinstance(token, str) or not token:
                raise RuntimeError("Copernicus authentication returned no access token.")
            self._token = token
            self._token_deadline = time.monotonic() + max(30.0, expires - 60.0)
            return token

    def account_usage(self):
        """Read the current account's monthly limits and consumption."""
        token = self.access_token()
        request = urllib.request.Request(
            ACCOUNT_USAGE_URL,
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/json", "User-Agent": self.user_agent},
        )
        raw, _ = self._open(request, MAX_CATALOGUE_RESPONSE_BYTES)
        try:
            value = json.loads(raw.decode("utf-8"))
            claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, IndexError,
                binascii.Error) as exc:
            raise RuntimeError("Copernicus returned invalid account usage data.") from exc
        if not isinstance(value, dict) or not isinstance(claims, dict):
            raise RuntimeError("Copernicus returned invalid account usage data.")
        realm_access = claims.get("realm_access")
        roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
        if not isinstance(roles, list):
            roles = []
        role = next((item for item in roles if isinstance(item, str)
                     and item.endswith("-quota")), "Unavailable")
        result = {"role": role}
        for key in ("processingUnitsMonthly", "requestsMonthly"):
            entry = value.get(key)
            if not isinstance(entry, dict):
                raise RuntimeError("Copernicus returned incomplete account usage data.")
            result[key] = {}
            for field in ("configuration", "consumed", "remaining"):
                item = entry.get(field)
                if not isinstance(item, (str, int, float)) or isinstance(item, bool):
                    raise RuntimeError("Copernicus returned incomplete account usage data.")
                result[key][field] = str(item)
        return result

    def _json_request(self, url, payload):
        request = urllib.request.Request(
            url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + self.access_token(),
                     "Content-Type": "application/json", "Accept": "application/geo+json",
                     "User-Agent": self.user_agent},
        )
        raw, _ = self._open(request, MAX_CATALOGUE_RESPONSE_BYTES)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Copernicus catalogue returned invalid JSON.") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Copernicus catalogue returned an unexpected response.")
        return value

    def _selection(self, profile):
        profile = normalize_profile(profile)
        product = get_product(profile["configuration"], profile["product"])
        layer = get_layer(product, profile["layer"])
        return profile, product, layer

    def _catalog_payload(self, profile, product, layer, width, height, start, end, limit=100):
        # Validate that the complete requested view fits Web Mercator.  Date
        # discovery itself uses the requested centre point, matching the custom
        # latitude/longitude selection even for views crossing the date line.
        geographic_bbox(profile, width, height)
        _cx, _cy, world_size = _scaled_view_center(profile, width, height)
        meters_per_pixel = 2 * WEB_MERCATOR_HALF_WORLD / world_size
        payload = {
            "intersects": {"type": "Point", "coordinates": [
                profile["longitude"], profile["latitude"]
            ]},
            "datetime": f"{start}/{end}",
            "collections": [_collection_for_resolution(layer, meters_per_pixel)],
            "limit": int(limit),
        }
        query_filter = _catalog_filter(layer, profile["max_cloud_cover"])
        if query_filter:
            payload["filter"] = query_filter
        return payload

    def latest(self, profile, output_size, *, reference_date=None):
        profile, product, layer = self._selection(profile)
        width, height = map(int, output_size)
        selected_date = profile["date"]
        acquisition = None
        if profile["date_mode"] in {"relative_quarter", "relative_month"}:
            quarterly = profile["date_mode"] == "relative_quarter"
            target = (rolling_quarter_start(reference_date or dt.date.today(), profile["quarter_offset"])
                      if quarterly else rolling_month_start(reference_date or dt.date.today(), profile["month_offset"]))
            step = 3 if quarterly else 1
            next_quarter = (target.replace(year=target.year + 1, month=1)
                            if target.month + step > 12 else target.replace(month=target.month + step))
            start = dt.datetime.combine(target, dt.time.min, dt.timezone.utc)
            end = dt.datetime.combine(next_quarter, dt.time.min, dt.timezone.utc)
            payload = self._catalog_payload(
                profile, product, layer, width, height,
                _utc_iso(start), _utc_iso(end - dt.timedelta(seconds=1)), limit=100,
            )
            payload["distinct"] = "date"
            result = self._json_request(CATALOG_URL, payload)
            matching = [value for value in _catalogue_dates(result)
                        if value.year == target.year
                        and ((value.month - 1) // 3 == (target.month - 1) // 3
                             if quarterly else value.month == target.month)]
            if not matching:
                label = (f"Quarter target {target.year} Q{(target.month - 1) // 3 + 1}"
                         if quarterly else f"Month target {target:%Y-%m}")
                raise RuntimeError(
                    f"Rolling {label} is not yet available for this "
                    "location and product. No other period was substituted."
                )
            acquisition = max(matching)
            selected_date = acquisition.date().isoformat()
        elif selected_date == "latest" and layer["data_type"] != "dem":
            now = dt.datetime.now(dt.timezone.utc)
            collection_start = dt.datetime.fromisoformat(layer["start_date"]).replace(tzinfo=dt.timezone.utc)
            for days in (45, 366, 3650, None):
                start = collection_start if days is None else max(collection_start, now - dt.timedelta(days=days))
                payload = self._catalog_payload(
                    profile, product, layer, width, height, _utc_iso(start), _utc_iso(now), limit=100
                )
                # One catalogue item per acquisition date keeps dense Sentinel
                # collections within the page limit while still allowing us to
                # choose the newest available date deterministically.
                payload["distinct"] = "date"
                result = self._json_request(CATALOG_URL, payload)
                candidates = list(_catalogue_dates(result))
                if candidates:
                    acquisition = max(candidates)
                    break
            if acquisition is None:
                cloud_limit = (
                    f" with at most {profile['max_cloud_cover']}% cloud cover per satellite tile"
                    if supports_cloud_filter(layer) else ""
                )
                advice = (
                    " Try a higher cloud limit or another location."
                    if supports_cloud_filter(layer) else
                    " DH monthly mosaics cover mainly polar regions; use IW for non-polar land."
                    if product["id"] == "MARBLESCAPE::S1-DH-MONTHLY" else
                    " Try another area or available date."
                )
                raise RuntimeError(
                    "No Copernicus acquisition is available for this location and product"
                    + cloud_limit + "." + advice
                )
            selected_date = acquisition.date().isoformat()
            # ``distinct: date`` deliberately returns day strings. Resolve the
            # newest feature time inside that day for an accurate latest-frame
            # signature while the Process API continues to mosaic the full day.
            day_start = dt.datetime.fromisoformat(selected_date).replace(tzinfo=dt.timezone.utc)
            payload = self._catalog_payload(
                profile, product, layer, width, height, _utc_iso(day_start),
                _utc_iso(day_start + dt.timedelta(days=1) - dt.timedelta(seconds=1)),
                limit=100,
            )
            exact = self._json_request(CATALOG_URL, payload)
            exact_times = list(_catalogue_dates(exact))
            if exact_times:
                acquisition = max(exact_times)
        elif selected_date != "latest":
            acquisition = dt.datetime.fromisoformat(selected_date).replace(tzinfo=dt.timezone.utc)

        return {
            "profile": profile,
            "product": product,
            "layer": layer,
            "width": width,
            "height": height,
            "date": selected_date,
            "timestamp": _utc_iso(acquisition) if acquisition else "timeless",
            "latest": profile["date_mode"] == "catalogue" and profile["date"] == "latest",
        }

    def list_dates(self, profile, output_size, maximum=MAX_CATALOGUE_DATES, since=None):
        profile, product, layer = self._selection(profile)
        if layer["data_type"] == "dem":
            return []
        maximum = max(1, min(MAX_CATALOGUE_DATES, int(maximum)))
        width, height = map(int, output_size)
        start = dt.datetime.fromisoformat(layer["start_date"]).replace(tzinfo=dt.timezone.utc)
        if since is not None:
            recent = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc)
            start = max(start, recent - dt.timedelta(days=14))
        end = dt.datetime.now(dt.timezone.utc)
        payload = self._catalog_payload(
            profile, product, layer, width, height, _utc_iso(start), _utc_iso(end), limit=100
        )
        payload["distinct"] = "date"
        dates = set()
        seen_pages = set()
        while len(dates) < maximum:
            result = self._json_request(CATALOG_URL, payload)
            for parsed in _catalogue_dates(result):
                dates.add(parsed.date().isoformat())
            next_value = result.get("context", {}).get("next")
            if next_value is None:
                break
            page_key = json.dumps(next_value, sort_keys=True, separators=(",", ":"))
            if page_key in seen_pages:
                raise RuntimeError("Copernicus catalogue returned a repeated page token.")
            seen_pages.add(page_key)
            payload["next"] = next_value
        return sorted(dates, reverse=True)[:maximum]

    def search_view(self, profile, output_size, start, end, progress=None, maximum_pages=30):
        """Catalogue items over the whole view between ``start`` and ``end``.

        Unlike the date search (the view's centre, one entry per day) this
        returns every satellite tile with its footprint and cloud estimate, for
        Recommend. Catalogue requests use no processing units. ``progress(page,
        pages or None)`` reports each page. Returns (features, normalized layer).
        """
        profile, product, layer = self._selection(profile)
        width, height = map(int, output_size)
        west, south, east, north = geographic_bbox(profile, width, height)
        payload = self._catalog_payload(
            profile, product, layer, width, height, _utc_iso(start), _utc_iso(end), limit=100)
        payload["intersects"] = {"type": "Polygon", "coordinates": [[
            [west, south], [east, south], [east, north], [west, north], [west, south]]]}
        # Every cloud estimate (the variants filter locally); night passes (-1) stay out.
        query_filter = _catalog_filter(layer, 100 if supports_cloud_filter(layer) else None)
        if query_filter:
            payload["filter"] = query_filter
        else:
            payload.pop("filter", None)
        features, seen_pages = [], set()
        for page in range(1, int(maximum_pages) + 1):
            NETWORK_ACTIVITY.check()
            result = self._json_request(CATALOG_URL, payload)
            features.extend(item for item in result.get("features", []) if isinstance(item, dict))
            context = result.get("context") if isinstance(result.get("context"), dict) else {}
            matched = context.get("matched")
            if callable(progress):
                progress(page, math.ceil(matched / 100)
                         if type(matched) is int and matched > 0 else None)
            next_value = context.get("next")
            if next_value is None:
                break
            page_key = json.dumps(next_value, sort_keys=True, separators=(",", ":"))
            if page_key in seen_pages:
                raise RuntimeError("Copernicus catalogue returned a repeated page token.")
            seen_pages.add(page_key)
            payload["next"] = next_value
        return features, layer

    @staticmethod
    def _view_pixels(frame):
        profile, width, height = frame["profile"], frame["width"], frame["height"]
        # A preview draws the view of the full output size smaller (view_scale < 1).
        scale = frame.get("view_scale", 1.0)
        if scale == 1.0:
            cx, cy, world_size = _scaled_view_center(profile, width, height)
        else:
            cx, cy, world_size = (value * scale for value in
                                  _scaled_view_center(profile, width / scale, height / scale))
        left, top = cx - width / 2, cy - height / 2
        if top < 0 or top + height > world_size or width > world_size:
            # raises the precise validation message
            geographic_bbox(profile, round(width / scale), round(height / scale))
        return left, top, world_size

    @staticmethod
    def _mercator_bbox(left, top, world_size, x, y, width, height):
        x1_world = left + x
        wrap = math.floor(x1_world / world_size)
        x1 = x1_world - wrap * world_size
        # A pixel can straddle the date line when the view starts at a
        # fractional world-pixel coordinate. Keep each Process request inside
        # EPSG:3857 and let that one transition pixel use the nearer side.
        x2 = min(world_size, x1 + width)
        scale = 2 * WEB_MERCATOR_HALF_WORLD / world_size
        xmin = -WEB_MERCATOR_HALF_WORLD + x1 * scale
        xmax = -WEB_MERCATOR_HALF_WORLD + x2 * scale
        ymax = WEB_MERCATOR_HALF_WORLD - (top + y) * scale
        ymin = WEB_MERCATOR_HALF_WORLD - (top + y + height) * scale
        return [xmin, ymin, xmax, ymax]

    @staticmethod
    def _horizontal_parts(left, width, world_size):
        x = 0
        while x < width:
            absolute = left + x
            boundary = (math.floor(absolute / world_size) + 1) * world_size
            until_wrap = max(1, int(math.floor(boundary - absolute + 1e-9)))
            part = min(PROCESS_TILE_LIMIT, width - x, until_wrap)
            yield x, part
            x += part

    def _process_parts(self, frame):
        """Yield Process API tiles, each within the documented 2500 px limit."""
        width, height = frame["width"], frame["height"]
        left, top, world_size = self._view_pixels(frame)
        for x, part_width in self._horizontal_parts(left, width, world_size):
            y = 0
            while y < height:
                part_height = min(PROCESS_TILE_LIMIT, height - y)
                bbox = self._mercator_bbox(
                    left, top, world_size, x, y, part_width, part_height
                )
                yield x, y, part_width, part_height, bbox
                y += part_height

    @staticmethod
    def _evalscript(frame, layer):
        """The layer's evalscript with the tone settings that apply (see tone_profile)."""
        tones = tone_profile(frame["profile"], layer)
        script = evalscript_for_brightness(
            layer, evalscript_brightness(tones, "date_granularity" in (layer or {})))
        if uses_soft_highlights(tones, layer):
            script = soft_highlight_script(script)
        script = evalscript_with_exact_data_mask(layer, script)
        return evalscript_with_data_mask_alpha(layer, script)

    def _process_tile(self, frame, bbox, width, height, evalscript=None):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= PROCESS_TILE_LIMIT
                or not 1 <= height <= PROCESS_TILE_LIMIT):
            raise ValueError(
                f"Each Copernicus Process request must be between 1 and "
                f"{PROCESS_TILE_LIMIT} pixels per side."
            )
        layer = frame["layer"]
        data_filter = dict(layer.get("data_filter", {}))
        if supports_cloud_filter(layer):
            data_filter["maxCloudCoverage"] = frame["profile"]["max_cloud_cover"]
        processing = dict(layer.get("processing", {}))
        if layer["data_type"] != "dem":
            selected_date = frame["date"]
            start = dt.datetime.fromisoformat(selected_date).replace(tzinfo=dt.timezone.utc)
            if (frame["profile"]["coverage_mode"] == "fill_gaps"
                    and "date_granularity" not in layer):
                start -= dt.timedelta(days=frame["profile"]["lookback_days"] - 1)
                data_filter["mosaickingOrder"] = "mostRecent"
            data_filter["timeRange"] = {
                "from": _utc_iso(start),
                "to": _utc_iso(
                    dt.datetime.fromisoformat(selected_date).replace(tzinfo=dt.timezone.utc)
                    + dt.timedelta(days=1) - dt.timedelta(seconds=1)
                ),
            }
            data_filter.setdefault("mosaickingOrder", "mostRecent")
        meters_per_pixel = (bbox[2] - bbox[0]) / width
        # Small requests (previews, masks) keep the collection of the full output.
        data = {"type": frame.get("data_type") or _collection_for_resolution(layer, meters_per_pixel)}
        if data_filter:
            data["dataFilter"] = data_filter
        if processing:
            data["processing"] = processing
        payload = {
            "input": {"bounds": {"bbox": bbox, "properties": {
                "crs": "http://www.opengis.net/def/crs/EPSG/0/3857"
            }}, "data": [data]},
            "output": {"width": width, "height": height, "responses": [{
                "identifier": "default", "format": {"type": "image/png"}
            }]},
            "evalscript": evalscript or self._evalscript(frame, layer),
        }
        request = urllib.request.Request(
            PROCESS_URL, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + self.access_token(),
                     "Content-Type": "application/json", "Accept": "image/png",
                     "User-Agent": self.user_agent},
        )
        raw, content_type = self._open(request, MAX_RESPONSE_BYTES, track=True)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(
                "Copernicus Process API did not return a PNG image"
                + (f" ({content_type})" if content_type else "") + "."
            )
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                if image.size != (width, height):
                    raise RuntimeError("Copernicus Process API returned an unexpected image size.")
                if "A" not in image.getbands():
                    frame["_coverage_mask_valid"] = False
                return image.convert("RGBA"), len(raw)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Copernicus Process API returned an invalid PNG image.") from exc

    def _refine_process_gaps(self, frame, image, origin_x, origin_y, view_pixels):
        left, top, world_size = view_pixels
        alpha = image.getchannel("A")
        downloaded = 0
        for y in range(0, image.height, GAP_REFINEMENT_TILE_SIZE):
            for x in range(0, image.width, GAP_REFINEMENT_TILE_SIZE):
                DOWNLOAD_PROGRESS.raise_if_cancelled()
                width = min(GAP_REFINEMENT_TILE_SIZE, image.width - x)
                height = min(GAP_REFINEMENT_TILE_SIZE, image.height - y)
                box = (x, y, x + width, y + height)
                if alpha.crop(box).getextrema()[0] == 255:
                    continue
                bbox = self._mercator_bbox(
                    left, top, world_size, origin_x + x, origin_y + y, width, height
                )
                fill, size = self._process_tile(frame, bbox, width, height)
                downloaded += size
                original = image.crop(box)
                image.paste(Image.alpha_composite(fill, original), (x, y))
        return downloaded

    def _render_satellite(self, frame):
        width, height = frame["width"], frame["height"]
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        downloaded = 0
        view_pixels = None
        for x, y, part_width, part_height, bbox in self._process_parts(frame):
            DOWNLOAD_PROGRESS.raise_if_cancelled()
            image, size = self._process_tile(frame, bbox, part_width, part_height)
            if (frame["profile"]["coverage_mode"] == "fill_gaps"
                    and (part_width > GAP_REFINEMENT_TILE_SIZE
                         or part_height > GAP_REFINEMENT_TILE_SIZE)
                    and image.getchannel("A").getextrema()[0] < 255):
                if view_pixels is None:
                    view_pixels = self._view_pixels(frame)
                size += self._refine_process_gaps(frame, image, x, y, view_pixels)
            canvas.alpha_composite(image, (x, y))
            downloaded += size
        if (frame["layer"].get("date_granularity")
                and canvas.getchannel("A").getextrema()[1] == 0):
            if frame["product"]["id"] == "MARBLESCAPE::S1-DH-MONTHLY":
                raise RuntimeError(
                    "No Sentinel-1 DH mosaic imagery exists for this area and month. "
                    "DH covers mainly polar regions; use Sentinel-1 IW for non-polar land."
                )
            raise RuntimeError("No mosaic imagery exists for this area and selected period.")
        return canvas, downloaded

    @staticmethod
    def _overzoom_tile(image, zoom, x, y):
        """Scale the matching part of a max-native-zoom GISCO tile."""
        if zoom <= GISCO_MAX_NATIVE_ZOOM:
            return image
        factor = 2 ** (zoom - GISCO_MAX_NATIVE_ZOOM)
        child_x, child_y = x % factor, y % factor
        left = child_x * TILE_SIZE / factor
        top = child_y * TILE_SIZE / factor
        right = (child_x + 1) * TILE_SIZE / factor
        bottom = (child_y + 1) * TILE_SIZE / factor
        return image.crop((left, top, right, bottom)).resize(
            (TILE_SIZE, TILE_SIZE), Image.Resampling.BILINEAR
        )

    def _cached_map_tile(self, template, zoom, x, y):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        key = (template, zoom, x, y)
        cached = self._load_cached_tile(key)
        if cached is not None:
            return cached, 0
        source_zoom = min(zoom, GISCO_MAX_NATIVE_ZOOM)
        factor = 2 ** (zoom - source_zoom)
        source_x, source_y = x // factor, y // factor
        source_key = (template, source_zoom, source_x, source_y)
        source_value = self._load_cached_tile(source_key)
        downloaded = 0
        if source_value is None:
            request = urllib.request.Request(
                template.format(z=source_zoom, x=source_x, y=source_y),
                headers={"User-Agent": self.user_agent},
            )
            raw, _ = self._open(request, 4 * 1024 * 1024, track=True)
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    if image.size != (TILE_SIZE, TILE_SIZE):
                        raise RuntimeError("GISCO map service returned an unexpected tile size.")
                    source_value = image.convert("RGBA")
            except (OSError, ValueError) as exc:
                raise RuntimeError("GISCO map service returned an invalid map tile.") from exc
            downloaded = len(raw)
            self._store_tile(source_key, source_value)
        value = self._overzoom_tile(source_value, zoom, x, y)
        self._store_tile(key, value)
        return value, downloaded

    def _load_cached_tile(self, key):
        with self._tile_cache_lock:
            encoded = self._tile_cache.get(key)
        if encoded is None:
            return None
        with Image.open(io.BytesIO(encoded)) as image:
            return image.convert("RGBA")

    def _store_tile(self, key, value):
        with self._tile_cache_lock:
            if key in self._tile_cache:
                return
        buffer = io.BytesIO()
        value.save(buffer, format="PNG")
        encoded = buffer.getvalue()
        with self._tile_cache_lock:
            if key not in self._tile_cache:
                self._tile_cache[key] = encoded
                self._tile_cache_order.append(key)
                while len(self._tile_cache_order) > TILE_CACHE_LIMIT:
                    old = self._tile_cache_order.pop(0)
                    self._tile_cache.pop(old, None)

    def _cached_border_tile(self, template, zoom, x, y):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        key = (template, zoom, x, y)
        cached = self._load_cached_tile(key)
        if cached is not None:
            return cached, 0
        source_zoom = min(zoom, GISCO_MAX_NATIVE_ZOOM)
        factor = 2 ** (zoom - source_zoom)
        source_x, source_y = x // factor, y // factor
        source_key = (template, source_zoom, source_x, source_y)
        source_value = self._load_cached_tile(source_key)
        downloaded = 0
        if source_value is None:
            request = urllib.request.Request(
                template.format(z=source_zoom, x=source_x, y=source_y),
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"},
            )
            raw, _ = self._open(request, 4 * 1024 * 1024, track=True)
            try:
                paths = _decode_vector_tile_lines(raw)
            except ValueError as exc:
                raise RuntimeError("GISCO map service returned an invalid boundary tile.") from exc
            source_value = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
            draw = ImageDraw.Draw(source_value, "RGBA")
            line_width = max(1, round(0.5 + max(0, min(source_zoom, 12) - 2) * 0.21))
            for extent, path in paths:
                points = [(point[0] * TILE_SIZE / extent, point[1] * TILE_SIZE / extent)
                          for point in path]
                draw.line(points, fill=(0, 0, 0, 128), width=line_width, joint="curve")
            downloaded = len(raw)
            self._store_tile(source_key, source_value)
        value = self._overzoom_tile(source_value, zoom, x, y)
        self._store_tile(key, value)
        return value, downloaded

    def _map_overlay(self, frame, template):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        width, height = frame["width"], frame["height"]
        left, top, world_size = self._view_pixels(frame)
        zoom = frame["profile"]["map_zoom"]
        count = 2 ** zoom
        display_tile_size = world_size / count
        first_x, last_x = (math.floor(left / display_tile_size),
                           math.floor((left + width - 1) / display_tile_size))
        first_y, last_y = (math.floor(top / display_tile_size),
                           math.floor((top + height - 1) / display_tile_size))
        coordinates = [(x, y) for y in range(first_y, last_y + 1)
                       for x in range(first_x, last_x + 1) if 0 <= y < count]
        fetched = {}
        downloaded = 0
        loader = self._cached_border_tile if template == GISCO_BORDERS_URL else self._cached_map_tile
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(coordinates)))) as pool:
            futures = {pool.submit(loader, template, zoom, x % count, y): (x, y)
                       for x, y in coordinates}
            for future in as_completed(futures):
                DOWNLOAD_PROGRESS.raise_if_cancelled()
                x, y = futures[future]
                image, size = future.result()
                fetched[(x, y)] = image
                downloaded += size
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for x, y in coordinates:
            px = round(x * display_tile_size - left)
            py = round(y * display_tile_size - top)
            tile_width = round((x + 1) * display_tile_size - left) - px
            tile_height = round((y + 1) * display_tile_size - top) - py
            tile = fetched[(x, y)]
            if tile.size != (tile_width, tile_height):
                tile = tile.resize((tile_width, tile_height), Image.Resampling.BILINEAR)
            canvas.alpha_composite(tile, (px, py))
        return canvas, downloaded

    @staticmethod
    def _draw_attribution(image):
        text = "© OpenStreetMap contributors · Tiles: © European Union, GISCO"
        draw = ImageDraw.Draw(image, "RGBA")
        font = ImageFont.load_default()
        box = draw.textbbox((0, 0), text, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        x, y = max(3, image.width - width - 8), max(3, image.height - height - 6)
        draw.rectangle((x - 3, y - 2, x + width + 3, y + height + 2), fill=(255, 255, 255, 176))
        draw.text((x, y), text, fill=(0, 0, 0, 230), font=font)

    def small_frame(self, profile, output_size, date, box):
        """A frame of the full output's view, drawn within ``box`` or finer where the
        collection's pixel limit needs it."""
        profile, product, layer = self._selection(profile)
        width, height = map(int, output_size)
        scale = min(box[0] / width, box[1] / height, 1.0)
        # The small request keeps the full output's collection: a mosaic's fine
        # collection holds data only near land, its coarse one also over the sea.
        _cx, _cy, world_size = _scaled_view_center(profile, width, height)
        collection = _collection_for_resolution(layer, 2 * WEB_MERCATOR_HALF_WORLD / world_size)
        if collection == layer["data_type"]:
            limit = PIXEL_LIMITS.get(collection, DEFAULT_PIXEL_LIMIT) * PIXEL_LIMIT_MARGIN
            scale = max(scale, min(1.0, ground_meters_per_pixel(profile, width, height) / limit))
        return {"profile": profile, "product": product, "layer": layer, "data_type": collection,
                "width": max(1, round(width * scale)), "height": max(1, round(height * scale)),
                "date": date, "view_scale": scale, "timestamp": "preview", "latest": False}

    def small_units(self, profile, output_size):
        """Estimated processing units of one preview and one Precise check mask."""
        preview = self.small_frame(profile, output_size, None, PREVIEW_BOX)
        mask = self.small_frame(profile, output_size, None, MASK_BOX)
        return (request_units(preview["width"], preview["height"], PREVIEW_UNITS_PER_TILE),
                request_units(mask["width"], mask["height"], MASK_UNITS_PER_TILE))

    def _small_request(self, profile, output_size, date, box, run):
        frame = self.small_frame(profile, output_size, date, box)
        try:
            return run(frame)
        except RuntimeError as exc:
            match = _PIXEL_LIMIT_REFUSAL.search(str(exc))
            if match is None or frame["data_type"] in PIXEL_LIMITS:
                raise
            PIXEL_LIMITS[frame["data_type"]] = float(match.group(1))
            return run(self.small_frame(profile, output_size, date, box))

    def render_preview(self, profile, output_size, date, box=PREVIEW_BOX):
        """The view on ``date`` as the wallpaper renders it, as an RGB image within ``box``."""
        def run(frame):
            data, _size = self.fetch_image(frame)
            with Image.open(io.BytesIO(data)) as image:
                picture = image.convert("RGB")
            picture.thumbnail(box)
            return picture

        return self._small_request(profile, output_size, date, box, run)

    def view_mask(self, profile, output_size, date, box=MASK_BOX):
        """(coverage, clouds) in percent of the view, mosaicked as the wallpaper on
        ``date``; clouds is None where the collection has no cloud mask."""
        def run(frame):
            covered = cloudy = 0
            cloud_mask = False
            for _x, _y, width, height, bbox in self._process_parts(frame):
                DOWNLOAD_PROGRESS.raise_if_cancelled()
                collection = frame["data_type"]
                cloud_mask = collection in CLOUD_MASKS
                image, _size = self._process_tile(frame, bbox, width, height,
                                                  evalscript=mask_evalscript(collection))
                covered += sum(image.getchannel("R").histogram()[128:])
                cloudy += sum(image.getchannel("G").histogram()[128:])
            pixels = frame["width"] * frame["height"]
            return 100.0 * covered / pixels, (100.0 * cloudy / pixels if cloud_mask else None)

        return self._small_request(profile, output_size, date, box, run)

    def fetch_image(self, frame):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        self.last_render_warnings = []
        self.last_render_adjustments = None
        from marblescape_data_coverage import unavailable, from_alpha
        self.last_data_coverage = unavailable()
        frame["_coverage_mask_valid"] = True
        satellite, downloaded = self._render_satellite(frame)
        if frame["_coverage_mask_valid"] and has_data_mask_alpha(frame.get("layer", {})):
            self.last_data_coverage = from_alpha(satellite)
        profile = frame["profile"]
        mosaic = "date_granularity" in frame.get("layer", {})
        rule = tone_rule(frame.get("layer"))
        if rule is not None:
            # Layers without a rule render stock (docs: "Tone rules").
            from marblescape_mosaic_adjustments import apply_rule
            satellite, self.last_render_adjustments = apply_rule(satellite, profile, rule, mosaic)
        color = no_data_choice(profile, frame.get("layer", {}))
        if color in BLUR_CHOICES:
            # After the tone adjustments, whose statistics exclude the gaps.
            satellite = fill_no_data_blur(satellite, edge=color == "blur_edge")
            DOWNLOAD_PROGRESS.raise_if_cancelled()
        # A regular layer's "transparent" shows the map background, as before.
        fill_missing_with_color = mosaic or color != "transparent"
        transparent = mosaic and color == "transparent"
        background_color = ((0, 0, 0, 0) if color == "transparent" else (0, 0, 0, 255) if color in BLUR_CHOICES else
                            tuple(int(color[index:index + 2], 16) for index in (1, 3, 5)) + (255,))

        def optional_map_overlay(template):
            nonlocal downloaded
            try:
                overlay, size = self._map_overlay(frame, template)
            except DownloadCancelledError:
                raise
            except RuntimeError as exc:
                name = {OSM_BACKGROUND_URL: "background", GISCO_BORDERS_URL: "boundaries",
                        OSM_LABELS_URL: "labels"}.get(template, "overlay")
                self.last_render_warnings.append(
                    f"GISCO {name} unavailable; satellite imagery was kept. "
                    f"Other map layers are attempted independently. {exc}"
                )
                return None
            downloaded += size
            return overlay

        def colored_map_overlay(template, label_color):
            if label_color == "transparent":
                return None
            overlay = optional_map_overlay(template)
            if overlay is None:
                return None
            alpha = overlay.getchannel("A")
            rgb = tuple(int(label_color[index:index + 2], 16) for index in (1, 3, 5))
            colored = Image.new("RGBA", overlay.size, rgb + (0,))
            colored.putalpha(alpha)
            return colored

        if fill_missing_with_color:
            result = Image.new("RGBA", satellite.size, background_color)
            used_map_tiles = False
        else:
            background = optional_map_overlay(OSM_BACKGROUND_URL)
            result = background if background is not None else Image.new(
                "RGBA", satellite.size, (0, 0, 0, 255)
            )
            used_map_tiles = background is not None
        result.alpha_composite(satellite)
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        if profile.get("map_borders", profile["map_labels"]):
            borders = colored_map_overlay(
                GISCO_BORDERS_URL,
                profile.get("map_border_color", profile.get("map_label_color", LEGACY_MAP_LABEL_COLOR)))
            if borders is not None:
                result.alpha_composite(borders)
                used_map_tiles = True
        if profile["map_labels"]:
            labels = colored_map_overlay(OSM_LABELS_URL, profile.get("map_label_color", LEGACY_MAP_LABEL_COLOR))
            if labels is not None:
                result.alpha_composite(labels)
                used_map_tiles = True
        if used_map_tiles:
            self._draw_attribution(result)
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        output = io.BytesIO()
        result.convert("RGBA" if transparent else "RGB").save(output, format="PNG", optimize=False)
        return output.getvalue(), downloaded
