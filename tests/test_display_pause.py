"""Pause wallpaper updates per display, and pictures as large as the displays need."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app

FIRST, SECOND = "DISPLAY-FIRST", "DISPLAY-SECOND"
MONITORS = [{"id": FIRST, "rect": (0, 0, 2560, 1440)}, {"id": SECOND, "rect": (-1050, 0, 0, 1680)}]


class PauseTests(unittest.TestCase):
    def test_older_do_not_update_positions_become_pauses_with_a_position(self):
        position, positions, paused = app.migrate_paused_positions(
            "fit", {FIRST: "none", SECOND: "span"}, frozenset())
        self.assertEqual((position, positions, paused), ("fit", {SECOND: "span"}, {FIRST}))
        # A shared Do not update paused every display; the position becomes Fill.
        position, positions, paused = app.migrate_paused_positions("none", {}, frozenset({SECOND}))
        self.assertEqual((position, positions, paused), ("fill", {}, {SECOND, app.PAUSE_ALL_DISPLAYS}))

    def test_paused_list_is_validated(self):
        self.assertEqual(app.normalize_paused_displays('["A", "B"]'), {"A", "B"})
        for bad in ("{", '"A"', "[1]", '[""]', {"A": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.normalize_paused_displays(bad)

    def test_a_paused_display_is_none_for_the_wallpaper_and_keeps_its_position(self):
        shared, positions = app.effective_wallpaper_positions("fill", {SECOND: "center"}, frozenset({SECOND}))
        self.assertEqual((shared, positions), ("fill", {SECOND: "none"}))
        shared, positions = app.effective_wallpaper_positions("fill", {SECOND: "center"},
                                                              frozenset({app.PAUSE_ALL_DISPLAYS}))
        self.assertEqual((shared, positions), ("none", {SECOND: "none"}))
        self.assertTrue(app.display_paused(FIRST, frozenset({app.PAUSE_ALL_DISPLAYS})))
        self.assertFalse(app.display_paused(FIRST, frozenset({SECOND})))

    def test_load_and_save_keep_the_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "settings.toml"
            text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            text = app.replace_toml_values(text, [
                ("windows", "position", "none"),
                ("windows", "monitor_positions", '{"DISPLAY-SECOND": "span"}'),
            ])
            config.write_text(text, encoding="utf-8")
            saved = app.capture_loaded_configuration()
            self.addCleanup(app.restore_loaded_configuration, saved)
            app.load_configuration(config)
            self.assertEqual(app.WINDOWS_WALLPAPER_POSITION, "fill")
            self.assertEqual(app.WINDOWS_WALLPAPER_PAUSED, {app.PAUSE_ALL_DISPLAYS})
            self.assertEqual(app.WINDOWS_WALLPAPER_MONITOR_POSITIONS, {SECOND: "span"})
            config.write_text(app.replace_toml_values(text, [
                ("windows", "position", "fit"), ("windows", "paused_displays", '["DISPLAY-FIRST"]')]),
                encoding="utf-8")
            app.load_configuration(config)
            self.assertEqual((app.WINDOWS_WALLPAPER_POSITION, app.WINDOWS_WALLPAPER_PAUSED), ("fit", {FIRST}))
            with self.assertRaises(ValueError):
                config.write_text(app.replace_toml_values(text, [("windows", "paused_displays", "[1]")]),
                                  encoding="utf-8")
                app.load_configuration(config)

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_set_wallpaper_skips_a_paused_display(self):
        events = []

        def method(_interface, index, _restype, *_argtypes):
            if index == 11:
                def get_position(_desktop, position):
                    import ctypes
                    ctypes.cast(position, ctypes.POINTER(ctypes.c_int))[0] = 4
                    return 0
                return get_position
            if index == 10:
                return lambda _desktop, value: events.append(("position", value)) or 0
            if index == 3:
                return lambda _desktop, monitor, path: events.append(("wallpaper", monitor)) or 0
            raise AssertionError(index)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "picture.png"
            Image.new("RGB", (8, 4), "red").save(image)
            with patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fill"), \
                 patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {SECOND: "center"}), \
                 patch.object(app, "WINDOWS_WALLPAPER_PAUSED", frozenset({SECOND})), \
                 patch.object(app, "WINDOWS_WALLPAPER_MONITOR_OUTPUTS", {}), \
                 patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "list_windows_wallpaper_monitors", return_value=MONITORS), \
                 patch.object(app, "capture_previous_wallpapers"), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"), \
                 patch.object(app, "sync_system_background_color"), \
                 patch.object(app, "windows_display_layout", return_value=[]), \
                 patch.object(app, "log"):
                app.set_windows_wallpaper(image)
        self.assertEqual([event[1] for event in events if event[0] == "wallpaper"], [FIRST])


class PictureSizeTests(unittest.TestCase):
    def size(self, provider="himawari", outputs=None, paused=frozenset(), wallpaper=True,
             monitors=MONITORS, fallback=(2160, 1215)):
        outputs = {FIRST: {"width": 3840, "height": 0, "aspect_ratio": "16:9"}} if outputs is None else outputs
        with patch.object(app, "SET_WINDOWS_WALLPAPER", wallpaper), \
             patch.object(app, "os", type("os", (), {"name": "nt"})), \
             patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            return app.displays_image_size(provider, fallback, outputs, paused)

    def test_the_picture_covers_the_largest_active_display(self):
        # A 3840-wide output on the first display: the 2160 picture would be enlarged again.
        self.assertEqual(self.size(), (3840, 2160))
        # Without own outputs the physical displays decide; the portrait one needs 1680 rows.
        self.assertEqual(self.size(outputs={}), (2987, 1680))
        # A paused display does not count.
        self.assertEqual(self.size(outputs={}, paused=frozenset({SECOND})), (2560, 1440))
        # A larger shared output stays as it is.
        self.assertEqual(self.size(outputs={}, monitors=[], fallback=(5120, 2880)), (5120, 2880))

    def test_eumetsat_stays_within_its_wms_limit_and_wallpapers_off_keep_the_output(self):
        outputs = {FIRST: {"width": 7680, "height": 0, "aspect_ratio": "16:9"}}
        width, height = self.size("eumetsat", outputs=outputs)
        self.assertEqual(max(width, height), app.MAX_WMS_DIMENSION)
        self.assertAlmostEqual(width / height, 16 / 9, places=2)
        self.assertEqual(self.size(outputs=outputs, wallpaper=False), (2160, 1215))

    def test_every_source_renders_at_the_display_sized_picture(self):
        # The size the picture is really drawn at, not only the one computed for it.
        for source in ("goes_east", "solar", "himawari", "slider", "worldview", "copernicus"):
            with self.subTest(source=source), \
                    patch.object(app, "IMAGE_SOURCE", source), \
                    patch.object(app, "displays_image_size", return_value=(3840, 2160)), \
                    patch.object(app, "copernicus_auto_size", return_value=(3840, 2160)):
                self.assertEqual(app.prepare_runtime_render_plan([])[:4], (3840, 2160, 3840, 2160))

    def test_image_dimensions_and_cache_key_follow_the_displays(self):
        with patch.object(app, "IMAGE_SOURCE", "goes_east"), \
             patch.object(app, "displays_image_size", return_value=(3840, 2160)) as enlarged:
            self.assertEqual(app.get_image_dimensions(), (3840, 2160))
        self.assertEqual(enlarged.call_args.args[0], "goes_east")
        configuration = app.capture_loaded_configuration()
        configuration.update(IMAGE_SOURCE="goes_east", SET_WINDOWS_WALLPAPER=True,
                             WINDOWS_WALLPAPER_MONITOR_OUTPUTS={FIRST: {"width": 3840}})
        self.assertEqual(app.image_configuration_key(configuration)["monitor_image_targets"],
                         {FIRST: {"width": 3840}})
        configuration["SET_WINDOWS_WALLPAPER"] = False
        self.assertNotIn("monitor_image_targets", app.image_configuration_key(configuration))


if __name__ == "__main__":
    unittest.main()
