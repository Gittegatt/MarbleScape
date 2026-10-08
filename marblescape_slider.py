"""CIRA SLIDER still-image catalogues and tiled image rendering.

SLIDER publishes each acquisition as a square PNG tile pyramid.  MarbleScape
uses the newest timestamp only and downloads the product tiles themselves;
SLIDER's optional borders, maps, and latitude/longitude grids are separate
layers and are therefore absent from the rendered wallpaper.
"""

from __future__ import annotations

import copy
import datetime as dt
import http.client
import html
import io
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from PIL import Image, ImageColor

from marblescape_download_progress import DOWNLOAD_PROGRESS, ResponseTooLargeError, read_response
from marblescape_network import NETWORK_ACTIVITY, open_response


PROVIDER = "slider"
SITE_URL = "https://slider.cira.colostate.edu/"
DATA_BASE = SITE_URL + "data/"
CATALOGUE_URL = SITE_URL + "js/define-products---rammb-slider.js"
ALLOWED_HOSTS = frozenset(("slider.cira.colostate.edu",))
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_TILE_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_PIXELS = 33_554_432
MAX_TILES = 1024
CATALOGUE_TTL = 60 * 60
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}")


# SLIDER pads non-square sectors (for example the 5:3 GOES CONUS) with black
# rows or columns to fill its square tile grid. The measured content box, as
# fractions (left, top, right, bottom) of the grid, is kept per sector.
FULL_CONTENT_BOX = (0.0, 0.0, 1.0, 1.0)
_CONTENT_BOXES = {}
_CONTENT_BOX_LOCK = threading.Lock()
_CONTENT_BOX_STORE = {"path": None}
# Sectors whose padding no listed frame showed (all black, e.g. at night): not
# measured again from Settings while MarbleScape runs.
_UNDECIDABLE_AREAS = set()


class SliderError(RuntimeError):
    """A SLIDER response cannot be safely or unambiguously used."""


class UnavailableError(SliderError):
    """The selected SLIDER image is not currently available."""


class SelectionLostError(UnavailableError):
    """The provider no longer lists the saved area, product or size (shown as LOST)."""



def _checked_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port not in (None, 443) or parsed.username is not None
            or parsed.password is not None):
        raise SliderError("CIRA SLIDER returned an unsupported image or catalogue host.")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _valid_content_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    left, top, right, bottom = box
    if not all(math.isfinite(item) for item in box) or not (
            0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        return None
    return box


def _symmetric_padding(first, last, total):
    """Accept edge padding only when both sides match, as SLIDER centres sectors.

    At least 3% per side: the thin space margin around a full disk is image content.
    """
    tolerance = max(1, round(total * 0.01))
    if min(first, last) >= total * 0.03 and abs(first - last) <= tolerance:
        return first, total - last
    return 0, total


def content_box_from_tile(image):
    """Measure SLIDER's black padding in a level-0 tile; None when undecidable."""
    with image.convert("L") as grey:
        with grey.point(lambda value: 255 if value else 0) as mask:
            bbox = mask.getbbox()
    if bbox is None:
        return None  # An entirely black tile (for example at night) shows no edges.
    width, height = image.size
    left, right = _symmetric_padding(bbox[0], width - bbox[2], width)
    top, bottom = _symmetric_padding(bbox[1], height - bbox[3], height)
    return (left / width, top / height, right / width, bottom / height)


def configure_content_box_store(path):
    """Load measured content boxes from ``path`` and save new ones there."""
    with _CONTENT_BOX_LOCK:
        if _CONTENT_BOX_STORE["path"] == path:
            return
    boxes = {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        stored = {}
    if isinstance(stored, dict):
        for area_id, value in stored.items():
            box = _valid_content_box(value)
            if isinstance(area_id, str) and box is not None:
                boxes[area_id] = box
    with _CONTENT_BOX_LOCK:
        _CONTENT_BOX_STORE["path"] = path
        for area_id, box in boxes.items():
            _CONTENT_BOXES.setdefault(area_id, box)


def cached_content_box(area_id):
    with _CONTENT_BOX_LOCK:
        return _CONTENT_BOXES.get(area_id)


def _remember_content_box(area_id, box):
    with _CONTENT_BOX_LOCK:
        if _CONTENT_BOXES.get(area_id) == box:
            return
        _CONTENT_BOXES[area_id] = box
        path = _CONTENT_BOX_STORE["path"]
        snapshot = {key: list(value) for key, value in sorted(_CONTENT_BOXES.items())}
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass  # The measurement is repeated next time; rendering does not depend on the file.


def content_size(width, height, box):
    left, top, right, bottom = box
    return max(1, round(width * (right - left))), max(1, round(height * (bottom - top)))


def effective_resolution(area_id, resolution):
    """The visible image size of a tile-grid resolution, once its padding is known."""
    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", str(resolution))
    box = cached_content_box(area_id)
    if not match or box is None:
        return str(resolution)
    width, height = content_size(int(match.group(1)), int(match.group(2)), box)
    return f"{width}x{height}"


def _clean_label(value):
    text = re.sub(r"<[^>]*>", " ", str(value))
    return " ".join(html.unescape(text).split())


def _resolution_key(value):
    width, height = map(int, value.split("x"))
    return width * height, width, height


def _parse_catalogue_script(script):
    marker = re.search(r"\bvar\s+json\s*=", script)
    start = script.find("{", marker.end() if marker else 0)
    if start < 0:
        raise SliderError("CIRA SLIDER returned an unrecognized catalogue document.")
    try:
        document, _end = json.JSONDecoder().raw_decode(script[start:])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SliderError("CIRA SLIDER returned invalid catalogue metadata.") from exc
    if not isinstance(document, dict) or not isinstance(document.get("satellites"), dict):
        raise SliderError("CIRA SLIDER catalogue contains no satellite list.")
    return document


def _fallback_catalogue():
    """Small current fallback used when the public catalogue is unreachable."""
    satellites = {}
    specifications = (
        ("goes-19", "GOES-19 (East; 75.2W)", 678, 5, 10),
        ("goes-18", "GOES-18 (West; 137.0W)", 678, 5, 10),
        ("himawari", "Himawari-9 (140.7E)", 688, 5, 10),
        ("gk2a", "GEO-KOMPSAT-2A (128E)", 688, 5, 10),
        ("meteosat-9", "Meteosat-9 (45.5E)", 464, 3, 10),
        ("meteosat-0deg", "Meteosat-10 (0.0)", 464, 3, 10),
        ("meteosat-12", "Meteosat-12 (0.0)", 696, 5, 10),
    )
    for satellite, title, tile_size, max_level, minutes in specifications:
        satellites[satellite] = {
            "satellite_title": title,
            "default_sector": "full_disk",
            "sectors": {"full_disk": {
                "sector_title": "Full Disk", "tile_size": tile_size,
                "max_zoom_level": max_level, "default_product": "geocolor",
                "defaults": {"minutes_between_images": minutes},
            }},
            "products": {"geocolor": {
                "product_title": "GeoColor", "zoom_level_adjust": 1,
            }},
        }
    return {"defaults": {"zoom_level_adjust": 0}, "satellites": satellites}


def _valid_identifier(value):
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _area_from_catalogue(document, area_id):
    if not isinstance(area_id, str) or area_id.count("---") != 1:
        return None
    satellite_id, sector_id = area_id.split("---", 1)
    if not _valid_identifier(satellite_id) or not _valid_identifier(sector_id):
        return None
    satellite = document.get("satellites", {}).get(satellite_id)
    sectors = satellite.get("sectors", {}) if isinstance(satellite, dict) else None
    sector = sectors.get(sector_id) if isinstance(sectors, dict) else None
    if not isinstance(sector, dict):
        return None
    try:
        tile_size = int(sector["tile_size"])
        max_level = int(sector["max_zoom_level"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 64 <= tile_size <= 2048 or not 0 <= max_level <= 10:
        return None
    defaults = sector.get("defaults", {})
    try:
        minutes = float(defaults.get("minutes_between_images", 10))
    except (TypeError, ValueError):
        minutes = 10
    if not math.isfinite(minutes) or minutes <= 0:
        minutes = 10
    return {
        "id": area_id,
        "label": _clean_label(sector.get("sector_title", sector_id)),
        "category": _clean_label(satellite.get("satellite_title", satellite_id)),
        "satellite": satellite_id,
        "sector": sector_id,
        "tile_size": tile_size,
        "max_zoom_level": max_level,
        "default_product": str(sector.get("default_product") or
                               satellite.get("default_product") or "geocolor"),
        "interval": int(round(minutes * 60)),
    }


def _areas(document):
    result = []
    for satellite_id, satellite in document.get("satellites", {}).items():
        if not _valid_identifier(satellite_id) or not isinstance(satellite, dict):
            continue
        sectors = satellite.get("sectors", {})
        if not isinstance(sectors, dict):
            continue
        for sector_id in sectors:
            area = _area_from_catalogue(document, f"{satellite_id}---{sector_id}")
            if area is not None:
                result.append(area)
    if not result:
        raise SliderError("CIRA SLIDER catalogue contains no supported sectors.")
    return result


def _products(document, area):
    satellite = document["satellites"][area["satellite"]]
    sector = satellite["sectors"][area["sector"]]
    missing = {str(value) for value in sector.get("missing_products", ())}
    overrides = sector.get("products", {})
    defaults = document.get("defaults", {})
    products = []
    catalogue_products = satellite.get("products", {})
    if not isinstance(catalogue_products, dict):
        raise SliderError("CIRA SLIDER lists no supported still-image products for this sector.")
    for product_id, product in catalogue_products.items():
        if (not _valid_identifier(product_id) or product_id in missing
                or not isinstance(product, dict)):
            continue
        title = _clean_label(product.get("product_title", product_id))
        if not title or title.startswith("---"):
            continue
        override = overrides.get(product_id, {}) if isinstance(overrides, dict) else {}
        value = (override.get("zoom_level_adjust") if isinstance(override, dict)
                 and "zoom_level_adjust" in override else
                 product.get("zoom_level_adjust", defaults.get("zoom_level_adjust", 0)))
        try:
            adjustment = max(0, int(value))
        except (TypeError, ValueError):
            adjustment = 0
        maximum = area["max_zoom_level"] - adjustment
        if maximum < 0:
            continue
        # Level 5 is a 32x32 grid. Larger full-frame grids would require
        # thousands of separate responses and are not safe for a wallpaper job.
        maximum = min(maximum, int(math.log2(math.isqrt(MAX_TILES))))
        resolutions = [
            f"{area['tile_size'] * (2 ** level)}x{area['tile_size'] * (2 ** level)}"
            for level in range(maximum + 1)
        ]
        products.append({"id": product_id, "label": title, "resolutions": resolutions})
    if not products:
        raise SliderError("CIRA SLIDER lists no supported still-image products for this sector.")
    return products


def _validate_output(output_size, fit_mode, zoom, background):
    try:
        width, height = map(int, output_size)
        zoom = float(zoom)
        color = ImageColor.getrgb(str(background))
    except (TypeError, ValueError) as exc:
        raise SliderError("CIRA SLIDER output settings are invalid.") from exc
    if (width <= 0 or height <= 0 or width * height > MAX_OUTPUT_PIXELS
            or fit_mode not in ("fit", "crop") or not math.isfinite(zoom) or zoom <= 0):
        raise SliderError("CIRA SLIDER output settings exceed the supported limits.")
    return (width, height), zoom, color


class SliderClient:
    def __init__(self, timeout=90, user_agent="MarbleScape/SLIDER", opener=None):
        self.timeout = float(timeout)
        self.user_agent = str(user_agent)
        self._opener = opener
        self._lock = threading.RLock()
        self._inflight = {}
        self._catalogue = _fallback_catalogue()
        self._catalogue_time = 0.0
        self.catalogue_warning = ""

    def _coordinated(self, key, operation):
        with self._lock:
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = self._inflight[key] = Future()
        if not owner:
            return future.result()
        try:
            result = operation()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result
        finally:
            with self._lock:
                if self._inflight.get(key) is future:
                    del self._inflight[key]

    def _request(self, url, limit, track=False):
        if track:
            DOWNLOAD_PROGRESS.raise_if_cancelled()
        _checked_url(url)
        cache = getattr(self, "metadata_cache", None) if not track else None
        request_headers = {
            "User-Agent": self.user_agent, "Cache-Control": "no-cache",
        }
        if cache:
            request_headers.update(cache.headers(url))
        request = urllib.request.Request(url, headers=request_headers)
        try:
            opener = self._opener or urllib.request.build_opener(_Redirects())
            timeout = self.timeout if track else min(self.timeout, 20.0)
            with open_response(opener.open, request, timeout=timeout) as response:
                _checked_url(response.geturl())
                try:
                    body = read_response(response, limit, track=track)
                    response_headers = dict(response.headers.items())
                    if cache:
                        cache.store(url, body, response_headers)
                    return body, response_headers
                except ResponseTooLargeError:
                    raise SliderError("CIRA SLIDER response exceeds the permitted download size.")
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cache and (saved := cache.response(url, limit)) is not None:
                exc.close()
                return saved
            raise UnavailableError(
                "CIRA SLIDER request failed (HTTP %s): %s" % (exc.code, url)
            ) from exc
        # http.client.HTTPException: a response cut off midway (IncompleteRead) or garbled.
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            raise UnavailableError("CIRA SLIDER is currently unreachable: %s" % exc) from exc

    @staticmethod
    def _validate_provider(provider):
        if provider != PROVIDER:
            raise SliderError("Unknown CIRA SLIDER provider: " + str(provider))

    def _refresh_catalogue(self):
        body, _ = self._request(CATALOGUE_URL, MAX_DOCUMENT_BYTES)
        document = _parse_catalogue_script(body.decode("utf-8", errors="strict"))
        _areas(document)
        with self._lock:
            self._catalogue = copy.deepcopy(document)
            self._catalogue_time = time.monotonic()
            self.catalogue_warning = ""
        return document

    def _document(self, refresh=False):
        with self._lock:
            fresh = self._catalogue_time and time.monotonic() - self._catalogue_time < CATALOGUE_TTL
            document = copy.deepcopy(self._catalogue)
        if refresh or not fresh:
            try:
                return self._coordinated(("catalogue",), self._refresh_catalogue)
            except (SliderError, UnicodeError) as exc:
                with self._lock:
                    self.catalogue_warning = str(exc)
                    # Avoid repeating the same failed network request when the
                    # UI immediately asks for products after listing sectors.
                    self._catalogue_time = time.monotonic()
                if document:
                    return document
                raise
        return document

    def list_areas(self, provider, refresh=False):
        self._validate_provider(provider)
        return copy.deepcopy(_areas(self._document(refresh=refresh)))

    def _area(self, area_id, refresh=False):
        document = self._document(refresh=refresh)
        area = _area_from_catalogue(document, area_id)
        if area is None:
            raise SelectionLostError("The selected CIRA SLIDER sector is not available: " + str(area_id))
        return document, area

    def list_products(self, provider, area_id, refresh=False):
        self._validate_provider(provider)
        document, area = self._area(area_id, refresh=refresh)
        products = copy.deepcopy(_products(document, area))
        if cached_content_box(area_id) is not None:
            for product in products:
                product["effective_resolutions"] = {
                    value: effective_resolution(area_id, value) for value in product["resolutions"]
                }
        return products

    def _latest_timestamp_code(self, area, product_id):
        return self._timestamp_codes(area, product_id)[-1]

    def _timestamp_codes(self, area, product_id):
        """The listed frame times, oldest first (SLIDER lists about the last 100)."""
        metadata_url = urllib.parse.urljoin(
            DATA_BASE, f"json/{area['satellite']}/{area['sector']}/{product_id}/latest_times.json"
        )
        body, _ = self._request(metadata_url, MAX_DOCUMENT_BYTES)
        try:
            metadata = json.loads(body.decode("utf-8"))
            values = metadata["timestamps_int"]
            codes = sorted({str(int(value)) for value in values})
            if not codes or not re.fullmatch(r"\d{14}", codes[-1]):
                raise ValueError
            return [code for code in codes if re.fullmatch(r"\d{14}", code)]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SliderError("CIRA SLIDER returned invalid latest-image metadata.") from exc

    def content_box(self, area_id, product_id, timestamp_code=None):
        """Measure the sector's black padding once from its smallest tile.

        Without ``timestamp_code`` (Settings) an all-black newest frame is
        followed by the oldest listed one, often taken in daylight; when neither
        shows the padding, the sector is not measured again while MarbleScape runs.
        """
        cached = cached_content_box(area_id)
        if cached is not None:
            return cached
        if timestamp_code is None and area_id in _UNDECIDABLE_AREAS:
            return FULL_CONTENT_BOX
        _document, area = self._area(area_id)
        if timestamp_code is None:
            codes = self._timestamp_codes(area, product_id)
            candidates = list(dict.fromkeys((codes[-1], codes[0])))
        else:
            candidates = [timestamp_code]
        for code in candidates:
            frame = {"product": product_id, "timestamp_code": code, "level": 0}
            _x, _y, tile = self._load_tile(area, frame, 0, 0, track=False)
            try:
                box = content_box_from_tile(tile)
            finally:
                tile.close()
            if box is not None:
                _remember_content_box(area_id, box)
                return box
        if timestamp_code is None:
            _UNDECIDABLE_AREAS.add(area_id)
        return FULL_CONTENT_BOX

    def refresh_all_catalogues(self, refresh=True):
        errors = []
        try:
            document = self._refresh_catalogue() if refresh else self._document()
            with self._lock:
                cached_warning = self.catalogue_warning
            if cached_warning:
                errors.append(cached_warning)
        except (SliderError, UnicodeError) as exc:
            errors.append(str(exc))
            with self._lock:
                document = copy.deepcopy(self._catalogue)
        areas = _areas(document)
        product_count = resolution_count = 0
        for area in areas:
            try:
                products = _products(document, area)
            except SliderError:
                continue
            product_count += len(products)
            resolution_count += sum(len(item["resolutions"]) for item in products)
        warning = ""
        if errors:
            warning = "CIRA SLIDER catalogue refresh is incomplete. " + " | ".join(errors)
        with self._lock:
            self.catalogue_warning = warning
        return {"providers": 1, "areas": len(areas), "products": product_count,
                "resolution_options": resolution_count, "errors": errors,
                "warning": warning, "complete": not errors}

    @staticmethod
    def _tile_url(area, product_id, timestamp_code, level, x, y):
        date_path = f"{timestamp_code[:4]}/{timestamp_code[4:6]}/{timestamp_code[6:8]}"
        path = (f"imagery/{date_path}/{area['satellite']}---{area['sector']}/"
                f"{product_id}/{timestamp_code}/{level:02d}/{y:03d}_{x:03d}.png")
        return urllib.parse.urljoin(DATA_BASE, path)

    def latest(self, provider, area_id, product_id, resolution):
        self._validate_provider(provider)
        document, area = self._area(area_id)
        products = _products(document, area)
        product = next((item for item in products if item["id"] == product_id), None)
        if product is None:
            raise SelectionLostError("The selected CIRA SLIDER product is not available: " + str(product_id))
        if resolution == "largest":
            resolution = max(product["resolutions"], key=_resolution_key)
        if resolution not in product["resolutions"]:
            raise SelectionLostError("The selected CIRA SLIDER resolution is not available: " + str(resolution))
        timestamp_code = self._latest_timestamp_code(area, product_id)
        timestamp = dt.datetime.strptime(timestamp_code, "%Y%m%d%H%M%S").replace(
            tzinfo=dt.timezone.utc
        )
        width = int(resolution.split("x", 1)[0])
        count = width // area["tile_size"]
        if width % area["tile_size"] or count <= 0 or count & (count - 1) or count * count > MAX_TILES:
            raise SliderError("CIRA SLIDER source resolution does not match a supported tile grid.")
        level = int(math.log2(count))
        first_url = self._tile_url(area, product_id, timestamp_code, level, 0, 0)
        try:
            box = self.content_box(area_id, product_id, timestamp_code)
        except SliderError:
            box = FULL_CONTENT_BOX  # Render the whole grid rather than fail the update.
        return {"content_box": list(box),
                "source": PROVIDER, "area": area_id, "area_label": area["label"],
                "satellite": area["satellite"], "sector": area["sector"],
                "product": product_id, "product_label": product["label"],
                "resolution": resolution,
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "timestamp_code": timestamp_code,
                "expected_interval_seconds": area["interval"], "count": count,
                "level": level, "tile_size": area["tile_size"], "url": first_url}

    def latest_identity(self, area, frame):
        timestamp = str(frame.get("timestamp_code", ""))
        product = str(frame.get("product", ""))
        count = frame.get("count")
        level = frame.get("level")
        resolution = str(frame.get("resolution", ""))
        valid = (re.fullmatch(r"\d{14}", timestamp) and _valid_identifier(product)
                 and isinstance(count, int) and count > 0 and not count & (count - 1)
                 and count * count <= MAX_TILES and isinstance(level, int)
                 and level == int(math.log2(count))
                 and resolution == f"{area['tile_size'] * count}x{area['tile_size'] * count}")
        timestamp_iso = ""
        if valid:
            try:
                timestamp_iso = dt.datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(
                    tzinfo=dt.timezone.utc
                ).isoformat().replace("+00:00", "Z")
            except ValueError:
                valid = False
        return {"url": self._tile_url(area, product, timestamp, level, 0, 0) if valid else "",
                "satellite": area["satellite"], "sector": area["sector"],
                "tile_size": area["tile_size"], "resolution": resolution if valid else "",
                "timestamp": timestamp_iso if valid else ""}

    def _load_tile(self, area, frame, x, y, track=True):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        url = self._tile_url(area, frame["product"], frame["timestamp_code"],
                             frame["level"], x, y)
        body, _ = self._request(url, MAX_TILE_BYTES, track=track)
        try:
            with Image.open(io.BytesIO(body), formats=("PNG",)) as image:
                if (getattr(image, "is_animated", False)
                        or getattr(image, "n_frames", 1) != 1
                        or image.size != (area["tile_size"], area["tile_size"])):
                    raise SliderError("CIRA SLIDER returned an invalid PNG tile.")
                image.load()
                return x, y, image.convert("RGBA")
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            if isinstance(exc, SliderError):
                raise
            raise SliderError("CIRA SLIDER returned a damaged or unsupported PNG tile: %s" % exc) from exc

    def fetch_image(self, frame, output_size, fit_mode="fit", zoom=1.0, background="#000000"):
        output_size, zoom, color = _validate_output(output_size, fit_mode, zoom, background)
        if frame.get("source") != PROVIDER:
            raise SliderError("The CIRA SLIDER frame identity is invalid.")
        document, area = self._area(frame.get("area"))
        if frame.get("product") not in {item["id"] for item in _products(document, area)}:
            raise SliderError("The CIRA SLIDER frame product is not available for its sector.")
        expected = self.latest_identity(area, frame)
        for key, value in expected.items():
            if frame.get(key) != value:
                raise SliderError("CIRA SLIDER frame identity does not match its image URL.")
        count = frame["count"]
        tile_size = area["tile_size"]
        source_size = tile_size * count
        out_width, out_height = output_size
        # Fit and crop frame the visible sector, not SLIDER's black square padding.
        box = _valid_content_box(frame.get("content_box", FULL_CONTENT_BOX)) or FULL_CONTENT_BOX
        content_left, content_top = box[0] * source_size, box[1] * source_size
        content_width = (box[2] - box[0]) * source_size
        content_height = (box[3] - box[1]) * source_size
        scale = (min if fit_mode == "fit" else max)(
            out_width / content_width, out_height / content_height
        ) * zoom
        offset_x = (out_width - content_width * scale) / 2 - content_left * scale
        offset_y = (out_height - content_height * scale) / 2 - content_top * scale
        clip = (max(0, round(offset_x + content_left * scale)),
                max(0, round(offset_y + content_top * scale)),
                min(out_width, round(offset_x + (content_left + content_width) * scale)),
                min(out_height, round(offset_y + (content_top + content_height) * scale)))
        visible = []
        for y in range(count):
            for x in range(count):
                left = round(offset_x + x * tile_size * scale)
                top = round(offset_y + y * tile_size * scale)
                right = round(offset_x + (x + 1) * tile_size * scale)
                bottom = round(offset_y + (y + 1) * tile_size * scale)
                if right > clip[0] and bottom > clip[1] and left < clip[2] and top < clip[3]:
                    visible.append((x, y, left, top, right, bottom))
        if not visible or len(visible) > MAX_TILES:
            raise SliderError("The selected CIRA SLIDER tile grid cannot be rendered safely.")
        # One request per visible tile lets the progress show a real total.
        DOWNLOAD_PROGRESS.set_expected_requests(len(visible))
        positions = {(x, y): (left, top, right, bottom)
                     for x, y, left, top, right, bottom in visible}
        with Image.new("RGB", output_size, color) as canvas:
            workers = min(8, len(visible))
            coordinates = iter((x, y) for x, y, *_ in visible)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                tasks = {}
                for _ in range(min(len(visible), workers * 2)):
                    x, y = next(coordinates)
                    tasks[pool.submit(self._load_tile, area, frame, x, y)] = (x, y)
                try:
                    while tasks:
                        DOWNLOAD_PROGRESS.raise_if_cancelled()
                        completed, _ = wait(tasks, return_when=FIRST_COMPLETED)
                        for task in completed:
                            tasks.pop(task)
                            x, y, tile = task.result()
                            try:
                                left, top, right, bottom = positions[(x, y)]
                                width, height = right - left, bottom - top
                                if width > 0 and height > 0:
                                    with tile.resize((width, height), Image.Resampling.LANCZOS) as resized:
                                        crop = (max(0, clip[0] - left), max(0, clip[1] - top),
                                                min(width, clip[2] - left), min(height, clip[3] - top))
                                        if crop[2] > crop[0] and crop[3] > crop[1]:
                                            with resized.crop(crop) as clipped:
                                                canvas.paste(clipped, (left + crop[0], top + crop[1]), clipped)
                            finally:
                                tile.close()
                            try:
                                next_x, next_y = next(coordinates)
                            except StopIteration:
                                pass
                            else:
                                DOWNLOAD_PROGRESS.raise_if_cancelled()
                                pending = pool.submit(self._load_tile, area, frame, next_x, next_y)
                                tasks[pending] = (next_x, next_y)
                except BaseException:
                    for pending in tasks:
                        pending.cancel()
                    raise
            output = io.BytesIO()
            canvas.save(output, "PNG")
            return output.getvalue()
