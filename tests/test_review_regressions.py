"""Regressions found during the independent image-profile review."""

from copy import deepcopy
import os
from pathlib import Path
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import marblescape_download as app
import test_settings_profiles_integration as settings_tests


class ApplicationIconTests(unittest.TestCase):
    def test_tk_windows_use_all_sphere_sizes_and_native_windows_ico(self):
        root = Mock()

        with patch("tkinter.PhotoImage", side_effect=lambda master, file: Path(file).name), \
             patch.object(app.os, "name", "nt"):
            app.apply_tk_window_icon(root)

        expected = [
            "marblescape_256.png", "marblescape_64.png", "marblescape_48.png",
            "marblescape_32.png", "marblescape_16.png",
        ]
        root.iconphoto.assert_called_once_with(True, *expected)
        # A default .ico combined with iconphoto(True) shows the generic icon.
        self.assertNotIn("default", root.iconbitmap.call_args.kwargs)
        self.assertEqual(Path(root.iconbitmap.call_args.args[0]).name, "marblescape.ico")
        self.assertEqual(root._marblescape_window_icons, expected)

    def test_windows_app_identity_is_stable(self):
        setter = Mock(return_value=0)
        windll = SimpleNamespace(shell32=SimpleNamespace(
            SetCurrentProcessExplicitAppUserModelID=setter
        ))
        with patch.object(app.os, "name", "nt"), \
             patch.object(app.ctypes, "windll", windll):
            self.assertTrue(app.set_windows_app_user_model_id())
        setter.assert_called_once_with("Gittegatt.MarbleScape")


class ConfigurationTextEditTests(unittest.TestCase):
    def test_added_key_goes_directly_below_the_section_header(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                text = newline.join((
                    "[view]", "# First comment line,", "# second comment line.",
                    'preset = "full_earth"', "", "[history]", "enabled = true", "",
                ))
                updated = app.replace_toml_section_value(text, "view", "bbox", [])
                self.assertEqual(updated.split(newline)[:4], [
                    "[view]", "bbox = []", "# First comment line,", "# second comment line.",
                ])
                self.assertEqual(tomllib.loads(updated)["view"]["bbox"], [])

    def test_comment_on_the_header_line_stays_on_that_line(self):
        text = "[view] # Map extent\n\n# Explanation\npreset = \"full_earth\"\n"
        updated = app.replace_toml_section_value(text, "view", "zoom", 1.0)
        self.assertTrue(updated.startswith("[view] # Map extent\nzoom = 1.0\n\n# Explanation\n"))


class CustomAreaTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()
        with patch.object(app, "log"):
            app.load_configuration(app.DEFAULT_CONFIG_TEMPLATE_PATH)

    def tearDown(self):
        app.restore_loaded_configuration(self.saved_configuration)

    @staticmethod
    def fields(latitude, longitude):
        return {"custom_latitude": latitude, "custom_longitude": longitude}

    def test_centre_and_zoom_become_an_area_at_the_output_ratio(self):
        bbox, zoom = app.custom_area_from_form(self.fields("51", "10,5°"), "Geographic", 24.0, 16 / 9)
        self.assertEqual(zoom, 1.0)
        self.assertEqual(bbox, [3.0, 46.78125, 18.0, 55.21875])
        self.assertAlmostEqual((bbox[2] - bbox[0]) / (bbox[3] - bbox[1]), 16 / 9)
        latitude, longitude, shown_zoom = app.custom_area_center(bbox, zoom)
        self.assertAlmostEqual(latitude, 51.0)
        self.assertAlmostEqual(longitude, 10.5)
        self.assertAlmostEqual(shown_zoom, 24.0)

    def test_area_is_moved_onto_the_map_and_limited_to_its_size(self):
        self.assertEqual(app.custom_area_extent(85.0, 179.0, 4.0, 16 / 9), [90.0, 39.375, 180.0, 90.0])
        self.assertEqual(app.custom_area_extent(-89.0, -179.0, 4.0, 16 / 9), [-180.0, -90.0, -90.0, -39.375])
        # Zoom 1 at 16:9 would be taller than the map: the full map height is used.
        self.assertEqual(app.custom_area_extent(0.0, 0.0, 1.0, 16 / 9), [-160.0, -90.0, 160.0, 90.0])
        self.assertEqual(app.custom_area_extent(0.0, 0.0, 0.5, 3.0), [-180.0, -60.0, 180.0, 60.0])

    def test_invalid_centre_and_zoom_explain_what_to_change(self):
        cases = (
            (("", "10"), 5.0, "Latitude must be a number"),
            (("51", "east"), 5.0, "Longitude must be a number"),
            (("nan", "10"), 5.0, "Latitude must be a number"),
            (("-91", "10"), 5.0, "Latitude must be between -90 and 90"),
            (("51", "180.5"), 5.0, "Longitude must be between -180 and 180"),
            (("51", "10"), 1001.0, "Zoom can be at most 1000"),
        )
        for values, zoom, message in cases:
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, message):
                app.custom_area_from_form(self.fields(*values), "Geographic", zoom, 16 / 9)
        with self.assertRaisesRegex(ValueError, "uses the Geographic projection"):
            app.custom_area_from_form(self.fields("51", "10"), "GEOS: MSG FES, MTG FD", 5.0, 16 / 9)

    def test_an_unchanged_saved_area_and_its_zoom_are_kept_exactly(self):
        saved = [-3000000.0, -2000000.0, 3000000.0, 2000000.0]
        self.assertEqual(app.custom_area_from_form({"custom_bbox": saved}, "GEOS: MSG FES, MTG FD", 1.5, 16 / 9),
                         (saved, 1.5))
        with self.assertRaisesRegex(ValueError, "saved custom area is invalid"):
            app.custom_area_from_form({"custom_bbox": [1.0, 2.0, 0.0, 4.0]}, "Geographic", 1.0, 16 / 9)

    def test_profile_table_shows_the_centre_in_the_lat_and_long_columns(self):
        import marblescape_profile_settings as profile_ui
        settings = {"source": {"provider": "eumetsat"}, "sources": {"eumetsat": {}},
                    "view": {"preset": "custom", "projection": "Geographic",
                             "bbox": [3.0, 46.78125, 18.0, 55.21875]}}
        self.assertEqual(profile_ui._profile_latitude(settings), "51")
        self.assertEqual(profile_ui._profile_longitude(settings), "10.5")
        self.assertEqual(profile_ui._profile_location(settings), "Custom area")
        settings["view"].update(projection="GEOS: MSG FES, MTG FD", bbox=[-3e6, -2e6, 3e6, 2e6])
        self.assertEqual(profile_ui._profile_latitude(settings), "-")
        self.assertTrue(profile_ui._profile_location(settings).startswith("Custom area · "))

    def test_without_form_fields_the_saved_area_is_kept(self):
        with patch.object(app, "CUSTOM_BBOX", (1.0, 2.0, 3.0, 4.0)):
            self.assertEqual(app.custom_area_from_form({}, "Geographic", 1.1, 16 / 9), ([1.0, 2.0, 3.0, 4.0], 1.1))
        with patch.object(app, "CUSTOM_BBOX", None), self.assertRaisesRegex(ValueError, "needs Latitude"):
            app.custom_area_from_form({}, "Geographic", 1.0, 16 / 9)

    def test_fit_must_stay_on_the_world_map_for_every_output_ratio(self):
        germany = [5.5, 47.0, 15.5, 55.5]
        app.validate_custom_area_fits_outputs(germany, "fit", 1.0, {16 / 9, 9 / 16})
        world = [-180.0, -90.0, 180.0, 90.0]
        app.validate_custom_area_fits_outputs(world, "crop", 1.0, {16 / 9, 21 / 9})
        with self.assertRaisesRegex(ValueError, "beyond the edge of the world map"):
            app.validate_custom_area_fits_outputs(world, "fit", 1.0, {16 / 9})
        app.validate_custom_area_fits_outputs([-25.0, 30.0, 45.0, 72.0], "fit", 1.0, {16 / 9})
        # A portrait monitor needs more latitude around a wide area.
        with self.assertRaisesRegex(ValueError, "Choose Crop"):
            app.validate_custom_area_fits_outputs([-25.0, 30.0, 45.0, 72.0], "fit", 1.0, {16 / 9, 9 / 16})

    def test_saved_geographic_area_must_stay_on_the_world_map(self):
        snapshot = app.image_settings_snapshot()
        snapshot["view"].update(preset="custom", projection="Geographic", bbox=[-10.0, -5.0, 10.0, 5.0])
        app.normalize_image_settings_snapshot(snapshot)
        snapshot["view"]["bbox"] = [-10.0, -5.0, 190.0, 5.0]
        with self.assertRaisesRegex(ValueError, "custom area must stay within"):
            app.normalize_image_settings_snapshot(snapshot)
        with patch.object(app, "VIEW_PRESET", "custom"), \
             patch.object(app, "PROJECTION", "Geographic"), \
             patch.object(app, "CUSTOM_BBOX", (-10.0, -95.0, 10.0, 5.0)), \
             self.assertRaisesRegex(ValueError, "within longitude -180 to 180"):
            app.validate_wms_configuration()

    def test_fitted_extent_matches_the_downloaded_bbox(self):
        projection = app.PROJECTIONS["Geographic"]
        with patch.object(app, "VIEW_MODE", "fit"), patch.object(app, "ZOOM", 1.1):
            _bbox, extent = app.calculate_bbox(projection, 16 / 9, (-25.0, 30.0, 45.0, 72.0))
        self.assertEqual(extent, app.fitted_view_extent((-25.0, 30.0, 45.0, 72.0), 16 / 9, "fit", 1.1))
        with patch.object(app, "VIEW_MODE", "fit"), patch.object(app, "ZOOM", 1.0), \
             self.assertRaisesRegex(ValueError, "exceeds valid longitude/latitude"):
            app.calculate_bbox(projection, 16 / 9, (-180.0, -90.0, 180.0, 90.0))


class ImageSnapshotValidationTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()
        with patch.object(app, "log"):
            app.load_configuration(app.DEFAULT_CONFIG_TEMPLATE_PATH)
        self.snapshot = app.image_settings_snapshot()

    def tearDown(self):
        app.restore_loaded_configuration(self.saved_configuration)

    def test_missing_render_quality_and_other_required_fields_are_rejected(self):
        for section, key in (("output", "render_scale"),
                             ("view", "zoom"), ("view", "bbox"), ("source", "provider")):
            with self.subTest(section=section, key=key):
                invalid = deepcopy(self.snapshot)
                del invalid[section][key]
                with self.assertRaises(ValueError):
                    app.normalize_image_settings_snapshot(invalid)
        for section in ("source", "sources", "view", "output", "layers"):
            with self.subTest(section=section):
                invalid = deepcopy(self.snapshot)
                del invalid[section]
                with self.assertRaises(ValueError):
                    app.normalize_image_settings_snapshot(invalid)

    def test_malformed_or_nonfinite_image_values_fail_before_runtime_mutation(self):
        cases = [
            ("source", "provider", []),
            ("view", "zoom", float("nan")),
            ("view", "zoom", True),
            ("view", "fit_mode", "invalid"),
            ("view", "projection", "unknown projection"),
            ("view", "preset", "unknown preset"),
            ("view", "truecolor_black_night", 1),
            ("view", "bbox", [0, 0, float("inf"), 10]),
            ("view", "bbox", [10, 0, 0, 10]),
            ("view", "bbox", [0, 0, 10]),
            ("output", "render_scale", float("inf")),
            ("output", "render_scale", True),
            ("output", "render_scale", None),
        ]
        before = app.capture_loaded_configuration()
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                invalid = deepcopy(self.snapshot)
                invalid[section][key] = value
                with self.assertRaises(ValueError):
                    app.normalize_image_settings_snapshot(invalid)
                self.assertEqual(app.capture_loaded_configuration(), before)

    def test_device_output_settings_of_older_profiles_are_dropped_unchecked(self):
        snapshot = deepcopy(self.snapshot)
        # Output size, background and the Latest folder are device settings:
        # an older profile's values are ignored, even malformed ones.
        snapshot["output"].update(width=True, height=-1, aspect_ratio="0:1",
                                  background_color=[], latest_folder="bad\x00path")
        with patch.object(app, "get_output_dimensions", side_effect=AssertionError("Read runtime dimensions")), \
             patch.object(app, "apply_image_settings", side_effect=AssertionError("Mutated runtime")):
            result = app.normalize_image_settings_snapshot(snapshot)
        self.assertEqual(result["output"], {"render_scale": self.snapshot["output"]["render_scale"]})
        self.assertEqual(self.snapshot["output"].keys(), {"render_scale"})

    def test_valid_custom_bbox_and_unknown_metadata_are_copied_and_preserved(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["view"].update(preset="custom", projection="Geographic", bbox=[-10.0, -5.0, 10.0, 5.0])
        snapshot["view"]["metadata"] = {"description": "Saved custom area"}
        snapshot["layers"][0]["metadata"] = {"attribution": "Fixture", "labels": ["one", "two"]}
        snapshot["output"]["metadata"] = {"purpose": "Example profile"}
        original = deepcopy(snapshot)
        result = app.normalize_image_settings_snapshot(snapshot)
        self.assertEqual(snapshot, original)
        self.assertEqual(result, original)
        result["layers"][0]["metadata"]["labels"].append("changed")
        self.assertEqual(snapshot, original)

    def test_noaa_preserves_unused_wms_values_but_eumetsat_rejects_them(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["source"]["provider"] = "solar"
        snapshot["view"].update(projection="External projection", preset="external_preset")
        snapshot["layers"] = [{"kind": "external", "metadata": {"owner": "Another version"}}]
        result = app.normalize_image_settings_snapshot(snapshot)
        self.assertEqual(result["view"], snapshot["view"])
        self.assertEqual(result["layers"], snapshot["layers"])
        snapshot["source"]["provider"] = "eumetsat"
        with self.assertRaises(ValueError):
            app.normalize_image_settings_snapshot(snapshot)

    def test_eumetsat_requires_valid_enabled_layers_and_custom_bbox(self):
        for changes in ({"enabled": "false"}, {"opacity": float("nan")}, {"style": {}}, {"name": ""}):
            snapshot = deepcopy(self.snapshot)
            active = next(layer for layer in snapshot["layers"] if layer.get("kind") == "wms")
            active.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                app.normalize_image_settings_snapshot(snapshot)
        snapshot = deepcopy(self.snapshot)
        snapshot["view"].update(preset="custom", bbox=[])
        with self.assertRaises(ValueError):
            app.normalize_image_settings_snapshot(snapshot)
        snapshot = deepcopy(self.snapshot)
        for layer in snapshot["layers"]:
            layer["enabled"] = False
        with self.assertRaises(ValueError):
            app.normalize_image_settings_snapshot(snapshot)

    def test_replacing_profile_layers_preserves_nested_layer_metadata(self):
        original = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8").rstrip()
        original += '\n\n[layers.metadata]\ncredit = "Fixture attribution"\n'
        parsed = tomllib.loads(original)
        snapshot = deepcopy(self.snapshot)
        snapshot["layers"] = deepcopy(parsed["layers"])
        updated = app.replace_image_settings(original, snapshot)
        after = tomllib.loads(updated)
        self.assertEqual(after["layers"], parsed["layers"])
        for section in ("service", "windows", "history"):
            self.assertEqual(after[section], parsed[section])


@unittest.skipUnless(os.name == "nt", "Windows real Settings form regression")
class ProfileFormReviewTests(unittest.TestCase):
    descendants = staticmethod(settings_tests.SettingsProfilesIntegrationTests.descendants)
    wait_for_source = settings_tests.SettingsProfilesIntegrationTests.wait_for_source
    apply = settings_tests.SettingsProfilesIntegrationTests.apply
    run_dialog = settings_tests.SettingsProfilesIntegrationTests.run_dialog

    def test_position_only_apply_requests_reload_without_forcing_image_download(self):
        def scenario(context):
            before = tomllib.loads(context.config.read_text(encoding="utf-8"))
            app.CONFIGURATION_RELOAD_EVENT.clear()
            app.SETTINGS_ONLY_RELOAD_EVENT.clear()
            app.FORCE_UPDATE_EVENT.clear()
            context.variables["position"].set("tile")
            after = self.apply(context)
            self.assertTrue(app.CONFIGURATION_RELOAD_EVENT.is_set())
            self.assertTrue(app.SETTINGS_ONLY_RELOAD_EVENT.is_set())
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
            for section in ("source", "sources", "view", "output", "layers"):
                self.assertEqual(after[section], before[section], section)
            expected = deepcopy(before)
            expected["windows"]["position"] = "tile"
            expected["windows"]["paused_displays"] = "[]"
            # Apply now persists all current table widths, even at their defaults.
            expected["profile_list"]["column_widths"] = context.profiles.get_column_widths()
            expected["profile_list"]["column_order"] = list(context.profiles.get_column_order())
            # Saving upgrades the table preferences, adding the History,
            # Country borders and Updates columns.
            expected["profile_list"]["columns_version"] = 19
            expected["profile_list"]["visible_columns"] = list(context.profiles.get_visible_columns())
            self.assertIn("history", expected["profile_list"]["visible_columns"])
            # Saving General also writes the window appearance, by default System.
            expected.setdefault("display", {})["appearance"] = "system"
            after.pop("image_profiles", None)
            expected.pop("image_profiles", None)
            self.assertEqual(after, expected)
        # With one display its position is the shared one.
        with patch.object(app, "list_windows_wallpaper_monitors",
                          return_value=[{"id": "DISPLAY-ONLY", "rect": (0, 0, 1920, 1080)}]):
            self.run_dialog(scenario)

    def test_apply_image_saves_only_image_tab_and_requests_image_reload(self):
        def scenario(context):
            before = tomllib.loads(context.config.read_text(encoding="utf-8"))
            app.CONFIGURATION_RELOAD_EVENT.clear()
            app.SETTINGS_ONLY_RELOAD_EVENT.set()
            app.FORCE_UPDATE_EVENT.clear()
            context.variables["zoom"].set("1.25")
            context.variables["time_zone"].set("UTC")
            after = self.apply(context, tab="Image")
            self.assertEqual(after["view"]["zoom"], 1.25)
            # Apply Image keeps the time zone draft; only the tab used last is saved at once.
            self.assertEqual(after["display"], {**before["display"], "settings_tab": "Image"})
            self.assertEqual(after["output"], before["output"])
            self.assertTrue(app.CONFIGURATION_RELOAD_EVENT.is_set())
            self.assertFalse(app.SETTINGS_ONLY_RELOAD_EVENT.is_set())
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())

        self.run_dialog(scenario)

    def test_incomplete_profile_load_leaves_form_and_source_selection_untouched(self):
        def scenario(context):
            before = context.profiles._capture_settings()
            generation = context.source._generation
            invalid = deepcopy(before)
            invalid["view"]["zoom"] = 3.2
            del invalid["output"]["render_scale"]
            with self.assertRaises(ValueError):
                context.profiles._on_load(invalid)
            self.assertEqual(context.profiles._capture_settings(), before)
            self.assertEqual(context.source._generation, generation)
            self.assertEqual(context.form["image_form_state"]["base"], before)
        self.run_dialog(scenario)

    def test_invalid_profile_value_does_not_partially_replace_the_form(self):
        def scenario(context):
            before = context.profiles._capture_settings()
            invalid = deepcopy(before)
            invalid["view"]["zoom"] = 3.2
            invalid["output"]["render_scale"] = "not a factor"
            with self.assertRaises(ValueError):
                context.profiles._on_load(invalid)
            self.assertEqual(context.profiles._capture_settings(), before)
        self.run_dialog(scenario)

    def test_custom_bbox_profile_survives_load_and_normal_apply(self):
        def scenario(context):
            snapshot = context.profiles._capture_settings()
            snapshot["view"].update(preset="custom", projection="Geographic",
                                    bbox=[-10.0, -5.0, 10.0, 5.0], fit_mode="crop")
            snapshot["layers"][0]["metadata"] = {"attribution": "Fixture", "labels": ["preserved"]}
            context.profiles._on_load(snapshot)
            self.wait_for_source(context)
            self.assertEqual(context.profiles._capture_settings(), snapshot)
            after = self.apply(context)
            self.assertEqual(after["view"]["bbox"], snapshot["view"]["bbox"])
            self.assertEqual(after["layers"], snapshot["layers"])
        self.run_dialog(scenario)

    def test_noaa_profile_keeps_unavailable_latent_wms_preset_and_projection(self):
        def scenario(context):
            snapshot = context.profiles._capture_settings()
            snapshot["source"]["provider"] = "solar"
            snapshot["view"].update(projection="External projection", preset="external_preset")
            context.profiles._on_load(snapshot)
            self.wait_for_source(context)
            self.assertEqual(context.variables["projection"].get(), "External projection")
            self.assertEqual(context.profiles._capture_settings(), snapshot)
            after = self.apply(context)
            self.assertEqual(after["view"]["projection"], "External projection")
            self.assertEqual(after["view"]["preset"], "external_preset")
        self.run_dialog(scenario)


if __name__ == "__main__":
    unittest.main()
