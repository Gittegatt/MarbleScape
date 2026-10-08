"""Profile cache limits: configuration, backups and the storage estimate."""

from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import marblescape_download as app


class ProfileCacheSettingsTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()

    def tearDown(self):
        app.restore_loaded_configuration(self.saved_configuration)

    def load(self, text):
        with tempfile.TemporaryDirectory(prefix="marblescape-cache-settings-") as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            with patch.object(app, "log"):
                app.load_configuration(path)

    def template(self):
        return app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_size_limit_is_half_a_gigabyte_steps_from_half_to_ten(self):
        self.assertEqual(app.normalize_profile_cache_max_size_gb(2), 2.0)
        self.assertEqual(app.normalize_profile_cache_max_size_gb("0.5"), 0.5)
        self.assertEqual(app.normalize_profile_cache_max_size_gb(10.0), 10.0)
        for value in (0, 0.25, 0.75, 10.5, -1, float("nan"), float("inf"), "big", True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.normalize_profile_cache_max_size_gb(value)
        self.assertEqual(app.profile_cache_max_bytes(1.5), 1_500_000_000)
        self.assertEqual(app.normalize_profile_cache_snapshot_size_gb("1.5"), 1.5)
        with self.assertRaisesRegex(ValueError, "Latest snapshot size limit"):
            app.normalize_profile_cache_snapshot_size_gb(0.25)
        self.assertEqual(app.profile_cache_snapshot_bytes(0.5), 500_000_000)

    def test_variants_are_a_whole_number_from_one_to_ten(self):
        self.assertEqual(app.normalize_profile_cache_variants(1), 1)
        self.assertEqual(app.normalize_profile_cache_variants(" 10 "), 10)
        for value in (0, 11, 2.0, "2.5", True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.normalize_profile_cache_variants(value)

    def test_template_has_the_defaults_and_older_files_get_them_too(self):
        self.assertEqual(tomllib.loads(self.template())["cache"],
                         {"max_size_gb": 2.0, "variants_per_profile": 5, "latest_snapshot_size_gb": 0.5})
        app.PROFILE_CACHE_MAX_SIZE_GB, app.PROFILE_CACHE_VARIANTS = 7.5, 9
        app.PROFILE_CACHE_SNAPSHOT_SIZE_GB = 3.0
        older = self.template().replace("[cache]", "[cache_removed]")
        self.load(older)
        self.assertEqual((app.PROFILE_CACHE_MAX_SIZE_GB, app.PROFILE_CACHE_VARIANTS,
                          app.PROFILE_CACHE_SNAPSHOT_SIZE_GB), (2.0, 5, 0.5))
        self.load(app.replace_toml_values(self.template(), [
            ("cache", "max_size_gb", 4.5), ("cache", "variants_per_profile", 1),
            ("cache", "latest_snapshot_size_gb", 1.5),
        ]))
        self.assertEqual((app.PROFILE_CACHE_MAX_SIZE_GB, app.PROFILE_CACHE_VARIANTS,
                          app.PROFILE_CACHE_SNAPSHOT_SIZE_GB), (4.5, 1, 1.5))
        cache = app.get_profile_cache()
        self.assertEqual((cache.max_bytes, cache.max_variants), (4_500_000_000, 1))
        # Pictures without a profile, also of a modified profile, keep any
        # number of variants within their own size limit.
        self.assertEqual(cache.unlimited_variants, {app.latest_snapshot.CACHE_ID})
        self.assertEqual(cache.separate_limits, {app.latest_snapshot.CACHE_ID: 1_500_000_000})
        with self.assertRaises(ValueError):
            self.load(app.replace_toml_values(self.template(), [("cache", "latest_snapshot_size_gb", 12)]))
        with self.assertRaises(ValueError):
            self.load(app.replace_toml_values(self.template(), [("cache", "variants_per_profile", 0)]))

    def test_saving_adds_the_cache_section_to_an_older_configuration(self):
        older = "[download]\nretries = 2\n"
        updated = app.ensure_cache_configuration_section(older)
        self.assertEqual(updated, "[download]\nretries = 2\n\n[cache]\n")
        self.assertEqual(app.ensure_cache_configuration_section(updated), updated)
        saved = app.replace_toml_values(updated, [("cache", "max_size_gb", 0.5),
                                                  ("cache", "variants_per_profile", 3)])
        self.assertEqual(tomllib.loads(saved)["cache"], {"max_size_gb": 0.5, "variants_per_profile": 3})
        # A configuration saved before the Latest snapshot limit gets the key added.
        saved = app.replace_toml_values(saved, [("cache", "latest_snapshot_size_gb", 2.0)])
        self.assertEqual(tomllib.loads(saved)["cache"],
                         {"max_size_gb": 0.5, "variants_per_profile": 3, "latest_snapshot_size_gb": 2.0})

    def test_backups_reject_invalid_cache_limits(self):
        config = tomllib.loads(self.template())
        app.validate_backup_configuration(config)
        for key, value in (("max_size_gb", 0.3), ("max_size_gb", "2"),
                           ("variants_per_profile", 11), ("variants_per_profile", 2.0),
                           ("latest_snapshot_size_gb", 0.2), ("latest_snapshot_size_gb", "1")):
            damaged = tomllib.loads(self.template())
            damaged["cache"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                app.validate_backup_configuration(damaged)
        damaged = tomllib.loads(self.template())
        damaged["cache"] = "large"
        with self.assertRaises(ValueError):
            app.validate_backup_configuration(damaged)

    def test_estimate_adds_the_latest_snapshot_limit_to_the_profiles(self):
        # Saved profiles: variants per profile within the size limit.
        self.assertEqual(app.estimate_profile_cache(100_000_000, 6, 5, 2_000_000_000, 500_000_000),
                         (20 + 5, 2_500_000_000, True))
        self.assertEqual(app.estimate_profile_cache(100_000_000, 2, 1, 2_000_000_000, 500_000_000),
                         (2 + 5, 700_000_000, True))
        self.assertEqual(app.estimate_profile_cache(300_000_000, 2, 5, 1_000_000_000, 1_000_000_000),
                         (3 + 3, 1_800_000_000, True))
        # The current pictures stay even beyond the limits.
        self.assertEqual(app.estimate_profile_cache(1_000_000_000, 4, 5, 500_000_000, 500_000_000),
                         (4 + 1, 5_000_000_000, True))
        self.assertEqual(app.estimate_profile_cache(100_000_000, 0, 5, 2_000_000_000, 500_000_000),
                         (5, 500_000_000, True))
        self.assertEqual(app.estimate_profile_cache(None, 3, 2, 500_000_000, 500_000_000), (8, None, False))

    def test_storage_status_uses_unsaved_limits_and_adds_the_cache_to_the_total(self):
        size = 100_000_000
        with patch.object(app, "get_current_image_path", return_value=None), \
             patch.object(app, "get_latest_image_files", return_value=[]), \
             patch.object(app, "get_history_files", return_value=[]), \
             patch.object(app, "estimated_total_history_slots", return_value=3), \
             patch.object(app, "IMAGE_PROFILE_LIBRARY", {"items": [{"id": "1" * 32}]}), \
             patch.object(app, "get_profile_cache") as cache:
            # Never the developer's real cache.
            cache.return_value.status.return_value = {
                "profiles": 1, "variants": 3, "files": 3, "bytes": 12,
                "main": {"variants": 3, "files": 2, "bytes": 9},
                "separate": {app.latest_snapshot.CACHE_ID: {"variants": 4, "files": 1, "bytes": 3}},
            }
            status = app.get_storage_status()
            self.assertIsNone(status["estimated_total_bytes"])
            with patch.object(app, "get_current_image_path") as current:
                current.return_value.stat.return_value.st_size = size
                saved = app.get_storage_status()
                draft = app.get_storage_status(max_size_gb=0.5, variants=2, snapshot_size_gb=1.0)
        self.assertEqual(status["estimated_cache_images"], 2 * app.PROFILE_CACHE_VARIANTS)
        self.assertEqual(draft["cache_slots"], 1)
        # One profile with two variants, and ten pictures fill the snapshot's 1 GB.
        self.assertEqual((draft["estimated_cache_images"], draft["cache_limited"]), (2 + 10, True))
        self.assertEqual(draft["estimated_total_bytes"], size * (3 + 1 + 12))
        self.assertEqual(draft["estimated_maximum_images"], 3 + 1 + 12)
        self.assertEqual(draft["cache_snapshot_limit_bytes"], 1_000_000_000)
        self.assertEqual(saved["cache_variants"], app.PROFILE_CACHE_VARIANTS)
        self.assertEqual((saved["cache_variant_count"], saved["cache_files"], saved["used_bytes"]), (3, 3, 12))
        self.assertEqual((saved["cache_profile_files"], saved["cache_profile_bytes"]), (2, 9))
        self.assertEqual((saved["cache_snapshot_variants"], saved["cache_snapshot_files"],
                          saved["cache_snapshot_bytes"]), (4, 1, 3))


if __name__ == "__main__":
    unittest.main()
