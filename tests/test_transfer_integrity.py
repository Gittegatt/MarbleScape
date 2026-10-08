"""Synthetic file-corruption checks; never use real profiles or credentials."""

from copy import deepcopy
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
import marblescape_profile_transfer as transfer
from marblescape_copernicus import get_product, get_layer, products
from marblescape_image_metadata import embed_png_metadata


class TransferIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(buffer, format="PNG")
        self.png = buffer.getvalue()

    def settings(self, provider="copernicus"):
        value = app.default_import_settings()
        value["source"]["provider"] = provider
        value["output"].update(width=8, height=6, aspect_ratio="8:6")
        return value

    def record(self, settings=None, resolved="2026-04-01"):
        settings = self.settings() if settings is None else settings
        provider = settings["source"]["provider"]
        item = {"id": "a" * 32, "name": "Süd - 海", "settings": settings}
        requests = []
        if provider == "copernicus":
            profile = settings["sources"][provider]
            product = get_product(profile["configuration"], profile["product"])
            requests = [{"frame": {"profile": profile, "product": product,
                "layer": get_layer(product, profile["layer"]), "date": resolved}}]
        with patch.object(app, "IMAGE_SOURCE", provider), \
             patch.object(app, "SOURCE_PROFILES", settings["sources"]), \
             patch.object(app, "LAYER_CONFIG", settings["layers"]), \
             patch.object(app, "IMAGE_PROFILE_LIBRARY", {"items": [item]}), \
             patch.object(app, "image_settings_snapshot", return_value=deepcopy(settings)):
            return app.image_provenance("copernicus" if requests else "server", requests,
                                        (8, 6), profile_id=item["id"])

    def read(self, data):
        path = self.root / "synthetic.png"
        path.write_bytes(data)
        return transfer.import_profiles([path], [], app.default_import_settings,
                                        app.normalize_image_settings_snapshot)

    def test_bad_iend_crc_is_rejected_by_import_and_metadata_writer(self):
        valid = embed_png_metadata(self.png, self.record())
        damaged = valid[:-1] + bytes([valid[-1] ^ 1])
        result = self.read(damaged)
        self.assertFalse(result.imported)
        self.assertIn("IEND checksum", result.warnings[0])
        with self.assertRaisesRegex(ValueError, "IEND checksum"):
            embed_png_metadata(damaged, self.record())
        self.assertEqual(len(self.read(valid).imported), 1)

    def test_incomplete_nonempty_or_followed_iend_is_rejected(self):
        valid = embed_png_metadata(self.png, self.record())
        for damaged in (valid[:-1], valid[:-12], valid + b"extra",
                        valid[:-12] + struct.pack(">I", 1) + valid[-8:]):
            with self.subTest(ending=damaged[-16:]):
                self.assertFalse(self.read(damaged).imported)

    def test_source_conflicts_rejected_for_every_provider(self):
        for provider in app.SOURCE_LABELS:
            with self.subTest(provider=provider):
                record = self.record(self.settings(provider))
                self.assertEqual(len(self.read(embed_png_metadata(self.png, record)).imported), 1)
                record["source"] = "Wrong provider"
                result = self.read(embed_png_metadata(self.png, record))
                self.assertFalse(result.imported)
                self.assertIn("source", result.warnings[0])

    def test_copernicus_descriptions_are_checked_against_catalogue_names(self):
        original = self.record()
        for field in ("mission", "product", "layer"):
            with self.subTest(field=field):
                record = deepcopy(original)
                record[field] = "Different selection"
                result = self.read(embed_png_metadata(self.png, record))
                self.assertFalse(result.imported)
                self.assertIn(field, result.warnings[0])
        profile = original["profile_settings"]["sources"]["copernicus"]
        self.assertNotEqual(original["product"], profile["product"])
        self.assertEqual(len(self.read(embed_png_metadata(self.png, original)).imported), 1)

    def test_other_provider_selectors_and_eumetsat_layers_are_checked(self):
        for provider in app.SOURCE_LABELS:
            if provider == "copernicus":
                continue
            original = self.record(self.settings(provider))
            for field in ("satellite", "mission", "area", "sector", "product", "layer", "resolution", "theme", "layers"):
                if field not in original:
                    continue
                with self.subTest(provider=provider, field=field):
                    record = deepcopy(original)
                    record[field] = ["wrong"] if field == "layers" else "wrong"
                    result = self.read(embed_png_metadata(self.png, record))
                    self.assertFalse(result.imported)
                    self.assertIn(field, result.warnings[0])

    def test_numeric_boolean_type_confusion_is_rejected(self):
        original = self.record()
        record = deepcopy(original)
        record["map_labels"] = int(original["map_labels"])
        result = self.read(embed_png_metadata(self.png, record))
        self.assertFalse(result.imported)
        self.assertIn("map_labels", result.warnings[0])

    def test_relative_quarter_and_month_use_recorded_period_not_current_date(self):
        for granularity, mode, offset, mission, expected in (
            ("quarter", "relative_quarter", "quarter_offset", "Sentinel-2 Mosaics", "2020-Q1"),
            ("month", "relative_month", "month_offset", "Sentinel-1 Mosaics", "2020-02"),
        ):
            with self.subTest(granularity=granularity):
                settings = self.settings()
                profile = settings["sources"]["copernicus"]
                product = next(p for p in products(profile["configuration"], mission)
                               if p["layers"][0].get("date_granularity") == granularity)
                profile.update(mission=mission, product=product["id"], layer=product["layers"][0]["id"],
                               date="latest", date_mode=mode, quarter_offset=0, month_offset=0)
                profile[offset] = 1
                record = self.record(settings, resolved="2020-02-01")
                record["generated_at_utc"] = "2020-05-01T00:00:00Z"
                self.assertEqual(record["mosaic_period"], expected)
                result = self.read(embed_png_metadata(self.png, record))
                self.assertEqual(result.warnings, [])
                self.assertEqual(result.imported[0]["settings"]["sources"]["copernicus"][offset], 1)
                record["mosaic_period"] = "2026-Q3"
                result = self.read(embed_png_metadata(self.png, record))
                self.assertFalse(result.imported)
                self.assertIn("mosaic_period", result.warnings[0])

    def test_fixed_mosaic_period_cannot_disagree_with_resolved_date(self):
        settings = self.settings()
        settings["sources"]["copernicus"].update(date="2026-04-01", date_mode="catalogue")
        record = self.record(settings, resolved="2026-07-01")
        result = self.read(embed_png_metadata(self.png, record))
        self.assertFalse(result.imported)
        self.assertIn("fixed profile period", result.warnings[0])
        record = self.record(settings, resolved="2026-05-01")
        self.assertEqual(len(self.read(embed_png_metadata(self.png, record)).imported), 1)

    def test_mosaic_period_requires_valid_resolved_date(self):
        for value in (None, "invalid", 3):
            record = self.record()
            if value is None:
                record.pop("resolved_date")
            else:
                record["resolved_date"] = value
            self.assertFalse(self.read(embed_png_metadata(self.png, record)).imported)

    def test_image_digest_remains_informational(self):
        record = self.record()
        record["rendered_image_sha256"] = "not-checked-by-profile-import"
        self.assertEqual(len(self.read(embed_png_metadata(self.png, record)).imported), 1)

    def export(self, folder=None, overwrite=False):
        items = [{"id": str(index) * 32, "name": f"Profile {index}", "settings": self.settings()}
                 for index in (1, 2)]
        return transfer.export_profiles(folder or self.root, items, app.normalize_image_settings_snapshot,
                                        confirm_conflict=(lambda _: "overwrite_all") if overwrite else None)

    def test_profile_exports_are_reread_both_before_and_after_publication(self):
        with patch.object(transfer, "_verify_export", wraps=transfer._verify_export) as verify:
            paths = self.export()
        self.assertEqual(verify.call_count, 4)
        self.assertEqual([call.args[0] for call in verify.call_args_list[2:]], paths)
        for path in paths:
            result = transfer.import_profiles([path], [], app.default_import_settings,
                                              app.normalize_image_settings_snapshot)
            self.assertEqual(len(result.imported), 1)

    def test_staging_validation_failure_never_replaces_originals(self):
        paths = self.export()
        before = {path.name: path.read_bytes() for path in paths}
        with patch.object(transfer, "_read_profile_json", side_effect=ValueError("Unreadable staged JSON")):
            with self.assertRaisesRegex(ValueError, "Export verification failed.*Unreadable"):
                self.export(overwrite=True)
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_published_profile_corruption_rolls_back_entire_batch(self):
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                folder = self.root / str(overwrite)
                folder.mkdir()
                if overwrite:
                    self.export(folder)
                before = {path.name: path.read_bytes() for path in folder.iterdir()}
                publish = transfer.os.replace if overwrite else transfer._publish_new

                def corrupt_second(source, destination):
                    result = publish(source, destination)
                    if Path(source).name.startswith(".marblescape-profile-") and "Profile 2" in Path(destination).name:
                        Path(destination).write_bytes(b"corrupted output")
                    return result

                target, name = (transfer.os, "replace") if overwrite else (transfer, "_publish_new")
                with patch.object(target, name, side_effect=corrupt_second):
                    with self.assertRaisesRegex(ValueError, "saved contents differ"):
                        self.export(folder, overwrite)
                self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, before)

    def test_settings_backups_are_verified_in_both_scopes_and_rollback_on_corruption(self):
        config = self.root / "settings.toml"
        config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        profiles = self.root / "profiles.toml"
        profiles.write_text(app.serialize_library(app.normalize_library({})), encoding="utf-8")
        with patch.object(app, "ACTIVE_CONFIG_PATH", config), \
             patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", profiles), \
             patch.object(app, "is_windows_startup_enabled", return_value=False):
            for full in (False, True):
                path = self.root / f"backup-{full}.json"
                with patch.object(transfer, "_verify_export", wraps=transfer._verify_export) as verify:
                    app.export_settings_backup(path, include_profiles=full)
                self.assertEqual(verify.call_count, 2)
                self.assertEqual(verify.call_args.args[0], path)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["scope"],
                                 "settings_and_profiles" if full else "settings_only")
                before = path.read_bytes()
                replace = transfer.os.replace

                def corrupt(source, destination):
                    result = replace(source, destination)
                    if Path(source).name.startswith(".marblescape-profile-"):
                        Path(destination).write_bytes(b"bad backup")
                    return result

                with patch.object(transfer.os, "replace", side_effect=corrupt):
                    with self.assertRaisesRegex(ValueError, "saved contents differ"):
                        app.export_settings_backup(path, include_profiles=full)
                self.assertEqual(path.read_bytes(), before)
                app.import_settings_backup(path, include_profiles=full)
        self.assertFalse(list(self.root.glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
