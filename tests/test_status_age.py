"""The Image header names the picture's age for every source and date choice."""

import datetime as dt
import unittest
from unittest import mock

import marblescape_copernicus as copernicus
import marblescape_download as app


def stamp(**ago):
    """A time this long ago, taken when the test runs."""
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**ago)).isoformat().replace("+00:00", "Z")


class AgeTextTests(unittest.TestCase):
    def test_minutes_then_hours_then_days(self):
        now = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.timezone.utc)
        for ago, expected in ((dt.timedelta(minutes=7), "7 min old"),
                              (dt.timedelta(minutes=119), "119 min old"),
                              (dt.timedelta(hours=7), "7 h old"),
                              (dt.timedelta(hours=47), "47 h old"),
                              (dt.timedelta(days=190), "190 days old"),
                              (dt.timedelta(minutes=-5), "0 min old")):
            with self.subTest(ago=ago):
                self.assertEqual(app.picture_age_text(now - ago, now), expected)


class OutcomeNoticeTests(unittest.TestCase):
    def test_every_outcome_has_its_symbol_color_profile_and_time(self):
        when = dt.datetime(2026, 10, 8, 10, 40, tzinfo=dt.timezone.utc)
        expected = {
            "downloaded": ("✓ Image downloaded", "success"),
            "cache": ("✓ Image restored from cache", "link"),
            "current": ("✓ Image up to date", "link"),
            "LOST": ("? Image source no longer listed", "warning"),
            "SOURCE": ("? Source unavailable", "warning"),
            "NETWORK": ("! Network issue", "warning"),
            "UNAVAIL": ("! Image unavailable", "warning"),
            "cancelled": ("× Download cancelled", None),
        }
        for kind, (text, color) in expected.items():
            with self.subTest(kind=kind):
                # Only the status and the time; the profile is not named.
                outcome = {"kind": kind, "profile": "Japan, Tokyo", "time": when}
                self.assertEqual(app.image_outcome_notice(outcome, "utc"), (f"{text} · 10:40 UTC", color))
        self.assertEqual(app.image_outcome_notice({"kind": "current", "profile": None, "time": when}, "utc"),
                         ("✓ Image up to date · 10:40 UTC", "link"))
        self.assertIsNone(app.image_outcome_notice({"kind": None}))

    def test_catalogue_notes_name_their_sources(self):
        when = dt.datetime(2026, 10, 8, 1, 0, tzinfo=dt.timezone.utc)
        expected = {
            ("catalogues_refreshed", None): ("✓ Catalogues refreshed · 01:00 UTC", "success"),
            ("catalogues_current", None): ("✓ Catalogues up to date · 01:00 UTC", None),
            ("catalogues_incomplete", "NOAA, Himawari"):
                ("? Catalogue refresh incomplete: NOAA, Himawari · 01:00 UTC", "warning"),
            ("catalogues_network", None): ("! Catalogue refresh failed: network issue · 01:00 UTC", "warning"),
            ("catalogue_refreshed", "Himawari"): ("✓ Catalogue refreshed: Himawari · 01:00 UTC", "success"),
            ("catalogue_incomplete", "Himawari"): ("? Catalogue incomplete: Himawari · 01:00 UTC", "warning"),
            ("catalogue_network", "Himawari"):
                ("! Catalogue refresh failed: network issue (Himawari) · 01:00 UTC", "warning"),
            ("storms_updated", "NOAA (+1 new, 1 ended)"):
                ("✓ Active storms updated: NOAA (+1 new, 1 ended) · 01:00 UTC", "link"),
        }
        for (kind, detail), notice in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(app.image_outcome_notice({"kind": kind, "detail": detail, "time": when}, "utc"),
                                 notice)

    def test_a_full_catalogue_refresh_notes_its_result(self):
        class Client:
            def __init__(self, result=None, error=None):
                self.result, self.error = result, error

            def refresh_all_catalogues(self, refresh=True, startup=False):
                if self.error:
                    raise self.error
                return self.result

        def run(client, online=True):
            with mock.patch.object(app, "get_catalogue_client", return_value=client), \
                    mock.patch.object(app, "COPERNICUS_CLIENT_ID", ""), \
                    mock.patch.object(app, "internet_connected", return_value=online), \
                    mock.patch.dict(app.IMAGE_OUTCOME, {"serial": 0, "kind": None, "profile": None,
                                                        "detail": None, "time": None}):
                try:
                    app.refresh_all_catalogues_now()
                except (RuntimeError, OSError):
                    pass  # A failed refresh still raises, so the schedule tries again.
                return app.image_outcome()["kind"], app.image_outcome()["detail"]

        complete = {"errors": [], "updated_sources": 2, "incomplete_sources": []}
        self.assertEqual(run(Client(complete)), ("catalogues_refreshed", None))
        self.assertEqual(run(Client(dict(complete, updated_sources=0))), ("catalogues_current", None))
        partial = {"errors": ["NOAA: one WFO page failed"], "updated_sources": 1, "incomplete_sources": ["NOAA"]}
        self.assertEqual(run(Client(partial)), ("catalogues_incomplete", "NOAA"))
        self.assertEqual(run(Client(partial), online=False), ("catalogues_network", "NOAA"))
        self.assertEqual(run(Client(error=OSError("offline")), online=False)[0], "catalogues_network")
        self.assertEqual(run(Client(error=OSError("broken"))), ("catalogues_incomplete", "all sources"))

    def test_recording_counts_up_and_names_the_profile(self):
        with mock.patch.dict(app.IMAGE_OUTCOME, {"serial": 4, "kind": None, "profile": None, "time": None}), \
                mock.patch.object(app, "image_profile_name", return_value="Japan, Tokyo"):
            app.record_image_outcome("NETWORK", "1" * 32)
            outcome = app.image_outcome()
            self.assertEqual((outcome["serial"], outcome["kind"], outcome["profile"]), (5, "NETWORK", "Japan, Tokyo"))
            app.record_image_outcome("current", None)
            self.assertEqual((app.image_outcome()["serial"], app.image_outcome()["profile"]), (6, None))


class ShortIntervalHintTests(unittest.TestCase):
    def test_update_checks_under_two_minutes_get_a_hint(self):
        self.assertIn("5-15 minutes", app.short_update_interval_hint("1", "minutes"))
        for value, unit in (("2", "minutes"), ("1", "hours"), ("x", "minutes"), ("1", "years")):
            self.assertEqual(app.short_update_interval_hint(value, unit), "")


class HeaderAgeTests(unittest.TestCase):
    def header(self, source, render_mode, requests, profiles=None, image_time=None, source_time=None):
        with mock.patch.object(app, "IMAGE_SOURCE", source), \
             mock.patch.object(app, "SOURCE_PROFILES", profiles or {}), \
             mock.patch.object(app, "IMAGE_TIME", image_time), \
             mock.patch.object(app, "IMAGE_STATUS", dict(app.IMAGE_STATUS)):
            app.record_source_frame_status(render_mode, requests, source_time=source_time)
            return app.image_header_status_text("utc")

    def copernicus(self, timestamp, latest=False, **values):
        profile = {**copernicus.DEFAULT_PROFILE, **values}
        frame = {"profile": profile, "layer": {}, "timestamp": timestamp, "latest": latest}
        return self.header("copernicus", "copernicus", [{"frame": frame}], {"copernicus": profile})

    def test_every_copernicus_date_choice_shows_the_age(self):
        latest = self.copernicus(stamp(hours=7), latest=True)
        self.assertIn("latest acquisition", latest)
        self.assertIn(" · 7 h old", latest)
        selected = self.copernicus(stamp(days=190), date_mode="catalogue")
        self.assertIn("selected acquisition", selected)
        self.assertIn(" · 190 days old", selected)
        for mode in ("relative_month", "relative_quarter"):
            with self.subTest(mode=mode):
                self.assertIn(" · 99 days old", self.copernicus(stamp(days=99), date_mode=mode))

    def test_the_age_is_always_last_and_mosaics_are_short(self):
        latest = self.copernicus(stamp(hours=7), latest=True, coverage_mode="fill_gaps", lookback_days=30)
        self.assertTrue(latest.endswith(" · 30-day gap fill · 7 h old"), latest)
        fixed = self.copernicus(stamp(days=190), date_mode="catalogue")
        self.assertTrue(fixed.endswith(" · 190 days old"), fixed)
        # A mosaic uses no Gap fill: the line does not name one.
        quarter = self.copernicus(stamp(days=99), date_mode="relative_quarter", quarter_offset=1,
                                  coverage_mode="fill_gaps", lookback_days=30)
        self.assertRegex(quarter, r"rolling quarter \d{4} Q\d \(-1Q\) · mosaic \d{4}-\d\d-\d\d · 99 days old$")
        self.assertNotIn("gap fill", quarter)
        for offset, text in ((0, "(current)"), (1, "(-1 month)"), (3, "(-3 months)")):
            with self.subTest(offset=offset):
                month = self.copernicus(stamp(days=40), date_mode="relative_month", month_offset=offset)
                self.assertIn(text, month)
                self.assertTrue(month.endswith(" · 40 days old"), month)
        self.assertIn("(current)", self.copernicus(stamp(days=9), date_mode="relative_quarter",
                                                   quarter_offset=0))

    def test_worldview_and_still_sources_show_the_age(self):
        frame = {"timestamp": stamp(days=3), "fixed_time": True}
        text = self.header("worldview", "worldview", [{"frame": frame}])
        self.assertIn("selected acquisition", text)
        self.assertIn(" · 3 days old", text)
        frame = {"timestamp": stamp(minutes=12), "expected_interval_seconds": 600}
        self.assertTrue(self.header("goes_east", "noaa", [{"frame": frame}]).endswith(" · 12 min old"))
        frame = {"timestamp": stamp(hours=5), "expected_interval_seconds": 600}
        self.assertTrue(self.header("himawari", "himawari", [{"frame": frame}]).endswith(" · 5 h old (delayed)"))

    def test_eumetsat_shows_its_layer_time_and_age(self):
        text = self.header("eumetsat", "server", [], source_time=stamp(minutes=20))
        self.assertTrue(text.startswith("On screen: EUMETSAT: "), text)
        self.assertTrue(text.endswith(" · 20 min old"), text)
        fixed = self.header("eumetsat", "local", [], image_time="2026-01-01T12:00:00Z",
                            source_time="2026-01-01T12:00:00Z")
        self.assertIn("selected acquisition", fixed)
        self.assertNotIn("delayed", fixed)
        # Layers without any time (basemaps only) keep the short line.
        self.assertEqual(self.header("eumetsat", "server", []), "On screen: EUMETSAT")


if __name__ == "__main__":
    unittest.main()
