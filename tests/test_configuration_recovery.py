"""Startup recovery when the tray cannot load its configuration."""

from pathlib import Path
import datetime as dt
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import marblescape_download as app


class LoadTrayConfigurationTests(unittest.TestCase):
    def test_recovery_retries_until_the_configuration_loads(self):
        error = ValueError("broken setting")
        recovered = []
        with patch.object(app, "load_configuration", side_effect=[error, None]) as load, \
             patch.object(app, "validate_configuration"), \
             patch.object(app, "save_working_settings_backup_safely") as backup:
            args = app.load_tray_configuration(
                ["--config", "custom.toml"], False, lambda exc: recovered.append(exc) or True)
        self.assertEqual(Path(args.config), Path("custom.toml"))
        self.assertEqual(recovered, [error])
        self.assertEqual(load.call_count, 2)
        backup.assert_called_once()

    def test_validation_errors_reach_recovery_and_are_not_backed_up(self):
        error = ValueError("At least one enabled layer with opacity above zero is required.")
        recovered = []
        with patch.object(app, "load_configuration"), \
             patch.object(app, "validate_configuration", side_effect=error), \
             patch.object(app, "save_working_settings_backup_safely") as backup:
            self.assertIsNone(app.load_tray_configuration(
                [], False, lambda exc: recovered.append(exc) and False))
        self.assertEqual(recovered, [error])
        backup.assert_not_called()

    def test_exit_from_recovery_stops_the_start(self):
        with patch.object(app, "load_configuration", side_effect=ValueError("broken")) as load:
            self.assertIsNone(app.load_tray_configuration([], False, lambda _exc: False))
        load.assert_called_once()

    def test_message_box_fallback_never_retries(self):
        with patch.object(app, "show_configuration_recovery_dialog",
                          side_effect=RuntimeError("no display")), \
             patch.object(app, "ctypes") as ctypes:
            self.assertFalse(app.report_configuration_error(ValueError("broken")))
        message_box = ctypes.windll.user32.MessageBoxW
        message_box.assert_called_once()
        self.assertEqual(message_box.call_args.args[1:3], ("broken", "MarbleScape configuration error"))

    def test_settings_file_opens_in_notepad_without_editor_association(self):
        with patch.object(app.os, "startfile", side_effect=OSError("no association"),
                          create=True) as startfile, \
             patch.object(app.subprocess, "Popen") as popen:
            app.open_file_for_editing(Path("settings.toml"))
        self.assertEqual([call.args[1] for call in startfile.call_args_list], ["edit", "open"])
        popen.assert_called_once_with(["notepad.exe", "settings.toml"])


class WorkingSettingsBackupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.config = self.folder / "settings.toml"
        self.config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        self.profiles = self.folder / "profiles.toml"
        self.profiles.write_text(app.serialize_library(app.normalize_library({})), encoding="utf-8")
        for patcher in (patch.object(app, "ACTIVE_CONFIG_PATH", self.config),
                        patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", self.profiles)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def loaded(self, config=b"[download]\n", profiles=None):
        return patch.object(app, "LOADED_SETTINGS_FILES", (self.config.resolve(), config, profiles))

    def test_nothing_is_saved_without_a_successful_load(self):
        with patch.object(app, "LOADED_SETTINGS_FILES", None):
            self.assertIsNone(app.save_working_settings_backup())
        self.assertFalse((self.folder / "backups").exists())

    def test_the_shipped_template_is_never_backed_up(self):
        template = app.DEFAULT_CONFIG_TEMPLATE_PATH.resolve()
        with patch.object(app, "LOADED_SETTINGS_FILES", (template, template.read_bytes(), None)), \
             patch.object(app, "list_working_settings_backups") as listing:
            self.assertIsNone(app.save_working_settings_backup())
        listing.assert_not_called()

    def test_backup_keeps_the_loaded_bytes_beside_the_configuration(self):
        with self.loaded(b"loaded = 1\n", b"profiles = 1\n"):
            target = app.save_working_settings_backup(dt.datetime(2026, 10, 1, 15, 40, 12))
        self.assertEqual(target, self.folder.resolve() / "backups" / "automatic" / "2026-10-01_154012")
        self.assertEqual((target / "marblescape_config.toml").read_bytes(), b"loaded = 1\n")
        self.assertEqual((target / "profiles.toml").read_bytes(), b"profiles = 1\n")
        self.assertEqual(app.list_working_settings_backups(),
                         [(dt.datetime(2026, 10, 1, 15, 40, 12), target)])

    def test_unchanged_settings_are_not_saved_again_and_old_backups_are_pruned(self):
        start = dt.datetime(2026, 10, 1, 8, 0, 0)
        for minute in range(7):
            with self.loaded(f"version = {minute}\n".encode()):
                self.assertIsNotNone(app.save_working_settings_backup(
                    start + dt.timedelta(minutes=minute)))
            with self.loaded(f"version = {minute}\n".encode()):
                self.assertIsNone(app.save_working_settings_backup(
                    start + dt.timedelta(minutes=minute, seconds=30)))
        backups = app.list_working_settings_backups()
        self.assertEqual(len(backups), app.WORKING_SETTINGS_BACKUP_LIMIT)
        self.assertEqual(backups[0][0], start + dt.timedelta(minutes=6))
        self.assertEqual(backups[-1][0], start + dt.timedelta(minutes=2))

    def test_restore_replaces_both_files_and_keeps_the_broken_ones(self):
        valid_config = self.config.read_bytes()
        with self.loaded(valid_config, self.profiles.read_bytes()):
            target = app.save_working_settings_backup()
        self.config.write_text("[output\nbroken", encoding="utf-8")
        self.profiles.write_text("not toml [", encoding="utf-8")
        kept = app.keep_settings_before_restore()
        self.assertTrue(app.restore_working_settings_backup(target))
        self.assertEqual(self.config.read_bytes(), valid_config)
        self.assertEqual(app.read_profile_library_file(), app.normalize_library({}))
        self.assertEqual((kept / "marblescape_config.toml").read_text(encoding="utf-8"),
                         "[output\nbroken")
        self.assertEqual((kept / "profiles.toml").read_text(encoding="utf-8"), "not toml [")
        # Copies of failing files are never offered as working settings.
        self.assertEqual([folder for _created, folder in app.list_working_settings_backups()],
                         [target])


class ConfigurationRecoveryDialogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.config = self.folder / "marblescape_config.toml"
        self.config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        self.profiles = self.folder / "profiles.toml"
        for patcher in (patch.object(app, "ACTIVE_CONFIG_PATH", self.config),
                        patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", self.profiles),
                        patch.object(app, "is_windows_startup_enabled", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_dialog(self, action):
        errors = []

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def drive():
            window = next(child for child in self.root.winfo_children()
                          if isinstance(child, tk.Toplevel))
            try:
                children = list(descendants(window))
                buttons = {child.cget("text"): child for child in children
                           if isinstance(child, ttk.Button)}
                details = next(child for child in children if isinstance(child, tk.Text))
                self.labels = [child.cget("text") for child in children
                               if isinstance(child, ttk.Label)]
                action(window, buttons, details.get("1.0", "end").strip())
            except Exception as exc:
                errors.append(exc)
            if window.winfo_exists():
                window.destroy()

        self.root.after(10, drive)
        result = app.show_configuration_recovery_dialog(
            ValueError("Invalid line 3"), master=self.root, backup_folder=self.folder)
        if errors:
            raise errors[0]
        return result

    def test_error_is_shown_and_buttons_decide_retry_or_exit(self):
        def inspect(_window, buttons, details):
            self.assertEqual(details, "Invalid line 3")
            self.assertFalse(buttons["Open settings file"].instate(["disabled"]))
            # No profiles.toml exists yet, so there is nothing to open.
            self.assertTrue(buttons["Open profiles file"].instate(["disabled"]))
            self.assertTrue(buttons["Restore last working settings"].instate(["disabled"]))
            buttons["Try again"].invoke()

        self.assertTrue(self.run_dialog(inspect))
        self.assertFalse(self.run_dialog(lambda _w, buttons, _d: buttons["Exit"].invoke()))
        self.assertFalse(self.run_dialog(
            lambda window, _b, _d: window.tk.call(window.protocol("WM_DELETE_WINDOW"))))

    def test_open_settings_file_keeps_the_dialog_open(self):
        def open_then_exit(window, buttons, _details):
            buttons["Open settings file"].invoke()
            self.assertTrue(window.winfo_exists())
            buttons["Exit"].invoke()

        with patch.object(app, "open_file_for_editing") as opener:
            self.assertFalse(self.run_dialog(open_then_exit))
        opener.assert_called_once_with(self.config.resolve())

    def test_restoring_a_backup_replaces_the_broken_configuration(self):
        backup = app.export_settings_backup(self.folder / "backup.json", include_profiles=False)
        valid = self.config.read_bytes()
        self.config.write_text("[general\nbroken", encoding="utf-8")
        with patch("tkinter.filedialog.askopenfilename", return_value=str(backup)), \
             patch("tkinter.messagebox.askyesno", return_value=True) as confirm:
            self.assertTrue(self.run_dialog(
                lambda _w, buttons, _d: buttons["Restore exported backup..."].invoke()))
        self.assertIn("replaces the current settings.", confirm.call_args.args[1])
        self.assertEqual(self.config.read_bytes(), valid)

    def test_last_working_settings_show_their_time_and_restore(self):
        valid = self.config.read_bytes()
        with patch.object(app, "LOADED_SETTINGS_FILES", (self.config.resolve(), valid, None)):
            app.save_working_settings_backup(dt.datetime(2026, 10, 1, 15, 40, 12))
        self.config.write_text("[output\nbroken", encoding="utf-8")

        def restore(_window, buttons, _details):
            self.assertIn("Last working settings: 2026-10-01 15:40:12", self.labels)
            buttons["Restore last working settings"].invoke()

        with patch("tkinter.messagebox.askyesno", return_value=True) as confirm:
            self.assertTrue(self.run_dialog(restore))
        self.assertIn("2026-10-01 15:40:12", confirm.call_args.args[1])
        self.assertEqual(self.config.read_bytes(), valid)
        kept = list((self.folder / "backups" / "automatic").glob("before-restore_*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "marblescape_config.toml").read_text(encoding="utf-8"),
                         "[output\nbroken")

    def test_declined_restore_changes_nothing(self):
        backup = app.export_settings_backup(self.folder / "backup.json", include_profiles=False)
        self.config.write_text("[general\nbroken", encoding="utf-8")

        def decline_then_exit(window, buttons, _details):
            buttons["Restore exported backup..."].invoke()
            self.assertTrue(window.winfo_exists())
            buttons["Exit"].invoke()

        with patch("tkinter.filedialog.askopenfilename", return_value=str(backup)), \
             patch("tkinter.messagebox.askyesno", return_value=False):
            self.assertFalse(self.run_dialog(decline_then_exit))
        self.assertEqual(self.config.read_text(encoding="utf-8"), "[general\nbroken")


if __name__ == "__main__":
    unittest.main()
