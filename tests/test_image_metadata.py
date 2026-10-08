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

    def test_source_resolution_names_the_source_picture_behind_the_png(self):
        self.assertEqual(app.picture_source_resolution(
            "noaa", [{"frame": {"resolution": "5424x5424"}}], (3840, 2160)), "5424x5424")
        # CIRA SLIDER: the visible part of the tile grid, without its padding.
        self.assertEqual(app.picture_source_resolution(
            "slider", [{"frame": {"resolution": "4000x4000", "content_box": [0.1, 0, 0.9, 1]}}],
            (3840, 2160)), "3200x4000")
        # EUMETSAT renders its WMS picture at the render size.
        self.assertEqual(app.picture_source_resolution("server", [], (5760, 3240)), "5760x3240")
        # Copernicus' PNG is its source picture; an unknown size is left out.
        self.assertIsNone(app.picture_source_resolution("copernicus", [{"frame": {}}], (2560, 1440)))
        self.assertIsNone(app.picture_source_resolution("worldview", [{"frame": {"resolution": ""}}], (1, 1)))

    def test_png_metadata_survives_open_without_changing_pixels(self):
        original = self.png()
        record = {"software": "MarbleScape", "profile_name": "Whitsundays, Australien"}
        updated = embed_png_metadata(original, record)
        updated = embed_png_metadata(updated, record)
        with Image.open(io.BytesIO(updated)) as image:
            self.assertEqual(image.size, (8, 6))
            self.assertEqual(image.getpixel((0, 0)), (12, 34, 56))
            self.assertEqual(json.loads(image.text["MarbleScape"]), record)
            self.assertEqual(json.loads(image.getexif()[270]), record)
            self.assertEqual(image.getexif()[305], "MarbleScape")
        self.assertEqual(updated.count(b"iTXtMarbleScape\0"), 1)
        self.assertEqual(updated.count(b"eXIf"), 1)
        self.assertLess(len(updated) - len(original), 500)

    def test_private_copernicus_settings_never_enter_image(self):
        profile = copy.deepcopy(app.SOURCE_PROFILES["copernicus"])
        profile.update(mission="Sentinel-2", product="DEFAULT-THEME::a91f72", layer="1_TRUE_COLOR",
                       latitude=-20.283, longitude=149.04, map_zoom=11,
                       date="latest", map_labels=False, coverage_mode="fill_gaps",
                       lookback_days=3, max_cloud_cover=30, brightness=100,
                       scene_no_data_color="blur", client_secret="never-embed-this")
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
        # A regular layer records its own No-data choice; the mosaic color does not apply.
        self.assertEqual(record["scene_no_data_color"], "blur")
        self.assertNotIn("no_data_color", record)

    def test_auto_recommendation_records_its_choice(self):
        profile = copy.deepcopy(app.SOURCE_PROFILES["copernicus"])
        profile.update(mission="Sentinel-2", product="DEFAULT-THEME::a91f72", layer="1_TRUE_COLOR",
                       coverage_mode="fill_gaps", lookback_days=30, max_cloud_cover=20,
                       auto_recommendation=True, auto_priority="fewest_clouds")
        frame = {"profile": profile, "product": {"name": "Sentinel-2 L2A"},
                 "layer": {"name": "True color", "data_type": "sentinel-2-l2a",
                           "data_filter": {"maxCloudCoverage": 30}}, "date": "2026-09-24",
                 "auto_choice": "Max. cloud cover 20% · Fill gaps, 30 days"}
        with patch.object(app, "IMAGE_SOURCE", "copernicus"):
            record = app.image_provenance("copernicus", [{"frame": frame}], (2560, 1440))
        self.assertEqual((record["auto_recommendation"], record["auto_priority"], record["auto_precise"],
                          record["auto_choice"]),
                         (True, "fewest_clouds", False, "Max. cloud cover 20% · Fill gaps, 30 days"))
        self.assertEqual(record["max_cloud_cover_percent"], 20)
        profile["auto_recommendation"] = False
        with patch.object(app, "IMAGE_SOURCE", "copernicus"):
            record = app.image_provenance("copernicus", [{"frame": frame}], (2560, 1440))
        self.assertNotIn("auto_choice", record)

    def test_auto_recommendation_renders_the_choice_or_the_saved_settings(self):
        import marblescape_copernicus_advice as advice
        saved = dict(app.SOURCE_PROFILES["copernicus"], auto_recommendation=True)
        rendered = dict(saved, date="latest")
        client = type("Client", (), {"latest": lambda _self, value, size, reference_date=None: {
            "profile": value, "timestamp": "t"}})()
        with patch.dict(app.SOURCE_PROFILES, {"copernicus": saved}), \
                patch.object(app, "get_copernicus_client", return_value=client), \
                patch.object(app, "log"), \
                patch.object(advice, "recommended_profile", return_value=(rendered, "Latest available (2026 Q2)")):
            frame = app.get_copernicus_frame((1920, 1080))
        self.assertIs(frame["profile"], rendered)
        self.assertEqual(frame["auto_choice"], "Latest available (2026 Q2)")
        with patch.dict(app.SOURCE_PROFILES, {"copernicus": saved}), \
                patch.object(app, "get_copernicus_client", return_value=client), \
                patch.object(app, "log") as log, \
                patch.object(advice, "recommended_profile", side_effect=RuntimeError("offline")):
            frame = app.get_copernicus_frame((1920, 1080))
        self.assertIs(frame["profile"], saved)
        self.assertEqual(frame["auto_choice"], "Saved settings")
        self.assertIn("offline", log.call_args.args[0])
        configuration = app.capture_loaded_configuration()
        configuration.update(IMAGE_SOURCE="copernicus",
                             SOURCE_PROFILES={**app.SOURCE_PROFILES, "copernicus": saved})
        self.assertTrue(app.image_configuration_key(configuration)["selection"]["auto_recommendation"])

    def test_mosaic_period_is_not_a_single_acquisition(self):
        profile = copy.deepcopy(app.SOURCE_PROFILES["copernicus"])
        profile["date_mode"] = "relative_quarter"
        profile["quarter_offset"] = 1
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
        self.assertEqual(record["no_data_color"], profile["no_data_color"])
        self.assertNotIn("scene_no_data_color", record)
        self.assertEqual(record["map_label_color"], profile["map_label_color"])
        self.assertEqual(record["profile_settings"]["sources"]["copernicus"]["no_data_color"],
                         profile["no_data_color"])
        self.assertEqual(
            record["profile_settings"]["sources"]["copernicus"]["map_label_color"],
            profile["map_label_color"],
        )
        self.assertEqual(record["date_mode"], "relative_quarter")
        self.assertEqual(record["quarter_offset"], 1)
        self.assertNotIn("source_time_utc", record)
        with Image.open(io.BytesIO(embed_png_metadata(self.png(), record))) as image:
            self.assertEqual(json.loads(image.getexif()[270]), record)
        profile["date_mode"] = "catalogue"
        profile["quarter_offset"] = 0
        profile["date"] = "2026-04-01"
        with patch.object(app, "IMAGE_SOURCE", "copernicus"):
            fixed = app.image_provenance("copernicus", [{"frame": frame}], (2560, 1440))
        self.assertEqual((fixed["date_mode"], fixed["quarter_offset"], fixed["mosaic_period"]),
                         ("catalogue", 0, "2026-Q2"))

    def test_download_path_embeds_metadata_before_saving(self):
        from marblescape_cache import ProfileImageCache
        import marblescape_snapshot as latest_snapshot

        frame = {"timestamp": "2026-09-24T10:00:00Z"}
        source = type("Source", (), {"fetch_image": lambda _self, *_args, **_kwargs: self.png()})()
        # The picture without a profile also goes to the cache: never the real one.
        folder = tempfile.TemporaryDirectory(prefix="marblescape-metadata-cache-")
        self.addCleanup(folder.cleanup)
        cache = ProfileImageCache(folder.name)
        with patch.object(app, "IMAGE_SOURCE", "goes_east"),              patch.object(app, "APPLIED_PROFILE_ID", ""),              patch.object(app, "get_profile_cache", return_value=cache), \
             patch.object(app, "get_noaa_client", return_value=source), \
             patch.object(app, "save_latest_image", return_value=Path("image.png")) as save, \
             patch.object(app, "save_latest_snapshot") as snapshot_save, \
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
        self.assertEqual(cache.entries()[latest_snapshot.CACHE_ID]["source_time"], frame["timestamp"])

    def test_all_sources_keep_named_manual_profile_identity_and_portable_fields(self):
        from marblescape_profile_transfer import import_profiles
        for provider in ("eumetsat", "goes_east", "goes_west", "solar", "himawari", "slider", "worldview"):
            with self.subTest(provider=provider):
                settings = app.default_import_settings()
                settings["source"]["provider"] = provider
                settings["output"].update(width=8, height=6, aspect_ratio="8:6")
                item = {"id": "a" * 32, "name": "Süd - 海 🌋", "settings": settings}
                with patch.object(app, "IMAGE_SOURCE", provider), \
                     patch.object(app, "SOURCE_PROFILES", settings["sources"]), \
                     patch.object(app, "image_settings_snapshot", return_value=copy.deepcopy(settings)), \
                     patch.object(app, "APPLIED_PROFILE_ID", item["id"]), \
                     patch.object(app, "IMAGE_PROFILE_LIBRARY", {"items": [item]}):
                    record = app.image_provenance("server" if provider == "eumetsat" else "noaa", [], (8, 6))
                self.assertEqual(record["profile_name"], item["name"])
                self.assertEqual(record["profile_id"], item["id"])
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "image.png"
                    path.write_bytes(embed_png_metadata(self.png(), record))
                    with Image.open(path) as image:
                        self.assertEqual(json.loads(image.getexif()[270]), record)
                    items, failures = import_profiles([path], [], app.default_import_settings,
                                                     app.normalize_image_settings_snapshot)
                self.assertEqual(failures, [])
                self.assertEqual(items[0]["name"], item["name"])
                self.assertEqual(items[0]["settings"], record["profile_settings"])

    def test_changed_manual_settings_do_not_claim_previous_profile_identity(self):
        settings = app.default_import_settings()
        item = {"id": "a" * 32, "name": "Original", "settings": copy.deepcopy(settings)}
        settings["view"]["zoom"] *= 2
        with patch.object(app, "image_settings_snapshot", return_value=settings), \
             patch.object(app, "APPLIED_PROFILE_ID", item["id"]), \
             patch.object(app, "IMAGE_PROFILE_LIBRARY", {"items": [item]}):
            record = app.image_provenance("server", [], (8, 6))
        self.assertNotIn("profile_id", record)
        self.assertNotIn("profile_name", record)


class SettingsBackupRoundtripTests(unittest.TestCase):
    def test_settings_only_payload_omits_library_and_rejects_full_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "settings.toml"
            config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
            profiles = root / "profiles.toml"
            profiles.write_text("Deliberately unreadable: settings-only must not read profiles.", encoding="utf-8")
            path = root / "settings-only.json"
            with patch.object(app, "ACTIVE_CONFIG_PATH", config), \
                 patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", profiles), \
                 patch.object(app, "is_windows_startup_enabled", return_value=False):
                app.export_settings_backup(path, include_profiles=False)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["scope"], "settings_only")
            self.assertNotIn("profiles", payload)
            self.assertNotIn("profiles_toml", payload)
            self.assertIsNone(app.import_settings_backup(path, include_profiles=False)[1])
            with self.assertRaisesRegex(ValueError, "settings only"):
                app.import_settings_backup(path, include_profiles=True)
            payload["profiles"] = {}
            with self.assertRaisesRegex(ValueError, "unexpectedly contains"):
                app.parse_settings_backup_payload(payload)

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
