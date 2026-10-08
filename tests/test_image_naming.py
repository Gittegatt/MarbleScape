from contextlib import ExitStack
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
from marblescape_cache import ProfileImageCache
from marblescape_image_metadata import embed_png_metadata
from marblescape_image_naming import descriptive_image_filename, image_filename_from_png, unused_image_path


class ImageNamingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.latest = self.root / "latest"
        self.history = self.root / "history"
        self.latest.mkdir()
        self.history.mkdir()
        self.record = {"software": "MarbleScape", "schema_version": 3,
            "generated_at_utc": "2026-09-26T21:00:00.123456Z", "profile_name": "Whitsundays",
            "profile_id": "1" * 32, "source": "Copernicus Browser",
            "product": "Sentinel-2 Quarterly Mosaics", "mosaic_period": "2026-Q2",
            "width": 8, "height": 6, "profile_settings": {"source": {"provider": "copernicus"}}}

    def png(self, record=None, color=(20, 40, 60)):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6), color).save(buffer, format="PNG")
        return embed_png_metadata(buffer.getvalue(), self.record if record is None else record)

    def runtime(self):
        stack = ExitStack()
        for key, value in (("LATEST_DIR", self.latest), ("HISTORY_DIR", self.history), ("ENABLE_HISTORY", True)):
            stack.enter_context(patch.object(app, key, value))
        stack.enter_context(patch.object(app, "log"))
        return stack

    def test_filename_holds_creation_utc_profile_name_and_image_hash(self):
        data = self.png()
        digest = hashlib.sha256(data).hexdigest()
        name = image_filename_from_png(data)
        self.assertEqual(name, f"MarbleScape_2026-09-26T210000Z_Whitsundays_{digest[:12]}.png")
        path = self.root / "hash.png"
        path.write_bytes(data)
        self.assertEqual(image_filename_from_png(path), name)
        self.assertEqual(path.read_bytes(), data)

    def test_source_product_acquisition_and_size_stay_out_of_the_filename(self):
        # They remain in the embedded record; only the hash tells the images apart.
        record = deepcopy(self.record)
        record.update(product="Sentinel-1 Monthly Mosaics", mosaic_period="2026-08",
                      source_time_utc="2026-09-25T12:00:00Z", coverage_mode="fill_gaps")
        data = self.png(record)
        name = image_filename_from_png(data)
        self.assertEqual(name, f"MarbleScape_2026-09-26T210000Z_Whitsundays_{hashlib.sha256(data).hexdigest()[:12]}.png")
        for token in ("Copernicus", "S1-Monthly", "2026-08", "2026-09-25", "8x6", "ref-"):
            self.assertNotIn(token, name)

    def test_missing_times_are_explicit_not_invented(self):
        record = deepcopy(self.record)
        for key in ("generated_at_utc", "mosaic_period"):
            record.pop(key)
        name = image_filename_from_png(self.png(record))
        self.assertIn("_created-unknown_", name)
        # A date-only creation value is not turned into an invented time of day.
        record["generated_at_utc"] = "2026-09-26"
        self.assertIn("_created-unknown_", image_filename_from_png(self.png(record)))

    def test_no_profile_uses_location_and_no_double_underscores(self):
        record = deepcopy(self.record)
        record.pop("profile_name")
        record.update(latitude=-20.283, longitude=149.04)
        name = image_filename_from_png(self.png(record))
        self.assertIn("_lat-20.2830-lon149.0400_", name)
        self.assertNotIn("__", name)

    def test_every_provider_without_profile_names_its_selection(self):
        for provider in ("eumetsat", "goes_east", "goes_west", "solar", "himawari", "slider", "worldview"):
            record = deepcopy(self.record)
            record.pop("profile_name")
            record.pop("mosaic_period")
            record.update(source=provider, product="GEOCOLOR", area="Full disk", source_time_utc="2026-09-26")
            record["profile_settings"]["source"]["provider"] = provider
            name = image_filename_from_png(self.png(record))
            place = "Custom" if provider == "worldview" else "Full-disk"
            self.assertRegex(name, rf"^MarbleScape_2026-09-26T210000Z_{place}_[0-9a-f]{{12}}\.png$")

    def test_long_unicode_and_unsafe_names_are_bounded_and_filename_only(self):
        record = deepcopy(self.record)
        record.update(profile_name='../../海🌋:<>|"?*\\__ ' * 20, product="very-long-product_" * 30)
        name = image_filename_from_png(self.png(record))
        self.assertEqual(Path(name).name, name)
        self.assertFalse(re.search(r'[<>:"/\\|?*\x00-\x1f]', name))
        self.assertLessEqual(len(name.encode("utf-16-le")) // 2, 180)
        self.assertNotIn("__", name)

    def test_invalid_metadata_and_plain_png_use_legacy_fallback(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6)).save(buffer, format="PNG")
        self.assertIsNone(image_filename_from_png(buffer.getvalue()))
        record = deepcopy(self.record)
        record["width"] = 99
        self.assertIsNone(image_filename_from_png(self.png(record)))
        self.assertIsNone(image_filename_from_png(b"not a png"))
        with self.assertRaises(ValueError):
            descriptive_image_filename(self.record, "invalid")

    def test_collision_adds_suffix_without_changing_time_or_original_file(self):
        name = image_filename_from_png(self.png())
        original = self.history / name
        original.write_bytes(b"keep")
        unused = unused_image_path(self.history, name)
        self.assertEqual(unused.stem, original.stem + "_1")
        unused.write_bytes(b"keep too")
        self.assertEqual(unused_image_path(self.history, name).stem, original.stem + "_2")
        self.assertEqual(original.read_bytes(), b"keep")

    def test_latest_and_history_keep_identical_filename_pixels_and_exif(self):
        first = self.png()
        second_record = deepcopy(self.record)
        second_record.update(generated_at_utc="2026-09-27T10:00:00Z", mosaic_period="2026-Q3")
        second = self.png(second_record, (30, 50, 70))
        with self.runtime():
            installed = app.save_latest_image(first, {"settings": 1}, (8, 6), {"source": 1})
            self.assertEqual(installed.name, image_filename_from_png(first))
            self.assertEqual(app.reusable_latest_image({"settings": 1}, (8, 6), {"source": 1}), installed)
            self.assertIsNone(app.save_latest_image(first, {"settings": 1}, (8, 6), {"source": 1}))
            self.assertEqual(app.get_history_files(), [])
            newest = app.save_latest_image(second, {"settings": 1}, (8, 6), {"source": 2})
            archived = self.history / "_no profile" / installed.name
            self.assertEqual(app.get_history_files(), [archived])
            self.assertEqual(app.get_latest_image_files(), [newest])
            self.assertEqual(archived.read_bytes(), first)
            self.assertEqual(newest.read_bytes(), second)
            with Image.open(archived) as picture:
                self.assertEqual(json.loads(picture.getexif()[270]), self.record)
                self.assertEqual(picture.getpixel((0, 0)), (20, 40, 60))

    def test_cache_names_stay_hashes_but_new_archive_names_are_readable(self):
        first = self.png()
        second_record = deepcopy(self.record)
        second_record["mosaic_period"] = "2026-Q3"
        second = self.png(second_record, (30, 50, 70))
        cache = ProfileImageCache(self.root)
        with self.runtime(), patch.object(app, "get_profile_cache", return_value=cache):
            _installed, current = app.save_profile_image("1" * 32, {"settings": 1}, {"source": 1}, first, (8, 6))
            self.assertEqual(current.stem, hashlib.sha256(first).hexdigest())
            _installed, current = app.save_profile_image("1" * 32, {"settings": 1}, {"source": 2}, second, (8, 6))
            self.assertEqual(current.stem, hashlib.sha256(second).hexdigest())
            archives = app.get_history_files()
            self.assertEqual([path.name for path in archives], [image_filename_from_png(first)])
            self.assertEqual(archives[0].read_bytes(), first)

    def test_legacy_images_not_renamed_and_retention_handles_both_prefixes(self):
        data = self.png()
        existing_latest = self.latest / "marblescape_2026-01-01_000000.png"
        existing_latest.write_bytes(data)
        old_archive = self.history / "marblescape_2026-02-01_000000.png"
        old_archive.write_bytes(data)
        new_archive = self.history / image_filename_from_png(data)
        new_archive.write_bytes(data)
        unrelated = self.history / "keep.png"
        unrelated.write_bytes(data)
        with self.runtime():
            self.assertEqual(app.generate_history_path(existing_latest).name, existing_latest.name)
            self.assertIsNone(app.save_latest_image(data))
            self.assertEqual(list(self.latest.glob("*.png")), [existing_latest])
            self.assertEqual(set(app.get_history_files()), {old_archive, new_archive})
            with patch.object(app, "HISTORY_RETENTION_MODE", "count"), patch.object(app, "HISTORY_MAX_FILES", 0):
                self.assertEqual(app.cleanup_history(), 2)
            self.assertTrue(unrelated.exists())
            self.assertTrue(existing_latest.exists())
