"""Regression checks for PNG provenance and settings-backup readability."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
from marblescape_image_metadata import embed_png_metadata


class ImageMetadataTests(unittest.TestCase):
    @staticmethod
    def png():
        output = io.BytesIO()
        Image.new("RGB", (8, 6), (12, 34, 56)).save(output, format="PNG")
        return output.getvalue()

    def test_png_metadata_survives_open_without_changing_pixels(self):
        original = self.png()
        record = {"software": "MarbleScape", "profile_name": "Whitsundays, Australien"}
        updated = embed_png_metadata(original, record)
        updated = embed_png_metadata(updated, record)
        with Image.open(io.BytesIO(updated)) as image:
            self.assertEqual(image.size, (8, 6))
            self.assertEqual(image.getpixel((0, 0)), (12, 34, 56))
            self.assertEqual(json.loads(image.text["MarbleScape"]), record)
        self.assertEqual(updated.count(b"MarbleScape\0"), 1)
        self.assertLess(len(updated) - len(original), 200)

    def test_private_copernicus_settings_never_enter_image(self):
        profile = copy.deepcopy(app.SOURCE_PROFILES["copernicus"])
        profile.update(mission="Sentinel-2", product="sample", layer="TRUE_COLOR",
                       latitude=-20.283, longitude=149.04, map_zoom=11,
                       date="latest", map_labels=False, coverage_mode="fill_gaps",
                       lookback_days=3, max_cloud_cover=30, brightness=100,
                       client_secret="never-embed-this")
        frame = {
            "profile": profile,
            "product": {"name": "Sentinel-2 L2A"},
            "layer": {"name": "True color", "data_type": "sentinel-2-l2a"},
            "date": "2026-09-24",
        }
        with patch.object(app, "IMAGE_SOURCE", "copernicus"):
            record = app.image_provenance(
                "copernicus", [{"frame": frame}], (2560, 1440),
                source_time="2026-09-24T10:00:00Z", profile_name="Whitsundays",
            )
        saved = embed_png_metadata(self.png(), record)
        self.assertNotIn(b"never-embed-this", saved)
        self.assertEqual(record["source_time_utc"], "2026-09-24T10:00:00Z")
        self.assertEqual(record["lookback_days"], 3)

    def test_mosaic_period_is_not_a_single_acquisition(self):
        profile = copy.deepcopy(app.SOURCE_PROFILES["copernicus"])
        frame = {
            "profile": profile,
            "product": {"name": "Sentinel-2 Quarterly Mosaics"},
            "layer": {"name": "True Color Cloudless", "date_granularity": "quarter"},
            "date": "2026-04-01",
        }
        with patch.object(app, "IMAGE_SOURCE", "copernicus"):
            record = app.image_provenance(
                "copernicus", [{"frame": frame}], (2560, 1440),
                source_time="2026-04-01T00:00:00Z",
            )
        self.assertEqual(record["mosaic_period"], "2026-Q2")
        self.assertNotIn("source_time_utc", record)

    def test_download_path_embeds_metadata_before_saving(self):
        frame = {"timestamp": "2026-09-24T10:00:00Z"}
        source = type("Source", (), {"fetch_image": lambda _self, *_args, **_kwargs: self.png()})()
        with patch.object(app, "IMAGE_SOURCE", "goes_east"), \
             patch.object(app, "get_noaa_client", return_value=source), \
             patch.object(app, "save_latest_image", return_value=Path("image.png")) as save, \
             patch.object(app, "record_source_frame_status"), \
             patch.object(app, "cleanup_history"):
            app._perform_update(
                "noaa", [{"frame": frame}], 8, 6, 8, 6,
                cache_configuration_signature={}, cache_source_signature=("test",),
                cache_source_time=frame["timestamp"],
            )
        with Image.open(io.BytesIO(save.call_args.args[0])) as image:
            record = json.loads(image.text["MarbleScape"])
            self.assertEqual(record["source"], "GOES-East")
            self.assertEqual(record["source_time_utc"], frame["timestamp"])
            self.assertEqual(image.getpixel((0, 0)), (12, 34, 56))


class SettingsBackupRoundtripTests(unittest.TestCase):
    def test_exported_backup_is_readable_and_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "marblescape_config.toml"
            config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
            profiles = root / "profiles.toml"
            profiles.write_text(app.serialize_library(app.normalize_library({})), encoding="utf-8")
            destination = root / "marblescape-settings-roundtrip.json"
            with patch.object(app, "ACTIVE_CONFIG_PATH", config), \
                 patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", profiles), \
                 patch.object(app, "is_windows_startup_enabled", return_value=False):
                saved = app.export_settings_backup(destination)
                restored_config, restored_profiles, startup = app.import_settings_backup(saved)
            self.assertEqual(restored_config.encode("utf-8"), config.read_bytes())
            self.assertEqual(restored_profiles, app.normalize_library({}))
            self.assertFalse(startup)
            tampered = json.loads(saved.read_text(encoding="utf-8"))
            tampered["configuration_toml"] += "\n# changed\n"
            saved.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                app.import_settings_backup(saved)


if __name__ == "__main__":
    unittest.main()
