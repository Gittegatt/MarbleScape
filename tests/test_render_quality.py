"""Render quality: General's display value and EUMETSAT's own value or default."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import marblescape_download as app
from marblescape_profile_transfer import portable_settings


class RenderQualityTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()

    def tearDown(self):
        app.restore_loaded_configuration(self.saved_configuration)

    def template(self):
        return app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")

    def load(self, text):
        with tempfile.TemporaryDirectory(prefix="marblescape-render-quality-") as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            with patch.object(app, "log"):
                app.load_configuration(path)

    def test_only_a_profile_value_may_be_default(self):
        self.assertEqual(app.parse_render_scale_setting("Default", allow_default=True), "default")
        self.assertEqual(app.parse_render_scale_setting("auto", allow_default=True), "auto")
        self.assertEqual(app.parse_render_scale_setting("1.5", allow_default=True), 1.5)
        with self.assertRaises(ValueError):
            app.parse_render_scale_setting("default")
        for value in ("0.5", "fine", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.parse_render_scale_setting(value, allow_default=True)

    def test_new_configurations_let_eumetsat_follow_general(self):
        output = tomllib.loads(self.template())["output"]
        self.assertEqual((output["render_scale"], output["display_render_scale"]), ("default", "auto"))
        self.load(app.replace_toml_values(self.template(), [("output", "display_render_scale", 1.25)]))
        self.assertEqual((app.get_render_scale_setting(), app.DISPLAY_RENDER_SCALE), ("default", 1.25))
        self.assertEqual(app.effective_render_scale_setting(), 1.25)
        self.assertEqual(app.get_requested_render_scale(1000, 1000), 1.25)
        self.assertEqual(app.image_settings_snapshot()["output"]["render_scale"], "default")
        # EUMETSAT's own value wins over General's.
        self.load(app.replace_toml_values(self.template(), [("output", "render_scale", 2.0),
                                                            ("output", "display_render_scale", 1.25)]))
        self.assertEqual((app.effective_render_scale_setting(), app.DISPLAY_RENDER_SCALE), (2.0, 1.25))

    def test_older_files_keep_the_display_render_quality_they_used(self):
        # Before General had its own value, the displays used the image's one.
        older = self.template().replace('display_render_scale = "auto"', "")
        self.load(app.replace_toml_values(older, [("output", "render_scale", 1.5)]))
        self.assertNotIn("display_render_scale", tomllib.loads(older)["output"])
        self.assertEqual((app.get_render_scale_setting(), app.DISPLAY_RENDER_SCALE), (1.5, 1.5))
        self.load(app.replace_toml_values(older, [("output", "render_scale", "auto")]))
        self.assertEqual((app.get_render_scale_setting(), app.DISPLAY_RENDER_SCALE), ("auto", "auto"))
        with self.assertRaises(ValueError):
            self.load(app.replace_toml_values(self.template(), [("output", "display_render_scale", "default")]))

    def test_default_and_the_same_own_value_give_the_same_eumetsat_picture_key(self):
        self.load(self.template())
        app.IMAGE_SOURCE = "eumetsat"
        inherited = app.capture_loaded_configuration()
        app.RENDER_SCALE_INHERITED, app.RENDER_SCALE_AUTOMATIC = False, True
        own = app.capture_loaded_configuration()
        self.assertEqual(app.image_configuration_key(inherited), app.image_configuration_key(own))
        # A different General value renders EUMETSAT anew while it inherits it.
        inherited["DISPLAY_RENDER_SCALE"] = 2.0
        self.assertNotEqual(app.image_configuration_key(inherited), app.image_configuration_key(own))
        # Other sources never sign the render quality.
        for configuration in (inherited, own):
            configuration["IMAGE_SOURCE"] = "goes_east"
        self.assertEqual(app.image_configuration_key(inherited), app.image_configuration_key(own))

    def test_profiles_exports_and_backups_accept_default(self):
        self.load(self.template())
        snapshot = app.image_settings_snapshot()
        self.assertEqual(app.normalize_image_settings_snapshot(snapshot)["output"]["render_scale"], "default")
        exported = portable_settings(snapshot, app.normalize_image_settings_snapshot)
        self.assertEqual(exported["output"], {"render_scale": "default"})
        config = tomllib.loads(self.template())
        app.validate_backup_configuration(config)
        for value in ("default", "huge", True, 0.5):
            damaged = deepcopy(config)
            damaged["output"]["display_render_scale"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.validate_backup_configuration(damaged)

    def test_backups_with_display_sizes_but_no_display_quality_export(self):
        # Two displays with their own size and General's render quality; the image
        # follows General ("default"). This failed both exports.
        config = tomllib.loads(self.template())
        self.assertEqual(config["output"]["render_scale"], "default")
        displays = {r"\\?\DISPLAY#HPN3678#7": {"aspect_ratio": "16:9", "height": 0, "width": 3840},
                    r"\\?\DISPLAY#LEN0A12#7": {"aspect_ratio": "8:5", "height": 0, "width": 1680}}
        config.setdefault("windows", {})["monitor_output_settings"] = json.dumps(displays)
        app.validate_backup_configuration(config)
        # An older file without General's value: the displays use the image's.
        older = deepcopy(config)
        older["output"].pop("display_render_scale", None)
        older["output"]["render_scale"] = 1.5
        app.validate_backup_configuration(older)
        # A display's own value must still be a real factor.
        damaged = deepcopy(config)
        damaged["windows"]["monitor_output_settings"] = json.dumps(
            {r"\\?\DISPLAY#HPN3678#7": {"width": 3840, "height": 0, "aspect_ratio": "16:9",
                                           "render_scale": "default"}})
        with self.assertRaises(ValueError):
            app.validate_backup_configuration(damaged)

    def test_saving_writes_both_values_and_hidden_eumetsat_controls_keep_theirs(self):
        self.load(app.replace_toml_values(self.template(), [("output", "render_scale", 1.5)]))
        base = {key: str(value) for key, value in (("zoom", app.ZOOM), ("width", app.WIDTH),
                                                    ("height", app.HEIGHT or 0))}
        values = self.form_values(base, render_scale="1.25", eumetsat_render_scale="default")
        updates = {(section, key): value for section, key, value
                   in app.normalize_settings_form_values(values, provider="eumetsat")}
        self.assertEqual((updates[("output", "render_scale")], updates[("output", "display_render_scale")]),
                         ("default", 1.25))
        updates = {(section, key): value for section, key, value
                   in app.normalize_settings_form_values(values, provider="goes_east")}
        self.assertNotIn(("output", "render_scale"), updates)
        self.assertEqual(updates[("output", "display_render_scale")], 1.25)

    def form_values(self, base, **changes):
        values = {
            "zoom": base["zoom"], "width": base["width"], "height": base["height"],
            "aspect_ratio": app.ASPECT_RATIO or "16:9", "background_color": app.BACKGROUND_COLOR,
            "position": app.WINDOWS_WALLPAPER_POSITION, "view_preset": app.VIEW_PRESET,
            "projection": app.PROJECTION, "fit_mode": app.VIEW_MODE,
            "truecolor_black_night": app.TRUECOLOR_BLACK_NIGHT, "set_wallpaper": app.SET_WINDOWS_WALLPAPER,
            "time_zone": app.DISPLAY_TIME_ZONE, "appearance": app.APPEARANCE,
            "update_interval_minutes": str(app.UPDATE_INTERVAL_MINUTES),
            "show_download_speed": True, "download_speed_unit": app.DOWNLOAD_SPEED_UNIT,
            "download_retries": str(app.DOWNLOAD_RETRIES), "catalogue_retries": str(app.CATALOGUE_RETRIES),
            "show_download_progress": True, "show_download_progress_bar": True,
            "keep_completed_download_visible": False, "history_enabled": True,
            "history_folder": app.CUSTOM_HISTORY_FOLDER, "retention_mode": app.HISTORY_RETENTION_MODE,
            "max_files": str(app.HISTORY_MAX_FILES), "years": "0", "months": "0", "days": "0",
            "hours": "0", "minutes": "0", "latest_folder": app.CUSTOM_LATEST_FOLDER,
        }
        values.update(changes)
        return values


if __name__ == "__main__":
    unittest.main()
