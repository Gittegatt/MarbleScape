"""Exercise rotation through the real update loop; network and Windows are mocked."""

from copy import deepcopy
import os
from pathlib import Path
import tomllib
import unittest
import urllib.error
from unittest.mock import patch

import marblescape_download as app
from marblescape_profiles import normalize_library, RotationScheduler
import test_source_runtime as source_tests


class RotationRuntimeTests(unittest.TestCase):
    tearDown = source_tests.SourceRuntimeTests.tearDown

    def setUp(self):
        source_tests.SourceRuntimeTests.setUp(self)
        # These tests time the rotation itself; RotationPreloadTests cover preloading.
        self.stack.enter_context(patch.object(app, "ROTATION_PRELOAD_LEAD_SECONDS", 0.0))
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

    def test_a_removed_latest_copy_of_a_rotation_profile_returns_without_a_download(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        app.RUN_CONTINUOUSLY = True
        clock, calls, removed = [100.0], [], []

        def sleep(*_args, **_kwargs):
            calls.append(app.NEXT_ROTATION_DEADLINE)
            # Latest emptied by hand once while the rotation profile is shown; no step is due.
            if len(calls) == 1:
                removed.extend(path.name for path in app.get_latest_image_files())
                for path in app.get_latest_image_files():
                    path.unlink()
            return len(calls) < 3

        with patch.object(app, "RotationScheduler",
                          side_effect=lambda value: RotationScheduler(value, clock=lambda: clock[0])), \
             patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(app, "sleep_until_next_cycle", side_effect=sleep):
            app.main([], configuration_loaded=True)
        # Its readable copy returns from the cache, without a second download, and
        # its row stays the active one.
        self.assertTrue(removed)
        self.assertEqual([path.name for path in app.get_latest_image_files()], removed)
        self.assertEqual(self.client.fetch_image.call_count, 1)
        self.assertTrue(app.get_current_image_path().is_relative_to(app.get_profile_cache().images_dir))
        self.assertEqual(app.shown_profile_row_id(), app.IMAGE_PROFILE_LIBRARY["items"][0]["id"])

    def test_switching_rotation_off_reports_disabled_instead_of_waiting(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        configuration = app.capture_loaded_configuration()
        disabled = deepcopy(app.IMAGE_PROFILE_LIBRARY)
        disabled["rotation"]["enabled"] = False

        def load(_path):
            app.restore_loaded_configuration(configuration)
            app.IMAGE_PROFILE_LIBRARY = deepcopy(disabled)

        # Profiles saves without touching the image: a settings-only reload.
        app.SETTINGS_ONLY_RELOAD_EVENT.set()
        app.CONFIGURATION_RELOAD_EVENT.set()
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(1)
        self.assertEqual(app.ROTATION_STATUS["text"], "Rotation is disabled.")
        self.assertIsNone(app.ROTATION_STATUS["deadline"])

        # Switching it back on still says it waits for the interval.
        enabled = deepcopy(disabled)
        enabled["rotation"]["enabled"] = True
        disabled = enabled
        app.SETTINGS_ONLY_RELOAD_EVENT.set()
        app.CONFIGURATION_RELOAD_EVENT.set()
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(1)
        self.assertEqual(app.ROTATION_STATUS["text"], "Waiting for the next rotation interval.")

    def test_apply_image_after_save_loads_the_picture_save_postponed(self):
        app.IMAGE_PROFILE_LIBRARY = normalize_library({"items": [], "rotation": {"enabled": False}})
        saved = app.capture_loaded_configuration()
        saved["ZOOM"] = 1.5
        steps = []

        def load(_path):
            app.restore_loaded_configuration(saved)

        def sleep(*args, **kwargs):
            steps.append(self.client.fetch_image.call_count)
            if len(steps) == 1:
                # Save: new Image settings, no picture now.
                app.SETTINGS_ONLY_RELOAD_EVENT.set()
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            if len(steps) == 2:
                # Apply Image without further changes: the saved settings are not on screen yet.
                app.SETTINGS_ONLY_RELOAD_EVENT.clear()
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False

        app.RUN_CONTINUOUSLY = True
        with patch.object(app, "load_configuration", side_effect=load), \
             patch.object(app.time, "monotonic", return_value=100.0), \
             patch.object(app, "sleep_until_next_cycle", side_effect=sleep):
            app.main([], configuration_loaded=True)
        # The first picture, none after Save, the saved settings' picture after Apply Image.
        self.assertEqual(steps, [1, 1, 2])
        self.assertEqual(app.ZOOM, 1.5)

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

    def test_a_profile_without_image_data_is_skipped_at_once(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        seen = []

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            if app.IMAGE_SOURCE == "goes_east":
                raise app.NoImageData(app.NO_IMAGE_DATA_TEXT)
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        self.run_cycles(2)
        # One attempt: asking again gives the same empty picture.
        self.assertEqual(seen, ["goes_east", "goes_west"])
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")

    def test_lost_selection_skips_the_step_at_once_and_is_tried_again_next_round(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        east = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        seen, failures = [], []

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            failures.append(app.profile_failures())
            if seen == ["goes_east"]:
                # As NOAA reports an ended storm, wrapped by the update.
                try:
                    raise app.NOAASelectionLost("The selected NOAA area is no longer available: storm_X")
                except app.NOAASelectionLost as exc:
                    raise RuntimeError("Update failed") from exc
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        self.run_cycles(3)
        # One attempt, the next profile, then the lost one again: never skipped for good.
        self.assertEqual(seen, ["goes_east", "goes_west", "goes_east"])
        self.assertEqual(failures[1:], [{east: "LOST"}, {east: "LOST"}])
        # Its success clears the state.
        self.assertEqual(app.profile_failures(), {})

    def test_unavailable_profile_shows_unavail_until_its_next_success(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        east = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        failures = []

        def fetch(frame, size, **options):
            failures.append(app.profile_failures())
            if app.IMAGE_SOURCE == "goes_east":
                raise RuntimeError("temporary outage")
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        self.run_cycles(4)
        self.assertEqual(failures[-1], {east: "UNAVAIL"})
        self.assertEqual(app.profile_failures(), {east: "UNAVAIL"})

    def test_failure_state_names_lost_selections_of_every_catalogue_source(self):
        import marblescape_himawari, marblescape_noaa, marblescape_slider, marblescape_worldview
        for module in (marblescape_noaa, marblescape_himawari, marblescape_slider, marblescape_worldview):
            with self.subTest(source=module.__name__):
                lost = module.SelectionLostError("no longer listed")
                self.assertIsInstance(lost, module.UnavailableError)
                self.assertEqual(app.profile_failure_state(lost), "LOST")
                try:
                    try:
                        raise lost
                    except module.SelectionLostError as exc:
                        raise RuntimeError("Update failed") from exc
                except RuntimeError as wrapped:
                    self.assertEqual(app.profile_failure_state(wrapped), "LOST")
                self.assertEqual(app.profile_failure_state(module.UnavailableError("offline")), "UNAVAIL")
        for other in (RuntimeError("x"), app.NoImageData(app.NO_IMAGE_DATA_TEXT),
                      app.DownloadRetriesExhausted("x")):
            self.assertEqual(app.profile_failure_state(other), "UNAVAIL")

    def test_failure_state_tells_the_source_from_this_computers_network(self):
        import socket
        import urllib.error
        import marblescape_himawari

        def wrapped(cause):
            try:
                try:
                    raise cause
                except Exception as exc:
                    raise marblescape_himawari.UnavailableError("Himawari is unreachable") from exc
            except marblescape_himawari.UnavailableError as error:
                return error

        http = urllib.error.HTTPError("https://example.org", 503, "Unavailable", {}, None)
        self.addCleanup(http.close)
        self.assertEqual(app.profile_failure_state(wrapped(http)), "SOURCE")
        dns = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        timeout = urllib.error.URLError(TimeoutError("timed out"))
        for online, failure, expected in ((True, dns, "SOURCE"), (False, dns, "NETWORK"),
                                          (True, timeout, "SOURCE"), (False, timeout, "NETWORK"),
                                          (None, dns, "NETWORK"), (None, timeout, "SOURCE")):
            with self.subTest(online=online, failure=failure), \
                    patch.object(app, "internet_connected", return_value=online):
                self.assertEqual(app.profile_failure_state(wrapped(failure)), expected)
                exhausted = app.DownloadRetriesExhausted("Image download failed after 3 attempts")
                exhausted.__cause__ = wrapped(failure)
                self.assertEqual(app.profile_failure_state(exhausted), expected)
        self.assertEqual(app.profile_failure_state(app.NoImageData(app.NO_IMAGE_DATA_TEXT)), "UNAVAIL")
        self.assertEqual(app.profile_failure_state(wrapped(marblescape_himawari.SelectionLostError("x"))),
                         "LOST")

    def test_without_network_the_rotation_keeps_its_profile(self):
        import urllib.error
        app.IMAGE_PROFILE_LIBRARY = self.library()
        east = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        seen = []

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            raise RuntimeError("Download failed") from urllib.error.URLError(TimeoutError("timed out"))

        self.client.fetch_image.side_effect = fetch
        with patch.object(app, "internet_connected", return_value=False),              patch.object(app, "wait_before_download_retry"):
            self.run_cycles(3)
        # No switch to the next profile: every profile would fail the same way.
        self.assertEqual(set(seen), {"goes_east"})
        self.assertEqual(app.profile_failures(), {east: "NETWORK"})

    def test_each_update_notes_how_it_went(self):
        import urllib.error
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_east",))

        def kind():
            outcome = app.image_outcome()
            return outcome["kind"], outcome["profile"]

        self.run_cycles(1)
        self.assertEqual(kind(), ("downloaded", "goes_east"))
        # After a restart the cached picture is shown without a download.
        app.set_current_image_path(None)
        self.run_cycles(1)
        self.assertEqual(self.client.fetch_image.call_count, 1)
        self.assertEqual(kind(), ("cache", "goes_east"))
        # The same picture stays: already up to date.
        self.run_cycles(1)
        self.assertEqual(kind(), ("current", "goes_east"))
        # A failure names its status; a cancelled download says so.
        self.client.latest.return_value = dict(self.frame, timestamp="2026-09-11T12:10:00Z",
                                               url=self.frame["url"].replace("20262541200", "20262541210"))
        self.client.fetch_image.side_effect = urllib.error.URLError(TimeoutError("timed out"))
        with patch.object(app, "internet_connected", return_value=False), \
             patch.object(app, "wait_before_download_retry"):
            self.run_cycles(1)
        self.assertEqual(kind(), ("NETWORK", "goes_east"))

        def cancel(frame, size, **options):
            app.DOWNLOAD_PROGRESS.request_cancel()
            app.DOWNLOAD_PROGRESS.raise_if_cancelled()

        self.client.fetch_image.side_effect = cancel
        self.run_cycles(1)
        self.assertEqual(kind(), ("cancelled", "goes_east"))

    def test_failure_state_is_kept_only_for_saved_profiles(self):
        import marblescape_snapshot
        identifier = "1" * 32
        app.note_profile_outcome(identifier, RuntimeError("offline"))
        app.note_profile_outcome(marblescape_snapshot.CACHE_ID, RuntimeError("offline"))
        app.note_profile_outcome(None, RuntimeError("offline"))
        self.assertEqual(app.profile_failures(), {identifier: "UNAVAIL"})
        app.note_profile_outcome(identifier)
        self.assertEqual(app.profile_failures(), {})

    def test_applied_profile_shows_lost_only_while_its_settings_are_loaded(self):
        library = self.library(("goes_east",))
        library["rotation"]["enabled"] = False
        app.IMAGE_PROFILE_LIBRARY = normalize_library(library)
        identifier = app.IMAGE_PROFILE_LIBRARY["items"][0]["id"]
        app.APPLIED_PROFILE_ID = identifier
        self.client.fetch_image.side_effect = app.NOAASelectionLost("no longer listed")
        self.run_cycles(1)
        self.assertEqual(app.profile_failures(), {identifier: "LOST"})
        self.client.fetch_image.side_effect = self.make_png
        self.run_cycles(1)
        self.assertEqual(app.profile_failures(), {})
        # Changed settings are no longer this profile: their failure is not its state.
        app.ZOOM = 1.7
        self.client.fetch_image.side_effect = RuntimeError("offline")
        self.run_cycles(1)
        self.assertEqual(app.profile_failures(), {})

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

    def test_settings_only_reload_during_apply_image_download_still_loads_the_image(self):
        """Update profile while an applied image downloads must not postpone that image."""
        file_settings = app.image_settings_snapshot()
        seen = []

        def load(_path):
            app.apply_image_settings(file_settings)

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            if seen == ["goes_east", "goes_west"]:
                # Profiles saves while the applied image downloads.
                app.SETTINGS_ONLY_RELOAD_EVENT.set()
                app.CONFIGURATION_RELOAD_EVENT.set()
            return self.make_png(frame, size, **options)

        def sleep(*_args, **_kwargs):
            if len(seen) == 1 and not app.CONFIGURATION_RELOAD_EVENT.is_set():
                # Apply Image saves another source.
                file_settings["source"]["provider"] = "goes_west"
                app.SETTINGS_ONLY_RELOAD_EVENT.clear()
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return len(seen) < 3 and app.CONFIGURATION_RELOAD_EVENT.is_set()

        self.client.fetch_image.side_effect = fetch
        app.RUN_CONTINUOUSLY = True
        clock = [100.0]
        with patch.object(app, "load_configuration", side_effect=load), \
             patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(app, "sleep_until_next_cycle", side_effect=sleep):
            app.main([], configuration_loaded=True)
        # The interrupted download kept its picture, which is shown at once,
        # not after the update interval and without loading it again.
        self.assertEqual(seen, ["goes_east", "goes_west"])
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")
        latest = app.get_latest_image_files()[0]
        self.assertEqual(app.get_current_image_path(), latest.resolve())
        from PIL import Image
        import json
        with Image.open(latest) as image:
            self.assertEqual(json.loads(image.text["MarbleScape"])["source"], "GOES-West")

    def test_profile_snapshot_restores_all_image_fields_without_general_settings(self):
        snapshot = app.image_settings_snapshot()
        self.assertNotIn("windows", snapshot)
        self.assertNotIn("history", snapshot)
        changed = deepcopy(snapshot)
        changed["source"]["provider"] = "solar"
        # An older profile's output size, background and Latest folder are
        # ignored: they are device settings.
        changed["output"].update(width=64, height=36, aspect_ratio="", render_scale=2.0,
                                 background_color="#123456", latest_folder="elsewhere")
        changed["view"].update(fit_mode="crop", zoom=1.5)
        device = (app.get_output_dimensions(), app.ASPECT_RATIO, app.BACKGROUND_COLOR,
                  app.CUSTOM_LATEST_FOLDER, app.LATEST_DIR)
        app.apply_image_settings(changed)
        self.assertEqual((app.get_output_dimensions(), app.ASPECT_RATIO, app.BACKGROUND_COLOR,
                          app.CUSTOM_LATEST_FOLDER, app.LATEST_DIR), device)
        self.assertEqual(app.VIEW_MODE, "crop")
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

    def test_eumetsat_settings_without_a_visible_layer_are_never_written(self):
        original = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        snapshot = app.image_settings_snapshot()
        snapshot["source"]["provider"] = "eumetsat"
        hidden = [dict(layer, enabled=False) for layer in snapshot["layers"]]
        transparent = [dict(layer, opacity=0.0) for layer in snapshot["layers"]]
        for layers in ([], hidden, transparent):
            snapshot["layers"] = layers
            with self.assertRaisesRegex(ValueError, "EUMETSAT needs at least one enabled layer"):
                app.replace_image_settings(original, snapshot)
        # Other sources keep writing an empty layer list, as applied profiles do.
        snapshot["source"]["provider"] = "goes_east"
        snapshot["layers"] = []
        self.assertEqual(tomllib.loads(app.replace_image_settings(original, snapshot))["layers"], [])

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


class LatestSnapshotCacheTests(unittest.TestCase):
    """The image without a profile keeps a profile-cache slot of its own."""

    tearDown = source_tests.SourceRuntimeTests.tearDown
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    library = RotationRuntimeTests.library
    run_cycles = RotationRuntimeTests.run_cycles

    def setUp(self):
        RotationRuntimeTests.setUp(self)
        library = self.library(("goes_west",))
        library["rotation"]["enabled"] = False
        app.IMAGE_PROFILE_LIBRARY = normalize_library(library)
        self.profile = app.IMAGE_PROFILE_LIBRARY["items"][0]
        self.no_profile = app.capture_loaded_configuration()
        self.seen = []

        def fetch(frame, size, **options):
            self.seen.append(app.IMAGE_SOURCE)
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch

    def reload(self, apply_profile):
        def load(_path):
            app.restore_loaded_configuration(self.no_profile)
            if apply_profile:
                app.apply_image_settings(self.profile["settings"])
                app.APPLIED_PROFILE_ID = self.profile["id"]

        app.CONFIGURATION_RELOAD_EVENT.set()
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(1)

    def test_returning_to_no_profile_reuses_its_cached_image(self):
        import marblescape_snapshot as system
        self.run_cycles(1)
        cache = app.get_profile_cache()
        no_profile_image = app.get_current_image_path().read_bytes()
        self.assertIn(system.CACHE_ID, cache.entries([system.CACHE_ID]))
        # Deleting profiles keeps the slot.
        app.synchronize_profile_image_cache()
        self.assertIn(system.CACHE_ID, cache.entries([system.CACHE_ID]))

        # The applied profile replaces the single Latest image.
        self.reload(apply_profile=True)
        self.assertEqual(self.seen, ["goes_east", "goes_west"])
        self.assertNotEqual(app.get_current_image_path().read_bytes(), no_profile_image)

        # Same settings and no newer source frame: no download.
        self.reload(apply_profile=False)
        self.assertEqual(self.seen, ["goes_east", "goes_west"])
        self.assertEqual(app.get_current_image_path().read_bytes(), no_profile_image)
        self.assertTrue(any("Reusing cached image of the latest snapshot" in " ".join(map(str, call.args))
                            for call in self.log.call_args_list))

    def test_snapshot_row_loads_in_the_background_without_touching_latest(self):
        import marblescape_snapshot as system
        app.ACTIVE_CONFIG_PATH.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        self.run_cycles(1)
        self.assertIsNotNone(app.read_latest_snapshot())
        self.assertEqual(app.shown_profile_row_id(), system.SYSTEM_ID)
        self.reload(apply_profile=True)
        self.assertEqual(app.shown_profile_row_id(), self.profile["id"])
        latest = app.get_latest_image_files()[0].read_bytes()
        cache = app.get_profile_cache()
        slot_before = cache.entries([system.CACHE_ID])[system.CACHE_ID]
        slot_image_before = Path(slot_before["path"]).read_bytes()

        # A check finds nothing newer and downloads nothing.
        app.queue_profile_refreshes([system.SYSTEM_ID], check_only=True)
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(self.seen, ["goes_east", "goes_west"])
        self.assertEqual(app.profile_check_summary()[1],
                         "Checked 1 profile(s): 0 new picture(s), 1 up to date.")

        # A newer frame: the check loads it with the snapshot's own settings and,
        # with History (no profile) on, archives the replaced one in "_no profile".
        app.ENABLE_HISTORY = True
        self.client.latest.return_value = dict(self.frame, timestamp="2026-09-11T12:10:00Z",
                                               url=self.frame["url"].replace("1200", "1210"))
        app.queue_profile_refreshes([system.SYSTEM_ID], check_only=True)
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(self.seen, ["goes_east", "goes_west", "goes_east"])
        slot = cache.entries([system.CACHE_ID])[system.CACHE_ID]
        self.assertEqual(slot["source_time"], "2026-09-11T12:10:00Z")
        self.assertNotEqual(slot["path"], slot_before["path"])
        self.assertEqual(app._history_profile_identity(slot["path"]), (None, None))
        archived = app.get_history_files(None)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), slot_image_before)
        self.assertEqual(app.get_history_files("profiles"), [])

        # Forced: always downloads. Latest, the wallpaper and the runtime stay.
        app.queue_profile_refreshes([system.SYSTEM_ID])
        self.assertTrue(app.run_queued_profile_refresh())
        self.assertEqual(self.seen, ["goes_east", "goes_west", "goes_east", "goes_east"])
        self.assertEqual(app.get_latest_image_files()[0].read_bytes(), latest)
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")
        self.assertEqual(app.APPLIED_PROFILE_ID, self.profile["id"])
        self.assertEqual(app.queued_profile_refreshes(), ())

        # Returning to the snapshot shows the preloaded picture without a download.
        self.reload(apply_profile=False)
        self.assertEqual(len(self.seen), 4)
        self.assertEqual(app.get_current_image_path().read_bytes(),
                         Path(cache.entries([system.CACHE_ID])[system.CACHE_ID]["path"]).read_bytes())

    def test_a_newer_source_frame_downloads_again(self):
        self.run_cycles(1)
        self.reload(apply_profile=True)
        self.client.latest.return_value = dict(self.frame, timestamp="2026-09-11T12:10:00Z",
                                               url=self.frame["url"].replace("1200", "1210"))
        self.reload(apply_profile=False)
        self.assertEqual(self.seen, ["goes_east", "goes_west", "goes_east"])

    def test_image_updates_off_reuses_it_by_settings_alone(self):
        self.run_cycles(1)
        self.reload(apply_profile=True)
        no_profile = self.no_profile
        self.no_profile = dict(no_profile, CHECK_FOR_SOURCE_UPDATES=False)
        self.reload(apply_profile=False)
        self.assertEqual(self.seen, ["goes_east", "goes_west"])


class InstantProfileSwitchTests(unittest.TestCase):
    """A profile applied during a download shows its cached picture at once."""

    setUp = RotationRuntimeTests.setUp
    tearDown = source_tests.SourceRuntimeTests.tearDown
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    library = RotationRuntimeTests.library
    run_cycles = RotationRuntimeTests.run_cycles

    def test_cached_picture_with_the_same_settings_and_size_is_shown_at_once(self):
        app.SET_WINDOWS_WALLPAPER = True
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_west",))
        self.run_cycles(1)  # Rotation fills the profile cache.
        profile = app.IMAGE_PROFILE_LIBRARY["items"][0]
        cached = app.get_profile_cache().current(profile["id"])
        self.wallpaper.reset_mock()
        latest_before = [path.read_bytes() for path in app.get_latest_image_files()]

        changed = deepcopy(profile["settings"])
        changed["view"]["zoom"] = 2.5
        self.assertIsNone(app.show_cached_profile_now(profile["id"], changed))
        with patch.object(app, "get_output_dimensions", return_value=(64, 36)):
            self.assertIsNone(app.show_cached_profile_now(profile["id"], profile["settings"]))
        self.assertIsNone(app.show_cached_profile_now("f" * 32, profile["settings"]))
        self.assertEqual([path.read_bytes() for path in app.get_latest_image_files()], latest_before)
        self.wallpaper.assert_not_called()

        for path in app.get_latest_image_files():
            path.unlink()  # Another picture was shown meanwhile.
        published = app.show_cached_profile_now(profile["id"], profile["settings"])
        self.assertEqual(Path(published).read_bytes(), Path(cached).read_bytes())
        self.assertEqual(app.get_latest_image_files(), [published])
        self.assertEqual(app.get_current_image_path(), Path(published).resolve())
        if os.name == "nt":
            self.wallpaper.assert_called_once_with(published)

    def test_a_rotation_download_superseded_by_a_reload_is_kept_in_its_cache(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        configuration = app.capture_loaded_configuration()
        disabled = deepcopy(app.IMAGE_PROFILE_LIBRARY)
        disabled["rotation"]["enabled"] = False
        second = app.IMAGE_PROFILE_LIBRARY["items"][1]["id"]
        seen = []

        def load(_path):
            app.restore_loaded_configuration(configuration)
            app.IMAGE_PROFILE_LIBRARY = deepcopy(disabled)

        def fetch(frame, size, **options):
            seen.append(app.IMAGE_SOURCE)
            if app.IMAGE_SOURCE == "goes_west":
                # Rotation is switched off while goes_west downloads.
                app.SETTINGS_ONLY_RELOAD_EVENT.set()
                app.CONFIGURATION_RELOAD_EVENT.set()
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(3)
        self.assertEqual(seen, ["goes_east", "goes_west"])
        self.assertIsNotNone(app.get_profile_cache().current(second))
        # Not shown: rotation is off, and goes_east stays on screen.
        self.assertNotEqual(app.ROTATION_STATUS["active_profile_id"], second)
        self.assertNotEqual(app.get_current_image_path(), app.get_profile_cache().current(second))

    def test_latest_snapshot_row_uses_its_own_slot(self):
        import marblescape_snapshot as system
        app.ACTIVE_CONFIG_PATH.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        app.IMAGE_PROFILE_LIBRARY = normalize_library({})
        self.run_cycles(1)  # A download without a profile fills the slot.
        snapshot = app.read_latest_snapshot()
        slot = app.get_profile_cache().current(system.CACHE_ID)
        for path in app.get_latest_image_files():
            path.unlink()
        published = app.show_cached_profile_now(system.SYSTEM_ID, snapshot["settings"])
        self.assertEqual(Path(published).read_bytes(), Path(slot).read_bytes())


class AppliedSelectionOwnsReusedPictureTests(unittest.TestCase):
    """The applied selection decides a reused picture's identity: profile before Latest snapshot."""

    tearDown = source_tests.SourceRuntimeTests.tearDown
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    library = RotationRuntimeTests.library
    run_cycles = RotationRuntimeTests.run_cycles
    def setUp(self):
        RotationRuntimeTests.setUp(self)
        # The profile is applied, but its saved settings differ (zoom): the
        # picture has no profile. Updating the profile to the loaded settings
        # later makes both share one cache key.
        library = self.library(("goes_east",))
        library["rotation"]["enabled"] = False
        self.updated_library = normalize_library(library)
        self.profile = self.updated_library["items"][0]
        modified = deepcopy(self.updated_library)
        modified["items"][0]["settings"]["view"]["zoom"] = 1.7
        app.IMAGE_PROFILE_LIBRARY = normalize_library(modified)
        app.APPLIED_PROFILE_ID = self.profile["id"]
        self.no_profile = app.capture_loaded_configuration()
        self.seen = []

        def fetch(frame, size, **options):
            self.seen.append(app.IMAGE_SOURCE)
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch

    @staticmethod
    def pixels(path):
        from PIL import Image
        with Image.open(path) as picture:
            return picture.convert("RGB").tobytes()

    def reload(self, updated):
        def load(_path):
            app.restore_loaded_configuration(self.no_profile)
            if updated:
                app.IMAGE_PROFILE_LIBRARY = deepcopy(self.updated_library)

        app.CONFIGURATION_RELOAD_EVENT.set()
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(1)

    def legacy_key(self):
        """Cache keys as before: the applied profile even while its settings were modified."""
        real = app.image_cache_configuration_key

        def key(configuration):
            result = real(configuration)
            identifier = configuration.get("APPLIED_PROFILE_ID", "")
            result.update(image_profile_id=identifier, image_profile_name=app.image_profile_name(identifier))
            return result

        return patch.object(app, "image_cache_configuration_key", side_effect=key)

    def test_an_applied_profile_owns_an_equal_snapshot_picture_and_back(self):
        import marblescape_snapshot as system
        # A picture made before modified settings stopped counting as the profile.
        with self.legacy_key():
            self.run_cycles(1)
        self.assertIsNone(app.unmodified_applied_profile_id())
        self.assertEqual(app.shown_profile_row_id(), system.SYSTEM_ID)
        snapshot_pixels = self.pixels(app.get_current_image_path())
        app.ENABLE_HISTORY = True
        # Updated to the loaded settings, the applied profile reuses the picture
        # without a download, as its own.
        self.reload(updated=True)
        self.assertEqual(self.seen, ["goes_east"])
        self.assertEqual(app.unmodified_applied_profile_id(), self.profile["id"])
        self.assertEqual(app.shown_profile_row_id(), self.profile["id"])
        latest = app.get_latest_image_files()
        self.assertEqual(len(latest), 1)
        self.assertEqual(app._history_profile_identity(latest[0])[0], self.profile["id"])
        self.assertEqual(self.pixels(latest[0]), snapshot_pixels)
        # The same picture under a new owner is not archived again.
        self.assertEqual(app.get_history_files(None), [])
        entry = app.get_profile_cache().entries([self.profile["id"]])[self.profile["id"]]
        self.assertEqual(app._history_profile_identity(entry["path"])[0], self.profile["id"])
        # Modified again, the same picture is the Latest snapshot's again (an
        # old key, so it is loaded once).
        self.reload(updated=False)
        self.assertEqual(app.shown_profile_row_id(), system.SYSTEM_ID)
        self.assertEqual(self.pixels(app.get_current_image_path()), snapshot_pixels)

    def test_modified_settings_share_the_snapshot_picture_whatever_was_applied(self):
        import marblescape_snapshot as system
        # The profile is applied but modified: the picture has no profile.
        self.run_cycles(1)
        self.assertEqual(app.shown_profile_row_id(), system.SYSTEM_ID)
        key_modified = app.image_cache_configuration_key(app.capture_loaded_configuration())
        self.assertEqual(key_modified["image_profile_id"], "")

        # The Latest snapshot row with the same settings reuses it without a download.
        def load(_path):
            app.restore_loaded_configuration(self.no_profile)
            app.APPLIED_PROFILE_ID = ""

        app.CONFIGURATION_RELOAD_EVENT.set()
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(1)
        self.assertEqual(self.seen, ["goes_east"])
        self.assertEqual(app.image_cache_configuration_key(app.capture_loaded_configuration()), key_modified)
        self.assertEqual(app.shown_profile_row_id(), system.SYSTEM_ID)
        # Unmodified, the applied profile has its own key, shared with rotation.
        self.reload(updated=True)
        key_profile = app.image_cache_configuration_key(app.capture_loaded_configuration())
        self.assertEqual(key_profile["image_profile_id"], self.profile["id"])
        self.assertEqual(app.shown_profile_row_id(), self.profile["id"])

    def test_relabel_changes_only_the_identity(self):
        import io
        import json
        import marblescape_snapshot as system
        from PIL import Image
        from marblescape_image_metadata import embed_png_metadata
        buffer = io.BytesIO()
        Image.new("RGB", (4, 3), (1, 2, 3)).save(buffer, format="PNG")
        record = {"profile_kind": system.SYSTEM_KIND, "profile_name": system.SYSTEM_NAME,
                  "profile_id": "9" * 32, "snapshot_id": "8" * 32, "source": "GOES-East"}
        data = embed_png_metadata(buffer.getvalue(), record)
        relabeled, new_record = app.relabel_picture(data, self.profile["id"])
        with Image.open(io.BytesIO(relabeled)) as picture:
            stored = json.loads(picture.text["MarbleScape"])
            self.assertEqual(picture.convert("RGB").getpixel((0, 0)), (1, 2, 3))
        self.assertEqual(stored, new_record)
        self.assertEqual((stored["profile_id"], stored["profile_name"], stored["source"]),
                         (self.profile["id"], self.profile["name"], "GOES-East"))
        self.assertNotIn("profile_kind", stored)
        self.assertNotIn("snapshot_id", stored)
        # Back to the snapshot; a picture without a record or a deleted profile is left alone.
        _data, snapshot_record = app.relabel_picture(relabeled, None)
        self.assertEqual(snapshot_record["profile_kind"], system.SYSTEM_KIND)
        self.assertEqual(snapshot_record["profile_name"], system.SYSTEM_NAME)
        self.assertIsNone(app.relabel_picture(buffer.getvalue(), self.profile["id"]))
        self.assertIsNone(app.relabel_picture(data, "7" * 32))


class KeepRotationPositionTests(unittest.TestCase):
    """Keep last rotation position also keeps the interval across restarts."""
    setUp = source_tests.SourceRuntimeTests.setUp
    tearDown = source_tests.SourceRuntimeTests.tearDown
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    run_cycles = RotationRuntimeTests.run_cycles

    def start(self):
        library = RotationRuntimeTests.library(self, ("goes_west", "solar"))
        library["rotation"]["keep_last_position"] = True
        app.IMAGE_PROFILE_LIBRARY = normalize_library(library)
        self.ids = [item["id"] for item in app.IMAGE_PROFILE_LIBRARY["items"]]
        self.seen = []

        def fetch(frame, size, **options):
            self.seen.append(app.IMAGE_SOURCE)
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        # The configuration file shows goes_east; rotation profiles exist at runtime only.
        self.file_configuration = app.capture_loaded_configuration()
        self.run_cycles(1)
        self.assertEqual(self.seen, ["goes_west"])

    def restart(self):
        app.restore_loaded_configuration(self.file_configuration)
        app.set_current_image_path(None)
        app.ROTATION_STATUS.update(text="", deadline=None, active_profile_id=None)
        self.run_cycles(1)

    def test_restart_within_the_interval_shows_the_rotation_profile_again(self):
        self.start()
        state = app.load_rotation_position()
        self.assertEqual(state["next_profile_id"], self.ids[1])
        self.assertEqual(state["shown_profile_id"], self.ids[0])
        self.assertIsNotNone(app.seconds_since_rotation_switch(state))
        self.restart()
        # Shown again from its cache; the next profile waits for the interval.
        self.assertEqual(self.seen, ["goes_west"])
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")
        self.assertEqual(app.ROTATION_STATUS["active_profile_id"], self.ids[0])
        self.assertTrue(app.get_current_image_path().is_relative_to(app.get_profile_cache().images_dir))

    def test_restart_after_the_interval_continues_with_the_next_profile(self):
        self.start()
        state = app.load_rotation_position()
        state["last_switch_utc"] = "2000-01-01T00:00:00Z"
        app.save_rotation_position(state)
        self.restart()
        self.assertEqual(self.seen, ["goes_west", "solar"])

    def test_without_a_saved_switch_time_the_rotation_starts_at_once(self):
        self.start()
        state = app.load_rotation_position()
        del state["last_switch_utc"]
        app.save_rotation_position(state)
        self.restart()
        self.assertEqual(self.seen, ["goes_west", "solar"])

    def test_applied_profile_stays_a_full_interval_and_rotation_continues_after_it(self):
        self.start()
        applied = app.IMAGE_PROFILE_LIBRARY["items"][1]

        def load(_path):
            app.restore_loaded_configuration(self.file_configuration)
            app.apply_image_settings(applied["settings"])
            app.APPLIED_PROFILE_ID = applied["id"]

        app.CONFIGURATION_RELOAD_EVENT.set()
        with patch.object(app, "load_configuration", side_effect=load):
            self.run_cycles(1)
        self.assertEqual(self.seen, ["goes_west", "solar"])
        state = app.load_rotation_position()
        # Solar was applied by hand: the rotation continues after it, at goes_west.
        self.assertEqual(state["next_profile_id"], self.ids[0])
        self.assertNotIn("shown_profile_id", state)
        self.assertEqual(app.ROTATION_STATUS["text"], "Waiting for the next rotation interval.")
        self.assertEqual(app.ROTATION_STATUS["deadline"], 160.0)
        # After a restart the applied image from the configuration file stays.
        self.file_configuration = app.capture_loaded_configuration()
        self.restart()
        self.assertEqual(self.seen, ["goes_west", "solar"])
        self.assertEqual(app.IMAGE_SOURCE, "solar")
        self.assertIsNone(app.ROTATION_STATUS["active_profile_id"])



class RotationPreloadTests(unittest.TestCase):
    """The next rotation profile is checked one minute before its switch."""

    setUp = source_tests.SourceRuntimeTests.setUp
    tearDown = source_tests.SourceRuntimeTests.tearDown
    make_png = staticmethod(source_tests.SourceRuntimeTests.make_png)
    library = RotationRuntimeTests.library
    run_cycles = RotationRuntimeTests.run_cycles

    def fetch_log(self):
        seen = []

        def fetch(frame, size, **options):
            seen.append((app.IMAGE_SOURCE, app.time.monotonic()))
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        return seen

    def test_lead_is_one_minute_or_half_a_shorter_rotation_interval(self):
        for interval, unit, lead in ((1, "minutes", 30.0), (2, "minutes", 60.0), (1, "days", 60.0)):
            library = self.library()
            library["rotation"].update(interval=interval, unit=unit)
            self.assertEqual(app.rotation_preload_lead(RotationScheduler(library)), lead, (interval, unit))

    def test_next_picture_loads_before_the_switch_and_the_switch_uses_it(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        app.SET_WINDOWS_WALLPAPER = True
        seen = self.fetch_log()
        deadlines, _ = self.run_cycles(3)
        # goes_west is fetched 30 s before its switch at 160; the switch reuses it.
        self.assertEqual(seen, [("goes_east", 100.0), ("goes_west", 130.0)])
        self.assertEqual(deadlines, [130, 160, 190])
        # The status line kept the result for the step due at 160.
        preload = app.ROTATION_STATUS["preload"]
        self.assertEqual((preload["deadline"], preload["name"], preload["outcome"]), (160, "goes_west", "new"))
        second = app.IMAGE_PROFILE_LIBRARY["items"][1]["id"]
        self.assertEqual(app.ROTATION_STATUS["active_profile_id"], second)
        self.assertTrue(app.get_current_image_path().is_relative_to(app.get_profile_cache().images_dir))
        if os.name == "nt":
            self.assertEqual(self.wallpaper.call_count, 2)
        self.assertIsNone(app.checking_profile_id())

    def test_status_line_note_belongs_to_the_next_step_only(self):
        import datetime as dt
        checked = dt.datetime(2026, 10, 3, 12, 35, 30, tzinfo=dt.timezone.utc)
        state = {"deadline": 160.0, "preload": {"deadline": 160.0, "name": "Bolivia",
                                                 "outcome": "unchanged", "checked_at": checked}}
        # Follows "Upcoming profile: Bolivia at ... | " in Now showing.
        self.assertEqual(app.rotation_preload_result(state, "utc"), "already current (12:35:30 UTC)")
        state["preload"]["outcome"] = "checking"
        self.assertEqual(app.rotation_preload_result(state, "utc"), "checking...")
        state["preload"].update(outcome="failed")
        self.assertIn("check failed, loads at the switch", app.rotation_preload_result(state, "utc"))
        # After the switch the rotation has a new deadline; the old result disappears.
        self.assertEqual(app.rotation_preload_result(dict(state, deadline=220.0), "utc"), "")
        self.assertEqual(app.rotation_preload_result({"deadline": 160.0, "preload": None}, "utc"), "")

    def test_switched_off_preload_loads_at_the_switch(self):
        library = self.library()
        library["rotation"]["preload_next"] = False
        app.IMAGE_PROFILE_LIBRARY = normalize_library(library)
        seen = self.fetch_log()
        deadlines, _ = self.run_cycles(3)
        self.assertEqual(seen, [("goes_east", 100.0), ("goes_west", 160.0)])
        self.assertEqual(deadlines, [160, 220, 280])

    def test_the_shown_profile_is_not_preloaded(self):
        app.IMAGE_PROFILE_LIBRARY = self.library(("goes_east",))
        seen = self.fetch_log()
        deadlines, _ = self.run_cycles(2)
        self.assertEqual(seen, [("goes_east", 100.0)])
        self.assertEqual(deadlines, [160, 220])

    def test_failed_preload_leaves_the_switch_to_load_the_picture(self):
        app.IMAGE_PROFILE_LIBRARY = self.library()
        seen = []

        def fetch(frame, size, **options):
            seen.append((app.IMAGE_SOURCE, app.time.monotonic()))
            if len(seen) == 2:
                raise ValueError("server busy")
            return self.make_png(frame, size, **options)

        self.client.fetch_image.side_effect = fetch
        self.run_cycles(3)
        self.assertEqual([source for source, _time in seen], ["goes_east", "goes_west", "goes_west"])
        self.assertEqual(seen[1][1], 130.0)
        self.assertEqual(seen[2][1], 160.0)
        self.assertTrue(any("Preloading goes_west failed" in " ".join(map(str, call.args))
                            for call in self.log.call_args_list))
        self.assertEqual(app.ROTATION_STATUS["active_profile_id"], app.IMAGE_PROFILE_LIBRARY["items"][1]["id"])


if __name__ == "__main__":
    unittest.main()
