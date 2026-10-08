"""Offline coverage, protected snapshot, PNG and backup round-trip regressions."""
from contextlib import ExitStack
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import marblescape_download as app
import marblescape_snapshot as snapshot
import marblescape_data_coverage as coverage
from marblescape_profile_transfer import import_profiles, export_profiles, read_png_record
from marblescape_download_progress import DownloadProgressTracker
from marblescape_copernicus import CopernicusClient
from marblescape_image_metadata import embed_png_metadata


class CoverageTests(unittest.TestCase):
    def test_binary_mask_counts_black_pixels_and_not_transparent_white(self):
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 255))
        image.putdata([(0, 0, 0, 255)] * 9842 + [(255, 255, 255, 0)] * 158)
        record = coverage.from_alpha(image)
        coverage.validate(record)
        self.assertEqual(coverage.label(record), "98.42%")
        self.assertEqual(record["data_coverage"]["valid_pixels"], 9842)
        for malformed in (dict(record, data_coverage_percent=True), dict(record, data_coverage_percent=100),
                          dict(record, data_coverage_percent=float("nan"))):
            with self.assertRaises(ValueError):
                coverage.validate(malformed)

    def test_unknown_and_fractional_alpha_are_not_claimed_complete(self):
        for mode, color in (("RGB", "black"), ("RGBA", (0, 0, 0, 128))):
            result = coverage.from_alpha(Image.new(mode, (2, 2), color))
            self.assertEqual(result, coverage.unavailable())
            self.assertEqual(coverage.label(result), "Not available")

    def test_map_background_and_labels_do_not_count_as_satellite_data(self):
        client = CopernicusClient("test", "test")
        image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        image.putpixel((0, 0), (0, 0, 0, 255))
        frame = {"profile": {"coverage_mode": "single", "map_labels": True},
                 "layer": {"evalscript": "return [r,g,b,s.dataMask];"}}
        with patch.object(client, "_render_satellite", return_value=(image, 100)), \
             patch.object(client, "_map_overlay", return_value=(Image.new("RGBA", (2, 2), "white"), 10)), \
             patch.object(client, "_draw_attribution"), \
             patch("marblescape_copernicus.DOWNLOAD_PROGRESS", DownloadProgressTracker()):
            client.fetch_image(frame)
        self.assertEqual(client.last_data_coverage["data_coverage_percent"], 25)


class SnapshotPipelineTests(unittest.TestCase):
    def setUp(self):
        self.previous = app.capture_loaded_configuration()
        self.addCleanup(lambda: app.restore_loaded_configuration(self.previous))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app, "PROFILE_LIBRARY_PATH", self.root / "profiles.toml"))
        self.config = self.root / "settings.toml"
        text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        text = app.replace_toml_values(text, [("output", "windows_root", self.root.as_posix()),
            ("output", "linux_root", self.root.as_posix()), ("output", "width", 8),
            ("output", "height", 6), ("output", "aspect_ratio", "4:3"), ("source", "provider", "goes_east")])
        self.config.write_text(text, encoding="utf-8", newline="")
        app.load_configuration(self.config)
        app.ensure_directories()
        self.stack.enter_context(patch.object(app, "log"))
        self.stack.enter_context(patch.object(app, "record_source_frame_status"))
        self.stack.enter_context(patch.object(app, "is_windows_startup_enabled", return_value=False))
        self.tracker = DownloadProgressTracker()
        self.stack.enter_context(patch.object(app, "DOWNLOAD_PROGRESS", self.tracker))
        self.stack.enter_context(patch.object(app.APPLICATION_STOP_EVENT, "is_set", return_value=False))
        self.stack.enter_context(patch.object(app.CONFIGURATION_RELOAD_EVENT, "is_set", return_value=False))
        self.client = Mock()
        self.stack.enter_context(patch.object(app, "get_noaa_client", return_value=self.client))

    def download(self, color="black"):
        data = io.BytesIO()
        Image.new("RGB", (8, 6), color).save(data, "PNG")
        self.client.fetch_image.return_value = data.getvalue()
        self.tracker.begin()
        return app._perform_update("noaa", [{"frame": {}}], 8, 6, 8, 6)

    def test_snapshot_updates_only_on_success_and_identical_images_keep_identity(self):
        self.assertIsNone(app.read_latest_snapshot())
        _, path, _ = self.download()
        first = app.read_latest_snapshot()
        record = read_png_record(path)
        self.assertEqual(record["profile_name"], snapshot.SYSTEM_NAME)
        self.assertEqual(record["profile_kind"], snapshot.SYSTEM_KIND)
        self.assertEqual(record["snapshot_id"], first["snapshot_id"])
        with Image.open(path) as image:
            self.assertEqual(json.loads(image.getexif()[270]), record)
        self.assertEqual(record["data_coverage_percent"], None)
        self.assertEqual(record["schema_version"], 4)
        self.download()
        self.assertEqual(app.read_latest_snapshot(), first)
        before = self.config.read_bytes()
        self.client.fetch_image.side_effect = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.download()
        self.assertEqual(self.config.read_bytes(), before)
        self.client.fetch_image.side_effect = None
        self.download("white")
        self.assertNotEqual(app.read_latest_snapshot()["snapshot_id"], first["snapshot_id"])

    def test_a_copernicus_picture_without_any_image_data_is_neither_stored_nor_shown(self):
        from marblescape_copernicus import DEFAULT_PROFILE, get_product, get_layer
        _, previous, _ = self.download("white")
        latest_before = sorted(app.LATEST_DIR.glob("*.png"))
        snapshot_before = app.read_latest_snapshot()
        profile = dict(DEFAULT_PROFILE, map_labels=False, scene_no_data_color="blur")
        product = get_product(profile["configuration"], "DEFAULT-THEME::a91f72")
        frame = {"profile": dict(profile, product=product["id"], layer="1_TRUE_COLOR", mission="Sentinel-2"),
                 "product": product, "layer": get_layer(product, "1_TRUE_COLOR"),
                 "date": "2026-09-28", "width": 16, "height": 12}
        client = CopernicusClient("test", "test")
        # Nothing passed the date and cloud limit: a fully transparent satellite picture.
        empty = Image.new("RGBA", (16, 12), (0, 0, 0, 0))
        with patch.object(app, "IMAGE_SOURCE", "copernicus"),              patch.object(app, "get_copernicus_client", return_value=client),              patch.object(client, "_render_satellite", return_value=(empty, 42)),              patch("marblescape_copernicus.DOWNLOAD_PROGRESS", self.tracker):
            self.tracker.begin()
            with self.assertRaises(app.NoImageData) as raised:
                app._perform_update("copernicus", [{"frame": frame}], 16, 12, 8, 6)
        self.assertIn("0% data coverage", str(raised.exception))
        # Not retried: another attempt gives the same empty picture.
        self.assertFalse(app.is_retryable_download_error(raised.exception))
        self.assertEqual(sorted(app.LATEST_DIR.glob("*.png")), latest_before)
        self.assertEqual(app.read_latest_snapshot(), snapshot_before)
        self.assertTrue(previous.is_file())

    def test_transparent_mosaic_survives_resize_publication_exif_and_transfer(self):
        from marblescape_copernicus import DEFAULT_PROFILE, get_product, get_layer
        profile = dict(DEFAULT_PROFILE, no_data_color="transparent", map_labels=False,
                       auto_brightness=True, auto_contrast=True, contrast=125)
        product = get_product(profile["configuration"], profile["product"])
        frame = {"profile": profile, "product": product, "layer": get_layer(product, profile["layer"]),
                 "date": "2026-04-01", "width": 16, "height": 12}
        client = CopernicusClient("test", "test")
        satellite = Image.new("RGBA", (16, 12), (0, 0, 0, 0))
        satellite.paste((12, 34, 56, 255), (0, 0, 8, 12))
        with patch.object(app, "IMAGE_SOURCE", "copernicus"), \
             patch.object(app, "get_copernicus_client", return_value=client), \
             patch.object(client, "_render_satellite", return_value=(satellite, 42)), \
             patch("marblescape_copernicus.DOWNLOAD_PROGRESS", self.tracker):
            self.tracker.begin()
            _, path, _ = app._perform_update("copernicus", [{"frame": frame}], 16, 12, 8, 6)
        record = read_png_record(path)
        with Image.open(path) as picture:
            self.assertEqual(picture.mode, "RGBA")
            self.assertEqual(picture.size, (8, 6))
            self.assertEqual(picture.getpixel((7, 0))[3], 0)
            self.assertEqual(picture.getpixel((0, 0))[3], 255)
            self.assertEqual(json.loads(picture.getexif()[270]), record)
        self.assertEqual(record["no_data_color"], "transparent")
        self.assertTrue(record["auto_brightness"])
        self.assertTrue(record["auto_contrast"])
        # Quarterly True Color uses tone rule c.
        self.assertEqual(record["mosaic_adjustments"]["algorithm"], "tone-rules-v1")
        self.assertEqual(record["mosaic_adjustments"]["rule"], "c")
        self.assertEqual(record["mosaic_adjustments"]["contrast_percent"], 125)
        self.assertEqual(record["image_size_selection"], "auto")
        self.assertEqual(record["data_coverage_percent"], 50.0)
        restored = import_profiles([path], [], app.default_import_settings, app.normalize_image_settings_snapshot)
        self.assertFalse(restored.warnings)
        self.assertEqual(restored.items[0]["settings"]["sources"]["copernicus"]["no_data_color"], "transparent")
        exports = export_profiles(self.root, restored.items, app.normalize_image_settings_snapshot)
        imported = import_profiles(exports, [], app.default_import_settings, app.normalize_image_settings_snapshot)
        self.assertEqual(imported.items, restored.items)
        self.assertFalse(imported.warnings)

    def test_regular_layer_with_tone_rule_records_and_imports_its_tones(self):
        from marblescape_copernicus import DEFAULT_PROFILE, get_product, get_layer
        profile = dict(DEFAULT_PROFILE, configuration="DEFAULT-THEME", mission="Sentinel-2",
                       product="DEFAULT-THEME::a91f72", layer="1_TRUE_COLOR", map_labels=False,
                       coverage_mode="single", auto_brightness=True, auto_contrast=True, contrast=110)
        product = get_product(profile["configuration"], profile["product"])
        frame = {"profile": profile, "product": product, "layer": get_layer(product, profile["layer"]),
                 "date": "2026-09-24", "width": 16, "height": 12}
        client = CopernicusClient("test", "test")
        satellite = Image.new("RGBA", (16, 12), (0, 0, 0, 0))
        satellite.paste((40, 60, 80, 255), (0, 0, 8, 12))
        satellite.paste((200, 205, 210, 255), (8, 0, 16, 12))
        with patch.object(app, "IMAGE_SOURCE", "copernicus"), \
             patch.object(app, "get_copernicus_client", return_value=client), \
             patch.object(client, "_render_satellite", return_value=(satellite, 42)), \
             patch("marblescape_copernicus.DOWNLOAD_PROGRESS", self.tracker):
            self.tracker.begin()
            _, path, _ = app._perform_update("copernicus", [{"frame": frame}], 16, 12, 8, 6)
        record = read_png_record(path)
        # Sentinel-2 L2A True color uses tone rule e and records the same tone fields as mosaics.
        self.assertEqual((record["mosaic_adjustments"]["rule"], record["mosaic_adjustments"]["algorithm"]),
                         ("e", "tone-rules-v1"))
        self.assertEqual(record["mosaic_adjustments"]["evalscript_brightness_percent"], 100)
        self.assertEqual((record["auto_brightness"], record["auto_contrast"], record["mosaic_contrast_percent"]),
                         (True, True, 110))
        restored = import_profiles([path], [], app.default_import_settings, app.normalize_image_settings_snapshot)
        self.assertFalse(restored.warnings)
        self.assertEqual(len(restored.items), 1)
        # A regular layer without a rule carries no tone metadata.
        stock = dict(profile, layer="2_FALSE_COLOR")
        frame = dict(frame, profile=stock, layer=get_layer(product, "2_FALSE_COLOR"))
        with patch.object(app, "IMAGE_SOURCE", "copernicus"), \
             patch.object(app, "get_copernicus_client", return_value=client), \
             patch.object(client, "_render_satellite", return_value=(satellite, 42)), \
             patch("marblescape_copernicus.DOWNLOAD_PROGRESS", self.tracker):
            self.tracker.begin()
            _, path, _ = app._perform_update("copernicus", [{"frame": frame}], 16, 12, 8, 6)
        record = read_png_record(path)
        self.assertNotIn("mosaic_adjustments", record)
        self.assertNotIn("auto_contrast", record)

    def test_png_and_json_import_are_normal_profiles_with_matching_uuid(self):
        _, path, _ = self.download()
        state = app.read_latest_snapshot()
        exports = export_profiles(self.root, [snapshot.export_item(state)], app.normalize_image_settings_snapshot)
        before = self.config.read_bytes()
        for source in (path, exports[0]):
            result = import_profiles([source], [], app.default_import_settings, app.normalize_image_settings_snapshot)
            self.assertEqual(result.warnings, [])
            self.assertEqual(result.items[0]["id"], state["profile_id"])
            self.assertEqual(result.items[0]["name"], snapshot.IMPORTED_NAME)
            self.assertNotIn("kind", result.items[0])
        self.assertEqual(self.config.read_bytes(), before)

    def test_both_backup_scopes_preserve_snapshot_and_reject_damaged_fields(self):
        self.download()
        state = app.read_latest_snapshot()
        for full in (False, True):
            file = self.root / f"backup-{full}.json"
            app.export_settings_backup(file, include_profiles=full)
            text, library, _startup = app.import_settings_backup(file, include_profiles=full)
            restored = snapshot.from_config(tomllib.loads(text), lambda value: app.strict_settings(value, app.normalize_image_settings_snapshot))
            self.assertEqual(restored, state)
            if library:
                self.assertNotIn(snapshot.SYSTEM_ID, [item["id"] for item in library["items"]])
        broken = deepcopy(state)
        del broken["settings"]["source"]
        config = tomllib.loads(self.config.read_text(encoding="utf-8"))
        config["latest_snapshot"]["record_json"] = json.dumps(broken)
        with self.assertRaises(ValueError):
            app.validate_backup_configuration(config)

    def test_named_download_does_not_replace_system_snapshot(self):
        self.download()
        before = app.read_latest_snapshot()
        settings = app.image_settings_snapshot()
        app.IMAGE_PROFILE_LIBRARY = app.normalize_library({"items": [{"id": "a" * 32, "name": "Named", "settings": settings}]})
        app.APPLIED_PROFILE_ID = "a" * 32
        _, path, _ = self.download("red")
        self.assertEqual(app.read_latest_snapshot(), before)
        record = read_png_record(path)
        self.assertEqual(record["profile_name"], "Named")
        self.assertNotIn("snapshot_id", record)

    def test_unwritable_snapshot_reports_warning_without_claiming_download_failure(self):
        with patch.object(app, "save_latest_snapshot", side_effect=PermissionError("read only")):
            _, path, _ = self.download()
        self.assertTrue(path.is_file())
        self.assertIsNone(app.read_latest_snapshot())
        self.assertIn("read only", self.tracker.snapshot()["warning"])

    def test_png_import_rejects_tampered_coverage_and_system_kind(self):
        _, path, _ = self.download()
        original = read_png_record(path)
        for fields in ({"data_coverage_percent": 100}, {"profile_kind": "normal"},
                       {"snapshot_id": "broken"}):
            record = dict(original, **fields)
            invalid = self.root / "invalid.png"
            invalid.write_bytes(embed_png_metadata(path.read_bytes(), record))
            result = import_profiles([invalid], [], app.default_import_settings, app.normalize_image_settings_snapshot)
            self.assertEqual(result.items, [])
            self.assertEqual(len(result.warnings), 1)

    def test_settings_only_restore_keeps_snapshot_without_a_local_image(self):
        _, path, _ = self.download()
        expected = app.read_latest_snapshot()
        file = self.root / "backup.json"
        app.export_settings_backup(file, include_profiles=False)
        text, library, _ = app.import_settings_backup(file, include_profiles=False)
        self.assertIsNone(library)
        path.unlink()
        self.config.write_text(text, encoding="utf-8", newline="")
        app.load_configuration(self.config)
        self.assertEqual(app.read_latest_snapshot(), expected)
        from marblescape_published_images import PublishedImageIndex
        self.assertEqual(PublishedImageIndex().scan(app.published_image_folders()), {})
