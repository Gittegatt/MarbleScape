import io
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image

from marblescape_cache import ProfileImageCache, signature_digest


def png(color, size=(8, 6)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class ProfileImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="marblescape-cache-test-")
        self.root = Path(self.temporary.name) / "content"
        self.cache = ProfileImageCache(self.root)
        self.first_id = "1" * 32
        self.second_id = "2" * 32

    def tearDown(self):
        self.temporary.cleanup()

    def test_signature_is_stable_for_paths_tuples_and_dictionary_order(self):
        first = {"path": Path("folder/file"), "values": (1, True), "nested": {"b": 2, "a": 1}}
        second = {"nested": {"a": 1, "b": 2}, "values": [1, True], "path": Path("folder/file")}
        self.assertEqual(signature_digest(first), signature_digest(second))
        with self.assertRaises(ValueError):
            signature_digest({"bad": float("nan")})

    def test_install_lookup_cross_profile_reuse_history_pointer_and_prune(self):
        red = png("red")
        blue = png("blue")
        configuration = {"source": "copernicus", "width": 8, "height": 6}
        source_one = ("2026-09-12T10:00:00Z",)
        source_two = ("2026-09-13T10:00:00Z",)

        first_path, previous = self.cache.install(
            self.first_id, configuration, source_one, red, (8, 6),
            source_time="2026-09-12T10:00:00Z",
        )
        self.assertIsNone(previous)
        self.assertEqual(first_path.read_bytes(), red)
        self.assertEqual(
            self.cache.lookup(self.first_id, configuration, source_one, (8, 6)),
            first_path,
        )

        # A second profile with the exact same render key reuses one immutable file.
        self.assertEqual(
            self.cache.lookup(self.second_id, configuration, source_one, (8, 6),
                              source_time="2026-09-12T10:00:00Z"),
            first_path,
        )
        metadata = self.cache.entries([self.first_id, self.second_id])
        self.assertEqual(metadata[self.first_id]["source_time"], "2026-09-12T10:00:00Z")
        self.assertEqual(metadata[self.second_id]["source_time"], "2026-09-12T10:00:00Z")
        self.assertEqual(metadata[self.first_id]["width"], 8)
        self.assertEqual(self.cache.status(), {"profiles": 2, "files": 1, "bytes": len(red)})

        second_path, previous = self.cache.install(
            self.first_id, configuration, source_two, blue, (8, 6)
        )
        self.assertEqual(previous, first_path)
        self.assertNotEqual(second_path, first_path)
        self.assertEqual(self.cache.prune(), 0)

        rebound = self.cache.lookup(self.second_id, configuration, source_two, (8, 6))
        self.assertEqual(rebound, second_path)
        self.assertEqual(self.cache.prune(), 1)
        self.assertFalse(first_path.exists())
        self.assertEqual(self.cache.status()["files"], 1)
        removed = self.cache.retain_profiles([self.first_id])
        self.assertEqual(removed["profiles"], 1)
        self.assertEqual(self.cache.status()["profiles"], 1)

    def test_corrupt_or_wrong_sized_images_are_not_reused(self):
        configuration = {"source": "noaa"}
        source = {"timestamp": "2026-09-13T10:00:00Z"}
        path, _previous = self.cache.install(
            self.first_id, configuration, source, png("green"), (8, 6)
        )
        path.write_bytes(b"not a png")
        self.assertIsNone(self.cache.lookup(self.first_id, configuration, source, (8, 6)))
        self.assertIsNone(self.cache.current(self.first_id))

        with self.assertRaises(ValueError):
            self.cache.install(self.first_id, configuration, source, png("green"), (7, 6))
        with self.assertRaises(ValueError):
            self.cache.lookup("unsafe/name", configuration, source, (8, 6))

    def test_clear_removes_only_cache_content_and_recovers_corrupt_database(self):
        unrelated = self.root / "latest" / "keep.png"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(png("white"))
        self.cache.install(self.first_id, {}, {}, png("black"), (8, 6))
        before = self.cache.clear()
        self.assertEqual(before["profiles"], 1)
        self.assertEqual(before["files"], 1)
        self.assertTrue(unrelated.exists())
        self.assertEqual(self.cache.status(), {"profiles": 0, "files": 0, "bytes": 0})

        self.cache.database_path.write_bytes(b"corrupt sqlite")
        recovered = self.cache.clear()
        self.assertEqual(recovered["profiles"], 0)
        with closing(sqlite3.connect(self.cache.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_version_one_database_is_migrated_without_losing_cached_images(self):
        self.root.mkdir(parents=True)
        with closing(sqlite3.connect(self.cache.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE profile_images (
                    profile_id TEXT PRIMARY KEY, configuration_hash TEXT NOT NULL,
                    source_hash TEXT NOT NULL, image_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL, width INTEGER NOT NULL,
                    height INTEGER NOT NULL, renderer_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        self.cache.install(
            self.first_id, {}, {}, png("purple"), (8, 6),
            source_time="2026-09-13T10:20:00Z",
        )
        self.assertEqual(
            self.cache.entries()[self.first_id]["source_time"],
            "2026-09-13T10:20:00Z",
        )
        with closing(sqlite3.connect(self.cache.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(profile_images)")}
        self.assertIn("source_time", columns)


if __name__ == "__main__":
    unittest.main()
