from copy import deepcopy
import io
import json
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
from marblescape_copernicus import DEFAULT_PROFILE
from marblescape_image_metadata import embed_png_metadata, wallpaper_metadata
from marblescape_profile_transfer import export_profiles, import_profiles, portable_settings, read_png_record


class ProfileTransferTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        settings = app.default_import_settings()
        settings["source"]["provider"] = "copernicus"
        settings["sources"]["copernicus"] = dict(DEFAULT_PROFILE, date_mode="relative_quarter", quarter_offset=0)
        settings["output"].update(width=8, height=6, aspect_ratio="8:6")
        self.items = [{"id": "1" * 32, "name": "First", "settings": settings},
                      {"id": "2" * 32, "name": "Second", "settings": deepcopy(settings)}]

    def tearDown(self):
        self.folder.cleanup()

    def read(self, paths, existing=(), decision=None):
        return tuple(import_profiles(paths, list(existing), app.default_import_settings, app.normalize_image_settings_snapshot,
                                     confirm_conflict=(lambda _context: decision) if decision else None))

    def test_json_multiple_profiles_roundtrip_preserves_ids_and_confirms_replacements(self):
        destinations = export_profiles(self.root, self.items, app.normalize_image_settings_snapshot)
        self.assertEqual(len(destinations), 2)
        imported, warnings = self.read(destinations, self.items, decision="overwrite")
        self.assertEqual([item["name"] for item in imported], ["First", "Second"])
        self.assertFalse(warnings)
        self.assertEqual(imported[0]["settings"]["sources"]["copernicus"]["quarter_offset"], 0)
        self.assertEqual(imported[0]["id"], self.items[0]["id"])

    def test_exported_coverage_is_numeric_and_reserved_import_filename_is_rejected(self):
        item = self.items[0]
        path = export_profiles(self.root, [item], app.normalize_image_settings_snapshot,
                               coverage_records={item["id"]: {"data_coverage_percent": 98.42}})[0]
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["profiles"][0]["data_coverage_percent"], 98.42)
        restored, failures = self.read([path])
        self.assertEqual(restored[0]["id"], item["id"])
        self.assertFalse(failures)
        reserved = self.root / "_reserved.json"
        reserved.write_bytes(path.read_bytes())
        restored, failures = self.read([reserved])
        self.assertFalse(restored)
        self.assertIn("reserved", failures[0])

    def test_all_sources_json_and_png_roundtrip_with_mosaic_color(self):
        from marblescape_copernicus_mosaics import MOSAIC_PRODUCTS
        from marblescape_data_coverage import unavailable
        from marblescape_image_metadata import SCHEMA_VERSION, SOURCE_LABELS
        configurations = []
        for provider in app.default_import_settings()["sources"]:
            settings = app.default_import_settings()
            settings["source"]["provider"] = provider
            if provider == "copernicus":
                settings["sources"][provider]["no_data_color"] = "#1234AB"
            configurations.append(settings)
        for product in MOSAIC_PRODUCTS:
            for layer in product["layers"]:
                settings = app.default_import_settings()
                settings["source"]["provider"] = "copernicus"
                settings["sources"]["copernicus"].update(
                    mission=product["missions"][0], product=product["id"], layer=layer["id"],
                    no_data_color="#ABCDEF", map_zoom=10)
                configurations.append(settings)
        for index, settings in enumerate(configurations):
            with self.subTest(index=index, source=settings["source"]["provider"]):
                settings["output"].update(width=8, height=6, aspect_ratio="8:6")
                item = {"id": f"{index + 1:032x}", "name": f"Island 海 {index}", "settings": settings}
                portable = portable_settings(settings, app.normalize_image_settings_snapshot)
                path = export_profiles(self.root, [item], app.normalize_image_settings_snapshot)[0]
                restored, failures = self.read([path])
                self.assertFalse(failures)
                self.assertEqual(restored[0]["settings"], portable)
                record = {"software": "MarbleScape", "schema_version": SCHEMA_VERSION,
                          "profile_id": item["id"], "profile_name": item["name"], "profile_settings": portable,
                          "source": SOURCE_LABELS[settings["source"]["provider"]], **unavailable()}
                if settings["source"]["provider"] == "copernicus":
                    record["no_data_color"] = portable["sources"]["copernicus"]["no_data_color"]
                raw = io.BytesIO()
                Image.new("RGB", (8, 6)).save(raw, format="PNG")
                png = self.root / f"image-{index}.png"
                png.write_bytes(embed_png_metadata(raw.getvalue(), record))
                with Image.open(png) as image:
                    self.assertEqual(json.loads(image.getexif()[270]), record)
                restored, failures = self.read([png])
                self.assertFalse(failures)
                self.assertEqual(restored[0], dict(item, settings=portable))

    def test_missing_legacy_color_defaults_white_but_invalid_color_is_rejected(self):
        from marblescape_profile_transfer import strict_settings
        settings = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        settings["sources"]["copernicus"].pop("no_data_color")
        normalized = strict_settings(settings, app.normalize_image_settings_snapshot)
        self.assertEqual(normalized["sources"]["copernicus"]["no_data_color"], "#FFFFFF")
        settings["sources"]["copernicus"]["no_data_color"] = "#GGGGGG"
        with self.assertRaisesRegex(ValueError, "no-data color"):
            strict_settings(settings, app.normalize_image_settings_snapshot)

    def test_missing_legacy_map_label_color_keeps_black_labels(self):
        from marblescape_profile_transfer import strict_settings
        settings = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        settings["sources"]["copernicus"].pop("map_label_color")
        # Exports from before the color existed rendered black labels; new profiles default to white.
        normalized = strict_settings(settings, app.normalize_image_settings_snapshot)
        self.assertEqual(normalized["sources"]["copernicus"]["map_label_color"], "#000000")

    def test_exports_from_before_separate_country_borders_import_without_repair(self):
        from marblescape_profile_transfer import strict_settings
        for labels, color in ((True, "#123456"), (False, "#FFFFFF")):
            with self.subTest(labels=labels):
                settings = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
                copernicus = settings["sources"]["copernicus"]
                copernicus.update(map_labels=labels, map_label_color=color)
                copernicus.pop("map_borders")
                copernicus.pop("map_border_color")
                # Labels and borders shared one switch and one color back then.
                normalized = strict_settings(settings, app.normalize_image_settings_snapshot)["sources"]["copernicus"]
                self.assertEqual((normalized["map_borders"], normalized["map_border_color"]), (labels, color))
        settings = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        settings["sources"]["copernicus"]["map_borders"] = 1
        with self.assertRaisesRegex(ValueError, "map_borders"):
            strict_settings(settings, app.normalize_image_settings_snapshot)

    def test_exports_from_before_regular_no_data_color_import_without_repair(self):
        from marblescape_profile_transfer import strict_settings
        for mode, expected in (("single", ("single", "transparent")),
                               ("fill_gaps", ("fill_gaps", "transparent")),
                               ("black", ("single", "#000000"))):
            with self.subTest(mode=mode):
                regular = deepcopy(self.items[0]["settings"])
                regular["sources"]["copernicus"].update(
                    mission="Sentinel-2", product="DEFAULT-THEME::a91f72", layer="1_TRUE_COLOR",
                    date_mode="catalogue", date="latest")
                settings = portable_settings(regular, app.normalize_image_settings_snapshot)
                copernicus = settings["sources"]["copernicus"]
                copernicus["coverage_mode"] = mode
                copernicus.pop("scene_no_data_color")
                # The map background showed, or black with Gap fill "black".
                normalized = strict_settings(settings, app.normalize_image_settings_snapshot)["sources"]["copernicus"]
                self.assertEqual((normalized["coverage_mode"], normalized["scene_no_data_color"]), expected)
        settings = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        settings["sources"]["copernicus"]["scene_no_data_color"] = "#GGGGGG"
        with self.assertRaisesRegex(ValueError, "no-data color"):
            strict_settings(settings, app.normalize_image_settings_snapshot)

    def test_conflicting_png_scene_no_data_color_is_rejected(self):
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 4, "profile_name": "Invalid color",
                  "scene_no_data_color": "#123456",
                  "profile_settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        path = self.root / "conflict.png"
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        restored, failures = self.read([path])
        self.assertFalse(restored)
        self.assertIn("scene_no_data_color", failures[0])

    def test_png_from_auto_recommendation_imports_with_its_saved_settings(self):
        settings = deepcopy(self.items[0]["settings"])
        settings["sources"]["copernicus"].update(auto_recommendation=True, auto_priority="full_coverage",
                                                 date_mode="catalogue", quarter_offset=0)
        portable = portable_settings(settings, app.normalize_image_settings_snapshot)
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "Auto",
                  "auto_recommendation": True, "auto_priority": "full_coverage",
                  "auto_choice": "Relative to now, 3 quarters ago (2026 Q1)",
                  "date_mode": "relative_quarter", "quarter_offset": 3, "profile_settings": portable}
        path = self.root / "auto.png"
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        restored, failures = self.read([path])
        self.assertFalse(failures)
        copernicus = restored[0]["settings"]["sources"]["copernicus"]
        self.assertEqual((copernicus["auto_recommendation"], copernicus["date_mode"]), (True, "catalogue"))
        # A record from the rule does not fit a profile without it.
        record["profile_settings"] = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        restored, failures = self.read([path])
        self.assertFalse(restored)
        self.assertIn("auto_recommendation", failures[0])

    def test_conflicting_png_no_data_color_is_rejected(self):
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "Invalid color",
                  "no_data_color": "#000000",
                  "profile_settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        path = self.root / "conflict.png"
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        restored, failures = self.read([path])
        self.assertFalse(restored)
        self.assertIn("no_data_color", failures[0])

    def test_export_does_not_include_paths_credentials_or_unrelated_selections(self):
        settings = self.items[0]["settings"]
        settings["output"]["latest_folder"] = "C:/private/images"
        settings["sources"]["copernicus"]["client_secret"] = "do-not-export"
        settings["output"]["private_comment"] = "do-not-export"
        settings["sources"]["solar"]["area"] = "private-other-selection"
        portable = portable_settings(settings, app.normalize_image_settings_snapshot)
        encoded = json.dumps(portable)
        self.assertNotIn("private", encoded)
        self.assertNotIn("do-not-export", encoded)
        self.assertEqual(set(portable["sources"]), {"copernicus"})

    def test_png_text_and_exif_restore_full_profile(self):
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "From image",
                  "profile_settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        image_path = self.root / "image.png"
        image_path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        self.assertEqual(read_png_record(image_path), record)
        imported, warnings = self.read([image_path, image_path], decision="rename")
        self.assertEqual([item["name"] for item in imported], ["From image", "From image (Copy)"])
        self.assertEqual(imported[0]["settings"], record["profile_settings"])
        self.assertFalse(warnings)

    def test_bad_second_file_does_not_change_existing_profiles(self):
        good = export_profiles(self.root, self.items[:1], app.normalize_image_settings_snapshot)[0]
        bad = self.root / "bad.json"
        bad.write_text('{"format":"unknown"}', encoding="utf-8")
        original = deepcopy(self.items)
        added, failures = self.read([good, bad], self.items, decision="overwrite")
        self.assertEqual(len(added), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("bad.json", failures[0])
        self.assertEqual(self.items, original)

    def test_exif_only_png_is_supported_and_malformed_record_is_rejected(self):
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "EXIF",
                  "profile_settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        encoded = embed_png_metadata(raw.getvalue(), record)
        output, offset = [encoded[:8]], 8
        while offset < len(encoded):
            size = struct.unpack_from(">I", encoded, offset)[0] + 12
            if encoded[offset+4:offset+8] != b"iTXt":
                output.append(encoded[offset:offset+size])
            offset += size
        path = self.root / "exif-only.png"
        path.write_bytes(b"".join(output))
        imported, _ = self.read([path])
        self.assertEqual(imported[0]["name"], "EXIF")
        record["profile_settings"]["sources"]["copernicus"]["quarter_offset"] = -3
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        added, failures = self.read([path])
        self.assertFalse(added)
        self.assertEqual(len(failures), 1)

    def test_png_without_metadata_is_rejected(self):
        path = self.root / "plain.png"
        Image.new("RGB", (8, 6)).save(path)
        added, failures = self.read([path])
        self.assertFalse(added)
        self.assertIn("No readable", failures[0])

    def test_export_uses_readable_name_uuid_and_never_overwrites(self):
        self.items[0]["name"] = 'Islands: Süd / 海'
        self.items[1]["name"] = self.items[0]["name"]
        paths = export_profiles(self.root, self.items, app.normalize_image_settings_snapshot)
        self.assertEqual(len(set(paths)), 2)
        self.assertIn("Islands_ Süd _ 海", paths[0].name)
        self.assertIn("11111111-1111-1111-1111-111111111111", paths[0].name)
        for path in paths:
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(document["profiles"]), 1)
        before = [path.read_bytes() for path in paths]
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_profiles(self.root, self.items, app.normalize_image_settings_snapshot)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_json_checksum_corruption_is_reported_without_import(self):
        path = export_profiles(self.root, self.items[:1], app.normalize_image_settings_snapshot)[0]
        document = json.loads(path.read_text(encoding="utf-8"))
        document["profiles"][0]["settings"]["sources"]["copernicus"]["brightness"] = 120
        path.write_text(json.dumps(document), encoding="utf-8")
        added, failures = self.read([path])
        self.assertFalse(added)
        self.assertIn("checksum", failures[0])

    def test_legacy_bundle_accepts_valid_entries_but_rejects_missing_fields(self):
        valid = {"name": "Good", "settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        bad = deepcopy(valid)
        bad["name"] = "Incomplete"
        del bad["settings"]["sources"]["copernicus"]["map_zoom"]
        path = self.root / "bundle.json"
        path.write_text(json.dumps({"format": "MarbleScape profiles", "version": 1,
                                    "profiles": [bad, valid]}), encoding="utf-8")
        added, failures = self.read([path])
        self.assertEqual([item["name"] for item in added], ["Good"])
        self.assertIn("Incomplete", failures[0])
        self.assertIn("map_zoom", failures[0])

    def test_duplicate_json_keys_and_invalid_uuid_are_rejected(self):
        path = self.root / "bad.json"
        path.write_text('{"format":"MarbleScape profiles","version":1,"version":2}', encoding="utf-8")
        self.assertIn("Duplicate JSON field", self.read([path])[1][0])
        path.write_text(json.dumps({"format": "MarbleScape profiles", "version": 1,
            "profiles": [{"id": "broken", "name": "Bad ID", "settings": self.items[0]["settings"]}]}), encoding="utf-8")
        self.assertIn("UUID", self.read([path])[1][0])

    def test_generated_profile_png_roundtrips_unicode_identity_and_all_fields(self):
        item = self.items[0]
        item["name"] = "Vulkane Süd - 火山 🌋"
        frame = {"profile": item["settings"]["sources"]["copernicus"],
                 "product": {"name": "Sentinel-2 Quarterly Mosaics"},
                 "layer": {"name": "True Color Cloudless", "date_granularity": "quarter"},
                 "date": "2026-07-01"}
        with patch.object(app, "IMAGE_SOURCE", "copernicus"), \
             patch.object(app, "IMAGE_PROFILE_LIBRARY", {"items": [item]}), \
             patch.object(app, "image_settings_snapshot", return_value=deepcopy(item["settings"])):
            record = app.image_provenance("copernicus", [{"frame": frame}], (8, 6), profile_id=item["id"])
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        path = self.root / "unicode.png"
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        with Image.open(path) as picture:
            self.assertEqual(json.loads(picture.getexif()[270]), record)
        added, failures = self.read([path])
        self.assertFalse(failures)
        self.assertEqual(added[0]["name"], item["name"])
        self.assertEqual(added[0]["id"], item["id"])
        self.assertEqual(added[0]["settings"], record["profile_settings"])
        self.assertEqual(record["profile_id"], item["id"])
        self.assertEqual(record["mosaic_period"], "2026-Q3")
        record["width"] = 100
        path.write_bytes(embed_png_metadata(raw.getvalue(), record))
        self.assertIn("dimensions", self.read([path])[1][0])

    def test_corrupt_png_pixels_are_rejected(self):
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "Corrupt",
                  "profile_settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        data = bytearray(embed_png_metadata(raw.getvalue(), record))
        data[data.index(b"IDAT") + 5] ^= 255
        path = self.root / "corrupt.png"
        path.write_bytes(data)
        added, failures = self.read([path])
        self.assertFalse(added)
        self.assertIn("corrupt.png", failures[0])

    def test_export_has_no_device_output_and_older_pngs_still_import(self):
        path = export_profiles(self.root, self.items[:1], app.normalize_image_settings_snapshot)[0]
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 3)
        self.assertEqual(document["profiles"][0]["settings"]["output"].keys(), {"render_scale"})
        # Older PNGs record the device output size: it must match the picture,
        # then import ignores it.
        settings = portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        older = deepcopy(settings)
        older["output"].update(width=8, height=6, aspect_ratio="8:6",
                               background_color="#102030", latest_folder="")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "Older",
                  "profile_settings": older}
        raw = io.BytesIO()
        Image.new("RGB", (8, 6)).save(raw, format="PNG")
        png = self.root / "older.png"
        png.write_bytes(embed_png_metadata(raw.getvalue(), record))
        added, failures = self.read([png])
        self.assertFalse(failures)
        self.assertEqual(added[0]["settings"], settings)
        older["output"]["width"] = 9
        png.write_bytes(embed_png_metadata(raw.getvalue(), record))
        self.assertIn("dimensions", self.read([png])[1][0])

    def test_wallpaper_derivative_retains_original_profile_and_actual_dimensions(self):
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "Source",
                  "profile_id": self.items[0]["id"], "width": 8, "height": 6,
                  "rendered_image_sha256": "source-digest",
                  "profile_settings": portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)}
        derivative = wallpaper_metadata(record, (16, 9), "fill")
        self.assertEqual((derivative["width"], derivative["height"]), (16, 9))
        self.assertEqual(derivative["profile_image_size"], [8, 6])
        self.assertNotIn("rendered_image_sha256", derivative)
        self.assertEqual(record["width"], 8)
        raw = io.BytesIO()
        Image.new("RGB", (16, 9)).save(raw, format="PNG")
        path = self.root / "wallpaper.png"
        path.write_bytes(embed_png_metadata(raw.getvalue(), derivative))
        added, failures = self.read([path])
        self.assertFalse(failures)
        self.assertEqual(added[0]["settings"], record["profile_settings"])
