#!/usr/bin/env python3

from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import argparse
import calendar
from copy import deepcopy
import ctypes
import gc
import datetime as dt
import http.client
import hashlib
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import re
import ssl
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import tomllib
import traceback
import uuid
import webbrowser
import xml.etree.ElementTree as ET

from marblescape_noaa import NOAAClient, SelectionLostError as NOAASelectionLost
from marblescape_himawari import (
    CENTERED_DATASETS as HIMAWARI_CENTERED_DATASETS, HimawariClient,
    SelectionLostError as HimawariSelectionLost, is_storm_area as is_himawari_storm_area,
    normalize_profile as normalize_himawari_profile, storm_view_zoom as himawari_storm_view_zoom,
    STORM_VIEW_KM as HIMAWARI_STORM_VIEW_KM,
)
from marblescape_theme import (
    APPEARANCE_LABELS,
    apply_appearance,
    resolve_appearance,
    set_native_menus,
    follow_system as follow_system_appearance,
    keep_background as keep_theme_background,
    keep_palette_color,
    markup_text,
    normalize_appearance,
    follow_wrap_width,
    on_theme_change,
    palette,
    style_scale,
    style_swatch,
)
from marblescape_slider import (
    FULL_CONTENT_BOX as SLIDER_FULL_CONTENT_BOX,
    SliderClient,
    SliderError,
    configure_content_box_store as configure_slider_content_box_store,
    effective_resolution as slider_effective_resolution,
)
from marblescape_worldview import WorldviewClient, SelectionLostError as WorldviewSelectionLost
from marblescape_slider import SelectionLostError as SliderSelectionLost
from marblescape_eumetsat import (
    normalize_profile as normalize_eumetsat_profile,
    supports_gap_fill as eumetsat_supports_gap_fill,
)
from marblescape_catalogues import CatalogueClient
from marblescape_copernicus import (
    CopernicusClient,
    map_overlay_signature,
    needs_data_mask_alpha as copernicus_needs_data_mask_alpha,
    no_data_choice as copernicus_no_data_choice,
    MAX_CATALOGUE_DATES,
    PROCESS_URL as COPERNICUS_PROCESS_URL,
    catalogue_revision as copernicus_catalogue_revision,
    get_layer as get_copernicus_layer,
    get_product as get_copernicus_product,
    normalize_auth_configuration,
    normalize_profile as normalize_copernicus_profile,
    protect_client_secret,
    supports_cloud_filter as copernicus_supports_cloud_filter,
)
from marblescape_profiles import (
    RotationScheduler,
    normalize_library,
    serialize_library,
    normalize_profile_column_widths,
    normalize_profile_column_order,
    normalize_profile_table_rows,
    toml_value,
    DEFAULT_PROFILE_TABLE_ROWS,
    DEFAULT_VISIBLE_PROFILE_COLUMNS,
    DEVICE_OUTPUT_KEYS,
)
from marblescape_cache import get_profile_image_cache, signature_digest
from marblescape_image_metadata import SCHEMA_VERSION as IMAGE_METADATA_VERSION, SOURCE_LABELS, embed_png_metadata, wallpaper_metadata
from marblescape_image_naming import image_filename_from_png, unused_image_path
from marblescape_network import NETWORK_ACTIVITY, NetworkCancelledError, open_response
from marblescape_profile_transfer import (
    portable_settings, strict_settings, prepare_profile_settings, resolve_import_repairs,
    ImportCancelled, _loads as load_transfer_json,
    write_verified_export_batch, _file_marker as export_file_marker,
)
from marblescape_import_dialog import confirm_import_repair
from marblescape_transfer_locations import ExportLocations, settings_backup_filename
from marblescape_catalogue_schedule import CatalogueSchedule, DEFAULT_REFRESH_TIME, normalize_refresh_time
import marblescape_snapshot as latest_snapshot
import marblescape_data_coverage as data_coverage
from marblescape_published_images import PublishedImageIndex
from marblescape_source_defaults import (
    AUTO_RESOLUTION_PROVIDERS,
    default_source_profiles,
)
from marblescape_download_progress import (
    DOWNLOAD_PROGRESS,
    DownloadCancelledError,
    activity_text,
    read_response,
)
from marblescape_time import (
    TIME_ZONE_MENU_CHOICES,
    format_display_datetime,
    normalize_time_zone,
)


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
# Friendly EUMETSAT names are handled below; kind "wms" uses an exact capabilities name.
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
# Applying a non-EUMETSAT profile saves no layers; choosing EUMETSAT again
# starts from this stack instead of an unusable empty one.
DEFAULT_LAYER_CONFIG = deepcopy(LAYER_CONFIG)

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
PROJECTION = "GEOS: MSG RSS"

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
# size and downsample to the configured output dimensions with Lanczos. With
# RENDER_SCALE_INHERITED (saved as "default") EUMETSAT uses General's
# DISPLAY_RENDER_SCALE instead of its own value.
RENDER_SCALE = 1.0
RENDER_SCALE_AUTOMATIC = True
RENDER_SCALE_INHERITED = False
# General > Monitor output: the shared render quality ("auto" or a factor) that
# sizes the picture for every display without its own value. A device setting,
# never part of a profile.
DISPLAY_RENDER_SCALE = "auto"

# View behavior when the requested aspect ratio differs from the projection's
# base extent.
#   "fit"  keeps the full base extent visible and adds surrounding map space.
#   "crop" fills the output frame by cropping the base extent.
VIEW_MODE = "fit"

# Map zoom factor.
#   1.0  = base view
#   >1.0 = zoom in
#   <1.0 = zoom out
DEFAULT_ZOOM = 1.0
ZOOM = DEFAULT_ZOOM
# Zoom dropdown steps: EUMETSAT presets and the still-image sources ("view"),
# and an EUMETSAT Custom area on the Geographic map (1 = the whole world width).
VIEW_ZOOM_LEVELS = (0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)
CUSTOM_AREA_ZOOM_LEVELS = (1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50)
ZOOM_LEVEL_NOTES = {
    "view": {1: "Default"},
    "custom": {5: "about Europe", 25: "about Germany"},
}
# EUMETSAT imagery has about 1-2 km per pixel; a Custom area beyond this zoom
# (about 2,000 km wide) gets blurry.
CUSTOM_AREA_SHARP_ZOOM = 20
# Still-image sources accept zooms within this range.
STILL_ZOOM_RANGE = (0.05, 20.0)

# When enabled, MTG TrueColor shows only the sunlit area. The remainder of
# the Earth disk is filled with black while the area outside the disk keeps
# the configured background color.
TRUECOLOR_BLACK_NIGHT = True

# Polling interval for checking the latest image.
UPDATE_INTERVAL_MINUTES = 15.0

# True keeps the process running and checking at UPDATE_INTERVAL_MINUTES.
# False performs one check and exits.
RUN_CONTINUOUSLY = True

# Global display-only time zone. Provider/cache timestamps remain in UTC.
DISPLAY_TIME_ZONE = "system"
# Window appearance: system (follow Windows), light or dark.
APPEARANCE = "system"
# The Settings window's last size at 100 % scaling; 0 = default.
SETTINGS_WINDOW_WIDTH = 0
SETTINGS_WINDOW_HEIGHT = 0
# The title of the Settings tab used last; "" = the first tab.
SETTINGS_TAB = ""

# Root output directories. The script selects the path for the current OS.
OUTPUT_ROOT_WINDOWS = SCRIPT_DIR
OUTPUT_ROOT_LINUX = SCRIPT_DIR

# The script creates these subdirectories below the selected output root.
CONTENT_DIRECTORY_NAME = "content"
LATEST_DIRECTORY_NAME = "latest"
HISTORY_DIRECTORY_NAME = "history"
CUSTOM_LATEST_FOLDER = ""
CUSTOM_HISTORY_FOLDER = ""

# File-name prefix for the current image. Each changed image receives a new
# name and can be set directly as the Windows desktop wallpaper.
LATEST_FILENAME_PREFIX = "marblescape"
LATEST_STATE_FILENAME = ".marblescape-latest.json"
ROTATION_STATE_FILENAME = "rotation_state.json"

# History settings.
ENABLE_HISTORY = False
HISTORY_FILENAME_PREFIX = "marblescape"
NO_PROFILE_HISTORY_FOLDER = "_no profile"

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
# Per-profile archive policies are local application state. They intentionally
# do not travel in profile JSON/PNG exports or imports.
PROFILE_HISTORY_POLICIES = {}

# Profile image cache limits (History & Storage). Each profile keeps up to
# PROFILE_CACHE_VARIANTS pictures, one per image settings and size; the least
# recently used go first when the cache exceeds PROFILE_CACHE_MAX_SIZE_GB
# (decimal GB). Each profile's current picture always stays. Pictures without a
# profile (the Latest snapshot) keep any number of variants within their own
# PROFILE_CACHE_SNAPSHOT_SIZE_GB, so they never push out saved profiles' pictures.
DEFAULT_PROFILE_CACHE_MAX_SIZE_GB = 2.0
PROFILE_CACHE_MAX_SIZE_GB_RANGE = (0.5, 10.0)
PROFILE_CACHE_MAX_SIZE_GB_STEP = 0.5
DEFAULT_PROFILE_CACHE_VARIANTS = 5
PROFILE_CACHE_VARIANTS_RANGE = (1, 10)
DEFAULT_PROFILE_CACHE_SNAPSHOT_SIZE_GB = 0.5
PROFILE_CACHE_MAX_SIZE_GB = DEFAULT_PROFILE_CACHE_MAX_SIZE_GB
PROFILE_CACHE_VARIANTS = DEFAULT_PROFILE_CACHE_VARIANTS
PROFILE_CACHE_SNAPSHOT_SIZE_GB = DEFAULT_PROFILE_CACHE_SNAPSHOT_SIZE_GB

# Optional fixed image time in ISO 8601 format.
# None requests the latest image currently available from EUMETSAT.
IMAGE_TIME = None

# Background color used outside rendered map content.
BACKGROUND_COLOR = "#000000"

# Network timeout in seconds.
NETWORK_TIMEOUT_SECONDS = 90

# Download display settings. Transfer sizes come from streamed HTTP response
# bytes; a percentage is shown only when the complete size is known.
DEFAULT_SHOW_DOWNLOAD_SPEED = True
DEFAULT_DOWNLOAD_SPEED_UNIT = "automatic"
DEFAULT_SHOW_DOWNLOAD_PROGRESS = True  # The percentage (download.show_progress).
DEFAULT_SHOW_DOWNLOAD_SIZE = True
DEFAULT_SHOW_DOWNLOAD_PROGRESS_BAR = True
DEFAULT_KEEP_COMPLETED_DOWNLOAD_VISIBLE = False
DEFAULT_DOWNLOAD_RETRIES = 2
DEFAULT_CATALOGUE_RETRIES = 2
DEFAULT_PROFILE_LIST_COLUMNS = (
    "rotation_enabled", "name", "status_symbol", "active", "history", "image_updates", "source", "mission", "product",
    "layer", "time_selection", "time", "quarter_mode",
    "quarter_offset",
    "quarter_target", "location", "latitude", "longitude",
    "gap_fill", "cloud_coverage", "mosaic_brightness", "zoom", "maximum_lookback",
    "output_resolution", "resolution", "map_labels", "map_borders", "cache_status", "last_download", "id", "short_id",
    "no_data_color", "image_size", "mosaic_contrast", "auto_brightness", "auto_contrast", "data_coverage",
    "auto_recommendation", "auto_priority", "auto_precise", "auto_choice", "shorelines",
)
SHOW_DOWNLOAD_SPEED = DEFAULT_SHOW_DOWNLOAD_SPEED
DOWNLOAD_SPEED_UNIT = DEFAULT_DOWNLOAD_SPEED_UNIT
SHOW_DOWNLOAD_PROGRESS = DEFAULT_SHOW_DOWNLOAD_PROGRESS
SHOW_DOWNLOAD_SIZE = DEFAULT_SHOW_DOWNLOAD_SIZE
SHOW_DOWNLOAD_PROGRESS_BAR = DEFAULT_SHOW_DOWNLOAD_PROGRESS_BAR
KEEP_COMPLETED_DOWNLOAD_VISIBLE = DEFAULT_KEEP_COMPLETED_DOWNLOAD_VISIBLE
DOWNLOAD_RETRIES = DEFAULT_DOWNLOAD_RETRIES
CATALOGUE_RETRIES = DEFAULT_CATALOGUE_RETRIES
CATALOGUE_REFRESH_TIME = DEFAULT_REFRESH_TIME
CATALOGUE_SCHEDULE = None
PROFILE_LIST_VISIBLE_COLUMNS = DEFAULT_VISIBLE_PROFILE_COLUMNS
PROFILE_LIST_SORT_COLUMN = ""
PROFILE_LIST_SORT_DESCENDING = False
PROFILE_LIST_COLUMN_WIDTHS = normalize_profile_column_widths()
PROFILE_LIST_COLUMN_ORDER = normalize_profile_column_order()
PROFILE_LIST_TABLE_ROWS = normalize_profile_table_rows()
APPLIED_PROFILE_ID = ""
DOWNLOAD_SPEED_UNITS = ("automatic", "KB/s", "MB/s", "Mbit/s")
DOWNLOAD_SPEED_UNIT_CHOICES = ("Automatic", "KB/s", "MB/s", "Mbit/s")
RETRY_COMBO_WIDTH = 4
# Profile, Retention mode and Maximum files share one width in both History
# sections; it is wide enough for profile names.
from marblescape_source_layout import SOURCE_COMBO_WIDTH as HISTORY_FIELD_WIDTH
UPDATE_INTERVAL_UNITS = {
    "months": 30 * 24 * 60,
    "weeks": 7 * 24 * 60,
    "days": 24 * 60,
    "hours": 60,
    "minutes": 1,
}
UPDATE_INTERVAL_VALUE_CHOICES = tuple(str(value) for value in range(1, 61))

# Request headers used for WMS requests.
from app_version import VERSION

USER_AGENT = f"MarbleScapeWallpaperDownloader/{VERSION}"
PROJECT_URL = "https://github.com/Gittegatt/MarbleScape"
DOCUMENTATION_URL = PROJECT_URL + "#documentation"
PRIVACY_URL = PROJECT_URL + "/blob/main/docs/PRIVACY_AND_NETWORK.md"
LEGAL_URL = PROJECT_URL + "/blob/main/docs/LEGAL_AND_ATTRIBUTION.md"
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/Gittegatt/MarbleScape/releases/latest"
)
GITHUB_TAGS_API = "https://api.github.com/repos/Gittegatt/MarbleScape/tags?per_page=1"
MAX_GITHUB_RESPONSE_BYTES = 1_000_000
SOURCE_VIEWER_URLS = (
    ("EUMETSAT", (
        "https://view.eumetsat.int/productviewer",
    )),
    ("NOAA STAR - GOES-East and GOES-West", (
        "https://www.star.nesdis.noaa.gov/goes/index.php",
    )),
    ("NOAA STAR - Solar (SUVI)", (
        "https://www.star.nesdis.noaa.gov/goes/SUVI.php?sat=G19",
    )),
    ("Himawari - NICT", (
        "https://himawari8.nict.go.jp/",
    )),
    ("Himawari - JMA", (
        "https://ds.data.jma.go.jp/mscweb/data/himawari/index.html",
    )),
    ("CIRA SLIDER", (
        "https://slider.cira.colostate.edu/",
    )),
    ("NASA Worldview", (
        "https://worldview.earthdata.nasa.gov/",
    )),
    ("Copernicus Browser", (
        "https://browser.dataspace.copernicus.eu/",
    )),
)
INFO_SECTIONS = (
    # Info texts use **bold** for what you click or choose, *italic* for examples and
    # "quotes" for texts MarbleScape shows (docs/DEVELOPMENT.md: Window appearance).
    ("Quick start", (
        "1. Preview the view on the provider website linked under **Sources**.\n"
        "2. Match source, mission, product, layer, location/coordinates, zoom, date and **Gap fill** in "
        "**Image**. Provider labels and available selections may differ.\n"
        "3. Set monitor size/aspect ratio, fit mode and wallpaper position under **General**.\n"
        "4. Use **Apply Image**, review the picture, then save it as a profile in **Profiles**.\n\n"
        "Detailed instructions: **About** > **Documentation**."
    )),
    ("Keyboard shortcuts", (
        "**Profile table**\n"
        "• **Double-click** a row: **Apply** the profile.\n"
        "• **Del**: delete the selected profiles (after confirmation).\n"
        "• **Ctrl+C**: copy the selected rows. **Ctrl+A**: select all rows.\n"
        "• **Ctrl+click**: select single rows. **Shift+click**: select a range.\n"
        "• **Ctrl+Up**/**Ctrl+Down**: move the selected rows one step.\n"
        "• Type a name: jump to that profile (letters within one second extend it).\n"
        "• **Esc**: cancel dragging a row or heading, or clear the typed name.\n"
        "• **Right-click** a row: the profile menu. **Right-click** a heading: the columns menu.\n"
        "• **Double-click** a column border: fit the column. **Double-click** the grip below the table: "
        "7 rows.\n\n"
        "**Dropdowns (everywhere)**\n"
        "• Type letters: jump to the first matching entry; the same letter again steps to the next one.\n"
        "• In an open list, **Enter** chooses the marked entry and **Esc** closes it.\n\n"
        "**Windows and dialogs**\n"
        "• **Tab**/**Shift+Tab**: next/previous field; the tab scrolls it into view.\n"
        "• **Enter**: the focused button of a confirmation. **Esc**: cancel or close the dialog.\n\n"
        "**Tray icon**\n"
        "• **Click** or **double-click**: open Settings. **Right-click**: the tray menu."
    )),
    ("Image sources at a glance", (
        "MarbleScape shows satellite pictures, not a map service such as Google Maps. Weather satellites "
        "see whole continents at about 0.5-3 km per pixel; Copernicus Sentinel-2 shows landscapes, fields, "
        "towns and coasts at about 10 m per pixel, not single houses or cars. A higher map zoom only "
        "enlarges the picture.\n\n"
        "• EUMETSAT: Europe, Africa, the Atlantic and Indian Ocean; every 10-15 minutes; about 0.5-3 km "
        "per pixel.\n"
        "• NOAA GOES-East and GOES-West: the Americas and the Pacific; every 10 minutes (smaller areas "
        "every 1-5 minutes); about 0.5-2 km per pixel.\n"
        "• NOAA Solar / Sun (SUVI): the Sun in extreme ultraviolet, every few minutes.\n"
        "• Himawari: Asia, Australia and the western Pacific; every 10 minutes; about 0.5-2 km per pixel. "
        "The NICT full disk can show shorelines, be centred on a place or follow an active storm.\n"
        "• CIRA SLIDER: the weather satellites above and GEO-KOMPSAT-2A in full resolution; every "
        "5-15 minutes.\n"
        "• NASA Worldview: the whole Earth, mostly one picture per day; about 250 m-1 km per pixel for "
        "true color.\n"
        "• Copernicus Browser: Sentinel-2 about 10 m per pixel, the same place every 2-5 days; Landsat "
        "30 m; Sentinel-1 radar 10 m, also through clouds and at night; Sentinel-3 300 m daily; "
        "Sentinel-5P gases about 5 km.\n\n"
        "Advantages over Google Maps: current (minutes to days old instead of months to years), regular "
        "(weather, seasons, floods, fires and snow become visible), consistent (one satellite, the same "
        "colors and light, no patchwork), worldwide including oceans and remote areas, views beyond natural "
        "color (infrared, night, radar), and freely available data.\n\n"
        "Details: **About** > **Documentation**, Image source guide."
    )),
    ("Save & apply", (
        "• **Apply Image** (on the **Image** tab) saves only Image-tab choices and reloads the picture only "
        "when they changed. **Save** right of it saves them without loading a picture now; they apply at the "
        "next regular image check, whose time the note below the buttons names (\"✓ Saved - loads at the "
        "next update (*19:45*)\"; during a rotation \"✓ Saved - rotation keeps showing its profiles\").\n"
        "• **Save** (on every other tab) saves non-Image settings without starting a new image load; it is "
        "available only while such changes are unsaved.\n"
        "• **OK** saves the complete dialog and closes it; **Close** discards unsaved changes.\n"
        "• Below the buttons, right-aligned, \"Unsaved changes\" (orange) marks a draft waiting for "
        "**Save** and a green \"✓ Saved\" confirms it.\n"
        "• Everything in **Profiles**, including the table view and **History** checkboxes, is saved at "
        "once and needs no **Save**."
    )),
    ("Appearance", (
        "• **General** > **Appearance**: **System** follows the Windows app mode (light or dark); **Light** "
        "and **Dark** stay fixed. **Save** or **OK** applies a new choice at once.\n"
        "• It changes MarbleScape's windows and the tray menu, never the downloaded image. Windows' own "
        "file, color and message dialogs keep the Windows look."
    )),
    ("Image quality", (
        "• **Automatic** source resolution is a good starting point: it considers the active monitors (all "
        "of them in a multi-display setup), fit/crop and zoom, and picks the smallest source size that avoids "
        "upscaling.\n"
        "• For finer detail choose one available size above the required output when bandwidth allows; "
        "larger source images keep more detail when zooming or cropping.\n"
        "• GOES, Solar, Himawari and CIRA SLIDER use the latest still image; their detail follows **Source "
        "resolution**. Each CIRA SLIDER size is a tile-pyramid level, so larger levels need more tile "
        "downloads.\n"
        "• CIRA SLIDER pads wide sectors such as GOES CONUS to a square with black rows; **fit**, **crop**, "
        "sizes and **Automatic** use the visible image without that padding.\n"
        "• NASA Worldview renders at **Render resolution**. EUMETSAT **Render quality** (**Image** > "
        "**Rendering**) controls WMS supersampling; **Default (General)** uses General's **Render quality "
        "factor**. A render factor adds no source detail.\n"
        "• **Largest available** uses the most detailed source image or the largest safe render. Resizing "
        "uses high-quality Lanczos filtering.\n"
        "• Below **Zoom** two lines show whether the chosen zoom is sharp, in orange with how much it "
        "enlarges the picture otherwise, and the source and output sizes. A Himawari storm view counts its "
        "own enlargement; clouds stay smooth when enlarged, coasts look softer."
    )),
    ("White, black or patchy images", (
        "• Seams, clouds, missing or partial imagery, day/night transitions, compression artifacts and "
        "provider annotations can come from the source and stay visible in the wallpaper.\n"
        "• Even cloudless mosaics can contain transparent No Data pixels, residual clouds or bright-terrain "
        "artifacts; compare the same period in Copernicus Browser.\n"
        "• A completely white Copernicus image is usually a closed cloud cover: clouds are valid image data "
        "(100% data coverage), and neither **Gap fill** nor the **No-data color** replaces them. Lower "
        "**Maximum cloud cover** or choose a date.\n"
        "• A picture without any image data (0% data coverage), for example a night pass or a fixed date "
        "without an acquisition here within the cloud limit, is not shown: the previous wallpaper stays and "
        "the header says why. The date list follows place, map zoom and cloud limit.\n\n"
        "Details: **About** > **Documentation**, Satellite imagery artifacts."
    )),
    ("Copernicus overlays & No-data", (
        "• **Labels** (place names, road names and POIs, one layer from the map service) and **Country "
        "borders** are independent overlays with their own switch and color, also for mosaics. A failing map "
        "layer does not stop the others.\n"
        "• **No-data color** fills areas without image data: mosaics with a color, PNG transparency, "
        "**Blur** or **Edge Blur**; regular layers with the map background (**Transparent**), a color, "
        "**Blur** or **Edge Blur**.\n"
        "• **Blur**, the default, spreads the nearby image into the gaps and far from real pixels fades to the "
        "whole image's average. **Edge Blur** fades to the colors along the gap's edge instead, so open sea "
        "beyond a coast stays sea colored. Neither is image data; dark valid pixels are never changed.\n"
        "• Brightness and contrast correction each have their own **auto** checkbox, on for new selections. "
        "They apply to Copernicus layers with a tone rule tuned for that collection and layer (docs: Tone "
        "rules); other layers render stock. Their statistics exclude gaps and map overlays. Contrast and "
        "Auto tones compress shadows and highlights instead of clipping them to black or white."
    )),
    ("Monitor output & framing", (
        "• **General** > **Output device**: choose a display; **Pause wallpaper updates** keeps its "
        "current picture. **Monitor output** shows its settings, "
        "including its own render quality. With one display they are the shared settings; with several, "
        "**Apply to all displays** makes the selected one's the shared settings (also for displays connected "
        "later). Disconnected displays keep their settings until reconnection.\n"
        "• Each source sets its image detail in **Image** > **Rendering**; **Auto** sizes follow the shared "
        "settings and the connected displays. The Copernicus saved PNG size is **Image** > **Rendering** > "
        "**Image resolution** (**Auto** by default).\n"
        "• **Fit** keeps the whole view; **Crop** fills the output and trims the edges. **Position** then "
        "places the finished file on the desktop."
    )),
    ("EUMETSAT view & presets", (
        "• A preset sets its satellite layer, projection, fit mode and zoom; each value can then be changed. "
        "All projections published by the EUMETSAT viewer are available.\n"
        "• Changing the projection resets the view to **Full Earth**; regional presets use **Geographic**.\n"
        "• **Custom area** shows your own region on the **Geographic** map: enter its centre as **Latitude** "
        "and **Longitude** in decimal degrees (negative for South and West) and its size as **Zoom** (1 = the "
        "whole world width; the dropdown offers 1-50, **5** is about Europe and **25** about Germany). It "
        "starts from the previously selected preset. **Image** > **Find location** fills the centre. Above "
        "about zoom 20 the imagery gets blurry; the Americas, East Asia and the poles lie outside the "
        "satellites' view.\n"
        "• **Zoom** is a dropdown: **1 (Default)** shows the preset as it is, up to **10** enlarges it. A "
        "saved zoom between the steps stays as \"(saved)\".\n"
        "• Where the layer has no data the basemap shows, for example a thin strip at the eastern edge when "
        "**GEOS: MSG RSS** (the disk seen from 9.5° East) shows an MTG layer taken from 0°.\n"
        "• **Black TrueColor night side** affects only the True Colour layer; GeoColour shows its own night "
        "side with city lights.\n"
        "• Products EUMETSAT lists but has not published yet are named below the view settings."
    )),
    ("Find location", (
        "• Shown above **Imagery updates** for Copernicus and for an EUMETSAT **Custom area** "
        "(**Geographic**).\n"
        "• Type a place or address in the search field and press **Search** or **Enter**; OpenStreetMap "
        "lists up to 40 places.\n"
        "• Click a place to copy its coordinates (latitude, longitude) to the **Coordinates** field below "
        "the list, where you can edit them; **Transfer** or a **double-click** on the place fills "
        "**Latitude** and **Longitude**. **Save** or **OK** keeps them.\n"
        "• **Preview** (Copernicus) opens the Copernicus Browser at the place and map zoom: the place in the "
        "**Coordinates** field, else the saved one. Choose product and date there; MarbleScape cannot pass "
        "them. The EUMETSAT viewer cannot be opened at a place, so **Preview** is grey for a **Custom area**. "
        "Coordinates copied from a map, such as *53.55, 9.99*, can be pasted into the **Coordinates** field "
        "or the search field.\n"
        "• Only the search text is sent, only when you search; results are not saved."
    )),
    ("Recommendation", (
        "• **Image** > **Recommendation** (Copernicus, below **Rendering**) finds good settings for the place "
        "and zoom.\n"
        "• **Compare variants...** opens the window; **Check** compares cloud limits and **Gap fill**, or the "
        "newest periods of a mosaic, and recommends two of them, each with **Apply** (a draft until **Save** "
        "or **OK**):\n"
        "   **Fewest clouds**: at least 90% of the view covered and at most 10% clouds today, the most "
        "reliable one over the last 90 days. **Newest** (mosaics): the newest published period.\n"
        "   **Data coverage**: at least 98% of the view, from as few and as new acquisitions as possible.\n"
        "• **Check** is free: it reads the Copernicus catalogue (dates, the tiles' own cloud estimates and "
        "footprints). Footprints also count empty swath edges, so the real coverage can be lower. Last 90 "
        "days: on how many days a setting gave full coverage.\n"
        "• **Precise check** measures coverage and clouds in the view itself, one small mask per distinct "
        "set of tiles; on by default for mosaics. **Load preview** (or a **double-click** on a row) renders a "
        "small picture like the wallpaper. Both use processing units; the buttons show an estimate.\n"
        "• **Use auto recommendation**: **Yes** renders every image check with the recommendation of the "
        "chosen **Priority**. Regular layers also offer **Newest** for events (eruption, fire, first snow): "
        "the newest acquisition on top however cloudy, the rest filled from earlier ones. Date, **Gap fill** and cloud limit are then greyed out and keep their saved "
        "values as the fallback. Regular layers decide from the catalogue (free); **Always use precise "
        "check** measures them at every check (about 0.6 processing units and up to 26 requests each) and "
        "starts **Compare variants...** with **Precise check** on."
    )),
    ("Quarterly & monthly mosaics", (
        "• Sentinel-2 offers **Specific quarter**, **Relative to now** and **Latest available**. **Quarters "
        "back** (including **Current quarter**) determines the **Resolved quarter**.\n"
        "• Sentinel-1 monthly mosaics use **Month selection** and **Months back**.\n"
        "• Specific periods stay fixed; relative periods advance with the calendar. Unpublished targets are "
        "reported, never silently replaced."
    )),
    ("Profiles", (
        "• Profiles save Image settings only; output size, background color and the Latest folder are device "
        "settings that a profile never changes.\n"
        "• Right-click a row for **Apply** (**double-click** does the same), **Load** "
        "(copies it to the **Image** tab), **Update**, **Check for new image** and **Force loading new image**; then "
        "**Edit** (**Rename**, **Duplicate**, **Delete**, **Move up**/**Move down**), **Copy cell**/**Copy "
        "row**, **Toggle updates**/**Toggle history**/**Toggle rotation** (checked while on for every "
        "selected row) and the History folder, and last **Import / Export** and **Columns**. "
        "Shortcuts: **Info** > **Keyboard shortcuts**.\n"
        "• **Force loading new image** works for one or several profiles and never changes the active "
        "profile: the shown one reloads at once, the others load into their cache in the background "
        "(⋯ \"QUEUE\" in **Status**). An applied picture (**Apply**, **Apply Image**) loads before the queue; "
        "a running background download finishes first.\n"
        "• **Check for new image** downloads only pictures newer than the cached ones; profiles with "
        "**Imagery updates** off are skipped. The result appears below the buttons.\n"
        "• After a failed attempt **Status** shows \"LOST\", \"SOURCE\", \"NETWORK\" or \"UNAVAIL\" "
        "(Info: Status).\n"
        "• **Create Profile from Image**, **Duplicate**, **Rename**, **Update**, **Delete**, moving, "
        "**Import profile**, **Toggle rotation** and rotation settings are saved immediately, after any "
        "confirmation; **Close** does not undo them. The footer says \"This tab saves automatically.\" and "
        "confirms each saved change with a green \"✓ Saved\".\n"
        "• The **Image** header shows the profile name, its short ID on the right; \"(modified)\" "
        "flags changed settings. Reverting clears the marker; **Update** saves the changes into the "
        "profile.\n"
        "• **Create Profile from Image** asks for the new profile's name and suggests the one the **Image** "
        "tab shows; **Rename** (button or right-click) renames the selected profile."
    )),
    ("Profile table", (
        "• Drag rows up or down to reorder them (a line shows where they land, several selected rows move as "
        "a block, the table scrolls at its edges, **Esc** cancels); **Ctrl+Up**/**Ctrl+Down** moves them one "
        "step.\n"
        "• **Filter** shows only profiles whose name contains the text. Focus the table and type a name "
        "prefix to select a row (typing within one second extends it).\n"
        "• Click headings to sort; sorting changes the view, not the rotation order. **Reset sorting** or "
        "moving a row returns to rotation order.\n"
        "• **Double-click** a column border to fit the column to its heading and values.\n"
        "• **Columns** or right-click headings show or hide columns; drag headings sideways to reorder them. "
        "Column visibility, order, widths and sorting are saved at once; **Reset columns** "
        "shows the default columns again.\n"
        "• Right-click table cells to copy them or their row (**Copy cell**, **Copy row**).\n"
        "• Drag the grip below the table for more or fewer rows (**double-click**: 7).\n"
        "• **Status** shows \"ACTIVE\", the running download or how the last attempt failed (Info: Status); "
        "**History** is a checkbox that switches History for that profile (or all selected rows) at once. "
        "Both are local and never exported.\n"
        "• **Updates** is **Imagery updates** > **Check for and download newer images** of each profile: a "
        "click switches it for that profile (or all selected rows) and saves it in the profile at once.\n"
        "• **Last download** is the newest verified image of the profile, also from Latest and History."
    )),
    ("Status", (
        "The **Profiles** table (symbol column and **Status**) and the note below the buttons show how "
        "the last update went, the note with its time, e.g. *✓ Image downloaded · 12:40*. Only the "
        "current state: the next success clears it.\n\n"
        "**After an update** (table · note)\n"
        "• ● \"ACTIVE\" · *✓ Image downloaded* (green), *✓ Image restored from cache* or *✓ Image up to "
        "date* (blue). Other rows stay blank.\n"
        "• ⊘ \"LOST\" · *? Image source no longer listed*: the area or product is gone, e.g. an ended "
        "storm; a rotation moves on.\n"
        "• ☁ \"SOURCE\" · *? Source unavailable*: the provider failed while this computer is online.\n"
        "• ↯ \"NETWORK\" · *! Network issue*: this computer is offline; a rotation waits one interval.\n"
        "• ⊖ \"UNAVAIL\" · *! Image unavailable*: anything else, e.g. no image data.\n"
        "Problems show in orange; the row on screen keeps its problem symbol, e.g. ↯ \"ACTIVE · NETWORK\". "
        "*× Download cancelled* sets no status.\n\n"
        "**While working** (table)\n"
        "• ⋯ \"QUEUE\" waits, ↻ \"CHECK\" asks the provider, ⭳ \"45%\" downloads, ⧉ \"RENDER\" "
        "Copernicus renders.\n\n"
        "**Catalogues** (note)\n"
        "• *✓ Catalogues refreshed* (green) or *✓ Catalogues up to date*; in orange *? Catalogue refresh "
        "incomplete: NOAA* (its saved catalogue stays in use) or *! Catalogue refresh failed: network "
        "issue*. **Refresh catalogue** on the **Image** tab notes its source, e.g. *✓ Catalogue "
        "refreshed: Himawari*.\n"
        "• *✓ Active storms updated: NOAA (+1 new, 1 ended)* (blue): the hourly storm check changed a list.\n\n"
        "A note after **Save** replaces the update note; \"Unsaved changes\" always shows."
    )),
    ("Rotation", (
        "• The leading **Rotation** checkbox includes a profile in the local rotation; with several rows "
        "selected it toggles all of them, as does right-click **Toggle rotation**. New, duplicated and newly "
        "imported profiles start excluded, and this choice is not exported.\n"
        "• Rotation follows the table order, with an interval from minutes to 30-day months, and can shuffle "
        "each complete pass.\n"
        "• **Keep last rotation position** keeps the position and the time of the last switch, so after a "
        "restart the shown picture stays until its interval ends.\n"
        "• **Load the next picture before the switch** (on by default) loads a newer picture of the next "
        "profile into its cache one minute in advance (30 seconds for a 1-minute rotation, with an orange "
        "hint), so the switch shows it at once.\n"
        "• Applying a profile by hand starts a full interval; rotation then continues after that profile.\n"
        "• Failed profile updates follow **Download retries**, then are skipped. Enable **General** > "
        "**Set wallpaper automatically** to update the desktop."
    )),
    ("Profile image cache", (
        "• Cached profile images avoid downloading and rendering an unchanged image again. Saved profiles "
        "and the Latest snapshot each have their own cache slot.\n"
        "• Each profile keeps one picture per image settings and output size (a variant), so returning to "
        "settings used recently, for example an earlier cloud cover or layer, shows its picture at once. "
        "A newer picture with the same settings replaces its variant.\n"
        "• Pictures of a modified profile (\"*Name* (modified)\") and pictures without a profile "
        "belong to no saved profile: they share the Latest snapshot's slot, which keeps any number of "
        "variants within its own Latest snapshot size limit (0.5-10 GB, default 0.5 GB). Trying settings "
        "therefore never pushes out saved profiles' pictures.\n"
        "• **History & Storage** > **Profile image cache**: **Size limit** (0.5-10 GB) and **Variants per "
        "profile** (1-10; 1 keeps only the current picture) apply to saved profiles. The least recently used "
        "variants go first; each profile's current picture always stays, so the cache can exceed the limit. "
        "A picture shared by several profiles is stored and counted once.\n"
        "• Removed variants are not archived to History. **Clear cache** removes only cached images, never "
        "Latest or History images.\n"
        "• **Status and storage** > **Total storage estimate** includes the cache and follows the sliders "
        "before **Save**."
    )),
    ("Profile import & export", (
        "• **Ctrl+click** selects single rows, **Shift+click** a range, **Ctrl+A** all.\n"
        "• **Export profile** writes one JSON per selected profile: **Save As** for one, **Select folder** "
        "for several.\n"
        "• **Import profile** checks JSON/PNG files first and shows an issue overview before any decision. "
        "Safe missing-field additions need consent (**Yes**/**Yes to all**, **Skip**/**Skip all**, "
        "**Cancel**); corrupt data and conflicting source/product/period metadata are rejected. PNG checks "
        "include the final IEND checksum.\n"
        "• UUIDs are preserved. The same UUID asks: **Overwrite**, **Skip** or **Import as copy**, with batch "
        "choices; a different UUID with a used name is added with \" (Imported)\". Copy and **Duplicate** "
        "create a new UUID.\n"
        "• Imported profiles are saved immediately; a single import shows its final name. **Cancel** aborts "
        "the transfer."
    )),
    ("Latest snapshot & data coverage", (
        "• **Latest snapshot (no profile)** is a protected system entry for the last successful "
        "profile-free image. It cannot be renamed, updated manually, deleted or rotated.\n"
        "• **Apply** reuses its settings; **Duplicate** creates a normal profile. JSON/PNG imports become "
        "\"Latest snapshot (Imported)\", never the system entry. Both backup scopes keep the snapshot, not "
        "images.\n"
        "• Data coverage is the share of valid satellite pixels before map overlays, not cloud cover or "
        "transfer progress. It appears in the Copernicus source-status line, profile details/table and PNG "
        "text/EXIF. Unsupported sources show \"Not available\", missing images a dash."
    )),
    ("Image metadata & filenames", (
        "• New PNG text/EXIF contains the profile name/UUID, source, image time or mosaic period, period "
        "mode/offset, location, **Labels**/**Country borders**/**No-data color** choices and portable render "
        "settings.\n"
        "• Latest/History filenames contain only the UTC generation time, the profile or location and a "
        "short image hash; everything else is in the metadata. History keeps the name; the cache uses hash "
        "names. Existing images are not mass-renamed.\n"
        "• History without a profile uses _no profile; named profiles use their name and full UUID. The "
        "**History folder** (**History & Storage**) is shared by both: it holds _no profile and one "
        "subfolder per profile.\n"
        "• Credentials and local paths are excluded from metadata and profile JSON, but names and coordinates "
        "may be private: check before sharing."
    )),
    ("Downloads & catalogues", (
        "• The footer shows an exact total and percentage when the server provides Content-Length; tiled and "
        "multi-request images stay indeterminate until their total is known.\n"
        "• **Download retries** and **Catalogue retries**: 1-9 retries mean 2-10 attempts. Temporary transfer "
        "errors are retried; invalid requests and authentication errors fail at once. If all catalogue "
        "attempts fail, the most recent cached catalogue is used.\n"
        "• **Image** > **Catalogue refresh** reloads the selected source; **Downloads & Updates** > "
        "**Catalogue refresh** refreshes all catalogues with progress.\n"
        "• Copernicus: **Refresh catalogue** loads all available acquisition dates for the selected location; "
        "this can take a while.\n"
        "• EUMETSAT: theme, service, mission, product type and layer combinations come from the public "
        "EUMETSAT catalogue. Projection is independent because the viewer reprojects the selected layer.\n"
        "• While all catalogues refresh (by hand, daily or at startup), the footer and the tray menu add it "
        "to the activity, for example \"Standing by... | Refreshing catalogues 2/5\".\n"
        "• **Daily catalogue refresh** runs at the saved local time; a missed run is caught up at the next "
        "start. **Save**/**OK** only moves the schedule. Unsuccessful refreshes retry after five minutes."
    )),
    ("Backups & History", (
        "• **Backup** transfers settings only (keeping profiles) or settings and all profiles (replacing the "
        "library after confirmation). Default folders are export/settings and export/profiles; each "
        "remembers its last successful destination.\n"
        "• Exports are read back and validated before success is reported; originals stay until "
        "verification completes, for rollback.\n"
        "• History retention is set separately for no-profile images and each profile, and the current "
        "profile policy can be applied to all. The protected snapshot always uses _no profile."
    )),
    ("Updates, log & exit", (
        "• **Downloads & Updates** > **Image update checks** sets the **Update check interval** (minutes to "
        "30-day months) and shows **Next check**. Below 2 minutes an orange hint notes that most sources "
        "publish only every 5-15 minutes.\n"
        "• **Image** > **Imagery updates**: **Check new image** checks now and loads only a newer picture; "
        "**Force loading new image** loads one even when the source has nothing newer.\n"
        "• If the picture in the Latest folder is removed, the next check puts it back from the cache.\n"
        "• Tray **Exit** (also **General** > **Actions**) cancels active downloads and catalogue refreshes "
        "and closes open windows; after 10 seconds it ends MarbleScape even if something hangs. **Restart** "
        "ends a previous MarbleScape that still hangs after 15 seconds. Manual starts open Settings; Windows "
        "autostart uses the silent tray mode.\n"
        "• **General** > **Actions** > **Show log** opens content/marblescape.log: what MarbleScape did, "
        "with times (about 1 MB, the two files before are kept; never Copernicus credentials)."
    )),
)

# Each image provider keeps its own selection; old configurations use EUMETSAT.
IMAGE_SOURCE = "eumetsat"
DEFAULT_SOURCE_PROFILES = default_source_profiles()
SOURCE_PROFILES = {key: dict(value) for key, value in DEFAULT_SOURCE_PROFILES.items()}
CHECK_FOR_SOURCE_UPDATES = True
IMAGE_STATUS_LOCK = threading.Lock()
IMAGE_STATUS = {"provider": None, "timestamp": None, "error": "", "interval_minutes": 10,
                "data_coverage": None}
NOAA_CLIENTS = {}
NOAA_CLIENTS_LOCK = threading.Lock()
HIMAWARI_CLIENTS = {}
HIMAWARI_CLIENTS_LOCK = threading.Lock()
SLIDER_CLIENTS = {}
SLIDER_CONTENT_BOXES_FILENAME = "slider_content_boxes.json"
SLIDER_CLIENTS_LOCK = threading.Lock()
WORLDVIEW_CLIENTS = {}
WORLDVIEW_CLIENTS_LOCK = threading.Lock()
CATALOGUE_CLIENTS = {}
CATALOGUE_CLIENTS_LOCK = threading.Lock()
COPERNICUS_CLIENT_ID = ""
COPERNICUS_CLIENT_SECRET = ""
COPERNICUS_CLIENT_SECRET_PROTECTED = ""
COPERNICUS_CLIENTS = {}
COPERNICUS_CLIENTS_LOCK = threading.Lock()
IMAGE_PROFILE_LIBRARY = normalize_library({})
ROTATION_STATUS_LOCK = threading.Lock()
# The last rotation switch made in the tray menu: (serial, enabled) for Settings.
ROTATION_SWITCH = {"serial": 0, "enabled": None}
ROTATION_STATUS = {"text": "Rotation is disabled.", "deadline": None,
                   "active_profile_id": None, "preload": None, "next_profile_name": None}
_ROTATION_ACTIVE_UNCHANGED = object()
NEXT_ROTATION_DEADLINE = None
CURRENT_IMAGE_LOCK = threading.Lock()
# The image worker and an instant profile switch from Settings both install
# pictures in Latest; one at a time.
LATEST_IMAGE_LOCK = threading.RLock()
CURRENT_IMAGE_PATH = None
ACTIVE_PROFILE_CACHE_ID = None
HISTORY_LOCK = threading.RLock()

# Optional TOML file next to this script. Pass --config to select another file.
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "marblescape_config.toml"
DEFAULT_CONFIG_TEMPLATE_PATH = SCRIPT_DIR / "marblescape_config.example.toml"
ACTIVE_CONFIG_PATH = DEFAULT_CONFIG_PATH
PROFILE_LIBRARY_PATH = SCRIPT_DIR / "profiles.toml"
ACTIVE_PROFILE_LIBRARY_PATH = PROFILE_LIBRARY_PATH

# If True on Windows, set every newly downloaded image directly as the desktop
# wallpaper. This avoids slideshow scheduling and file-cache delays.
SET_WINDOWS_WALLPAPER = True

# Windows wallpaper positioning.
# Supported values: "center", "tile", "stretch", "fit", "fill", "span", "none".
WINDOWS_WALLPAPER_POSITION = "fit"
WINDOWS_WALLPAPER_MONITOR_POSITIONS = {}
WINDOWS_WALLPAPER_MONITOR_OUTPUTS = {}
# Displays whose wallpaper MarbleScape leaves alone (General > Output device >
# Pause wallpaper updates); their position stays saved. "*" pauses every display,
# from an older shared "Do not update (keep current wallpaper)".
WINDOWS_WALLPAPER_PAUSED = frozenset()
PAUSE_ALL_DISPLAYS = "*"
WINDOWS_WALLPAPER_LOCK = threading.RLock()
WINDOWS_SINGLE_INSTANCE_HANDLE = None
SKIPPED_UPDATE_VERSION = ""

# Keep the console open after a fatal error when launched by double-click on
# Windows. This has no effect on Linux or Docker.
WINDOWS_PAUSE_ON_EXIT = True


# =============================================================================
# INTERNAL CONSTANTS
# =============================================================================

WMS_URL = "https://view.eumetsat.int/geoserver/wms"
WMS_VERSION = "1.3.0"
# Image > Find location (OpenStreetMap Nominatim); [service] location_search_endpoint.
DEFAULT_LOCATION_SEARCH_ENDPOINT = "https://nominatim.openstreetmap.org/search"
LOCATION_SEARCH_ENDPOINT = DEFAULT_LOCATION_SEARCH_ENDPOINT
IMAGE_FORMAT = "image/png"
MAX_WMS_DIMENSION = 4000
TRUECOLOR_LAYER_NAME = "mtg_fd:rgb_truecolour"
TRUECOLOR_EARTH_MASK_LAYER = "backgrounds:ne_gray"

OUTPUT_ROOT = OUTPUT_ROOT_WINDOWS if os.name == "nt" else OUTPUT_ROOT_LINUX
CONTENT_DIR = OUTPUT_ROOT / CONTENT_DIRECTORY_NAME
LATEST_DIR = CONTENT_DIR / LATEST_DIRECTORY_NAME
HISTORY_DIR = CONTENT_DIR / HISTORY_DIRECTORY_NAME

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

def available_projection_choices():
    """Return all projections published by the official EUMETSAT viewer config."""
    return tuple(PROJECTIONS)


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
WALLPAPER_POSITION_CHOICES = (*WINDOWS_WALLPAPER_POSITIONS, "none")
WALLPAPER_POSITION_LABELS = {
    **{name: name for name in WINDOWS_WALLPAPER_POSITIONS},
    "none": "Do not update (keep current wallpaper)",
}

APPLICATION_STOP_EVENT = threading.Event()
FORCE_UPDATE_EVENT = threading.Event()
# Saved profiles waiting for a forced background download into their cache.
PROFILE_REFRESH_LOCK = threading.Lock()
PROFILE_REFRESH_QUEUE = []
# Queued profiles that only load a picture when the provider has a newer one
# ("Check for new image"), the profile being checked and the batch result.
PROFILE_CHECK_ONLY = set()
PROFILE_CHECKING_ID = None
PROFILE_CHECK_COUNTS = {"new": 0, "unchanged": 0, "skipped": 0, "failed": 0}
PROFILE_CHECK_SUMMARY = {"serial": 0, "text": ""}
# Run the regular check of the shown picture now, without forcing a download.
CHECK_NOW_EVENT = threading.Event()
CONFIGURATION_RELOAD_EVENT = threading.Event()
SETTINGS_ONLY_RELOAD_EVENT = threading.Event()
CONFIGURATION_FILE_LOCK = threading.RLock()
LOADED_SETTINGS_FILES = None
WORKING_SETTINGS_BACKUP_LIMIT = 5
WORKING_SETTINGS_CONFIG_NAME = "marblescape_config.toml"
WORKING_SETTINGS_PROFILES_NAME = "profiles.toml"
LOADED_CONFIGURATION_FIELDS = (
    "IMAGE_SOURCE", "SOURCE_PROFILES", "CHECK_FOR_SOURCE_UPDATES", "IMAGE_PROFILE_LIBRARY",
    "COPERNICUS_CLIENT_ID", "COPERNICUS_CLIENT_SECRET",
    "COPERNICUS_CLIENT_SECRET_PROTECTED",
    "WMS_URL", "WMS_VERSION", "IMAGE_TIME", "NETWORK_TIMEOUT_SECONDS", "LOCATION_SEARCH_ENDPOINT",
    "SHOW_DOWNLOAD_SPEED", "DOWNLOAD_SPEED_UNIT", "SHOW_DOWNLOAD_PROGRESS", "SHOW_DOWNLOAD_SIZE",
    "SHOW_DOWNLOAD_PROGRESS_BAR", "KEEP_COMPLETED_DOWNLOAD_VISIBLE",
    "DOWNLOAD_RETRIES", "CATALOGUE_RETRIES", "CATALOGUE_REFRESH_TIME", "PROFILE_LIST_COLUMN_ORDER",
    "PROFILE_LIST_VISIBLE_COLUMNS", "PROFILE_LIST_SORT_COLUMN", "PROFILE_LIST_SORT_DESCENDING",
    "PROFILE_LIST_COLUMN_WIDTHS", "PROFILE_LIST_TABLE_ROWS", "APPLIED_PROFILE_ID",
    "UPDATE_INTERVAL_MINUTES", "RUN_CONTINUOUSLY", "RENDER_MODE",
    "WIDTH", "HEIGHT", "ASPECT_RATIO", "BACKGROUND_COLOR", "RENDER_SCALE",
    "RENDER_SCALE_AUTOMATIC", "RENDER_SCALE_INHERITED", "DISPLAY_RENDER_SCALE",
    "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX",
    "CUSTOM_LATEST_FOLDER", "CUSTOM_HISTORY_FOLDER", "PROJECTION",
    "VIEW_PRESET", "CUSTOM_BBOX", "VIEW_MODE", "ZOOM",
    "TRUECOLOR_BLACK_NIGHT", "ENABLE_HISTORY", "HISTORY_RETENTION_MODE",
    "HISTORY_MAX_FILES", "HISTORY_RETENTION_YEARS",
    "HISTORY_RETENTION_MONTHS", "HISTORY_RETENTION_DAYS",
    "HISTORY_RETENTION_HOURS", "HISTORY_RETENTION_MINUTES", "PROFILE_HISTORY_POLICIES",
    "PROFILE_CACHE_MAX_SIZE_GB", "PROFILE_CACHE_VARIANTS", "PROFILE_CACHE_SNAPSHOT_SIZE_GB",
    "SET_WINDOWS_WALLPAPER", "WINDOWS_WALLPAPER_POSITION",
    "WINDOWS_WALLPAPER_MONITOR_POSITIONS", "WINDOWS_WALLPAPER_MONITOR_OUTPUTS",
    "WINDOWS_WALLPAPER_PAUSED", "WINDOWS_PAUSE_ON_EXIT", "DISPLAY_TIME_ZONE", "APPEARANCE", "SETTINGS_WINDOW_WIDTH",
    "SETTINGS_WINDOW_HEIGHT", "SETTINGS_TAB", "LAYER_CONFIG", "ACTIVE_CONFIG_PATH",
    "ACTIVE_PROFILE_LIBRARY_PATH", "SKIPPED_UPDATE_VERSION",
)
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE_NAME = "MarbleScape"
SETTINGS_BACKUP_FORMAT = "marblescape-settings-backup"
SETTINGS_BACKUP_VERSION = 3
MAX_BACKUP_CONFIGURATION_BYTES = 1_000_000
MAX_SETTINGS_BACKUP_FILE_BYTES = 5_000_000

VIEW_PRESET_MENU_CHOICES = (
    ("Full Earth", "full_earth"),
    ("Europe", "europe"),
    ("Mediterranean", "mediterranean"),
    ("Central Europe", "central_europe"),
    ("Custom area", "custom"),
)

# Selecting a preset applies a complete, predictable starting profile. Users
# can still change individual settings afterwards without changing the preset.
VIEW_PRESET_PROFILES = {
    "full_earth": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "GEOS: MSG RSS",
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

# Sizes as "W × H (Name)", grouped by their exact aspect ratio; settings show
# them as "16:9 | 3840 × 2160 (4K UHD)". Names only where they are common.
OUTPUT_SIZE_MENU_GROUPS = (
    (
        "16:9",
        (
            ("1280 × 720 (HD)", 1280, 720),
            ("1366 × 768 (HD)", 1366, 768),
            ("1600 × 900 (HD+)", 1600, 900),
            ("1920 × 1080 (Full HD)", 1920, 1080),
            ("2560 × 1440 (QHD)", 2560, 1440),
            ("3200 × 1800 (QHD+)", 3200, 1800),
            ("3840 × 2160 (4K UHD)", 3840, 2160),
            ("5120 × 2880 (5K)", 5120, 2880),
            ("7680 × 4320 (8K UHD)", 7680, 4320),
        ),
    ),
    (
        "16:10",
        (
            ("1280 × 800", 1280, 800),
            ("1440 × 900", 1440, 900),
            ("1680 × 1050", 1680, 1050),
            ("1920 × 1200 (WUXGA)", 1920, 1200),
            ("2560 × 1600 (WQXGA)", 2560, 1600),
            ("2880 × 1800", 2880, 1800),
            ("3840 × 2400 (WQUXGA)", 3840, 2400),
        ),
    ),
    (
        "3:2",
        (
            ("1920 × 1280", 1920, 1280),
            ("2160 × 1440", 2160, 1440),
            ("2256 × 1504", 2256, 1504),
            ("2304 × 1536", 2304, 1536),
            ("2496 × 1664", 2496, 1664),
            ("3000 × 2000", 3000, 2000),
            ("3240 × 2160", 3240, 2160),
        ),
    ),
    (
        "4:3",
        (
            ("1024 × 768 (XGA)", 1024, 768),
            ("1280 × 960", 1280, 960),
            ("1600 × 1200 (UXGA)", 1600, 1200),
        ),
    ),
    (
        "5:4",
        (
            ("1280 × 1024 (SXGA)", 1280, 1024),
        ),
    ),
    (
        "~21:9",
        (
            ("2560 × 1080 (UWFHD)", 2560, 1080),
            ("3440 × 1440 (UWQHD)", 3440, 1440),
            ("3840 × 1600 (UW4K)", 3840, 1600),
            ("5120 × 2160 (5K2K)", 5120, 2160),
        ),
    ),
    (
        "32:9",
        (
            ("3840 × 1080 (DFHD)", 3840, 1080),
            ("5120 × 1440 (DQHD)", 5120, 1440),
            ("7680 × 2160 (DUHD)", 7680, 2160),
        ),
    ),
    (
        "9:16",
        (
            ("1080 × 1920 (Full HD)", 1080, 1920),
            ("1440 × 2560 (QHD)", 1440, 2560),
            ("2160 × 3840 (4K UHD)", 2160, 3840),
        ),
    ),
    (
        "10:16",
        (
            ("1050 × 1680", 1050, 1680),
            ("1200 × 1920 (WUXGA)", 1200, 1920),
            ("1600 × 2560 (WQXGA)", 1600, 2560),
        ),
    ),
)

OUTPUT_SIZE_MENU_CHOICES = tuple(
    choice
    for _group_label, group_choices in OUTPUT_SIZE_MENU_GROUPS
    for choice in group_choices
)

# The usual way of writing a ratio first; the reduced one in brackets when it differs.
ASPECT_RATIO_MENU_GROUPS = (
    (
        "Standard",
        (
            ("16:9", "16:9"),
            ("16:10 (8:5)", "16:10"),
            ("3:2", "3:2"),
            ("4:3", "4:3"),
            ("5:4", "5:4"),
            ("1:1", "1:1"),
        ),
    ),
    (
        "Ultrawide",
        (
            ("18:9 (2:1)", "2:1"),
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

# EUMETSAT's choice to use General's display render quality (saved as "default").
RENDER_QUALITY_DEFAULT_LABEL = "Default (General)"
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

HISTORY_MAX_FILES_MENU_CHOICES = (1, 5, 10, 25, 50, 100, 250, 500)
# Settings window width at 100 % Windows scaling. At the minimum the whole tab
# strip, every tab and the footer fit without clipping or scrolling (enforced
# by a regression test). Settings opens at the size saved in [display]
# (settings_window_width/height, at 100 % scaling; 0 = default): by default
# at the minimum width and the default height.
SETTINGS_MIN_WIDTH = 900
SETTINGS_DEFAULT_WIDTH = SETTINGS_MIN_WIDTH
SETTINGS_MIN_HEIGHT = 420
SETTINGS_DEFAULT_HEIGHT = 700
# Dropdowns on General share one width: the longest Monitor output choice fits.
GENERAL_COMBO_WIDTH = 38
# Footer text while Profiles, the tab that needs no Save, is shown.
PROFILES_AUTOSAVE_HINT = "This tab saves automatically."

HISTORY_MAX_AGE_MENU_CHOICES = (
    ("1 hour", {"years": 0, "months": 0, "days": 0, "hours": 1, "minutes": 0}),
    ("6 hours", {"years": 0, "months": 0, "days": 0, "hours": 6, "minutes": 0}),
    ("12 hours", {"years": 0, "months": 0, "days": 0, "hours": 12, "minutes": 0}),
    ("1 day", {"years": 0, "months": 0, "days": 1, "hours": 0, "minutes": 0}),
    ("7 days", {"years": 0, "months": 0, "days": 7, "hours": 0, "minutes": 0}),
    ("30 days", {"years": 0, "months": 0, "days": 30, "hours": 0, "minutes": 0}),
)


def refresh_output_paths():
    global OUTPUT_ROOT, CONTENT_DIR, LATEST_DIR, HISTORY_DIR
    OUTPUT_ROOT = OUTPUT_ROOT_WINDOWS if os.name == "nt" else OUTPUT_ROOT_LINUX
    CONTENT_DIR = OUTPUT_ROOT / CONTENT_DIRECTORY_NAME
    LATEST_DIR = (
        resolve_script_relative_path(CUSTOM_LATEST_FOLDER)
        if CUSTOM_LATEST_FOLDER else CONTENT_DIR / LATEST_DIRECTORY_NAME
    )
    HISTORY_DIR = (
        resolve_script_relative_path(CUSTOM_HISTORY_FOLDER)
        if CUSTOM_HISTORY_FOLDER else CONTENT_DIR / HISTORY_DIRECTORY_NAME
    )
    validate_image_folders(LATEST_DIR, HISTORY_DIR)


def validate_image_folders(latest, history):
    if latest.resolve() == history.resolve():
        raise ValueError("Latest and history folders must be different.")
    cache_directory = (OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / "cache").resolve()
    cache_database = (OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / "cache.sqlite3").resolve()
    for folder in (latest, history):
        resolved = folder.resolve()
        if resolved == cache_directory or cache_directory in resolved.parents:
            raise ValueError(
                "Latest and history folders cannot use the profile cache directory or its subfolders."
            )
        if resolved == cache_database or cache_database in resolved.parents:
            raise ValueError(
                "Latest and history folders cannot use the profile cache database path."
            )
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Image folder points to a file: {folder}")


# =============================================================================
# GENERAL HELPERS
# =============================================================================


def timestamp_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# content/marblescape.log keeps what the tray app, which has no console, would
# lose. It starts anew at about 1 MB and keeps the two files before. Only a real
# run (the entry point) writes it; tests never do.
LOG_FILE_NAME = "marblescape.log"
LOG_FILE_MAX_BYTES = 1024 * 1024
LOG_FILE_BACKUPS = 2
LOG_FILE_LOCK = threading.Lock()
LOG_FILE_STATE = {"enabled": False, "handler": None}


def log_file_path():
    return CONTENT_DIR / LOG_FILE_NAME


def enable_log_file():
    """Write every log line to the log file from now on, and log unexpected errors."""
    with LOG_FILE_LOCK:
        LOG_FILE_STATE["enabled"] = True
    original_thread_hook = threading.excepthook
    original_hook = sys.excepthook

    def log_thread_error(args):
        if args.exc_type is not SystemExit:
            log(f"Unexpected error in {getattr(args.thread, 'name', 'a thread')}: " + "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
        original_thread_hook(args)

    def log_error(kind, error, error_traceback):
        log("Unexpected error: " + "".join(traceback.format_exception(kind, error, error_traceback)).rstrip())
        original_hook(kind, error, error_traceback)

    threading.excepthook = log_thread_error
    sys.excepthook = log_error


def close_log_file():
    with LOG_FILE_LOCK:
        handler = LOG_FILE_STATE["handler"]
        LOG_FILE_STATE["handler"] = None
    if handler is not None:
        handler.close()


def redact_log_text(text):
    """Mask Copernicus credentials and access tokens, should a message ever carry one."""
    for secret in (COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET, COPERNICUS_CLIENT_SECRET_PROTECTED):
        if len(secret) >= 4:
            text = text.replace(secret, "***")
    text = re.sub(r"(?i)\b(bearer\s+)[^\s\"',;]+", r"\1***", text)
    return re.sub(r"(?i)\b(access_token|refresh_token|client_secret|password)(\"?\s*[:=]\s*\"?)[^\s\"',;&}]+",
                  r"\1\2***", text)


def write_log_file(line):
    with LOG_FILE_LOCK:
        if not LOG_FILE_STATE["enabled"]:
            return
        try:
            path = log_file_path()
            handler = LOG_FILE_STATE["handler"]
            if handler is None or handler.baseFilename != os.path.abspath(path):
                if handler is not None:
                    handler.close()
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(path, maxBytes=LOG_FILE_MAX_BYTES,
                                              backupCount=LOG_FILE_BACKUPS, encoding="utf-8", delay=True)
                # A full disk or a locked file must never stop MarbleScape.
                handler.handleError = lambda _record: None
                LOG_FILE_STATE["handler"] = handler
            handler.emit(logging.LogRecord("marblescape", logging.INFO, "", 0, line, None, None))
        except Exception:
            pass


def log(message=""):
    if message:
        line = f"[{timestamp_text()}] {redact_log_text(str(message))}"
        print(line, flush=True)
        write_log_file(line)
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


def get_profile_cache():
    cache = get_profile_image_cache(CONTENT_DIR)
    # Pictures without a profile, including those of a modified profile, share
    # the Latest snapshot's slot: any number of variants within its own size limit.
    cache.set_limits(profile_cache_max_bytes(), PROFILE_CACHE_VARIANTS,
                     unlimited_variants=(latest_snapshot.CACHE_ID,),
                     separate_limits={latest_snapshot.CACHE_ID: profile_cache_snapshot_bytes()})
    return cache


def set_current_image_path(path, profile_id=None):
    global CURRENT_IMAGE_PATH, ACTIVE_PROFILE_CACHE_ID
    resolved = Path(path).resolve() if path is not None else None
    with CURRENT_IMAGE_LOCK:
        CURRENT_IMAGE_PATH = resolved
        ACTIVE_PROFILE_CACHE_ID = profile_id


def get_current_image_path():
    with CURRENT_IMAGE_LOCK:
        path = CURRENT_IMAGE_PATH
    try:
        return path if path is not None and path.is_file() else None
    except OSError:
        return None


def get_active_profile_cache_id():
    with CURRENT_IMAGE_LOCK:
        return ACTIVE_PROFILE_CACHE_ID


def clear_profile_image_cache():
    """Clear only profile-cache data and refresh an active rotated profile."""
    global CURRENT_IMAGE_PATH
    result = get_profile_cache().clear()
    with CURRENT_IMAGE_LOCK:
        active = ACTIVE_PROFILE_CACHE_ID is not None
        if active:
            CURRENT_IMAGE_PATH = None
    if active:
        FORCE_UPDATE_EVENT.set()
    return result


def synchronize_profile_image_cache():
    identifiers = [item["id"] for item in IMAGE_PROFILE_LIBRARY.get("items", ())]
    identifiers.append(latest_snapshot.CACHE_ID)
    try:
        cache = get_profile_cache()
        result = cache.retain_profiles(identifiers)
        # Changed limits in History & Storage take effect on the next reload.
        result["variants"] = cache.enforce_limits()
        return result
    except Exception as exc:
        log(f"Profile cache maintenance warning: {exc}")
        return {"profiles": 0, "files": 0, "variants": 0}


def ensure_directories():
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    if ENABLE_HISTORY or not CUSTOM_HISTORY_FOLDER:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    get_profile_cache().images_dir.mkdir(parents=True, exist_ok=True)
    for stale_temp in LATEST_DIR.glob("*.tmp"):
        if stale_temp.is_file():
            stale_temp.unlink(missing_ok=True)
    migrate_history_folders()
    synchronize_profile_history_folders()


def _history_folder_name(profile_id=None, profile_name=None):
    """Stable, readable subfolder name; the complete UUID prevents collisions."""
    if not profile_id or profile_id == latest_snapshot.SYSTEM_ID:
        return NO_PROFILE_HISTORY_FOLDER
    if profile_name is None:
        profile_name = image_profile_name(profile_id) or "Unknown profile"
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(profile_name)).strip(" .") or "Profile"
    return f"{safe_name}_{profile_id}"


def _default_history_policy():
    return {
        "enabled": bool(ENABLE_HISTORY), "retention_mode": HISTORY_RETENTION_MODE,
        "max_files": HISTORY_MAX_FILES, "years": HISTORY_RETENTION_YEARS,
        "months": HISTORY_RETENTION_MONTHS, "days": HISTORY_RETENTION_DAYS,
        "hours": HISTORY_RETENTION_HOURS, "minutes": HISTORY_RETENTION_MINUTES,
    }


def _normalize_history_policy(policy):
    if not isinstance(policy, dict) or set(policy) - {"enabled", "folder", "retention_mode", "max_files", "years", "months", "days", "hours", "minutes"}:
        raise ValueError("Profile history policy is invalid.")
    result = _default_history_policy()
    result.update(policy)
    # Per-profile History roots were dropped in favor of the shared History
    # folder; earlier unreleased drafts may still carry the key.
    result.pop("folder", None)
    if type(result["enabled"]) is not bool:
        raise ValueError("Profile history policy is invalid.")
    if result["retention_mode"] not in {"count", "time", "both"}:
        raise ValueError("Profile history retention mode is invalid.")
    for key in ("max_files", "years", "months", "days", "hours", "minutes"):
        if type(result[key]) is not int or result[key] < 0:
            raise ValueError("Profile history values must be nonnegative integers.")
    if result["retention_mode"] in {"time", "both"} and not any(result[key] for key in ("years", "months", "days", "hours", "minutes")):
        raise ValueError("Profile history time retention needs a positive age.")
    return result


def profile_history_policy(profile_id):
    return _normalize_history_policy(PROFILE_HISTORY_POLICIES.get(profile_id, {}))


def profile_history_directory(profile_id=None, profile_name=None, create=False):
    # One shared History root holds _no profile and every profile subfolder.
    folder = HISTORY_DIR / _history_folder_name(profile_id, profile_name)
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def rename_profile_history_directory(profile_id, old_name, new_name):
    """Keep a profile's archive readable after its visible name changes."""
    with HISTORY_LOCK:
        old_folder = profile_history_directory(profile_id, old_name)
        new_folder = profile_history_directory(profile_id, new_name)
        if old_folder == new_folder or not old_folder.exists():
            return
        if new_folder.exists():
            raise ValueError("The target profile history folder already exists; history was not renamed.")
        old_folder.rename(new_folder)


def synchronize_profile_history_folders():
    """Finish a saved name change if shutdown occurred before the folder rename."""
    with HISTORY_LOCK:
        for item in IMAGE_PROFILE_LIBRARY.get("items", ()):
            identifier, name = item["id"], item["name"]
            expected = profile_history_directory(identifier, name)
            if expected.exists() or not expected.parent.is_dir():
                continue
            candidates = [folder for folder in expected.parent.glob(f"*_{identifier}") if folder.is_dir()]
            if len(candidates) == 1:
                try:
                    candidates[0].rename(expected)
                except OSError as exc:
                    log(f"Profile History folder rename warning for {identifier}: {exc}")


def _history_profile_identity(path):
    """Read only MarbleScape's public iTXt profile identity for archive routing."""
    try:
        from PIL import Image
        with Image.open(path) as picture:
            record = json.loads(picture.text.get("MarbleScape", "{}"))
        known_profile_ids = {
            item["id"] for item in IMAGE_PROFILE_LIBRARY.get("items", ())
        }
        if (record.get("profile_kind") == latest_snapshot.SYSTEM_KIND
                or (record.get("profile_name") == latest_snapshot.SYSTEM_NAME
                    and record.get("profile_id") not in known_profile_ids)):
            return None, None
        identifier, name = record.get("profile_id"), record.get("profile_name")
        if isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{32}", identifier):
            return identifier, name if isinstance(name, str) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None, None


# The last answer of shown_profile_row_id, per picture file and modification time.
_SHOWN_ROW_CACHE = {"key": None, "row": None}


def shown_profile_row_id():
    """The profile table row whose picture is on screen: a saved profile's ID, or
    the Latest snapshot row for an image without a profile (also one made from a
    profile's modified settings)."""
    current = get_current_image_path()
    if current is None:
        return None
    try:
        stat = current.stat()
        key = (str(current), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return _history_profile_identity(current)[0] or latest_snapshot.SYSTEM_ID
    cached = _SHOWN_ROW_CACHE
    if cached["key"] != key:
        row = _history_profile_identity(current)[0] or latest_snapshot.SYSTEM_ID
        _SHOWN_ROW_CACHE.update(key=key, row=row)
        return row
    return cached["row"]


def migrate_history_folders():
    """Move managed legacy history files into profile/no-profile subfolders once safely."""
    if not HISTORY_DIR.is_dir():
        return 0
    moved = 0
    with HISTORY_LOCK:
        no_profile_folder = profile_history_directory(None, create=True)
        protected_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]+', "-", latest_snapshot.SYSTEM_NAME
        ).strip(" .") or "Profile"
        protected_prefix = protected_name + "_"
        for folder in list(HISTORY_DIR.iterdir()):
            if (not folder.is_dir() or not folder.name.startswith(protected_prefix)
                    or re.fullmatch(r"[0-9a-f]{32}", folder.name[len(protected_prefix):]) is None):
                continue
            for path in list(folder.iterdir()):
                if (path.is_file()
                        and path.suffix.casefold() == ".png"
                        and path.name.casefold().startswith(HISTORY_FILENAME_PREFIX.casefold() + "_")):
                    try:
                        shutil.move(str(path), str(unused_image_path(no_profile_folder, path.name)))
                        moved += 1
                    except OSError as exc:
                        log(f"Protected snapshot History migration warning for {path.name}: {exc}")
            try:
                folder.rmdir()
            except OSError:
                # Preserve an unexpected non-managed file instead of deleting user data.
                pass
        for path in list(HISTORY_DIR.glob("*.png")):
            if not path.is_file() or not path.name.casefold().startswith(HISTORY_FILENAME_PREFIX.casefold() + "_"):
                continue
            identifier, name = _history_profile_identity(path)
            target = unused_image_path(profile_history_directory(identifier, name, create=True), path.name)
            try:
                shutil.move(str(path), str(target))
                moved += 1
            except OSError as exc:
                log(f"History migration warning for {path.name}: {exc}")
    return moved


def resolve_script_relative_path(value):
    """Resolve relative configuration paths from the script directory."""
    expanded = os.path.expandvars(str(value))
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def parse_render_scale_setting(value, allow_default=False):
    """Return a numeric render scale or the persistent automatic mode.

    With ``allow_default`` (a profile's own render quality) "default" means
    General's display render quality.
    """
    if isinstance(value, str) and value.strip().lower() in {"auto", "automatic"}:
        return "auto"
    if allow_default and isinstance(value, str) and value.strip().lower() == "default":
        return "default"
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Render quality must be 'default', 'auto' or a numeric factor." if allow_default
            else "Render quality must be 'auto' or a numeric factor."
        ) from exc
    if not math.isfinite(numeric) or numeric < 1.0:
        raise ValueError("Render quality factor must be at least 1.0.")
    return numeric


def normalize_download_speed_unit(value):
    """Return the persistent spelling for a supported transfer-rate unit."""
    normalized = str(value).strip().casefold()
    aliases = {item.casefold(): item for item in DOWNLOAD_SPEED_UNITS}
    aliases.update({"auto": "automatic", "mbit/second": "Mbit/s"})
    if normalized not in aliases:
        raise ValueError("Download speed unit is invalid.")
    return aliases[normalized]


# The Settings Save check rebuilds what Save would write at least this often,
# also when none of its known inputs changed.
SAVE_CHECK_REFRESH_SECONDS = 30.0

# Below this many minutes the update check interval and the rotation get a hint.
SHORT_INTERVAL_MINUTES = 2
SHORT_UPDATE_INTERVAL_HINT = (
    "Very short: most sources publish every 5-15 minutes (Copernicus every few days); "
    "checks rarely find more.")
MONTH_INTERVAL_NOTE = "Elapsed interval; one month equals 30 days."


def short_update_interval_hint(value, unit):
    """The hint below Update check interval, or "" for an interval of 2 minutes or more."""
    try:
        minutes = update_interval_minutes(value, unit)
    except ValueError:
        return ""
    return SHORT_UPDATE_INTERVAL_HINT if minutes < SHORT_INTERVAL_MINUTES else ""


def update_interval_minutes(value, unit):
    """Convert the friendly update interval selection to elapsed minutes."""
    normalized_unit = str(unit).strip().casefold()
    if normalized_unit not in UPDATE_INTERVAL_UNITS:
        raise ValueError("Update interval unit is invalid.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Update interval value must be a valid number.") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("Update interval must be greater than zero.")
    return numeric * UPDATE_INTERVAL_UNITS[normalized_unit]


def update_interval_selection(minutes):
    """Return the largest exact friendly unit for a minute interval."""
    numeric = float(minutes)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("Update interval must be greater than zero.")
    for unit, factor in UPDATE_INTERVAL_UNITS.items():
        value = numeric / factor
        if value.is_integer() and 1 <= value <= 60:
            return f"{value:g}", unit
    return f"{numeric:g}", "minutes"


def normalize_download_retries(value):
    if type(value) is int and 1 <= value <= 9:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9]", value.strip()):
        return int(value.strip())
    raise ValueError("Download retries must be a whole number from 1 to 9.")


def normalize_profile_cache_max_size_gb(value, name="The profile cache size limit"):
    """Return a profile cache size limit: 0.5 to 10 GB in steps of 0.5 GB."""
    low, high = PROFILE_CACHE_MAX_SIZE_GB_RANGE
    message = f"{name} must be {low:g} to {high:g} GB in steps of {PROFILE_CACHE_MAX_SIZE_GB_STEP:g} GB."
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(message) from None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(message)
    steps = value / PROFILE_CACHE_MAX_SIZE_GB_STEP
    if not low <= value <= high or abs(steps - round(steps)) > 1e-9:
        raise ValueError(message)
    return round(steps) * PROFILE_CACHE_MAX_SIZE_GB_STEP


def normalize_profile_cache_variants(value):
    """Return the pictures kept per profile: a whole number from 1 to 10."""
    low, high = PROFILE_CACHE_VARIANTS_RANGE
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}", value.strip()):
        value = int(value.strip())
    if type(value) is int and low <= value <= high:
        return value
    raise ValueError(f"Profile cache variants per profile must be a whole number from {low} to {high}.")


def normalize_profile_cache_snapshot_size_gb(value):
    """Return the Latest snapshot's own cache size limit (as the main limit)."""
    return normalize_profile_cache_max_size_gb(value, "The Latest snapshot size limit")


def profile_cache_max_bytes(max_size_gb=None):
    """The size limit in bytes; GB are decimal, as in the storage status."""
    gb = PROFILE_CACHE_MAX_SIZE_GB if max_size_gb is None else max_size_gb
    return round(gb * 1_000_000_000)


def profile_cache_snapshot_bytes(size_gb=None):
    """The Latest snapshot's own size limit in bytes."""
    gb = PROFILE_CACHE_SNAPSHOT_SIZE_GB if size_gb is None else size_gb
    return round(gb * 1_000_000_000)


def normalize_catalogue_retries(value):
    if type(value) is int and 1 <= value <= 9:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9]", value.strip()):
        return int(value.strip())
    raise ValueError("Catalogue retries must be a whole number from 1 to 9.")


def format_download_speed(bytes_per_second, unit):
    """Format a byte rate in the user's selected decimal transfer unit."""
    speed = max(0.0, float(bytes_per_second))
    unit = normalize_download_speed_unit(unit)
    if unit == "automatic":
        if speed >= 1_000_000:
            unit = "MB/s"
        elif speed >= 1_000:
            unit = "KB/s"
        else:
            return f"{speed:.0f} B/s"
    if unit == "KB/s":
        return f"{speed / 1_000:.1f} KB/s"
    if unit == "MB/s":
        return f"{speed / 1_000_000:.2f} MB/s"
    return f"{speed * 8 / 1_000_000:.2f} Mbit/s"


def format_download_progress(snapshot, show_speed=True, speed_unit="automatic",
                             show_progress=True, show_size=None):
    """Format the optional settings-footer transfer status.

    ``show_progress`` shows the percentage (or finished parts), ``show_size``
    the downloaded size; without ``show_size`` both follow ``show_progress``.
    """
    if show_size is None:
        show_size = show_progress
    parts = []
    total = snapshot.get("total")
    transferred = int(snapshot.get("transferred", 0))
    expected = snapshot.get("expected_requests")
    if total is None and expected and expected > 1:
        # Tiles and layers: the total is exact only once every part has started.
        if show_progress:
            parts.append(f"{snapshot.get('finished_requests', 0)}/{expected} parts")
        if show_size:
            parts.append(f"{format_bytes(transferred)} downloaded")
    elif total is None:
        if show_size:
            parts.append(f"{format_bytes(transferred)} downloaded · total size unknown")
    else:
        if show_progress:
            parts.append(f"{float(snapshot.get('percent') or 0.0):.0f}%")
        if show_size:
            parts.append(f"{format_bytes(transferred)} / {format_bytes(total)}")
    if show_speed:
        parts.append(format_download_speed(snapshot.get("speed", 0.0), speed_unit))
    if not parts:
        return ""
    if snapshot.get("active") and snapshot.get("cancel_requested"):
        prefix = "Cancelling download"
    elif snapshot.get("active"):
        prefix = "Downloading"
    elif snapshot.get("cancelled"):
        prefix = "Download cancelled"
    else:
        prefix = "Downloaded" if snapshot.get("successful") else "Download interrupted"
    return prefix + ": " + " · ".join(parts)


def download_completion_text(snapshot):
    return "Completed." if (snapshot.get("visible") and snapshot.get("successful")
                            and not snapshot.get("active")) else ""


def version_tuple(value):
    """Return a comparable numeric version tuple for v1.2.3-style tags."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})(?:[-+].*)?", str(value).strip())
    if not match:
        raise ValueError(f"Unsupported version tag: {value}")
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def support_icon(kind, color, size=20):
    """A monochrome Support icon ("star", "coffee" or "heart") in ``color``, as a PIL image."""
    from PIL import Image, ImageDraw
    # Drawn four times larger, then reduced: smooth edges at 20 px.
    s = 4 * size / 20
    image = Image.new("RGBA", (4 * size, 4 * size), (*color, 0))
    draw = ImageDraw.Draw(image)
    if kind == "star":
        points = []
        for index in range(10):
            angle = -math.pi / 2 + index * math.pi / 5
            radius = 20 * s * (0.45 if index % 2 == 0 else 0.19)
            points.append((10 * s + math.cos(angle) * radius, 10.6 * s + math.sin(angle) * radius))
        draw.polygon(points, fill=color)
    elif kind == "heart":
        # A neutral donation sign; the PayPal logo is a trademark.
        points = []
        for step in range(200):
            t = 2 * math.pi * step / 200
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            points.append((10 * s + 0.52 * s * x, 10.4 * s - 0.52 * s * y))
        draw.polygon(points, fill=color)
    else:
        # A mug with its handle and two lines of steam.
        draw.rounded_rectangle((2.5 * s, 7 * s, 14 * s, 18 * s), radius=2 * s, fill=color)
        draw.ellipse((11.5 * s, 9 * s, 18.5 * s, 15.5 * s), outline=color, width=round(1.8 * s))
        for x in (6.2, 10.2):
            draw.line(((x * s, 5.5 * s), ((x - 0.9) * s, 3.5 * s), (x * s, 1.5 * s)),
                      fill=color, width=round(1.3 * s), joint="curve")
    return image.resize((size, size), Image.LANCZOS)


def check_github_update(current_version=VERSION):
    """Read the newest public GitHub release, falling back to the newest tag."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def read_json(url):
        request = Request(url, headers=headers)
        with open_response(urlopen, request, timeout=min(float(NETWORK_TIMEOUT_SECONDS), 30.0)) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_GITHUB_RESPONSE_BYTES:
                raise RuntimeError("GitHub returned an unexpectedly large response.")
            data = response.read(MAX_GITHUB_RESPONSE_BYTES + 1)
            if len(data) > MAX_GITHUB_RESPONSE_BYTES:
                raise RuntimeError("GitHub returned an unexpectedly large response.")
            return json.loads(data.decode("utf-8"))

    release_url = None
    try:
        payload = read_json(GITHUB_LATEST_RELEASE_API)
        tag = payload.get("tag_name")
        release_url = payload.get("html_url")
    except HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError(f"GitHub update check failed with HTTP {exc.code}.") from exc
        try:
            tags = read_json(GITHUB_TAGS_API)
        except HTTPError as tag_error:
            if tag_error.code == 404:
                raise RuntimeError(
                    "No public GitHub release or version tag is accessible. "
                    "A private repository cannot be checked without GitHub authentication."
                ) from tag_error
            raise RuntimeError(
                f"GitHub update check failed with HTTP {tag_error.code}."
            ) from tag_error
        if not isinstance(tags, list) or not tags:
            raise RuntimeError(
                "No public GitHub release or version tag is available."
            ) from exc
        tag = tags[0].get("name")
        release_url = PROJECT_URL + "/releases"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub update check failed: {exc}") from exc
    if not isinstance(tag, str) or not tag.strip():
        raise RuntimeError("GitHub returned no usable version tag.")
    if not isinstance(release_url, str) or not release_url.startswith(PROJECT_URL):
        release_url = PROJECT_URL + "/releases"
    return {
        "current": str(current_version),
        "latest": tag.strip(),
        "update_available": version_tuple(tag) > version_tuple(current_version),
        "url": release_url,
    }


def should_show_update_notification(result, skipped_version=""):
    """Show only a newer published release that has not been skipped."""
    if not result.get("update_available"):
        return False
    latest = result.get("latest", "")
    url = result.get("url", "")
    if not isinstance(url, str) or not url.startswith(PROJECT_URL + "/releases/tag/"):
        return False
    try:
        return not skipped_version or version_tuple(latest) != version_tuple(skipped_version)
    except ValueError:
        return True


def load_configuration(config_path):
    """Load optional user settings and update the script defaults."""
    global WMS_URL, WMS_VERSION, IMAGE_TIME, NETWORK_TIMEOUT_SECONDS
    global UPDATE_INTERVAL_MINUTES, RUN_CONTINUOUSLY, RENDER_MODE, LOCATION_SEARCH_ENDPOINT
    global WIDTH, HEIGHT, ASPECT_RATIO, BACKGROUND_COLOR, RENDER_SCALE
    global RENDER_SCALE_AUTOMATIC, RENDER_SCALE_INHERITED, DISPLAY_RENDER_SCALE
    global OUTPUT_ROOT_WINDOWS, OUTPUT_ROOT_LINUX
    global CUSTOM_LATEST_FOLDER, CUSTOM_HISTORY_FOLDER
    global PROJECTION, VIEW_PRESET, CUSTOM_BBOX, VIEW_MODE, ZOOM
    global TRUECOLOR_BLACK_NIGHT
    global ENABLE_HISTORY, HISTORY_RETENTION_MODE, HISTORY_MAX_FILES
    global HISTORY_RETENTION_YEARS, HISTORY_RETENTION_MONTHS
    global HISTORY_RETENTION_DAYS, HISTORY_RETENTION_HOURS
    global HISTORY_RETENTION_MINUTES, PROFILE_HISTORY_POLICIES, SET_WINDOWS_WALLPAPER
    global PROFILE_CACHE_MAX_SIZE_GB, PROFILE_CACHE_VARIANTS, PROFILE_CACHE_SNAPSHOT_SIZE_GB
    global WINDOWS_WALLPAPER_POSITION, WINDOWS_WALLPAPER_MONITOR_POSITIONS, WINDOWS_WALLPAPER_PAUSED
    global WINDOWS_WALLPAPER_MONITOR_OUTPUTS
    global WINDOWS_PAUSE_ON_EXIT, LAYER_CONFIG
    global ACTIVE_CONFIG_PATH, ACTIVE_PROFILE_LIBRARY_PATH
    global IMAGE_SOURCE, SOURCE_PROFILES, CHECK_FOR_SOURCE_UPDATES, IMAGE_PROFILE_LIBRARY
    global COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET
    global COPERNICUS_CLIENT_SECRET_PROTECTED
    global DISPLAY_TIME_ZONE, APPEARANCE, SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT, SETTINGS_TAB
    global SHOW_DOWNLOAD_SPEED, DOWNLOAD_SPEED_UNIT, SHOW_DOWNLOAD_PROGRESS, SHOW_DOWNLOAD_SIZE
    global SHOW_DOWNLOAD_PROGRESS_BAR, KEEP_COMPLETED_DOWNLOAD_VISIBLE
    global DOWNLOAD_RETRIES, CATALOGUE_RETRIES, PROFILE_LIST_VISIBLE_COLUMNS, APPLIED_PROFILE_ID
    global PROFILE_LIST_SORT_COLUMN, PROFILE_LIST_SORT_DESCENDING
    global PROFILE_LIST_COLUMN_WIDTHS, PROFILE_LIST_TABLE_ROWS
    global PROFILE_LIST_COLUMN_ORDER, CATALOGUE_REFRESH_TIME
    global SKIPPED_UPDATE_VERSION, LOADED_SETTINGS_FILES

    LOADED_SETTINGS_FILES = None
    config_path = resolve_script_relative_path(config_path)
    ACTIVE_CONFIG_PATH = config_path
    ACTIVE_PROFILE_LIBRARY_PATH = Path(PROFILE_LIBRARY_PATH).resolve()
    if not config_path.exists():
        if config_path != DEFAULT_CONFIG_PATH:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        if DEFAULT_CONFIG_TEMPLATE_PATH.exists():
            initial_text = first_run_configuration_text(
                DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            )
            temporary = config_path.with_name(
                f".{config_path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(initial_text, encoding="utf-8", newline="")
                os.replace(temporary, config_path)
            finally:
                temporary.unlink(missing_ok=True)
            log(f"Created configuration from template: {config_path.name}")
        else:
            refresh_output_paths()
            return False

    config_bytes = config_path.read_bytes()
    config = tomllib.loads(config_bytes.decode("utf-8"))
    profile_bytes = None

    if config_path.resolve() == DEFAULT_CONFIG_TEMPLATE_PATH.resolve():
        IMAGE_PROFILE_LIBRARY = normalize_library({})
    else:
        with CONFIGURATION_FILE_LOCK:
            if ACTIVE_PROFILE_LIBRARY_PATH.exists():
                profile_bytes = ACTIVE_PROFILE_LIBRARY_PATH.read_bytes()
                IMAGE_PROFILE_LIBRARY = read_profile_library_file(
                    ACTIVE_PROFILE_LIBRARY_PATH
                )
            else:
                IMAGE_PROFILE_LIBRARY = normalize_library({})
                write_profile_library_file_unlocked(
                    IMAGE_PROFILE_LIBRARY, ACTIVE_PROFILE_LIBRARY_PATH
                )
    DISPLAY_TIME_ZONE = normalize_time_zone(
        config.get("display", {}).get("time_zone", "system")
    )
    APPEARANCE = normalize_appearance(config.get("display", {}).get("appearance", "system"))
    SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT = normalize_settings_window_size(config.get("display", {}))
    SETTINGS_TAB = normalize_settings_tab(config.get("display", {}))

    source = config.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("TOML 'source' must be a table.")
    if "check_for_updates" in source and type(source["check_for_updates"]) is not bool:
        raise ValueError("source.check_for_updates must be true or false.")
    CHECK_FOR_SOURCE_UPDATES = source.get("check_for_updates", True)
    configured_profiles = deepcopy(config.get("sources", {}))
    if isinstance(configured_profiles, dict):
        copernicus = configured_profiles.get("copernicus")
        if isinstance(copernicus, dict) and "gap_fill_mode" in copernicus:
            copernicus["coverage_mode"] = copernicus.pop("gap_fill_mode")
    IMAGE_SOURCE, SOURCE_PROFILES = normalize_source_configuration(
        source.get("provider", "eumetsat"), configured_profiles,
    )
    (COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET,
     COPERNICUS_CLIENT_SECRET_PROTECTED) = normalize_auth_configuration(
        config.get("copernicus", {})
    )

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
    location_endpoint = service.get("location_search_endpoint", DEFAULT_LOCATION_SEARCH_ENDPOINT)
    if not isinstance(location_endpoint, str) or not location_endpoint.strip().lower().startswith("https://"):
        raise ValueError("TOML 'service.location_search_endpoint' must be an https:// URL.")
    LOCATION_SEARCH_ENDPOINT = location_endpoint.strip()

    download = config.get("download", {})
    if not isinstance(download, dict):
        raise ValueError("TOML 'download' must be a table.")
    for key in (
        "show_speed", "show_progress", "show_size", "show_progress_bar",
        "keep_completed_visible",
    ):
        if key in download and type(download[key]) is not bool:
            raise ValueError(f"download.{key} must be true or false.")
    SHOW_DOWNLOAD_SPEED = download.get("show_speed", DEFAULT_SHOW_DOWNLOAD_SPEED)
    DOWNLOAD_SPEED_UNIT = normalize_download_speed_unit(
        download.get("speed_unit", DEFAULT_DOWNLOAD_SPEED_UNIT)
    )
    SHOW_DOWNLOAD_PROGRESS = download.get(
        "show_progress", DEFAULT_SHOW_DOWNLOAD_PROGRESS
    )
    # Older files had one switch for percentage and size; the size keeps it.
    SHOW_DOWNLOAD_SIZE = download.get(
        "show_size", download.get("show_progress", DEFAULT_SHOW_DOWNLOAD_SIZE)
    )
    SHOW_DOWNLOAD_PROGRESS_BAR = download.get(
        "show_progress_bar", DEFAULT_SHOW_DOWNLOAD_PROGRESS_BAR
    )
    KEEP_COMPLETED_DOWNLOAD_VISIBLE = download.get(
        "keep_completed_visible", DEFAULT_KEEP_COMPLETED_DOWNLOAD_VISIBLE
    )
    DOWNLOAD_RETRIES = normalize_download_retries(
        download.get("retries", DEFAULT_DOWNLOAD_RETRIES)
    )
    CATALOGUE_RETRIES = normalize_catalogue_retries(
        download.get("catalogue_retries", DEFAULT_CATALOGUE_RETRIES)
    )
    CATALOGUE_REFRESH_TIME = normalize_refresh_time(download.get("catalogue_refresh_time", DEFAULT_REFRESH_TIME))
    profile_cache = config.get("cache", {})
    if not isinstance(profile_cache, dict):
        raise ValueError("TOML 'cache' must be a table.")
    # Missing keys mean the defaults, not the previously loaded values.
    PROFILE_CACHE_MAX_SIZE_GB = normalize_profile_cache_max_size_gb(
        profile_cache.get("max_size_gb", DEFAULT_PROFILE_CACHE_MAX_SIZE_GB)
    )
    PROFILE_CACHE_VARIANTS = normalize_profile_cache_variants(
        profile_cache.get("variants_per_profile", DEFAULT_PROFILE_CACHE_VARIANTS)
    )
    PROFILE_CACHE_SNAPSHOT_SIZE_GB = normalize_profile_cache_snapshot_size_gb(
        profile_cache.get("latest_snapshot_size_gb", DEFAULT_PROFILE_CACHE_SNAPSHOT_SIZE_GB)
    )

    latest_snapshot.from_config(config, lambda value: strict_settings(value, normalize_image_settings_snapshot))
    profile_list = config.get("profile_list", {})
    if not isinstance(profile_list, dict):
        raise ValueError("TOML 'profile_list' must be a table.")
    configured_columns = profile_list.get(
        "visible_columns", list(DEFAULT_PROFILE_LIST_COLUMNS)
    )
    if not isinstance(configured_columns, list):
        raise ValueError("profile_list.visible_columns must be a list.")
    columns_version = profile_list.get("columns_version", 1)
    if type(columns_version) is not int or columns_version not in range(1, 20):
        raise ValueError("profile_list.columns_version must be between 1 and 19.")
    if columns_version == 1 and "visible_columns" in profile_list:
        configured_columns = [
            "gap_fill" if column == "coverage" else column
            for column in configured_columns
        ]
        configured_columns.extend(
            column for column in ("cloud_coverage", "mosaic_brightness")
            if column not in configured_columns
        )
    if columns_version in (1, 2) and "visible_columns" in profile_list:
        configured_columns.extend(
            column for column in ("quarter_mode", "quarter_offset", "quarter_target")
            if column not in configured_columns
        )
    if columns_version < 4 and "id" not in configured_columns:
        configured_columns.append("id")
    if columns_version < 5:
        configured_columns.extend(
            column for column in ("zoom", "maximum_lookback", "output_resolution", "map_labels", "cache_status")
            if column not in configured_columns
        )
    if columns_version < 6 and "last_download" not in configured_columns:
        configured_columns.append("last_download")
    if columns_version < 7 and "no_data_color" not in configured_columns:
        configured_columns.append("no_data_color")
    if columns_version < 8:
        configured_columns.extend(key for key in ("image_size", "mosaic_contrast", "auto_brightness", "auto_contrast")
                                  if key not in configured_columns)
    if columns_version < 9 and "visible_columns" not in profile_list:
        configured_columns.extend(key for key in ("rotation_enabled", "data_coverage") if key not in configured_columns)
    if columns_version < 10 and "active" not in configured_columns:
        name_index = configured_columns.index("name") + 1 if "name" in configured_columns else 0
        configured_columns.insert(name_index, "active")
    column_order = profile_list.get("column_order")
    if columns_version < 11:
        # The History column appears visible next to Active.
        if "history" not in configured_columns:
            anchor = next((column for column in ("active", "name") if column in configured_columns), None)
            configured_columns.insert(configured_columns.index(anchor) + 1 if anchor else 0, "history")
        if isinstance(column_order, list) and "history" not in column_order and "active" in column_order:
            column_order = list(column_order)
            column_order.insert(column_order.index("active") + 1, "history")
    if columns_version < 12:
        # Country borders got their own column next to the (map) labels.
        if "map_labels" in configured_columns and "map_borders" not in configured_columns:
            configured_columns.insert(configured_columns.index("map_labels") + 1, "map_borders")
        if isinstance(column_order, list) and "map_borders" not in column_order and "map_labels" in column_order:
            column_order = list(column_order)
            column_order.insert(column_order.index("map_labels") + 1, "map_borders")
    if columns_version < 13:
        # Short ID stays hidden in saved layouts but follows Profile ID in a saved order.
        if isinstance(column_order, list) and "short_id" not in column_order and "id" in column_order:
            column_order = list(column_order)
            column_order.insert(column_order.index("id") + 1, "short_id")
    if columns_version < 14:
        # The Updates column (Imagery updates) appears visible next to History.
        if "image_updates" not in configured_columns:
            anchor = next((column for column in ("history", "active", "name") if column in configured_columns), None)
            configured_columns.insert(configured_columns.index(anchor) + 1 if anchor else 0, "image_updates")
        if isinstance(column_order, list) and "image_updates" not in column_order and "history" in column_order:
            column_order = list(column_order)
            column_order.insert(column_order.index("history") + 1, "image_updates")
    if columns_version < 15:
        # Resolution (the picture's real size) stays hidden but follows Resolution selection.
        if (isinstance(column_order, list) and "resolution" not in column_order
                and "output_resolution" in column_order):
            column_order = list(column_order)
            column_order.insert(column_order.index("output_resolution") + 1, "resolution")
    if columns_version < 17:
        # Shorelines (Himawari) stays hidden but follows Country borders in a saved order.
        if (isinstance(column_order, list) and "shorelines" not in column_order
                and "map_borders" in column_order):
            column_order = list(column_order)
            column_order.insert(column_order.index("map_borders") + 1, "shorelines")
    if columns_version < 18:
        # The Status symbols got a column of their own, visible left of Status.
        if "active" in configured_columns and "status_symbol" not in configured_columns:
            configured_columns.insert(configured_columns.index("active"), "status_symbol")
        if isinstance(column_order, list) and "status_symbol" not in column_order and "active" in column_order:
            column_order = list(column_order)
            column_order.insert(column_order.index("active"), "status_symbol")
    if columns_version < 19:
        # Time was split: Time selection (the choice) shows left of Time (the date).
        if "time" in configured_columns and "time_selection" not in configured_columns:
            configured_columns.insert(configured_columns.index("time"), "time_selection")
        if isinstance(column_order, list) and "time_selection" not in column_order and "time" in column_order:
            column_order = list(column_order)
            column_order.insert(column_order.index("time"), "time_selection")
    if "visible_columns" not in profile_list:
        # Without a saved choice the table shows the default columns.
        configured_columns = list(DEFAULT_VISIBLE_PROFILE_COLUMNS)
    # Version 16 split Selection into Satellite / mission, Product and Layer at its place.
    configured_columns = split_selection_column(configured_columns)
    column_order = split_selection_column(column_order)
    PROFILE_LIST_COLUMN_ORDER = normalize_profile_column_order(column_order)
    APPLIED_PROFILE_ID = profile_list.get("applied_profile_id", "")
    if not isinstance(APPLIED_PROFILE_ID, str) or (APPLIED_PROFILE_ID and re.fullmatch(r"[0-9a-f]{32}", APPLIED_PROFILE_ID) is None):
        raise ValueError("profile_list.applied_profile_id must be empty or a profile UUID.")
    if (
        any(type(column) is not str for column in configured_columns)
        or len(set(configured_columns)) != len(configured_columns)
        or any(column not in DEFAULT_PROFILE_LIST_COLUMNS for column in configured_columns)
    ):
        raise ValueError("profile_list.visible_columns contains invalid columns.")
    PROFILE_LIST_VISIBLE_COLUMNS = tuple(
        column for column in PROFILE_LIST_COLUMN_ORDER
        if column in configured_columns
    )
    PROFILE_LIST_SORT_COLUMN = profile_list.get("sort_column", "")
    if PROFILE_LIST_SORT_COLUMN == "selection":
        PROFILE_LIST_SORT_COLUMN = "mission"
    PROFILE_LIST_SORT_DESCENDING = profile_list.get("sort_descending", False)
    if not isinstance(PROFILE_LIST_SORT_COLUMN, str) or PROFILE_LIST_SORT_COLUMN not in ("", *DEFAULT_PROFILE_LIST_COLUMNS):
        raise ValueError("profile_list.sort_column must be empty or a valid profile column.")
    if type(PROFILE_LIST_SORT_DESCENDING) is not bool:
        raise ValueError("profile_list.sort_descending must be true or false.")
    if not PROFILE_LIST_SORT_COLUMN:
        PROFILE_LIST_SORT_DESCENDING = False
    column_widths = profile_list.get("column_widths", {})
    if isinstance(column_widths, dict):
        column_widths = {key: value for key, value in column_widths.items() if key != "selection"}
    PROFILE_LIST_COLUMN_WIDTHS = normalize_profile_column_widths(column_widths)
    PROFILE_LIST_TABLE_ROWS = normalize_profile_table_rows(profile_list.get("table_rows", DEFAULT_PROFILE_TABLE_ROWS))

    output = config.get("output", {})
    CUSTOM_LATEST_FOLDER = str(output.get("latest_folder", "")).strip()
    WIDTH = output.get("width", WIDTH)
    configured_height = output.get("height", HEIGHT)
    HEIGHT = None if configured_height in (None, 0) else configured_height
    configured_ratio = output.get("aspect_ratio", ASPECT_RATIO)
    ASPECT_RATIO = configured_ratio or None
    BACKGROUND_COLOR = str(output.get("background_color", BACKGROUND_COLOR))
    configured_render_scale = parse_render_scale_setting(
        output.get("render_scale", get_render_scale_setting()), allow_default=True
    )
    RENDER_SCALE_INHERITED = configured_render_scale == "default"
    RENDER_SCALE_AUTOMATIC = configured_render_scale == "auto"
    RENDER_SCALE = (
        1.0 if isinstance(configured_render_scale, str) else float(configured_render_scale)
    )
    # Before General had its own value, the displays used the image's render
    # quality; a file without the key keeps that behaviour.
    if type(output.get("display_render_scale")) is bool:
        raise ValueError("output.display_render_scale must be 'auto' or a numeric factor.")
    DISPLAY_RENDER_SCALE = parse_render_scale_setting(output.get(
        "display_render_scale",
        "auto" if configured_render_scale == "default" else configured_render_scale,
    ))
    if "windows_root" in output:
        OUTPUT_ROOT_WINDOWS = resolve_script_relative_path(output["windows_root"])
    if "linux_root" in output:
        OUTPUT_ROOT_LINUX = resolve_script_relative_path(output["linux_root"])

    view = config.get("view", {})
    PROJECTION = str(view.get("projection", PROJECTION))
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
    raw_policies = history.get("profile_policies", "{}")
    try:
        parsed_policies = json.loads(raw_policies) if isinstance(raw_policies, str) else raw_policies
    except json.JSONDecodeError as exc:
        raise ValueError("history.profile_policies must be valid JSON.") from exc
    if not isinstance(parsed_policies, dict):
        raise ValueError("history.profile_policies must be an object.")
    PROFILE_HISTORY_POLICIES = {}
    for identifier, policy in parsed_policies.items():
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None or not isinstance(policy, dict):
            raise ValueError("history.profile_policies contains an invalid profile entry.")
        PROFILE_HISTORY_POLICIES[identifier] = _normalize_history_policy(policy)

    windows = config.get("windows", {})
    SET_WINDOWS_WALLPAPER = windows.get(
        "set_wallpaper", SET_WINDOWS_WALLPAPER
    )
    WINDOWS_WALLPAPER_POSITION = str(
        windows.get("position", WINDOWS_WALLPAPER_POSITION)
    ).lower()
    monitor_positions = windows.get("monitor_positions", {})
    if isinstance(monitor_positions, str):
        try:
            monitor_positions = json.loads(monitor_positions)
        except json.JSONDecodeError as exc:
            raise ValueError("windows.monitor_positions is invalid JSON.") from exc
    if not isinstance(monitor_positions, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        or value.lower() not in WALLPAPER_POSITION_CHOICES
        for key, value in monitor_positions.items()
    ):
        raise ValueError("windows.monitor_positions contains invalid monitor settings.")
    WINDOWS_WALLPAPER_MONITOR_POSITIONS = {
        key: value.lower() for key, value in monitor_positions.items()
    }
    (WINDOWS_WALLPAPER_POSITION, WINDOWS_WALLPAPER_MONITOR_POSITIONS,
     WINDOWS_WALLPAPER_PAUSED) = migrate_paused_positions(
        WINDOWS_WALLPAPER_POSITION, WINDOWS_WALLPAPER_MONITOR_POSITIONS,
        normalize_paused_displays(windows.get("paused_displays", "[]")))
    WINDOWS_WALLPAPER_MONITOR_OUTPUTS = normalize_monitor_output_settings(
        windows.get("monitor_output_settings", {}),
        {"width": WIDTH, "height": HEIGHT or 0, "aspect_ratio": ASPECT_RATIO,
         "render_scale": DISPLAY_RENDER_SCALE, "background_color": BACKGROUND_COLOR},
    )
    WINDOWS_PAUSE_ON_EXIT = windows.get("pause_on_error", WINDOWS_PAUSE_ON_EXIT)

    updates = config.get("updates", {})
    if not isinstance(updates, dict):
        raise ValueError("TOML 'updates' must be a table.")
    skipped_version = updates.get("skipped_version", "")
    if not isinstance(skipped_version, str):
        raise ValueError("updates.skipped_version must be a string.")
    if skipped_version:
        try:
            version_tuple(skipped_version)
        except ValueError as exc:
            raise ValueError("updates.skipped_version is not a version tag.") from exc
    SKIPPED_UPDATE_VERSION = skipped_version

    if "layers" in config:
        if not isinstance(config["layers"], list):
            raise ValueError("TOML 'layers' must be an array of tables.")
        LAYER_CONFIG = [dict(entry) for entry in config["layers"]]

    refresh_output_paths()
    # Exactly the bytes that loaded, for the automatic working-settings backup.
    LOADED_SETTINGS_FILES = (config_path.resolve(), config_bytes, profile_bytes)
    log(f"Loaded configuration: {config_path.resolve()}")
    return True


def capture_loaded_configuration():
    """Capture live settings so a failed hot reload can be rolled back."""
    state = {}
    for name in LOADED_CONFIGURATION_FIELDS:
        value = globals()[name]
        if name == "LAYER_CONFIG":
            value = [dict(entry) for entry in value]
        elif name in {"SOURCE_PROFILES", "IMAGE_PROFILE_LIBRARY", "PROFILE_HISTORY_POLICIES", "PROFILE_LIST_COLUMN_WIDTHS",
                      "WINDOWS_WALLPAPER_MONITOR_POSITIONS",
                      "WINDOWS_WALLPAPER_MONITOR_OUTPUTS"}:
            value = deepcopy(value)
        state[name] = value
    return state


def restore_loaded_configuration(state):
    """Restore a previously captured live configuration."""
    for name in LOADED_CONFIGURATION_FIELDS:
        value = state[name]
        if name == "LAYER_CONFIG":
            value = [dict(entry) for entry in value]
        elif name in {"SOURCE_PROFILES", "IMAGE_PROFILE_LIBRARY", "PROFILE_HISTORY_POLICIES", "PROFILE_LIST_COLUMN_WIDTHS",
                      "WINDOWS_WALLPAPER_MONITOR_POSITIONS",
                      "WINDOWS_WALLPAPER_MONITOR_OUTPUTS"}:
            value = deepcopy(value)
        globals()[name] = value
    refresh_output_paths()


# Only settings represented by the Image tab belong to an image profile.
# Output size, background and the Latest folder are device settings
# (DEVICE_OUTPUT_KEYS); a profile's output holds only render_scale.
IMAGE_SETTING_FIELDS = {
    "view": {"projection": "PROJECTION", "preset": "VIEW_PRESET", "bbox": "CUSTOM_BBOX",
             "fit_mode": "VIEW_MODE", "zoom": "ZOOM",
             "truecolor_black_night": "TRUECOLOR_BLACK_NIGHT"},
    "output": {},
}


def default_import_settings():
    config = tomllib.loads(DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"))
    config["view"].setdefault("bbox", [])
    # Output roots and sizes are device settings; a profile keeps only render quality.
    config["output"] = {"render_scale": config["output"]["render_scale"]}
    return normalize_image_settings_snapshot({key: config[key] for key in
        ("source", "sources", "view", "output", "layers")})


def image_settings_snapshot(configuration=None):
    """The loaded Image settings, or those of a captured ``configuration``."""
    values = globals() if configuration is None else configuration

    def value(name):
        return values[name] if name in values else globals()[name]

    snapshot = {section: {key: deepcopy(value(name)) for key, name in fields.items()}
                for section, fields in IMAGE_SETTING_FIELDS.items()}
    snapshot["view"]["bbox"] = list(value("CUSTOM_BBOX") or ())
    snapshot["output"]["render_scale"] = (
        "default" if value("RENDER_SCALE_INHERITED")
        else "auto" if value("RENDER_SCALE_AUTOMATIC") else value("RENDER_SCALE")
    )
    snapshot.update(source={"provider": value("IMAGE_SOURCE"),
                            "check_for_updates": value("CHECK_FOR_SOURCE_UPDATES")},
                    sources=deepcopy(value("SOURCE_PROFILES")),
                    layers=deepcopy(value("LAYER_CONFIG")))
    return snapshot


def normalize_device_output_settings(output):
    """Validate the device output size and background of a settings table offline."""
    for key in ("width", "height"):
        if type(output.get(key)) is not int or output[key] < (1 if key == "width" else 0):
            raise ValueError(f"Output {key} must be a {'positive' if key == 'width' else 'nonnegative'} integer.")
    width, height = output["width"], output["height"]
    aspect = output.get("aspect_ratio", "")
    if aspect is None or aspect == "":
        if height <= 0:
            raise ValueError("Output needs a positive height when no aspect ratio is specified.")
        ratio = width / height
        aspect = ""
    else:
        if type(aspect) not in (str, int, float):
            raise ValueError("Output aspect ratio must be text or a number.")
        ratio = parse_aspect_ratio(aspect)
    try:
        calculated_height = width / ratio
        if not math.isfinite(calculated_height):
            raise ValueError("Output calculated height must be finite.")
        actual_height = height or round(calculated_height)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Output dimensions are outside the supported range.") from exc
    if actual_height <= 0 or (height and abs(width / height - ratio) / ratio > 0.005):
        raise ValueError("Output width and height do not match its aspect ratio.")
    if not isinstance(output.get("background_color", "#000000"), str):
        raise ValueError("Output background color must be text.")
    if "display_render_scale" in output:
        if type(output["display_render_scale"]) is bool:
            raise ValueError("output.display_render_scale must be 'auto' or a numeric factor.")
        parse_render_scale_setting(output["display_render_scale"])
    return {"width": width, "height": height, "aspect_ratio": aspect,
            "background_color": normalize_background_color(output.get("background_color", "#000000"))}


def normalize_image_settings_snapshot(snapshot):
    """Validate a complete Image profile without touching runtime globals or Tk.

    Return an independent copy with normalized known values. Unknown metadata
    in view/output/layer tables is retained. WMS-only validity rules apply to
    EUMETSAT; still-image providers can retain unused WMS settings without changing them.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("Image profile settings must be a table.")
    required = {"source", "sources", "view", "output", "layers"}
    missing = required - snapshot.keys()
    if missing:
        raise ValueError("Image profile is incomplete: missing " + ", ".join(sorted(missing)) + ".")
    for section in required - {"layers"}:
        if not isinstance(snapshot[section], dict):
            raise ValueError(f"Image profile {section} must be a table.")
    if "provider" not in snapshot["source"]:
        raise ValueError("Image profile source.provider is missing.")
    for section, fields in IMAGE_SETTING_FIELDS.items():
        missing = set(fields) - snapshot[section].keys()
        if section == "output" and "render_scale" not in snapshot[section]:
            missing.add("render_scale")
        if missing:
            raise ValueError(f"Image profile {section} is incomplete: " + ", ".join(sorted(missing)) + ".")
    if not isinstance(snapshot["source"]["provider"], str):
        raise ValueError("Image profile source.provider must be text.")
    if type(snapshot["source"].get("check_for_updates", True)) is not bool:
        raise ValueError("Image profile source.check_for_updates must be true or false.")
    result = deepcopy(snapshot)
    provider, profiles = normalize_source_configuration(result["source"]["provider"], result["sources"])
    result["source"]["provider"] = provider
    result["source"]["check_for_updates"] = result["source"].get(
        "check_for_updates", True
    )
    result["sources"] = profiles
    view, output = result["view"], result["output"]
    for key in DEVICE_OUTPUT_KEYS:
        # Device settings in older profiles, PNGs and exports are ignored.
        output.pop(key, None)

    def number(value, label):
        if type(value) not in (int, float):
            raise ValueError(f"Image profile {label} must be a number.")
        try:
            value = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Image profile {label} must be finite.") from exc
        if not math.isfinite(value):
            raise ValueError(f"Image profile {label} must be finite.")
        return value

    for key in ("projection", "preset", "fit_mode"):
        if not isinstance(view[key], str) or not view[key].strip():
            raise ValueError(f"Image profile view.{key} must be nonempty text.")
    for key in ("truecolor_black_night",):
        if type(view[key]) is not bool:
            raise ValueError(f"Image profile view.{key} must be true or false.")
    if view["fit_mode"] not in {"fit", "crop"}:
        raise ValueError("Image profile fit mode must be fit or crop.")
    view["zoom"] = number(view["zoom"], "zoom")
    if view["zoom"] <= 0 or (provider != "eumetsat" and not 0.05 <= view["zoom"] <= 20):
        raise ValueError("Image profile zoom is outside the supported range.")
    bbox = view["bbox"]
    if not isinstance(bbox, (list, tuple)) or len(bbox) not in (0, 4):
        raise ValueError("Image profile bbox must be empty or contain four numbers.")
    view["bbox"] = [number(value, "bbox") for value in bbox]
    if bbox and not (view["bbox"][0] < view["bbox"][2] and view["bbox"][1] < view["bbox"][3]):
        raise ValueError("Image profile bbox must satisfy xmin < xmax and ymin < ymax.")
    if type(output["render_scale"]) is bool:
        raise ValueError("Image profile render quality must be default, auto or a numeric factor.")
    output["render_scale"] = parse_render_scale_setting(output["render_scale"], allow_default=True)
    if not isinstance(result["layers"], list) or any(not isinstance(layer, dict) for layer in result["layers"]):
        raise ValueError("Image profile layers must be a list of tables.")
    if provider == "eumetsat":
        if view["projection"] not in PROJECTIONS or view["preset"] not in VIEW_PRESETS:
            raise ValueError("Image profile contains an unknown EUMETSAT projection or preset.")
        if view["preset"] == "custom" and not view["bbox"]:
            raise ValueError("A custom Image profile requires four bbox values.")
        if (view["preset"] == "custom" and view["projection"] == "Geographic"
                and not geographic_extent_within_world(view["bbox"])):
            raise ValueError("Image profile custom area must stay within longitude -180 to 180 "
                             "and latitude -90 to 90.")
        normalized_layers = []
        for index, layer in enumerate(result["layers"], 1):
            if not isinstance(layer.get("name"), str) or not layer["name"].strip():
                raise ValueError(f"Image profile layer {index} needs a name.")
            if "enabled" in layer and type(layer["enabled"]) is not bool:
                raise ValueError(f"Image profile layer {index} enabled must be true or false.")
            if "opacity" in layer:
                number(layer["opacity"], f"layer {index} opacity")
            for key in ("kind", "style", "time"):
                if key in layer and not isinstance(layer[key], str):
                    raise ValueError(f"Image profile layer {index} {key} must be text.")
            normalized_layers.append(normalize_layer_config(layer, index))
        if not any(layer["enabled"] and layer["opacity"] > 0 for layer in normalized_layers):
            raise ValueError("Image profile requires at least one enabled EUMETSAT layer with nonzero opacity.")
        primary_layer = next(
            (layer["name"] for layer in normalized_layers
             if layer["kind"] == "wms" and layer["enabled"] and layer["opacity"] > 0),
            None,
        )
        if primary_layer:
            result["sources"]["eumetsat"]["layer"] = primary_layer
    # Metadata is preserved only when it is valid, bounded profile data too.
    from marblescape_profiles import toml_value
    toml_value(result)
    return result


def apply_image_settings(snapshot):
    """Atomically apply a complete Image snapshot in the runtime worker only."""
    global IMAGE_SOURCE, SOURCE_PROFILES, CHECK_FOR_SOURCE_UPDATES
    global LAYER_CONFIG, RENDER_SCALE, RENDER_SCALE_AUTOMATIC, RENDER_SCALE_INHERITED
    snapshot = normalize_image_settings_snapshot(snapshot)
    previous = capture_loaded_configuration()
    try:
        provider, profiles = normalize_source_configuration(
            snapshot["source"]["provider"], snapshot["sources"])
        for section, fields in IMAGE_SETTING_FIELDS.items():
            for key, name in fields.items():
                value = deepcopy(snapshot[section][key])
                if name == "HEIGHT":
                    value = value or None
                elif name == "ASPECT_RATIO":
                    value = value or None
                elif name == "CUSTOM_BBOX":
                    value = tuple(value) if value else None
                globals()[name] = value
        scale = parse_render_scale_setting(snapshot["output"]["render_scale"], allow_default=True)
        RENDER_SCALE_INHERITED = scale == "default"
        RENDER_SCALE_AUTOMATIC = scale == "auto"
        RENDER_SCALE = 1.0 if isinstance(scale, str) else float(scale)
        IMAGE_SOURCE, SOURCE_PROFILES = provider, profiles
        CHECK_FOR_SOURCE_UPDATES = snapshot["source"].get("check_for_updates", True)
        if not isinstance(snapshot["layers"], list):
            raise ValueError("Image profile layers must be a list.")
        LAYER_CONFIG = deepcopy(snapshot["layers"])
        validate_configuration()
        refresh_output_paths()
    except Exception:
        restore_loaded_configuration(previous)
        raise


def replace_image_settings(text, snapshot):
    """Persist an Image form/profile while retaining General and storage settings."""
    if snapshot["source"]["provider"] == "eumetsat" and not has_visible_layer(snapshot["layers"]):
        # Startup would refuse such a file; keep the working one instead.
        raise ValueError(
            "EUMETSAT needs at least one enabled layer with opacity above zero. Nothing was saved."
        )
    updated = replace_source_configuration(
        text,
        snapshot["source"]["provider"],
        snapshot["sources"],
        snapshot["source"].get("check_for_updates", True),
    )
    for section, fields in IMAGE_SETTING_FIELDS.items():
        keys = (*fields, "render_scale") if section == "output" else fields
        for key in keys:
            value = snapshot[section][key]
            updated = replace_toml_section_value(updated, section, key, value)
    # Preserve every saved layer, including overlays and disabled custom layers.
    from marblescape_profiles import table_spans, toml_value
    for start, end in reversed(table_spans(updated, "layers")):
        updated = updated[:start] + updated[end:]
    first_header = re.search(r"(?m)^[ \t]*\[", updated)
    root_end = first_header.start() if first_header else len(updated)
    root_text = re.sub(r"(?m)^layers\s*=.*(?:\r?\n|$)", "", updated[:root_end])
    updated = root_text + updated[root_end:]
    newline = "\r\n" if "\r\n" in text else "\n"
    updated = updated.rstrip() + newline + newline
    if not snapshot["layers"]:
        updated = "layers = []" + newline + updated
    for layer in snapshot["layers"]:
        updated += "[[layers]]" + newline
        updated += newline.join(
            f'{key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else toml_value(key)} = {toml_value(value)}'
            for key, value in layer.items())
        updated += newline + newline
    tomllib.loads(updated)
    return updated


def set_rotation_status(text, deadline=None, active_profile_id=_ROTATION_ACTIVE_UNCHANGED):
    with ROTATION_STATUS_LOCK:
        ROTATION_STATUS.update(text=text, deadline=deadline)
        if active_profile_id is not _ROTATION_ACTIVE_UNCHANGED:
            ROTATION_STATUS["active_profile_id"] = active_profile_id


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


def copernicus_auto_size(fallback, monitor_outputs=None, monitors=None, paused=None):
    """The output size enlarged for every active display (a paused one does not count).

    Each display's physical size and its own Monitor output: the picture keeps the
    shared aspect ratio and never needs enlarging for a display. Every source uses
    it since 2026-10-08; Copernicus's Image resolution Auto was the first.
    """
    from marblescape_copernicus import resolve_image_size
    outputs = WINDOWS_WALLPAPER_MONITOR_OUTPUTS if monitor_outputs is None else monitor_outputs
    if monitors is None:
        try:
            monitors = list_windows_wallpaper_monitors() if os.name == "nt" else []
        except Exception:
            monitors = []
    targets = []
    for monitor in monitors:
        if display_paused(monitor["id"], paused):
            continue
        left, top, right, bottom = monitor["rect"]
        targets.append((right - left, bottom - top))
        override = outputs.get(monitor["id"], {})
        if override:
            width = int(override.get("width", fallback[0]))
            height = int(override.get("height", 0)) or round(width / parse_aspect_ratio(
                override.get("aspect_ratio", f"{fallback[0]}:{fallback[1]}")))
            targets.append((width, height))
    return resolve_image_size({"image_size": "auto"}, fallback, targets)


def normalize_paused_displays(value):
    """The saved list of paused display IDs (a JSON text or a list)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("windows.paused_displays is invalid JSON.") from exc
    if not isinstance(value, (list, tuple, set, frozenset)) or any(
            not isinstance(item, str) or not item or len(item) > 500 for item in value):
        raise ValueError("windows.paused_displays must list display IDs.")
    return frozenset(value)


def migrate_paused_positions(position, monitor_positions, paused):
    """Older "Do not update (keep current wallpaper)" positions become pauses.

    Returns (shared position, display positions, paused displays): a paused
    display keeps a real position for when it is resumed (the shared one).
    """
    paused = set(paused)
    positions = {}
    for identifier, value in monitor_positions.items():
        if value == "none":
            paused.add(identifier)
        else:
            positions[identifier] = value
    if position == "none":
        paused.add(PAUSE_ALL_DISPLAYS)
        position = "fill"
    return position, positions, frozenset(paused)


def display_paused(monitor_id, paused=None):
    paused = WINDOWS_WALLPAPER_PAUSED if paused is None else paused
    return PAUSE_ALL_DISPLAYS in paused or monitor_id in paused


def effective_wallpaper_positions(position=None, monitor_positions=None, paused=None):
    """(shared position, display positions) as the wallpaper code takes them:
    a paused display, or every display with "*", is "none"."""
    position = WINDOWS_WALLPAPER_POSITION if position is None else position
    positions = dict(WINDOWS_WALLPAPER_MONITOR_POSITIONS if monitor_positions is None else monitor_positions)
    paused = WINDOWS_WALLPAPER_PAUSED if paused is None else paused
    if PAUSE_ALL_DISPLAYS in paused:
        return "none", {identifier: "none" for identifier in positions}
    for identifier in paused:
        positions[identifier] = "none"
    return position, positions


def split_selection_column(columns):
    """A saved column list with the former Selection replaced by its three columns."""
    if not isinstance(columns, list) or "selection" not in columns:
        return columns
    index = columns.index("selection")
    parts = [column for column in ("mission", "product", "layer") if column not in columns]
    return columns[:index] + parts + columns[index + 1:]


def get_image_dimensions():
    """Saved source PNG dimensions, separate from per-monitor wallpaper placement."""
    return image_dimensions_for(IMAGE_SOURCE, SOURCE_PROFILES.get("copernicus", {}))


def image_dimensions_for(provider, copernicus_profile):
    """Saved PNG dimensions of a source selection with the current device settings."""
    fallback = get_output_dimensions()
    if provider != "copernicus":
        return displays_image_size(provider, fallback)
    from marblescape_copernicus import resolve_image_size
    return (copernicus_auto_size(fallback) if copernicus_profile.get("image_size", "auto") == "auto"
            else resolve_image_size(copernicus_profile, fallback))


def displays_image_size(provider, fallback, monitor_outputs=None, paused=None):
    """The picture as large as the largest active display needs, in the shared aspect ratio.

    A smaller shared output would be enlarged again for each display and lose the
    detail Automatic resolution downloaded for it. EUMETSAT stays within its WMS
    limit. Without Windows wallpapers the shared output stays.
    """
    if os.name != "nt" or not SET_WINDOWS_WALLPAPER:
        return fallback
    width, height = copernicus_auto_size(fallback, monitor_outputs, paused=paused)
    if provider == "eumetsat" and max(width, height) > MAX_WMS_DIMENSION:
        factor = MAX_WMS_DIMENSION / max(width, height)
        width, height = (max(fallback[0], math.floor(width * factor)),
                         max(fallback[1], math.floor(height * factor)))
    return width, height


MONITOR_OUTPUT_FIELDS = (
    "width", "height", "aspect_ratio", "render_scale", "background_color",
)


def normalize_monitor_output_settings(value, defaults):
    """Validate saved per-display overrides without requiring attached displays."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("windows.monitor_output_settings is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("windows.monitor_output_settings must be a monitor map.")
    normalized = {}
    for monitor_id, fields in value.items():
        if not isinstance(monitor_id, str) or not monitor_id or not isinstance(fields, dict):
            raise ValueError("Monitor output settings need a device ID and field map.")
        if set(fields) - set(MONITOR_OUTPUT_FIELDS):
            raise ValueError("Monitor output settings contain an unknown field.")
        settings = dict(defaults)
        settings.update(fields)
        try:
            width = int(settings["width"])
            height = int(settings["height"])
            ratio = normalize_aspect_ratio_text(settings["aspect_ratio"])
            scale = parse_render_scale_setting(settings["render_scale"])
            color = normalize_background_color(settings["background_color"])
            actual_height = height or round(width / parse_aspect_ratio(ratio))
        except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            raise ValueError(f"Invalid output settings for monitor {monitor_id}.") from exc
        if (width <= 0 or height < 0 or actual_height <= 0
                or max(width, actual_height) > 32768
                or width * actual_height > 33_554_432
                or (height and abs(width / height - parse_aspect_ratio(ratio))
                    / parse_aspect_ratio(ratio) > 0.005)):
            raise ValueError(f"Invalid output dimensions for monitor {monitor_id}.")
        typed = {"width": width, "height": height, "aspect_ratio": ratio,
                 "render_scale": scale, "background_color": color}
        normalized[monitor_id] = {key: typed[key] for key in fields}
    return normalized


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
    """Return the image's serializable render-quality setting ("default", "auto" or a factor)."""
    if RENDER_SCALE_INHERITED:
        return "default"
    return "auto" if RENDER_SCALE_AUTOMATIC else RENDER_SCALE


def effective_render_scale_setting(configuration=None):
    """EUMETSAT's render quality: its own, or General's while it inherits ("auto" or a factor)."""
    # A configuration captured before the inherited value existed has its own value.
    value = configuration.get if configuration is not None else globals().get
    if value("RENDER_SCALE_INHERITED", False):
        return value("DISPLAY_RENDER_SCALE", "auto")
    return "auto" if value("RENDER_SCALE_AUTOMATIC", True) else value("RENDER_SCALE", 1.0)


def get_requested_render_scale(output_width, output_height):
    """Return the requested factor for the current output dimensions."""
    scale = effective_render_scale_setting()
    if scale == "auto":
        return min(
            MAX_WMS_DIMENSION / output_width,
            MAX_WMS_DIMENSION / output_height,
        )
    return float(scale)


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


def normalize_source_configuration(provider, profiles):
    """Validate saved source selections without requiring a network connection."""
    if provider not in SOURCE_LABELS:
        raise ValueError(f"Unknown image source: {provider}")
    if not isinstance(profiles, dict):
        raise ValueError("[sources] must contain source selection tables.")
    normalized = {}
    for key, defaults in DEFAULT_SOURCE_PROFILES.items():
        profile = profiles.get(key, {})
        if not isinstance(profile, dict):
            raise ValueError(f"[sources.{key}] must be a table.")
        if key == "eumetsat":
            normalized[key] = normalize_eumetsat_profile(profile)
            continue
        if key == "copernicus":
            normalized[key] = normalize_copernicus_profile(profile)
            continue
        if key == "himawari":
            normalized[key] = normalize_himawari_profile(profile)
            continue
        normalized[key] = dict(defaults)
        for field in defaults:
            value = profile.get(field, defaults[field])
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                raise ValueError(f"Invalid {field} for {SOURCE_LABELS[key]}.")
            normalized[key][field] = value.strip()
        if normalized[key]["resolution"] not in {"auto", "largest"} and not re.fullmatch(
            r"[1-9]\d{1,4}x[1-9]\d{1,4}", normalized[key]["resolution"]
        ):
            raise ValueError(f"Invalid source resolution for {SOURCE_LABELS[key]}.")
    return provider, normalized


def has_visible_layer(layers):
    """Whether a layer stack has an enabled layer with opacity above zero."""
    return any(layer["enabled"] and layer["opacity"] > 0
               for layer in (normalize_layer_config(entry, index)
                             for index, entry in enumerate(layers, start=1)))


def validate_wms_configuration():
    """Validate only the settings used by the EUMETSAT provider."""
    configured_layers = get_configured_layers()
    if not has_visible_layer(LAYER_CONFIG):
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
        if PROJECTION == "Geographic" and not geographic_extent_within_world((xmin, ymin, xmax, ymax)):
            raise ValueError(
                "A Geographic custom bbox must stay within longitude -180 to 180 "
                "and latitude -90 to 90."
            )


def validate_configuration():
    normalize_source_configuration(IMAGE_SOURCE, SOURCE_PROFILES)
    normalize_time_zone(DISPLAY_TIME_ZONE)
    normalize_appearance(APPEARANCE)
    if IMAGE_SOURCE == "eumetsat":
        validate_wms_configuration()
    elif IMAGE_SOURCE == "copernicus" and not (
        COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET
    ):
        raise ValueError(
            "Copernicus requires a Sentinel Hub OAuth Client ID and Client secret in Settings > Image."
        )

    if VIEW_MODE not in {"fit", "crop"}:
        raise ValueError("VIEW_MODE must be 'fit' or 'crop'.")

    if not math.isfinite(ZOOM) or ZOOM <= 0:
        raise ValueError("ZOOM must be greater than zero.")

    if (
        not RENDER_SCALE_AUTOMATIC
        and (not math.isfinite(RENDER_SCALE) or RENDER_SCALE < 1.0)
    ):
        raise ValueError("RENDER_SCALE must be a finite value of at least 1.0.")

    if not math.isfinite(UPDATE_INTERVAL_MINUTES) or UPDATE_INTERVAL_MINUTES <= 0:
        raise ValueError("UPDATE_INTERVAL_MINUTES must be greater than zero.")

    if not math.isfinite(NETWORK_TIMEOUT_SECONDS) or NETWORK_TIMEOUT_SECONDS <= 0:
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

    if WINDOWS_WALLPAPER_POSITION.lower() not in WALLPAPER_POSITION_CHOICES:
        raise ValueError(
            f"Unsupported WINDOWS_WALLPAPER_POSITION: {WINDOWS_WALLPAPER_POSITION}"
        )
    if any(value not in WALLPAPER_POSITION_CHOICES
           for value in WINDOWS_WALLPAPER_MONITOR_POSITIONS.values()):
        raise ValueError("Unsupported monitor wallpaper position.")

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


def geographic_extent_within_world(extent):
    west, south, east, north = extent
    return -180.0 <= west and east <= 180.0 and -90.0 <= south and north <= 90.0


def calculate_bbox(projection, target_ratio, base_extent):
    extent = fitted_view_extent(base_extent, target_ratio, VIEW_MODE, ZOOM)
    if projection["crs"] == "EPSG:4326" and not geographic_extent_within_world(extent):
        raise ValueError(
            "The calculated geographic bbox exceeds valid longitude/latitude "
            "bounds. Use fit_mode='crop', a larger zoom, or a regional preset."
        )
    return serialize_bbox(projection, extent), extent


def fitted_view_extent(base_extent, target_ratio, fit_mode, zoom):
    """Return the map extent requested for an output ratio, fit mode and zoom."""
    xmin, ymin, xmax, ymax = base_extent

    full_width = xmax - xmin
    full_height = ymax - ymin
    base_ratio = full_width / full_height

    center_x = (xmin + xmax) / 2.0
    center_y = (ymin + ymax) / 2.0

    if fit_mode == "fit":
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

    new_width /= zoom
    new_height /= zoom

    return (
        center_x - new_width / 2.0,
        center_y - new_height / 2.0,
        center_x + new_width / 2.0,
        center_y + new_height / 2.0,
    )


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
    with open_response(urlopen, make_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
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


_WMS_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_wms_duration(value):
    """Parse the fixed ISO-8601 durations used by EUMETSAT time dimensions."""
    match = _WMS_DURATION_PATTERN.fullmatch(str(value).strip().upper())
    if not match:
        return None
    parts = match.groupdict(default="0")
    duration = dt.timedelta(
        days=int(parts["days"]),
        hours=int(parts["hours"]),
        minutes=int(parts["minutes"]),
        seconds=float(parts["seconds"]),
    )
    return duration if duration.total_seconds() > 0 else None


def parse_wms_datetime(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def format_wms_datetime(value):
    value = value.astimezone(dt.timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def get_gap_fill_layer_times(metadata, current_time, lookback_hours, maximum=32):
    """Return older WMS times in chronological order for an LEO mosaic."""
    current = parse_wms_datetime(current_time)
    if current is None:
        return []
    cutoff = current - dt.timedelta(hours=int(lookback_hours))
    values = str(
        metadata.get("dimensions", {}).get("time", {}).get("values", "")
    ).strip()
    explicit = []
    interval_steps = []
    for entry in (values.split(",") if values else ()):
        parts = [part.strip() for part in entry.split("/")]
        if len(parts) == 1:
            parsed = parse_wms_datetime(parts[0])
            if parsed is not None:
                explicit.append(parsed)
        elif len(parts) >= 3:
            step = parse_wms_duration(parts[2])
            if step is not None:
                interval_steps.append(step)
    if explicit:
        selected = sorted({value for value in explicit if cutoff <= value < current})
        return [format_wms_datetime(value) for value in selected[-maximum:]]
    if not interval_steps:
        return []
    step = min(interval_steps)
    selected = []
    value = current - step
    while value >= cutoff and len(selected) < maximum:
        selected.append(value)
        value -= step
    return [format_wms_datetime(value) for value in reversed(selected)]


def eumetsat_gap_fill_profile():
    profile = SOURCE_PROFILES.get("eumetsat", {})
    if not profile.get("fill_gaps"):
        return None
    if not eumetsat_supports_gap_fill(
        profile.get("layer", ""), profile.get("orbit_type", "")
    ):
        return None
    return profile


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
    if eumetsat_gap_fill_profile() is not None:
        return "local"
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
    gap_fill = eumetsat_gap_fill_profile()
    gap_fill_applied = False
    for layer in resolved_layers:
        image_times = [layer["time"]]
        is_gap_fill_target = (
            gap_fill is not None
            and layer["kind"] == "wms"
            and layer["name"] == gap_fill["layer"]
        )
        if is_gap_fill_target:
            gap_fill_applied = True
            if not layer["time"] or "time" not in layer["metadata"]["dimensions"]:
                raise RuntimeError(
                    "The selected EUMETSAT layer does not advertise archive times "
                    "required for gap filling."
                )
            image_times = [
                *get_gap_fill_layer_times(
                    layer["metadata"], layer["time"],
                    gap_fill["gap_fill_lookback_hours"],
                ),
                layer["time"],
            ]
        for image_time in image_times:
            label = layer["name"]
            if is_gap_fill_target:
                label += f" @ {image_time}"
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
                        image_time=image_time,
                    ),
                    "label": label,
                    "opacity": layer["opacity"],
                    "role": "gap_fill" if is_gap_fill_target else "content",
                }
            )
    if gap_fill is not None and not gap_fill_applied:
        raise RuntimeError(
            "EUMETSAT gap filling is enabled, but the selected single-overpass "
            "layer is not an enabled WMS layer."
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

    DOWNLOAD_PROGRESS.raise_if_cancelled()
    try:
        with open_response(urlopen, make_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            data = read_response(response, track=True)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if not content_type.startswith("image/"):
        error_text = data.decode("utf-8", errors="replace")
        raise RuntimeError(
            "The EUMETSAT service returned a non-image response:\n" + error_text[:4000]
        )

    if not data:
        raise RuntimeError("The EUMETSAT service returned an empty image response.")

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

    index = 0
    while index < len(requests):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        request_spec = requests[index]
        if request_spec.get("role") == "gap_fill":
            end = index
            while end < len(requests) and requests[end].get("role") == "gap_fill":
                end += 1
            group = requests[index:end]
            # The plan is chronological for deterministic URLs and cache keys,
            # but fetch the newest mandatory image first. Older images are only
            # optional material for transparent No Data pixels.
            layer_image, downloaded_size = download_rendered_layer(
                group[-1], output_width, output_height
            )
            total_downloaded += downloaded_size
            for older_request in reversed(group[:-1]):
                DOWNLOAD_PROGRESS.raise_if_cancelled()
                if layer_image.getchannel("A").getextrema() == (255, 255):
                    break
                try:
                    older_image, downloaded_size = download_rendered_layer(
                        older_request, output_width, output_height
                    )
                except DownloadCancelledError:
                    raise
                except Exception as exc:
                    log(
                        "Skipping optional EUMETSAT gap-fill pass "
                        f"{older_request['label']}: {exc}"
                    )
                    continue
                total_downloaded += downloaded_size
                layer_image = Image.alpha_composite(older_image, layer_image)
            canvas = Image.alpha_composite(canvas, layer_image)
            index = end
            continue
        layer_image, downloaded_size = download_rendered_layer(
            request_spec,
            output_width,
            output_height,
        )
        total_downloaded += downloaded_size
        canvas = Image.alpha_composite(canvas, layer_image)
        index += 1

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
            mode = "RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB"
            resized = source.convert(mode).resize(
                (output_width, output_height),
                Image.Resampling.LANCZOS,
            )
    except Exception as exc:
        raise RuntimeError("Could not resize the rendered WMS image.") from exc

    output = io.BytesIO()
    resized.save(output, format="PNG", optimize=False)
    return output.getvalue()


def generate_history_path(source_path=None, profile_id=None, profile_name=None):
    folder = profile_history_directory(profile_id, profile_name, create=True)
    if source_path is not None:
        source_path = Path(source_path)
        # Preserve published names, including old timestamp-only images. Cached
        # PNGs keep their hash names in cache, but receive readable archive names.
        filename = (source_path.name if source_path.name.lower().startswith("marblescape_")
                    else image_filename_from_png(source_path))
        if filename:
            return unused_image_path(folder, filename)
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    candidate = folder / f"{HISTORY_FILENAME_PREFIX}_{timestamp}.png"
    counter = 1

    while candidate.exists():
        candidate = folder / (
            f"{HISTORY_FILENAME_PREFIX}_{timestamp}_{counter}.png"
        )
        counter += 1

    return candidate


def archive_history_copy(source_path, profile_id=None, profile_name=None):
    """Copy an image into its History folder; None when that folder already holds it.

    Cached images return byte for byte, so leaving a profile again must not
    archive the same image a second time. Call with HISTORY_LOCK held.
    """
    source_path = Path(source_path)
    folder = profile_history_directory(profile_id, profile_name, create=True)
    size = source_path.stat().st_size
    source_hash = None
    for existing in folder.glob("*.png"):
        try:
            if not existing.is_file() or existing.stat().st_size != size:
                continue
            source_hash = source_hash or calculate_file_sha256(source_path)
            if calculate_file_sha256(existing) == source_hash:
                return None
        except OSError:
            continue
    archived_path = generate_history_path(source_path, profile_id, profile_name)
    shutil.copy2(source_path, archived_path)
    return archived_path


def get_latest_image_files():
    """Return image files in the latest folder, newest first."""
    files = [path for path in LATEST_DIR.glob("*.png") if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def generate_latest_path(data=None, image_hash=None):
    """Name new images from their embedded record; retain a legacy fallback."""
    filename = image_filename_from_png(data, image_hash) if data is not None else None
    if filename:
        return unused_image_path(LATEST_DIR, filename)
    timestamp = dt.datetime.now().replace(microsecond=0)

    while True:
        candidate = LATEST_DIR / (
            f"{LATEST_FILENAME_PREFIX}_{timestamp.strftime('%Y-%m-%d_%H%M%S')}.png"
        )
        if not candidate.exists():
            return candidate
        timestamp += dt.timedelta(seconds=1)


def _latest_state_path():
    return LATEST_DIR / LATEST_STATE_FILENAME


def _rotation_state_path():
    return CONTENT_DIR / ROTATION_STATE_FILENAME


def load_rotation_position():
    """Read bounded local-only rotation state; invalid state starts a fresh cycle."""
    state_path = _rotation_state_path()
    try:
        if state_path.stat().st_size > 64_000:
            return {}
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}


def save_rotation_position(payload):
    """Atomically persist or clear the local-only next rotation position."""
    state_path = _rotation_state_path()
    if not payload:
        try:
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            log(f"Unable to clear rotation position: {exc}")
        return
    temporary = state_path.with_name(state_path.name + ".tmp")
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, state_path)
    except (OSError, TypeError, ValueError) as exc:
        log(f"Unable to save rotation position: {exc}")
    finally:
        temporary.unlink(missing_ok=True)


def publish_next_rotation_profile(rotation):
    """Name the profile of the next rotation step for Now showing (None while none is due)."""
    upcoming = rotation.upcoming()
    with ROTATION_STATUS_LOCK:
        ROTATION_STATUS["next_profile_name"] = upcoming[0]["name"] if upcoming else None


def _without_period(text):
    """Drop one closing period; an ellipsis stays."""
    return text[:-1] if text.endswith(".") and not text.endswith("...") else text


def now_showing_text(shown_name, state, time_zone, now=None):
    """Profiles > Now showing, in lines that end without a period.

    The first line names the picture on screen (``shown_name``; None before
    the first one). While rotation waits, the next line names the next profile
    with its time and, after a pipe, the preload result; while a step runs,
    that step. The last line says whether rotation runs.
    """
    lines = [f"Active profile: {shown_name}" if shown_name else "No picture yet"]
    text = str(state.get("text") or "").strip()
    deadline = state.get("deadline")
    disabled = deadline is None and text == "Rotation is disabled."
    # The switch and waiting messages are replaced by the next profile; others
    # (starting, loading, a cancelled or failed step) stay.
    step = ("" if disabled or not text or text.startswith("Active profile:")
            or text == "Waiting for the next rotation interval." else _without_period(text))
    rotation = "Rotation: disabled" if disabled else "Rotation: enabled"
    if deadline is not None:
        now = time.monotonic() if now is None else now
        try:
            due = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(0, deadline - now))
            when = format_display_datetime(due, time_zone, include_seconds=True)
        except OverflowError:
            when = ""
        next_name = state.get("next_profile_name")
        upcoming = (f"Upcoming profile: {next_name} at {when}" if next_name and when
                    else f"Upcoming profile: {when}" if when else "")
        result = rotation_preload_result(state, time_zone)
        if upcoming or result:
            lines.append(" | ".join(part for part in (upcoming, result) if part))
        if step:
            # A cancelled or failed step while waiting for the next one.
            rotation += " | " + step
    elif step:
        lines.append(step)
    # Rotation always takes the third line; an empty second line keeps it there.
    if len(lines) == 1:
        lines.append("")
    lines.append(rotation)
    return "\n".join(lines)


def rotation_status_text(rotation, starting=False):
    if starting:
        return "Starting rotation..."
    return "Rotation is disabled." if rotation.deadline is None else "Waiting for the next rotation interval."


def rotation_switch_state():
    """(serial, enabled) of the last rotation switch made in the tray menu."""
    with ROTATION_STATUS_LOCK:
        return ROTATION_SWITCH["serial"], ROTATION_SWITCH["enabled"]


def save_rotation_progress(rotation, shown_profile_id=None, now=None):
    """Persist the next rotation position with the time of the last profile switch.

    ``shown_profile_id`` names a rotation profile shown at runtime only; a
    manually applied image is already in the configuration file. Without Keep
    last rotation position the state is cleared instead.
    """
    state = rotation.position_state()
    if state:
        now = now or dt.datetime.now(dt.timezone.utc)
        state["last_switch_utc"] = now.isoformat().replace("+00:00", "Z")
        if shown_profile_id:
            state["shown_profile_id"] = shown_profile_id
    save_rotation_position(state)


def resume_rotation_profile(state):
    """Apply the rotation profile that was shown before a restart; return it or None."""
    identifier = state.get("shown_profile_id") if isinstance(state, dict) else None
    profile = next((item for item in IMAGE_PROFILE_LIBRARY.get("items", [])
                    if identifier and item.get("id") == identifier), None)
    if profile is None:
        return None
    try:
        apply_image_settings(profile["settings"])
    except Exception as exc:
        log(f"Unable to show rotation profile {profile['name']} again: {exc}")
        return None
    log(f"Continuing rotation with profile {profile['name']} until its interval ends.")
    return profile


def seconds_since_rotation_switch(state, now=None):
    """Return the seconds since the saved last profile switch, or None."""
    text = state.get("last_switch_utc") if isinstance(state, dict) else None
    if not isinstance(text, str):
        return None
    try:
        switched = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if switched.tzinfo is None:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - switched).total_seconds()


def _write_latest_state(path, configuration_signature, output_size,
                        source_signature=None, source_time=None):
    if configuration_signature is None:
        return
    width, height = map(int, output_size)
    payload = {
        "version": 2,
        "file": path.name,
        "configuration_hash": signature_digest(configuration_signature),
        "source_hash": (
            signature_digest(source_signature)
            if source_signature is not None else None
        ),
        "image_hash": calculate_file_sha256(path),
        "width": width,
        "height": height,
        "source_time": source_time,
    }
    state_path = _latest_state_path()
    temporary = state_path.with_name(state_path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
    finally:
        temporary.unlink(missing_ok=True)


def reusable_latest_image(configuration_signature, output_size,
                          source_signature=None, source_time=None,
                          allow_legacy_source_time=False):
    """Return a verified normal image matching its settings and source frame."""
    state_path = _latest_state_path()
    try:
        if state_path.stat().st_size > 64_000:
            return None
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if payload.get("version") not in (1, 2):
            return None
        if payload.get("configuration_hash") != signature_digest(configuration_signature):
            return None
        if source_signature is not None:
            source_matches = (
                payload.get("version") == 2
                and payload.get("source_hash") == signature_digest(source_signature)
            )
            legacy_time_matches = (
                allow_legacy_source_time and payload.get("version") == 1
                and source_time is not None
                and payload.get("source_time") == source_time
            )
            if not source_matches and not legacy_time_matches:
                return None
        width, height = map(int, output_size)
        if (payload.get("width"), payload.get("height")) != (width, height):
            return None
        filename = payload.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            return None
        path = (LATEST_DIR / filename).resolve()
        if path.parent != LATEST_DIR.resolve() or not path.is_file():
            return None
        if calculate_file_sha256(path) != payload.get("image_hash"):
            return None
        from PIL import Image
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != (width, height):
                return None
            image.verify()
        from marblescape_image_naming import has_no_image_data
        if has_no_image_data(path):
            return None  # Never reused: it would be a black or blank wallpaper again.
        return path
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def unmodified_applied_profile_id(configuration=None):
    """The manually applied profile, while the loaded (or given) settings still equal it."""
    values = globals() if configuration is None else configuration
    applied = values.get("APPLIED_PROFILE_ID", APPLIED_PROFILE_ID)
    if not applied:
        return None
    for item in values.get("IMAGE_PROFILE_LIBRARY", IMAGE_PROFILE_LIBRARY).get("items", ()):
        if item["id"] == applied:
            saved = portable_settings(item["settings"], normalize_image_settings_snapshot)
            loaded = portable_settings(image_settings_snapshot(configuration), normalize_image_settings_snapshot)
            return item["id"] if saved == loaded else None
    return None


def show_cached_profile_now(identifier, settings):
    """Show the cached picture of a profile applied while the image worker downloads.

    Runs in its own thread, so the switch does not wait for the running
    download. Only a picture made with exactly these image settings at the
    expected size is used; ``identifier`` is a saved profile's ID or the Latest
    snapshot row. Returns the published path, or None to leave the switch to the
    worker. Afterwards the worker checks the provider as usual.
    """
    cache_id = latest_snapshot.CACHE_ID if identifier == latest_snapshot.SYSTEM_ID else identifier
    try:
        cached = get_profile_cache().current(cache_id)
        if cached is None:
            return None
        from PIL import Image
        with Image.open(cached) as image:
            size = image.size
            record = json.loads(image.text.get("MarbleScape", "{}"))
        wanted = portable_settings(settings, normalize_image_settings_snapshot)
        made_with = portable_settings(record.get("profile_settings") or {}, normalize_image_settings_snapshot)
        expected = image_dimensions_for(settings.get("source", {}).get("provider"),
                                        settings.get("sources", {}).get("copernicus", {}))
        if made_with != wanted or tuple(size) != tuple(expected):
            return None
        published = publish_cached_profile_image(cached, None, None)
        if published is None:
            return None
        set_current_image_path(published)
        if os.name == "nt" and SET_WINDOWS_WALLPAPER:
            set_windows_wallpaper(published)
        log(f"Showing the cached picture of the applied profile at once: {cached}")
        return published
    except Exception as exc:
        log(f"Unable to show the cached picture at once; it follows the running download: {exc}")
        return None


def relabel_picture(data, profile_id):
    """``data`` with the provenance of ``profile_id``, or of the Latest snapshot for None.

    Returns (PNG bytes, record), or None when the picture has no MarbleScape
    record or the profile is gone. Pixels and render settings stay; only the
    profile identity changes, so a picture shared by equal settings belongs to
    the selection that is applied.
    """
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as picture:
            record = json.loads(picture.text.get("MarbleScape", "") or "null")
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    if profile_id:
        name = image_profile_name(profile_id)
        if not name:
            return None
        record.pop("profile_kind", None)
        record.pop("snapshot_id", None)
        record.update(profile_id=profile_id, profile_name=name)
    else:
        try:
            snapshot = read_latest_snapshot() or {}
        except (OSError, ValueError):
            snapshot = {}
        record.update(
            profile_kind=latest_snapshot.SYSTEM_KIND, profile_name=latest_snapshot.SYSTEM_NAME,
            profile_id=snapshot.get("profile_id") or uuid.uuid4().hex,
            snapshot_id=snapshot.get("snapshot_id") or uuid.uuid4().hex,
        )
    return embed_png_metadata(data, record), record


def shown_identity_differs(path, profile_id):
    """True when the picture at ``path`` names another owner than ``profile_id`` (None: the Latest snapshot)."""
    return _history_profile_identity(path)[0] != (profile_id or None)


def publish_with_identity(path, profile_id, configuration_signature, output_size,
                          source_signature=None, source_time=None, archive_previous=True):
    """Publish the reused picture at ``path`` as the applied selection's own.

    The copy carries ``profile_id``'s identity (None: the Latest snapshot) and,
    with a known source signature, also replaces that slot's cache entry.
    Without a record it is published unchanged.
    """
    data = Path(path).read_bytes()
    relabeled = relabel_picture(data, profile_id)
    if relabeled is None:
        return publish_cached_profile_image(path, configuration_signature, output_size,
                                            source_signature, source_time)
    data, record = relabeled
    if source_signature is not None:
        try:
            cache = get_profile_cache()
            cache.install(profile_id or latest_snapshot.CACHE_ID, configuration_signature,
                          source_signature, data, output_size, source_time=source_time)
            cache.prune()
        except Exception as exc:
            log(f"Profile cache store warning: {exc}")
    installed = save_latest_image(data, configuration_signature=configuration_signature,
                                  output_size=output_size, source_signature=source_signature,
                                  source_time=source_time, archive_previous=archive_previous)
    cleanup_history()
    if profile_id is None:
        try:
            save_latest_snapshot(record)
        except (OSError, ValueError) as exc:
            log(f"Latest snapshot could not be saved: {exc}")
    log(f"Reused picture published as {cached_image_owner(profile_id)}'s own.")
    if installed is not None:
        return installed
    latest_files = get_latest_image_files()
    return latest_files[0] if latest_files else None


def cached_image_owner(applied_profile_id):
    """Log wording for the profile-cache slot reused while no rotation profile is shown."""
    return "the applied profile" if applied_profile_id else "the latest snapshot"


def publish_cached_profile_image(cached_path, configuration_signature, output_size,
                                 source_signature=None, source_time=None):
    """Install a cached profile PNG as the Latest image instead of downloading it again.

    Manually applied and shown rotation profiles publish through Latest; the replaced Latest image
    is archived exactly as after a download.
    """
    installed = save_latest_image(
        Path(cached_path).read_bytes(),
        configuration_signature=configuration_signature,
        output_size=output_size,
        source_signature=source_signature,
        source_time=source_time,
    )
    cleanup_history()
    if installed is not None:
        return installed
    latest_files = get_latest_image_files()
    return latest_files[0] if latest_files else None


def save_latest_image(data, configuration_signature=None, output_size=None,
                      source_signature=None, source_time=None, archive_previous=True):
    with LATEST_IMAGE_LOCK:
        return _save_latest_image_unlocked(data, configuration_signature, output_size,
                                           source_signature, source_time, archive_previous)


def _save_latest_image_unlocked(data, configuration_signature=None, output_size=None,
                                source_signature=None, source_time=None, archive_previous=True):
    new_hash = calculate_sha256(data)
    existing_files = get_latest_image_files()
    current_path = existing_files[0] if existing_files else None

    if current_path is not None:
        old_hash = calculate_file_sha256(current_path)
        if new_hash == old_hash:
            if output_size is not None:
                _write_latest_state(
                    current_path, configuration_signature, output_size,
                    source_signature, source_time
                )
            log("No image change detected. Latest file remains unchanged.")
            return None

    latest_path = generate_latest_path(data, new_hash)
    temp_path = latest_path.with_suffix(latest_path.suffix + ".tmp")
    temp_path.write_bytes(data)

    archived_path = None
    installed = False

    try:
        # A manually applied profile publishes through Latest; its image
        # belongs in that profile's History folder under that profile's policy.
        owner_id = _history_profile_identity(current_path)[0] if current_path is not None else None
        owner_name = image_profile_name(owner_id)
        if owner_name is None:
            owner_id = None
        history_enabled = profile_history_policy(owner_id)["enabled"] if owner_id else ENABLE_HISTORY
        if current_path is not None and history_enabled and archive_previous:
            with HISTORY_LOCK:
                archived_path = archive_history_copy(current_path, owner_id, owner_name)

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
    if output_size is not None:
        _write_latest_state(
            latest_path, configuration_signature, output_size,
            source_signature, source_time
        )
    return latest_path


def save_profile_image(profile_id, configuration_signature, source_signature,
                       data, output_size, source_time=None):
    """Install an immutable cached profile image and archive its predecessor."""
    cache = get_profile_cache()
    current_before = cache.current(profile_id)
    current_path, previous_path = cache.install(
        profile_id, configuration_signature, source_signature, data, output_size,
        source_time=source_time,
    )
    # The Latest snapshot's slot archives to "_no profile" under History (no profile).
    owner_id = None if profile_id == latest_snapshot.CACHE_ID else profile_id
    history_enabled = profile_history_policy(owner_id)["enabled"] if owner_id else ENABLE_HISTORY
    if previous_path is not None and history_enabled:
        with HISTORY_LOCK:
            archived_path = archive_history_copy(previous_path, owner_id, image_profile_name(owner_id))
        if archived_path is not None:
            log(f"Archived previous profile image: {archived_path}")
    cache.prune()
    if current_before == current_path:
        log("No profile image change detected. Cached file remains unchanged.")
        return None, current_path
    log(f"Installed profile image in cache: {current_path}")
    log(f"Final image size: {format_bytes(len(data))}")
    return current_path, current_path


# =============================================================================
# HISTORY RETENTION
# =============================================================================


def get_history_files(profile_id="all"):
    # Both the old lowercase prefix and new human-readable names are managed,
    # including on case-sensitive filesystems. Unrelated PNGs remain untouched.
    roots = ([profile_history_directory(None)] if profile_id is None else
             [profile_history_directory(profile_id)] if profile_id not in {"all", "profiles"} else
             [HISTORY_DIR])
    files = sorted({path for root in roots if root.is_dir() for path in root.rglob("*.png")
                    if path.is_file() and path.name.casefold().startswith(HISTORY_FILENAME_PREFIX.casefold() + "_")})
    if profile_id == "profiles":
        files = [path for path in files if re.search(r"_[0-9a-f]{32}$", path.parent.name)]
    return files


def clear_history_images(profile_id="all"):
    """Remove only MarbleScape-managed history PNGs and report reclaimed space."""
    with HISTORY_LOCK:
        files = get_history_files(profile_id)
        total_bytes = 0
        removed = 0
        for path in files:
            try:
                total_bytes += path.stat().st_size
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        return {"files": removed, "bytes": total_bytes}


def _profile_history_folders(profile_id):
    """Every History subfolder of one normal profile, whatever name it carries."""
    if (not isinstance(profile_id, str) or profile_id == latest_snapshot.SYSTEM_ID
            or re.fullmatch(r"[0-9a-f]{32}", profile_id) is None or not HISTORY_DIR.is_dir()):
        return []
    return sorted(folder for folder in HISTORY_DIR.glob(f"*_{profile_id}") if folder.is_dir())


def _managed_history_files(folder):
    return sorted(path for path in folder.rglob("*.png") if path.is_file()
                  and path.name.casefold().startswith(HISTORY_FILENAME_PREFIX.casefold() + "_"))


def profile_history_usage(profile_id):
    """Count one profile's History folder: managed PNGs and any other files."""
    with HISTORY_LOCK:
        usage = {"files": 0, "bytes": 0, "other_files": 0, "other_bytes": 0}
        for folder in _profile_history_folders(profile_id):
            managed = set(_managed_history_files(folder))
            for path in folder.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    size = path.stat().st_size
                except OSError:
                    continue
                prefix = "" if path in managed else "other_"
                usage[prefix + "files"] += 1
                usage[prefix + "bytes"] += size
        return usage


def _remove_read_only(function, path, error):
    # onerror (Python < 3.12) passes exc_info, onexc the exception itself.
    error = error[1] if isinstance(error, tuple) else error
    # Windows refuses to delete read-only files until the flag is cleared.
    if not isinstance(error, PermissionError) or function not in (os.unlink, os.remove, os.rmdir):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def delete_profile_history(profile_id):
    """Remove a deleted profile's whole History folder, other files included."""
    with HISTORY_LOCK:
        usage = profile_history_usage(profile_id)
        for folder in _profile_history_folders(profile_id):
            # rmtree refuses a linked folder and never descends into nested links.
            if sys.version_info >= (3, 12):
                shutil.rmtree(folder, onexc=_remove_read_only)
            else:
                shutil.rmtree(folder, onerror=_remove_read_only)
        return usage


def subtract_calendar_period(value, policy=None):
    policy = _default_history_policy() if policy is None else policy
    months_to_subtract = policy["years"] * 12 + policy["months"]
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
        days=policy["days"], hours=policy["hours"], minutes=policy["minutes"],
    )


def cleanup_history():
    with HISTORY_LOCK:
        removed = 0
        folders = {path for path in HISTORY_DIR.iterdir() if path.is_dir()} if HISTORY_DIR.is_dir() else set()
        # Legacy root files are treated as no-profile until migration can route them.
        folders.add(HISTORY_DIR)
        for folder in folders:
            match = re.search(r"_([0-9a-f]{32})$", folder.name)
            policy = (profile_history_policy(match.group(1)) if match else _default_history_policy())
            if not policy["enabled"]:
                continue
            files = [path for path in folder.glob("*.png") if path.is_file()
                     and path.name.casefold().startswith(HISTORY_FILENAME_PREFIX.casefold() + "_")]
            if policy["retention_mode"] in {"time", "both"}:
                cutoff = subtract_calendar_period(dt.datetime.now(), policy)
                for path in list(files):
                    modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
                    if modified < cutoff:
                        path.unlink(missing_ok=True)
                        removed += 1
                files = [path for path in files if path.exists()]
            if policy["retention_mode"] in {"count", "both"}:
                files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
                for path in files[policy["max_files"]:]:
                    path.unlink(missing_ok=True)
                    removed += 1

    if removed:
        log(f"History cleanup removed {removed} file(s).")

    return removed


# =============================================================================
# STORAGE ESTIMATE
# =============================================================================


def estimated_time_retention_slots(policy=None):
    now = dt.datetime.now()
    cutoff = subtract_calendar_period(now, policy)
    retention_seconds = max(0.0, (now - cutoff).total_seconds())
    interval_seconds = UPDATE_INTERVAL_MINUTES * 60.0
    return int(math.ceil(retention_seconds / interval_seconds))


def estimated_history_slots(policy=None):
    policy = _default_history_policy() if policy is None else policy
    if not policy["enabled"]:
        return 0

    if policy["retention_mode"] == "count":
        return policy["max_files"]

    time_slots = estimated_time_retention_slots(policy)

    if policy["retention_mode"] == "time":
        return time_slots

    return min(policy["max_files"], time_slots)


def estimated_total_history_slots():
    """Include the no-profile archive and each independent profile archive."""
    return estimated_history_slots() + sum(
        estimated_history_slots(profile_history_policy(item["id"]))
        for item in IMAGE_PROFILE_LIBRARY.get("items", ())
    )


def count_text(count, noun):
    """"1 image", "3 images"."""
    return f"{count} {noun}" + ("" if count == 1 else "s")


def profile_cache_slots():
    """Saved profiles, each keeping up to PROFILE_CACHE_VARIANTS cache variants."""
    return len(IMAGE_PROFILE_LIBRARY.get("items", ()))


def estimate_profile_cache(image_size, slots, variants, limit_bytes, snapshot_limit_bytes):
    """Return (images, bytes, limited) the profile cache may hold at most.

    The ``slots`` saved profiles keep up to ``variants`` each within
    ``limit_bytes``; the Latest snapshot keeps any number of variants, so it
    can fill its own ``snapshot_limit_bytes``. Every current picture stays even
    beyond a limit. Without a known image size the bytes are None and the
    images count one round of ``variants`` per profile and the Latest snapshot.
    """
    if image_size is None or image_size <= 0:
        return (slots + 1) * variants, None, False
    profiles = min(slots * variants, max(limit_bytes // image_size, slots))
    snapshot = max(snapshot_limit_bytes // image_size, 1)
    kept = profiles + snapshot
    return kept, kept * image_size, True


def print_storage_estimate(image_size):
    history_slots = estimated_total_history_slots()
    cache_slots = profile_cache_slots()
    cache_images, cache_bytes, limited = estimate_profile_cache(
        image_size, cache_slots, PROFILE_CACHE_VARIANTS, profile_cache_max_bytes(),
        profile_cache_snapshot_bytes(),
    )
    expected_bytes = image_size * (history_slots + 1) + (cache_bytes or 0)

    log()
    log("Storage estimate based on the first downloaded image:")
    log(f"  Current image size: {format_bytes(image_size)}")
    log(f"  Estimated retained history images: {history_slots}")
    log("  Normal latest images: 1")
    log(f"  Cached profile images: up to {cache_images} ({cache_slots} profile(s) and the Latest snapshot, "
        + (f"size limits {format_disk_usage(profile_cache_max_bytes())} and "
           f"{format_disk_usage(profile_cache_snapshot_bytes())}" if limited else "no image size yet")
        + ")")
    log(f"  Estimated maximum total: {format_bytes(expected_bytes)}")

    if HISTORY_RETENTION_MODE in {"time", "both"}:
        log(
            "  Time-based estimate assumes every polling cycle produces a new "
            "image and is therefore a conservative upper-bound estimate."
        )

    log()


def get_storage_status(max_size_gb=None, variants=None, snapshot_size_gb=None):
    """Storage counts and estimates; the cache limits default to the saved ones.

    Settings passes its unsaved slider values, so the estimate follows them live.
    """
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
    current_path = get_current_image_path()
    if current_path is not None:
        try:
            current_image_size = current_path.stat().st_size
        except (FileNotFoundError, OSError):
            pass
    elif latest_files:
        try:
            current_image_size = latest_files[0].stat().st_size
        except (FileNotFoundError, OSError):
            pass

    estimated_history_images = estimated_total_history_slots()
    cache_slots = profile_cache_slots()
    cache_variants = PROFILE_CACHE_VARIANTS if variants is None else variants
    cache_limit_bytes = profile_cache_max_bytes(max_size_gb)
    cache_snapshot_limit_bytes = profile_cache_snapshot_bytes(snapshot_size_gb)
    cache_images, cache_bytes, cache_limited = estimate_profile_cache(
        current_image_size, cache_slots, cache_variants, cache_limit_bytes,
        cache_snapshot_limit_bytes,
    )
    estimated_maximum_images = estimated_history_images + 1 + cache_images
    estimated_total_bytes = (
        current_image_size * (estimated_history_images + 1) + cache_bytes
        if current_image_size is not None
        else None
    )
    try:
        cache_status = get_profile_cache().status()
    except Exception:
        cache_status = {"profiles": 0, "variants": 0, "files": 0, "bytes": 0}
    used_bytes = total_file_size((*latest_files, *history_files)) + cache_status["bytes"]
    empty = {"variants": 0, "files": 0, "bytes": 0}
    cache_main = cache_status.get("main", empty)
    cache_snapshot = cache_status.get("separate", {}).get(latest_snapshot.CACHE_ID, empty)

    return {
        "latest_images": len(latest_files),
        "current_image_size": current_image_size,
        "estimated_history_images": estimated_history_images,
        "estimated_maximum_images": estimated_maximum_images,
        "estimated_total_bytes": estimated_total_bytes,
        "used_bytes": used_bytes,
        "cache_profiles": cache_status["profiles"],
        "cache_variant_count": cache_status["variants"],
        "cache_files": cache_status["files"],
        "cache_bytes": cache_status["bytes"],
        "cache_profile_files": cache_main["files"],
        "cache_profile_bytes": cache_main["bytes"],
        "cache_snapshot_variants": cache_snapshot["variants"],
        "cache_snapshot_files": cache_snapshot["files"],
        "cache_snapshot_bytes": cache_snapshot["bytes"],
        "cache_slots": cache_slots,
        "cache_variants": cache_variants,
        "cache_limit_bytes": cache_limit_bytes,
        "cache_snapshot_limit_bytes": cache_snapshot_limit_bytes,
        "cache_limited": cache_limited,
        "estimated_cache_images": cache_images,
        "estimated_cache_bytes": cache_bytes,
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


class WindowsRect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


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


def windows_display_layout():
    """The attached displays as user32 sees them: sorted (device path, rectangle) pairs.

    Cheap and without COM. It names a display the wallpaper API lists without its
    device path, and shows when displays are added, woken, rotated or resized. A
    path is empty when it is unknown or the display is mirrored.
    """
    if os.name != "nt":
        return []
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    class DisplayDevice(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, enum_proc, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.POINTER(DisplayDevice), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    handles = []
    callback = enum_proc(lambda handle, _dc, _rect, _data: handles.append(handle) or True)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    layout = []
    for handle in handles:
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            continue
        rect = (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom)
        paths = []
        for index in range(16):
            device = DisplayDevice()
            device.cb = ctypes.sizeof(device)
            # 1 = EDD_GET_DEVICE_INTERFACE_NAME: the path IDesktopWallpaper uses.
            if not user32.EnumDisplayDevicesW(info.szDevice, index, ctypes.byref(device), 1):
                break
            if device.StateFlags & 1 and device.DeviceID:  # DISPLAY_DEVICE_ACTIVE
                paths.append(device.DeviceID)
        layout.append((paths[0] if len(paths) == 1 else "", rect))
    return sorted(layout)


def resolve_wallpaper_monitors(entries, layout):
    """Give every wallpaper slot its display's device path.

    ``entries`` are the attached (slot, rectangle) pairs IDesktopWallpaper lists.
    After a start or wake-up Windows can list a display with an empty slot while
    its real path shows as detached. That slot gets the path of the display at
    the same rectangle, so its own settings apply; it keeps ``slot`` for the
    wallpaper call. An empty slot without a match, or one whose display is also
    listed by its path, is left out: an empty ID can set every display.
    """
    listed = {slot.casefold() for slot, _rect in entries if slot}
    monitors = []
    for slot, rect in entries:
        if slot:
            monitors.append({"id": slot, "rect": rect})
            continue
        path = next((path for path, place in layout if path and tuple(place) == tuple(rect)), "")
        if not path or path.casefold() in listed:
            log(f"Skipping a display Windows lists without a device path at {rect}.")
            continue
        listed.add(path.casefold())
        monitors.append({"id": path, "slot": "", "rect": rect})
    return monitors


def list_windows_wallpaper_monitors():
    if os.name != "nt":
        return []

    def read_monitors():
        desktop = create_desktop_wallpaper_interface()
        try:
            get_count = get_com_method(desktop, 6, ctypes.c_long,
                                       ctypes.POINTER(ctypes.c_uint))
            get_id = get_com_method(desktop, 5, ctypes.c_long, ctypes.c_uint,
                                    ctypes.POINTER(ctypes.c_void_p))
            get_rect = get_com_method(desktop, 7, ctypes.c_long, ctypes.c_wchar_p,
                                      ctypes.POINTER(WindowsRect))
            free_memory = ctypes.windll.ole32.CoTaskMemFree
            free_memory.argtypes = [ctypes.c_void_p]
            free_memory.restype = None
            count = ctypes.c_uint()
            check_hresult(get_count(desktop, ctypes.byref(count)),
                          "IDesktopWallpaper.GetMonitorDevicePathCount")
            entries = []
            for index in range(count.value):
                raw_id = ctypes.c_void_p()
                check_hresult(get_id(desktop, index, ctypes.byref(raw_id)),
                              "IDesktopWallpaper.GetMonitorDevicePathAt")
                try:
                    monitor_id = ctypes.wstring_at(raw_id.value)
                finally:
                    free_memory(raw_id)
                rectangle = WindowsRect()
                result = get_rect(desktop, monitor_id, ctypes.byref(rectangle))
                # S_FALSE, E_FAIL or E_INVALIDARG: a detached display (Windows
                # keeps a display's old entry while it is listed again).
                if result in (1, ctypes.c_long(0x80004005).value, ctypes.c_long(0x80070057).value):
                    continue
                check_hresult(result, "IDesktopWallpaper.GetMonitorRECT")
                if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
                    continue
                entries.append((monitor_id, (rectangle.left, rectangle.top,
                                             rectangle.right, rectangle.bottom)))
            if all(slot for slot, _rect in entries):
                return [{"id": slot, "rect": rect} for slot, rect in entries]
            return resolve_wallpaper_monitors(entries, windows_display_layout())
        finally:
            release_com_pointer(desktop)

    return with_windows_com(read_monitors)


def acquire_windows_single_instance(wait_seconds=0):
    global WINDOWS_SINGLE_INSTANCE_HANDLE
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    deadline = (None if wait_seconds is None else
                time.monotonic() + max(0, float(wait_seconds)))
    while True:
        handle = kernel32.CreateMutexW(
            None, False, "Global\\Gittegatt.MarbleScape.7A87D1D4-80E9-4F7E-91EC-DF10A4969899"
        )
        error = ctypes.get_last_error()
        if handle and error != 183:
            WINDOWS_SINGLE_INSTANCE_HANDLE = handle
            return True
        if handle:
            kernel32.CloseHandle(handle)
        elif error != 5:
            raise ctypes.WinError(error)
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.1 if deadline is None else
                   max(0, min(0.1, deadline - time.monotonic())))


def release_windows_single_instance():
    """Release only after the application worker has stopped writing files."""
    global WINDOWS_SINGLE_INSTANCE_HANDLE
    if os.name == "nt" and WINDOWS_SINGLE_INSTANCE_HANDLE:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        if not kernel32.CloseHandle(WINDOWS_SINGLE_INSTANCE_HANDLE):
            raise ctypes.WinError(ctypes.get_last_error())
        WINDOWS_SINGLE_INSTANCE_HANDLE = None


# Exit and Restart end a real run (the entry point arms it) after this many seconds
# even when something hangs, for example a download that does not react.
FORCED_EXIT_SECONDS = 10
FORCED_EXIT = {"armed": False}
# A restart waits this long for the previous MarbleScape, which ends itself within
# FORCED_EXIT_SECONDS; one still holding the lock after that is ended.
RESTART_WAIT_SECONDS = 15


def start_forced_exit_timer():
    if not FORCED_EXIT["armed"]:
        return

    def force_exit():
        log(f"MarbleScape did not stop within {FORCED_EXIT_SECONDS} seconds; ending it now.")
        close_log_file()
        os._exit(0)

    timer = threading.Timer(FORCED_EXIT_SECONDS, force_exit)
    timer.daemon = True
    timer.start()


def windows_process_image_name(pid):
    """The program file name of a running Windows process, or None."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    # PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def end_previous_instance(pid_text):
    """End the MarbleScape a restart replaces when it still holds the lock; True if ended.

    Only a Python or MarbleScape program is ended, never a process that reused the number.
    """
    try:
        pid = int(pid_text)
    except (TypeError, ValueError):
        return False
    if os.name != "nt" or pid <= 0 or pid == os.getpid():
        return False
    name = windows_process_image_name(pid)
    if name is None or name.lower() not in {"python.exe", "pythonw.exe", Path(sys.executable).name.lower()}:
        log(f"The previous MarbleScape (process {pid}) was not found; it is not ended.")
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        log(f"Unable to end the previous MarbleScape (process {pid}): {exc}")
        return False
    log(f"Ended the previous MarbleScape (process {pid}), which did not stop in time.")
    return True


def acquire_instance_for_start(environment=None):
    """Take the single-instance lock at startup; a restart ends a hung previous instance."""
    environment = os.environ if environment is None else environment
    if environment.get("MARBLESCAPE_RESTART_WAIT") != "1":
        return acquire_windows_single_instance(wait_seconds=3 if should_use_windows_tray() else 0)
    if acquire_windows_single_instance(wait_seconds=RESTART_WAIT_SECONDS):
        return True
    return (end_previous_instance(environment.get("MARBLESCAPE_RESTART_FROM_PID"))
            and acquire_windows_single_instance(wait_seconds=5))


def compose_monitor_wallpaper(source, mode, rect, all_rects, background_color):
    from PIL import Image

    if "A" in source.getbands():
        # Windows wallpaper is opaque; flatten only this derivative, never latest/history.
        flattened = Image.new("RGB", source.size, background_color)
        try:
            flattened.paste(source, mask=source.getchannel("A"))
            return compose_monitor_wallpaper(flattened, mode, rect, all_rects, background_color)
        finally:
            flattened.close()
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= 0 or height <= 0:
        raise ValueError("Monitor dimensions must be positive.")
    if mode == "span":
        left = min(item[0] for item in all_rects)
        top = min(item[1] for item in all_rects)
        right = max(item[2] for item in all_rects)
        bottom = max(item[3] for item in all_rects)
        span_width, span_height = right - left, bottom - top
        # Like Windows: the picture covers the whole desktop at one scale, centred,
        # and each display shows its part; it is never stretched to the desktop's
        # shape. Crop in source coordinates before resizing to avoid a full
        # virtual-desktop buffer.
        scale = max(span_width / source.width, span_height / source.height)
        offset_x = (source.width * scale - span_width) / 2
        offset_y = (source.height * scale - span_height) / 2
        source_box = ((rect[0] - left + offset_x) / scale,
                      (rect[1] - top + offset_y) / scale,
                      (rect[2] - left + offset_x) / scale,
                      (rect[3] - top + offset_y) / scale)
        return source.crop(source_box).resize((width, height), Image.Resampling.LANCZOS)
    if mode == "stretch":
        return source.resize((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), background_color)
    if mode == "tile":
        for y in range(0, height, source.height):
            for x in range(0, width, source.width):
                canvas.paste(source, (x, y))
        return canvas
    if mode == "center":
        canvas.paste(source, ((width - source.width) // 2,
                              (height - source.height) // 2))
        return canvas
    scale = (min if mode == "fit" else max)(
        width / source.width, height / source.height
    )
    rendered = source.resize((max(1, round(source.width * scale)),
                              max(1, round(source.height * scale))),
                             Image.Resampling.LANCZOS)
    canvas.paste(rendered, ((width - rendered.width) // 2,
                            (height - rendered.height) // 2))
    return canvas


def render_monitor_output_source(source, settings):
    """Size the shared image for one display using available source pixels."""
    from PIL import Image

    width = settings["width"]
    height = settings["height"] or round(width / parse_aspect_ratio(settings["aspect_ratio"]))
    target_size = (width, height)
    if source.size == target_size:
        return source
    if abs(source.width * height / (source.height * width) - 1.0) > 0.01:
        # A picture of another shape (a portrait Copernicus size, or a display
        # override of another aspect ratio) keeps its aspect ratio: it is only
        # reduced, uniformly, while it still covers the output size; fit, fill
        # or span place it on the display afterwards.
        cover = max(width / source.width, height / source.height)
        if cover >= 1.0:
            return source.copy()
        return source.resize((max(1, round(source.width * cover)), max(1, round(source.height * cover))),
                             Image.Resampling.LANCZOS)
    quality = settings["render_scale"]
    if quality == "auto":
        sample_size = source.size
    else:
        sample_size = (
            min(source.width, max(width, round(width * min(quality, source.width / width)))),
            min(source.height, max(height, round(height * min(quality, source.height / height)))),
        )
    if sample_size == source.size:
        return source.resize(target_size, Image.Resampling.LANCZOS)
    sampled = source.resize(sample_size, Image.Resampling.LANCZOS)
    try:
        return sampled.resize(target_size, Image.Resampling.LANCZOS)
    finally:
        sampled.close()


def previous_wallpaper_backup(monitor_id):
    directory = CONTENT_DIR / "previous_wallpapers"
    stem = hashlib.sha256(monitor_id.encode("utf-8")).hexdigest()
    for extension in (".png", ".jpg", ".bmp", ".tif", ".webp"):
        candidate = directory / (stem + extension)
        if candidate.is_file():
            return candidate
    return None


def capture_previous_wallpapers(monitors, image_path):
    """Keep each display's current image before MarbleScape replaces it."""
    monitors = [monitor for monitor in monitors
                if previous_wallpaper_backup(monitor["id"]) is None]
    if not monitors:
        return

    def capture():
        desktop = create_desktop_wallpaper_interface()
        try:
            get_wallpaper = get_com_method(
                desktop, 4, ctypes.c_long, ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            free_memory = ctypes.windll.ole32.CoTaskMemFree
            free_memory.argtypes = [ctypes.c_void_p]
            free_memory.restype = None
            directory = CONTENT_DIR / "previous_wallpapers"
            for monitor in monitors:
                monitor_id = monitor["id"]
                raw_path = ctypes.c_void_p()
                try:
                    result = get_wallpaper(desktop, monitor_id, ctypes.byref(raw_path))
                    check_hresult(result, "IDesktopWallpaper.GetWallpaper")
                    if result != 0:
                        continue
                    current_path = ctypes.wstring_at(raw_path.value) if raw_path.value else ""
                finally:
                    if raw_path.value:
                        free_memory(raw_path)

                extension = ".png"
                if current_path:
                    source = Path(current_path)
                    resolved = source.resolve()
                    managed_dir = (CONTENT_DIR / "device_wallpapers").resolve()
                    if (resolved == Path(image_path).resolve()
                            or resolved.is_relative_to(managed_dir)
                            or source.name.lower().startswith("marblescape_")):
                        continue
                    from PIL import Image

                    with Image.open(source) as picture:
                        extension = {
                            "PNG": ".png", "JPEG": ".jpg", "BMP": ".bmp",
                            "TIFF": ".tif", "WEBP": ".webp",
                        }.get(picture.format)
                    if extension is None:
                        raise ValueError("Unsupported previous wallpaper image format.")
                else:
                    get_color = get_com_method(
                        desktop, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_uint32),
                    )
                    color = ctypes.c_uint32()
                    check_hresult(get_color(desktop, ctypes.byref(color)),
                                  "IDesktopWallpaper.GetBackgroundColor")

                directory.mkdir(parents=True, exist_ok=True)
                stem = hashlib.sha256(monitor_id.encode("utf-8")).hexdigest()
                destination = directory / (stem + extension)
                temporary = directory / (stem + ".tmp")
                try:
                    if current_path:
                        shutil.copyfile(source, temporary)
                    else:
                        from PIL import Image

                        left, top, right, bottom = monitor["rect"]
                        value = color.value
                        background = Image.new(
                            "RGB", (right - left, bottom - top),
                            (value & 255, (value >> 8) & 255, (value >> 16) & 255),
                        )
                        try:
                            background.save(temporary, format="PNG")
                        finally:
                            background.close()
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
        finally:
            release_com_pointer(desktop)

    with_windows_com(capture)


def previous_system_background_path():
    return CONTENT_DIR / "previous_wallpapers" / "background_color.json"


def sync_system_background_color(desktop):
    """Give Windows' desktop color the background color, keeping the user's own once.

    Windows uses this one system-wide color where it leaves part of a display
    uncovered; MarbleScape's own margins are already in the images.
    """
    get_color = get_com_method(desktop, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_uint32))
    set_color = get_com_method(desktop, 8, ctypes.c_long, ctypes.c_uint32)
    current = ctypes.c_uint32()
    check_hresult(get_color(desktop, ctypes.byref(current)), "IDesktopWallpaper.GetBackgroundColor")
    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    wanted = red | green << 8 | blue << 16
    if current.value == wanted:
        return
    backup = previous_system_background_path()
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps({"color": current.value}), encoding="utf-8")
    check_hresult(set_color(desktop, wanted), "IDesktopWallpaper.SetBackgroundColor")


def restore_system_background_color(desktop):
    """Give Windows back the desktop color it had before MarbleScape changed it."""
    backup = previous_system_background_path()
    if not backup.exists():
        return
    color = json.loads(backup.read_text(encoding="utf-8"))["color"]
    if type(color) is not int or not 0 <= color <= 0xFFFFFF:
        raise ValueError("The saved Windows desktop color is invalid.")
    set_color = get_com_method(desktop, 8, ctypes.c_long, ctypes.c_uint32)
    check_hresult(set_color(desktop, color), "IDesktopWallpaper.SetBackgroundColor")
    backup.unlink()


def current_wallpaper_path(desktop, monitor_id):
    """The picture Windows shows on one display, or "" (a solid color or unknown)."""
    get_wallpaper = get_com_method(desktop, 4, ctypes.c_long, ctypes.c_wchar_p,
                                   ctypes.POINTER(ctypes.c_void_p))
    raw_path = ctypes.c_void_p()
    try:
        if get_wallpaper(desktop, monitor_id, ctypes.byref(raw_path)) != 0:
            return ""
        return ctypes.wstring_at(raw_path.value) if raw_path.value else ""
    finally:
        if raw_path.value:
            ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(raw_path.value))


def restore_previous_wallpaper(monitor_id):
    """Restore one connected display and pause its wallpaper updates."""
    global WINDOWS_WALLPAPER_PAUSED
    with WINDOWS_WALLPAPER_LOCK:
        backup = previous_wallpaper_backup(monitor_id)
        if backup is None:
            raise FileNotFoundError(
                "No previous wallpaper was saved for this display. MarbleScape "
                "cannot recover a wallpaper replaced before this feature was installed."
            )
        connected = list_windows_wallpaper_monitors()
        if monitor_id not in {monitor["id"] for monitor in connected}:
            raise ValueError("The selected display is no longer connected.")
        slot = next(monitor.get("slot", monitor_id) for monitor in connected
                    if monitor["id"] == monitor_id)

        previous_paused = WINDOWS_WALLPAPER_PAUSED
        updated_paused = previous_paused | {monitor_id}
        update_active_configuration(lambda text: replace_toml_section_value(
            text, "windows", "paused_displays", json.dumps(sorted(updated_paused)),
        ))
        WINDOWS_WALLPAPER_PAUSED = updated_paused

        def restore():
            desktop = create_desktop_wallpaper_interface()
            try:
                set_wallpaper = get_com_method(
                    desktop, 3, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_wchar_p,
                )
                # An empty ID can set every display: the others get their pictures back.
                others = ({other.get("slot", other["id"]): current_wallpaper_path(desktop, other["id"])
                           for other in connected if other["id"] != monitor_id} if not slot else {})
                check_hresult(
                    set_wallpaper(desktop, slot, str(backup.resolve())),
                    "IDesktopWallpaper.SetWallpaper",
                )
                if slot != monitor_id:
                    set_wallpaper(desktop, monitor_id, str(backup.resolve()))
                for other_slot, path in sorted(others.items(), key=lambda item: item[0] != ""):
                    if path:
                        set_wallpaper(desktop, other_slot, path)
                # The desktop color is system-wide: give it back once
                # MarbleScape no longer updates any connected display.
                shared, positions = effective_wallpaper_positions(paused=updated_paused)
                if all(positions.get(monitor["id"], shared) == "none" for monitor in connected):
                    try:
                        restore_system_background_color(desktop)
                    except Exception as exc:
                        log(f"Unable to restore the Windows desktop color: {exc}")
            finally:
                release_com_pointer(desktop)

        try:
            with_windows_com(restore)
        except Exception as restore_error:
            WINDOWS_WALLPAPER_PAUSED = previous_paused
            try:
                update_active_configuration(lambda text: replace_toml_section_value(
                    text, "windows", "paused_displays", json.dumps(sorted(previous_paused)),
                ))
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{restore_error} Configuration rollback also failed: "
                    f"{rollback_error}"
                ) from restore_error
            raise


def set_windows_wallpaper(image_path):
    with WINDOWS_WALLPAPER_LOCK:
        _set_windows_wallpaper_unlocked(image_path)


def _set_windows_wallpaper_unlocked(image_path):
    if os.name != "nt":
        return

    shared_position, positions = effective_wallpaper_positions()
    position_name = shared_position.lower()
    monitor_positions = {
        monitor_id: mode for monitor_id, mode in positions.items()
        if mode != position_name
    }
    monitor_outputs = {
        monitor_id: values for monitor_id, values in WINDOWS_WALLPAPER_MONITOR_OUTPUTS.items()
        if values
    }
    if position_name == "none" and not monitor_positions:
        return
    monitors = list_windows_wallpaper_monitors()
    if (monitor_positions or monitor_outputs) and not monitors:
        raise RuntimeError("No connected display could be identified for per-display wallpaper settings.")
    per_display = bool(monitors and (monitor_positions or monitor_outputs))
    if monitors and not per_display:
        from PIL import Image
        with Image.open(image_path) as image:
            per_display = "A" in image.getbands() or "transparency" in image.info
    selected = [monitor for monitor in monitors
                if monitor_positions.get(monitor["id"], position_name) != "none"]
    if per_display and not selected:
        return
    for monitor in selected:
        try:
            capture_previous_wallpapers([monitor], image_path)
        except Exception as exc:
            log(f"Unable to save previous wallpaper: {exc}")
    position = WINDOWS_WALLPAPER_POSITIONS.get(position_name, 2)
    # The ID Windows takes for each display; it differs only for a display listed without its path.
    slots = {monitor["id"]: monitor.get("slot", monitor["id"]) for monitor in monitors}
    device_files = {}
    if per_display:
        from PIL import Image

        target_dir = CONTENT_DIR / "device_wallpapers"
        target_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(image_path) as image:
            source = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            source_metadata = json.loads(image.info["MarbleScape"]) if "MarbleScape" in image.info else None
        try:
            rects = [monitor["rect"] for monitor in monitors]
            for monitor in selected:
                monitor_id = monitor["id"]
                mode = monitor_positions.get(monitor_id, position_name)
                settings = {
                    "width": WIDTH, "height": HEIGHT or 0,
                    "aspect_ratio": ASPECT_RATIO,
                    "render_scale": DISPLAY_RENDER_SCALE,
                    "background_color": BACKGROUND_COLOR,
                }
                settings.update(monitor_outputs.get(monitor_id, {}))
                monitor_source = render_monitor_output_source(source, settings)
                try:
                    composed = compose_monitor_wallpaper(
                        monitor_source, mode, monitor["rect"], rects,
                        parse_background_color(settings["background_color"]),
                    )
                finally:
                    if monitor_source is not source:
                        monitor_source.close()
                filename = hashlib.sha256(monitor_id.encode("utf-8")).hexdigest()[:16] + ".png"
                output_file = target_dir / filename
                temporary = output_file.with_suffix(".tmp")
                try:
                    if source_metadata:
                        buffer = io.BytesIO()
                        composed.save(buffer, format="PNG")
                        temporary.write_bytes(embed_png_metadata(buffer.getvalue(),
                            wallpaper_metadata(source_metadata, composed.size, mode)))
                    else:
                        composed.save(temporary, format="PNG")
                    os.replace(temporary, output_file)
                finally:
                    temporary.unlink(missing_ok=True)
                    composed.close()
                device_files[monitor_id] = output_file
        finally:
            source.close()

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
            target_position = 2 if device_files else position
            if device_files and len(selected) < len(monitors):
                get_position = get_com_method(
                    desktop_wallpaper, 11, ctypes.c_long,
                    ctypes.POINTER(ctypes.c_int),
                )
                current_position = ctypes.c_int()
                check_hresult(
                    get_position(desktop_wallpaper, ctypes.byref(current_position)),
                    "IDesktopWallpaper.GetPosition",
                )
                if current_position.value in range(5):
                    target_position = current_position.value
            check_hresult(
                set_position(desktop_wallpaper, target_position),
                "IDesktopWallpaper.SetPosition",
            )
            if device_files:
                # A display Windows lists without its path goes first: its empty ID
                # can set every display, and the others then get their own picture.
                for monitor_id, output_file in sorted(device_files.items(),
                                                      key=lambda item: slots[item[0]] != ""):
                    check_hresult(
                        set_wallpaper(desktop_wallpaper, slots[monitor_id], str(output_file.resolve())),
                        "IDesktopWallpaper.SetWallpaper",
                    )
                for monitor_id, output_file in device_files.items():
                    if slots[monitor_id] != monitor_id:
                        # Also under its real path, which Windows uses again at a later start.
                        set_wallpaper(desktop_wallpaper, monitor_id, str(output_file.resolve()))
            else:
                check_hresult(
                    set_wallpaper(desktop_wallpaper, None, str(image_path.resolve())),
                    "IDesktopWallpaper.SetWallpaper",
                )
            try:
                sync_system_background_color(desktop_wallpaper)
            except Exception as exc:
                log(f"Unable to set the Windows desktop color: {exc}")
        finally:
            release_com_pointer(desktop_wallpaper)

    with_windows_com(apply_wallpaper)
    remember_wallpaper_layout()
    log(f"Windows wallpaper set directly: {image_path}")
    log(f"Windows wallpaper position: {WINDOWS_WALLPAPER_POSITION}")


# The display layout the wallpaper was last set for; None before the first one.
WALLPAPER_LAYOUT_LOCK = threading.Lock()
WALLPAPER_LAYOUT = None
DISPLAY_WATCH_SECONDS = 5.0


def remember_wallpaper_layout():
    global WALLPAPER_LAYOUT
    try:
        layout = windows_display_layout()
    except Exception as exc:
        log(f"Unable to read the display layout: {exc}")
        return
    with WALLPAPER_LAYOUT_LOCK:
        WALLPAPER_LAYOUT = layout


def watch_display_layout(stop_event=None, interval=DISPLAY_WATCH_SECONDS):
    """Set the shown picture again after the displays change; never downloads.

    Displays come up, wake, rotate or change resolution after MarbleScape set
    the wallpaper (at a Windows start the picture is set once, often before a
    slow display is ready). Once a new layout holds for one more check, the
    current picture is composed for it again.
    """
    global WALLPAPER_LAYOUT
    stop_event = APPLICATION_STOP_EVENT if stop_event is None else stop_event
    seen = None
    while not stop_event.wait(interval):
        try:
            current = windows_display_layout()
        except Exception:
            continue
        if current != seen:
            seen = current
            continue
        with WALLPAPER_LAYOUT_LOCK:
            applied = WALLPAPER_LAYOUT
        if applied is None or not current or current == applied or not SET_WINDOWS_WALLPAPER:
            continue
        path = get_current_image_path()
        if path is None or not Path(path).is_file():
            continue
        log("The displays changed; setting the wallpaper again.")
        try:
            set_windows_wallpaper(path)
        except Exception as exc:
            log(f"Windows wallpaper update warning: {exc}")
            # Retried at the next display change or picture, not every few seconds.
            with WALLPAPER_LAYOUT_LOCK:
                WALLPAPER_LAYOUT = current


def start_display_watch():
    if os.name == "nt":
        threading.Thread(target=watch_display_layout, name="MarbleScapeDisplays", daemon=True).start()


# =============================================================================
# UPDATE LOOP
# =============================================================================


def get_noaa_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with NOAA_CLIENTS_LOCK:
        if key not in NOAA_CLIENTS:
            NOAA_CLIENTS.clear()
            NOAA_CLIENTS[key] = NOAAClient(timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT)
        return NOAA_CLIENTS[key]


def get_himawari_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with HIMAWARI_CLIENTS_LOCK:
        if key not in HIMAWARI_CLIENTS:
            HIMAWARI_CLIENTS.clear()
            HIMAWARI_CLIENTS[key] = HimawariClient(
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT
            )
            # NICT's shoreline tiles never change; read each once.
            HIMAWARI_CLIENTS[key].shoreline_cache_dir = CONTENT_DIR / "himawari_shorelines"
        return HIMAWARI_CLIENTS[key]


def get_slider_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    # Measured sector padding survives restarts, so sizes are known at once.
    configure_slider_content_box_store(CONTENT_DIR / SLIDER_CONTENT_BOXES_FILENAME)
    with SLIDER_CLIENTS_LOCK:
        if key not in SLIDER_CLIENTS:
            SLIDER_CLIENTS.clear()
            SLIDER_CLIENTS[key] = SliderClient(
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT
            )
        return SLIDER_CLIENTS[key]


def get_worldview_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with WORLDVIEW_CLIENTS_LOCK:
        if key not in WORLDVIEW_CLIENTS:
            WORLDVIEW_CLIENTS.clear()
            WORLDVIEW_CLIENTS[key] = WorldviewClient(
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT
            )
        return WORLDVIEW_CLIENTS[key]


def get_catalogue_client():
    cache_path = (CONTENT_DIR / "catalogues.json").resolve()
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT, CATALOGUE_RETRIES, str(cache_path))
    with CATALOGUE_CLIENTS_LOCK:
        if key not in CATALOGUE_CLIENTS:
            CATALOGUE_CLIENTS.clear()
            CATALOGUE_CLIENTS[key] = CatalogueClient(
                noaa=get_noaa_client(), himawari=get_himawari_client(),
                slider=get_slider_client(), worldview=get_worldview_client(),
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT,
                cache_path=cache_path, retries=CATALOGUE_RETRIES,
            )
            CATALOGUE_CLIENTS[key].on_storms_changed = note_storms_changed
        return CATALOGUE_CLIENTS[key]


def cached_catalogue_entry(provider, item_id, area_id=None):
    """A saved choice's cached catalogue entry for readable names, or None."""
    try:
        return get_catalogue_client().cached_entry(provider, item_id, area_id)
    except Exception:
        return None


def catalogue_refresh_activity_text():
    """'Refreshing catalogues 2/5' while all catalogues refresh, else ''.

    Covers the manual, daily and startup refresh. The step counts the public
    catalogue groups; the Copernicus dates that follow show no count. Never
    creates a catalogue client.
    """
    with CATALOGUE_CLIENTS_LOCK:
        clients = tuple(CATALOGUE_CLIENTS.values())
    for client in clients:
        status = client.catalogue_refresh_status or {}
        total = status.get("total")
        if status.get("running"):
            if type(total) is int and total > 0:
                step = min(int(status.get("done") or 0) + 1, total)
                return f"Refreshing catalogues {step}/{total}"
            return "Refreshing catalogues"
    schedule = CATALOGUE_SCHEDULE.status() if CATALOGUE_SCHEDULE is not None else {}
    return "Refreshing catalogues" if (schedule or {}).get("running") else ""


def get_copernicus_client():
    key = (COPERNICUS_CLIENT_ID, hashlib.sha256(
        COPERNICUS_CLIENT_SECRET.encode("utf-8")
    ).hexdigest(), float(NETWORK_TIMEOUT_SECONDS), USER_AGENT)
    with COPERNICUS_CLIENTS_LOCK:
        if key not in COPERNICUS_CLIENTS:
            COPERNICUS_CLIENTS.clear()
            COPERNICUS_CLIENTS[key] = CopernicusClient(
                COPERNICUS_CLIENT_ID,
                COPERNICUS_CLIENT_SECRET,
                timeout=NETWORK_TIMEOUT_SECONDS,
                user_agent=USER_AGENT,
                network_attempts=1,
            )
        return COPERNICUS_CLIENTS[key]


def refresh_all_catalogues_now():
    """Force public source catalogues and the configured Copernicus locations.

    The note below the Settings buttons tells how it went.
    """
    try:
        result, errors, incomplete = _refresh_all_catalogues()
    except NetworkCancelledError:
        raise
    except Exception:
        record_catalogue_problem("catalogues", "all sources")
        raise
    if errors:
        record_catalogue_problem("catalogues", ", ".join(incomplete) or "see Downloads & Updates")
        raise RuntimeError("; ".join(str(value) for value in errors))
    record_image_outcome("catalogues_refreshed" if result.get("updated_sources") else "catalogues_current")


def note_storms_changed(source, added, ended):
    """The hourly storm check changed a list: "NOAA (+1 new, 1 ended)" below the buttons."""
    parts = ([f"+{added} new"] if added else []) + ([f"{ended} ended"] if ended else [])
    label = {"noaa": "NOAA", "himawari": "Himawari"}.get(source, source)
    record_image_outcome("storms_updated", detail=f"{label} ({', '.join(parts) or 'names updated'})")


def _refresh_all_catalogues():
    """(result, errors, incomplete source names) of refreshing every catalogue."""
    from marblescape_copernicus import resolve_image_size
    client = get_catalogue_client()
    result = client.refresh_all_catalogues(refresh=True, startup=False)
    errors = list(result.get("errors", ()))
    incomplete = list(result.get("incomplete_sources", ()))
    if COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET:
        selections = [(deepcopy(SOURCE_PROFILES["copernicus"]), get_image_dimensions())]
        for item in deepcopy(IMAGE_PROFILE_LIBRARY).get("items", []):
            settings = item["settings"]
            if settings.get("source", {}).get("provider") == "copernicus":
                output_size = get_output_dimensions()
                profile = settings["sources"]["copernicus"]
                size = (copernicus_auto_size(output_size) if profile.get("image_size", "auto") == "auto"
                        else resolve_image_size(profile, output_size))
                selections.append((profile, size))
        seen = set()
        for profile, output_size in selections:
            key = json.dumps([profile, output_size], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            for attempt in range(CATALOGUE_RETRIES + 1):
                NETWORK_ACTIVITY.check()
                try:
                    dates = get_copernicus_client().list_dates(profile, output_size)
                    client.store_copernicus_dates(profile, output_size, dates)
                    break
                except Exception as exc:
                    NETWORK_ACTIVITY.check()
                    if attempt == CATALOGUE_RETRIES:
                        errors.append(f"Copernicus: {exc}")
                        if "Copernicus" not in incomplete:
                            incomplete.append("Copernicus")
    return result, errors, incomplete


def warm_public_catalogues():
    """Start the daily service; a missed slot is refreshed immediately once."""
    global CATALOGUE_SCHEDULE
    CATALOGUE_SCHEDULE = CatalogueSchedule(
        CONTENT_DIR / "catalogue_refresh_schedule.json", saved_catalogue_refresh_time,
        refresh_all_catalogues_now, APPLICATION_STOP_EVENT,
    )
    CATALOGUE_SCHEDULE.start()


def saved_catalogue_refresh_time():
    """Use the saved schedule even while an image worker finishes its old config."""
    try:
        with CONFIGURATION_FILE_LOCK:
            config = tomllib.loads(ACTIVE_CONFIG_PATH.read_text(encoding="utf-8"))
        return normalize_refresh_time(config.get("download", {}).get("catalogue_refresh_time", DEFAULT_REFRESH_TIME))
    except (OSError, ValueError, TypeError, AttributeError):
        return CATALOGUE_REFRESH_TIME


def _direct_subfolders(folder):
    try:
        return {path for path in folder.iterdir() if path.is_dir()}
    except OSError:
        return set()


def published_image_folders():
    # History images sit one level down (_no profile, <name>_<UUID>). The
    # Latest folder is never searched recursively: a custom one may be large.
    return {LATEST_DIR, HISTORY_DIR} | _direct_subfolders(HISTORY_DIR)


def read_latest_snapshot():
    with CONFIGURATION_FILE_LOCK:
        config = tomllib.loads(ACTIVE_CONFIG_PATH.read_text(encoding="utf-8"))
    return latest_snapshot.from_config(config, lambda value: strict_settings(value, normalize_image_settings_snapshot))


def save_latest_snapshot(record):
    snapshot = latest_snapshot.validate_snapshot(latest_snapshot.from_record(record),
        lambda value: strict_settings(value, normalize_image_settings_snapshot))
    raw = json.dumps(snapshot, ensure_ascii=True, sort_keys=True, allow_nan=False)
    def transform(text):
        if "latest_snapshot" not in tomllib.loads(text):
            text = text.rstrip() + '\n\n[latest_snapshot]\nrecord_json = ""\n'
        return replace_toml_section_value(text, "latest_snapshot", "record_json", raw)
    update_active_configuration(transform)


def choose_automatic_source_resolution(client, provider, profile, output_size,
                                       fit_mode=None, zoom=None):
    """Choose the smallest listed source that avoids enlarging visible pixels."""
    if not callable(getattr(client, "list_products", None)):
        return "largest"
    products = client.list_products(provider, profile["area"], refresh=False)
    product = next(
        (item for item in products if item.get("id") == profile["product"]), None
    )
    if product is None:
        # ``latest(..., 'largest')`` remains an offline-compatible fallback for
        # clients whose catalogue is temporarily unavailable or test doubles
        # that only implement image lookup.
        return "largest"
    resolutions = []
    # SLIDER lists square tile grids; compare the visible size without padding.
    effective = product.get("effective_resolutions") or {}
    for value in product.get("resolutions", ()):
        match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", str(effective.get(value, value)))
        if match:
            resolutions.append((str(value), int(match.group(1)), int(match.group(2))))
    if not resolutions:
        raise RuntimeError("The selected product has no supported source resolution.")
    return automatic_resolution_choice(resolutions, output_size,
                                       VIEW_MODE if fit_mode is None else fit_mode,
                                       ZOOM if zoom is None else zoom)


def automatic_resolution_choice(resolutions, output_size, fit_mode, zoom):
    """The smallest of (key, width, height) that avoids enlarging visible pixels, else the largest."""
    output_width, output_height = map(int, output_size)
    zoom = float(zoom)
    scale_function = min if fit_mode == "fit" else max
    sufficient = [
        item for item in resolutions
        if scale_function(output_width / item[1], output_height / item[2]) * zoom <= 1.0
    ]
    candidates = sufficient or resolutions
    selected = min(candidates, key=lambda item: (item[1] * item[2], item[1], item[2])) \
        if sufficient else max(candidates, key=lambda item: (item[1] * item[2], item[1], item[2]))
    return selected[0]


def automatic_source_output_size(output_size):
    """Cover the configured image and every active Windows display."""
    width, height = map(int, output_size)
    if os.name != "nt" or not SET_WINDOWS_WALLPAPER:
        return width, height
    try:
        monitors = list_windows_wallpaper_monitors()
    except Exception as exc:
        log(f"Unable to size automatic source resolution for monitors: {exc}")
        return width, height
    if len(monitors) < 2:
        return width, height
    display_width = display_height = 0
    for monitor in monitors:
        monitor_id = monitor["id"]
        if display_paused(monitor_id) or WINDOWS_WALLPAPER_MONITOR_POSITIONS.get(
            monitor_id, WINDOWS_WALLPAPER_POSITION
        ) == "none":
            continue
        left, top, right, bottom = monitor["rect"]
        display_width = max(display_width, right - left)
        display_height = max(display_height, bottom - top)
        output = WINDOWS_WALLPAPER_MONITOR_OUTPUTS.get(monitor_id, {})
        if output:
            device_width = int(output.get("width", WIDTH))
            device_height = int(output.get("height", HEIGHT or 0))
            device_ratio = parse_aspect_ratio(output.get("aspect_ratio", ASPECT_RATIO))
            display_width = max(display_width, device_width)
            display_height = max(
                display_height, device_height or round(device_width / device_ratio)
            )
    return (display_width, display_height) if display_width and display_height else (width, height)


def _resolved_profile_resolution(client, provider, profile, output_size):
    if profile["resolution"] != "auto":
        return profile["resolution"]
    size = automatic_source_output_size(output_size)
    fit_mode, zoom = VIEW_MODE, ZOOM
    if provider == "himawari" and is_himawari_storm_area(profile.get("area")):
        # A storm is shown storm-sized: the sharp source for that view.
        fit_mode, zoom = "fit", himawari_storm_view_zoom(size, ZOOM)
    return choose_automatic_source_resolution(client, provider, profile, size, fit_mode, zoom)


def get_noaa_frame(output_size):
    profile = SOURCE_PROFILES[IMAGE_SOURCE]
    client = get_noaa_client()
    resolution = _resolved_profile_resolution(client, IMAGE_SOURCE, profile, output_size)
    return client.latest(
        IMAGE_SOURCE, profile["area"], profile["product"], resolution
    )


def get_himawari_frame(output_size):
    profile = SOURCE_PROFILES["himawari"]
    client = get_himawari_client()
    resolution = _resolved_profile_resolution(client, "himawari", profile, output_size)
    frame = client.latest(
        "himawari", profile["area"], profile["product"], resolution
    )
    # A storm brings its own centre; a full disk takes the saved coordinates.
    if ("center" not in frame and profile.get("center")
            and frame.get("dataset") in HIMAWARI_CENTERED_DATASETS):
        frame["center"] = [profile["latitude"], profile["longitude"]]
    return frame


def himawari_shoreline_color(profile=None):
    """The shoreline color to draw, or None while Plot shorelines is off."""
    profile = SOURCE_PROFILES["himawari"] if profile is None else profile
    return profile.get("shoreline_color") if profile.get("shorelines") else None


def get_slider_frame(output_size):
    profile = SOURCE_PROFILES["slider"]
    client = get_slider_client()
    measure = getattr(client, "content_box", None)
    if profile["resolution"] == "auto" and callable(measure):
        try:
            # Automatic sizing needs the sector's visible size, measured once.
            measure(profile["area"], profile["product"])
        except SliderError as exc:
            log(f"CIRA SLIDER sector size unavailable; using its tile grid: {exc}")
    resolution = _resolved_profile_resolution(client, "slider", profile, output_size)
    return client.latest(
        "slider", profile["area"], profile["product"], resolution
    )


def get_worldview_frame(output_size):
    profile = SOURCE_PROFILES["worldview"]
    client = get_worldview_client()
    resolution = _resolved_profile_resolution(client, "worldview", profile, output_size)
    return client.latest(
        "worldview", profile["area"], profile["product"], resolution
    )


def get_copernicus_frame(output_size):
    reference = (dt.datetime.now(dt.timezone.utc).date()
                 if DISPLAY_TIME_ZONE == "utc" else dt.date.today())
    client = get_copernicus_client()
    profile = SOURCE_PROFILES["copernicus"]
    choice = None
    if profile.get("auto_recommendation"):
        from marblescape_copernicus_advice import recommended_profile
        try:
            profile, choice = recommended_profile(client, profile, output_size, today=reference)
        except DownloadCancelledError:
            raise
        except Exception as exc:  # The saved settings still render.
            log(f"Auto recommendation unavailable; the saved settings are used: {exc}")
        else:
            log(f"Auto recommendation: {choice}" if choice else
                "Auto recommendation found no variant; the saved settings are used.")
    frame = client.latest(profile, output_size, reference_date=reference)
    if profile.get("auto_recommendation"):
        frame["auto_choice"] = choice or "Saved settings"
    return frame


def noaa_frame_signature(frame):
    profile = SOURCE_PROFILES[IMAGE_SOURCE]
    return (IMAGE_SOURCE, profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"])


def himawari_frame_signature(frame):
    profile = SOURCE_PROFILES["himawari"]
    # A moving storm, or other coordinates, give another picture of the same time.
    return ("himawari", profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"], tuple(frame.get("center") or ()),
            himawari_shoreline_color(profile))


def slider_frame_signature(frame):
    profile = SOURCE_PROFILES["slider"]
    box = tuple(frame.get("content_box") or SLIDER_FULL_CONTENT_BOX)
    return ("slider", profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            # Padded sectors are framed without their padding; square ones keep their keys.
            *((("content_box", box),) if box != SLIDER_FULL_CONTENT_BOX else ()),
            frame["timestamp"], frame["url"])


def worldview_frame_signature(frame):
    profile = SOURCE_PROFILES["worldview"]
    return ("worldview", profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"])


def copernicus_frame_signature(frame):
    from marblescape_mosaic_adjustments import TONE_REVISION
    profile = frame["profile"]
    return (
        "copernicus", profile["configuration"], profile["mission"],
        profile["product"], profile["layer"], profile["date"],
        profile["date_mode"], profile["quarter_offset"], profile["month_offset"],
        profile["latitude"], profile["longitude"], profile["map_zoom"],
        profile["map_labels"], profile["coverage_mode"], profile["lookback_days"],
        profile["max_cloud_cover"], profile["brightness"],
        profile.get("no_data_color", "#FFFFFF"),
        # The labels' color counts only while they are drawn.
        map_overlay_signature(profile)[1],
        profile.get("contrast", 100), profile.get("auto_brightness", False), profile.get("auto_contrast", False),
        profile.get("image_size", "auto"),
        # Separate country-border choices; empty while they match the labels.
        *map_overlay_signature(profile)[2:],
        # A regular layer's No-data color; empty while it keeps the map background.
        *((("scene_no_data_color", profile["scene_no_data_color"]),)
          if profile.get("scene_no_data_color", "transparent") != "transparent" else ()),
        # Layers that now get dataMask as alpha render their gaps differently.
        *((("data_mask_alpha", True),)
          if copernicus_needs_data_mask_alpha(frame.get("layer")) else ()),
        # Mosaics whose tones changed with mosaic-tone-v2 render once more;
        # unaffected cached pictures stay valid. Auto contrast pictures also
        # carry the tuning revision, so a retuned look replaces cached ones.
        *((("tone", "mosaic-tone-v2", TONE_REVISION) if profile.get("auto_contrast", False)
           else ("tone", "mosaic-tone-v2"),)
          if mosaic_tone_changed_in_v2(profile, frame.get("layer")) else ()),
        *copernicus_tone_rule_signature(profile, frame.get("layer")),
        frame["timestamp"],
    )


def copernicus_tone_rule_signature(profile, layer):
    """The tone rule of a picture whose tones differ from before the rules.

    Pictures of a layer with a rule and any tone setting, and mosaics without a
    rule that had one (now rendered stock), render once more; untouched
    pictures stay valid.
    """
    from marblescape_copernicus import needs_exact_data_mask, tone_rule
    from marblescape_mosaic_adjustments import RULE_ALGORITHM
    tones = (profile.get("auto_brightness", False) or profile.get("auto_contrast", False)
             or profile.get("brightness", 100) != 100 or profile.get("contrast", 100) != 100)
    rule = tone_rule(layer)
    signature = []
    if rule is not None and tones:
        signature.append(("tone_rule", RULE_ALGORITHM, rule))
    elif rule is None and tones and "date_granularity" in (layer or {}):
        signature.append(("tone_rule", RULE_ALGORITHM, "stock"))
    if needs_exact_data_mask(layer):
        signature.append(("data_mask", "exact"))
    return tuple(signature)


def mosaic_tone_changed_in_v2(profile, layer):
    """True when mosaic-tone-v2 renders this selection differently from v1."""
    from marblescape_copernicus_mosaics import soft_highlight_layer
    if "date_granularity" not in (layer or {}):
        return False
    return (profile.get("auto_brightness", False) or profile.get("auto_contrast", False)
            or profile.get("contrast", 100) != 100
            or (soft_highlight_layer(layer) and profile.get("brightness", 100) > 100))


def image_save_notice(changed, rotation_shown, interval_minutes, time_zone=None, now=None):
    """The Image tab's note after Save: when its saved settings show.

    Save loads no picture: the next regular image check (one update interval
    from now) uses them, unless a rotation profile is on screen.
    """
    if not changed:
        return "✓ Saved"
    if rotation_shown:
        return "✓ Saved - rotation keeps showing its profiles"
    zone = DISPLAY_TIME_ZONE if time_zone is None else normalize_time_zone(time_zone)
    now = dt.datetime.now(dt.timezone.utc) if now is None else now
    when = now + dt.timedelta(minutes=float(interval_minutes))
    if zone == "utc":
        shown, today, label = when.astimezone(dt.timezone.utc), now.astimezone(dt.timezone.utc), " UTC"
    else:
        shown, today, label = when.astimezone(), now.astimezone(), ""
    time_text = f"{shown:%H:%M}" if shown.date() == today.date() else f"{shown:%Y-%m-%d %H:%M}"
    return f"✓ Saved - loads at the next update ({time_text}{label})"


def picture_age_text(timestamp, now=None):
    """How old a picture's acquisition is: minutes, then hours, then days."""
    now = dt.datetime.now(dt.timezone.utc) if now is None else now
    minutes = max(0.0, (now - timestamp).total_seconds() / 60)
    if minutes < 120:
        return f"{minutes:.0f} min old"
    if minutes < 48 * 60:
        return f"{minutes / 60:.0f} h old"
    return f"{minutes / 1440:.0f} days old"


def image_source_status_text(time_zone=None):
    """Describe the installed image, keeping source errors and image age visible."""
    time_zone = DISPLAY_TIME_ZONE if time_zone is None else normalize_time_zone(time_zone)
    with IMAGE_STATUS_LOCK:
        status = dict(IMAGE_STATUS)
    if status["error"]:
        return "Update unavailable; previous image kept. " + status["error"]
    if status["provider"] != IMAGE_SOURCE or not status["timestamp"]:
        return "Waiting for an image from " + SOURCE_LABELS[IMAGE_SOURCE]
    if IMAGE_SOURCE == "copernicus":
        return copernicus_source_status_text(status, time_zone)
    if IMAGE_SOURCE == "worldview":
        timestamp_text = str(status["timestamp"])
        if timestamp_text == "timeless":
            return "NASA Worldview | timeless visualization"
        timestamp = dt.datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        kind = "selected" if status.get("fixed_time") else "latest"
        return (f"NASA Worldview | {kind} acquisition "
                f"{format_display_datetime(timestamp, time_zone)}"
                f" | {picture_age_text(timestamp)}")
    timestamp = dt.datetime.fromisoformat(status["timestamp"].replace("Z", "+00:00"))
    age = max(0, (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds() / 60)
    stale = not status.get("fixed_time") and age > max(30, status["interval_minutes"] * 3)
    selected = "selected acquisition " if status.get("fixed_time") else ""
    return (f"{SOURCE_LABELS[IMAGE_SOURCE]} | {selected}"
            f"{format_display_datetime(timestamp, time_zone, include_seconds=True)}"
            f" | {picture_age_text(timestamp)}" + (" (delayed)" if stale else ""))


def copernicus_source_status_text(status, time_zone):
    """The Image header's Copernicus line: the picture's date, age and coverage."""
    profile = SOURCE_PROFILES["copernicus"]
    auto = status.get("auto") or {}
    # With the rule on, the third line names the Gap fill it took, not the saved one.
    coverage = (
        f" | {profile['lookback_days']}-day gap fill"
        if profile["coverage_mode"] == "fill_gaps" and not auto.get("enabled") else ""
    )
    coverage_value = status.get("data_coverage")
    coverage_text = (f" | Data coverage: {coverage_value:.2f}%"
                     if type(coverage_value) in (int, float) else "")
    timestamp_text = str(status["timestamp"])
    if timestamp_text == "timeless":
        return "Copernicus Browser | timeless dataset"
    timestamp = dt.datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    # Every picture shows its age, also a selected date or a mosaic period, always last.
    age = f" | {picture_age_text(timestamp)}"
    # Short enough for one line at the minimum window width. A mosaic has no Gap fill.
    if profile["date_mode"] == "relative_month":
        offset = profile["month_offset"]
        back = "current" if not offset else f"-{offset} month" + ("s" if offset != 1 else "")
        return (f"Copernicus Browser | rolling month {timestamp:%Y-%m} ({back}) | "
                f"mosaic {timestamp:%Y-%m-%d}{coverage_text}{age}")
    if profile["date_mode"] == "relative_quarter":
        quarter = (timestamp.month - 1) // 3 + 1
        offset = profile["quarter_offset"]
        back = "current" if not offset else f"-{offset}Q"
        return (f"Copernicus Browser | rolling quarter {timestamp.year} Q{quarter} ({back}) | "
                f"mosaic {timestamp:%Y-%m-%d}{coverage_text}{age}")
    if status.get("fixed_time"):
        return (f"Copernicus Browser | selected acquisition {timestamp:%Y-%m-%d}"
                f"{coverage}{coverage_text}{age}")
    return (f"Copernicus Browser | latest acquisition "
            f"{format_display_datetime(timestamp, time_zone)}{coverage}{coverage_text}{age}")


def run_noaa_diagnostics(args):
    """Use the selected NOAA catalog for the existing diagnostic CLI options."""
    client = get_noaa_client()
    profile = SOURCE_PROFILES[IMAGE_SOURCE]
    if args.list_layers is not None or args.export_layers is not None:
        products = client.list_products(IMAGE_SOURCE, profile["area"], refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for product in products:
                if all(word in (product["id"] + " " + product["label"]).casefold() for word in words):
                    print(f'{product["id"]}: {product["label"]} | {", ".join(product["resolutions"])}')
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(products, indent=2), encoding="utf-8")
        return
    frame = get_noaa_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log(f"Configuration is valid against the NOAA catalog; image time: {frame['timestamp']}")


def run_himawari_diagnostics(args):
    """Use the selected Himawari catalogue for diagnostic CLI options."""
    client = get_himawari_client()
    profile = SOURCE_PROFILES["himawari"]
    if args.list_layers is not None or args.export_layers is not None:
        products = client.list_products("himawari", profile["area"], refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for product in products:
                if all(word in (product["id"] + " " + product["label"]).casefold()
                       for word in words):
                    print(f'{product["id"]}: {product["label"]} | {", ".join(product["resolutions"])}')
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(products, indent=2), encoding="utf-8")
        return
    frame = get_himawari_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log(f"Configuration is valid against the Himawari catalogue; image time: {frame['timestamp']}")


def run_slider_diagnostics(args):
    """Use the selected CIRA SLIDER catalogue for diagnostic CLI options."""
    client = get_slider_client()
    profile = SOURCE_PROFILES["slider"]
    if args.list_layers is not None or args.export_layers is not None:
        products = client.list_products("slider", profile["area"], refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for product in products:
                if all(word in (product["id"] + " " + product["label"]).casefold()
                       for word in words):
                    print(f'{product["id"]}: {product["label"]} | {", ".join(product["resolutions"])}')
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(products, indent=2), encoding="utf-8")
        return
    frame = get_slider_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log(f"Configuration is valid against the CIRA SLIDER catalogue; image time: {frame['timestamp']}")


def run_copernicus_diagnostics(args):
    profile = SOURCE_PROFILES["copernicus"]
    product = get_copernicus_product(profile["configuration"], profile["product"])
    layer = get_copernicus_layer(product, profile["layer"])
    if args.list_layers is not None:
        words = args.list_layers.casefold().split()
        for item in product["layers"]:
            text = f"{item['id']} {item['name']} {item['data_type']}"
            if all(word in text.casefold() for word in words):
                print(f"{item['id']}: {item['name']} | {item['data_type']}")
    if args.export_layers is not None:
        args.export_layers.write_text(json.dumps(product["layers"], indent=2), encoding="utf-8")
    if args.print_urls:
        print(COPERNICUS_PROCESS_URL)
    if args.validate_config:
        width, height = get_image_dimensions()
        frame = get_copernicus_frame((width, height))
        log("Configuration is valid against the Copernicus catalogue; image time: " + frame["timestamp"])


def run_worldview_diagnostics(args):
    client = get_worldview_client()
    if args.list_layers is not None or args.export_layers is not None:
        layers = client.list_areas("worldview", refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for layer in layers:
                text = f"{layer['id']} {layer['label']} {layer['category']}"
                if all(word in text.casefold() for word in words):
                    print(f"{layer['id']}: {layer['label']} | {layer['matrix_set']}")
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(layers, indent=2), encoding="utf-8")
        return
    frame = get_worldview_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log("Configuration is valid against the NASA GIBS catalogue; image time: "
            + frame["timestamp"])


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
    if render_mode in {"noaa", "himawari", "slider", "worldview"}:
        profile = SOURCE_PROFILES[IMAGE_SOURCE]
        log(f"Source: {SOURCE_LABELS[IMAGE_SOURCE]}")
        if render_mode == "worldview":
            log(f"Layer: {profile['area']} | Date: {profile['product']}")
        else:
            log(f"Area: {profile['area']} | Product: {profile['product']}")
        source_size = profile["resolution"]
        if IMAGE_SOURCE == "slider":
            source_size = slider_effective_resolution(profile["area"], source_size)
        log(f"Source size: {source_size} | Output: {output_width} x {output_height}")
        time_label = ("Latest available" if profile["product"] == "latest"
                      else "Fixed " + profile["product"])
        log(f"View: {VIEW_MODE}, zoom {ZOOM:g} | {time_label} still image")
        log(f"Update interval: {UPDATE_INTERVAL_MINUTES:g} minute(s)")
        log(f"Latest directory: {LATEST_DIR}")
        return
    if render_mode == "copernicus":
        profile = SOURCE_PROFILES["copernicus"]
        product = get_copernicus_product(profile["configuration"], profile["product"])
        layer = get_copernicus_layer(product, profile["layer"])
        log("Source: Copernicus Browser / Sentinel Hub")
        log(f"Mission: {profile['mission']} | Configuration: {profile['configuration']}")
        log(f"Product: {product['name']} | Layer: {layer['name']}")
        log(f"Location: {profile['latitude']:g}, {profile['longitude']:g} | map zoom {profile['map_zoom']}")
        log(f"Date: {profile['date']} | Output: {output_width} x {output_height}")
        coverage = profile["coverage_mode"]
        if coverage == "fill_gaps":
            coverage += f" ({profile['lookback_days']} days)"
        log(f"Map labels: {profile['map_labels']} | Country borders: "
            f"{profile.get('map_borders', profile['map_labels'])} | Coverage: {coverage} | "
            f"No-data color: {copernicus_no_data_choice(profile, layer)}")
        log(f"Catalogue revision: {copernicus_catalogue_revision()}")
        log(f"Update interval: {UPDATE_INTERVAL_MINUTES:g} minute(s)")
        log(f"Latest directory: {LATEST_DIR}")
        return
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
    inherited = " (General's display render quality)" if RENDER_SCALE_INHERITED else ""
    if effective_render_scale_setting() == "auto":
        log(
            "Requested render quality: automatic maximum "
            f"({requested_render_scale:g}x for this output){inherited}"
        )
    else:
        log(f"Requested render quality: {requested_render_scale:g}x{inherited}")
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
            "Warning: EUMETSAT may reject dimensions above approximately "
            f"{MAX_WMS_DIMENSION} pixels because of its rendering memory limit."
        )
    log(f"BBOX: {bbox}")
    log(f"Render mode: {render_mode}")
    log(f"Background color: {normalize_background_color(BACKGROUND_COLOR)}")
    log(f"Black TrueColor night side: {TRUECOLOR_BLACK_NIGHT}")
    eumetsat_profile = SOURCE_PROFILES["eumetsat"]
    if eumetsat_profile.get("fill_gaps"):
        log(
            "LEO gap filling: enabled, maximum lookback "
            f"{eumetsat_profile['gap_fill_lookback_hours']} hours"
        )
    else:
        log("LEO gap filling: disabled")
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


def record_source_frame_status(render_mode, requests, coverage=None, source_time=None):
    """Record provider timing after either a download or a persistent cache hit.

    EUMETSAT's ``source_time`` is the oldest layer time of the picture.
    """
    if render_mode in {"server", "local", "truecolor_black_night"}:
        with IMAGE_STATUS_LOCK:
            IMAGE_STATUS.update(provider="eumetsat", timestamp=source_time, interval_minutes=15,
                                fixed_time=IMAGE_TIME is not None, error="")
    elif render_mode in {"noaa", "himawari", "slider", "worldview"}:
        frame = requests[0]["frame"]
        with IMAGE_STATUS_LOCK:
            IMAGE_STATUS.update(provider=IMAGE_SOURCE, timestamp=frame["timestamp"],
                                interval_minutes=frame.get("expected_interval_seconds", 600) / 60,
                                fixed_time=bool(frame.get("fixed_time")), error="")
    elif render_mode == "copernicus":
        frame = requests[0]["frame"]
        with IMAGE_STATUS_LOCK:
            IMAGE_STATUS.update(
                provider="copernicus", timestamp=frame["timestamp"],
                interval_minutes=UPDATE_INTERVAL_MINUTES,
                fixed_time=(not frame["latest"] and
                            frame["profile"]["date_mode"] == "catalogue"), error="",
                data_coverage=(coverage or {}).get("data_coverage_percent") if isinstance(coverage, dict) else None,
                auto=copernicus_auto_status(frame),
            )


def copernicus_auto_status(frame):
    """Use auto recommendation of a rendered Copernicus picture, for the Image header."""
    profile = frame.get("profile") or {}
    if not profile.get("auto_recommendation"):
        return {"enabled": False}
    from marblescape_copernicus_advice import priority_label
    mosaic = "date_granularity" in (frame.get("layer") or {})
    return {"enabled": True,
            "priority": priority_label(profile.get("auto_priority", "fewest_clouds"), mosaic),
            # Mosaics have no precise check option.
            "precise": None if mosaic else bool(profile.get("auto_precise", False)),
            "choice": frame.get("auto_choice") or "Saved settings"}


def image_header_status_text(time_zone=None):
    """The Image header's line about the picture on screen: "On screen: ..." with "·"."""
    text = image_source_status_text(time_zone)
    if IMAGE_SOURCE == "eumetsat" and text.startswith("Waiting for"):
        return "On screen: EUMETSAT"  # Layers without a time (basemaps only).
    if text.startswith(("Update unavailable", "Waiting for")):
        return text  # No picture of the selection on screen yet, or an error.
    # "Source: detail · detail": a colon after the image source, then dots.
    text = text.replace(" | ", ": ", 1).replace(" | ", " · ")
    text = re.sub(r"\b(\d+) day\(s\) old", lambda match: (
        f"{match.group(1)} day old" if match.group(1) == "1" else f"{match.group(1)} days old"), text)
    return "On screen: " + text


# The priorities' colors, as in Compare variants and the Priority dropdown.
AUTO_PRIORITY_PALETTE = {"Fewest clouds": "link", "Data coverage": "success", "Newest": "newest"}


def image_header_auto_parts():
    """(priority, palette key, rest) of the Copernicus picture on screen, ("No", None, "")
    while the rule was off, or None for other sources and before a picture."""
    with IMAGE_STATUS_LOCK:
        status = dict(IMAGE_STATUS)
    auto = status.get("auto")
    if IMAGE_SOURCE != "copernicus" or status.get("provider") != "copernicus" or not auto:
        return None
    if not auto.get("enabled"):
        return "No", None, ""
    rest = " · " + auto["choice"] + (" · precise check" if auto.get("precise") else "")
    return auto["priority"], AUTO_PRIORITY_PALETTE.get(auto["priority"]), rest


def profile_cache_source_time(render_mode, requests, source_signature=None):
    """Return the acquisition time represented by a rendered profile image."""
    if render_mode in {"noaa", "himawari", "slider", "copernicus", "worldview"} and requests:
        timestamp = requests[0].get("frame", {}).get("timestamp")
        if timestamp and timestamp != "timeless":
            return str(timestamp)
    if render_mode in {"server", "local", "truecolor_black_night"}:
        timestamps = []
        for entry in source_signature or ():
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            value = str(entry[1]).strip()
            try:
                parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                timestamps.append(parsed.astimezone(dt.timezone.utc))
            except (ValueError, OverflowError):
                continue
        if timestamps:
            return min(timestamps).isoformat().replace("+00:00", "Z")
    return None


def image_profile_name(profile_id):
    """Resolve a saved profile name without putting the profile library in a PNG."""
    if profile_id is None:
        return None
    for item in IMAGE_PROFILE_LIBRARY.get("items", ()):
        if item["id"] == profile_id:
            return item["name"]
    return None


def picture_source_resolution(render_mode, requests, render_size):
    """"WIDTHxHEIGHT" of the source picture behind an image, or None.

    NOAA, Himawari, CIRA SLIDER (its visible part) and NASA Worldview: the
    downloaded picture; EUMETSAT: the rendered WMS size. Copernicus' PNG is its
    own source picture.
    """
    if render_mode in {"noaa", "himawari", "slider", "worldview"} and requests:
        frame = requests[0].get("frame", {})
        match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", str(frame.get("resolution", "")))
        if not match:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        if render_mode == "slider" and frame.get("content_box"):
            from marblescape_slider import content_size
            width, height = content_size(width, height, frame["content_box"])
        return f"{width}x{height}"
    if render_mode in {"server", "local", "truecolor_black_night"}:
        return "{}x{}".format(*map(int, render_size))
    return None


def image_provenance(render_mode, requests, output_size, source_time=None,
                     profile_name=None, profile_id=None):
    """Select only public, image-relevant values; never serialize full settings."""
    width, height = map(int, output_size)
    result = {
        "schema_version": IMAGE_METADATA_VERSION,
        "software": "MarbleScape",
        "software_version": VERSION,
        "source": SOURCE_LABELS[IMAGE_SOURCE],
        "width": width,
        "height": height,
    }
    result["generated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    result.update(data_coverage.unavailable())
    if profile_id is None:
        profile_id = unmodified_applied_profile_id()
    if profile_id:
        profile_name = image_profile_name(profile_id) or profile_name
        result["profile_id"] = profile_id
    if profile_name:
        result["profile_name"] = profile_name
    if render_mode == "copernicus":
        frame = requests[0]["frame"]
        profile, product, layer = frame["profile"], frame["product"], frame["layer"]
        result.update(
            mission=profile["mission"], product=product["name"],
            layer=layer["name"], latitude=profile["latitude"],
            longitude=profile["longitude"], map_zoom=profile["map_zoom"],
            map_labels=profile["map_labels"],
            map_label_color=profile.get("map_label_color", "#000000"),
            map_borders=profile.get("map_borders", profile["map_labels"]),
            map_border_color=profile.get("map_border_color", profile.get("map_label_color", "#000000")),
            selected_date=profile["date"], resolved_date=frame["date"],
        )
        if "date_granularity" in layer:
            if layer["date_granularity"] in {"quarter", "month"}:
                result["date_mode"] = profile["date_mode"]
                offset_key = "quarter_offset" if layer["date_granularity"] == "quarter" else "month_offset"
                result[offset_key] = profile[offset_key]
            period_date = dt.date.fromisoformat(frame["date"])
            granularity = layer["date_granularity"]
            result["mosaic_period"] = (
                f"{period_date.year}-Q{(period_date.month - 1) // 3 + 1}"
                if granularity == "quarter" else
                f"{period_date.year}-{period_date.month:02d}"
                if granularity == "month" else str(period_date.year)
            )
            result["mosaic_brightness_percent"] = profile["brightness"]
            result["no_data_color"] = profile.get("no_data_color", "#FFFFFF")
            result["mosaic_contrast_percent"] = profile.get("contrast", 100)
            result["auto_brightness"] = profile.get("auto_brightness", False)
            result["auto_contrast"] = profile.get("auto_contrast", False)
        else:
            from marblescape_copernicus import tone_rule
            if tone_rule(layer) is not None:
                # Regular layers with a tone rule record the same tone fields as mosaics.
                result["mosaic_brightness_percent"] = profile["brightness"]
                result["mosaic_contrast_percent"] = profile.get("contrast", 100)
                result["auto_brightness"] = profile.get("auto_brightness", False)
                result["auto_contrast"] = profile.get("auto_contrast", False)
            result["coverage_mode"] = profile["coverage_mode"]
            result["scene_no_data_color"] = profile.get("scene_no_data_color", "transparent")
            if profile["coverage_mode"] == "fill_gaps":
                result["lookback_days"] = profile["lookback_days"]
            if copernicus_supports_cloud_filter(layer):
                result["max_cloud_cover_percent"] = profile["max_cloud_cover"]
            if source_time:
                result["source_time_utc"] = source_time
        if profile.get("auto_recommendation"):
            # The fields above describe the rendered picture; the rule chose them.
            result.update(auto_recommendation=True, auto_priority=profile["auto_priority"],
                          auto_precise=profile.get("auto_precise", False),
                          auto_choice=frame.get("auto_choice") or "Saved settings")
    else:
        profile = SOURCE_PROFILES.get(IMAGE_SOURCE, {})
        for name in ("satellite", "mission", "area", "sector", "product",
                     "layer", "resolution", "theme"):
            value = profile.get(name)
            if type(value) in (str, int, float, bool) and value != "":
                result[name] = value
        if IMAGE_SOURCE == "eumetsat":
            result["layers"] = [
                layer["name"] for layer in LAYER_CONFIG
                if layer.get("enabled", True) and layer.get("name")
            ]
        if IMAGE_SOURCE == "himawari":
            result["shorelines"] = bool(profile.get("shorelines"))
            if result["shorelines"]:
                result["shoreline_color"] = profile["shoreline_color"]
            frame = requests[0]["frame"] if requests else {}
            if frame.get("center"):
                # The place in the middle: the saved coordinates or the storm's position.
                result["center_latitude"], result["center_longitude"] = (
                    float(value) for value in frame["center"])
            if profile.get("center"):
                result.update(center=True, latitude=profile["latitude"],
                              longitude=profile["longitude"])
        if source_time:
            result["source_time_utc"] = source_time
    if render_mode == "copernicus":
        result["image_size_selection"] = requests[0]["frame"]["profile"].get("image_size", "auto")
    snapshot = image_settings_snapshot()
    if render_mode == "copernicus":
        snapshot["sources"]["copernicus"] = deepcopy(requests[0]["frame"]["profile"])
    result["profile_settings"] = portable_settings(snapshot, normalize_image_settings_snapshot)
    return result


def _perform_update(
    render_mode,
    requests,
    render_width,
    render_height,
    output_width,
    output_height,
    cache_profile_id=None,
    cache_configuration_signature=None,
    cache_source_signature=None,
    cache_source_time=None,
):
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    coverage = data_coverage.unavailable()
    adjustments = None
    if render_mode == "noaa":
        data = get_noaa_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "himawari":
        data = get_himawari_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
            shorelines=himawari_shoreline_color(),
        )
        downloaded_size = len(data)
    elif render_mode == "slider":
        data = get_slider_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "worldview":
        data = get_worldview_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "copernicus":
        frame = requests[0]["frame"]
        client = get_copernicus_client()
        data, downloaded_size = client.fetch_image(frame)
        coverage = getattr(client, "last_data_coverage", None) or data_coverage.unavailable()
        adjustments = getattr(client, "last_render_adjustments", None)
        for warning in client.last_render_warnings:
            log(f"Copernicus map overlay warning: {warning}")
    else:
        data, downloaded_size = render_image(
            render_mode,
            requests,
            render_width,
            render_height,
        )
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    if isinstance(coverage, dict) and coverage.get("data_coverage_percent") == 0:
        # A picture without any image data would be a black or blank wallpaper:
        # the previous picture stays, and the header says why.
        raise NoImageData(NO_IMAGE_DATA_TEXT)
    if (render_width, render_height) != (output_width, output_height):
        data = resize_rendered_image(data, output_width, output_height)
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    if APPLICATION_STOP_EVENT.is_set():
        raise RuntimeError("Update cancelled because the application is stopping.")
    # Settings changed during the download: its picture is kept in the cache of
    # the profile it was made for (without a profile in the Latest snapshot's
    # slot) instead of being discarded; Latest and the wallpaper stay as they are.
    superseded = CONFIGURATION_RELOAD_EVENT.is_set()
    if superseded and cache_profile_id is None:
        cache_profile_id = unmodified_applied_profile_id() or latest_snapshot.CACHE_ID
    # The Latest snapshot's cache slot holds an image without a profile.
    named_cache_id = None if cache_profile_id == latest_snapshot.CACHE_ID else cache_profile_id
    provenance = image_provenance(
            render_mode, requests, (output_width, output_height),
            source_time=cache_source_time,
            profile_name=image_profile_name(named_cache_id),
            profile_id=named_cache_id,
    )
    source_resolution = picture_source_resolution(render_mode, requests, (render_width, render_height))
    if source_resolution:
        provenance["source_resolution"] = source_resolution
    provenance["rendered_image_sha256"] = calculate_sha256(data)
    data_coverage.validate(coverage)
    provenance.update(coverage)
    if isinstance(adjustments, dict):
        from marblescape_mosaic_adjustments import validate as validate_adjustments
        validate_adjustments(adjustments, requests[0]["frame"]["profile"])
        provenance["mosaic_adjustments"] = adjustments
    anonymous = not provenance.get("profile_id")
    if anonymous:
        provenance.update(profile_kind=latest_snapshot.SYSTEM_KIND, profile_name=latest_snapshot.SYSTEM_NAME)
    # Preserve the original creation time when a forced refresh returns identical
    # pixels/settings; otherwise timestamps alone would create duplicate history.
    previous_path = (get_profile_cache().current(cache_profile_id) if cache_profile_id
                     else next(iter(get_latest_image_files()), None))
    if previous_path:
        try:
            from PIL import Image
            with Image.open(previous_path) as previous_image:
                previous = json.loads(previous_image.text.get("MarbleScape", "{}"))
            previous_time = previous.pop("generated_at_utc", None)
            previous_identity = {}
            if anonymous and previous.get("profile_kind") == latest_snapshot.SYSTEM_KIND:
                previous_identity = {key: previous.pop(key) for key in ("profile_id", "snapshot_id") if key in previous}
            comparable = {key: value for key, value in provenance.items() if key != "generated_at_utc"}
            if previous_time and previous == comparable:
                provenance["generated_at_utc"] = previous_time
                provenance.update(previous_identity)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if anonymous:
        provenance.setdefault("profile_id", uuid.uuid4().hex)
        provenance.setdefault("snapshot_id", uuid.uuid4().hex)
    data = embed_png_metadata(data, provenance)
    DOWNLOAD_PROGRESS.seal_cancellation()
    if cache_profile_id is None:
        installed_path = save_latest_image(
            data,
            configuration_signature=cache_configuration_signature,
            output_size=(output_width, output_height),
            source_signature=cache_source_signature,
            source_time=cache_source_time,
        )
        current_path = installed_path
        if current_path is None:
            latest_files = get_latest_image_files()
            current_path = latest_files[0] if latest_files else None
        if cache_configuration_signature is not None:
            # An unmodified manually applied profile (forced downloads included)
            # also keeps its image in the profile cache, so returning to the
            # profile later can skip the download; an image without a profile
            # keeps it in the latest snapshot's slot. Latest already archived
            # the previous image, so the cache does not archive it again.
            try:
                cache = get_profile_cache()
                cache.install(latest_snapshot.CACHE_ID if anonymous else provenance["profile_id"],
                              cache_configuration_signature,
                              cache_source_signature, data, (output_width, output_height),
                              source_time=cache_source_time)
                cache.prune()
            except Exception as exc:
                log(f"Profile cache store warning: {exc}")
    else:
        installed_path, current_path = save_profile_image(
            cache_profile_id,
            cache_configuration_signature,
            cache_source_signature,
            data,
            (output_width, output_height),
            source_time=cache_source_time,
        )
    if anonymous and current_path is not None and Path(current_path).is_file():
        try:
            save_latest_snapshot(provenance)
        except (OSError, ValueError) as exc:
            warning = f"Image saved, but latest snapshot could not be saved: {exc}"
            log(warning)
            DOWNLOAD_PROGRESS.set_warning(warning)
    DOWNLOAD_PROGRESS.set_data_coverage(coverage)
    if not superseded:
        # A kept picture of the previous settings does not describe the source now.
        record_source_frame_status(render_mode, requests, coverage, cache_source_time)
    cleanup_history()
    if render_mode in {"local", "truecolor_black_night", "copernicus"}:
        log(f"Total downloaded layer data: {format_bytes(downloaded_size)}")
    if superseded:
        owner = image_profile_name(named_cache_id) or latest_snapshot.SYSTEM_NAME
        raise UpdateSuperseded(
            f"Settings changed during the download; its picture was kept in the cache of {owner}.")
    return installed_path, current_path, len(data)


def expected_download_request_count(render_mode, requests):
    """Return a reliable response count, or None for dynamic tiled renders."""
    if render_mode in {"server", "noaa", "worldview"}:
        return 1
    if render_mode in {"local", "truecolor_black_night"}:
        return len(requests) or None
    if render_mode in {"himawari", "slider"} and requests:
        if render_mode == "slider":
            return None
        return 1 if requests[0].get("frame", {}).get("kind") == "jma" else None
    return None


class DownloadRetriesExhausted(RuntimeError):
    """A transient image-transfer failure used all configured attempts."""


class UpdateSuperseded(RuntimeError):
    """A settings change arrived during a download; its picture went to the cache only."""


# The provider no longer lists a saved area, sector, layer, product or size.
SELECTION_LOST_ERRORS = (NOAASelectionLost, HimawariSelectionLost, SliderSelectionLost,
                         WorldviewSelectionLost)
# The profile table's Status of a profile whose last attempt failed: LOST when its
# selection is no longer listed, UNAVAIL otherwise (network after all retries, a
# source without an image). Only the current state: the next success clears it,
# nothing is saved, and the profile is tried again as usual.
PROFILE_FAILURE_LOCK = threading.Lock()
PROFILE_FAILURES = {}


# Errors of a connection, not of the provider's answer (an HTTP error is an answer).
CONNECTION_ERRORS = (URLError, ConnectionError, TimeoutError, socket.gaierror, ssl.SSLError,
                     http.client.HTTPException)
INTERNET_CHECK_LOCK = threading.Lock()
INTERNET_CHECK = {"time": 0.0, "value": None}


def _exception_chain(exc):
    """The exception, its causes, and the reason inside a connection error."""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        reason = getattr(exc, "reason", None) if isinstance(exc, URLError) else None
        exc = exc.__cause__ or exc.__context__ or (reason if isinstance(reason, BaseException) else None)
    return chain


def internet_connected():
    """Whether Windows sees the internet (its own connectivity check), or None when unknown.

    Asks Windows' Network List Manager; MarbleScape sends nothing itself. A result
    serves 30 seconds.
    """
    if os.name != "nt":
        return None
    with INTERNET_CHECK_LOCK:
        if time.monotonic() - INTERNET_CHECK["time"] < 30:
            return INTERNET_CHECK["value"]

    def query():
        ole32 = ctypes.windll.ole32
        clsid = GUID.from_string("DCB00C01-570F-4A9B-8D69-199FDBA5723B")
        iid = GUID.from_string("DCB00000-570F-4A9B-8D69-199FDBA5723B")
        manager = ctypes.c_void_p()
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        check_hresult(ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x17, ctypes.byref(iid),
                                             ctypes.byref(manager)), "CoCreateInstance(INetworkListManager)")
        try:
            # INetworkListManager::get_IsConnectedToInternet, after IUnknown and IDispatch.
            connected = ctypes.c_short()
            get = get_com_method(manager, 11, ctypes.c_long, ctypes.POINTER(ctypes.c_short))
            check_hresult(get(manager, ctypes.byref(connected)), "INetworkListManager.IsConnectedToInternet")
            return connected.value != 0
        finally:
            release_com_pointer(manager)

    try:
        value = with_windows_com(query)
    except Exception:
        value = None
    with INTERNET_CHECK_LOCK:
        INTERNET_CHECK.update(time=time.monotonic(), value=value)
    return value


def profile_failure_state(exc):
    """The Status of a profile whose attempt failed with ``exc``.

    LOST: the provider no longer lists the selection. SOURCE: the provider failed
    (an HTTP error, or no connection to it while the internet works). NETWORK: no
    network or internet on this computer. UNAVAIL: anything else, for example a
    picture without image data.
    """
    chain = _exception_chain(exc)
    if any(isinstance(item, SELECTION_LOST_ERRORS) for item in chain):
        return "LOST"
    if any(isinstance(item, HTTPError) for item in chain):
        return "SOURCE"
    if any(isinstance(item, CONNECTION_ERRORS) for item in chain):
        online = internet_connected()
        if online is None:
            # Unknown (not Windows): a name that cannot be resolved points at this computer.
            return "NETWORK" if any(isinstance(item, socket.gaierror) for item in chain) else "SOURCE"
        return "SOURCE" if online else "NETWORK"
    return "UNAVAIL"


def note_profile_outcome(profile_id, exc=None):
    """Record a profile's last attempt: ``exc`` None for success, else its failure."""
    if not isinstance(profile_id, str) or not profile_id or profile_id == latest_snapshot.CACHE_ID:
        return
    with PROFILE_FAILURE_LOCK:
        if exc is None:
            PROFILE_FAILURES.pop(profile_id, None)
        else:
            PROFILE_FAILURES[profile_id] = profile_failure_state(exc)


# The latest image update's outcome, for the note below the Settings buttons.
IMAGE_OUTCOME_LOCK = threading.Lock()
IMAGE_OUTCOME = {"serial": 0, "kind": None, "profile": None, "detail": None, "time": None}
# Kind: (symbol and text, palette color or None for the normal text color). A
# "{detail}" names the sources; otherwise a profile name follows after a colon.
IMAGE_OUTCOME_TEXTS = {
    "downloaded": ("✓ Image downloaded", "success"),
    "cache": ("✓ Image restored from cache", "link"),
    "current": ("✓ Image up to date", "link"),
    "LOST": ("? Image source no longer listed", "warning"),
    "SOURCE": ("? Source unavailable", "warning"),
    "NETWORK": ("! Network issue", "warning"),
    "UNAVAIL": ("! Image unavailable", "warning"),
    "cancelled": ("× Download cancelled", None),
    # Refreshing all catalogues (daily, at startup, Refresh all catalogues).
    "catalogues_refreshed": ("✓ Catalogues refreshed", "success"),
    "catalogues_current": ("✓ Catalogues up to date", None),
    "catalogues_incomplete": ("? Catalogue refresh incomplete: {detail}", "warning"),
    "catalogues_network": ("! Catalogue refresh failed: network issue", "warning"),
    # One source's Refresh catalogue on the Image tab.
    "catalogue_refreshed": ("✓ Catalogue refreshed: {detail}", "success"),
    "catalogue_incomplete": ("? Catalogue incomplete: {detail}", "warning"),
    "catalogue_network": ("! Catalogue refresh failed: network issue ({detail})", "warning"),
    # The hourly storm check, only when the list changed.
    "storms_updated": ("✓ Active storms updated: {detail}", "link"),
}


def record_image_outcome(kind, profile_id=None, detail=None):
    """Note how an update ended: a kind of IMAGE_OUTCOME_TEXTS, for a profile or with a detail."""
    name = (image_profile_name(profile_id)
            if isinstance(profile_id, str) and profile_id != latest_snapshot.CACHE_ID else None)
    with IMAGE_OUTCOME_LOCK:
        IMAGE_OUTCOME.update(serial=IMAGE_OUTCOME["serial"] + 1, kind=kind, profile=name,
                             detail=detail, time=dt.datetime.now(dt.timezone.utc))


def record_catalogue_problem(kind_prefix, detail):
    """A failed catalogue refresh: a network issue on this computer, or incomplete sources."""
    record_image_outcome(f"{kind_prefix}_network" if internet_connected() is False
                         else f"{kind_prefix}_incomplete", detail=detail)


def image_outcome():
    with IMAGE_OUTCOME_LOCK:
        return dict(IMAGE_OUTCOME)


def image_outcome_notice(outcome, time_zone=None):
    """("✓ Image downloaded · 12:40", color) for the footer, or None: the status and the time.

    The profile is not named; catalogue notes name their catalogues.
    """
    if outcome.get("kind") not in IMAGE_OUTCOME_TEXTS:
        return None
    text, color = IMAGE_OUTCOME_TEXTS[outcome["kind"]]
    if "{detail}" in text:
        text = text.format(detail=outcome.get("detail") or "")
    when = outcome.get("time")
    if isinstance(when, dt.datetime):
        zone = DISPLAY_TIME_ZONE if time_zone is None else normalize_time_zone(time_zone)
        shown = when.astimezone(dt.timezone.utc) if zone == "utc" else when.astimezone()
        text += f" · {shown:%H:%M}" + (" UTC" if zone == "utc" else "")
    return text, color


def profile_failures():
    with PROFILE_FAILURE_LOCK:
        return dict(PROFILE_FAILURES)


class NoImageData(RuntimeError):
    """The rendered picture holds no image data at all (0% data coverage): nothing
    passed the date, Gap fill and cloud limit here. It is neither stored nor shown."""


NO_IMAGE_DATA_TEXT = ("No image data for this selection: no acquisition here passes its date, "
                      "Gap fill and cloud limit (0% data coverage). Try Latest available, "
                      "Gap fill or a higher Maximum cloud cover.")


def is_retryable_download_error(exc):
    """Retry transient transport failures, never invalid requests or bad certificates."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLCertVerificationError):
            return False
        if isinstance(exc, HTTPError):
            return exc.code in {408, 425, 429, 500, 502, 503, 504}
        if isinstance(exc, URLError):
            reason = getattr(exc, "reason", None)
            return not (isinstance(reason, ssl.SSLCertVerificationError)
                        or "certificate verify failed" in str(reason).casefold())
        if isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead,
                            http.client.RemoteDisconnected)):
            return True
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {
            10051, 10052, 10053, 10054, 10060, 10061,
        }:
            return True
        exc = exc.__cause__
    return False


def wait_before_download_retry(seconds):
    deadline = time.monotonic() + seconds
    while True:
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        if APPLICATION_STOP_EVENT.is_set() or CONFIGURATION_RELOAD_EVENT.is_set():
            raise RuntimeError(
                "Download retry stopped because the application is stopping or settings changed."
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        DOWNLOAD_PROGRESS.wait_or_raise(min(0.25, remaining))


def download_subject(cache_profile_id=None):
    """The profile a download belongs to, and whether the wallpaper already shows it."""
    if cache_profile_id == latest_snapshot.CACHE_ID:
        # A background download for the Latest snapshot row; Latest is not touched.
        return {"profile_id": latest_snapshot.SYSTEM_ID, "source": IMAGE_SOURCE, "updating": False}
    profile_id = cache_profile_id or unmodified_applied_profile_id()
    if not profile_id:
        return None
    current = get_current_image_path()
    shown = _history_profile_identity(current)[0] if current is not None else None
    return {"profile_id": profile_id, "source": IMAGE_SOURCE, "updating": shown == profile_id}


def profile_download_status():
    """Progress of a running profile download for the profile table's Status column."""
    snapshot = DOWNLOAD_PROGRESS.snapshot()
    if not snapshot["active"]:
        return None
    # A download without a profile belongs to the Latest snapshot row.
    subject = snapshot.get("subject") or {"profile_id": latest_snapshot.SYSTEM_ID}
    status = {key: snapshot[key] for key in
              ("percent", "transferred", "expected_requests", "finished_requests")}
    status.update(subject)
    return status


def perform_update(
    render_mode,
    requests,
    render_width,
    render_height,
    output_width,
    output_height,
    cache_profile_id=None,
    cache_configuration_signature=None,
    cache_source_signature=None,
    cache_source_time=None,
):
    """Track network transfer progress while rendering and installing an image."""
    expected_requests = expected_download_request_count(render_mode, requests)
    DOWNLOAD_PROGRESS.begin(expected_requests, download_subject(cache_profile_id))
    max_attempts = normalize_download_retries(DOWNLOAD_RETRIES) + 1
    for attempt in range(max_attempts):
        try:
            result = _perform_update(
                render_mode,
                requests,
                render_width,
                render_height,
                output_width,
                output_height,
                cache_profile_id=cache_profile_id,
                cache_configuration_signature=cache_configuration_signature,
                cache_source_signature=cache_source_signature,
                cache_source_time=cache_source_time,
            )
        except DownloadCancelledError:
            DOWNLOAD_PROGRESS.finish(False, cancelled=True)
            raise
        except UpdateSuperseded:
            DOWNLOAD_PROGRESS.finish(True)
            raise
        except Exception as exc:
            if (not DOWNLOAD_PROGRESS.snapshot()["cancellable"]
                    or not is_retryable_download_error(exc)):
                DOWNLOAD_PROGRESS.finish(False)
                raise
            if attempt + 1 >= max_attempts:
                DOWNLOAD_PROGRESS.finish(False)
                raise DownloadRetriesExhausted(
                    f"Image download failed after {max_attempts} attempts: {exc}"
                ) from exc
            log(f"Image transfer attempt {attempt + 1}/{max_attempts} failed; retrying: {exc}")
            try:
                wait_before_download_retry(min(10.0, 0.5 * (2 ** attempt)))
                DOWNLOAD_PROGRESS.restart_attempt(expected_requests)
            except DownloadCancelledError:
                DOWNLOAD_PROGRESS.finish(False, cancelled=True)
                raise
            except BaseException:
                DOWNLOAD_PROGRESS.finish(False)
                raise
        except BaseException:
            DOWNLOAD_PROGRESS.finish(False)
            raise
        else:
            DOWNLOAD_PROGRESS.finish(True)
            return result


def queue_profile_refreshes(profile_ids, check_only=False):
    """Queue saved profiles for a background download into their cache.

    ``check_only`` loads a picture only when the provider lists a newer one.
    A forced download replaces a queued check of the same profile. Returns the
    newly queued IDs.
    """
    profile_ids = list(dict.fromkeys(profile_ids))
    with PROFILE_REFRESH_LOCK:
        added = [identifier for identifier in profile_ids
                 if identifier not in PROFILE_REFRESH_QUEUE]
        PROFILE_REFRESH_QUEUE.extend(added)
        if check_only:
            PROFILE_CHECK_ONLY.update(added)
        else:
            PROFILE_CHECK_ONLY.difference_update(profile_ids)
    if added:
        log(f"Queued {len(added)} profile(s) "
            + ("to check for a new picture." if check_only else "for a new picture."))
    return added


def profile_refresh_is_check(identifier):
    with PROFILE_REFRESH_LOCK:
        return identifier in PROFILE_CHECK_ONLY


def checking_profile_id():
    """The saved profile whose provider is being asked for a newer picture."""
    with PROFILE_REFRESH_LOCK:
        return PROFILE_CHECKING_ID


def set_checking_profile_id(identifier):
    global PROFILE_CHECKING_ID
    with PROFILE_REFRESH_LOCK:
        PROFILE_CHECKING_ID = identifier


def profile_check_summary():
    """Return (serial, text) of the last finished "Check for new image" batch."""
    with PROFILE_REFRESH_LOCK:
        return PROFILE_CHECK_SUMMARY["serial"], PROFILE_CHECK_SUMMARY["text"]


def _profile_check_summary_text(counts, cancelled=False):
    checked = sum(counts.values())
    parts = [f"{counts['new']} new picture(s)", f"{counts['unchanged']} up to date"]
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped (image updates off)")
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed")
    prefix = "Check cancelled after" if cancelled else "Checked"
    return f"{prefix} {checked} profile(s): " + ", ".join(parts) + "."


def _finish_profile_check_batch(cancelled=False):
    """Publish the batch summary once no check is left; call with the lock held."""
    if PROFILE_CHECK_ONLY and not cancelled:
        return None
    if not any(PROFILE_CHECK_COUNTS.values()) and not cancelled:
        return None
    text = _profile_check_summary_text(PROFILE_CHECK_COUNTS, cancelled)
    PROFILE_CHECK_SUMMARY["serial"] += 1
    PROFILE_CHECK_SUMMARY["text"] = text
    for key in PROFILE_CHECK_COUNTS:
        PROFILE_CHECK_COUNTS[key] = 0
    return text


def _report_profile_check_summary(text):
    if text is None:
        return
    log(text)


def queued_profile_refreshes():
    with PROFILE_REFRESH_LOCK:
        return tuple(PROFILE_REFRESH_QUEUE)


def clear_profile_refresh_queue():
    with PROFILE_REFRESH_LOCK:
        cleared = len(PROFILE_REFRESH_QUEUE)
        PROFILE_REFRESH_QUEUE.clear()
        summary = None
        if PROFILE_CHECK_ONLY or any(PROFILE_CHECK_COUNTS.values()):
            PROFILE_CHECK_ONLY.clear()
            summary = _finish_profile_check_batch(cancelled=True)
    if cleared:
        log(f"Cleared {cleared} queued profile download(s).")
    _report_profile_check_summary(summary)
    return cleared


def next_profile_refresh():
    """Return the next queued saved profile, skipping IDs no longer in the library.

    The Latest snapshot row is returned with its snapshot's settings.
    """
    while True:
        with PROFILE_REFRESH_LOCK:
            if not PROFILE_REFRESH_QUEUE:
                return None
            identifier = PROFILE_REFRESH_QUEUE[0]
        if identifier == latest_snapshot.SYSTEM_ID:
            try:
                snapshot = read_latest_snapshot()
            except (OSError, ValueError) as exc:
                log(f"Latest snapshot unavailable for a background download: {exc}")
                snapshot = None
            profile = None if snapshot is None else {
                "id": latest_snapshot.SYSTEM_ID, "name": latest_snapshot.SYSTEM_NAME,
                "settings": deepcopy(snapshot["settings"]),
            }
        else:
            profile = next((item for item in IMAGE_PROFILE_LIBRARY.get("items", [])
                            if item.get("id") == identifier), None)
        if profile is not None:
            return profile
        summary = None
        with PROFILE_REFRESH_LOCK:
            if PROFILE_REFRESH_QUEUE and PROFILE_REFRESH_QUEUE[0] == identifier:
                PROFILE_REFRESH_QUEUE.pop(0)
                if identifier in PROFILE_CHECK_ONLY:
                    # A deleted profile is not counted.
                    PROFILE_CHECK_ONLY.discard(identifier)
                    summary = _finish_profile_check_batch()
        _report_profile_check_summary(summary)


def finish_profile_refresh(identifier, outcome=None):
    """Remove a finished profile; ``outcome`` counts a check (new, unchanged, skipped, failed)."""
    summary = None
    with PROFILE_REFRESH_LOCK:
        if PROFILE_REFRESH_QUEUE and PROFILE_REFRESH_QUEUE[0] == identifier:
            PROFILE_REFRESH_QUEUE.pop(0)
            if identifier in PROFILE_CHECK_ONLY:
                PROFILE_CHECK_ONLY.discard(identifier)
                if outcome in PROFILE_CHECK_COUNTS:
                    PROFILE_CHECK_COUNTS[outcome] += 1
                summary = _finish_profile_check_batch()
    _report_profile_check_summary(summary)


FRAME_RENDER_MODES = {
    "noaa": (get_noaa_frame, noaa_frame_signature),
    "himawari": (get_himawari_frame, himawari_frame_signature),
    "slider": (get_slider_frame, slider_frame_signature),
    "worldview": (get_worldview_frame, worldview_frame_signature),
    "copernicus": (get_copernicus_frame, copernicus_frame_signature),
}


def _parse_source_time(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.timezone.utc)


def profile_cache_is_current(profile, configuration_signature, source_signature,
                             output_size, source_time):
    """True when the profile's cached picture is as new as the provider's latest.

    An identical source is current; otherwise the stored acquisition time must
    not be older than the listed one, so a provider that lists an older image
    never replaces a newer cached picture.
    """
    cache = get_profile_cache()
    if cache.lookup(profile["id"], configuration_signature, source_signature, output_size,
                    source_time=source_time) is not None:
        return True
    stored = _parse_source_time(cache.stored_source_time(
        profile["id"], configuration_signature, output_size))
    listed = _parse_source_time(source_time)
    if stored is None or listed is None or listed > stored:
        return False
    if listed < stored:
        log(f"The provider lists an older picture for profile {profile['name']}; keeping the cached one.")
    return True


def refresh_profile_cache(profile, check_only=False):
    """Download a new picture of a saved profile into its cache without publishing it.

    Runs in the image worker only. The profile's settings are applied for the
    download and the previous runtime configuration is always restored, so the
    wallpaper, Latest and rotation stay as they are. Like a rotation step, the
    replaced cache image is archived when the profile's History is on.

    ``check_only`` asks the provider for its latest picture first and downloads
    only when it is newer than the cached one; profiles with image updates off
    are skipped. Returns (outcome, path): "new" with the stored picture, or
    "unchanged" or "skipped" without one.

    The Latest snapshot row loads its settings into its own cache slot, as an
    image without a profile.
    """
    global APPLIED_PROFILE_ID
    previous = capture_loaded_configuration()
    snapshot_row = profile["id"] == latest_snapshot.SYSTEM_ID
    cache_id = latest_snapshot.CACHE_ID if snapshot_row else profile["id"]
    try:
        apply_image_settings(profile["settings"])
        if snapshot_row:
            # No applied profile may claim the image; restored with the configuration.
            APPLIED_PROFILE_ID = ""
        if check_only and not CHECK_FOR_SOURCE_UPDATES:
            return "skipped", None
        if check_only:
            set_checking_profile_id(profile["id"])
        layers = parse_layers(download_capabilities()) if IMAGE_SOURCE == "eumetsat" else {}
        (output_width, output_height, render_width, render_height, _scale, _bbox, _projection_name,
         _projection, _layers, render_mode, requests, _refresh, source_signature
         ) = prepare_runtime_render_plan(layers)
        if render_mode in FRAME_RENDER_MODES:
            get_frame, frame_signature = FRAME_RENDER_MODES[render_mode]
            frame = get_frame((output_width, output_height))
            requests, source_signature = [{"frame": frame}], frame_signature(frame)
        configuration_signature = image_cache_configuration_key(capture_loaded_configuration())
        if not snapshot_row:
            configuration_signature.update(
                image_metadata_version=IMAGE_METADATA_VERSION,
                image_profile_id=profile["id"],
                image_profile_name=image_profile_name(profile["id"]),
            )
        source_time = profile_cache_source_time(render_mode, requests, source_signature)
        if check_only:
            if profile_cache_is_current(dict(profile, id=cache_id), configuration_signature, source_signature,
                                        (output_width, output_height), source_time):
                return "unchanged", None
            set_checking_profile_id(None)
        _installed_path, current_path, _size = perform_update(
            render_mode, requests, render_width, render_height, output_width, output_height,
            cache_profile_id=cache_id,
            cache_configuration_signature=configuration_signature,
            cache_source_signature=source_signature,
            cache_source_time=source_time,
        )
        return "new", current_path
    finally:
        if check_only:
            set_checking_profile_id(None)
        restore_loaded_configuration(previous)


def run_queued_profile_refresh():
    """Download the first queued profile; return False when the queue is empty.

    A failed profile is logged and skipped. Cancelling the download clears the
    whole queue. A configuration change during the download keeps its picture;
    one before it keeps the profile queued for a retry.
    """
    profile = next_profile_refresh()
    if profile is None:
        return False
    check_only = profile_refresh_is_check(profile["id"])
    log(f"Checking profile {profile['name']} for a new picture in the background..." if check_only
        else f"Loading a new picture of profile {profile['name']} in the background...")
    try:
        outcome, path = refresh_profile_cache(profile, check_only=check_only)
    except DownloadCancelledError:
        log(f"Background download of {profile['name']} cancelled.")
        clear_profile_refresh_queue()
        return True
    except UpdateSuperseded as exc:
        # Finished despite a settings change; the picture is in its cache.
        log(str(exc))
        outcome = "new"
    except Exception as exc:
        if CONFIGURATION_RELOAD_EVENT.is_set() and not APPLICATION_STOP_EVENT.is_set():
            log(f"Background download of {profile['name']} interrupted by a settings change; retrying.")
            return True
        log(f"Background download of {profile['name']} failed: {exc}")
        note_profile_outcome(profile["id"], exc)
        record_image_outcome(profile_failure_state(exc), profile["id"])
        outcome = "failed"
    else:
        if outcome != "skipped":
            note_profile_outcome(profile["id"])
        if outcome == "new":
            record_image_outcome("downloaded", profile["id"])
            log(f"New picture of profile {profile['name']} stored: {path}")
        elif outcome == "skipped":
            log(f"Profile {profile['name']} has image updates off; not checked.")
        else:
            log(f"No newer picture is available for profile {profile['name']}.")
    finish_profile_refresh(profile["id"], outcome)
    return True


# The next rotation profile is checked this long before its switch (half the
# rotation interval when that is shorter), so its picture is ready and current.
ROTATION_PRELOAD_LEAD_SECONDS = 60.0


def rotation_preload_lead(rotation):
    return min(ROTATION_PRELOAD_LEAD_SECONDS, rotation.interval_seconds / 2)


def rotation_preload_step(rotation, done_step):
    """Return (profile, step, start time) of the next rotation step to preload, or None.

    ``step`` identifies the switch (deadline and profile) so each one is
    preloaded once. The profile the wallpaper shows needs no preload.
    """
    upcoming = rotation.upcoming() if rotation.preload_next else None
    if upcoming is None:
        return None
    profile, deadline = upcoming
    step = (deadline, profile["id"])
    if step == done_step:
        return None
    current = get_current_image_path()
    if current is not None and _history_profile_identity(current)[0] == profile["id"]:
        return None
    return profile, step, deadline - rotation_preload_lead(rotation)


def set_rotation_preload_status(deadline, name, outcome):
    """Record the preload of the rotation step due at ``deadline`` for the status line."""
    with ROTATION_STATUS_LOCK:
        ROTATION_STATUS["preload"] = {
            "deadline": deadline, "name": name, "outcome": outcome,
            "checked_at": dt.datetime.now(dt.timezone.utc),
        }


ROTATION_PRELOAD_PHRASES = {
    "checking": "checking...",
    "new": "new picture loaded",
    "unchanged": "already current",
    "skipped": "not checked (image updates off)",
    "failed": "check failed, loads at the switch",
}


def rotation_preload_result(state, time_zone):
    """The preload result of the next rotation step for Now showing, or "".

    For example "already current (12:35:30 UTC)" or "checking..."; it follows
    the next profile's name, so it does not repeat it.
    """
    preload = state.get("preload")
    if not preload or preload.get("deadline") != state.get("deadline"):
        return ""  # Belongs to an earlier step.
    result = ROTATION_PRELOAD_PHRASES.get(preload["outcome"], preload["outcome"])
    if preload["outcome"] != "checking":
        stamp = format_display_datetime(preload["checked_at"], time_zone, include_seconds=True)
        result += f" ({stamp.split(' ', 1)[1]})"
    return result


def run_rotation_preload(profile, deadline=None):
    """Load the next rotation profile's picture into its cache if the provider has a newer one.

    Runs in the image worker before the switch; a failure only means the switch
    loads the picture as usual. The result stays in the rotation status line
    until the switch.
    """
    log(f"Checking the next rotation profile {profile['name']} for a new picture...")
    set_rotation_preload_status(deadline, profile["name"], "checking")
    try:
        outcome, path = refresh_profile_cache(profile, check_only=True)
    except DownloadCancelledError:
        log(f"Preloading {profile['name']} cancelled.")
        set_rotation_preload_status(deadline, profile["name"], "failed")
        return
    except UpdateSuperseded as exc:
        log(str(exc))
        outcome, path = "new", None
        superseded = True
    except Exception as exc:
        log(f"Preloading {profile['name']} failed; the switch loads it instead: {exc}")
        set_rotation_preload_status(deadline, profile["name"], "failed")
        note_profile_outcome(profile["id"], exc)
        return
    else:
        superseded = False
    set_rotation_preload_status(deadline, profile["name"], outcome)
    if outcome != "skipped" and not superseded:
        note_profile_outcome(profile["id"])
    if outcome == "new" and path is not None:
        log(f"Next rotation picture of {profile['name']} is ready: {path}")
    elif outcome == "unchanged":
        log(f"The cached picture of {profile['name']} is current.")


def report_update_status(status_callback, state, next_check=None):
    if status_callback is None:
        return
    try:
        status_callback(state, next_check)
    except Exception as exc:
        log(f"Tray status update warning: {exc}")


def sleep_until_next_cycle(cycle_started_monotonic, status_callback=None, wake_for_queue=False):
    interval_seconds = UPDATE_INTERVAL_MINUTES * 60.0
    elapsed = time.monotonic() - cycle_started_monotonic
    remaining = max(0.0, interval_seconds - elapsed)
    if NEXT_ROTATION_DEADLINE is not None:
        remaining = min(remaining, max(0.0, NEXT_ROTATION_DEADLINE - time.monotonic()))

    next_check = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=remaining)
    report_update_status(status_callback, "waiting", next_check)
    log(f"Next check: {format_display_datetime(next_check, DISPLAY_TIME_ZONE, include_seconds=True)}.")
    deadline = time.monotonic() + remaining
    while not APPLICATION_STOP_EVENT.is_set():
        remaining = deadline - time.monotonic()
        if (FORCE_UPDATE_EVENT.is_set() or CHECK_NOW_EVENT.is_set()
                or CONFIGURATION_RELOAD_EVENT.is_set() or remaining <= 0
                or (wake_for_queue and queued_profile_refreshes())):
            return True
        APPLICATION_STOP_EVENT.wait(min(remaining, 0.25))
    return False


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description=("Download EUMETSAT, NOAA GOES/Solar, Himawari, CIRA SLIDER, "
                     "Copernicus, and NASA Worldview wallpaper images.")
    )
    parser.add_argument("--version", action="version", version=f"MarbleScape {VERSION}")
    parser.add_argument("--background", action="store_true", help="Start silently in the Windows tray (autostart).")
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
        help="List layers or products for the selected source, then exit.",
    )
    parser.add_argument(
        "--export-layers",
        type=Path,
        metavar="FILE",
        help="Export layers or products for the selected source as JSON, then exit.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration against the selected live source, then exit.",
    )
    parser.add_argument(
        "--print-urls",
        action="store_true",
        help="Print the effective endpoint or image URLs for the selected source, then exit.",
    )
    return parser.parse_args(argv)


def prepare_runtime_render_plan(layers):
    """Build a complete render plan from the currently loaded settings.

    Every source renders at get_image_dimensions(): the output enlarged for the
    displays (displays_image_size), never the plain shared output.
    """
    if IMAGE_SOURCE in {"goes_east", "goes_west", "solar"}:
        width, height = get_image_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "noaa", [], True, None)
    if IMAGE_SOURCE == "himawari":
        width, height = get_image_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "himawari", [], True, None)
    if IMAGE_SOURCE == "slider":
        width, height = get_image_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "slider", [], True, None)
    if IMAGE_SOURCE == "worldview":
        width, height = get_image_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "worldview", [], True, None)
    if IMAGE_SOURCE == "copernicus":
        width, height = get_image_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "copernicus", [], True, None)
    target_ratio = parse_aspect_ratio(ASPECT_RATIO)
    output_width, output_height = get_image_dimensions()
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


def image_configuration_key(configuration):
    """Compare settings that change the downloaded/rendered image, not Windows placement."""
    source = configuration["IMAGE_SOURCE"]
    common = ["WIDTH", "HEIGHT", "ASPECT_RATIO", "CUSTOM_LATEST_FOLDER",
              "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX"]
    if source != "copernicus":
        common += ["BACKGROUND_COLOR", "VIEW_MODE", "ZOOM"]
    key = {name: configuration[name] for name in common}
    key["applied_profile_id"] = configuration.get("APPLIED_PROFILE_ID", "")
    key["source"] = source
    if source == "eumetsat":
        for name in ("WMS_URL", "WMS_VERSION", "IMAGE_TIME", "RENDER_MODE", "VIEW_PRESET",
                     "TRUECOLOR_BLACK_NIGHT", "LAYER_CONFIG"):
            key[name] = configuration[name]
        # The render quality actually used, also when it is General's: the same
        # keys and values as before an inherited value existed.
        scale = effective_render_scale_setting(configuration)
        key["RENDER_SCALE"] = 1.0 if scale == "auto" else float(scale)
        key["RENDER_SCALE_AUTOMATIC"] = scale == "auto"
        preset = VIEW_PRESETS[configuration["VIEW_PRESET"]]
        key["projection"] = preset["projection"] or configuration["PROJECTION"]
        key["bbox"] = configuration["CUSTOM_BBOX"] if configuration["VIEW_PRESET"] == "custom" else None
        profile = configuration["SOURCE_PROFILES"]["eumetsat"]
        key["gap_fill"] = {
            "enabled": profile.get("fill_gaps", False),
            "lookback_hours": profile.get("gap_fill_lookback_hours", 12),
        }
    else:
        key["selection"] = configuration["SOURCE_PROFILES"][source]
        if source == "copernicus":
            # The color of a switched-off overlay draws nothing and does not count.
            overlay = map_overlay_signature(key["selection"])
            key["selection"] = dict(key["selection"], map_label_color=overlay[1])
            if len(overlay) == 4:
                key["selection"]["map_border_color"] = overlay[3]
        if source == "copernicus" and len(map_overlay_signature(key["selection"])) == 2:
            # Borders drawn like the labels render as before they were separate.
            key["selection"] = {name: value for name, value in key["selection"].items()
                                if name not in {"map_borders", "map_border_color"}}
        if source == "copernicus" and not key["selection"].get("auto_recommendation", False):
            # Without the rule the picture is the same as before it existed.
            key["selection"] = {name: value for name, value in key["selection"].items()
                                if name not in {"auto_recommendation", "auto_priority", "auto_precise"}}
        if source == "copernicus" and key["selection"].get("scene_no_data_color") == "transparent":
            # The map background shows as before regular layers had a No-data color.
            key["selection"] = {name: value for name, value in key["selection"].items()
                                if name != "scene_no_data_color"}
        if source == "himawari":
            # Settings that draw nothing keep the key of pictures made before they existed.
            selection = dict(key["selection"])
            if not selection.pop("shorelines", False):
                selection.pop("shoreline_color", None)
            if not selection.pop("center", False):
                selection.pop("latitude", None)
                selection.pop("longitude", None)
            else:
                selection["center"] = True
            if key["selection"].get("shorelines"):
                selection["shorelines"] = True
            key["selection"] = selection
        if source != "copernicus" and configuration.get("SET_WINDOWS_WALLPAPER"):
            # The picture is enlarged for the displays (displays_image_size).
            key["monitor_image_targets"] = configuration.get("WINDOWS_WALLPAPER_MONITOR_OUTPUTS", {})
        if source == "copernicus":
            if key["selection"].get("image_size", "auto") == "auto":
                key["monitor_image_targets"] = configuration.get("WINDOWS_WALLPAPER_MONITOR_OUTPUTS", {})
            key["copernicus_client_id"] = configuration["COPERNICUS_CLIENT_ID"]
            key["copernicus_secret_fingerprint"] = hashlib.sha256(
                configuration["COPERNICUS_CLIENT_SECRET"].encode("utf-8")
            ).hexdigest()
    return deepcopy(key)


def image_cache_configuration_key(configuration):
    """Return only render-affecting values for persistent profile reuse."""
    key = image_configuration_key(configuration)
    # The pictured profile is image_profile_id; the last manually applied one
    # must not matter, so rotation and manual apply share cache entries. It
    # pictures the applied profile only while its settings are unmodified;
    # modified settings picture no profile (the Latest snapshot), whichever
    # profile was applied before.
    key.pop("applied_profile_id", None)
    identifier = unmodified_applied_profile_id(configuration) or ""
    key.update(image_metadata_version=IMAGE_METADATA_VERSION,
               image_profile_id=identifier, image_profile_name=image_profile_name(identifier))
    for name in (
        "CUSTOM_LATEST_FOLDER", "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX"
    ):
        key.pop(name, None)
    return key


def main(argv=None, configuration_loaded=False, status_callback=None):
    global RUN_CONTINUOUSLY, NEXT_ROTATION_DEADLINE

    args = parse_arguments(argv)
    if not configuration_loaded:
        load_configuration(args.config)
    if args.once:
        RUN_CONTINUOUSLY = False

    validate_configuration()
    if get_current_image_path() is None:
        existing_latest = get_latest_image_files()
        set_current_image_path(existing_latest[0] if existing_latest else None)
    diagnostic = (args.list_layers is not None or args.export_layers is not None
                  or args.print_urls or args.validate_config)
    if not diagnostic:
        synchronize_profile_image_cache()
    rotation = RotationScheduler(IMAGE_PROFILE_LIBRARY if RUN_CONTINUOUSLY and not diagnostic else {})
    saved_rotation_state = load_rotation_position()
    resumed_profile = None
    if rotation.restore_position(saved_rotation_state):
        # Keep last rotation position also keeps the time of the last switch:
        # the image shown before the restart stays until its interval ends.
        elapsed = seconds_since_rotation_switch(saved_rotation_state)
        if elapsed is not None:
            rotation.resume_interval(elapsed)
            if not rotation.due():
                resumed_profile = resume_rotation_profile(saved_rotation_state)
    elif not IMAGE_PROFILE_LIBRARY.get("rotation", {}).get("keep_last_position", False):
        save_rotation_position({})
    rotation.set_max_attempts(DOWNLOAD_RETRIES + 1)
    rotating_at_start = rotation.due()
    pending_profile = None
    profile_previous = None
    profile_previous_image = None
    active_profile_id = None
    configuration_before_plan = None
    needs_plan = rotating_at_start
    NEXT_ROTATION_DEADLINE = None
    # The rotation step whose next picture was already preloaded.
    preloaded_rotation_step = None
    if resumed_profile is not None:
        active_profile_id = resumed_profile["id"]
        set_rotation_status(f"Active profile: {resumed_profile['name']}", rotation.deadline,
                            active_profile_id=active_profile_id)
    else:
        set_rotation_status(rotation_status_text(rotation, starting=rotating_at_start),
                            None if rotating_at_start else rotation.deadline, active_profile_id=None)
    report_update_status(status_callback, "checking")

    if IMAGE_SOURCE == "copernicus" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_copernicus_diagnostics(args)
        return

    if IMAGE_SOURCE in {"goes_east", "goes_west", "solar"} and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_noaa_diagnostics(args)
        return

    if IMAGE_SOURCE == "himawari" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_himawari_diagnostics(args)
        return

    if IMAGE_SOURCE == "slider" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_slider_diagnostics(args)
        return

    if IMAGE_SOURCE == "worldview" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_worldview_diagnostics(args)
        return

    layers = {}
    initial_saved_image = None
    if (
        not diagnostic and not rotating_at_start
        and not CHECK_FOR_SOURCE_UPDATES and not FORCE_UPDATE_EVENT.is_set()
    ):
        initial_output_size = get_image_dimensions()
        initial_saved_image = reusable_latest_image(
            image_cache_configuration_key(capture_loaded_configuration()),
            initial_output_size,
        )
    if IMAGE_SOURCE == "eumetsat" and not rotating_at_start and initial_saved_image is None:
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
    ) = (
        (*initial_output_size, *initial_output_size, 1.0, None, "", None, [],
         "saved", [], False, ())
        if initial_saved_image is not None else
        prepare_runtime_render_plan(layers) if not rotating_at_start else
        (0, 0, 0, 0, 1.0, None, "", None, [], "noaa", [], True, None)
    )

    if not rotating_at_start and initial_saved_image is None:
        print_configuration(output_width, output_height, render_width, render_height,
                            effective_render_scale, bbox, projection_name, projection,
                            resolved_layers, render_mode)
    elif initial_saved_image is not None:
        log(
            "Image update checks are disabled; a verified matching local image "
            "is available."
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

    if not rotating_at_start:
        ensure_directories()

    storage_estimate_printed = False
    wallpaper_applied_path = None
    latest_published_path = None
    wallpaper_position_pending = False
    first_cycle = True
    last_image_cycle_started = None
    defer_image_check_until = None
    # An image requested by a settings reload (Apply Image) that no cycle has
    # finished yet; a later settings-only reload must not postpone it.
    image_load_pending = False
    # Save on the Image tab changed the picture's settings but postponed the
    # picture to the next regular check; Apply Image then loads it at once.
    saved_image_waiting = False
    # A download that a reload superseded kept its picture in the cache; the
    # cycle after the reload shows it at once if it still applies.
    superseded_download = False
    while True:
        if APPLICATION_STOP_EVENT.is_set():
            break

        if CONFIGURATION_RELOAD_EVENT.is_set():
            settings_only_reload = SETTINGS_ONLY_RELOAD_EVENT.is_set()
            CONFIGURATION_RELOAD_EVENT.clear()
            SETTINGS_ONLY_RELOAD_EVENT.clear()
            previous_configuration = capture_loaded_configuration()
            try:
                load_configuration(args.config)
                validate_configuration()
                save_working_settings_backup_safely()
                rotation.set_max_attempts(DOWNLOAD_RETRIES + 1)
                synchronize_profile_image_cache()
                previous_rotation_position = rotation.position_state()
                rotation_changed = rotation.configure(IMAGE_PROFILE_LIBRARY if RUN_CONTINUOUSLY else {})
                # Save on other tabs keeps showing the rotation profile: the file
                # only supplies the device settings around its Image settings.
                shown_profile = next((item for item in IMAGE_PROFILE_LIBRARY.get("items", [])
                                      if item["id"] == active_profile_id), None)
                if (settings_only_reload and not rotation_changed
                        and pending_profile is None and shown_profile is not None):
                    try:
                        apply_image_settings(shown_profile["settings"])
                    except Exception as exc:
                        log(f"Unable to keep showing profile {shown_profile['name']}: {exc}")
                        shown_profile = None
                else:
                    shown_profile = None
                image_changed = image_configuration_key(previous_configuration) != image_configuration_key(capture_loaded_configuration())
                if saved_image_waiting and not settings_only_reload:
                    # Apply Image after Save: the saved settings are not on screen yet.
                    image_changed = True
                if image_changed and not settings_only_reload:
                    image_load_pending = True
                # A new output size or background applies to the shown image now.
                device_output_changed = any(
                    previous_configuration[name] != globals()[name]
                    for name in ("WIDTH", "HEIGHT", "ASPECT_RATIO", "BACKGROUND_COLOR"))
                if rotation_changed:
                    rotation.restore_position(previous_rotation_position or load_rotation_position())
                    if not IMAGE_PROFILE_LIBRARY.get("rotation", {}).get("keep_last_position", False):
                        save_rotation_position({})
                    pending_profile = None
                    profile_previous = None
                    profile_previous_image = None
                    active_profile_id = None
                    set_rotation_status("Starting rotation..." if rotation.due() else "Rotation is disabled.",
                                        active_profile_id=None)
                    if settings_only_reload:
                        rotation.defer()
                        # Waiting for the interval, or disabled when switched off.
                        set_rotation_status(rotation_status_text(rotation), rotation.deadline,
                                            active_profile_id=None)
                if image_changed and not settings_only_reload and not rotation_changed:
                    # A manually applied image stays for a full rotation interval;
                    # an applied rotation profile moves the rotation after it.
                    # A changed rotation list restarts the rotation instead.
                    if pending_profile is not None:
                        rotation.cancel()
                        pending_profile = None
                        profile_previous = None
                        profile_previous_image = None
                    rotation.manual_switch(APPLIED_PROFILE_ID or None)
                    save_rotation_progress(rotation)
                    active_profile_id = None
                    set_rotation_status(rotation_status_text(rotation), rotation.deadline,
                                        active_profile_id=None)
                elif image_changed and not rotation_changed and shown_profile is None and not image_load_pending:
                    active_profile_id = None
                    set_rotation_status(rotation_status_text(rotation),
                                        rotation.deadline, active_profile_id=None)
                if image_changed:
                    configuration_before_plan = previous_configuration
                    needs_plan = True
                    storage_estimate_printed = False
                    wallpaper_applied_path = None
                    first_cycle = True
                    defer_image_check_until = (
                        time.monotonic() + UPDATE_INTERVAL_MINUTES * 60.0
                        if settings_only_reload and not device_output_changed and not image_load_pending
                        else None
                    )
                    saved_image_waiting = defer_image_check_until is not None
                elif image_load_pending:
                    # A reload discarded the requested image; load it now.
                    defer_image_check_until = None
                elif last_image_cycle_started is not None:
                    # Wake to apply local settings, then resume the existing
                    # download/retry schedule without checking any image URLs.
                    defer_image_check_until = (
                        NEXT_ROTATION_DEADLINE if pending_profile is not None and NEXT_ROTATION_DEADLINE is not None
                        else last_image_cycle_started + UPDATE_INTERVAL_MINUTES * 60.0
                    )
                wallpaper_position_pending = (
                    SET_WINDOWS_WALLPAPER and (
                        WINDOWS_WALLPAPER_POSITION != previous_configuration["WINDOWS_WALLPAPER_POSITION"]
                        or WINDOWS_WALLPAPER_MONITOR_POSITIONS != previous_configuration[
                            "WINDOWS_WALLPAPER_MONITOR_POSITIONS"]
                        or WINDOWS_WALLPAPER_PAUSED != previous_configuration[
                            "WINDOWS_WALLPAPER_PAUSED"]
                        or WINDOWS_WALLPAPER_MONITOR_OUTPUTS != previous_configuration[
                            "WINDOWS_WALLPAPER_MONITOR_OUTPUTS"]
                        # Display margins and the Windows desktop color.
                        or BACKGROUND_COLOR != previous_configuration["BACKGROUND_COLOR"]
                        or not previous_configuration["SET_WINDOWS_WALLPAPER"]
                    )
                )
                if superseded_download:
                    # The kept picture may be the one to show now.
                    defer_image_check_until = None
                    superseded_download = False
                log("Applied configuration without restarting MarbleScape.")
                if image_changed:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
            except Exception as exc:
                restore_loaded_configuration(previous_configuration)
                rotation.set_max_attempts(DOWNLOAD_RETRIES + 1)
                log(f"Unable to apply configuration: {exc}")

        # Name the next profile right away, also while this cycle downloads.
        publish_next_rotation_profile(rotation)

        # Shortly before a rotation switch the next profile's picture is checked
        # and, when newer, loaded into its cache, so the switch shows it at once.
        # An applied image (Apply Image, Apply of a profile or the Latest
        # snapshot) loads first; a background download already running finishes.
        preload = (rotation_preload_step(rotation, preloaded_rotation_step)
                   if RUN_CONTINUOUSLY and pending_profile is None and not image_load_pending
                   else None)
        if (preload is not None and time.monotonic() >= preload[2]
                and not FORCE_UPDATE_EVENT.is_set() and not CHECK_NOW_EVENT.is_set()
                and not rotation.due()):
            preloaded_rotation_step = preload[1]
            report_update_status(status_callback, "fetching")
            run_rotation_preload(preload[0], preload[1][0])
            if (defer_image_check_until is None and last_image_cycle_started is not None
                    and not image_load_pending):
                # Resume the regular schedule instead of checking at once.
                defer_image_check_until = last_image_cycle_started + UPDATE_INTERVAL_MINUTES * 60.0
            continue

        # Queued profile pictures load between regular cycles; a due rotation
        # step, a forced update of the shown image or an applied image goes first.
        if (pending_profile is None and not image_load_pending
                and not FORCE_UPDATE_EVENT.is_set()
                and not CHECK_NOW_EVENT.is_set()
                and not rotation.due() and queued_profile_refreshes()):
            report_update_status(status_callback, "fetching")
            if run_queued_profile_refresh():
                if (defer_image_check_until is None and last_image_cycle_started is not None
                        and not image_load_pending):
                    # Resume the regular schedule instead of checking at once.
                    defer_image_check_until = last_image_cycle_started + UPDATE_INTERVAL_MINUTES * 60.0
                continue

        cycle_started = time.monotonic()
        force_download = FORCE_UPDATE_EVENT.is_set()
        FORCE_UPDATE_EVENT.clear()
        # "Check for new image" runs the regular check now, without forcing.
        check_now = CHECK_NOW_EVENT.is_set()
        CHECK_NOW_EVENT.clear()
        cycle_next_check = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            minutes=UPDATE_INTERVAL_MINUTES
        )
        report_update_status(status_callback, "checking", cycle_next_check)

        # Windows placement is independent of downloading a replacement image.
        # A source outage must not prevent positioning the existing wallpaper.
        if wallpaper_position_pending and os.name == "nt" and SET_WINDOWS_WALLPAPER:
            try:
                current_path = get_current_image_path()
                if current_path is None:
                    current_images = get_latest_image_files()
                    current_path = current_images[0] if current_images else None
                if current_path is not None:
                    set_windows_wallpaper(current_path)
                    wallpaper_applied_path = current_path
                    wallpaper_position_pending = False
            except Exception as exc:
                log(f"Windows wallpaper position warning: {exc}")

        if defer_image_check_until is not None:
            if (not force_download and not check_now and not rotation.due()
                    and time.monotonic() < defer_image_check_until):
                NEXT_ROTATION_DEADLINE = defer_image_check_until
                if pending_profile is None and rotation.deadline is not None:
                    NEXT_ROTATION_DEADLINE = min(NEXT_ROTATION_DEADLINE, rotation.deadline)
                preload = rotation_preload_step(rotation, preloaded_rotation_step)
                if pending_profile is None and preload is not None:
                    NEXT_ROTATION_DEADLINE = min(NEXT_ROTATION_DEADLINE, preload[2])
                if wallpaper_position_pending:
                    NEXT_ROTATION_DEADLINE = min(NEXT_ROTATION_DEADLINE, time.monotonic() + 5)
                if not sleep_until_next_cycle(last_image_cycle_started, status_callback,
                                              wake_for_queue=pending_profile is None):
                    break
                continue
            defer_image_check_until = None
        # This cycle checks the image: a picture Save postponed is loaded now.
        saved_image_waiting = False

        last_image_cycle_started = cycle_started

        if pending_profile is None and rotation.due():
            pending_profile = rotation.start_next()
            profile_previous = image_settings_snapshot()
            profile_previous_image = (
                get_current_image_path(), get_active_profile_cache_id()
            )
        # The profile this cycle loads, for its Status (LOST, UNAVAIL): the rotation
        # step, the shown rotation profile or the applied unmodified profile.
        cycle_profile_id = (pending_profile["id"] if pending_profile is not None
                            else active_profile_id or unmodified_applied_profile_id())

        try:
            if pending_profile is not None:
                set_rotation_status(
                    f"Loading {pending_profile['name']} "
                    f"(attempt {rotation.attempts + 1}/{rotation.max_attempts})..."
                )
                apply_image_settings(pending_profile["settings"])
                needs_plan = True

            if IMAGE_SOURCE == "copernicus" and get_image_dimensions() != (output_width, output_height):
                # Connected displays may change without a configuration edit.
                needs_plan = True

            cache_profile_id = (
                pending_profile["id"] if pending_profile is not None else active_profile_id
            )
            cache_configuration_signature = image_cache_configuration_key(
                capture_loaded_configuration()
            )
            if cache_profile_id is not None:
                # A cached PNG must not be shared with a differently named
                # profile while still claiming the previous profile's name.
                cache_configuration_signature["image_metadata_version"] = IMAGE_METADATA_VERSION
                cache_configuration_signature["image_profile_id"] = cache_profile_id
                cache_configuration_signature["image_profile_name"] = image_profile_name(cache_profile_id)
            frozen_path = None
            if not CHECK_FOR_SOURCE_UPDATES and not force_download:
                frozen_output_size = get_image_dimensions()
                try:
                    if cache_profile_id is None:
                        # The applied profile's own picture comes before Latest,
                        # which may hold an equal picture of another owner.
                        applied_profile_id = unmodified_applied_profile_id()
                        cached_applied_path = None
                        if applied_profile_id:
                            cached_applied_path = get_profile_cache().lookup_configuration(
                                applied_profile_id, cache_configuration_signature, frozen_output_size,
                            )
                        if cached_applied_path is None:
                            frozen_path = reusable_latest_image(
                                cache_configuration_signature,
                                frozen_output_size,
                            )
                        if frozen_path is None and cached_applied_path is None and not applied_profile_id:
                            cached_applied_path = get_profile_cache().lookup_configuration(
                                latest_snapshot.CACHE_ID,
                                cache_configuration_signature, frozen_output_size,
                            )
                        if cached_applied_path is not None:
                            frozen_path = publish_cached_profile_image(
                                cached_applied_path, cache_configuration_signature, frozen_output_size,
                            )
                            log(f"Reusing cached image of {cached_image_owner(applied_profile_id)}: "
                                f"{cached_applied_path}")
                        if frozen_path is not None and shown_identity_differs(frozen_path, applied_profile_id):
                            frozen_path = publish_with_identity(
                                frozen_path, applied_profile_id, cache_configuration_signature,
                                frozen_output_size, archive_previous=False,
                            )
                    else:
                        frozen_path = get_profile_cache().lookup_configuration(
                            cache_profile_id,
                            cache_configuration_signature,
                            frozen_output_size,
                        )
                except Exception as exc:
                    log(f"Saved image lookup warning: {exc}")

            if frozen_path is not None:
                output_width, output_height = frozen_output_size
                render_width, render_height = frozen_output_size
                render_mode, requests = "saved", []
                refresh_latest_times, time_signature = False, ()
                ensure_directories()
                configuration_before_plan = None
                # Keep the plan pending so a later manual force can resolve and
                # contact the selected provider normally.
                needs_plan = True
            elif needs_plan:
                if IMAGE_SOURCE == "eumetsat":
                    layers = parse_layers(download_capabilities())
                (output_width, output_height, render_width, render_height,
                 effective_render_scale, bbox, projection_name, projection, resolved_layers,
                 render_mode, requests, refresh_latest_times, time_signature) = prepare_runtime_render_plan(layers)
                ensure_directories()
                first_cycle = True
                if pending_profile is not None:
                    wallpaper_applied_path = None
                storage_estimate_printed = False
                needs_plan = False
                configuration_before_plan = None
                print_configuration(output_width, output_height, render_width, render_height,
                                    effective_render_scale, bbox, projection_name, projection,
                                    resolved_layers, render_mode)
            should_download = True
            pending_signature = time_signature

            if frozen_path is not None:
                should_download = False
                first_cycle = False
                log(
                    "Image update checks are disabled; reusing the saved image: "
                    f"{frozen_path}"
                )
            elif render_mode == "noaa":
                frame = get_noaa_frame((output_width, output_height))
                pending_signature = noaa_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if time_signature and pending_signature[:4] == time_signature[:4]:
                    pending_time = dt.datetime.fromisoformat(pending_signature[4].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[4].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError("NOAA currently lists an older image; keeping the newer installed image.")
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer image is available for the selected NOAA product and size.")
            elif render_mode == "himawari":
                frame = get_himawari_frame((output_width, output_height))
                pending_signature = himawari_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if time_signature and pending_signature[:4] == time_signature[:4]:
                    pending_time = dt.datetime.fromisoformat(pending_signature[4].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[4].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "Himawari currently lists an older image; keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer Himawari image is available for the selected product and size.")
            elif render_mode == "slider":
                frame = get_slider_frame((output_width, output_height))
                pending_signature = slider_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if time_signature and pending_signature[:4] == time_signature[:4]:
                    pending_time = dt.datetime.fromisoformat(pending_signature[4].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[4].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "CIRA SLIDER currently lists an older image; keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer CIRA SLIDER image is available for the selected product and size.")
            elif render_mode == "worldview":
                frame = get_worldview_frame((output_width, output_height))
                pending_signature = worldview_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if (time_signature and pending_signature[:4] == time_signature[:4]
                        and pending_signature[4] != "timeless" and time_signature[4] != "timeless"):
                    pending_time = dt.datetime.fromisoformat(
                        pending_signature[4].replace("Z", "+00:00")
                    )
                    installed_time = dt.datetime.fromisoformat(
                        time_signature[4].replace("Z", "+00:00")
                    )
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "NASA GIBS currently lists an older acquisition; "
                            "keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer NASA Worldview acquisition is available for the selected layer.")
            elif render_mode == "copernicus":
                frame = get_copernicus_frame((output_width, output_height))
                pending_signature = copernicus_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if (time_signature and pending_signature[:-1] == time_signature[:-1]
                        and pending_signature[-1] != "timeless" and time_signature[-1] != "timeless"):
                    pending_time = dt.datetime.fromisoformat(pending_signature[-1].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[-1].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "Copernicus currently lists an older acquisition; "
                            "keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer Copernicus acquisition is available for the selected product and location.")
            elif not first_cycle and refresh_latest_times:
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

            if (not should_download and frozen_path is None and cache_profile_id is None
                    and not get_latest_image_files()):
                # The Latest picture was removed (for example by hand): put the shown
                # one back, from the cache when it is there, else by loading it again.
                # The profile table marks its row active again. A rotation profile's
                # copy in Latest returns where it is copied, below.
                should_download = True
                log("The Latest folder has no picture; restoring the current one.")

            cached_profile_path = frozen_path
            cache_lookup_needed = should_download
            cache_source_time = (
                None if frozen_path is not None else
                profile_cache_source_time(render_mode, requests, pending_signature)
            )
            cached_latest_path = None
            if (
                cache_profile_id is None and frozen_path is None
                and should_download and not force_download
            ):
                applied_profile_id = unmodified_applied_profile_id()

                def reuse_from_latest():
                    try:
                        return reusable_latest_image(
                            cache_configuration_signature,
                            (output_width, output_height),
                            source_signature=pending_signature,
                            source_time=cache_source_time,
                            allow_legacy_source_time=render_mode in {
                                "noaa", "himawari", "slider", "copernicus", "worldview"
                            },
                        )
                    except Exception as exc:
                        log(f"Latest image lookup warning: {exc}")
                        return None

                def reuse_from_cache():
                    # Latest holds only the last image; an applied profile's or
                    # the latest snapshot's earlier image may still be in the
                    # profile cache.
                    try:
                        return get_profile_cache().lookup(
                            applied_profile_id or latest_snapshot.CACHE_ID,
                            cache_configuration_signature,
                            pending_signature,
                            (output_width, output_height),
                            source_time=cache_source_time,
                        )
                    except Exception as exc:
                        log(f"Applied profile cache lookup warning: {exc}")
                        return None

                # The applied profile's own picture comes before Latest, which
                # may hold an equal picture of another owner (the snapshot).
                order = ((("cache", reuse_from_cache), ("latest", reuse_from_latest)) if applied_profile_id
                         else (("latest", reuse_from_latest), ("cache", reuse_from_cache)))
                for origin, reuse in order:
                    candidate = reuse()
                    if candidate is None:
                        continue
                    try:
                        if shown_identity_differs(candidate, applied_profile_id):
                            # Same pixels and settings, but made for another owner:
                            # published as the applied selection's own. Replacing
                            # Latest's own copy archives nothing.
                            cached_latest_path = publish_with_identity(
                                candidate, applied_profile_id, cache_configuration_signature,
                                (output_width, output_height), pending_signature, cache_source_time,
                                archive_previous=origin == "cache",
                            )
                        elif origin == "cache":
                            cached_latest_path = publish_cached_profile_image(
                                candidate,
                                cache_configuration_signature,
                                (output_width, output_height),
                                pending_signature,
                                cache_source_time,
                            )
                        else:
                            cached_latest_path = candidate
                    except Exception as exc:
                        log(f"Cached image reuse warning: {exc}")
                        continue
                    if origin == "cache":
                        log(f"Reusing cached image of {cached_image_owner(applied_profile_id)}: {candidate}")
                    break
                if cached_latest_path is not None:
                    try:
                        _write_latest_state(
                            cached_latest_path,
                            cache_configuration_signature,
                            (output_width, output_height),
                            pending_signature,
                            cache_source_time,
                        )
                    except Exception as exc:
                        log(f"Latest image state update warning: {exc}")
                    should_download = False
                    time_signature = pending_signature
                    first_cycle = False
                    record_source_frame_status(render_mode, requests, source_time=cache_source_time)
                    log(
                        "No newer source image is available; reusing the "
                        f"verified latest image: {cached_latest_path}"
                    )
            if cache_profile_id is not None and frozen_path is None:
                if not force_download:
                    try:
                        cached_profile_path = get_profile_cache().lookup(
                            cache_profile_id,
                            cache_configuration_signature,
                            pending_signature,
                            (output_width, output_height),
                            source_time=cache_source_time,
                        )
                    except Exception as exc:
                        log(f"Profile cache lookup warning: {exc}")
                    if cached_profile_path is not None:
                        should_download = False
                        time_signature = pending_signature
                        first_cycle = False
                        record_source_frame_status(render_mode, requests, source_time=cache_source_time)
                        if cache_lookup_needed:
                            log(f"Reusing cached profile image: {cached_profile_path}")
                    elif not should_download:
                        # The active cache entry was cleared, removed or damaged.
                        should_download = True

            if should_download:
                report_update_status(status_callback, "fetching", cycle_next_check)
                installed_path, current_path, downloaded_size = perform_update(
                    render_mode,
                    requests,
                    render_width,
                    render_height,
                    output_width,
                    output_height,
                    cache_profile_id=cache_profile_id,
                    cache_configuration_signature=cache_configuration_signature,
                    cache_source_signature=pending_signature,
                    cache_source_time=cache_source_time,
                )
                time_signature = pending_signature
                first_cycle = False
            elif cached_profile_path is not None:
                installed_path = None
                current_path = cached_profile_path
                downloaded_size = 0
            elif cached_latest_path is not None:
                installed_path = None
                current_path = cached_latest_path
                downloaded_size = 0
            else:
                installed_path = None
                latest_files = get_latest_image_files()
                current_path = latest_files[0] if latest_files else None
                downloaded_size = 0

            shown_before = get_current_image_path()
            if current_path is not None:
                set_current_image_path(current_path, cache_profile_id)
            # The note below the Settings buttons: a new picture, another one from
            # the cache, or the one on screen is still current.
            cycle_outcome = (
                "downloaded" if installed_path is not None else
                "cache" if (not should_download and current_path is not None
                            and (shown_before is None
                                 or Path(current_path).resolve() != Path(shown_before).resolve())) else
                "current"
            )

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
                    wallpaper_position_pending = False
                except Exception as exc:
                    if pending_profile is not None:
                        raise
                    log(f"Windows wallpaper update warning: {exc}")

            if pending_profile is not None:
                if CONFIGURATION_RELOAD_EVENT.is_set() or APPLICATION_STOP_EVENT.is_set():
                    raise RuntimeError("Profile update cancelled because configuration changed or the application is stopping.")
                rotation.success()
                save_rotation_progress(rotation, shown_profile_id=pending_profile["id"])
                active_profile_id = pending_profile["id"]
                set_rotation_status(f"Active profile: {pending_profile['name']}", rotation.deadline,
                                    active_profile_id=pending_profile["id"])
                pending_profile = None
                profile_previous = None
                profile_previous_image = None

            # Latest always holds the picture on screen: a rotation profile's
            # cached image is copied there once it is shown, and again when the
            # copy was removed (for example by hand).
            if cache_profile_id is None:
                latest_published_path = None
            elif (current_path is not None
                  and (current_path != latest_published_path or not get_latest_image_files())
                  and Path(current_path).resolve().is_relative_to(get_profile_cache().images_dir.resolve())
                  and not CONFIGURATION_RELOAD_EVENT.is_set() and not APPLICATION_STOP_EVENT.is_set()):
                if current_path == latest_published_path:
                    log("The Latest folder has no picture; restoring the current one.")
                try:
                    publish_cached_profile_image(
                        current_path, cache_configuration_signature,
                        (output_width, output_height), pending_signature, cache_source_time,
                    )
                    latest_published_path = current_path
                except Exception as exc:
                    log(f"Unable to copy the shown image to Latest: {exc}")
            note_profile_outcome(cycle_profile_id)
            record_image_outcome(cycle_outcome, cycle_profile_id)

        except DownloadCancelledError:
            log("Download cancelled by user; keeping the current wallpaper.")
            record_image_outcome("cancelled", cycle_profile_id)
            if render_mode in {"noaa", "himawari", "slider", "copernicus", "worldview"}:
                with IMAGE_STATUS_LOCK:
                    IMAGE_STATUS["error"] = ""
            if pending_profile is not None:
                name = pending_profile["name"]
                rotation.cancel()
                try:
                    apply_image_settings(profile_previous)
                except Exception as rollback_error:
                    log(f"Unable to restore the previous image selection: {rollback_error}")
                pending_profile = None
                profile_previous = None
                if profile_previous_image is not None:
                    set_current_image_path(*profile_previous_image)
                profile_previous_image = None
                needs_plan = True
                set_rotation_status(
                    f"Cancelled {name}; waiting for the next rotation interval.",
                    rotation.deadline,
                )

        except Exception as exc:
            if isinstance(exc, UpdateSuperseded):
                log(str(exc))
                # A rotation step keeps its own retry; only the shown selection's
                # picture may be the one to show after the reload.
                superseded_download = pending_profile is None
            else:
                log(f"Update error: {exc}")
                if not CONFIGURATION_RELOAD_EVENT.is_set() and not APPLICATION_STOP_EVENT.is_set():
                    note_profile_outcome(cycle_profile_id, exc)
                    record_image_outcome(profile_failure_state(exc), cycle_profile_id)
            if pending_profile is None and configuration_before_plan is not None:
                restore_loaded_configuration(configuration_before_plan)
                rotation.configure(IMAGE_PROFILE_LIBRARY if RUN_CONTINUOUSLY else {})
                configuration_before_plan = None
                needs_plan = True
            if render_mode in {"noaa", "himawari", "slider", "copernicus", "worldview"} and not CONFIGURATION_RELOAD_EVENT.is_set():
                with IMAGE_STATUS_LOCK:
                    IMAGE_STATUS["error"] = str(exc)
            if args.once:
                raise
            if pending_profile is not None and not CONFIGURATION_RELOAD_EVENT.is_set() and not APPLICATION_STOP_EVENT.is_set():
                # A picture without image data stays empty however often it is asked
                # for, and a selection the provider no longer lists stays lost: this
                # step moves on; the next rotation round tries the profile again.
                # Without network every profile fails: the rotation keeps this one
                # and waits an interval instead of switching.
                state = profile_failure_state(exc)
                options = {}
                if isinstance(exc, (DownloadRetriesExhausted, NoImageData)) or state in ("LOST", "NETWORK"):
                    options["exhausted"] = True
                if state == "NETWORK":
                    options["keep"] = True
                result = rotation.failure(**options)
                name = pending_profile["name"]
                if result == "retry":
                    set_rotation_status(
                        f"{name}: attempt {rotation.attempts}/{rotation.max_attempts} failed; retrying..."
                    )
                else:
                    try:
                        apply_image_settings(profile_previous)
                    except Exception as rollback_error:
                        # A concurrent folder change can make the old Image
                        # snapshot incompatible with new History settings.
                        # Never roll back those newly saved General settings.
                        log(f"Unable to restore the previous image selection: {rollback_error}")
                    pending_profile = None
                    profile_previous = None
                    if profile_previous_image is not None:
                        set_current_image_path(*profile_previous_image)
                    profile_previous_image = None
                    needs_plan = True
                    set_rotation_status(
                        f"All profiles failed after {rotation.max_attempts} attempts each; "
                        "waiting for the next rotation interval."
                        if result == "wait" else
                        f"Skipped {name} after {rotation.max_attempts} failed attempts.",
                        rotation.deadline)

        if not CONFIGURATION_RELOAD_EVENT.is_set():
            # The cycle finished without being discarded by a reload.
            image_load_pending = False
        log(f"Update cycle took {time.monotonic() - cycle_started:.1f} s.")

        if not RUN_CONTINUOUSLY:
            break

        # Profile intervals are independent of source image refresh intervals.
        # A short, interruptible retry delay avoids hammering unavailable servers.
        NEXT_ROTATION_DEADLINE = (time.monotonic() + 5 if pending_profile is not None
                                  else rotation.deadline)
        preload = rotation_preload_step(rotation, preloaded_rotation_step)
        if pending_profile is None and preload is not None:
            NEXT_ROTATION_DEADLINE = (preload[2] if NEXT_ROTATION_DEADLINE is None
                                      else min(NEXT_ROTATION_DEADLINE, preload[2]))
        publish_next_rotation_profile(rotation)

        if not sleep_until_next_cycle(cycle_started, status_callback,
                                      wake_for_queue=pending_profile is None):
            log("Stop requested.")
            break


# =============================================================================
# WINDOWS NOTIFICATION AREA
# =============================================================================


WINDOWS_APP_USER_MODEL_ID = "Gittegatt.MarbleScape"


def set_windows_app_user_model_id():
    """Give every MarbleScape window the executable's stable taskbar identity."""
    if os.name != "nt":
        return False
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except (AttributeError, OSError) as exc:
        log(f"Unable to set the Windows application identity: {exc}")
        return False
    if result != 0:
        log(f"Unable to set the Windows application identity: HRESULT 0x{result & 0xFFFFFFFF:08X}")
        return False
    return True


def apply_tk_window_icon(root):
    """Apply the MarbleScape sphere to a Tk title bar and taskbar window."""
    import tkinter as tk

    icon_directory = RESOURCE_DIR / "assets" / "icons"
    images = []
    for size in (256, 64, 48, 32, 16):
        path = icon_directory / f"marblescape_{size}.png"
        if not path.is_file():
            raise FileNotFoundError(f"Window icon asset not found: {path}")
        images.append(tk.PhotoImage(master=root, file=str(path)))
    root.iconphoto(True, *images)
    if os.name == "nt":
        ico_path = icon_directory / "marblescape.ico"
        if not ico_path.is_file():
            raise FileNotFoundError(f"Windows icon asset not found: {ico_path}")
        # Set the .ico on this window only: combining iconphoto(True) with
        # iconbitmap(default=...) shows Windows' generic title-bar icon.
        root.iconbitmap(str(ico_path))
    root._marblescape_window_icons = images


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


def create_windows_tray_icon(*args, **kwargs):
    """Keep pystray's click/menu handling and explicitly accept double-clicks."""
    import pystray

    class SettingsTrayIcon(pystray.Icon):
        def _on_notify(self, wparam, lparam):
            if lparam == 0x0203:  # Windows WM_LBUTTONDBLCLK
                self()  # Invoke the same default Settings action as a left click.
                return
            if lparam == 0x0205:  # WM_RBUTTONUP: the menu opens in the saved appearance.
                try:
                    set_native_menus(resolve_appearance(APPEARANCE))
                except ValueError:
                    pass
            return super()._on_notify(wparam, lparam)

    return SettingsTrayIcon(*args, **kwargs)


def activate_settings_window(root):
    """Called only on the Settings GUI thread, never from the tray thread."""
    root.deiconify()
    root.lift()
    root.focus_force()


def dispose_owned_tk_roots(roots):
    """Release this thread's destroyed interpreters, not other GUI threads' GC."""
    import tkinter as tk
    from tkinter import ttk, font
    interpreters = [root.tk for root in roots]
    for root in roots:
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass
    # Tk callbacks can leave reference cycles. Their later collection must not
    # issue Tcl calls from the tray thread or during interpreter finalization.
    # Do not run global gc.collect(): that could collect another GUI's objects.
    for obj in gc.get_objects():
        if isinstance(obj, tk.Variable) and any(obj._tk is owner for owner in interpreters):
            try:
                obj.__del__()
            except tk.TclError:
                pass
            obj._tk = None
        elif isinstance(obj, tk.Image) and any(obj.tk is owner for owner in interpreters):
            try:
                obj.__del__()
            except tk.TclError:
                pass
            obj.name = None
            obj.tk = None
        elif isinstance(obj, font.Font) and any(obj._tk is owner for owner in interpreters):
            obj.__del__()
            obj.delete_font = False
            obj._tk = obj._call = None
        elif isinstance(obj, (tk.Misc, ttk.Style)) and any(obj.tk is owner for owner in interpreters):
            obj.tk = None
    roots.clear()


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


def get_restart_arguments():
    """Relaunch with the current options, naming the active config only once.

    Earlier ``--config`` values are dropped, so repeated restarts do not
    lengthen the command line.
    """
    current, skip_value = [], False
    for argument in sys.argv[1:]:
        if skip_value:
            skip_value = False
        elif argument == "--config":
            skip_value = True
        elif not argument.startswith("--config="):
            current.append(argument)
    return [*get_application_launch_arguments(), *current,
            "--config", str(ACTIVE_CONFIG_PATH.resolve())]


def get_windows_startup_command():
    """Start this installation with its active config, without transient CLI flags."""
    arguments = [*get_application_launch_arguments(), "--background", "--config",
                 str(resolve_script_relative_path(ACTIVE_CONFIG_PATH))]
    command = subprocess.list2cmdline(arguments)
    # HKCU Run command lines are limited to 260 Windows characters.
    if len(command.encode("utf-16-le")) // 2 > 260:
        raise ValueError(
            "The Windows startup command exceeds 260 characters. "
            "Use a shorter application or configuration path."
        )
    return command



def replace_toml_section_value(text, section_name, key, value):
    """Replace a TOML value or add the key to an existing section."""
    serialized = toml_value(value) if isinstance(value, dict) else json.dumps(value)
    header_pattern = re.compile(
        rf"(?m)^[ \t]*\[{re.escape(section_name)}\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)"
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
        insertion = f"{leading_newline}{key} = {serialized}{newline}"
        position = header_match.end()
        return text[:position] + insertion + text[position:]

    replacement = value_match.group(1) + serialized + value_match.group(3)
    start = header_match.end() + value_match.start()
    end = header_match.end() + value_match.end()
    return text[:start] + replacement + text[end:]


def replace_toml_values(text, updates):
    """Replace multiple existing TOML values while preserving file formatting."""
    updated = text
    for section_name, key, value in updates:
        updated = replace_toml_section_value(updated, section_name, key, value)
    return updated


def normalize_settings_window_size(display):
    """(width, height) of [display] settings_window_width/height; 0 = default."""
    size = []
    for key in ("settings_window_width", "settings_window_height"):
        value = display.get(key, 0) if isinstance(display, dict) else 0
        if type(value) is not int or not 0 <= value <= 20000:
            raise ValueError(f"display.{key} must be an integer from 0 to 20000.")
        size.append(value)
    return tuple(size)


def normalize_settings_tab(display):
    """The [display] settings_tab title; "" = the first tab."""
    value = display.get("settings_tab", "") if isinstance(display, dict) else ""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("display.settings_tab must be a tab title of at most 64 characters.")
    return value


def ensure_display_configuration_section(text):
    """Add the display table when saving a configuration from an older version."""
    if re.search(r"(?m)^\s*\[display\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[display]" + newline


def ensure_download_configuration_section(text):
    """Add the download table when saving a configuration from an older version."""
    if re.search(r"(?m)^\s*\[download\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[download]" + newline


def ensure_cache_configuration_section(text):
    """Add the profile cache table when saving a configuration from an older version."""
    if re.search(r"(?m)^\s*\[cache\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[cache]" + newline


def ensure_profile_list_configuration_section(text):
    """Add the profile-list table when saving an older configuration."""
    if re.search(r"(?m)^\s*\[profile_list\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[profile_list]" + newline


def replace_profile_column_widths(text, widths):
    """Keep either supported TOML spelling (inline table or explicit subtable)."""
    widths = normalize_profile_column_widths(widths)
    section = "profile_list.column_widths"
    if re.search(r"(?m)^\s*\[profile_list\.column_widths\]\s*(?:#[^\r\n]*)?$", text):
        for column, width in widths.items():
            text = replace_toml_section_value(text, section, column, width)
        return text
    return replace_toml_section_value(text, "profile_list", "column_widths", widths)


def ensure_updates_configuration_section(text):
    if re.search(r"(?m)^\s*\[updates\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[updates]" + newline


def source_configuration_updates(provider, profiles, check_for_updates=None):
    provider, profiles = normalize_source_configuration(provider, profiles)
    updates = [("source", "provider", provider)]
    if check_for_updates is not None:
        if type(check_for_updates) is not bool:
            raise ValueError("Image update checking must be true or false.")
        updates.append(("source", "check_for_updates", check_for_updates))
    return updates + [
        (f"sources.{name}",
         "gap_fill_mode" if name == "copernicus" and field == "coverage_mode" else field,
         value)
        for name, profile in profiles.items() for field, value in profile.items()
    ]


def replace_source_configuration(text, provider, profiles, check_for_updates=None):
    """Add source tables to older TOML files while retaining all existing settings."""
    newline = "\r\n" if "\r\n" in text else "\n"
    for section, key, value in source_configuration_updates(
        provider, profiles, check_for_updates
    ):
        if not re.search(rf"(?m)^\s*\[{re.escape(section)}\]\s*(?:#[^\r\n]*)?$", text):
            text = text.rstrip() + newline * 2 + f"[{section}]" + newline
        text = replace_toml_section_value(text, section, key, value)
    header = re.search(r"(?m)^[ \t]*\[sources\.copernicus\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)", text)
    if header:
        next_header = re.search(r"(?m)^\s*\[", text[header.end():])
        end = header.end() + next_header.start() if next_header else len(text)
        section = re.sub(
            r"(?m)^[ \t]*coverage_mode[ \t]*=[^\r\n]*(?:\r?\n|$)",
            "", text[header.end():end],
        )
        text = text[:header.end()] + section + text[end:]
    return text


def first_run_configuration_text(template_text):
    """Start every source with an automatic resolution on a fresh install."""
    text = template_text
    newline = "\r\n" if "\r\n" in text else "\n"
    for provider in AUTO_RESOLUTION_PROVIDERS:
        section = f"sources.{provider}"
        if not re.search(rf"(?m)^\s*\[{re.escape(section)}\]\s*(?:#[^\r\n]*)?$", text):
            text = text.rstrip() + newline * 2 + f"[{section}]" + newline
        text = replace_toml_section_value(text, section, "resolution", "auto")
    tomllib.loads(text)
    return text


def replace_copernicus_auth_configuration(text, auth):
    """Persist global Copernicus credentials outside rotatable image profiles."""
    if not isinstance(auth, dict):
        raise ValueError("Copernicus authentication settings are invalid.")
    client_id = str(auth.get("client_id", "")).strip()
    secret = str(auth.get("client_secret", ""))
    if len(client_id) > 500 or len(secret) > 4000:
        raise ValueError("Copernicus OAuth credentials are too long.")
    protected = (
        COPERNICUS_CLIENT_SECRET_PROTECTED
        if secret == COPERNICUS_CLIENT_SECRET
        else protect_client_secret(secret)
    )
    newline = "\r\n" if "\r\n" in text else "\n"
    if not re.search(r"(?m)^\s*\[copernicus\]\s*(?:#[^\r\n]*)?$", text):
        text = text.rstrip() + newline * 2 + "[copernicus]" + newline
    return replace_toml_values(text, (
        ("copernicus", "client_id", client_id),
        ("copernicus", "client_secret_protected", protected),
    ))


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


CUSTOM_AREA_FIELDS = (("custom_latitude", "Latitude"), ("custom_longitude", "Longitude"))
# Zoom 1000 still spans 0.36 degrees, finer than EUMETSAT imagery resolves.
CUSTOM_AREA_MAX_ZOOM = 1000.0


def zoom_in_range(value, mode):
    """Whether ``value`` is a valid zoom for the dropdown mode (view, custom or still)."""
    if value is None or not math.isfinite(value) or value <= 0:
        return False
    if mode == "still":
        return STILL_ZOOM_RANGE[0] <= value <= STILL_ZOOM_RANGE[1]
    if mode == "custom":
        return value <= CUSTOM_AREA_MAX_ZOOM
    return True


def zoom_choices(value, mode):
    """(zoom, label) pairs for the Zoom dropdown, sorted by zoom.

    A saved value between the steps is kept as its own "(saved)" entry, so a
    saved selection is never changed by the list.
    """
    custom = mode == "custom"
    notes = ZOOM_LEVEL_NOTES["custom" if custom else "view"]
    entries = {
        float(level): f"{level:g} ({notes[level]})" if level in notes else f"{level:g}"
        for level in (CUSTOM_AREA_ZOOM_LEVELS if custom else VIEW_ZOOM_LEVELS)
    }
    if value is not None and not any(math.isclose(value, level, abs_tol=1e-9) for level in entries):
        entries[float(value)] = f"{value:g} (saved)"
    return sorted(entries.items())


def custom_area_quality_text(zoom):
    """What EUMETSAT can show in a Custom area: its view and its sharpness."""
    text = ("Regions outside the satellites' view (the Americas, East Asia, the poles) "
            "show only the basemap.")
    if zoom > CUSTOM_AREA_SHARP_ZOOM:
        text = (f"Above about zoom {CUSTOM_AREA_SHARP_ZOOM} EUMETSAT imagery gets blurry. "
                + text)
    return text


def parse_custom_area_degrees(text, label, limit):
    """Parse decimal degrees; a decimal comma and a trailing degree sign are accepted."""
    text = str(text).strip().removesuffix("°").strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ValueError(f"Custom area: {label} must be a number of degrees, for example 47.5.")
    if not -limit <= value <= limit:
        raise ValueError(f"Custom area: {label} must be between -{limit:g} and {limit:g} degrees.")
    return value


def custom_area_extent(latitude, longitude, zoom, aspect_ratio):
    """Return the Geographic [west, south, east, north] around a centre.

    Zoom 1 spans the whole world width and the height follows the output ratio.
    An area larger than the world map is reduced to fit it, and an area reaching
    past the map edge is shifted onto the map, which moves its centre.
    """
    width = min(360.0 / zoom, 360.0)
    height = width / aspect_ratio
    if height > 180.0:
        height = 180.0
        width = height * aspect_ratio
    west = min(max(longitude - width / 2.0, -180.0), 180.0 - width)
    south = min(max(latitude - height / 2.0, -90.0), 90.0 - height)
    return [round(west, 6), round(south, 6),
            min(round(west + width, 6), 180.0), min(round(south + height, 6), 90.0)]


def custom_area_center(bbox, zoom):
    """Return the (latitude, longitude, zoom) that Settings shows for a Geographic bbox."""
    west, south, east, north = map(float, bbox)
    return (south + north) / 2.0, (west + east) / 2.0, 360.0 * zoom / (east - west)


def custom_area_from_form(values, projection_name, zoom, aspect_ratio):
    """Return the Custom area bbox and zoom that Settings saves.

    ``custom_bbox`` in ``values`` keeps a saved area the form left unchanged,
    including one in map units from the TOML file. Otherwise the Latitude and
    Longitude centre with the zoom define a Geographic area saved at zoom 1.
    """
    if "custom_bbox" in values:
        bbox = [float(value) for value in values["custom_bbox"]]
        if len(bbox) != 4 or not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
            raise ValueError("The saved custom area is invalid.")
        return bbox, zoom
    if not any(key in values for key, _label in CUSTOM_AREA_FIELDS):
        if not CUSTOM_BBOX:
            raise ValueError("Custom area needs Latitude and Longitude values.")
        return list(CUSTOM_BBOX), zoom
    if projection_name != "Geographic":
        raise ValueError("Custom area uses the Geographic projection.")
    latitude = parse_custom_area_degrees(values.get("custom_latitude", ""), "Latitude", 90.0)
    longitude = parse_custom_area_degrees(values.get("custom_longitude", ""), "Longitude", 180.0)
    if zoom > CUSTOM_AREA_MAX_ZOOM:
        raise ValueError(f"Custom area: Zoom can be at most {CUSTOM_AREA_MAX_ZOOM:g}.")
    return custom_area_extent(latitude, longitude, zoom, aspect_ratio), 1.0


def validate_custom_area_fits_outputs(bbox, fit_mode, zoom, aspect_ratios):
    """Fit adds map around the area; it must stay on the Geographic world map."""
    for ratio in aspect_ratios:
        if not geographic_extent_within_world(fitted_view_extent(bbox, ratio, fit_mode, zoom)):
            raise ValueError(
                "Custom area: with Fit, the output would extend beyond the edge of the "
                "world map. Choose Crop, a larger zoom, or an area further from the "
                "map edge."
            )


def normalize_settings_form_values(values, provider=None):
    """Validate settings dialog text and return typed TOML updates."""
    is_wms = (provider or IMAGE_SOURCE) == "eumetsat"
    has_update_value = "update_interval_value" in values
    has_update_unit = "update_interval_unit" in values
    if has_update_value != has_update_unit:
        raise ValueError("Update interval requires both a value and a unit.")
    try:
        zoom = float(values["zoom"])
        width = int(values["width"])
        height = int(values["height"])
        update_interval = (None if has_update_value
                           else float(values["update_interval_minutes"]))
        max_files = int(values["max_files"])
        years = int(values["years"])
        months = int(values["months"])
        days = int(values["days"])
        hours = int(values["hours"])
        minutes = int(values["minutes"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Numeric settings must contain valid numbers.") from exc
    if has_update_value:
        update_interval = update_interval_minutes(
            values["update_interval_value"], values["update_interval_unit"]
        )

    # The image's own render quality (EUMETSAT, Image > Rendering; "default"
    # inherits General's) and General's display render quality for every display.
    render_scale = (parse_render_scale_setting(values.get("eumetsat_render_scale", get_render_scale_setting()),
                                               allow_default=True)
                    if is_wms else get_render_scale_setting())
    display_render_scale = parse_render_scale_setting(values.get("render_scale", DISPLAY_RENDER_SCALE))
    position = str(values["position"]).strip().lower()
    view_preset = str(values["view_preset"]).strip().lower() if is_wms else VIEW_PRESET
    projection_name = str(values.get("projection", PROJECTION)).strip() if is_wms else PROJECTION
    fit_mode = str(values["fit_mode"]).strip().lower()
    aspect_ratio = normalize_aspect_ratio_text(values["aspect_ratio"])
    background_color = normalize_background_color(values["background_color"])
    latest_folder = str(values.get("latest_folder", CUSTOM_LATEST_FOLDER)).strip()
    history_folder = str(values.get("history_folder", CUSTOM_HISTORY_FOLDER)).strip()
    try:
        profile_history_policies = json.loads(str(values.get("profile_history_policies", "{}")))
    except json.JSONDecodeError as exc:
        raise ValueError("Profile History settings are invalid.") from exc
    if not isinstance(profile_history_policies, dict):
        raise ValueError("Profile History settings are invalid.")
    normalized_profile_history_policies = {}
    for identifier, policy in profile_history_policies.items():
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
            raise ValueError("Profile History settings contain an invalid profile ID.")
        normalized_profile_history_policies[identifier] = _normalize_history_policy(policy)
    validate_image_folders(
        resolve_script_relative_path(latest_folder)
        if latest_folder else OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / LATEST_DIRECTORY_NAME,
        resolve_script_relative_path(history_folder)
        if history_folder else OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / HISTORY_DIRECTORY_NAME,
    )
    retention_mode = str(values["retention_mode"]).strip().lower()
    display_time_zone = normalize_time_zone(values["time_zone"])
    appearance = values.get("appearance", APPEARANCE)
    appearance = normalize_appearance(next(
        (key for key, label in APPEARANCE_LABELS.items() if label == appearance), appearance
    ))
    download_speed_unit = normalize_download_speed_unit(values["download_speed_unit"])
    download_retries = normalize_download_retries(
        values.get("download_retries", DOWNLOAD_RETRIES)
    )
    catalogue_retries = normalize_catalogue_retries(
        values.get("catalogue_retries", CATALOGUE_RETRIES)
    )

    if position not in WALLPAPER_POSITION_CHOICES:
        raise ValueError("Wallpaper position is invalid.")
    paused_displays = normalize_paused_displays(list(values.get("paused_displays", WINDOWS_WALLPAPER_PAUSED)))
    monitor_positions = values.get("monitor_positions", WINDOWS_WALLPAPER_MONITOR_POSITIONS)
    if not isinstance(monitor_positions, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        or value not in WALLPAPER_POSITION_CHOICES
        for key, value in monitor_positions.items()
    ):
        raise ValueError("Monitor wallpaper positions are invalid.")
    monitor_outputs = normalize_monitor_output_settings(
        values.get("monitor_output_settings", WINDOWS_WALLPAPER_MONITOR_OUTPUTS),
        {"width": width, "height": height, "aspect_ratio": aspect_ratio,
         "render_scale": display_render_scale, "background_color": background_color},
    )
    if is_wms and view_preset not in VIEW_PRESETS:
        raise ValueError("View preset is invalid.")
    if is_wms and projection_name not in PROJECTIONS:
        raise ValueError("Projection is invalid.")
    # Existing regional presets remain geographic until projected presets
    # are implemented. Persist the effective projection shown in the UI.
    if is_wms:
        projection_name = VIEW_PRESETS[view_preset]["projection"] or projection_name
    if is_wms and projection_name not in available_projection_choices():
        raise ValueError("Projection is invalid.")
    if fit_mode not in {"fit", "crop"}:
        raise ValueError("Fit mode must be 'fit' or 'crop'.")
    if retention_mode not in {"count", "time", "both"}:
        raise ValueError("History retention mode is invalid.")
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("Zoom must be greater than zero.")
    if width <= 0:
        raise ValueError("Width must be greater than zero.")
    if height < 0:
        raise ValueError("Height must be zero or greater.")
    if not math.isfinite(update_interval) or update_interval <= 0:
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
    custom_bbox = None
    if is_wms and view_preset == "custom":
        custom_bbox, zoom = custom_area_from_form(values, projection_name, zoom, ratio)
    if custom_bbox is not None and projection_name == "Geographic":
        output_ratios = {ratio, *(
            parse_aspect_ratio(fields["aspect_ratio"])
            for fields in monitor_outputs.values() if "aspect_ratio" in fields
        )}
        validate_custom_area_fits_outputs(custom_bbox, fit_mode, zoom, output_ratios)

    retention_values = (years, months, days, hours, minutes)
    if any(value < 0 for value in retention_values):
        raise ValueError("History age values cannot be negative.")
    if retention_mode in {"time", "both"} and not any(retention_values):
        raise ValueError(
            "Time-based history retention requires an age greater than zero."
        )

    updates = (
        ("windows", "set_wallpaper", bool(values["set_wallpaper"])),
        ("windows", "position", position),
        ("windows", "monitor_positions", json.dumps(monitor_positions, sort_keys=True)),
        ("windows", "paused_displays", json.dumps(sorted(paused_displays))),
        ("windows", "monitor_output_settings", json.dumps(monitor_outputs, sort_keys=True)),
        ("display", "time_zone", display_time_zone),
        ("display", "appearance", appearance),
        ("view", "preset", view_preset),
        ("view", "projection", projection_name),
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
        ("output", "display_render_scale", display_render_scale),
        ("output", "background_color", background_color),
        ("output", "latest_folder", latest_folder),
        ("service", "update_interval_minutes", update_interval),
        ("download", "show_speed", bool(values["show_download_speed"])),
        ("download", "speed_unit", download_speed_unit),
        ("download", "retries", download_retries),
        ("download", "catalogue_retries", catalogue_retries),
        ("download", "catalogue_refresh_time", normalize_refresh_time(values.get("catalogue_refresh_time", CATALOGUE_REFRESH_TIME))),
        ("download", "show_progress", bool(values["show_download_progress"])),
        ("download", "show_size", bool(values.get("show_download_size", SHOW_DOWNLOAD_SIZE))),
        ("download", "show_progress_bar", bool(values["show_download_progress_bar"])),
        (
            "download", "keep_completed_visible",
            bool(values["keep_completed_download_visible"]),
        ),
        ("history", "enabled", bool(values["history_enabled"])),
        ("history", "folder", history_folder),
        ("history", "retention_mode", retention_mode),
        ("history", "max_files", max_files),
        ("history", "years", years),
        ("history", "months", months),
        ("history", "days", days),
        ("history", "hours", hours),
        ("history", "minutes", minutes),
        ("history", "profile_policies", json.dumps(normalized_profile_history_policies, sort_keys=True)),
        ("cache", "max_size_gb", normalize_profile_cache_max_size_gb(
            values.get("profile_cache_max_size_gb", PROFILE_CACHE_MAX_SIZE_GB))),
        ("cache", "variants_per_profile", normalize_profile_cache_variants(
            values.get("profile_cache_variants", PROFILE_CACHE_VARIANTS))),
        ("cache", "latest_snapshot_size_gb", normalize_profile_cache_snapshot_size_gb(
            values.get("profile_cache_snapshot_size_gb", PROFILE_CACHE_SNAPSHOT_SIZE_GB))),
    )
    if custom_bbox is not None:
        # Other presets keep the saved bbox so switching back restores it.
        updates += (("view", "bbox", custom_bbox),)
    if (provider or IMAGE_SOURCE) != "eumetsat":
        # Hidden WMS controls must never overwrite a retained EUMETSAT selection.
        wms_fields = {"preset", "projection", "truecolor_black_night"}
        updates = tuple(item for item in updates
                        if not (item[0] == "view" and item[1] in wms_fields)
                        and not (item[0] == "output" and item[1] == "render_scale"))
    return updates


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


def read_profile_library_file(path=None):
    """Read and validate the standalone profiles.toml document."""
    path = Path(path or ACTIVE_PROFILE_LIBRARY_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Profile file not found: {path}")
    if path.stat().st_size > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("profiles.toml is too large.")
    try:
        text = path.read_text(encoding="utf-8-sig")
        parsed = tomllib.loads(text)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read profiles.toml: {exc}") from exc
    if set(parsed) != {"image_profiles"}:
        raise ValueError(
            "profiles.toml must contain exactly one [image_profiles] document."
        )
    library = normalize_library(parsed["image_profiles"])
    # Saved names are cleaned in memory; the next save writes the clean form.
    cleaned = sum(1 for raw, item in zip(parsed["image_profiles"].get("items", []), library["items"])
                  if raw["name"].strip() != item["name"])
    if cleaned:
        log(f"Removed invisible or unusual characters from {cleaned} saved profile name(s).")
    return library


def write_profile_library_file_unlocked(library, path=None):
    """Atomically write a validated standalone profile library."""
    path = Path(path or ACTIVE_PROFILE_LIBRARY_PATH).resolve()
    text = serialize_library(library)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("profiles.toml is too large.")
    if path.exists() and path.read_bytes() == encoded:
        return False
    if not path.parent.exists():
        raise FileNotFoundError(f"Profile folder not found: {path.parent}")
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_bytes(encoded)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return True


def update_profile_library_file(library):
    with CONFIGURATION_FILE_LOCK:
        return write_profile_library_file_unlocked(library)


def read_active_configuration_text():
    with CONFIGURATION_FILE_LOCK:
        with ACTIVE_CONFIG_PATH.resolve().open("r", encoding="utf-8", newline="") as handle:
            return handle.read()


def update_active_configuration_and_profiles(transform, library):
    """Commit settings and profiles together, rolling profiles back on failure."""
    library = normalize_library(library)
    with CONFIGURATION_FILE_LOCK:
        config_path = ACTIVE_CONFIG_PATH.resolve()
        profile_path = ACTIVE_PROFILE_LIBRARY_PATH.resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        with config_path.open("r", encoding="utf-8", newline="") as handle:
            original_config = handle.read()
        updated_config = transform(original_config)
        tomllib.loads(updated_config)
        updated_profiles = serialize_library(library)
        original_profile_bytes = (
            profile_path.read_bytes() if profile_path.exists() else None
        )
        profile_bytes = updated_profiles.encode("utf-8")
        if len(profile_bytes) > MAX_BACKUP_CONFIGURATION_BYTES:
            raise ValueError("profiles.toml is too large.")
        config_changed = updated_config != original_config
        profiles_changed = original_profile_bytes != profile_bytes
        if not config_changed and not profiles_changed:
            return False

        config_temp = config_path.with_name(
            f".{config_path.name}.{uuid.uuid4().hex}.tmp"
        )
        profile_temp = profile_path.with_name(
            f".{profile_path.name}.{uuid.uuid4().hex}.tmp"
        )
        profile_replaced = False
        try:
            if config_changed:
                config_temp.write_text(updated_config, encoding="utf-8", newline="")
            if profiles_changed:
                profile_temp.write_bytes(profile_bytes)
                os.replace(profile_temp, profile_path)
                profile_replaced = True
            if config_changed:
                os.replace(config_temp, config_path)
        except Exception as exc:
            if profile_replaced:
                try:
                    if original_profile_bytes is None:
                        profile_path.unlink(missing_ok=True)
                    else:
                        rollback = profile_path.with_name(
                            f".{profile_path.name}.{uuid.uuid4().hex}.rollback"
                        )
                        try:
                            rollback.write_bytes(original_profile_bytes)
                            os.replace(rollback, profile_path)
                        finally:
                            rollback.unlink(missing_ok=True)
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"{exc} Profile rollback also failed: {rollback_error}"
                    ) from exc
            raise
        finally:
            config_temp.unlink(missing_ok=True)
            profile_temp.unlink(missing_ok=True)
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


def create_settings_backup_payload(include_profiles=True):
    """Return a lossless JSON backup of settings and profiles.toml."""
    with CONFIGURATION_FILE_LOCK:
        config_path = ACTIVE_CONFIG_PATH.resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        with config_path.open("r", encoding="utf-8", newline="") as handle:
            config_text = handle.read()
        parsed_settings = tomllib.loads(config_text)
        if include_profiles:
            if ACTIVE_PROFILE_LIBRARY_PATH.exists():
                profile_library = read_profile_library_file()
            else:
                profile_library = normalize_library(IMAGE_PROFILE_LIBRARY)
            # Upgrade only known legacy period defaults in the exported copy.
            # The original profile file, names, IDs and rotation stay unchanged.
            for item in profile_library["items"]:
                try:
                    item["settings"], _changes = prepare_profile_settings(
                        item["settings"], normalize_image_settings_snapshot, preserve_full=True)
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(f"Invalid backup profile '{item['name']}': {exc}") from exc
            profiles_text = serialize_library(profile_library)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    created_at = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    payload = {
        "scope": "settings_and_profiles" if include_profiles else "settings_only",
        "format": SETTINGS_BACKUP_FORMAT,
        "version": SETTINGS_BACKUP_VERSION,
        "created_at_utc": created_at,
        "configuration_sha256": config_digest,
        "settings": make_json_compatible(parsed_settings),
        "configuration_toml": config_text,
        "windows_startup_enabled": is_windows_startup_enabled(),
    }
    if include_profiles:
        payload.update(profiles_sha256=hashlib.sha256(profiles_text.encode("utf-8")).hexdigest(),
                       profiles=make_json_compatible(profile_library), profiles_toml=profiles_text)
    return payload


def validate_backup_configuration(config):
    """Offline preflight of restored settings; never load them into runtime globals."""
    latest_snapshot.from_config(config, lambda value: strict_settings(value, normalize_image_settings_snapshot))
    for section in ("source", "sources", "view", "output", "service", "download", "display",
                    "history", "cache", "windows", "updates", "profile_list", "copernicus"):
        if section in config and not isinstance(config[section], dict):
            raise ValueError(f"Backup settings.{section} must be a table.")
    required = {"source", "sources", "view", "output", "layers"}
    missing = required - config.keys()
    if missing:
        raise ValueError("Incomplete backup settings: missing " + ", ".join(sorted(missing)) + ".")
    snapshot = {key: deepcopy(config[key]) for key in required}
    snapshot["view"].setdefault("bbox", [])  # Optional except for a custom extent.
    copernicus = snapshot["sources"].get("copernicus")
    if isinstance(copernicus, dict) and "gap_fill_mode" in copernicus:
        copernicus["coverage_mode"] = copernicus.pop("gap_fill_mode")
    normalized = normalize_image_settings_snapshot(snapshot)
    device_output = normalize_device_output_settings(config["output"])
    if normalized["source"]["provider"] != "eumetsat":
        from marblescape_noaa import MAX_OUTPUT_PIXELS
        width = device_output["width"]
        height = device_output["height"] or round(width / parse_aspect_ratio(device_output["aspect_ratio"]))
        if width * height > MAX_OUTPUT_PIXELS or max(width, height) > 32768:
            raise ValueError("Backup output dimensions are too large for this image source.")
    for section, keys in (("service", ("run_continuously",)),
                          ("download", ("show_speed", "show_progress", "show_size", "show_progress_bar",
                                        "keep_completed_visible")),
                          ("history", ("enabled",)), ("windows", ("set_wallpaper", "pause_on_error"))):
        for key in keys:
            if key in config.get(section, {}) and type(config[section][key]) is not bool:
                raise ValueError(f"Backup settings.{section}.{key} must be true or false.")
    service = config.get("service", {})
    for key in ("timeout_seconds", "update_interval_minutes"):
        if key in service and (type(service[key]) not in (int, float) or not math.isfinite(service[key]) or service[key] <= 0):
            raise ValueError(f"Backup settings.service.{key} must be positive and finite.")
    if "render_mode" in service and service["render_mode"] not in {"auto", "server", "local"}:
        raise ValueError("Invalid backup settings.service.render_mode.")
    download = config.get("download", {})
    normalize_download_speed_unit(download.get("speed_unit", DEFAULT_DOWNLOAD_SPEED_UNIT))
    normalize_download_retries(download.get("retries", DEFAULT_DOWNLOAD_RETRIES))
    normalize_catalogue_retries(download.get("catalogue_retries", DEFAULT_CATALOGUE_RETRIES))
    normalize_refresh_time(download.get("catalogue_refresh_time", DEFAULT_REFRESH_TIME))
    normalize_time_zone(config.get("display", {}).get("time_zone", "system"))
    normalize_appearance(config.get("display", {}).get("appearance", "system"))
    normalize_settings_window_size(config.get("display", {}))
    normalize_settings_tab(config.get("display", {}))
    history = config.get("history", {})
    for key in ("max_files", "years", "months", "days", "hours", "minutes"):
        if key in history and (type(history[key]) is not int or history[key] < 0):
            raise ValueError(f"Backup settings.history.{key} must be a nonnegative integer.")
    mode = history.get("retention_mode", "count")
    if mode not in {"count", "time", "both"}:
        raise ValueError("Invalid backup settings.history.retention_mode.")
    profile_cache = config.get("cache", {})
    if "max_size_gb" in profile_cache:
        if type(profile_cache["max_size_gb"]) not in (int, float):
            raise ValueError("Backup settings.cache.max_size_gb must be a number.")
        normalize_profile_cache_max_size_gb(profile_cache["max_size_gb"])
    if "variants_per_profile" in profile_cache:
        if type(profile_cache["variants_per_profile"]) is not int:
            raise ValueError("Backup settings.cache.variants_per_profile must be a whole number.")
        normalize_profile_cache_variants(profile_cache["variants_per_profile"])
    if "latest_snapshot_size_gb" in profile_cache:
        if type(profile_cache["latest_snapshot_size_gb"]) not in (int, float):
            raise ValueError("Backup settings.cache.latest_snapshot_size_gb must be a number.")
        normalize_profile_cache_snapshot_size_gb(profile_cache["latest_snapshot_size_gb"])
    if mode in {"time", "both"} and not any(history.get(key, 0) for key in ("years", "months", "days", "hours", "minutes")):
        raise ValueError("Backup history requires a positive retention time.")
    windows = config.get("windows", {})
    if windows.get("position", "fill") not in WALLPAPER_POSITION_CHOICES:
        raise ValueError("Invalid backup wallpaper position.")
    positions = windows.get("monitor_positions", {})
    if isinstance(positions, str):
        positions = load_transfer_json(positions)
    if not isinstance(positions, dict) or any(not isinstance(value, str) or value.lower() not in WALLPAPER_POSITION_CHOICES
                                              for value in positions.values()):
        raise ValueError("Invalid backup monitor positions.")
    try:
        normalize_paused_displays(windows.get("paused_displays", "[]"))
    except ValueError as exc:
        raise ValueError("Invalid backup paused displays.") from exc
    # A display without its own render quality uses General's, as when the file
    # is loaded; the image's "default" (follow General) is no display value.
    image_scale = normalized["output"].get("render_scale", "auto")
    display_scale = config["output"].get(
        "display_render_scale", "auto" if image_scale == "default" else image_scale)
    normalize_monitor_output_settings(windows.get("monitor_output_settings", {}),
                                      {**device_output, **normalized["output"],
                                       "render_scale": display_scale})
    for section, keys in (("output", ("windows_root", "linux_root", "latest_folder")), ("history", ("folder",))):
        for key in keys:
            if key in config.get(section, {}) and (not isinstance(config[section][key], str) or "\0" in config[section][key]):
                raise ValueError(f"Invalid backup path settings.{section}.{key}.")


def parse_settings_backup_payload(payload, confirm_repair=None):
    """Validate a JSON settings backup and return its restorable values."""
    if not isinstance(payload, dict):
        raise ValueError("The backup root must be a JSON object.")
    if payload.get("format") != SETTINGS_BACKUP_FORMAT:
        raise ValueError("This is not a MarbleScape settings backup.")
    version = payload.get("version")
    if type(version) is not int or version not in (2, SETTINGS_BACKUP_VERSION):
        raise ValueError(
            f"Unsupported backup version: {version!r}."
        )

    scope = payload.get("scope", "settings_and_profiles" if version == 2 else None)
    if scope not in {"settings_only", "settings_and_profiles"}:
        raise ValueError("The backup scope is missing or unsupported.")
    if scope == "settings_only" and any(key in payload for key in ("profiles", "profiles_toml", "profiles_sha256")):
        raise ValueError("Settings-only backup unexpectedly contains profiles.")
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
    validate_backup_configuration(parsed_settings)

    profile_library = None
    if scope == "settings_and_profiles":
        profiles_text = payload.get("profiles_toml")
        if not isinstance(profiles_text, str) or not profiles_text.strip():
            raise ValueError("The backup contains no profiles.toml document.")
        if len(profiles_text.encode("utf-8")) > MAX_BACKUP_CONFIGURATION_BYTES:
            raise ValueError("The profile library stored in the backup is too large.")
        expected_profiles_digest = payload.get("profiles_sha256")
        actual_profiles_digest = hashlib.sha256(
            profiles_text.encode("utf-8")
        ).hexdigest()
        if (
            not isinstance(expected_profiles_digest, str)
            or expected_profiles_digest.lower() != actual_profiles_digest
        ):
            raise ValueError("The backup profile-library checksum is invalid.")
        try:
            parsed_profiles = tomllib.loads(profiles_text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"The backup contains invalid profile TOML: {exc}") from exc
        if set(parsed_profiles) != {"image_profiles"}:
            raise ValueError("The backup profile document is invalid.")
        profile_library = normalize_library(parsed_profiles["image_profiles"])
        readable_profiles = payload.get("profiles")
        try:
            # Older backups list device output settings that reading now drops.
            readable_profiles = make_json_compatible(normalize_library(readable_profiles))
        except (ValueError, TypeError):
            pass
        if readable_profiles != make_json_compatible(profile_library):
            raise ValueError(
                "The readable profile snapshot does not match profiles.toml."
            )

    startup_enabled = payload.get("windows_startup_enabled")
    if not isinstance(startup_enabled, bool):
        raise ValueError("The backup contains an invalid Windows startup setting.")

    if profile_library is not None:
        prepared, failures = [], []
        for item in profile_library["items"]:
            try:
                item["settings"], changes = prepare_profile_settings(
                    item["settings"], normalize_image_settings_snapshot, preserve_full=True)
                prepared.append((f"Backup profile '{item['name']}'", item, changes))
            except (ValueError, TypeError, KeyError) as exc:
                failures.append(f"Invalid backup profile '{item['name']}': {exc}")
        if failures:
            raise ValueError("\n".join(failures))
        # A full restore is atomic: skipping a profile could invalidate rotation.
        profile_library["items"], _skipped = resolve_import_repairs(
            prepared, confirm_repair, allow_skip=False)

    return config_text, profile_library, startup_enabled


def export_settings_backup(output_path, include_profiles=True):
    """Write the active settings to an atomic JSON backup file."""
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".json":
        output_path = output_path.with_suffix(".json")
    output_path = output_path.resolve()
    if not output_path.parent.exists():
        raise FileNotFoundError(
            f"Backup folder does not exist: {output_path.parent}"
        )

    payload = create_settings_backup_payload(include_profiles=include_profiles)
    parse_settings_backup_payload(payload)  # Never create an unrestorable backup.
    data = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_SETTINGS_BACKUP_FILE_BYTES:
        raise ValueError("The backup file is too large.")
    write_verified_export_batch(
        [(output_path, data, export_file_marker(output_path))],
        lambda saved: parse_settings_backup_payload(load_transfer_json(saved.decode("utf-8"))),
    )

    return output_path


def import_settings_backup(input_path, include_profiles=True, confirm_repair=None):
    """Read and validate a JSON settings backup without changing live state.

    include_profiles=None restores whatever scope the backup contains.
    """
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Backup file not found: {input_path}")
    if input_path.stat().st_size > MAX_SETTINGS_BACKUP_FILE_BYTES:
        raise ValueError("The backup file is too large.")

    try:
        payload = load_transfer_json(input_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the JSON backup: {exc}") from exc

    config_text, library, startup = parse_settings_backup_payload(payload, confirm_repair)
    if include_profiles and library is None:
        raise ValueError("This backup contains settings only. Choose Import settings only.")
    return config_text, None if include_profiles is False else library, startup


def get_selected_wms_layer_name():
    for entry in LAYER_CONFIG:
        if (
            str(entry.get("kind", "")).lower() == "wms"
            and entry.get("enabled", True)
            and float(entry.get("opacity", 1.0)) > 0
        ):
            return str(entry.get("name", ""))
    return None


def _parse_windows_startup_command(command):
    """Parse Windows quoting without executing the registry command."""
    if not isinstance(command, str) or not command.strip() or "\0" in command:
        return None
    if len(command.encode("utf-16-le")) // 2 > 260:
        return None
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    free = kernel32.LocalFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = parse(command, ctypes.byref(count))
    if not pointer:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        free(pointer)


def _windows_startup_identity(snapshot):
    """Return (application path, config path, interpreter/launcher path) for a plain app launch only."""
    if snapshot is None:
        return None
    import winreg
    command, kind = snapshot
    if kind == winreg.REG_EXPAND_SZ and isinstance(command, str):
        command = winreg.ExpandEnvironmentStrings(command)
    elif kind != winreg.REG_SZ:
        return None
    arguments = _parse_windows_startup_command(command)
    if not arguments or not Path(arguments[0]).is_absolute():
        return None
    launcher = Path(arguments[0])
    if (launcher.name.casefold() in {"python.exe", "pythonw.exe"} or
            (not getattr(sys, "frozen", False) and
             launcher.name.casefold() == Path(sys.executable).name.casefold())):
        if len(arguments) < 2 or not Path(arguments[1]).is_absolute():
            return None
        application = Path(arguments[1])
        if application.suffix.casefold() != ".py":
            return None
        remaining = arguments[2:]
    else:
        if launcher.suffix.casefold() != ".exe":
            return None
        application = launcher
        remaining = arguments[1:]
    if "--background" in remaining:
        remaining = [argument for argument in remaining if argument != "--background"]
    if not remaining:
        config_path = DEFAULT_CONFIG_PATH
    elif len(remaining) == 2 and remaining[0] == "--config" and remaining[1]:
        config_path = remaining[1]
    elif len(remaining) == 1 and remaining[0].startswith("--config=") and remaining[0][9:]:
        config_path = remaining[0][9:]
    else:
        # --once, diagnostics, unrelated arguments, and shell wrappers are not
        # a persistent MarbleScape startup registration.
        return None
    return (os.path.normcase(str(application.resolve())),
            os.path.normcase(str(resolve_script_relative_path(config_path))),
            os.path.normcase(str(launcher.resolve())))


def _windows_startup_matches_current(snapshot):
    identity = _windows_startup_identity(snapshot)
    if identity is None:
        return False
    active_config = os.path.normcase(str(resolve_script_relative_path(ACTIVE_CONFIG_PATH)))
    if identity[1] != active_config:
        return False
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        expected = os.path.normcase(str(executable))
        return identity[0] == expected and identity[2] == expected
    launchers = {os.path.normcase(str(executable))}
    if executable.name.casefold() in {"python.exe", "pythonw.exe"}:
        launchers.update(os.path.normcase(str(executable.with_name(name)))
                         for name in ("python.exe", "pythonw.exe"))
    return (identity[0] == os.path.normcase(str(Path(__file__).resolve()))
            and identity[2] in launchers)



def capture_windows_startup_state():
    """Read exactly this Run value for a later lossless transaction rollback."""
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                            access=winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, WINDOWS_RUN_VALUE_NAME)
            return (list(value) if isinstance(value, list) else value, kind)
    except FileNotFoundError:
        return None


def restore_windows_startup_state(snapshot):
    """Restore only MarbleScape's Run value, preserving its data and registry type."""
    if os.name != "nt":
        return
    import winreg
    if snapshot is not None and (not isinstance(snapshot, tuple) or len(snapshot) != 2):
        raise ValueError("Invalid Windows startup snapshot.")
    if capture_windows_startup_state() == snapshot:
        return
    if snapshot is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                                access=winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE_NAME)
        except FileNotFoundError:
            pass
    else:
        value, kind = snapshot
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                                access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, WINDOWS_RUN_VALUE_NAME, 0, kind, value)


def is_windows_startup_enabled():
    """Whether Run registers this installation and active configuration."""
    if os.name != "nt":
        return False
    return _windows_startup_matches_current(capture_windows_startup_state())


def set_windows_startup_enabled(enabled):
    if os.name != "nt":
        return
    import winreg
    previous = capture_windows_startup_state()
    if enabled:
        desired = (get_windows_startup_command(), winreg.REG_SZ)
    else:
        if not _windows_startup_matches_current(previous):
            return
        desired = None
    if previous == desired:
        return
    try:
        restore_windows_startup_state(desired)
    except OSError as error:
        try:
            restore_windows_startup_state(previous)
        except OSError as rollback_error:
            raise RuntimeError(
                f"{error} Windows startup rollback also failed: {rollback_error}"
            ) from error
        raise



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


def restore_settings_backup(config_text, profile_library, startup_enabled):
    """Write a validated backup; roll the Windows startup setting back on failure."""
    startup_before_import = is_windows_startup_enabled()
    startup_changed = startup_enabled != startup_before_import
    if startup_changed:
        startup_snapshot = capture_windows_startup_state()
        set_windows_startup_enabled(startup_enabled)

    try:
        if profile_library is None:
            return update_active_configuration(lambda _text: config_text)
        return update_active_configuration_and_profiles(
            lambda _text: config_text, profile_library
        )
    except Exception as config_error:
        if startup_changed:
            try:
                restore_windows_startup_state(startup_snapshot)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{config_error} Windows startup rollback also "
                    f"failed: {rollback_error}"
                ) from config_error
        raise


def working_settings_backup_folder(config_path=None):
    """Automatic backups live beside the configuration file they protect."""
    return Path(config_path or ACTIVE_CONFIG_PATH).resolve().parent / "backups" / "automatic"


def list_working_settings_backups(folder=None):
    """Return (local time, folder) pairs of automatic backups, newest first."""
    folder = Path(folder or working_settings_backup_folder())
    backups = []
    try:
        candidates = list(folder.iterdir())
    except OSError:
        return []
    for candidate in candidates:
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}_\d{6})(?:_\d+)?", candidate.name)
        if not match or not (candidate / WORKING_SETTINGS_CONFIG_NAME).is_file():
            continue
        try:
            created = dt.datetime.strptime(match.group(1), "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        backups.append((created, candidate))
    return sorted(backups, key=lambda item: (item[0], item[1].name), reverse=True)


def read_working_settings_backup(folder):
    profiles = folder / WORKING_SETTINGS_PROFILES_NAME
    return ((folder / WORKING_SETTINGS_CONFIG_NAME).read_bytes(),
            profiles.read_bytes() if profiles.is_file() else None)


def save_working_settings_backup(now=None):
    """Keep the last successfully loaded settings so startup errors can be undone."""
    if LOADED_SETTINGS_FILES is None:
        return None
    config_path, config_bytes, profile_bytes = LOADED_SETTINGS_FILES
    # The shipped template is never edited, so it needs no backup.
    if not config_path.exists() or config_path == DEFAULT_CONFIG_TEMPLATE_PATH.resolve():
        return None
    folder = working_settings_backup_folder(config_path)
    with CONFIGURATION_FILE_LOCK:
        backups = list_working_settings_backups(folder)
        if backups and read_working_settings_backup(backups[0][1]) == (config_bytes, profile_bytes):
            return None
        stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M%S")
        target, counter = folder / stamp, 2
        while target.exists():
            target, counter = folder / f"{stamp}_{counter}", counter + 1
        temporary = folder / f".{target.name}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir(parents=True)
        try:
            (temporary / WORKING_SETTINGS_CONFIG_NAME).write_bytes(config_bytes)
            if profile_bytes is not None:
                (temporary / WORKING_SETTINGS_PROFILES_NAME).write_bytes(profile_bytes)
            os.replace(temporary, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        for _created, old in list_working_settings_backups(folder)[WORKING_SETTINGS_BACKUP_LIMIT:]:
            shutil.rmtree(old, ignore_errors=True)
    return target


def save_working_settings_backup_safely():
    try:
        save_working_settings_backup()
    except Exception as exc:
        log(f"Unable to save the automatic settings backup: {exc}")


def keep_settings_before_restore(now=None):
    """Copy the current (failing) files aside before a restore replaces them."""
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    target = working_settings_backup_folder() / f"before-restore_{stamp}_{uuid.uuid4().hex[:6]}"
    target.mkdir(parents=True)
    for source, name in ((ACTIVE_CONFIG_PATH, WORKING_SETTINGS_CONFIG_NAME),
                         (ACTIVE_PROFILE_LIBRARY_PATH, WORKING_SETTINGS_PROFILES_NAME)):
        if Path(source).is_file():
            shutil.copy2(source, target / name)
    return target


def restore_working_settings_backup(folder):
    """Write an automatic backup back; profiles.toml is replaced only if it was saved."""
    config_bytes, profile_bytes = read_working_settings_backup(folder)
    config_text = config_bytes.decode("utf-8")
    if profile_bytes is None:
        return update_active_configuration(lambda _text: config_text)
    library = read_profile_library_file(folder / WORKING_SETTINGS_PROFILES_NAME)
    return update_active_configuration_and_profiles(lambda _text: config_text, library)


def open_file_for_editing(path):
    """Open a settings file in the user's editor, falling back to Notepad."""
    for verb in ("edit", "open"):
        try:
            os.startfile(str(path), verb)
            return
        except OSError:
            pass
    subprocess.Popen(["notepad.exe", str(path)])


def show_configuration_recovery_dialog(error, master=None, backup_folder=None):
    """Explain a startup configuration error; True means load the settings again."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    owned = master is None
    if owned:
        master = tk.Tk()
        master.withdraw()
        try:
            apply_appearance(master, APPEARANCE)
        except Exception as exc:
            log(f"Window appearance warning: {exc}")
        try:
            apply_tk_window_icon(master)
        except Exception as exc:
            log(f"Unable to apply window icon: {exc}")
    result = [False]
    try:
        window = tk.Toplevel(master)
        window.title("MarbleScape configuration error")
        window.attributes("-topmost", True)
        frame = ttk.Frame(window, padding=16)
        frame.grid(sticky="nsew")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(
            frame, text="MarbleScape could not start because its settings could not be loaded:",
        ).grid(row=0, column=0, sticky="w")
        details = tk.Text(frame, width=80, height=6, wrap="word")
        details.insert("1.0", str(error))
        details.configure(state="disabled")
        details.grid(row=1, column=0, sticky="nsew", pady=(6, 10))
        frame.rowconfigure(1, weight=1)
        config_path = ACTIVE_CONFIG_PATH.resolve()
        profile_path = ACTIVE_PROFILE_LIBRARY_PATH.resolve()
        ttk.Label(
            frame, justify="left", wraplength=640,
            text=(f"Settings file: {config_path}\nProfiles file: {profile_path}\n\n"
                  "Correct the file in a text editor, save it and choose Try again. "
                  "Alternatively, restore the last working settings or a settings backup "
                  "exported from Settings > Backup. The current files are kept in "
                  "backups\\automatic before a restore replaces them."),
        ).grid(row=2, column=0, sticky="w")

        def choose(value):
            result[0] = value
            window.destroy()

        def open_file(path):
            try:
                open_file_for_editing(path)
            except Exception as exc:
                messagebox.showerror("Unable to open file", str(exc), parent=window)

        def restore_backup():
            selected_path = filedialog.askopenfilename(
                parent=window,
                title="Restore MarbleScape settings backup",
                initialdir=str(backup_folder or SCRIPT_DIR),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                config_text, profile_library, startup_enabled = import_settings_backup(
                    selected_path, include_profiles=None,
                    confirm_repair=lambda label, changes: confirm_import_repair(
                        window, label, changes, allow_skip=False),
                )
            except ImportCancelled:
                return
            except Exception as exc:
                messagebox.showerror("Unable to restore backup", str(exc), parent=window)
                return
            scope = "settings" if profile_library is None else "settings and all profiles"
            if not messagebox.askyesno(
                "Restore settings backup",
                f"Restoring this backup replaces the current {scope}. Continue?",
                parent=window, icon="warning",
            ):
                return
            try:
                keep_settings_before_restore()
                restore_settings_backup(config_text, profile_library, startup_enabled)
            except Exception as exc:
                messagebox.showerror("Unable to restore backup", str(exc), parent=window)
                return
            choose(True)

        working_backups = list_working_settings_backups()

        def restore_working_settings():
            created, folder = working_backups[0]
            if not messagebox.askyesno(
                "Restore last working settings",
                "Restore the settings and profiles that last loaded successfully on "
                f"{created:%Y-%m-%d %H:%M:%S}? Changes made since then are replaced.",
                parent=window, icon="warning",
            ):
                return
            try:
                keep_settings_before_restore()
                restore_working_settings_backup(folder)
            except Exception as exc:
                messagebox.showerror("Unable to restore settings", str(exc), parent=window)
                return
            choose(True)

        working = ttk.Frame(frame)
        working.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        working.columnconfigure(0, weight=1)
        ttk.Label(working, text=(
            f"Last working settings: {working_backups[0][0]:%Y-%m-%d %H:%M:%S}"
            if working_backups else "No automatic backup of working settings yet."
        )).grid(row=0, column=0, sticky="w")
        restore_working = ttk.Button(working, text="Restore last working settings",
                                     command=restore_working_settings)
        restore_working.grid(row=0, column=1, sticky="e")
        if not working_backups:
            restore_working.state(["disabled"])

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(3, weight=1)
        open_config = ttk.Button(buttons, text="Open settings file",
                                 command=lambda: open_file(config_path))
        open_config.grid(row=0, column=0, padx=(0, 6))
        if not config_path.exists():
            open_config.state(["disabled"])
        open_profiles = ttk.Button(buttons, text="Open profiles file",
                                   command=lambda: open_file(profile_path))
        open_profiles.grid(row=0, column=1, padx=(0, 6))
        if not profile_path.exists():
            open_profiles.state(["disabled"])
        ttk.Button(buttons, text="Restore exported backup...", command=restore_backup).grid(
            row=0, column=2)
        retry = ttk.Button(buttons, text="Try again", command=lambda: choose(True))
        retry.grid(row=0, column=4, padx=(6, 0))
        ttk.Button(buttons, text="Exit", command=lambda: choose(False)).grid(
            row=0, column=5, padx=(6, 0))
        window.protocol("WM_DELETE_WINDOW", lambda: choose(False))
        window.bind("<Escape>", lambda _event: choose(False))
        retry.focus_set()
        window.focus_force()
        master.wait_window(window)
        return result[0]
    finally:
        if owned:
            master.destroy()


def report_configuration_error(error, backup_folder=None):
    """Offer the recovery dialog; fall back to a plain message box without Tk."""
    try:
        return show_configuration_recovery_dialog(error, backup_folder=backup_folder)
    except Exception as dialog_error:
        log(f"Unable to show the configuration recovery dialog: {dialog_error}")
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            str(error),
            "MarbleScape configuration error",
            0x10,
        )
    except Exception:
        pass
    return False


def load_tray_configuration(argv, migrate_startup, recover):
    """Load settings for the tray; recover(error) returning True retries."""
    while True:
        try:
            tray_args = parse_arguments(argv)
            load_configuration(tray_args.config)
            # Validation errors would otherwise stop the tray worker after start.
            validate_configuration()
            if migrate_startup and is_windows_startup_enabled():
                # Upgrade only this installation's existing registration; never
                # enable autostart when the user has disabled it.
                set_windows_startup_enabled(True)
            save_working_settings_backup_safely()
            return tray_args
        except Exception as exc:
            log(f"Fatal error: {exc}")
            if not recover(exc):
                return None


def run_with_windows_tray(argv=None, migrate_startup=False):
    export_locations = ExportLocations(SCRIPT_DIR)
    set_windows_app_user_model_id()
    try:
        import pystray
    except ImportError:
        log(
            "Windows tray icon unavailable. Install dependencies with "
            "'python -m pip install -r requirements.txt'."
        )
        return run_application(argv)

    def settings_backup_directory():
        """Where settings backups are exported, so imports start there too."""
        try:
            return export_locations.initial_directory("settings")
        except Exception:
            return SCRIPT_DIR

    def recover_configuration(error):
        return report_configuration_error(error, settings_backup_directory())

    tray_args = load_tray_configuration(argv, migrate_startup, recover_configuration)
    if tray_args is None:
        return 1

    APPLICATION_STOP_EVENT.clear()
    NETWORK_ACTIVITY.reset()
    FORCE_UPDATE_EVENT.clear()
    CONFIGURATION_RELOAD_EVENT.clear()
    SETTINGS_ONLY_RELOAD_EVENT.clear()
    result = {"exit_code": 0}
    settings_dialog_lock = threading.Lock()
    settings_dialog_state = {"open": False, "support_parent_available": False,
                             "support_requested": False, "activate_requested": False}
    support_dialog_lock = threading.Lock()
    support_dialog_state = {"open": False, "window": None, "token": None}
    gui_threads = []
    gui_threads_lock = threading.Lock()
    gui_local = threading.local()

    def start_gui_worker(target, name):
        def run():
            gui_local.roots = []
            try:
                target()
            finally:
                dispose_owned_tk_roots(gui_local.roots)
        thread = threading.Thread(target=run, name=name, daemon=True)
        with gui_threads_lock:
            gui_threads.append(thread)
        thread.start()
    tray_status_lock = threading.Lock()
    tray_status = {
        "state": "starting",
        "next_check": None,
    }

    def create_tray_dialog_root(tk):
        """Close each Tk window on its own GUI thread when the tray exits."""
        root = tk.Tk()
        if hasattr(gui_local, "roots"):
            gui_local.roots.append(root)
        try:
            apply_appearance(root, APPEARANCE)
            follow_system_appearance(root, lambda: getattr(root, "_marblescape_saved_appearance", APPEARANCE))
        except Exception as exc:
            log(f"Window appearance warning: {exc}")
        original_destroy = root.destroy

        def destroy_on_gui_thread():
            icons = getattr(root, "_marblescape_window_icons", [])
            icons.clear()
            root._marblescape_window_icons = []
            original_destroy()

        root.destroy = destroy_on_gui_thread

        def close_when_stopping():
            if APPLICATION_STOP_EVENT.is_set():
                root.destroy()
            else:
                root.after(100, close_when_stopping)

        root.after(100, close_when_stopping)
        return root

    def get_tray_status_snapshot():
        with tray_status_lock:
            return tray_status["state"], tray_status["next_check"]

    def format_next_check_status(time_zone=None):
        state, next_check = get_tray_status_snapshot()
        if next_check is not None:
            return format_display_datetime(
                next_check,
                DISPLAY_TIME_ZONE if time_zone is None else time_zone,
                include_seconds=True,
            )
        if state == "checking":
            return "Checking now"
        return "Calculating"

    def open_output_folder(icon, item):
        del item
        try:
            # Latest always holds the picture on screen; the cache is internal.
            folder = LATEST_DIR
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder.resolve()))
        except Exception as exc:
            try:
                icon.notify(str(exc), "Unable to open image folder")
            except Exception:
                log(f"Unable to open image folder: {exc}")

    def stop_application(icon):
        APPLICATION_STOP_EVENT.set()
        NETWORK_ACTIVITY.cancel()
        DOWNLOAD_PROGRESS.request_cancel()
        start_forced_exit_timer()
        icon.stop()

    def exit_application(icon, item):
        del item
        stop_application(icon)

    def show_tray_error(icon, title, error):
        try:
            icon.notify(str(error), title)
        except Exception:
            log(f"{title}: {error}")

    def run_update_notice_dialog(release, parent=None):
        import tkinter as tk
        from tkinter import messagebox, ttk

        root = tk.Toplevel(parent) if parent is not None else create_tray_dialog_root(tk)
        if parent is not None:
            root.transient(parent)
        apply_tk_window_icon(root)
        original_destroy = root.destroy

        def destroy_notice():
            icons = getattr(root, "_marblescape_window_icons", [])
            root._marblescape_window_icons = []
            icons.clear()
            original_destroy()

        root.destroy = destroy_notice
        root.protocol("WM_DELETE_WINDOW", destroy_notice)
        root.withdraw()
        root.title("MarbleScape update available")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame, text="A new MarbleScape version is available.",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text=f"Installed: v{VERSION.lstrip('v')}    Latest: {release['latest']}",
        ).grid(row=1, column=0, columnspan=2, pady=(6, 12), sticky="w")

        def skip_version():
            global SKIPPED_UPDATE_VERSION
            try:
                update_active_configuration(
                    lambda text: replace_toml_section_value(
                        ensure_updates_configuration_section(text),
                        "updates", "skipped_version", release["latest"],
                    )
                )
                SKIPPED_UPDATE_VERSION = release["latest"]
            except Exception as exc:
                messagebox.showerror("Unable to skip this version", str(exc), parent=root)
                return
            root.destroy()

        def open_release():
            try:
                if not webbrowser.open(release["url"], new=2):
                    raise RuntimeError("The web browser could not be opened.")
            except Exception as exc:
                messagebox.showerror("Unable to open GitHub", str(exc), parent=root)
                return
            root.destroy()

        ttk.Button(frame, text="Skip this version", command=skip_version).grid(
            row=2, column=0, padx=(0, 8), sticky="ew"
        )
        ttk.Button(frame, text="Open GitHub", command=open_release).grid(
            row=2, column=1, sticky="ew"
        )
        root.update_idletasks()
        root.geometry(
            f"+{max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)}"
            f"+{max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 2)}"
        )
        root.deiconify()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        if parent is None:
            root.mainloop()
        else:
            root.grab_set()
            root.focus_set()

    def check_for_startup_update():
        def worker():
            try:
                release = check_github_update()
            except Exception as exc:
                log(f"GitHub update check warning: {exc}")
                return
            if (APPLICATION_STOP_EVENT.is_set() or not
                    should_show_update_notification(release, SKIPPED_UPDATE_VERSION)):
                return
            try:
                run_update_notice_dialog(release)
            except Exception as exc:
                log(f"Unable to show update notice: {exc}")

        start_gui_worker(worker, "MarbleScapeStartupUpdate")

    def restart_from_tray(icon):
        try:
            restart_environment = os.environ.copy()
            if getattr(sys, "frozen", False):
                restart_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            restart_environment["MARBLESCAPE_RESTART_WAIT"] = "1"
            # The new instance ends this one should it still hang after RESTART_WAIT_SECONDS.
            restart_environment["MARBLESCAPE_RESTART_FROM_PID"] = str(os.getpid())

            subprocess.Popen(
                get_restart_arguments(),
                cwd=str(SCRIPT_DIR),
                close_fds=True,
                env=restart_environment,
            )
        except Exception as exc:
            show_tray_error(icon, "Unable to restart MarbleScape", exc)
            return False

        stop_application(icon)
        return True

    def restart_application(icon, item):
        del item
        restart_from_tray(icon)

    def open_project_url(url):
        allowed = {
            "https://github.com/Gittegatt/MarbleScape",
            "https://ko-fi.com/gittegatt",
            "https://paypal.me/gittegatt",
        }
        if url not in allowed:
            raise ValueError("Unsupported project link.")
        if not webbrowser.open(url, new=2):
            raise RuntimeError("The web browser could not be opened.")

    def run_support_dialog(parent=None, token=None):
        import tkinter as tk
        from tkinter import messagebox, ttk
        from PIL import ImageTk

        root = tk.Toplevel(parent) if parent is not None else create_tray_dialog_root(tk)
        original_destroy = root.destroy
        closed = False

        def destroy_support():
            nonlocal closed
            if closed:
                return
            closed = True
            # Release image objects on their owning GUI thread, before its interpreter closes.
            getattr(root, "_support_icons", {}).clear()
            getattr(root, "_marblescape_window_icons", []).clear()
            with support_dialog_lock:
                if support_dialog_state["token"] is token:
                    support_dialog_state.update(open=False, window=None, token=None)
            original_destroy()

        root.destroy = destroy_support
        root.protocol("WM_DELETE_WINDOW", destroy_support)
        root.bind("<Escape>", lambda _event: destroy_support())
        with support_dialog_lock:
            support_dialog_state["window"] = root

        def icon_color():
            """The text color of the current light or dark look, as RGB."""
            color = ttk.Style(root).lookup(".", "foreground") or "black"
            try:
                return tuple(value // 257 for value in root.winfo_rgb(color))
            except tk.TclError:
                return (0, 0, 0)

        def monochrome_icon(kind):
            # Never use Tk's implicit default root: another dialog may own that interpreter.
            return ImageTk.PhotoImage(support_icon(kind, icon_color()), master=root)

        def recolor_icons():
            """Redraw the icons in place when the window switches between light and dark."""
            color = icon_color()
            for kind, photo in getattr(root, "_support_icons", {}).items():
                photo.paste(support_icon(kind, color))

        def open_link(url):
            try:
                open_project_url(url)
            except Exception as exc:
                messagebox.showerror(
                    "Unable to open link", str(exc), parent=root
                )
            finally:
                if parent is None:
                    root.attributes("-topmost", False)

        choices = (
            ("Star on GitHub", "star", "https://github.com/Gittegatt/MarbleScape"),
            ("Ko-fi", "coffee", "https://ko-fi.com/gittegatt"),
            ("PayPal", "heart", "https://paypal.me/gittegatt"),
        )
        try:
            root.withdraw()
            if parent is not None:
                root.transient(parent)
            apply_tk_window_icon(root)
            root.title("Support this project")
            root.resizable(False, False)
            if parent is None:
                root.attributes("-topmost", True)
            frame = ttk.Frame(root, padding=14)
            frame.grid(row=0, column=0, sticky="nsew")
            ttk.Label(frame, text="Support MarbleScape", font=("TkDefaultFont", 11, "bold")).grid(
                row=0, column=0, sticky="w", pady=(0, 10))
            root._support_icons = {kind: monochrome_icon(kind) for kind in ("star", "coffee", "heart")}
            on_theme_change(frame, recolor_icons)
            for row, (label, icon_name, url) in enumerate(choices, start=2):
                ttk.Button(frame, text=label, image=root._support_icons[icon_name], compound="left",
                           command=lambda value=url: open_link(value), width=28).grid(
                               row=row, column=0, pady=3, sticky="ew")
            ttk.Button(frame, text="Close", command=destroy_support).grid(row=len(choices) + 2, column=0, pady=(10, 0), sticky="e")
            root.update_idletasks()
            if parent is not None:
                x = parent.winfo_rootx() + (parent.winfo_width() - root.winfo_reqwidth()) // 2
                y = parent.winfo_rooty() + (parent.winfo_height() - root.winfo_reqheight()) // 2
            else:
                x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
                y = (root.winfo_screenheight() - root.winfo_reqheight()) // 2
            root.geometry(f"+{max(0, x)}+{max(0, y)}")
            root.deiconify()
            root.lift()
            if parent is None:
                try:
                    root.mainloop()
                finally:
                    destroy_support()
            return root
        except Exception:
            destroy_support()
            raise

    def open_support_dialog(icon, item, *, parent=None):
        del item
        if parent is None:
            with settings_dialog_lock:
                if settings_dialog_state["support_parent_available"]:
                    # The Settings GUI thread consumes this request; no cross-thread Tk calls.
                    settings_dialog_state["support_requested"] = True
                    return
        with support_dialog_lock:
            if support_dialog_state["open"]:
                window = support_dialog_state["window"]
                token = None
            else:
                token = object()
                support_dialog_state.update(open=True, token=token)
                window = None
        if token is None:
            if parent is not None and window is not None and window.tk is parent.tk:
                window.lift()
            return

        if parent is not None:
            try:
                return run_support_dialog(parent, token)
            except Exception as exc:
                with support_dialog_lock:
                    if support_dialog_state["token"] is token:
                        support_dialog_state.update(open=False, window=None, token=None)
                from tkinter import messagebox
                messagebox.showerror("Unable to open project support", str(exc), parent=parent)
                return

        def worker():
            try:
                run_support_dialog(token=token)
            except Exception as exc:
                show_tray_error(icon, "Unable to open project support", exc)
            finally:
                with support_dialog_lock:
                    if support_dialog_state["token"] is token:
                        support_dialog_state.update(open=False, window=None, token=None)

        start_gui_worker(worker, "MarbleScapeSupport")

    def values_match(current, expected):
        if (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
        ):
            return math.isclose(float(current), float(expected), rel_tol=1e-9)
        return current == expected

    def request_runtime_configuration_reload(icon, *, load_image=True):
        if load_image:
            SETTINGS_ONLY_RELOAD_EVENT.clear()
        else:
            SETTINGS_ONLY_RELOAD_EVENT.set()
        CONFIGURATION_RELOAD_EVENT.set()
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

    def apply_settings_backup(icon, config_text, profile_library, startup_enabled):
        changed = restore_settings_backup(config_text, profile_library, startup_enabled)
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
                update_active_configuration(
                    lambda text: apply_preset_to_configuration(text, preset_name)
                )
            except Exception as exc:
                show_tray_error(icon, "Unable to change view preset", exc)
                return
            # Also unchanged: a picture an earlier Save scheduled loads now.
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
                update_active_configuration(
                    lambda text: replace_primary_wms_layer_name(text, layer_name)
                )
            except Exception as exc:
                show_tray_error(icon, "Unable to change satellite layer", exc)
                return
            # Also unchanged: a picture an earlier Save scheduled loads now.
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
        from marblescape_tabbar import ResponsiveNotebookTabs
        from marblescape_source_layout import COLOR_VALUE_WIDTH, SOURCE_COMBO_WIDTH, configure_source_columns
        from marblescape_source_settings import SourceSettings, still_zoom_lines
        from marblescape_catalogue_activity import CatalogueRefreshPanel

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.title("MarbleScape")
        published_images = PublishedImageIndex()
        root.resizable(True, True)

        def number_text(value):
            numeric = float(value)
            return str(int(numeric)) if numeric.is_integer() else str(numeric)

        def custom_area_display(preset, projection_name, bbox, zoom):
            """Return a saved Custom area with the Latitude/Longitude/Zoom texts Settings shows.

            The texts are rounded; while they stay unchanged, Save keeps the saved
            bbox and zoom exactly. A bbox in map units shows no centre.
            """
            if preset != "custom" or not bbox or len(bbox) != 4:
                return None
            if projection_name != "Geographic":
                return {"bbox": list(bbox), "zoom": zoom, "texts": ("", "", number_text(zoom))}
            latitude, longitude, shown_zoom = custom_area_center(bbox, zoom)
            return {"bbox": list(bbox), "zoom": zoom,
                    "texts": (number_text(round(latitude, 4)), number_text(round(longitude, 4)),
                              number_text(round(shown_zoom, 2)))}

        # "previous": the preset shown before the current one; Custom area starts from it.
        # "origin": the saved Custom area loaded into the form, or None for a new one.
        custom_area_state = {
            "previous": VIEW_PRESET,
            "origin": custom_area_display(VIEW_PRESET, PROJECTION, CUSTOM_BBOX, ZOOM),
        }

        def custom_area_form_values(values, preset):
            """Keep the saved Custom area unless Latitude, Longitude or Zoom was edited."""
            origin = custom_area_state["origin"]
            if preset != "custom" or origin is None:
                return
            if values["projection"] != "Geographic":
                # Map units from the TOML file: Zoom keeps its usual meaning.
                values["custom_bbox"] = origin["bbox"]
            elif (values["custom_latitude"], values["custom_longitude"], values["zoom"]) == origin["texts"]:
                values.update(custom_bbox=origin["bbox"], zoom=str(origin["zoom"]))

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
        current_update_interval_value, current_update_interval_unit = (
            update_interval_selection(UPDATE_INTERVAL_MINUTES)
        )
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
        def render_quality_label(current_value):
            """The preset label of a render quality.

            Only presets are offered. A factor saved by hand in the settings
            file shows as "Custom (1.75×)" and stays until another is chosen.
            """
            if current_value == "default":
                return RENDER_QUALITY_DEFAULT_LABEL
            preset = next(
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
                None,
            )
            return preset if preset is not None else f"Custom ({number_text(current_value)}×)"

        # General holds the display render quality; EUMETSAT its own or Default (General).
        current_render_quality_label = render_quality_label(DISPLAY_RENDER_SCALE)
        current_eumetsat_render_quality_label = render_quality_label(get_render_scale_setting())
        time_zone_label_to_value = dict(TIME_ZONE_MENU_CHOICES)
        current_time_zone_label = next(
            label for label, value in TIME_ZONE_MENU_CHOICES
            if value == DISPLAY_TIME_ZONE
        )

        variables = {
            "set_wallpaper": tk.BooleanVar(value=SET_WINDOWS_WALLPAPER),
            "position": tk.StringVar(value=WINDOWS_WALLPAPER_POSITION),
            "output_device": tk.StringVar(),
            "start_with_windows": tk.BooleanVar(
                value=is_windows_startup_enabled()
            ),
            "time_zone": tk.StringVar(value=current_time_zone_label),
            "appearance": tk.StringVar(value=APPEARANCE_LABELS[APPEARANCE]),
            "check_for_source_updates": tk.BooleanVar(
                value=CHECK_FOR_SOURCE_UPDATES
            ),
            "view_preset": tk.StringVar(value=current_preset_label),
            **{key: tk.StringVar(value=text) for (key, _label), text in zip(
                CUSTOM_AREA_FIELDS, (custom_area_state["origin"] or {"texts": ("", "")})["texts"])},
            "projection": tk.StringVar(value=get_active_view()[0]),
            "satellite_layer": tk.StringVar(value=current_layer_label),
            "fit_mode": tk.StringVar(value=VIEW_MODE),
            "zoom": tk.StringVar(value=(custom_area_state["origin"]["texts"][2]
                                        if custom_area_state["origin"] else number_text(ZOOM))),
            "truecolor_black_night": tk.BooleanVar(
                value=TRUECOLOR_BLACK_NIGHT
            ),
            "width": tk.StringVar(value=str(WIDTH)),
            "height": tk.StringVar(value=str(0 if HEIGHT is None else HEIGHT)),
            "aspect_ratio": tk.StringVar(value=str(ASPECT_RATIO or aspect_ratio_for_dimensions(*current_output_size))),
            "resolution_preset": tk.StringVar(
                value=current_resolution_label
            ),
            "aspect_ratio_preset": tk.StringVar(
                value=current_aspect_label
            ),
            "render_quality_preset": tk.StringVar(
                value=current_render_quality_label
            ),
            # The selected display's render quality in General > Monitor output;
            # with one display (or none identified) the shared one.
            "render_scale": tk.StringVar(
                value=(
                    DISPLAY_RENDER_SCALE
                    if isinstance(DISPLAY_RENDER_SCALE, str)
                    else number_text(DISPLAY_RENDER_SCALE)
                )
            ),
            # EUMETSAT's own field in Image > Rendering: default, auto or a factor.
            "eumetsat_render_quality_preset": tk.StringVar(
                value=current_eumetsat_render_quality_label
            ),
            "eumetsat_render_scale": tk.StringVar(
                value=(
                    get_render_scale_setting()
                    if isinstance(get_render_scale_setting(), str)
                    else number_text(get_render_scale_setting())
                )
            ),
            "background_color": tk.StringVar(
                value=normalize_background_color(BACKGROUND_COLOR)
            ),
            "update_interval_minutes": tk.StringVar(
                value=number_text(UPDATE_INTERVAL_MINUTES)
            ),
            "update_interval_value": tk.StringVar(
                value=current_update_interval_value
            ),
            "update_interval_unit": tk.StringVar(
                value=current_update_interval_unit
            ),
            "show_download_speed": tk.BooleanVar(value=SHOW_DOWNLOAD_SPEED),
            "download_speed_unit": tk.StringVar(
                value="Automatic" if DOWNLOAD_SPEED_UNIT == "automatic" else DOWNLOAD_SPEED_UNIT
            ),
            "download_retries": tk.StringVar(value=str(DOWNLOAD_RETRIES)),
            "catalogue_retries": tk.StringVar(value=str(CATALOGUE_RETRIES)),
            "catalogue_refresh_time": tk.StringVar(value=CATALOGUE_REFRESH_TIME),
            "show_download_progress": tk.BooleanVar(value=SHOW_DOWNLOAD_PROGRESS),
            "show_download_size": tk.BooleanVar(value=SHOW_DOWNLOAD_SIZE),
            "show_download_progress_bar": tk.BooleanVar(value=SHOW_DOWNLOAD_PROGRESS_BAR),
            "keep_completed_download_visible": tk.BooleanVar(
                value=KEEP_COMPLETED_DOWNLOAD_VISIBLE
            ),
            "history_enabled": tk.BooleanVar(value=ENABLE_HISTORY),
            "retention_mode": tk.StringVar(value=HISTORY_RETENTION_MODE),
            "max_files": tk.StringVar(value=str(HISTORY_MAX_FILES)),
            "years": tk.StringVar(value=str(HISTORY_RETENTION_YEARS)),
            "months": tk.StringVar(value=str(HISTORY_RETENTION_MONTHS)),
            "days": tk.StringVar(value=str(HISTORY_RETENTION_DAYS)),
            "hours": tk.StringVar(value=str(HISTORY_RETENTION_HOURS)),
            "minutes": tk.StringVar(value=str(HISTORY_RETENTION_MINUTES)),
            "profile_cache_max_size_gb": tk.DoubleVar(value=PROFILE_CACHE_MAX_SIZE_GB),
            "profile_cache_variants": tk.IntVar(value=PROFILE_CACHE_VARIANTS),
            "profile_cache_snapshot_size_gb": tk.DoubleVar(value=PROFILE_CACHE_SNAPSHOT_SIZE_GB),
        }
        status_variables = {
            "image_source": tk.StringVar(),
            "activity": tk.StringVar(),
            "latest_images": tk.StringVar(),
            "current_image_size": tk.StringVar(),
            "estimated_history_images": tk.StringVar(),
            "estimated_maximum_total": tk.StringVar(),
            "total_storage_estimate": tk.StringVar(),
            "currently_used_disk_space": tk.StringVar(),
            "profile_cache": tk.StringVar(),
            "snapshot_cache": tk.StringVar(),
            "cache_action": tk.StringVar(),
            "history_action": tk.StringVar(),
            "next_check": tk.StringVar(),
            "download": tk.StringVar(),
        }
        variables["latest_folder"] = tk.StringVar(value=CUSTOM_LATEST_FOLDER)
        variables["history_folder"] = tk.StringVar(value=CUSTOM_HISTORY_FOLDER)
        variables["profile_history_policies"] = tk.StringVar(value=json.dumps(PROFILE_HISTORY_POLICIES, sort_keys=True))
        image_form_state = {"base": image_settings_snapshot(), "loaded": False,
                            "profile_id": APPLIED_PROFILE_ID}

        container = ttk.Frame(root, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)
        notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        # The theme's thin line separates the scrolling tab content from the footer.
        footer_divider = ttk.Separator(container, orient="horizontal")
        footer_divider.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        scroll_pages = {}

        def add_settings_tab(title):
            page = ttk.Frame(notebook)
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            canvas = tk.Canvas(
                page, highlightthickness=0, borderwidth=0,
                background=ttk.Style(root).lookup("TFrame", "background") or "#f0f0f0",
            )
            keep_theme_background(canvas)
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            scrollbar.grid(row=0, column=1, sticky="ns")
            canvas.configure(yscrollcommand=scrollbar.set)
            content = ttk.Frame(canvas, padding=(0, 8, 8, 0))
            content.columnconfigure(0, weight=1)
            window = canvas.create_window(0, 0, window=content, anchor="nw")

            def update_scroll_region(_event=None):
                top = max(0, canvas.canvasy(0))
                width = max(1, canvas.winfo_width())
                height = content.winfo_reqheight()
                canvas.itemconfigure(window, width=width)
                canvas.configure(scrollregion=(0, 0, width, height))
                # Preserve the pixel offset as source fields and labels change.
                # Reserving the scrollbar avoids repeated width/reflow changes.
                top = min(top, max(0, height - canvas.winfo_height()))
                canvas.yview_moveto(top / max(1, height))

            content.bind("<Configure>", update_scroll_region)
            canvas.bind("<Configure>", update_scroll_region)
            notebook.add(page, text=title)
            scroll_pages[str(page)] = (canvas, content)
            return content

        general_tab = add_settings_tab("General")
        download_tab = add_settings_tab("Downloads & Updates")
        image_tab = add_settings_tab("Image")
        profiles_tab = add_settings_tab("Profiles")
        history_tab = add_settings_tab("History & Storage")
        backup_tab = add_settings_tab("Backup")
        sources_tab = add_settings_tab("Sources")
        access_tab = add_settings_tab("Access")
        about_tab = add_settings_tab("About")
        info_tab = add_settings_tab("Info")
        tab_strip = ResponsiveNotebookTabs(container, notebook)
        tab_strip.frame.grid(row=0, column=0, sticky="ew")

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
            if getattr(event, "num", None) in (4, 5):
                units = -1 if event.num == 4 else 1
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return
                units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            if isinstance(event.widget, ttk.Treeview) and event.widget.yview() != (0.0, 1.0):
                event.widget.yview_scroll(units * 3, "units")
                return "break"
            if canvas.yview() == (0.0, 1.0):
                return "break"
            canvas.yview_scroll(units * 3, "units")
            return "break"

        def reveal_focused_setting():
            selected = scroll_pages.get(notebook.select())
            if selected is None:
                return
            canvas, content = selected
            widget = root.focus_get()
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
        # Mouse clicks and dropdown focus changes must not move the viewport.
        # Only keyboard traversal brings an offscreen control into view.
        root.bind_class(scroll_tag, "<KeyPress-Tab>",
                        lambda _event: root.after_idle(reveal_focused_setting))
        root.bind_class(scroll_tag, "<KeyPress-ISO_Left_Tab>",
                        lambda _event: root.after_idle(reveal_focused_setting))

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

        def add_folder_picker(parent, row, label, key, default_folder, open_label, open_subfolder=None):
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
                if not configured:
                    try:
                        initial.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
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

            def open_folder():
                try:
                    configured = variables[key].get().strip()
                    folder = (
                        resolve_script_relative_path(configured)
                        if configured else default_folder
                    )
                    if open_subfolder:
                        folder = folder / open_subfolder
                    folder.mkdir(parents=True, exist_ok=True)
                    os.startfile(str(folder.resolve()))
                except Exception as exc:
                    messagebox.showerror(
                        f"Unable to open {label.lower()}", str(exc), parent=root
                    )

            ttk.Button(field, text=open_label, command=open_folder).grid(
                row=1, column=1, pady=(4, 0), sticky="e"
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

        # The preset dropdowns by variable prefix ("" General, "eumetsat_" EUMETSAT).
        render_quality_combos = {}

        def render_quality_choices(prefix, current_label=None):
            """The presets; a factor saved by hand in the file is listed while it is chosen."""
            choices = [*((RENDER_QUALITY_DEFAULT_LABEL,) if prefix == "eumetsat_" else ()),
                       *render_quality_label_to_value]
            if current_label and current_label not in choices:
                choices.append(current_label)
            return tuple(choices)

        def refresh_render_quality_preset(*_args, prefix=""):
            if render_quality_update["active"]:
                return
            try:
                current_value = parse_render_scale_setting(
                    variables[prefix + "render_scale"].get(),
                    # Only EUMETSAT's own value can follow General's.
                    allow_default=prefix == "eumetsat_",
                )
            except ValueError:
                # Not reachable from the dropdown; never shows an invalid value.
                return
            label = render_quality_label(current_value)
            variables[prefix + "render_quality_preset"].set(label)
            if prefix in render_quality_combos:
                render_quality_combos[prefix].configure(values=render_quality_choices(prefix, label))

        def select_render_quality_preset(_event=None, prefix=""):
            selected = variables[prefix + "render_quality_preset"].get()
            if selected not in render_quality_label_to_value and selected != RENDER_QUALITY_DEFAULT_LABEL:
                return  # The kept hand-set factor: nothing to change.
            render_quality_update["active"] = True
            try:
                value = ("default" if selected == RENDER_QUALITY_DEFAULT_LABEL
                         else render_quality_label_to_value[selected])
                variables[prefix + "render_scale"].set(
                    value if isinstance(value, str) else number_text(value)
                )
            finally:
                render_quality_update["active"] = False
            refresh_render_quality_preset(prefix=prefix)

        def choose_backup_export(include_profiles=True):
            try:
                initial_directory = export_locations.initial_directory("settings")
            except OSError as exc:
                messagebox.showerror("Unable to open backup folder", str(exc), parent=root)
                return
            selected_path = filedialog.asksaveasfilename(
                parent=root,
                title="Export MarbleScape settings backup",
                initialdir=str(initial_directory),
                initialfile=settings_backup_filename(include_profiles),
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                saved_path = export_settings_backup(selected_path, include_profiles=include_profiles)
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
            if not export_locations.remember("settings", saved_path.parent):
                messagebox.showwarning("Export folder", "The backup was exported, but the folder could not be remembered for next time.", parent=root)

        def choose_backup_import(include_profiles=True):
            selected_path = filedialog.askopenfilename(
                parent=root,
                title="Import MarbleScape settings backup",
                initialdir=str(settings_backup_directory()),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                config_text, profile_library, startup_enabled = import_settings_backup(
                    selected_path, include_profiles=include_profiles,
                    confirm_repair=lambda label, changes: confirm_import_repair(root, label, changes, allow_skip=False)
                )
            except ImportCancelled:
                return
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return

            confirmed = messagebox.askyesno(
                "Import settings backup",
                ("Replace settings AND ALL profiles (including rotation) and apply immediately? Existing profiles will be replaced."
                 if include_profiles else "Replace settings and apply immediately? Existing profiles and rotation will remain unchanged."),
                parent=root,
                icon="warning",
            )
            if not confirmed:
                return

            try:
                apply_settings_backup(
                    icon, config_text, profile_library, startup_enabled
                )
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
            custom_area_state["previous"] = "full_earth"
            variables["zoom"].set(str(updates["zoom"]))
            variables["fit_mode"].set(updates["fit_mode"])

        def refresh_projection_choices():
            choices = available_projection_choices()
            projection_combo.configure(values=choices)
            if variables["projection"].get() not in choices:
                variables["projection"].set("GEOS: MSG FES, MTG FD")
                select_projection()

        def select_custom_area(previous):
            if previous == "custom":
                return
            custom_area_state["origin"] = None
            bbox = VIEW_PRESETS.get(previous, {}).get("bbox")
            if bbox is None:
                # From Full Earth: the whole world map.
                latitude, longitude, zoom = 0.0, 0.0, 1.0
            else:
                try:
                    current_zoom = float(variables["zoom"].get())
                except ValueError:
                    current_zoom = DEFAULT_ZOOM
                if not math.isfinite(current_zoom) or current_zoom <= 0:
                    current_zoom = DEFAULT_ZOOM
                latitude, longitude, zoom = custom_area_center(bbox, current_zoom)
            variables["custom_latitude"].set(number_text(round(latitude, 4)))
            variables["custom_longitude"].set(number_text(round(longitude, 4)))
            variables["zoom"].set(number_text(round(zoom, 2)))
            variables["projection"].set("Geographic")
            # The saved area already has the output ratio; Crop never reaches
            # past the map edge on monitors with another ratio.
            variables["fit_mode"].set("crop")

        def select_view_preset(event=None):
            del event
            preset_name = preset_label_to_value[variables["view_preset"].get()]
            previous = custom_area_state["previous"]
            custom_area_state["previous"] = preset_name
            if preset_name == "custom":
                select_custom_area(previous)
                return
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
            try:
                source_settings.select_eumetsat_layer(profile["satellite_layer"])
            except NameError:
                pass

        try:
            output_monitors = list_windows_wallpaper_monitors()
        except Exception as exc:
            log(f"Unable to list output devices: {exc}")
            output_monitors = []
        # Only real displays; the shared settings behind them apply to displays
        # connected later. Without an identified display they stay editable.
        all_displays_label = "All displays"
        output_device_ids = {}
        for index, monitor in enumerate(output_monitors, start=1):
            left, top, right, bottom = monitor["rect"]
            label = f"Display {index} ({right - left} × {bottom - top})"
            output_device_ids[label] = monitor["id"]
        if not output_device_ids:
            output_device_ids[all_displays_label] = None
        variables["output_device"].set(next(iter(output_device_ids)))
        monitor_positions_draft = dict(WINDOWS_WALLPAPER_MONITOR_POSITIONS)
        paused_displays_draft = set(WINDOWS_WALLPAPER_PAUSED)
        pause_variable = tk.BooleanVar(master=root)
        monitor_outputs_draft = deepcopy(WINDOWS_WALLPAPER_MONITOR_OUTPUTS)
        global_position_draft = {"value": WINDOWS_WALLPAPER_POSITION}
        global_output_draft = {
            key: variables[key].get() for key in MONITOR_OUTPUT_FIELDS
        }
        switching_output_device = {"active": False}
        active_output_device = {"id": None}
        position_display = tk.StringVar(
            value=WALLPAPER_POSITION_LABELS[variables["position"].get()]
        )

        def update_position_display(*_args):
            position_display.set(WALLPAPER_POSITION_LABELS[variables["position"].get()])

        def shared_editing():
            """With one display (or none identified) its settings are the shared ones."""
            return sum(1 for identifier in output_device_ids.values() if identifier) <= 1

        variables["position"].trace_add("write", update_position_display)

        def select_output_device(_event=None):
            monitor_id = output_device_ids[variables["output_device"].get()]
            for button in (restore_wallpaper_button, apply_all_displays_button, pause_button):
                button.configure(state="normal" if monitor_id is not None else "disabled")
            switching_output_device["active"] = True
            try:
                active_output_device["id"] = monitor_id
                pause_variable.set(monitor_id is not None and display_paused(monitor_id, paused_displays_draft))
                variables["position"].set(
                    monitor_positions_draft.get(monitor_id, global_position_draft["value"])
                    if monitor_id else global_position_draft["value"]
                )
                overrides = monitor_outputs_draft.get(monitor_id, {}) if monitor_id else {}
                for key in MONITOR_OUTPUT_FIELDS:
                    variables[key].set(str(overrides.get(key, global_output_draft[key])))
            finally:
                switching_output_device["active"] = False

        def select_wallpaper_position(*_args):
            if switching_output_device["active"]:
                return
            monitor_id = active_output_device["id"]
            value = variables["position"].get()
            if monitor_id and not shared_editing():
                if value == global_position_draft["value"]:
                    monitor_positions_draft.pop(monitor_id, None)
                else:
                    monitor_positions_draft[monitor_id] = value
            else:
                global_position_draft["value"] = value
                if monitor_id:
                    monitor_positions_draft.pop(monitor_id, None)

        def select_monitor_output_setting(key):
            if switching_output_device["active"]:
                return
            monitor_id = active_output_device["id"]
            keys = ("width", "height", "aspect_ratio") if key in {
                "width", "height", "aspect_ratio"
            } else (key,)
            if monitor_id is None or shared_editing():
                for field in keys:
                    global_output_draft[field] = variables[field].get()
                if monitor_id is not None and monitor_id in monitor_outputs_draft:
                    for field in keys:
                        monitor_outputs_draft[monitor_id].pop(field, None)
                    if not monitor_outputs_draft[monitor_id]:
                        monitor_outputs_draft.pop(monitor_id)
                return
            overrides = monitor_outputs_draft.setdefault(monitor_id, {})
            if len(keys) == 3:
                if all(variables[field].get() == str(global_output_draft[field])
                       for field in keys):
                    for field in keys:
                        overrides.pop(field, None)
                else:
                    for field in keys:
                        overrides[field] = variables[field].get()
            else:
                value = variables[key].get()
                if value == str(global_output_draft[key]):
                    overrides.pop(key, None)
                else:
                    overrides[key] = value
            if not overrides:
                monitor_outputs_draft.pop(monitor_id, None)

        variables["position"].trace_add("write", select_wallpaper_position)
        for key in MONITOR_OUTPUT_FIELDS:
            variables[key].trace_add(
                "write", lambda *_args, field=key: select_monitor_output_setting(field)
            )

        wallpaper_frame = ttk.LabelFrame(general_tab, text="Startup and wallpaper", padding=8)
        wallpaper_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            wallpaper_frame,
            text="Set wallpaper automatically",
            variable=variables["set_wallpaper"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            wallpaper_frame,
            text="Start minimized with Windows",
            variable=variables["start_with_windows"],
        ).grid(row=1, column=0, columnspan=2, pady=3, sticky="w")

        # Appearance comes before Actions, which closes the General tab.
        appearance_frame = ttk.LabelFrame(general_tab, text="Appearance", padding=8)
        appearance_frame.grid(row=6, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            appearance_frame, 0, "Mode", variables["appearance"],
            tuple(APPEARANCE_LABELS.values()), width=GENERAL_COMBO_WIDTH,
        )
        ttk.Label(appearance_frame, wraplength=640, text=(
            "System follows the Windows app mode (light or dark). Save or OK applies a new choice."
        )).grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")

        # Actions closes the General tab: the log, and ending or restarting MarbleScape.
        actions_frame = ttk.LabelFrame(general_tab, text="Actions", padding=8, name="actions")
        actions_frame.grid(row=7, column=0, pady=(0, 8), sticky="ew")

        def show_log():
            path = log_file_path()
            if not path.exists():
                messagebox.showinfo("Show log", "MarbleScape has not written a log yet.", parent=root)
                return
            try:
                os.startfile(str(path))
            except Exception as exc:
                messagebox.showerror("Unable to open the log", str(exc), parent=root)

        def leave_application(restart):
            action = "Restart" if restart else "Exit"
            try:
                pending = save_is_pending()
            except Exception:
                pending = True
            if pending and not messagebox.askokcancel(
                    f"{action} MarbleScape",
                    f"Settings has unsaved changes. {action} without saving them?",
                    icon="warning", parent=root):
                return
            if restart:
                restart_from_tray(icon)
            else:
                stop_application(icon)

        # Left-aligned and equally wide (uniform columns), in a row of their own so
        # the text below cannot widen them.
        actions_row = ttk.Frame(actions_frame, name="buttons")
        actions_row.grid(row=0, column=0, sticky="w")
        for column, (text, name, command) in enumerate((
                ("Show log", "show_log", show_log),
                ("Restart", "restart", lambda: leave_application(True)),
                ("Exit", "exit", lambda: leave_application(False)))):
            actions_row.columnconfigure(column, uniform="actions")
            ttk.Button(actions_row, text=text, name=name, command=command).grid(
                row=0, column=column, padx=(0, 6), sticky="ew")
        ttk.Label(actions_frame, wraplength=640, text=(
            "Show log opens marblescape.log from the content folder. Exit ends MarbleScape; "
            "Restart starts it again. Both ask first while Settings has unsaved changes."
        )).grid(row=1, column=0, pady=(4, 0), sticky="w")

        display_time_frame = ttk.LabelFrame(general_tab, text="Date and time", padding=8)
        # Date and time sits right above Appearance, which closes the tab.
        display_time_frame.grid(row=5, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            display_time_frame, 0, "Time zone", variables["time_zone"],
            tuple(time_zone_label_to_value), width=GENERAL_COMBO_WIDTH,
        )
        ttk.Label(display_time_frame, wraplength=640, text=(
            "System time uses the Windows time zone and daylight-saving rules. "
            "Provider and cache timestamps remain stored in UTC."
        )).grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")

        output_device_frame = ttk.LabelFrame(general_tab, text="Output device", padding=8)
        output_device_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        output_device_combo = add_combo(
            output_device_frame, 0, "Display", variables["output_device"],
            tuple(output_device_ids), width=GENERAL_COMBO_WIDTH,
        )
        output_device_combo.bind("<<ComboboxSelected>>", select_output_device)

        def refresh_output_device_choices():
            try:
                attached = list_windows_wallpaper_monitors()
            except Exception as exc:
                log(f"Unable to refresh output devices: {exc}")
                return
            choices = {}
            for index, monitor in enumerate(attached, start=1):
                left, top, right, bottom = monitor["rect"]
                choices[f"Display {index} ({right - left} × {bottom - top})"] = monitor["id"]
            if not choices:
                choices[all_displays_label] = None
            if choices == output_device_ids:
                return
            selected_id = active_output_device["id"]
            output_device_ids.clear()
            output_device_ids.update(choices)
            output_device_combo.configure(values=tuple(choices))
            selected_label = next(
                (label for label, monitor_id in choices.items() if monitor_id == selected_id),
                next(iter(choices)),
            )
            if selected_label != variables["output_device"].get():
                variables["output_device"].set(selected_label)
                select_output_device()

        output_device_combo.configure(postcommand=refresh_output_device_choices)
        def restore_selected_wallpaper():
            monitor_id = active_output_device["id"]
            if monitor_id is None:
                return
            try:
                save_was_pending = save_is_pending()
            except Exception:
                save_was_pending = True
            try:
                restore_previous_wallpaper(monitor_id)
            except FileNotFoundError as exc:
                messagebox.showinfo("Restore previous wallpaper", str(exc), parent=root)
                return
            except Exception as exc:
                messagebox.showerror("Unable to restore wallpaper", str(exc), parent=root)
                return
            # The restore saved the pause for this display itself.
            paused_displays_draft.add(monitor_id)
            switching_output_device["active"] = True
            try:
                pause_variable.set(True)
            finally:
                switching_output_device["active"] = False
            # The restore is saved at once (the pause of that display);
            # it is no draft waiting for Save.
            if not save_was_pending:
                save_baseline["draft"] = None
                update_apply_button()
            request_runtime_configuration_reload(icon)

        def apply_output_to_all_displays():
            """Make the selected display's settings the shared ones of every display.

            Displays connected later use them too. A draft until Save, like the
            other General settings.
            """
            monitor_id = active_output_device["id"]
            if monitor_id is None:
                return
            global_position_draft["value"] = variables["position"].get()
            monitor_positions_draft.clear()
            for key in MONITOR_OUTPUT_FIELDS:
                global_output_draft[key] = variables[key].get()
            monitor_outputs_draft.clear()
            select_output_device()
            root.event_generate("<<SettingsDraftChanged>>")

        def toggle_display_pause():
            """Pause wallpaper updates: this display keeps its picture; its position stays."""
            if switching_output_device["active"]:
                return
            monitor_id = active_output_device["id"]
            if monitor_id is None:
                return
            if pause_variable.get():
                paused_displays_draft.add(monitor_id)
            else:
                if PAUSE_ALL_DISPLAYS in paused_displays_draft:
                    # An older shared pause: the other displays stay paused.
                    paused_displays_draft.discard(PAUSE_ALL_DISPLAYS)
                    paused_displays_draft.update(
                        identifier for identifier in output_device_ids.values()
                        if identifier and identifier != monitor_id)
                paused_displays_draft.discard(monitor_id)
            root.event_generate("<<SettingsDraftChanged>>")

        # Right below the dropdown: the display's pause, then both actions.
        pause_button = ttk.Checkbutton(
            output_device_frame, text="Pause wallpaper updates", variable=pause_variable,
            command=toggle_display_pause,
        )
        pause_button.grid(row=1, column=1, pady=(5, 0), sticky="w")
        output_device_actions = ttk.Frame(output_device_frame)
        output_device_actions.grid(row=2, column=1, pady=(5, 0), sticky="w")
        output_device_actions.columnconfigure((0, 1), weight=1, uniform="output_device_actions")
        apply_all_displays_button = ttk.Button(
            output_device_actions, text="Apply to all displays",
            command=apply_output_to_all_displays,
        )
        # The gap is split, so both columns hold equally wide buttons.
        apply_all_displays_button.grid(row=0, column=0, padx=(0, 3), sticky="ew")
        restore_wallpaper_button = ttk.Button(
            output_device_actions, text="Restore previous wallpaper",
            command=restore_selected_wallpaper,
        )
        restore_wallpaper_button.grid(row=0, column=1, padx=(3, 0), sticky="ew")
        restore_wallpaper_button.configure(state="disabled")

        output_frame = ttk.LabelFrame(general_tab, text="Monitor output", padding=8)
        output_frame.grid(row=3, column=0, pady=(0, 8), sticky="ew")
        resolution_combo = add_combo(
            output_frame,
            0,
            "Monitor resolution preset",
            variables["resolution_preset"],
            (*resolution_label_to_size, "Custom"),
            width=GENERAL_COMBO_WIDTH,
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
            width=GENERAL_COMBO_WIDTH,
        )
        aspect_combo.bind(
            "<<ComboboxSelected>>",
            select_aspect_ratio_preset,
        )
        add_entry(output_frame, 2, "Width", variables["width"])
        add_entry(output_frame, 3, "Height (0 = auto)", variables["height"])
        add_entry(output_frame, 4, "Aspect ratio", variables["aspect_ratio"])
        # Presets only: a dropdown cannot hold an empty or invalid factor.
        render_quality_combo = add_combo(
            output_frame,
            5,
            "Render quality factor",
            variables["render_quality_preset"],
            render_quality_choices(""),
            width=GENERAL_COMBO_WIDTH,
        )
        render_quality_combo.bind(
            "<<ComboboxSelected>>",
            select_render_quality_preset,
        )
        render_quality_combos[""] = render_quality_combo
        refresh_render_quality_preset()
        ttk.Label(output_frame, text="Background color").grid(
            row=7, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        # Like the Copernicus color rows: swatch, Choose color..., value.
        color_frame = ttk.Frame(output_frame)
        color_frame.grid(row=7, column=1, pady=3, sticky="ew")
        background_swatch = tk.Label(color_frame, width=2, height=1)
        style_swatch(background_swatch)
        background_swatch.pack(side="left", padx=(0, 7))
        ttk.Button(
            color_frame,
            text="Choose color…",
            command=choose_background_color,
        ).pack(side="left")
        ttk.Label(color_frame, textvariable=variables["background_color"],
                  width=COLOR_VALUE_WIDTH).pack(side="left", padx=8)

        def refresh_background_swatch(*_args):
            value = variables["background_color"].get()
            background_swatch.configure(
                background=value if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) else "#000000")

        variables["background_color"].trace_add("write", refresh_background_swatch)
        # Loading the theme resets plain tk colors; the swatch shows the chosen one again.
        on_theme_change(background_swatch, refresh_background_swatch)
        refresh_background_swatch()
        for key in ("width", "height", "aspect_ratio"):
            variables[key].trace_add("write", refresh_output_preset_labels)
        variables["render_scale"].trace_add(
            "write",
            refresh_render_quality_preset,
        )
        # "Do not update" became Output device > Pause wallpaper updates.
        position_combo = add_combo(
            output_frame, 8, "Position", position_display,
            tuple(label for name, label in WALLPAPER_POSITION_LABELS.items() if name != "none"),
            width=GENERAL_COMBO_WIDTH,
        )
        position_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: variables["position"].set(next(
                name for name, label in WALLPAPER_POSITION_LABELS.items()
                if label == position_display.get()
            )),
        )
        ttk.Label(output_frame, wraplength=640, text=(
            "Choose each display under Output device and set its position under Output. "
            "Pause wallpaper updates keeps a display's current picture while downloads continue. "
            "Restore previous wallpaper uses a saved copy when one is available."
        )).grid(row=9, column=0, columnspan=2, pady=(4, 0), sticky="w")

        # Monitor output shows the same rows for every image source.
        # Open on the first display.
        select_output_device()

        def source_changed(provider):
            if source_settings.view_defaults_requested:
                variables["zoom"].set(number_text(DEFAULT_ZOOM))
            try:
                refresh_zoom_choices()
            except NameError:
                pass  # The Zoom dropdowns are built after the source settings.

        def copernicus_output_size():
            try:
                width = int(global_output_draft["width"])
                configured_height = int(global_output_draft["height"])
                ratio = parse_aspect_ratio(global_output_draft["aspect_ratio"])
                height = configured_height or round(width / ratio)
                if width <= 0 or height <= 0:
                    raise ValueError
                return copernicus_auto_size((width, height), monitor_outputs_draft, output_monitors)
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                return get_output_dimensions()

        def copernicus_reference_date():
            selected_zone = time_zone_label_to_value.get(
                variables["time_zone"].get(), DISPLAY_TIME_ZONE,
            )
            return (dt.datetime.now(dt.timezone.utc).date()
                    if selected_zone == "utc" else dt.date.today())

        # The header: the profile name as the frame title, its short ID right-aligned
        # on the same top edge, the picture on screen and, for Copernicus, its auto
        # recommendation inside.
        profile_display = tk.StringVar(master=root, value=latest_snapshot.SYSTEM_NAME)
        header_frame = ttk.LabelFrame(image_tab, text=profile_display.get(), padding=(8, 2, 8, 6),
                                      name="image_header")
        header_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        header_frame.columnconfigure(0, weight=1)
        profile_display.trace_add("write", lambda *_args: header_frame.configure(text=profile_display.get()))
        # In the title's style, as far from the right edge as the title from the left.
        header_short_id = tk.StringVar(master=root, value="")
        ttk.Label(header_frame, textvariable=header_short_id, name="short_id", style="TLabelframe.Label").place(
            relx=1.0, x=-6, y=0, anchor="ne", bordermode="outside")
        # Access: what an image source needs to be reached, chosen by source.
        # Copernicus Browser needs OAuth credentials; further sources that need
        # access join ACCESS_PANELS.
        access_frame = ttk.LabelFrame(access_tab, text="Image source access", padding=8)
        access_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        access_frame.columnconfigure(1, weight=1)
        access_source_var = tk.StringVar(master=root, value="Copernicus Browser")
        access_combo = add_combo(access_frame, 0, "Image source", access_source_var,
                                 ("Copernicus Browser",), width=28)
        access_host = ttk.Frame(access_frame)
        access_host.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="ew")
        access_host.columnconfigure(0, weight=1)
        source_settings = SourceSettings(
            image_tab, IMAGE_SOURCE, SOURCE_PROFILES,
            timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT,
            on_change=source_changed, client=get_catalogue_client(),
            copernicus_auth={"client_id": COPERNICUS_CLIENT_ID,
                             "client_secret": COPERNICUS_CLIENT_SECRET},
            output_size=copernicus_output_size,
            reference_date=copernicus_reference_date,
            eumetsat_layer=selected_wms_layer,
            account_parent=access_host,
            credits_parent=access_tab,
            location_search_endpoint=LOCATION_SEARCH_ENDPOINT,
        )
        # Copernicus credits close the Access tab as an own section.
        source_settings.copernicus_settings.credits_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        source_settings.frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        # Rendering right below Source; Recommendation (Copernicus) after it, above Find location.
        source_settings.rendering_frame.grid(row=3, column=0, pady=(0, 8), sticky="ew")
        source_settings.grid_recommend_section(row=4, column=0, pady=(0, 8), sticky="ew")
        # Find location above Imagery updates, only for sources with latitude/longitude.
        source_settings.grid_location_section(row=5, column=0, pady=(0, 8), sticky="ew")
        # Catalogue refresh closes the Image tab, below Imagery updates.
        source_settings.catalogue_frame.grid(row=7, column=0, pady=(0, 8), sticky="ew")
        shown_tones = {"key": None}

        def refresh_shown_tones():
            """Give the Copernicus auto sliders the values of the picture on screen."""
            path = get_current_image_path()
            try:
                key = (path, path.stat().st_mtime_ns) if path is not None else None
            except OSError:
                key = None
            if key == shown_tones["key"]:
                return
            shown_tones["key"] = key
            profile = adjustments = None
            if path is not None:
                try:
                    from PIL import Image
                    with Image.open(path) as picture:
                        record = json.loads(picture.text.get("MarbleScape", "") or "null")
                    settings = record.get("profile_settings") if isinstance(record, dict) else None
                    if isinstance(settings, dict) and (settings.get("source") or {}).get("provider") == "copernicus":
                        profile = (settings.get("sources") or {}).get("copernicus")
                        adjustments = record.get("mosaic_adjustments")
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
            source_settings.copernicus_settings.show_picture(profile, adjustments)

        refresh_shown_tones()
        generic_view_frame = source_settings.generic_view_frame
        add_combo(
            generic_view_frame, 0, "Fit mode", variables["fit_mode"], ("fit", "crop"),
            width=SOURCE_COMBO_WIDTH,
        )
        # Zoom: a dropdown of steps for the source (see zoom_choices); the text
        # of variables["zoom"] stays the value that is saved.
        zoom_display = tk.StringVar(master=root)
        zoom_lookup = {}
        zoom_combos = []
        zoom_refresh = {"active": False}

        def select_zoom(_event=None):
            number = zoom_lookup.get(zoom_display.get())
            if number is not None:
                variables["zoom"].set(number_text(number))

        def add_zoom_combo(parent, row):
            combo = add_combo(parent, row, "Zoom", zoom_display, (), width=SOURCE_COMBO_WIDTH)
            combo.bind("<<ComboboxSelected>>", select_zoom)
            zoom_combos.append(combo)
            return combo

        add_zoom_combo(generic_view_frame, 1)
        # Two lines below Zoom: the result for the chosen zoom (orange when the
        # picture is enlarged), then the facts behind it.
        still_zoom_label = ttk.Frame(generic_view_frame, name="still_zoom_hint")
        still_zoom_label.grid(row=2, column=1, pady=(0, 3), sticky="w")
        still_zoom_result = ttk.Label(still_zoom_label, name="result")
        still_zoom_result.grid(row=0, column=0, sticky="w")
        still_zoom_facts = ttk.Label(still_zoom_label, name="facts")
        still_zoom_facts.grid(row=1, column=0, sticky="w")

        def draft_output_size():
            try:
                width = int(global_output_draft["width"])
                configured_height = int(global_output_draft["height"])
                height = configured_height or round(width / parse_aspect_ratio(global_output_draft["aspect_ratio"]))
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                return None
            return (width, height) if width > 0 and height > 0 else None

        def refresh_still_zoom_hint(*_args):
            size = draft_output_size()
            if size is not None:
                try:
                    size = displays_image_size(source_settings.provider, size, monitor_outputs_draft,
                                               paused_displays_draft)
                except Exception:
                    pass
            fit_mode, storm = variables["fit_mode"].get(), {}
            if (size is not None and source_settings.provider == "himawari"
                    and is_himawari_storm_area(source_settings.selected_area())):
                # As the download: a storm is shown storm-sized, on top of Zoom.
                fit_mode = "fit"
                storm = {"storm_view_km": HIMAWARI_STORM_VIEW_KM,
                         "view_scale": himawari_storm_view_zoom(size, 1)}
            lines = still_zoom_lines(source_settings.source_image_size(), size, fit_mode,
                                     zoom=variables["zoom"].get(), **storm)
            if not lines:
                still_zoom_label.grid_remove()
                return
            result, facts, enlarged = lines
            still_zoom_result.configure(
                text=result, foreground=palette(still_zoom_result)["warning"] if enlarged else "")
            still_zoom_facts.configure(text=facts)
            still_zoom_label.grid()

        on_theme_change(still_zoom_result, refresh_still_zoom_hint)

        def automatic_resolution_for_draft(resolutions):
            """The key Automatic takes among (key, width, height) with the Image tab's draft."""
            try:
                zoom = float(variables["zoom"].get())
            except (TypeError, ValueError):
                zoom = 1.0
            size = automatic_source_output_size(draft_output_size() or get_output_dimensions())
            fit_mode = variables["fit_mode"].get()
            if source_settings.provider == "himawari" and is_himawari_storm_area(source_settings.selected_area()):
                # As the download does: a storm is shown storm-sized.
                fit_mode, zoom = "fit", himawari_storm_view_zoom(size, zoom)
            return automatic_resolution_choice(resolutions, size, fit_mode, zoom)

        source_settings.set_automatic_resolution(automatic_resolution_for_draft)

        def refresh_resolution_sizes(*_args):
            refresh_still_zoom_hint()
            # The pixel sizes behind Source resolution and Image resolution.
            source_settings.refresh_resolution_label()
            source_settings.copernicus_settings.refresh_image_size_label()

        source_settings.add_source_image_listener(refresh_resolution_sizes)
        for name in ("fit_mode", "zoom", "width", "height", "aspect_ratio"):
            # After the output draft follows the field.
            variables[name].trace_add("write", lambda *_args: root.after_idle(refresh_resolution_sizes))
        refresh_resolution_sizes()
        preset_frame = source_settings.eumetsat_view_frame
        configure_source_columns(preset_frame)
        projection_combo = add_combo(
            preset_frame, 0, "Projection", variables["projection"],
            available_projection_choices(), width=SOURCE_COMBO_WIDTH,
        )
        projection_combo.bind("<<ComboboxSelected>>", select_projection)
        add_combo(
            preset_frame, 1, "Fit mode", variables["fit_mode"], ("fit", "crop"),
            width=SOURCE_COMBO_WIDTH,
        )
        add_zoom_combo(preset_frame, 2)
        preset_combo = add_combo(preset_frame, 3, "Preset", variables["view_preset"],
                                 tuple(preset_label_to_value), width=SOURCE_COMBO_WIDTH)
        preset_combo.bind("<<ComboboxSelected>>", select_view_preset)
        custom_area_frame = ttk.Frame(preset_frame)
        custom_area_frame.grid(row=4, column=0, columnspan=2, sticky="ew")
        configure_source_columns(custom_area_frame)
        add_entry(custom_area_frame, 0, "Latitude", variables["custom_latitude"])
        add_entry(custom_area_frame, 1, "Longitude", variables["custom_longitude"])
        custom_area_rows = custom_area_frame.grid_slaves(row=0) + custom_area_frame.grid_slaves(row=1)
        custom_area_hint = tk.StringVar(master=root)
        ttk.Label(custom_area_frame, textvariable=custom_area_hint,
                  wraplength=420, justify="left").grid(row=2, column=1, pady=(0, 3), sticky="w")

        def custom_area_size_text():
            """Describe the area the current centre and zoom would save, or ''."""
            try:
                latitude = parse_custom_area_degrees(variables["custom_latitude"].get(), "Latitude", 90.0)
                longitude = parse_custom_area_degrees(variables["custom_longitude"].get(), "Longitude", 180.0)
                zoom = float(variables["zoom"].get())
                ratio = parse_aspect_ratio(variables["aspect_ratio"].get())
            except (ValueError, ZeroDivisionError):
                return ""
            if not math.isfinite(zoom) or not 0 < zoom <= CUSTOM_AREA_MAX_ZOOM:
                return ""
            west, south, east, north = custom_area_extent(latitude, longitude, zoom, ratio)
            kilometres = (east - west) * 111.32 * math.cos(math.radians((south + north) / 2.0))
            if kilometres >= 1:
                kilometres = round(kilometres, 1 - int(math.floor(math.log10(kilometres))))
            text = (f"Shows about {east - west:.3g}° × {north - south:.3g}° "
                    f"(about {kilometres:,.0f} km wide).")
            if (abs((west + east) / 2.0 - longitude) > 1e-4
                    or abs((south + north) / 2.0 - latitude) > 1e-4):
                text += " Moved to stay on the world map."
            return text

        def refresh_custom_area(*_args):
            if preset_label_to_value.get(variables["view_preset"].get()) != "custom":
                custom_area_frame.grid_remove()
                return
            custom_area_frame.grid()
            projection_name = variables["projection"].get()
            if projection_name != "Geographic":
                for widget in custom_area_rows:
                    widget.grid_remove()
                custom_area_hint.set(
                    f"This area was set in the settings file in {projection_name} map units "
                    "and is kept unchanged. To choose a centre in degrees instead, select the "
                    "Geographic projection, then the Custom area preset."
                )
                return
            for widget in custom_area_rows:
                widget.grid()
            try:
                zoom = float(variables["zoom"].get())
            except ValueError:
                zoom = DEFAULT_ZOOM
            custom_area_hint.set(" ".join(filter(None, (
                "Centre of the area in decimal degrees, for example 47.5 or 47,5; South and "
                "West are negative. Zoom sets the size: 1 = the whole world width, 10 = a tenth.",
                custom_area_size_text(),
                custom_area_quality_text(zoom),
            ))))

        for name in ("view_preset", "projection", "zoom", "aspect_ratio", *(
                key for key, _label in CUSTOM_AREA_FIELDS)):
            variables[name].trace_add("write", refresh_custom_area)
        refresh_custom_area()

        def transfer_custom_area_centre(latitude, longitude):
            variables["custom_latitude"].set(number_text(round(latitude, 6)))
            variables["custom_longitude"].set(number_text(round(longitude, 6)))

        # Find location fills the centre while Latitude and Longitude are shown.
        def catalogue_refreshed(label, problem):
            """The Image tab's Refresh catalogue ended: its note below the buttons."""
            if problem:
                record_catalogue_problem("catalogue", label)
            else:
                record_image_outcome("catalogue_refreshed", detail=label)

        source_settings.add_catalogue_listener(catalogue_refreshed)
        source_settings.add_location_target(
            "eumetsat", transfer_custom_area_centre,
            available=lambda: (preset_label_to_value.get(variables["view_preset"].get()) == "custom"
                               and variables["projection"].get() == "Geographic"),
        )
        for name in ("view_preset", "projection"):
            variables[name].trace_add("write", source_settings.refresh_location_section)

        def zoom_mode():
            provider = source_settings.provider
            if provider == "copernicus":
                return None  # Copernicus has its own Map zoom.
            if provider != "eumetsat":
                return "still"
            custom = (preset_label_to_value.get(variables["view_preset"].get()) == "custom"
                      and variables["projection"].get() == "Geographic")
            return "custom" if custom else "view"

        def refresh_zoom_choices(*_args):
            """Steps for the source and view; an invalid zoom for them becomes the default."""
            mode = zoom_mode()
            if mode is None or zoom_refresh["active"]:
                return
            try:
                value = float(str(variables["zoom"].get()).strip().replace(",", "."))
            except ValueError:
                value = None
            if not zoom_in_range(value, mode):
                value = DEFAULT_ZOOM
                zoom_refresh["active"] = True
                try:
                    variables["zoom"].set(number_text(value))
                finally:
                    zoom_refresh["active"] = False
            choices = zoom_choices(value, mode)
            zoom_lookup.clear()
            zoom_lookup.update((label, number) for number, label in choices)
            for combo in zoom_combos:
                combo.configure(values=[label for _number, label in choices])
            zoom_display.set(next(label for number, label in choices
                                  if math.isclose(number, value, abs_tol=1e-9)))

        for name in ("zoom", "view_preset", "projection"):
            variables[name].trace_add("write", refresh_zoom_choices)
        refresh_zoom_choices()
        ttk.Checkbutton(
            preset_frame,
            text="Black TrueColor night side",
            variable=variables["truecolor_black_night"],
        ).grid(row=7, column=0, columnspan=2, pady=3, sticky="w")
        # EUMETSAT's render quality belongs to the Rendering section.
        eumetsat_rendering_frame = source_settings.eumetsat_rendering_frame
        eumetsat_quality_combo = add_combo(
            eumetsat_rendering_frame, 0, "Render quality factor",
            variables["eumetsat_render_quality_preset"],
            render_quality_choices("eumetsat_"),
            width=SOURCE_COMBO_WIDTH,
        )
        eumetsat_quality_combo.bind(
            "<<ComboboxSelected>>",
            lambda event: select_render_quality_preset(event, prefix="eumetsat_"),
        )
        render_quality_combos["eumetsat_"] = eumetsat_quality_combo
        refresh_render_quality_preset(prefix="eumetsat_")
        variables["eumetsat_render_scale"].trace_add(
            "write", lambda *args: refresh_render_quality_preset(*args, prefix="eumetsat_"),
        )
        source_changed(IMAGE_SOURCE)
        # The picture on screen, inside the header frame.
        image_source_label = ttk.Label(header_frame, textvariable=status_variables["image_source"],
                                       wraplength=640, name="on_screen")
        image_source_label.grid(row=0, column=0, sticky="ew")
        # Lines wrap only at the frame's width.
        image_source_label.bind(
            "<Configure>", lambda event: event.widget.configure(wraplength=max(300, event.width)),
        )
        # Copernicus: "Auto Recommend: " and the priority in its color, or "No".
        auto_line = ttk.Frame(header_frame, name="auto_line")
        auto_line.grid(row=1, column=0, sticky="w")
        ttk.Label(auto_line, text="Auto Recommend: ", name="caption").grid(row=0, column=0)
        auto_priority_label = ttk.Label(auto_line, name="priority")
        auto_priority_label.grid(row=0, column=1)
        auto_rest_label = ttk.Label(auto_line, name="rest")
        auto_rest_label.grid(row=0, column=2)
        auto_line_state = {"parts": None}

        def show_auto_line(parts=None):
            parts = auto_line_state["parts"] if parts is None else parts
            auto_line_state["parts"] = parts
            if not parts:
                auto_line.grid_remove()
                return
            priority, key, rest = parts
            auto_priority_label.configure(text=priority,
                                          foreground=palette(auto_priority_label)[key] if key else "")
            auto_rest_label.configure(text=rest)
            # Indented to start below the text after "On screen: ": both lines
            # describe the picture on screen. A probe label renders it like the line.
            probe = ttk.Label(header_frame, text="On screen: ")
            indent = probe.winfo_reqwidth()
            probe.destroy()
            auto_line.grid(padx=(indent, 0))

        on_theme_change(auto_line, show_auto_line)
        show_auto_line(False)

        def reserve_header_lines():
            """Two lines always (the picture, then Copernicus' Auto Recommend): the tab
            below does not move when another source is shown."""
            probe = ttk.Label(header_frame, text="Ag")
            line = probe.winfo_reqheight()
            probe.destroy()
            for row in (0, 1):
                header_frame.rowconfigure(row, minsize=line)

        on_theme_change(header_frame, reserve_header_lines)

        # How often the source is checked for a newer picture; first on Downloads & Updates.
        update_frame = ttk.LabelFrame(download_tab, text="Image update checks", padding=8)
        update_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        update_frame.columnconfigure(1, weight=1)
        ttk.Label(update_frame, text="Update check interval").grid(
            row=0, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        update_interval_frame = ttk.Frame(update_frame)
        update_interval_frame.grid(row=0, column=1, pady=3, sticky="w")
        ttk.Combobox(
            update_interval_frame,
            textvariable=variables["update_interval_value"],
            values=UPDATE_INTERVAL_VALUE_CHOICES,
            state="readonly",
            width=7,
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Combobox(
            update_interval_frame,
            textvariable=variables["update_interval_unit"],
            values=tuple(UPDATE_INTERVAL_UNITS),
            state="readonly",
            width=12,
        ).grid(row=0, column=1)
        # One reserved line below the dropdowns: an orange hint below 2 minutes (shows
        # at once, nothing is blocked), the month note for months, else empty.
        interval_note_label = ttk.Label(update_frame, name="interval_note")
        interval_note_label.grid(row=1, column=1, sticky="w", pady=(0, 3))

        def refresh_short_interval_hint(*_args):
            hint = short_update_interval_hint(variables["update_interval_value"].get(),
                                              variables["update_interval_unit"].get())
            months = variables["update_interval_unit"].get() == "months"
            interval_note_label.configure(
                text=hint or (MONTH_INTERVAL_NOTE if months else ""),
                foreground=palette(interval_note_label)["warning"] if hint else "")
            probe = ttk.Label(update_frame, text="Ag")
            update_frame.rowconfigure(1, minsize=probe.winfo_reqheight() + 3)
            probe.destroy()

        for name in ("update_interval_value", "update_interval_unit"):
            variables[name].trace_add("write", refresh_short_interval_hint)
        on_theme_change(interval_note_label, refresh_short_interval_hint)
        ttk.Label(update_frame, text="Next check:").grid(
            row=2, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        ttk.Label(update_frame, textvariable=status_variables["next_check"]).grid(
            row=2, column=1, pady=3, sticky="w"
        )

        download_display_frame = ttk.LabelFrame(
            download_tab, text="Download display", padding=8
        )
        download_display_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            download_display_frame,
            text="Show download speed",
            variable=variables["show_download_speed"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        add_combo(
            download_display_frame,
            1,
            "Speed unit",
            variables["download_speed_unit"],
            DOWNLOAD_SPEED_UNIT_CHOICES,
            width=18,
        )
        ttk.Checkbutton(
            download_display_frame,
            text="Show percentage",
            variable=variables["show_download_progress"],
        ).grid(row=2, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            download_display_frame,
            text="Show downloaded size",
            variable=variables["show_download_size"],
        ).grid(row=3, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            download_display_frame,
            text="Show progress bar",
            variable=variables["show_download_progress_bar"],
        ).grid(row=4, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            download_display_frame,
            text="Keep completed download visible until next download",
            variable=variables["keep_completed_download_visible"],
        ).grid(row=5, column=0, columnspan=2, pady=3, sticky="w")

        download_retry_frame = ttk.LabelFrame(
            download_tab, text="Download retries", padding=8
        )
        download_retry_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            download_retry_frame, 0, "Retries after first attempt",
            variables["download_retries"], tuple(str(value) for value in range(1, 10)),
            width=RETRY_COMBO_WIDTH,
        ).grid_configure(sticky="w")

        # Refresh all catalogues with its progress; the daily schedule is a
        # subsection. Catalogue retries stay separate.
        catalogue_refresh_frame = ttk.LabelFrame(download_tab, text="Catalogue refresh", padding=8)
        catalogue_refresh_frame.grid(row=3, column=0, pady=(0, 8), sticky="ew")
        catalogue_refresh_frame.columnconfigure(0, weight=1)

        def force_all_catalogues():
            if APPLICATION_STOP_EVENT.is_set():
                return
            if CATALOGUE_SCHEDULE is None:
                warm_public_catalogues()
            CATALOGUE_SCHEDULE.request(force=True)

        catalogue_refresh_panel = CatalogueRefreshPanel(
            catalogue_refresh_frame, 0, force_all_catalogues,
            lambda: getattr(get_catalogue_client(), "catalogue_refresh_status", None),
            lambda: CATALOGUE_SCHEDULE.status() if CATALOGUE_SCHEDULE is not None else None,
        )
        catalogue_refresh_frame.bind(
            "<Destroy>",
            lambda event: catalogue_refresh_panel.close() if event.widget is catalogue_refresh_frame else None,
            add="+",
        )
        catalogue_schedule_frame = ttk.LabelFrame(catalogue_refresh_frame, text="Daily catalogue refresh", padding=8)
        catalogue_schedule_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="ew")
        catalogue_schedule_frame.columnconfigure(0, weight=1)
        ttk.Label(catalogue_schedule_frame, text="Time (system time): hours / minutes / seconds").grid(row=0, column=0, sticky="w")
        clock_fields = ttk.Frame(catalogue_schedule_frame)
        clock_fields.grid(row=1, column=0, sticky="w", pady=4)
        clock_vars = [tk.StringVar(master=root, value=part) for part in CATALOGUE_REFRESH_TIME.split(":")]
        for index, variable in enumerate(clock_vars):
            ttk.Combobox(clock_fields, textvariable=variable, state="readonly", width=4,
                         values=tuple(f"{value:02d}" for value in range(24 if index == 0 else 60))).grid(row=0, column=index * 2)
            if index < 2:
                ttk.Label(clock_fields, text=":").grid(row=0, column=index * 2 + 1, padx=4)
            variable.trace_add("write", lambda *_: variables["catalogue_refresh_time"].set(":".join(value.get() for value in clock_vars)))
        saved_refresh_time = tk.StringVar(master=root, value=f"Saved time: {CATALOGUE_REFRESH_TIME} (system time)")
        ttk.Label(catalogue_schedule_frame, textvariable=saved_refresh_time).grid(row=2, column=0, sticky="w")
        catalogue_schedule_status = tk.StringVar(master=root)
        ttk.Label(catalogue_schedule_frame, textvariable=catalogue_schedule_status,
                  wraplength=610).grid(row=3, column=0, sticky="ew", pady=(4, 0))

        def poll_catalogue_schedule():
            if CATALOGUE_SCHEDULE is not None:
                state = CATALOGUE_SCHEDULE.status()
                # Progress and results of a running refresh show above; this
                # line only reports the schedule itself.
                lines = []
                # When the last refresh actually finished, not its planned slot.
                lines.append("Last completed refresh: " + (
                    state["completed_at"].replace("T", " ") + " (system time)"
                    if state.get("completed_at") else "not yet"))
                if state.get("error") and not state.get("running"):
                    lines.append("Last attempt was incomplete; it is retried after five minutes.")
                catalogue_schedule_status.set("\n".join(lines))
            root.after(500, poll_catalogue_schedule)

        root.after(0, poll_catalogue_schedule)
        catalogue_retry_frame = ttk.LabelFrame(
            download_tab, text="Catalogue retries", padding=8
        )
        catalogue_retry_frame.grid(row=4, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            catalogue_retry_frame, 0, "Retries after first attempt",
            variables["catalogue_retries"], tuple(str(value) for value in range(1, 10)),
            width=RETRY_COMBO_WIDTH,
        ).grid_configure(sticky="w")

        def request_picture_from_settings():
            if not force_loading_is_enabled(None):
                return
            force_picture_button.state(["disabled"])
            check_picture_button.state(["disabled"])
            force_loading_new_picture(icon, None)

        def check_picture_from_settings():
            """Check new image: the regular check now; a picture loads only when newer."""
            if not force_loading_is_enabled(None) or CHECK_NOW_EVENT.is_set():
                return
            force_picture_button.state(["disabled"])
            check_picture_button.state(["disabled"])
            CHECK_NOW_EVENT.set()
            log("Check for a new image requested.")

        image_update_frame = ttk.LabelFrame(
            image_tab, text="Imagery updates", padding=8
        )
        image_update_frame.grid(row=6, column=0, pady=(0, 8), sticky="ew")
        source_settings.account_frame.grid(row=0, column=0, sticky="ew")
        access_panels = {"Copernicus Browser": source_settings.account_frame}

        def show_access_panel(_event=None):
            for label, panel in access_panels.items():
                if label == access_source_var.get():
                    panel.grid()
                else:
                    panel.grid_remove()

        access_combo.bind("<<ComboboxSelected>>", show_access_panel)
        show_access_panel()
        ttk.Checkbutton(
            image_update_frame,
            text="Check for and download newer images",
            variable=variables["check_for_source_updates"],
        ).grid(row=0, column=0, columnspan=2, pady=(0, 4), sticky="w")
        ttk.Label(
            image_update_frame,
            text=("When off, the image is loaded once and kept; Force loading new image "
                  "still refreshes it. Saved in the profile. Check new image asks the source "
                  "now and loads only a newer picture."),
            wraplength=620, justify="left",
        ).grid(row=1, column=0, columnspan=2, pady=(0, 6), sticky="w")
        # Check new image asks the source now and loads only a newer picture;
        # Force loading new image loads one even when the source has nothing newer.
        image_update_buttons = ttk.Frame(image_update_frame)
        image_update_buttons.grid(row=2, column=0, columnspan=2, sticky="w")
        check_picture_button = ttk.Button(
            image_update_buttons,
            text="Check new image",
            command=check_picture_from_settings,
        )
        check_picture_button.grid(row=0, column=0, sticky="w")
        force_picture_button = ttk.Button(
            image_update_buttons,
            text="Force loading new image",
            command=request_picture_from_settings,
        )
        force_picture_button.grid(row=0, column=1, padx=(6, 0), sticky="w")

        def capture_image_form(validated_updates=None):
            provider, selections = source_settings.get_selection()
            values = {key: variable.get() for key, variable in variables.items()}
            values.update(global_output_draft)
            values["monitor_positions"] = dict(monitor_positions_draft)
            values["paused_displays"] = sorted(paused_displays_draft)
            values["monitor_output_settings"] = deepcopy(monitor_outputs_draft)
            values["view_preset"] = preset_label_to_value[values["view_preset"]]
            custom_area_form_values(values, values["view_preset"])
            # Saving an Image snapshot does not depend on unfinished edits in
            # General or History. Apply validates those tabs separately.
            values.update(position=WINDOWS_WALLPAPER_POSITION, time_zone=DISPLAY_TIME_ZONE,
                          appearance=APPEARANCE,
                          update_interval_minutes=str(UPDATE_INTERVAL_MINUTES),
                          max_files=str(HISTORY_MAX_FILES), years=str(HISTORY_RETENTION_YEARS),
                          months=str(HISTORY_RETENTION_MONTHS), days=str(HISTORY_RETENTION_DAYS),
                          hours=str(HISTORY_RETENTION_HOURS), minutes=str(HISTORY_RETENTION_MINUTES),
                          retention_mode=HISTORY_RETENTION_MODE, history_folder=CUSTOM_HISTORY_FOLDER,
                          profile_history_policies=json.dumps(PROFILE_HISTORY_POLICIES, sort_keys=True))
            updates = (normalize_settings_form_values(values, provider=provider)
                       if validated_updates is None else validated_updates)
            snapshot = deepcopy(image_form_state["base"])
            for section, key, value in updates:
                if section == "view" or (section, key) == ("output", "render_scale"):
                    snapshot[section][key] = value
            snapshot.update(
                source={
                    "provider": provider,
                    "check_for_updates": bool(
                        variables["check_for_source_updates"].get()
                    ),
                },
                sources=selections,
            )
            if provider == "eumetsat":
                selected_layer = source_settings.get_eumetsat_layer()
                if not snapshot["layers"]:
                    snapshot["layers"] = deepcopy(DEFAULT_LAYER_CONFIG)
                for layer in snapshot["layers"]:
                    if layer.get("kind") == "wms" and layer.get("enabled", True):
                        layer["name"] = selected_layer
                        break
            return snapshot

        def load_image_form(snapshot, switch_to_image=True):
            # Validate every field before changing any Tk variable or form draft.
            # This pure check must never swap the worker's runtime configuration.
            snapshot = normalize_image_settings_snapshot(snapshot)
            # Profiles are form drafts until Apply, just like all other settings.
            provider, selections = normalize_source_configuration(
                snapshot["source"]["provider"], snapshot["sources"])
            for section, fields in IMAGE_SETTING_FIELDS.items():
                if any(key not in snapshot[section] for key in fields):
                    raise ValueError("This profile has incomplete Image settings.")
            view, output = snapshot["view"], snapshot["output"]
            selected_layer = next((entry.get("name") for entry in snapshot["layers"]
                                   if entry.get("kind") == "wms" and entry.get("enabled", True)), None)
            preset_label = next((label for label, value in preset_label_to_value.items()
                                 if value == view["preset"]), None)
            if preset_label is None:
                if provider == "eumetsat" and view["preset"] not in VIEW_PRESETS:
                    raise ValueError("The profile contains an unknown EUMETSAT preset.")
                preset_label = f"Custom preset ({view['preset']})"
                preset_label_to_value[preset_label] = view["preset"]
                preset_combo.configure(values=tuple(preset_label_to_value))
            layer_label = next((label for label, value in layer_label_to_value.items()
                                if value == selected_layer), None)
            if layer_label is None:
                layer_label = f"Custom layer ({selected_layer})"
                layer_label_to_value[layer_label] = selected_layer
            image_form_state.update(base=deepcopy(snapshot), loaded=True)
            for key in ("projection", "fit_mode", "zoom", "truecolor_black_night"):
                variables[key].set(view[key])
            origin = custom_area_display(view["preset"], view["projection"], view["bbox"], view["zoom"])
            custom_area_state.update(previous=view["preset"], origin=origin)
            if origin is not None:
                variables["zoom"].set(origin["texts"][2])
            for (key, _label), text in zip(CUSTOM_AREA_FIELDS, origin["texts"] if origin else ("", "")):
                variables[key].set(text)
            variables["view_preset"].set(preset_label)
            variables["satellite_layer"].set(layer_label)
            variables["check_for_source_updates"].set(
                snapshot["source"].get("check_for_updates", True)
            )
            # Of the output settings only render quality belongs to a profile
            # (EUMETSAT's own, or default for General's); size, background,
            # the display render quality and the Latest folder are device settings.
            scale = output["render_scale"]
            variables["eumetsat_render_scale"].set(scale if isinstance(scale, str) else number_text(scale))
            select_output_device()
            if provider == "eumetsat":
                refresh_projection_choices()
            else:
                # Retain unused WMS values in a non-EUMETSAT profile without
                # changing the source-specific selections.
                projection_combo.configure(values=available_projection_choices())
            if provider == "eumetsat":
                selections["eumetsat"]["layer"] = selected_layer
            source_settings.set_selection(provider, selections)
            if provider == "eumetsat":
                source_settings.select_eumetsat_layer(selected_layer)
            if switch_to_image:
                notebook.select(image_tab.master.master)

        coverage_images = PublishedImageIndex()

        def image_header_profile(state=None):
            """(id, name, " (modified)" or "") of the profile the Image header names.

            The name is None while the Image tab shows the Latest snapshot.
            """
            if state is None:
                with ROTATION_STATUS_LOCK:
                    state = dict(ROTATION_STATUS)
            chosen_id = (image_form_state.get("profile_id") if image_form_state["loaded"] else
                         state.get("active_profile_id") or image_form_state.get("profile_id"))
            try:
                chosen_name = next((item["name"] for item in profile_settings._items if item["id"] == chosen_id), None)
            except NameError:
                chosen_name = None
            # During construction the controller does not exist yet.
            if chosen_name is None:
                chosen_name = image_profile_name(chosen_id)
            suffix = ""
            if chosen_name:
                try:
                    saved = next(item["settings"] for item in profile_settings._items if item["id"] == chosen_id)
                    current = capture_image_form()
                    if (portable_settings(current, normalize_image_settings_snapshot)
                            != portable_settings(saved, normalize_image_settings_snapshot)):
                        suffix = " (modified)"
                except (ValueError, TypeError, StopIteration, NameError):
                    suffix = " (modified)"
            return chosen_id, chosen_name, suffix

        def suggested_profile_name():
            """The name the Image header shows, as a suggestion for a new profile."""
            _chosen_id, chosen_name, suffix = image_header_profile()
            return chosen_name + suffix if chosen_name else ""

        def profile_status_text():
            with ROTATION_STATUS_LOCK:
                state = dict(ROTATION_STATUS)
            selected_zone = time_zone_label_to_value.get(variables["time_zone"].get(), DISPLAY_TIME_ZONE)
            shown_id = shown_profile_row_id()
            if shown_id == latest_snapshot.SYSTEM_ID:
                shown_name = latest_snapshot.SYSTEM_NAME
            elif shown_id:
                shown_name = (next((item["name"] for item in IMAGE_PROFILE_LIBRARY.get("items", ())
                                    if item["id"] == shown_id), None)
                              or image_profile_name(shown_id) or latest_snapshot.SYSTEM_NAME)
            else:
                shown_name = None
            message = now_showing_text(shown_name, state, selected_zone)
            profile_ids = [item["id"] for item in IMAGE_PROFILE_LIBRARY.get("items", [])]
            try:
                cache_entries = get_profile_cache().entries([*profile_ids, latest_snapshot.CACHE_ID])
            except Exception:
                cache_entries = {}
            snapshot = read_latest_snapshot()
            snapshot_entry = cache_entries.pop(latest_snapshot.CACHE_ID, None)
            if snapshot and snapshot_entry:
                cache_entries[latest_snapshot.SYSTEM_ID] = snapshot_entry
            folders = published_image_folders()
            published = published_images.snapshot(folders)
            for identifier, entry in cache_entries.items():
                cached_time = entry.get("updated_at")
                if not cached_time:
                    continue
                try:
                    current = published.get(identifier)
                    if (current is None
                            or dt.datetime.fromisoformat(cached_time.replace("Z", "+00:00"))
                            > dt.datetime.fromisoformat(current.replace("Z", "+00:00"))):
                        published[identifier] = cached_time
                except (TypeError, ValueError, OverflowError):
                    continue
            # Cached PNGs are content-addressed in two-character subfolders.
            cache_dir = get_profile_cache().images_dir
            records = coverage_images.records_snapshot(folders | {cache_dir} | _direct_subfolders(cache_dir))
            if snapshot:
                identifier = snapshot["profile_id"]
                if identifier in published:
                    published[latest_snapshot.SYSTEM_ID] = published[identifier]
                if identifier in records:
                    records[latest_snapshot.SYSTEM_ID] = records[identifier]
            chosen_id, chosen_name, suffix = image_header_profile(state)
            if chosen_name:
                displayed_profile = f"{chosen_name}{suffix}"
                # The short ID (first 8 characters), as in the profile table.
                displayed_id = f"({str(chosen_id)[:8]})"
            else:
                displayed_profile, displayed_id = latest_snapshot.SYSTEM_NAME, ""
            if profile_display.get() != displayed_profile:
                profile_display.set(displayed_profile)
            if header_short_id.get() != displayed_id:
                header_short_id.set(displayed_id)
            try:
                output_width = int(global_output_draft["width"])
                output_height = int(global_output_draft["height"]) or round(
                    output_width / parse_aspect_ratio(global_output_draft["aspect_ratio"]))
                output_resolution = f"{output_width} × {output_height}"
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                output_resolution = "-"
            return {
                "text": message,
                # The row whose picture is on screen; the Image tab's draft does
                # not count, and modified settings show as the Latest snapshot.
                "active_profile_id": shown_profile_row_id(),
                "download": profile_download_status(),
                "queued": queued_profile_refreshes(),
                "failures": profile_failures(),
                "checking": checking_profile_id(),
                "check_summary": profile_check_summary(),
                "rotation_switch": rotation_switch_state(),
                "profiles": cache_entries,
                "last_downloads": published,
                "image_records": records,
                "latest_snapshot": snapshot,
                "wallpaper_position": variables["position"].get(),
                "output_resolution": output_resolution,
                "display_time_zone": time_zone_label_to_value.get(
                    variables["time_zone"].get(), DISPLAY_TIME_ZONE
                ),
            }

        def load_saved_profile(snapshot):
            load_image_form(snapshot)
            selection = profile_settings.tree.selection()
            image_form_state["profile_id"] = selection[0] if len(selection) == 1 and selection[0] != latest_snapshot.SYSTEM_ID else ""

        def apply_image_profile(snapshot):
            save_was_pending = save_is_pending()
            normalized = normalize_image_settings_snapshot(snapshot)
            requested_library = profile_settings.get_library()
            requested_auth = source_settings.get_copernicus_auth(require=False)
            selected_id = profile_settings.tree.selection()[0]
            selected_id = "" if selected_id == latest_snapshot.SYSTEM_ID else selected_id

            def transform(text):
                updated = replace_image_settings(text, normalized)
                updated = replace_copernicus_auth_configuration(
                    updated, requested_auth
                )
                updated = ensure_profile_list_configuration_section(updated)
                updated = replace_toml_section_value(
                    updated, "profile_list", "applied_profile_id", selected_id,
                )
                return write_table_layout(updated)

            changed = update_active_configuration_and_profiles(
                transform, requested_library
            )
            history_rename_errors = profile_settings.commit_history_renames()
            if history_rename_errors:
                messagebox.showwarning("History folder rename incomplete",
                                       "Profile settings were saved, but these History folders could not be renamed:\n"
                                       + "\n".join(history_rename_errors), parent=root)
            warn_history_deletion_errors(profile_settings.commit_history_deletions())
            load_image_form(normalized, switch_to_image=False)
            image_form_state.update(base=deepcopy(normalized), loaded=False,
                                    profile_id=selected_id)
            if not save_was_pending:
                # Apply profile saved the table and profiles; compare anew
                # unless other edits are still waiting for Save.
                save_baseline["draft"] = None
            # Always asked for: the settings may already be saved (Save on the
            # Image tab) while another picture is on screen.
            request_runtime_configuration_reload(icon)
            if changed:
                if DOWNLOAD_PROGRESS.snapshot()["active"]:
                    # The worker applies the profile after its running download;
                    # a cached picture is shown now.
                    threading.Thread(
                        target=show_cached_profile_now,
                        args=(selected_id or latest_snapshot.SYSTEM_ID, normalized),
                        name="MarbleScapeInstantProfile", daemon=True,
                    ).start()

        def force_load_profiles(profile_ids):
            """Profile table "Force loading new image"; never changes the active profile.

            The profile shown as wallpaper, if selected, is downloaded afresh at
            once. The others are queued for background downloads into their
            cache. Returns (newly queued IDs, whether the shown profile reloads).
            """
            shown = shown_profile_row_id()
            active = shown in profile_ids
            if active:
                force_loading_new_picture(icon, None)
            queued = queue_profile_refreshes(
                identifier for identifier in profile_ids if identifier != shown)
            return queued, active

        def check_profiles(profile_ids):
            """Profile table "Check for new image"; never changes the active profile.

            The profile shown as wallpaper, if selected, gets its regular check
            at once. The others are queued to load a picture only when their
            provider lists a newer one. Returns (newly queued IDs, whether the
            shown profile is checked).
            """
            shown = shown_profile_row_id()
            active = shown in profile_ids
            if active:
                CHECK_NOW_EVENT.set()
            queued = queue_profile_refreshes(
                (identifier for identifier in profile_ids if identifier != shown), check_only=True)
            return queued, active

        def persist_profile_list(library):
            """Save a profile-list change at once; the Profiles tab has no draft."""
            changed = update_active_configuration_and_profiles(lambda text: text, library)
            if save_baseline["draft"] is not None:
                # Save compares against what it would write; the list is saved now.
                save_baseline["draft"]["profiles"] = serialize_library(normalize_library(library))
            if changed:
                request_runtime_configuration_reload(icon, load_image=False)

        def write_table_layout(text):
            """Write the profile table's current column layout.

            Values are read when the text is written, not when a save was
            prepared: the layout is saved at once, so it never leaves Save
            pending.
            """
            updated = ensure_profile_list_configuration_section(text)
            updated = replace_toml_section_value(
                updated, "profile_list", "visible_columns", list(profile_settings.get_visible_columns()))
            for key, value in profile_settings.get_sort_settings().items():
                updated = replace_toml_section_value(updated, "profile_list", key, value)
            updated = replace_profile_column_widths(updated, profile_settings.get_column_widths())
            updated = replace_toml_section_value(
                updated, "profile_list", "column_order", list(profile_settings.get_column_order()))
            updated = replace_toml_section_value(
                updated, "profile_list", "table_rows", profile_settings.get_table_rows())
            return replace_toml_section_value(updated, "profile_list", "columns_version", 19)

        def persist_table_layout():
            """Save visibility, order, widths, sorting and height as soon as they change."""
            global PROFILE_LIST_VISIBLE_COLUMNS, PROFILE_LIST_SORT_COLUMN, PROFILE_LIST_SORT_DESCENDING
            global PROFILE_LIST_COLUMN_WIDTHS, PROFILE_LIST_COLUMN_ORDER, PROFILE_LIST_TABLE_ROWS
            update_active_configuration(write_table_layout)
            # Only this window uses the layout; the next window opens with it
            # without a full runtime reload.
            PROFILE_LIST_VISIBLE_COLUMNS = profile_settings.get_visible_columns()
            sort = profile_settings.get_sort_settings()
            PROFILE_LIST_SORT_COLUMN = sort["sort_column"]
            PROFILE_LIST_SORT_DESCENDING = sort["sort_descending"]
            PROFILE_LIST_COLUMN_WIDTHS = profile_settings.get_column_widths()
            PROFILE_LIST_COLUMN_ORDER = profile_settings.get_column_order()
            PROFILE_LIST_TABLE_ROWS = profile_settings.get_table_rows()

        def persist_image_updates(identifiers, enabled, library):
            """Save switched Updates checkboxes of the profile table at once.

            The profile shown in the Image header keeps its unmodified state:
            its Imagery updates checkbox follows, and for the applied profile the
            running settings follow too.
            """
            shown_id = image_form_state.get("profile_id")
            sync_form = False
            if shown_id in identifiers:
                try:
                    saved = next(item["settings"] for item in library["items"] if item["id"] == shown_id)
                    current = capture_image_form()
                    current["source"]["check_for_updates"] = enabled
                    sync_form = (portable_settings(current, normalize_image_settings_snapshot)
                                 == portable_settings(saved, normalize_image_settings_snapshot))
                except (ValueError, TypeError, KeyError, StopIteration):
                    sync_form = False
            sync_running = sync_form and not image_form_state["loaded"]

            def transform(text):
                if sync_running:
                    return replace_toml_section_value(text, "source", "check_for_updates", enabled)
                return text

            changed = update_active_configuration_and_profiles(transform, library)
            if sync_form:
                variables["check_for_source_updates"].set(enabled)
                if sync_running:
                    image_form_state["base"]["source"]["check_for_updates"] = enabled
            if save_baseline["draft"] is not None:
                save_baseline["draft"]["profiles"] = serialize_library(normalize_library(library))
            if changed:
                request_runtime_configuration_reload(icon, load_image=False)

        def persist_history_switches(identifiers, enabled):
            """Save History on/off from the profile table at once.

            Only these switches are written; other unsaved History & Storage
            edits stay drafts. The Latest snapshot row is History (no profile).
            """
            save_was_pending = save_is_pending()
            profile_ids = [identifier for identifier in identifiers if identifier != latest_snapshot.SYSTEM_ID]
            no_profile = latest_snapshot.SYSTEM_ID in identifiers

            def switched(policies):
                policies = dict(policies)
                for identifier in profile_ids:
                    policy = _normalize_history_policy(policies.get(identifier, {}))
                    policy["enabled"] = enabled
                    policies[identifier] = policy
                return policies

            def transform(text):
                raw = tomllib.loads(text).get("history", {}).get("profile_policies", "{}")
                saved = json.loads(raw) if isinstance(raw, str) else raw
                updated = replace_toml_section_value(
                    text, "history", "profile_policies", json.dumps(switched(saved), sort_keys=True))
                if no_profile:
                    updated = replace_toml_section_value(updated, "history", "enabled", enabled)
                return updated

            changed = update_active_configuration(transform)
            # Mirror the saved switches in the open History & Storage draft.
            try:
                draft = json.loads(variables["profile_history_policies"].get())
            except json.JSONDecodeError:
                draft = None
            if isinstance(draft, dict):
                variables["profile_history_policies"].set(json.dumps(switched(draft), sort_keys=True))
            if no_profile:
                variables["history_enabled"].set(enabled)
            if selected_profile_history_id() in profile_ids:
                load_profile_history()
            if not save_was_pending:
                save_baseline["draft"] = None
            if changed:
                request_runtime_configuration_reload(icon, load_image=False)

        def warn_history_deletion_errors(errors):
            if errors:
                messagebox.showwarning("History images not deleted",
                                       "Profile settings were saved, but History images of these deleted profiles could not be removed:\n"
                                       + "\n".join(errors), parent=root)

        def draft_history_enabled(identifier):
            """History switch of the open History & Storage draft."""
            try:
                if identifier == latest_snapshot.SYSTEM_ID:
                    return bool(variables["history_enabled"].get())
                draft = json.loads(variables["profile_history_policies"].get())
                return _normalize_history_policy(draft.get(identifier, {}))["enabled"]
            except (tk.TclError, ValueError, TypeError, json.JSONDecodeError):
                return False

        from marblescape_profile_settings import ProfilesSettings
        profile_settings = ProfilesSettings(profiles_tab, IMAGE_PROFILE_LIBRARY,
                                             capture_image_form, load_saved_profile,
                                             on_apply=apply_image_profile,
                                             system_snapshot=read_latest_snapshot,
                                             status=profile_status_text,
                                             visible_columns=PROFILE_LIST_VISIBLE_COLUMNS,
                                             sort_column=PROFILE_LIST_SORT_COLUMN,
                                             sort_descending=PROFILE_LIST_SORT_DESCENDING,
                                             column_widths=PROFILE_LIST_COLUMN_WIDTHS,
                                             table_rows=PROFILE_LIST_TABLE_ROWS,
                                             column_order=PROFILE_LIST_COLUMN_ORDER,
                                             normalize_settings=normalize_image_settings_snapshot,
                                             import_defaults=default_import_settings,
                                             export_locations=export_locations,
                                             history_directory=profile_history_directory,
                                             on_profile_rename=rename_profile_history_directory,
                                             history_enabled=draft_history_enabled,
                                             history_usage=profile_history_usage,
                                             on_history_delete=delete_profile_history,
                                             on_save=persist_profile_list,
                                             on_history_toggle=persist_history_switches,
                                             on_image_updates=persist_image_updates,
                                             suggest_name=suggested_profile_name,
                                             on_layout_change=persist_table_layout,
                                             on_saved=lambda: profile_change_saved(),
                                             on_force_load=force_load_profiles,
                                             on_check=check_profiles,
                                             catalogue_entry=cached_catalogue_entry)
        profile_settings.frame.grid(row=0, column=0, sticky="ew")
        for key in ("profile_history_policies", "history_enabled"):
            variables[key].trace_add("write", lambda *_args: profile_settings.refresh_history())

        latest_folder_frame = ttk.LabelFrame(
            history_tab, text="Latest image folder", padding=8
        )
        latest_folder_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        add_folder_picker(
            latest_folder_frame, 0, "Custom latest folder", "latest_folder",
            CONTENT_DIR / LATEST_DIRECTORY_NAME, "Open latest folder",
        )

        # The History root is shared by no-profile and profile History, so it
        # has its own section outside both.
        history_folder_frame = ttk.LabelFrame(
            history_tab, text="History folder", padding=8
        )
        history_folder_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        add_folder_picker(
            history_folder_frame, 0, "Custom history folder", "history_folder",
            CONTENT_DIR / HISTORY_DIRECTORY_NAME, "Open history folder",
        )
        # That it holds _no profile and one subfolder per profile is explained in Info.

        history_frame = ttk.LabelFrame(history_tab, text="History (no profile)", padding=8)
        history_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        ttk.Label(history_frame, text=(
            "Applies to images without a saved profile (Latest snapshot). "
            "Saved profiles use History (profile) below."
        ), wraplength=620, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Checkbutton(
            history_frame,
            text="Enable history",
            variable=variables["history_enabled"],
        ).grid(row=1, column=0, columnspan=2, pady=3, sticky="w")
        add_combo(
            history_frame,
            2,
            "Retention mode",
            variables["retention_mode"],
            ("count", "time", "both"),
            width=HISTORY_FIELD_WIDTH,
        ).grid_configure(sticky="w")
        # Same widget type as Retention mode, so both are exactly as wide.
        max_files_combo = add_combo(
            history_frame, 3, "Maximum files", variables["max_files"],
            tuple(str(value) for value in HISTORY_MAX_FILES_MENU_CHOICES), width=HISTORY_FIELD_WIDTH,
        )
        max_files_combo.configure(state="normal")
        max_files_combo.grid_configure(sticky="w")

        age_frame = ttk.Frame(history_frame)
        age_frame.grid(row=4, column=0, columnspan=2, pady=(6, 0), sticky="ew")
        for column, key in enumerate(("years", "months", "days", "hours", "minutes")):
            ttk.Label(age_frame, text=key.capitalize()).grid(
                row=0, column=column, padx=3, sticky="w"
            )
            ttk.Entry(age_frame, textvariable=variables[key], width=8).grid(
                row=1, column=column, padx=3, sticky="ew"
            )

        ttk.Label(history_frame, text="No-profile images are also kept in the profile image cache (Latest snapshot slot).").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(5, 0),
        )

        profile_history_frame = ttk.LabelFrame(history_tab, text="History (profile)", padding=8)
        profile_history_frame.grid(row=3, column=0, pady=(0, 8), sticky="ew")
        profile_history_frame.columnconfigure(1, weight=1)
        ttk.Label(profile_history_frame, text=(
            "Each profile uses its own History subfolder named from the profile and its full UUID. "
            "Its local policy is not included in PNG or JSON profile imports/exports."
        ), wraplength=620, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        def sorted_profile_history_choices(items):
            # Alphabetical, independent of the profile table's order or sorting.
            return {f"{item['name']} [{item['id'][:8]}]": item["id"]
                    for item in sorted(items, key=lambda item: (item["name"].casefold(), item["id"]))}

        profile_choices = sorted_profile_history_choices(profile_settings.get_library()["items"])
        profile_history_selected = tk.StringVar(value=next(iter(profile_choices), ""))
        profile_history_enabled = tk.BooleanVar()
        profile_history_mode = tk.StringVar()
        profile_history_max = tk.StringVar()
        profile_history_years = tk.StringVar()
        profile_history_months = tk.StringVar()
        profile_history_days = tk.StringVar()
        profile_history_hours = tk.StringVar()
        profile_history_minutes = tk.StringVar()
        profile_history_loading = {"value": False}

        def selected_profile_history_id():
            return profile_choices.get(profile_history_selected.get())

        def store_profile_history(*_args):
            if profile_history_loading["value"]:
                return
            identifier = selected_profile_history_id()
            if not identifier:
                return
            try:
                policy = _normalize_history_policy({
                    "enabled": bool(profile_history_enabled.get()),
                    "retention_mode": profile_history_mode.get(), "max_files": int(profile_history_max.get()),
                    "years": int(profile_history_years.get()), "months": int(profile_history_months.get()),
                    "days": int(profile_history_days.get()), "hours": int(profile_history_hours.get()),
                    "minutes": int(profile_history_minutes.get()),
                })
                draft = json.loads(variables["profile_history_policies"].get())
                draft[identifier] = policy
                variables["profile_history_policies"].set(json.dumps(draft, sort_keys=True))
            except (ValueError, TypeError, json.JSONDecodeError):
                return

        def current_profile_history_policy():
            return _normalize_history_policy({
                "enabled": bool(profile_history_enabled.get()),
                "retention_mode": profile_history_mode.get(),
                "max_files": int(profile_history_max.get()),
                "years": int(profile_history_years.get()),
                "months": int(profile_history_months.get()),
                "days": int(profile_history_days.get()),
                "hours": int(profile_history_hours.get()),
                "minutes": int(profile_history_minutes.get()),
            })

        def apply_profile_history_to_all():
            try:
                policy = current_profile_history_policy()
                draft = json.loads(variables["profile_history_policies"].get())
                for identifier in profile_choices.values():
                    draft[identifier] = deepcopy(policy)
                variables["profile_history_policies"].set(
                    json.dumps(draft, sort_keys=True)
                )
                # A draft: the footer shows Unsaved changes, then Saved after Save.
                status_variables["history_action"].set("")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                messagebox.showerror(
                    "Unable to apply profile History settings", str(exc), parent=root
                )

        def load_profile_history(*_args):
            profile_history_loading["value"] = True
            try:
                draft = json.loads(variables["profile_history_policies"].get())
                policy = _normalize_history_policy(draft.get(selected_profile_history_id(), {}))
                profile_history_enabled.set(policy["enabled"])
                profile_history_mode.set(policy["retention_mode"])
                profile_history_max.set(str(policy["max_files"]))
                profile_history_years.set(str(policy["years"]))
                profile_history_months.set(str(policy["months"]))
                profile_history_days.set(str(policy["days"]))
                profile_history_hours.set(str(policy["hours"]))
                profile_history_minutes.set(str(policy["minutes"]))
            finally:
                profile_history_loading["value"] = False

        ttk.Label(profile_history_frame, text="Profile").grid(row=1, column=0, sticky="w", pady=3)
        profile_history_combo = ttk.Combobox(profile_history_frame, textvariable=profile_history_selected,
                                             values=tuple(profile_choices), state="readonly", width=HISTORY_FIELD_WIDTH)
        profile_history_combo.grid(row=1, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(profile_history_frame, text="Enable history for this profile", variable=profile_history_enabled,
                        command=store_profile_history).grid(row=2, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(profile_history_frame, text="Retention mode").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(profile_history_frame, textvariable=profile_history_mode, values=("count", "time", "both"),
                     state="readonly", width=HISTORY_FIELD_WIDTH).grid(row=3, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Label(profile_history_frame, text="Maximum files").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(profile_history_frame, textvariable=profile_history_max,
                     values=tuple(str(value) for value in HISTORY_MAX_FILES_MENU_CHOICES),
                     width=HISTORY_FIELD_WIDTH).grid(row=4, column=1, columnspan=2, sticky="w", pady=3)
        age_profile_frame = ttk.Frame(profile_history_frame)
        age_profile_frame.grid(row=5, column=0, columnspan=3, sticky="w", pady=4)
        for column, (label, variable) in enumerate((("Years", profile_history_years), ("Months", profile_history_months),
                                                     ("Days", profile_history_days), ("Hours", profile_history_hours),
                                                     ("Minutes", profile_history_minutes))):
            ttk.Label(age_profile_frame, text=label).grid(row=0, column=column, padx=3, sticky="w")
            ttk.Entry(age_profile_frame, textvariable=variable, width=8).grid(row=1, column=column, padx=3)
            variable.trace_add("write", store_profile_history)
        profile_history_mode.trace_add("write", store_profile_history)
        profile_history_max.trace_add("write", store_profile_history)
        profile_history_selected.trace_add("write", load_profile_history)
        load_profile_history()

        def refresh_profile_history_choices(_event=None):
            """Offer profiles added, imported, renamed or deleted in the open draft."""
            if notebook.tab(notebook.select(), "text") != "History & Storage":
                return
            selected = selected_profile_history_id()
            # Mutate in place: the History actions above share this mapping.
            profile_choices.clear()
            profile_choices.update(sorted_profile_history_choices(profile_settings._items))
            profile_history_combo.configure(values=tuple(profile_choices))
            label = next((label for label, identifier in profile_choices.items() if identifier == selected),
                         next(iter(profile_choices), ""))
            if profile_history_selected.get() != label:
                profile_history_selected.set(label)

        notebook.bind("<<NotebookTabChanged>>", refresh_profile_history_choices, add="+")

        def open_selected_profile_history():
            """Open the History folder of the profile chosen in the dropdown above."""
            identifier = selected_profile_history_id()
            name = next((item["name"] for item in profile_settings._items if item["id"] == identifier), None)
            if name:
                name = profile_settings._saved_history_names.get(identifier, name)
            # Without any profile there is no profile folder; show the shared root.
            folder = profile_history_directory(identifier, name, create=True) if name else HISTORY_DIR
            folder.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(folder))

        def clear_profile_history_from_settings():
            if not messagebox.askyesno(
                "Clear profile history",
                "You are about to delete all MarbleScape images in every profile History folder. Do you really want to proceed?",
                parent=root, icon="warning", default="no",
            ):
                return
            try:
                cleared = clear_history_images("profiles")
                status_variables["history_action"].set(
                    f"Cleared {cleared['files']} profile-history image(s) ({format_disk_usage(cleared['bytes'])})."
                )
            except Exception as exc:
                messagebox.showerror("Unable to clear profile history", str(exc), parent=root)

        # Clear and Apply to all side by side on the left; Open stays on the right.
        profile_history_buttons = ttk.Frame(profile_history_frame)
        profile_history_buttons.grid(row=7, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Button(profile_history_buttons, text="Clear profile history",
                   command=clear_profile_history_from_settings).grid(row=0, column=0, sticky="w")
        ttk.Button(profile_history_buttons, text="Apply to all profiles",
                   command=apply_profile_history_to_all).grid(row=0, column=1, padx=(8, 0), sticky="w")
        ttk.Button(profile_history_frame, text="Open profile history", command=open_selected_profile_history).grid(row=7, column=2, sticky="e", pady=(5, 0))

        status_frame = ttk.LabelFrame(
            history_tab,
            text="Status and storage",
            padding=8,
        )
        status_frame.grid(
            row=4,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="nsew",
        )
        status_rows = (
            ("Normal latest images", "latest_images"),
            ("Current image size", "current_image_size"),
            ("Estimated history images", "estimated_history_images"),
            ("Estimated maximum total", "estimated_maximum_total"),
            ("Total storage estimate", "total_storage_estimate"),
            (
                "Currently used disk space (latest + cache + history)",
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

        def clear_history_from_settings():
            if not messagebox.askyesno(
                "Clear no-profile history",
                "You are about to delete all MarbleScape images in History (no profile). Do you really want to proceed?",
                parent=root, icon="warning", default="no",
            ):
                return
            clear_history_button.state(["disabled"])
            try:
                cleared = clear_history_images(None)
                status_variables["history_action"].set(
                    f"Cleared {cleared['files']} history image(s) "
                    f"({format_disk_usage(cleared['bytes'])})."
                )
            except Exception as exc:
                status_variables["history_action"].set("Unable to clear History.")
                messagebox.showerror(
                    "Unable to clear history", str(exc), parent=root
                )
            finally:
                clear_history_button.state(["!disabled"])

        clear_history_button = ttk.Button(
            history_frame,
            text="Clear history",
            command=clear_history_from_settings,
        )
        clear_history_button.grid(
            row=7, column=0, pady=(7, 0), sticky="w"
        )
        ttk.Label(
            history_frame,
            textvariable=status_variables["history_action"],
            wraplength=470,
            justify="left",
        ).grid(
            row=7, column=1, pady=(7, 0), sticky="w"
        )

        cache_frame = ttk.LabelFrame(
            profile_history_frame,
            text="Profile image cache",
            padding=8,
        )
        cache_frame.grid(
            row=8,
            column=0,
            columnspan=3,
            pady=(8, 0),
            sticky="ew",
        )
        cache_frame.columnconfigure(1, weight=1)
        ttk.Label(
            cache_frame,
            text=(
                "Keeps recent pictures, so they show again without a new download."
            ),
            wraplength=740,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, pady=(0, 6), sticky="w")
        # Two groups, each with its stored pictures and its indented limits.
        for row, label, key in (
            (1, "Saved profiles", "profile_cache"),
            (4, "Latest snapshot (no profile)", "snapshot_cache"),
        ):
            ttk.Label(cache_frame, text=label).grid(
                row=row, column=0, padx=(0, 12), pady=(4 if row > 1 else 0, 0), sticky="w"
            )
            ttk.Label(cache_frame, textvariable=status_variables[key]).grid(
                row=row, column=1, pady=(4 if row > 1 else 0, 0), sticky="w"
            )
        cache_limit_labels = {
            "profile_cache_max_size_gb": tk.StringVar(),
            "profile_cache_variants": tk.StringVar(),
            "profile_cache_snapshot_size_gb": tk.StringVar(),
        }

        def show_cache_limits(*_args):
            for key in ("profile_cache_max_size_gb", "profile_cache_snapshot_size_gb"):
                cache_limit_labels[key].set(f"{variables[key].get():.1f} GB")
            cache_limit_labels["profile_cache_variants"].set(
                str(variables["profile_cache_variants"].get())
            )

        def cache_limit_changed(*_args):
            show_cache_limits()
            update_status_section()

        for row, key, label, (low, high), step in (
            (2, "profile_cache_max_size_gb", "Size limit", PROFILE_CACHE_MAX_SIZE_GB_RANGE,
             PROFILE_CACHE_MAX_SIZE_GB_STEP),
            (3, "profile_cache_variants", "Variants per profile", PROFILE_CACHE_VARIANTS_RANGE, 1),
            (5, "profile_cache_snapshot_size_gb", "Size limit",
             PROFILE_CACHE_MAX_SIZE_GB_RANGE, PROFILE_CACHE_MAX_SIZE_GB_STEP),
        ):
            ttk.Label(cache_frame, text=label).grid(
                row=row, column=0, padx=(16, 12), pady=(4, 0), sticky="w"
            )
            limit_frame = ttk.Frame(cache_frame)
            limit_frame.grid(row=row, column=1, pady=(4, 0), sticky="ew")
            limit_frame.columnconfigure(0, weight=1)
            scale = tk.Scale(
                limit_frame, from_=low, to=high, resolution=step, orient="horizontal",
                showvalue=False, variable=variables[key], highlightthickness=0,
            )
            scale.grid(row=0, column=0, sticky="ew")
            value_label = ttk.Label(limit_frame, textvariable=cache_limit_labels[key], width=7)
            value_label.grid(row=0, column=1, padx=(6, 0), sticky="e")
            style_scale(scale, value_label)
            # The estimate follows every change, while dragging too.
            variables[key].trace_add("write", cache_limit_changed)
        show_cache_limits()

        def clear_cache_from_settings():
            if not messagebox.askyesno(
                "Clear profile image cache",
                "You are about to delete all cached profile images. Do you really want to proceed?",
                parent=root, icon="warning", default="no",
            ):
                return
            clear_cache_button.state(["disabled"])
            try:
                cleared = clear_profile_image_cache()
                status_variables["cache_action"].set(
                    f"Cleared {cleared['files']} cached image(s) "
                    f"({format_disk_usage(cleared['bytes'])})."
                )
            except Exception as exc:
                status_variables["cache_action"].set("Unable to clear the profile cache.")
                messagebox.showerror(
                    "Unable to clear cache", str(exc), parent=root
                )
            finally:
                clear_cache_button.state(["!disabled"])

        clear_cache_button = ttk.Button(
            cache_frame,
            text="Clear cache",
            command=clear_cache_from_settings,
        )
        clear_cache_button.grid(row=6, column=0, pady=(7, 0), sticky="w")
        ttk.Label(
            cache_frame,
            textvariable=status_variables["cache_action"],
            wraplength=470,
            justify="left",
        ).grid(row=6, column=1, padx=(12, 0), pady=(7, 0), sticky="w")

        def update_status_section():
            selected_time_zone = time_zone_label_to_value.get(
                variables["time_zone"].get(), DISPLAY_TIME_ZONE
            )
            status_variables["image_source"].set(image_header_status_text(selected_time_zone))
            show_auto_line(image_header_auto_parts() or False)
            force_picture_button.state(
                ["!disabled"] if force_loading_is_enabled(None) else ["disabled"]
            )
            check_picture_button.state(
                ["!disabled"] if force_loading_is_enabled(None) and not CHECK_NOW_EVENT.is_set()
                else ["disabled"]
            )
            # The estimate follows the unsaved cache sliders.
            storage = get_storage_status(
                max_size_gb=variables["profile_cache_max_size_gb"].get(),
                variants=variables["profile_cache_variants"].get(),
                snapshot_size_gb=variables["profile_cache_snapshot_size_gb"].get(),
            )
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
            status_variables["profile_cache"].set(
                f"{count_text(storage['cache_profile_files'], 'image')} of "
                f"{count_text(storage['cache_profiles'], 'profile')}, "
                f"{format_disk_usage(storage['cache_profile_bytes'])}"
            )
            status_variables["snapshot_cache"].set(
                f"{count_text(storage['cache_snapshot_files'], 'image')}, "
                f"{format_disk_usage(storage['cache_snapshot_bytes'])}"
            )
            status_variables["next_check"].set(
                format_next_check_status(selected_time_zone)
            )

        def refresh_status_section():
            update_status_section()
            refresh_shown_tones()
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
        # Labels in their natural width, the equally wide buttons right after
        # them in two aligned columns; the free space stays on the right.
        backup_frame.columnconfigure(3, weight=1)
        def open_export_folder(category):
            try:
                os.startfile(str(export_locations.initial_directory(category)))
            except Exception as exc:
                messagebox.showerror("Unable to open export folder", str(exc), parent=root)
        for row, (label, export_label, import_label, include_profiles) in enumerate((
            ("Settings", "Export settings", "Import settings", False),
            ("Settings + profiles", "Export settings + profiles", "Import settings + profiles", True),
        )):
            ttk.Label(backup_frame, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            ttk.Button(backup_frame, text=export_label,
                       command=lambda full=include_profiles: choose_backup_export(full)).grid(row=row, column=1, padx=3, pady=4, sticky="ew")
            ttk.Button(backup_frame, text=import_label,
                       command=lambda full=include_profiles: choose_backup_import(full)).grid(row=row, column=2, padx=3, pady=4, sticky="ew")
        # Folder buttons get their own row so the tab fits the minimum window width.
        ttk.Label(backup_frame, text="Folders").grid(row=2, column=0, sticky="w", pady=4, padx=(0, 12))
        for column, category in enumerate(("settings", "profiles"), 1):
            ttk.Button(backup_frame, text=f"Open {category} folder",
                       command=lambda selected=category: open_export_folder(selected)).grid(
                           row=2, column=column, padx=3, pady=4, sticky="ew")
        for column in (1, 2):
            backup_frame.columnconfigure(column, uniform="backup_buttons")
        ttk.Label(backup_frame, text=(
            "Settings only preserves the existing profile library and rotation. "
            "Settings and all profiles replaces both after confirmation. "
            "Exports start in export/settings; profile exports use export/profiles. "
            "Each successful destination is remembered separately; unavailable folders reset to the default. "
            "Backups may contain private settings; keep them safe."
        ), wraplength=650, justify="left").grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        sources_frame = ttk.LabelFrame(
            sources_tab, text="Satellite imagery viewers", padding=10
        )
        sources_frame.grid(row=0, column=0, sticky="ew")
        sources_frame.columnconfigure(0, weight=1)
        ttk.Label(
            sources_frame,
            text=("Open a viewer to browse the imagery used by MarbleScape. "
                  "Select a URL to open it in your default browser."),
            wraplength=650, justify="left",
        ).grid(row=0, column=0, pady=(0, 8), sticky="w")
        allowed_source_urls = {
            url for _provider, urls in SOURCE_VIEWER_URLS for url in urls
        }

        def open_source_reference(url):
            if url not in allowed_source_urls:
                return
            try:
                if not webbrowser.open(url, new=2):
                    raise RuntimeError("The web browser could not be opened.")
            except Exception as exc:
                messagebox.showerror(
                    "Unable to open source", str(exc), parent=root
                )

        source_row = 1
        for provider_name, urls in SOURCE_VIEWER_URLS:
            ttk.Label(
                sources_frame, text=provider_name,
                font=("TkDefaultFont", 10, "bold"),
            ).grid(row=source_row, column=0, pady=(8 if source_row > 1 else 0, 2), sticky="w")
            source_row += 1
            for url in urls:
                link = tk.Label(
                    sources_frame, text=url, cursor="hand2",
                    anchor="w", justify="left", wraplength=650,
                )
                keep_palette_color(link, "foreground", "link")
                link.grid(row=source_row, column=0, pady=1, sticky="ew")
                link.bind(
                    "<Button-1>",
                    lambda _event, value=url: open_source_reference(value),
                )
                source_row += 1

        for row, (title, content) in enumerate(INFO_SECTIONS):
            info_frame = ttk.LabelFrame(info_tab, text=title, padding=10)
            info_frame.grid(row=row, column=0, pady=(0, 8), sticky="ew")
            info_frame.columnconfigure(0, weight=1)
            # Formatted (**bold**, *italic*), wrapping at the section's width.
            markup_text(info_frame, content).grid(row=0, column=0, sticky="ew")

        about_frame = ttk.LabelFrame(about_tab, text="About MarbleScape", padding=10)
        about_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        about_frame.columnconfigure(0, weight=1)
        ttk.Label(
            about_frame,
            text="MarbleScape",
            font=("TkDefaultFont", 13, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            about_frame, text=f"Version {VERSION}"
        ).grid(row=1, column=0, pady=(2, 8), sticky="w")
        ttk.Label(
            about_frame,
            text=("Real-Time Satellite Imagery for your desktop. An unofficial third-party utility, "
                  "not affiliated with or endorsed by the imagery providers. Source imagery "
                  "may contain seams, missing scans or processing artifacts."),
            wraplength=620, justify="left",
        ).grid(row=2, column=0, sticky="w")

        about_updates = ttk.LabelFrame(about_tab, text="Project & updates", padding=10)
        about_updates.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        about_updates.columnconfigure(0, weight=1)
        about_privacy = ttk.LabelFrame(about_tab, text="Privacy & local data", padding=10)
        about_privacy.grid(row=3, column=0, pady=(0, 8), sticky="ew")
        about_privacy.columnconfigure(0, weight=1)
        ttk.Label(
            about_privacy,
            text=("No analytics, telemetry, advertising, or built-in API keys. Settings, pictures, history, "
                  "caches and the log stay on this computer. MarbleScape contacts only the selected imagery "
                  "services, OpenStreetMap when you search a place, and GitHub for the update check; Copernicus "
                  "credentials go only to the Copernicus Data Space Ecosystem and never into the log.\n\n"
                  "Saved pictures carry their settings in the PNG text/EXIF (profile name and UUID, source, "
                  "time or period, location, render settings), so they can be imported as profiles; profile "
                  "JSON carries the same. Credentials and local paths are never included, but names and "
                  "coordinates can be private: review them before sharing. The profile table's Status and "
                  "History columns, rotation membership and per-profile History rules stay local and are "
                  "never exported."),
            wraplength=620, justify="left",
        ).grid(row=0, column=0, sticky="w")

        about_legal = ttk.LabelFrame(about_tab, text="License & credits", padding=10)
        about_legal.grid(row=4, column=0, pady=(0, 8), sticky="ew")
        about_legal.columnconfigure(0, weight=1)
        ttk.Label(
            about_legal,
            text=("Licensed under PolyForm Noncommercial 1.0.0. Third-party software "
                  "and imagery remain subject to their own terms. Copyright 2026 "
                  "Gittegatt."),
            wraplength=620, justify="left",
        ).grid(row=0, column=0, pady=(0, 8), sticky="w")
        ttk.Button(about_legal, text="License & attribution",
                   command=lambda: webbrowser.open(LEGAL_URL, new=2)).grid(row=1, column=0, sticky="w")
        about_actions = ttk.Frame(about_updates)
        about_actions.grid(row=0, column=0, sticky="w")
        ttk.Button(
            about_actions,
            text="Open GitHub project",
            command=lambda: webbrowser.open(PROJECT_URL, new=2),
        ).grid(row=0, column=0, padx=(0, 6))
        update_status_var = tk.StringVar(master=root, value="Update status has not been checked.")
        latest_release_url = {"value": None}

        def open_latest_release():
            url = latest_release_url["value"]
            if url:
                webbrowser.open(url, new=2)

        latest_release_button = ttk.Button(
            about_actions,
            text="Open latest release",
            command=open_latest_release,
        )
        latest_release_button.grid(row=0, column=2)
        latest_release_button.state(["disabled"])

        about_help = ttk.LabelFrame(about_tab, text="Help & support", padding=10)
        # Help & support follows Project & updates.
        about_help.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        about_help.columnconfigure(0, weight=1)
        about_reference_actions = ttk.Frame(about_help)
        about_reference_actions.grid(row=0, column=0, sticky="w")
        for column, (label, url) in enumerate((
            ("Documentation", DOCUMENTATION_URL),
            ("Privacy & network", PRIVACY_URL),
        )):
            ttk.Button(
                about_reference_actions,
                text=label,
                command=lambda value=url: webbrowser.open(value, new=2),
            ).grid(row=0, column=column, padx=(0, 6))
        support_button = ttk.Button(about_reference_actions, text="Support this project", compound="left",
                                    command=lambda: open_support_dialog(icon, None, parent=root))
        support_button.grid(row=0, column=2)

        def heart_on_support_button():
            """The PayPal heart before Support, in the text color of the mode."""
            from PIL import ImageTk
            color = ttk.Style(root).lookup(".", "foreground") or "black"
            rgb = tuple(value // 257 for value in root.winfo_rgb(color))
            image = getattr(support_button, "_marblescape_heart", None)
            if image is None:
                image = ImageTk.PhotoImage(support_icon("heart", rgb), master=root)
                support_button._marblescape_heart = image
                support_button.configure(image=image)
            else:
                image.paste(support_icon("heart", rgb))

        heart_on_support_button()
        on_theme_change(support_button, heart_on_support_button)

        def check_for_updates():
            check_update_button.state(["disabled"])
            latest_release_button.state(["disabled"])
            update_status_var.set("Checking GitHub for updates...")

            def finish(result=None, error=None):
                check_update_button.state(["!disabled"])
                if error:
                    update_status_var.set(str(error))
                    return
                latest_release_url["value"] = result["url"]
                latest_release_button.state(["!disabled"])
                if result["update_available"]:
                    update_status_var.set(
                        f"Update available: {result['latest']} (installed: {VERSION})."
                    )
                    if should_show_update_notification(result):
                        run_update_notice_dialog(result, parent=root)
                else:
                    update_status_var.set(
                        f"MarbleScape is up to date ({VERSION}); latest public version: "
                        f"{result['latest']}."
                    )

            def worker():
                try:
                    result = check_github_update()
                    try:
                        root.after(0, lambda: finish(result=result))
                    except tk.TclError:
                        pass
                except Exception as exc:
                    try:
                        root.after(0, lambda value=str(exc): finish(error=value))
                    except tk.TclError:
                        pass

            threading.Thread(
                target=worker, name="MarbleScape-update-check", daemon=True
            ).start()

        check_update_button = ttk.Button(
            about_actions,
            text="Check for updates",
            command=check_for_updates,
        )
        check_update_button.grid(row=0, column=1, padx=(0, 6))
        ttk.Label(
            about_updates,
            textvariable=update_status_var,
            wraplength=620,
            justify="left",
        ).grid(row=1, column=0, pady=(8, 0), sticky="w")

        # Footer. Left, top to bottom: next check, activity, download text.
        # Right: Save/OK/Close, below them the save notice (right-aligned, with
        # room for long notes); progress bar, download status and Cancel
        # download on the download text's row.
        button_frame = ttk.Frame(container)
        button_frame.grid(row=3, column=0, sticky="ew")
        button_frame.columnconfigure(0, weight=1)
        activity_label = ttk.Label(
            button_frame,
            textvariable=status_variables["activity"],
        )
        activity_label.grid(
            row=1,
            column=0,
            columnspan=2,
            padx=(0, 12),
            pady=(2, 0),
            sticky="w",
        )

        next_check_frame = ttk.Frame(button_frame)
        next_check_frame.grid(row=0, column=0, padx=(0, 12), sticky="w")
        ttk.Label(next_check_frame, text="Next check:").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            next_check_frame,
            textvariable=status_variables["next_check"],
            # Reserve room for a timestamp plus an explicit UTC offset so status
            # changes do not move the action buttons.
            width=31,
            anchor="w",
        ).grid(row=0, column=1, padx=(6, 0), sticky="w")
        # Below the buttons: "Unsaved changes" (warning color) while a Save tab's draft
        # differs, a green "✓ Saved" after saving until the next change, and on
        # Profiles, which saves without Save, PROFILES_AUTOSAVE_HINT
        # (a saved profile change shows "✓ Saved" there for a few seconds).
        save_notice = ttk.Label(button_frame, anchor="w", name="save_notice")
        # Placed, not gridded: a long note never widens the button columns, so the
        # buttons never move. Right-aligned, it ends below Close and reaches left up to
        # the activity text; a longer one is cut with "…". Row 1 keeps one line for it.
        notice_state = {"text": "", "shown": False}
        notice_probe = ttk.Label(button_frame, text="Ag")
        button_frame.rowconfigure(1, minsize=notice_probe.winfo_reqheight() + 2)
        notice_probe.destroy()

        def fit_save_notice(_event=None):
            if not notice_state["shown"]:
                save_notice.place_forget()
                return
            text = notice_state["text"]
            width = button_frame.winfo_width()
            # Only a shown window has its real widths.
            if width > 1 and button_frame.winfo_ismapped():
                room = width - (activity_label.winfo_x() + activity_label.winfo_reqwidth() + 12)
                font = ttk.Style(save_notice).lookup("TLabel", "font") or "TkDefaultFont"

                def measure(value):
                    return int(save_notice.tk.call("font", "measure", font, value))

                if measure(text) > room:
                    while text and measure(text + "…") > room:
                        text = text[:-1]
                    text = text.rstrip() + "…"
            save_notice.configure(text=text)
            row_top = button_frame.grid_bbox(0, 1)[1]
            save_notice.place(relx=1.0, x=0, y=row_top + 2, anchor="ne")

        def set_save_notice(text=None, color=None):
            """Show ``text`` in a palette ``color`` below the buttons; None hides the note."""
            notice_state.update(text=text or "", shown=text is not None)
            if text is not None:
                save_notice.configure(foreground=palette(save_notice)[color] if color else "")
            fit_save_notice()

        button_frame.bind("<Configure>", fit_save_notice, add="+")
        activity_label.bind("<Configure>", fit_save_notice, add="+")
        # "event_at": when something was last saved; a newer image outcome replaces
        # its note (and the Profiles hint); "Unsaved changes" always shows.
        save_notice_state = {"saved": False, "profiles_saved_until": 0.0, "key": None,
                             "event_at": 0.0}
        outcome_state = {"serial": None, "outcome": None, "shown_at": -1.0}
        SAVE_NOTICES = {
            "unsaved": ("Unsaved changes", "warning"),
            "saved": ("✓ Saved", "success"),
            "autosave": (PROFILES_AUTOSAVE_HINT, "link"),
        }

        # The Image tab's Save: its note and the draft it saved.
        image_save_state = {"text": None, "draft": None}

        def show_save_notice(key):
            save_notice_state["key"] = key
            if (key in (None, "saved", "image_saved", "autosave") and outcome_state["outcome"]
                    and outcome_state["shown_at"] >= save_notice_state["event_at"]):
                notice = image_outcome_notice(outcome_state["outcome"], time_zone_label_to_value.get(
                    variables["time_zone"].get(), DISPLAY_TIME_ZONE))
                if notice:
                    set_save_notice(*notice)
                    return
            if key is None:
                set_save_notice()
                return
            text, color = ((image_save_state["text"], "success") if key == "image_saved"
                           else SAVE_NOTICES[key])
            set_save_notice(text, color)

        on_theme_change(save_notice, lambda: show_save_notice(save_notice_state["key"]))
        show_save_notice(None)

        def refresh_outcome_notice():
            """A new image update outcome replaces the note below the buttons."""
            outcome = image_outcome()
            if outcome["serial"] != outcome_state["serial"]:
                # The last outcome shows when Settings opens, until something is saved.
                first = outcome_state["serial"] is None
                outcome_state.update(serial=outcome["serial"], outcome=outcome if outcome["kind"] else None,
                                     shown_at=0.0 if first else time.monotonic())
                show_save_notice(save_notice_state["key"])
            root.after(1000, refresh_outcome_notice)

        refresh_outcome_notice()

        def profile_change_saved():
            save_notice_state["profiles_saved_until"] = time.monotonic() + 4.0
            save_notice_state["event_at"] = time.monotonic()
            root.event_generate("<<SettingsDraftChanged>>")

        download_status_frame = ttk.Frame(button_frame)
        download_status_frame.grid(
            row=2, column=0, columnspan=6, pady=(2, 0), sticky="ew"
        )
        download_status_frame.columnconfigure(0, weight=1)
        button_frame.rowconfigure(2, minsize=22)
        download_status_label = ttk.Label(
            download_status_frame,
            textvariable=status_variables["download"],
            anchor="w",
        )
        download_status_label.grid(row=0, column=0, padx=(0, 12), sticky="ew")
        download_progress_value = tk.DoubleVar(value=0.0)
        download_progress_bar = ttk.Progressbar(
            download_status_frame,
            variable=download_progress_value,
            maximum=100.0,
            length=210,
            mode="determinate",
        )
        download_progress_bar.grid(row=0, column=1, sticky="e")
        download_progress_bar.grid_remove()
        completion_status = tk.StringVar(value="")
        ttk.Label(download_status_frame, textvariable=completion_status).grid(
            row=0, column=2, padx=(10, 0), sticky="e"
        )

        def cancel_download():
            if DOWNLOAD_PROGRESS.request_cancel():
                # Queued background profile downloads are cancelled as well.
                clear_profile_refresh_queue()
                status_variables["download"].set("Cancelling download...")
                cancel_download_button.state(["disabled"])

        cancel_download_button = ttk.Button(
            download_status_frame,
            text="Cancel download",
            command=cancel_download,
        )
        cancel_download_button.grid(row=0, column=3, padx=(10, 0), sticky="e")
        cancel_download_button.state(["disabled"])

        def refresh_download_status():
            show_speed = bool(variables["show_download_speed"].get())
            show_progress = bool(variables["show_download_progress"].get())
            show_size = bool(variables["show_download_size"].get())
            show_bar = bool(variables["show_download_progress_bar"].get())
            keep_completed_visible = bool(
                variables["keep_completed_download_visible"].get()
            )
            snapshot = DOWNLOAD_PROGRESS.snapshot(
                keep_completed_visible=keep_completed_visible
            )
            completion_status.set(download_completion_text(snapshot))
            if (
                snapshot["active"]
                and snapshot["cancellable"]
                and not snapshot["cancel_requested"]
            ):
                cancel_download_button.state(["!disabled"])
            else:
                cancel_download_button.state(["disabled"])
            if (
                snapshot["active"]
                and snapshot["cancel_requested"]
                and not (show_speed or show_progress or show_size)
            ):
                status_variables["download"].set("Cancelling download...")
            elif (
                snapshot["cancelled"]
                and snapshot["visible"]
                and not (show_speed or show_progress or show_size)
            ):
                status_variables["download"].set("Download cancelled.")
            elif snapshot["visible"] and (show_speed or show_progress or show_size):
                status_variables["download"].set(format_download_progress(
                    snapshot,
                    show_speed=show_speed,
                    speed_unit=variables["download_speed_unit"].get(),
                    show_progress=show_progress,
                    show_size=show_size,
                ))
            else:
                status_variables["download"].set("")
            if snapshot["visible"] and show_bar:
                download_progress_bar.grid()
                if snapshot["percent"] is None:
                    download_progress_bar.configure(mode="indeterminate")
                    download_progress_value.set((time.monotonic() * 35.0) % 100.0)
                else:
                    download_progress_bar.configure(mode="determinate")
                    download_progress_value.set(snapshot["percent"])
            else:
                download_progress_bar.grid_remove()
                download_progress_value.set(0.0)
            root.after(200, refresh_download_status)

        refresh_download_status()

        def save_settings(close_after=False, dry_run=False, load_image=True):
            """Save the open draft.

            ``dry_run`` never writes or asks: it returns what Save would write
            (configuration transform, serialized profiles, Windows startup), or
            None when the draft cannot be saved as is. On the Image tab
            ``load_image=False`` (its Save) keeps the picture on screen: the
            saved settings apply at the next regular image check.
            """
            selected_tab = notebook.tab(notebook.select(), "text")
            image_only = selected_tab == "Image" and not close_after and not dry_run
            settings_only = (selected_tab != "Image" and not close_after) or dry_run

            if image_only:
                save_was_pending = save_is_pending()
                try:
                    # Apply Image owns only the Image tab. Its snapshot holds
                    # no monitor output or folder settings: those live on other
                    # tabs and keep their saved values until Save.
                    image_snapshot = capture_image_form()
                    requested_auth = source_settings.get_copernicus_auth()

                    def transform_image_configuration(text):
                        updated = replace_image_settings(text, image_snapshot)
                        updated = replace_copernicus_auth_configuration(updated, requested_auth)
                        updated = ensure_profile_list_configuration_section(updated)
                        return replace_toml_section_value(
                            updated, "profile_list", "applied_profile_id",
                            image_form_state["profile_id"],
                        )

                    changed = update_active_configuration(transform_image_configuration)
                except Exception as exc:
                    messagebox.showerror("Unable to save Image settings", str(exc), parent=root)
                    return
                if changed or load_image:
                    # Apply Image also loads a picture an earlier Save only scheduled.
                    request_runtime_configuration_reload(icon, load_image=load_image)
                else:
                    icon.update_menu()
                if not load_image:
                    with ROTATION_STATUS_LOCK:
                        shown_id = ROTATION_STATUS.get("active_profile_id")
                    rotation_shown = any(item["id"] == shown_id for item in IMAGE_PROFILE_LIBRARY.get("items", []))
                    selected_zone = time_zone_label_to_value.get(variables["time_zone"].get(), DISPLAY_TIME_ZONE)
                    image_save_state.update(
                        text=image_save_notice(changed, rotation_shown, UPDATE_INTERVAL_MINUTES, selected_zone),
                        draft=portable_settings(image_snapshot, normalize_image_settings_snapshot))
                    save_notice_state["event_at"] = time.monotonic()
                else:
                    image_save_state.update(text=None, draft=None)
                provider, selections = source_settings.get_selection()
                if provider == "eumetsat":
                    saved_layer_state["value"] = selections["eumetsat"]["layer"]
                image_form_state.update(base=image_snapshot, loaded=False)
                if not save_was_pending:
                    # Nothing else was waiting for Save; compare anew.
                    save_baseline["draft"] = None
                return

            raw_values = {
                key: variable.get()
                for key, variable in variables.items()
            }
            raw_values.pop("output_device")
            raw_values.update(global_output_draft)
            raw_values["position"] = global_position_draft["value"]
            raw_values["monitor_positions"] = dict(monitor_positions_draft)
            raw_values["paused_displays"] = sorted(paused_displays_draft)
            raw_values["monitor_output_settings"] = deepcopy(monitor_outputs_draft)
            try:
                if settings_only:
                    try:
                        validation_source, _draft_profiles = source_settings.get_selection()
                    except ValueError:
                        if not dry_run:
                            raise
                        # An unfinished Image selection is not saved by Save.
                        validation_source = IMAGE_SOURCE
                    requested_source = IMAGE_SOURCE
                    requested_profiles = deepcopy(SOURCE_PROFILES)
                    # Save also stores the credentials of the Access tab.
                    requested_copernicus_auth = source_settings.copernicus_settings.get_auth(require=False)
                    raw_values.update(
                        zoom=str(ZOOM), fit_mode=VIEW_MODE,
                        view_preset=VIEW_PRESET, projection=PROJECTION,
                        truecolor_black_night=TRUECOLOR_BLACK_NIGHT,
                        eumetsat_render_scale=get_render_scale_setting(),
                    )
                    for key, _label in CUSTOM_AREA_FIELDS:
                        raw_values.pop(key)
                else:
                    requested_source, requested_profiles = source_settings.get_selection()
                    requested_copernicus_auth = source_settings.get_copernicus_auth()
                time_zone_label = raw_values["time_zone"]
                if time_zone_label not in time_zone_label_to_value:
                    raise ValueError("Display time zone is invalid.")
                raw_values["time_zone"] = time_zone_label_to_value[time_zone_label]
                preset_label = raw_values.pop("view_preset")
                if settings_only:
                    raw_values["view_preset"] = preset_label
                else:
                    if preset_label not in preset_label_to_value:
                        raise ValueError("View preset is invalid.")
                    raw_values["view_preset"] = preset_label_to_value[preset_label]
                    custom_area_form_values(raw_values, raw_values["view_preset"])

                raw_values.pop("satellite_layer")
                requested_layer = requested_profiles["eumetsat"]["layer"]
                if (
                    requested_layer is None
                    and requested_layer != saved_layer_state["value"]
                ):
                    raise ValueError("Satellite layer cannot be empty.")

                updates = normalize_settings_form_values(
                    raw_values,
                    provider=validation_source if settings_only else requested_source,
                )
                if settings_only:
                    updates = tuple(update for update in updates if update[0] != "view"
                                    and update[:2] != ("output", "render_scale"))
                requested_library = profile_settings.get_library()
                image_snapshot = None if settings_only else capture_image_form(updates)
                startup_before_save = is_windows_startup_enabled()
                requested_startup = bool(raw_values["start_with_windows"])
                startup_changed = requested_startup != startup_before_save
                if startup_changed and not dry_run:
                    startup_snapshot = capture_windows_startup_state()
                    set_windows_startup_enabled(requested_startup)

                try:
                    def transform_configuration(text):
                        updated = replace_toml_values(
                            ensure_cache_configuration_section(
                                ensure_download_configuration_section(
                                    ensure_display_configuration_section(text)
                                )
                            ),
                            updates,
                        )
                        if settings_only:
                            pass
                        elif image_form_state["loaded"]:
                            updated = replace_image_settings(updated, image_snapshot)
                        else:
                            updated = replace_source_configuration(
                                updated,
                                requested_source,
                                requested_profiles,
                                bool(raw_values["check_for_source_updates"]),
                            )
                        if not image_form_state["loaded"] and requested_source == "eumetsat" and requested_layer != saved_layer_state["value"]:
                            updated = replace_primary_wms_layer_name(
                                updated,
                                requested_layer,
                            )
                        updated = replace_copernicus_auth_configuration(
                            updated, requested_copernicus_auth
                        )
                        updated = ensure_profile_list_configuration_section(updated)
                        if not settings_only:
                            # Save on other tabs keeps the applied profile as saved.
                            updated = replace_toml_section_value(
                                updated, "profile_list", "applied_profile_id",
                                image_form_state["profile_id"],
                            )
                        return write_table_layout(updated)

                    if dry_run:
                        return {
                            "transform": transform_configuration,
                            "profiles": serialize_library(normalize_library(requested_library)),
                            "startup": requested_startup,
                        }
                    changed = update_active_configuration_and_profiles(
                        transform_configuration, requested_library
                    )
                except Exception as config_error:
                    if startup_changed and not dry_run:
                        try:
                            restore_windows_startup_state(startup_snapshot)
                        except Exception as rollback_error:
                            raise RuntimeError(
                                f"{config_error} Windows startup rollback also "
                                f"failed: {rollback_error}"
                            ) from config_error
                    raise
            except Exception as exc:
                if dry_run:
                    return None
                messagebox.showerror(
                    "Unable to save settings",
                    str(exc),
                    parent=root,
                )
                return

            history_rename_errors = profile_settings.commit_history_renames()
            if history_rename_errors:
                messagebox.showwarning("History folder rename incomplete",
                                       "Profile settings were saved, but these History folders could not be renamed:\n"
                                       + "\n".join(history_rename_errors), parent=root)
            warn_history_deletion_errors(profile_settings.commit_history_deletions())
            if changed or not settings_only:
                # OK also loads a picture an earlier Save only scheduled.
                request_runtime_configuration_reload(icon, load_image=not settings_only)
            else:
                icon.update_menu()
            if not settings_only and requested_source == "eumetsat":
                saved_layer_state["value"] = requested_layer
            if not settings_only:
                image_form_state.update(base=image_snapshot, loaded=False)
            saved_refresh_time.set(f"Saved time: {raw_values['catalogue_refresh_time']} (system time)")
            saved_appearance = next(
                (value for section, key, value in updates if (section, key) == ("display", "appearance")),
                None,
            )
            if saved_appearance is not None and not close_after:
                # Show a newly saved appearance at once, before the reload reaches the worker.
                root._marblescape_saved_appearance = saved_appearance
                try:
                    apply_appearance(root, saved_appearance)
                except Exception as exc:
                    log(f"Window appearance warning: {exc}")
            if close_after:
                root.destroy()
            else:
                save_baseline["draft"] = None
                save_notice_state["saved"] = True
                save_notice_state["event_at"] = time.monotonic()
                update_apply_button()

        apply_button = ttk.Button(button_frame, text="Save", command=save_settings)
        # Image tab only: Save stores its choices without loading a picture now;
        # Apply Image left of it also loads one. Other tabs have one Save. Save
        # stands right before OK on every tab.
        image_save_button = ttk.Button(button_frame, text="Save",
                                       command=lambda: save_settings(load_image=False))
        image_save_button.grid(row=0, column=3, padx=(8, 0), sticky="ew")
        # Save (Apply Image), OK and Close are equally wide: uniform columns,
        # each with the same gap before its button.
        apply_button.grid(row=0, column=3, padx=(8, 0), sticky="ew")
        for column in (3, 4, 5):
            button_frame.columnconfigure(column, uniform="footer_buttons")

        def match_widest_footer_button():
            # As wide as the widest of them, Save's "Apply Image" included, so
            # switching tabs does not resize them.
            widths = []
            for text in ("Save", "Apply Image", "OK", "Close"):
                probe = ttk.Button(button_frame, text=text)
                widths.append(probe.winfo_reqwidth())
                probe.destroy()
            for column in (3, 4, 5):
                button_frame.columnconfigure(column, minsize=max(widths) + 8)
            footer_button_width["value"] = max(widths) + 8
            show_image_save_button()

        footer_button_width = {"value": 0}

        def show_image_save_button():
            # Shown, it is as wide as the others; hidden, its column takes no room.
            on_image = notebook.tab(notebook.select(), "text") == "Image"
            if on_image:
                apply_button.grid(column=2)
                image_save_button.grid(column=3)
                button_frame.columnconfigure(2, minsize=footer_button_width["value"], uniform="footer_buttons")
            else:
                image_save_button.grid_remove()
                apply_button.grid(column=3)
                button_frame.columnconfigure(2, minsize=0, uniform="")

        on_theme_change(apply_button, match_widest_footer_button)
        apply_button_poll = {"after_id": None}
        # What Save would write when the dialog opened or after the last save.
        # Comparing against it, rather than against the file, ignores format
        # upgrades that any save also writes.
        save_baseline = {"draft": None}

        # What Save would write is costly to build (the whole configuration and
        # profile list); it is built anew only when one of its inputs changed, and
        # at least every SAVE_CHECK_REFRESH_SECONDS as a safety net.
        save_check_cache = {"key": None, "value": None, "at": 0.0}

        def save_check_inputs():
            """A cheap key of everything the Save check reads."""
            def stat(path):
                try:
                    status = Path(path).stat()
                    return status.st_mtime_ns, status.st_size
                except OSError:
                    return None
            return (
                tuple((name, variable.get()) for name, variable in variables.items()),
                json.dumps([global_output_draft, global_position_draft["value"], monitor_positions_draft,
                            sorted(paused_displays_draft), monitor_outputs_draft], sort_keys=True, default=str),
                repr(source_settings.copernicus_settings.get_auth(require=False)),
                profile_settings.library_fingerprint(),
                (tuple(profile_settings.get_visible_columns()), tuple(profile_settings.get_column_order()),
                 tuple(sorted(profile_settings.get_column_widths().items())),
                 tuple(sorted(profile_settings.get_sort_settings().items())), profile_settings.get_table_rows()),
                is_windows_startup_enabled(),
                stat(ACTIVE_CONFIG_PATH), stat(ACTIVE_PROFILE_LIBRARY_PATH),
                IMAGE_SOURCE, saved_layer_state["value"], id(save_baseline["draft"]),
            )

        def save_is_pending():
            try:
                key = save_check_inputs()
            except Exception:
                key = None
            now = time.monotonic()
            if (key is not None and key == save_check_cache["key"]
                    and now - save_check_cache["at"] < SAVE_CHECK_REFRESH_SECONDS):
                return save_check_cache["value"]
            value = save_is_pending_now()
            # A first check sets the baseline: key the result with it.
            save_check_cache.update(key=save_check_inputs() if key is not None else None, value=value, at=now)
            return value

        def save_is_pending_now():
            try:
                draft = save_settings(dry_run=True)
            except Exception:
                draft = None
            if draft is None:
                # Keep Save available so clicking it reports the problem.
                return True
            baseline = save_baseline["draft"]
            if baseline is None:
                save_baseline["draft"] = draft
                return False
            if draft["profiles"] != baseline["profiles"] or draft["startup"] != baseline["startup"]:
                return True
            text = read_active_configuration_text()
            return draft["transform"](text) != baseline["transform"](text)

        def update_apply_button(_event=None):
            """Apply Image stays available; Save only when the draft differs."""
            selected = notebook.tab(notebook.select(), "text")
            show_image_save_button()
            if selected == "Image":
                apply_button.configure(text="Apply Image", state="normal")
                # Save's note stays until the Image tab's draft changes.
                if image_save_state["text"]:
                    try:
                        draft = portable_settings(capture_image_form(), normalize_image_settings_snapshot)
                    except (ValueError, TypeError):
                        draft = None
                    if draft != image_save_state["draft"]:
                        image_save_state.update(text=None, draft=None)
                show_save_notice("image_saved" if image_save_state["text"] else None)
                return
            try:
                pending = save_is_pending()
            except Exception:
                pending = True
            apply_button.configure(text="Save", state="normal" if pending else "disabled")
            if pending:
                save_notice_state["saved"] = False
            if selected == "Profiles":
                recently_saved = time.monotonic() < save_notice_state["profiles_saved_until"]
                show_save_notice("saved" if recently_saved else "autosave")
            elif pending:
                show_save_notice("unsaved")
            else:
                show_save_notice("saved" if save_notice_state["saved"] else None)

        def poll_apply_button():
            apply_button_poll["after_id"] = None
            try:
                update_apply_button()
            except tk.TclError:
                return
            apply_button_poll["after_id"] = root.after(400, poll_apply_button)

        notebook.bind("<<NotebookTabChanged>>", update_apply_button, add="+")
        # Tests and other code can request an immediate state check.
        root.bind("<<SettingsDraftChanged>>", update_apply_button, add="+")
        save_is_pending()
        update_apply_button()
        root.after(400, poll_apply_button)
        ttk.Button(
            button_frame,
            text="OK",
            command=lambda: save_settings(close_after=True),
        ).grid(row=0, column=4, padx=(8, 0), sticky="ew")
        ttk.Button(button_frame, text="Close", command=root.destroy).grid(
            row=0, column=5, padx=(8, 0), sticky="ew"
        )

        root.protocol("WM_DELETE_WINDOW", root.destroy)

        def cancel_dialog_timers(event):
            if event.widget is root:
                # Each dialog owns a Tk interpreter. Cancel pending status and
                # focus callbacks before their Python commands are destroyed.
                for timer in root.tk.call("after", "info"):
                    root.tk.call("after", "cancel", timer)

        # The window size is saved at once, like the table layout (no Save):
        # shortly after a resize and when the window closes. A maximized or
        # minimized window keeps the last normal size.
        window_size = {"pending": None, "after_id": None, "opened": None, "scale": 1.0}

        def save_window_size():
            global SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT
            window_size["after_id"] = None
            size, window_size["pending"] = window_size["pending"], None
            if size is None or size == (SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT):
                return

            def write(text):
                updated = ensure_display_configuration_section(text)
                updated = replace_toml_section_value(updated, "display", "settings_window_width", size[0])
                return replace_toml_section_value(updated, "display", "settings_window_height", size[1])

            try:
                update_active_configuration(write)
            except Exception as exc:
                log(f"Settings window size was not saved: {exc}")
                return
            SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT = size

        def window_resized(event):
            if event.widget is not root or window_size["opened"] is None:
                # Not shown at its opening size yet.
                return
            try:
                if str(root.state()) != "normal":
                    return
            except tk.TclError:
                return
            scale = window_size["scale"]
            size = (max(SETTINGS_MIN_WIDTH, round(event.width / scale)),
                    max(SETTINGS_MIN_HEIGHT, round(event.height / scale)))
            if size == window_size["opened"] and window_size["pending"] is None:
                return
            window_size["pending"] = size
            if window_size["after_id"] is not None:
                root.after_cancel(window_size["after_id"])
            window_size["after_id"] = root.after(500, save_window_size)

        def save_window_size_on_close(event):
            if event.widget is root:
                save_window_size()

        root.bind("<Configure>", window_resized, add="+")

        def open_last_tab():
            """Open on the tab used last; save each tab change at once (no Save)."""
            tab_ids = {notebook.tab(tab_id, "text"): tab_id for tab_id in notebook.tabs()}
            if SETTINGS_TAB in tab_ids:
                notebook.select(tab_ids[SETTINGS_TAB])

            def save_tab(_event=None):
                global SETTINGS_TAB
                try:
                    title = notebook.tab(notebook.select(), "text")
                except tk.TclError:
                    return
                if title == SETTINGS_TAB:
                    return

                def write(text):
                    return replace_toml_section_value(
                        ensure_display_configuration_section(text), "display", "settings_tab", title)

                try:
                    update_active_configuration(write)
                except Exception as exc:
                    log(f"Settings tab was not saved: {exc}")
                    return
                SETTINGS_TAB = title

            notebook.bind("<<NotebookTabChanged>>", save_tab, add="+")

        open_last_tab()
        # Before cancel_dialog_timers drops a pending save.
        root.bind("<Destroy>", save_window_size_on_close, add="+")
        root.bind("<Destroy>", cancel_dialog_timers, add="+")
        def bind_page_scrolling(widget):
            # Handle wheel input before combobox class bindings can change a value.
            # Native dropdown popups keep their own scrolling bindings.
            widget.bindtags((scroll_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                bind_page_scrolling(child)

        for page in notebook.tabs():
            bind_page_scrolling(root.nametowidget(page))

        # Texts wrap where their section ends, not at a fixed width. Profiles
        # sizes its own texts, and so does the Copernicus cloud hint.
        def wrapped_labels(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label):
                    yield child
                yield from wrapped_labels(child)

        profiles_root = str(profile_settings.frame)
        for page in notebook.tabs():
            for label in wrapped_labels(root.nametowidget(page)):
                try:
                    wraps = int(float(str(label.cget("wraplength")) or 0)) > 0
                except (ValueError, tk.TclError):
                    wraps = False
                if (wraps and not getattr(label, "_marblescape_own_wrap", False)
                        and not str(label).startswith(profiles_root)):
                    follow_wrap_width(label)
        for section in (
            preset_frame, generic_view_frame,
            catalogue_schedule_frame,
            latest_folder_frame, history_folder_frame, history_frame, status_frame, cache_frame,
        ):
            section.columnconfigure(1, weight=1)
        # Dropdowns and fields keep their natural, shared width instead of
        # spanning the window: the field column does not grow, a spare third
        # column takes the rest, and texts spanning the row span it too.
        for section in (
            display_time_frame, output_device_frame, output_frame, appearance_frame,
            update_frame, download_display_frame, download_retry_frame, catalogue_retry_frame,
        ):
            section.columnconfigure(1, weight=0)
            section.columnconfigure(2, weight=1)
            for child in section.grid_slaves():
                info = child.grid_info()
                if int(info.get("column", 0)) == 0 and int(info.get("columnspan", 1)) >= 2:
                    child.grid_configure(columnspan=3)
        general_sections = (display_time_frame, output_device_frame, output_frame, appearance_frame)

        def align_general_fields():
            # On General every dropdown and field starts at one line: each label
            # column is as wide as the widest label of all its sections.
            widest = 0
            for section in general_sections:
                for child in section.grid_slaves(column=0):
                    info = child.grid_info()
                    if int(info.get("columnspan", 1)) == 1:
                        padx = info.get("padx", 0)
                        pad = sum(int(value) for value in padx) if isinstance(padx, tuple) else 2 * int(padx)
                        widest = max(widest, child.winfo_reqwidth() + pad)
            for section in general_sections:
                section.columnconfigure(0, minsize=widest)

        on_theme_change(output_frame, align_general_fields)
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
        # The saved size, at least the minimum; by default the minimum width.
        window_width = min(round(max(SETTINGS_MIN_WIDTH, SETTINGS_WINDOW_WIDTH or SETTINGS_DEFAULT_WIDTH)
                                 * scale), usable_width)
        window_height = min(round(max(SETTINGS_MIN_HEIGHT, SETTINGS_WINDOW_HEIGHT or SETTINGS_DEFAULT_HEIGHT)
                                  * scale), usable_height)
        root.minsize(min(round(SETTINGS_MIN_WIDTH * scale), usable_width),
                     min(round(SETTINGS_MIN_HEIGHT * scale), usable_height))
        window_size.update(scale=scale, opened=(max(SETTINGS_MIN_WIDTH, round(window_width / scale)),
                                                max(SETTINGS_MIN_HEIGHT, round(window_height / scale))))
        window_x = left + max(0, (right - left - window_width) // 2)
        window_y = top + max(0, (bottom - top - window_height - round(32 * scale)) // 2)
        root.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        support_poll_id = None

        def poll_support_requests():
            nonlocal support_poll_id
            with settings_dialog_lock:
                requested = settings_dialog_state["support_requested"]
                settings_dialog_state["support_requested"] = False
                activate = settings_dialog_state["activate_requested"]
                settings_dialog_state["activate_requested"] = False
            if activate and not APPLICATION_STOP_EVENT.is_set():
                activate_settings_window(root)
            if requested and not APPLICATION_STOP_EVENT.is_set():
                open_support_dialog(icon, None, parent=root)
            support_poll_id = root.after(100, poll_support_requests)

        with settings_dialog_lock:
            settings_dialog_state["support_parent_available"] = True
        support_poll_id = root.after(0, poll_support_requests)
        try:
            root.mainloop()
        finally:
            with settings_dialog_lock:
                settings_dialog_state.update(support_parent_available=False, support_requested=False,
                                             activate_requested=False)
            if support_poll_id is not None:
                try:
                    root.after_cancel(support_poll_id)
                except tk.TclError:
                    pass
            source_settings.close()
            profile_settings.close()

    def open_settings_dialog(icon, item):
        del item
        if APPLICATION_STOP_EVENT.is_set():
            return
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                settings_dialog_state["activate_requested"] = True
                return
            settings_dialog_state["open"] = True

        def settings_worker():
            try:
                run_settings_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to open settings", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state.update(open=False, activate_requested=False)

        start_gui_worker(settings_worker, "MarbleScapeSettings")

    def run_backup_export_dialog():
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected_path = filedialog.asksaveasfilename(
                parent=root,
                title="Export MarbleScape settings backup",
                initialdir=str(export_locations.initial_directory("settings")),
                initialfile=settings_backup_filename(True),
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            saved_path = export_settings_backup(selected_path)
            if not export_locations.remember("settings", saved_path.parent):
                messagebox.showwarning("Export folder", "The backup was exported, but the folder could not be remembered for next time.", parent=root)
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

        start_gui_worker(backup_export_worker, "MarbleScapeBackupExport")

    def run_backup_import_dialog(icon):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        config_text = None
        profile_library = None
        startup_enabled = None
        try:
            selected_path = filedialog.askopenfilename(
                parent=root,
                title="Import MarbleScape settings backup",
                initialdir=str(settings_backup_directory()),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return

            try:
                config_text, profile_library, startup_enabled = import_settings_backup(
                    selected_path,
                    confirm_repair=lambda label, changes: confirm_import_repair(root, label, changes, allow_skip=False)
                )
            except ImportCancelled:
                return
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

        apply_settings_backup(icon, config_text, profile_library, startup_enabled)

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

        start_gui_worker(backup_import_worker, "MarbleScapeBackupImport")

    def run_background_color_picker(icon):
        import tkinter as tk
        from tkinter import colorchooser

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
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

        start_gui_worker(color_picker_worker, "MarbleScapeColorPicker")

    def run_custom_render_factor_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
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

        start_gui_worker(render_factor_worker, "MarbleScapeRenderQuality")

    def run_custom_resolution_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        configured_height = 0 if HEIGHT is None else HEIGHT
        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
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

        start_gui_worker(resolution_worker, "MarbleScapeResolution")

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

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
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

        start_gui_worker(aspect_ratio_worker, "MarbleScapeAspectRatio")

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

    def profile_rotation_is_enabled(item):
        del item
        return bool(IMAGE_PROFILE_LIBRARY.get("rotation", {}).get("enabled"))

    def toggle_profile_rotation(icon, item):
        del item
        try:
            with CONFIGURATION_FILE_LOCK:
                # The file, not the update loop's copy: a list saved in Settings
                # that the loop has not loaded yet (during a download) must stay.
                library = (read_profile_library_file() if ACTIVE_PROFILE_LIBRARY_PATH.exists()
                           else deepcopy(IMAGE_PROFILE_LIBRARY))
                if not library.get("items"):
                    raise ValueError(
                        "Add at least one image profile before enabling rotation."
                    )
                rotation = library.setdefault("rotation", {})
                rotation["enabled"] = not bool(rotation.get("enabled"))
                changed = write_profile_library_file_unlocked(library)
            if changed:
                with ROTATION_STATUS_LOCK:
                    ROTATION_SWITCH["serial"] += 1
                    ROTATION_SWITCH["enabled"] = rotation["enabled"]
                request_runtime_configuration_reload(icon)
        except Exception as exc:
            show_tray_error(icon, "Unable to change profile rotation", exc)

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
        """The image activity; a running catalogue refresh follows after " | "."""
        del item
        state, _next_check = get_tray_status_snapshot()

        if state == "fetching":
            text = activity_text("Fetching new image...")
        elif state == "checking":
            text = activity_text("Checking for new image...")
        else:
            text = "Standing by..."
            if IMAGE_SOURCE != "eumetsat":
                with IMAGE_STATUS_LOCK:
                    unavailable = bool(IMAGE_STATUS["error"])
                if unavailable:
                    text = "Source unavailable - keeping previous image"
        try:
            catalogues = catalogue_refresh_activity_text()
        except Exception:
            catalogues = ""
        return f"{text} | {catalogues}" if catalogues else text

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

    tray_icon = create_windows_tray_icon(
        "MarbleScape",
        create_windows_tray_image(),
        "MarbleScape - Real-Time Satellite Imagery for your desktop.",
        menu=pystray.Menu(
            pystray.MenuItem("Open image folder", open_output_folder),
            pystray.MenuItem(
                "Settings...",
                open_settings_dialog,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Profile rotation",
                toggle_profile_rotation,
                checked=profile_rotation_is_enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Force loading new image",
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
                "Support this project",
                open_support_dialog,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start minimized with Windows",
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
        start_display_watch()
        warm_public_catalogues()
        if not tray_args.background:
            open_settings_dialog(icon, None)
            check_for_startup_update()

    tray_icon.run(setup=tray_setup)
    APPLICATION_STOP_EVENT.set()
    NETWORK_ACTIVITY.cancel()
    DOWNLOAD_PROGRESS.request_cancel()
    # Keep Python alive until each Tk loop has handled its shutdown event.
    with gui_threads_lock:
        pending_gui_threads = list(gui_threads)
    for gui_thread in pending_gui_threads:
        gui_thread.join()
    worker.join()
    release_windows_single_instance()
    return result["exit_code"]


# =============================================================================
# PROGRAM ENTRY POINT
# =============================================================================


if __name__ == "__main__":
    enable_log_file()
    if not acquire_instance_for_start():
        log("MarbleScape is already running.")
        sys.exit(0)
    log(f"MarbleScape {VERSION} started (process {os.getpid()}).")
    FORCED_EXIT["armed"] = True
    if should_use_windows_tray():
        sys.exit(run_with_windows_tray(migrate_startup=True))
    sys.exit(run_application())
