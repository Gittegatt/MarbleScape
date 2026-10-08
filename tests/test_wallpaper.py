"""Wallpaper COM and runtime tests; no calls reach the real Windows desktop.

The enum contract follows Microsoft's DESKTOP_WALLPAPER_POSITION documentation:
https://learn.microsoft.com/windows/win32/api/shobjidl_core/ne-shobjidl_core-desktop_wallpaper_position
"""

from contextlib import ExitStack
from copy import deepcopy
import ctypes
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import marblescape_download as app


@unittest.skipUnless(os.name == "nt", "Windows COM wallpaper contract")
class WallpaperCOMTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events = []
        self.method_signatures = []
        self.pointer = ctypes.c_void_p(12345)
        self.position_result = 0
        self.wallpaper_result = 0
        self.initialize_result = 0
        self.ole32 = SimpleNamespace(
            CoInitialize=Mock(side_effect=self.initialize),
            CoUninitialize=Mock(side_effect=lambda: self.events.append(("uninitialize",))),
        )
        self.stack.enter_context(patch.object(app.ctypes, "windll", SimpleNamespace(ole32=self.ole32)))
        self.stack.enter_context(patch.object(app, "create_desktop_wallpaper_interface", side_effect=self.create_interface))
        self.stack.enter_context(patch.object(app, "get_com_method", side_effect=self.get_method))
        self.stack.enter_context(patch.object(app, "list_windows_wallpaper_monitors", return_value=[]))
        self.stack.enter_context(patch.object(app, "log"))
        self.stack.enter_context(patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fit"))
        self.stack.enter_context(patch.object(app, "WINDOWS_WALLPAPER_PAUSED", frozenset()))
        # The saved Windows desktop color goes to a temporary content folder.
        content = tempfile.TemporaryDirectory(prefix="marblescape-wallpaper-com-")
        self.addCleanup(content.cleanup)
        self.stack.enter_context(patch.object(app, "CONTENT_DIR", Path(content.name)))
        self.stack.enter_context(patch.object(app, "BACKGROUND_COLOR", "#000000"))
        self.desktop_color = 0

    def initialize(self, reserved):
        self.assertIsNone(reserved)
        self.events.append(("initialize",))
        return self.initialize_result

    def create_interface(self):
        self.events.append(("create",))
        return self.pointer

    def get_method(self, pointer, index, restype, *argtypes):
        self.assertIs(pointer, self.pointer)
        self.method_signatures.append((index, restype, argtypes))
        if index == 10:
            def position(interface, value):
                self.assertIs(interface, self.pointer)
                self.events.append(("position", value))
                return self.position_result
            return position
        if index == 3:
            def wallpaper(interface, monitor, filename):
                self.assertIs(interface, self.pointer)
                self.events.append(("wallpaper", monitor, filename))
                return self.wallpaper_result
            return wallpaper
        if index == 9:
            def get_color(interface, result):
                result._obj.value = self.desktop_color
                return 0
            return get_color
        if index == 8:
            def set_color(interface, value):
                self.events.append(("color", value))
                self.desktop_color = value
                return 0
            return set_color
        if index == 2:
            def release(interface):
                self.assertIs(interface, self.pointer)
                self.events.append(("release",))
                return 0
            return release
        self.fail(f"Unexpected COM vtable index: {index}")

    def test_six_windows_modes_set_position_before_wallpaper_and_release_com(self):
        # These values come from the Windows ABI, not from the app's mapping.
        expected_modes = {"center": 0, "tile": 1, "stretch": 2, "fit": 3, "fill": 4, "span": 5}
        self.assertEqual(app.WINDOWS_WALLPAPER_POSITIONS, expected_modes)
        image_path = Path("wallpaper test.png")
        for mode, value in expected_modes.items():
            with self.subTest(mode=mode):
                self.events.clear()
                self.method_signatures.clear()
                app.WINDOWS_WALLPAPER_POSITION = mode
                app.set_windows_wallpaper(image_path)
                self.assertEqual(self.events, [
                    ("initialize",), ("create",), ("position", value),
                    ("wallpaper", None, str(image_path.resolve())),
                    ("release",), ("uninitialize",),
                ])
                self.assertEqual(self.method_signatures, [
                    (3, ctypes.c_long, (ctypes.c_wchar_p, ctypes.c_wchar_p)),
                    (10, ctypes.c_long, (ctypes.c_int,)),
                    (9, ctypes.c_long, (ctypes.POINTER(ctypes.c_uint32),)),
                    (8, ctypes.c_long, (ctypes.c_uint32,)),
                    (2, ctypes.c_ulong, ()),
                ])

    def test_windows_desktop_color_follows_background_and_is_restored(self):
        # Windows' color is 0x00BBGGRR; the user's own one is kept once.
        self.desktop_color = 0x00336699
        app.BACKGROUND_COLOR = "#123456"
        app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertIn(("color", 0x00563412), self.events)
        backup = app.previous_system_background_path()
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"color": 0x00336699})
        app.BACKGROUND_COLOR = "#FFFFFF"
        app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual(self.desktop_color, 0x00FFFFFF)
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"color": 0x00336699})
        self.events.clear()
        app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertNotIn("color", [event[0] for event in self.events], "An unchanged color is not set again")
        app.restore_system_background_color(self.pointer)
        self.assertEqual(self.desktop_color, 0x00336699)
        self.assertFalse(backup.exists())

    def test_failed_position_does_not_set_image_and_still_releases_com(self):
        self.position_result = ctypes.c_long(0x80004005).value
        with self.assertRaisesRegex(OSError, "SetPosition.*80004005"):
            app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual([event[0] for event in self.events],
                         ["initialize", "create", "position", "release", "uninitialize"])

    def test_failed_wallpaper_still_releases_com(self):
        self.wallpaper_result = ctypes.c_long(0x80004005).value
        with self.assertRaisesRegex(OSError, "SetWallpaper.*80004005"):
            app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual([event[0] for event in self.events],
                         ["initialize", "create", "position", "wallpaper", "release", "uninitialize"])

    def test_s_false_initialization_and_position_are_successful_and_balanced(self):
        self.initialize_result = self.position_result = 1
        app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual(self.ole32.CoUninitialize.call_count, 1)
        self.assertEqual([event[0] for event in self.events][-3:], ["wallpaper", "release", "uninitialize"])

    def test_failed_com_initialization_does_not_create_or_uninitialize_interface(self):
        self.initialize_result = ctypes.c_long(0x80010106).value
        with self.assertRaisesRegex(OSError, "CoInitialize.*80010106"):
            app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual(self.events, [("initialize",)])
        self.ole32.CoUninitialize.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows wallpaper runtime integration")
class WallpaperRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()
        self.saved_image_status = deepcopy(app.IMAGE_STATUS)
        self.saved_rotation_status = deepcopy(app.ROTATION_STATUS)
        self.saved_rotation_deadline = app.NEXT_ROTATION_DEADLINE
        self.events = (app.APPLICATION_STOP_EVENT, app.CONFIGURATION_RELOAD_EVENT,
                       app.SETTINGS_ONLY_RELOAD_EVENT, app.FORCE_UPDATE_EVENT)
        self.saved_event_states = [event.is_set() for event in self.events]
        for event in self.events:
            event.clear()
        self.temporary = tempfile.TemporaryDirectory(prefix="marblescape-wallpaper-test-")
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.toml"
        config = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        config = app.replace_source_configuration(config, "goes_east", app.DEFAULT_SOURCE_PROFILES)
        config = app.replace_toml_values(config, [
            ("output", "windows_root", self.root.as_posix()),
            ("output", "linux_root", self.root.as_posix()),
            ("output", "width", 32), ("output", "height", 18),
            ("output", "aspect_ratio", "16:9"),
            ("history", "enabled", False),
            ("service", "run_continuously", True),
            ("windows", "set_wallpaper", True),
            ("windows", "position", "fit"),
            ("windows", "pause_on_error", False),
        ])
        self.config_path.write_text(config, encoding="utf-8")
        buffer = io.BytesIO()
        with Image.new("RGB", (32, 18), (30, 70, 90)) as image:
            image.save(buffer, format="PNG")
        self.frame = {
            "url": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/GEOCOLOR/20262550000_GOES19-ABI-FD-GEOCOLOR-1808x1808.jpg",
            "timestamp": "2026-09-12T00:00:00Z", "expected_interval_seconds": 600,
        }
        self.client = SimpleNamespace(latest=Mock(return_value=self.frame), fetch_image=Mock(return_value=buffer.getvalue()))
        self.wallpaper_calls = []
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(
            app, "PROFILE_LIBRARY_PATH", self.root / "profiles.toml"
        ))
        self.stack.enter_context(patch.object(app, "get_noaa_client", return_value=self.client))
        self.stack.enter_context(patch.object(app, "download_capabilities", side_effect=AssertionError("Unexpected WMS request")))
        self.stack.enter_context(patch.object(app, "urlopen", side_effect=AssertionError("Unexpected network request")))
        self.stack.enter_context(patch.object(app, "log"))
        self.wallpaper = self.stack.enter_context(patch.object(app, "set_windows_wallpaper", side_effect=self.record_wallpaper))
        # Picture sizes never depend on the displays of the computer running the tests.
        self.stack.enter_context(patch.object(app, "list_windows_wallpaper_monitors", return_value=[]))

    def tearDown(self):
        try:
            app.restore_loaded_configuration(self.saved_configuration)
            app.IMAGE_STATUS.clear()
            app.IMAGE_STATUS.update(self.saved_image_status)
            app.ROTATION_STATUS.clear()
            app.ROTATION_STATUS.update(self.saved_rotation_status)
            app.NEXT_ROTATION_DEADLINE = self.saved_rotation_deadline
            for event, was_set in zip(self.events, self.saved_event_states):
                event.set() if was_set else event.clear()
        finally:
            self.stack.close()
            self.temporary.cleanup()

    def record_wallpaper(self, path):
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(self.root))
        self.wallpaper_calls.append((path, app.WINDOWS_WALLPAPER_POSITION))

    def write_two_profiles(self, check_for_updates=True):
        """Profiles First and Second differ only in zoom; returns an apply(identifier) helper."""
        app.load_configuration(self.config_path)
        first_settings = app.normalize_image_settings_snapshot(app.image_settings_snapshot())
        first_settings["source"]["check_for_updates"] = check_for_updates
        second_settings = deepcopy(first_settings)
        second_settings["view"]["zoom"] = 1.5
        profiles = {"a" * 32: ("First", first_settings), "b" * 32: ("Second", second_settings)}
        self.profiles = profiles
        self.write_rotation([])

        def apply(identifier, modified=False):
            """What Apply profile writes: the profile's image settings and its ID."""
            settings = deepcopy(profiles[identifier][1])
            if modified:
                settings["view"]["zoom"] = 2.5
            text = app.replace_image_settings(self.config_path.read_text(encoding="utf-8"), settings)
            text = app.ensure_profile_list_configuration_section(text)
            text = app.replace_toml_section_value(text, "profile_list", "applied_profile_id", identifier)
            self.config_path.write_text(text, encoding="utf-8")
        return apply

    def write_rotation(self, order):
        """Save the two profiles; a non-empty order enables rotation over them."""
        (self.root / "profiles.toml").write_text(app.serialize_library(app.normalize_library({"items": [
            {"id": identifier, "name": name, "settings": settings}
            for identifier, (name, settings) in self.profiles.items()],
            "rotation": {"enabled": bool(order), "interval": 60, "unit": "minutes", "order": list(order)},
        })), encoding="utf-8")

    def run_applied_sequence(self, apply, sequence):
        """Apply the first entry, then each further one after a reload."""
        apply(*sequence[0])
        remaining = list(sequence[1:])

        def next_cycle(*args, **kwargs):
            if not remaining:
                return False
            apply(*remaining.pop(0))
            app.CONFIGURATION_RELOAD_EVENT.set()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])

    @staticmethod
    def latest_profile_id():
        with Image.open(app.get_latest_image_files()[0]) as image:
            return json.loads(image.text["MarbleScape"]).get("profile_id")

    def test_returning_to_an_applied_profile_reuses_its_cached_image(self):
        first, second = "a" * 32, "b" * 32
        apply = self.write_two_profiles()
        self.run_applied_sequence(apply, [(first,), (second,), (first,)])
        # First and Second were downloaded once each; returning to First only
        # checked the provider's latest frame and reused the cached image.
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertEqual(self.client.latest.call_count, 3)
        latest = app.get_latest_image_files()
        self.assertEqual(len(latest), 1)
        self.assertEqual(self.latest_profile_id(), first)
        self.assertEqual(self.wallpaper_calls[-1][0], latest[0].resolve())
        self.assertEqual(set(app.get_profile_cache().entries()), {first, second})

    def test_switching_back_and_forth_archives_each_cached_image_once(self):
        first, second = "a" * 32, "b" * 32
        self.config_path.write_text(app.replace_toml_values(
            self.config_path.read_text(encoding="utf-8"), [("history", "enabled", True)]), encoding="utf-8")
        apply = self.write_two_profiles()
        self.run_applied_sequence(apply, [(first,), (second,), (first,), (second,), (first,)])
        self.assertEqual(self.client.fetch_image.call_count, 2)
        # Leaving a profile again archives the same cached image only once.
        for identifier, name in ((first, "First"), (second, "Second")):
            archived = list(app.profile_history_directory(identifier, name).glob("*.png"))
            self.assertEqual(len(archived), 1, archived)
        self.assertEqual(self.latest_profile_id(), first)

    def test_history_archive_skips_an_identical_image_but_keeps_different_ones(self):
        app.load_configuration(self.config_path)
        source = self.root / "MarbleScape_2026-09-30T222347Z_test.png"
        Image.new("RGB", (32, 18), (10, 20, 30)).save(source)
        with app.HISTORY_LOCK:
            archived = app.archive_history_copy(source, None)
            self.assertIsNotNone(archived)
            self.assertIsNone(app.archive_history_copy(source, None))
            # Same name, different content: kept next to it with a suffix.
            Image.new("RGB", (32, 18), (40, 50, 60)).save(source)
            second = app.archive_history_copy(source, None)
        self.assertEqual(second.name, "MarbleScape_2026-09-30T222347Z_test_1.png")
        self.assertEqual(len(list(archived.parent.glob("*.png"))), 2)

    def test_download_status_tells_updating_the_shown_profile_from_loading_another(self):
        first, second = "a" * 32, "b" * 32
        apply = self.write_two_profiles()
        self.run_applied_sequence(apply, [(first,)])
        # The wallpaper shows First: downloading First again renews it.
        self.assertEqual(app.download_subject(),
                         {"profile_id": first, "source": "goes_east", "updating": True})
        apply(second)
        app.load_configuration(self.config_path)
        self.assertEqual(app.download_subject(),
                         {"profile_id": second, "source": "goes_east", "updating": False})
        # Rotation passes its profile explicitly.
        self.assertEqual(app.download_subject(first)["updating"], True)
        apply(second, modified=True)
        app.load_configuration(self.config_path)
        self.assertIsNone(app.download_subject())  # Modified settings belong to no profile.

        self.assertIsNone(app.profile_download_status())
        app.DOWNLOAD_PROGRESS.begin(1, app.download_subject(first))
        try:
            status = app.profile_download_status()
            self.assertEqual((status["profile_id"], status["updating"], status["expected_requests"]),
                             (first, True, 1))
        finally:
            app.DOWNLOAD_PROGRESS.finish(False)
        self.assertIsNone(app.profile_download_status())
        # A download without a profile shows in the Latest snapshot row.
        import marblescape_snapshot as system
        app.DOWNLOAD_PROGRESS.begin(1, app.download_subject())
        try:
            self.assertEqual(app.profile_download_status()["profile_id"], system.SYSTEM_ID)
        finally:
            app.DOWNLOAD_PROGRESS.finish(False)

    def test_applied_profile_without_image_updates_reuses_its_cache_offline(self):
        first, second = "a" * 32, "b" * 32
        apply = self.write_two_profiles(check_for_updates=False)
        self.run_applied_sequence(apply, [(first,), (second,), (first,)])
        # With Imagery updates off, returning to First contacts no provider at all.
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertEqual(self.client.latest.call_count, 2)
        self.assertEqual(self.latest_profile_id(), first)
        self.assertEqual(self.wallpaper_calls[-1][0], app.get_latest_image_files()[0].resolve())

    def test_forced_download_of_an_applied_profile_also_fills_the_cache(self):
        first, second = "a" * 32, "b" * 32
        apply = self.write_two_profiles()
        apply(first)

        def force_after_emptying_cache():
            app.get_profile_cache().clear()
            app.FORCE_UPDATE_EVENT.set()

        def reload_with(identifier):
            apply(identifier)
            app.CONFIGURATION_RELOAD_EVENT.set()
        steps = [force_after_emptying_cache, lambda: reload_with(second), lambda: reload_with(first)]

        def next_cycle(*args, **kwargs):
            if not steps:
                return False
            steps.pop(0)()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        # First, forced First and Second were downloaded; the final return to
        # First reused the image the forced download had put into the cache.
        self.assertEqual(self.client.fetch_image.call_count, 3)
        self.assertEqual(self.latest_profile_id(), first)

    def test_newer_frame_or_modified_profile_still_downloads(self):
        first, second = "a" * 32, "b" * 32
        apply = self.write_two_profiles()
        newer = dict(self.frame, timestamp="2026-09-12T00:10:00Z",
                     url=self.frame["url"].replace("20262550000", "20262550010"))
        frames = [self.frame, self.frame, newer]
        self.client.latest.side_effect = lambda *args, **kwargs: frames.pop(0) if frames else newer
        self.run_applied_sequence(apply, [(first,), (second,), (first,)])
        # The provider had a newer frame for First, so it was downloaded again.
        self.assertEqual(self.client.fetch_image.call_count, 3)
        self.assertEqual(self.latest_profile_id(), first)
        # Changed settings ("modified") belong to no profile: they never use or
        # fill the profile's entry, only the Latest snapshot's slot.
        import marblescape_snapshot as system
        self.client.fetch_image.reset_mock()
        self.client.latest.side_effect = None
        self.client.latest.return_value = newer
        app.get_profile_cache().clear()
        self.run_applied_sequence(apply, [(first, True), (second,), (first, True)])
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertNotIn(first, app.get_profile_cache().entries())
        self.assertIn(system.CACHE_ID, app.get_profile_cache().entries())

    def test_rotation_and_manual_apply_share_profile_cache_entries(self):
        first, second = "a" * 32, "b" * 32
        apply = self.write_two_profiles()
        apply(first)
        self.write_rotation([second])

        def reload_with(order, identifier=None):
            self.write_rotation(order)
            if identifier:
                apply(identifier)
            app.CONFIGURATION_RELOAD_EVENT.set()
        steps = [
            # Rotation showed Second while First was applied; now apply Second by hand.
            lambda: reload_with([], second),
            lambda: reload_with([], first),
            # First was applied by hand; now rotation shows First while Second is applied.
            lambda: reload_with([first], second),
        ]
        fetches = []
        latest_ids = []

        def next_cycle(*args, **kwargs):
            fetches.append(self.client.fetch_image.call_count)
            latest_ids.append(self.latest_profile_id() if app.get_latest_image_files() else None)
            if not steps:
                return False
            steps.pop(0)()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        # Only rotation's Second and the manual First were downloaded: each
        # side reused the image the other one had put into the profile cache.
        self.assertEqual(fetches, [1, 1, 2, 2])
        # Latest always holds the picture on screen, also the rotation's.
        self.assertEqual(latest_ids, [second, second, first, first])
        self.assertEqual(app.ROTATION_STATUS["active_profile_id"], first)
        self.assertTrue(app.get_current_image_path().is_relative_to(app.get_profile_cache().images_dir))
        self.assertEqual(self.wallpaper_calls[-1][0], app.get_current_image_path().resolve())

    def test_latest_always_holds_the_picture_on_screen(self):
        first, second = "a" * 32, "b" * 32
        self.config_path.write_text(app.replace_toml_values(
            self.config_path.read_text(encoding="utf-8"), [("history", "enabled", True)]), encoding="utf-8")
        self.write_two_profiles()
        shades = iter(range(10, 250, 10))

        def render(frame, output_size, **kwargs):
            # Every download is a different picture.
            buffer = io.BytesIO()
            with Image.new("RGB", tuple(output_size), (next(shades), 70, 90)) as image:
                image.save(buffer, format="PNG")
            return buffer.getvalue()
        self.client.fetch_image.side_effect = render

        def rotate(order):
            self.write_rotation(order)
            app.CONFIGURATION_RELOAD_EVENT.set()
        steps = [lambda: rotate([first]), lambda: rotate([second])]
        shown = []

        def next_cycle(*args, **kwargs):
            self.assertEqual(len(app.get_latest_image_files()), 1)
            shown.append(self.latest_profile_id())
            with Image.open(app.get_latest_image_files()[0]) as latest,                  Image.open(app.get_current_image_path()) as current:
                self.assertEqual(latest.tobytes(), current.tobytes(), "Latest is the picture on screen")
            if not steps:
                return False
            steps.pop(0)()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        # The profile-free picture carries the Latest snapshot's ID.
        self.assertNotIn(shown[0], (first, second))
        self.assertEqual(shown[1:], [first, second])
        self.assertFalse(app.get_latest_image_files()[0].is_relative_to(app.get_profile_cache().images_dir))
        # Each picture that left the screen went to its own History folder once.
        self.assertEqual(len(list(app.profile_history_directory(None).glob("*.png"))), 1)
        self.assertEqual(len(list(app.profile_history_directory(first, "First").glob("*.png"))), 1)
        self.assertEqual(list(app.profile_history_directory(second, "Second").glob("*.png")), [])

    def test_cache_key_ignores_the_last_manually_applied_profile(self):
        app.load_configuration(self.config_path)
        configuration = app.capture_loaded_configuration()
        keys = []
        for applied in ("a" * 32, "b" * 32, ""):
            configuration["APPLIED_PROFILE_ID"] = applied
            key = app.image_cache_configuration_key(configuration)
            # What the main loop does for a rotated profile.
            key["image_profile_id"] = "c" * 32
            key["image_profile_name"] = None
            keys.append(key)
        self.assertNotIn("applied_profile_id", keys[0])
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[0], keys[2])
        # Switching profiles by hand still counts as an image change.
        changed = dict(configuration, APPLIED_PROFILE_ID="a" * 32)
        self.assertNotEqual(app.image_configuration_key(configuration), app.image_configuration_key(changed))

    def test_reloaded_position_reapplies_identical_image_path(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "span"])
        self.assertEqual(self.wallpaper_calls[0][0], self.wallpaper_calls[1][0])
        self.assertEqual(len(app.get_latest_image_files()), 1)
        self.assertEqual(app.WINDOWS_WALLPAPER_POSITION, "span")
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_position_change_applies_existing_image_even_when_noaa_is_offline(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                self.client.fetch_image.side_effect = OSError("NOAA is offline")
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "span"])
        self.assertEqual(self.wallpaper_calls[0][0], self.wallpaper_calls[1][0])
        self.assertEqual(len(app.get_latest_image_files()), 1)
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["error"], "")

    def test_failed_position_change_retries_existing_image_while_noaa_stays_offline(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                self.client.fetch_image.side_effect = OSError("NOAA is still offline")
                app.CONFIGURATION_RELOAD_EVENT.set()
            return cycles < 3
        def fail_first_span(path):
            self.record_wallpaper(path)
            if app.WINDOWS_WALLPAPER_POSITION == "span" and len(self.wallpaper_calls) == 2:
                raise OSError("Temporary Windows position failure")
        self.wallpaper.side_effect = fail_first_span
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "span", "span"])
        self.assertEqual(len({path for path, _ in self.wallpaper_calls}), 1)
        self.assertEqual(len(app.get_latest_image_files()), 1)
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["error"], "")

    def test_every_position_change_uses_local_image_without_checking_source(self):
        modes = iter(("center", "tile", "stretch", "fit", "fill", "span"))
        def next_cycle(*args, **kwargs):
            mode = next(modes, None)
            if mode is None:
                return False
            config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                            [("windows", "position", mode)])
            self.config_path.write_text(config, encoding="utf-8")
            app.CONFIGURATION_RELOAD_EVENT.set()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls],
                         ["fit", "center", "tile", "stretch", "fit", "fill", "span"])
        self.assertEqual(len({path for path, _ in self.wallpaper_calls}), 1)
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_position_only_reload_preserves_next_regular_check_deadline(self):
        # A fixed 5-minute interval puts the next regular check at 100 + 300 s.
        self.config_path.write_text(app.replace_toml_values(
            self.config_path.read_text(encoding="utf-8"),
            [("service", "update_interval_minutes", 5.0)]), encoding="utf-8")
        clock = [100.0]
        starts = []
        def next_cycle(start, *args, **kwargs):
            starts.append(start)
            if len(starts) == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "center")])
                self.config_path.write_text(config, encoding="utf-8")
                clock[0] = 110
                app.CONFIGURATION_RELOAD_EVENT.set()
            elif len(starts) == 2:
                self.assertEqual(app.NEXT_ROTATION_DEADLINE, 400)
                clock[0] = 399
            elif len(starts) == 3:
                clock[0] = 400
            return len(starts) < 4
        with patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(starts, [100, 100, 100, 400])
        self.assertEqual(self.client.latest.call_count, 2)
        self.client.fetch_image.assert_called_once()

    def test_force_download_still_works_after_local_position_change(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                app.CONFIGURATION_RELOAD_EVENT.set()
            elif cycles == 2:
                app.FORCE_UPDATE_EVENT.set()
            return cycles < 3
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertEqual(self.client.latest.call_count, 2)

    def save_settings(self, values):
        """What Save on the General tab writes, followed by its settings-only reload."""
        self.config_path.write_text(app.replace_toml_values(
            self.config_path.read_text(encoding="utf-8"), values), encoding="utf-8")
        app.SETTINGS_ONLY_RELOAD_EVENT.set()
        app.CONFIGURATION_RELOAD_EVENT.set()

    def test_saved_background_renders_the_shown_image_at_once(self):
        steps = [
            # Other General settings keep waiting for the regular check.
            lambda: self.save_settings([("display", "time_zone", "utc")]),
            lambda: self.save_settings([("output", "background_color", "#123456")]),
        ]
        fetches = []

        def next_cycle(*args, **kwargs):
            fetches.append(self.client.fetch_image.call_count)
            if not steps:
                return False
            steps.pop(0)()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(fetches, [1, 1, 2])
        self.assertEqual(app.BACKGROUND_COLOR, "#123456")
        self.assertEqual(self.wallpaper_calls[-1][0], app.get_current_image_path().resolve())

    def test_saved_settings_keep_showing_the_rotation_profile(self):
        first, second = "a" * 32, "b" * 32
        self.write_two_profiles()
        # The settings file holds First's image settings; rotation shows Second.
        self.write_rotation([second])

        def render(frame, output_size, **kwargs):
            # NOAA renders the requested output size.
            buffer = io.BytesIO()
            with Image.new("RGB", tuple(output_size), (30, 70, 90)) as image:
                image.save(buffer, format="PNG")
            return buffer.getvalue()
        self.client.fetch_image.side_effect = render
        shown = []

        def record():
            with Image.open(app.get_current_image_path()) as image:
                profile_id = json.loads(image.text["MarbleScape"]).get("profile_id")
            shown.append((self.client.fetch_image.call_count, app.ZOOM,
                          app.ROTATION_STATUS["active_profile_id"], profile_id))
        steps = [
            lambda: self.save_settings([("display", "time_zone", "utc")]),
            lambda: self.save_settings([("output", "width", 48), ("output", "height", 27)]),
        ]

        def next_cycle(*args, **kwargs):
            record()
            if not steps:
                return False
            steps.pop(0)()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(shown, [(1, 1.5, second, second), (1, 1.5, second, second),
                                 (2, 1.5, second, second)])
        with Image.open(app.get_current_image_path()) as image:
            self.assertEqual(image.size, (48, 27))

    def test_eumetsat_position_change_does_not_rebuild_or_fetch_image(self):
        text = app.replace_source_configuration(self.config_path.read_text(encoding="utf-8"),
                                               "eumetsat", app.DEFAULT_SOURCE_PROFILES)
        self.config_path.write_text(text, encoding="utf-8")
        app.download_capabilities.side_effect = None
        app.download_capabilities.return_value = b"<Capabilities/>"
        path = self.root / "content" / "latest" / "marblescape_2026-09-12_00-00-00.png"
        def update(*args, **kwargs):
            path.write_bytes(self.client.fetch_image.return_value)
            return path, path, path.stat().st_size
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "tile")])
                self.config_path.write_text(config, encoding="utf-8")
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False
        plan = (32, 18, 32, 18, 1.0, "", "", {}, [], "server", [], False, None)
        with patch.object(app, "parse_layers", return_value={}), \
             patch.object(app, "prepare_runtime_render_plan", return_value=plan) as prepare, \
             patch.object(app, "perform_update", side_effect=update) as fetch, \
             patch.object(app, "print_configuration"), \
             patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        app.download_capabilities.assert_called_once()
        prepare.assert_called_once()
        fetch.assert_called_once()
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "tile"])

    def test_unchanged_frame_without_reload_does_not_reapply_wallpaper(self):
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main(["--config", str(self.config_path)])
        self.wallpaper.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_transient_windows_failure_retries_existing_path_without_redownload(self):
        def fail_first(path):
            self.record_wallpaper(path)
            if len(self.wallpaper_calls) == 1:
                raise OSError("Temporary desktop API failure")
        self.wallpaper.side_effect = fail_first
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(len(self.wallpaper_calls), 2)
        self.assertEqual(self.wallpaper_calls[0][0], self.wallpaper_calls[1][0])
        self.client.fetch_image.assert_called_once()


if __name__ == "__main__":
    unittest.main()
