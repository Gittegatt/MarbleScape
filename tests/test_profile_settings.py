"""Hidden Tk tests for the profile draft editor; no user settings are written."""
from contextlib import ExitStack
from copy import deepcopy
import datetime as dt
import gc
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from marblescape_profile_settings import (
    ProfilesSettings, PROFILE_LIST_COLUMNS, PROFILE_LIST_COLUMN_LABELS, STATUS_COLUMN_WIDTH, profile_status_text, _profile_coverage, _profile_latitude, _profile_location,
    _profile_longitude, _profile_quarter_values, _profile_resolution, _profile_time,
)
from marblescape_copernicus import DEFAULT_PROFILE, rolling_quarter_start
from marblescape_profiles import normalize_library

FIRST, SECOND = "1" * 32, "2" * 32


class ProfileSettingsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.root.geometry("740x650")
        self.original = normalize_library({"items": [
            {"id": FIRST, "name": "Earth", "settings": {"source": {"provider": "eumetsat"}}},
            {"id": SECOND, "name": "Sun", "settings": {"source": {"provider": "solar"}}}],
            "rotation": {"order": [SECOND]}})
        self.original_copy = deepcopy(self.original)
        self.snapshot = {"source": {"provider": "goes_east"}, "view": {"fit_mode": "crop", "zoom": 1.2}}
        self.capture = Mock(return_value=self.snapshot)
        self.load = Mock()
        self.apply = Mock()
        self.status = Mock(return_value={
            "text": "Rotation is disabled.",
            "active_profile_id": SECOND,
            "profiles": {SECOND: {
                "source_time": "2026-09-13T10:20:00Z", "updated_at": "2026-09-13T10:21:00Z",
                "bytes": 2048, "width": 1920, "height": 1080,
            }},
            "wallpaper_position": "fit",
            "output_resolution": "3840 × 2160",
            "display_time_zone": "utc",
        })
        self.error_patch = patch("marblescape_profile_settings.messagebox.showerror")
        self.error = self.error_patch.start()
        self.confirm = self.enterContext(patch("marblescape_profile_settings.messagebox.askyesno", return_value=True))
        self.rename_prompt = self.enterContext(patch(
            "marblescape_profile_settings._ask_profile_name",
            return_value=None,
        ))
        self.create_prompt = self.enterContext(patch(
            "marblescape_profile_settings._ask_new_profile_name",
            return_value=None,
        ))
        self.info = self.enterContext(patch("marblescape_profile_settings.messagebox.showinfo"))
        self.preflight = self.enterContext(patch("marblescape_profile_settings.review_import_preflight", return_value=True))
        # Tk's clipboard is the real desktop clipboard even for withdrawn test windows.
        # Assert the requested writes without reading, clearing, or owning user data.
        self.clipboard_clear = self.enterContext(patch.object(tk.Misc, "clipboard_clear"))
        self.clipboard_append = self.enterContext(patch.object(tk.Misc, "clipboard_append"))
        self.enterContext(patch.object(tk.Misc, "clipboard_get",
                                      side_effect=AssertionError("Tests must not read the system clipboard.")))
        self.ui = ProfilesSettings(
            self.root, self.original, self.capture, self.load,
            on_apply=self.apply, status=self.status,
        )
        self.ui.reopen_column_menu = False  # No real menu on screen in tests.
        self.ui.frame.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.ui.close()
        self.root.destroy()
        self.ui = None
        gc.collect()
        self.error_patch.stop()

    def select(self, identifier):
        self.ui.tree.selection_set(identifier)
        self.root.update()

    def menu_state(self, label):
        # Greyed-out entries stay technically normal (no white shadow on Windows).
        return "normal" if self.ui.menu_entry_enabled(label) else "disabled"

    def enable_system_snapshot(self):
        import marblescape_snapshot as system
        value = {"kind": system.SYSTEM_KIND, "snapshot_id": "3" * 32, "profile_id": "4" * 32,
                 "generated_at_utc": "2026-09-27T10:00:00Z", "settings": deepcopy(self.snapshot)}
        self.ui._system_snapshot = lambda: value
        self.ui._snapshot = value
        self.ui._refresh(system.SYSTEM_ID)
        return system, value

    def test_snapshot_row_can_force_load_and_check_once_it_has_an_image(self):
        system, _value = self.enable_system_snapshot()
        force = Mock(return_value=([system.SYSTEM_ID], False))
        check = Mock(return_value=([], True))
        self.ui._on_force_load, self.ui._on_check = force, check
        self.ui._selection_changed()
        for label in ("Force loading new image", "Check for new image"):
            self.assertEqual(self.menu_state(label), "normal")
        self.ui.force_load_selected()
        force.assert_called_once_with([system.SYSTEM_ID])
        # Together with saved profiles the pinned row comes first.
        self.ui.tree.selection_set((FIRST, system.SYSTEM_ID))
        self.ui.check_selected()
        check.assert_called_once_with([system.SYSTEM_ID, FIRST])
        # Without an image the row has nothing to load.
        self.ui._snapshot = None
        self.select(system.SYSTEM_ID)
        self.ui._selection_changed()
        for label in ("Force loading new image", "Check for new image"):
            self.assertEqual(self.menu_state(label), "disabled")

    def test_slider_resolution_column_shows_the_visible_sector_size(self):
        import marblescape_slider as slider
        settings = {"source": {"provider": "slider"},
                    "sources": {"slider": {"area": "goes-19---conus", "resolution": "5000x5000"}}}
        self.assertEqual(_profile_resolution(settings), "5000 × 5000")
        with patch.dict(slider._CONTENT_BOXES, {"goes-19---conus": (0.0, 0.2, 1.0, 0.8)}):
            self.assertEqual(_profile_resolution(settings), "5000 × 3000")
            settings["sources"]["slider"]["resolution"] = "auto"
            self.assertEqual(_profile_resolution(settings), "Automatic")

    def test_history_column_shows_local_switch_including_snapshot_and_sorts(self):
        import marblescape_snapshot as system
        enabled = {SECOND: True, system.SYSTEM_ID: True}
        self.ui._history_enabled = lambda identifier: enabled.get(identifier, False)
        self.ui._system_snapshot = lambda: None
        self.ui._snapshot = None
        self.ui._refresh(FIRST)
        self.assertIn("history", self.ui.get_visible_columns())
        self.assertEqual(self.ui.tree.heading("history", "text"), "History")
        for column in ("history", "active"):
            self.assertEqual(str(self.ui.tree.column(column, "anchor")), "center")
        # The snapshot row shows no-profile History even before its first image.
        self.assertEqual(self.ui.tree.set(system.SYSTEM_ID, "history"), "☑")
        self.assertEqual(self.ui.tree.set(FIRST, "history"), "☐")
        self.assertEqual(self.ui.tree.set(SECOND, "history"), "☑")
        enabled.update({FIRST: True, SECOND: False, system.SYSTEM_ID: False})
        self.ui.refresh_history()
        self.assertEqual(self.ui.tree.set(FIRST, "history"), "☑")
        self.assertEqual(self.ui.tree.set(SECOND, "history"), "☐")
        self.assertEqual(self.ui.tree.set(system.SYSTEM_ID, "history"), "☐")
        self.ui.sort_by("history")
        self.assertEqual(self.ui.tree.get_children()[1:], (SECOND, FIRST))
        self.ui.sort_by("history")
        self.assertEqual(self.ui.tree.get_children()[1:], (FIRST, SECOND))
        # Visibility toggles like every other column.
        self.ui._column_visibility_vars["history"].set(False)
        self.ui._toggle_column("history")
        self.assertNotIn("history", self.ui.get_visible_columns())
        # The local switch is never part of the exported profile library.
        self.assertTrue(all("history" not in item and "history" not in item["settings"]
                            for item in self.ui.get_library()["items"]))

    def test_history_checkbox_click_switches_all_selected_rows(self):
        enabled = {SECOND: True}
        self.ui._history_enabled = lambda identifier: enabled.get(identifier, False)
        toggle = Mock(side_effect=lambda identifiers, value: enabled.update(dict.fromkeys(identifiers, value)))
        self.ui._on_history_toggle = toggle
        self.ui.refresh_history()
        self.ui.tree.selection_set((FIRST, SECOND))
        column = f"#{self.ui.get_visible_columns().index('history') + 1}"
        with patch.object(self.ui.tree, "identify_region", return_value="cell"), \
             patch.object(self.ui.tree, "identify_row", return_value=FIRST), \
             patch.object(self.ui.tree, "identify_column", return_value=column):
            self.assertEqual(self.ui._toggle_rotation_clicked(SimpleNamespace(x=5, y=5)), "break")
        # Not all were on, so all selected rows are switched on together.
        toggle.assert_called_once_with([SECOND, FIRST], True)
        self.assertEqual((self.ui.tree.set(FIRST, "history"), self.ui.tree.set(SECOND, "history")), ("☑", "☑"))
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})
        self.assertEqual(self.ui.last_saved_change, "History on for 2 profile(s).")
        # A failed save shows an error and leaves the table as it was.
        toggle.side_effect = OSError("locked")
        self.ui.toggle_history_selected()
        self.assertTrue(self.error.called)
        self.assertEqual(self.ui.tree.set(FIRST, "history"), "☑")
        # The rotation list is not touched.
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])

    def test_create_asks_for_the_name_and_suggests_the_image_tab_profile(self):
        self.ui._suggest_name = Mock(return_value="Bahamas (modified)")
        self.create_prompt.return_value = "Bahamas at dusk"
        self.ui.buttons["Create Profile from Image"].invoke()
        self.create_prompt.assert_called_once_with(self.root, "Bahamas (modified)")
        self.assertEqual(self.ui.get_library()["items"][-1]["name"], "Bahamas at dusk")
        self.assertEqual(self.ui.get_library()["items"][-1]["settings"], self.snapshot)
        # Cancelling creates nothing.
        before = self.ui.get_library()
        self.create_prompt.reset_mock(return_value=True, side_effect=True)
        self.create_prompt.return_value = None
        self.ui.add_current()
        self.assertEqual(self.ui.get_library(), before)
        # An invalid name is reported and asked for again, keeping what was typed.
        self.create_prompt.side_effect = ["x" * 81, None]
        self.ui.add_current()
        self.assertEqual(self.create_prompt.call_args_list[-1].args, (self.root, "x" * 81))
        self.assertTrue(self.error.called)
        self.assertEqual(self.ui.get_library(), before)
        # No Profile name field remains on the tab.
        self.assertFalse(hasattr(self.ui, "name_var"))

    def test_system_snapshot_cannot_be_edited_deleted_moved_or_rotated(self):
        before = self.ui.get_library()
        system, value = self.enable_system_snapshot()
        self.assertEqual(self.ui.tree.get_children()[0], system.SYSTEM_ID)
        self.assertEqual(self.ui.tree.set(system.SYSTEM_ID, "id"), value["snapshot_id"])
        self.assertEqual(self.ui.tree.set(system.SYSTEM_ID, "short_id"), value["snapshot_id"][:8])
        self.assertTrue(self.ui.buttons["Delete"].instate(["disabled"]))
        for label in ("Delete", "Update", "Rename", "Move up", "Move down"):
            self.assertEqual(self.menu_state(label), "disabled")
        self.assertEqual(self.ui.get_library(), before)
        self.ui.sort_by("name")
        self.assertEqual(self.ui.tree.get_children()[0], system.SYSTEM_ID)
        self.ui._select_all()
        self.ui.delete_selected()
        self.assertEqual(self.ui.tree.get_children(), (system.SYSTEM_ID,))
        self.assertIn("will be kept", self.confirm.call_args.args[1])
        self.assertEqual(self.ui.get_library()["items"], [])

    def test_system_snapshot_applies_and_duplicates_as_normal_profile(self):
        system, value = self.enable_system_snapshot()
        self.ui.apply_selected()
        self.apply.assert_called_once_with(value["settings"])
        self.ui.load_selected()
        self.load.assert_called_once_with(value["settings"])
        self.ui.duplicate_selected()
        copied = self.ui.get_library()["items"][0]
        self.assertEqual(copied["name"], system.SYSTEM_NAME + " (Copy)")
        self.assertNotEqual(copied["id"], value["profile_id"])
        self.assertNotIn("kind", copied)
        self.select(system.SYSTEM_ID)
        self.ui.duplicate_selected()
        self.assertEqual(self.ui.get_library()["items"][0]["name"], system.SYSTEM_NAME + " (Copy 1)")
        self.assertEqual(self.error.call_count, 0)

    def test_empty_system_snapshot_disables_transfer_and_shows_no_image(self):
        system, _value = self.enable_system_snapshot()
        self.ui._snapshot = None
        self.ui._refresh(system.SYSTEM_ID)
        self.assertEqual(self.ui.tree.set(system.SYSTEM_ID, "time"), "No image yet")
        for label in ("Export profile", "Delete"):
            self.assertTrue(self.ui.buttons[label].instate(["disabled"]))
        for label in ("Apply", "Load", "Export profile", "Delete", "Duplicate"):
            self.assertEqual(self.menu_state(label), "disabled")

    def test_profile_details_show_coverage_only_when_matching_image_exists(self):
        import marblescape_data_coverage as coverage
        from PIL import Image
        state = deepcopy(self.status())
        state["image_records"] = {FIRST: coverage.from_alpha(Image.new("RGBA", (2, 2), "black"))}
        self.ui._apply_runtime_status(state)
        self.select(FIRST)
        self.assertEqual(self.ui.detail_vars["data_coverage"].get(), "100.00%")
        state["image_records"] = {}
        self.ui._apply_runtime_status(state)
        # Without a recorded picture the row is hidden instead of showing "-".
        self.assertEqual(self.ui.detail_vars["data_coverage"].get(), "")
        self.assertFalse(any(widget.winfo_manager() for widget in self.ui._detail_rows["data_coverage"]))

    def test_selection_is_split_into_mission_product_and_layer_with_catalogue_names(self):
        names = {
            ("goes_east", "conus", None): {"label": "CONUS", "satellite": "G19"},
            ("goes_east", "GEOCOLOR", "conus"): {"label": "GeoColor"},
            ("slider", "gk2a---full_disk", None): {"label": "Full Disk", "category": "GEO-KOMPSAT-2A (128E)"},
            ("slider", "geocolor", "gk2a---full_disk"): {"label": "GeoColor"},
            ("eumetsat", "mtg_fd:rgb_geocolour", None): {"label": "GeoColour RGB - MTG - 0 degree"},
        }
        self.ui._catalogue_entry = lambda provider, item_id, area_id=None: names.get((provider, item_id, area_id))
        cases = (
            ({"source": {"provider": "goes_east"},
              "sources": {"goes_east": {"area": "conus", "product": "GEOCOLOR"}}},
             ("GOES-19", "GeoColor", "-")),
            # Without a cached catalogue the saved IDs show.
            ({"source": {"provider": "goes_west"},
              "sources": {"goes_west": {"area": "conus", "product": "GEOCOLOR"}}},
             ("GOES-West", "GEOCOLOR", "-")),
            ({"source": {"provider": "slider"},
              "sources": {"slider": {"area": "gk2a---full_disk", "product": "geocolor"}}},
             ("GEO-KOMPSAT-2A (128E)", "GeoColor", "-")),
            ({"source": {"provider": "himawari"},
              "sources": {"himawari": {"area": "nict_full_disk", "product": "true_color"}}},
             ("Himawari · NICT", "true_color", "-")),
            ({"source": {"provider": "eumetsat"},
              "sources": {"eumetsat": {"satellite": "MTG - 0 Degree", "mission": "MTG",
                                       "product_type": "RGB"}},
              "layers": [{"kind": "wms", "name": "mtg_fd:rgb_geocolour", "enabled": True}]},
             ("MTG - 0 Degree", "RGB", "GeoColour RGB - MTG - 0 degree")),
        )
        for settings, expected in cases:
            with self.subTest(provider=settings["source"]["provider"]):
                self.ui._row_entries = {}
                self.assertEqual(self.ui._selection_columns(settings), expected)
        self.assertEqual([self.ui.tree.heading(key, "text").rstrip(" ▲▼") for key in ("mission", "product", "layer")],
                         ["Satellite / mission", "Product", "Layer"])
        self.assertNotIn("selection", PROFILE_LIST_COLUMNS)

    def test_resolution_shows_the_newest_pictures_real_size(self):
        self.assertEqual(self.ui.tree.heading("output_resolution", "text").rstrip(" ▲▼"), "Resolution selection")
        self.assertEqual(self.ui.tree.heading("resolution", "text").rstrip(" ▲▼"), "Resolution")
        items = deepcopy(self.ui._items)
        first = next(item for item in items if item["id"] == FIRST)
        first["settings"] = {"source": {"provider": "copernicus"},
                             "sources": {"copernicus": dict(DEFAULT_PROFILE)}}
        state = deepcopy(self.status())
        state["image_records"] = {FIRST: {"width": 2987, "height": 1680, "source_resolution": "1x1"},
                                  SECOND: {"width": 3840, "height": 2160, "source_resolution": "4096x4096"}}
        self.ui._apply_runtime_status(state)
        self.ui._commit(items, FIRST)
        # Copernicus: the saved PNG; other sources: the downloaded source picture.
        self.assertEqual(self.ui.tree.set(FIRST, "resolution"), "2987 × 1680")
        self.assertEqual(self.ui.tree.set(SECOND, "resolution"), "4096 × 4096")
        # Pictures saved before the field existed show a dash.
        state["image_records"] = {SECOND: {"width": 3840, "height": 2160}}
        self.ui._apply_runtime_status(state)
        self.assertEqual(self.ui.tree.set(SECOND, "resolution"), "-")

    def test_auto_recommendation_columns_details_and_priority_colors(self):
        from marblescape_copernicus import get_product
        from marblescape_theme import palette
        scene = {**DEFAULT_PROFILE, "mission": "Sentinel-2", "product": "DEFAULT-THEME::a91f72",
                 "layer": "1_TRUE_COLOR", "auto_recommendation": True, "auto_priority": "newest",
                 "auto_precise": True}
        monthly = get_product("DEFAULT-THEME", "MARBLESCAPE::S1-IW-MONTHLY")
        mosaic = {**DEFAULT_PROFILE, "mission": "Sentinel-1 Mosaics", "product": monthly["id"],
                  "layer": monthly["layers"][0]["id"], "auto_recommendation": True,
                  "auto_priority": "full_coverage", "auto_precise": True}
        items = deepcopy(self.ui._items)
        first = next(item for item in items if item["id"] == FIRST)
        first["settings"] = {"source": {"provider": "copernicus"}, "sources": {"copernicus": scene}}
        items.append({"id": "5" * 32, "name": "Mosaic", "settings": {
            "source": {"provider": "copernicus"}, "sources": {"copernicus": mosaic}}})
        state = deepcopy(self.status())
        state["image_records"] = {FIRST: {"auto_recommendation": True, "auto_choice": "Max. cloud cover 100% · Fill gaps, 3 days"}}
        self.ui._apply_runtime_status(state)
        self.ui._commit(items, FIRST)
        self.root.update()
        columns = ("auto_recommendation", "auto_priority", "auto_precise", "auto_choice")
        self.assertEqual(tuple(self.ui.tree.set(FIRST, column) for column in columns),
                         ("Yes", "Newest", "Yes", "Max. cloud cover 100% · Fill gaps, 3 days"))
        # Mosaics have no precise check; without a picture there is no choice yet.
        self.assertEqual(tuple(self.ui.tree.set("5" * 32, column) for column in columns),
                         ("Yes", "Data coverage", "-", "-"))
        self.assertEqual(tuple(self.ui.tree.set(SECOND, column) for column in columns), ("-",) * 4)
        values = {key: self.ui.detail_vars[key].get() for key in columns}
        self.assertEqual(values, {"auto_recommendation": "Yes", "auto_priority": "Newest",
                                  "auto_precise": "Yes",
                                  "auto_choice": "Max. cloud cover 100% · Fill gaps, 3 days"})
        # The priority in its color: the details row and the table cell.
        colors = palette(self.ui.tree)
        self.assertEqual(str(self.ui._detail_rows["auto_priority"][1].cget("foreground")), colors["newest"])
        try:
            cells = self.ui.tree.tk.call(self.ui.tree, "tag", "cell", "has", "auto_priority_newest")
        except tk.TclError:
            self.skipTest("Tk without cell tags")
        self.assertEqual([tuple(map(str, cell)) for cell in cells], [(FIRST, "auto_priority")])
        self.assertEqual(str(self.ui.tree.tag_configure("auto_priority_success", "foreground")), colors["success"])
        self.select("5" * 32)
        self.assertEqual(self.ui.detail_vars["auto_precise"].get(), "")
        self.assertFalse(any(widget.winfo_manager() for widget in self.ui._detail_rows["auto_precise"]))
        self.assertEqual(str(self.ui._detail_rows["auto_priority"][1].cget("foreground")), colors["success"])

    def test_last_download_uses_published_status_not_cache_and_sorts(self):
        self.assertEqual(self.ui.tree.set(SECOND, "last_download"), "-")
        state = deepcopy(self.status.return_value)
        state["last_downloads"] = {SECOND: "2026-09-27T05:00:00Z", FIRST: "2026-09-26T05:00:00Z"}
        self.ui._apply_runtime_status(state)
        self.assertIn("2026-09-27", self.ui.tree.set(SECOND, "last_download"))
        self.ui.sort_by("last_download")
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))
        self.ui.sort_by("last_download")
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))

    def test_heading_drag_reorders_without_sort_and_hidden_columns_keep_position(self):
        with patch.object(self.ui.tree, "identify_region", return_value="heading"), \
             patch.object(self.ui.tree, "identify_column", side_effect=["#1", "#3"]):
            self.ui._heading_press(SimpleNamespace(x=10, y=10))
            self.ui._heading_motion(SimpleNamespace(x=300, y=10))
            self.ui._heading_release(SimpleNamespace(x=300, y=10))
        self.assertEqual(
            self.ui.get_column_order()[:5],
            ("name", "status_symbol", "active", "rotation_enabled", "history"),
        )
        self.assertEqual(self.ui.get_sort_settings()["sort_column"], "")
        self.ui._column_visibility_vars["name"].set(False)
        self.ui._toggle_column("name")
        self.ui._column_visibility_vars["name"].set(True)
        self.ui._toggle_column("name")
        self.assertEqual(
            self.ui.get_visible_columns()[:5],
            ("name", "status_symbol", "active", "rotation_enabled", "history"),
        )
        self.assertEqual(self.ui._cell_menu.type(self.ui._cell_menu.index("Columns")), "cascade")
        self.assertEqual(self.ui._column_menu.entrycget(self.ui._column_menu_index["last_download"], "label"),
                         "Last download")

    def test_columns_menu_lists_every_column_once_in_areas(self):
        from marblescape_profile_settings import COLUMN_MENU_GROUPS
        menu = self.ui._column_menu
        entries = [menu.entrycget(index, "label") if menu.type(index) != "separator" else "-"
                   for index in range(menu.index("end") + 1)]
        grouped = [column for group in COLUMN_MENU_GROUPS for column in group]
        self.assertEqual(sorted(grouped), sorted(PROFILE_LIST_COLUMNS))
        # One area after another, then the two resets.
        expected = []
        for group in COLUMN_MENU_GROUPS:
            expected += (["-"] if expected else []) + [PROFILE_LIST_COLUMN_LABELS[column] for column in group]
        self.assertEqual(entries, expected + ["-", "Reset sorting", "Reset columns"])
        # Moving a column in the table leaves the menu's areas as they are.
        self.ui._drop_column("cache_status", 0)
        self.assertEqual(menu.entrycget(self.ui._column_menu_index["cache_status"], "label"), "Cache status")
        self.assertEqual(menu.index("end"), len(entries) - 1)

    def test_grip_below_the_table_changes_its_height_in_whole_rows(self):
        layout = Mock()
        self.ui._on_layout_change = layout
        self.assertEqual(self.ui.get_table_rows(), 7)
        self.assertEqual(int(self.ui.tree.cget("height")), 7)
        grip = self.ui.table_grip
        self.assertEqual(str(grip.cget("cursor")), "sb_v_double_arrow")
        line = self.ui.table_grip_line
        self.assertEqual(str(line.cget("orient")), "horizontal")
        self.assertEqual(str(line.cget("cursor")), "sb_v_double_arrow")
        self.assertEqual(line.place_info()["relwidth"], "1")
        for widget in (grip, line):
            for sequence in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Double-1>"):
                self.assertTrue(widget.bind(sequence))
        frame_height = int(self.ui._list_frame.cget("height"))
        row = self.ui._row_pixels()

        def drag(*positions):
            self.ui._grip_press(SimpleNamespace(y_root=2000))
            for position in positions[:-1]:
                self.ui._grip_motion(SimpleNamespace(y_root=2000 + position))
            self.ui._grip_release(SimpleNamespace(y_root=2000 + positions[-1]))

        self.ui._grip_press(SimpleNamespace(y_root=2000))
        self.ui._grip_motion(SimpleNamespace(y_root=2000 + 3 * row + row // 3))
        self.assertEqual(self.ui.get_table_rows(), 10)
        self.assertEqual(int(self.ui._list_frame.cget("height")), frame_height + 3 * row)
        layout.assert_not_called()
        self.ui._grip_release(SimpleNamespace(y_root=2000 + 5 * row))
        self.assertEqual(self.ui.get_table_rows(), 12)
        self.assertEqual(int(self.ui.tree.cget("height")), 12)
        layout.assert_called_once_with()
        # Limited to 5..40 rows; a release without a change saves nothing.
        drag(-50 * row)
        self.assertEqual(self.ui.get_table_rows(), 5)
        drag(80 * row)
        self.assertEqual(self.ui.get_table_rows(), 40)
        self.assertEqual(layout.call_count, 3)
        drag(4 * row, row // 3)
        self.assertEqual(self.ui.get_table_rows(), 40)
        self.assertEqual(layout.call_count, 3)
        # Double-click restores the default height.
        self.ui._grip_reset()
        self.assertEqual(self.ui.get_table_rows(), 7)
        self.assertEqual(int(self.ui._list_frame.cget("height")), frame_height)
        self.assertEqual(layout.call_count, 4)
        self.ui._grip_reset()
        self.assertEqual(layout.call_count, 4)

    def test_saved_table_height_is_used_and_validated(self):
        ui = ProfilesSettings(self.root, self.original, self.capture, self.load, table_rows=15)
        try:
            self.assertEqual(int(ui.tree.cget("height")), 15)
        finally:
            ui.close()
            ui.frame.destroy()
        for value in (4, 41, "12", 12.0, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProfilesSettings(self.root, self.original, self.capture, self.load, table_rows=value)

    def test_every_view_change_saves_the_layout_at_once(self):
        layout = Mock()
        self.ui._on_layout_change = layout
        self.ui.sort_by("name")
        self.ui.reset_sort()
        self.ui._column_visibility_vars["source"].set(False)
        self.ui._toggle_column("source")
        self.ui._drop_column("name", 0)
        self.assertEqual(layout.call_count, 4)
        # A release without a width change saves nothing; a dragged border does.
        self.ui._check_column_widths()
        self.assertEqual(layout.call_count, 4)
        self.ui.tree.column("name", width=321)
        self.ui._check_column_widths()
        self.assertEqual(layout.call_count, 5)
        self.ui._check_column_widths()
        self.assertEqual(layout.call_count, 5)
        # Moving rows of a sorted view also clears the sorting.
        self.ui.sort_by("name")
        self.select(SECOND)
        self.ui.move_selected(-1)
        self.assertEqual(layout.call_count, 7)
        # A failing save is reported, never raised into Tk.
        layout.side_effect = OSError("locked")
        self.ui.sort_by("zoom")
        self.assertTrue(self.error.called)

    def test_heading_drag_within_same_column_is_noop(self):
        before = self.ui.get_column_order()
        # The Rotation column spans x 0-70 by default; both of its edges are its own place.
        for x in (30, 60):
            with self.subTest(x=x), \
                 patch.object(self.ui.tree, "identify_region", return_value="heading"), \
                 patch.object(self.ui.tree, "identify_column", return_value="#1"):
                self.ui._heading_press(SimpleNamespace(x=10, y=10))
                self.ui._heading_motion(SimpleNamespace(x=x, y=10))
                self.ui._heading_release(SimpleNamespace(x=x, y=10))
            self.assertEqual(self.ui.get_column_order(), before)
            self.assertEqual(self.ui.get_sort_settings()["sort_column"], "")

    def test_heading_drag_shows_a_line_scrolls_and_can_be_cancelled(self):
        tree = self.ui.tree
        with patch.object(tree, "identify_region", return_value="heading"), \
             patch.object(tree, "identify_column", return_value="#1"), \
             patch.object(tree, "winfo_width", return_value=400), \
             patch.object(tree, "xview", return_value=(0.0, 0.2)), \
             patch.object(tree, "xview_moveto") as scroll:
            self.ui._heading_press(SimpleNamespace(x=10, y=10))
            self.ui._heading_motion(SimpleNamespace(x=12, y=10))
            self.assertIsNone(self.ui._column_line)  # Not a drag yet.
            self.ui._heading_motion(SimpleNamespace(x=360, y=10))
            # Left half of History (353-398): the line sits on its left edge.
            self.assertEqual(int(self.ui._column_line.place_info()["x"]), 352)
            self.assertEqual(str(tree.cget("cursor")), "sb_h_double_arrow")
            scroll.assert_not_called()
            # Near the right edge the table scrolls on.
            self.ui._heading_motion(SimpleNamespace(x=395, y=10))
            scroll.assert_called_once()
            self.assertEqual(self.ui._escape_key(), "break")
            self.assertIsNone(self.ui._heading_release(SimpleNamespace(x=300, y=10)))
        self.assertEqual(self.ui._column_line.place_info(), {})
        self.assertEqual(str(tree.cget("cursor")), "")
        self.assertEqual(self.ui.get_column_order()[:4], ("rotation_enabled", "name", "status_symbol", "active"))
        # Past the last column a heading moves to the end.
        with patch.object(tree, "identify_region", return_value="heading"), \
             patch.object(tree, "identify_column", return_value="#1"):
            self.ui._heading_press(SimpleNamespace(x=10, y=10))
            self.ui._heading_motion(SimpleNamespace(x=100000, y=10))
            self.ui._heading_release(SimpleNamespace(x=100000, y=10))
        self.assertEqual(self.ui.get_visible_columns()[-1], "rotation_enabled")

    def test_columns_menu_contains_every_column_including_hidden(self):
        for column in PROFILE_LIST_COLUMNS:
            self.ui._column_visibility_vars[column].set(False)
            self.ui._toggle_column(column)
        self.assertEqual(self.ui.get_visible_columns(), ())
        checkbuttons = [index for index in range(self.ui._column_menu.index("end") + 1)
                        if self.ui._column_menu.type(index) == "checkbutton"]
        self.assertEqual(len(checkbuttons), len(PROFILE_LIST_COLUMNS))
        for column in PROFILE_LIST_COLUMNS:
            index = self.ui._column_menu_index[column]
            self.assertEqual(self.ui._column_menu.type(index), "checkbutton")
            self.ui._column_menu.invoke(index)
            self.assertIn(column, self.ui.get_visible_columns())

    def test_status_column_shows_active_and_the_running_download(self):
        def status(download, identifier=FIRST, active=SECOND):
            return profile_status_text(identifier, {"active_profile_id": active, "download": download})

        base = {"profile_id": FIRST, "source": "goes_east", "updating": False, "percent": None,
                "expected_requests": 1, "finished_requests": 0, "transferred": 0}
        self.assertEqual(status(None), "")
        self.assertEqual(status(None, SECOND), "ACTIVE")
        # A download carries the download symbol; Copernicus server rendering its own.
        self.assertEqual(status(base), "DOWNL")
        self.assertEqual(status(dict(base, percent=44.6)), "45%")
        self.assertEqual(status(dict(base, expected_requests=16, finished_requests=7)), "7/16")
        self.assertEqual(status(dict(base, expected_requests=None, transferred=3 * 1024 * 1024)),
                         "3.0 MiB")
        self.assertEqual(status(dict(base, source="copernicus", expected_requests=None)), "RENDER")
        self.assertEqual(status(dict(base, source="copernicus", expected_requests=None,
                                     transferred=2048)), "2.0 KiB")
        # The shown profile renews its image; the download wins over Active.
        self.assertEqual(status(dict(base, profile_id=SECOND, updating=True, percent=10), SECOND),
                         "10%")
        # Another profile's download leaves this row as it is.
        self.assertEqual(status(dict(base, profile_id=FIRST), SECOND), "ACTIVE")

        self.status.return_value = dict(self.status.return_value, download=dict(base, percent=45.0))
        self.ui._apply_runtime_status(self.status())
        self.assertEqual(self.ui.tree.set(FIRST, "active"), "45%")
        self.assertEqual(self.ui.tree.set(SECOND, "active"), "ACTIVE")
        self.assertEqual(self.ui.tree.item(SECOND, "tags"), ("active",))
        # The symbols stand in their own column left of Status.
        self.assertEqual((self.ui.tree.set(FIRST, "status_symbol"), self.ui.tree.set(SECOND, "status_symbol")),
                         ("⭳", "●"))

    def test_the_symbol_column_shows_what_runs_and_the_row_on_screen(self):
        from marblescape_profile_settings import profile_status
        identifier = "1" * 32
        download = {"profile_id": identifier, "source": "goes_east", "percent": None,
                    "expected_requests": 1, "transferred": 0}
        cases = (
            ({}, ("", "")),
            ({"active_profile_id": identifier}, ("●", "ACTIVE")),
            # A failure's symbol replaces the active one; Status still names ACTIVE.
            ({"active_profile_id": identifier, "failures": {identifier: "NETWORK"}}, ("↯", "ACTIVE · NETWORK")),
            ({"failures": {identifier: "LOST"}}, ("⊘", "LOST")),
            ({"failures": {identifier: "SOURCE"}}, ("☁", "SOURCE")),
            ({"failures": {identifier: "UNAVAIL"}}, ("⊖", "UNAVAIL")),
            ({"failures": {identifier: "NETWORK"}, "queued": (identifier,)}, ("⋯", "QUEUE")),
            ({"queued": (identifier,), "active_profile_id": identifier}, ("⋯", "QUEUE")),
            ({"checking": identifier, "queued": (identifier,)}, ("↻", "CHECK")),
            ({"download": download, "checking": identifier}, ("⭳", "DOWNL")),
            ({"download": dict(download, percent=45.0)}, ("⭳", "45%")),
            ({"download": dict(download, source="copernicus")}, ("⧉", "RENDER")),
        )
        for runtime, expected in cases:
            with self.subTest(runtime=runtime):
                self.assertEqual(profile_status(identifier, runtime), expected)
        # A narrow centred column with an empty heading, named "Status symbol" in the Columns menu.
        tree = self.ui.tree
        visible = self.ui.get_visible_columns()
        self.assertEqual(visible[visible.index("active") - 1], "status_symbol")
        self.assertEqual(tree.heading("status_symbol", "text"), "")
        self.assertEqual((int(tree.column("status_symbol", "width")), int(tree.column("status_symbol", "minwidth"))),
                         (28, 28))
        self.assertEqual(str(tree.column("status_symbol", "anchor")), "center")
        self.assertEqual(self.ui._column_menu.entrycget(self.ui._column_menu_index["status_symbol"], "label"),
                         "Status symbol")
        # Sorted by it, its heading shows only the arrow.
        self.ui.sort_by("status_symbol")
        self.assertEqual(tree.heading("status_symbol", "text"), " ▲")

    def test_status_column_can_be_narrower_than_its_text(self):
        ui = ProfilesSettings(self.root, self.original, self.capture, self.load,
                              on_apply=self.apply, status=self.status, column_widths={"active": 45})
        try:
            # A saved narrow width stays, like any column; the text is then cut.
            self.assertEqual(int(ui.tree.column("active", "width")), 45)
            self.assertEqual(int(ui.tree.column("active", "minwidth")), 45)
            from marblescape_profiles import CHECKBOX_COLUMN_MIN_WIDTH
            self.assertEqual(int(ui.tree.column("history", "minwidth")), CHECKBOX_COLUMN_MIN_WIDTH)
        finally:
            ui.close()

    def test_status_column_fits_the_longest_status_text(self):
        import tkinter as tk
        from tkinter import ttk
        import marblescape_theme as theme
        from marblescape_profiles import DEFAULT_PROFILE_COLUMN_WIDTHS

        texts = ("999.9 MiB", "RENDER", "CHECK", "DOWNL", "100%", "99/99", "QUEUE", "ACTIVE",
                 "ACTIVE · UNAVAIL", "ACTIVE · LOST", "UNAVAIL", "LOST",
                 "ACTIVE · NETWORK", "ACTIVE · SOURCE", "NETWORK", "SOURCE")
        # Measured in the table font of the default theme and of Sun Valley (larger).
        root = tk.Tk()
        try:
            root.withdraw()
            for appearance in ("default", "light"):
                if appearance != "default":
                    if theme.sv_ttk is None:
                        continue
                    theme.apply_appearance(root, appearance)
                font = ttk.Style(root).lookup("Treeview", "font") or "TkDefaultFont"
                longest = max(int(root.tk.call("font", "measure", font, text)) for text in texts)
                # Cell padding needs a few pixels on top of the text.
                self.assertLessEqual(longest + 10, STATUS_COLUMN_WIDTH, appearance)
        finally:
            root.destroy()
        # The default width (also after Reset columns) fits every status.
        self.assertEqual(DEFAULT_PROFILE_COLUMN_WIDTHS["active"], STATUS_COLUMN_WIDTH)

    def test_order_is_rotation_first_then_remaining_and_input_is_unchanged(self):
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])
        self.assertEqual(self.original, self.original_copy)
        self.assertIn("Solar", str(self.ui.tree.item(SECOND, "values")))
        self.assertEqual(self.ui.tree["columns"], PROFILE_LIST_COLUMNS)
        self.assertEqual(self.ui.tree.heading("gap_fill", "text"), "Gap fill")
        self.assertEqual(self.ui.tree.heading("cloud_coverage", "text"), "Max. cloud cover")
        self.assertEqual(self.ui.tree.heading("mosaic_brightness", "text"), "Brightness correction")
        self.assertEqual(self.ui.tree.heading("mosaic_contrast", "text"), "Contrast correction")
        # Without the slider beside them, the table keeps the full auto names.
        self.assertEqual(self.ui.tree.heading("auto_brightness", "text"), "Auto brightness")
        self.assertEqual(self.ui.tree.heading("auto_contrast", "text"), "Auto contrast")
        values = self.ui.tree.item(SECOND, "values")
        self.assertEqual(self.ui.tree.set(SECOND, "name"), "Sun")
        self.assertEqual(self.ui.tree.heading("active", "text"), "Status")
        self.assertEqual(self.ui.tree.set(SECOND, "active"), "ACTIVE")
        self.assertEqual(self.ui.tree.set(FIRST, "active"), "")
        self.assertEqual(self.ui.tree.set(SECOND, "time"), "2026-09-13 10:20 UTC")
        self.assertEqual(self.ui.tree.set(SECOND, "time_selection"), "Latest")
        self.assertEqual(self.ui.tree.set(SECOND, "rotation_enabled"), "☑")
        self.assertEqual(self.ui.tree.set(FIRST, "rotation_enabled"), "☐")
        self.assertEqual(self.ui.detail_vars["time_utc"].get(), "2026-09-13 10:20 UTC")

    def test_type_ahead_uses_profile_names_and_does_not_apply_or_copy(self):
        for item in self.ui._items:
            item["name"] = "Sunrise" if item["id"] == FIRST else "Sunset"
        self.ui.tree.focus(FIRST)
        def key(char, state=0):
            return self.ui._type_to_select(SimpleNamespace(char=char, keysym=char, state=state))
        with patch("marblescape_profile_settings.time.monotonic", return_value=10):
            key("s")
            self.assertEqual(self.ui.tree.selection(), (SECOND,))
            for char in "unr":
                key(char)
            self.assertEqual(self.ui.tree.selection(), (FIRST,))
            self.assertIsNone(key("a", 4))
            self.assertIsNone(key("a", 0x20000))  # Alt on Windows.
            self.assertEqual(self.ui.tree.selection(), (FIRST,))
        with patch("marblescape_profile_settings.time.monotonic", return_value=11):
            # Num Lock on (state 0x0008 on Windows) does not stop typing.
            for char in "suns":
                key(char, 0x0008)
            self.assertEqual(self.ui.tree.selection(), (SECOND,))
        self.ui.tree.focus(FIRST)
        with patch("marblescape_profile_settings.time.monotonic", return_value=12):
            key("s")
            self.assertEqual(self.ui.tree.selection(), (SECOND,))
            key("s")
            self.assertEqual(self.ui.tree.selection(), (FIRST,))
        self.apply.assert_not_called()
        self.load.assert_not_called()
        self.clipboard_clear.assert_not_called()
        self.clipboard_append.assert_not_called()
        self.assertTrue(self.ui.tree.bind("<KeyPress>"))
        self.assertFalse(self.root.bind("<KeyPress>"))

    def test_clicking_rotate_checkbox_toggles_all_selected_profiles(self):
        self.ui.tree.selection_set(FIRST, SECOND)
        self.root.update()
        event = SimpleNamespace(x=10, y=10)
        with patch.object(self.ui.tree, "identify_region", return_value="cell"), \
             patch.object(self.ui.tree, "identify_column", return_value="#1"), \
             patch.object(self.ui.tree, "identify_row", return_value=FIRST):
            self.assertEqual(self.ui._toggle_rotation_clicked(event), "break")
        self.assertEqual(set(self.ui.get_library()["rotation"]["order"]), {FIRST, SECOND})
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})

        with patch.object(self.ui.tree, "identify_region", return_value="cell"), \
             patch.object(self.ui.tree, "identify_column", return_value="#1"), \
             patch.object(self.ui.tree, "identify_row", return_value=FIRST):
            self.assertEqual(self.ui._toggle_rotation_clicked(event), "break")
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [])
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})

    def test_context_menu_puts_frequent_actions_first_and_the_rest_in_submenus(self):
        def entries(menu):
            return [menu.entrycget(index, "label") if menu.type(index) != "separator" else "-"
                    for index in range(menu.index("end") + 1)]

        self.assertEqual(entries(self.ui._cell_menu), [
            "Apply", "Load", "Update", "Check for new image", "Force loading new image", "-",
            "Edit", "-",
            "Copy cell", "Copy row", "-",
            "Toggle updates", "Toggle history", "Toggle rotation", "-", "Open profile history folder", "-",
            "Import / Export", "Columns",
        ])
        self.assertEqual(entries(self.ui._edit_menu), [
            "Rename", "Duplicate", "Delete", "-", "Move up", "Move down",
        ])
        self.assertEqual(entries(self.ui._transfer_menu), ["Import profile", "Export profile"])
        accelerators = {label: self.ui._menu_of[label].entrycget(label, "accelerator")
                        for label in ("Delete", "Copy row", "Move up", "Move down", "Rename")}
        self.assertEqual(accelerators, {"Delete": "Del", "Copy row": "Ctrl+C", "Move up": "Ctrl+Up",
                                        "Move down": "Ctrl+Down", "Rename": ""})
        # With one saved profile Edit offers its actions; with no selection only
        # Import stays, so Edit greys out while Import / Export stays available.
        self.select(FIRST)
        self.assertTrue(self.ui.menu_entry_enabled("Edit"))
        self.assertTrue(self.ui.menu_entry_enabled("Rename"))
        self.ui.tree.selection_set(())
        self.ui._context_item = None
        self.ui._selection_changed()
        self.assertFalse(self.ui.menu_entry_enabled("Edit"))
        self.assertTrue(self.ui.menu_entry_enabled("Import / Export"))

    def test_menu_arrows_and_check_marks_follow_the_text_color_of_the_mode(self):
        import marblescape_theme as theme
        if theme.sv_ttk is None:
            self.skipTest("sv-ttk is not installed")
        from tkinter import ttk

        menus = (self.ui._cell_menu, self.ui._edit_menu, self.ui._transfer_menu, self.ui._column_menu)
        for mode, color in (("dark", "#fafafa"), ("light", "#1c1c1c"), ("dark", "#fafafa")):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            self.assertEqual(str(ttk.Style(self.root).lookup(".", "foreground")), color)
            for menu in menus:
                # Tk draws a submenu's arrow in the entry's text color; check marks use selectcolor.
                self.assertEqual((str(menu.cget("foreground")), str(menu.cget("selectcolor"))),
                                 (color, color), (mode, str(menu)))
        # The Columns check marks are images in the text color, also after a mode change.
        import marblescape_theme as theme_module
        for mode in ("light", "dark"):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            unchecked, checked = theme_module.menu_check_images(self.root)
            first = self.ui._column_menu
            self.assertEqual(str(first.entrycget(0, "indicatoron")), "0")
            self.assertEqual((str(first.entrycget(0, "image")), str(first.entrycget(0, "selectimage"))),
                             (str(unchecked), str(checked)), mode)
            pixels = [checked.tk.call(str(checked), "get", x, y, "-withalpha")
                      for x in range(checked.width()) for y in range(checked.height())]
            opaque = {tuple(int(v) for v in pixel[:3]) for pixel in pixels if int(pixel[3]) > 200}
            color = "#fafafa" if mode == "dark" else "#1c1c1c"
            expected = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
            # The mark is drawn in the text color; smoothed edges vary by a few steps.
            self.assertTrue(opaque, mode)
            self.assertTrue(all(max(abs(a - b) for a, b in zip(pixel, expected)) <= 8 for pixel in opaque),
                            (mode, opaque))
        # A submenu entry available again takes the menu's color, not the grey.
        self.select(FIRST)
        self.ui.tree.selection_set(())
        self.ui._context_item = None
        self.ui._selection_changed()
        self.select(FIRST)
        self.assertEqual(str(self.ui._cell_menu.entrycget("Edit", "foreground")), "")

    def test_toggles_show_the_selected_rows_value_with_a_check_mark(self):
        menu = self.ui._cell_menu
        for label in ("Toggle updates", "Toggle history", "Toggle rotation"):
            self.assertEqual(menu.type(menu.index(label)), "checkbutton")
            self.assertEqual(str(menu.entrycget(label, "indicatoron")), "0")
        # Every entry keeps the same space for the mark, so the labels line up.
        self.assertTrue(str(menu.entrycget("Apply", "image")))
        self.select(FIRST)
        rotation = self.ui._toggle_vars["Toggle rotation"]
        before = rotation.get()
        self.ui.invoke_menu_entry("Toggle rotation")
        self.ui._selection_changed()
        self.assertEqual(rotation.get(), not before)
        updates = self.ui._toggle_vars["Toggle updates"]
        before = updates.get()
        self.ui.invoke_menu_entry("Toggle updates")
        self.ui._selection_changed()
        self.assertEqual(updates.get(), not before)
        from marblescape_profile_settings import _image_updates_enabled
        first = next(item for item in self.ui.get_library()["items"] if item["id"] == FIRST)
        self.assertEqual(_image_updates_enabled(first["settings"]), not before)

    def test_context_toggle_rotation_applies_to_all_selected_profiles(self):
        labels = [self.ui._cell_menu.entrycget(index, "label")
                  for index in range(self.ui._cell_menu.index("end") + 1)
                  if self.ui._cell_menu.type(index) in ("command", "checkbutton")]
        self.assertIn("Toggle rotation", labels)
        self.assertNotIn("Check", labels)
        self.assertNotIn("Uncheck", labels)
        self.ui.tree.selection_set(FIRST, SECOND)
        self.root.update()
        self.assertEqual(self.menu_state("Toggle rotation"), "normal")
        self.ui.invoke_menu_entry("Toggle rotation")
        self.assertEqual(set(self.ui.get_library()["rotation"]["order"]), {FIRST, SECOND})
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})
        self.ui.invoke_menu_entry("Toggle rotation")
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [])
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})

    def test_new_and_duplicated_profiles_start_excluded_from_rotation(self):
        self.ui.add_current("Brand new")
        self.select(SECOND)
        self.ui.duplicate_selected()
        library = self.ui.get_library()
        self.assertEqual({item["name"] for item in library["items"]},
                         {"Earth", "Sun", "Sun (Copy)", "Brand new"})
        self.assertEqual(library["rotation"]["order"], [SECOND])
        self.error.assert_not_called()

    def test_no_data_color_shows_the_applicable_copernicus_choice(self):
        settings = {"source": {"provider": "copernicus"},
                    "sources": {"copernicus": dict(DEFAULT_PROFILE, no_data_color="#123456",
                                                   scene_no_data_color="blur")}}
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(
            {"id": FIRST, "name": "Mosaic", "settings": settings})))
        self.assertEqual(values["no_data_color"], "#123456")
        # Regular layers show their own choice; Gap fill "black" saved earlier shows black.
        regular = settings["sources"]["copernicus"]
        regular.update(mission="Sentinel-2", product="DEFAULT-THEME::a91f72", layer="1_TRUE_COLOR")
        # Shown like the Image tab buttons: Blur and Transparent.
        self.assertEqual(self.ui._no_data_color(settings), "Blur")
        regular.pop("scene_no_data_color")
        self.assertEqual(self.ui._no_data_color(settings), "Transparent")
        regular["coverage_mode"] = "black"
        self.assertEqual(self.ui._no_data_color(settings), "#000000")
        self.assertEqual(self.ui._no_data_color(self.snapshot), "-")

    def test_profile_rows_show_location_and_coverage_by_source(self):
        copernicus = {"source": {"provider": "copernicus"}, "sources": {"copernicus": {
            "configuration": "DEFAULT-THEME", "mission": "Sentinel-2", "product": "DEFAULT-THEME::a91f72", "layer": "1_TRUE_COLOR",
            "latitude": 19.60508, "longitude": -155.43457,
            "coverage_mode": "single", "lookback_days": 90,
            "max_cloud_cover": 35, "brightness": 125,
        }}}
        item = {"id": FIRST, "name": "Hawaii", "settings": copernicus}
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual(tuple(values[key] for key in ("location", "latitude", "longitude", "gap_fill",
                                                       "cloud_coverage", "mosaic_brightness")),
                         ("Custom Lat/Long", "19.60508", "-155.43457",
                          "Single latest acquisition", "35%", "125%"))
        # A layer without a tone rule shows no brightness correction.
        without_rule = deepcopy(copernicus)
        without_rule["sources"]["copernicus"]["layer"] = "2_FALSE_COLOR"
        self.assertEqual(dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(
            {"id": FIRST, "name": "False color", "settings": without_rule})))["mosaic_brightness"], "-")
        self.assertEqual(_profile_latitude(copernicus), "19.60508")
        self.assertEqual(_profile_longitude(copernicus), "-155.43457")
        # Gap fill "black" is now Single latest acquisition with a black No-data color.
        copernicus["sources"]["copernicus"]["coverage_mode"] = "black"
        self.assertEqual(_profile_coverage(copernicus), "Single latest acquisition")
        copernicus["sources"]["copernicus"]["coverage_mode"] = "fill_gaps"
        self.assertEqual(_profile_coverage(copernicus), "Gap fill · 90 days")

        eumetsat = {"source": {"provider": "eumetsat"},
                    "sources": {"eumetsat": {"fill_gaps": True, "gap_fill_lookback_hours": 24}},
                    "view": {"preset": "europe"}}
        self.assertEqual((_profile_location(eumetsat), _profile_coverage(eumetsat)),
                         ("Europe", "Gap fill · 24 h"))
        eumetsat["sources"]["eumetsat"]["fill_gaps"] = False
        self.assertEqual(_profile_coverage(eumetsat), "-")
        goes = {"source": {"provider": "goes_west"},
                "sources": {"goes_west": {"area": "gwas"}}}
        self.assertEqual((_profile_location(goes), _profile_coverage(goes)), ("gwas", "-"))

    def test_quarter_values_show_saved_mode_offset_and_current_target(self):
        profile = dict(DEFAULT_PROFILE, date_mode="relative_quarter", quarter_offset=1)
        settings = {"source": {"provider": "copernicus"},
                    "sources": {"copernicus": profile}}
        target = rolling_quarter_start(dt.datetime.now(dt.timezone.utc).date(), 1)
        expected = f"{target.year} Q{(target.month - 1) // 3 + 1}"
        self.assertEqual(_profile_quarter_values(settings, "utc"),
                         ("Relative to now", "1 quarter ago", expected))
        item = {"id": FIRST, "name": "Rolling", "settings": settings}
        self.ui._runtime["display_time_zone"] = "utc"
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual(tuple(values[key] for key in ("quarter_mode", "quarter_offset", "quarter_target")),
                         ("Relative to now", "1 quarter ago", expected))
        self.assertEqual(self.ui._detail_values(item)["quarter_selection"],
                         f"Relative to now · 1 quarter ago · {expected}")
        profile.update(date_mode="catalogue", quarter_offset=0, date="2026-01-01")
        self.assertEqual(_profile_quarter_values(settings, "utc"),
                         ("Specific quarter", "-", "2026 Q1"))

    def test_rows_and_cells_can_be_copied_and_columns_can_be_hidden(self):
        self.select(SECOND)
        self.ui._context_item = SECOND
        self.ui._context_column = "source"
        self.ui._copy_context_cell()
        self.clipboard_clear.assert_called_once_with()
        self.clipboard_append.assert_called_once_with("Solar / Sun")

        self.ui._copy_selected_row()
        self.assertEqual(self.clipboard_clear.call_count, 2)
        copied = self.clipboard_append.call_args.args[0].split("\t")
        self.assertEqual(tuple(copied), self.ui.tree.item(SECOND, "values"))

        self.ui._column_visibility_vars["source"].set(False)
        self.ui._toggle_column("source")
        self.assertNotIn("source", self.ui.get_visible_columns())
        self.assertEqual(tuple(self.ui.tree["displaycolumns"]), self.ui.get_visible_columns())

        for column in tuple(self.ui.get_visible_columns())[1:]:
            self.ui._column_visibility_vars[column].set(False)
            self.ui._toggle_column(column)
        last_column = self.ui.get_visible_columns()[0]
        self.ui._column_visibility_vars[last_column].set(False)
        self.ui._toggle_column(last_column)
        self.assertEqual(self.ui.get_visible_columns(), ())
        self.assertFalse(self.ui._column_visibility_vars[last_column].get())
        with patch.object(self.ui._column_menu, "tk_popup") as popup:
            self.ui.columns_button.invoke()
        popup.assert_called_once()
        self.ui._column_menu.invoke("Profile name")
        self.assertEqual(self.ui.get_visible_columns(), ("name",))

    def test_selection_sort_refresh_and_column_changes_do_not_copy(self):
        self.select(SECOND)
        self.ui._select_all()
        self.ui.sort_by("name")
        self.ui._refresh(FIRST)
        self.ui._apply_runtime_status(self.status())
        self.ui._column_visibility_vars["name"].set(False)
        self.ui._toggle_column("name")
        self.ui.tree.column("source", width=300)
        self.root.update()
        self.clipboard_clear.assert_not_called()
        self.clipboard_append.assert_not_called()

    def test_new_columns_show_saved_values_and_live_cache_metadata(self):
        settings = {"source": {"provider": "copernicus"}, "sources": {"copernicus": {
            "map_zoom": 12, "lookback_days": 21, "map_labels": True,
        }}, "output": {"render_scale": 1.5}}
        item = {"id": SECOND, "name": "Example", "settings": settings}
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual(values["zoom"], "12")
        self.assertEqual(values["maximum_lookback"], "21 days")
        # Resolution is the profile's own choice; the output size is global.
        self.assertEqual(values["output_resolution"], "Automatic")
        settings["sources"]["copernicus"]["image_size"] = "3840x2160"
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual(values["output_resolution"], "3840 × 2160")
        # An older selection drew its country borders like its (then black) labels.
        self.assertEqual((values["map_labels"], values["map_borders"]), ("On · #000000", "On · #000000"))
        settings["sources"]["copernicus"].update(map_label_color="#FFFFFF", map_borders=False)
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual((values["map_labels"], values["map_borders"]), ("On · #FFFFFF", "Off"))
        self.assertEqual(values["cache_status"], self.ui._detail_values(item)["cache"])
        self.assertIn("Cached · 1920 × 1080", values["cache_status"])
        with patch("marblescape_profile_settings.get_product", return_value={"id": "mosaic"}), \
             patch("marblescape_profile_settings.get_layer", return_value={"date_granularity": "quarter"}):
            self.assertEqual(dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))["maximum_lookback"], "-")
        settings.update(source={"provider": "eumetsat"}, sources={"eumetsat": {
            "gap_fill_lookback_hours": 48}}, view={"zoom": 1.2})
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual((values["zoom"], values["maximum_lookback"], values["map_labels"]), ("1.2", "48 h", "-"))
        self.assertEqual(values["output_resolution"], "Render quality 1.5×")
        settings.update(source={"provider": "goes_west"}, sources={"goes_west": {"resolution": "largest"}})
        values = dict(zip(PROFILE_LIST_COLUMNS, self.ui._row_values(item)))
        self.assertEqual(values["output_resolution"], "Largest available")

    def test_heading_sort_preserves_selection_rotation_and_active_name(self):
        self.ui._commit([{"id": FIRST, "name": "Profile 10", "settings": {}},
                         {"id": SECOND, "name": "Profile 2", "settings": {}}], FIRST)
        self.ui._select_all()
        before = self.ui.get_library()
        self.root.tk.call(self.ui.tree.heading("name", "command"))
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})
        self.assertEqual(self.ui.get_library(), before)
        self.assertTrue(self.ui.tree.heading("name", "text").endswith("▲"))
        self.root.tk.call(self.ui.tree.heading("name", "command"))
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))
        self.assertTrue(self.ui.tree.heading("name", "text").endswith("▼"))
        self.ui._commit([{"id": FIRST, "name": "Profile 1", "settings": {}},
                         {"id": SECOND, "name": "Profile 2", "settings": {}}], FIRST)
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.ui._column_menu.invoke("Reset sorting")
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))
        self.assertEqual(self.ui.get_sort_settings(), {"sort_column": "", "sort_descending": False})

    def test_switching_a_column_opens_the_columns_menu_again_at_that_entry(self):
        self.ui.reopen_column_menu = True
        with patch.object(self.ui, "_reopen_column_menu") as reopen:
            self.ui._column_menu.invoke(self.ui._column_menu_index["zoom"])
            self.root.update()
        reopen.assert_called_once_with(self.ui._column_menu_index["zoom"])
        self.assertIn("zoom", self.ui.get_visible_columns())

    def test_columns_menu_opens_again_at_its_former_corner(self):
        with patch.object(self.ui._column_menu, "tk_popup") as popup,                 patch.object(self.ui.tree, "winfo_pointerxy", return_value=(500, 300)):
            # Without a known corner, with the entry at the pointer.
            self.ui._reopen_column_menu(4)
            popup.assert_called_with(500, 300, 4)
            with patch("marblescape_profile_settings._native_menu_corner", return_value=(420, 120)):
                self.ui._remember_column_menu_corner()
            self.ui._reopen_column_menu(4)
            popup.assert_called_with(420, 120)
            # A pointer outside a menu keeps the remembered corner.
            with patch("marblescape_profile_settings._native_menu_corner", return_value=None):
                self.ui._remember_column_menu_corner()
            self.ui._reopen_column_menu(5)
            popup.assert_called_with(420, 120)

    def test_reset_columns_restores_default_columns_order_and_widths_after_confirmation(self):
        from marblescape_profiles import DEFAULT_PROFILE_COLUMN_WIDTHS, DEFAULT_VISIBLE_PROFILE_COLUMNS
        layout_saves = Mock()
        self.ui._on_layout_change = layout_saves
        self.ui._column_visibility_vars["zoom"].set(True)
        self.ui._toggle_column("zoom")
        self.ui._drop_column("source", 0)
        self.ui.tree.column("name", width=400)
        self.ui.sort_by("zoom")
        expected = tuple(column for column in PROFILE_LIST_COLUMNS if column in DEFAULT_VISIBLE_PROFILE_COLUMNS)
        # Cancelled: nothing changes.
        self.confirm.return_value = False
        self.ui._column_menu.invoke("Reset columns")
        self.assertIn("zoom", self.ui.get_visible_columns())
        self.confirm.return_value = True
        layout_saves.reset_mock()
        self.ui._column_menu.invoke("Reset columns")
        self.assertEqual(self.ui.get_visible_columns(), expected)
        self.assertEqual(tuple(self.ui.tree.cget("displaycolumns")), expected)
        self.assertEqual(self.ui.get_column_order(), PROFILE_LIST_COLUMNS)
        self.assertEqual(self.ui.get_column_widths()["name"], DEFAULT_PROFILE_COLUMN_WIDTHS["name"])
        self.assertFalse(self.ui._column_visibility_vars["zoom"].get())
        self.assertTrue(self.ui._column_visibility_vars["mission"].get())
        # Sorting is its own reset; the new layout is saved at once.
        self.assertEqual(self.ui.get_sort_settings()["sort_column"], "zoom")
        layout_saves.assert_called_once_with()

    def test_numeric_columns_sort_values_not_formatted_strings_and_missing_last(self):
        third = "3" * 32
        def item(identifier, value):
            return {"id": identifier, "name": identifier, "settings": {
                "source": {"provider": "copernicus"}, "sources": {"copernicus": {
                    "configuration": "DEFAULT-THEME", "mission": "Sentinel-2", "product": "DEFAULT-THEME::a91f72", "layer": "1_TRUE_COLOR",
                    "latitude": -value, "longitude": value, "max_cloud_cover": value,
                    "brightness": value, "map_zoom": value, "lookback_days": value,
                    "map_labels": value == 10, "image_size": f"{value * 100}x100"}}}}
        self.ui._commit([item(FIRST, 10), {"id": third, "name": "Missing", "settings": {
            "source": {"provider": "solar"}}}, item(SECOND, 2)], FIRST)
        for column in ("latitude", "longitude", "cloud_coverage", "mosaic_brightness", "zoom",
                       "maximum_lookback", "output_resolution", "map_labels"):
            with self.subTest(column=column):
                self.ui.sort_by(column)
                ascending = (FIRST, SECOND, third) if column == "latitude" else (SECOND, FIRST, third)
                self.assertEqual(self.ui.tree.get_children(), ascending)
                self.ui.sort_by(column)
                self.assertEqual(self.ui.tree.get_children(), (ascending[1], ascending[0], third))

    def test_lookback_units_and_resolution_pixel_counts_are_compared(self):
        first = {"id": FIRST, "name": "Hours", "settings": {
            "source": {"provider": "eumetsat"}, "sources": {"eumetsat": {"gap_fill_lookback_hours": 48}}}}
        second = {"id": SECOND, "name": "Days", "settings": {
            "source": {"provider": "copernicus"}, "sources": {"copernicus": {"lookback_days": 3}}}}
        self.ui._commit([second, first], FIRST)
        self.ui.sort_by("maximum_lookback")
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))
        first = {"id": FIRST, "name": "Wide", "settings": {
            "source": {"provider": "goes_east"}, "sources": {"goes_east": {"resolution": "1500x500"}}}}
        second = {"id": SECOND, "name": "Square", "settings": {
            "source": {"provider": "copernicus"}, "sources": {"copernicus": {"image_size": "1000x1000"}}}}
        self.ui._commit([second, first], FIRST)
        self.ui.sort_by("output_resolution")
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))

    def test_every_heading_sorts_both_ways_including_hidden_columns(self):
        for column in PROFILE_LIST_COLUMNS:
            with self.subTest(column=column):
                self.ui._column_visibility_vars[column].set(False)
                self.ui._toggle_column(column)
                self.ui.sort_by(column)
                self.assertFalse(self.ui.get_sort_settings()["sort_descending"])
                self.ui.sort_by(column)
                self.assertTrue(self.ui.get_sort_settings()["sort_descending"])
                self.assertEqual(set(self.ui.tree.get_children()), {FIRST, SECOND})
        self.assertEqual(self.ui.get_visible_columns(), ())

    def test_runtime_refresh_reorders_cached_rows_without_changing_selection(self):
        self.ui.sort_by("cache_status")
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))
        self.select(SECOND)
        status = self.status()
        status["profiles"] = {FIRST: status["profiles"][SECOND]}
        self.ui._apply_runtime_status(status)
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.assertEqual(self.ui.tree.selection(), (SECOND,))
        self.assertEqual(self.ui.tree.set(SECOND, "cache_status"), "Not cached")

    def test_move_follows_the_visible_order_and_clears_sorting(self):
        self.ui.sort_by("name")  # On screen: Earth (FIRST), Sun (SECOND).
        self.select(SECOND)
        # Already last on screen: nothing moves and the sorting stays.
        self.ui.move_selected(1)
        self.assertEqual(self.ui.get_sort_settings()["sort_column"], "name")
        self.assertEqual(self.menu_state("Move down"), "disabled")
        self.ui.move_selected(-1)
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])
        self.assertEqual(self.ui.get_sort_settings()["sort_column"], "")

    def four_profiles(self):
        ids = tuple(letter * 32 for letter in "abcd")
        self.ui._commit([{"id": identifier, "name": identifier[0].upper(), "settings": {}}
                         for identifier in ids], ids[0])
        self.ui._on_save = Mock()
        self.root.update()
        return ids

    def test_ctrl_arrows_move_the_selected_rows_as_a_block(self):
        a, b, c, d = self.four_profiles()
        self.assertTrue(self.ui.tree.bind("<Control-Up>"))
        self.assertTrue(self.ui.tree.bind("<Control-Down>"))
        self.ui.tree.selection_set((b, c))
        self.assertEqual(self.ui._move_key(-1), "break")
        self.assertEqual(self.ui.tree.get_children(), (b, c, a, d))
        self.assertEqual(set(self.ui.tree.selection()), {b, c})
        self.assertEqual(self.ui.last_saved_change, "Moved 2 profiles up.")
        self.assertEqual([item["id"] for item in self.ui._on_save.call_args.args[0]["items"]], [b, c, a, d])
        # At the top nothing moves and nothing is saved.
        self.ui._move_key(-1)
        self.assertEqual(self.ui._on_save.call_count, 1)
        self.ui._move_key(1)
        self.ui._move_key(1)
        self.assertEqual(self.ui.tree.get_children(), (a, d, b, c))
        self.select(a)
        self.ui._move_key(1)
        self.assertEqual(self.ui.last_saved_change, "Moved 'A' down.")

    def drag_patches(self, rows, column="#2"):
        """Rows are 20 px high; `rows` maps each id to its top edge."""
        def identify_row(y):
            return next((identifier for identifier, top in rows.items() if top <= y < top + 20), "")
        tree = self.ui.tree
        return (patch.object(tree, "identify_region", return_value="cell"),
                patch.object(tree, "identify_row", side_effect=identify_row),
                patch.object(tree, "identify_column", return_value=column),
                patch.object(tree, "bbox", side_effect=lambda item, column=None:
                             (0, rows[item], 300, 20) if item in rows else ""))

    def test_dragging_rows_shows_a_line_and_drops_the_selection_as_a_block(self):
        a, b, c, d = self.four_profiles()
        self.ui.tree.selection_set((a, b))
        rows = {a: 0, b: 20, c: 40, d: 60}
        with ExitStack() as stack:
            for context in self.drag_patches(rows):
                stack.enter_context(context)
            # Pressing a row of a multiple selection keeps that selection.
            self.assertEqual(self.ui._row_press(SimpleNamespace(x=50, y=5, state=0)), "break")
            self.assertIsNone(self.ui._row_motion(SimpleNamespace(x=50, y=8)))
            self.assertIsNone(self.ui._drop_line)  # A few pixels are not a drag yet.
            self.assertEqual(self.ui._row_motion(SimpleNamespace(x=50, y=52)), "break")
            # The lower half of C puts the rows after C; the line sits on D's top edge.
            self.assertEqual(self.ui._row_drag["target"], 3)
            self.assertEqual(int(self.ui._drop_line.place_info()["y"]), 59)
            self.assertEqual(self.ui._row_release(SimpleNamespace(x=50, y=52)), "break")
        self.assertEqual(self.ui.tree.get_children(), (c, a, b, d))
        self.assertEqual(set(self.ui.tree.selection()), {a, b})
        self.assertEqual(self.ui.last_saved_change, "Moved 2 profiles to position 2.")
        self.assertEqual(self.ui._drop_line.place_info(), {})
        self.assertEqual(str(self.ui.tree.cget("cursor")), "")
        self.ui._on_save.assert_called_once()

    def test_row_drag_click_escape_and_checkbox_behave_like_before(self):
        a, b, c, d = self.four_profiles()
        rows = {a: 0, b: 20, c: 40, d: 60}
        with ExitStack() as stack:
            for context in self.drag_patches(rows):
                stack.enter_context(context)
            # Escape cancels; the later release neither moves nor toggles anything.
            self.ui.tree.selection_set((a, b))
            self.ui._row_press(SimpleNamespace(x=50, y=5, state=0))
            self.ui._row_motion(SimpleNamespace(x=50, y=70))
            self.assertEqual(self.ui._escape_key(), "break")
            self.assertEqual(self.ui._row_release(SimpleNamespace(x=50, y=70)), "break")
            self.assertEqual(self.ui.tree.get_children(), (a, b, c, d))
            # A plain click (no movement) on a selected row selects only that row.
            self.ui._row_press(SimpleNamespace(x=50, y=25, state=0))
            self.assertIsNone(self.ui._row_release(SimpleNamespace(x=50, y=25)))
            self.assertEqual(self.ui.tree.selection(), (b,))
            # Ctrl/Shift clicks are left to normal selection handling.
            self.assertIsNone(self.ui._row_press(SimpleNamespace(x=50, y=5, state=0x0004)))
            self.assertIsNone(self.ui._row_drag)
        self.ui._on_save.assert_not_called()
        # A click on the Rotation checkbox of a selected row keeps the selection and toggles all.
        with ExitStack() as stack:
            for context in self.drag_patches(rows, column="#1"):
                stack.enter_context(context)
            self.ui.tree.selection_set((a, b))
            self.ui._row_press(SimpleNamespace(x=5, y=5, state=0))
            self.ui._row_release(SimpleNamespace(x=5, y=5))
            self.assertEqual(self.ui._toggle_rotation_clicked(SimpleNamespace(x=5, y=5)), "break")
        self.assertEqual(set(self.ui.tree.selection()), {a, b})
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [a, b])

    def test_pinned_snapshot_row_is_neither_dragged_nor_passed(self):
        system, _value = self.enable_system_snapshot()
        a, b, c, d = self.four_profiles()
        self.ui.tree.selection_set((c,))
        rows = {system.SYSTEM_ID: 0, a: 20, b: 40, c: 60, d: 80}
        with ExitStack() as stack:
            for context in self.drag_patches(rows):
                stack.enter_context(context)
            self.assertIsNone(self.ui._row_press(SimpleNamespace(x=50, y=5, state=0)))
            self.assertIsNone(self.ui._row_drag)
            self.ui._row_press(SimpleNamespace(x=50, y=65, state=0))
            self.ui._row_motion(SimpleNamespace(x=50, y=5))
            self.assertEqual(self.ui._row_drag["target"], 0)
            self.ui._row_release(SimpleNamespace(x=50, y=5))
        self.assertEqual(self.ui.tree.get_children(), (system.SYSTEM_ID, c, a, b, d))

    def test_filter_shows_matching_profile_names_and_clear_resets_it(self):
        from tkinter import ttk
        a, b, c, d = self.four_profiles()
        self.ui._commit([{"id": a, "name": "Earth day", "settings": {"source": {"provider": "solar"}}},
                         {"id": b, "name": "Sun", "settings": {}},
                         {"id": c, "name": "EARTH night", "settings": {}},
                         {"id": d, "name": "Moon", "settings": {}}], a)
        header = self.ui.filter_entry.master
        self.assertEqual([widget.cget("text") for widget in header.winfo_children()
                          if isinstance(widget, (ttk.Label, ttk.Button))],
                         ["Filter", "Clear", "Columns"])
        self.ui.tree.selection_set((a, b))
        self.ui.filter_var.set("earth")
        self.assertEqual(self.ui.tree.get_children(), (a, c))
        # Hidden rows leave the selection, so no action can reach them.
        self.assertEqual(self.ui.tree.selection(), (a,))
        # Only the Profile name counts, not other columns such as Source.
        self.ui.filter_var.set("solar")
        self.assertEqual(self.ui.tree.get_children(), ())
        self.ui.filter_var.set("  moon ")
        self.assertEqual(self.ui.tree.get_children(), (d,))
        # Row updates while filtered keep the hidden rows intact.
        self.ui._refresh(d)
        self.assertEqual(self.ui.tree.get_children(), (d,))
        self.ui.filter_clear_button.invoke()
        self.assertEqual(self.ui.filter_var.get(), "")
        self.assertEqual(self.ui.tree.get_children(), (a, b, c, d))
        # The filter is never part of the saved profile list.
        self.ui._on_save.assert_not_called()

    def test_filter_ends_at_half_the_header_at_the_minimum_window_width(self):
        width = ProfilesSettings.filter_field_width
        # Header 860 px in a 900 px (minimum) window: Filter, field and Clear fill 430 px.
        self.assertEqual(width(860, 900, 900, 30, 60), 430 - 30 - 6 - 60 - 6)
        # A wider window keeps the same field width.
        self.assertEqual(width(1260, 1300, 900, 30, 60), width(860, 900, 900, 30, 60))
        self.assertEqual(width(100, 900, 900, 30, 60), 80)
        header = self.ui.filter_entry.master
        self.assertEqual(self.ui.filter_entry.grid_info()["sticky"], "ew")
        self.assertEqual(int(header.columnconfigure(2)["weight"]), 0)

    def test_moving_filtered_rows_keeps_hidden_rows_in_place(self):
        a, b, c, d = self.four_profiles()
        self.ui._commit([{"id": a, "name": "Earth 1", "settings": {}}, {"id": b, "name": "Sun", "settings": {}},
                         {"id": c, "name": "Earth 2", "settings": {}}, {"id": d, "name": "Moon", "settings": {}}], a)
        self.ui.filter_var.set("earth")
        self.ui.tree.selection_set((c,))
        self.ui.move_selected(-1)
        self.assertEqual(self.ui.tree.get_children(), (c, a))
        self.ui.filter_var.set("")
        # Earth 2 took Earth 1's slot; Sun and Moon did not move.
        self.assertEqual(self.ui.tree.get_children(), (c, b, a, d))
        self.assertEqual([item["id"] for item in self.ui._on_save.call_args.args[0]["items"]], [c, b, a, d])

    def test_saved_table_preferences_are_restored_on_new_editor(self):
        restored = ProfilesSettings(self.root, self.original, self.capture, self.load,
                                   visible_columns=[], sort_column="name", sort_descending=True)
        try:
            self.assertEqual(restored.get_visible_columns(), ())
            self.assertEqual(restored.tree.get_children(), (SECOND, FIRST))
            self.assertEqual(restored.get_sort_settings(), {"sort_column": "name", "sort_descending": True})
        finally:
            restored.close()
            restored.frame.destroy()

    def test_time_sort_uses_chronology_across_fixed_latest_and_time_zones(self):
        self.ui._commit([{"id": FIRST, "name": "Fixed", "settings": {
            "source": {"provider": "worldview"}, "sources": {"worldview": {"product": "2026-09-13"}}}},
            {"id": SECOND, "name": "Latest", "settings": {"source": {"provider": "solar"}}}], FIRST)
        self.ui._runtime["profiles"][SECOND]["source_time"] = "2026-09-13T01:00:00+02:00"
        self.ui.sort_by("time")
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.ui.sort_by("time")
        self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))

    def test_period_sort_uses_month_chronology_and_numeric_offsets(self):
        with patch("marblescape_profile_settings.get_product", return_value={"id": "mosaic"}), \
             patch("marblescape_profile_settings.get_layer", return_value={"date_granularity": "quarter"}):
            self.ui._commit([{"id": identifier, "name": identifier, "settings": {
                "source": {"provider": "copernicus"}, "sources": {"copernicus": {
                    "date_mode": "relative_quarter", "quarter_offset": offset}}}}
                for identifier, offset in ((FIRST, 10), (SECOND, 2))], FIRST)
            self.ui.sort_by("quarter_offset")
            self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
            self.ui.sort_by("quarter_target")
            self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))
            self.ui.sort_by("time")
            self.assertEqual(self.ui.tree.get_children(), (FIRST, SECOND))

    def test_manual_column_width_survives_refresh_and_layout_changes(self):
        self.ui.tree.column("name", width=237)
        self.ui._refresh(SECOND)
        self.root.geometry("900x650")
        self.root.update()
        self.assertEqual(self.ui.tree.column("name", "width"), 237)
        self.assertTrue(all(
            not bool(self.ui.tree.column(column, "stretch"))
            for column in self.ui.tree["columns"]
        ))

    def test_all_column_widths_restore_even_when_hidden_and_do_not_change_profiles(self):
        before = self.ui.get_library()
        widths = {column: 220 + index * 7 for index, column in enumerate(PROFILE_LIST_COLUMNS)}
        for column, width in widths.items():
            self.ui.tree.column(column, width=width)
            self.ui._column_visibility_vars[column].set(False)
            self.ui._toggle_column(column)
        self.assertEqual(self.ui.get_column_widths(), widths)
        self.assertEqual(self.ui.get_library(), before)
        restored = ProfilesSettings(self.root, before, self.capture, self.load,
                                   visible_columns=[], column_widths=widths)
        try:
            self.assertEqual(restored.get_column_widths(), widths)
            for column in PROFILE_LIST_COLUMNS:
                restored._column_visibility_vars[column].set(True)
                restored._toggle_column(column)
            restored.sort_by("name")
            restored._refresh(FIRST)
            restored._apply_runtime_status(self.status())
            self.root.update_idletasks()
            self.assertEqual(restored.get_column_widths(), widths)
            widths["name"] = 333  # No mutable connection to the constructor's input.
            self.assertEqual(restored.get_column_widths()["name"], 227)
        finally:
            restored.close()
            restored.frame.destroy()

    def test_partial_width_preferences_keep_other_defaults(self):
        from marblescape_profiles import DEFAULT_PROFILE_COLUMN_WIDTHS
        self.assertEqual(tuple(DEFAULT_PROFILE_COLUMN_WIDTHS), PROFILE_LIST_COLUMNS)
        restored = ProfilesSettings(self.root, self.original, self.capture, self.load,
                                   column_widths={"name": 327, "id": 400})
        try:
            self.assertEqual(restored.get_column_widths(), dict(DEFAULT_PROFILE_COLUMN_WIDTHS, name=327, id=400))
        finally:
            restored.close()
            restored.frame.destroy()

    def test_eight_buttons_share_one_row_with_transfer_at_right(self):
        self.root.update_idletasks()
        self.assertEqual(set(self.ui.buttons), {"Create Profile from Image", "Apply", "Load", "Update", "Rename",
                                                "Delete", "Import profile", "Export profile"})
        created, renamed = self.ui.buttons["Create Profile from Image"], self.ui.buttons["Rename"]
        applied, updated = self.ui.buttons["Apply"], self.ui.buttons["Update"]
        deleted = self.ui.buttons["Delete"]
        imported, exported = self.ui.buttons["Import profile"], self.ui.buttons["Export profile"]
        self.assertEqual({button.winfo_y() for button in self.ui.buttons.values()}, {created.winfo_y()})
        # Apply, Load, Update, Rename and Delete follow Create in this order, equally wide.
        edit = [applied, self.ui.buttons["Load"], updated, renamed, deleted]
        self.assertLess(created.winfo_x(), applied.winfo_x())
        self.assertEqual([int(button.grid_info()["column"]) for button in edit], [1, 2, 3, 4, 5])
        self.assertEqual(len({button.winfo_width() for button in edit}), 1)
        self.assertLess(deleted.winfo_x() + deleted.winfo_width(), imported.winfo_x())
        self.assertLess(imported.winfo_x(), exported.winfo_x())
        self.assertEqual(imported.winfo_width(), exported.winfo_width())
        # Every label fits: each button is at least as wide as its text needs.
        for button in self.ui.buttons.values():
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), button.cget("text"))
        self.assertLessEqual(exported.master.winfo_width() - exported.winfo_x() - exported.winfo_width(), 4)
        # Update and Rename need exactly one saved profile; Apply one loadable row.
        self.select(FIRST)
        for button in (updated, renamed):
            self.assertFalse(button.instate(["disabled"]), button.cget("text"))
        self.assertEqual(applied.instate(["disabled"]), self.ui._on_apply is None)
        self.ui.tree.selection_set((FIRST, SECOND))
        self.ui._selection_changed()
        for button in (applied, updated, renamed):
            self.assertTrue(button.instate(["disabled"]), button.cget("text"))
        self.assertFalse(self.ui.buttons["Export profile"].instate(["disabled"]))
        # Other single-profile actions are in the context menu.
        for label in ("Apply", "Load", "Update", "Rename"):
            self.assertEqual(self.menu_state(label), "disabled")

    def test_rename_button_renames_the_selected_profile(self):
        self.ui._on_save = Mock()
        self.select(FIRST)
        self.rename_prompt.return_value = "Terra"
        self.ui.buttons["Rename"].invoke()
        self.assertEqual(next(item["name"] for item in self.ui.get_library()["items"] if item["id"] == FIRST),
                         "Terra")
        self.assertEqual(self.ui.last_saved_change, "Renamed 'Earth' to 'Terra'.")

    def test_profile_transfer_buttons_export_and_import_multiple_rows_as_drafts(self):
        import marblescape_download as app
        self.ui._normalize_settings = app.normalize_image_settings_snapshot
        self.ui._import_defaults = app.default_import_settings
        settings = app.default_import_settings()
        self.ui._commit([
            {"id": FIRST, "name": "First", "settings": settings},
            {"id": SECOND, "name": "Second", "settings": deepcopy(settings)},
        ], FIRST)
        self.ui.tree.selection_set((FIRST, SECOND))
        self.ui._selection_changed()
        with tempfile.TemporaryDirectory() as folder:
            with patch("marblescape_profile_settings.filedialog.askdirectory", return_value=folder):
                self.ui.buttons["Export profile"].invoke()
            paths = tuple(str(p) for p in sorted(Path(folder).glob("*.json")))
            self.assertEqual(len(paths), 2)
            with patch("marblescape_profile_settings.filedialog.askopenfilenames", return_value=paths), \
                 patch("marblescape_profile_settings.confirm_transfer_conflict", return_value="rename"):
                self.ui.buttons["Import profile"].invoke()
        self.assertEqual([item["name"] for item in self.ui.get_library()["items"]],
                         ["First", "Second", "First (Copy)", "Second (Copy)"])
        self.assertEqual(len(self.ui.tree.selection()), 2)
        self.assertEqual(self.info.call_args.args[1], "Imported/replaced 2 profile(s).")
        self.apply.assert_not_called()
        self.error.assert_not_called()

    def test_single_import_message_uses_actual_name_after_conflict_resolution(self):
        import marblescape_download as app
        from marblescape_profile_transfer import export_profiles
        self.ui._normalize_settings = app.normalize_image_settings_snapshot
        self.ui._import_defaults = app.default_import_settings
        self.ui._commit([])
        name = "Whitsunday Islands, Australien"
        item = {"id": FIRST, "name": name, "settings": app.default_import_settings()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Unrelated filename.json"
            export_profiles(directory, [item], self.ui._normalize_settings, destination=path)
            for decision, expected_name in ((None, name), ("rename", name + " (Copy)"),
                                            ("rename", name + " (Copy 1)"), ("overwrite", name)):
                with self.subTest(decision=decision, expected=expected_name), \
                     patch("marblescape_profile_settings.filedialog.askopenfilenames", return_value=(str(path),)), \
                     patch("marblescape_profile_settings.confirm_transfer_conflict", return_value=decision):
                    self.ui.buttons["Import profile"].invoke()
                self.assertEqual(self.info.call_args.args[0], "Profile import")
                self.assertEqual(self.info.call_args.args[1],
                                 f"Imported/replaced 1 profile(s).\n\nProfile: {expected_name}")
                identifier = self.ui.tree.selection()[0]
                saved = next(entry for entry in self.ui.get_library()["items"] if entry["id"] == identifier)
                self.assertEqual(saved["name"], expected_name)
        self.error.assert_not_called()
        self.apply.assert_not_called()

    def test_import_overwrite_keeps_rotation_membership_and_new_profiles_start_excluded(self):
        import marblescape_download as app
        from marblescape_profile_transfer import export_profiles
        self.ui._normalize_settings = app.normalize_image_settings_snapshot
        self.ui._import_defaults = app.default_import_settings
        third = "3" * 32
        settings = app.default_import_settings()
        items = [{"id": FIRST, "name": "Included", "settings": deepcopy(settings)},
                 {"id": SECOND, "name": "Excluded", "settings": deepcopy(settings)}]
        self.ui._rotation_ids = {FIRST}
        self.ui._commit(deepcopy(items))
        incoming = items + [{"id": third, "name": "New", "settings": deepcopy(settings)}]
        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(map(str, export_profiles(directory, incoming, self.ui._normalize_settings)))
            with patch("marblescape_profile_settings.filedialog.askopenfilenames", return_value=paths), \
                 patch("marblescape_profile_settings.confirm_transfer_conflict", return_value="overwrite"):
                self.ui.import_selected()
        library = self.ui.get_library()
        self.assertEqual([item["id"] for item in library["items"]], [FIRST, SECOND, third])
        self.assertEqual(library["rotation"]["order"], [FIRST])
        self.assertEqual(self.info.call_args.args[1], "Imported/replaced 3 profile(s).")
        self.assertEqual(self.ui.last_saved_change, "Imported/replaced 3 profile(s).")
        self.error.assert_not_called()

    def test_invisible_characters_neither_rename_nor_hide_an_import_name_conflict(self):
        import json
        import marblescape_download as app
        from marblescape_profile_transfer import FORMAT
        self.select(FIRST)
        before = self.ui.get_library()
        self.confirm.reset_mock()
        self.rename_prompt.return_value = "Earth​"
        self.ui.rename_selected_from_dialog()
        # Only invisible characters differ, so there is nothing to rename or confirm.
        self.confirm.assert_not_called()
        self.assertEqual(self.ui.get_library(), before)
        self.ui._normalize_settings = app.normalize_image_settings_snapshot
        self.ui._import_defaults = app.default_import_settings
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps({"format": FORMAT, "version": 1, "profiles": [
                {"id": "3" * 32, "name": "Earth​", "settings": app.default_import_settings()}]}), encoding="utf-8")
            with patch("marblescape_profile_settings.filedialog.askopenfilenames", return_value=(str(path),)), \
                 patch("marblescape_profile_settings.confirm_transfer_conflict",
                       side_effect=AssertionError("A different UUID is not a conflict.")), \
                 patch("marblescape_profile_settings.confirm_import_repair", return_value="yes_all"):
                self.ui.import_selected()
        names = [item["name"] for item in self.ui.get_library()["items"]]
        self.assertIn("Earth (Imported)", names)
        self.assertNotIn("Earth​", names)
        self.error.assert_not_called()

    def test_partial_batch_import_keeps_count_summary_when_only_one_succeeds(self):
        import marblescape_download as app
        from marblescape_profile_transfer import export_profiles
        self.ui._normalize_settings = app.normalize_image_settings_snapshot
        self.ui._import_defaults = app.default_import_settings
        self.ui._commit([])
        item = {"id": FIRST, "name": "Whitsunday Islands, Australien", "settings": app.default_import_settings()}
        with tempfile.TemporaryDirectory() as directory:
            paths = export_profiles(directory, [item], self.ui._normalize_settings)
            paths.append(Path(directory) / "missing.json")
            with patch("marblescape_profile_settings.filedialog.askopenfilenames", return_value=tuple(map(str, paths))):
                self.ui.import_selected()
        summary = self.info.call_args.args[1]
        self.assertTrue(summary.startswith("Imported/replaced 1 profile(s).\n\nSkipped or could not import:\n"))
        self.assertNotIn("Profile:", summary)
        self.assertIn("missing.json", summary)
        self.error.assert_not_called()

    def test_duplicate_has_new_uuid_copy_suffix_and_no_shared_settings(self):
        self.select(FIRST)
        self.ui.duplicate_selected()
        first_copy = self.ui.get_library()["items"][-1]
        self.assertEqual(first_copy["name"], "Earth (Copy)")
        self.assertNotEqual(first_copy["id"], FIRST)
        self.select(FIRST)
        self.ui.duplicate_selected()
        items = self.ui.get_library()["items"]
        self.assertEqual(items[2]["name"], "Earth (Copy 1)")
        self.assertEqual(len({item["id"] for item in items}), 4)
        self.assertEqual(self.ui.tree.set(FIRST, "id"), FIRST)
        self.assertEqual(self.ui.tree.set(FIRST, "short_id"), FIRST[:8])
        self.assertEqual(self.ui.tree.heading("short_id", "text").rstrip(" ▲▼"), "Short ID")
        self.ui._items[2]["settings"]["source"]["provider"] = "solar"
        self.assertEqual(self.ui._items[1]["settings"]["source"]["provider"], "eumetsat")

    def test_multiselection_menu_preserves_selection_and_disables_single_actions(self):
        self.ui._select_all()
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})
        with patch.object(self.ui.tree, "identify_region", return_value="cell"), \
             patch.object(self.ui.tree, "identify_row", return_value=FIRST), \
             patch.object(self.ui.tree, "identify_column", return_value="#1"), \
             patch.object(self.ui._cell_menu, "tk_popup"):
            self.ui._show_tree_menu(SimpleNamespace(x=1, y=1, x_root=1, y_root=1))
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})
        for label in ("Apply", "Update", "Rename", "Duplicate", "Move up", "Move down"):
            self.assertEqual(self.menu_state(label), "disabled")
        self.assertEqual(self.menu_state("Delete"), "normal")
        self.assertEqual(self.menu_state("Export profile"), "normal")
        before = self.ui.get_library()
        self.ui.duplicate_selected()
        self.assertEqual(self.ui.get_library(), before)

    def test_update_and_delete_confirmation_applies_to_button_key_and_menu(self):
        self.select(FIRST)
        before = self.ui.get_library()
        self.confirm.return_value = False
        self.ui.buttons["Delete"].invoke()
        self.ui._delete_key()
        self.ui.invoke_menu_entry("Update")
        self.ui.invoke_menu_entry("Delete")
        self.assertEqual(self.confirm.call_count, 4)
        self.assertEqual(self.ui.get_library(), before)
        self.capture.assert_not_called()

    def test_add_and_update_capture_current_image_as_independent_draft(self):
        self.create_prompt.return_value = "Americas"
        self.ui.buttons["Create Profile from Image"].invoke()
        self.root.update()
        library = self.ui.get_library()
        added = library["items"][-1]
        self.assertEqual(added["name"], "Americas")
        self.assertEqual(added["settings"], self.snapshot)
        self.snapshot["view"]["zoom"] = 1.8
        self.assertEqual(self.ui.get_library()["items"][-1]["settings"]["view"]["zoom"], 1.2)
        self.select(added["id"])
        self.ui.invoke_menu_entry("Update")
        self.assertEqual(self.ui.get_library()["items"][-1]["settings"]["view"]["zoom"], 1.8)
        self.assertEqual(self.original, self.original_copy)
        self.error.assert_not_called()

    def test_invalid_name_or_capture_does_not_mutate_draft(self):
        before = self.ui.get_library()
        for name in ("", "x" * 81):
            self.ui.add_current(name)
            self.assertEqual(self.ui.get_library(), before)
        self.capture.assert_not_called()
        self.capture.side_effect = ValueError("Current image is invalid")
        self.ui.add_current("Valid name")
        self.assertEqual(self.ui.get_library(), before)
        self.assertTrue(self.error.called)
        self.assertEqual(self.error.call_args.kwargs["parent"], self.root)

    def test_rename_move_and_delete_are_reversible_draft_changes(self):
        self.select(FIRST)
        self.rename_prompt.return_value = "Earth renamed"
        self.ui.rename_selected_from_dialog()
        self.ui.move_selected(-1)
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])
        self.assertEqual(self.ui.get_library()["items"][0]["name"], "Earth renamed")
        self.ui.move_selected(-1)
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])
        self.ui.delete_selected()
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])
        self.assertEqual(self.original, self.original_copy)
        self.error.assert_not_called()

    def test_context_menu_rename_uses_wide_prompt_and_confirmation(self):
        self.select(FIRST)
        self.rename_prompt.return_value = "Earth via context menu"
        self.ui.invoke_menu_entry("Rename")
        self.rename_prompt.assert_called_once_with(self.root, "Earth")
        self.assertEqual(self.ui.get_library()["items"][1]["name"], "Earth via context menu")
        self.assertIn("Overwrite profile name 'Earth' with 'Earth via context menu'?",
                      self.confirm.call_args.args[1])

    def test_profile_name_dialog_is_wide(self):
        from tkinter import ttk
        import marblescape_profile_settings as profile_ui
        dialog = object.__new__(profile_ui._ProfileNameDialog)
        dialog._initial_value = "A long profile name"
        dialog._prompt = "New profile name:"
        host = ttk.Frame(self.root)
        try:
            entry = dialog.body(host)
            self.assertEqual(int(entry.cget("width")), 52)
            self.assertEqual(entry.get(), "A long profile name")
        finally:
            host.destroy()

    def test_history_folder_is_renamed_with_the_saved_profile(self):
        rename = Mock()
        self.ui._on_profile_rename = rename
        save = Mock()
        self.ui._on_save = save
        self.select(FIRST)
        self.rename_prompt.return_value = "Earth renamed"
        self.ui.rename_selected_from_dialog()
        save.assert_called_once()
        rename.assert_called_once_with(FIRST, "Earth", "Earth renamed")
        self.assertEqual(self.ui.commit_history_renames(), [])
        rename.assert_called_once()

    def test_delete_offers_history_images_and_removes_them_at_once(self):
        usage = {FIRST: {"files": 2, "bytes": 3 * 1024 * 1024}, SECOND: {"files": 1, "bytes": 512}}
        self.ui._history_usage = lambda identifier: usage[identifier]
        remove = Mock()
        self.ui._on_history_delete = remove
        save = Mock()
        self.ui._on_save = save
        self.ui._select_all()
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=True) as confirm:
            self.ui.delete_selected()
        # Multi-selection sums every selected profile's images.
        self.assertEqual(confirm.call_args.args[3], "Also delete History images (3 images, 3.0 MiB)")
        # Without other files there is nothing extra to warn about.
        self.assertEqual(confirm.call_args.args[4], "")
        self.confirm.assert_not_called()
        self.assertEqual(save.call_args.args[0]["items"], [])
        self.assertEqual(self.ui.last_saved_change, "Deleted 2 profile(s) and their History folders.")
        self.assertEqual(sorted(call.args[0] for call in remove.call_args_list), [FIRST, SECOND])
        self.assertEqual(self.ui.commit_history_deletions(), [])
        self.assertEqual(remove.call_count, 2)

    def test_delete_without_history_opt_in_or_when_cancelled_keeps_images(self):
        self.ui._history_usage = lambda identifier: {"files": 1, "bytes": 10}
        remove = Mock()
        self.ui._on_history_delete = remove
        self.select(FIRST)
        before = self.ui.get_library()
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=None):
            self.ui.delete_selected()
        self.assertEqual(self.ui.get_library(), before)
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=False) as confirm:
            self.ui.delete_selected()
        self.assertEqual(confirm.call_args.args[3], "Also delete History images (1 image, 10 B)")
        self.assertEqual([item["id"] for item in self.ui.get_library()["items"]], [SECOND])
        self.assertEqual(self.ui.commit_history_deletions(), [])
        remove.assert_not_called()

    def test_delete_dialog_warns_that_other_files_go_with_the_history_folder(self):
        usage = {FIRST: {"files": 0, "bytes": 0, "other_files": 1, "other_bytes": 2048},
                 SECOND: {"files": 1, "bytes": 10, "other_files": 2, "other_bytes": 1024}}
        self.ui._history_usage = lambda identifier: usage[identifier]
        self.select(FIRST)
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=None) as confirm:
            self.ui.delete_selected()
        # Other files alone are enough to offer deleting the folder.
        self.assertEqual(confirm.call_args.args[3], "Also delete History images (0 images, 0 B)")
        self.assertEqual(confirm.call_args.args[4],
                         "The History folder also contains 1 other file (2.0 KiB) not created by "
                         "MarbleScape. They are deleted together with the folder.")
        self.ui._select_all()
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=None) as confirm:
            self.ui.delete_selected()
        self.assertEqual(confirm.call_args.args[3], "Also delete History images (1 image, 10 B)")
        self.assertEqual(confirm.call_args.args[4],
                         "The History folders also contain 3 other files (3.0 KiB) not created by "
                         "MarbleScape. They are deleted together with the folder.")

    def test_every_profile_list_change_is_saved_immediately(self):
        save = Mock()
        self.ui._on_save = save
        saved = Mock()
        self.ui._on_saved = saved
        self.root.update()
        self.assertEqual(self.ui.notice_label.winfo_manager(), "")

        def expect(message, count):
            self.assertEqual(save.call_count, count)
            self.assertEqual(save.call_args.args[0], self.ui.get_library())
            self.assertEqual(self.ui.last_saved_change, message)
            # Each saved change is confirmed only in the Settings footer.
            self.assertEqual(saved.call_count, count)
            self.assertEqual(self.ui.notice_label.winfo_manager(), "")

        self.ui.add_current("Moon")
        expect("Added profile 'Moon'.", 1)
        self.select(FIRST)
        self.ui.update_selected()
        expect("Updated the image settings of 'Earth'.", 2)
        self.rename_prompt.return_value = "Terra"
        self.ui.rename_selected_from_dialog()
        expect("Renamed 'Earth' to 'Terra'.", 3)
        self.ui.duplicate_selected()
        expect("Duplicated as 'Terra (Copy)'.", 4)
        self.select(FIRST)
        self.ui.move_selected(-1)
        expect("Moved 'Terra' up.", 5)
        self.ui.toggle_rotation_selected()
        expect("Enabled 1 profile(s) for rotation.", 6)
        self.ui.random_shuffle_var.set(True)
        expect("Saved rotation settings.", 7)
        self.select(FIRST)
        self.ui.delete_selected()
        expect("Deleted 1 profile(s).", 8)
        self.assertNotIn("draft", self.confirm.call_args.args[1])
        self.assertNotIn("Save", self.confirm.call_args.args[1])
        self.root.update()
        self.assertEqual(self.ui.notice_label.winfo_manager(), "")
        # The rotation status poll rewrites its own line, never the notice line.
        self.ui._apply_runtime_status({"text": "Rotation is disabled."})
        self.assertEqual(self.ui.notice_var.get(), "")
        self.assertEqual(self.ui.last_saved_change, "Deleted 1 profile(s).")
        # Cancelled confirmations save nothing.
        self.confirm.return_value = False
        self.select(SECOND)
        self.ui.delete_selected()
        self.assertEqual(save.call_count, 8)

    def test_failed_save_restores_the_last_saved_profile_list(self):
        save = Mock()
        self.ui._on_save = save
        remove = Mock()
        self.ui._on_history_delete = remove
        self.ui._history_usage = lambda identifier: {"files": 1, "bytes": 10}
        self.select(FIRST)
        self.rename_prompt.return_value = "Terra"
        self.ui.rename_selected_from_dialog()
        saved = self.ui.get_library()
        save.side_effect = OSError("profiles.toml is locked")
        self.select(FIRST)
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=True):
            self.ui.delete_selected()
        self.assertTrue(self.error.called)
        self.assertEqual(self.ui.get_library(), saved)
        self.assertTrue(self.ui.tree.exists(FIRST))
        # Nothing was saved, so no History folder may go.
        remove.assert_not_called()
        # Rotation inputs return to their saved values, too.
        self.ui.enabled_var.set(True)
        self.assertFalse(self.ui.enabled_var.get())
        self.assertEqual(self.ui.get_library(), saved)

    def test_delete_key_matches_delete_button(self):
        self.assertTrue(self.ui.tree.bind("<Delete>"))
        self.select(FIRST)
        with patch.object(self.ui, "delete_selected") as delete:
            self.assertEqual(self.ui._delete_key(), "break")
        delete.assert_called_once_with()
        # Like the disabled button, the key does nothing for the protected snapshot row.
        system, _value = self.enable_system_snapshot()
        self.select(system.SYSTEM_ID)
        with patch.object(self.ui, "delete_selected") as delete:
            self.assertEqual(self.ui._delete_key(), "break")
        delete.assert_not_called()

    def test_delete_without_history_images_uses_plain_prompt(self):
        self.ui._history_usage = lambda identifier: {"files": 0, "bytes": 0}
        self.select(FIRST)
        with patch("marblescape_profile_settings._confirm_profile_delete") as confirm:
            self.ui.delete_selected()
        confirm.assert_not_called()
        self.assertTrue(self.confirm.called)
        self.assertEqual([item["id"] for item in self.ui.get_library()["items"]], [SECOND])

    def test_history_deletion_skips_listed_profiles_and_reports_failures(self):
        self.ui._history_usage = lambda identifier: {"files": 1, "bytes": 10}
        remove = Mock(side_effect=[None, OSError("locked")])
        self.ui._on_history_delete = remove
        restored = deepcopy(self.ui._items)
        self.ui._select_all()
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=True):
            self.ui.delete_selected()
        # FIRST is listed again (same UUID), so its History stays.
        self.ui._commit([deepcopy(item) for item in restored if item["id"] == FIRST], FIRST)
        self.assertEqual(self.ui.commit_history_deletions(), [])
        remove.assert_called_once_with(SECOND)
        # A failed removal is reported by profile name.
        self.ui._select_all()
        with patch("marblescape_profile_settings._confirm_profile_delete", return_value=True):
            self.ui.delete_selected()
        self.assertEqual(self.ui.commit_history_deletions(), ["Earth: locked"])

    def test_delete_dialog_history_checkbox_defaults_off(self):
        from tkinter import ttk
        import marblescape_profile_settings as profile_ui
        dialog = object.__new__(profile_ui._DeleteProfilesDialog)
        dialog._prompt = "Delete Earth?"
        dialog._history_label = "Also delete History images (1 image, 10 B)"
        dialog._history_note = "The History folder also contains 1 other file (4 B)."
        host = ttk.Frame(self.root)
        try:
            dialog.body(host)
            checkbox = next(widget for widget in host.winfo_children() if isinstance(widget, ttk.Checkbutton))
            self.assertEqual(checkbox.cget("text"), "Also delete History images (1 image, 10 B)")
            labels = [widget.cget("text") for widget in host.winfo_children() if isinstance(widget, ttk.Label)]
            self.assertEqual(labels, ["Delete Earth?", "The History folder also contains 1 other file (4 B)."])
            self.assertFalse(dialog._history_var.get())
            dialog.apply()
            self.assertIs(dialog.result, False)
            dialog._history_var.set(True)
            dialog.apply()
            self.assertIs(dialog.result, True)
        finally:
            host.destroy()

    def test_duplicate_rename_and_bad_update_keep_original_profile(self):
        self.select(FIRST)
        before = self.ui.get_library()
        self.rename_prompt.return_value = "SUN"
        self.ui.rename_selected_from_dialog()
        self.assertEqual(self.ui.get_library()["items"][1]["name"], "SUN")
        before = self.ui.get_library()
        self.capture.return_value = {"windows": {"position": "fill"}}
        self.ui.update_selected()
        self.assertEqual(self.ui.get_library(), before)
        self.assertEqual(self.error.call_count, 1)

    def test_load_only_calls_image_form_callback_with_copy(self):
        self.select(SECOND)
        before = self.ui.get_library()
        self.assertEqual(self.menu_state("Load"), "normal")
        self.ui.invoke_menu_entry("Load")
        passed = self.load.call_args.args[0]
        self.assertEqual(passed, {"source": {"provider": "solar"}})
        passed["source"]["provider"] = "goes_west"
        self.assertEqual(self.ui.get_library(), before)
        self.capture.assert_not_called()
        self.error.assert_not_called()

    def test_load_button_right_of_apply_does_what_load_into_image_does(self):
        buttons = self.ui.buttons
        self.assertEqual(int(buttons["Load"].grid_info()["column"]), int(buttons["Apply"].grid_info()["column"]) + 1)
        self.select(SECOND)
        self.assertTrue(buttons["Load"].instate(["!disabled"]))
        buttons["Load"].invoke()
        self.assertEqual(self.load.call_args.args[0], {"source": {"provider": "solar"}})
        self.tree_clear = self.ui.tree.selection_remove(*self.ui.tree.selection())
        self.root.update()
        self.assertEqual(self.menu_state("Load") == "normal", buttons["Load"].instate(["!disabled"]))

    def test_apply_profile_calls_apply_callback_with_independent_copy(self):
        self.select(SECOND)
        before = self.ui.get_library()
        self.ui.invoke_menu_entry("Apply")
        passed = self.apply.call_args.args[0]
        self.assertEqual(passed, {"source": {"provider": "solar"}})
        passed["source"]["provider"] = "goes_west"
        self.assertEqual(self.ui.get_library(), before)
        self.load.assert_not_called()

    def test_double_click_selects_and_applies_clicked_profile(self):
        self.select(FIRST)
        event = SimpleNamespace(x=20, y=40)
        with patch.object(self.ui.tree, "identify_region", return_value="cell"), \
                patch.object(self.ui.tree, "identify_row", return_value=SECOND):
            result = self.ui._apply_double_clicked(event)
        self.assertEqual(result, "break")
        self.assertEqual(self.ui.tree.selection(), (SECOND,))
        self.apply.assert_called_once_with({"source": {"provider": "solar"}})
        self.load.assert_not_called()

    def test_double_click_outside_profile_rows_does_nothing(self):
        event = SimpleNamespace(x=20, y=5)
        with patch.object(self.ui.tree, "identify_region", return_value="heading"), \
                patch.object(self.ui.tree, "identify_row", return_value=""):
            result = self.ui._apply_double_clicked(event)
        self.assertIsNone(result)
        self.apply.assert_not_called()

    def test_rotation_status_is_framed_right_below_the_table_with_two_lines_reserved(self):
        from tkinter import ttk
        self.root.update_idletasks()
        frame = self.ui.status_label.master
        self.assertIsInstance(frame, ttk.LabelFrame)
        self.assertEqual(frame.cget("text"), "Now showing")
        # Across the whole tab, in the row right below the table.
        self.assertIs(frame.master, self.ui.frame)
        self.assertEqual(frame.grid_info()["sticky"], "ew")
        table = self.ui.tree.master
        self.assertEqual(int(frame.grid_info()["row"]), int(table.grid_info()["row"]) + 1)
        self.assertLess(int(frame.grid_info()["row"]),
                        int(self.ui.buttons["Create Profile from Image"].master.grid_info()["row"]))
        reserved = int(frame.rowconfigure(0)["minsize"])
        one_line = self.ui.status_label.winfo_reqheight()
        self.assertGreater(reserved, one_line)
        # Two lines of status keep the same height as one.
        self.ui._apply_runtime_status({"text": "Rotation is disabled."})
        self.root.update_idletasks()
        height = frame.winfo_reqheight()
        self.ui._apply_runtime_status({"text": "Waiting for the next rotation interval.\nNext picture: already current."})
        self.root.update_idletasks()
        self.assertEqual(frame.winfo_reqheight(), height)
        self.assertEqual(self.ui.status_var.get(),
                         "Waiting for the next rotation interval.\nNext picture: already current.")

    def test_preload_switch_is_saved_at_once(self):
        save = Mock()
        self.ui._on_save = save
        self.assertTrue(self.ui.preload_next_var.get())
        self.ui.preload_next_var.set(False)
        self.assertEqual(save.call_count, 1)
        self.assertFalse(save.call_args.args[0]["rotation"]["preload_next"])
        self.assertEqual(self.ui.last_saved_change, "Saved rotation settings.")
        self.assertEqual(self.ui.notice_label.winfo_manager(), "")

    def test_get_library_validates_current_rotation_inputs(self):
        self.ui.enabled_var.set(True)
        self.ui.interval_var.set("2")
        self.ui.unit_var.set("weeks")
        self.ui.random_shuffle_var.set(True)
        self.ui.keep_last_position_var.set(True)
        self.ui.preload_next_var.set(False)
        result = self.ui.get_library()
        self.assertEqual(result["rotation"], {
            "enabled": True, "interval": 2, "unit": "weeks", "order": [SECOND],
            "random_shuffle": True, "keep_last_position": True, "preload_next": False,
        })
        for interval in ("", "NaN", "0", "1.5", "525601"):
            self.ui.interval_var.set(interval)
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.ui.get_library()
        self.ui.interval_var.set("15")
        self.ui.unit_var.set("hours")
        self.assertEqual(self.ui.get_library()["rotation"]["unit"], "hours")
        self.ui.unit_var.set("months")
        self.assertEqual(self.ui.get_library()["rotation"]["unit"], "months")

    def test_empty_library_cannot_enable_rotation(self):
        self.ui.delete_selected()
        self.root.update()
        self.ui.delete_selected()
        self.assertEqual(self.ui.get_library()["items"], [])
        self.ui.enabled_var.set(True)
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.ui.get_library()
        self.assertEqual(self.menu_state("Load"), "disabled")

    def test_buttons_are_between_rotation_status_and_selected_details(self):
        from tkinter import ttk
        self.root.update_idletasks()
        details = next(widget for widget in self.ui.frame.winfo_children()
                       if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Selected profile details")
        status_frame = self.ui.status_label.master
        button_frame = self.ui.buttons["Create Profile from Image"].master
        self.assertLess(int(status_frame.grid_info()["row"]), int(button_frame.grid_info()["row"]))
        self.assertLess(int(button_frame.grid_info()["row"]), int(details.grid_info()["row"]))
        self.assertLess(status_frame.winfo_y(), button_frame.winfo_y())
        self.assertLessEqual(button_frame.winfo_y() + button_frame.winfo_height(), details.winfo_y())

    def test_profile_table_has_no_border_in_either_theme(self):
        from tkinter import ttk
        import marblescape_theme as theme
        from marblescape_profile_settings import PROFILE_TREE_STYLE

        self.assertEqual(self.ui.tree.cget("style"), PROFILE_TREE_STYLE)
        for mode in ("light", "dark"):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            elements = str(ttk.Style(self.root).layout(PROFILE_TREE_STYLE))
            self.assertNotIn("field", elements, mode)
            self.assertIn("Treeview.treearea", elements, mode)

    def test_updates_column_switches_image_updates_of_the_selected_profiles(self):
        from marblescape_profile_settings import PROFILE_LIST_COLUMN_LABELS
        from tkinter import ttk
        from marblescape_profiles import (
            CHECKBOX_COLUMN_MIN_WIDTH, CHECKBOX_COLUMN_WIDTH, DEFAULT_PROFILE_COLUMN_WIDTHS,
            normalize_profile_column_widths,
        )

        self.assertEqual(PROFILE_LIST_COLUMN_LABELS["image_updates"], "Updates")
        # Rotation, History and Updates share one width, which is also their minimum.
        for column in ("rotation_enabled", "history", "image_updates"):
            self.assertEqual(DEFAULT_PROFILE_COLUMN_WIDTHS[column], CHECKBOX_COLUMN_WIDTH, column)
            self.assertEqual(int(self.ui.tree.column(column, "minwidth")), CHECKBOX_COLUMN_MIN_WIDTH, column)
            # Saved at the minimum, all three are equally narrow; narrower is refused.
            self.assertEqual(normalize_profile_column_widths({column: CHECKBOX_COLUMN_MIN_WIDTH})[column],
                             CHECKBOX_COLUMN_MIN_WIDTH)
            with self.assertRaises(ValueError):
                normalize_profile_column_widths({column: CHECKBOX_COLUMN_MIN_WIDTH - 1})
        # The checkbox still fits at the minimum width.
        font = ttk.Style(self.root).lookup("Treeview", "font") or "TkDefaultFont"
        self.assertLessEqual(int(self.root.tk.call("font", "measure", font, "☑")) + 8, CHECKBOX_COLUMN_MIN_WIDTH)
        self.assertIn("image_updates", self.ui.get_visible_columns())
        self.assertEqual(self.ui.tree.set(FIRST, "image_updates"), "☑")
        save = Mock()
        self.ui._on_save = save
        self.ui.tree.selection_set((FIRST, SECOND))
        self.ui.toggle_image_updates_selected()
        library = save.call_args.args[0]
        self.assertEqual({item["id"]: item["settings"]["source"]["check_for_updates"]
                          for item in library["items"]}, {FIRST: False, SECOND: False})
        self.assertEqual((self.ui.tree.set(FIRST, "image_updates"), self.ui.tree.set(SECOND, "image_updates")),
                         ("☐", "☐"))
        self.assertEqual(set(self.ui.tree.selection()), {FIRST, SECOND})
        self.assertEqual(self.ui.last_saved_change, "Imagery updates off for 2 profile(s).")
        # With a host callback, it saves this change instead of on_save.
        host = Mock()
        self.ui._on_image_updates = host
        self.select(FIRST)
        self.ui.toggle_image_updates_selected()
        identifiers, enabled, library = host.call_args.args
        self.assertEqual((identifiers, enabled), ([FIRST], True))
        self.assertTrue(next(item for item in library["items"] if item["id"] == FIRST)
                        ["settings"]["source"]["check_for_updates"])
        self.assertEqual(save.call_count, 1)
        # A failed save returns the table to the saved state.
        host.side_effect = OSError("disk full")
        self.ui.toggle_image_updates_selected()
        self.assertEqual(self.ui.tree.set(FIRST, "image_updates"), "☑")
        self.error.assert_called()

    def test_clicking_an_updates_cell_switches_it_but_not_on_the_snapshot_row(self):
        import marblescape_snapshot as latest_snapshot

        self.ui._on_save = Mock()
        event = SimpleNamespace(x=1, y=1)
        with patch.object(self.ui, "_checkbox_column", return_value="image_updates"), \
             patch.object(self.ui.tree, "identify_row", return_value=FIRST):
            self.assertEqual(self.ui._toggle_rotation_clicked(event), "break")
        self.assertEqual(self.ui.tree.set(FIRST, "image_updates"), "☐")
        with patch.object(self.ui, "_checkbox_column", return_value="image_updates"), \
             patch.object(self.ui.tree, "identify_row", return_value=latest_snapshot.SYSTEM_ID), \
             patch.object(self.ui, "toggle_image_updates_selected") as toggle:
            self.assertIsNone(self.ui._toggle_rotation_clicked(event))
        toggle.assert_not_called()
        # Sorting by the column puts switched-off profiles first.
        self.ui.sort_by("image_updates")
        self.assertEqual(self.ui.tree.get_children()[0], FIRST)

    def test_double_click_on_a_column_border_fits_the_column(self):
        layout = Mock()
        self.ui._on_layout_change = layout
        self.ui._commit([{"id": FIRST, "name": "A rather long profile name for this test", "settings": {}},
                         {"id": SECOND, "name": "Sun", "settings": {}}], FIRST)
        self.ui.tree.column("name", width=60)
        self.ui._saved_widths = self.ui.get_column_widths()
        visible = self.ui.get_visible_columns()
        border = sum(int(self.ui.tree.column(column, "width"))
                     for column in visible[:visible.index("name") + 1])
        with patch.object(self.ui.tree, "identify_region", return_value="separator"), \
             patch.object(self.ui.tree, "xview", return_value=(0.0, 1.0)):
            self.assertEqual(self.ui._separator_column(border + 2), "name")
            self.assertEqual(self.ui._apply_double_clicked(SimpleNamespace(x=border + 2, y=5)), "break")
        from tkinter import ttk
        font = ttk.Style(self.root).lookup("Treeview", "font") or "TkDefaultFont"
        needed = int(self.root.tk.call("font", "measure", font, "A rather long profile name for this test"))
        width = int(self.ui.tree.column("name", "width"))
        self.assertGreater(width, needed)
        self.assertLess(width, needed + 40)
        layout.assert_called_once_with()
        self.assertEqual(self.ui.get_column_widths()["name"], width)
        self.apply.assert_not_called()
        # A narrow checkbox column fits its heading again.
        self.ui.tree.column("history", width=28)
        self.ui.autofit_column("history")
        heading_font = ttk.Style(self.root).lookup("Treeview.Heading", "font") or "TkDefaultFont"
        self.assertGreater(int(self.ui.tree.column("history", "width")),
                           int(self.root.tk.call("font", "measure", heading_font, "History")))

    def test_only_saved_changes_are_confirmed_in_the_footer(self):
        self.ui._on_save = Mock()
        saved = Mock()
        self.ui._on_saved = saved
        self.ui.add_current("Moon")
        self.root.update()
        saved.assert_called_once_with()
        self.assertEqual(self.ui.last_saved_change, "Added profile 'Moon'.")
        self.assertEqual(self.ui.notice_label.winfo_manager(), "")
        # Notices that save nothing (Force loading new image) show below the buttons.
        self.ui._show_notice("Loading a new picture of the active profile.")
        self.root.update()
        saved.assert_called_once_with()
        self.assertEqual(self.ui.notice_var.get(), "Loading a new picture of the active profile.")
        self.assertEqual(self.ui.notice_label.winfo_manager(), "grid")
        # The next saved change clears that line.
        self.ui.add_current("Mars")
        self.assertEqual(self.ui.notice_label.winfo_manager(), "")

    def test_table_usage_help_is_in_info_not_on_the_profile_page(self):
        from tkinter import ttk

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        self.assertFalse(any(
            isinstance(widget, ttk.Label) and "Right-click table cells" in str(widget.cget("text"))
            for widget in descendants(self.ui.frame)
        ))

    def test_a_rotation_under_two_minutes_shows_a_warning_hint(self):
        from marblescape_profile_settings import short_rotation_hint
        self.assertIn("only 30 seconds before the switch", short_rotation_hint("1", "minutes"))
        for interval, unit in (("2", "minutes"), ("1", "hours"), ("x", "minutes"), ("1", "decades")):
            self.assertEqual(short_rotation_hint(interval, unit), "")
        label = self.ui.short_rotation_label
        self.ui.interval_var.set("1")
        self.ui.unit_var.set("minutes")
        self.root.update_idletasks()
        self.assertIn("30 seconds", str(label.cget("text")))
        import marblescape_theme as theme
        self.assertEqual(str(label.cget("foreground")), theme.palette(label)["warning"])
        # Its line below the row stays reserved, empty for longer intervals.
        self.assertEqual(int(label.grid_info()["row"]), 1)
        self.assertGreater(int(label.master.grid_rowconfigure(1, "minsize")), 0)
        self.ui.interval_var.set("5")
        self.assertEqual(str(label.cget("text")), "")
        self.assertEqual(label.winfo_manager(), "grid")
        # Shown, it still leaves the interval controls next to their label.
        self.ui.interval_var.set("1")
        self.test_rotation_interval_controls_stay_next_to_their_label()

    def test_rotation_interval_controls_stay_next_to_their_label(self):
        from tkinter import ttk
        self.root.update_idletasks()
        rotation = next(widget for widget in self.ui.frame.winfo_children()
                        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Rotation")

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        label = next(widget for widget in descendants(rotation)
                     if isinstance(widget, ttk.Label) and widget.cget("text") == "Change every")
        value, unit = sorted((widget for widget in descendants(rotation) if isinstance(widget, ttk.Combobox)),
                             key=lambda widget: int(widget.grid_info()["column"]))
        # The wide hint below must not spread the row across the frame.
        self.assertLessEqual(value.winfo_x() - (label.winfo_x() + label.winfo_width()), 10)
        self.assertLessEqual(unit.winfo_x() - (value.winfo_x() + value.winfo_width()), 6)

    def test_multi_delete_via_button_or_menu_confirms_count_and_preserves_unselected(self):
        for menu in (False, True):
            with self.subTest(menu=menu):
                third = {"id": "3" * 32, "name": "Keep", "settings": {"source": {"provider": "solar"}}}
                self.ui._commit([*deepcopy(self.original["items"]), third], FIRST)
                self.ui.enabled_var.set(True)
                self.ui.tree.selection_set((FIRST, SECOND))
                self.ui._selection_changed()
                before = self.ui.get_library()
                self.confirm.reset_mock()
                self.confirm.return_value = False
                action = (lambda: self.ui.invoke_menu_entry("Delete")) if menu else self.ui.buttons["Delete"].invoke
                action()
                self.confirm.assert_called_once()
                self.assertIn("2 selected profiles", self.confirm.call_args.args[1])
                self.assertEqual(self.confirm.call_args.kwargs["default"], "no")
                self.assertEqual(self.ui.get_library(), before)
                self.confirm.return_value = True
                action()
                result = self.ui.get_library()
                self.assertEqual(result["items"], [third])
                self.assertEqual(result["rotation"]["order"], [])
                self.assertTrue(result["rotation"]["enabled"])
                self.assertEqual(self.ui.tree.selection(), (third["id"],))
                self.assertEqual(self.original, self.original_copy)
        self.error.assert_not_called()

    def test_deleting_all_profiles_disables_rotation_only_after_confirmation(self):
        self.ui.enabled_var.set(True)
        self.ui._select_all()
        self.confirm.return_value = False
        self.ui.buttons["Delete"].invoke()
        self.assertTrue(self.ui.get_library()["rotation"]["enabled"])
        self.confirm.return_value = True
        self.ui.invoke_menu_entry("Delete")
        self.assertIn("rotation will also be disabled", self.confirm.call_args.args[1])
        library = self.ui.get_library()
        self.assertEqual(library["items"], [])
        self.assertEqual(library["rotation"]["order"], [])
        self.assertFalse(library["rotation"]["enabled"])
        self.assertTrue(self.ui.buttons["Delete"].instate(["disabled"]))
        self.assertEqual(self.menu_state("Delete"), "disabled")

    def test_export_summary_for_single_multiple_and_skipped_profiles(self):
        import marblescape_download as app
        self.ui._normalize_settings = app.normalize_image_settings_snapshot
        settings = app.default_import_settings()
        self.ui._commit([{"id": FIRST, "name": "First", "settings": settings},
                         {"id": SECOND, "name": "Second", "settings": deepcopy(settings)}], FIRST)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "one.json")
            with patch("marblescape_profile_settings.filedialog.asksaveasfilename", return_value=path):
                self.ui.export_selected()
            self.assertEqual(self.info.call_args.args[0], "Profile export complete")
            self.assertIn("Successfully exported 1 profile(s)", self.info.call_args.args[1])
            self.assertIn(path, self.info.call_args.args[1])
            self.ui._select_all()
            with patch("marblescape_profile_settings.filedialog.askdirectory", return_value=directory):
                self.ui.export_selected()
                self.assertIn("Successfully exported 2 profile(s)", self.info.call_args.args[1])
                with patch("marblescape_profile_settings.confirm_transfer_conflict", return_value="skip_all"):
                    self.ui.export_selected()
            self.assertEqual(self.info.call_args.args[0], "Profile export skipped")
            self.assertIn("Skipped: 2", self.info.call_args.args[1])
            self.assertNotIn("Successfully", self.info.call_args.args[1])
        self.error.assert_not_called()

    def test_export_failure_reports_reason_without_success(self):
        self.ui._normalize_settings = Mock()
        self.select(FIRST)
        with patch("marblescape_profile_settings.filedialog.asksaveasfilename", return_value="failure.json"), \
             patch("marblescape_profile_settings.export_profiles", side_effect=PermissionError("Access denied: locked.json")):
            self.ui.export_selected()
        self.info.assert_not_called()
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "Profile export failed")
        self.assertIn("Access denied: locked.json", self.error.call_args.args[1])
        self.assertIn("Access denied: locked.json", self.ui.status_var.get())

    def test_export_cancellation_is_not_reported_as_failure_or_success(self):
        from marblescape_profile_transfer import ImportCancelled
        self.ui._normalize_settings = Mock()
        self.select(FIRST)
        with patch("marblescape_profile_settings.filedialog.asksaveasfilename", return_value=""):
            self.ui.export_selected()
        with patch("marblescape_profile_settings.filedialog.asksaveasfilename", return_value="cancel.json"), \
             patch("marblescape_profile_settings.export_profiles", side_effect=ImportCancelled()):
            self.ui.export_selected()
        self.info.assert_not_called()
        self.error.assert_not_called()
        self.assertIn("Export cancelled", self.ui.status_var.get())

    def test_failed_folder_preference_does_not_misreport_successful_export(self):
        self.ui._normalize_settings = Mock()
        self.ui._export_locations = Mock()
        self.ui._export_locations.initial_directory.return_value = Path(".")
        self.ui._export_locations.remember.side_effect = OSError("Preference file locked")
        self.select(FIRST)
        with patch("marblescape_profile_settings.filedialog.asksaveasfilename", return_value="saved.json"), \
             patch("marblescape_profile_settings.export_profiles", return_value=[Path("saved.json")]), \
             patch("marblescape_profile_settings.messagebox.showwarning") as warning:
            self.ui.export_selected()
        self.error.assert_not_called()
        self.assertIn("Successfully exported 1", warning.call_args.args[1])
        self.assertIn("Preference file locked", warning.call_args.args[1])

    def test_status_polling_and_close_cancel_pending_callback(self):
        self.assertEqual(self.ui.status_var.get(), "Rotation is disabled.")
        timer = self.ui._after_id
        self.assertIsNotNone(timer)
        self.assertIn(timer, self.root.tk.call("after", "info"))
        self.ui.close()
        self.ui.close()
        self.assertIsNone(self.ui._after_id)
        self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.root.update()

    def test_time_syntax_selection_and_selected_details(self):
        copernicus = {
            "source": {"provider": "copernicus"},
            "sources": {"copernicus": {
                "configuration": "DEFAULT-THEME", "mission": "Sentinel-2",
                "product": "DEFAULT-THEME::a91f72", "layer": "1_TRUE_COLOR",
                "highlight": "", "date": "2025-05-30", "latitude": 51.1657,
                "longitude": 10.4515, "map_zoom": 10,
            }},
            "view": {"fit_mode": "fit", "zoom": 1},
            "output": {"render_scale": "auto"},
            "layers": [],
        }
        self.assertEqual(self.ui._selection_columns(copernicus), ("Sentinel-2", "Sentinel-2 L2A", "True color"))
        self.assertEqual(_profile_time(copernicus, {}), ("Fixed", "2025-05-30"))
        copernicus["sources"]["copernicus"]["date"] = "latest"
        self.assertEqual(_profile_time(copernicus, {}), ("Latest", "not loaded yet"))
        # Rolling periods name how far back; Time shows the period they give.
        saved_choice = dict(copernicus["sources"]["copernicus"])
        for mode, offset, selection, period in (
                ("relative_quarter", 0, "Rolling · current quarter", r"^\d{4} Q[1-4]$"),
                ("relative_quarter", 1, "Rolling · 1 quarter back", r"^\d{4} Q[1-4]$"),
                ("relative_month", 3, "Rolling · 3 months back", r"^\d{4}-\d{2}$")):
            copernicus["sources"]["copernicus"].update(date_mode=mode, quarter_offset=offset, month_offset=offset)
            shown = _profile_time(copernicus, {})
            self.assertEqual(shown[0], selection)
            self.assertRegex(shown[1], period)
        copernicus["sources"]["copernicus"].clear()
        copernicus["sources"]["copernicus"].update(saved_choice)
        self.assertEqual(_profile_time({"source": {"provider": "worldview"},
                                        "sources": {"worldview": {"product": "timeless"}}}, {}),
                         ("Timeless", "-"))

        item = {"id": FIRST, "name": "Germany Sentinel", "settings": copernicus}
        details = self.ui._detail_values(item)
        self.assertEqual(details["image_source"], "Copernicus Browser · Sentinel-2")
        self.assertEqual(details["configuration"], "Default")
        self.assertEqual(details["area"], "51.1657, 10.4515")
        self.assertEqual((details["product"], details["layer"]), ("Sentinel-2 L2A", "True color"))
        self.assertEqual(details["source_resolution"], "Map zoom 10")
        self.assertEqual(details["output_resolution"], "3840 × 2160 (global)")
        self.assertEqual(details["wallpaper_position"], "Fit (global)")
        self.assertEqual(details["time_utc"], "Not loaded yet")
        # Rows that do not apply are hidden: no highlight, no period, and the
        # map zoom already is the source resolution.
        for key in ("highlight", "quarter_selection", "fit_zoom", "data_coverage"):
            self.assertEqual(details[key], "", key)
        self.ui._update_details(item)
        self.assertEqual([key for key, widgets in self.ui._detail_rows.items()
                          if all(widget.winfo_manager() for widget in widgets)],
                         ["image_source", "configuration", "area", "product", "layer",
                          "source_resolution", "output_resolution", "wallpaper_position",
                          "auto_recommendation", "time_utc", "cache"])
        # Without the rule only "No"; its priority and precise check are hidden.
        self.assertEqual((details["auto_recommendation"], details["auto_priority"],
                          details["auto_precise"], details["auto_choice"]), ("No", "", "", ""))

        for provider, profile, expected in (
            ("goes_east", {"area": "full_disk", "product": "GEOCOLOR", "resolution": "largest"}, "full_disk"),
            ("goes_west", {"area": "gwas", "product": "13", "resolution": "14400x8640"}, "gwas"),
            ("solar", {"area": "sun", "product": "Fe171", "resolution": "largest"}, "sun"),
        ):
            settings = deepcopy(copernicus)
            settings["source"]["provider"] = provider
            settings["sources"] = {provider: profile}
            details = self.ui._detail_values({"id": FIRST, "name": provider, "settings": settings})
            # Without a cached catalogue the IDs stay; the Sun needs no area row.
            self.assertEqual(details["area"], "" if provider == "solar" else expected)
            self.assertEqual(details["fit_zoom"], "Fit · 1x")
            expected_resolution = (
                "Largest available" if profile["resolution"] == "largest" else profile["resolution"]
            )
            self.assertEqual(details["source_resolution"], expected_resolution)
        # A cached catalogue names the NOAA area and product.
        names = {("goes_east", "full_disk", None): {"label": "Full Disk"},
                 ("goes_east", "GEOCOLOR", "full_disk"): {"label": "GeoColor"}}
        self.ui._catalogue_entry = lambda provider, item_id, area_id=None: names.get((provider, item_id, area_id))
        settings = deepcopy(copernicus)
        settings["source"]["provider"] = "goes_east"
        settings["sources"] = {"goes_east": {"area": "full_disk", "product": "GEOCOLOR", "resolution": "auto"}}
        details = self.ui._detail_values({"id": FIRST, "name": "Earth", "settings": settings})
        self.assertEqual((details["image_source"], details["area"], details["product"]),
                         ("NOAA GOES-East", "Full Disk", "GeoColor"))
        self.ui._catalogue_entry = None

        eumetsat = deepcopy(copernicus)
        eumetsat.update(
            source={"provider": "eumetsat"}, sources={},
            view={"preset": "europe", "fit_mode": "crop", "zoom": 1.2},
            output={"width": 2560, "height": 1440, "render_scale": "auto"},
            layers=[{"kind": "wms", "name": "mtg_fd:rgb_geocolour", "enabled": True}],
        )
        details = self.ui._detail_values({"id": FIRST, "name": "Earth", "settings": eumetsat})
        self.assertTrue(details["image_source"].startswith("EUMETSAT"))
        self.assertTrue(details["configuration"].endswith("Europe"))
        self.assertEqual(details["layer"], "mtg_fd:rgb_geocolour")
        self.assertEqual(details["fit_zoom"], "Crop · 1.2x")
        self.assertEqual(details["source_resolution"], "Render quality · Automatic")
        eumetsat["output"]["render_scale"] = "default"
        details = self.ui._detail_values({"id": FIRST, "name": "Earth", "settings": eumetsat})
        self.assertEqual(details["source_resolution"], "Render quality · Default (General)")
        eumetsat["output"]["render_scale"] = "auto"
        # A cached catalogue gives readable names.
        names = {("eumetsat", "mtg_fd:rgb_geocolour", None): {"label": "GeoColour RGB - MTG - 0 degree"}}
        self.ui._catalogue_entry = lambda provider, item_id, area_id=None: names.get((provider, item_id, area_id))
        details = self.ui._detail_values({"id": FIRST, "name": "Earth", "settings": eumetsat})
        self.assertEqual(details["layer"], "GeoColour RGB - MTG - 0 degree")

        worldview = deepcopy(copernicus)
        worldview.update(
            source={"provider": "worldview"},
            sources={"worldview": {
                "area": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
                "product": "2026-09-12", "resolution": "8192x4096",
            }},
            view={"fit_mode": "fit", "zoom": 1},
        )
        self.assertEqual(self.ui._selection_columns(worldview),
                         ("-", "-", "VIIRS_NOAA20_CorrectedReflectance_TrueColor"))
        self.assertEqual(_profile_time(worldview, {}), ("Fixed", "2026-09-12"))
        details = self.ui._detail_values({
            "id": FIRST, "name": "NASA true color", "settings": worldview,
        })
        self.assertEqual(details["image_source"], "NASA Worldview")
        self.assertEqual((details["configuration"], details["highlight"]), ("", ""))
        self.assertEqual(details["area"], "Global (EPSG:4326)")
        self.assertEqual(details["time_utc"], "Fixed date · 2026-09-12")
        self.assertEqual(details["product"], "VIIRS_NOAA20_CorrectedReflectance_TrueColor")
        self.assertEqual(details["source_resolution"], "8192x4096")
        names = {("worldview", "VIIRS_NOAA20_CorrectedReflectance_TrueColor", None):
                 {"label": "Corrected Reflectance (True Color, VIIRS, NOAA-20)"}}
        self.ui._catalogue_entry = lambda provider, item_id, area_id=None: names.get((provider, item_id, area_id))
        details = self.ui._detail_values({"id": FIRST, "name": "NASA true color", "settings": worldview})
        self.assertEqual(details["product"], "Corrected Reflectance (True Color, VIIRS, NOAA-20)")

    def test_frame_destruction_cleans_status_timer_and_layout_fits(self):
        self.assertLessEqual(self.ui.frame.winfo_reqwidth(), 740)
        timer = self.ui._after_id
        self.ui.frame.destroy()
        self.root.update()
        self.assertTrue(self.ui._closed)
        self.assertNotIn(timer, self.root.tk.call("after", "info"))


if __name__ == "__main__":
    unittest.main()
