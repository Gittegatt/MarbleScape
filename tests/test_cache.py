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
        self.assertEqual(self.cache.status(),
                         {"profiles": 2, "variants": 2, "files": 1, "bytes": len(red)})

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

    def test_a_picture_without_image_data_is_never_reused(self):
        from marblescape_image_metadata import embed_png_metadata
        configuration = {"source": "copernicus", "width": 8, "height": 6}
        source = ("2026-09-28T00:00:00Z",)
        empty = embed_png_metadata(png("black"), {"software": "MarbleScape", "width": 8, "height": 6, "data_coverage_percent": 0.0})
        path, _previous = self.cache.install(self.first_id, configuration, source, empty, (8, 6))
        self.assertIsNone(self.cache.lookup(self.first_id, configuration, source, (8, 6)))
        self.assertIsNone(self.cache.lookup_configuration(self.first_id, configuration, (8, 6)))
        self.assertIsNone(self.cache.current(self.first_id))
        # Pictures with data, or without a coverage record, are reused as before.
        partial = embed_png_metadata(png("blue"), {"software": "MarbleScape", "width": 8, "height": 6, "data_coverage_percent": 0.5})
        kept, _previous = self.cache.install(self.first_id, configuration, source, partial, (8, 6))
        self.assertEqual(self.cache.lookup(self.first_id, configuration, source, (8, 6)), kept)
        plain, _previous = self.cache.install(self.second_id, configuration, ("x",), png("red"), (8, 6))
        self.assertEqual(self.cache.lookup(self.second_id, configuration, ("x",), (8, 6)), plain)

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
        self.assertEqual(self.cache.status(), {"profiles": 0, "variants": 0, "files": 0, "bytes": 0})

        self.cache.database_path.write_bytes(b"corrupt sqlite")
        recovered = self.cache.clear()
        self.assertEqual(recovered["profiles"], 0)
        with closing(sqlite3.connect(self.cache.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

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
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(profile_images)")}
        self.assertIn("source_time", columns)
        self.assertIn("used_at", columns)

    def test_version_two_pictures_become_the_first_variants(self):
        self.root.mkdir(parents=True)
        data = png("orange")
        stored = self.cache.install(self.first_id, {"layer": "a"}, {}, data, (8, 6))[0]
        image_hash = stored.stem
        self.cache.database_path.unlink()
        with closing(sqlite3.connect(self.cache.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE profile_images (
                    profile_id TEXT PRIMARY KEY, configuration_hash TEXT NOT NULL,
                    source_hash TEXT NOT NULL, image_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL, width INTEGER NOT NULL,
                    height INTEGER NOT NULL, renderer_version INTEGER NOT NULL,
                    source_time TEXT, updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO profile_images VALUES (?, ?, ?, ?, ?, 8, 6, 1, ?, ?)",
                (self.first_id, signature_digest({"layer": "a"}), signature_digest({}),
                 image_hash, len(data), "2026-09-13T10:20:00Z", "2026-09-14T08:00:00+00:00"),
            )
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        cache = ProfileImageCache(self.root)
        self.assertEqual(cache.current(self.first_id), stored)
        self.assertEqual(cache.lookup_configuration(self.first_id, {"layer": "a"}, (8, 6)), stored)
        entry = cache.entries()[self.first_id]
        self.assertEqual((entry["source_time"], entry["variants"]), ("2026-09-13T10:20:00Z", 1))
        with closing(sqlite3.connect(cache.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"profile_images"})

    def test_each_image_setting_keeps_its_own_variant(self):
        red, blue, green = png("red"), png("blue"), png("green")
        red_path = self.cache.install(self.first_id, {"layer": "a"}, ("t1",), red, (8, 6))[0]
        blue_path, previous = self.cache.install(self.first_id, {"layer": "b"}, ("t1",), blue, (8, 6))
        # The replaced current picture is still reported, so History can archive it.
        self.assertEqual(previous, red_path)
        self.assertEqual(self.cache.current(self.first_id), blue_path)
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 2)
        # Returning to the first settings finds their picture and makes it current.
        self.assertEqual(self.cache.lookup(self.first_id, {"layer": "a"}, ("t1",), (8, 6)), red_path)
        self.assertEqual(self.cache.current(self.first_id), red_path)
        self.assertEqual(self.cache.lookup_configuration(self.first_id, {"layer": "b"}, (8, 6)),
                         blue_path)
        self.assertEqual(self.cache.current(self.first_id), blue_path)
        # Another size is another variant.
        self.cache.install(self.first_id, {"layer": "b"}, ("t1",), png("blue", (4, 3)), (4, 3))
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 3)
        # A newer frame replaces the variant of the same settings and size.
        green_path, previous = self.cache.install(self.first_id, {"layer": "a"}, ("t2",), green, (8, 6))
        self.assertNotEqual(previous, red_path)
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 3)
        self.assertIsNone(self.cache.lookup(self.first_id, {"layer": "a"}, ("t1",), (8, 6)))
        self.assertEqual(self.cache.prune(), 1)
        self.assertFalse(red_path.exists())
        self.assertTrue(green_path.exists())

    def test_variants_beyond_the_limit_drop_the_least_recently_used(self):
        self.cache.set_limits(10 ** 9, 2)
        paths = [self.cache.install(self.first_id, {"layer": name}, (), png(color), (8, 6))[0]
                 for name, color in (("a", "red"), ("b", "blue"))]
        self.cache.lookup_configuration(self.first_id, {"layer": "a"}, (8, 6))
        third, previous = self.cache.install(self.first_id, {"layer": "c"}, (), png("green"), (8, 6))
        # "b" was used least recently; "a" was looked at again.
        self.assertEqual(previous, paths[0])
        self.assertIsNone(self.cache.lookup_configuration(self.first_id, {"layer": "b"}, (8, 6)))
        self.assertEqual(self.cache.lookup_configuration(self.first_id, {"layer": "a"}, (8, 6)), paths[0])
        # Install leaves the file for the caller's archive; prune removes it.
        self.assertTrue(paths[1].exists())
        self.assertEqual(self.cache.prune(), 1)
        self.assertFalse(paths[1].exists())
        self.assertEqual(self.cache.status()["variants"], 2)
        # Fewer variants per profile take effect on the next maintenance.
        self.cache.set_limits(10 ** 9, 1)
        self.assertEqual(self.cache.enforce_limits(), 1)
        self.assertEqual(self.cache.current(self.first_id), paths[0])
        self.assertFalse(third.exists())

    def test_a_separate_limit_never_evicts_the_other_profiles_pictures(self):
        snapshot_id = "3" * 32
        red, blue = png("red"), png("blue")
        kept = [self.cache.install(self.first_id, {"layer": name}, (), data, (8, 6))[0]
                for name, data in (("a", red), ("b", blue))]
        # The snapshot slot keeps any number of variants within its own budget
        # of two files; the profile's two files fit the main budget exactly.
        self.cache.set_limits(len(red) + len(blue), 5, unlimited_variants=(snapshot_id,),
                              separate_limits={snapshot_id: 2 * len(red)})
        colors = ("green", "yellow", "purple", "orange")
        snapshots = [self.cache.install(snapshot_id, {"layer": color}, (), png(color), (8, 6))[0]
                     for color in colors]
        self.cache.enforce_limits()
        # Install leaves dropped files for the caller's archive; prune removes them.
        self.cache.prune()
        for path in kept:
            self.assertTrue(path.exists())
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 2)
        # The snapshot's oldest variants went; its current picture stays.
        self.assertEqual(self.cache.current(snapshot_id), snapshots[-1])
        self.assertLessEqual(self.cache.entries()[snapshot_id]["variants"], 3)
        self.assertFalse(snapshots[0].exists())
        status = self.cache.status()
        self.assertEqual((status["profiles"], status["variants"]), (1, 2))
        self.assertEqual(status["main"], {"variants": 2, "files": 2, "bytes": len(red) + len(blue)})
        separate = status["separate"][snapshot_id]
        self.assertLessEqual(separate["bytes"], 2 * len(red) + 64)
        self.assertEqual(status["files"], 2 + separate["files"])
        # Snapshot pictures never count toward the main budget either.
        self.cache.set_limits(len(red) + len(blue), 5, unlimited_variants=(snapshot_id,),
                              separate_limits={snapshot_id: 10 ** 9})
        for color in ("white", "black", "gray"):
            self.cache.install(snapshot_id, {"layer": color}, (), png(color), (8, 6))
        self.cache.enforce_limits()
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 2)
        with self.assertRaises(ValueError):
            self.cache.set_limits(10, 5, separate_limits={snapshot_id: 0})

    def test_size_limit_keeps_every_current_picture_and_counts_shared_files_once(self):
        red, blue, green = png("red"), png("blue"), png("green")
        red_path = self.cache.install(self.first_id, {"layer": "a"}, (), red, (8, 6))[0]
        blue_path = self.cache.install(self.first_id, {"layer": "b"}, (), blue, (8, 6))[0]
        self.assertEqual(self.cache.lookup(self.second_id, {"layer": "a"}, (), (8, 6)), red_path)
        # Three variants share two files, which fit exactly.
        self.cache.set_limits(len(red) + len(blue), 5)
        self.assertEqual(self.cache.enforce_limits(), 0)
        self.assertEqual(self.cache.status()["variants"], 3)
        # Dropping the first profile's older variant would free nothing: its
        # file is the second profile's current picture.
        self.cache.set_limits(1, 5)
        self.assertEqual(self.cache.enforce_limits(), 0)
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 2)
        # Once that file is current nowhere, the too small limit drops it;
        # the current pictures stay even beyond the limit.
        green_path = self.cache.install(self.second_id, {"layer": "c"}, (), green, (8, 6))[0]
        self.cache.prune()
        self.assertFalse(red_path.exists())
        entries = self.cache.entries()
        self.assertEqual({key: value["variants"] for key, value in entries.items()},
                         {self.first_id: 1, self.second_id: 1})
        self.assertEqual(self.cache.current(self.first_id), blue_path)
        self.assertEqual(self.cache.current(self.second_id), green_path)
        self.assertEqual(self.cache.enforce_limits(), 0)

    def test_an_unlimited_slot_keeps_any_number_of_variants_within_the_size(self):
        self.cache.set_limits(10 ** 9, 1, unlimited_variants=[self.first_id])
        colors = ("red", "blue", "green", "white")
        for name, color in zip("abcd", colors):
            self.cache.install(self.first_id, {"layer": name}, (), png(color), (8, 6))
            self.cache.install(self.second_id, {"layer": name}, (), png(color, (4, 3)), (4, 3))
        entries = self.cache.entries()
        self.assertEqual((entries[self.first_id]["variants"], entries[self.second_id]["variants"]), (4, 1))
        # Returning to an earlier variant of the unlimited slot needs no download.
        self.assertIsNotNone(self.cache.lookup_configuration(self.first_id, {"layer": "a"}, (8, 6)))
        # The size limit still applies to it; its current picture stays.
        self.cache.set_limits(1, 1, unlimited_variants=[self.first_id])
        self.cache.enforce_limits()
        self.assertEqual(self.cache.entries()[self.first_id]["variants"], 1)
        self.assertIsNotNone(self.cache.lookup_configuration(self.first_id, {"layer": "a"}, (8, 6)))
        with self.assertRaises(ValueError):
            self.cache.set_limits(1, 1, unlimited_variants=["not a profile id"])

    def test_limits_must_be_positive_whole_numbers(self):
        for max_bytes, variants in ((0, 1), (1, 0), (1.5, 1), (1, True), (10, "2")):
            with self.subTest(max_bytes=max_bytes, variants=variants):
                with self.assertRaises(ValueError):
                    self.cache.set_limits(max_bytes, variants)


if __name__ == "__main__":
    unittest.main()
