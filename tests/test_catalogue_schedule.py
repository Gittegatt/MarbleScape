import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from marblescape_catalogue_schedule import CatalogueSchedule, latest_due_slot, normalize_refresh_time


class CatalogueScheduleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.clock_time = "03:00:00"
        self.stop = threading.Event()
        self.refresh = Mock()
        self.service = CatalogueSchedule(self.path, lambda: self.clock_time, self.refresh, self.stop)
        self.enterContext(patch("marblescape_catalogue_schedule.threading.Thread",
            side_effect=lambda **kw: SimpleNamespace(start=kw["target"])))

    def test_strict_clock_format(self):
        for value in ("00:00:00", "23:59:59", "03:04:05"):
            self.assertEqual(normalize_refresh_time(value), value)
        for value in (None, "3:00:00", "24:00:00", "12:60:00", "00:00:60", "12:00", 300):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_refresh_time(value)

    def test_due_slot_before_at_and_after_time(self):
        self.assertEqual(latest_due_slot(dt.datetime(2026, 1, 1, 2), self.clock_time), "2025-12-31T03:00:00")
        self.assertEqual(latest_due_slot(dt.datetime(2026, 1, 1, 3), self.clock_time), "2026-01-01T03:00:00")
        self.assertEqual(latest_due_slot(dt.datetime(2026, 1, 1, 4), self.clock_time), "2026-01-01T03:00:00")

    def test_catchup_once_and_persist_across_restart(self):
        self.assertTrue(self.service.request(now=dt.datetime(2026, 9, 20, 4)))
        restarted = CatalogueSchedule(self.path, lambda: self.clock_time, self.refresh, self.stop)
        self.assertFalse(restarted.request(now=dt.datetime(2026, 9, 20, 5)))
        self.assertTrue(restarted.request(now=dt.datetime(2026, 9, 27, 2)))
        self.assertEqual(restarted.status()["completed_slot"], "2026-09-26T03:00:00")
        self.assertTrue(restarted.request(now=dt.datetime(2026, 9, 27, 3)))
        self.assertEqual(self.refresh.call_count, 3)

    def test_manual_force_and_overlap(self):
        now = dt.datetime(2026, 9, 27, 4)
        self.assertTrue(self.service.request(now=now))
        self.assertTrue(self.service.request(force=True, now=now))
        self.service._running = True
        self.assertFalse(self.service.request(force=True, now=now))
        self.assertEqual(self.refresh.call_count, 2)

    def test_failure_is_not_completed_and_cancelled_job_catches_up(self):
        self.refresh.side_effect = RuntimeError("offline")
        self.assertTrue(self.service.request(now=dt.datetime(2026, 9, 27, 4)))
        self.assertFalse(self.path.exists())
        self.assertIn("offline", self.service.status()["message"])
        self.assertFalse(self.service.request(now=dt.datetime(2026, 9, 27, 4)))
        self.refresh.side_effect = self.stop.set
        self.assertTrue(self.service.request(force=True, now=dt.datetime(2026, 9, 27, 4)))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.service.request(force=True))

    def test_dst_skipped_hour_and_repeated_hour_run_once(self):
        self.clock_time = "02:30:00"
        self.service.request(now=dt.datetime(2026, 3, 29, 3))
        self.assertEqual(self.service.status()["completed_slot"], "2026-03-29T02:30:00")
        self.service.request(now=dt.datetime(2026, 10, 25, 2, 31, fold=0))
        self.assertFalse(self.service.request(now=dt.datetime(2026, 10, 25, 2, 31, fold=1)))

    def test_changed_time_and_corrupt_state(self):
        self.path.write_text("broken", encoding="utf-8")
        service = CatalogueSchedule(self.path, lambda: self.clock_time, self.refresh, self.stop)
        service.request(now=dt.datetime(2026, 9, 27, 4))
        self.clock_time = "05:00:00"
        self.assertFalse(service.request(now=dt.datetime(2026, 9, 27, 4)))
        self.assertTrue(service.request(now=dt.datetime(2026, 9, 27, 5)))
        self.assertEqual(json.loads(self.path.read_text())["completed_slot"], "2026-09-27T05:00:00")

    def test_status_reports_when_the_last_refresh_actually_completed(self):
        self.assertEqual(self.service.status()["completed_at"], "")
        before = dt.datetime.now().replace(microsecond=0)
        self.assertTrue(self.service.request(now=dt.datetime(2026, 9, 29, 4)))
        finished = self.service.status()["completed_at"]
        self.assertGreaterEqual(dt.datetime.fromisoformat(finished), before)
        self.assertEqual(json.loads(self.path.read_text())["completed_at"], finished)
        # A newly saved time moves the planned slot, not the completion time.
        self.clock_time = "22:00:00"
        self.assertFalse(self.service.request(now=dt.datetime(2026, 9, 29, 23, 50)))
        self.assertEqual(self.service.status()["completed_at"], finished)
        self.assertEqual(json.loads(self.path.read_text())["completed_at"], finished)
        # A failed refresh keeps the last completion time; a restart reads it.
        self.refresh.side_effect = RuntimeError("offline")
        self.service.request(force=True, now=dt.datetime(2026, 9, 30, 23))
        self.assertEqual(self.service.status()["completed_at"], finished)
        restarted = CatalogueSchedule(self.path, lambda: self.clock_time, self.refresh, self.stop)
        self.assertEqual(restarted.status()["completed_at"], finished)

    def test_newly_saved_time_that_already_passed_today_waits_for_its_next_slot(self):
        self.assertTrue(self.service.request(now=dt.datetime(2026, 9, 29, 4)))
        self.assertEqual(self.refresh.call_count, 1)
        # Saving 22:00 at 23:50 must not count today's 22:00 as a missed run.
        self.clock_time = "22:00:00"
        self.assertFalse(self.service.request(now=dt.datetime(2026, 9, 29, 23, 50)))
        self.assertEqual(self.service.status()["completed_slot"], "2026-09-29T22:00:00")
        self.assertEqual(json.loads(self.path.read_text())["completed_slot"], "2026-09-29T22:00:00")
        restarted = CatalogueSchedule(self.path, lambda: self.clock_time, self.refresh, self.stop)
        self.assertFalse(restarted.request(now=dt.datetime(2026, 9, 29, 23, 55)))
        self.assertTrue(restarted.request(now=dt.datetime(2026, 9, 30, 22)))
        self.assertEqual(self.refresh.call_count, 2)

    def test_first_request_after_start_still_catches_up_a_missed_run(self):
        self.path.write_text(json.dumps({"completed_slot": "2026-09-28T03:00:00"}), encoding="utf-8")
        service = CatalogueSchedule(self.path, lambda: self.clock_time, self.refresh, self.stop)
        self.assertTrue(service.request(now=dt.datetime(2026, 9, 29, 9)))

    def test_status_reports_the_last_error_until_the_next_run(self):
        self.refresh.side_effect = RuntimeError("offline")
        self.service.request(force=True, now=dt.datetime(2026, 9, 27, 4))
        self.assertEqual(self.service.status()["error"], "offline")
        self.refresh.side_effect = None
        self.service.request(force=True, now=dt.datetime(2026, 9, 27, 4))
        self.assertEqual(self.service.status()["error"], "")


class CatalogueRefreshIntegrationTests(unittest.TestCase):
    def test_all_public_sources_and_distinct_saved_copernicus_selections_refresh(self):
        import marblescape_download as app
        from copy import deepcopy

        selected = deepcopy(app.DEFAULT_SOURCE_PROFILES["copernicus"])
        second = dict(selected, latitude=20.0)
        public, copernicus = Mock(), Mock()
        public.refresh_all_catalogues.return_value = {"errors": []}
        copernicus.list_dates.return_value = ["2026-07-01"]
        library = {"items": [{"settings": {
            "source": {"provider": "copernicus"},
            "sources": {"copernicus": profile},
            "output": {"width": 3840, "height": 2160},
        }} for profile in (selected, selected, second)]}
        with patch.object(app, "get_catalogue_client", return_value=public), \
             patch.object(app, "get_copernicus_client", return_value=copernicus), \
             patch.object(app, "COPERNICUS_CLIENT_ID", "test"), \
             patch.object(app, "COPERNICUS_CLIENT_SECRET", "test"), \
             patch.object(app, "SOURCE_PROFILES", {"copernicus": selected}), \
             patch.object(app, "IMAGE_PROFILE_LIBRARY", library), \
             patch.object(app, "get_output_dimensions", return_value=(3840, 2160)), \
             patch.object(app, "NETWORK_ACTIVITY", Mock()):
            app.refresh_all_catalogues_now()
        public.refresh_all_catalogues.assert_called_once_with(refresh=True, startup=False)
        self.assertEqual(copernicus.list_dates.call_count, 2)
        self.assertEqual(public.store_copernicus_dates.call_count, 2)
        self.assertEqual(copernicus.list_dates.call_args.args, (second, (3840, 2160)))

    def test_missing_copernicus_credentials_skip_auth_and_public_failure_is_reported(self):
        import marblescape_download as app

        public = Mock()
        public.refresh_all_catalogues.return_value = {"errors": []}
        with patch.object(app, "get_catalogue_client", return_value=public), \
             patch.object(app, "COPERNICUS_CLIENT_ID", ""), \
             patch.object(app, "get_copernicus_client") as copernicus:
            app.refresh_all_catalogues_now()
            copernicus.assert_not_called()
            public.refresh_all_catalogues.return_value = {"errors": ["NOAA unavailable"]}
            with self.assertRaisesRegex(RuntimeError, "NOAA unavailable"):
                app.refresh_all_catalogues_now()
