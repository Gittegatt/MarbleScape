from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import marblescape_download as app
import marblescape_profile_transfer as transfer
from marblescape_copernicus import DEFAULT_PROFILE
from marblescape_image_metadata import embed_png_metadata


class ImportPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        settings = app.default_import_settings()
        settings["source"]["provider"] = "copernicus"
        settings["sources"]["copernicus"] = dict(DEFAULT_PROFILE)
        settings["sources"]["copernicus"]["date"] = "2026-04-01"
        settings["output"].update(width=8, height=6, aspect_ratio="8:6")
        self.current = transfer.portable_settings(settings, app.normalize_image_settings_snapshot)
        self.legacy = deepcopy(self.current)
        for key in ("date_mode", "quarter_offset", "month_offset"):
            self.legacy["sources"]["copernicus"].pop(key)

    def entry(self, name, settings=None):
        identifier = "a" * 32 if name == "Legacy" else hashlib.md5(name.encode()).hexdigest()
        return {"id": identifier, "name": name, "settings": deepcopy(self.legacy if settings is None else settings)}

    def file(self, name, entries):
        path = self.root / name
        path.write_text(json.dumps({"format": transfer.FORMAT, "version": 2, "profiles": entries,
                                   "profiles_sha256": transfer._digest(entries)}), encoding="utf-8")
        return path

    def read(self, paths, callback=None, existing=()):
        return transfer.import_profiles(paths, list(existing), app.default_import_settings,
                                        app.normalize_image_settings_snapshot, confirm_repair=callback)

    def test_all_files_checked_before_prompt_and_yes_all_is_batch_local(self):
        paths = [self.file(f"{index}.json", [self.entry(str(index))]) for index in range(2)]
        with patch.object(transfer, "_read_entries", wraps=transfer._read_entries) as read:
            def decide(label, changes):
                self.assertEqual(read.call_count, 2)
                self.assertEqual(len(changes), 3)
                return "yes_all"
            confirm = Mock(side_effect=decide)
            items, failures = self.read(paths, confirm)
        self.assertFalse(failures)
        confirm.assert_called_once()
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(item["settings"], self.current)
        no_callback, rejected = self.read(paths)
        self.assertFalse(no_callback)
        self.assertEqual(len(rejected), 2)

    def test_no_cancels_everything_even_after_yes_and_preserves_existing(self):
        path = self.file("batch.json", [self.entry("One"), self.entry("Two")])
        existing = [self.entry("Existing", self.current)]
        before = deepcopy(existing)
        with self.assertRaises(transfer.ImportCancelled):
            self.read([path], Mock(side_effect=["yes", "no"]), existing)
        self.assertEqual(existing, before)

    def test_skip_all_only_skips_repairable_profiles_not_complete_ones(self):
        path = self.file("batch.json", [self.entry("One"), self.entry("Two"), self.entry("Ready", self.current)])
        confirm = Mock(return_value="skip_all")
        items, failures = self.read([path], confirm)
        confirm.assert_called_once()
        self.assertEqual([item["name"] for item in items], ["Ready"])
        self.assertEqual(len(failures), 2)

    def test_report_lists_missing_fields_and_cannot_override_blocked_entries(self):
        broken = deepcopy(self.current)
        for key in ("latitude", "longitude"):
            broken["sources"]["copernicus"].pop(key)
        path = self.file("batch.json", [self.entry("Broken", broken), self.entry("Repair"), self.entry("Ready", self.current)])
        report = Mock(return_value=True)
        result = transfer.import_profiles([path], [], app.default_import_settings, app.normalize_image_settings_snapshot,
            confirm_repair=lambda *_: "yes_all", review_preflight=report)
        data = report.call_args.args[0]
        self.assertIn("latitude", data["failures"][0])
        self.assertIn("longitude", data["failures"][0])
        self.assertEqual(data["valid_count"], 2)
        self.assertEqual(len(data["repairs"]), 1)
        self.assertEqual([item["name"] for item in result.items], ["Repair", "Ready"])
        with self.assertRaises(transfer.ImportCancelled):
            transfer.import_profiles([path], [], app.default_import_settings, app.normalize_image_settings_snapshot,
                review_preflight=lambda _report: False)

    def test_known_optional_fields_require_confirmation_but_invalid_values_never_do(self):
        missing = deepcopy(self.current)
        missing["source"].pop("check_for_updates")
        missing["sources"]["copernicus"].pop("brightness")
        path = self.file("missing.json", [self.entry("Optional", missing)])
        confirm = Mock(return_value="yes")
        items, failures = self.read([path], confirm)
        self.assertFalse(failures)
        self.assertEqual(items[0]["settings"]["sources"]["copernicus"]["brightness"], 100)
        self.assertEqual(len(confirm.call_args.args[1]), 2)

    def test_skip_omits_one_and_valid_entries_still_import(self):
        path = self.file("batch.json", [self.entry("Legacy"), self.entry("Current", self.current)])
        items, failures = self.read([path], lambda *_: "skip")
        self.assertEqual([item["name"] for item in items], ["Current"])
        self.assertIn("skipped by user", failures[0])

    def test_essential_missing_fields_and_ambiguous_period_cannot_be_approved(self):
        invalid = deepcopy(self.legacy)
        invalid["sources"]["copernicus"].pop("latitude")
        ambiguous = deepcopy(self.legacy)
        ambiguous["sources"]["copernicus"]["quarter_offset"] = 1
        active_missing = deepcopy(self.legacy)
        active_missing["sources"]["copernicus"]["date_mode"] = "relative_quarter"
        active_missing["sources"]["copernicus"]["date"] = "latest"
        path = self.file("bad.json", [self.entry("Missing", invalid), self.entry("Ambiguous", ambiguous),
                                      self.entry("Missing active offset", active_missing)])
        confirm = Mock(return_value="yes_all")
        items, failures = self.read([path], confirm)
        self.assertFalse(items)
        self.assertEqual(len(failures), 3)
        confirm.assert_not_called()

    def test_bad_checksum_never_offers_repair(self):
        path = self.file("bad.json", [self.entry("Legacy")])
        data = json.loads(path.read_text())
        data["profiles_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        confirm = Mock(return_value="yes_all")
        items, failures = self.read([path], confirm)
        self.assertFalse(items)
        self.assertIn("checksum", failures[0])
        confirm.assert_not_called()

    def test_png_repair_requires_consent_and_preserves_pixels_and_selection(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6), (20, 40, 60)).save(buffer, format="PNG")
        record = {"software": "MarbleScape", "schema_version": 3, "profile_name": "Legacy PNG",
                  "date_mode": "catalogue", "quarter_offset": 0, "month_offset": 0,
                  "profile_settings": self.legacy}
        original = embed_png_metadata(buffer.getvalue(), record)
        path = self.root / "image.png"
        path.write_bytes(original)
        items, failures = self.read([path], lambda *_: "yes")
        self.assertFalse(failures)
        self.assertEqual(items[0]["settings"], self.current)
        self.assertEqual(path.read_bytes(), original)

    def backup(self):
        config = self.root / "settings.toml"
        config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        profiles = self.root / "profiles.toml"
        profiles.write_text(app.serialize_library(app.normalize_library({"items": [self.entry("Legacy")]})), encoding="utf-8")
        before = profiles.read_bytes()
        with patch.object(app, "ACTIVE_CONFIG_PATH", config), patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", profiles), \
             patch.object(app, "is_windows_startup_enabled", return_value=False):
            path = app.export_settings_backup(self.root / "backup.json")
        self.assertEqual(profiles.read_bytes(), before)
        return json.loads(path.read_text(encoding="utf-8"))

    def legacy_backup(self):
        payload = self.backup()
        payload["profiles"]["items"][0]["settings"] = deepcopy(self.legacy)
        text = app.serialize_library(payload["profiles"])
        payload["profiles_toml"] = text
        payload["profiles_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return payload

    def test_device_output_settings_of_older_exports_and_backups_are_ignored(self):
        older = deepcopy(self.current)
        older["output"].update(width=1920, height=0, aspect_ratio="16:9",
                               background_color="#102030", latest_folder="")
        items, failures = self.read([self.file("older.json", [self.entry("Older", older)])])
        self.assertFalse(failures)
        self.assertEqual(items[0]["settings"], self.current)
        # An older backup lists them in profiles.toml and in its readable copy.
        payload = self.backup()
        text = payload["profiles_toml"].replace(
            '"output" = { ', '"output" = { "width" = 1920, "height" = 0, "aspect_ratio" = "16:9", '
            '"background_color" = "#102030", "latest_folder" = "", ', 1)
        self.assertIn('"width" = 1920', text)
        payload["profiles_toml"] = text
        payload["profiles_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload["profiles"] = app.make_json_compatible(app.tomllib.loads(text)["image_profiles"])
        _config, library, _startup = app.parse_settings_backup_payload(payload, lambda *_: "yes")
        self.assertEqual(library["items"][0]["settings"], self.current)
        payload["profiles"]["items"][0]["name"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "readable profile snapshot"):
            app.parse_settings_backup_payload(payload, lambda *_: "yes")

    def test_screenshot_regression_export_upgrades_only_copy_and_is_restorable(self):
        payload = self.backup()
        _config, library, _startup = app.parse_settings_backup_payload(payload)
        self.assertEqual(library["items"][0]["settings"], self.current)
        self.assertEqual(library["items"][0]["id"], "a" * 32)
        self.assertEqual(library["items"][0]["name"], "Legacy")

    def test_full_backup_repairs_need_consent_and_cannot_skip(self):
        payload = self.legacy_backup()
        with self.assertRaisesRegex(ValueError, "confirmation"):
            app.parse_settings_backup_payload(payload)
        for decision in ("skip", "no"):
            with self.assertRaises(transfer.ImportCancelled):
                app.parse_settings_backup_payload(payload, lambda *_: decision)
        _config, library, _startup = app.parse_settings_backup_payload(payload, lambda *_: "yes")
        self.assertEqual(library["items"][0]["settings"], self.current)
        self.assertNotIn("date_mode", payload["profiles"]["items"][0]["settings"]["sources"]["copernicus"])

    def test_full_backup_snapshot_and_startup_checked_before_prompt(self):
        for kind in ("snapshot", "startup"):
            payload = self.legacy_backup()
            if kind == "snapshot":
                payload["profiles"]["items"][0]["name"] = "Changed"
            else:
                payload["windows_startup_enabled"] = "true"
            confirm = Mock(return_value="yes_all")
            with self.assertRaises(ValueError):
                app.parse_settings_backup_payload(payload, confirm)
            confirm.assert_not_called()

    def test_invalid_configuration_rejected_before_repair_or_runtime_changes(self):
        baseline = app.capture_loaded_configuration()
        for section, key, value in (("output", "width", -1), ("service", "timeout_seconds", 0),
                                    ("download", "show_progress", "yes"), ("history", "max_files", -1)):
            payload = self.legacy_backup()
            text = app.replace_toml_values(payload["configuration_toml"], [(section, key, value)])
            payload["configuration_toml"] = text
            payload["configuration_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            payload["settings"] = app.make_json_compatible(app.tomllib.loads(text))
            confirm = Mock(return_value="yes_all")
            with self.assertRaises(ValueError):
                app.parse_settings_backup_payload(payload, confirm)
            confirm.assert_not_called()
            self.assertEqual(app.capture_loaded_configuration(), baseline)
