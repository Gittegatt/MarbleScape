#!/usr/bin/env python3

from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import argparse
import calendar
import ctypes
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET


# =============================================================================
# CONFIGURATION
# =============================================================================

# This file contains safe defaults. If marblescape_config.toml exists next to the
# application, its values take precedence so users do not need to edit Python
# code. Frozen builds use the executable directory instead of the temporary
# bundle directory.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR)).resolve()
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = SCRIPT_DIR

# Ordered from bottom to top. Kinds "basemap" and "overlay" accept the friendly
# EUMETView names handled below; kind "wms" uses an exact capabilities name.
LAYER_CONFIG = [
    {
        "kind": "basemap",
        "name": "Natural Earth",
        "enabled": True,
        "opacity": 1.0,
        "style": "",
    },
    {
        "kind": "wms",
        "name": "mtg_fd:rgb_geocolour",
        "enabled": True,
        "opacity": 1.0,
        "style": "",
    },
]

# "auto" uses one efficient WMS request unless per-layer opacity/time requires
# local composition. "server" always uses one request; "local" always requests
# transparent PNG layers separately and combines them with Pillow.
RENDER_MODE = "auto"

# Map projection.
# Supported values:
#   "Geographic"
#   "GEOS: MSG FES, MTG FD"
#   "GEOS: MSG RSS"
#   "GEOS: MSG IODC"
#   "Spherical Mercator"
#   "North Polar"
#   "South Polar"
PROJECTION = "GEOS: MSG FES, MTG FD"

# Built-in view name. Supported values are defined in VIEW_PRESETS below.
# "custom" uses CUSTOM_BBOX in logical x/y coordinate order.
VIEW_PRESET = "full_earth"
CUSTOM_BBOX = None

# Output aspect ratio.
# Examples: "16:9", "3:2", "4:3", "1:1", "21:9".
# Set to None to derive the ratio from WIDTH and HEIGHT.
ASPECT_RATIO = "16:9"

# Output image size.
# If HEIGHT is None, it is calculated from WIDTH and ASPECT_RATIO.
WIDTH = 2560
HEIGHT = None

# WMS supersampling factor. Values above 1.0 render at a larger intermediate
# size and downsample to the configured output dimensions with Lanczos.
RENDER_SCALE = 1.0
RENDER_SCALE_AUTOMATIC = True

# View behavior when the requested aspect ratio differs from the projection's
# base extent.
#   "fit"  keeps the full base extent visible and adds surrounding map space.
#   "crop" fills the output frame by cropping the base extent.
VIEW_MODE = "fit"

# Map zoom factor.
#   1.0  = base view
#   >1.0 = zoom in
#   <1.0 = zoom out
DEFAULT_ZOOM = 1.1
ZOOM = DEFAULT_ZOOM

# Hide extended projections in Settings until explicitly enabled.
SHOW_EXTENDED_PROJECTIONS = False

# When enabled, MTG TrueColor shows only the sunlit area. The remainder of
# the Earth disk is filled with black while the area outside the disk keeps
# the configured background color.
TRUECOLOR_BLACK_NIGHT = False

# Polling interval for checking the latest image.
UPDATE_INTERVAL_MINUTES = 5.0

# True keeps the process running and checking at UPDATE_INTERVAL_MINUTES.
# False performs one check and exits.
RUN_CONTINUOUSLY = True

# Root output directories. The script selects the path for the current OS.
OUTPUT_ROOT_WINDOWS = SCRIPT_DIR
OUTPUT_ROOT_LINUX = SCRIPT_DIR

# The script creates these subdirectories below the selected output root.
LATEST_DIRECTORY_NAME = "latest"
HISTORY_DIRECTORY_NAME = "history"
CUSTOM_LATEST_FOLDER = ""
CUSTOM_HISTORY_FOLDER = ""

# File-name prefix for the current image. Each changed image receives a new
# name and can be set directly as the Windows desktop wallpaper.
LATEST_FILENAME_PREFIX = "marblescape"

# History settings.
ENABLE_HISTORY = False
HISTORY_FILENAME_PREFIX = "marblescape"

# History retention mode:
#   "count" keeps at most HISTORY_MAX_FILES files.
#   "time" keeps files newer than the configured retention period.
#   "both" applies both limits; whichever removes a file first wins.
HISTORY_RETENTION_MODE = "count"

# Maximum number of archived files when count-based retention is enabled.
HISTORY_MAX_FILES = 100

# Maximum archive age when time-based retention is enabled.
HISTORY_RETENTION_YEARS = 0
HISTORY_RETENTION_MONTHS = 0
HISTORY_RETENTION_DAYS = 1
HISTORY_RETENTION_HOURS = 0
HISTORY_RETENTION_MINUTES = 0

# Optional fixed image time in ISO 8601 format.
# None requests the latest image currently available from EUMETView.
IMAGE_TIME = None

# Background color used outside rendered map content.
BACKGROUND_COLOR = "#000000"

# Network timeout in seconds.
NETWORK_TIMEOUT_SECONDS = 90

# Request headers used for WMS requests.
from app_version import VERSION

USER_AGENT = f"MarbleScapeWallpaperDownloader/{VERSION}"

# Optional TOML file next to this script. Pass --config to select another file.
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "marblescape_config.toml"
DEFAULT_CONFIG_TEMPLATE_PATH = SCRIPT_DIR / "marblescape_config.example.toml"
ACTIVE_CONFIG_PATH = DEFAULT_CONFIG_PATH

# If True on Windows, set every newly downloaded image directly as the desktop
# wallpaper. This avoids slideshow scheduling and file-cache delays.
SET_WINDOWS_WALLPAPER = True

# Windows wallpaper positioning.
# Supported values: "center", "tile", "stretch", "fit", "fill", "span".
WINDOWS_WALLPAPER_POSITION = "fit"

# Keep the console open after a fatal error when launched by double-click on
# Windows. This has no effect on Linux or Docker.
WINDOWS_PAUSE_ON_EXIT = True


# =============================================================================
# INTERNAL CONSTANTS
# =============================================================================

WMS_URL = "https://view.eumetsat.int/geoserver/wms"
WMS_VERSION = "1.3.0"
IMAGE_FORMAT = "image/png"
MAX_WMS_DIMENSION = 4000
TRUECOLOR_LAYER_NAME = "mtg_fd:rgb_truecolour"
TRUECOLOR_EARTH_MASK_LAYER = "backgrounds:ne_gray"

OUTPUT_ROOT = OUTPUT_ROOT_WINDOWS if os.name == "nt" else OUTPUT_ROOT_LINUX
LATEST_DIR = OUTPUT_ROOT / LATEST_DIRECTORY_NAME
HISTORY_DIR = OUTPUT_ROOT / HISTORY_DIRECTORY_NAME

KNOWN_OVERLAYS = {
    "Coastlines": "backgrounds:ne_10m_coastline",
    "Labels (dark)": "osmgray:dark_labels",
    "Labels (light)": "osmgray:light_labels",
}

# CRS and new-mode extents follow the EUMETSAT viewer configuration:
# https://view.eumetsat.int/assets/data/config.json
# Keep the existing GEOS extents to preserve established framing.
PROJECTIONS = {
    "Geographic": {
        "crs": "EPSG:4326",
        "xmin": -180.0,
        "xmax": 180.0,
        "ymin": -90.0,
        "ymax": 90.0,
        "axis_order": "yx",
    },
    "GEOS: MSG FES, MTG FD": {
        "crs": "AUTO:97004,9001,0,0",
        "xmin": -6500000.0,
        "xmax": 6500000.0,
        "ymin": -6500000.0,
        "ymax": 6500000.0,
        "axis_order": "xy",
    },
    "GEOS: MSG RSS": {
        "crs": "AUTO:97004,9001,9.5,0",
        "xmin": -6500000.0,
        "xmax": 6500000.0,
        "ymin": -6500000.0,
        "ymax": 6500000.0,
        "axis_order": "xy",
    },
    "GEOS: MSG IODC": {
        "crs": "AUTO:97004,9001,41.5,0",
        "xmin": -5440000.0,
        "xmax": 5440000.0,
        "ymin": -5440000.0,
        "ymax": 5440000.0,
        "axis_order": "xy",
    },
    "Spherical Mercator": {
        "crs": "EPSG:3857",
        "xmin": -20037508.342789244,
        "xmax": 20037508.342789244,
        "ymin": -20037508.342789244,
        "ymax": 20037508.342789244,
        "axis_order": "xy",
    },
    "North Polar": {
        "crs": "EPSG:3995",
        "xmin": -12700000.0,
        "xmax": 12700000.0,
        "ymin": -12700000.0,
        "ymax": 12700000.0,
        "axis_order": "xy",
    },
    "South Polar": {
        "crs": "EPSG:3976",
        "xmin": -12700000.0,
        "xmax": 12700000.0,
        "ymin": -12700000.0,
        "ymax": 12700000.0,
        "axis_order": "xy",
    },
}

EXTENDED_PROJECTIONS = frozenset({
    "GEOS: MSG IODC", "Spherical Mercator", "North Polar", "South Polar",
})


def available_projection_choices(show_extended=False):
    """Return the projection choices visible in Settings."""
    return tuple(
        name for name in PROJECTIONS
        if show_extended or name not in EXTENDED_PROJECTIONS
    )


# Geographic preset extents use logical x/y order: west, south, east, north.
# Regional presets deliberately use EPSG:4326 because those coordinates are
# understandable and editable without an additional projection library.
VIEW_PRESETS = {
    "full_earth": {"projection": None, "bbox": None},
    "europe": {
        "projection": "Geographic",
        "bbox": (-25.0, 30.0, 45.0, 72.0),
    },
    "mediterranean": {
        "projection": "Geographic",
        "bbox": (-12.0, 28.0, 42.0, 48.0),
    },
    "central_europe": {
        "projection": "Geographic",
        "bbox": (-2.0, 43.0, 25.0, 57.0),
    },
    "custom": {"projection": None, "bbox": None},
}

WINDOWS_WALLPAPER_POSITIONS = {
    "center": 0,
    "tile": 1,
    "stretch": 2,
    "fit": 3,
    "fill": 4,
    "span": 5,
}

APPLICATION_STOP_EVENT = threading.Event()
FORCE_UPDATE_EVENT = threading.Event()
CONFIGURATION_RELOAD_EVENT = threading.Event()
CONFIGURATION_FILE_LOCK = threading.RLock()
LOADED_CONFIGURATION_FIELDS = (
    "WMS_URL", "WMS_VERSION", "IMAGE_TIME", "NETWORK_TIMEOUT_SECONDS",
    "UPDATE_INTERVAL_MINUTES", "RUN_CONTINUOUSLY", "RENDER_MODE",
    "WIDTH", "HEIGHT", "ASPECT_RATIO", "BACKGROUND_COLOR", "RENDER_SCALE",
    "RENDER_SCALE_AUTOMATIC", "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX",
    "CUSTOM_LATEST_FOLDER", "CUSTOM_HISTORY_FOLDER", "PROJECTION",
    "VIEW_PRESET", "CUSTOM_BBOX", "VIEW_MODE", "ZOOM",
    "SHOW_EXTENDED_PROJECTIONS",
    "TRUECOLOR_BLACK_NIGHT", "ENABLE_HISTORY", "HISTORY_RETENTION_MODE",
    "HISTORY_MAX_FILES", "HISTORY_RETENTION_YEARS",
    "HISTORY_RETENTION_MONTHS", "HISTORY_RETENTION_DAYS",
    "HISTORY_RETENTION_HOURS", "HISTORY_RETENTION_MINUTES",
    "SET_WINDOWS_WALLPAPER", "WINDOWS_WALLPAPER_POSITION",
    "WINDOWS_PAUSE_ON_EXIT", "LAYER_CONFIG", "ACTIVE_CONFIG_PATH",
)
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE_NAME = "MarbleScape"
SETTINGS_BACKUP_FORMAT = "marblescape-settings-backup"
SETTINGS_BACKUP_VERSION = 1
MAX_BACKUP_CONFIGURATION_BYTES = 1_000_000
MAX_SETTINGS_BACKUP_FILE_BYTES = 5_000_000

VIEW_PRESET_MENU_CHOICES = (
    ("Full Earth", "full_earth"),
    ("Europe", "europe"),
    ("Mediterranean", "mediterranean"),
    ("Central Europe", "central_europe"),
)

# Selecting a preset applies a complete, predictable starting profile. Users
# can still change individual settings afterwards without changing the preset.
VIEW_PRESET_PROFILES = {
    "full_earth": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "GEOS: MSG FES, MTG FD",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
    "europe": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "Geographic",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
    "mediterranean": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "Geographic",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
    "central_europe": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "Geographic",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
}

SATELLITE_LAYER_MENU_CHOICES = (
    ("MTG GeoColor", "mtg_fd:rgb_geocolour"),
    ("MTG TrueColor (day)", "mtg_fd:rgb_truecolour"),
    ("MTG Cloud Phase (day)", "mtg_fd:rgb_cloudphase"),
    ("MTG Cloud Type (day)", "mtg_fd:rgb_cloudtype"),
    ("MTG Dust", "mtg_fd:rgb_dust"),
    ("MTG Fog / Low Clouds (night)", "mtg_fd:rgb_fog"),
    ("MSG Natural Color Enhanced (day)", "msg_fes:rgb_naturalenhncd"),
)

WALLPAPER_POSITION_MENU_CHOICES = (
    ("Fit", "fit"),
    ("Fill", "fill"),
    ("Stretch", "stretch"),
    ("Center", "center"),
    ("Tile", "tile"),
    ("Span", "span"),
)

FIT_MODE_MENU_CHOICES = (
    ("Fit", "fit"),
    ("Crop", "crop"),
)

ZOOM_MENU_CHOICES = (
    ("0.8x", 0.8),
    ("1.0x", 1.0),
    ("1.2x", 1.2),
    ("1.5x", 1.5),
    ("2.0x", 2.0),
)

OUTPUT_SIZE_MENU_GROUPS = (
    (
        "16:9 widescreen",
        (
            ("1280 x 720 (HD)", 1280, 720),
            ("1360 x 768 (HD)", 1360, 768),
            ("1366 x 768 (HD)", 1366, 768),
            ("1600 x 900 (HD+)", 1600, 900),
            ("1920 x 1080 (Full HD)", 1920, 1080),
            ("2160 x 1215", 2160, 1215),
            ("2560 x 1440 (QHD)", 2560, 1440),
            ("3200 x 1800 (QHD+)", 3200, 1800),
            ("3840 x 2160 (4K UHD)", 3840, 2160),
            ("5120 x 2880 (5K)", 5120, 2880),
            ("7680 x 4320 (8K UHD)", 7680, 4320),
        ),
    ),
    (
        "16:10",
        (
            ("1280 x 800", 1280, 800),
            ("1440 x 900", 1440, 900),
            ("1680 x 1050", 1680, 1050),
            ("1920 x 1200 (WUXGA)", 1920, 1200),
            ("2560 x 1600", 2560, 1600),
            ("2880 x 1800", 2880, 1800),
            ("3840 x 2400", 3840, 2400),
        ),
    ),
    (
        "3:2",
        (
            ("1920 x 1280", 1920, 1280),
            ("2160 x 1440", 2160, 1440),
            ("2256 x 1504", 2256, 1504),
            ("2304 x 1536", 2304, 1536),
            ("2496 x 1664", 2496, 1664),
            ("3000 x 2000", 3000, 2000),
            ("3240 x 2160", 3240, 2160),
        ),
    ),
    (
        "Classic",
        (
            ("1024 x 768 (4:3)", 1024, 768),
            ("1280 x 960 (4:3)", 1280, 960),
            ("1600 x 1200 (4:3)", 1600, 1200),
            ("1280 x 1024 (5:4)", 1280, 1024),
        ),
    ),
    (
        "Ultrawide",
        (
            ("2560 x 1080 (~21:9)", 2560, 1080),
            ("3440 x 1440 (~21:9)", 3440, 1440),
            ("3840 x 1600 (~21:9)", 3840, 1600),
            ("5120 x 2160 (5K2K)", 5120, 2160),
        ),
    ),
    (
        "Super ultrawide",
        (
            ("3840 x 1080 (32:9)", 3840, 1080),
            ("5120 x 1440 (32:9)", 5120, 1440),
            ("7680 x 2160 (32:9)", 7680, 2160),
        ),
    ),
    (
        "Portrait",
        (
            ("1080 x 1920 (9:16)", 1080, 1920),
            ("1200 x 1920 (10:16)", 1200, 1920),
            ("1440 x 2560 (9:16)", 1440, 2560),
            ("2160 x 3840 (9:16)", 2160, 3840),
        ),
    ),
)

OUTPUT_SIZE_MENU_CHOICES = tuple(
    choice
    for _group_label, group_choices in OUTPUT_SIZE_MENU_GROUPS
    for choice in group_choices
)

ASPECT_RATIO_MENU_GROUPS = (
    (
        "Standard",
        (
            ("16:9", "16:9"),
            ("16:10", "16:10"),
            ("3:2", "3:2"),
            ("4:3", "4:3"),
            ("5:4", "5:4"),
            ("1:1", "1:1"),
        ),
    ),
    (
        "Ultrawide",
        (
            ("2:1 (18:9)", "2:1"),
            ("21:9 (7:3)", "21:9"),
            ("64:27 (~21:9)", "64:27"),
            ("43:18 (~21:9)", "43:18"),
            ("24:10 (12:5)", "12:5"),
            ("32:10 (16:5)", "16:5"),
            ("32:9", "32:9"),
        ),
    ),
    (
        "Portrait",
        (
            ("9:16", "9:16"),
            ("10:16 (5:8)", "5:8"),
            ("2:3", "2:3"),
            ("3:4", "3:4"),
            ("4:5", "4:5"),
        ),
    ),
)

ASPECT_RATIO_MENU_CHOICES = tuple(
    choice
    for _group_label, group_choices in ASPECT_RATIO_MENU_GROUPS
    for choice in group_choices
)

RENDER_QUALITY_MENU_CHOICES = (
    ("Auto (max useful)", "auto"),
    ("Standard (1.0×)", 1.0),
    ("High (1.25×)", 1.25),
    ("Very high (1.5×)", 1.5),
    ("Ultra (2.0×)", 2.0),
)

UPDATE_INTERVAL_MENU_CHOICES = (
    ("5 minutes", 5.0),
    ("10 minutes", 10.0),
    ("15 minutes", 15.0),
    ("30 minutes", 30.0),
    ("60 minutes", 60.0),
)

HISTORY_RETENTION_MODE_MENU_CHOICES = (
    ("Count", "count"),
    ("Time", "time"),
    ("Count and time", "both"),
)

HISTORY_MAX_FILES_MENU_CHOICES = (25, 50, 100, 250, 500)

HISTORY_MAX_AGE_MENU_CHOICES = (
    ("1 hour", {"years": 0, "months": 0, "days": 0, "hours": 1, "minutes": 0}),
    ("6 hours", {"years": 0, "months": 0, "days": 0, "hours": 6, "minutes": 0}),
    ("12 hours", {"years": 0, "months": 0, "days": 0, "hours": 12, "minutes": 0}),
    ("1 day", {"years": 0, "months": 0, "days": 1, "hours": 0, "minutes": 0}),
    ("7 days", {"years": 0, "months": 0, "days": 7, "hours": 0, "minutes": 0}),
    ("30 days", {"years": 0, "months": 0, "days": 30, "hours": 0, "minutes": 0}),
)


def refresh_output_paths():
    global OUTPUT_ROOT, LATEST_DIR, HISTORY_DIR
    OUTPUT_ROOT = OUTPUT_ROOT_WINDOWS if os.name == "nt" else OUTPUT_ROOT_LINUX
    LATEST_DIR = (
        resolve_script_relative_path(CUSTOM_LATEST_FOLDER)
        if CUSTOM_LATEST_FOLDER else OUTPUT_ROOT / LATEST_DIRECTORY_NAME
    )
    HISTORY_DIR = (
        resolve_script_relative_path(CUSTOM_HISTORY_FOLDER)
        if CUSTOM_HISTORY_FOLDER else OUTPUT_ROOT / HISTORY_DIRECTORY_NAME
    )
    validate_image_folders(LATEST_DIR, HISTORY_DIR)


def validate_image_folders(latest, history):
    if latest.resolve() == history.resolve():
        raise ValueError("Latest and history folders must be different.")
    for folder in (latest, history):
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Image folder points to a file: {folder}")


# =============================================================================
# GENERAL HELPERS
# =============================================================================


def timestamp_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message=""):
    if message:
        print(f"[{timestamp_text()}] {message}", flush=True)
    else:
        print(flush=True)


def format_bytes(value):
    value = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024.0


def format_disk_usage(value):
    megabytes = float(value) / 1_000_000.0
    if megabytes >= 1000.0:
        return f"{megabytes / 1000.0:.2f} GB"
    return f"{megabytes:.2f} MB"


def local_xml_name(tag):
    return tag.split("}")[-1]


def calculate_sha256(data):
    return hashlib.sha256(data).hexdigest()


def calculate_file_sha256(file_path):
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def ensure_directories():
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    if ENABLE_HISTORY:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for stale_temp in LATEST_DIR.glob("*.tmp"):
        if stale_temp.is_file():
            stale_temp.unlink(missing_ok=True)


def resolve_script_relative_path(value):
    """Resolve relative configuration paths from the script directory."""
    expanded = os.path.expandvars(str(value))
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def parse_render_scale_setting(value):
    """Return a numeric render scale or the persistent automatic mode."""
    if isinstance(value, str) and value.strip().lower() in {"auto", "automatic"}:
        return "auto"
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Render quality must be 'auto' or a numeric factor."
        ) from exc
    if not math.isfinite(numeric) or numeric < 1.0:
        raise ValueError("Render quality factor must be at least 1.0.")
    return numeric


def load_configuration(config_path):
    """Load optional user settings and update the script defaults."""
    global WMS_URL, WMS_VERSION, IMAGE_TIME, NETWORK_TIMEOUT_SECONDS
    global UPDATE_INTERVAL_MINUTES, RUN_CONTINUOUSLY, RENDER_MODE
    global WIDTH, HEIGHT, ASPECT_RATIO, BACKGROUND_COLOR, RENDER_SCALE
    global RENDER_SCALE_AUTOMATIC
    global OUTPUT_ROOT_WINDOWS, OUTPUT_ROOT_LINUX
    global CUSTOM_LATEST_FOLDER, CUSTOM_HISTORY_FOLDER
    global PROJECTION, VIEW_PRESET, CUSTOM_BBOX, VIEW_MODE, ZOOM
    global SHOW_EXTENDED_PROJECTIONS
    global TRUECOLOR_BLACK_NIGHT
    global ENABLE_HISTORY, HISTORY_RETENTION_MODE, HISTORY_MAX_FILES
    global HISTORY_RETENTION_YEARS, HISTORY_RETENTION_MONTHS
    global HISTORY_RETENTION_DAYS, HISTORY_RETENTION_HOURS
    global HISTORY_RETENTION_MINUTES, SET_WINDOWS_WALLPAPER
    global WINDOWS_WALLPAPER_POSITION, WINDOWS_PAUSE_ON_EXIT, LAYER_CONFIG
    global ACTIVE_CONFIG_PATH

    config_path = resolve_script_relative_path(config_path)
    ACTIVE_CONFIG_PATH = config_path
    if not config_path.exists():
        if config_path != DEFAULT_CONFIG_PATH:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        legacy_config = next(
            (SCRIPT_DIR / name for name in
             ("earthscape_config.toml", "satscape_config.toml", "eumetview_config.toml")
             if (SCRIPT_DIR / name).is_file()),
            SCRIPT_DIR / "eumetview_config.toml",
        )
        if legacy_config.exists():
            shutil.copyfile(legacy_config, config_path)
            log(f"Migrated configuration to {config_path.name}")
        elif DEFAULT_CONFIG_TEMPLATE_PATH.exists():
            shutil.copyfile(DEFAULT_CONFIG_TEMPLATE_PATH, config_path)
            log(f"Created configuration from template: {config_path.name}")
        else:
            refresh_output_paths()
            return False

    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    service = config.get("service", {})
    WMS_URL = str(service.get("endpoint", WMS_URL)).rstrip("?")
    WMS_VERSION = str(service.get("version", WMS_VERSION))
    IMAGE_TIME = service.get("time", IMAGE_TIME) or None
    NETWORK_TIMEOUT_SECONDS = service.get(
        "timeout_seconds", NETWORK_TIMEOUT_SECONDS
    )
    UPDATE_INTERVAL_MINUTES = service.get(
        "update_interval_minutes", UPDATE_INTERVAL_MINUTES
    )
    RUN_CONTINUOUSLY = service.get("run_continuously", RUN_CONTINUOUSLY)
    RENDER_MODE = str(service.get("render_mode", RENDER_MODE)).lower()

    output = config.get("output", {})
    CUSTOM_LATEST_FOLDER = str(output.get("latest_folder", "")).strip()
    WIDTH = output.get("width", WIDTH)
    configured_height = output.get("height", HEIGHT)
    HEIGHT = None if configured_height in (None, 0) else configured_height
    configured_ratio = output.get("aspect_ratio", ASPECT_RATIO)
    ASPECT_RATIO = configured_ratio or None
    BACKGROUND_COLOR = str(output.get("background_color", BACKGROUND_COLOR))
    configured_render_scale = parse_render_scale_setting(
        output.get(
            "render_scale",
            "auto" if RENDER_SCALE_AUTOMATIC else RENDER_SCALE,
        )
    )
    RENDER_SCALE_AUTOMATIC = configured_render_scale == "auto"
    RENDER_SCALE = (
        1.0 if RENDER_SCALE_AUTOMATIC else float(configured_render_scale)
    )
    if "windows_root" in output:
        OUTPUT_ROOT_WINDOWS = resolve_script_relative_path(output["windows_root"])
    if "linux_root" in output:
        OUTPUT_ROOT_LINUX = resolve_script_relative_path(output["linux_root"])

    view = config.get("view", {})
    PROJECTION = str(view.get("projection", PROJECTION))
    # Preserve older configurations and backups; the new key takes precedence.
    SHOW_EXTENDED_PROJECTIONS = bool(view.get(
        "show_extended_projections",
        view.get("unlock_experimental_projections", PROJECTION in EXTENDED_PROJECTIONS),
    ))
    VIEW_PRESET = str(view.get("preset", VIEW_PRESET)).lower()
    configured_bbox = view.get("bbox", CUSTOM_BBOX)
    CUSTOM_BBOX = tuple(configured_bbox) if configured_bbox else None
    VIEW_MODE = str(view.get("fit_mode", VIEW_MODE)).lower()
    ZOOM = view.get("zoom", ZOOM)
    TRUECOLOR_BLACK_NIGHT = bool(
        view.get(
            "truecolor_black_night",
            view.get("truecolour_black_night", TRUECOLOR_BLACK_NIGHT),
        )
    )

    history = config.get("history", {})
    CUSTOM_HISTORY_FOLDER = str(history.get("folder", "")).strip()
    ENABLE_HISTORY = history.get("enabled", ENABLE_HISTORY)
    HISTORY_RETENTION_MODE = str(
        history.get("retention_mode", HISTORY_RETENTION_MODE)
    ).lower()
    HISTORY_MAX_FILES = history.get("max_files", HISTORY_MAX_FILES)
    HISTORY_RETENTION_YEARS = history.get("years", HISTORY_RETENTION_YEARS)
    HISTORY_RETENTION_MONTHS = history.get("months", HISTORY_RETENTION_MONTHS)
    HISTORY_RETENTION_DAYS = history.get("days", HISTORY_RETENTION_DAYS)
    HISTORY_RETENTION_HOURS = history.get("hours", HISTORY_RETENTION_HOURS)
    HISTORY_RETENTION_MINUTES = history.get("minutes", HISTORY_RETENTION_MINUTES)

    windows = config.get("windows", {})
    SET_WINDOWS_WALLPAPER = windows.get(
        "set_wallpaper", SET_WINDOWS_WALLPAPER
    )
    WINDOWS_WALLPAPER_POSITION = str(
        windows.get("position", WINDOWS_WALLPAPER_POSITION)
    ).lower()
    WINDOWS_PAUSE_ON_EXIT = windows.get("pause_on_error", WINDOWS_PAUSE_ON_EXIT)

    if "layers" in config:
        if not isinstance(config["layers"], list):
            raise ValueError("TOML 'layers' must be an array of tables.")
        LAYER_CONFIG = [dict(entry) for entry in config["layers"]]

    refresh_output_paths()
    log(f"Loaded configuration: {config_path.resolve()}")
    return True


def capture_loaded_configuration():
    """Capture live settings so a failed hot reload can be rolled back."""
    state = {}
    for name in LOADED_CONFIGURATION_FIELDS:
        value = globals()[name]
        if name == "LAYER_CONFIG":
            value = [dict(entry) for entry in value]
        state[name] = value
    return state


def restore_loaded_configuration(state):
    """Restore a previously captured live configuration."""
    for name in LOADED_CONFIGURATION_FIELDS:
        value = state[name]
        if name == "LAYER_CONFIG":
            value = [dict(entry) for entry in value]
        globals()[name] = value
    refresh_output_paths()


# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================


def parse_aspect_ratio(value):
    if value is None:
        if WIDTH is None or HEIGHT is None:
            raise ValueError(
                "WIDTH and HEIGHT must both be set when ASPECT_RATIO is None."
            )
        if WIDTH <= 0 or HEIGHT <= 0:
            raise ValueError("WIDTH and HEIGHT must be greater than zero.")
        return WIDTH / HEIGHT

    if isinstance(value, (int, float)):
        ratio = float(value)
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("ASPECT_RATIO must be greater than zero.")
        return ratio

    value = str(value).strip()
    separator = ":" if ":" in value else "/" if "/" in value else None

    if separator is None:
        try:
            ratio = float(value)
        except ValueError as exc:
            raise ValueError(f"Invalid ASPECT_RATIO: {value}") from exc
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("ASPECT_RATIO must be greater than zero.")
        return ratio

    left, right = value.split(separator, 1)
    try:
        width_ratio = float(left.strip())
        height_ratio = float(right.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid ASPECT_RATIO: {value}") from exc

    if (
        not math.isfinite(width_ratio)
        or not math.isfinite(height_ratio)
        or width_ratio <= 0
        or height_ratio <= 0
    ):
        raise ValueError("ASPECT_RATIO values must be greater than zero.")

    ratio = width_ratio / height_ratio
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("ASPECT_RATIO must resolve to a finite value.")
    return ratio


def normalize_aspect_ratio_text(value):
    """Return a validated, compact aspect-ratio value for the TOML file."""
    parse_aspect_ratio(value)
    text = str(value).strip()
    separator = ":" if ":" in text else "/" if "/" in text else None

    if separator is None:
        return format(float(text), ".12g")

    left, right = text.split(separator, 1)

    def format_component(component):
        number = float(component.strip())
        return str(int(number)) if number.is_integer() else format(number, ".12g")

    return f"{format_component(left)}:{format_component(right)}"


def aspect_ratio_for_dimensions(width, height):
    """Return the exact reduced aspect ratio for positive pixel dimensions."""
    if width <= 0 or height <= 0:
        raise ValueError("Width and height must be greater than zero.")
    divisor = math.gcd(int(width), int(height))
    return f"{int(width) // divisor}:{int(height) // divisor}"


def parse_resolution_text(value, automatic_aspect_ratio):
    """Parse WIDTH x HEIGHT text and return matching TOML output values."""
    match = re.fullmatch(r"\s*(\d+)\s*[xX\u00d7]\s*(\d+)\s*", str(value))
    if match is None:
        raise ValueError("Resolution must use the format WIDTH x HEIGHT.")

    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0:
        raise ValueError("Width must be greater than zero.")

    if height == 0:
        if automatic_aspect_ratio is None:
            raise ValueError(
                "An aspect ratio is required when the custom height is zero."
            )
        aspect_ratio = normalize_aspect_ratio_text(automatic_aspect_ratio)
    else:
        aspect_ratio = aspect_ratio_for_dimensions(width, height)

    return width, height, aspect_ratio


def get_output_dimensions():
    ratio = parse_aspect_ratio(ASPECT_RATIO)

    if WIDTH is None or WIDTH <= 0:
        raise ValueError("WIDTH must be greater than zero.")

    if HEIGHT is None:
        calculated_height = round(WIDTH / ratio)
        if calculated_height <= 0:
            raise ValueError("The calculated output height is invalid.")
        return int(WIDTH), int(calculated_height)

    if HEIGHT <= 0:
        raise ValueError("HEIGHT must be greater than zero.")

    actual_ratio = WIDTH / HEIGHT
    relative_difference = abs(actual_ratio - ratio) / ratio
    if relative_difference > 0.005:
        raise ValueError(
            "WIDTH and HEIGHT do not match ASPECT_RATIO. "
            "Set HEIGHT to None or use matching dimensions."
        )

    return int(WIDTH), int(HEIGHT)


def get_render_dimensions(output_width, output_height):
    """Return capped WMS dimensions and the effective supersampling factor."""
    maximum_scale = min(
        MAX_WMS_DIMENSION / output_width,
        MAX_WMS_DIMENSION / output_height,
    )
    requested_scale = get_requested_render_scale(output_width, output_height)
    effective_scale = min(requested_scale, maximum_scale)
    render_width = min(
        MAX_WMS_DIMENSION,
        max(1, round(output_width * effective_scale)),
    )
    render_height = min(
        MAX_WMS_DIMENSION,
        max(1, round(output_height * effective_scale)),
    )
    return int(render_width), int(render_height), float(effective_scale)


def get_render_scale_setting():
    """Return the serializable render-quality setting."""
    return "auto" if RENDER_SCALE_AUTOMATIC else RENDER_SCALE


def get_requested_render_scale(output_width, output_height):
    """Return the requested factor for the current output dimensions."""
    if RENDER_SCALE_AUTOMATIC:
        return min(
            MAX_WMS_DIMENSION / output_width,
            MAX_WMS_DIMENSION / output_height,
        )
    return RENDER_SCALE


def normalize_layer_config(entry, index):
    if not isinstance(entry, dict):
        raise ValueError(f"Layer {index} must be a TOML table.")

    name = str(entry.get("name", "")).strip()
    if not name:
        raise ValueError(f"Layer {index} has no name.")

    kind = str(entry.get("kind", "wms")).lower()
    if kind not in {"wms", "basemap", "overlay"}:
        raise ValueError(
            f"Layer {index} ({name}) has unsupported kind '{kind}'."
        )

    try:
        opacity = float(entry.get("opacity", 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Layer {index} ({name}) has invalid opacity.") from exc
    if not 0.0 <= opacity <= 1.0:
        raise ValueError(f"Layer {index} ({name}) opacity must be from 0.0 to 1.0.")

    time_specified = "time" in entry
    layer_time = entry.get("time")
    if layer_time is not None:
        layer_time = str(layer_time).strip()
        if not layer_time or layer_time.lower() == "latest":
            layer_time = None

    return {
        "kind": kind,
        "name": name,
        "enabled": bool(entry.get("enabled", True)),
        "opacity": opacity,
        "style": str(entry.get("style", "")).strip(),
        "time": layer_time,
        "time_specified": time_specified,
    }


def get_configured_layers():
    return [
        normalize_layer_config(entry, index)
        for index, entry in enumerate(LAYER_CONFIG, start=1)
    ]


def parse_background_color(value):
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    elif text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        raise ValueError("BACKGROUND_COLOR must contain exactly six RGB hex digits.")
    try:
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError("BACKGROUND_COLOR is not a valid RGB hex color.") from exc


def normalize_background_color(value):
    red, green, blue = parse_background_color(value)
    return f"#{red:02X}{green:02X}{blue:02X}"


def validate_configuration():
    configured_layers = get_configured_layers()
    if not any(layer["enabled"] and layer["opacity"] > 0 for layer in configured_layers):
        raise ValueError("At least one enabled layer with opacity above zero is required.")

    if RENDER_MODE not in {"auto", "server", "local"}:
        raise ValueError("RENDER_MODE must be 'auto', 'server', or 'local'.")

    if RENDER_MODE == "server" and any(
        layer["enabled"] and layer["opacity"] != 1.0
        for layer in configured_layers
    ):
        raise ValueError(
            "Per-layer opacity requires render_mode='auto' or 'local'."
        )

    if PROJECTION not in PROJECTIONS:
        raise ValueError(f"Unsupported PROJECTION: {PROJECTION}")
    if (
        PROJECTION in EXTENDED_PROJECTIONS
        and not SHOW_EXTENDED_PROJECTIONS
    ):
        raise ValueError(
            "Enable view.show_extended_projections to use this projection."
        )

    if VIEW_PRESET not in VIEW_PRESETS:
        raise ValueError(
            f"Unsupported VIEW_PRESET: {VIEW_PRESET}. "
            f"Use one of: {', '.join(VIEW_PRESETS)}"
        )

    if VIEW_PRESET == "custom":
        if CUSTOM_BBOX is None or len(CUSTOM_BBOX) != 4:
            raise ValueError("The custom view requires four bbox values.")
        try:
            xmin, ymin, xmax, ymax = map(float, CUSTOM_BBOX)
        except (TypeError, ValueError) as exc:
            raise ValueError("The custom bbox contains a non-numeric value.") from exc
        if xmin >= xmax or ymin >= ymax:
            raise ValueError("The custom bbox must satisfy xmin < xmax and ymin < ymax.")

    if VIEW_MODE not in {"fit", "crop"}:
        raise ValueError("VIEW_MODE must be 'fit' or 'crop'.")

    if ZOOM <= 0:
        raise ValueError("ZOOM must be greater than zero.")

    if (
        not RENDER_SCALE_AUTOMATIC
        and (not math.isfinite(RENDER_SCALE) or RENDER_SCALE < 1.0)
    ):
        raise ValueError("RENDER_SCALE must be a finite value of at least 1.0.")

    if UPDATE_INTERVAL_MINUTES <= 0:
        raise ValueError("UPDATE_INTERVAL_MINUTES must be greater than zero.")

    if NETWORK_TIMEOUT_SECONDS <= 0:
        raise ValueError("NETWORK_TIMEOUT_SECONDS must be greater than zero.")

    parse_background_color(BACKGROUND_COLOR)

    if HISTORY_RETENTION_MODE not in {"count", "time", "both"}:
        raise ValueError(
            "HISTORY_RETENTION_MODE must be 'count', 'time', or 'both'."
        )

    if HISTORY_RETENTION_MODE in {"count", "both"} and HISTORY_MAX_FILES < 0:
        raise ValueError("HISTORY_MAX_FILES cannot be negative.")

    retention_values = (
        HISTORY_RETENTION_YEARS,
        HISTORY_RETENTION_MONTHS,
        HISTORY_RETENTION_DAYS,
        HISTORY_RETENTION_HOURS,
        HISTORY_RETENTION_MINUTES,
    )

    if any(value < 0 for value in retention_values):
        raise ValueError("History retention time values cannot be negative.")

    if HISTORY_RETENTION_MODE in {"time", "both"} and not any(retention_values):
        raise ValueError(
            "At least one time-based history retention value must be greater than zero."
        )

    if WINDOWS_WALLPAPER_POSITION.lower() not in WINDOWS_WALLPAPER_POSITIONS:
        raise ValueError(
            f"Unsupported WINDOWS_WALLPAPER_POSITION: {WINDOWS_WALLPAPER_POSITION}"
        )

    get_output_dimensions()


# =============================================================================
# MAP EXTENT AND OUTPUT SIZE
# =============================================================================


def get_active_view():
    preset = VIEW_PRESETS[VIEW_PRESET]
    projection_name = preset["projection"] or PROJECTION
    projection = PROJECTIONS[projection_name]

    if VIEW_PRESET == "custom":
        extent = tuple(map(float, CUSTOM_BBOX))
    elif preset["bbox"] is not None:
        extent = tuple(map(float, preset["bbox"]))
    else:
        extent = (
            projection["xmin"],
            projection["ymin"],
            projection["xmax"],
            projection["ymax"],
        )

    return projection_name, projection, extent


def serialize_bbox(projection, extent):
    xmin, ymin, xmax, ymax = extent
    if projection["axis_order"] == "yx":
        values = (ymin, xmin, ymax, xmax)
    else:
        values = (xmin, ymin, xmax, ymax)
    return ",".join(f"{value:.6f}" for value in values)


def calculate_bbox(projection, target_ratio, base_extent):
    xmin, ymin, xmax, ymax = base_extent

    full_width = xmax - xmin
    full_height = ymax - ymin
    base_ratio = full_width / full_height

    center_x = (xmin + xmax) / 2.0
    center_y = (ymin + ymax) / 2.0

    if VIEW_MODE == "fit":
        if target_ratio > base_ratio:
            new_height = full_height
            new_width = full_height * target_ratio
        else:
            new_width = full_width
            new_height = full_width / target_ratio
    else:
        if target_ratio > base_ratio:
            new_width = full_width
            new_height = full_width / target_ratio
        else:
            new_height = full_height
            new_width = full_height * target_ratio

    new_width /= ZOOM
    new_height /= ZOOM

    new_xmin = center_x - new_width / 2.0
    new_xmax = center_x + new_width / 2.0
    new_ymin = center_y - new_height / 2.0
    new_ymax = center_y + new_height / 2.0

    if projection["crs"] == "EPSG:4326" and (
        new_xmin < -180.0
        or new_xmax > 180.0
        or new_ymin < -90.0
        or new_ymax > 90.0
    ):
        raise ValueError(
            "The calculated geographic bbox exceeds valid longitude/latitude "
            "bounds. Use fit_mode='crop', a larger zoom, or a regional preset."
        )

    extent = (new_xmin, new_ymin, new_xmax, new_ymax)
    return serialize_bbox(projection, extent), extent


# =============================================================================
# WMS CAPABILITIES AND LAYER RESOLUTION
# =============================================================================


def make_request(url):
    return Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


def download_capabilities():
    params = {
        "service": "WMS",
        "version": WMS_VERSION,
        "request": "GetCapabilities",
    }
    url = WMS_URL + "?" + urlencode(params)

    log("Downloading WMS capabilities...")
    with urlopen(make_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
        return response.read()


def parse_layers(xml_data):
    root = ET.fromstring(xml_data)
    result = []

    def direct_text(element, tag_name):
        for child in element:
            if local_xml_name(child.tag) == tag_name and child.text:
                return child.text.strip()
        return ""

    def parse_layer(layer, inherited):
        crs_values = set(inherited["crs"])
        styles = dict(inherited["styles"])
        dimensions = dict(inherited["dimensions"])
        bounding_boxes = dict(inherited["bounding_boxes"])
        geographic_bbox = inherited["geographic_bbox"]

        for child in layer:
            tag = local_xml_name(child.tag)

            if tag in {"CRS", "SRS"} and child.text:
                crs_values.update(child.text.split())
            elif tag == "Style":
                style_name = direct_text(child, "Name")
                if style_name:
                    styles[style_name] = {
                        "name": style_name,
                        "title": direct_text(child, "Title"),
                        "abstract": direct_text(child, "Abstract"),
                    }
            elif tag in {"Dimension", "Extent"}:
                dimension_name = child.attrib.get("name", "").strip()
                if dimension_name:
                    dimensions[dimension_name] = {
                        "name": dimension_name,
                        "units": child.attrib.get("units", ""),
                        "default": child.attrib.get("default", ""),
                        "nearest_value": child.attrib.get("nearestValue", ""),
                        "multiple_values": child.attrib.get("multipleValues", ""),
                        "current": child.attrib.get("current", ""),
                        "values": (child.text or "").strip(),
                    }
            elif tag == "BoundingBox":
                bbox_crs = child.attrib.get("CRS") or child.attrib.get("SRS")
                if bbox_crs:
                    try:
                        bounding_boxes[bbox_crs] = [
                            float(child.attrib[key])
                            for key in ("minx", "miny", "maxx", "maxy")
                        ]
                    except (KeyError, ValueError):
                        pass
            elif tag in {"EX_GeographicBoundingBox", "LatLonBoundingBox"}:
                if tag == "LatLonBoundingBox":
                    try:
                        geographic_bbox = [
                            float(child.attrib[key])
                            for key in ("minx", "miny", "maxx", "maxy")
                        ]
                    except (KeyError, ValueError):
                        pass
                else:
                    values = {}
                    for coordinate in child:
                        if coordinate.text:
                            try:
                                values[local_xml_name(coordinate.tag)] = float(
                                    coordinate.text
                                )
                            except ValueError:
                                pass
                    required = (
                        "westBoundLongitude",
                        "southBoundLatitude",
                        "eastBoundLongitude",
                        "northBoundLatitude",
                    )
                    if all(key in values for key in required):
                        geographic_bbox = [values[key] for key in required]

        opaque = inherited["opaque"]
        if "opaque" in layer.attrib:
            opaque = layer.attrib["opaque"] not in {"0", "false", "False"}

        queryable = inherited["queryable"]
        if "queryable" in layer.attrib:
            queryable = layer.attrib["queryable"] not in {"0", "false", "False"}

        current = {
            "crs": crs_values,
            "styles": styles,
            "dimensions": dimensions,
            "bounding_boxes": bounding_boxes,
            "geographic_bbox": geographic_bbox,
            "opaque": opaque,
            "queryable": queryable,
        }

        name = direct_text(layer, "Name")
        if name:
            result.append(
                {
                    "name": name,
                    "title": direct_text(layer, "Title"),
                    "abstract": direct_text(layer, "Abstract"),
                    "crs": sorted(crs_values),
                    "styles": sorted(styles.values(), key=lambda item: item["name"]),
                    "dimensions": dimensions,
                    "bounding_boxes": bounding_boxes,
                    "geographic_bbox": geographic_bbox,
                    "opaque": opaque,
                    "queryable": queryable,
                }
            )

        for child in layer:
            if local_xml_name(child.tag) == "Layer":
                parse_layer(child, current)

    empty = {
        "crs": set(),
        "styles": {},
        "dimensions": {},
        "bounding_boxes": {},
        "geographic_bbox": None,
        "opaque": False,
        "queryable": False,
    }
    top_layers = []
    for element in root.iter():
        if local_xml_name(element.tag) == "Capability":
            top_layers = [
                child for child in element if local_xml_name(child.tag) == "Layer"
            ]
            break

    if not top_layers:
        top_layers = [
            element for element in root if local_xml_name(element.tag) == "Layer"
        ]

    for layer in top_layers:
        parse_layer(layer, empty)

    return result


def layer_exists(layers, name):
    return any(layer["name"] == name for layer in layers)


def get_layer_metadata(layers, name):
    return next((layer for layer in layers if layer["name"] == name), None)


def filter_layer_catalog(layers, search_text=""):
    words = str(search_text or "").lower().split()
    if not words:
        return list(layers)
    return [
        layer
        for layer in layers
        if all(
            word in f"{layer['name']} {layer['title']} {layer['abstract']}".lower()
            for word in words
        )
    ]


def print_layer_catalog(layers, search_text=""):
    matches = filter_layer_catalog(layers, search_text)
    print(f"Available WMS layers: {len(matches)} of {len(layers)}")
    for layer in matches:
        style_names = ", ".join(style["name"] for style in layer["styles"])
        time_dimension = layer["dimensions"].get("time")
        details = []
        if style_names:
            details.append(f"styles={style_names}")
        if time_dimension:
            details.append("time=yes")
        suffix = f" [{'; '.join(details)}]" if details else ""
        print(f"{layer['name']} | {layer['title']}{suffix}")


def export_layer_catalog(layers, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(layers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Exported {len(layers)} WMS layers to: {output_path.resolve()}")


def find_layer(layers, required_words, excluded_words=(), prefer_workspace=None):
    candidates = []

    for layer in layers:
        searchable = f"{layer['name']} {layer['title']}".lower()

        if not all(word.lower() in searchable for word in required_words):
            continue
        if any(word.lower() in searchable for word in excluded_words):
            continue

        score = 0
        if prefer_workspace and layer["name"].startswith(prefer_workspace + ":"):
            score += 100
        score -= len(layer["name"])
        candidates.append((score, layer["name"], layer["title"]))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def resolve_overlay(name, layers):
    if name in KNOWN_OVERLAYS:
        exact = KNOWN_OVERLAYS[name]
        if layer_exists(layers, exact):
            return exact

    if name == "Coastlines":
        return find_layer(
            layers,
            required_words=("coast",),
            prefer_workspace="backgrounds",
        )

    if name == "Boundaries":
        preferred = find_layer(
            layers,
            required_words=("gisco", "bound"),
            excluded_words=("label",),
            prefer_workspace="backgrounds",
        )
        if preferred:
            return preferred

        fallback = "backgrounds:ne_boundary_lines_land"
        if layer_exists(layers, fallback):
            return fallback

        return find_layer(
            layers,
            required_words=("bound",),
            excluded_words=("label",),
            prefer_workspace="backgrounds",
        )

    if name == "Labels (dark)":
        return find_layer(
            layers,
            required_words=("dark", "label"),
            prefer_workspace="osmgray",
        )

    if name == "Labels (light)":
        return find_layer(
            layers,
            required_words=("light", "label"),
            prefer_workspace="osmgray",
        )

    if name == "Graticules (dark)":
        return find_layer(
            layers,
            required_words=("gratic", "dark"),
        )

    if name == "Graticules (light)":
        return find_layer(
            layers,
            required_words=("gratic", "light"),
        )

    raise ValueError(f"Unsupported overlay: {name}")


def resolve_basemap(name, layers):
    if name is None:
        return None

    if name == "Natural Earth":
        for candidate in ("backgrounds:ne_gray", "osmgray:ne_gray"):
            if layer_exists(layers, candidate):
                return candidate

        return find_layer(
            layers,
            required_words=("natural", "earth"),
            excluded_words=("coast", "boundary", "label", "gratic"),
        )

    if name == "OSM Dark":
        return find_layer(
            layers,
            required_words=("dark",),
            excluded_words=("label", "gratic", "boundary", "coast"),
            prefer_workspace="osmgray",
        )

    if name == "OSM Light":
        for candidate in ("osmgray:light", "osmgray:light_bg"):
            if layer_exists(layers, candidate):
                return candidate

        return find_layer(
            layers,
            required_words=("light",),
            excluded_words=("label", "gratic", "boundary", "coast"),
            prefer_workspace="osmgray",
        )

    raise ValueError(f"Unsupported basemap: {name}")


def get_latest_layer_time(metadata):
    """Return the newest explicit timestamp advertised for a WMS layer."""
    time_dimension = metadata.get("dimensions", {}).get("time")
    if not time_dimension:
        return None

    default = str(time_dimension.get("default", "")).strip()
    if default and default.lower() not in {"current", "latest"}:
        return default

    values = str(time_dimension.get("values", "")).strip()
    if not values:
        return None

    newest = values.split(",")[-1].strip()
    interval_parts = newest.split("/")
    if len(interval_parts) >= 2:
        newest = interval_parts[1].strip()
    return newest or None


def resolve_configured_layers(layers, projection, log_resolutions=True):
    resolved_layers = []

    for configured in get_configured_layers():
        if not configured["enabled"] or configured["opacity"] == 0:
            continue

        if configured["kind"] == "wms":
            layer_name = configured["name"]
        elif configured["kind"] == "basemap":
            layer_name = resolve_basemap(configured["name"], layers)
        else:
            layer_name = resolve_overlay(configured["name"], layers)

        if not layer_name or not layer_exists(layers, layer_name):
            raise RuntimeError(
                f"Configured {configured['kind']} layer is unavailable: "
                f"{configured['name']}"
            )

        metadata = get_layer_metadata(layers, layer_name)
        available_styles = {style["name"] for style in metadata["styles"]}
        if configured["style"] and configured["style"] not in available_styles:
            raise RuntimeError(
                f"Style '{configured['style']}' is unavailable for {layer_name}. "
                f"Available: {', '.join(sorted(available_styles)) or 'default only'}"
            )

        # AUTO geostationary projections are GeoServer-defined on demand and
        # therefore are not necessarily listed per layer in GetCapabilities.
        if (
            not projection["crs"].startswith("AUTO:")
            and metadata["crs"]
            and projection["crs"] not in metadata["crs"]
        ):
            raise RuntimeError(
                f"Layer {layer_name} does not advertise CRS {projection['crs']}."
            )

        uses_latest_time = (
            configured["time_specified"] and configured["time"] is None
        ) or (
            not configured["time_specified"] and IMAGE_TIME is None
        )
        if uses_latest_time:
            effective_time = get_latest_layer_time(metadata)
            if "time" in metadata["dimensions"] and not effective_time:
                raise RuntimeError(
                    f"No explicit latest timestamp is advertised for {layer_name}."
                )
        else:
            effective_time = (
                configured["time"] if configured["time_specified"] else IMAGE_TIME
            )
        resolved = dict(configured)
        resolved.update(
            {
                "requested_name": configured["name"],
                "name": layer_name,
                "title": metadata["title"],
                "time": effective_time,
                "uses_latest_time": uses_latest_time and effective_time is not None,
                "metadata": metadata,
            }
        )
        resolved_layers.append(resolved)

        if log_resolutions and configured["name"] != layer_name:
            log(
                f"Resolved {configured['kind']}: "
                f"{configured['name']} -> {layer_name}"
            )

    return resolved_layers


def normalize_wms_background_color():
    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    return f"0x{red:02X}{green:02X}{blue:02X}"


def build_getmap_url(
    layer_names,
    styles,
    projection,
    bbox,
    output_width,
    output_height,
    *,
    transparent,
    image_time,
):
    params = {
        "service": "WMS",
        "version": WMS_VERSION,
        "request": "GetMap",
        "layers": ",".join(layer_names),
        "styles": ",".join(styles),
        "crs": projection["crs"],
        "bbox": bbox,
        "width": output_width,
        "height": output_height,
        "format": IMAGE_FORMAT,
        "transparent": "true" if transparent else "false",
        "bgcolor": normalize_wms_background_color(),
    }

    if image_time:
        params["time"] = image_time

    return WMS_URL + "?" + urlencode(params, safe=",:")


def determine_render_mode(resolved_layers):
    if RENDER_MODE in {"server", "local"}:
        return RENDER_MODE

    opacities_require_local = any(
        layer["opacity"] != 1.0 for layer in resolved_layers
    )
    layer_times = {
        layer["time"] for layer in resolved_layers if layer["time"] is not None
    }
    times_require_local = len(layer_times) > 1
    return "local" if opacities_require_local or times_require_local else "server"


def should_blacken_truecolor_night(resolved_layers):
    if not TRUECOLOR_BLACK_NIGHT:
        return False

    primary_wms_layer = next(
        (layer for layer in resolved_layers if layer["kind"] == "wms"),
        None,
    )
    return (
        primary_wms_layer is not None
        and primary_wms_layer["name"] == TRUECOLOR_LAYER_NAME
    )


def build_render_plan(
    resolved_layers,
    projection,
    bbox,
    output_width,
    output_height,
):
    if should_blacken_truecolor_night(resolved_layers):
        requests = [
            {
                "url": build_getmap_url(
                    [TRUECOLOR_EARTH_MASK_LAYER],
                    [""],
                    projection,
                    bbox,
                    output_width,
                    output_height,
                    transparent=True,
                    image_time=None,
                ),
                "label": f"{TRUECOLOR_EARTH_MASK_LAYER} (Earth mask)",
                "opacity": 1.0,
                "role": "earth_mask",
            }
        ]

        for layer in resolved_layers:
            if layer["kind"] == "basemap":
                continue
            requests.append(
                {
                    "url": build_getmap_url(
                        [layer["name"]],
                        [layer["style"]],
                        projection,
                        bbox,
                        output_width,
                        output_height,
                        transparent=True,
                        image_time=layer["time"],
                    ),
                    "label": layer["name"],
                    "opacity": layer["opacity"],
                    "role": "content",
                }
            )

        return "truecolor_black_night", requests

    render_mode = determine_render_mode(resolved_layers)

    if render_mode == "server":
        if any(layer["opacity"] != 1.0 for layer in resolved_layers):
            raise ValueError("Server rendering does not support per-layer opacity.")
        layer_times = {
            layer["time"] for layer in resolved_layers if layer["time"] is not None
        }
        if len(layer_times) > 1:
            raise ValueError("Server rendering cannot use different per-layer times.")
        image_time = next(iter(layer_times), None)
        if VIEW_PRESET == "full_earth":
            # In the geostationary full-Earth projection, the Natural Earth
            # basemap is opaque. Keep configured bottom-to-top order so the
            # selected satellite product is rendered above the basemap.
            server_layers = list(resolved_layers)
        else:
            # Preserve the established rendering behavior of regional presets.
            server_layers = list(reversed(resolved_layers))
        url = build_getmap_url(
            [layer["name"] for layer in server_layers],
            [layer["style"] for layer in server_layers],
            projection,
            bbox,
            output_width,
            output_height,
            transparent=False,
            image_time=image_time,
        )
        return render_mode, [
            {
                "url": url,
                "label": ", ".join(layer["name"] for layer in resolved_layers),
                "opacity": 1.0,
            }
        ]

    requests = []
    for layer in resolved_layers:
        requests.append(
            {
                "url": build_getmap_url(
                    [layer["name"]],
                    [layer["style"]],
                    projection,
                    bbox,
                    output_width,
                    output_height,
                    transparent=True,
                    image_time=layer["time"],
                ),
                "label": layer["name"],
                "opacity": layer["opacity"],
            }
        )
    return render_mode, requests


# =============================================================================
# IMAGE DOWNLOAD AND INSTALLATION
# =============================================================================


def download_image(url, label=None):
    if label:
        log(f"Requesting layer: {label}")
    else:
        log("Requesting the latest image...")

    try:
        with urlopen(make_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            data = response.read()
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if not content_type.startswith("image/"):
        error_text = data.decode("utf-8", errors="replace")
        raise RuntimeError(
            "EUMETView returned a non-image response:\n" + error_text[:4000]
        )

    if not data:
        raise RuntimeError("EUMETView returned an empty image response.")

    return data


def download_rendered_layer(request_spec, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Local image composition requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    data = download_image(request_spec["url"], request_spec["label"])
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            layer_image = source.convert("RGBA")
    except Exception as exc:
        raise RuntimeError(
            f"Could not decode WMS layer image: {request_spec['label']}"
        ) from exc

    if layer_image.size != (output_width, output_height):
        raise RuntimeError(
            f"Layer {request_spec['label']} returned {layer_image.size[0]} x "
            f"{layer_image.size[1]} instead of {output_width} x {output_height}."
        )

    opacity = request_spec["opacity"]
    if opacity != 1.0:
        alpha = layer_image.getchannel("A").point(
            lambda value: round(value * opacity)
        )
        layer_image.putalpha(alpha)

    return layer_image, len(data)


def compose_rendered_layers(requests, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Per-layer opacity/local rendering requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    canvas = Image.new("RGBA", (output_width, output_height), (red, green, blue, 255))
    total_downloaded = 0

    for request_spec in requests:
        layer_image, downloaded_size = download_rendered_layer(
            request_spec,
            output_width,
            output_height,
        )
        total_downloaded += downloaded_size
        canvas = Image.alpha_composite(canvas, layer_image)

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=False)
    return output.getvalue(), total_downloaded


def compose_truecolor_black_night(requests, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "TrueColor night-side masking requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    if not requests or requests[0].get("role") != "earth_mask":
        raise RuntimeError("TrueColor render plan has no Earth mask.")

    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    canvas = Image.new("RGBA", (output_width, output_height), (red, green, blue, 255))
    total_downloaded = 0

    earth_image, downloaded_size = download_rendered_layer(
        requests[0],
        output_width,
        output_height,
    )
    total_downloaded += downloaded_size
    canvas.paste((0, 0, 0, 255), (0, 0), earth_image.getchannel("A"))

    for request_spec in requests[1:]:
        layer_image, downloaded_size = download_rendered_layer(
            request_spec,
            output_width,
            output_height,
        )
        total_downloaded += downloaded_size
        canvas = Image.alpha_composite(canvas, layer_image)

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=False)
    return output.getvalue(), total_downloaded


def render_image(render_mode, requests, output_width, output_height):
    if render_mode == "server":
        data = download_image(requests[0]["url"])
        return data, len(data)
    if render_mode == "truecolor_black_night":
        return compose_truecolor_black_night(
            requests,
            output_width,
            output_height,
        )
    return compose_rendered_layers(requests, output_width, output_height)


def resize_rendered_image(data, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Image resizing requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            if source.size == (output_width, output_height):
                return data
            resized = source.convert("RGB").resize(
                (output_width, output_height),
                Image.Resampling.LANCZOS,
            )
    except Exception as exc:
        raise RuntimeError("Could not resize the rendered WMS image.") from exc

    output = io.BytesIO()
    resized.save(output, format="PNG", optimize=False)
    return output.getvalue()


def generate_history_path():
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    candidate = HISTORY_DIR / f"{HISTORY_FILENAME_PREFIX}_{timestamp}.png"
    counter = 1

    while candidate.exists():
        candidate = HISTORY_DIR / (
            f"{HISTORY_FILENAME_PREFIX}_{timestamp}_{counter}.png"
        )
        counter += 1

    return candidate


def get_latest_image_files():
    """Return image files in the latest folder, newest first."""
    files = [path for path in LATEST_DIR.glob("*.png") if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def generate_latest_path():
    """Create an unused timestamped path for the newest image."""
    timestamp = dt.datetime.now().replace(microsecond=0)

    while True:
        candidate = LATEST_DIR / (
            f"{LATEST_FILENAME_PREFIX}_{timestamp.strftime('%Y-%m-%d_%H%M%S')}.png"
        )
        if not candidate.exists():
            return candidate
        timestamp += dt.timedelta(seconds=1)


def save_latest_image(data):
    new_hash = calculate_sha256(data)
    existing_files = get_latest_image_files()
    current_path = existing_files[0] if existing_files else None

    if current_path is not None:
        old_hash = calculate_file_sha256(current_path)
        if new_hash == old_hash:
            log("No image change detected. Latest file remains unchanged.")
            return None

    latest_path = generate_latest_path()
    temp_path = latest_path.with_suffix(latest_path.suffix + ".tmp")
    temp_path.write_bytes(data)

    archived_path = None
    installed = False

    try:
        if current_path is not None and ENABLE_HISTORY:
            archived_path = generate_history_path()
            shutil.copy2(current_path, archived_path)

        os.replace(temp_path, latest_path)
        installed = True

        # Keep exactly one PNG in the latest folder. Install the new path first
        # so the folder is never temporarily empty.
        for old_path in existing_files:
            old_path.unlink(missing_ok=True)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if not installed and archived_path is not None and archived_path.exists():
            archived_path.unlink(missing_ok=True)
        raise

    if archived_path is not None:
        log(f"Archived previous image: {archived_path}")

    log(f"Installed new latest image: {latest_path}")
    log(f"Final image size: {format_bytes(len(data))}")
    return latest_path


# =============================================================================
# HISTORY RETENTION
# =============================================================================


def get_history_files():
    return list({
        path
        for prefix in (HISTORY_FILENAME_PREFIX, "earthscape", "satscape", "eumetview")
        for path in HISTORY_DIR.glob(f"{prefix}_*.png")
        if path.is_file()
    })


def subtract_calendar_period(value):
    months_to_subtract = HISTORY_RETENTION_YEARS * 12 + HISTORY_RETENTION_MONTHS
    absolute_month = value.year * 12 + (value.month - 1) - months_to_subtract
    target_year = absolute_month // 12
    target_month = absolute_month % 12 + 1
    target_day = min(value.day, calendar.monthrange(target_year, target_month)[1])

    shifted = value.replace(
        year=target_year,
        month=target_month,
        day=target_day,
    )

    return shifted - dt.timedelta(
        days=HISTORY_RETENTION_DAYS,
        hours=HISTORY_RETENTION_HOURS,
        minutes=HISTORY_RETENTION_MINUTES,
    )


def cleanup_history():
    if not ENABLE_HISTORY:
        return 0

    removed = 0
    files = get_history_files()

    if HISTORY_RETENTION_MODE in {"time", "both"}:
        cutoff = subtract_calendar_period(dt.datetime.now())
        for path in list(files):
            modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
            if modified < cutoff:
                path.unlink(missing_ok=True)
                removed += 1

        files = get_history_files()

    if HISTORY_RETENTION_MODE in {"count", "both"}:
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files[HISTORY_MAX_FILES:]:
            path.unlink(missing_ok=True)
            removed += 1

    if removed:
        log(f"History cleanup removed {removed} file(s).")

    return removed


# =============================================================================
# STORAGE ESTIMATE
# =============================================================================


def estimated_time_retention_slots():
    now = dt.datetime.now()
    cutoff = subtract_calendar_period(now)
    retention_seconds = max(0.0, (now - cutoff).total_seconds())
    interval_seconds = UPDATE_INTERVAL_MINUTES * 60.0
    return int(math.ceil(retention_seconds / interval_seconds))


def estimated_history_slots():
    if not ENABLE_HISTORY:
        return 0

    if HISTORY_RETENTION_MODE == "count":
        return HISTORY_MAX_FILES

    time_slots = estimated_time_retention_slots()

    if HISTORY_RETENTION_MODE == "time":
        return time_slots

    return min(HISTORY_MAX_FILES, time_slots)


def print_storage_estimate(image_size):
    history_slots = estimated_history_slots()
    total_slots = history_slots + 1
    expected_bytes = image_size * total_slots

    log()
    log("Storage estimate based on the first downloaded image:")
    log(f"  Current image size: {format_bytes(image_size)}")
    log(f"  Estimated retained history images: {history_slots}")
    log(f"  Latest images: 1")
    log(f"  Estimated maximum total: {format_bytes(expected_bytes)}")

    if HISTORY_RETENTION_MODE in {"time", "both"}:
        log(
            "  Time-based estimate assumes every polling cycle produces a new "
            "image and is therefore a conservative upper-bound estimate."
        )

    log()


def get_storage_status():
    try:
        latest_files = get_latest_image_files()
    except OSError:
        latest_files = []
    try:
        history_files = get_history_files()
    except OSError:
        history_files = []

    def total_file_size(paths):
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except (FileNotFoundError, OSError):
                continue
        return total

    current_image_size = None
    if latest_files:
        try:
            current_image_size = latest_files[0].stat().st_size
        except (FileNotFoundError, OSError):
            pass

    estimated_history_images = estimated_history_slots()
    estimated_maximum_images = estimated_history_images + 1
    estimated_total_bytes = (
        current_image_size * estimated_maximum_images
        if current_image_size is not None
        else None
    )
    used_bytes = total_file_size((*latest_files, *history_files))

    return {
        "latest_images": len(latest_files),
        "current_image_size": current_image_size,
        "estimated_history_images": estimated_history_images,
        "estimated_maximum_images": estimated_maximum_images,
        "estimated_total_bytes": estimated_total_bytes,
        "used_bytes": used_bytes,
    }


# =============================================================================
# WINDOWS DESKTOP WALLPAPER
# =============================================================================


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def check_hresult(result, operation):
    if result < 0:
        unsigned = result & 0xFFFFFFFF
        raise OSError(f"{operation} failed with HRESULT 0x{unsigned:08X}")


def get_com_method(interface_pointer, index, restype, *argtypes):
    vtable = ctypes.cast(
        interface_pointer,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def release_com_pointer(interface_pointer):
    if not interface_pointer:
        return
    release = get_com_method(interface_pointer, 2, ctypes.c_ulong)
    release(interface_pointer)


def create_desktop_wallpaper_interface():
    ole32 = ctypes.windll.ole32

    clsid_desktop_wallpaper = GUID.from_string(
        "C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD"
    )
    iid_desktop_wallpaper = GUID.from_string(
        "B92B56A9-8B55-4E14-9A89-0199BBB6F93B"
    )

    interface_pointer = ctypes.c_void_p()

    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    CLSCTX_ALL = 0x17
    result = ole32.CoCreateInstance(
        ctypes.byref(clsid_desktop_wallpaper),
        None,
        CLSCTX_ALL,
        ctypes.byref(iid_desktop_wallpaper),
        ctypes.byref(interface_pointer),
    )
    check_hresult(result, "CoCreateInstance(IDesktopWallpaper)")
    return interface_pointer


def with_windows_com(callback):
    if os.name != "nt":
        return callback()

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    result = ole32.CoInitialize(None)

    initialized = result >= 0
    if result < 0:
        check_hresult(result, "CoInitialize")

    try:
        return callback()
    finally:
        if initialized:
            ole32.CoUninitialize()


def set_windows_wallpaper(image_path):
    if os.name != "nt":
        return

    position = WINDOWS_WALLPAPER_POSITIONS[WINDOWS_WALLPAPER_POSITION.lower()]

    def apply_wallpaper():
        desktop_wallpaper = None

        try:
            desktop_wallpaper = create_desktop_wallpaper_interface()

            # IDesktopWallpaper vtable indices include the three IUnknown methods.
            set_wallpaper = get_com_method(
                desktop_wallpaper,
                3,
                ctypes.c_long,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
            )
            set_position = get_com_method(
                desktop_wallpaper,
                10,
                ctypes.c_long,
                ctypes.c_int,
            )

            check_hresult(
                set_position(desktop_wallpaper, position),
                "IDesktopWallpaper.SetPosition",
            )
            check_hresult(
                set_wallpaper(desktop_wallpaper, None, str(image_path.resolve())),
                "IDesktopWallpaper.SetWallpaper",
            )
        finally:
            release_com_pointer(desktop_wallpaper)

    with_windows_com(apply_wallpaper)
    log(f"Windows wallpaper set directly: {image_path}")
    log(f"Windows wallpaper position: {WINDOWS_WALLPAPER_POSITION}")


# =============================================================================
# UPDATE LOOP
# =============================================================================


def print_configuration(
    output_width,
    output_height,
    render_width,
    render_height,
    effective_render_scale,
    bbox,
    projection_name,
    projection,
    resolved_layers,
    render_mode,
):
    log(f"MarbleScape Wallpaper Downloader {VERSION}")
    log("--------------------------------")
    log(f"Projection: {projection_name} ({projection['crs']})")
    log(f"View preset: {VIEW_PRESET}")
    log(f"Aspect ratio: {ASPECT_RATIO}")
    log(f"View mode: {VIEW_MODE}")
    log(f"Zoom: {ZOOM:g}")
    log(f"Output size: {output_width} x {output_height}")
    requested_render_scale = get_requested_render_scale(
        output_width,
        output_height,
    )
    if RENDER_SCALE_AUTOMATIC:
        log(
            "Requested render quality: automatic maximum "
            f"({requested_render_scale:g}x for this output)"
        )
    else:
        log(f"Requested render quality: {RENDER_SCALE:g}x")
    log(
        f"Effective WMS render: {render_width} x {render_height} "
        f"({effective_render_scale:g}x)"
    )
    if requested_render_scale > effective_render_scale + 1e-9:
        log(
            f"Render quality capped at approximately {MAX_WMS_DIMENSION} pixels "
            "per WMS axis."
        )
    if output_width > MAX_WMS_DIMENSION or output_height > MAX_WMS_DIMENSION:
        log(
            "Warning: EUMETView may reject dimensions above approximately "
            f"{MAX_WMS_DIMENSION} pixels because of its rendering memory limit."
        )
    log(f"BBOX: {bbox}")
    log(f"Render mode: {render_mode}")
    log(f"Background color: {normalize_background_color(BACKGROUND_COLOR)}")
    log(f"Black TrueColor night side: {TRUECOLOR_BLACK_NIGHT}")
    log(f"Update interval: {UPDATE_INTERVAL_MINUTES:g} minute(s)")
    log(f"Output root: {OUTPUT_ROOT}")
    log(f"Latest directory: {LATEST_DIR}")
    log(f"History directory: {HISTORY_DIR}")
    log(f"History enabled: {ENABLE_HISTORY}")
    if os.name == "nt":
        log(f"Set Windows wallpaper directly: {SET_WINDOWS_WALLPAPER}")
        if SET_WINDOWS_WALLPAPER:
            log(f"Windows wallpaper position: {WINDOWS_WALLPAPER_POSITION}")
    if ENABLE_HISTORY:
        log(f"History retention mode: {HISTORY_RETENTION_MODE}")
    log("Layer order (bottom to top):")
    for index, layer in enumerate(resolved_layers, start=1):
        style = layer["style"] or "default"
        layer_time = layer["time"] or "latest"
        log(
            f"  {index}. {layer['name']} | opacity={layer['opacity']:.2f} | "
            f"style={style} | time={layer_time}"
        )
    log()


def perform_update(
    render_mode,
    requests,
    render_width,
    render_height,
    output_width,
    output_height,
):
    data, downloaded_size = render_image(
        render_mode,
        requests,
        render_width,
        render_height,
    )
    if (render_width, render_height) != (output_width, output_height):
        data = resize_rendered_image(data, output_width, output_height)
    if APPLICATION_STOP_EVENT.is_set():
        raise RuntimeError("Update cancelled because the application is stopping.")
    if CONFIGURATION_RELOAD_EVENT.is_set():
        raise RuntimeError("Update discarded because configuration changed.")
    installed_path = save_latest_image(data)
    current_path = installed_path
    if current_path is None:
        latest_files = get_latest_image_files()
        current_path = latest_files[0] if latest_files else None
    cleanup_history()
    if render_mode in {"local", "truecolor_black_night"}:
        log(f"Total downloaded layer data: {format_bytes(downloaded_size)}")
    return installed_path, current_path, len(data)


def report_update_status(status_callback, state, next_check=None):
    if status_callback is None:
        return
    try:
        status_callback(state, next_check)
    except Exception as exc:
        log(f"Tray status update warning: {exc}")


def sleep_until_next_cycle(cycle_started_monotonic, status_callback=None):
    interval_seconds = UPDATE_INTERVAL_MINUTES * 60.0
    elapsed = time.monotonic() - cycle_started_monotonic
    remaining = max(0.0, interval_seconds - elapsed)

    next_check = dt.datetime.now() + dt.timedelta(seconds=remaining)
    report_update_status(status_callback, "waiting", next_check)
    log(f"Next check: {next_check.strftime('%Y-%m-%d %H:%M:%S')}.")
    deadline = time.monotonic() + remaining
    while not APPLICATION_STOP_EVENT.is_set():
        remaining = deadline - time.monotonic()
        if FORCE_UPDATE_EVENT.is_set() or remaining <= 0:
            return True
        APPLICATION_STOP_EVENT.wait(min(remaining, 0.25))
    return False


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="Download configurable EUMETView WMS wallpaper images."
    )
    parser.add_argument("--version", action="version", version=f"MarbleScape {VERSION}")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="TOML configuration path (default: marblescape_config.toml).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Perform one image update and exit.",
    )
    parser.add_argument(
        "--list-layers",
        nargs="?",
        const="",
        metavar="SEARCH",
        help="List available WMS layers, optionally filtered by words, then exit.",
    )
    parser.add_argument(
        "--export-layers",
        type=Path,
        metavar="FILE",
        help="Export the complete capabilities layer catalog as JSON, then exit.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration against live capabilities, then exit.",
    )
    parser.add_argument(
        "--print-urls",
        action="store_true",
        help="Print the effective GetMap URL or URLs, then exit.",
    )
    return parser.parse_args(argv)


def prepare_runtime_render_plan(layers):
    """Build a complete render plan from the currently loaded settings."""
    target_ratio = parse_aspect_ratio(ASPECT_RATIO)
    output_width, output_height = get_output_dimensions()
    render_width, render_height, effective_render_scale = get_render_dimensions(
        output_width,
        output_height,
    )
    projection_name, projection, base_extent = get_active_view()
    bbox, _ = calculate_bbox(projection, target_ratio, base_extent)
    resolved_layers = resolve_configured_layers(layers, projection)
    render_mode, requests = build_render_plan(
        resolved_layers,
        projection,
        bbox,
        render_width,
        render_height,
    )
    refresh_latest_times = any(
        layer["uses_latest_time"] for layer in resolved_layers
    )
    time_signature = tuple(
        (layer["name"], layer["time"])
        for layer in resolved_layers
        if layer["uses_latest_time"]
    )
    return (
        output_width,
        output_height,
        render_width,
        render_height,
        effective_render_scale,
        bbox,
        projection_name,
        projection,
        resolved_layers,
        render_mode,
        requests,
        refresh_latest_times,
        time_signature,
    )


def main(argv=None, configuration_loaded=False, status_callback=None):
    global RUN_CONTINUOUSLY

    args = parse_arguments(argv)
    if not configuration_loaded:
        load_configuration(args.config)
    if args.once:
        RUN_CONTINUOUSLY = False

    validate_configuration()
    report_update_status(status_callback, "checking")

    capabilities_xml = download_capabilities()
    layers = parse_layers(capabilities_xml)
    log(f"Discovered {len(layers)} WMS layer(s).")

    if args.list_layers is not None:
        print_layer_catalog(layers, args.list_layers)
    if args.export_layers is not None:
        export_layer_catalog(layers, args.export_layers)
    if args.list_layers is not None or args.export_layers is not None:
        return

    (
        output_width,
        output_height,
        render_width,
        render_height,
        effective_render_scale,
        bbox,
        projection_name,
        projection,
        resolved_layers,
        render_mode,
        requests,
        refresh_latest_times,
        time_signature,
    ) = prepare_runtime_render_plan(layers)

    print_configuration(
        output_width,
        output_height,
        render_width,
        render_height,
        effective_render_scale,
        bbox,
        projection_name,
        projection,
        resolved_layers,
        render_mode,
    )

    if args.print_urls:
        for index, request_spec in enumerate(requests, start=1):
            print(f"URL {index} ({request_spec['label']}):")
            print(request_spec["url"])
        return

    if args.validate_config:
        if (
            render_mode in {"local", "truecolor_black_night"}
            or (render_width, render_height) != (output_width, output_height)
        ):
            try:
                import PIL  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "Configuration needs local composition. Install Pillow with "
                    "'python -m pip install -r requirements.txt'."
                ) from exc
        log("Configuration is valid against the live WMS capabilities.")
        return

    ensure_directories()

    storage_estimate_printed = False
    wallpaper_applied_path = None
    first_cycle = True
    while True:
        if APPLICATION_STOP_EVENT.is_set():
            break

        if CONFIGURATION_RELOAD_EVENT.is_set():
            CONFIGURATION_RELOAD_EVENT.clear()
            previous_configuration = capture_loaded_configuration()
            previous_service = (WMS_URL, WMS_VERSION)
            try:
                load_configuration(args.config)
                validate_configuration()
                if (WMS_URL, WMS_VERSION) != previous_service:
                    capabilities_xml = download_capabilities()
                    layers = parse_layers(capabilities_xml)
                (
                    output_width,
                    output_height,
                    render_width,
                    render_height,
                    effective_render_scale,
                    bbox,
                    projection_name,
                    projection,
                    resolved_layers,
                    render_mode,
                    requests,
                    refresh_latest_times,
                    time_signature,
                ) = prepare_runtime_render_plan(layers)
                ensure_directories()
                storage_estimate_printed = False
                wallpaper_applied_path = None
                first_cycle = True
                log("Applied configuration without restarting MarbleScape.")
                print_configuration(
                    output_width,
                    output_height,
                    render_width,
                    render_height,
                    effective_render_scale,
                    bbox,
                    projection_name,
                    projection,
                    resolved_layers,
                    render_mode,
                )
            except Exception as exc:
                restore_loaded_configuration(previous_configuration)
                log(f"Unable to apply configuration: {exc}")

        cycle_started = time.monotonic()
        force_download = FORCE_UPDATE_EVENT.is_set()
        FORCE_UPDATE_EVENT.clear()
        cycle_next_check = dt.datetime.now() + dt.timedelta(
            minutes=UPDATE_INTERVAL_MINUTES
        )
        report_update_status(status_callback, "checking", cycle_next_check)

        try:
            should_download = True
            pending_signature = time_signature

            if not first_cycle and refresh_latest_times:
                refreshed_xml = download_capabilities()
                refreshed_catalog = parse_layers(refreshed_xml)
                refreshed_layers = resolve_configured_layers(
                    refreshed_catalog,
                    projection,
                    log_resolutions=False,
                )
                refreshed_mode, refreshed_requests = build_render_plan(
                    refreshed_layers,
                    projection,
                    bbox,
                    render_width,
                    render_height,
                )
                refreshed_signature = tuple(
                    (layer["name"], layer["time"])
                    for layer in refreshed_layers
                    if layer["uses_latest_time"]
                )

                if refreshed_signature == time_signature and not force_download:
                    should_download = False
                    log("No newer complete layer timestamp is available.")
                elif refreshed_signature == time_signature:
                    log("Downloading the current layer timestamp again on request.")
                else:
                    details = ", ".join(
                        f"{name}={layer_time}"
                        for name, layer_time in refreshed_signature
                    )
                    log(f"Latest complete layer time advanced: {details}")

                resolved_layers = refreshed_layers
                render_mode = refreshed_mode
                requests = refreshed_requests
                pending_signature = refreshed_signature
                refresh_latest_times = any(
                    layer["uses_latest_time"] for layer in resolved_layers
                )

            if should_download:
                report_update_status(status_callback, "fetching", cycle_next_check)
                installed_path, current_path, downloaded_size = perform_update(
                    render_mode,
                    requests,
                    render_width,
                    render_height,
                    output_width,
                    output_height,
                )
                time_signature = pending_signature
                first_cycle = False
            else:
                installed_path = None
                latest_files = get_latest_image_files()
                current_path = latest_files[0] if latest_files else None
                downloaded_size = 0

            if should_download and not storage_estimate_printed:
                print_storage_estimate(downloaded_size)
                storage_estimate_printed = True

            if installed_path is not None:
                log("Status: new image installed.")
            else:
                log("Status: already up to date.")

            # Also apply the existing image once after startup or a settings
            # reload. If a transient Windows API error occurs, leaving this
            # value unchanged retries the operation during the next cycle.
            if (
                os.name == "nt"
                and SET_WINDOWS_WALLPAPER
                and current_path is not None
                and current_path != wallpaper_applied_path
                and not APPLICATION_STOP_EVENT.is_set()
                and not CONFIGURATION_RELOAD_EVENT.is_set()
            ):
                try:
                    set_windows_wallpaper(current_path)
                    wallpaper_applied_path = current_path
                except Exception as exc:
                    log(f"Windows wallpaper update warning: {exc}")

        except Exception as exc:
            log(f"Update error: {exc}")

        if not RUN_CONTINUOUSLY:
            break

        if not sleep_until_next_cycle(cycle_started, status_callback):
            log("Stop requested.")
            break


# =============================================================================
# WINDOWS NOTIFICATION AREA
# =============================================================================


def create_windows_tray_image():
    from PIL import Image

    available_sizes = (16, 32, 48, 64, 128, 256, 512)
    requested_size = 16
    try:
        requested_size = max(
            ctypes.windll.user32.GetSystemMetrics(49),
            ctypes.windll.user32.GetSystemMetrics(50),
        )
    except (AttributeError, OSError):
        pass

    selected_size = next(
        (size for size in available_sizes if size >= requested_size),
        available_sizes[-1],
    )
    icon_path = (
        RESOURCE_DIR
        / "assets"
        / "icons"
        / f"marblescape_{selected_size}.png"
    )
    if not icon_path.is_file():
        raise FileNotFoundError(f"Tray icon asset not found: {icon_path}")

    with Image.open(icon_path) as image:
        return image.convert("RGBA").copy()


def should_use_windows_tray(argv=None):
    if os.name != "nt":
        return False

    arguments = list(sys.argv[1:] if argv is None else argv)
    non_tray_options = {
        "--version",
        "-h",
        "--help",
        "--once",
        "--list-layers",
        "--export-layers",
        "--validate-config",
        "--print-urls",
    }
    return not any(argument.split("=", 1)[0] in non_tray_options for argument in arguments)


def get_application_launch_arguments(include_current_arguments=False):
    if getattr(sys, "frozen", False):
        arguments = [str(Path(sys.executable).resolve())]
    else:
        launcher = Path(sys.executable).resolve()
        pythonw = launcher.with_name("pythonw.exe")
        if launcher.name.lower() == "python.exe" and pythonw.exists():
            launcher = pythonw
        arguments = [str(launcher), str(Path(__file__).resolve())]

    if include_current_arguments:
        arguments.extend(sys.argv[1:])

    return arguments


def get_windows_startup_command():
    return subprocess.list2cmdline(get_application_launch_arguments())


def replace_toml_section_value(text, section_name, key, value):
    """Replace a TOML value or add the key to an existing section."""
    header_pattern = re.compile(
        rf"(?m)^\s*\[{re.escape(section_name)}\]\s*(?:#[^\r\n]*)?(?:\r?\n|$)"
    )
    header_match = header_pattern.search(text)
    if header_match is None:
        raise ValueError(f"TOML section [{section_name}] was not found.")

    next_section = re.search(r"(?m)^\s*\[", text[header_match.end() :])
    section_end = (
        header_match.end() + next_section.start()
        if next_section is not None
        else len(text)
    )
    section_text = text[header_match.end() : section_end]
    value_pattern = re.compile(
        rf"(?m)^(\s*{re.escape(key)}\s*=\s*)"
        rf"(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^#\r\n]*?)"
        rf"(\s*(?:#.*)?)$"
    )
    value_match = value_pattern.search(section_text)
    if value_match is None:
        newline = "\r\n" if "\r\n" in text else "\n"
        leading_newline = "" if header_match.group(0).endswith(newline) else newline
        insertion = f"{leading_newline}{key} = {json.dumps(value)}{newline}"
        position = header_match.end()
        return text[:position] + insertion + text[position:]

    replacement = value_match.group(1) + json.dumps(value) + value_match.group(3)
    start = header_match.end() + value_match.start()
    end = header_match.end() + value_match.end()
    return text[:start] + replacement + text[end:]


def replace_toml_values(text, updates):
    """Replace multiple existing TOML values while preserving file formatting."""
    updated = text
    for section_name, key, value in updates:
        updated = replace_toml_section_value(updated, section_name, key, value)
    return updated


def current_history_max_age():
    return {
        "years": HISTORY_RETENTION_YEARS,
        "months": HISTORY_RETENTION_MONTHS,
        "days": HISTORY_RETENTION_DAYS,
        "hours": HISTORY_RETENTION_HOURS,
        "minutes": HISTORY_RETENTION_MINUTES,
    }


def projection_selection_updates(projection_name):
    """Select a projection without reusing a bbox from another CRS."""
    if projection_name not in PROJECTIONS:
        raise ValueError("Projection is invalid.")
    return (
        ("view", "projection", projection_name),
        ("view", "preset", "full_earth"),
        ("view", "zoom", DEFAULT_ZOOM),
        # Fitting a global geographic bbox to most screen ratios would extend
        # beyond +/-90 latitude. Crop keeps the request within valid bounds.
        ("view", "fit_mode", "crop" if projection_name == "Geographic" else "fit"),
    )


def preset_configuration_updates(preset_name):
    """Return the view settings belonging to a selectable preset profile."""
    if preset_name not in VIEW_PRESET_PROFILES:
        raise ValueError("View preset has no selectable profile.")
    profile = VIEW_PRESET_PROFILES[preset_name]
    return (
        ("view", "preset", preset_name),
        ("view", "projection", profile["projection"]),
        ("view", "fit_mode", profile["fit_mode"]),
        ("view", "zoom", profile["zoom"]),
    )


def apply_preset_to_configuration(text, preset_name):
    """Apply the complete preset profile to TOML configuration text."""
    profile = VIEW_PRESET_PROFILES[preset_name]
    updated = replace_toml_values(
        text,
        preset_configuration_updates(preset_name),
    )
    return replace_primary_wms_layer_name(
        updated,
        profile["satellite_layer"],
    )


def normalize_settings_form_values(values):
    """Validate settings dialog text and return typed TOML updates."""
    try:
        zoom = float(values["zoom"])
        width = int(values["width"])
        height = int(values["height"])
        update_interval = float(values["update_interval_minutes"])
        max_files = int(values["max_files"])
        years = int(values["years"])
        months = int(values["months"])
        days = int(values["days"])
        hours = int(values["hours"])
        minutes = int(values["minutes"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Numeric settings must contain valid numbers.") from exc

    render_scale = parse_render_scale_setting(values["render_scale"])
    position = str(values["position"]).strip().lower()
    view_preset = str(values["view_preset"]).strip().lower()
    projection_name = str(values.get("projection", PROJECTION)).strip()
    fit_mode = str(values["fit_mode"]).strip().lower()
    aspect_ratio = normalize_aspect_ratio_text(values["aspect_ratio"])
    background_color = normalize_background_color(values["background_color"])
    latest_folder = str(values.get("latest_folder", CUSTOM_LATEST_FOLDER)).strip()
    history_folder = str(values.get("history_folder", CUSTOM_HISTORY_FOLDER)).strip()
    validate_image_folders(
        resolve_script_relative_path(latest_folder)
        if latest_folder else OUTPUT_ROOT / LATEST_DIRECTORY_NAME,
        resolve_script_relative_path(history_folder)
        if history_folder else OUTPUT_ROOT / HISTORY_DIRECTORY_NAME,
    )
    retention_mode = str(values["retention_mode"]).strip().lower()

    if position not in WINDOWS_WALLPAPER_POSITIONS:
        raise ValueError("Wallpaper position is invalid.")
    if view_preset not in VIEW_PRESETS:
        raise ValueError("View preset is invalid.")
    if projection_name not in PROJECTIONS:
        raise ValueError("Projection is invalid.")
    # Existing regional presets remain geographic until projected presets
    # are implemented. Persist the effective projection shown in the UI.
    projection_name = VIEW_PRESETS[view_preset]["projection"] or projection_name
    show_extended = bool(values.get(
        "show_extended_projections", SHOW_EXTENDED_PROJECTIONS
    ))
    if projection_name not in available_projection_choices(show_extended):
        raise ValueError("Enable Show extended projections before selecting this projection.")
    if fit_mode not in {"fit", "crop"}:
        raise ValueError("Fit mode must be 'fit' or 'crop'.")
    if retention_mode not in {"count", "time", "both"}:
        raise ValueError("History retention mode is invalid.")
    if zoom <= 0:
        raise ValueError("Zoom must be greater than zero.")
    if width <= 0:
        raise ValueError("Width must be greater than zero.")
    if height < 0:
        raise ValueError("Height must be zero or greater.")
    if update_interval <= 0:
        raise ValueError("Update interval must be greater than zero.")
    if max_files < 0:
        raise ValueError("Maximum history files cannot be negative.")

    ratio = parse_aspect_ratio(aspect_ratio)
    if height > 0:
        actual_ratio = width / height
        if abs(actual_ratio - ratio) / ratio > 0.005:
            raise ValueError(
                "Width and height do not match the aspect ratio. "
                "Set height to 0 for automatic calculation."
            )

    retention_values = (years, months, days, hours, minutes)
    if any(value < 0 for value in retention_values):
        raise ValueError("History age values cannot be negative.")
    if retention_mode in {"time", "both"} and not any(retention_values):
        raise ValueError(
            "Time-based history retention requires an age greater than zero."
        )

    return (
        ("windows", "set_wallpaper", bool(values["set_wallpaper"])),
        ("windows", "position", position),
        ("view", "preset", view_preset),
        ("view", "projection", projection_name),
        ("view", "show_extended_projections", show_extended),
        ("view", "fit_mode", fit_mode),
        ("view", "zoom", zoom),
        (
            "view",
            "truecolor_black_night",
            bool(values["truecolor_black_night"]),
        ),
        ("output", "width", width),
        ("output", "height", height),
        ("output", "aspect_ratio", aspect_ratio),
        ("output", "render_scale", render_scale),
        ("output", "background_color", background_color),
        ("output", "latest_folder", latest_folder),
        ("service", "update_interval_minutes", update_interval),
        ("history", "enabled", bool(values["history_enabled"])),
        ("history", "folder", history_folder),
        ("history", "retention_mode", retention_mode),
        ("history", "max_files", max_files),
        ("history", "years", years),
        ("history", "months", months),
        ("history", "days", days),
        ("history", "hours", hours),
        ("history", "minutes", minutes),
    )


def replace_primary_wms_layer_name(text, layer_name):
    block_pattern = re.compile(
        r"(?ms)^\s*\[\[layers\]\][^\r\n]*(?:\r?\n|$).*?"
        r"(?=^\s*\[\[layers\]\]|^\s*\[(?!\[)|\Z)"
    )
    name_pattern = re.compile(
        r'(?m)^(\s*name\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?)$'
    )

    for block_match in block_pattern.finditer(text):
        block = block_match.group(0)
        try:
            parsed = tomllib.loads(block)
        except tomllib.TOMLDecodeError:
            continue

        layers = parsed.get("layers", [])
        if not layers:
            continue
        layer = layers[0]
        if layer.get("kind") != "wms" or not layer.get("enabled", True):
            continue

        name_match = name_pattern.search(block)
        if name_match is None:
            raise ValueError("The enabled WMS layer has no name setting.")
        replacement = name_match.group(1) + json.dumps(layer_name) + name_match.group(3)
        updated_block = block[: name_match.start()] + replacement + block[name_match.end() :]
        return text[: block_match.start()] + updated_block + text[block_match.end() :]

    raise ValueError("No enabled [[layers]] entry with kind='wms' was found.")


def update_active_configuration(transform):
    """Update the active TOML file without losing concurrent UI changes."""
    with CONFIGURATION_FILE_LOCK:
        return update_active_configuration_unlocked(transform)


def update_active_configuration_unlocked(transform):
    config_path = ACTIVE_CONFIG_PATH.resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8", newline="") as handle:
        original = handle.read()

    updated = transform(original)
    tomllib.loads(updated)
    if updated == original:
        return False

    temporary_path = config_path.with_name(
        f".{config_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
        os.replace(temporary_path, config_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return True


def make_json_compatible(value):
    """Convert TOML values to a human-readable JSON-compatible snapshot."""
    if isinstance(value, dict):
        return {
            str(key): make_json_compatible(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [make_json_compatible(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return value


def create_settings_backup_payload():
    """Return a lossless JSON backup payload for the active configuration."""
    config_path = ACTIVE_CONFIG_PATH.resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8", newline="") as handle:
        config_text = handle.read()
    parsed_settings = tomllib.loads(config_text)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    created_at = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    return {
        "format": SETTINGS_BACKUP_FORMAT,
        "version": SETTINGS_BACKUP_VERSION,
        "created_at_utc": created_at,
        "configuration_sha256": config_digest,
        "settings": make_json_compatible(parsed_settings),
        "configuration_toml": config_text,
        "windows_startup_enabled": is_windows_startup_enabled(),
    }


def parse_settings_backup_payload(payload):
    """Validate a JSON settings backup and return its restorable values."""
    if not isinstance(payload, dict):
        raise ValueError("The backup root must be a JSON object.")
    if payload.get("format") not in {
        SETTINGS_BACKUP_FORMAT, "earthscape-settings-backup",
        "satscape-settings-backup", "eumetview-settings-backup"
    }:
        raise ValueError("This is not a MarbleScape settings backup.")
    if payload.get("version") != SETTINGS_BACKUP_VERSION:
        raise ValueError(
            f"Unsupported backup version: {payload.get('version')!r}."
        )

    config_text = payload.get("configuration_toml")
    if not isinstance(config_text, str) or not config_text.strip():
        raise ValueError("The backup contains no TOML configuration.")
    if len(config_text.encode("utf-8")) > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("The configuration stored in the backup is too large.")

    expected_digest = payload.get("configuration_sha256")
    actual_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    if (
        not isinstance(expected_digest, str)
        or expected_digest.lower() != actual_digest
    ):
        raise ValueError("The backup configuration checksum is invalid.")

    try:
        parsed_settings = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"The backup contains invalid TOML: {exc}") from exc

    settings_snapshot = payload.get("settings")
    if settings_snapshot != make_json_compatible(parsed_settings):
        raise ValueError(
            "The readable settings snapshot does not match the configuration."
        )

    startup_enabled = payload.get("windows_startup_enabled")
    if not isinstance(startup_enabled, bool):
        raise ValueError("The backup contains an invalid Windows startup setting.")

    return config_text, startup_enabled


def export_settings_backup(output_path):
    """Write the active settings to an atomic JSON backup file."""
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".json":
        output_path = output_path.with_suffix(".json")
    output_path = output_path.resolve()
    if not output_path.parent.exists():
        raise FileNotFoundError(
            f"Backup folder does not exist: {output_path.parent}"
        )

    payload = create_settings_backup_payload()
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return output_path


def import_settings_backup(input_path):
    """Read and validate a JSON settings backup without changing live state."""
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Backup file not found: {input_path}")
    if input_path.stat().st_size > MAX_SETTINGS_BACKUP_FILE_BYTES:
        raise ValueError("The backup file is too large.")

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the JSON backup: {exc}") from exc

    return parse_settings_backup_payload(payload)


def get_selected_wms_layer_name():
    for entry in LAYER_CONFIG:
        if (
            str(entry.get("kind", "")).lower() == "wms"
            and entry.get("enabled", True)
            and float(entry.get("opacity", 1.0)) > 0
        ):
            return str(entry.get("name", ""))
    return None


def is_windows_startup_enabled():
    if os.name != "nt":
        return False

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            WINDOWS_RUN_KEY,
            access=winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(key, WINDOWS_RUN_VALUE_NAME)
    except FileNotFoundError:
        return False

    return True


def migrate_legacy_windows_startup():
    """Retarget recognized legacy startup entries to MarbleScape."""
    if os.name != "nt":
        return
    import winreg

    for legacy_name in ("EarthScape", "SatScape", "EUMETView"):
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                access=winreg.KEY_READ | winreg.KEY_SET_VALUE,
            ) as key:
                command, _kind = winreg.QueryValueEx(key, legacy_name)
                command = str(command).lower()
                if str(SCRIPT_DIR).lower() + "\\" not in command:
                    continue
                if not any(name in command for name in (
                    "eumetview.exe", "eumetview_download.py",
                    "satscape.exe", "satscape_download.py",
                    "earthscape.exe", "earthscape_download.py",
                )):
                    continue
                winreg.SetValueEx(
                    key, WINDOWS_RUN_VALUE_NAME, 0, winreg.REG_SZ,
                    get_windows_startup_command(),
                )
                winreg.DeleteValue(key, legacy_name)
        except FileNotFoundError:
            continue


def set_windows_startup_enabled(enabled):
    if os.name != "nt":
        return

    import winreg

    if enabled:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            WINDOWS_RUN_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                WINDOWS_RUN_VALUE_NAME,
                0,
                winreg.REG_SZ,
                get_windows_startup_command(),
            )
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            WINDOWS_RUN_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, WINDOWS_RUN_VALUE_NAME)
    except FileNotFoundError:
        pass


def run_application(
    argv=None,
    pause_on_error=True,
    configuration_loaded=False,
    status_callback=None,
):
    exit_code = 0

    try:
        main(
            argv,
            configuration_loaded=configuration_loaded,
            status_callback=status_callback,
        )
    except KeyboardInterrupt:
        log("Stopped by user.")
        exit_code = 130
    except Exception as exc:
        log(f"Fatal error: {exc}")
        exit_code = 1
    finally:
        can_pause = sys.stdin is not None and getattr(sys.stdin, "isatty", lambda: False)()
        if (
            os.name == "nt"
            and pause_on_error
            and WINDOWS_PAUSE_ON_EXIT
            and exit_code != 0
            and can_pause
        ):
            try:
                input("\nPress Enter to exit...")
            except (EOFError, OSError):
                pass

    return exit_code


def run_with_windows_tray(argv=None):
    try:
        import pystray
    except ImportError:
        log(
            "Windows tray icon unavailable. Install dependencies with "
            "'python -m pip install -r requirements.txt'."
        )
        return run_application(argv)

    try:
        tray_args = parse_arguments(argv)
        load_configuration(tray_args.config)
        migrate_legacy_windows_startup()
    except Exception as exc:
        log(f"Fatal error: {exc}")
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                str(exc),
                "MarbleScape configuration error",
                0x10,
            )
        except Exception:
            pass
        return 1

    APPLICATION_STOP_EVENT.clear()
    FORCE_UPDATE_EVENT.clear()
    CONFIGURATION_RELOAD_EVENT.clear()
    result = {"exit_code": 0}
    settings_dialog_lock = threading.Lock()
    settings_dialog_state = {"open": False}
    tray_status_lock = threading.Lock()
    tray_status = {
        "state": "starting",
        "next_check": None,
    }

    def get_tray_status_snapshot():
        with tray_status_lock:
            return tray_status["state"], tray_status["next_check"]

    def format_next_check_status():
        state, next_check = get_tray_status_snapshot()
        if next_check is not None:
            return next_check.strftime("%Y-%m-%d %H:%M:%S")
        if state == "checking":
            return "Checking now"
        return "Calculating"

    def open_output_folder(icon, item):
        del item
        try:
            LATEST_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LATEST_DIR.resolve()))
        except Exception as exc:
            try:
                icon.notify(str(exc), "Unable to open image folder")
            except Exception:
                log(f"Unable to open image folder: {exc}")

    def exit_application(icon, item):
        del item
        APPLICATION_STOP_EVENT.set()
        icon.stop()

    def show_tray_error(icon, title, error):
        try:
            icon.notify(str(error), title)
        except Exception:
            log(f"{title}: {error}")

    def restart_from_tray(icon):
        try:
            restart_environment = os.environ.copy()
            if getattr(sys, "frozen", False):
                restart_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

            subprocess.Popen(
                [
                    *get_application_launch_arguments(include_current_arguments=True),
                    "--config",
                    str(ACTIVE_CONFIG_PATH.resolve()),
                ],
                cwd=str(SCRIPT_DIR),
                close_fds=True,
                env=restart_environment,
            )
        except Exception as exc:
            show_tray_error(icon, "Unable to restart MarbleScape", exc)
            return False

        APPLICATION_STOP_EVENT.set()
        icon.stop()
        return True

    def restart_application(icon, item):
        del item
        restart_from_tray(icon)

    def values_match(current, expected):
        if (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
        ):
            return math.isclose(float(current), float(expected), rel_tol=1e-9)
        return current == expected

    def request_runtime_configuration_reload(icon):
        CONFIGURATION_RELOAD_EVENT.set()
        FORCE_UPDATE_EVENT.set()
        try:
            icon.update_menu()
        except Exception as exc:
            log(f"Tray menu refresh warning: {exc}")

    def apply_configuration_updates(icon, error_title, updates):
        try:
            changed = update_active_configuration(
                lambda text: replace_toml_values(text, updates)
            )
        except Exception as exc:
            show_tray_error(icon, error_title, exc)
            return False

        if changed:
            request_runtime_configuration_reload(icon)
        else:
            icon.update_menu()
        return changed

    def apply_settings_backup(icon, config_text, startup_enabled):
        startup_before_import = is_windows_startup_enabled()
        startup_changed = startup_enabled != startup_before_import
        if startup_changed:
            set_windows_startup_enabled(startup_enabled)

        try:
            changed = update_active_configuration(lambda _text: config_text)
        except Exception as config_error:
            if startup_changed:
                try:
                    set_windows_startup_enabled(startup_before_import)
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"{config_error} Windows startup rollback also "
                        f"failed: {rollback_error}"
                    ) from config_error
            raise

        if changed:
            request_runtime_configuration_reload(icon)
        else:
            icon.update_menu()
        return changed

    def make_setting_action(section_name, key, value, current_value, extra_updates=()):
        def select_setting(icon, item):
            del item
            if values_match(current_value(), value) and not extra_updates:
                return
            updates = ((section_name, key, value), *extra_updates)
            apply_configuration_updates(icon, "Unable to change setting", updates)

        return select_setting

    def make_setting_checked(value, current_value):
        def setting_checked(item):
            del item
            return values_match(current_value(), value)

        return setting_checked

    def make_boolean_toggle(section_name, key, current_value):
        def toggle_setting(icon, item):
            del item
            apply_configuration_updates(
                icon,
                "Unable to change setting",
                ((section_name, key, not bool(current_value())),),
            )

        return toggle_setting

    def make_projection_action(projection_name):
        def select_projection(icon, item):
            del item
            apply_configuration_updates(
                icon,
                "Unable to change projection",
                projection_selection_updates(projection_name),
            )

        return select_projection

    def make_preset_action(preset_name):
        def select_preset(icon, item):
            del item
            try:
                changed = update_active_configuration(
                    lambda text: apply_preset_to_configuration(text, preset_name)
                )
            except Exception as exc:
                show_tray_error(icon, "Unable to change view preset", exc)
                return
            if changed:
                request_runtime_configuration_reload(icon)

        return select_preset

    def make_preset_checked(preset_name):
        def preset_checked(item):
            del item
            return VIEW_PRESET == preset_name

        return preset_checked

    def make_layer_action(layer_name):
        def select_layer(icon, item):
            del item
            if get_selected_wms_layer_name() == layer_name:
                return
            try:
                changed = update_active_configuration(
                    lambda text: replace_primary_wms_layer_name(text, layer_name)
                )
            except Exception as exc:
                show_tray_error(icon, "Unable to change satellite layer", exc)
                return
            if changed:
                request_runtime_configuration_reload(icon)

        return select_layer

    def make_layer_checked(layer_name):
        def layer_checked(item):
            del item
            return get_selected_wms_layer_name() == layer_name

        return layer_checked

    def make_output_size_action(width, height):
        aspect_ratio = aspect_ratio_for_dimensions(width, height)
        return make_setting_action(
            "output",
            "width",
            width,
            lambda: WIDTH,
            extra_updates=(
                ("output", "height", 0),
                ("output", "aspect_ratio", aspect_ratio),
            ),
        )

    def make_output_size_checked(width, height):
        def output_size_checked(item):
            del item
            try:
                actual_width, actual_height = get_output_dimensions()
            except (TypeError, ValueError):
                return False
            return actual_width == width and actual_height == height

        return output_size_checked

    def make_aspect_ratio_action(aspect_ratio):
        return make_setting_action(
            "output",
            "aspect_ratio",
            aspect_ratio,
            lambda: ASPECT_RATIO,
            extra_updates=(("output", "height", 0),),
        )

    def make_aspect_ratio_checked(aspect_ratio):
        def aspect_ratio_checked(item):
            del item
            try:
                return math.isclose(
                    parse_aspect_ratio(ASPECT_RATIO),
                    parse_aspect_ratio(aspect_ratio),
                    rel_tol=1e-9,
                )
            except (TypeError, ValueError):
                return False

        return aspect_ratio_checked

    def make_history_age_action(age):
        updates = tuple(("history", key, value) for key, value in age.items())

        def select_history_age(icon, item):
            del item
            if current_history_max_age() == age:
                return
            apply_configuration_updates(icon, "Unable to change history age", updates)

        return select_history_age

    def make_history_age_checked(age):
        def history_age_checked(item):
            del item
            return current_history_max_age() == age

        return history_age_checked

    def run_settings_dialog(icon):
        import tkinter as tk
        from tkinter import colorchooser, filedialog, messagebox, ttk

        root = tk.Tk()
        root.withdraw()
        root.title("MarbleScape settings")
        root.resizable(True, True)

        def number_text(value):
            numeric = float(value)
            return str(int(numeric)) if numeric.is_integer() else str(numeric)

        def prepare_choice_lookup(choices, current_value, custom_label):
            label_to_value = dict(choices)
            current_label = next(
                (
                    label
                    for label, value in choices
                    if value == current_value
                ),
                None,
            )
            if current_label is None:
                current_label = custom_label
                label_to_value[current_label] = current_value
            return label_to_value, current_label

        selected_wms_layer = get_selected_wms_layer_name()
        saved_layer_state = {"value": selected_wms_layer}
        preset_label_to_value, current_preset_label = prepare_choice_lookup(
            VIEW_PRESET_MENU_CHOICES,
            VIEW_PRESET,
            "Custom (configured bounding box)",
        )
        layer_label_to_value, current_layer_label = prepare_choice_lookup(
            SATELLITE_LAYER_MENU_CHOICES,
            selected_wms_layer,
            (
                f"Custom layer ({selected_wms_layer})"
                if selected_wms_layer
                else "No enabled WMS layer"
            ),
        )
        resolution_label_to_size = {
            f"{group_label} | {label}": (width, height)
            for group_label, choices in OUTPUT_SIZE_MENU_GROUPS
            for label, width, height in choices
        }
        resolution_label_by_size = {
            size: label
            for label, size in resolution_label_to_size.items()
        }
        aspect_label_to_value = {
            f"{group_label} | {label}": aspect_ratio
            for group_label, choices in ASPECT_RATIO_MENU_GROUPS
            for label, aspect_ratio in choices
        }
        render_quality_label_to_value = dict(RENDER_QUALITY_MENU_CHOICES)
        current_output_size = get_output_dimensions()
        current_resolution_label = resolution_label_by_size.get(
            current_output_size,
            "Custom",
        )
        current_aspect_label = next(
            (
                label
                for label, aspect_ratio in aspect_label_to_value.items()
                if math.isclose(
                    parse_aspect_ratio(ASPECT_RATIO),
                    parse_aspect_ratio(aspect_ratio),
                    rel_tol=1e-9,
                )
            ),
            "Custom",
        )
        current_render_quality = get_render_scale_setting()
        current_render_quality_label = next(
            (
                label
                for label, value in RENDER_QUALITY_MENU_CHOICES
                if (
                    value == current_render_quality
                    if isinstance(value, str)
                    else (
                        not isinstance(current_render_quality, str)
                        and math.isclose(
                            float(value),
                            float(current_render_quality),
                            rel_tol=1e-9,
                        )
                    )
                )
            ),
            "Custom",
        )

        variables = {
            "set_wallpaper": tk.BooleanVar(value=SET_WINDOWS_WALLPAPER),
            "position": tk.StringVar(value=WINDOWS_WALLPAPER_POSITION),
            "start_with_windows": tk.BooleanVar(
                value=is_windows_startup_enabled()
            ),
            "view_preset": tk.StringVar(value=current_preset_label),
            "projection": tk.StringVar(value=get_active_view()[0]),
            "show_extended_projections": tk.BooleanVar(
                value=SHOW_EXTENDED_PROJECTIONS
            ),
            "satellite_layer": tk.StringVar(value=current_layer_label),
            "fit_mode": tk.StringVar(value=VIEW_MODE),
            "zoom": tk.StringVar(value=number_text(ZOOM)),
            "truecolor_black_night": tk.BooleanVar(
                value=TRUECOLOR_BLACK_NIGHT
            ),
            "width": tk.StringVar(value=str(WIDTH)),
            "height": tk.StringVar(value=str(0 if HEIGHT is None else HEIGHT)),
            "aspect_ratio": tk.StringVar(value=str(ASPECT_RATIO)),
            "resolution_preset": tk.StringVar(
                value=current_resolution_label
            ),
            "aspect_ratio_preset": tk.StringVar(
                value=current_aspect_label
            ),
            "render_quality_preset": tk.StringVar(
                value=current_render_quality_label
            ),
            "render_scale": tk.StringVar(
                value=(
                    "auto"
                    if RENDER_SCALE_AUTOMATIC
                    else number_text(RENDER_SCALE)
                )
            ),
            "background_color": tk.StringVar(
                value=normalize_background_color(BACKGROUND_COLOR)
            ),
            "update_interval_minutes": tk.StringVar(
                value=number_text(UPDATE_INTERVAL_MINUTES)
            ),
            "history_enabled": tk.BooleanVar(value=ENABLE_HISTORY),
            "retention_mode": tk.StringVar(value=HISTORY_RETENTION_MODE),
            "max_files": tk.StringVar(value=str(HISTORY_MAX_FILES)),
            "years": tk.StringVar(value=str(HISTORY_RETENTION_YEARS)),
            "months": tk.StringVar(value=str(HISTORY_RETENTION_MONTHS)),
            "days": tk.StringVar(value=str(HISTORY_RETENTION_DAYS)),
            "hours": tk.StringVar(value=str(HISTORY_RETENTION_HOURS)),
            "minutes": tk.StringVar(value=str(HISTORY_RETENTION_MINUTES)),
        }
        status_variables = {
            "activity": tk.StringVar(),
            "latest_images": tk.StringVar(),
            "current_image_size": tk.StringVar(),
            "estimated_history_images": tk.StringVar(),
            "estimated_maximum_total": tk.StringVar(),
            "total_storage_estimate": tk.StringVar(),
            "currently_used_disk_space": tk.StringVar(),
            "next_check": tk.StringVar(),
        }
        variables["latest_folder"] = tk.StringVar(value=CUSTOM_LATEST_FOLDER)
        variables["history_folder"] = tk.StringVar(value=CUSTOM_HISTORY_FOLDER)

        container = ttk.Frame(root, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        notebook = ttk.Notebook(container)
        notebook.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        scroll_pages = {}

        def add_settings_tab(title):
            page = ttk.Frame(notebook)
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            canvas = tk.Canvas(
                page, highlightthickness=0, borderwidth=0,
                background=ttk.Style(root).lookup("TFrame", "background") or "#f0f0f0",
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            content = ttk.Frame(canvas, padding=(0, 8, 8, 0))
            content.columnconfigure(0, weight=1)
            window = canvas.create_window(0, 0, window=content, anchor="nw")

            def update_scroll_region(_event=None):
                width = max(1, canvas.winfo_width())
                height = content.winfo_reqheight()
                canvas.itemconfigure(window, width=width)
                canvas.configure(scrollregion=(0, 0, width, height))
                if height > canvas.winfo_height():
                    scrollbar.grid(row=0, column=1, sticky="ns")
                else:
                    scrollbar.grid_remove()
                    canvas.yview_moveto(0)

            content.bind("<Configure>", update_scroll_region)
            canvas.bind("<Configure>", update_scroll_region)
            notebook.add(page, text=title)
            scroll_pages[str(page)] = (canvas, content)
            return content

        general_tab = add_settings_tab("General")
        image_tab = add_settings_tab("Image")
        history_tab = add_settings_tab("History & Storage")
        backup_tab = add_settings_tab("Backup")

        def scroll_settings(event):
            selected = scroll_pages.get(notebook.select())
            if selected is None:
                return
            canvas, _content = selected
            # Only scroll the selected page, not the fixed footer or other windows.
            widget = event.widget
            while widget is not None and widget is not canvas:
                widget = getattr(widget, "master", None)
            if widget is None:
                return
            if canvas.yview() == (0.0, 1.0):
                return "break"
            if getattr(event, "num", None) in (4, 5):
                units = -1 if event.num == 4 else 1
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return
                units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            canvas.yview_scroll(units * 3, "units")
            return "break"

        def reveal_focused_setting(event):
            selected = scroll_pages.get(notebook.select())
            if selected is None:
                return
            canvas, content = selected
            widget = event.widget
            ancestor = widget
            while ancestor is not None and ancestor is not content:
                ancestor = getattr(ancestor, "master", None)
            if ancestor is None:
                return
            top = widget.winfo_rooty() - content.winfo_rooty()
            bottom = top + widget.winfo_height()
            visible_top = canvas.canvasy(0)
            visible_height = canvas.winfo_height()
            if top < visible_top:
                canvas.yview_moveto(max(0, top - 8) / max(1, content.winfo_height()))
            elif bottom > visible_top + visible_height:
                canvas.yview_moveto((bottom + 8 - visible_height) / max(1, content.winfo_height()))

        scroll_tag = "MarbleScapeSettingsScroll"
        root.bind_class(scroll_tag, "<MouseWheel>", scroll_settings)
        root.bind_class(scroll_tag, "<Button-4>", scroll_settings)
        root.bind_class(scroll_tag, "<Button-5>", scroll_settings)
        root.bind("<FocusIn>", reveal_focused_setting)

        def add_entry(parent, row, label, variable, width=16):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w"
            )
            entry = ttk.Entry(parent, textvariable=variable, width=width)
            entry.grid(row=row, column=1, pady=3, sticky="ew")
            return entry

        def add_combo(parent, row, label, variable, choices, width=18):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w"
            )
            combo = ttk.Combobox(
                parent,
                textvariable=variable,
                values=choices,
                state="readonly",
                width=width,
            )
            combo.grid(row=row, column=1, pady=3, sticky="ew")
            return combo

        def add_folder_picker(parent, row, label, key, default_folder):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w"
            )
            field = ttk.Frame(parent)
            field.grid(row=row, column=1, pady=3, sticky="ew")
            field.columnconfigure(0, weight=1)
            ttk.Entry(field, textvariable=variables[key], width=28).grid(
                row=0, column=0, padx=(0, 5), sticky="ew"
            )

            def choose_folder():
                configured = variables[key].get().strip()
                initial = (
                    resolve_script_relative_path(configured)
                    if configured else default_folder
                )
                while not initial.is_dir() and initial != initial.parent:
                    initial = initial.parent
                selected = filedialog.askdirectory(
                    parent=root,
                    title=label,
                    initialdir=str(initial),
                    mustexist=False,
                )
                if selected:
                    variables[key].set(selected)

            ttk.Button(field, text="Choose...", command=choose_folder).grid(
                row=0, column=1
            )
            ttk.Label(
                parent, text="Empty = default; relative paths use the script folder."
            ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 3))

        def choose_background_color():
            try:
                initial_color = normalize_background_color(
                    variables["background_color"].get()
                )
            except ValueError as exc:
                messagebox.showerror("Invalid color", str(exc), parent=root)
                return

            selected = colorchooser.askcolor(
                color=initial_color,
                title="Choose background color",
                parent=root,
            )[1]
            if selected:
                variables["background_color"].set(
                    normalize_background_color(selected)
                )

        output_preset_update = {"active": False}

        def refresh_output_preset_labels(*_args):
            if output_preset_update["active"]:
                return
            output_preset_update["active"] = True
            try:
                try:
                    width = int(variables["width"].get())
                    configured_height = int(variables["height"].get())
                    ratio = parse_aspect_ratio(
                        variables["aspect_ratio"].get()
                    )
                    effective_height = (
                        round(width / ratio)
                        if configured_height == 0
                        else configured_height
                    )
                    resolution_label = resolution_label_by_size.get(
                        (width, effective_height),
                        "Custom",
                    )
                except (TypeError, ValueError, OverflowError):
                    resolution_label = "Custom"

                try:
                    current_ratio = parse_aspect_ratio(
                        variables["aspect_ratio"].get()
                    )
                    aspect_label = next(
                        (
                            label
                            for label, aspect_ratio
                            in aspect_label_to_value.items()
                            if math.isclose(
                                current_ratio,
                                parse_aspect_ratio(aspect_ratio),
                                rel_tol=1e-9,
                            )
                        ),
                        "Custom",
                    )
                except (TypeError, ValueError, OverflowError):
                    aspect_label = "Custom"

                variables["resolution_preset"].set(resolution_label)
                variables["aspect_ratio_preset"].set(aspect_label)
            finally:
                output_preset_update["active"] = False

        def select_resolution_preset(_event=None):
            selected = variables["resolution_preset"].get()
            if selected == "Custom":
                return
            width, height = resolution_label_to_size[selected]
            output_preset_update["active"] = True
            try:
                variables["width"].set(str(width))
                variables["height"].set("0")
                variables["aspect_ratio"].set(
                    aspect_ratio_for_dimensions(width, height)
                )
            finally:
                output_preset_update["active"] = False
            refresh_output_preset_labels()

        def select_aspect_ratio_preset(_event=None):
            selected = variables["aspect_ratio_preset"].get()
            if selected == "Custom":
                return
            output_preset_update["active"] = True
            try:
                variables["aspect_ratio"].set(
                    aspect_label_to_value[selected]
                )
                variables["height"].set("0")
            finally:
                output_preset_update["active"] = False
            refresh_output_preset_labels()

        render_quality_update = {"active": False}

        def refresh_render_quality_preset(*_args):
            if render_quality_update["active"]:
                return
            try:
                current_value = parse_render_scale_setting(
                    variables["render_scale"].get()
                )
            except ValueError:
                variables["render_quality_preset"].set("Custom")
                return
            current_label = next(
                (
                    label
                    for label, value in RENDER_QUALITY_MENU_CHOICES
                    if (
                        value == current_value
                        if isinstance(value, str)
                        else (
                            not isinstance(current_value, str)
                            and math.isclose(
                                float(value),
                                float(current_value),
                                rel_tol=1e-9,
                            )
                        )
                    )
                ),
                "Custom",
            )
            variables["render_quality_preset"].set(current_label)

        def select_render_quality_preset(_event=None):
            selected = variables["render_quality_preset"].get()
            if selected == "Custom":
                return
            render_quality_update["active"] = True
            try:
                value = render_quality_label_to_value[selected]
                variables["render_scale"].set(
                    value if isinstance(value, str) else number_text(value)
                )
            finally:
                render_quality_update["active"] = False
            refresh_render_quality_preset()

        def choose_backup_export():
            selected_path = filedialog.asksaveasfilename(
                parent=root,
                title="Export MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                initialfile=(
                    "marblescape-settings-"
                    f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.json"
                ),
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                saved_path = export_settings_backup(selected_path)
            except Exception as exc:
                messagebox.showerror(
                    "Unable to export backup",
                    str(exc),
                    parent=root,
                )
                return
            messagebox.showinfo(
                "Settings backup exported",
                f"Backup saved to:\n{saved_path}",
                parent=root,
            )

        def choose_backup_import():
            selected_path = filedialog.askopenfilename(
                parent=root,
                title="Import MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                config_text, startup_enabled = import_settings_backup(
                    selected_path
                )
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return

            confirmed = messagebox.askyesno(
                "Import settings backup",
                "Importing this backup replaces the current configuration "
                "and applies it immediately. Continue?",
                parent=root,
                icon="warning",
            )
            if not confirmed:
                return

            try:
                apply_settings_backup(icon, config_text, startup_enabled)
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return
            root.destroy()

        def select_projection(event=None):
            del event
            updates = dict(
                (key, value)
                for _section, key, value in projection_selection_updates(
                    variables["projection"].get()
                )
            )
            variables["view_preset"].set(next(
                label for label, value in preset_label_to_value.items()
                if value == "full_earth"
            ))
            variables["zoom"].set(str(updates["zoom"]))
            variables["fit_mode"].set(updates["fit_mode"])

        def refresh_projection_choices():
            choices = available_projection_choices(
                variables["show_extended_projections"].get()
            )
            projection_combo.configure(values=choices)
            if variables["projection"].get() not in choices:
                variables["projection"].set("GEOS: MSG FES, MTG FD")
                select_projection()

        def select_view_preset(event=None):
            del event
            preset_name = preset_label_to_value[variables["view_preset"].get()]
            if preset_name not in VIEW_PRESET_PROFILES:
                return
            profile = VIEW_PRESET_PROFILES[preset_name]
            variables["projection"].set(profile["projection"])
            variables["fit_mode"].set(profile["fit_mode"])
            variables["zoom"].set(number_text(profile["zoom"]))
            layer_label = next(
                label for label, layer_name in layer_label_to_value.items()
                if layer_name == profile["satellite_layer"]
            )
            variables["satellite_layer"].set(layer_label)

        preset_frame = ttk.LabelFrame(general_tab, text="Preset", padding=8)
        preset_frame.grid(
            row=0,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="nsew",
        )
        preset_combo = add_combo(
            preset_frame,
            0,
            "Preset",
            variables["view_preset"],
            tuple(preset_label_to_value),
            width=30,
        )
        preset_combo.bind("<<ComboboxSelected>>", select_view_preset)
        ttk.Label(
            preset_frame,
            text=(
                "Selecting a preset sets its satellite layer, projection, "
                "fit mode, and zoom."
            ),
        ).grid(row=1, column=0, columnspan=2, pady=(3, 0), sticky="w")

        wallpaper_frame = ttk.LabelFrame(general_tab, text="Wallpaper", padding=8)
        wallpaper_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            wallpaper_frame,
            text="Set wallpaper automatically",
            variable=variables["set_wallpaper"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        add_combo(
            wallpaper_frame,
            1,
            "Position",
            variables["position"],
            tuple(WINDOWS_WALLPAPER_POSITIONS),
        )
        ttk.Checkbutton(
            wallpaper_frame,
            text="Start with Windows",
            variable=variables["start_with_windows"],
        ).grid(row=2, column=0, columnspan=2, pady=3, sticky="w")

        view_frame = ttk.LabelFrame(image_tab, text="View", padding=8)
        view_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            view_frame,
            0,
            "Satellite layer",
            variables["satellite_layer"],
            tuple(layer_label_to_value),
            width=30,
        )
        projection_combo = add_combo(
            view_frame, 1, "Projection", variables["projection"],
            available_projection_choices(SHOW_EXTENDED_PROJECTIONS), width=30,
        )
        projection_combo.bind("<<ComboboxSelected>>", select_projection)
        add_combo(view_frame, 2, "Fit mode", variables["fit_mode"], ("fit", "crop"))
        add_entry(view_frame, 3, "Zoom", variables["zoom"])
        ttk.Checkbutton(
            view_frame,
            text="Black TrueColor night side",
            variable=variables["truecolor_black_night"],
        ).grid(row=4, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            view_frame,
            text="Show extended projections",
            variable=variables["show_extended_projections"],
            command=refresh_projection_choices,
        ).grid(row=5, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(
            view_frame,
            text="Coverage depends on the selected satellite layer.",
        ).grid(row=6, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(
            view_frame,
            text="Changing projection resets the view to Full Earth.\n"
                 "Regional presets use Geographic.",
        ).grid(row=7, column=0, columnspan=2, pady=3, sticky="w")

        output_frame = ttk.LabelFrame(image_tab, text="Output", padding=8)
        output_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        resolution_combo = add_combo(
            output_frame,
            0,
            "Resolution preset",
            variables["resolution_preset"],
            (*resolution_label_to_size, "Custom"),
            width=38,
        )
        resolution_combo.bind(
            "<<ComboboxSelected>>",
            select_resolution_preset,
        )
        aspect_combo = add_combo(
            output_frame,
            1,
            "Aspect ratio preset",
            variables["aspect_ratio_preset"],
            (*aspect_label_to_value, "Custom"),
            width=38,
        )
        aspect_combo.bind(
            "<<ComboboxSelected>>",
            select_aspect_ratio_preset,
        )
        add_entry(output_frame, 2, "Width", variables["width"])
        add_entry(output_frame, 3, "Height (0 = auto)", variables["height"])
        add_entry(output_frame, 4, "Aspect ratio", variables["aspect_ratio"])
        render_quality_combo = add_combo(
            output_frame,
            5,
            "Render quality preset",
            variables["render_quality_preset"],
            (*render_quality_label_to_value, "Custom"),
            width=30,
        )
        render_quality_combo.bind(
            "<<ComboboxSelected>>",
            select_render_quality_preset,
        )
        add_entry(
            output_frame,
            6,
            "Render quality factor",
            variables["render_scale"],
        )
        ttk.Label(output_frame, text="Background color").grid(
            row=7, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        color_frame = ttk.Frame(output_frame)
        color_frame.grid(row=7, column=1, pady=3, sticky="ew")
        ttk.Entry(
            color_frame,
            textvariable=variables["background_color"],
            width=11,
        ).grid(row=0, column=0, padx=(0, 5), sticky="ew")
        ttk.Button(
            color_frame,
            text="Choose...",
            command=choose_background_color,
        ).grid(row=0, column=1)
        for key in ("width", "height", "aspect_ratio"):
            variables[key].trace_add("write", refresh_output_preset_labels)
        add_folder_picker(
            output_frame, 8, "Custom latest folder", "latest_folder",
            OUTPUT_ROOT / LATEST_DIRECTORY_NAME,
        )
        variables["render_scale"].trace_add(
            "write",
            refresh_render_quality_preset,
        )

        update_frame = ttk.LabelFrame(general_tab, text="Updates", padding=8)
        update_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        add_entry(
            update_frame,
            0,
            "Interval (minutes)",
            variables["update_interval_minutes"],
        )

        def request_picture_from_settings():
            if not force_loading_is_enabled(None):
                return
            force_picture_button.state(["disabled"])
            force_loading_new_picture(icon, None)

        force_picture_button = ttk.Button(
            update_frame,
            text="Force loading new picture",
            command=request_picture_from_settings,
        )
        force_picture_button.grid(
            row=1, column=0, columnspan=2, pady=(8, 3), sticky="w"
        )

        history_frame = ttk.LabelFrame(history_tab, text="History", padding=8)
        history_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            history_frame,
            text="Enable history",
            variable=variables["history_enabled"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        add_combo(
            history_frame,
            1,
            "Retention mode",
            variables["retention_mode"],
            ("count", "time", "both"),
        )
        add_entry(history_frame, 2, "Maximum files", variables["max_files"])

        age_frame = ttk.Frame(history_frame)
        age_frame.grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="ew")
        for column, key in enumerate(("years", "months", "days", "hours", "minutes")):
            ttk.Label(age_frame, text=key.capitalize()).grid(
                row=0, column=column, padx=3, sticky="w"
            )
            ttk.Entry(age_frame, textvariable=variables[key], width=8).grid(
                row=1, column=column, padx=3, sticky="ew"
            )

        add_folder_picker(
            history_frame, 4, "Custom history folder", "history_folder",
            OUTPUT_ROOT / HISTORY_DIRECTORY_NAME,
        )

        status_frame = ttk.LabelFrame(
            history_tab,
            text="Status and storage",
            padding=8,
        )
        status_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="nsew",
        )
        status_rows = (
            ("Latest images", "latest_images"),
            ("Current image size", "current_image_size"),
            ("Estimated history images", "estimated_history_images"),
            ("Estimated maximum total", "estimated_maximum_total"),
            ("Total storage estimate", "total_storage_estimate"),
            (
                "Currently used disk space (latest + history)",
                "currently_used_disk_space",
            ),
            ("Next check", "next_check"),
        )
        for row, (label, key) in enumerate(status_rows):
            ttk.Label(status_frame, text=label).grid(
                row=row,
                column=0,
                padx=(0, 12),
                pady=2,
                sticky="w",
            )
            ttk.Label(
                status_frame,
                textvariable=status_variables[key],
            ).grid(row=row, column=1, pady=2, sticky="w")

        def refresh_status_section():
            force_picture_button.state(
                ["!disabled"] if force_loading_is_enabled(None) else ["disabled"]
            )
            storage = get_storage_status()
            current_size = storage["current_image_size"]
            estimated_bytes = storage["estimated_total_bytes"]

            status_variables["activity"].set(
                update_activity_status_text(None)
            )
            status_variables["latest_images"].set(
                str(storage["latest_images"])
            )
            status_variables["current_image_size"].set(
                format_bytes(current_size)
                if current_size is not None
                else "No image available"
            )
            status_variables["estimated_history_images"].set(
                str(storage["estimated_history_images"])
            )
            status_variables["estimated_maximum_total"].set(
                f'{storage["estimated_maximum_images"]} images'
            )
            status_variables["total_storage_estimate"].set(
                format_bytes(estimated_bytes)
                if estimated_bytes is not None
                else "Unavailable until the first image"
            )
            status_variables["currently_used_disk_space"].set(
                format_disk_usage(storage["used_bytes"])
            )
            status_variables["next_check"].set(
                format_next_check_status()
            )
            root.after(1000, refresh_status_section)

        refresh_status_section()

        backup_frame = ttk.LabelFrame(backup_tab, text="Backup", padding=8)
        backup_frame.grid(
            row=0,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="nsew",
        )
        ttk.Label(
            backup_frame,
            text="Export or restore the complete saved configuration.",
        ).grid(row=0, column=0, padx=(0, 12), sticky="w")
        ttk.Button(
            backup_frame,
            text="Export...",
            command=choose_backup_export,
        ).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(
            backup_frame,
            text="Import...",
            command=choose_backup_import,
        ).grid(row=0, column=2)

        button_frame = ttk.Frame(container)
        button_frame.grid(row=1, column=0, sticky="ew")
        button_frame.columnconfigure(0, weight=1)
        ttk.Label(
            button_frame,
            textvariable=status_variables["activity"],
        ).grid(
            row=0,
            column=0,
            padx=(0, 12),
            sticky="w",
        )

        next_check_frame = ttk.Frame(button_frame)
        next_check_frame.grid(row=0, column=1, padx=(0, 12), sticky="e")
        ttk.Label(next_check_frame, text="Next check:").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            next_check_frame,
            textvariable=status_variables["next_check"],
            # Reserve room beyond the current 19-character timestamp so status
            # changes and longer date formats do not move the action buttons.
            width=24,
            anchor="w",
        ).grid(row=0, column=1, padx=(6, 0), sticky="w")

        def save_settings(close_after=False):
            raw_values = {
                key: variable.get()
                for key, variable in variables.items()
            }
            try:
                preset_label = raw_values.pop("view_preset")
                if preset_label not in preset_label_to_value:
                    raise ValueError("View preset is invalid.")
                raw_values["view_preset"] = preset_label_to_value[preset_label]

                layer_label = raw_values.pop("satellite_layer")
                if layer_label not in layer_label_to_value:
                    raise ValueError("Satellite layer is invalid.")
                requested_layer = layer_label_to_value[layer_label]
                if (
                    requested_layer is None
                    and requested_layer != saved_layer_state["value"]
                ):
                    raise ValueError("Satellite layer cannot be empty.")

                updates = normalize_settings_form_values(raw_values)
                startup_before_save = is_windows_startup_enabled()
                requested_startup = bool(raw_values["start_with_windows"])
                startup_changed = requested_startup != startup_before_save
                if startup_changed:
                    set_windows_startup_enabled(requested_startup)

                try:
                    def transform_configuration(text):
                        updated = replace_toml_values(text, updates)
                        if requested_layer != saved_layer_state["value"]:
                            updated = replace_primary_wms_layer_name(
                                updated,
                                requested_layer,
                            )
                        return updated

                    changed = update_active_configuration(transform_configuration)
                except Exception as config_error:
                    if startup_changed:
                        try:
                            set_windows_startup_enabled(startup_before_save)
                        except Exception as rollback_error:
                            raise RuntimeError(
                                f"{config_error} Windows startup rollback also "
                                f"failed: {rollback_error}"
                            ) from config_error
                    raise
            except Exception as exc:
                messagebox.showerror(
                    "Unable to save settings",
                    str(exc),
                    parent=root,
                )
                return

            if changed:
                request_runtime_configuration_reload(icon)
            else:
                icon.update_menu()
            saved_layer_state["value"] = requested_layer
            if close_after:
                root.destroy()

        ttk.Button(button_frame, text="Apply", command=save_settings).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(
            button_frame,
            text="OK",
            command=lambda: save_settings(close_after=True),
        ).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(button_frame, text="Cancel", command=root.destroy).grid(
            row=0, column=4
        )

        root.protocol("WM_DELETE_WINDOW", root.destroy)
        def bind_page_scrolling(widget):
            # Handle wheel input before combobox class bindings can change a value.
            # Native dropdown popups keep their own scrolling bindings.
            widget.bindtags((scroll_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                bind_page_scrolling(child)

        for page in notebook.tabs():
            bind_page_scrolling(root.nametowidget(page))
        for section in (preset_frame, wallpaper_frame, view_frame, output_frame, update_frame, history_frame):
            section.columnconfigure(1, weight=1)
        root.update_idletasks()
        # Center within the primary monitor's usable area, excluding the taskbar.
        left, top = 0, 0
        right, bottom = root.winfo_screenwidth(), root.winfo_screenheight()
        if os.name == "nt":
            from ctypes import wintypes
            work_area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
                left, top, right, bottom = work_area.left, work_area.top, work_area.right, work_area.bottom
        scale = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        usable_width = max(1, right - left - round(32 * scale))
        usable_height = max(1, bottom - top - round(64 * scale))
        window_width = min(round(800 * scale), usable_width)
        window_height = min(round(700 * scale), usable_height)
        root.minsize(min(round(680 * scale), usable_width), min(round(420 * scale), usable_height))
        window_x = left + max(0, (right - left - window_width) // 2)
        window_y = top + max(0, (bottom - top - window_height - round(32 * scale)) // 2)
        root.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        root.mainloop()

    def open_settings_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def settings_worker():
            try:
                run_settings_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to open settings", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=settings_worker,
            name="MarbleScapeSettings",
            daemon=True,
        ).start()

    def run_backup_export_dialog():
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected_path = filedialog.asksaveasfilename(
                parent=root,
                title="Export MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                initialfile=(
                    "marblescape-settings-"
                    f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.json"
                ),
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            saved_path = export_settings_backup(selected_path)
            messagebox.showinfo(
                "Settings backup exported",
                f"Backup saved to:\n{saved_path}",
                parent=root,
            )
        finally:
            root.destroy()

    def open_backup_export_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def backup_export_worker():
            try:
                run_backup_export_dialog()
            except Exception as exc:
                show_tray_error(icon, "Unable to export settings backup", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=backup_export_worker,
            name="MarbleScapeBackupExport",
            daemon=True,
        ).start()

    def run_backup_import_dialog(icon):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        config_text = None
        startup_enabled = None
        try:
            selected_path = filedialog.askopenfilename(
                parent=root,
                title="Import MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return

            try:
                config_text, startup_enabled = import_settings_backup(
                    selected_path
                )
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return

            confirmed = messagebox.askyesno(
                "Import settings backup",
                "Importing this backup replaces the current configuration "
                "and applies it immediately. Continue?",
                parent=root,
                icon="warning",
            )
            if not confirmed:
                return
        finally:
            root.destroy()

        apply_settings_backup(icon, config_text, startup_enabled)

    def open_backup_import_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def backup_import_worker():
            try:
                run_backup_import_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to import settings backup", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=backup_import_worker,
            name="MarbleScapeBackupImport",
            daemon=True,
        ).start()

    def run_background_color_picker(icon):
        import tkinter as tk
        from tkinter import colorchooser

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected = colorchooser.askcolor(
                color=normalize_background_color(BACKGROUND_COLOR),
                title="Choose MarbleScape background color",
                parent=root,
            )[1]
        finally:
            root.destroy()

        if selected:
            apply_configuration_updates(
                icon,
                "Unable to change background color",
                (
                    (
                        "output",
                        "background_color",
                        normalize_background_color(selected),
                    ),
                ),
            )

    def open_background_color_picker(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def color_picker_worker():
            try:
                run_background_color_picker(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to open color picker", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=color_picker_worker,
            name="MarbleScapeColorPicker",
            daemon=True,
        ).start()

    def run_custom_render_factor_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            output_width, output_height = get_output_dimensions()
            initial_factor = max(
                1.0,
                get_requested_render_scale(output_width, output_height),
            )
            selected = simpledialog.askfloat(
                "Custom render quality",
                "Render quality factor (minimum 1.0):",
                initialvalue=initial_factor,
                minvalue=1.0,
                parent=root,
            )
        finally:
            root.destroy()

        if selected is None:
            return
        if not math.isfinite(selected) or selected < 1.0:
            raise ValueError("Render quality factor must be at least 1.0.")
        apply_configuration_updates(
            icon,
            "Unable to change render quality",
            (("output", "render_scale", selected),),
        )

    def open_custom_render_factor_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def render_factor_worker():
            try:
                run_custom_render_factor_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to set render quality", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=render_factor_worker,
            name="MarbleScapeRenderQuality",
            daemon=True,
        ).start()

    def run_custom_resolution_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        configured_height = 0 if HEIGHT is None else HEIGHT
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected = simpledialog.askstring(
                "Custom resolution",
                "Resolution as WIDTH x HEIGHT (use height 0 for automatic):",
                initialvalue=f"{WIDTH} x {configured_height}",
                parent=root,
            )
        finally:
            root.destroy()

        if selected is None:
            return

        automatic_ratio = ASPECT_RATIO
        if automatic_ratio is None and WIDTH and HEIGHT:
            automatic_ratio = f"{WIDTH}:{HEIGHT}"
        width, height, aspect_ratio = parse_resolution_text(
            selected,
            automatic_ratio,
        )
        apply_configuration_updates(
            icon,
            "Unable to change resolution",
            (
                ("output", "width", width),
                ("output", "height", height),
                ("output", "aspect_ratio", aspect_ratio),
            ),
        )

    def open_custom_resolution_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def resolution_worker():
            try:
                run_custom_resolution_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to set custom resolution", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=resolution_worker,
            name="MarbleScapeResolution",
            daemon=True,
        ).start()

    def custom_resolution_is_selected(item):
        del item
        try:
            actual_width, actual_height = get_output_dimensions()
        except (TypeError, ValueError):
            return True

        return not any(
            actual_width == width
            and actual_height == height
            for _label, width, height in OUTPUT_SIZE_MENU_CHOICES
        )

    def run_custom_aspect_ratio_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        initial_ratio = ASPECT_RATIO
        if initial_ratio is None and WIDTH and HEIGHT:
            initial_ratio = f"{WIDTH}:{HEIGHT}"

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected = simpledialog.askstring(
                "Custom aspect ratio",
                "Aspect ratio (for example 16:10 or 2.35):",
                initialvalue=str(initial_ratio),
                parent=root,
            )
        finally:
            root.destroy()

        if selected is None:
            return

        aspect_ratio = normalize_aspect_ratio_text(selected)
        apply_configuration_updates(
            icon,
            "Unable to change aspect ratio",
            (
                ("output", "aspect_ratio", aspect_ratio),
                ("output", "height", 0),
            ),
        )

    def open_custom_aspect_ratio_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def aspect_ratio_worker():
            try:
                run_custom_aspect_ratio_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to set custom aspect ratio", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=aspect_ratio_worker,
            name="MarbleScapeAspectRatio",
            daemon=True,
        ).start()

    def custom_aspect_ratio_is_selected(item):
        del item
        try:
            current_ratio = parse_aspect_ratio(ASPECT_RATIO)
        except (TypeError, ValueError):
            return True
        return not any(
            math.isclose(
                current_ratio,
                parse_aspect_ratio(aspect_ratio),
                rel_tol=1e-9,
            )
            for _label, aspect_ratio in ASPECT_RATIO_MENU_CHOICES
        )

    def custom_render_factor_is_selected(item):
        del item
        return not any(
            values_match(get_render_scale_setting(), factor)
            for _label, factor in RENDER_QUALITY_MENU_CHOICES
        )

    def startup_is_enabled(item):
        del item
        return is_windows_startup_enabled()

    def toggle_windows_startup(icon, item):
        del item
        try:
            set_windows_startup_enabled(not is_windows_startup_enabled())
            icon.update_menu()
        except Exception as exc:
            try:
                icon.notify(str(exc), "Unable to change Windows startup")
            except Exception:
                log(f"Unable to change Windows startup: {exc}")

    def update_tray_status(state, next_check):
        with tray_status_lock:
            tray_status["state"] = state
            tray_status["next_check"] = next_check
        try:
            tray_icon.update_menu()
        except Exception as exc:
            log(f"Tray menu refresh warning: {exc}")

    def update_activity_status_text(item):
        del item
        state, _next_check = get_tray_status_snapshot()

        if state == "fetching":
            return "↻ Fetching new image..."
        if state == "checking":
            return "Checking for new image..."
        return "Standing by..."

    def force_loading_new_picture(icon, item):
        del item
        FORCE_UPDATE_EVENT.set()
        log("Manual image download requested.")
        icon.update_menu()

    def force_loading_is_enabled(item):
        del item
        state, _next_check = get_tray_status_snapshot()
        return (
            state == "waiting"
            and not FORCE_UPDATE_EVENT.is_set()
            and not APPLICATION_STOP_EVENT.is_set()
        )

    def next_check_status_text(item):
        del item
        return f"Next check: {format_next_check_status()}"

    tray_icon = pystray.Icon(
        "MarbleScape",
        create_windows_tray_image(),
        "MarbleScape — Satellite live imagery for your desktop.",
        menu=pystray.Menu(
            pystray.MenuItem("Open image folder", open_output_folder),
            pystray.MenuItem(
                "Settings...",
                open_settings_dialog,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Force loading new picture",
                force_loading_new_picture,
                enabled=force_loading_is_enabled,
            ),
            pystray.MenuItem(
                update_activity_status_text,
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                next_check_status_text,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                toggle_windows_startup,
                checked=startup_is_enabled,
            ),
            pystray.MenuItem("Restart", restart_application),
            pystray.MenuItem("Exit", exit_application),
        ),
    )

    def application_worker():
        try:
            result["exit_code"] = run_application(
                argv,
                pause_on_error=False,
                configuration_loaded=True,
                status_callback=update_tray_status,
            )
        except SystemExit as exc:
            result["exit_code"] = int(exc.code or 0)
        finally:
            tray_icon.stop()

    worker = threading.Thread(
        target=application_worker,
        name="MarbleScapeWorker",
        daemon=True,
    )

    def tray_setup(icon):
        icon.visible = True
        worker.start()

    tray_icon.run(setup=tray_setup)
    APPLICATION_STOP_EVENT.set()
    worker.join(timeout=2.0)
    return result["exit_code"]


# =============================================================================
# PROGRAM ENTRY POINT
# =============================================================================


if __name__ == "__main__":
    if should_use_windows_tray():
        sys.exit(run_with_windows_tray())
    sys.exit(run_application())
