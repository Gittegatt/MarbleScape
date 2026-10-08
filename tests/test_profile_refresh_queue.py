"""Force loading new images of saved profiles in the background; network and Windows are mocked."""

from copy import deepcopy
import os
import unittest
import unittest.mock
from unittest.mock import patch

import marblescape_download as app
from marblescape_download_progress import DownloadCancelledError
from marblescape_profile_settings import profile_status_text
from marblescape_profiles import normalize_library
import test_rotation_runtime as rotation_tests
import test_source_runtime as source_tests


class ProfileRefreshQueueTests(unittest.TestCase):
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    run_cycles = rotation_tests.RotationRuntimeTests.run_cycles

    def setUp(self):
        source_tests.SourceRuntimeTests.setUp(self)
        app.clear_profile_refresh_queue()

    def tearDown(self):
        app.clear_profile_refresh_queue()
        source_tests.SourceRuntimeTests.tearDown(self)

    def library(self, providers=("goes_east", "goes_west")):
        snapshot = app.image_settings_snapshot()
        items = []
        for index, provider in enumerate(providers, 1):
            settings = deepcopy(snapshot)
            settings["source"]["provider"] = provider
            settings["view"]["zoom"] = 2.0
            items.append({"id": f"{index:032x}", "name": provider, "settings": settings})
        return normalize_library({"items": items, "rotation": {
            "enabled": False, "interval": 1, "unit": "minutes", "order": []}})

    def test_queue_keeps_order_without_duplicates_and_skips_deleted_profiles(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        first, second = (item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"])
        self.assertEqual(app.queue_profile_refreshes(["9" * 32, second, first, second]),
                         ["9" * 32, second, first])
        self.assertEqual(app.queue_profile_refreshes([first]), [])
        self.assertEqual(app.next_profile_refresh()["id"], second)
        self.assertEqual(app.queued_profile_refreshes(), (second, first))
        self.assertEqual(app.clear_profile_refresh_queue(), 2)
        self.assertIsNone(app.next_profile_refresh())

    def test_queued_profiles_load_into_their_cache_without_changing_the_wallpaper(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        app.SET_WINDOWS_WALLPAPER = True
        app.ACTIVE_CONFIG_PATH.write_text("unchanged", encoding="utf-8")
        ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        seen = []

        def fetch(frame, size, **options):
            seen.append((app.IMAGE_SOURCE, app.ZOOM))
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        app.queue_profile_refreshes(ids)
        self.run_cycles(1)
        # Both profiles load first with their own settings, then the regular cycle.
        self.assertEqual(seen, [("goes_east", 2.0), ("goes_west", 2.0), ("goes_east", 1.0)])
        self.assertEqual(app.queued_profile_refreshes(), ())
        entries = app.get_profile_cache().entries(ids)
        self.assertEqual(set(entries), set(ids))
        self.assertEqual((app.IMAGE_SOURCE, app.ZOOM), ("goes_east", 1.0))
        self.assertEqual(app.ACTIVE_CONFIG_PATH.read_text(encoding="utf-8"), "unchanged")
        cache_dir = app.get_profile_cache().images_dir
        self.assertFalse(app.get_current_image_path().is_relative_to(cache_dir))
        if os.name == "nt":
            self.assertEqual(self.wallpaper.call_count, 1)
            self.assertFalse(self.wallpaper.call_args.args[0].is_relative_to(cache_dir))

    def test_forced_update_of_the_shown_picture_goes_before_the_queue(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_west",))
        app.queue_profile_refreshes([app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]])
        app.FORCE_UPDATE_EVENT.set()
        self.run_cycles(1)
        self.assertEqual(self.client.fetch_image.call_count, 1)
        self.assertEqual(len(app.queued_profile_refreshes()), 1)

    def test_an_applied_image_loads_before_the_rest_of_the_queue(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        applied = app.image_settings_snapshot()
        applied["source"]["provider"] = "goes_west"
        applied["view"]["zoom"] = 3.0
        seen = []

        def fetch(frame, size, **options):
            seen.append((app.IMAGE_SOURCE, app.ZOOM))
            if len(seen) == 1:
                # Apply Image while the first queued profile downloads.
                app.CONFIGURATION_RELOAD_EVENT.set()
            return self.make_png(frame, size, **options)

        def sleep(*_args, **_kwargs):
            return bool(app.queued_profile_refreshes())

        self.client.fetch_image.side_effect = fetch
        app.queue_profile_refreshes(ids)
        app.RUN_CONTINUOUSLY = True
        clock = [100.0]
        with patch.object(app, "load_configuration", side_effect=lambda _path: app.apply_image_settings(applied)),              patch.object(app.time, "monotonic", side_effect=lambda: clock[0]),              patch.object(app, "sleep_until_next_cycle", side_effect=sleep):
            app.main([], configuration_loaded=True)
        # The running download finishes, then the applied image, then the queue.
        self.assertEqual(seen, [("goes_east", 2.0), ("goes_west", 3.0), ("goes_west", 2.0)])
        self.assertEqual(app.queued_profile_refreshes(), ())
        self.assertEqual(set(app.get_profile_cache().entries(ids)), set(ids))

    def test_failed_download_restores_the_configuration_and_skips_the_profile(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        before = app.capture_loaded_configuration()
        self.client.fetch_image.side_effect = [ValueError("broken source"),
                                               self.make_png(self.frame, (32, 18))]
        app.queue_profile_refreshes(ids)
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(app.capture_loaded_configuration(), before)
        self.assertEqual(app.queued_profile_refreshes(), (ids[1],))
        self.assertTrue(any("failed: broken source" in str(call.args[0]) for call in self.log.call_args_list))
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(set(app.get_profile_cache().entries(ids)), {ids[1]})
        self.assertFalse(app.run_queued_profile_refresh())

    def test_cancel_clears_the_queue_and_a_settings_change_retries_the_profile(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        app.queue_profile_refreshes(ids)
        with patch.object(app, "refresh_profile_cache", side_effect=RuntimeError("configuration changed")):
            app.CONFIGURATION_RELOAD_EVENT.set()
            try:
                self.assertTrue(app.run_queued_profile_refresh())
            finally:
                app.CONFIGURATION_RELOAD_EVENT.clear()
        self.assertEqual(app.queued_profile_refreshes(), tuple(ids))
        with patch.object(app, "refresh_profile_cache", side_effect=DownloadCancelledError("cancelled")):
            self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(app.queued_profile_refreshes(), ())

    def check(self, identifier):
        app.queue_profile_refreshes([identifier], check_only=True)
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(app.queued_profile_refreshes(), ())
        return app.profile_check_summary()[1]

    def test_check_downloads_only_when_the_provider_lists_a_newer_picture(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_west",))
        identifier = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        before = app.capture_loaded_configuration()
        # Nothing cached yet: the latest picture is new.
        self.assertEqual(self.check(identifier),
                         "Checked 1 profile(s): 1 new picture(s), 0 up to date.")
        self.assertEqual(self.client.fetch_image.call_count, 1)
        self.assertEqual(app.capture_loaded_configuration(), before)
        # The same picture again: only the provider's listing is read.
        listings = self.client.latest.call_count
        self.assertEqual(self.check(identifier),
                         "Checked 1 profile(s): 0 new picture(s), 1 up to date.")
        self.assertEqual(self.client.fetch_image.call_count, 1)
        self.assertGreater(self.client.latest.call_count, listings)
        # A newer acquisition is downloaded.
        self.client.latest.return_value = dict(
            self.frame, timestamp="2026-09-11T12:10:00Z",
            url=self.frame["url"].replace("20262541200", "20262541210"))
        self.assertEqual(self.check(identifier),
                         "Checked 1 profile(s): 1 new picture(s), 0 up to date.")
        self.assertEqual(self.client.fetch_image.call_count, 2)
        # A provider listing an older picture never replaces the newer cached one.
        self.client.latest.return_value = dict(self.frame)
        self.assertEqual(self.check(identifier),
                         "Checked 1 profile(s): 0 new picture(s), 1 up to date.")
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertTrue(any("lists an older picture" in str(call.args[0])
                            for call in self.log.call_args_list))
        self.assertIsNone(app.checking_profile_id())

    def test_check_skips_profiles_with_image_updates_off(self):
        library = self.library(("goes_west", "goes_east"))
        library["items"][0]["settings"]["source"]["check_for_updates"] = False
        app.IMAGE_PROFILE_LIBRARY = normalize_library(library)
        ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        serial = app.profile_check_summary()[0]
        app.queue_profile_refreshes(ids, check_only=True)
        self.assertTrue(app.run_queued_profile_refresh())
        # No summary while a check is still queued.
        self.assertEqual(app.profile_check_summary()[0], serial)
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(app.profile_check_summary(), (serial + 1,
                         "Checked 2 profile(s): 1 new picture(s), 0 up to date, "
                         "1 skipped (image updates off)."))
        self.assertEqual(self.client.fetch_image.call_count, 1)

    def test_forced_download_replaces_a_queued_check_and_failures_are_counted(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        first, second = (item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"])
        app.queue_profile_refreshes([first, second], check_only=True)
        self.assertEqual(app.queue_profile_refreshes([first]), [])
        self.assertFalse(app.profile_refresh_is_check(first))
        self.assertTrue(app.profile_refresh_is_check(second))
        with patch.object(app, "refresh_profile_cache", return_value=("new", None)) as refresh:
            self.assertTrue(app.run_queued_profile_refresh())
        self.assertFalse(refresh.call_args.kwargs["check_only"])
        with patch.object(app, "refresh_profile_cache", side_effect=ValueError("broken source")):
            self.assertTrue(app.run_queued_profile_refresh())
        # The forced profile is not part of the check result.
        self.assertEqual(app.profile_check_summary()[1],
                         "Checked 1 profile(s): 0 new picture(s), 0 up to date, 1 failed.")

    def test_cancelled_check_reports_its_partial_result(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        app.queue_profile_refreshes(ids, check_only=True)
        with patch.object(app, "refresh_profile_cache", return_value=("unchanged", None)):
            self.assertTrue(app.run_queued_profile_refresh())
        with patch.object(app, "refresh_profile_cache", side_effect=DownloadCancelledError("cancelled")):
            self.assertTrue(app.run_queued_profile_refresh())
        text = "Check cancelled after 1 profile(s): 0 new picture(s), 1 up to date."
        self.assertEqual(app.profile_check_summary()[1], text)

    def test_check_now_runs_the_regular_check_before_queued_checks(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_west",))
        identifier = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        app.queue_profile_refreshes([identifier], check_only=True)
        app.CHECK_NOW_EVENT.set()
        self.addCleanup(app.CHECK_NOW_EVENT.clear)
        seen = []

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        self.run_cycles(1)
        self.assertEqual(seen, ["goes_east"])
        self.assertFalse(app.CHECK_NOW_EVENT.is_set())
        self.assertEqual(app.queued_profile_refreshes(), (identifier,))

    def test_waiting_worker_wakes_for_check_now(self):
        app.UPDATE_INTERVAL_MINUTES = 60
        app.CHECK_NOW_EVENT.set()
        self.addCleanup(app.CHECK_NOW_EVENT.clear)
        with patch.object(app.APPLICATION_STOP_EVENT, "wait", side_effect=AssertionError("Worker kept waiting")):
            self.assertTrue(app.sleep_until_next_cycle(app.time.monotonic()))

    def test_status_column_shows_checking(self):
        identifier = "1" * 32
        self.assertEqual(profile_status_text(identifier, {"queued": (identifier,), "checking": identifier}),
                         "CHECK")
        # A download that follows the check shows its progress instead.
        self.assertEqual(profile_status_text(identifier, {"checking": identifier, "download": {
            "profile_id": identifier, "percent": 20.0}}), "20%")

    def test_waiting_worker_wakes_for_a_queued_profile(self):
        app.UPDATE_INTERVAL_MINUTES = 60
        app.queue_profile_refreshes(["1" * 32])
        with patch.object(app.APPLICATION_STOP_EVENT, "wait", side_effect=AssertionError("Worker kept waiting")):
            self.assertTrue(app.sleep_until_next_cycle(app.time.monotonic(), wake_for_queue=True))

    def test_failed_background_download_and_check_mark_the_profile_until_success(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        first, second = (item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"])
        app.queue_profile_refreshes([first, second])
        lost = app.NOAASelectionLost("The selected NOAA area is no longer available: storm_X")
        with patch.object(app, "refresh_profile_cache", side_effect=lost):
            self.assertTrue(app.run_queued_profile_refresh())
        with patch.object(app, "refresh_profile_cache", side_effect=OSError("offline")):
            self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(app.profile_failures(), {first: "LOST", second: "UNAVAIL"})
        # A check that finds the picture current is a success; a skipped one says nothing.
        app.queue_profile_refreshes([first, second], check_only=True)
        with patch.object(app, "refresh_profile_cache", return_value=("unchanged", None)):
            self.assertTrue(app.run_queued_profile_refresh())
        with patch.object(app, "refresh_profile_cache", return_value=("skipped", None)):
            self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(app.profile_failures(), {second: "UNAVAIL"})

    def test_failed_rotation_preload_marks_the_profile_and_a_superseded_one_does_not_clear(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        profile = app.IMAGE_PROFILE_LIBRARY["items"][0]
        lost = app.NOAASelectionLost("no longer listed")
        with patch.object(app, "refresh_profile_cache", side_effect=lost):
            app.run_rotation_preload(profile)
        self.assertEqual(app.profile_failures(), {profile["id"]: "LOST"})
        with patch.object(app, "refresh_profile_cache", side_effect=app.UpdateSuperseded("newer settings")):
            app.run_rotation_preload(profile)
        self.assertEqual(app.profile_failures(), {profile["id"]: "LOST"})
        with patch.object(app, "refresh_profile_cache", return_value=("new", None)):
            app.run_rotation_preload(profile)
        self.assertEqual(app.profile_failures(), {})

    def test_status_column_shows_lost_and_unavail_with_active(self):
        identifier = "1" * 32
        runtime = {"failures": {identifier: "LOST"}}
        self.assertEqual(profile_status_text(identifier, runtime), "LOST")
        self.assertEqual(profile_status_text(identifier, dict(runtime, active_profile_id=identifier)),
                         "ACTIVE · LOST")
        self.assertEqual(profile_status_text(identifier, {"failures": {identifier: "UNAVAIL"},
                                                          "active_profile_id": identifier}),
                         "ACTIVE · UNAVAIL")
        # Running work shows first; another profile's failure is not this one's.
        self.assertEqual(profile_status_text(identifier, dict(runtime, queued=(identifier,))), "QUEUE")
        self.assertEqual(profile_status_text("2" * 32, runtime), "")

    def test_status_column_shows_queued_until_the_download_starts(self):
        identifier = "1" * 32
        self.assertEqual(profile_status_text(identifier, {"queued": (identifier,)}), "QUEUE")
        downloading = profile_status_text(identifier, {"queued": (identifier,), "download": {
            "profile_id": identifier, "percent": 50.0}})
        self.assertEqual(downloading, "50%")
        self.assertEqual(profile_status_text(identifier, {"active_profile_id": identifier}), "ACTIVE")


if __name__ == "__main__":
    unittest.main()
