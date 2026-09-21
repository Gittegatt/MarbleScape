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
        self.assertEqual(
            Path(root.iconbitmap.call_args.kwargs["default"]).name,
            "marblescape.ico",
        )
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


class ImageSnapshotValidationTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()
        with patch.object(app, "log"):
            app.load_configuration(app.DEFAULT_CONFIG_TEMPLATE_PATH)
        self.snapshot = app.image_settings_snapshot()

    def tearDown(self):
        app.restore_loaded_configuration(self.saved_configuration)

    def test_missing_render_quality_and_other_required_fields_are_rejected(self):
        for section, key in (("output", "render_scale"), ("output", "width"),
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
            ("view", "show_extended_projections", "false"),
            ("view", "truecolor_black_night", 1),
            ("view", "bbox", [0, 0, float("inf"), 10]),
            ("view", "bbox", [10, 0, 0, 10]),
            ("view", "bbox", [0, 0, 10]),
            ("output", "width", True),
            ("output", "width", 1024.5),
            ("output", "height", -1),
            ("output", "render_scale", float("inf")),
            ("output", "render_scale", True),
            ("output", "render_scale", None),
            ("output", "background_color", []),
            ("output", "latest_folder", "bad\x00path"),
            ("output", "aspect_ratio", "0:1"),
        ]
        before = app.capture_loaded_configuration()
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                invalid = deepcopy(self.snapshot)
                invalid[section][key] = value
                with self.assertRaises(ValueError):
                    app.normalize_image_settings_snapshot(invalid)
                self.assertEqual(app.capture_loaded_configuration(), before)

    def test_dimensions_and_missing_ratio_are_resolved_from_snapshot_only(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["output"].update(width=1440, height=900, aspect_ratio="")
        app.WIDTH, app.HEIGHT, app.ASPECT_RATIO = 1, None, "invalid global ratio"
        with patch.object(app, "get_output_dimensions", side_effect=AssertionError("Read runtime dimensions")), \
             patch.object(app, "apply_image_settings", side_effect=AssertionError("Mutated runtime")):
            result = app.normalize_image_settings_snapshot(snapshot)
        self.assertEqual(result["output"]["aspect_ratio"], "")
        self.assertEqual(result["output"]["width"], 1440)
        snapshot["output"]["height"] = 0
        with self.assertRaises(ValueError):
            app.normalize_image_settings_snapshot(snapshot)
        snapshot["output"].update(height=900, aspect_ratio="16:9")
        with self.assertRaises(ValueError):
            app.normalize_image_settings_snapshot(snapshot)

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
            app.FORCE_UPDATE_EVENT.clear()
            context.variables["position"].set("tile")
            after = self.apply(context)
            self.assertTrue(app.CONFIGURATION_RELOAD_EVENT.is_set())
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
            for section in ("source", "sources", "view", "output", "layers"):
                self.assertEqual(after[section], before[section], section)
            expected = deepcopy(before)
            expected["windows"]["position"] = "tile"
            after.pop("image_profiles", None)
            expected.pop("image_profiles", None)
            self.assertEqual(after, expected)
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
            invalid["output"]["width"] = "not a dimension"
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
