"""Exercise rotation through the real update loop; network and Windows are mocked."""

from copy import deepcopy
import os
import tomllib
import unittest
import urllib.error
from unittest.mock import patch

import marblescape_download as app
from marblescape_profiles import normalize_library, RotationScheduler
import test_source_runtime as source_tests


class RotationRuntimeTests(unittest.TestCase):
    setUp = source_tests.SourceRuntimeTests.setUp
    tearDown = source_tests.SourceRuntimeTests.tearDown
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    assert_installed_png = source_tests.SourceRuntimeTests.assert_installed_png

    def library(self, providers=("goes_east", "goes_west")):
        snapshot = app.image_settings_snapshot()
        items = []
        for index, provider in enumerate(providers, 1):
            settings = deepcopy(snapshot)
            settings["source"]["provider"] = provider
            items.append({"id": f"{index:032x}", "name": provider, "settings": settings})
        return normalize_library({"items": items, "rotation": {
            "enabled": True, "interval": 1, "unit": "minutes",
            "order": [item["id"] for item in items]}})

    def run_cycles(self, cycles, advance=True):
        app.RUN_CONTINUOUSLY = True
        clock = [100.0]
        calls = []
        def sleep(*args, **kwargs):
            calls.append(app.NEXT_ROTATION_DEADLINE)
            if len(calls) >= cycles:
                return False
            if advance:
                clock[0] = max(clock[0], app.NEXT_ROTATION_DEADLINE or clock[0])
            return True
        with patch.object(app, "RotationScheduler", side_effect=lambda value: RotationScheduler(value, clock=lambda: clock[0])), \
             patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(app, "sleep_until_next_cycle", side_effect=sleep):
            app.main([], configuration_loaded=True)
        return calls, clock[0]

    def test_rotation_installs_each_profile_and_wraps_without_rewriting_config(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        app.SET_WINDOWS_WALLPAPER = True
        app.WINDOWS_WALLPAPER_POSITION = "tile"
        app.ACTIVE_CONFIG_PATH.write_text("unchanged", encoding="utf-8")
        seen = []
        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            return self.make_png(frame, size, **options)
        self.client.fetch_image.side_effect = fetch
        deadlines, _ = self.run_cycles(3)
        self.assertEqual(seen, ["goes_east", "goes_west"])
        self.assertEqual(deadlines, [160, 220, 280])
        self.assertEqual(app.WINDOWS_WALLPAPER_POSITION, "tile")
        self.assertEqual(app.ACTIVE_CONFIG_PATH.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual(self.wallpaper.call_count, 3)
        self.assertTrue(app.get_current_image_path().is_file())
        self.assertTrue(app.get_current_image_path().is_relative_to(app.get_profile_cache().images_dir))
        active_id = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        self.assertEqual(app.ROTATION_STATUS["active_profile_id"], active_id)
        self.assertEqual(
            app.get_profile_cache().entries([active_id])[active_id]["source_time"],
            self.frame["timestamp"],
        )

    def test_profile_cache_survives_runtime_restart_and_force_bypasses_it(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_east",))
        self.run_cycles(1)
        self.assertEqual(self.client.fetch_image.call_count, 1)
        cached_path = app.get_current_image_path()

        app.set_current_image_path(None)
        self.run_cycles(1)
        self.assertEqual(self.client.fetch_image.call_count, 1)
        self.assertEqual(app.get_current_image_path(), cached_path)

        app.FORCE_UPDATE_EVENT.set()
        self.run_cycles(1)
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertEqual(app.get_current_image_path(), cached_path)

    def test_exactly_three_failures_then_next_profile_succeeds(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        seen = []
        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            if app.IMAGE_SOURCE == "goes_east":
                raise RuntimeError("temporary outage")
            return self.make_png(frame, size, **options)
        self.client.fetch_image.side_effect = fetch
        self.run_cycles(4)
        self.assertEqual(seen, ["goes_east"] * 3 + ["goes_west"])
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")
        self.assertIn("Active profile: goes_west", app.ROTATION_STATUS["text"])
        self.assertTrue(app.get_current_image_path().is_file())
        self.assertTrue(app.get_current_image_path().is_relative_to(app.get_profile_cache().images_dir))

    def test_exhausted_download_retries_skip_profile_without_multiplying_attempts(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        app.DOWNLOAD_RETRIES = 1
        seen = []

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            if app.IMAGE_SOURCE == "goes_east":
                try:
                    raise urllib.error.URLError("temporary outage")
                except urllib.error.URLError as exc:
                    raise RuntimeError("NOAA unavailable") from exc
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        with patch.object(app, "wait_before_download_retry"):
            self.run_cycles(2)
        self.assertEqual(seen, ["goes_east", "goes_east", "goes_west"])
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")

    def test_two_failed_attempts_then_success_do_not_skip_profile(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        self.client.fetch_image.side_effect = [RuntimeError("one"), RuntimeError("two"), self.make_png({}, (32, 18))]
        deadlines, _ = self.run_cycles(3)
        self.assertEqual(self.client.fetch_image.call_count, 3)
        self.assertEqual(app.IMAGE_SOURCE, "goes_east")
        self.assertEqual(deadlines, [105, 110, 170])

    def test_cancelled_profile_waits_full_interval_without_using_a_failure(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("solar",))
        seen = []

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            if len(seen) == 1:
                self.assertTrue(app.DOWNLOAD_PROGRESS.request_cancel())
                app.DOWNLOAD_PROGRESS.raise_if_cancelled()
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        with patch.object(
            RotationScheduler,
            "failure",
            side_effect=AssertionError("Cancellation must not count as a failure."),
        ):
            deadlines, _ = self.run_cycles(2)

        self.assertEqual(seen, ["solar", "solar"])
        self.assertEqual(deadlines, [160, 220])
        self.assertIn("Active profile: solar", app.ROTATION_STATUS["text"])

    def test_failed_wms_catalogue_is_counted_and_does_not_block_noaa_rotation(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("eumetsat", "solar"))
        self.run_cycles(4)
        self.assertEqual(self.wms.call_count, 3)
        self.client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_SOURCE, "solar")

    def test_all_failed_profiles_wait_after_one_pass_and_keep_existing_image(self):
        app.main(["--once"], configuration_loaded=True)
        original = self.assert_installed_png().read_bytes()
        app.IMAGE_PROFILE_LIBRARY = self.library()
        self.client.fetch_image.reset_mock()
        self.client.fetch_image.side_effect = RuntimeError("offline")
        deadlines, now = self.run_cycles(6)
        self.assertEqual(self.client.fetch_image.call_count, 6)
        self.assertEqual(deadlines[-1], now + 60)
        self.assertIn("All profiles failed", app.ROTATION_STATUS["text"])
        latest = self.assert_installed_png()
        self.assertEqual(latest.read_bytes(), original)
        self.assertEqual(app.get_current_image_path(), latest)
        self.assertIsNone(app.get_active_profile_cache_id())

    def test_once_ignores_enabled_rotation(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("solar",))
        app.main(["--once"], configuration_loaded=True)
        self.assertEqual(app.IMAGE_SOURCE, "goes_east")
        self.client.fetch_image.assert_called_once()

    def test_reload_between_attempts_preserves_new_general_and_history_settings(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        self.client.fetch_image.side_effect = RuntimeError("offline")
        def load(_path):
            app.UPDATE_INTERVAL_MINUTES = 99
            app.HISTORY_MAX_FILES = 17
        real_failure = RotationScheduler.failure
        def failure(scheduler):
            result = real_failure(scheduler)
            if scheduler.attempts == 1:
                # Save during the retry delay so the first error still counts.
                app.CONFIGURATION_RELOAD_EVENT.set()
            return result
        with patch.object(app, "load_configuration", side_effect=load), \
             patch.object(RotationScheduler, "failure", failure):
            self.run_cycles(3)
        self.assertEqual(self.client.fetch_image.call_count, 3)
        self.assertEqual(app.UPDATE_INTERVAL_MINUTES, 99)
        self.assertEqual(app.HISTORY_MAX_FILES, 17)

    def test_profile_snapshot_restores_all_image_fields_without_general_settings(self):
        snapshot = app.image_settings_snapshot()
        self.assertNotIn("windows", snapshot)
        self.assertNotIn("history", snapshot)
        changed = deepcopy(snapshot)
        changed["source"]["provider"] = "solar"
        changed["output"].update(width=64, height=36, render_scale=2.0, background_color="#123456")
        changed["view"].update(fit_mode="crop", zoom=1.5)
        app.apply_image_settings(changed)
        self.assertEqual(app.get_output_dimensions(), (64, 36))
        self.assertEqual(app.VIEW_MODE, "crop")
        self.assertEqual(app.BACKGROUND_COLOR, "#123456")
        self.assertEqual(app.RENDER_SCALE, 2.0)
        self.assertFalse(app.SET_WINDOWS_WALLPAPER)
        app.apply_image_settings(snapshot)
        self.assertEqual(app.image_settings_snapshot(), snapshot)

    def test_invalid_profile_is_atomic(self):
        before = app.capture_loaded_configuration()
        invalid = app.image_settings_snapshot()
        invalid["source"]["provider"] = "solar"
        invalid["view"]["fit_mode"] = "invalid"
        with self.assertRaises(ValueError):
            app.apply_image_settings(invalid)
        self.assertEqual(app.capture_loaded_configuration(), before)

    def test_explicit_dimensions_without_ratio_roundtrip(self):
        app.ASPECT_RATIO = None
        snapshot = app.image_settings_snapshot()
        app.ASPECT_RATIO = "1:1"
        app.apply_image_settings(snapshot)
        self.assertIsNone(app.ASPECT_RATIO)
        self.assertEqual(app.get_output_dimensions(), (32, 18))

    def test_saved_profile_layers_roundtrip_and_existing_layer_editor_still_works(self):
        original = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        snapshot = app.image_settings_snapshot()
        snapshot["source"]["provider"] = "eumetsat"
        snapshot["view"]["bbox"] = [-1.0, -2.0, 3.0, 4.0]
        snapshot["layers"][0]["enabled"] = False
        updated = app.replace_image_settings(original, snapshot)
        parsed = tomllib.loads(updated)
        before = tomllib.loads(original)
        self.assertEqual(parsed["layers"], snapshot["layers"])
        self.assertEqual(parsed["view"]["bbox"], snapshot["view"]["bbox"])
        for key in ("service", "windows", "history"):
            self.assertEqual(parsed[key], before[key])
        self.assertNotIn("image_profiles", parsed)
        edited = app.replace_primary_wms_layer_name(updated, "mtg_fd:rgb_truecolour")
        self.assertTrue(any(layer["name"] == "mtg_fd:rgb_truecolour" for layer in tomllib.loads(edited)["layers"]))
        again = app.replace_image_settings(updated, snapshot)
        self.assertEqual(tomllib.loads(again), parsed)

    def test_backup_contains_profile_library_and_rotation(self):
        library = self.library()
        text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        app.ACTIVE_CONFIG_PATH.write_text(text, encoding="utf-8")
        app.write_profile_library_file_unlocked(library)
        with patch.object(app, "is_windows_startup_enabled", return_value=False):
            payload = app.create_settings_backup_payload()
        restored, restored_library, _ = app.parse_settings_backup_payload(payload)
        self.assertNotIn("image_profiles", tomllib.loads(restored))
        self.assertEqual(restored_library, library)

    def test_combined_save_rolls_profile_file_back_if_config_replace_fails(self):
        old_library = self.library(("goes_east",))
        new_library = self.library(("goes_west",))
        app.ACTIVE_CONFIG_PATH.write_text(
            app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        app.write_profile_library_file_unlocked(old_library)
        real_replace = os.replace

        def replace(source, destination):
            if destination == app.ACTIVE_CONFIG_PATH.resolve():
                raise OSError("simulated config replacement failure")
            return real_replace(source, destination)

        with patch.object(app.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                app.update_active_configuration_and_profiles(
                    lambda text: text + "\n# changed\n", new_library
                )

        self.assertEqual(app.read_profile_library_file(), old_library)

    def test_profile_metadata_cannot_overwrite_shared_output_roots(self):
        text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        snapshot = app.image_settings_snapshot()
        snapshot["output"]["windows_root"] = "another-installation"
        snapshot["output"]["linux_root"] = "another-location"
        parsed = tomllib.loads(app.replace_image_settings(text, snapshot))
        original = tomllib.loads(text)
        self.assertEqual(parsed["output"]["windows_root"], original["output"]["windows_root"])
        self.assertEqual(parsed["output"]["linux_root"], original["output"]["linux_root"])


if __name__ == "__main__":
    unittest.main()
