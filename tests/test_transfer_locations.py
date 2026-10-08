import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from marblescape_transfer_locations import ExportLocations, settings_backup_filename


class ExportLocationsTests(unittest.TestCase):
    def test_old_default_destinations_migrate_but_custom_folders_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "backups"
            old.mkdir()
            custom = root / "custom"
            custom.mkdir()
            (old / "export_locations.json").write_text(json.dumps({
                "settings": str(old / "settings"), "profiles": str(custom)}), encoding="utf-8")
            manager = ExportLocations(root)
            self.assertEqual(manager.initial_directory("settings"), root / "export" / "settings")
            self.assertEqual(manager.initial_directory("profiles"), custom)
            self.assertTrue((old / "export_locations.json").is_file())

    def test_scope_filenames_keep_timestamp_at_end(self):
        when = dt.datetime(2026, 9, 26, 19, 3, 7)
        self.assertEqual(settings_backup_filename(False, when), "marblescape-settings-2026-09-26_190307.json")
        self.assertEqual(settings_backup_filename(True, when), "marblescape-settings-profiles-2026-09-26_190307.json")

    def test_defaults_and_remembered_folders_are_separate_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ExportLocations(root)
            for category in ("settings", "profiles"):
                self.assertEqual(manager.initial_directory(category), root / "export" / category)
            settings, profiles = root / "chosen-settings", root / "chosen-profiles"
            settings.mkdir()
            profiles.mkdir()
            self.assertTrue(manager.remember("settings", settings))
            self.assertEqual(manager.initial_directory("profiles"), root / "export" / "profiles")
            self.assertTrue(manager.remember("profiles", profiles))
            reopened = ExportLocations(root)
            self.assertEqual(reopened.initial_directory("settings"), settings)
            self.assertEqual(reopened.initial_directory("profiles"), profiles)

    def test_unavailable_choice_is_forgotten_even_when_folder_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "custom"
            custom.mkdir()
            manager = ExportLocations(root)
            manager.remember("settings", custom)
            custom.rmdir()
            self.assertEqual(manager.initial_directory("settings"), root / "export" / "settings")
            custom.mkdir()
            self.assertEqual(ExportLocations(root).initial_directory("settings"), root / "export" / "settings")
            manager.remember("settings", custom)
            self.assertEqual(ExportLocations(root).initial_directory("settings"), custom)

    def test_access_denied_custom_folder_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "custom"
            custom.mkdir()
            manager = ExportLocations(root)
            manager.remember("profiles", custom)
            available = manager._available
            with patch.object(manager, "_available", side_effect=lambda path: path != custom and available(path)):
                self.assertEqual(manager.initial_directory("profiles"), root / "export" / "profiles")

    def test_corrupt_state_is_ignored_and_invalid_category_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "export").mkdir()
            (root / "export" / "export_locations.json").write_text("[broken", encoding="utf-8")
            manager = ExportLocations(root)
            self.assertEqual(manager.initial_directory("profiles"), root / "export" / "profiles")
            with self.assertRaises(ValueError):
                manager.initial_directory("../outside")
            with self.assertRaises(ValueError):
                manager.remember("invalid", root)
