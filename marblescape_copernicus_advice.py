"""Recommend Copernicus cloud limit and Gap fill before any picture is rendered.

The catalogue lists every satellite tile over the view with its footprint and
its cloud estimate; it costs no processing units. Footprints drawn on a coarse
grid of the view give the share of the view each acquisition covers. Each
variant (cloud limit x Gap fill) is then replayed the way the renderer works:
the newest acquisition at the view's centre whose tile meets the cloud limit,
the tiles of that day, and with Fill gaps the older tiles of the lookback below
it, newest first. Replaying it for each of the last 90 days shows how reliable a
variant is, because the weather changes while the orbits repeat.

No Tk here: the window is in ``marblescape_copernicus_advice_window``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from dataclasses import dataclass, replace
import datetime as dt
import math

from PIL import Image, ImageChops, ImageDraw

from marblescape_copernicus import (
    _parse_catalogue_datetime,
    _scaled_view_center,
    normalize_profile,
    rolling_month_start,
    rolling_quarter_start,
    supports_cloud_filter,
)

CLOUD_LIMITS = (10, 20, 30, 50, 100)
GAP_FILL_VARIANTS = (("single", None), ("fill_gaps", 7), ("fill_gaps", 14),
                     ("fill_gaps", 30), ("fill_gaps", 60))
FULL_COVERAGE = 98.0           # "Coverage" needs at least this share of the view.
FEWEST_CLOUDS_COVERAGE = 90.0  # "Fewest clouds" accepts at most this much missing.
CLEAR_CLOUDS = 10.0            # A replayed day counts as clear at or below this estimate.
REPLAY_DAYS = 90
HISTORY_DAYS = REPLAY_DAYS + max(days or 1 for _mode, days in GAP_FILL_VARIANTS)
GRID_WIDTH = 128
# Mosaics: how many of the newest published periods the window compares, and how far
# back the catalogue is searched for them (publication lags a few periods).
MOSAIC_PERIODS = {"quarter": 8, "month": 12, "year": 6}
MOSAIC_SEARCH_DAYS = {"quarter": 92 * 11, "month": 31 * 15, "year": 366 * 7}
PERIOD_UNITS = {"quarter": 3, "month": 1}
MASK_WORKERS = 4
# Auto recommendation measures a mosaic period once; masks are kept for the session.
MASK_CACHE_LIMIT = 512
_MASK_CACHE = {}
_LAST_CHOICES = {}
_STATE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Acquisition:
    """One satellite tile over the view: its day, cloud estimate and footprint."""
    day: dt.date
    cloud: float | None
    mask: Image.Image        # mode "1": the grid cells the footprint covers
    covers_center: bool


@dataclass(frozen=True)
class Variant:
    cloud_limit: int | None  # None: the layer has no cloud estimate (radar, DEM)
    coverage_mode: str
    lookback_days: int | None
    # Mosaics: the period instead of cloud limit and Gap fill. Apply takes the newest
    # as Latest available and an older quarter or month relative to now.
    period: dt.date | None = None
    granularity: str | None = None
    offset: int | None = None  # periods back from today (quarters and months)
    newest: bool = False

    def gap_fill_text(self):
        if self.period is not None:
            return period_label(self.period, self.granularity)
        return ("Single latest" if self.coverage_mode == "single"
                else f"Fill gaps, {self.lookback_days} days")

    def settings_text(self):
        if self.period is not None:
            label = period_label(self.period, self.granularity)
            if self.newest:
                return f"Latest available ({label})"
            if self.offset is not None:
                unit = self.granularity
                back = f"1 {unit} ago" if self.offset == 1 else f"{self.offset} {unit}s ago"
                return f"Relative to now, {back} ({label})"
            return f"Specific {self.granularity} {label}"
        gap = ("Single latest acquisition" if self.coverage_mode == "single"
               else f"Fill gaps, {self.lookback_days} days")
        if self.cloud_limit is None:
            return gap
        return f"Max. cloud cover {self.cloud_limit}% · {gap}"


@dataclass(frozen=True)
class Outcome:
    """What a variant gives for one day: its newest date, coverage and clouds."""
    newest: dt.date
    oldest: dt.date
    coverage: float | None   # percent of the view; None: a mosaic before Precise check
    clouds: float | None     # area-weighted tile estimate, percent; None without estimates
    dates_used: int
    measured: bool = False         # Precise check: coverage from the view mask
    measured_clouds: bool = False  # Precise check: clouds in percent of the view


@dataclass
class Row:
    variant: Variant
    outcome: Outcome | None
    complete_share: float     # percent of the replayed days with full coverage
    clear_share: float        # percent of the replayed days at least 90% covered and clear
    covered_share: float = 0.0  # percent of the replayed days at least 90% covered
    current: bool = False


@dataclass
class Advice:
    rows: list
    fewest_clouds: Row | None
    full_coverage: Row | None
    full_coverage_reached: bool
    cloud_estimates: bool
    acquisitions: int
    today: dt.date
    masks: int = 0  # Precise check: view masks read
    mosaic: bool = False  # rows are periods; the first box is Newest


def period_start(day, granularity):
    if granularity == "year":
        return dt.date(day.year, 1, 1)
    if granularity == "quarter":
        return dt.date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
    return dt.date(day.year, day.month, 1)


def period_label(start, granularity):
    if granularity == "year":
        return str(start.year)
    if granularity == "quarter":
        return f"{start.year} Q{(start.month - 1) // 3 + 1}"
    return f"{start:%Y-%m}"


def period_offset(start, today, granularity):
    """Periods from ``start`` back to today's, as Relative to now counts them; None for years."""
    step = PERIOD_UNITS.get(granularity)
    if step is None:
        return None
    def index(day):
        return (day.year * 12 + day.month - 1) // step
    return max(0, index(today) - index(start))


def grid_size(width, height):
    return GRID_WIDTH, max(1, round(GRID_WIDTH * height / width))


def view_transform(profile, width, height):
    """Map longitude/latitude to grid cells of the view (Web Mercator, as rendered)."""
    cx, cy, world_size = _scaled_view_center(profile, width, height)
    left, top = cx - width / 2, cy - height / 2
    grid_width, grid_height = grid_size(width, height)
    scale_x, scale_y = grid_width / width, grid_height / height

    def to_grid(longitude, latitude):
        x = (longitude + 180.0) / 360.0 * world_size
        # A footprint across the date line: take the copy nearest the view.
        x += round((cx - x) / world_size) * world_size
        latitude = max(-85.05112878, min(85.05112878, latitude))
        sine = math.sin(math.radians(latitude))
        y = (0.5 - math.log((1 + sine) / (1 - sine)) / (4 * math.pi)) * world_size
        return (x - left) * scale_x, (y - top) * scale_y

    return to_grid, (grid_width, grid_height)


def footprint_mask(geometry, to_grid, size):
    """The grid cells a GeoJSON Polygon or MultiPolygon covers (holes excluded)."""
    mask = Image.new("1", size, 0)
    draw = ImageDraw.Draw(mask)
    if not isinstance(geometry, dict):
        return mask
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    polygons = ([coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else [])
    for polygon in polygons or ():
        for index, ring in enumerate(polygon or ()):
            try:
                points = [to_grid(float(point[0]), float(point[1])) for point in ring]
            except (TypeError, ValueError, IndexError):
                continue
            if len(points) >= 3:
                draw.polygon(points, fill=0 if index else 255)
    return mask


def acquisitions_from_features(features, profile, output_size):
    """Acquisitions from catalogue features; items without a usable time are skipped."""
    profile = normalize_profile(profile)
    width, height = map(int, output_size)
    to_grid, size = view_transform(profile, width, height)
    center = (size[0] // 2, size[1] // 2)
    result = []
    for feature in features:
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        moment = _parse_catalogue_datetime(properties.get("datetime"))
        if moment is None:
            continue
        cloud = properties.get("eo:cloud_cover")
        cloud = float(cloud) if isinstance(cloud, (int, float)) and not isinstance(cloud, bool) else None
        if cloud is not None and cloud < 0:
            continue  # Night passes without data.
        mask = footprint_mask(feature.get("geometry"), to_grid, size)
        if not mask.getbbox():
            continue
        result.append(Acquisition(moment.date(), cloud, mask, bool(mask.getpixel(center))))
    return result


def covered_cells(mask):
    """Cells set in a mode "1" mask (drawn and combined cells use different values)."""
    return mask.width * mask.height - mask.histogram()[0]


class Replay:
    """Variants evaluated over the acquisitions, with results cached per newest day."""

    def __init__(self, acquisitions, size):
        self._acquisitions = sorted(acquisitions, key=lambda item: item.day, reverse=True)
        self._size = size
        self._cells = size[0] * size[1]
        self._cache = {}

    def evaluate(self, variant, reference_day):
        eligible = [item for item in self._acquisitions if item.day <= reference_day
                    and (variant.cloud_limit is None
                         or (item.cloud is not None and item.cloud <= variant.cloud_limit))]
        newest = next((item.day for item in eligible if item.covers_center), None)
        if newest is None:
            return None
        key = (variant, newest)
        if key not in self._cache:
            start = newest if variant.coverage_mode == "single" else (
                newest - dt.timedelta(days=variant.lookback_days - 1))
            covered = Image.new("1", self._size, 0)
            weighted, days = 0.0, set()
            # Newest first, like the renderer's mostRecent mosaicking.
            for item in eligible:
                if not start <= item.day <= newest:
                    continue
                new = ImageChops.logical_and(item.mask, ImageChops.invert(covered))
                count = covered_cells(new)
                if count:
                    covered = ImageChops.logical_or(covered, item.mask)
                    days.add(item.day)
                    weighted += (item.cloud or 0.0) * count
            area = covered_cells(covered)
            clouds = (weighted / area if area and variant.cloud_limit is not None else None)
            self._cache[key] = Outcome(newest, min(days) if days else newest,
                                       100.0 * area / self._cells, clouds, len(days))
        return self._cache[key]

    def tile_key(self, variant, newest):
        """The tiles a variant mosaics for ``newest``: equal keys render the same picture."""
        start = newest if variant.coverage_mode == "single" else (
            newest - dt.timedelta(days=variant.lookback_days - 1))
        return frozenset(index for index, item in enumerate(self._acquisitions)
                         if start <= item.day <= newest
                         and (variant.cloud_limit is None
                              or (item.cloud is not None and item.cloud <= variant.cloud_limit)))


def measure_rows(rows, measure, key, progress=None):
    """Precise check: coverage and clouds of today's outcomes from view masks, one
    ``measure(variant, newest)`` per distinct ``key(row)``. Returns the masks read."""
    groups = {}
    for row in rows:
        if row.outcome is not None:
            groups.setdefault(key(row), []).append(row)
    # Four at a time, like the renderer's map tiles.
    with ThreadPoolExecutor(max_workers=MASK_WORKERS) as pool:
        futures = {pool.submit(measure, members[0].variant, members[0].outcome.newest): members
                   for members in groups.values()}
        for index, future in enumerate(as_completed(futures), 1):
            coverage, clouds = future.result()
            for row in futures[future]:
                row.outcome = replace(
                    row.outcome, coverage=coverage, measured=True,
                    clouds=clouds if clouds is not None else row.outcome.clouds,
                    measured_clouds=clouds is not None)
            if progress is not None:
                progress(index, len(groups))
    return len(groups)


def variants_for(layer):
    limits = CLOUD_LIMITS if supports_cloud_filter(layer) else (None,)
    return [Variant(limit, mode, days) for limit in limits for mode, days in GAP_FILL_VARIANTS]


def current_variant(profile, layer):
    profile = normalize_profile(profile)
    limit = int(profile["max_cloud_cover"]) if supports_cloud_filter(layer) else None
    if profile["coverage_mode"] == "fill_gaps" and "date_granularity" not in layer:
        return Variant(limit, "fill_gaps", int(profile["lookback_days"]))
    return Variant(limit, "single", None)


def variant_profile(profile, variant):
    """``profile`` with the cloud limit and Gap fill of ``variant`` (a mosaic's period
    comes with the date of the request)."""
    if variant.period is not None:
        return dict(profile)
    changed = dict(profile, coverage_mode=variant.coverage_mode)
    if variant.cloud_limit is not None:
        changed["max_cloud_cover"] = variant.cloud_limit
    if variant.lookback_days:
        changed["lookback_days"] = variant.lookback_days
    return changed


def advise(acquisitions, size, layer, profile, today, measure=None, progress=None):
    """Rows for every variant and the current settings, plus the two recommendations.

    With ``measure`` (Precise check) today's coverage and clouds come from view masks
    before the recommendations are picked; the last 90 days stay estimates.
    """
    replay = Replay(acquisitions, size)
    days = [today - dt.timedelta(days=offset) for offset in range(REPLAY_DAYS)]

    def row_for(variant, current=False):
        outcome = replay.evaluate(variant, today)
        complete = clear = covered = 0
        for day in days:
            result = replay.evaluate(variant, day)
            if result is None:
                continue
            complete += result.coverage >= FULL_COVERAGE
            covered += result.coverage >= FEWEST_CLOUDS_COVERAGE
            clear += (result.coverage >= FEWEST_CLOUDS_COVERAGE
                      and (result.clouds is None or result.clouds <= CLEAR_CLOUDS))
        share = 100.0 / len(days)
        return Row(variant, outcome, complete * share, clear * share,
                   covered_share=covered * share, current=current)

    rows = [row_for(variant) for variant in variants_for(layer)]
    current = row_for(current_variant(profile, layer), current=True)
    masks = (measure_rows(rows + [current], measure,
                          lambda row: replay.tile_key(row.variant, row.outcome.newest), progress)
             if measure else 0)

    def age(row):
        return (today - row.outcome.newest).days

    usable = [row for row in rows if row.outcome is not None]
    complete = [row for row in usable if row.outcome.coverage >= FULL_COVERAGE]
    full_reached = bool(complete)
    full = min(complete or usable, key=lambda row: (
        0 if full_reached else -row.outcome.coverage, row.outcome.dates_used, age(row),
        row.outcome.clouds or 0.0, -row.complete_share), default=None)
    cloud_estimates = supports_cloud_filter(layer)
    fewest = pick_fewest_clouds(usable, today) if cloud_estimates else None
    # Both priorities can pick the same row: list it once, by identity.
    recommended = []
    for row in (fewest, full):
        if row is not None and not any(row is other for other in recommended):
            recommended.append(row)
    rest = sorted((row for row in rows if not any(row is other for other in recommended)),
                  key=lambda row: (
                      row.outcome is None, -(row.outcome.coverage if row.outcome else 0),
                      row.outcome.clouds if row.outcome and row.outcome.clouds is not None else 0.0))
    ordered = [current] + recommended + rest
    return Advice(ordered, fewest, full, full_reached, cloud_estimates, len(acquisitions), today,
                  masks=masks)


def mosaic_current(profile, layer, rows, today):
    """The row of the period the settings render now, marked current."""
    granularity = layer["date_granularity"]
    if profile["date_mode"] == "relative_quarter":
        start = rolling_quarter_start(today, profile["quarter_offset"])
    elif profile["date_mode"] == "relative_month":
        start = rolling_month_start(today, profile["month_offset"])
    elif profile["date"] == "latest":
        start = rows[0].variant.period if rows else None
    else:
        start = period_start(dt.date.fromisoformat(profile["date"]), granularity)
    match = next((row for row in rows if row.variant.period == start), None)
    if match is not None:
        return Row(match.variant, match.outcome, 0.0, 0.0, current=True)
    variant = Variant(None, "single", None, period=start, granularity=granularity,
                      offset=period_offset(start, today, granularity) if start else None)
    return Row(variant, None, 0.0, 0.0, current=True)


def advise_mosaic(features, layer, profile, today, measure=None, progress=None):
    """Periods of a mosaic: the newest published ones, Newest and Data coverage.

    The catalogue lists each period's tiles but not where they hold data, so the
    coverage needs Precise check (one mask per period); without it Data coverage
    stays open.
    """
    granularity = layer["date_granularity"]
    profile = normalize_profile(profile)
    dates = {}
    for feature in features:
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        moment = _parse_catalogue_datetime(properties.get("datetime"))
        if moment is not None:
            start = period_start(moment.date(), granularity)
            dates[start] = max(dates.get(start, moment.date()), moment.date())
    periods = sorted(dates, reverse=True)[:MOSAIC_PERIODS[granularity]]
    rows = []
    for index, start in enumerate(periods):
        variant = Variant(None, "single", None, period=start, granularity=granularity,
                          offset=period_offset(start, today, granularity), newest=index == 0)
        day = dates[start]
        rows.append(Row(variant, Outcome(day, day, None, None, 1), 0.0, 0.0))
    current = mosaic_current(profile, layer, rows, today)
    masks = (measure_rows(rows + [current], measure, lambda row: row.variant.period, progress)
             if measure else 0)
    newest = rows[0] if rows else None
    measured = [row for row in rows if row.outcome.coverage is not None]
    complete = [row for row in measured if row.outcome.coverage >= FULL_COVERAGE]
    full = (complete[0] if complete else max(measured, key=lambda row: row.outcome.coverage)
            if measured else None)
    return Advice([current] + rows, newest, full, bool(complete), False, len(features), today,
                  masks=masks, mosaic=True)


def pick_fewest_clouds(rows, today):
    """The Fewest clouds recommendation among rows with an outcome.

    Rows covering at least 90% of the view qualify. Among those that are clear
    today (at most 10% clouds) the most reliable one over the last 90 days wins:
    first the most days that were clear and at least 90% covered, then the most
    days at least 90% covered (in a cloudy region few days are clear), then the
    most days fully covered, then fewer clouds today, a newer picture and fewer
    acquisitions. Without a clear row the lowest cloud estimate wins.
    """
    covered = [row for row in rows if row.outcome.coverage >= FEWEST_CLOUDS_COVERAGE
               and row.outcome.clouds is not None]
    clear = [row for row in covered if row.outcome.clouds <= CLEAR_CLOUDS]

    def age(row):
        return (today - row.outcome.newest).days

    if clear:
        return min(clear, key=lambda row: (
            -round(row.clear_share), -round(row.covered_share), -round(row.complete_share),
            round(row.outcome.clouds, 1), age(row), row.outcome.dates_used))
    return min(covered, key=lambda row: (
        round(row.outcome.clouds, 1), age(row), row.outcome.dates_used, -row.clear_share), default=None)


def fetch_advice(client, profile, output_size, today=None, progress=None, precise=False,
                 mask_progress=None, reuse_masks=False):
    """Search the catalogue for the view and advise; the catalogue uses no processing
    units, a Precise check (``precise``) one small mask per distinct set of tiles."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    end = dt.datetime.combine(today, dt.time.max, dt.timezone.utc)
    granularity = (client._selection(profile)[2] or {}).get("date_granularity")
    days = MOSAIC_SEARCH_DAYS[granularity] if granularity else HISTORY_DAYS
    start = dt.datetime.combine(today - dt.timedelta(days=days), dt.time.min, dt.timezone.utc)
    features, layer = client.search_view(profile, output_size, start, end, progress=progress)
    acquisitions = acquisitions_from_features(features, profile, output_size)
    width, height = map(int, output_size)
    measure = None
    if precise:
        def measure(variant, newest):
            key = (*view_key(profile), tuple(map(int, output_size)), variant, newest)
            with _STATE_LOCK:
                if reuse_masks and key in _MASK_CACHE:
                    return _MASK_CACHE[key]
            value = client.view_mask(variant_profile(profile, variant), output_size, newest.isoformat())
            if reuse_masks:
                with _STATE_LOCK:
                    if len(_MASK_CACHE) >= MASK_CACHE_LIMIT:
                        _MASK_CACHE.clear()
                    _MASK_CACHE[key] = value
            return value
    if is_mosaic(layer):
        return advise_mosaic(features, layer, profile, today, measure=measure, progress=mask_progress)
    return advise(acquisitions, grid_size(width, height), layer, profile, today,
                  measure=measure, progress=mask_progress)


def is_mosaic(layer):
    return "date_granularity" in (layer or {})


def view_key(profile):
    """The selection and view a recommendation belongs to."""
    profile = normalize_profile(profile)
    return (profile["configuration"], profile["product"], profile["layer"],
            profile["latitude"], profile["longitude"], profile["map_zoom"])


def newest_row(advice):
    """Newest for regular layers: the newest acquisition on top, whatever its clouds.

    Among the variants that take every cloud estimate (100%, or none for radar),
    which all share the newest acquisition, the shortest Gap fill reaching
    FEWEST_CLOUDS_COVERAGE wins (Single latest first); without one the most covered.
    """
    open_limits = [row for row in advice.rows if not row.current and row.outcome is not None
                   and row.outcome.coverage is not None
                   and row.variant.cloud_limit in (None, max(CLOUD_LIMITS))]

    def lookback(row):
        return 0 if row.variant.coverage_mode == "single" else row.variant.lookback_days

    open_limits.sort(key=lookback)
    enough = [row for row in open_limits if row.outcome.coverage >= FEWEST_CLOUDS_COVERAGE]
    if enough:
        return enough[0]
    return max(open_limits, key=lambda row: (row.outcome.coverage, -lookback(row)), default=None)


def auto_row(advice, priority):
    """The row the rule takes: its priority, else the others; None keeps the saved settings.

    For mosaics Newest is the first box (the newest period); for regular layers it is
    the third priority (newest_row), then Data coverage, then Fewest clouds.
    """
    first, full = advice.fewest_clouds, advice.full_coverage
    if priority == "full_coverage":
        order = (full, first)
    elif priority == "newest" and not advice.mosaic:
        order = (newest_row(advice), full, first)
    else:
        order = (first, full)
    return next((row for row in order if row is not None and row.outcome is not None), None)


def applied_profile(profile, row):
    """``profile`` with a row taken in, as Apply does: Latest available for scenes and
    the newest period, relative to now for an older quarter or month, else the year."""
    variant = row.variant
    base = dict(profile, date="latest", date_mode="catalogue", quarter_offset=0, month_offset=0)
    if variant.period is None:
        return variant_profile(base, variant)
    if variant.newest:
        return base
    if variant.offset is not None:
        offset_key = "quarter_offset" if variant.granularity == "quarter" else "month_offset"
        return dict(base, date_mode=f"relative_{variant.granularity}", **{offset_key: variant.offset})
    return dict(base, date=row.outcome.newest.isoformat())


def recommended_profile(client, profile, output_size, today=None):
    """(profile to render, chosen settings text or None) for Use auto recommendation.

    The catalogue decides for scenes (no processing units) unless Always use
    precise check measures them at every check; a mosaic's Data coverage measures each
    period once per session. Without a choice the saved settings render.
    """
    profile = normalize_profile(profile)
    layer = client._selection(profile)[2] or {}
    if not profile["auto_recommendation"] or layer.get("data_type") == "dem":
        return profile, None
    mosaic = is_mosaic(layer)
    precise = profile["auto_priority"] == "full_coverage" if mosaic else profile["auto_precise"]
    # A mosaic period never changes; a scene's day can gain tiles, so it is measured anew.
    advice = fetch_advice(client, profile, output_size, today=today, precise=precise, reuse_masks=mosaic)
    row = auto_row(advice, profile["auto_priority"])
    if row is None:
        return profile, None
    text = row.variant.settings_text()
    with _STATE_LOCK:
        _LAST_CHOICES[view_key(profile)] = (text, dt.datetime.now().astimezone())
    return applied_profile(profile, row), text


def last_choice(profile):
    """(settings text, local time) the rule last took for this view in this session, or None."""
    try:
        key = view_key(profile)
    except ValueError:
        return None
    with _STATE_LOCK:
        return _LAST_CHOICES.get(key)


def priority_label(priority, mosaic):
    """The Priority entry of a saved value; fewest_clouds is the first box, Newest for mosaics."""
    if priority == "full_coverage":
        return "Data coverage"
    if priority == "newest" or mosaic:
        return "Newest"
    return "Fewest clouds"


def current_text(profile, layer):
    """'Current: ...' above the boxes: the date choice and, for scenes, the settings."""
    profile = normalize_profile(profile)
    if is_mosaic(layer):
        unit = layer["date_granularity"]
        if profile["date_mode"].startswith("relative_"):
            offset = profile["quarter_offset" if unit == "quarter" else "month_offset"]
            back = f"1 {unit} ago" if offset == 1 else f"{offset} {unit}s ago"
            return f"Current: Relative to now, {back}"
        if profile["date"] == "latest":
            return "Current: Latest available"
        return f"Current: Specific {unit} {period_label(dt.date.fromisoformat(profile['date']), unit)}"
    date = "Latest available" if profile["date"] == "latest" else f"Date {profile['date']}"
    return "Current: " + date + " · " + current_variant(profile, layer).settings_text()


def percent(value):
    return "-" if value is None else f"{value:.0f}%"


def age_text(newest, today):
    days = (today - newest).days
    return "today" if days <= 0 else "1 day" if days == 1 else f"{days} days"
