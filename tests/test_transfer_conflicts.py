from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import marblescape_download as app
import marblescape_profile_transfer as transfer


class TransferConflictTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        settings = app.default_import_settings()
        self.items = [{"id": str(index) * 32, "name": f"Profile {index}", "settings": deepcopy(settings)}
                      for index in (1, 2, 3)]

    def export(self, items=None, decision=None, **kwargs):
        return transfer.export_profiles(self.root, self.items[:2] if items is None else items,
            app.normalize_image_settings_snapshot, confirm_conflict=decision, **kwargs)

    def read(self, paths, existing=(), decision=None):
        return transfer.import_profiles(paths, list(existing), app.default_import_settings,
            app.normalize_image_settings_snapshot, confirm_conflict=decision)

    def changed(self):
        changed = deepcopy(self.items[:2])
        for item in changed:
            item["settings"]["view"]["zoom"] = 2.0
        return changed

    def test_filename_uses_single_underscores_even_inside_name(self):
        item = deepcopy(self.items[0])
        item["name"] = "__Name__:/__"
        filename = transfer.profile_export_filename(item)
        self.assertNotIn("__", filename)
        self.assertTrue(filename.startswith("MarbleScape_Profile_"))
        self.assertIn("11111111-1111-1111-1111-111111111111", filename)

    def test_single_save_as_honors_custom_filename(self):
        paths = self.export(self.items[:1], destination=self.root / "Chosen file")
        self.assertEqual(paths, [self.root / "Chosen file.json"])
        self.assertEqual(json.loads(paths[0].read_text())["profiles"][0]["id"], self.items[0]["id"])
        with self.assertRaisesRegex(ValueError, "single profile"):
            self.export(destination=self.root / "invalid.json")

    def test_overwrite_all_replaces_each_file_and_is_not_remembered(self):
        paths = self.export()
        original = [path.read_bytes() for path in paths]
        confirm = Mock(return_value="overwrite_all")
        self.assertEqual(self.export(self.changed(), confirm), paths)
        confirm.assert_called_once()
        for index, path in enumerate(paths):
            self.assertNotEqual(path.read_bytes(), original[index])
            self.assertEqual(json.loads(path.read_text())["profiles"][0]["settings"]["view"]["zoom"], 2)
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.export()
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_skip_all_skips_conflicts_only(self):
        existing = self.export()
        original = [path.read_bytes() for path in existing]
        confirm = Mock(return_value="skip_all")
        paths = self.export(self.items, confirm)
        confirm.assert_called_once()
        self.assertEqual(paths, [self.root / transfer.profile_export_filename(self.items[2])])
        self.assertEqual([path.read_bytes() for path in existing], original)

    def test_rename_numbers_filenames_but_keeps_embedded_identity(self):
        original = self.export(self.items[:1])[0]
        copy = self.export(self.items[:1], lambda _: "rename")[0]
        next_copy = self.export(self.items[:1], lambda _: "rename")[0]
        self.assertEqual(copy.stem, original.stem + " (Copy)")
        self.assertEqual(next_copy.stem, original.stem + " (Copy 1)")
        self.assertEqual(original.read_bytes(), copy.read_bytes())
        self.assertEqual(original.read_bytes(), next_copy.read_bytes())

    def test_cancel_second_conflict_changes_no_files(self):
        paths = self.export()
        before = {path.name: path.read_bytes() for path in paths}
        with self.assertRaises(transfer.ImportCancelled):
            self.export(self.changed(), Mock(side_effect=["overwrite", "cancel"]))
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_all_profiles_validated_before_first_export_question(self):
        paths = self.export()
        before = [path.read_bytes() for path in paths]
        invalid = self.changed()
        invalid[1]["settings"]["output"].pop("render_scale")
        confirm = Mock(return_value="overwrite_all")
        with self.assertRaises(ValueError):
            self.export(invalid, confirm)
        confirm.assert_not_called()
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_later_write_failure_restores_original_files(self):
        paths = self.export()
        before = [path.read_bytes() for path in paths]
        replace = transfer.os.replace

        def fail_second(source, destination):
            if Path(destination) == paths[1] and Path(source).name.startswith(".marblescape-profile-"):
                raise OSError("Simulated disk failure")
            return replace(source, destination)
        with patch.object(transfer.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "Simulated"):
                self.export(self.changed(), lambda _: "overwrite_all")
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertEqual(set(self.root.iterdir()), set(paths))

    def test_concurrent_change_is_not_overwritten(self):
        path = self.export(self.items[:1])[0]
        def decide(_context):
            path.write_text("External change", encoding="utf-8")
            return "overwrite"
        with self.assertRaisesRegex(ValueError, "Destination changed"):
            self.export(self.items[:1], decide)
        self.assertEqual(path.read_text(), "External change")

    def test_import_preserves_uuid_and_overwrites_by_id_at_same_position(self):
        paths = self.export()
        result = self.read(paths)
        self.assertEqual([item["id"] for item in result.items], [item["id"] for item in self.items[:2]])
        existing = deepcopy(self.items)
        existing[0]["name"] = "Renamed locally"
        before = deepcopy(existing)
        confirm = Mock(return_value="overwrite_all")
        result = self.read(paths, existing, confirm)
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[0]["reason"], "UUID")
        self.assertEqual(result.items[0]["name"], self.items[0]["name"])
        self.assertEqual([item["id"] for item in result.items], [item["id"] for item in existing])
        self.assertEqual(existing, before)

    def test_same_name_with_different_uuid_is_kept_as_marked_second_profile(self):
        paths = self.export(self.items[:1])
        second = deepcopy(self.items[:1])
        second[0]["id"] = "4" * 32
        second_path = self.root / "second.json"
        self.export(second, destination=second_path)
        existing = deepcopy(self.items[1:])
        existing[0]["name"] = self.items[0]["name"].upper()
        before = deepcopy(existing)
        confirm = Mock(return_value="overwrite")
        result = self.read(paths + [second_path], existing, confirm)
        confirm.assert_not_called()
        self.assertEqual(result.items[:2], before)
        self.assertEqual([(item["id"], item["name"]) for item in result.items[2:]],
                         [(self.items[0]["id"], "Profile 1 (Imported)"),
                          ("4" * 32, "Profile 1 (Imported 2)")])
        self.assertEqual(result.renamed, ["Profile 1 -> Profile 1 (Imported)",
                                          "Profile 1 -> Profile 1 (Imported 2)"])
        self.assertFalse(result.warnings)
        self.assertEqual(existing, before)

    def test_imported_suffix_is_capitalized_and_replaces_older_lowercase_suffix(self):
        taken = {"bahamas (imported)"}
        self.assertEqual(transfer.imported_name("Bahamas", lambda name: name.casefold() in taken),
                         "Bahamas (Imported 2)")
        self.assertEqual(transfer.imported_name("Bahamas (imported)", lambda name: False),
                         "Bahamas (Imported)")

    def test_overwrite_by_uuid_marks_name_already_used_by_another_profile(self):
        path = self.export(self.items[:1])[0]
        existing = deepcopy(self.items[:2])
        existing[0]["name"] = "Old local name"
        existing[1]["name"] = self.items[0]["name"].lower()
        result = self.read([path], existing, lambda _: "overwrite")
        self.assertEqual([(item["id"], item["name"]) for item in result.items],
                         [(self.items[0]["id"], "Profile 1 (Imported)"),
                          (self.items[1]["id"], "profile 1")])
        self.assertEqual(result.renamed, ["Profile 1 -> Profile 1 (Imported)"])
        # Overwriting the only profile with that name keeps the name unchanged.
        result = self.read([path], self.items[:1], lambda _: "overwrite")
        self.assertEqual(result.items[0]["name"], self.items[0]["name"])
        self.assertEqual(result.renamed, [])

    def test_import_copy_has_new_uuid_and_incrementing_name(self):
        path = self.export(self.items[:1])[0]
        existing = deepcopy(self.items[:1])
        first = self.read([path], existing, lambda _: "rename")
        second = self.read([path], first.items, lambda _: "rename")
        self.assertEqual([item["name"] for item in second.items], ["Profile 1", "Profile 1 (Copy)", "Profile 1 (Copy 1)"])
        self.assertEqual(len({item["id"] for item in second.items}), 3)
        self.assertEqual(existing, self.items[:1])

    def test_import_skip_all_keeps_existing_and_imports_nonconflicting_profile(self):
        paths = self.export(self.items)
        confirm = Mock(return_value="skip_all")
        result = self.read(paths, self.items[:2], confirm)
        confirm.assert_called_once()
        self.assertEqual(len(result.imported), 1)
        self.assertEqual(result.imported[0]["id"], self.items[2]["id"])
        self.assertEqual(result.items[:2], self.items[:2])
        self.assertEqual(len(result.warnings), 2)

    def test_import_cancel_rolls_back_prior_decisions(self):
        paths = self.export()
        existing = deepcopy(self.items)
        before = deepcopy(existing)
        with self.assertRaises(transfer.ImportCancelled):
            self.read(paths, existing, Mock(side_effect=["overwrite", "cancel"]))
        self.assertEqual(existing, before)

    def test_multiple_same_names_cannot_choose_an_arbitrary_overwrite_target(self):
        # Only legacy records without a UUID are matched by name.
        settings = transfer.portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        path = self.root / "legacy.json"
        path.write_text(json.dumps({"format": transfer.FORMAT, "version": 1,
            "profiles": [{"name": self.items[0]["name"], "settings": settings}]}), encoding="utf-8")
        existing = deepcopy(self.items[1:])
        for item in existing:
            item["name"] = self.items[0]["name"]
        confirm = Mock(return_value="skip")
        result = self.read([path], existing, confirm)
        self.assertFalse(confirm.call_args.args[0]["overwrite_allowed"])
        self.assertEqual(result.items, existing)

    def test_repeated_input_is_also_a_conflict_and_does_not_duplicate_uuid(self):
        path = self.export(self.items[:1])[0]
        confirm = Mock(return_value="overwrite")
        result = self.read([path, path], (), confirm)
        confirm.assert_called_once()
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0]["id"], self.items[0]["id"])

    def test_no_callback_cannot_silently_overwrite_existing_profile(self):
        path = self.export(self.items[:1])[0]
        result = self.read([path], self.items)
        self.assertFalse(result.imported)
        self.assertEqual(result.items, self.items)
        self.assertIn("confirmation required", result.warnings[0])

    def test_legacy_record_without_uuid_keeps_existing_id_on_overwrite(self):
        settings = transfer.portable_settings(self.items[0]["settings"], app.normalize_image_settings_snapshot)
        path = self.root / "legacy.json"
        path.write_text(json.dumps({"format": transfer.FORMAT, "version": 1,
            "profiles": [{"name": self.items[0]["name"], "settings": settings}]}), encoding="utf-8")
        confirm = Mock(return_value="overwrite")
        result = self.read([path], self.items[:1], confirm)
        self.assertFalse(confirm.call_args.args[0]["has_id"])
        self.assertEqual(result.items[0]["id"], self.items[0]["id"])

    def test_full_library_can_replace_without_exceeding_profile_limit(self):
        existing = [{"id": f"{index:032x}", "name": f"Item {index}", "settings": deepcopy(self.items[0]["settings"])}
                    for index in range(100)]
        path = self.export(existing[:1])[0]
        result = self.read([path], existing, lambda _: "overwrite")
        self.assertEqual(len(result.items), 100)
        self.assertEqual(len(result.imported), 1)
        self.assertFalse(result.warnings)
