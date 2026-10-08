"""Recommend: footprints, the renderer's Gap fill replay and the two priorities, offline."""

import datetime as dt
import unittest
import unittest.mock

import marblescape_copernicus as copernicus
import marblescape_copernicus_advice as advice

SIZE = (1280, 720)
TODAY = dt.date(2026, 10, 6)
CLOUD_LAYER = {"data_type": "sentinel-2-l2a", "data_filter": {"maxCloudCoverage": 30}}
RADAR_LAYER = {"data_type": "sentinel-1-grd", "data_filter": {}}


def profile(**changes):
    values = {"latitude": 0.0, "longitude": 0.0, "map_zoom": 10, "max_cloud_cover": 100,
              "coverage_mode": "single", "lookback_days": 7}
    values.update(changes)
    return copernicus.normalize_profile(values)


def view():
    west, south, east, north = copernicus.geographic_bbox(profile(), *SIZE)
    return west, south, east, north


def box(west, south, east, north):
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north],
                                                 [west, north], [west, south]]]}


def halves():
    """Footprints: the left 55% and the right 55% of the view, both over the centre."""
    west, south, east, north = view()
    width = east - west
    left = box(west - 1, south - 1, west + 0.55 * width, north + 1)
    right = box(east - 0.55 * width, south - 1, east + 1, north + 1)
    whole = box(west - 1, south - 1, east + 1, north + 1)
    return left, right, whole


def feature(day, cloud, geometry):
    return {"properties": {"datetime": f"{day.isoformat()}T21:00:06Z", "eo:cloud_cover": cloud},
            "geometry": geometry}


class FootprintTests(unittest.TestCase):
    def acquisitions(self, *features):
        return advice.acquisitions_from_features(list(features), profile(), SIZE)

    def test_footprints_cover_their_share_of_the_view(self):
        left, _right, whole = halves()
        full, half = self.acquisitions(feature(TODAY, 5, whole), feature(TODAY, 5, left))
        cells = advice.grid_size(*SIZE)
        self.assertEqual(cells, (128, 72))
        self.assertEqual(advice.covered_cells(full.mask), 128 * 72)
        self.assertAlmostEqual(advice.covered_cells(half.mask) / (128 * 72), 0.55, delta=0.02)
        self.assertTrue(full.covers_center and half.covers_center)

    def test_night_passes_unusable_times_and_outside_footprints_are_skipped(self):
        west, south, east, north = view()
        far = box(east + 5, south, east + 6, north)
        items = self.acquisitions(
            feature(TODAY, -1, halves()[2]),
            {"properties": {"datetime": "never"}, "geometry": halves()[2]},
            feature(TODAY, 10, far),
            feature(TODAY, None, halves()[2]),
        )
        self.assertEqual([(item.day, item.cloud) for item in items], [(TODAY, None)])

    def test_holes_are_left_out(self):
        west, south, east, north = view()
        width, height = east - west, north - south
        outer = [[west - 1, south - 1], [east + 1, south - 1], [east + 1, north + 1],
                 [west - 1, north + 1], [west - 1, south - 1]]
        hole = [[west + 0.25 * width, south + 0.25 * height], [west + 0.75 * width, south + 0.25 * height],
                [west + 0.75 * width, south + 0.75 * height], [west + 0.25 * width, south + 0.75 * height],
                [west + 0.25 * width, south + 0.25 * height]]
        (item,) = self.acquisitions(feature(TODAY, 0, {"type": "MultiPolygon", "coordinates": [[outer, hole]]}))
        self.assertAlmostEqual(advice.covered_cells(item.mask) / (128 * 72), 0.75, delta=0.03)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        left, right, whole = halves()
        self.items = advice.acquisitions_from_features([
            feature(TODAY, 20, left),
            feature(TODAY - dt.timedelta(days=3), 5, right),
            feature(TODAY - dt.timedelta(days=10), 60, whole),
        ], profile(), SIZE)
        self.replay = advice.Replay(self.items, advice.grid_size(*SIZE))

    def outcome(self, limit, mode="single", days=None, day=TODAY):
        return self.replay.evaluate(advice.Variant(limit, mode, days), day)

    def test_single_latest_takes_the_newest_tiles_within_the_cloud_limit(self):
        newest = self.outcome(20)
        self.assertEqual((newest.newest, newest.dates_used), (TODAY, 1))
        self.assertAlmostEqual(newest.coverage, 55, delta=2)
        self.assertAlmostEqual(newest.clouds, 20)
        clearer = self.outcome(10)
        self.assertEqual(clearer.newest, TODAY - dt.timedelta(days=3))
        self.assertAlmostEqual(clearer.clouds, 5)

    def test_fill_gaps_adds_older_tiles_newest_first_within_the_lookback(self):
        week = self.outcome(20, "fill_gaps", 7)
        self.assertEqual((week.newest, week.oldest, week.dates_used),
                         (TODAY, TODAY - dt.timedelta(days=3), 2))
        self.assertAlmostEqual(week.coverage, 100, delta=0.1)
        # Today's tile fills its 55% first; the older one only the rest (45%).
        self.assertAlmostEqual(week.clouds, 0.55 * 20 + 0.45 * 5, delta=0.6)
        # The 60% tile is outside the 7-day window and above the 20% limit.
        fortnight = self.outcome(100, "fill_gaps", 14)
        self.assertEqual(fortnight.dates_used, 2)
        self.assertAlmostEqual(fortnight.coverage, 100, delta=0.1)

    def test_a_day_before_every_acquisition_has_no_result(self):
        self.assertIsNone(self.outcome(100, day=TODAY - dt.timedelta(days=11)))

    def test_layers_without_cloud_estimates_compare_only_gap_fill(self):
        variants = advice.variants_for(RADAR_LAYER)
        self.assertEqual({variant.cloud_limit for variant in variants}, {None})
        self.assertEqual(len(variants), len(advice.GAP_FILL_VARIANTS))
        self.assertEqual(len(advice.variants_for(CLOUD_LAYER)), 25)


class AdviceTests(unittest.TestCase):
    def test_the_two_priorities_and_the_current_row(self):
        left, right, whole = halves()
        items = advice.acquisitions_from_features([
            feature(TODAY - dt.timedelta(days=1), 70, whole),
            feature(TODAY - dt.timedelta(days=2), 25, whole),
            feature(TODAY - dt.timedelta(days=4), 3, left),
            feature(TODAY - dt.timedelta(days=9), 2, right),
        ], profile(), SIZE)
        result = advice.advise(items, advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY)
        current = result.rows[0]
        self.assertTrue(current.current)
        self.assertEqual(current.variant, advice.Variant(100, "single", None))
        self.assertEqual(current.outcome.newest, TODAY - dt.timedelta(days=1))
        # Data coverage: one acquisition, as new as possible.
        self.assertTrue(result.full_coverage_reached)
        self.assertEqual(result.full_coverage.outcome.dates_used, 1)
        self.assertEqual(result.full_coverage.outcome.newest, TODAY - dt.timedelta(days=1))
        # Fewest clouds: at least 90% covered, the lowest cloud estimate.
        fewest = result.fewest_clouds
        self.assertGreaterEqual(fewest.outcome.coverage, advice.FEWEST_CLOUDS_COVERAGE)
        self.assertLess(fewest.outcome.clouds, 5)
        self.assertEqual(fewest.variant.coverage_mode, "fill_gaps")
        self.assertEqual(result.rows[1:3], [fewest, result.full_coverage])
        self.assertEqual(len(result.rows), 1 + 25)

    def test_fewest_clouds_prefers_the_most_reliable_clear_setting(self):
        def row(limit, mode, days, clouds, clear_share, age=2, dates=2, coverage=100.0, covered=50.0):
            newest = TODAY - dt.timedelta(days=age)
            return advice.Row(advice.Variant(limit, mode, days),
                              advice.Outcome(newest, newest, coverage, clouds, dates), 0.0, clear_share,
                              covered_share=covered)

        clearest_today = row(10, "fill_gaps", 14, 4.0, 10.0)
        reliable = row(20, "fill_gaps", 60, 8.0, 70.0)
        cloudy = row(100, "single", None, 30.0, 95.0, dates=1)
        thin = row(10, "single", None, 1.0, 99.0, coverage=80.0)
        rows = [clearest_today, reliable, cloudy, thin]
        # At most 10% clouds today and at least 90% covered: the most reliable wins.
        self.assertIs(advice.pick_fewest_clouds(rows, TODAY), reliable)
        # Equally often clear (a cloudy region): the most days at least 90% covered.
        steady = row(30, "fill_gaps", 60, 9.0, 70.0, covered=80.0)
        self.assertIs(advice.pick_fewest_clouds(rows + [steady], TODAY), steady)
        # Equally often fully covered too: fewer clouds today, then a newer picture.
        tied = row(30, "fill_gaps", 30, 6.0, 70.0)
        self.assertIs(advice.pick_fewest_clouds(rows + [tied], TODAY), tied)
        # Equally often covered: the most days fully covered beats a point fewer clouds.
        complete = advice.Row(steady.variant, steady.outcome, 77.0, 70.0, covered_share=80.0)
        less_cloudy = advice.Row(advice.Variant(20, "fill_gaps", 30), advice.Outcome(
            steady.outcome.newest, steady.outcome.newest, 97.0, 8.0, 3), 0.0, 70.0, covered_share=80.0)
        self.assertIs(advice.pick_fewest_clouds([less_cloudy, complete], TODAY), complete)
        # Nothing clear today: the lowest cloud estimate.
        self.assertEqual(advice.pick_fewest_clouds([cloudy, row(50, "single", None, 20.0, 5.0)], TODAY).variant,
                         advice.Variant(50, "single", None))
        self.assertIsNone(advice.pick_fewest_clouds([thin], TODAY))

    def test_without_full_coverage_the_best_coverage_is_named(self):
        left, _right, _whole = halves()
        items = advice.acquisitions_from_features([feature(TODAY, 5, left)], profile(), SIZE)
        result = advice.advise(items, advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY)
        self.assertFalse(result.full_coverage_reached)
        self.assertAlmostEqual(result.full_coverage.outcome.coverage, 55, delta=2)
        self.assertIsNone(result.fewest_clouds)

    def test_radar_has_no_fewest_clouds(self):
        items = advice.acquisitions_from_features([feature(TODAY, None, halves()[2])], profile(), SIZE)
        result = advice.advise(items, advice.grid_size(*SIZE), RADAR_LAYER, profile(), TODAY)
        self.assertFalse(result.cloud_estimates)
        self.assertIsNone(result.fewest_clouds)
        self.assertEqual(result.full_coverage.outcome.coverage, 100)

    def test_replay_counts_days_with_full_coverage(self):
        whole = halves()[2]
        items = advice.acquisitions_from_features(
            [feature(TODAY - dt.timedelta(days=offset), 5, whole) for offset in range(0, 90, 10)],
            profile(), SIZE)
        result = advice.advise(items, advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY)
        single = next(row for row in result.rows if row.variant == advice.Variant(10, "single", None))
        # Each replayed day has a newer acquisition within ten days.
        self.assertGreater(single.complete_share, 85)


class VariantProfileTests(unittest.TestCase):
    def test_a_variant_sets_cloud_limit_and_gap_fill(self):
        base = profile(max_cloud_cover=30, coverage_mode="single", lookback_days=7)
        filled = advice.variant_profile(base, advice.Variant(50, "fill_gaps", 30))
        self.assertEqual((filled["max_cloud_cover"], filled["coverage_mode"], filled["lookback_days"]),
                         (50, "fill_gaps", 30))
        single = advice.variant_profile(base, advice.Variant(None, "single", None))
        self.assertEqual((single["max_cloud_cover"], single["coverage_mode"], single["lookback_days"]),
                         (30, "single", 7))
        self.assertEqual(base["coverage_mode"], "single")


class PreviewRenderTests(unittest.TestCase):
    def client(self, layer):
        client = copernicus.CopernicusClient("id", "secret")
        client._selection = lambda value: (copernicus.normalize_profile(value), {"id": "p"}, layer)
        frames = []

        def fetch_image(frame):
            frames.append(frame)
            import io
            from PIL import Image
            output = io.BytesIO()
            Image.new("RGB", (frame["width"], frame["height"]), "red").save(output, format="PNG")
            return output.getvalue(), 0

        client.fetch_image = fetch_image
        return client, frames

    def test_a_preview_shows_the_same_view_smaller(self):
        client, frames = self.client(CLOUD_LAYER)
        picture = client.render_preview(profile(map_zoom=10), (1920, 1080), "2026-10-03")
        frame = frames[0]
        self.assertEqual((frame["width"], frame["height"], frame["date"]), (384, 216, "2026-10-03"))
        self.assertAlmostEqual(frame["view_scale"], 0.2)
        self.assertEqual((picture.mode, picture.size), ("RGB", (384, 216)))
        full = {"profile": frame["profile"], "width": 1920, "height": 1080}
        left, top, world = client._view_pixels(full)
        small = client._view_pixels(frame)
        for value, expected in zip(small, (left * 0.2, top * 0.2, world * 0.2)):
            self.assertAlmostEqual(value, expected, places=6)
        # The same Web Mercator box: the preview's corners match the full view's.
        self.assertEqual([round(value) for value in client._mercator_bbox(*small, 0, 0, 384, 216)],
                         [round(value) for value in client._mercator_bbox(left, top, world, 0, 0, 1920, 1080)])

    def test_a_wide_view_renders_finer_than_the_pixel_limit_and_shrinks(self):
        client, frames = self.client(CLOUD_LAYER)
        picture = client.render_preview(profile(map_zoom=7), (1920, 1080), "2026-10-03")
        frame = frames[0]
        world = 256 * 2 ** 7
        meters = 2 * copernicus.WEB_MERCATOR_HALF_WORLD / (world * frame["view_scale"])
        self.assertLessEqual(meters, copernicus.PIXEL_LIMITS["sentinel-2-l2a"] + 1e-6)
        self.assertGreater(frame["width"], 384)
        self.assertEqual(picture.size, (384, 216))
        # A mosaic whose full output uses its fine collection keeps it, at that
        # collection's pixel limit; the coarse one also shows the sea.
        fine = dict(CLOUD_LAYER, data_type="byoc-fine", low_resolution_data_type="byoc-coarse",
                    low_resolution_threshold_m=320)
        client, frames = self.client(fine)
        client.render_preview(profile(map_zoom=9), (1920, 1080), "2026-04-01")
        self.assertEqual(frames[0]["data_type"], "byoc-fine")
        self.assertGreater(frames[0]["width"], 384)
        # A layer with a coarse collection for wide views keeps the box size.
        coarse = dict(CLOUD_LAYER, low_resolution_data_type="sentinel-3-olci")
        client, frames = self.client(coarse)
        client.render_preview(profile(map_zoom=7), (1920, 1080), "2026-10-03")
        self.assertEqual((frames[0]["width"], frames[0]["height"]), (384, 216))


class PreciseCheckTests(unittest.TestCase):
    def acquisitions(self):
        left, right, whole = halves()
        return advice.acquisitions_from_features([
            feature(TODAY - dt.timedelta(days=1), 70, whole),
            feature(TODAY - dt.timedelta(days=4), 3, left),
            feature(TODAY - dt.timedelta(days=9), 2, right),
        ], profile(), SIZE)

    def test_one_mask_per_distinct_set_of_tiles_and_measured_values_rank(self):
        calls = []

        def measure(variant, newest):
            calls.append((variant, newest))
            # Only the single newest tile (70% clouds in its estimate) is clear here.
            return (100.0, 1.0) if variant.coverage_mode == "single" else (100.0, 40.0)

        progress = []
        result = advice.advise(self.acquisitions(), advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY,
                               measure=measure, progress=lambda page, pages: progress.append((page, pages)))
        replay = advice.Replay(self.acquisitions(), advice.grid_size(*SIZE))
        keys = {replay.tile_key(row.variant, row.outcome.newest) for row in result.rows if row.outcome}
        self.assertEqual(len(calls), len(keys))
        self.assertEqual(result.masks, len(keys))
        self.assertEqual(progress[-1], (len(keys), len(keys)))
        self.assertLess(len(calls), len([row for row in result.rows if row.outcome]))
        for row in result.rows:
            if row.outcome is not None:
                self.assertTrue(row.outcome.measured and row.outcome.measured_clouds)
        # The measured clouds decide: Single latest is clear now, so Fewest clouds takes it.
        self.assertEqual(result.fewest_clouds.variant.coverage_mode, "single")
        self.assertEqual(result.fewest_clouds.outcome.clouds, 1.0)

    def test_without_a_cloud_mask_the_tile_estimate_stays(self):
        result = advice.advise(self.acquisitions(), advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY,
                               measure=lambda variant, newest: (88.0, None))
        row = next(row for row in result.rows if row.outcome is not None)
        self.assertTrue(row.outcome.measured)
        self.assertFalse(row.outcome.measured_clouds)
        self.assertEqual(row.outcome.coverage, 88.0)
        plain = advice.advise(self.acquisitions(), advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY)
        self.assertEqual(plain.masks, 0)
        self.assertFalse(any(row.outcome.measured for row in plain.rows if row.outcome))


QUARTER_LAYER = {"data_type": "byoc-quarterly", "data_filter": {}, "date_granularity": "quarter"}
YEAR_LAYER = {"data_type": "byoc-annual", "data_filter": {}, "date_granularity": "year"}


def mosaic_profile(**changes):
    values = {"product": "MARBLESCAPE::S2-QUARTERLY", "layer": "TRUE_COLOR_CLOUDLESS",
              "mission": copernicus.get_product("DEFAULT-THEME", "MARBLESCAPE::S2-QUARTERLY")["missions"][0],
              "configuration": "DEFAULT-THEME", "date": "latest", "date_mode": "catalogue"}
    values.update(changes)
    return profile(**values)


def period_features(*days):
    whole = halves()[2]
    return [feature(day, None, whole) for day in days for _tile in range(3)]


class MosaicAdviceTests(unittest.TestCase):
    QUARTERS = [dt.date(2024, 7, 1), dt.date(2024, 10, 1)] + [dt.date(year, month, 1) for year in (2025, 2026)
                                          for month in (1, 4, 7, 10) if dt.date(year, month, 1) <= dt.date(2026, 4, 1)]

    def test_periods_offsets_and_labels_follow_relative_to_now(self):
        today = dt.date(2026, 12, 31)
        for offset in range(6):
            start = copernicus.rolling_quarter_start(today, offset)
            self.assertEqual(advice.period_offset(start, today, "quarter"), offset)
            start = copernicus.rolling_month_start(today, offset)
            self.assertEqual(advice.period_offset(start, today, "month"), offset)
        self.assertIsNone(advice.period_offset(dt.date(2024, 1, 1), today, "year"))
        self.assertEqual(advice.period_label(dt.date(2026, 4, 1), "quarter"), "2026 Q2")
        self.assertEqual(advice.period_label(dt.date(2026, 8, 1), "month"), "2026-08")
        self.assertEqual(advice.period_label(dt.date(2025, 1, 1), "year"), "2025")
        self.assertEqual(advice.period_start(dt.date(2026, 5, 17), "quarter"), dt.date(2026, 4, 1))

    def test_the_newest_periods_with_newest_and_needs_precise_check(self):
        result = advice.advise_mosaic(period_features(*self.QUARTERS), QUARTER_LAYER, mosaic_profile(), TODAY)
        self.assertTrue(result.mosaic)
        periods = [row.variant.period for row in result.rows[1:]]
        self.assertEqual(len(periods), advice.MOSAIC_PERIODS["quarter"])
        self.assertEqual(periods[0], dt.date(2026, 4, 1))
        self.assertEqual(periods, sorted(periods, reverse=True))
        newest = result.fewest_clouds
        self.assertIs(newest, result.rows[1])
        self.assertEqual(newest.variant.settings_text(), "Latest available (2026 Q2)")
        self.assertEqual(result.rows[2].variant.settings_text(), "Relative to now, 3 quarters ago (2026 Q1)")
        self.assertIsNone(newest.outcome.coverage)
        self.assertIsNone(result.full_coverage)  # needs Precise check
        self.assertTrue(result.rows[0].current)
        self.assertEqual(result.rows[0].variant, newest.variant)

    def test_precise_check_measures_each_period_and_full_coverage_takes_the_newest_complete(self):
        coverage = {dt.date(2026, 4, 1): 71.0, dt.date(2026, 1, 1): 99.0, dt.date(2025, 10, 1): 100.0}
        calls = []

        def measure(variant, newest):
            calls.append(variant.period)
            return coverage.get(variant.period, 100.0), None

        result = advice.advise_mosaic(period_features(*self.QUARTERS), QUARTER_LAYER, mosaic_profile(), TODAY,
                                      measure=measure)
        self.assertEqual(sorted(calls, reverse=True), [row.variant.period for row in result.rows[1:]])
        self.assertEqual(result.masks, advice.MOSAIC_PERIODS["quarter"])
        self.assertEqual(result.full_coverage.variant.period, dt.date(2026, 1, 1))
        self.assertTrue(result.full_coverage_reached)
        self.assertEqual(result.rows[0].outcome.coverage, 71.0)  # Current, measured too
        coverage = {period: 60.0 + index for index, period in enumerate(sorted(self.QUARTERS))}
        result = advice.advise_mosaic(period_features(*self.QUARTERS), QUARTER_LAYER, mosaic_profile(), TODAY,
                                      measure=lambda variant, newest: (coverage[variant.period], None))
        self.assertFalse(result.full_coverage_reached)
        self.assertEqual(result.full_coverage.variant.period, dt.date(2026, 4, 1))

    def test_the_current_row_follows_the_date_choice(self):
        features = period_features(*self.QUARTERS)
        relative = advice.advise_mosaic(features, QUARTER_LAYER,
                                        mosaic_profile(date_mode="relative_quarter", quarter_offset=3), TODAY)
        self.assertEqual(relative.rows[0].variant.period, dt.date(2026, 1, 1))
        fixed = advice.advise_mosaic(features, QUARTER_LAYER, mosaic_profile(date="2025-07-01"), TODAY)
        self.assertEqual(fixed.rows[0].variant.period, dt.date(2025, 7, 1))
        old = advice.advise_mosaic(features, QUARTER_LAYER, mosaic_profile(date="2020-01-01"), TODAY)
        self.assertIsNone(old.rows[0].outcome)
        self.assertEqual(advice.current_text(mosaic_profile(date_mode="relative_quarter", quarter_offset=2),
                                             QUARTER_LAYER), "Current: Relative to now, 2 quarters ago")
        self.assertEqual(advice.current_text(mosaic_profile(date="2025-07-01"), QUARTER_LAYER),
                         "Current: Specific quarter 2025 Q3")
        self.assertEqual(advice.current_text(mosaic_profile(), QUARTER_LAYER), "Current: Latest available")

    def test_an_older_year_is_taken_as_that_year(self):
        years = [dt.date(year, 1, 1) for year in range(2020, 2026)]
        result = advice.advise_mosaic(period_features(*years), YEAR_LAYER, mosaic_profile(), TODAY)
        self.assertEqual(result.rows[1].variant.settings_text(), "Latest available (2025)")
        self.assertEqual(result.rows[2].variant.settings_text(), "Specific year 2024")
        self.assertIs(advice.variant_profile({"date": "latest"}, result.rows[2].variant)["date"], "latest")


class AutoRecommendationTests(unittest.TestCase):
    def advice(self):
        left, right, whole = halves()
        items = advice.acquisitions_from_features([
            feature(TODAY - dt.timedelta(days=1), 70, whole),
            feature(TODAY - dt.timedelta(days=4), 3, left),
            feature(TODAY - dt.timedelta(days=9), 2, right),
        ], profile(), SIZE)
        return advice.advise(items, advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY)

    def test_the_setting_is_validated_and_off_by_default(self):
        self.assertFalse(profile()["auto_recommendation"])
        self.assertEqual(profile()["auto_priority"], "fewest_clouds")
        with self.assertRaises(ValueError):
            profile(auto_priority="sharpest")
        with self.assertRaises(ValueError):
            profile(auto_recommendation="yes")

    def test_the_priority_falls_back_to_the_other_one_then_to_none(self):
        result = self.advice()
        self.assertIs(advice.auto_row(result, "fewest_clouds"), result.fewest_clouds)
        self.assertIs(advice.auto_row(result, "full_coverage"), result.full_coverage)
        result.fewest_clouds = None
        self.assertIs(advice.auto_row(result, "fewest_clouds"), result.full_coverage)
        # Newest of regular layers is its own rule; it still finds the newest variant.
        self.assertEqual(advice.auto_row(result, "newest").variant.cloud_limit, 100)
        result.full_coverage = None
        self.assertIsNone(advice.auto_row(result, "fewest_clouds"))

    def test_newest_takes_every_cloud_estimate_and_the_shortest_gap_fill_reaching_90(self):
        left, right, whole = halves()
        items = advice.acquisitions_from_features([
            feature(TODAY - dt.timedelta(days=1), 95, whole),
            feature(TODAY - dt.timedelta(days=4), 3, left),
            feature(TODAY - dt.timedelta(days=9), 2, right),
        ], profile(), SIZE)
        coverage = {("single", None): 40.0, ("fill_gaps", 7): 85.0, ("fill_gaps", 14): 95.0,
                    ("fill_gaps", 30): 100.0, ("fill_gaps", 60): 100.0}

        def measure(variant, newest):
            return coverage[(variant.coverage_mode, variant.lookback_days)], 60.0

        result = advice.advise(items, advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY, measure=measure)
        row = advice.auto_row(result, "newest")
        self.assertEqual((row.variant.cloud_limit, row.variant.coverage_mode, row.variant.lookback_days),
                         (100, "fill_gaps", 14))
        self.assertEqual(row.outcome.newest, TODAY - dt.timedelta(days=1))  # the cloudy newest one
        # Without a variant at 90% the most covered one is taken, the newest still on top.
        coverage.update({("fill_gaps", 14): 60.0, ("fill_gaps", 30): 70.0, ("fill_gaps", 60): 70.0})
        result = advice.advise(items, advice.grid_size(*SIZE), CLOUD_LAYER, profile(), TODAY, measure=measure)
        row = advice.auto_row(result, "newest")
        self.assertEqual((row.variant.coverage_mode, row.variant.lookback_days), ("fill_gaps", 7))
        # The labels: Newest is the third priority of regular layers, the first of mosaics.
        self.assertEqual(advice.priority_label("newest", False), "Newest")
        self.assertEqual(advice.priority_label("fewest_clouds", False), "Fewest clouds")
        self.assertEqual(advice.priority_label("fewest_clouds", True), "Newest")
        self.assertEqual(advice.priority_label("full_coverage", False), "Data coverage")

    def test_a_choice_renders_like_apply(self):
        saved = profile(max_cloud_cover=30, coverage_mode="single", date="2026-09-01")
        scene = advice.Row(advice.Variant(50, "fill_gaps", 30), None, 0, 0)
        rendered = advice.applied_profile(saved, scene)
        self.assertEqual((rendered["max_cloud_cover"], rendered["coverage_mode"], rendered["lookback_days"],
                          rendered["date"], rendered["date_mode"]), (50, "fill_gaps", 30, "latest", "catalogue"))
        day = dt.date(2026, 4, 1)
        outcome = advice.Outcome(day, day, 100.0, None, 1)
        older = advice.Row(advice.Variant(None, "single", None, period=day, granularity="quarter", offset=2),
                           outcome, 0, 0)
        rendered = advice.applied_profile(mosaic_profile(), older)
        self.assertEqual((rendered["date_mode"], rendered["quarter_offset"], rendered["date"]),
                         ("relative_quarter", 2, "latest"))
        newest = advice.Row(advice.Variant(None, "single", None, period=day, granularity="quarter", offset=2,
                                           newest=True), outcome, 0, 0)
        self.assertEqual(advice.applied_profile(mosaic_profile(date="2025-07-01"), newest)["date"], "latest")
        year = advice.Row(advice.Variant(None, "single", None, period=dt.date(2024, 1, 1), granularity="year"),
                          advice.Outcome(dt.date(2024, 1, 1), dt.date(2024, 1, 1), 100.0, None, 1), 0, 0)
        self.assertEqual(advice.applied_profile(mosaic_profile(), year)["date"], "2024-01-01")

    def test_recommended_profile_uses_the_catalogue_and_remembers_the_choice(self):
        result = self.advice()

        class Client:
            def _selection(self, value):
                return copernicus.normalize_profile(value), {"id": "p"}, CLOUD_LAYER

        saved = profile(latitude=12.5, auto_recommendation=False)
        with unittest.mock.patch.object(advice, "fetch_advice", return_value=result) as fetch:
            self.assertEqual(advice.recommended_profile(Client(), saved, SIZE), (saved, None))
            fetch.assert_not_called()
            chosen, text = advice.recommended_profile(Client(), dict(saved, auto_recommendation=True), SIZE)
        self.assertFalse(fetch.call_args.kwargs["precise"])  # scenes: catalogue only
        with unittest.mock.patch.object(advice, "fetch_advice", return_value=result) as fetch:
            advice.recommended_profile(Client(), dict(saved, auto_recommendation=True, auto_precise=True), SIZE)
        self.assertTrue(fetch.call_args.kwargs["precise"])  # Always use precise check
        self.assertFalse(fetch.call_args.kwargs["reuse_masks"])
        self.assertEqual(text, result.fewest_clouds.variant.settings_text())
        self.assertEqual(chosen["max_cloud_cover"], result.fewest_clouds.variant.cloud_limit)
        self.assertEqual(advice.last_choice(saved)[0], text)
        result.fewest_clouds = result.full_coverage = None
        with unittest.mock.patch.object(advice, "fetch_advice", return_value=result):
            chosen, text = advice.recommended_profile(Client(), dict(saved, auto_recommendation=True), SIZE)
        self.assertIsNone(text)
        self.assertEqual(chosen["max_cloud_cover"], saved["max_cloud_cover"])

    def test_a_mosaic_full_coverage_measures_each_period_once(self):
        calls = []

        class Client:
            def _selection(self, value):
                return copernicus.normalize_profile(value), {"id": "p"}, QUARTER_LAYER

            def search_view(self, value, size, start, end, progress=None):
                return period_features(*MosaicAdviceTests.QUARTERS), QUARTER_LAYER

            def view_mask(self, value, size, date):
                calls.append(date)
                return 100.0, None

        saved = mosaic_profile(latitude=33.25, auto_recommendation=True, auto_priority="full_coverage")
        chosen, text = advice.recommended_profile(Client(), saved, SIZE, today=TODAY)
        self.assertEqual(text, "Latest available (2026 Q2)")
        self.assertEqual(len(calls), advice.MOSAIC_PERIODS["quarter"])
        advice.recommended_profile(Client(), saved, SIZE, today=TODAY)
        self.assertEqual(len(calls), advice.MOSAIC_PERIODS["quarter"])  # kept for the session


class ViewMaskTests(unittest.TestCase):
    def client(self, layer, refuse_above=None):
        client = copernicus.CopernicusClient("id", "secret")
        client._selection = lambda value: (copernicus.normalize_profile(value), {"id": "p"}, layer)
        requests = []

        def process_tile(frame, bbox, width, height, evalscript=None):
            from PIL import Image
            meters = (bbox[2] - bbox[0]) / width
            if refuse_above and meters > refuse_above:
                raise RuntimeError(f"Copernicus service returned HTTP 400: Pixel size of {meters:.2f} "
                                   f"meters per pixel exceeds the limit {refuse_above:.2f} meters per "
                                   f"pixel for collection X.")
            requests.append((frame, width, height, evalscript))
            image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            image.paste((255, 0, 0, 255), (0, 0, width // 2, height))        # data, clear
            image.paste((255, 255, 0, 255), (0, 0, width // 4, height))      # data, cloudy
            return image, 0

        client._process_tile = process_tile
        return client, requests

    def test_coverage_and_clouds_are_shares_of_the_view(self):
        client, requests = self.client(CLOUD_LAYER)
        coverage, clouds = client.view_mask(profile(map_zoom=12), (1920, 1080), "2026-10-03")
        self.assertAlmostEqual(coverage, 50.0, delta=0.5)
        self.assertAlmostEqual(clouds, 25.0, delta=0.5)
        frame, width, height, script = requests[0]
        self.assertEqual((width, height), (160, 90))
        self.assertEqual(frame["date"], "2026-10-03")
        self.assertIn('"SCL"', script)

    def test_radar_has_coverage_only(self):
        client, requests = self.client(RADAR_LAYER)
        coverage, clouds = client.view_mask(profile(), (1920, 1080), "2026-10-03")
        self.assertIsNone(clouds)
        self.assertNotIn("SCL", requests[0][3])

    def test_a_collection_limit_is_respected_and_an_unknown_one_learnt(self):
        l1c = {"data_type": "sentinel-2-l1c", "data_filter": {"maxCloudCoverage": 30}}
        client, requests = self.client(l1c)
        client.view_mask(profile(map_zoom=10), (1920, 1080), "2026-10-03")
        frame = requests[0][0]
        meters = copernicus.ground_meters_per_pixel(frame["profile"], 1920, 1080) / frame["view_scale"]
        self.assertLessEqual(meters, 200.0)
        layer = {"data_type": "sentinel-x-test", "data_filter": {}}
        self.addCleanup(copernicus.PIXEL_LIMITS.pop, "sentinel-x-test", None)
        client, requests = self.client(layer, refuse_above=500.0)
        client.view_mask(profile(map_zoom=10), (1920, 1080), "2026-10-03")
        self.assertEqual(copernicus.PIXEL_LIMITS["sentinel-x-test"], 500.0)
        self.assertEqual(len(requests), 1)

    def test_costs_follow_the_request_size(self):
        client, _requests = self.client(CLOUD_LAYER)
        preview, mask = client.small_units(profile(map_zoom=12), (1920, 1080))
        self.assertAlmostEqual(preview, 384 * 216 / 512 ** 2 * 4 / 3)
        self.assertAlmostEqual(mask, 160 * 90 / 512 ** 2 / 3)
        l1c = {"data_type": "sentinel-2-l1c", "data_filter": {"maxCloudCoverage": 30}}
        client, _requests = self.client(l1c)
        wide, _mask = client.small_units(profile(map_zoom=10), (1920, 1080))
        self.assertGreater(wide, 1.0)


class SearchViewTests(unittest.TestCase):
    def test_pages_the_whole_view_with_every_cloud_estimate(self):
        client = copernicus.CopernicusClient("id", "secret")
        sent = []
        pages = [
            {"features": [feature(TODAY, 5, halves()[2])], "context": {"next": "two", "matched": 150}},
            {"features": [feature(TODAY, 50, halves()[2]), "junk"], "context": {"matched": 150}},
        ]

        def request(url, payload):
            sent.append((url, dict(payload)))
            return pages[len(sent) - 1]

        client._json_request = request
        progress = []
        layer = {"data_type": "sentinel-2-l2a", "data_filter": {"maxCloudCoverage": 30},
                 "start_date": "2015-06-27"}
        client._selection = lambda value: (copernicus.normalize_profile(value), {"id": "p"}, layer)
        start = dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc)
        end = dt.datetime(2026, 10, 6, tzinfo=dt.timezone.utc)
        features, returned_layer = client.search_view(
            profile(), SIZE, start, end, progress=lambda page, total: progress.append((page, total)))
        self.assertIs(returned_layer, layer)
        self.assertEqual(len(features), 2)
        self.assertEqual(progress, [(1, 2), (2, 2)])
        first = sent[0][1]
        self.assertEqual(first["intersects"]["type"], "Polygon")
        self.assertEqual(first["filter"], "eo:cloud_cover>=0 and eo:cloud_cover<=100")
        self.assertNotIn("distinct", first)
        self.assertEqual(sent[1][1]["next"], "two")


if __name__ == "__main__":
    unittest.main()
