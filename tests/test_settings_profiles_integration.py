"""Real Settings profile/scroll integration using hidden Tk and offline fixtures."""

from contextlib import ExitStack
from copy import deepcopy
import datetime as dt
import gc
import inspect
import io
import json
import os
import shutil
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import tomllib
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import marblescape_download as app


def source_png(size):
    output = io.BytesIO()
    Image.new("RGB", size, (20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


class FakeCatalogue:
    catalogue_refresh_status = {"running": False, "done": 0, "total": 0, "message": "", "error": ""}
    catalogue_warning = ""

    def list_areas(self, provider, refresh=False):
        if provider == "solar":
            return [{"id": "sun", "label": "Sun", "category": "Solar"}]
        return [
            {"id": "full_disk", "label": "Full Disk", "category": "Full Disk"},
            {"id": "storm_alpha", "label": "Storm Alpha", "category": "Active storms"},
            {"id": "storm_beta", "label": "Storm Beta", "category": "Active storms"},
        ]

    def list_products(self, provider, area, refresh=False):
        if provider == "solar":
            return [{"id": "Fe171", "label": "171 Angstrom", "resolutions": ["300x300", "1200x1200"]}]
        return [
            {"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["339x339", "1808x1808"]},
            {"id": "13", "label": "Infrared", "resolutions": ["678x678", "5424x5424"]},
        ]


@unittest.skipUnless(os.name == "nt", "Windows tray Settings integration")
class SettingsProfilesIntegrationTests(unittest.TestCase):
    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from SettingsProfilesIntegrationTests.descendants(child)

    def wait_for_source(self, context):
        deadline = time.monotonic() + 5
        while context.source._loading:
            if time.monotonic() > deadline:
                self.fail("Fixture source catalogue did not finish")
            context.root.update()
            self.assertEqual(context.errors, [])
            time.sleep(0.01)
        context.root.update()
        self.assertEqual(context.errors, [])

    def footer_notice(self, context):
        """The text beside Save in the footer, or "" while it is hidden."""
        from tkinter import ttk
        save = next(widget for widget in self.descendants(context.root)
                    if isinstance(widget, ttk.Button) and widget.cget("text") in ("Save", "Apply Image"))
        notice = save.master.nametowidget("save_notice")
        return str(notice.cget("text")) if notice.winfo_manager() == "place" else ""

    def apply(self, context, *, tab=None):
        from tkinter import ttk
        if tab is not None:
            selected_tab = next(tab_id for tab_id in context.notebook.tabs()
                                if context.notebook.tab(tab_id, "text") == tab)
            context.notebook.select(selected_tab)
            context.root.update()
        expected = "Apply Image" if context.notebook.tab(context.notebook.select(), "text") == "Image" else "Save"
        # Check the draft now instead of waiting for the periodic Save check.
        context.root.event_generate("<<SettingsDraftChanged>>")
        button = next(widget for widget in self.descendants(context.root)
                      if isinstance(widget, ttk.Button) and widget.cget("text") == expected)
        button.invoke()
        self.assertEqual(context.errors, [])
        return tomllib.loads(context.config.read_text(encoding="utf-8"))

    def test_manual_resolution_is_saved_without_changing_other_defaults(self):
        def scenario(context):
            context.source.set_selection("goes_east", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            self.assertEqual(context.source._resolution_var.get(),
                             "Automatic (recommended)")
            context.source._resolution_var.set("Largest available (1808 × 1808)")
            context.source._select_resolution()
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["sources"]["goes_east"]["resolution"], "largest")
            for provider in ("goes_west", "solar", "himawari", "slider", "worldview"):
                self.assertEqual(saved["sources"][provider]["resolution"], "auto")
        self.run_dialog(scenario)

    def test_image_profile_header_and_system_apply_keep_identity_separate(self):
        import marblescape_snapshot as system
        from tkinter import ttk

        def scenario(context):
            # The profile is the title of the header frame, its short ID right-aligned
            # on the same top edge.
            header = context.source.frame.master.nametowidget("image_header")
            self.assertIsInstance(header, ttk.LabelFrame)
            short_id = header.nametowidget("short_id")
            placed = short_id.place_info()
            self.assertEqual((placed["relx"], placed["y"], placed["anchor"], placed["bordermode"]),
                             ("1", "0", "ne", "outside"))
            def displayed():
                context.profiles._apply_runtime_status(context.profiles._status())
                identifier = str(short_id.cget("text")) or context.root.getvar(short_id.cget("textvariable"))
                return f"{header.cget('text')} | {identifier}"
            self.assertEqual(displayed(), f"{system.SYSTEM_NAME} | ")
            context.profiles.add_current("Named island")
            context.profiles.load_selected()
            profile_id = context.profiles.tree.selection()[0]
            self.assertEqual(displayed(), f"Named island | ({profile_id[:8]})")
            original = context.variables["zoom"].get()
            context.variables["zoom"].set("1.25")
            self.assertEqual(displayed(), f"Named island (modified) | ({profile_id[:8]})")
            context.variables["zoom"].set(original)
            self.assertEqual(displayed(), f"Named island | ({profile_id[:8]})")
            saved = deepcopy(context.profiles._items[0]["settings"])
            # Device settings never modify a profile.
            original = {name: context.variables[name].get()
                        for name in ("latest_folder", "width", "background_color")}
            context.variables["latest_folder"].set((context.directory / "other-latest").as_posix())
            context.variables["width"].set("1280")
            context.variables["background_color"].set("#123456")
            self.assertEqual(displayed(), f"Named island | ({profile_id[:8]})")
            self.assertEqual(context.profiles._items[0]["settings"], saved)
            for name, value in original.items():
                context.variables[name].set(value)
            context.variables["zoom"].set("1.25")
            with patch("tkinter.messagebox.askyesno", return_value=False):
                context.profiles.update_selected()
            self.assertEqual(displayed(), f"Named island (modified) | ({profile_id[:8]})")
            self.assertEqual(context.profiles._items[0]["settings"], saved)
            with patch("tkinter.messagebox.askyesno", return_value=True):
                context.profiles.update_selected()
            self.assertEqual(displayed(), f"Named island | ({profile_id[:8]})")
            self.assertEqual(context.profiles._items[0]["name"], "Named island")
            record = {"snapshot_id": "3" * 32, "profile_id": "4" * 32,
                      "generated_at_utc": "2026-09-27T10:00:00Z",
                      "profile_settings": app.portable_settings(context.profiles._capture_settings(), app.normalize_image_settings_snapshot)}
            app.save_latest_snapshot(record)
            displayed()
            context.profiles.tree.selection_set(system.SYSTEM_ID)
            context.profiles.load_selected()
            self.assertEqual(displayed(), f"{system.SYSTEM_NAME} | ")
            context.profiles.apply_selected()
            self.assertEqual(context.errors, [])
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertEqual(saved["profile_list"]["applied_profile_id"], "")
            self.assertEqual(len(app.read_profile_library_file()["items"]), 1)
            self.assertEqual(app.read_latest_snapshot()["profile_id"], "4" * 32)
        self.run_dialog(scenario)

    def test_general_fields_start_at_one_line(self):
        from tkinter import ttk

        def scenario(context):
            general_id = next(item for item in context.notebook.tabs()
                              if context.notebook.tab(item, "text") == "General")
            context.notebook.select(general_id)
            context.root.update()
            general = context.root.nametowidget(general_id)
            sections = {str(widget.cget("text")): widget for widget in self.descendants(general)
                        if isinstance(widget, ttk.LabelFrame)}
            starts = {}
            for title in ("Date and time", "Output device", "Monitor output", "Appearance"):
                field = next(child for child in sections[title].grid_slaves(row=0)
                             if int(child.grid_info()["column"]) == 1)
                starts[title] = field.winfo_x()
            self.assertEqual(len(set(starts.values())), 1, starts)
            # Also below the display dropdown: its two buttons.
            buttons = next(widget for widget in self.descendants(sections["Output device"])
                           if isinstance(widget, ttk.Button) and widget.cget("text") == "Apply to all displays")
            self.assertEqual(buttons.master.winfo_x(), starts["Output device"])
            self.assertEqual(context.errors, [])
        self.run_dialog(scenario)

    def test_settings_open_on_the_tab_used_last(self):
        def switch(context):
            self.assertEqual(context.notebook.tab(context.notebook.select(), "text"), "General")
            backup = next(item for item in context.notebook.tabs()
                          if context.notebook.tab(item, "text") == "Backup")
            context.notebook.select(backup)
            context.root.update()
            # Saved at once, without Save.
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertEqual(saved["display"]["settings_tab"], "Backup")
            self.assertEqual(context.errors, [])

        self.run_dialog(switch)

        def tab(title):
            def prepare(text):
                return app.replace_toml_section_value(
                    app.ensure_display_configuration_section(text), "display", "settings_tab", title)
            return prepare

        opened = []

        def record(context):
            opened.append(context.notebook.tab(context.notebook.select(), "text"))

        self.run_dialog(record, prepare_config=tab("Profiles"))
        # A title that no longer exists opens the first tab.
        self.run_dialog(record, prepare_config=tab("Old tab name"))
        self.assertEqual(opened, ["Profiles", "General"])
        with self.assertRaises(ValueError):
            app.normalize_settings_tab({"settings_tab": 3})

    def test_settings_window_size_is_saved_and_restored(self):
        def scale(root):
            return max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))

        requested = []
        real_geometry = tk.Tk.wm_geometry

        def record_geometry(window, new_geometry=None):
            # Hidden test windows keep 1x1; record the size Settings asks for.
            if new_geometry and "x" in new_geometry:
                requested.append(new_geometry)
            return real_geometry(window, new_geometry)

        def opened_size(_root):
            width, height = requested[-1].split("+")[0].split("x")
            return int(width), int(height)

        def default_and_resize(context):
            root = context.root
            # By default the minimum width and the former height.
            self.assertEqual(opened_size(root), (round(app.SETTINGS_MIN_WIDTH * scale(root)),
                                                 round(app.SETTINGS_DEFAULT_HEIGHT * scale(root))))
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            # Opening saves nothing: 0 still means the default size.
            self.assertEqual(saved["display"].get("settings_window_width", 0), 0)
            root.state = lambda *args: "normal"
            root.event_generate("<Configure>", width=round(1000 * scale(root)), height=round(800 * scale(root)))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                root.update()
                saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
                if saved["display"].get("settings_window_width", 0):
                    break
                time.sleep(0.02)
            # Saved at once, without Save, at 100 % scaling.
            self.assertEqual((saved["display"]["settings_window_width"],
                              saved["display"]["settings_window_height"]), (1000, 800))
            self.assertEqual(context.errors, [])

        with patch.object(tk.Tk, "wm_geometry", record_geometry), patch.object(tk.Tk, "geometry", record_geometry):
            self.run_dialog(default_and_resize)

        def saved_size(text):
            text = app.ensure_display_configuration_section(text)
            text = app.replace_toml_section_value(text, "display", "settings_window_width", 1100)
            return app.replace_toml_section_value(text, "display", "settings_window_height", 750)

        def reopened(context):
            root = context.root
            self.assertEqual(opened_size(root), (round(1100 * scale(root)), round(750 * scale(root))))

        with patch.object(tk.Tk, "wm_geometry", record_geometry), patch.object(tk.Tk, "geometry", record_geometry):
            self.run_dialog(reopened, prepare_config=saved_size)
        with self.assertRaises(ValueError):
            app.normalize_settings_window_size({"settings_window_width": "wide"})
        self.assertEqual(app.normalize_settings_window_size({}), (0, 0))

    def test_button_groups_are_equally_wide_and_backup_buttons_sit_left(self):
        from tkinter import ttk

        def scenario(context):
            def buttons(tab):
                tab_id = next(item for item in context.notebook.tabs()
                              if context.notebook.tab(item, "text") == tab)
                context.notebook.select(tab_id)
                context.root.update()
                # Only shown buttons: the Image tab's Save is hidden elsewhere.
                return {str(widget.cget("text")): widget for widget in self.descendants(context.root)
                        if isinstance(widget, ttk.Button) and widget.winfo_manager()}

            backup = buttons("Backup")
            footer = [backup[text] for text in ("Save", "OK", "Close")]
            # Save, OK and Close are as wide as the widest of them ("Apply Image"),
            # on every tab; Cancel download keeps its own width, right-aligned below.
            self.assertEqual(len({button.winfo_width() for button in footer}), 1)
            footer_width = footer[0].winfo_width()
            probe = ttk.Button(context.root, text="Apply Image")
            self.assertEqual(footer_width, probe.winfo_reqwidth())
            probe.destroy()
            # Cancel download ends where Close ends: its row spans all footer
            # columns and it sits at that row's right edge.
            cancel = backup["Cancel download"]
            row = cancel.master
            self.assertEqual((int(row.grid_info()["columnspan"]), row.grid_info()["sticky"]), (6, "ew"))
            self.assertIn("e", cancel.grid_info()["sticky"])
            self.assertEqual(max(int(child.grid_info()["column"]) for child in row.grid_slaves()),
                             int(cancel.grid_info()["column"]))
            self.assertEqual(int(footer[2].grid_info()["column"]), 5)
            names = ("Export settings", "Import settings", "Export settings + profiles",
                     "Import settings + profiles", "Open settings folder", "Open profiles folder")
            self.assertEqual(len({backup[name].winfo_width() for name in names}), 1)
            # Both button columns line up, right after the labels; the space is on the right.
            self.assertEqual({backup[name].winfo_x() for name in names[0::2]}, {backup[names[0]].winfo_x()})
            self.assertEqual({backup[name].winfo_x() for name in names[1::2]}, {backup[names[1]].winfo_x()})
            frame = backup[names[0]].master
            self.assertEqual(int(frame.columnconfigure(0)["weight"]), 0)
            self.assertEqual(int(frame.columnconfigure(3)["weight"]), 1)
            widest_label = max(widget.winfo_reqwidth() for widget in frame.grid_slaves(column=0)
                               if isinstance(widget, ttk.Label) and int(widget.grid_info()["row"]) < 3)
            # Frame padding 8, label gap 12, button gap 3.
            self.assertEqual(backup[names[0]].winfo_x(), 8 + widest_label + 12 + 3)
            profiles = buttons("Profiles")
            self.assertEqual(profiles["Rename"].winfo_width(), profiles["Delete"].winfo_width())
            image = buttons("Image")
            # The Image tab adds Apply Image left of Save, equally wide; Save stays right
            # before OK, where it is on every tab.
            self.assertEqual({image[text].winfo_width() for text in ("Save", "Apply Image", "OK", "Close")},
                             {footer_width})
            self.assertLess(image["Apply Image"].winfo_x(), image["Save"].winfo_x())
            self.assertEqual([int(image[text].grid_info()["column"]) for text in ("Apply Image", "Save", "OK")],
                             [2, 3, 4])
            self.assertEqual(context.errors, [])
        self.run_dialog(scenario)

    def test_image_save_keeps_the_picture_and_apply_image_loads_one(self):
        from tkinter import ttk

        def scenario(context):
            tab_id = next(item for item in context.notebook.tabs()
                          if context.notebook.tab(item, "text") == "Image")
            context.notebook.select(tab_id)
            context.root.update()
            shown = {str(widget.cget("text")): widget for widget in self.descendants(context.root)
                     if isinstance(widget, ttk.Button) and widget.winfo_manager()}
            context.variables["zoom"].set("2")
            app.CONFIGURATION_RELOAD_EVENT.clear()
            app.SETTINGS_ONLY_RELOAD_EVENT.clear()
            shown["Save"].invoke()
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertEqual(float(saved["view"]["zoom"]), 2.0)
            # Saved, but no picture now: a settings-only reload.
            self.assertTrue(app.CONFIGURATION_RELOAD_EVENT.is_set())
            self.assertTrue(app.SETTINGS_ONLY_RELOAD_EVENT.is_set())
            # The note beside the buttons says when the picture loads, until the draft changes.
            context.root.event_generate("<<SettingsDraftChanged>>")
            context.root.update()
            note = context.root.nametowidget(str(shown["Save"].master) + ".save_notice")
            self.assertTrue(note.winfo_manager())
            self.assertRegex(str(note.cget("text")),
                             r"^✓ Saved - loads at the next update \((\d{4}-\d{2}-\d{2} )?\d{2}:\d{2}( UTC)?\)$")
            context.variables["zoom"].set("3")
            context.root.event_generate("<<SettingsDraftChanged>>")
            context.root.update()
            self.assertFalse(note.winfo_manager())
            app.CONFIGURATION_RELOAD_EVENT.clear()
            shown["Apply Image"].invoke()
            self.assertTrue(app.CONFIGURATION_RELOAD_EVENT.is_set())
            self.assertFalse(app.SETTINGS_ONLY_RELOAD_EVENT.is_set())
            app.CONFIGURATION_RELOAD_EVENT.clear()
            self.assertEqual(context.errors, [])

        self.run_dialog(scenario)

    def test_backup_buttons_restore_selected_scope_and_preserve_ids(self):
        from tkinter import ttk

        for full in (False, True):
            def scenario(context):
                source_settings = context.profiles._capture_settings()
                incoming = app.normalize_library({"items": [{"id": "1" * 32, "name": "Incoming", "settings": source_settings}]})
                existing = app.normalize_library({"items": [{"id": "2" * 32, "name": "Existing", "settings": source_settings}]})
                app.write_profile_library_file_unlocked(incoming)
                path = context.directory / "backup.json"
                app.export_settings_backup(path, include_profiles=full)
                app.write_profile_library_file_unlocked(existing)
                profile_path = context.directory / "profiles.toml"
                before = profile_path.read_bytes()
                buttons = {widget.cget("text"): widget for widget in self.descendants(context.root)
                           if isinstance(widget, ttk.Button)}
                for label in ("Import settings", "Export settings",
                              "Import settings + profiles", "Export settings + profiles"):
                    self.assertIn(label, buttons)
                with patch("tkinter.filedialog.askopenfilename", return_value=str(path)), \
                     patch("tkinter.messagebox.askyesno", return_value=True) as confirm, \
                     patch.object(context.root, "destroy"):
                    buttons["Import settings + profiles" if full else "Import settings"].invoke()
                self.assertEqual(context.errors, [])
                confirm.assert_called_once()
                self.assertEqual(app.read_profile_library_file(), incoming if full else existing)
                if not full:
                    self.assertEqual(profile_path.read_bytes(), before)
            with self.subTest(full=full):
                self.run_dialog(scenario)

    def test_table_preferences_are_saved_globally_at_once(self):
        from marblescape_profile_settings import PROFILE_LIST_COLUMNS
        for apply_profile in (False, True):
            def scenario(context):
                context.profiles.add_current("Table example")
                library = deepcopy(context.profiles.get_library())
                order = tuple(reversed(PROFILE_LIST_COLUMNS))
                context.profiles._column_order = order
                widths = {column: 200 + index * 9 for index, column in enumerate(PROFILE_LIST_COLUMNS)}
                for column, width in widths.items():
                    context.profiles.tree.column(column, width=width)
                    context.profiles._column_visibility_vars[column].set(False)
                    context.profiles._toggle_column(column)
                context.profiles.sort_by("zoom")
                context.profiles.sort_by("zoom")
                # No Save is needed; Apply writes the same layout again.
                if apply_profile:
                    context.profiles.apply_selected()
                    self.assertEqual(context.errors, [])
                saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
                self.assertEqual(saved["profile_list"]["columns_version"], 19)
                self.assertEqual(saved["profile_list"]["visible_columns"], [])
                self.assertEqual(saved["profile_list"]["sort_column"], "zoom")
                self.assertTrue(saved["profile_list"]["sort_descending"])
                self.assertEqual(saved["profile_list"]["column_widths"], widths)
                self.assertEqual(saved["profile_list"]["column_order"], list(order))
                self.assertEqual(context.profiles.get_library(), library)
                self.assertNotIn("profile_list", library["items"][0]["settings"])
                app.load_configuration(context.config)
                self.assertEqual(app.PROFILE_LIST_VISIBLE_COLUMNS, ())
                self.assertEqual(app.PROFILE_LIST_SORT_COLUMN, "zoom")
                self.assertTrue(app.PROFILE_LIST_SORT_DESCENDING)
                self.assertEqual(app.PROFILE_LIST_COLUMN_WIDTHS, widths)
                self.assertEqual(app.PROFILE_LIST_COLUMN_ORDER, order)
                restored = type(context.profiles)(context.root, app.read_profile_library_file(),
                    context.profiles._capture_settings, lambda _settings: None,
                    visible_columns=app.PROFILE_LIST_VISIBLE_COLUMNS,
                    sort_column=app.PROFILE_LIST_SORT_COLUMN,
                    sort_descending=app.PROFILE_LIST_SORT_DESCENDING,
                    column_widths=app.PROFILE_LIST_COLUMN_WIDTHS,
                    column_order=app.PROFILE_LIST_COLUMN_ORDER)
                try:
                    self.assertEqual(restored.get_visible_columns(), ())
                    self.assertEqual(restored.get_sort_settings(), context.profiles.get_sort_settings())
                    self.assertEqual(restored.get_library(), library)
                    self.assertEqual(restored.get_column_widths(), widths)
                    self.assertEqual(restored.get_column_order(), order)
                finally:
                    restored.close()
                    restored.frame.destroy()
                backup = context.directory / "table-preferences.json"
                app.export_settings_backup(backup, include_profiles=False)
                config_text, backup_profiles, _startup = app.import_settings_backup(backup, include_profiles=False)
                self.assertIsNone(backup_profiles)
                self.assertEqual(tomllib.loads(config_text)["profile_list"], saved["profile_list"])
            with self.subTest(apply_profile=apply_profile):
                self.run_dialog(scenario)

    def test_table_preferences_are_kept_when_settings_are_closed(self):
        def scenario(context):
            context.profiles.sort_by("name")
            self.assertEqual(tomllib.loads(context.config.read_text(encoding="utf-8"))
                             ["profile_list"]["sort_column"], "name")
            # Dragging a column border is detected when the mouse is released.
            context.profiles.tree.column("name", width=399)
            context.profiles._check_column_widths()
            context.profiles._column_visibility_vars["source"].set(False)
            context.profiles._toggle_column("source")
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))["profile_list"]
            self.assertEqual(saved["column_widths"]["name"], 399)
            self.assertNotIn("source", saved["visible_columns"])
            # The next Settings window opens with this layout without a reload.
            self.assertEqual(app.PROFILE_LIST_SORT_COLUMN, "name")
            self.assertEqual(app.PROFILE_LIST_COLUMN_WIDTHS["name"], 399)
            self.assertNotIn("source", app.PROFILE_LIST_VISIBLE_COLUMNS)
            self.assertEqual(context.errors, [])
        self.run_dialog(scenario)

    def test_manual_apply_persists_profile_identity(self):
        def scenario(context):
            context.profiles.add_current("Named profile")
            identifier = context.profiles.tree.selection()[0]
            context.profiles.apply_selected()
            self.assertEqual(context.errors, [])
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertEqual(saved["profile_list"]["applied_profile_id"], identifier)
        self.run_dialog(scenario)

    def test_export_dialogs_use_scope_filenames_and_separate_remembered_paths(self):
        from tkinter import ttk

        def scenario(context):
            buttons = {widget.cget("text"): widget for widget in self.descendants(context.root)
                       if isinstance(widget, ttk.Button)}
            settings_folder = context.directory / "custom-settings"
            profile_folder = context.directory / "custom-profiles"
            settings_folder.mkdir()
            profile_folder.mkdir()
            for full in (False, True):
                destination = settings_folder / ("full.json" if full else "settings.json")
                with patch("tkinter.filedialog.asksaveasfilename", return_value=str(destination)) as dialog, \
                     patch("tkinter.messagebox.showinfo"):
                    buttons["Export settings + profiles" if full else "Export settings"].invoke()
                self.assertEqual(context.errors, [])
                initial = settings_folder if full else context.directory / "export" / "settings"
                self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), initial)
                filename = dialog.call_args.kwargs["initialfile"]
                self.assertTrue(filename.startswith("marblescape-settings-profiles-" if full else "marblescape-settings-"))
                self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["scope"],
                                 "settings_and_profiles" if full else "settings_only")
            context.profiles.add_current("Test export")
            with patch("tkinter.filedialog.asksaveasfilename", return_value=str(profile_folder / "profile.json")) as dialog, \
                 patch("tkinter.messagebox.showinfo") as completed:
                context.profiles.export_selected()
            self.assertIn("Successfully exported 1", completed.call_args.args[1])
            self.assertEqual(context.errors, [])
            self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), context.directory / "export" / "profiles")
            self.assertFalse(dialog.call_args.kwargs["confirmoverwrite"])
            self.assertNotIn("__", dialog.call_args.kwargs["initialfile"])
            self.assertEqual(len(list(profile_folder.glob("*.json"))), 1)
            with patch("tkinter.filedialog.asksaveasfilename", return_value="") as dialog:
                context.profiles.export_selected()
            self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), profile_folder)
            with patch("tkinter.filedialog.asksaveasfilename", return_value="") as dialog:
                buttons["Export settings"].invoke()
            self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), settings_folder)
            # Imports start where the exports went, not in the program folder.
            for label in ("Import settings", "Import settings + profiles"):
                with patch("tkinter.filedialog.askopenfilename", return_value="") as dialog:
                    buttons[label].invoke()
                self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), settings_folder)
            with patch("tkinter.filedialog.askopenfilenames", return_value=()) as dialog:
                context.profiles.import_selected()
            self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), profile_folder)
            self.assertEqual(context.errors, [])
            with patch.object(os, "startfile") as open_folder:
                buttons["Open settings folder"].invoke()
                buttons["Open profiles folder"].invoke()
            self.assertEqual([Path(call.args[0]) for call in open_folder.call_args_list],
                             [settings_folder, profile_folder])
        self.run_dialog(scenario)

    def test_daily_catalogue_controls_save_system_time_and_force_refresh(self):
        from tkinter import ttk
        from unittest.mock import Mock

        schedule = Mock()
        schedule.status.return_value = {"running": False, "message": "Ready"}

        def scenario(context):
            def labelled(text):
                return next(widget for widget in self.descendants(context.root)
                            if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == text)

            refresh = labelled("Catalogue refresh")
            section = labelled("Daily catalogue refresh")
            retries = labelled("Catalogue retries")
            download_retries = labelled("Download retries")
            # Daily refresh is a subsection; both retry sections stay separate.
            self.assertIs(section.master, refresh)
            self.assertIs(retries.master, refresh.master)
            self.assertLess(refresh.grid_info()["row"], retries.grid_info()["row"])
            retry_combos = [next(widget for widget in self.descendants(frame) if isinstance(widget, ttk.Combobox))
                            for frame in (download_retries, retries)]
            self.assertEqual({int(combo.cget("width")) for combo in retry_combos}, {app.RETRY_COMBO_WIDTH})
            self.assertTrue(all(combo.grid_info()["sticky"] == "w" for combo in retry_combos))
            combos = [widget for widget in self.descendants(section) if isinstance(widget, ttk.Combobox)]
            self.assertEqual(len(combos), 3)
            for index, (combo, value) in enumerate(zip(combos, ("23", "58", "59"))):
                self.assertEqual(tuple(combo.cget("values")),
                                 tuple(f"{v:02d}" for v in range(24 if index == 0 else 60)))
                self.assertEqual(str(combo.cget("state")), "readonly")
                combo.set(value)
            self.assertEqual(context.variables["catalogue_refresh_time"].get(), "23:58:59")
            def saved_time():
                return next(context.root.getvar(widget.cget("textvariable"))
                            for widget in self.descendants(section)
                            if isinstance(widget, ttk.Label) and widget.cget("textvariable")
                            and str(context.root.getvar(widget.cget("textvariable"))).startswith("Saved time:"))
            self.assertEqual(saved_time(), "Saved time: 03:00:00 (system time)")
            context.variables["time_zone"].set("UTC")
            saved = self.apply(context)
            self.assertEqual(saved["download"]["catalogue_refresh_time"], "23:58:59")
            self.assertEqual(saved_time(), "Saved time: 23:58:59 (system time)")
            self.assertEqual(app.saved_catalogue_refresh_time(), "23:58:59")
            # Saving the time alone never starts a refresh.
            schedule.request.assert_not_called()
            self.assertFalse(any(isinstance(widget, ttk.Button) for widget in self.descendants(section)))
            force = next(widget for widget in self.descendants(refresh)
                         if isinstance(widget, ttk.Button) and widget.cget("text") == "Refresh all catalogues")
            force.invoke()
            schedule.request.assert_called_once_with(force=True)
            image_catalogue = next(widget for widget in self.descendants(context.root)
                                   if isinstance(widget, ttk.LabelFrame)
                                   and str(widget.cget("text")).startswith("Catalogue refresh - "))
            # Catalogue refresh closes the Image tab.
            self.assertEqual(int(image_catalogue.grid_info()["row"]),
                             max(int(child.grid_info()["row"]) for child in image_catalogue.master.grid_slaves()))
            self.assertFalse(any(isinstance(widget, ttk.Button) and widget.cget("text") == "Refresh all catalogues"
                                 for widget in self.descendants(image_catalogue)))

        with patch.object(app, "CATALOGUE_SCHEDULE", schedule):
            self.run_dialog(scenario)

    def test_profile_import_same_name_other_uuid_keeps_both_profiles(self):
        from marblescape_profile_transfer import export_profiles

        def scenario(context):
            settings = context.profiles._capture_settings()
            original = [{"id": "1" * 32, "name": "Same name", "settings": settings},
                        {"id": "2" * 32, "name": "Other", "settings": deepcopy(settings)}]
            context.profiles._commit(original, original[0]["id"])
            incoming = deepcopy(original[0])
            incoming["id"] = "3" * 32
            incoming["settings"]["view"]["zoom"] = 2.0
            path = export_profiles(context.directory, [incoming], app.normalize_image_settings_snapshot)[0]
            before = context.config.read_bytes()
            with patch("tkinter.filedialog.askopenfilenames", return_value=(str(path),)), \
                 patch("marblescape_profile_settings.confirm_transfer_conflict", return_value="overwrite") as conflict, \
                 patch("tkinter.messagebox.showinfo") as info:
                context.profiles.import_selected()
            conflict.assert_not_called()
            self.assertEqual(context.errors, [])
            self.assertIn("Same name -> Same name (Imported)", info.call_args.args[1])
            library = context.profiles.get_library()
            self.assertEqual(library["rotation"]["order"], [])
            self.assertEqual([(item["id"], item["name"]) for item in library["items"]],
                             [("1" * 32, "Same name"), ("2" * 32, "Other"), ("3" * 32, "Same name (Imported)")])
            self.assertEqual(library["items"][0]["settings"], original[0]["settings"])
            self.assertEqual(library["items"][2]["settings"]["view"]["zoom"], 2.0)
            self.assertEqual(context.profiles.tree.selection(), ("3" * 32,))
            self.assertEqual(context.config.read_bytes(), before)
            self.apply(context)
            self.assertEqual(app.read_profile_library_file(), library)
        self.run_dialog(scenario)

    def test_delete_all_profiles_saves_disabled_empty_rotation_at_once(self):
        def scenario(context):
            settings = context.profiles._capture_settings()
            items = [{"id": str(index) * 32, "name": f"Profile {index}", "settings": deepcopy(settings)}
                     for index in (1, 2)]
            context.profiles._commit(items, items[0]["id"])
            # Enabling rotation saves the whole list immediately.
            context.profiles.enabled_var.set(True)
            self.assertTrue(app.read_profile_library_file()["rotation"]["enabled"])
            context.profiles._select_all()
            with patch("tkinter.messagebox.askyesno", return_value=True) as confirm:
                context.profiles.buttons["Delete"].invoke()
            confirm.assert_called_once()
            self.assertIn("2 selected profiles", confirm.call_args.args[1])
            self.assertEqual(context.errors, [])
            saved = app.read_profile_library_file()
            self.assertEqual(saved["items"], [])
            self.assertFalse(saved["rotation"]["enabled"])
            self.assertEqual(saved["rotation"]["order"], [])
        self.run_dialog(scenario)

    def test_only_apply_image_persists_loaded_profile_identity(self):
        def scenario(context):
            context.profiles.add_current("Loaded profile")
            identifier = context.profiles.tree.selection()[0]
            context.profiles.load_selected()
            self.wait_for_source(context)
            saved = self.apply(context, tab="Profiles")
            self.assertEqual(saved["profile_list"]["applied_profile_id"], "")
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["profile_list"]["applied_profile_id"], identifier)
        self.run_dialog(scenario)

    def test_choosing_eumetsat_after_a_layerless_profile_saves_a_startable_configuration(self):
        def without_layers(text):
            # What applying a non-EUMETSAT profile leaves behind: layers = [].
            snapshot = app.default_import_settings()
            snapshot["source"]["provider"] = "goes_east"
            snapshot["layers"] = []
            return app.replace_image_settings(text, snapshot)

        def scenario(context):
            self.assertEqual(tomllib.loads(context.config.read_text(encoding="utf-8"))["layers"], [])
            context.source.set_selection("eumetsat", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["source"]["provider"], "eumetsat")
            selected = app.DEFAULT_SOURCE_PROFILES["eumetsat"]["layer"]
            self.assertIn({"kind": "wms", "name": selected, "enabled": True, "opacity": 1.0, "style": ""},
                          saved["layers"])
            self.assertTrue(app.has_visible_layer(saved["layers"]))
            # The saved file passes the same check that failed at startup.
            app.load_configuration(context.config)
            app.validate_configuration()

        self.run_dialog(scenario, prepare_config=without_layers)

    def test_image_profile_capture_ignores_unsaved_history_policy_draft(self):
        def scenario(context):
            context.variables["profile_history_policies"].set("{unfinished")
            captured = context.profiles._capture_settings()
            self.assertIn("source", captured)
            self.assertIn("sources", captured)
            self.assertEqual(context.errors, [])
        self.run_dialog(scenario)

    def test_table_height_is_saved_at_once_and_used_next_time(self):
        def scenario(context):
            profiles = context.profiles
            self.assertEqual(int(profiles.tree.cget("height")), 7)
            row = profiles._row_pixels()
            profiles._grip_press(SimpleNamespace(y_root=1000))
            profiles._grip_release(SimpleNamespace(y_root=1000 + 3 * row))
            self.assertEqual(context.errors, [])
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertEqual(saved["profile_list"]["table_rows"], 10)
            self.assertEqual(app.PROFILE_LIST_TABLE_ROWS, 10)
        self.run_dialog(scenario)

        def reopened(context):
            self.assertEqual(context.profiles.get_table_rows(), 15)
            self.assertEqual(int(context.profiles.tree.cget("height")), 15)
        self.run_dialog(reopened, prepare_config=lambda text: app.replace_toml_section_value(
            text, "profile_list", "table_rows", 15))

    def test_force_loading_new_picture_never_changes_the_active_profile(self):
        import marblescape_snapshot as system
        ids = ["a" * 32, "b" * 32, "c" * 32]
        library = {"items": [{"id": identifier, "name": f"Profile {index}",
                              "settings": app.default_import_settings()}
                             for index, identifier in enumerate(ids, 1)],
                   "rotation": {"order": []}}

        def scenario(context):
            self.addCleanup(app.clear_profile_refresh_queue)
            app.clear_profile_refresh_queue()
            menu = context.profiles._cell_menu
            label = "Force loading new image"
            self.assertEqual(menu.index(label), menu.index("Check for new image") + 1)
            before = context.config.read_text(encoding="utf-8")

            def invoke(*selection):
                context.profiles.tree.selection_set(selection)
                context.profiles._selection_changed()
                self.assertEqual(("normal" if context.profiles.menu_entry_enabled(label) else "disabled"), "normal")
                menu.invoke(label)
                self.assertEqual(context.errors, [])

            # One profile that is not shown: loaded in the background only.
            invoke(ids[1])
            self.assertEqual(app.queued_profile_refreshes(), (ids[1],))
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
            self.assertIn("1 profile(s) in the background", context.profiles.notice_var.get())
            # Several: queued in profile-list order, already queued ones once.
            invoke(ids[2], ids[0], ids[1])
            self.assertEqual(app.queued_profile_refreshes(), (ids[1], ids[0], ids[2]))
            self.assertIn("2 profile(s) in the background", context.profiles.notice_var.get())
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
            self.assertEqual(context.config.read_text(encoding="utf-8"), before)
            context.profiles._apply_runtime_status(context.profiles._status())
            self.assertEqual(context.profiles.tree.set(ids[0], "active"), "QUEUE")
            # The profile the wallpaper shows reloads at once instead of being queued.
            app.clear_profile_refresh_queue()
            with patch.object(app, "get_current_image_path", return_value=context.directory / "shown.png"),                  patch.object(app, "_history_profile_identity", return_value=(ids[1], "Profile 2")):
                invoke(ids[1])
                self.assertTrue(app.FORCE_UPDATE_EVENT.is_set())
                self.assertEqual(app.queued_profile_refreshes(), ())
                self.assertEqual(context.profiles.notice_var.get(), "Loading a new picture of the active profile.")
                app.FORCE_UPDATE_EVENT.clear()
                invoke(ids[1], ids[2])
                self.assertTrue(app.FORCE_UPDATE_EVENT.is_set())
                self.assertEqual(app.queued_profile_refreshes(), (ids[2],))
            # The active profile and every setting stay as they were.
            self.assertEqual(context.config.read_text(encoding="utf-8"), before)
            # The Latest snapshot row is not a saved profile.
            context.profiles.tree.selection_set((system.SYSTEM_ID,))
            context.profiles._selection_changed()
            self.assertEqual(("normal" if context.profiles.menu_entry_enabled(label) else "disabled"), "disabled")
            app.clear_profile_refresh_queue()
        self.run_dialog(scenario, initial_library=library)

    def test_check_for_new_pictures_queues_checks_and_shows_the_result(self):
        import marblescape_snapshot as system
        ids = ["a" * 32, "b" * 32, "c" * 32]
        library = {"items": [{"id": identifier, "name": f"Profile {index}",
                              "settings": app.default_import_settings()}
                             for index, identifier in enumerate(ids, 1)],
                   "rotation": {"order": []}}

        def scenario(context):
            self.addCleanup(app.clear_profile_refresh_queue)
            self.addCleanup(app.CHECK_NOW_EVENT.clear)
            app.clear_profile_refresh_queue()
            profiles = context.profiles
            menu = profiles._cell_menu
            label = "Check for new image"
            self.assertEqual(menu.index(label), menu.index("Update") + 1)
            before = context.config.read_text(encoding="utf-8")

            def invoke(*selection):
                profiles.tree.selection_set(selection)
                profiles._selection_changed()
                self.assertEqual(("normal" if context.profiles.menu_entry_enabled(label) else "disabled"), "normal")
                menu.invoke(label)
                self.assertEqual(context.errors, [])

            invoke(ids[2], ids[0])
            self.assertEqual(app.queued_profile_refreshes(), (ids[0], ids[2]))
            self.assertTrue(all(app.profile_refresh_is_check(identifier) for identifier in (ids[0], ids[2])))
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
            self.assertFalse(app.CHECK_NOW_EVENT.is_set())
            self.assertIn("Checking 2 profile(s) for new pictures", profiles.notice_var.get())
            profiles._apply_runtime_status(profiles._status())
            # The shown profile gets its regular check at once, never a forced download.
            app.clear_profile_refresh_queue()
            with patch.object(app, "get_current_image_path", return_value=context.directory / "shown.png"), \
                    patch.object(app, "_history_profile_identity", return_value=(ids[1], "Profile 2")):
                invoke(ids[1], ids[2])
            self.assertTrue(app.CHECK_NOW_EVENT.is_set())
            self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
            self.assertEqual(app.queued_profile_refreshes(), (ids[2],))
            self.assertTrue(profiles.notice_var.get().startswith("Checking the active profile now."))
            # The finished batch appears below the buttons, without a saved mark.
            with patch.object(app, "refresh_profile_cache", return_value=("unchanged", None)):
                self.assertTrue(app.run_queued_profile_refresh())
            profiles._apply_runtime_status(profiles._status())
            self.assertEqual(profiles.notice_var.get(),
                             "Checked 1 profile(s): 0 new picture(s), 1 up to date.")
            self.assertNotEqual(self.footer_notice(context), "✓ Saved")
            self.assertEqual(context.config.read_text(encoding="utf-8"), before)
            profiles.tree.selection_set((system.SYSTEM_ID,))
            profiles._selection_changed()
            self.assertEqual(("normal" if context.profiles.menu_entry_enabled(label) else "disabled"), "disabled")
        self.run_dialog(scenario, initial_library=library)

    def test_first_profile_cache_image_immediately_sets_last_download(self):
        identifier = "f" * 32
        library = {
            "items": [{
                "id": identifier,
                "name": "First download",
                "settings": app.default_import_settings(),
            }],
            "rotation": {"order": []},
        }

        def scenario(context):
            app.get_profile_cache().install(
                identifier,
                {"configuration": "first"},
                {"source": "first"},
                source_png((8, 6)),
                (8, 6),
                source_time="2026-09-28T12:00:00Z",
            )
            state = context.profiles._status()
            self.assertIn(identifier, state["last_downloads"])
            context.profiles._apply_runtime_status(state)
            self.assertNotEqual(context.profiles.tree.set(identifier, "last_download"), "-")

        self.run_dialog(scenario, initial_library=library)

    def run_dialog(self, scenario, initial_library=None, prepare_config=None):
        import tkinter as tk
        from tkinter import ttk
        import pystray
        import marblescape_source_settings as source_ui
        import marblescape_profile_settings as profile_ui

        class Finished(Exception):
            pass

        icons = []

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu
                self.stopped = False
                icons.append(self)

            def update_menu(self):
                pass

            def stop(self):
                self.stopped = True

            def notify(self, *args):
                pass

            def run(self, setup):
                action = self.menu.items[1]._action
                inspect.getclosurevars(action).nonlocals["run_settings_dialog"](self)
                raise Finished()

        saved_configuration = app.capture_loaded_configuration()
        saved_image_status = deepcopy(app.IMAGE_STATUS)
        saved_rotation_status = deepcopy(app.ROTATION_STATUS)
        saved_rotation_deadline = app.NEXT_ROTATION_DEADLINE
        events = (app.APPLICATION_STOP_EVENT, app.CONFIGURATION_RELOAD_EVENT,
                  app.SETTINGS_ONLY_RELOAD_EVENT, app.FORCE_UPDATE_EVENT)
        event_states = [event.is_set() for event in events]
        real_tk = tk.Tk
        real_source = source_ui.SourceSettings
        real_profiles = profile_ui.ProfilesSettings
        sources, profiles, errors, roots = [], [], [], []

        def capture_source(*args, **kwargs):
            result = real_source(*args, **kwargs)
            sources.append(result)
            return result

        def capture_profiles(*args, **kwargs):
            result = real_profiles(*args, **kwargs)
            profiles.append(result)
            return result

        with tempfile.TemporaryDirectory(prefix="marblescape-image-ui-") as directory, ExitStack() as stack:
            stack.enter_context(patch.object(app, "SCRIPT_DIR", Path(directory)))
            config = Path(directory) / "settings.toml"
            stack.enter_context(patch.object(
                app, "PROFILE_LIBRARY_PATH", Path(directory) / "profiles.toml"
            ))
            text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            text = app.replace_toml_values(text, [
                ("output", "windows_root", Path(directory).as_posix()),
                ("output", "linux_root", Path(directory).as_posix()),
            ])
            if prepare_config is not None:
                text = prepare_config(text)
            config.write_text(text, encoding="utf-8")

            def hidden_tk():
                root = real_tk()
                roots.append(root)
                root.withdraw()
                root.deiconify = lambda: None
                root.lift = lambda: None
                root.attributes = lambda *args, **kwargs: None
                root.report_callback_exception = lambda kind, error, traceback: errors.append(error)

                def run_scenario(*args, **kwargs):
                    try:
                        form = inspect.getclosurevars(profiles[0]._capture_settings).nonlocals
                        notebook = next(widget for widget in self.descendants(root) if isinstance(widget, ttk.Notebook))
                        context = SimpleNamespace(root=root, source=sources[0], profiles=profiles[0], icon=icons[0],
                                                  variables=form["variables"], form=form, notebook=notebook,
                                                  config=config, directory=Path(directory), errors=errors)
                        scenario(context)
                        self.assertEqual(errors, [])
                    finally:
                        root.destroy()
                root.mainloop = run_scenario
                return root

            stack.enter_context(patch.object(pystray, "Icon", FakeIcon))
            stack.enter_context(patch.object(tk, "Tk", side_effect=hidden_tk))
            stack.enter_context(patch.object(source_ui, "SourceSettings", side_effect=capture_source))
            stack.enter_context(patch.object(profile_ui, "ProfilesSettings", side_effect=capture_profiles))
            stack.enter_context(patch.object(app, "get_noaa_client", return_value=FakeCatalogue()))
            stack.enter_context(patch.object(app, "get_catalogue_client", return_value=FakeCatalogue()))
            stack.enter_context(patch.object(app, "is_windows_startup_enabled", return_value=False))
            stack.enter_context(patch.object(app, "set_windows_startup_enabled", side_effect=AssertionError("Unexpected startup change")))
            stack.enter_context(patch.object(app, "create_windows_tray_image", return_value=None))
            stack.enter_context(patch.object(app, "set_windows_wallpaper", side_effect=AssertionError("Unexpected wallpaper change")))
            # No note of an image update made by another test.
            stack.enter_context(patch.dict(app.IMAGE_OUTCOME, {"serial": 0, "kind": None,
                                                               "profile": None, "time": None}))
            if not isinstance(app.list_windows_wallpaper_monitors, Mock):
                # Sizes never depend on the displays of the computer running the tests.
                stack.enter_context(patch.object(app, "list_windows_wallpaper_monitors", return_value=[]))
            stack.enter_context(patch.object(app, "urlopen", side_effect=AssertionError("Unexpected live network request")))
            stack.enter_context(patch("tkinter.messagebox.showerror", side_effect=lambda *args, **kwargs: errors.append(args)))
            for clipboard_method in ("clipboard_clear", "clipboard_append", "clipboard_get"):
                stack.enter_context(patch.object(tk.Misc, clipboard_method,
                    side_effect=AssertionError("Settings integration tests must not access the system clipboard.")))
            stack.enter_context(patch.object(app, "log"))
            if initial_library is not None:
                app.write_profile_library_file_unlocked(
                    app.normalize_library(initial_library),
                    Path(directory) / "profiles.toml",
                )
            try:
                with self.assertRaises(Finished):
                    app.run_with_windows_tray(["--config", str(config)])
                self.assertEqual(errors, [])
                self.assertTrue(sources[0]._closed)
                self.assertTrue(profiles[0]._closed)
            finally:
                for controller in [*sources, *profiles]:
                    controller.close()
                for root in roots:
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass
                sources.clear()
                profiles.clear()
                roots.clear()
                controller = root = None
                gc.collect()
                app.restore_loaded_configuration(saved_configuration)
                app.IMAGE_STATUS.clear()
                app.IMAGE_STATUS.update(saved_image_status)
                app.ROTATION_STATUS.clear()
                app.ROTATION_STATUS.update(saved_rotation_status)
                app.NEXT_ROTATION_DEADLINE = saved_rotation_deadline
                for event, was_set in zip(events, event_states):
                    event.set() if was_set else event.clear()

    def test_about_update_check_opens_the_startup_notice(self):
        from tkinter import ttk

        release = {
            "current": app.VERSION,
            "latest": "v9.9.9",
            "update_available": True,
            "url": app.PROJECT_URL + "/releases/tag/v9.9.9",
        }
        original_thread = app.threading.Thread

        def immediate_update_thread(*args, **kwargs):
            if kwargs.get("name") == "MarbleScape-update-check":
                return SimpleNamespace(start=kwargs["target"])
            return original_thread(*args, **kwargs)

        def scenario(context):
            check_button = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.Button)
                and widget.cget("text") == "Check for updates"
            )
            check_button.invoke()
            context.root.update()
            notice = next(
                widget for widget in context.root.winfo_children()
                if isinstance(widget, tk.Toplevel)
                and widget.title() == "MarbleScape update available"
            )
            labels = [
                widget.cget("text") for widget in self.descendants(notice)
                if isinstance(widget, ttk.Label)
            ]
            self.assertIn("A new MarbleScape version is available.", labels)
            self.assertIn(f"Installed: v{app.VERSION}    Latest: v9.9.9", labels)
            buttons = {
                widget.cget("text"): widget
                for widget in self.descendants(notice)
                if isinstance(widget, ttk.Button)
            }
            self.assertEqual(set(buttons), {"Skip this version", "Open GitHub"})
            buttons["Skip this version"].invoke()
            self.assertFalse(notice.winfo_exists())
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertEqual(saved["updates"]["skipped_version"], "v9.9.9")

        with patch.object(app.threading, "Thread", side_effect=immediate_update_thread), \
             patch.object(app, "check_github_update", return_value=release), \
             patch.object(app, "SKIPPED_UPDATE_VERSION", "v9.9.9"):
            self.run_dialog(scenario)

    def test_info_and_about_are_sectioned_and_keep_key_facts(self):
        from tkinter import ttk
        def scenario(context):
            headings = {str(widget.cget("text")) for widget in self.descendants(context.root)
                        if isinstance(widget, ttk.LabelFrame)}
            self.assertTrue({title for title, _text in app.INFO_SECTIONS}.issubset(headings))
            self.assertTrue({"About MarbleScape", "Project & updates", "Privacy & local data",
                             "License & credits", "Help & support"}.issubset(headings))
            # Help & support follows Project & updates.
            rows = {str(widget.cget("text")): int(widget.grid_info()["row"])
                    for widget in self.descendants(context.root)
                    if isinstance(widget, ttk.LabelFrame) and widget.grid_info()}
            self.assertEqual(sorted(("Project & updates", "Help & support", "Privacy & local data",
                                     "License & credits"), key=rows.get),
                             ["Project & updates", "Help & support", "Privacy & local data",
                              "License & credits"])
            # The shortcut legend is the second section.
            self.assertEqual([title for title, _text in app.INFO_SECTIONS][:2],
                             ["Quick start", "Keyboard shortcuts"])
            self.assertEqual(rows["Keyboard shortcuts"], rows["Quick start"] + 1)
            # Key facts as shown: without the **bold** and *italic* markers.
            from marblescape_theme import plain_text
            legend = plain_text(dict(app.INFO_SECTIONS)["Keyboard shortcuts"])
            for shortcut in ("Double-click a row", "Del:", "Ctrl+C", "Ctrl+A", "Ctrl+Up/Ctrl+Down",
                             "Shift+click", "Esc:", "Type letters", "Enter chooses", "Tab/Shift+Tab",
                             "Tray icon"):
                self.assertIn(shortcut, legend)
            text = " ".join(plain_text(body) for _title, body in app.INFO_SECTIONS)
            # The Info sections show their text formatted: bold for what you click.
            info = next(widget for widget in self.descendants(context.root)
                        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Keyboard shortcuts")
            shown = next(widget for widget in info.winfo_children() if widget.winfo_class() == "Text")
            self.assertEqual(shown.get("1.0", "end-1c"), legend)
            self.assertIn("bold", shown.tag_names("1.0"))
            self.assertEqual(str(shown.cget("state")), "disabled")
            for fact in ("Save/OK", "UUID", "EXIF", "UTC", "Relative to now", "Months back",
                         "No Data", "rotation", "widths", "export/settings", "catalogue refreshes",
                         "Update check interval", "_no profile", "Right-click table cells",
                         "Load (copies it to the Image tab)", "Toggle rotation", "regional presets use Geographic",
                         "GEOS: MSG RSS", "only the True Colour layer", "closed cloud cover",
                         "night pass", "Variants per profile", "current picture always stays"):
                self.assertIn(fact, text)
            self.assertNotIn("Check/Uncheck", text)
            # About > Privacy names the table columns by their current titles.
            labels = " ".join(str(widget.cget("text")) for widget in self.descendants(context.root)
                              if isinstance(widget, ttk.Label))
            self.assertIn("profile table's Status and History columns", labels)
            self.assertNotIn("Active table marker", labels)
        self.run_dialog(scenario)

    def test_about_support_opens_centered_reuses_dialog_and_opens_only_requested_links(self):
        from tkinter import ttk
        def scenario(context):
            support = next(widget for widget in self.descendants(context.root)
                           if isinstance(widget, ttk.Button) and widget.cget("text") == "Support this project")
            # The PayPal heart before the text, in the text color of the mode.
            self.assertEqual(str(support.cget("compound")), "left")
            self.assertIn(str(support._marblescape_heart), support.tk.splitlist(support.cget("image")))
            with patch.object(app.webbrowser, "open", return_value=True) as browser:
                support.invoke()
                browser.assert_not_called()
                dialogs = [child for child in context.root.winfo_children() if isinstance(child, tk.Toplevel)]
                self.assertEqual(len(dialogs), 1)
                dialog = dialogs[0]
                self.assertEqual(dialog.title(), "Support this project")
                texts = [str(widget.cget("text")) for widget in self.descendants(dialog)
                         if isinstance(widget, ttk.Label)]
                self.assertEqual(texts, ["Support MarbleScape"])
                self.assertIs(dialog.tk, context.root.tk)
                self.assertEqual(str(dialog.transient()), str(context.root))
                x = max(0, context.root.winfo_rootx() + (context.root.winfo_width() - dialog.winfo_reqwidth()) // 2)
                y = max(0, context.root.winfo_rooty() + (context.root.winfo_height() - dialog.winfo_reqheight()) // 2)
                self.assertTrue(dialog.geometry().endswith(f"+{x}+{y}"), dialog.geometry())
                images = tuple(str(image) for image in dialog._support_icons.values())
                self.assertTrue(all(name in dialog.tk.call("image", "names") for name in images))
                # The icons take the text color: white on the dark look, dark on the light one.
                import marblescape_theme as theme
                if theme.sv_ttk is not None:
                    def star_center():
                        value = dialog.tk.call(images[0], "get", 10, 10)
                        parts = value.split() if isinstance(value, str) else value
                        return tuple(int(part) for part in parts[:3])
                    for mode, expected in (("dark", (250, 250, 250)), ("light", (28, 28, 28)),
                                           ("dark", (250, 250, 250))):
                        theme.apply_appearance(context.root, mode)
                        context.root.update()
                        self.assertEqual(star_center(), expected, mode)
                support.invoke()
                self.assertEqual([child for child in context.root.winfo_children() if isinstance(child, tk.Toplevel)], dialogs)
                buttons = {widget.cget("text"): widget for widget in self.descendants(dialog) if isinstance(widget, ttk.Button)}
                for label, url in (("Star on GitHub", app.PROJECT_URL), ("Ko-fi", "https://ko-fi.com/gittegatt"),
                                   ("PayPal", "https://paypal.me/gittegatt")):
                    buttons[label].invoke()
                    browser.assert_called_with(url, new=2)
                self.assertEqual(browser.call_count, 3)
                browser.return_value = False
                with patch("tkinter.messagebox.showerror") as error:
                    buttons["Ko-fi"].invoke()
                self.assertIn("browser could not be opened", error.call_args.args[1])
                self.assertIs(error.call_args.kwargs["parent"], dialog)
                buttons["Close"].invoke()
                self.assertFalse(dialog.winfo_exists())
                self.assertFalse(any(name in context.root.tk.call("image", "names") for name in images))
                support.invoke()
                reopened = next(child for child in context.root.winfo_children() if isinstance(child, tk.Toplevel))
                self.assertIsNot(reopened, dialog)
                reopened.destroy()
        self.run_dialog(scenario)

    def test_tray_support_request_is_handled_by_settings_gui_without_new_thread(self):
        def scenario(context):
            self.wait_for_source(context)
            icon = inspect.getclosurevars(context.profiles._on_apply).nonlocals["icon"]
            action = next(item._action for item in icon.menu.items if item.text == "Support this project")
            with patch.object(app.threading, "Thread", side_effect=AssertionError("Do not create another GUI thread.")):
                action(icon, None)
                deadline = time.monotonic() + 2
                while not any(isinstance(child, tk.Toplevel) for child in context.root.winfo_children()):
                    self.assertLess(time.monotonic(), deadline, "Tray support request was not handled")
                    context.root.update()
                    time.sleep(0.01)
            dialog = next(child for child in context.root.winfo_children() if isinstance(child, tk.Toplevel))
            self.assertEqual(dialog.title(), "Support this project")
            self.assertIs(dialog.tk, context.root.tk)
            dialog.destroy()
        self.run_dialog(scenario)

    def test_standalone_support_uses_its_own_image_interpreter_when_another_root_exists(self):
        from tkinter import ttk
        original_tk, original_thread = tk.Tk, app.threading.Thread
        def scenario(context):
            self.wait_for_source(context)
            icon = inspect.getclosurevars(context.profiles._on_apply).nonlocals["icon"]
            action = next(item._action for item in icon.menu.items if item.text == "Support this project")
            state = inspect.getclosurevars(action).nonlocals["settings_dialog_state"]
            old_available = state["support_parent_available"]
            state["support_parent_available"] = False  # Model another non-Settings Tk dialog owning the default root.
            inspected = []
            def hidden_support():
                root = original_tk()
                root.withdraw()
                root.deiconify = lambda: None
                root.lift = lambda: None
                root.attributes = lambda *args: None
                def inspect_support():
                    self.assertIsNot(root.tk, context.root.tk)
                    self.assertEqual(root.title(), "Support this project")
                    names = root.tk.call("image", "names")
                    for image in root._support_icons.values():
                        self.assertIs(image.tk, root.tk)
                        self.assertIn(str(image), names)
                    buttons = {widget.cget("text"): widget for widget in self.descendants(root)
                               if isinstance(widget, ttk.Button)}
                    self.assertEqual(set(buttons), {"Star on GitHub", "Ko-fi", "PayPal", "Close"})
                    inspected.append(True)
                    buttons["Close"].invoke()
                root.mainloop = inspect_support
                return root
            def immediate_support(*args, **kwargs):
                return SimpleNamespace(start=kwargs["target"]) if kwargs.get("name") == "MarbleScapeSupport" else original_thread(*args, **kwargs)
            try:
                with patch.object(tk, "Tk", side_effect=hidden_support), \
                     patch.object(app.threading, "Thread", side_effect=immediate_support):
                    action(icon, None)
                    action(icon, None)
                self.assertEqual(inspected, [True, True])
            finally:
                state["support_parent_available"] = old_available
        self.run_dialog(scenario)

    def test_failed_support_construction_is_cleaned_up_and_can_be_retried(self):
        from tkinter import ttk
        def scenario(context):
            support = next(widget for widget in self.descendants(context.root)
                           if isinstance(widget, ttk.Button) and widget.cget("text") == "Support this project")
            with patch("PIL.ImageTk.PhotoImage", side_effect=RuntimeError("Test icon failure")), \
                 patch("tkinter.messagebox.showerror") as error:
                support.invoke()
            self.assertIn("Test icon failure", error.call_args.args[1])
            self.assertFalse(any(isinstance(child, tk.Toplevel) for child in context.root.winfo_children()))
            support.invoke()
            dialog = next(child for child in context.root.winfo_children() if isinstance(child, tk.Toplevel))
            dialog.destroy()
        self.run_dialog(scenario)

    def test_complete_image_profile_restores_form_and_survives_apply_and_backup(self):
        def scenario(context):
            variables = context.variables
            saved_sources = deepcopy(app.DEFAULT_SOURCE_PROFILES)
            saved_sources["goes_west"]["resolution"] = "339x339"
            context.source.set_selection("eumetsat", saved_sources)
            values = {
                "satellite_layer": "MTG TrueColor (day)", "projection": "North Polar",
                "fit_mode": "crop", "zoom": "1.65",
                "truecolor_black_night": True, "width": "1024", "height": "768",
                "aspect_ratio": "4:3", "eumetsat_render_scale": "1.5", "background_color": "#123456",
                "latest_folder": (context.directory / "profile-latest").as_posix(),
            }
            for name, value in values.items():
                variables[name].set(value)
            expected = context.profiles._capture_settings()
            before = context.config.read_text(encoding="utf-8")
            context.profiles.add_current("Detailed Earth")
            self.assertEqual(context.errors, [])
            saved_library = context.profiles.get_library()
            self.assertEqual(len(saved_library["items"]), 1)
            self.assertEqual(saved_library["items"][0]["settings"], expected)
            self.assertEqual(context.config.read_text(encoding="utf-8"), before, "Profile drafts must wait for Apply")

            context.source.set_selection("solar", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            context.source.select_eumetsat_layer("mtg_fd:rgb_geocolour")
            changed = {
                "projection": "Geographic",
                "fit_mode": "fit", "zoom": "2.8",
                "truecolor_black_night": False, "width": "800", "height": "450",
                "aspect_ratio": "16:9", "eumetsat_render_scale": "auto", "background_color": "#FFFFFF",
                "latest_folder": (context.directory / "changed-latest").as_posix(),
            }
            for name, value in changed.items():
                variables[name].set(value)
            context.profiles.load_selected()
            self.wait_for_source(context)
            self.assertEqual(context.source.provider, "eumetsat")
            self.assertEqual(context.profiles._capture_settings(), expected)
            self.assertEqual(variables["eumetsat_render_scale"].get(), "1.5")
            self.assertEqual(variables["projection"].get(), "North Polar")
            self.assertEqual(context.source.get_selection()[1], saved_sources)
            # Output size, background and the Latest folder are device settings.
            for name in ("width", "height", "aspect_ratio", "background_color", "latest_folder"):
                self.assertEqual(variables[name].get(), changed[name], name)

            self.apply(context, tab="Profiles")
            after = self.apply(context, tab="Image")
            self.assertNotIn("image_profiles", after)
            self.assertEqual(
                app.read_profile_library_file(context.directory / "profiles.toml"),
                saved_library,
            )
            for section in ("source", "layers"):
                self.assertEqual(after[section], expected[section])
            expected_sources = deepcopy(expected["sources"])
            expected_sources["copernicus"]["gap_fill_mode"] = (
                expected_sources["copernicus"].pop("coverage_mode")
            )
            self.assertEqual(after["sources"], expected_sources)
            for name, value in expected["view"].items():
                self.assertEqual(after["view"][name], value, f"view.{name}")
            # Save stored the device settings edited on other tabs; Apply Image
            # added only the profile's EUMETSAT render quality.
            self.assertEqual(after["output"], {
                **tomllib.loads(before)["output"], "width": 800, "height": 450,
                "aspect_ratio": "16:9", "background_color": "#FFFFFF",
                "latest_folder": changed["latest_folder"], "render_scale": 1.5,
            })
            payload = app.create_settings_backup_payload()
            restored, restored_profiles, startup = app.parse_settings_backup_payload(
                json.loads(json.dumps(payload))
            )
            self.assertFalse(startup)
            self.assertNotIn("image_profiles", tomllib.loads(restored))
            self.assertEqual(restored_profiles, saved_library)
            self.assertEqual(tomllib.loads(restored)["view"], after["view"])
            self.assertEqual(tomllib.loads(restored)["output"], after["output"])
        with patch.object(app, "list_windows_wallpaper_monitors",
                          return_value=[{"id": "DISPLAY-ONLY", "rect": (0, 0, 1920, 1080)}]):
            self.run_dialog(scenario)

    def test_image_update_checks_lead_the_updates_and_downloads_tab(self):
        from tkinter import ttk

        def scenario(context):
            sections = {
                widget.cget("text"): widget
                for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") in {"Image update checks", "Download display", "Monitor output"}
            }
            self.assertEqual(set(sections), {"Image update checks", "Download display", "Monitor output"})
            checks = sections["Image update checks"]
            self.assertIs(checks.master, sections["Download display"].master)
            self.assertEqual(int(checks.grid_info()["row"]), 0)
            self.assertGreater(int(sections["Download display"].grid_info()["row"]), 0)
            # Monitor output stays on General.
            self.assertIsNot(sections["Monitor output"].master, checks.master)
            tabs = {context.notebook.tab(tab_id, "text"): context.root.nametowidget(tab_id)
                    for tab_id in context.notebook.tabs()}
            updates_tab = tabs["Downloads & Updates"]
            self.assertTrue(str(checks).startswith(str(updates_tab)))

        self.run_dialog(scenario)

    def test_appearance_closes_general_and_the_wallpaper_hint_follows_monitor_output(self):
        from tkinter import ttk

        def scenario(context):
            sections = {widget.cget("text"): widget for widget in self.descendants(context.root)
                        if isinstance(widget, ttk.LabelFrame)}
            # Info has an Appearance section too; this one shares General with Wallpaper.
            output = sections["Monitor output"]
            general = [widget for widget in sections["Startup and wallpaper"].master.winfo_children()
                       if isinstance(widget, ttk.LabelFrame) and widget.grid_info()]
            ordered = [widget.cget("text") for widget in
                       sorted(general, key=lambda widget: int(widget.grid_info()["row"]))]
            self.assertEqual(ordered, ["Startup and wallpaper", "Output device", "Monitor output",
                                       "Date and time", "Appearance", "Actions"])
            hint = next(widget for widget in output.winfo_children() if isinstance(widget, ttk.Label)
                        and str(widget.cget("text")).startswith("Choose each display under Output device"))
            self.assertEqual(int(hint.grid_info()["row"]),
                             max(int(widget.grid_info()["row"]) for widget in output.winfo_children()
                                 if widget.grid_info()))
            self.assertFalse(any(isinstance(widget, ttk.Label)
                                 and str(widget.cget("text")).startswith("Choose each display")
                                 for widget in sections["Startup and wallpaper"].winfo_children()))

        self.run_dialog(scenario)

    def test_update_check_interval_uses_value_and_unit_dropdowns(self):
        from tkinter import ttk

        def scenario(context):
            updates = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Image update checks"
            )
            labels = {str(widget.cget("text")) for widget in self.descendants(updates)
                      if isinstance(widget, ttk.Label)}
            self.assertIn("Update check interval", labels)
            self.assertIn("Next check:", labels)
            combos = [widget for widget in self.descendants(updates)
                      if isinstance(widget, ttk.Combobox)]
            self.assertEqual(len(combos), 2)
            self.assertEqual(set(combos[1]["values"]), set(app.UPDATE_INTERVAL_UNITS))
            context.variables["update_interval_value"].set("2")
            context.variables["update_interval_unit"].set("weeks")
            saved = self.apply(context)
            self.assertEqual(saved["service"]["update_interval_minutes"], 20160.0)

        self.run_dialog(scenario)

    def test_profile_history_actions_apply_current_policy_to_every_profile(self):
        from tkinter import ttk

        first, second = "a" * 32, "b" * 32
        library = {"items": [
            {"id": first, "name": "Earth", "settings": {}},
            {"id": second, "name": "Sun", "settings": {}},
        ]}

        def scenario(context):
            section = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "History (profile)"
            )
            buttons = {str(widget.cget("text")): widget
                       for widget in self.descendants(section)
                       if isinstance(widget, ttk.Button)}
            self.assertTrue({"Clear profile history",
                             "Apply to all profiles", "Open profile history"}.issubset(buttons))
            # Profiles share the global History folder; there is no per-profile picker.
            self.assertNotIn("Choose...", buttons)
            # Apply to all profiles sits right beside Clear profile history; Open stays right.
            clear, apply_all = buttons["Clear profile history"], buttons["Apply to all profiles"]
            self.assertIs(apply_all.master, clear.master)
            self.assertEqual(int(apply_all.grid_info()["column"]), int(clear.grid_info()["column"]) + 1)
            self.assertEqual(clear.master.grid_info()["sticky"], "w")
            self.assertEqual(buttons["Open profile history"].grid_info()["sticky"], "e")
            # Open profile history opens the folder of the profile chosen in the dropdown.
            combo = next(widget for widget in self.descendants(section) if isinstance(widget, ttk.Combobox)
                         and str(widget.cget("state")) == "readonly"
                         and any(value.startswith("Sun [") for value in widget.cget("values")))
            for name, identifier in (("Sun", second), ("Earth", first)):
                combo.set(next(value for value in combo.cget("values") if value.startswith(name + " [")))
                with patch.object(app.os, "startfile", create=True) as startfile:
                    buttons["Open profile history"].invoke()
                folder = app.profile_history_directory(identifier, name)
                startfile.assert_called_once_with(str(folder))
                self.assertTrue(folder.is_dir())
            # Until its folder rename succeeded, a profile's History keeps its saved name.
            context.profiles._saved_history_names[first] = "Earth before"
            combo.set(next(value for value in combo.cget("values") if value.startswith("Earth [")))
            with patch.object(app.os, "startfile", create=True) as startfile:
                buttons["Open profile history"].invoke()
            startfile.assert_called_once_with(str(app.profile_history_directory(first, "Earth before")))
            context.variables["profile_history_policies"].set("{}")
            check = next(widget for widget in self.descendants(section)
                         if isinstance(widget, ttk.Checkbutton)
                         and widget.cget("text") == "Enable history for this profile")
            check.invoke()
            buttons["Apply to all profiles"].invoke()
            policies = json.loads(context.variables["profile_history_policies"].get())
            self.assertEqual(set(policies), {first, second})
            self.assertTrue(all(policy["enabled"] for policy in policies.values()))
            self.assertTrue(all("folder" not in policy for policy in policies.values()))
            # A draft: no message next to Clear history, the footer says it waits for Save.
            self.assertFalse(any(str(widget.cget("text")).startswith("Applied ")
                                 or "Save or OK" in str(context.root.getvar(widget.cget("textvariable"))
                                                        if str(widget.cget("textvariable")) else "")
                                 for widget in self.descendants(context.root) if isinstance(widget, ttk.Label)))
            tab = next(tab_id for tab_id in context.notebook.tabs()
                       if context.notebook.tab(tab_id, "text") == "History & Storage")
            context.notebook.select(tab)
            context.root.event_generate("<<SettingsDraftChanged>>")
            context.root.update()
            self.assertEqual(self.footer_notice(context), "Unsaved changes")

        self.run_dialog(scenario, initial_library=library)

    def test_save_is_enabled_only_for_pending_changes_and_image_tab_keeps_apply_image(self):
        from tkinter import ttk

        def scenario(context):
            def select(tab):
                context.notebook.select(next(tab_id for tab_id in context.notebook.tabs()
                                             if context.notebook.tab(tab_id, "text") == tab))
                context.root.update()

            def button():
                return next(widget for widget in self.descendants(context.root)
                            if isinstance(widget, ttk.Button)
                            and widget.cget("text") in {"Save", "Apply Image"})

            def state():
                context.root.event_generate("<<SettingsDraftChanged>>")
                return str(button()["state"])

            select("General")
            self.assertEqual(button().cget("text"), "Save")
            self.assertEqual(state(), "disabled")
            select("Image")
            self.assertEqual(button().cget("text"), "Apply Image")
            self.assertEqual(state(), "normal")
            select("General")
            original = context.variables["time_zone"].get()
            context.variables["time_zone"].set("UTC")
            self.assertEqual(state(), "normal")
            # Reverting the edit by hand disables Save again.
            context.variables["time_zone"].set(original)
            self.assertEqual(state(), "disabled")
            # Profile-list changes are saved at once and never leave Save pending.
            context.profiles.add_current("Saved at once")
            self.assertEqual([item["name"] for item in app.read_profile_library_file()["items"]],
                             ["Saved at once"])
            self.assertEqual(state(), "disabled")
            context.profiles.random_shuffle_var.set(not context.profiles.random_shuffle_var.get())
            self.assertEqual(state(), "disabled")
            # The table's column layout is saved at once, too ...
            context.profiles.sort_by("name")
            self.assertEqual(state(), "disabled")
            # ... also while another tab has an unsaved edit, which alone keeps Save pending.
            context.variables["time_zone"].set("UTC")
            context.profiles.sort_by("name")
            self.assertEqual(state(), "normal")
            context.variables["time_zone"].set(original)
            self.assertEqual(state(), "disabled")
            # Apply Image does not make Save available by itself ...
            self.apply(context, tab="Image")
            select("General")
            self.assertEqual(state(), "disabled")
            # ... and does not hide edits on other tabs that still need Save.
            context.variables["time_zone"].set("UTC")
            self.apply(context, tab="Image")
            select("General")
            self.assertEqual(state(), "normal")
            saved = self.apply(context)
            self.assertEqual(saved["display"]["time_zone"], "utc")
            self.assertEqual(state(), "disabled")

        self.run_dialog(scenario)

    def test_every_tab_fits_the_minimum_window_width(self):
        def scenario(context):
            root = context.root
            min_width = root.minsize()[0]
            self.assertGreaterEqual(min_width, app.SETTINGS_MIN_WIDTH)
            root.tk.call("wm", "deiconify", root._w)
            root.tk.call("wm", "geometry", root._w, f"{min_width}x760")
            for _ in range(3):
                root.update()
            self.assertEqual(root.winfo_width(), min_width)
            notebook = context.notebook

            def clipped(container, right_edge):
                found = []
                for widget in self.descendants(container):
                    # The profile table scrolls sideways by design.
                    if not widget.winfo_ismapped() or widget.winfo_class() == "Treeview":
                        continue
                    over = max(widget.winfo_rootx() + widget.winfo_width() - right_edge,
                               widget.winfo_reqwidth() - widget.winfo_width())
                    if over > 0:
                        found.append((widget.winfo_class(), str(widget), over))
                return found

            for tab in notebook.tabs():
                notebook.select(tab)
                for _ in range(3):
                    root.update()
                canvas = next(child for child in root.nametowidget(tab).winfo_children()
                              if child.winfo_class() == "Canvas")
                right_edge = canvas.winfo_rootx() + canvas.winfo_width()
                self.assertEqual(clipped(canvas, right_edge), [], notebook.tab(tab, "text"))
            close = next(widget for widget in self.descendants(root)
                         if widget.winfo_class() == "TButton" and str(widget.cget("text")) == "Close")
            footer = close.master
            self.assertEqual(clipped(footer, root.winfo_rootx() + root.winfo_width()), [], "footer")
            # The whole tab strip fits: no overflow scrollbar, so switching tabs never
            # scrolls the strip, and every tab shows its full title.
            from marblescape_theme import TAB_BUTTON_STYLE
            tab_buttons = [widget for widget in self.descendants(root)
                           if widget.winfo_class() == "TRadiobutton"
                           and str(widget.cget("style")) == TAB_BUTTON_STYLE]
            self.assertEqual(len(tab_buttons), len(notebook.tabs()))
            strip = tab_buttons[0].master.master
            strip_scrollbar = next(widget for widget in strip.winfo_children()
                                   if widget.winfo_class() == "TScrollbar")
            self.assertFalse(strip_scrollbar.winfo_ismapped())
            for button in tab_buttons:
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), button.cget("text"))

        self.run_dialog(scenario)

    def test_rotation_status_names_the_active_profile_then_the_next_switch(self):
        def scenario(context):
            saved = dict(app.ROTATION_STATUS)
            self.addCleanup(app.ROTATION_STATUS.update, saved)
            # Now showing names the picture on screen first, here the Latest snapshot.
            with patch.object(app, "shown_profile_row_id", return_value=app.latest_snapshot.SYSTEM_ID):
                app.set_rotation_status("Active profile: Tongue of the Ocean", time.monotonic() + 60)
                text = context.profiles._status()["text"]
                self.assertTrue(text.startswith(
                    "Active profile: Latest snapshot (no profile)\nUpcoming profile: "), text)
                self.assertTrue(text.endswith("\nRotation: enabled"), text)
                app.set_rotation_status("Rotation is disabled.", None)
                self.assertEqual(context.profiles._status()["text"],
                                 "Active profile: Latest snapshot (no profile)\n\nRotation: disabled")
        self.run_dialog(scenario)

    def test_image_header_shows_the_auto_priority_in_its_color(self):
        from tkinter import ttk
        import marblescape_theme as theme

        def scenario(context):
            header = context.source.frame.master.nametowidget("image_header")
            auto_line = header.nametowidget("auto_line")
            priority = auto_line.nametowidget("priority")
            self.assertEqual(str(auto_line.nametowidget("caption").cget("text")), "Auto Recommend: ")
            rest = auto_line.nametowidget("rest")

            def refreshed(auto):
                status = {"provider": "copernicus", "timestamp": "2026-10-07T05:37:00Z", "error": "",
                          "interval_minutes": 15, "fixed_time": False, "data_coverage": 100.0, "auto": auto}
                with patch.object(app, "IMAGE_SOURCE", "copernicus"), \
                        patch.object(app, "SOURCE_PROFILES", dict(app.SOURCE_PROFILES, copernicus=dict(
                            app.SOURCE_PROFILES["copernicus"], coverage_mode="single"))), \
                        patch.dict(app.IMAGE_STATUS, status):
                    deadline = time.monotonic() + 3
                    expected = app.image_header_auto_parts()
                    # The status section refreshes every second.
                    while time.monotonic() < deadline:
                        context.root.update()
                        if str(priority.cget("text")) == expected[0] and auto_line.winfo_manager():
                            break
                        time.sleep(0.05)
                return str(priority.cget("text")), str(priority.cget("foreground")), str(rest.cget("text"))

            for key, label, color in (("newest", "Newest", "newest"), ("fewest_clouds", "Fewest clouds", "link"),
                                      ("full_coverage", "Data coverage", "success")):
                auto = {"enabled": True, "priority": label, "precise": False,
                        "choice": "Max. cloud cover 100% · Fill gaps, 7 days"}
                with self.subTest(priority=key):
                    shown = refreshed(auto)
                    # The color of the mode the window shows (Settings may follow Windows).
                    self.assertEqual(shown, (label, theme.palette(priority)[color],
                                             " · Max. cloud cover 100% · Fill gaps, 7 days"))
                    self.assertTrue(auto_line.winfo_manager())
            # The auto line starts below the text after "On screen: ".
            probe = ttk.Label(header, text="On screen: ")
            width = probe.winfo_reqwidth()
            probe.destroy()
            padx = auto_line.tk.splitlist(auto_line.grid_info()["padx"])
            self.assertEqual(int(str(padx[0])), width)
            self.assertGreater(width, 40)
            # A mode change recolors it at once, to the other mode's color.
            other = "light" if theme.current_mode(priority) == "dark" else "dark"
            with patch.object(theme, "current_mode", return_value=other):
                theme._notify_theme_listeners(context.root)
                self.assertEqual(str(priority.cget("foreground")), theme.PALETTES[other]["success"])
            # Off: "No" in the normal text color.
            text, foreground, rest_text = refreshed({"enabled": False})
            self.assertEqual((text, rest_text), ("No", ""))
            self.assertNotIn(foreground, set(theme.palette(priority).values()) - {""})
            self.assertEqual(context.errors, [])

        self.run_dialog(scenario)

    def test_texts_wrap_only_where_their_section_ends(self):
        from tkinter import ttk

        def scenario(context):
            image_tab = context.source.frame.master
            status = image_tab.nametowidget("image_header").nametowidget("on_screen")
            self.assertTrue(getattr(status, "_marblescape_follows_width", False))
            tabs = {context.notebook.tab(tab_id, "text"): tab_id for tab_id in context.notebook.tabs()}
            context.notebook.select(tabs["Image"])
            context.root.tk.call("wm", "deiconify", context.root._w)
            context.root.tk.call("wm", "geometry", context.root._w, "1400x800")
            for _ in range(3):
                context.root.update()
            wide = int(str(status.cget("wraplength")))
            # Far wider than the old fixed 640 pixels: one line in a wide window.
            self.assertGreater(wide, 900)
            self.assertLessEqual(wide, image_tab.winfo_width())
            context.root.tk.call("wm", "geometry", context.root._w, f"{context.root.wm_minsize()[0]}x800")
            for _ in range(3):
                context.root.update()
            self.assertLess(int(str(status.cget("wraplength"))), wide)
            # Profiles sizes its own texts.
            self.assertFalse(getattr(context.profiles.status_label, "_marblescape_follows_width", False))

        self.run_dialog(scenario)

    def test_image_source_status_is_right_below_the_profile_header(self):
        from tkinter import ttk

        def scenario(context):
            image_tab = context.source.frame.master
            header = image_tab.nametowidget("image_header")
            self.assertEqual(int(header.grid_info()["row"]), 0)
            # The status refresh fills the picture line when the dialog opens.
            on_screen = header.nametowidget("on_screen")
            shown = context.root.getvar(on_screen.cget("textvariable"))
            self.assertEqual(shown, app.image_header_status_text(app.DISPLAY_TIME_ZONE))
            # Only a Copernicus picture has an auto recommendation line.
            auto_line = header.nametowidget("auto_line")
            self.assertEqual(bool(auto_line.winfo_manager()), app.image_header_auto_parts() is not None)
            # Two lines always, with or without the auto line: the tab does not move.
            reserved = [int(header.grid_rowconfigure(row, "minsize")) for row in (0, 1)]
            self.assertGreater(reserved[0], 0)
            self.assertEqual(reserved[0], reserved[1])
            self.assertEqual(int(context.source.frame.grid_info()["row"]), 2)
            # Rendering (resolution, tone corrections) right below Source; Find
            # location (sources with latitude/longitude) follows above Imagery updates.
            self.assertEqual(int(context.source.rendering_frame.grid_info()["row"]), 3)
            self.assertEqual(context.source.rendering_frame.cget("text"), "Rendering")
            self.assertEqual(int(context.source.catalogue_frame.grid_info()["row"]), 7)
            # Copernicus Data Space access moved to the Access tab.
            self.assertFalse(str(context.source.account_frame).startswith(str(image_tab)))

        self.run_dialog(scenario)

    def test_access_tab_holds_the_copernicus_credentials_and_save_stores_them(self):
        from tkinter import ttk

        def scenario(context):
            tabs = {context.notebook.tab(tab_id, "text"): context.root.nametowidget(tab_id)
                    for tab_id in context.notebook.tabs()}
            self.assertIn("Access", tabs)
            access = next(widget for widget in self.descendants(context.root)
                          if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Image source access")
            self.assertTrue(str(access).startswith(str(tabs["Access"])))
            combo = next(widget for widget in self.descendants(access) if isinstance(widget, ttk.Combobox))
            self.assertEqual(tuple(combo.cget("values")), ("Copernicus Browser",))
            self.assertEqual(combo.get(), "Copernicus Browser")
            auth = context.source.copernicus_settings.auth_frame
            self.assertTrue(str(auth).startswith(str(access)))
            self.assertEqual(auth.cget("text"), "Copernicus Data Space access")
            # The credits are an own section at the end of the Access tab.
            credits = context.source.copernicus_settings.credits_frame
            self.assertEqual(credits.cget("text"), "Credits")
            self.assertIs(credits.master, access.master)
            self.assertFalse(str(credits).startswith(str(auth)))
            self.assertEqual(int(credits.grid_info()["row"]),
                             max(int(child.grid_info()["row"]) for child in access.master.grid_slaves()))
            self.assertGreater(int(credits.grid_info()["row"]), int(access.grid_info()["row"]))
            # Shown whatever source the Image tab uses.
            # Copernicus without credentials could not be saved; end on another source.
            for provider in ("copernicus", "goes_east"):
                context.source.set_selection(provider, app.DEFAULT_SOURCE_PROFILES)
                self.wait_for_source(context)
                self.assertEqual(auth.winfo_manager(), "grid", provider)
            # Save on the Access tab stores the client ID; the secret stays protected.
            context.source.copernicus_settings._client_id_var.set("access-tab-client")
            saved = self.apply(context, tab="Access")
            self.assertEqual(saved["copernicus"]["client_id"], "access-tab-client")
            self.assertNotIn("client_secret", saved["copernicus"])

        self.run_dialog(scenario)

    def test_find_location_shows_for_copernicus_fits_and_fills_latitude_longitude(self):
        def scenario(context):
            root = context.root
            source = context.source
            section = source.location_frame
            image_tab = next(tab for tab in context.notebook.tabs()
                             if context.notebook.tab(tab, "text") == "Image")
            context.notebook.select(image_tab)
            source.set_selection("copernicus", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            self.assertEqual(section.cget("text"), "Find location")
            self.assertEqual(section.winfo_manager(), "grid")
            self.assertEqual(int(section.grid_info()["row"]), 5)
            # Recommendation, a small own section between Rendering and Find location.
            recommend = source.recommend_frame
            self.assertEqual(recommend.cget("text"), "Recommendation")
            self.assertEqual(int(source.rendering_frame.grid_info()["row"]), 3)
            self.assertEqual(int(recommend.grid_info()["row"]), 4)
            self.assertEqual(int(section.grid_info()["row"]), 5)
            updates = next(widget for widget in section.master.grid_slaves(column=0)
                           if "text" in widget.keys() and widget.cget("text") == "Imagery updates")
            self.assertEqual(int(updates.grid_info()["row"]), 6)
            # The whole section fits at the minimum window width.
            min_width = root.minsize()[0]
            root.tk.call("wm", "deiconify", root._w)
            root.tk.call("wm", "geometry", root._w, f"{min_width}x760")
            for _ in range(3):
                root.update()
            canvas = next(child for child in root.nametowidget(image_tab).winfo_children()
                          if child.winfo_class() == "Canvas")
            right_edge = canvas.winfo_rootx() + canvas.winfo_width()
            clipped = [(widget.winfo_class(), str(widget)) for widget in [section, *self.descendants(section)]
                       if widget.winfo_ismapped()
                       and (widget.winfo_rootx() + widget.winfo_width() > right_edge
                            or widget.winfo_reqwidth() > widget.winfo_width())]
            self.assertEqual(clipped, [])
            # The steps fill the label column beside the fields without widening it.
            steps = source.location_search.steps_label
            self.assertEqual((int(steps.grid_info()["column"]), int(steps.grid_info()["rowspan"])), (0, 4))
            self.assertTrue(steps.cget("text").startswith("1. Search a place."))
            self.assertLessEqual(steps.winfo_reqwidth(), section.grid_bbox(0, 0)[2])
            # Transfer fills Latitude and Longitude as a draft; the example scene turns None.
            search = source.location_search
            search.coordinates_var.set("53.55, 9.99")
            search.transfer_button.invoke()
            copernicus = source.copernicus_settings
            self.assertEqual((copernicus._latitude_var.get(), copernicus._longitude_var.get()),
                             ("53.55", "9.99"))
            self.assertEqual(copernicus._highlight_var.get(), "None")
            profile = copernicus.get_profile()
            self.assertEqual((profile["latitude"], profile["longitude"]), (53.55, 9.99))
            # Preview opens the Copernicus Browser at that place and the map zoom.
            opened = []
            search._open_url = opened.append
            self.assertTrue(search.preview_button.instate(["!disabled"]))
            search.preview_button.invoke()
            self.assertEqual(opened, [
                f"https://browser.dataspace.copernicus.eu/?zoom={profile['map_zoom']}&lat=53.55&lng=9.99"])
            # Recommend opens the advice window. For the default selection, a mosaic,
            # without OAuth credentials it says so and asks the catalogue nothing.
            self.assertEqual(source.recommend_button.cget("text"), "Compare variants...")
            source.recommend_button.invoke()
            root.update()
            advice_window = copernicus._advice_window
            self.assertTrue(advice_window.is_open)
            self.assertEqual(advice_window.state_var.get(),
                             "Enter the Copernicus OAuth client under Access first.")
            self.assertEqual(advice_window.boxes["fewest"]["name"].cget("text"), "Newest")
            self.assertTrue(advice_window.check_button.instate(["disabled"]))
            self.assertEqual((advice_window.latitude_var.get(), advice_window.longitude_var.get()),
                             ("53.55", "9.99"))
            # Transfer in Find location fills the open window too; a place from the
            # list is named beside its coordinates.
            self.assertEqual(advice_window.place_name_var.get(), "")
            search._show_places([{"label": "Honolulu - Hawaii, United States",
                                  "latitude": 21.3, "longitude": -157.8}])
            search.coordinates_var.set("21.3, -157.8")
            search.transfer_button.invoke()
            self.assertEqual(advice_window.place()[:2], (21.3, -157.8))
            self.assertEqual(advice_window.place_name_var.get(), "Place: Honolulu - Hawaii, United States")
            # Get location from: Find takes the Coordinates field into the window only.
            search.coordinates_var.set("19.6, -155.5")
            advice_window.get_from_find()
            self.assertEqual(advice_window.place()[:2], (19.6, -155.5))
            self.assertEqual(copernicus._latitude_var.get(), "21.3")
            # Get location from: Source goes back to the Image tab's place, named again.
            advice_window.image_button.invoke()
            self.assertEqual(advice_window.place()[:2], (21.3, -157.8))
            self.assertEqual(advice_window.place_name_var.get(), "Place: Honolulu - Hawaii, United States")
            advice_window.close()
            # Apply on a quarterly mosaic: an older quarter relative to now, the newest
            # as Latest available.
            from marblescape_copernicus_advice import Variant
            older = Variant(None, "single", None, period=dt.date(2026, 4, 1), granularity="quarter",
                            offset=2)
            copernicus.apply_recommendation(older, 21.3, -157.8, 10)
            applied = copernicus.get_profile()
            self.assertEqual((applied["date_mode"], applied["quarter_offset"], applied["date"]),
                             ("relative_quarter", 2, "latest"))
            newest = Variant(None, "single", None, period=dt.date(2026, 7, 1), granularity="quarter",
                             offset=1, newest=True)
            copernicus.apply_recommendation(newest, 21.3, -157.8, 10)
            applied = copernicus.get_profile()
            self.assertEqual((applied["date_mode"], applied["quarter_offset"], applied["date"]),
                             ("catalogue", 0, "latest"))
            # Use auto recommendation: Yes takes Latest available and greys out the
            # date; mosaics choose between Newest and Data coverage.
            self.assertEqual(copernicus._auto_var.get(), "No")
            self.assertFalse(copernicus._auto_priority_combo.winfo_manager())
            copernicus._auto_var.set("Yes")
            copernicus._select_auto()
            root.update()
            self.assertEqual(tuple(copernicus._auto_priority_combo.cget("values")), ("Newest", "Data coverage"))
            self.assertEqual(copernicus._auto_priority_var.get(), "Newest")
            self.assertTrue(copernicus._auto_priority_combo.winfo_manager())
            self.assertEqual(str(copernicus._quarter_mode_combo.cget("state")), "disabled")
            self.assertEqual(copernicus._auto_choice_var.get(), "Chosen at the next image check.")
            self.assertFalse(copernicus._auto_precise_check.winfo_manager())  # mosaics: not needed
            # Each priority in its window color: Newest orange, Data coverage green.
            from marblescape_theme import palette
            combo = copernicus._auto_priority_combo
            self.assertEqual(str(combo.cget("foreground")), palette(combo)["newest"])
            copernicus._auto_priority_var.set("Data coverage")
            copernicus._select_auto_priority()
            self.assertEqual(str(combo.cget("foreground")), palette(combo)["success"])
            copernicus._color_priority_list()
            listbox = f"{combo.tk.call('ttk::combobox::PopdownWindow', combo)}.f.l"
            combo.tk.call(listbox, "delete", 0, "end")
            combo.tk.call(listbox, "insert", "end", *combo.cget("values"))
            copernicus._color_priority_list()
            self.assertEqual([str(combo.tk.call(listbox, "itemcget", index, "-foreground")) for index in (0, 1)],
                             [palette(combo)["newest"], palette(combo)["success"]])
            copernicus._auto_priority_var.set("Newest")
            copernicus._select_auto_priority()
            chosen = copernicus.get_profile()
            self.assertEqual((chosen["auto_recommendation"], chosen["auto_priority"], chosen["date"],
                              chosen["date_mode"]), (True, "newest", "latest", "catalogue"))
            # The rule chooses the period: Quarter selection is greyed out, and
            # selectable again once the rule is off.
            self.assertEqual(str(copernicus._quarter_mode_combo.cget("state")), "disabled")
            copernicus._auto_var.set("No")
            copernicus._select_auto()
            self.assertEqual(str(copernicus._quarter_mode_combo.cget("state")), "readonly")
            copernicus._auto_var.set("Yes")
            copernicus._select_auto()
            self.assertEqual(str(copernicus._quarter_mode_combo.cget("state")), "disabled")
            # A single-acquisition layer: without OAuth credentials the window says so.
            copernicus._mission_var.set("Sentinel-2")
            copernicus._select_mission()
            root.update()
            # The rule stays on and Newest stays Newest (regular layers offer it too,
            # orange like Newest of mosaics); Gap fill and cloud limit greyed out.
            self.assertEqual(copernicus._auto_priority_var.get(), "Newest")
            self.assertEqual(tuple(copernicus._auto_priority_combo.cget("values")),
                             ("Fewest clouds", "Data coverage", "Newest"))
            self.assertEqual(copernicus.get_profile()["auto_priority"], "newest")
            self.assertEqual(str(copernicus._auto_priority_combo.cget("foreground")),
                             palette(copernicus._auto_priority_combo)["newest"])
            copernicus._auto_priority_var.set("Fewest clouds")
            copernicus._select_auto_priority()
            # Always use precise check shows for regular layers only, with its cost.
            self.assertTrue(copernicus._auto_precise_check.winfo_manager())
            self.assertEqual(copernicus._auto_precise_check.cget("text"),
                             "Always use precise check (consumes more credits)")
            self.assertFalse(copernicus.get_profile()["auto_precise"])
            copernicus._auto_precise_check.invoke()
            self.assertTrue(copernicus.get_profile()["auto_precise"])
            # Compare variants then opens with Precise check on (here greyed: no credentials).
            import marblescape_copernicus_advice_window as window_module
            with patch.object(window_module, "RecommendationWindow") as opened:
                source.recommend_button.invoke()
            self.assertTrue(opened.call_args.kwargs["precise_default"])
            copernicus._advice_window = None
            copernicus._auto_precise_check.invoke()
            self.assertEqual(str(copernicus._coverage_combo.cget("state")), "disabled")
            self.assertEqual(str(copernicus._cloud_scale.cget("state")), "disabled")
            self.assertEqual(copernicus.get_profile()["auto_priority"], "fewest_clouds")
            copernicus._auto_var.set("No")
            copernicus._select_auto()
            self.assertEqual(str(copernicus._coverage_combo.cget("state")), "readonly")
            self.assertFalse(copernicus._auto_priority_combo.winfo_manager())
            self.assertFalse(copernicus.get_profile()["auto_recommendation"])
            source.recommend_button.invoke()
            root.update()
            advice_window = copernicus._advice_window
            self.assertEqual(advice_window.state_var.get(),
                             "Enter the Copernicus OAuth client under Access first.")
            # Apply writes the row's settings into the Image tab as a draft.
            from marblescape_copernicus_advice import Variant
            copernicus.apply_recommendation(Variant(20, "fill_gaps", 14), 19.6, -155.5, 11)
            applied = copernicus.get_profile()
            self.assertEqual((applied["max_cloud_cover"], applied["coverage_mode"], applied["lookback_days"],
                              applied["latitude"], applied["longitude"], applied["map_zoom"]),
                             (20, "fill_gaps", 14, 19.6, -155.5, 11))
            advice_window.close()
            # Other sources have no latitude/longitude: no section, no recommendation.
            source.set_selection("goes_east", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            self.assertEqual(section.winfo_manager(), "")
            self.assertEqual(recommend.winfo_manager(), "")
            # Still images: Zoom is a dropdown of steps with a sharpness hint below.
            from marblescape_source_settings import still_zoom_lines
            zoom_combo = next(widget for widget in self.descendants(source.generic_view_frame)
                              if widget.winfo_class() == "TCombobox"
                              and "1 (Default)" in widget.cget("values"))
            self.assertEqual(list(zoom_combo.cget("values"))[:4], ["0.5", "0.75", "0.9", "1 (Default)"])
            self.assertEqual(str(zoom_combo.cget("state")), "readonly")
            zoom_combo.set("2")
            zoom_combo.event_generate("<<ComboboxSelected>>")
            root.update()
            self.assertEqual(context.variables["zoom"].get(), "2")
            image = source.source_image_size()
            self.assertIsNotNone(image)
            hint = source.generic_view_frame.nametowidget("still_zoom_hint")
            result, facts = hint.nametowidget("result"), hint.nametowidget("facts")
            width = int(context.variables["width"].get())
            height = int(context.variables["height"].get()) or round(
                width / app.parse_aspect_ratio(context.variables["aspect_ratio"].get()))
            expected = still_zoom_lines(image, (width, height), context.variables["fit_mode"].get(), zoom="2")
            self.assertEqual((str(result.cget("text")), str(facts.cget("text"))), expected[:2])
            self.assertEqual(hint.winfo_manager(), "grid")
            # A Himawari storm view counts its own enlargement on top of Zoom.
            from unittest.mock import PropertyMock
            with patch.object(type(source), "provider", new_callable=PropertyMock, return_value="himawari"), \
                    patch.object(source, "selected_area", return_value="jma_storm_TC2634"):
                context.variables["zoom"].set("1.5")
                root.update()
                self.assertTrue(str(facts.cget("text")).startswith("Storm view, 3000 km across at zoom 1 · "),
                                facts.cget("text"))
                self.assertTrue(str(result.cget("text")).startswith("Zoom 1.5: "), result.cget("text"))
            context.variables["zoom"].set("2")
            root.update()
            # EUMETSAT shows it while its Custom area centre (Geographic) is shown.
            source.set_selection("eumetsat", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            variables = context.variables
            variables["projection"].set("Geographic")
            variables["view_preset"].set("Custom area")
            root.update()
            self.assertEqual(section.winfo_manager(), "grid")
            search.coordinates_var.set("-33.8688, 151.2093")
            search.transfer_button.invoke()
            self.assertEqual((variables["custom_latitude"].get(), variables["custom_longitude"].get()),
                             ("-33.8688", "151.2093"))
            # Geographic allows the poles; Copernicus' map stops at about 85 degrees.
            self.assertEqual(search.latitude_limit, 90.0)
            # The EUMETSAT viewer cannot be opened at a place: no Preview, no Recommend.
            self.assertTrue(search.preview_button.instate(["disabled"]))
            self.assertEqual(recommend.winfo_manager(), "")
            other = next(name for name in app.available_projection_choices() if name != "Geographic")
            variables["projection"].set(other)
            root.update()
            self.assertEqual(section.winfo_manager(), "")

        self.run_dialog(scenario)

    def test_updates_column_keeps_the_applied_profile_and_image_tab_in_step(self):
        from tkinter import ttk

        def scenario(context):
            profiles = context.profiles
            for name in ("Applied", "Other"):
                profiles.add_current(name)
            applied, other = (item["id"] for item in profiles.get_library()["items"])
            profiles.tree.selection_set((applied,))
            profiles.apply_selected()
            self.assertEqual(context.errors, [])
            self.assertTrue(context.variables["check_for_source_updates"].get())
            header = context.source.frame.master.nametowidget("image_header")

            def displayed():
                # The applied profile's picture is on screen and no rotation profile
                # is shown (other tests may leave either behind).
                with patch.object(app, "shown_profile_row_id", return_value=applied), \
                     patch.dict(app.ROTATION_STATUS, {"active_profile_id": None}):
                    profiles._apply_runtime_status(profiles._status())
                short_id = header.nametowidget("short_id")
                return f"{header.cget('text')} {context.root.getvar(short_id.cget('textvariable'))}"

            # Switching the applied profile also switches the Image tab and the running settings.
            profiles.tree.selection_set((applied,))
            profiles.toggle_image_updates_selected()
            self.assertEqual(context.errors, [])
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertIs(saved["source"]["check_for_updates"], False)
            self.assertFalse(context.variables["check_for_source_updates"].get())
            stored = {item["id"]: item["settings"]["source"]["check_for_updates"]
                      for item in app.read_profile_library_file()["items"]}
            self.assertEqual(stored, {applied: False, other: True})
            self.assertEqual(displayed(), f"Applied ({applied[:8]})")
            tab = next(item for item in context.notebook.tabs()
                       if context.notebook.tab(item, "text") == "Profiles")
            context.notebook.select(tab)
            context.root.event_generate("<<SettingsDraftChanged>>")
            context.root.update()
            self.assertEqual(self.footer_notice(context), "✓ Saved")
            # Another profile changes only itself.
            profiles.tree.selection_set((other,))
            profiles.toggle_image_updates_selected()
            saved = tomllib.loads(context.config.read_text(encoding="utf-8"))
            self.assertIs(saved["source"]["check_for_updates"], False)
            self.assertFalse(context.variables["check_for_source_updates"].get())
            stored = {item["id"]: item["settings"]["source"]["check_for_updates"]
                      for item in app.read_profile_library_file()["items"]}
            self.assertEqual(stored, {applied: False, other: False})

        self.run_dialog(scenario)

    def test_a_thin_line_separates_content_and_footer(self):
        from tkinter import ttk

        def scenario(context):
            footer = next(widget for widget in self.descendants(context.root)
                          if isinstance(widget, ttk.Button) and widget.cget("text") == "OK").master
            container = footer.master
            divider = next(widget for widget in container.winfo_children()
                           if isinstance(widget, ttk.Separator) and widget.grid_info()
                           and int(widget.grid_info()["row"]) == int(footer.grid_info()["row"]) - 1)
            self.assertEqual(str(divider.cget("orient")), "horizontal")
            self.assertEqual(divider.grid_info()["sticky"], "ew")
            self.assertLess(int(context.notebook.grid_info()["row"]), int(divider.grid_info()["row"]))

        self.run_dialog(scenario)

    def test_the_save_check_is_built_again_only_when_its_inputs_change(self):
        def scenario(context):
            def check():
                context.root.event_generate("<<SettingsDraftChanged>>")
                context.root.update()

            # Opening writes the configuration once (for example the tab); the next
            # check builds anew for that, then nothing changes.
            check()
            check()
            with patch.object(app, "serialize_library", wraps=app.serialize_library) as built:
                # Nothing changed: the costly build of what Save would write is reused.
                for _ in range(5):
                    check()
                self.assertEqual(built.call_count, 0)
                # A changed field builds it again, and Save reports the draft.
                variants = context.variables["profile_cache_variants"]
                original = variants.get()
                variants.set(original + 1)
                check()
                self.assertEqual(built.call_count, 1)
                self.assertEqual(self.footer_notice(context), "Unsaved changes")
                variants.set(original)
                check()
                self.assertEqual(self.footer_notice(context), "")
                # A changed profile list counts as well.
                calls = built.call_count
                context.profiles.add_current("Save check key")
                check()
                self.assertGreater(built.call_count, calls)
                # Without any change it is built again after the safety interval.
                calls = built.call_count
                with patch.object(app.time, "monotonic",
                                  return_value=app.time.monotonic() + app.SAVE_CHECK_REFRESH_SECONDS + 1):
                    check()
                self.assertEqual(built.call_count, calls + 1)

        self.run_dialog(scenario)

    def test_check_new_image_sits_left_of_force_loading_and_checks_without_forcing(self):
        from tkinter import ttk

        def scenario(context):
            image_tab = context.source.frame.master
            buttons = {str(widget.cget("text")): widget for widget in self.descendants(image_tab)
                       if isinstance(widget, ttk.Button)
                       and widget.cget("text") in ("Check new image", "Force loading new image")}
            check, force = buttons["Check new image"], buttons["Force loading new image"]
            self.assertIs(check.master, force.master)
            self.assertEqual([int(button.grid_info()["column"]) for button in (check, force)], [0, 1])
            # While MarbleScape stands by, Check new image asks for the regular check now.
            item = next(entry for entry in context.icon.menu.items
                        if getattr(entry, "text", "") == "Force loading new image")
            snapshot = inspect.getclosurevars(item._enabled).nonlocals["get_tray_status_snapshot"]
            inspect.getclosurevars(snapshot).nonlocals["tray_status"]["state"] = "waiting"
            app.CHECK_NOW_EVENT.clear()
            # The status refresh would enable it; the click itself checks the state again.
            check.state(["!disabled"])
            try:
                check.invoke()
                self.assertTrue(app.CHECK_NOW_EVENT.is_set())
                self.assertFalse(app.FORCE_UPDATE_EVENT.is_set())
                self.assertIn("disabled", check.state())
            finally:
                app.CHECK_NOW_EVENT.clear()

        self.run_dialog(scenario)

    def test_a_long_footer_note_never_moves_the_buttons(self):
        from tkinter import ttk

        def scenario(context):
            root = context.root
            root.tk.call("wm", "deiconify", root._w)
            root.tk.call("wm", "geometry", root._w, f"{root.minsize()[0]}x760")
            for _ in range(3):
                root.update()
            tabs = {context.notebook.tab(tab_id, "text"): tab_id for tab_id in context.notebook.tabs()}
            footer = next(widget for widget in self.descendants(context.root)
                          if isinstance(widget, ttk.Button) and widget.cget("text") == "OK").master
            notice = footer.nametowidget("save_notice")
            activity = next(widget for widget in footer.winfo_children()
                            if isinstance(widget, ttk.Label) and str(widget.cget("textvariable"))
                            and widget.grid_info() and int(widget.grid_info()["row"]) == 1)
            long_detail = "NOAA GOES, Himawari, CIRA SLIDER, NASA Worldview and the Copernicus mosaics"

            def buttons():
                context.root.update()
                return {widget.cget("text"): (widget.winfo_x(), widget.winfo_width())
                        for widget in footer.winfo_children()
                        if isinstance(widget, ttk.Button) and widget.winfo_manager()
                        and widget.cget("text") in ("Save", "Apply Image", "OK", "Close")}

            for title in ("General", "Image"):
                with self.subTest(tab=title):
                    context.notebook.select(tabs[title])
                    app.record_image_outcome("current")
                    deadline = time.monotonic() + 8
                    while "up to date" not in str(notice.cget("text")) and time.monotonic() < deadline:
                        context.root.update()
                        time.sleep(0.05)
                    before = buttons()
                    app.record_image_outcome("catalogue_incomplete", detail=long_detail)
                    deadline = time.monotonic() + 8
                    while "incomplete" not in str(notice.cget("text")) and time.monotonic() < deadline:
                        context.root.update()
                        time.sleep(0.05)
                    self.assertIn("Catalogue incomplete: NOAA GOES", str(notice.cget("text")))
                    # The buttons stay where they were; the note ends below Close and
                    # never covers the activity text on its left.
                    self.assertEqual(buttons(), before)
                    close_x, close_width = before["Close"]
                    self.assertEqual(notice.winfo_x() + notice.winfo_width(), close_x + close_width)
                    self.assertGreaterEqual(notice.winfo_x(), activity.winfo_x() + activity.winfo_reqwidth())
                    self.assertFalse(str(notice.cget("text")).endswith("…"))
                    # Too long even for that room: cut with "…", still clear of the activity text.
                    app.record_image_outcome("catalogue_incomplete", detail=long_detail * 3)
                    deadline = time.monotonic() + 8
                    while not str(notice.cget("text")).endswith("…") and time.monotonic() < deadline:
                        context.root.update()
                        time.sleep(0.05)
                    self.assertTrue(str(notice.cget("text")).startswith("? Catalogue incomplete: NOAA GOES"))
                    self.assertTrue(str(notice.cget("text")).endswith("…"))
                    self.assertEqual(buttons(), before)
                    self.assertGreaterEqual(notice.winfo_x(), activity.winfo_x() + activity.winfo_reqwidth())

        with patch.dict(app.IMAGE_OUTCOME, {"serial": 0, "kind": None, "profile": None, "time": None}):
            self.run_dialog(scenario)

    def test_footer_layout_and_save_notices(self):
        from tkinter import ttk
        import marblescape_theme as theme

        def scenario(context):
            buttons = {widget.cget("text"): widget for widget in self.descendants(context.root)
                       if isinstance(widget, ttk.Button)
                       and widget.cget("text") in ("Save", "OK", "Close", "Cancel download")}
            footer = buttons["OK"].master
            # Left, top to bottom: next check, activity, download text beside Cancel download.
            next_check = next(widget for widget in self.descendants(footer)
                              if isinstance(widget, ttk.Label) and widget.cget("text") == "Next check:").master
            # The activity is the footer's only status label of its own in the first column.
            activity = next(widget for widget in footer.winfo_children()
                            if isinstance(widget, ttk.Label) and str(widget.cget("textvariable"))
                            and widget.grid_info() and int(widget.grid_info()["column"]) == 0)
            download_row = buttons["Cancel download"].master
            self.assertEqual([int(widget.grid_info()["row"]) for widget in (next_check, activity, download_row)],
                             [0, 1, 2])
            self.assertEqual(int(buttons["OK"].grid_info()["row"]), 0)
            # Cancel download is rightmost, after the progress bar and the download status.
            bar = next(widget for widget in download_row.winfo_children()
                       if isinstance(widget, ttk.Progressbar))
            bar.grid()  # Hidden while nothing downloads.
            columns = {widget.winfo_class(): int(widget.grid_info()["column"])
                       for widget in download_row.winfo_children() if widget.grid_info()}
            bar.grid_remove()
            self.assertLess(columns["TProgressbar"], columns["TButton"])

            tabs = {context.notebook.tab(tab_id, "text"): tab_id for tab_id in context.notebook.tabs()}

            def show(title):
                context.notebook.select(tabs[title])
                context.root.event_generate("<<SettingsDraftChanged>>")
                context.root.update()
                return self.footer_notice(context)

            # Next check stays visible on every tab, also Profiles.
            for title in ("Profiles", "Image", "General"):
                show(title)
                self.assertEqual(next_check.winfo_manager(), "grid", title)
            self.assertEqual(show("Profiles"), app.PROFILES_AUTOSAVE_HINT)
            # The notice sits below the buttons, right-aligned, where long notes fit.
            # Placed at the right edge, so it never widens the button columns (its
            # pixels in a shown window: test_a_long_footer_note_never_moves_the_buttons).
            placed = footer.nametowidget("save_notice").place_info()
            self.assertEqual((placed["relx"], placed["anchor"]), ("1", "ne"))
            self.assertEqual(show("Image"), "")
            self.assertEqual(show("General"), "")
            # A draft waits for Save in the warning color; Save confirms it in green.
            context.variables["profile_cache_variants"].set(3)
            self.assertEqual(show("History & Storage"), "Unsaved changes")
            notice = next(widget for widget in footer.winfo_children()
                          if isinstance(widget, ttk.Label) and widget.cget("text") == "Unsaved changes")
            self.assertEqual(str(notice.cget("foreground")), theme.palette(notice)["warning"])
            self.apply(context, tab="History & Storage")
            self.assertEqual(show("History & Storage"), "✓ Saved")
            self.assertEqual(str(notice.cget("foreground")), theme.palette(notice)["success"])
            self.assertEqual(show("General"), "✓ Saved")
            context.variables["profile_cache_variants"].set(4)
            self.assertEqual(show("General"), "Unsaved changes")
            context.variables["profile_cache_variants"].set(3)
            self.assertEqual(show("General"), "")
            # A saved profile-list change shows the green mark, then the hint again.
            show("Profiles")
            context.profiles.add_current("Footer check")
            context.root.update()
            self.assertEqual(self.footer_notice(context), "✓ Saved")
            with patch.object(app.time, "monotonic", return_value=app.time.monotonic() + 10):
                self.assertEqual(show("Profiles"), app.PROFILES_AUTOSAVE_HINT)

        self.run_dialog(scenario)

    def test_general_actions_show_log_restart_and_exit(self):
        from tkinter import ttk

        def scenario(context):
            context.root.update()
            context.root.event_generate("<<SettingsDraftChanged>>")
            actions = context.root.nametowidget(next(
                str(widget) for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Actions"))
            # Actions closes the General tab.
            sections = [widget for widget in actions.master.winfo_children() if isinstance(widget, ttk.LabelFrame)]
            self.assertIs(max(sections, key=lambda widget: int(widget.grid_info()["row"])), actions)
            buttons = {str(widget.cget("text")): widget for widget in self.descendants(actions)
                       if isinstance(widget, ttk.Button)}
            self.assertEqual(list(buttons), ["Show log", "Restart", "Exit"])
            # Side by side, left-aligned and equally wide.
            context.notebook.select(next(tab_id for tab_id in context.notebook.tabs()
                                         if context.notebook.tab(tab_id, "text") == "General"))
            context.root.update()
            row = buttons["Show log"].master
            self.assertEqual(row.grid_info()["sticky"], "w")
            self.assertEqual(buttons["Show log"].winfo_x(), 0)
            self.assertEqual(len({button.winfo_width() for button in buttons.values()}), 1)
            self.assertEqual(len({button.winfo_y() for button in buttons.values()}), 1)
            self.assertLess(buttons["Show log"].winfo_x(), buttons["Restart"].winfo_x())
            self.assertLess(buttons["Restart"].winfo_x(), buttons["Exit"].winfo_x())
            # Natural widths, evenly spaced: the widest text sets them, not the section.
            self.assertEqual(buttons["Restart"].winfo_x() - buttons["Show log"].winfo_x(),
                             buttons["Exit"].winfo_x() - buttons["Restart"].winfo_x())
            self.assertLess(buttons["Exit"].winfo_width(), 2 * buttons["Exit"].winfo_reqwidth())
            # Show log: a note while there is no log yet, else the file opens.
            path = app.log_file_path()
            self.assertTrue(str(path).startswith(str(context.directory)))
            with patch("tkinter.messagebox.showinfo") as info, \
                    patch.object(app.os, "startfile", create=True) as start:
                buttons["Show log"].invoke()
                info.assert_called_once()
                start.assert_not_called()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[2026-10-08 12:00:00] started\n", encoding="utf-8")
                buttons["Show log"].invoke()
                start.assert_called_once_with(str(path))

            stopping = (patch.object(app.NETWORK_ACTIVITY, "cancel"),
                        patch.object(app.DOWNLOAD_PROGRESS, "request_cancel"))
            original = context.variables["profile_cache_variants"].get()
            # With unsaved changes Exit and Restart ask first; Cancel keeps MarbleScape running.
            context.variables["profile_cache_variants"].set(original + 1)
            with patch("tkinter.messagebox.askokcancel", return_value=False) as ask, \
                    patch.object(app.subprocess, "Popen") as popen:
                buttons["Exit"].invoke()
                buttons["Restart"].invoke()
            self.assertEqual(ask.call_count, 2)
            popen.assert_not_called()
            self.assertFalse(app.APPLICATION_STOP_EVENT.is_set())
            self.assertFalse(context.icon.stopped)
            # Confirmed, Restart starts the new MarbleScape with this one's process number, then stops.
            with patch("tkinter.messagebox.askokcancel", return_value=True), \
                    patch.object(app.subprocess, "Popen") as popen, stopping[0], stopping[1]:
                buttons["Restart"].invoke()
            environment = popen.call_args.kwargs["env"]
            self.assertEqual((environment["MARBLESCAPE_RESTART_WAIT"], environment["MARBLESCAPE_RESTART_FROM_PID"]),
                             ("1", str(os.getpid())))
            self.assertTrue(app.APPLICATION_STOP_EVENT.is_set())
            self.assertTrue(context.icon.stopped)
            app.APPLICATION_STOP_EVENT.clear()
            context.icon.stopped = False
            # Without unsaved changes Exit stops at once.
            context.variables["profile_cache_variants"].set(original)
            with patch("tkinter.messagebox.askokcancel") as ask, stopping[0], stopping[1]:
                buttons["Exit"].invoke()
            ask.assert_not_called()
            self.assertTrue(app.APPLICATION_STOP_EVENT.is_set())
            self.assertTrue(context.icon.stopped)
            app.APPLICATION_STOP_EVENT.clear()

        self.run_dialog(scenario)

    def test_update_check_interval_hint_and_month_note(self):
        from tkinter import ttk
        import marblescape_theme as theme

        def scenario(context):
            label = next(widget for widget in self.descendants(context.root)
                         if isinstance(widget, ttk.Label) and widget.cget("text") == "Update check interval")
            frame = label.master
            note = frame.nametowidget("interval_note")
            # The hint and the month note share the reserved line below the dropdowns.
            self.assertEqual(int(note.grid_info()["row"]), 1)
            reserved = int(frame.grid_rowconfigure(1, "minsize"))
            self.assertGreater(reserved, 0)
            for value, unit_name, text, color in (
                    ("2", "months", app.MONTH_INTERVAL_NOTE, ""),
                    ("1", "minutes", app.SHORT_UPDATE_INTERVAL_HINT, theme.palette(note)["warning"]),
                    ("15", "minutes", "", "")):
                with self.subTest(value=value, unit=unit_name):
                    context.variables["update_interval_unit"].set(unit_name)
                    context.variables["update_interval_value"].set(value)
                    context.root.update()
                    self.assertEqual(str(note.cget("text")), text)
                    self.assertEqual(str(note.cget("foreground")), color)
                    self.assertEqual(int(frame.grid_rowconfigure(1, "minsize")), reserved)
            self.assertEqual(context.errors, [])

        self.run_dialog(scenario)

    def test_footer_shows_the_newest_image_update_or_save_note(self):
        from tkinter import ttk
        import time as clock
        import marblescape_theme as theme

        def scenario(context):
            tabs = {context.notebook.tab(tab_id, "text"): tab_id for tab_id in context.notebook.tabs()}

            def show(title, expected_start=None):
                context.notebook.select(tabs[title])
                deadline = clock.monotonic() + 3
                while True:
                    context.root.event_generate("<<SettingsDraftChanged>>")
                    context.root.update()
                    text = self.footer_notice(context)
                    if expected_start is None or text.startswith(expected_start) or clock.monotonic() > deadline:
                        return text
                    clock.sleep(0.05)

            self.assertEqual(show("General"), "")
            app.record_image_outcome("NETWORK")
            self.assertTrue(show("General", "! Network issue").startswith("! Network issue · "))
            notice = next(widget for widget in self.descendants(context.root)
                          if isinstance(widget, ttk.Label) and str(widget).endswith("save_notice"))
            self.assertEqual(str(notice.cget("foreground")), theme.palette(notice)["warning"])
            # Every tab shows it, also Image and Profiles (instead of its hint).
            self.assertTrue(show("Image").startswith("! Network issue"))
            self.assertTrue(show("Profiles").startswith("! Network issue"))
            # A newer save note replaces it; a newer update note replaces that.
            context.variables["profile_cache_variants"].set(3)
            self.assertEqual(show("History & Storage"), "Unsaved changes")
            self.apply(context, tab="History & Storage")
            self.assertEqual(show("History & Storage"), "✓ Saved")
            app.record_image_outcome("downloaded")
            self.assertTrue(show("History & Storage", "✓ Image downloaded").startswith("✓ Image downloaded"))
            self.assertEqual(str(notice.cget("foreground")), theme.palette(notice)["success"])
            # Unsaved changes always show.
            context.variables["profile_cache_variants"].set(4)
            self.assertEqual(show("History & Storage"), "Unsaved changes")
            self.assertEqual(context.errors, [])

        self.run_dialog(scenario)

    def test_general_appearance_is_saved_and_applied_to_the_open_window(self):
        from tkinter import ttk
        import marblescape_theme as theme

        def scenario(context):
            headings = {str(widget.cget("text")) for widget in self.descendants(context.root)
                        if isinstance(widget, ttk.LabelFrame)}
            self.assertIn("Appearance", headings)
            self.assertEqual(context.variables["appearance"].get(), "System (recommended)")
            context.variables["appearance"].set("Dark")
            saved = self.apply(context, tab="General")
            self.assertEqual(saved["display"]["appearance"], "dark")
            # The open window switches at once, before the runtime reload.
            expected = "dark" if theme.sv_ttk is not None else "light"
            self.assertEqual(theme.current_mode(context.root), expected)
            if theme.sv_ttk is not None:
                self.assertEqual(ttk.Style(context.root).theme_use(), "sun-valley-dark")
            context.variables["appearance"].set("Light")
            saved = self.apply(context)
            self.assertEqual(saved["display"]["appearance"], "light")
            self.assertEqual(theme.current_mode(context.root), "light")

        self.run_dialog(scenario)

    def test_history_column_follows_storage_history_drafts(self):
        import marblescape_snapshot as system
        first, second = "a" * 32, "b" * 32
        library = {"items": [
            {"id": first, "name": "Earth", "settings": {}},
            {"id": second, "name": "Sun", "settings": {}},
        ]}

        def scenario(context):
            tree = context.profiles.tree
            policies = {first: {"enabled": True}, second: {"enabled": False}}
            context.variables["profile_history_policies"].set(json.dumps(policies))
            self.assertEqual((tree.set(first, "history"), tree.set(second, "history")), ("☑", "☐"))
            policies = {first: {"enabled": False}, second: {"enabled": True}}
            context.variables["profile_history_policies"].set(json.dumps(policies))
            self.assertEqual((tree.set(first, "history"), tree.set(second, "history")), ("☐", "☑"))
            # The Latest snapshot row follows History (no profile).
            context.variables["history_enabled"].set(True)
            self.assertEqual(tree.set(system.SYSTEM_ID, "history"), "☑")
            context.variables["history_enabled"].set(False)
            self.assertEqual(tree.set(system.SYSTEM_ID, "history"), "☐")

        self.run_dialog(scenario, initial_library=library)

    def test_tray_rotation_switch_keeps_the_saved_list_and_settings_follow_it(self):
        first, second = "a" * 32, "b" * 32
        library = {"items": [
            {"id": first, "name": "Earth", "settings": {}},
            {"id": second, "name": "Sun", "settings": {}},
        ], "rotation": {"enabled": True, "order": [first, second]}}

        def scenario(context):
            profiles = context.profiles
            # Unchecking a profile is saved at once ...
            profiles.tree.selection_set((first,))
            profiles.toggle_rotation_selected()
            self.assertEqual(app.read_profile_library_file()["rotation"]["order"], [second])
            # ... but the update loop has not loaded it yet (as during a download).
            self.assertEqual(app.IMAGE_PROFILE_LIBRARY["rotation"]["order"], [first, second])
            toggle = next(item for item in context.icon.menu.items
                          if getattr(item, "text", None) == "Profile rotation")
            toggle._action(context.icon, None)
            saved = app.read_profile_library_file()["rotation"]
            self.assertEqual((saved["enabled"], saved["order"]), (False, [second]))
            # Settings shows the switch, and its next save keeps it.
            profiles._apply_runtime_status(profiles._status())
            self.assertFalse(profiles.enabled_var.get())
            self.assertEqual(context.errors, [])
            profiles.tree.selection_set((first,))
            profiles.toggle_rotation_selected()
            saved = app.read_profile_library_file()["rotation"]
            self.assertFalse(saved["enabled"])
            self.assertEqual(set(saved["order"]), {first, second})
            # Switching it on again in the tray is followed too.
            toggle._action(context.icon, None)
            profiles._apply_runtime_status(profiles._status())
            self.assertTrue(profiles.enabled_var.get())
            self.assertTrue(app.read_profile_library_file()["rotation"]["enabled"])
            app.CONFIGURATION_RELOAD_EVENT.clear()

        self.run_dialog(scenario, initial_library=library)

    def test_apply_during_a_download_shows_the_cached_picture_at_once(self):
        import threading
        first, second = "a" * 32, "b" * 32
        settings = app.default_import_settings()
        library = {"items": [
            {"id": first, "name": "Earth", "settings": deepcopy(settings)},
            {"id": second, "name": "Sun", "settings": deepcopy(settings)},
        ]}

        def scenario(context):
            profiles = context.profiles
            calls, started = [], threading.Event()

            def instant(identifier, settings):
                calls.append((identifier, settings))
                started.set()

            with patch.object(app, "show_cached_profile_now", side_effect=instant):
                # Idle: the update loop applies the profile itself.
                profiles.tree.selection_set((first,))
                profiles.apply_selected()
                self.assertFalse(started.wait(0.2))
                app.DOWNLOAD_PROGRESS.begin(1)
                try:
                    profiles.tree.selection_set((second,))
                    profiles.apply_selected()
                    self.assertTrue(started.wait(5))
                finally:
                    app.DOWNLOAD_PROGRESS.finish(False)
            self.assertEqual(calls[0][0], second)
            self.assertEqual(context.errors, [])
            app.CONFIGURATION_RELOAD_EVENT.clear()

        self.run_dialog(scenario, initial_library=library)

    def test_active_follows_the_picture_on_screen_not_the_image_form(self):
        import marblescape_snapshot as system
        first = "a" * 32
        library = {"items": [{"id": first, "name": "Earth", "settings": app.default_import_settings()}]}

        def scenario(context):
            app.save_latest_snapshot({
                "snapshot_id": "3" * 32, "profile_id": "4" * 32,
                "generated_at_utc": "2026-09-27T10:00:00Z",
                "profile_settings": app.portable_settings(context.profiles._capture_settings(),
                                                          app.normalize_image_settings_snapshot)})
            tree = context.profiles.tree
            shown = context.directory / "shown.png"
            # The applied profile's picture, then one from its modified settings (Apply Image).
            for identity, active, other in (((first, "Earth"), first, system.SYSTEM_ID),
                                            ((None, None), system.SYSTEM_ID, first)):
                with patch.object(app, "get_current_image_path", return_value=shown), \
                     patch.object(app, "_history_profile_identity", return_value=identity):
                    context.profiles._apply_runtime_status(context.profiles._status())
                self.assertEqual(tree.set(active, "active"), "ACTIVE", identity)
                self.assertEqual(tree.set(other, "active"), "", identity)

        self.run_dialog(scenario, initial_library=library)

    def test_latest_snapshot_row_shows_its_cached_image(self):
        import io
        from PIL import Image
        import marblescape_snapshot as system

        def scenario(context):
            tree = context.profiles.tree
            app.save_latest_snapshot({
                "snapshot_id": "3" * 32, "profile_id": "4" * 32,
                "generated_at_utc": "2026-09-27T10:00:00Z",
                "profile_settings": app.portable_settings(context.profiles._capture_settings(),
                                                          app.normalize_image_settings_snapshot)})
            context.profiles._apply_runtime_status(context.profiles._status())
            self.assertEqual(tree.set(system.SYSTEM_ID, "cache_status"), "Not cached")
            png = io.BytesIO()
            Image.new("RGB", (4, 3)).save(png, "PNG")
            app.get_profile_cache().install(system.CACHE_ID, {"test": 1}, ("frame",), png.getvalue(), (4, 3),
                                            source_time="2026-09-27T10:00:00Z")
            context.profiles._apply_runtime_status(context.profiles._status())
            self.assertTrue(tree.set(system.SYSTEM_ID, "cache_status").startswith("Cached · 4 × 3"))
            # A picture without a profile is shown: the snapshot row is the active one.
            with patch.object(app, "get_current_image_path", return_value=context.directory / "shown.png"), \
                 patch.object(app, "_history_profile_identity", return_value=(None, None)):
                context.profiles._apply_runtime_status(context.profiles._status())
            self.assertEqual(tree.set(system.SYSTEM_ID, "active"), "ACTIVE")
            self.assertIn("active", tree.item(system.SYSTEM_ID, "tags"))
            # Its history folder is "_no profile".
            tree.selection_set((system.SYSTEM_ID,))
            context.profiles._selection_changed()
            menu = context.profiles._cell_menu
            self.assertEqual(("normal" if context.profiles.menu_entry_enabled("Open profile history folder") else "disabled"), "normal")
            with patch.object(app.os, "startfile", create=True) as startfile:
                context.profiles.open_history_folder()
            self.assertEqual(Path(startfile.call_args.args[0]).name, app.NO_PROFILE_HISTORY_FOLDER)
            # A download without a profile shows its progress there.
            app.DOWNLOAD_PROGRESS.begin(4)
            try:
                context.profiles._apply_runtime_status(context.profiles._status())
                self.assertEqual(tree.set(system.SYSTEM_ID, "active"), "0/4")
            finally:
                app.DOWNLOAD_PROGRESS.finish(False)

        self.run_dialog(scenario)

    def test_history_checkbox_saves_only_that_switch_at_once(self):
        import marblescape_snapshot as system
        first, second = "a" * 32, "b" * 32
        library = {"items": [
            {"id": first, "name": "Earth", "settings": {}},
            {"id": second, "name": "Sun", "settings": {}},
        ]}

        def saved_history():
            history = tomllib.loads(app.ACTIVE_CONFIG_PATH.read_text(encoding="utf-8"))["history"]
            return json.loads(history.get("profile_policies", "{}")), history["enabled"]

        def scenario(context):
            profiles, tree = context.profiles, context.profiles.tree
            before_policies, before_enabled = saved_history()
            # An unsaved retention edit on History & Storage must stay a draft.
            draft = json.loads(context.variables["profile_history_policies"].get())
            draft[second] = app._normalize_history_policy({"enabled": False, "max_files": 7})
            context.variables["profile_history_policies"].set(json.dumps(draft, sort_keys=True))
            self.assertEqual(tree.set(first, "history"), "☐")
            tree.selection_set(first)
            with patch.object(tree, "identify_region", return_value="cell"), \
                 patch.object(tree, "identify_row", return_value=first), \
                 patch.object(tree, "identify_column",
                              return_value=f"#{profiles.get_visible_columns().index('history') + 1}"):
                self.assertEqual(profiles._toggle_rotation_clicked(SimpleNamespace(x=5, y=5)), "break")
            self.assertEqual(context.errors, [])
            self.assertEqual(tree.set(first, "history"), "☑")
            self.assertEqual(profiles.last_saved_change, "History on for 1 profile(s).")
            policies, enabled = saved_history()
            self.assertTrue(policies[first]["enabled"])
            self.assertNotIn(second, set(policies) - set(before_policies))
            self.assertEqual(enabled, before_enabled)
            # The History & Storage draft shows the switch and keeps its own edit.
            draft = json.loads(context.variables["profile_history_policies"].get())
            self.assertTrue(draft[first]["enabled"])
            self.assertEqual(draft[second]["max_files"], 7)
            # The Latest snapshot row switches History (no profile).
            tree.selection_set(system.SYSTEM_ID)
            profiles.toggle_history_selected()
            self.assertEqual(saved_history()[1], not before_enabled)
            self.assertEqual(context.variables["history_enabled"].get(), (not before_enabled))

        self.run_dialog(scenario, initial_library=library)

    def test_profile_history_dropdown_follows_draft_profiles_on_tab_switch(self):
        from tkinter import ttk

        first, second, third = "a" * 32, "b" * 32, "c" * 32
        library = {"items": [
            {"id": first, "name": "Earth", "settings": {}},
            {"id": second, "name": "Sun", "settings": {}},
        ]}

        def scenario(context):
            section = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "History (profile)"
            )
            combo = next(widget for widget in self.descendants(section)
                         if isinstance(widget, ttk.Combobox)
                         and first[:8] in " ".join(map(str, widget.cget("values"))))
            notebook = next(widget for widget in self.descendants(context.root)
                            if isinstance(widget, ttk.Notebook))
            history_tab = next(tab for tab in notebook.tabs()
                               if notebook.tab(tab, "text") == "History & Storage")
            other_tab = next(tab for tab in notebook.tabs() if tab != history_tab)
            notebook.select(other_tab)
            context.root.update()
            items = [item for item in context.profiles._items if item["id"] != second]
            # Appended last in the table, but listed alphabetically in History.
            items.append({"id": third, "name": "aurora", "settings": deepcopy(items[0]["settings"])})
            context.profiles._commit(items, third)
            notebook.select(history_tab)
            context.root.update()
            self.assertEqual(tuple(map(str, combo.cget("values"))),
                             (f"aurora [{third[:8]}]", f"Earth [{first[:8]}]"))
            context.variables["profile_history_policies"].set("{}")
            buttons = {str(widget.cget("text")): widget
                       for widget in self.descendants(section)
                       if isinstance(widget, ttk.Button)}
            buttons["Apply to all profiles"].invoke()
            policies = json.loads(context.variables["profile_history_policies"].get())
            self.assertEqual(set(policies), {first, third})

        self.run_dialog(scenario, initial_library=library)

    def test_source_dropdowns_share_one_width_and_label_column(self):
        from tkinter import ttk
        from marblescape_source_layout import (
            SOURCE_COMBO_WIDTH,
            SOURCE_LABEL_COLUMN_MINSIZE,
            SOURCE_LABEL_COLUMN_WIDEST,
        )

        def scenario(context):
            combos = [
                widget for widget in self.descendants(context.source.frame)
                if isinstance(widget, ttk.Combobox)
            ]
            self.assertGreater(len(combos), 10)
            self.assertEqual(
                {int(widget.cget("width")) for widget in combos},
                {SOURCE_COMBO_WIDTH},
            )
            frames = (
                context.source.frame,
                context.source.eumetsat_settings.frame,
                context.source.eumetsat_view_frame,
                context.source.generic_view_frame,
                context.source.copernicus_settings.frame,
            )
            # One shared label column, wide enough for the widest label in this theme.
            minsizes = {int(frame.grid_columnconfigure(0)["minsize"]) for frame in frames}
            self.assertEqual(len(minsizes), 1)
            widest = ttk.Checkbutton(context.source.frame, text=SOURCE_LABEL_COLUMN_WIDEST)
            self.assertGreaterEqual(minsizes.pop(), max(SOURCE_LABEL_COLUMN_MINSIZE,
                                                        widest.winfo_reqwidth()))
            widest.destroy()

        self.run_dialog(scenario)

    def test_eumetsat_dropdowns_have_equal_rendered_widths(self):
        from tkinter import ttk

        def scenario(context):
            context.source._provider_var.set("EUMETSAT")
            context.source._select_provider()
            context.notebook.select(2)  # Image
            context.root.tk.call("wm", "attributes", context.root._w, "-alpha", 0)
            context.root.tk.call("wm", "deiconify", context.root._w)
            context.root.update()
            combos = [context.source._provider_combo] + [
                widget for widget in self.descendants(context.source.eumetsat_settings.frame)
                if isinstance(widget, ttk.Combobox)
            ]
            widths = [widget.winfo_width() for widget in combos]
            lefts = [widget.winfo_rootx() for widget in combos]
            self.assertGreater(min(widths), 100)
            self.assertLessEqual(max(widths) - min(widths), 2)
            self.assertLessEqual(max(lefts) - min(lefts), 2)

        self.run_dialog(scenario)

    def test_copernicus_dropdowns_keep_equal_rendered_widths_after_source_switch(self):
        from tkinter import ttk

        def scenario(context):
            context.source.set_selection("eumetsat", app.DEFAULT_SOURCE_PROFILES)
            context.source.set_selection("copernicus", app.DEFAULT_SOURCE_PROFILES)
            context.notebook.select(2)  # Image
            context.root.tk.call("wm", "attributes", context.root._w, "-alpha", 0)
            context.root.tk.call("wm", "deiconify", context.root._w)
            context.root.update()
            combos = [context.source._provider_combo] + [
                widget for widget in self.descendants(context.source.copernicus_settings.frame)
                if isinstance(widget, ttk.Combobox) and widget.winfo_ismapped()
            ]
            widths = [widget.winfo_width() for widget in combos]
            lefts = [widget.winfo_rootx() for widget in combos]
            geometry = [(widget.get(), widget.winfo_width(), widget.winfo_rootx())
                        for widget in combos]
            self.assertGreater(len(combos), 7)
            self.assertGreater(min(widths), 100)
            self.assertLessEqual(max(widths) - min(widths), 2, geometry)
            self.assertLessEqual(max(lefts) - min(lefts), 2, geometry)

        self.run_dialog(scenario)

    def test_download_tab_persists_speed_progress_and_bar_preferences(self):
        from tkinter import ttk

        def scenario(context):
            tabs = [context.notebook.tab(tab, "text") for tab in context.notebook.tabs()]
            self.assertEqual(
                tabs,
                ["General", "Downloads & Updates", "Image", "Profiles",
                 "History & Storage", "Backup", "Sources", "Access", "About", "Info"],
            )
            source_urls = {
                str(widget.cget("text")) for widget in self.descendants(context.root)
                if isinstance(widget, tk.Label)
                and str(widget.cget("text")).startswith("https://")
            }
            self.assertEqual(source_urls, {
                "https://view.eumetsat.int/productviewer",
                "https://www.star.nesdis.noaa.gov/goes/index.php",
                "https://www.star.nesdis.noaa.gov/goes/SUVI.php?sat=G19",
                "https://himawari8.nict.go.jp/",
                "https://ds.data.jma.go.jp/mscweb/data/himawari/index.html",
                "https://slider.cira.colostate.edu/",
                "https://worldview.earthdata.nasa.gov/",
                "https://browser.dataspace.copernicus.eu/",
            })
            # Labels, and the formatted Info sections (read-only Text widgets).
            info_text = "\n".join(
                str(widget.cget("text")) if isinstance(widget, ttk.Label) else widget.get("1.0", "end-1c")
                for widget in self.descendants(context.root)
                if isinstance(widget, ttk.Label) or widget.winfo_class() == "Text"
            )
            self.assertIn("one available size above the required output", info_text)
            self.assertIn("missing or partial imagery", info_text)
            self.assertIn("Country borders are independent overlays with their own switch and color", info_text)
            self.assertIn("transparent No Data pixels", info_text)
            self.assertIn("No analytics, telemetry, advertising", info_text)
            self.assertIn("PolyForm Noncommercial 1.0.0", info_text)
            about_buttons = {
                str(widget.cget("text")) for widget in self.descendants(context.root)
                if isinstance(widget, ttk.Button)
            }
            self.assertTrue({
                "Open GitHub project", "Check for updates", "Open latest release",
                "Documentation", "Privacy & network", "License & attribution",
                "Support this project",
            }.issubset(about_buttons))
            section = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Download display"
            )
            checkboxes = {
                widget.cget("text") for widget in section.winfo_children()
                if isinstance(widget, ttk.Checkbutton)
            }
            self.assertEqual(checkboxes, {
                "Show download speed",
                "Show percentage",
                "Show downloaded size",
                "Show progress bar",
                "Keep completed download visible until next download",
            })
            retries = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Download retries"
            )
            self.assertIn("9", next(widget for widget in retries.winfo_children()
                                    if isinstance(widget, ttk.Combobox))["values"])
            catalogue_retries = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Catalogue retries"
            )
            self.assertIn("9", next(widget for widget in catalogue_retries.winfo_children()
                                    if isinstance(widget, ttk.Combobox))["values"])
            cancel_download = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.Button)
                and widget.cget("text") == "Cancel download"
            )
            self.assertTrue(cancel_download.instate(["disabled"]))
            context.variables["show_download_speed"].set(False)
            context.variables["download_speed_unit"].set("Mbit/s")
            context.variables["show_download_progress"].set(True)
            context.variables["show_download_size"].set(False)
            context.variables["show_download_progress_bar"].set(False)
            context.variables["keep_completed_download_visible"].set(True)
            context.variables["download_retries"].set("9")
            context.variables["catalogue_retries"].set("8")
            saved = self.apply(context)
            self.assertEqual(saved["download"], {
                "show_speed": False,
                "speed_unit": "Mbit/s",
                "show_progress": True,
                "show_size": False,
                "show_progress_bar": False,
                "keep_completed_visible": True,
                "retries": 9,
                "catalogue_retries": 8,
                "catalogue_refresh_time": "03:00:00",
            })

        self.run_dialog(scenario)

    def test_storage_folder_controls_open_current_targets_below_choose(self):
        from tkinter import ttk

        def scenario(context):
            sections = {
                widget.cget("text"): widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") in {
                    "Monitor output", "Latest image folder", "History folder",
                    "History (no profile)", "History (profile)", "Status and storage"
                }
            }
            latest = sections["Latest image folder"]
            self.assertEqual(int(latest.grid_info()["row"]), 0)
            self.assertEqual(int(sections["History (no profile)"].grid_info()["row"]), 2)
            self.assertEqual(int(sections["History (profile)"].grid_info()["row"]), 3)
            self.assertEqual(int(sections["Status and storage"].grid_info()["row"]), 4)
            self.assertFalse(any(
                isinstance(widget, ttk.Label)
                and widget.cget("text") == "Custom latest folder"
                for widget in self.descendants(sections["Monitor output"])
            ))
            # One shared History root in its own section; profiles
            # no longer have their own folder field.
            for title in ("History (no profile)", "History (profile)"):
                self.assertFalse(any(
                    isinstance(widget, ttk.Label)
                    and widget.cget("text") == "Custom history folder"
                    for widget in self.descendants(sections[title])
                ))

            def button(section, label):
                return next(widget for widget in self.descendants(section)
                            if isinstance(widget, ttk.Button)
                            and widget.cget("text") == label)

            latest_open = button(latest, "Open latest folder")
            history_section = sections["History folder"]
            self.assertEqual(int(history_section.grid_info()["row"]), 1)
            self.assertIs(history_section.master, latest.master)
            # Both folder fields stretch the same way.
            self.assertEqual(int(latest.columnconfigure(1)["weight"]), 1)
            self.assertEqual(int(history_section.columnconfigure(1)["weight"]), 1)
            history_open = button(history_section, "Open history folder")
            for open_button in (latest_open, history_open):
                choose = next(widget for widget in open_button.master.winfo_children()
                              if isinstance(widget, ttk.Button)
                              and widget.cget("text") == "Choose...")
                self.assertEqual(int(choose.grid_info()["row"]), 0)
            self.assertEqual(int(latest_open.grid_info()["row"]), 1)
            self.assertEqual(int(history_open.grid_info()["row"]), 1)
            custom_latest = context.directory / "custom-latest"
            custom_history = context.directory / "custom-history"
            context.variables["latest_folder"].set(str(custom_latest))
            context.variables["history_folder"].set(str(custom_history))
            with patch.object(app.os, "startfile") as startfile:
                latest_open.invoke()
                history_open.invoke()
            self.assertTrue(custom_latest.is_dir())
            self.assertTrue(custom_history.is_dir())
            self.assertEqual(startfile.call_args_list, [
                unittest.mock.call(str(custom_latest.resolve())),
                unittest.mock.call(str(custom_history.resolve())),
            ])
            context.variables["latest_folder"].set("")
            context.variables["history_folder"].set("")
            with patch.object(app.os, "startfile") as startfile:
                latest_open.invoke()
                history_open.invoke()
            self.assertEqual(startfile.call_args_list, [
                unittest.mock.call(str((app.CONTENT_DIR / "latest").resolve())),
                unittest.mock.call(str((app.CONTENT_DIR / "history").resolve())),
            ])
            # Choose... starts in the folder in use: the default one (even before
            # it exists) or the custom one.
            self.assertTrue(app.CONTENT_DIR.resolve().is_relative_to(context.directory.resolve()))
            shutil.rmtree(app.CONTENT_DIR / "history")
            for open_button, default, key, custom in (
                    (latest_open, app.CONTENT_DIR / "latest", "latest_folder", custom_latest),
                    (history_open, app.CONTENT_DIR / "history", "history_folder", custom_history)):
                choose = next(widget for widget in open_button.master.winfo_children()
                              if isinstance(widget, ttk.Button) and widget.cget("text") == "Choose...")
                for value, expected in (("", default), (str(custom), custom)):
                    context.variables[key].set(value)
                    with patch("tkinter.filedialog.askdirectory", return_value="") as dialog:
                        choose.invoke()
                    self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]).resolve(), expected.resolve())
                    self.assertEqual(context.variables[key].get(), value)

        self.run_dialog(scenario)

    def test_profile_table_finds_history_and_cache_images_in_subfolders(self):
        from marblescape_image_metadata import embed_png_metadata

        archived_id, cached_id = "a" * 32, "b" * 32

        def tagged_png(identifier, coverage):
            return embed_png_metadata(source_png((2, 2)), {
                "software": "MarbleScape", "width": 2, "height": 2, "profile_id": identifier,
                "generated_at_utc": "2026-09-27T02:00:00Z", "data_coverage_percent": coverage,
            })

        def scenario(context):
            # A replaced Latest image lands in the profile's History subfolder.
            folder = app.profile_history_directory(archived_id, "Archived", create=True)
            (folder / "MarbleScape_2026-09-27T020000Z_archive.png").write_bytes(tagged_png(archived_id, 75.0))
            cached, _previous = app.get_profile_cache().install(
                cached_id, {"configuration": 1}, {"source": 1}, tagged_png(cached_id, 50.0), (2, 2))
            self.assertNotEqual(cached.parent, app.get_profile_cache().images_dir)
            self.assertIn(folder, app.published_image_folders())
            deadline = time.monotonic() + 5
            while True:
                status = context.profiles._status()
                records = status["image_records"]
                if {archived_id, cached_id} <= set(records) or time.monotonic() > deadline:
                    break
                context.root.update()
                time.sleep(0.05)
            self.assertEqual(status["last_downloads"].get(archived_id), "2026-09-27T02:00:00+00:00")
            self.assertEqual(records[archived_id]["data_coverage_percent"], 75.0)
            self.assertEqual(records[cached_id]["data_coverage_percent"], 50.0)
            # A cached render alone is never reported as a download.
            self.assertNotIn(cached_id, status["last_downloads"])

        self.run_dialog(scenario)

    def test_deleted_profile_history_is_removed_at_once_when_opted_in(self):
        import marblescape_profile_settings as profile_ui

        def scenario(context):
            profiles = context.profiles
            profile_path = context.directory / "profiles.toml"
            for name in ("Keep", "Gone"):
                profiles.add_current(name)
            self.assertEqual(profiles.last_saved_change, "Added profile 'Gone'.")
            self.assertEqual(len(app.read_profile_library_file(profile_path)["items"]), 2)
            ids = {item["name"]: item["id"] for item in profiles._items}
            folders = {}
            for name in ("Keep", "Gone"):
                folders[name] = app.profile_history_directory(ids[name], name, create=True)
                (folders[name] / "MarbleScape_2026-09-26T120000Z_archive.png").write_bytes(source_png((32, 18)))
            (folders["Gone"] / "notes.txt").write_text("user", encoding="utf-8")
            profiles.tree.selection_set(ids["Gone"])
            with patch.object(profile_ui, "_confirm_profile_delete", return_value=True) as confirm:
                profiles.delete_selected()
            self.assertTrue(confirm.call_args.args[3].startswith("Also delete History images (1 image, "))
            self.assertIn("1 other file (4 B)", confirm.call_args.args[4])
            self.assertEqual(context.errors, [])
            self.assertEqual(profiles.last_saved_change, "Deleted 1 profile(s) and their History folders.")
            self.assertEqual([item["name"] for item in app.read_profile_library_file(profile_path)["items"]], ["Keep"])
            self.assertFalse(folders["Gone"].exists())
            self.assertEqual(len(list(folders["Keep"].glob("*.png"))), 1)

        self.run_dialog(scenario)

    def test_history_profile_and_retention_fields_share_one_width(self):
        from tkinter import ttk

        def scenario(context):
            fields = {}
            for section in ("History (no profile)", "History (profile)"):
                frame = next(widget for widget in self.descendants(context.root)
                             if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == section)
                labels = {int(widget.grid_info()["row"]): widget.cget("text") for widget in frame.winfo_children()
                          if isinstance(widget, ttk.Label) and widget.grid_info()}
                for widget in frame.winfo_children():
                    if isinstance(widget, ttk.Combobox):
                        label = labels.get(int(widget.grid_info()["row"]))
                        if label in {"Profile", "Retention mode", "Maximum files"}:
                            fields[(section, label)] = widget
            self.assertEqual(len(fields), 5)
            self.assertIn(("History (profile)", "Profile"), fields)
            context.root.update_idletasks()
            self.assertEqual({int(widget.cget("width")) for widget in fields.values()}, {app.HISTORY_FIELD_WIDTH})
            self.assertEqual(len({widget.winfo_reqwidth() for widget in fields.values()}), 1)
            # Fixed width: none of them stretches with the window.
            self.assertTrue(all(widget.grid_info()["sticky"] == "w" for widget in fields.values()))
            # Maximum files stays a free entry with the same suggestions in both sections.
            for section in ("History (no profile)", "History (profile)"):
                combo = fields[(section, "Maximum files")]
                self.assertEqual(str(combo.cget("state")), "normal")
                self.assertEqual(tuple(combo.cget("values")),
                                 tuple(str(value) for value in app.HISTORY_MAX_FILES_MENU_CHOICES))
                self.assertEqual(tuple(combo.cget("values"))[:3], ("1", "5", "10"))
                self.assertEqual(str(fields[(section, "Retention mode")].cget("state")), "readonly")

        self.run_dialog(scenario)

    def test_storage_clear_buttons_are_positioned_and_delete_only_managed_files(self):
        from tkinter import ttk

        def scenario(context):
            app.ensure_directories()
            latest = app.LATEST_DIR / "keep.png"
            latest.write_bytes(source_png((32, 18)))
            history = app.profile_history_directory(None, create=True) / "marblescape_2026-09-13_120000.png"
            history.write_bytes(source_png((32, 18)))
            unrelated_history = app.HISTORY_DIR / "keep.png"
            unrelated_history.write_bytes(source_png((32, 18)))
            cached, _previous = app.get_profile_cache().install(
                "f" * 32, {"configuration": "same"}, {"source": "same"},
                source_png((32, 18)), (32, 18),
            )
            widgets = list(self.descendants(context.root))
            cache_section = next(
                widget for widget in widgets
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Profile image cache"
            )
            status_section = next(
                widget for widget in widgets
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Status and storage"
            )
            self.assertEqual(cache_section.master.cget("text"), "History (profile)")
            button = next(
                widget for widget in cache_section.winfo_children()
                if isinstance(widget, ttk.Button) and widget.cget("text") == "Clear cache"
            )
            with patch("tkinter.messagebox.askyesno", return_value=True):
                button.invoke()
            context.root.update_idletasks()
            self.assertFalse(cached.exists())
            self.assertTrue(latest.exists())
            self.assertEqual(app.get_profile_cache().status()["files"], 0)

            clear_history = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.Button) and widget.cget("text") == "Clear history"
            )
            next_check = next(
                widget for widget in status_section.winfo_children()
                if isinstance(widget, ttk.Label) and widget.cget("text") == "Next check"
            )
            self.assertEqual(clear_history.master.cget("text"), "History (no profile)")
            with patch("tkinter.messagebox.askyesno", return_value=True):
                clear_history.invoke()
            context.root.update_idletasks()
            self.assertFalse(history.exists())
            self.assertTrue(unrelated_history.exists())
            self.assertTrue(latest.exists())

        self.run_dialog(scenario)

    def test_every_source_keeps_the_same_gap_between_its_fields(self):
        from tkinter import ttk

        def shown(widget, stop):
            while widget is not stop:
                if not widget.winfo_manager():
                    return False
                widget = widget.nametowidget(widget.winfo_parent())
            return True

        def top(widget, stop):
            y = 0
            while widget is not stop:
                y += widget.winfo_y()
                widget = widget.nametowidget(widget.winfo_parent())
            return y

        def uneven_gaps(context, title):
            context.root.update_idletasks()
            context.root.update()
            section = next(widget for widget in self.descendants(context.image_tab)
                           if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Source")
            fields, others = [], []
            for widget in self.descendants(section):
                if not shown(widget, section):
                    continue
                box = (top(widget, section), top(widget, section) + widget.winfo_height())
                if widget.winfo_class() in ("TCombobox", "TEntry"):
                    fields.append(box)
                elif widget.winfo_class() in ("TLabel", "TCheckbutton", "TButton"):
                    others.append(box)
            fields.sort()
            self.assertGreater(len(fields), 2, title)
            uneven = []
            for (_upper_top, upper_bottom), (lower_top, _lower_bottom) in zip(fields, fields[1:]):
                # A hint or check box between two fields has its own height.
                between = any(upper_bottom <= start and end <= lower_top for start, end in others)
                if not between and lower_top - upper_bottom != 6:
                    uneven.append((title, upper_bottom, lower_top - upper_bottom))
            return uneven

        def scenario(context):
            context.notebook.select(next(tab_id for tab_id in context.notebook.tabs()
                                         if context.notebook.tab(tab_id, "text") == "Image"))
            context.root.update()
            context.image_tab = context.notebook.nametowidget(context.notebook.select())
            # Canvas window items are placed only while the canvas is on screen;
            # place the scrolled content directly to measure in the hidden window.
            canvas = next(widget for widget in self.descendants(context.image_tab)
                          if widget.winfo_class() == "Canvas")
            content = canvas.winfo_children()[0]
            canvas.delete("all")
            content.place(in_=canvas, x=0, y=0, width=900)
            uneven = []
            filtered = []
            for provider in ("goes_east", "goes_west", "solar", "himawari", "slider", "worldview", "eumetsat"):
                context.source.set_selection(provider, app.DEFAULT_SOURCE_PROFILES)
                self.wait_for_source(context)
                uneven += uneven_gaps(context, provider)
                source = context.source
                if shown(source._filter_frame, source.frame):
                    # Field and Clear span exactly the width of the other fields.
                    field, area = source._filter_frame, source._area_combo
                    clear = source._filter_clear_button
                    self.assertEqual((field.winfo_x(), field.winfo_x() + field.winfo_width()),
                                     (area.winfo_x(), area.winfo_x() + area.winfo_width()), provider)
                    self.assertEqual(clear.winfo_x() + clear.winfo_width(), field.winfo_width(), provider)
                    filtered.append(provider)
            self.assertEqual(filtered, ["goes_east", "goes_west", "himawari", "slider", "worldview"])
            context.source.set_selection("copernicus", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            copernicus = context.source.copernicus_settings
            for mission in copernicus._mission_combo.cget("values"):
                copernicus._mission_var.set(mission)
                copernicus._mission_combo.event_generate("<<ComboboxSelected>>")
                context.root.update()
                for product in copernicus._product_combo.cget("values"):
                    copernicus._product_var.set(product)
                    copernicus._product_combo.event_generate("<<ComboboxSelected>>")
                    uneven += uneven_gaps(context, f"{mission} / {product}")
            self.assertEqual(uneven, [])

        self.run_dialog(scenario)

    def test_profile_cache_sliders_update_the_estimate_and_save_as_drafts(self):
        from tkinter import ttk

        def scenario(context):
            app.ensure_directories()
            latest = app.LATEST_DIR / "marblescape_latest.png"
            latest.write_bytes(source_png((32, 18)))
            app.set_current_image_path(latest)
            size = latest.stat().st_size
            cache_section = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Profile image cache"
            )
            scales = [widget for widget in self.descendants(cache_section) if widget.winfo_class() == "Scale"]
            self.assertEqual(
                [tuple(float(scale.cget(option)) for option in ("from", "to", "resolution"))
                 for scale in scales],
                [(0.5, 10.0, 0.5), (1.0, 10.0, 1.0), (0.5, 10.0, 0.5)],
            )
            shown = [context.root.getvar(widget.cget("textvariable"))
                     for widget in self.descendants(cache_section)
                     if isinstance(widget, ttk.Label) and widget.cget("width") == 7]
            self.assertEqual(shown, ["2.0 GB", "5", "0.5 GB"])
            status_section = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Status and storage"
            )
            labels = [str(widget.cget("text")) for widget in status_section.winfo_children()
                      if isinstance(widget, ttk.Label) and widget.grid_info()["column"] == 0]
            self.assertNotIn("Estimated profile cache", labels)
            row = next(int(widget.grid_info()["row"]) for widget in status_section.winfo_children()
                       if isinstance(widget, ttk.Label) and widget.cget("text") == "Total storage estimate")
            estimate = next(widget for widget in status_section.winfo_children()
                            if int(widget.grid_info()["row"]) == row and widget.grid_info()["column"] == 1)

            def estimate_text():
                return context.root.getvar(estimate.cget("textvariable"))

            # The dialog opened before the picture existed.
            self.assertEqual(estimate_text(), "Unavailable until the first image")
            # Moving a slider updates the total, which includes the cache, before Save.
            scales[0].set(1.5)
            scales[1].set(2)
            context.root.update()
            self.assertEqual(context.variables["profile_cache_max_size_gb"].get(), 1.5)
            # The Latest snapshot can fill its own size limit with variants.
            smaller = app.get_storage_status(max_size_gb=1.5, variants=2)
            snapshot = smaller["estimated_cache_bytes"] - smaller["cache_slots"] * 2 * size
            self.assertGreater(snapshot, 500_000_000 - size)
            self.assertLessEqual(snapshot, 500_000_000)
            self.assertEqual(estimate_text(), app.format_bytes(smaller["estimated_total_bytes"]))
            scales[2].set(3.0)
            context.root.update()
            larger = app.get_storage_status(max_size_gb=1.5, variants=2, snapshot_size_gb=3.0)
            self.assertGreater(larger["estimated_total_bytes"], smaller["estimated_total_bytes"])
            self.assertEqual(estimate_text(), app.format_bytes(larger["estimated_total_bytes"]))
            self.assertEqual([context.root.getvar(widget.cget("textvariable"))
                              for widget in self.descendants(cache_section)
                              if isinstance(widget, ttk.Label) and widget.cget("width") == 7],
                             ["1.5 GB", "2", "3.0 GB"])
            # A draft: the configuration and the running limits stay until Save.
            self.assertEqual(app.PROFILE_CACHE_VARIANTS, 5)
            self.assertEqual(tomllib.loads(context.config.read_text(encoding="utf-8"))["cache"],
                             {"max_size_gb": 2.0, "variants_per_profile": 5, "latest_snapshot_size_gb": 0.5})
            saved = self.apply(context, tab="History & Storage")
            self.assertEqual(saved["cache"], {"max_size_gb": 1.5, "variants_per_profile": 2,
                                              "latest_snapshot_size_gb": 3.0})

        self.run_dialog(scenario)

    def test_active_storm_profile_loads_and_normal_apply_persists_actual_area(self):
        def scenario(context):
            context.source.set_selection("goes_east", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            context.source._category_var.set("Active storms")
            context.source._select_category()
            self.wait_for_source(context)
            self.assertEqual(context.source.get_selection()[1]["goes_east"]["area"], "storm_alpha")
            context.variables["zoom"].set("1.4")
            context.variables["fit_mode"].set("crop")
            context.profiles.add_current("Current storm")
            self.assertEqual(context.errors, [])
            expected = context.profiles.get_library()["items"][0]["settings"]
            context.source.set_selection("solar", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            context.variables["zoom"].set("2.0")
            context.variables["fit_mode"].set("fit")
            context.profiles.load_selected()
            self.wait_for_source(context)
            self.assertEqual(context.profiles._capture_settings(), expected)
            self.assertEqual(context.source._area_var.get(), "Storm Alpha [storm_alpha]")
            self.apply(context, tab="Profiles")
            after = self.apply(context, tab="Image")
            self.assertEqual(after["source"]["provider"], "goes_east")
            self.assertEqual(after["sources"]["goes_east"],
                             {"area": "storm_alpha", "product": "GEOCOLOR", "resolution": "auto"})
            self.assertEqual(after["view"]["zoom"], 1.4)
            self.assertEqual(after["view"]["fit_mode"], "crop")
            self.assertNotIn("image_profiles", after)
            self.assertEqual(
                app.read_profile_library_file(context.directory / "profiles.toml")
                ["items"][0]["settings"],
                expected,
            )
        self.run_dialog(scenario)

    def test_eumetsat_preset_is_under_source_and_mouse_focus_keeps_viewport(self):
        from tkinter import ttk
        def scenario(context):
            source = context.source
            preset = next(widget for widget in source.eumetsat_view_frame.winfo_children()
                          if isinstance(widget, ttk.Combobox)
                          and "Full Earth" in widget["values"])
            self.assertIs(source.eumetsat_frame.master, source.frame)
            self.assertIs(preset.master, source.eumetsat_view_frame)
            eumetsat_labels = {
                widget.cget("text") for widget in source.eumetsat_view_frame.winfo_children()
                if isinstance(widget, ttk.Label)
            }
            self.assertTrue({"Projection", "Fit mode", "Zoom", "Preset"}.issubset(eumetsat_labels))
            self.assertTrue(any(
                isinstance(widget, ttk.Checkbutton)
                and widget.cget("text") == "Black TrueColor night side"
                for widget in source.eumetsat_view_frame.winfo_children()
            ))
            # The "not available yet" line closes the view block; hints live in Info.
            announced = source.eumetsat_settings._announced_label
            source.eumetsat_settings._show_announced([{"layer": "epssg:m01_metimage_ir1069"}])
            rows = {str(widget.cget("text")): int(widget.grid_info()["row"])
                    for widget in source.eumetsat_view_frame.winfo_children()
                    if widget is not announced and widget.grid_info() and "text" in widget.keys()}
            self.assertEqual(max(rows.values()), rows["Black TrueColor night side"])
            self.assertEqual(int(announced.grid_info()["row"]), rows["Black TrueColor night side"] + 1)
            self.assertFalse(any(text.startswith(("Selecting a preset", "Coverage depends"))
                                 for text in rows))
            source.eumetsat_settings._show_announced([])
            self.assertEqual(announced.grid_info(), {})
            source._provider_var.set("NOAA GOES")
            source._select_provider()
            self.assertTrue(source.generic_view_frame.grid_info())
            self.assertEqual(context.variables["zoom"].get(), "1")
            self.assertEqual({
                widget.cget("text") for widget in source.generic_view_frame.winfo_children()
                if isinstance(widget, ttk.Label)
            } & {"Fit mode", "Zoom"}, {"Fit mode", "Zoom"})
            source._provider_var.set("EUMETSAT")
            source._select_provider()
            # Both views start at the default zoom 1.
            self.assertEqual(context.variables["zoom"].get(), "1")
            self.assertGreater(source.eumetsat_frame.grid_info()["row"], source._provider_combo.grid_info()["row"])
            general_id = next(tab for tab in context.notebook.tabs() if context.notebook.tab(tab, "text") == "General")
            general = context.root.nametowidget(general_id)
            general_text = [str(widget.cget("text")) for widget in self.descendants(general)
                            if "text" in widget.keys()]
            self.assertNotIn("Preset", general_text)
            content = source.frame.master
            canvas, page = content.master, content.master.master
            context.notebook.select(page)
            context.root.update_idletasks()
            zoom_label = next(widget for widget in self.descendants(content)
                              if isinstance(widget, ttk.Label) and widget.cget("text") == "Zoom")
            row = zoom_label.grid_info()["row"]
            zoom_entry = next(widget for widget in zoom_label.master.winfo_children()
                              if isinstance(widget, ttk.Entry) and widget.grid_info().get("row") == row)
            canvas.configure(scrollregion=(0, 0, 800, content.winfo_reqheight()))
            canvas.yview_moveto(0.45)
            before = canvas.yview()
            self.assertGreater(before[0], 0)
            self.assertEqual(context.root.bind("<FocusIn>"), "", "Mouse focus must not trigger viewport reveal")
            zoom_entry.event_generate("<Button-1>", x=3, y=3, when="now")
            zoom_entry.event_generate("<FocusIn>", when="now")
            source._provider_combo.event_generate("<FocusIn>", when="now")
            context.root.update()
            self.assertAlmostEqual(
                canvas.yview()[0], before[0], places=9,
                msg="Mouse/combobox focus moved the Image viewport",
            )
        self.run_dialog(scenario)

    CUSTOM_AREA_KEYS = ("custom_latitude", "custom_longitude")

    def select_preset(self, context, label):
        from tkinter import ttk
        combo = next(widget for widget in context.source.eumetsat_view_frame.winfo_children()
                     if isinstance(widget, ttk.Combobox)
                     and str(widget.cget("textvariable")) == str(context.variables["view_preset"]))
        context.variables["view_preset"].set(label)
        combo.event_generate("<<ComboboxSelected>>")
        context.root.update()

    def custom_area_frame(self, context):
        from tkinter import ttk
        name = str(context.variables["custom_latitude"])
        return next(widget for widget in self.descendants(context.source.eumetsat_view_frame)
                    if isinstance(widget, ttk.Entry) and str(widget.cget("textvariable")) == name).master

    def shown_texts(self, context, frame):
        from tkinter import ttk
        return {context.root.getvar(str(widget.cget("textvariable")))
                if str(widget.cget("textvariable")) else str(widget.cget("text"))
                for widget in self.descendants(frame)
                if isinstance(widget, ttk.Label) and widget.grid_info()}

    def custom_area_values(self, context):
        return [context.variables[key].get() for key in (*self.CUSTOM_AREA_KEYS, "zoom")]

    def set_custom_area(self, context, latitude, longitude, zoom):
        for key, value in zip((*self.CUSTOM_AREA_KEYS, "zoom"), (latitude, longitude, zoom)):
            context.variables[key].set(value)

    def output_ratio(self, context):
        return app.parse_aspect_ratio(context.variables["aspect_ratio"].get())

    def test_custom_area_starts_from_the_previous_preset_and_saves_centre_and_zoom(self):
        def scenario(context):
            self.wait_for_source(context)
            area_frame = self.custom_area_frame(context)
            self.assertEqual(area_frame.grid_info(), {})
            self.select_preset(context, "Europe")
            self.select_preset(context, "Custom area")
            self.assertTrue(area_frame.grid_info())
            # Europe at zoom 1 spans 70 degrees around 51 N, 10 E: 360 / 70 = 5.14.
            self.assertEqual(self.custom_area_values(context), ["51", "10", "5.14"])
            # The Zoom dropdown offers the Custom area steps and keeps the value between them.
            zoom_combo = next(widget for widget in self.descendants(context.source.eumetsat_view_frame)
                              if widget.winfo_class() == "TCombobox"
                              and str(widget.cget("values")).find("about Germany") >= 0)
            self.assertEqual(zoom_combo.get(), "5.14 (saved)")
            labels = list(zoom_combo.cget("values"))
            self.assertEqual(labels[:7], ["1", "1.5", "2", "3", "4", "5 (about Europe)", "5.14 (saved)"])
            self.assertEqual(labels[-1], "50")
            zoom_combo.set("25 (about Germany)")
            zoom_combo.event_generate("<<ComboboxSelected>>")
            context.root.update()
            self.assertEqual(context.variables["zoom"].get(), "25")
            hint = next(text for text in self.shown_texts(context, area_frame) if text.startswith("Centre"))
            self.assertIn("Above about zoom 20 EUMETSAT imagery gets blurry.", hint)
            self.assertIn("outside the satellites' view", hint)
            self.assertEqual(context.variables["projection"].get(), "Geographic")
            self.assertEqual(context.variables["fit_mode"].get(), "crop")
            self.assertTrue({"Latitude", "Longitude"} <= self.shown_texts(context, area_frame))
            self.set_custom_area(context, "51,5", "10", "24")
            hint = next(text for text in self.shown_texts(context, area_frame) if text.startswith("Centre"))
            self.assertIn("Shows about 15° ×", hint)
            ratio = self.output_ratio(context)
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["view"]["preset"], "custom")
            self.assertEqual(saved["view"]["projection"], "Geographic")
            self.assertEqual(saved["view"]["zoom"], 1.0)
            expected = app.custom_area_extent(51.5, 10.0, 24.0, ratio)
            self.assertEqual(saved["view"]["bbox"], expected)
            # Another preset hides the fields but keeps the saved area in the file.
            self.select_preset(context, "Central Europe")
            self.assertEqual(area_frame.grid_info(), {})
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["view"]["preset"], "central_europe")
            self.assertEqual(saved["view"]["bbox"], expected)
        self.run_dialog(scenario)

    def test_unchanged_custom_area_is_kept_exactly(self):
        def prepare(text):
            return app.replace_toml_values(text, [
                ("view", "projection", "Geographic"), ("view", "preset", "custom"),
                ("view", "bbox", [-25.0, 30.0, 45.0, 72.0]), ("view", "zoom", 1.1),
                ("view", "fit_mode", "fit"),
            ])

        def scenario(context):
            self.wait_for_source(context)
            self.assertEqual(context.variables["view_preset"].get(), "Custom area")
            self.assertEqual(self.custom_area_values(context), ["51", "10", "5.66"])
            context.variables["fit_mode"].set("crop")
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["view"]["bbox"], [-25.0, 30.0, 45.0, 72.0])
            self.assertEqual(saved["view"]["zoom"], 1.1)
            self.assertEqual(saved["view"]["fit_mode"], "crop")
            context.variables["custom_latitude"].set("50")
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["view"]["zoom"], 1.0)
            self.assertEqual(saved["view"]["bbox"],
                             app.custom_area_extent(50.0, 10.0, 5.66, self.output_ratio(context)))
        self.run_dialog(scenario, prepare_config=prepare)

    def test_custom_area_from_full_earth_starts_with_the_whole_world(self):
        from tkinter import ttk

        def scenario(context):
            self.wait_for_source(context)
            self.assertEqual(context.variables["view_preset"].get(), "Full Earth")
            self.select_preset(context, "Custom area")
            self.assertEqual(self.custom_area_values(context), ["0", "0", "1"])
            self.assertEqual(context.variables["fit_mode"].get(), "crop")
            ratio = self.output_ratio(context)
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["view"]["bbox"], app.custom_area_extent(0.0, 0.0, 1.0, ratio))
            # A projection change returns to Full Earth and hides the fields.
            projection = next(widget for widget in context.source.eumetsat_view_frame.winfo_children()
                              if isinstance(widget, ttk.Combobox)
                              and str(widget.cget("textvariable")) == str(context.variables["projection"]))
            context.variables["projection"].set("GEOS: MSG FES, MTG FD")
            projection.event_generate("<<ComboboxSelected>>")
            context.root.update()
            self.assertEqual(context.variables["view_preset"].get(), "Full Earth")
            self.assertEqual(self.custom_area_frame(context).grid_info(), {})
        self.run_dialog(scenario)

    def test_invalid_custom_area_is_reported_and_nothing_is_saved(self):
        from tkinter import ttk

        def scenario(context):
            self.wait_for_source(context)
            self.select_preset(context, "Custom area")
            context.notebook.select(next(tab for tab in context.notebook.tabs()
                                         if context.notebook.tab(tab, "text") == "Image"))
            context.root.update()
            button = next(widget for widget in self.descendants(context.root)
                          if isinstance(widget, ttk.Button) and widget.cget("text") == "Apply Image")
            before = context.config.read_text(encoding="utf-8")
            for values, message in (
                (("north", "10", "5"), "Latitude must be a number"),
                (("51", "200", "5"), "Longitude must be between -180 and 180"),
            ):
                with self.subTest(values=values):
                    self.set_custom_area(context, *values)
                    button.invoke()
                    self.assertEqual(len(context.errors), 1)
                    self.assertIn(message, context.errors.pop()[1])
                    self.assertEqual(context.config.read_text(encoding="utf-8"), before)
            # A zoom beyond the Custom area range cannot stay: the dropdown shows the default.
            self.set_custom_area(context, "51", "10", "2000")
            context.root.update()
            self.assertEqual(context.variables["zoom"].get(), "1")
        self.run_dialog(scenario)

    def test_custom_area_in_map_units_is_kept_and_zoom_keeps_its_meaning(self):
        bbox = [-3000000.0, -2000000.0, 3000000.0, 2000000.0]

        def prepare(text):
            return app.replace_toml_values(text, [
                ("view", "projection", "GEOS: MSG FES, MTG FD"),
                ("view", "preset", "custom"),
                ("view", "bbox", bbox),
            ])

        def scenario(context):
            self.wait_for_source(context)
            self.assertEqual(context.variables["view_preset"].get(), "Custom area")
            area_frame = self.custom_area_frame(context)
            self.assertTrue(area_frame.grid_info())
            texts = self.shown_texts(context, area_frame)
            self.assertNotIn("Latitude", texts)
            self.assertTrue(any("map units" in text for text in texts))
            context.variables["zoom"].set("1.5")
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["view"]["projection"], "GEOS: MSG FES, MTG FD")
            self.assertEqual(saved["view"]["preset"], "custom")
            self.assertEqual(saved["view"]["bbox"], bbox)
            self.assertEqual(saved["view"]["zoom"], 1.5)
        self.run_dialog(scenario, prepare_config=prepare)

    def test_output_device_position_is_saved_for_one_monitor(self):
        from tkinter import ttk

        monitors = [
            {"id": r"\\?\DISPLAY#FIRST", "rect": (0, 0, 1920, 1080)},
            {"id": r"\\?\DISPLAY#SECOND", "rect": (1920, 0, 3840, 1080)},
        ]

        def scenario(context):
            general_id = next(tab for tab in context.notebook.tabs()
                              if context.notebook.tab(tab, "text") == "General")
            general = context.root.nametowidget(general_id)
            device_combo = next(widget for widget in self.descendants(general)
                                if isinstance(widget, ttk.Combobox)
                                and str(widget.cget("textvariable")) ==
                                str(context.variables["output_device"]))
            context.variables["output_device"].set("Display 2 (1920 × 1080)")
            device_combo.event_generate("<<ComboboxSelected>>")
            context.variables["position"].set("none")
            # Display 1 keeps the shared position.
            context.variables["output_device"].set("Display 1 (1920 × 1080)")
            device_combo.event_generate("<<ComboboxSelected>>")
            self.assertEqual(context.variables["position"].get(), "fit")
            context.variables["output_device"].set("Display 2 (1920 × 1080)")
            device_combo.event_generate("<<ComboboxSelected>>")
            self.assertEqual(context.variables["position"].get(), "none")
            saved = self.apply(context)
            positions = json.loads(saved["windows"]["monitor_positions"])
            self.assertEqual(positions, {monitors[1]["id"]: "none"})
            self.assertEqual(saved["windows"]["position"], "fit")

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.run_dialog(scenario)

    def test_restore_previous_wallpaper_needs_no_save(self):
        from tkinter import ttk

        monitors = [
            {"id": r"\\?\DISPLAY#FIRST", "rect": (0, 0, 1920, 1080)},
            {"id": r"\\?\DISPLAY#SECOND", "rect": (1920, 0, 3840, 1080)},
        ]

        def restore(monitor_id):
            # As the real restore: the display paused, saved at once.
            paused = [monitor_id]
            app.update_active_configuration(lambda text: app.replace_toml_section_value(
                text, "windows", "paused_displays", json.dumps(paused)))
            app.WINDOWS_WALLPAPER_PAUSED = frozenset(paused)

        def scenario(context):
            general_id = next(tab for tab in context.notebook.tabs()
                              if context.notebook.tab(tab, "text") == "General")
            context.notebook.select(general_id)
            general = context.root.nametowidget(general_id)
            device_combo = next(widget for widget in self.descendants(general)
                                if isinstance(widget, ttk.Combobox)
                                and str(widget.cget("textvariable")) == str(context.variables["output_device"]))
            restore_button = next(widget for widget in self.descendants(general)
                                  if isinstance(widget, ttk.Button)
                                  and widget.cget("text") == "Restore previous wallpaper")
            for other_draft in (False, True):
                context.variables["output_device"].set("Display 2 (1920 × 1080)")
                device_combo.event_generate("<<ComboboxSelected>>")
                context.root.event_generate("<<SettingsDraftChanged>>")
                context.root.update()
                if other_draft:
                    context.variables["background_color"].set("#123456")
                with patch.object(app, "restore_previous_wallpaper", side_effect=restore) as restored:
                    restore_button.invoke()
                restored.assert_called_once_with(monitors[1]["id"])
                context.root.event_generate("<<SettingsDraftChanged>>")
                context.root.update()
                pause = next(widget for widget in self.descendants(general)
                             if isinstance(widget, ttk.Checkbutton)
                             and widget.cget("text") == "Pause wallpaper updates")
                self.assertTrue(context.root.getboolean(context.root.getvar(pause.cget("variable"))))
                # The position stays for when the display is resumed.
                self.assertNotEqual(context.variables["position"].get(), "none")
                # Only a different, unsaved edit still waits for Save.
                self.assertEqual(self.footer_notice(context) == "Unsaved changes", other_draft)
            self.assertEqual(context.errors, [])

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.run_dialog(scenario)

    def test_pause_wallpaper_updates_per_display_keeps_its_position(self):
        from tkinter import ttk

        monitors = [
            {"id": "DISPLAY-FIRST", "rect": (0, 0, 2560, 1440)},
            {"id": "DISPLAY-SECOND", "rect": (2560, 0, 3610, 1680)},
        ]

        def scenario(context):
            general_id = next(tab for tab in context.notebook.tabs()
                              if context.notebook.tab(tab, "text") == "General")
            context.notebook.select(general_id)
            general = context.root.nametowidget(general_id)
            combo = next(widget for widget in self.descendants(general)
                         if isinstance(widget, ttk.Combobox)
                         and str(widget.cget("textvariable")) == str(context.variables["output_device"]))
            pause = next(widget for widget in self.descendants(general)
                         if isinstance(widget, ttk.Checkbutton) and widget.cget("text") == "Pause wallpaper updates")
            position = next(widget for widget in self.descendants(general)
                            if isinstance(widget, ttk.Combobox)
                            and "fill" in tuple(widget.cget("values")))
            # Do not update is now the pause, no longer a position.
            self.assertNotIn(app.WALLPAPER_POSITION_LABELS["none"], tuple(position.cget("values")))

            def paused():
                return context.root.getboolean(context.root.getvar(pause.cget("variable")))

            def select(label):
                context.variables["output_device"].set(label)
                combo.event_generate("<<ComboboxSelected>>")

            select("Display 2 (1050 × 1680)")
            context.variables["position"].set("center")
            self.assertFalse(paused())
            pause.invoke()
            select("Display 1 (2560 × 1440)")
            self.assertFalse(paused())
            select("Display 2 (1050 × 1680)")
            self.assertTrue(paused())
            self.assertEqual(context.variables["position"].get(), "center")

            saved = self.apply(context)
            self.assertEqual(json.loads(saved["windows"]["paused_displays"]), ["DISPLAY-SECOND"])
            self.assertEqual(json.loads(saved["windows"]["monitor_positions"]), {"DISPLAY-SECOND": "center"})

            pause.invoke()
            saved = self.apply(context)
            self.assertEqual(json.loads(saved["windows"]["paused_displays"]), [])
            self.assertEqual(json.loads(saved["windows"]["monitor_positions"]), {"DISPLAY-SECOND": "center"})
            self.assertEqual(context.errors, [])

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.run_dialog(scenario)

    def test_each_monitor_keeps_its_output_settings(self):
        from tkinter import ttk

        monitors = [
            {"id": "DISPLAY-FIRST", "rect": (0, 0, 2560, 1440)},
            {"id": "DISPLAY-SECOND", "rect": (2560, 0, 3610, 1680)},
        ]

        def scenario(context):
            general_id = next(tab for tab in context.notebook.tabs()
                              if context.notebook.tab(tab, "text") == "General")
            general = context.root.nametowidget(general_id)
            combo = next(widget for widget in self.descendants(general)
                         if isinstance(widget, ttk.Combobox)
                         and str(widget.cget("textvariable")) ==
                         str(context.variables["output_device"]))
            self.assertEqual(tuple(combo.cget("values")), (
                "Display 1 (2560 × 1440)", "Display 2 (1050 × 1680)",
            ))
            self.assertEqual(context.variables["output_device"].get(), "Display 1 (2560 × 1440)")
            general_labels = {widget.cget("text") for widget in self.descendants(general)
                              if isinstance(widget, ttk.Label)}
            self.assertIn("Display", general_labels)
            self.assertNotIn("Screen / monitor", general_labels)
            # Apply to all displays and Restore previous wallpaper: equally wide, below the dropdown.
            buttons = {widget.cget("text"): widget for widget in self.descendants(general)
                       if isinstance(widget, ttk.Button)}
            apply_all = buttons["Apply to all displays"]
            restore = buttons["Restore previous wallpaper"]
            self.assertIs(apply_all.master, restore.master)
            # The display's pause right below the dropdown, then both actions.
            pause = next(widget for widget in self.descendants(general)
                         if isinstance(widget, ttk.Checkbutton) and widget.cget("text") == "Pause wallpaper updates")
            self.assertEqual(int(pause.grid_info()["row"]), int(combo.grid_info()["row"]) + 1)
            self.assertEqual(int(apply_all.master.grid_info()["row"]), int(combo.grid_info()["row"]) + 2)
            self.assertEqual(int(apply_all.master.grid_info()["column"]), int(combo.grid_info()["column"]))
            self.assertEqual((int(apply_all.grid_info()["column"]), int(restore.grid_info()["column"])), (0, 1))
            context.notebook.select(general_id)
            context.root.update()
            self.assertEqual(apply_all.winfo_width(), restore.winfo_width())
            global_width = context.variables["width"].get()
            context.variables["width"].set("1920")
            context.variables["height"].set("0")
            context.variables["aspect_ratio"].set("16:9")
            context.variables["render_scale"].set("1.5")
            context.variables["background_color"].set("#102030")
            context.variables["position"].set("center")

            context.variables["output_device"].set("Display 2 (1050 × 1680)")
            combo.event_generate("<<ComboboxSelected>>")
            context.variables["width"].set("1200")
            context.variables["height"].set("0")
            context.variables["aspect_ratio"].set("5:7")
            context.variables["background_color"].set("#abcdef")
            context.variables["position"].set("none")

            context.variables["output_device"].set("Display 1 (2560 × 1440)")
            combo.event_generate("<<ComboboxSelected>>")
            self.assertEqual(context.variables["width"].get(), "1920")
            self.assertEqual(context.variables["render_scale"].get(), "1.5")
            self.assertEqual(context.variables["position"].get(), "center")
            # A profile holds no output size, whichever display is selected.
            self.assertNotIn("width", context.profiles._capture_settings()["output"])

            saved = self.apply(context)
            outputs = json.loads(saved["windows"]["monitor_output_settings"])
            positions = json.loads(saved["windows"]["monitor_positions"])
            self.assertEqual(outputs["DISPLAY-FIRST"], {
                "width": 1920, "height": 0, "aspect_ratio": "16:9",
                "render_scale": 1.5,
                "background_color": "#102030",
            })
            self.assertEqual(outputs["DISPLAY-SECOND"], {
                "width": 1200, "height": 0, "aspect_ratio": "5:7",
                "background_color": "#ABCDEF",
            })
            self.assertEqual(positions, {
                "DISPLAY-FIRST": "center", "DISPLAY-SECOND": "none",
            })
            # The shared settings stayed as they were.
            self.assertEqual(str(saved["output"]["width"]), global_width)

            # Apply to all displays: Display 1's settings become everyone's.
            apply_all.invoke()
            context.variables["output_device"].set("Display 2 (1050 × 1680)")
            combo.event_generate("<<ComboboxSelected>>")
            for name, value in (("width", "1920"), ("aspect_ratio", "16:9"), ("render_scale", "1.5"),
                                ("background_color", "#102030"), ("position", "center")):
                self.assertEqual(context.variables[name].get(), value, name)
            saved = self.apply(context)
            self.assertEqual((saved["output"]["width"], saved["output"]["aspect_ratio"],
                              saved["output"]["background_color"], saved["windows"]["position"]),
                             (1920, "16:9", "#102030", "center"))
            # The display render quality becomes the shared one like every other setting.
            self.assertEqual(json.loads(saved["windows"]["monitor_output_settings"]), {})
            self.assertEqual(saved["output"]["display_render_scale"], 1.5)
            self.assertEqual(json.loads(saved["windows"]["monitor_positions"]), {})
            self.assertEqual(context.errors, [])

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.run_dialog(scenario)

    def test_one_display_edits_the_shared_output_settings(self):
        from tkinter import ttk

        monitors = [{"id": "DISPLAY-ONLY", "rect": (0, 0, 2560, 1440)}]

        def scenario(context):
            self.assertEqual(context.variables["output_device"].get(), "Display 1 (2560 × 1440)")
            context.variables["width"].set("2560")
            context.variables["height"].set("0")
            context.variables["aspect_ratio"].set("16:9")
            context.variables["background_color"].set("#203040")
            context.variables["position"].set("center")
            saved = self.apply(context, tab="General")
            self.assertEqual((saved["output"]["width"], saved["output"]["background_color"],
                              saved["windows"]["position"]), (2560, "#203040", "center"))
            self.assertEqual(json.loads(saved["windows"]["monitor_positions"]), {})
            self.assertEqual(json.loads(saved["windows"]["monitor_output_settings"]), {})
            self.assertEqual(context.errors, [])

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.run_dialog(scenario)

    def test_without_identified_displays_all_displays_edits_the_shared_settings(self):
        from tkinter import ttk

        def scenario(context):
            general_id = next(tab for tab in context.notebook.tabs()
                              if context.notebook.tab(tab, "text") == "General")
            general = context.root.nametowidget(general_id)
            combo = next(widget for widget in self.descendants(general)
                         if isinstance(widget, ttk.Combobox)
                         and str(widget.cget("textvariable")) == str(context.variables["output_device"]))
            self.assertEqual(tuple(combo.cget("values")), ("All displays",))
            buttons = {widget.cget("text"): widget for widget in self.descendants(general)
                       if isinstance(widget, ttk.Button)}
            for text in ("Apply to all displays", "Restore previous wallpaper"):
                self.assertEqual(str(buttons[text].cget("state")), "disabled", text)
            context.variables["background_color"].set("#405060")
            saved = self.apply(context, tab="General")
            self.assertEqual(saved["output"]["background_color"], "#405060")

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=[]):
            self.run_dialog(scenario)

    def test_general_display_render_quality_is_the_default_for_eumetsat(self):
        from tkinter import ttk

        monitors = [{"id": "DISPLAY-FIRST", "rect": (0, 0, 2560, 1440)}]

        def scenario(context):
            tabs = {context.notebook.tab(tab, "text"): context.root.nametowidget(tab)
                    for tab in context.notebook.tabs()}

            def labels(tab):
                return {widget.cget("text"): widget for widget in self.descendants(tabs[tab])
                        if isinstance(widget, ttk.Label)}

            image_labels, general_labels = labels("Image"), labels("General")
            # One name in both tabs; General's row always shows; EUMETSAT starts on its default.
            self.assertIn("Render quality factor", image_labels)
            self.assertTrue(general_labels["Render quality factor"].grid_info())
            background = general_labels["Background color"]

            def quality_combo(tab, key):
                return next(widget for widget in self.descendants(tabs[tab])
                            if isinstance(widget, ttk.Combobox)
                            and str(widget.cget("textvariable")) == str(context.variables[key]))

            eumetsat_combo = quality_combo("Image", "eumetsat_render_quality_preset")
            general_combo = quality_combo("General", "render_quality_preset")
            presets = ("Auto (max useful)", "Standard (1.0×)", "High (1.25×)", "Very high (1.5×)", "Ultra (2.0×)")
            # Dropdowns only, with the same presets; EUMETSAT adds Default (General).
            self.assertEqual(tuple(general_combo.cget("values")), presets)
            self.assertEqual(tuple(eumetsat_combo.cget("values")), ("Default (General)", *presets))
            for tab in ("Image", "General"):
                entries = {str(widget.cget("textvariable")) for widget in self.descendants(tabs[tab])
                           if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox)}
                self.assertFalse(entries & {str(context.variables["render_scale"]),
                                            str(context.variables["eumetsat_render_scale"])}, tab)
            self.assertEqual(context.variables["eumetsat_render_quality_preset"].get(), "Default (General)")
            self.assertEqual(context.variables["eumetsat_render_scale"].get(), "default")
            self.assertEqual(context.variables["render_scale"].get(), "auto")

            # The two values are independent of each other.
            context.variables["eumetsat_render_scale"].set("1.5")
            self.assertEqual(context.variables["eumetsat_render_quality_preset"].get(), "Very high (1.5×)")
            self.assertEqual(context.variables["render_scale"].get(), "auto")
            context.variables["render_scale"].set("1.25")
            self.assertEqual(context.variables["eumetsat_render_scale"].get(), "1.5")
            # A factor set by hand in the file is listed while it is chosen.
            context.variables["eumetsat_render_scale"].set("1.75")
            self.assertEqual(context.variables["eumetsat_render_quality_preset"].get(), "Custom (1.75×)")
            self.assertEqual(eumetsat_combo.cget("values")[-1], "Custom (1.75×)")
            context.variables["eumetsat_render_quality_preset"].set("Default (General)")
            eumetsat_combo.event_generate("<<ComboboxSelected>>")
            self.assertEqual(context.variables["eumetsat_render_scale"].get(), "default")
            self.assertNotIn("Custom (1.75×)", eumetsat_combo.cget("values"))
            self.assertEqual(context.variables["render_scale"].get(), "1.25")

            # Changing the source never hides Monitor output rows.
            for provider in ("copernicus", "solar"):
                context.source.set_selection(provider, app.DEFAULT_SOURCE_PROFILES)
                context.root.update()
                self.assertTrue(background.grid_info(), provider)
            context.source.set_selection("eumetsat", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            saved = self.apply(context, tab="Image")
            self.assertEqual(saved["output"]["render_scale"], "default")
            saved = self.apply(context, tab="General")
            # One display: its render quality is General's shared one, not an override.
            self.assertEqual(saved["output"]["display_render_scale"], 1.25)
            self.assertEqual(json.loads(saved["windows"]["monitor_output_settings"]), {})
            self.assertEqual(saved["output"]["render_scale"], "default")
            # Like zoom, an Image > Rendering draft waits for Apply Image.
            context.variables["eumetsat_render_scale"].set("2")
            context.variables["background_color"].set("#102030")
            saved = self.apply(context, tab="General")
            self.assertEqual(saved["output"]["background_color"], "#102030")
            self.assertEqual(saved["output"]["render_scale"], "default")
            # Loaded as the worker loads it, EUMETSAT renders with General's value.
            with patch.object(app, "log"):
                app.load_configuration(context.config)
            self.assertEqual((app.get_render_scale_setting(), app.DISPLAY_RENDER_SCALE,
                              app.effective_render_scale_setting()), ("default", 1.25, 1.25))

        with patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.run_dialog(scenario)


if __name__ == "__main__":
    unittest.main()
