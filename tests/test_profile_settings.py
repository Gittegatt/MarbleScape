"""Hidden Tk tests for the profile draft editor; no user settings are written."""
from copy import deepcopy
import gc
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from marblescape_profile_settings import (
    ProfilesSettings, _profile_coverage, _profile_latitude, _profile_location,
    _profile_longitude, _profile_selection, _profile_time,
)
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
            "display_time_zone": "utc",
        })
        self.error_patch = patch("marblescape_profile_settings.messagebox.showerror")
        self.error = self.error_patch.start()
        self.ui = ProfilesSettings(
            self.root, self.original, self.capture, self.load,
            on_apply=self.apply, status=self.status,
        )
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

    def test_order_is_rotation_first_then_remaining_and_input_is_unchanged(self):
        self.assertEqual(self.ui.tree.get_children(), (SECOND, FIRST))
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND, FIRST])
        self.assertEqual(self.original, self.original_copy)
        self.assertIn("Solar", str(self.ui.tree.item(SECOND, "values")))
        self.assertEqual(self.ui.tree["columns"],
                         ("name", "source", "selection", "time", "location",
                          "latitude", "longitude", "coverage"))
        values = self.ui.tree.item(SECOND, "values")
        self.assertEqual(values[0], "Sun · Active")
        self.assertEqual(values[3], "Latest · 2026-09-13 10:20 UTC")
        self.assertEqual(values[4:], ("Sun", "-", "-", "-"))
        self.assertEqual(self.ui.detail_vars["time_utc"].get(), "2026-09-13 10:20 UTC")

    def test_profile_rows_show_location_and_coverage_by_source(self):
        copernicus = {"source": {"provider": "copernicus"}, "sources": {"copernicus": {
            "latitude": 19.60508, "longitude": -155.43457,
            "coverage_mode": "single", "lookback_days": 90,
        }}}
        item = {"id": FIRST, "name": "Hawaii", "settings": copernicus}
        self.assertEqual(self.ui._row_values(item)[4:],
                         ("Custom Lat/Long", "19.60508", "-155.43457",
                          "Single latest acquisition"))
        self.assertEqual(_profile_latitude(copernicus), "19.60508")
        self.assertEqual(_profile_longitude(copernicus), "-155.43457")
        copernicus["sources"]["copernicus"]["coverage_mode"] = "black"
        self.assertEqual(_profile_coverage(copernicus), "No-data areas black")
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

    def test_rows_and_cells_can_be_copied_and_columns_can_be_hidden(self):
        self.select(SECOND)
        self.ui._context_item = SECOND
        self.ui._context_column = "source"
        self.ui._copy_context_cell()
        self.assertEqual(self.root.clipboard_get(), "Solar / Sun")

        self.ui._copy_selected_row()
        copied = self.root.clipboard_get().split("\t")
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
        self.assertEqual(self.ui.get_visible_columns(), (last_column,))
        self.assertTrue(self.ui._column_visibility_vars[last_column].get())

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

    def test_add_and_update_capture_current_image_as_independent_draft(self):
        self.ui.name_var.set("Americas")
        self.ui.buttons["Add current image"].invoke()
        self.root.update()
        library = self.ui.get_library()
        added = library["items"][-1]
        self.assertEqual(added["name"], "Americas")
        self.assertEqual(added["settings"], self.snapshot)
        self.snapshot["view"]["zoom"] = 1.8
        self.assertEqual(self.ui.get_library()["items"][-1]["settings"]["view"]["zoom"], 1.2)
        self.select(added["id"])
        self.ui.buttons["Update selected"].invoke()
        self.assertEqual(self.ui.get_library()["items"][-1]["settings"]["view"]["zoom"], 1.8)
        self.assertEqual(self.original, self.original_copy)
        self.error.assert_not_called()

    def test_invalid_name_or_capture_does_not_mutate_draft(self):
        before = self.ui.get_library()
        for name in ("", "sUn", "x" * 81):
            self.ui.name_var.set(name)
            self.ui.add_current()
            self.assertEqual(self.ui.get_library(), before)
        self.capture.assert_not_called()
        self.ui.name_var.set("Valid name")
        self.capture.side_effect = ValueError("Current image is invalid")
        self.ui.add_current()
        self.assertEqual(self.ui.get_library(), before)
        self.assertTrue(self.error.called)
        self.assertEqual(self.error.call_args.kwargs["parent"], self.root)

    def test_rename_move_and_delete_are_reversible_draft_changes(self):
        self.select(FIRST)
        self.ui.name_var.set("Earth renamed")
        self.ui.rename_selected()
        self.ui.move_selected(-1)
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [FIRST, SECOND])
        self.assertEqual(self.ui.get_library()["items"][0]["name"], "Earth renamed")
        self.ui.move_selected(-1)
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [FIRST, SECOND])
        self.ui.delete_selected()
        self.assertEqual(self.ui.get_library()["rotation"]["order"], [SECOND])
        self.assertEqual(self.original, self.original_copy)
        self.error.assert_not_called()

    def test_duplicate_rename_and_bad_update_keep_original_profile(self):
        self.select(FIRST)
        before = self.ui.get_library()
        self.ui.name_var.set("SUN")
        self.ui.rename_selected()
        self.assertEqual(self.ui.get_library(), before)
        self.capture.return_value = {"windows": {"position": "fill"}}
        self.ui.update_selected()
        self.assertEqual(self.ui.get_library(), before)
        self.assertEqual(self.error.call_count, 2)

    def test_load_only_calls_image_form_callback_with_copy(self):
        self.select(SECOND)
        before = self.ui.get_library()
        self.ui.buttons["Load into Image"].invoke()
        passed = self.load.call_args.args[0]
        self.assertEqual(passed, {"source": {"provider": "solar"}})
        passed["source"]["provider"] = "goes_west"
        self.assertEqual(self.ui.get_library(), before)
        self.capture.assert_not_called()
        self.error.assert_not_called()

    def test_apply_profile_calls_apply_callback_with_independent_copy(self):
        self.select(SECOND)
        before = self.ui.get_library()
        self.ui.buttons["Apply profile"].invoke()
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

    def test_get_library_validates_current_rotation_inputs(self):
        self.ui.enabled_var.set(True)
        self.ui.interval_var.set("2")
        self.ui.unit_var.set("weeks")
        result = self.ui.get_library()
        self.assertEqual(result["rotation"], {"enabled": True, "interval": 2, "unit": "weeks", "order": [SECOND, FIRST]})
        for interval in ("", "NaN", "0", "1.5", "525601"):
            self.ui.interval_var.set(interval)
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.ui.get_library()
        self.ui.interval_var.set("15")
        self.ui.unit_var.set("hours")
        with self.assertRaises(ValueError):
            self.ui.get_library()

    def test_empty_library_cannot_enable_rotation(self):
        self.ui.delete_selected()
        self.root.update()
        self.ui.delete_selected()
        self.assertEqual(self.ui.get_library()["items"], [])
        self.ui.enabled_var.set(True)
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.ui.get_library()
        self.assertIn("disabled", self.ui.buttons["Load into Image"].state())

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
            "output": {"width": 3840, "height": 0, "aspect_ratio": "16:9"},
            "layers": [],
        }
        self.assertEqual(_profile_selection(copernicus), "Sentinel-2 L2A · True color")
        self.assertEqual(_profile_time(copernicus, {}), "Fixed · 2025-05-30")
        copernicus["sources"]["copernicus"]["date"] = "latest"
        self.assertEqual(_profile_time(copernicus, {}), "Latest · not loaded yet")

        item = {"id": FIRST, "name": "Germany Sentinel", "settings": copernicus}
        details = self.ui._detail_values(item)
        self.assertEqual(details["configuration"], "Default")
        self.assertEqual(details["area"], "51.1657, 10.4515")
        self.assertEqual(details["product"], "Sentinel-2 L2A · True color")
        self.assertEqual(details["source_resolution"], "Map zoom 10")
        self.assertEqual(details["output_resolution"], "3840 × 2160 (16:9)")
        self.assertEqual(details["wallpaper_position"], "fit (global)")
        self.assertEqual(details["time_utc"], "Not loaded yet")

        for provider, profile, expected in (
            ("goes_east", {"area": "full_disk", "product": "GEOCOLOR", "resolution": "largest"}, "full_disk"),
            ("goes_west", {"area": "gwas", "product": "13", "resolution": "14400x8640"}, "gwas"),
            ("solar", {"area": "sun", "product": "Fe171", "resolution": "largest"}, "sun"),
        ):
            settings = deepcopy(copernicus)
            settings["source"]["provider"] = provider
            settings["sources"] = {provider: profile}
            details = self.ui._detail_values({"id": FIRST, "name": provider, "settings": settings})
            self.assertEqual(details["area"], expected)
            expected_resolution = (
                "Largest available" if profile["resolution"] == "largest" else profile["resolution"]
            )
            self.assertEqual(details["source_resolution"], expected_resolution)

        eumetsat = deepcopy(copernicus)
        eumetsat.update(
            source={"provider": "eumetsat"}, sources={},
            view={"preset": "europe", "fit_mode": "crop", "zoom": 1.2},
            output={"width": 2560, "height": 1440, "render_scale": "auto"},
            layers=[{"kind": "wms", "name": "mtg_fd:rgb_geocolour", "enabled": True}],
        )
        details = self.ui._detail_values({"id": FIRST, "name": "Earth", "settings": eumetsat})
        self.assertTrue(details["configuration"].startswith("EUMETSAT Viewer"))
        self.assertTrue(details["configuration"].endswith("Europe"))
        self.assertIn("mtg_fd:rgb_geocolour", details["product"])
        self.assertEqual(details["fit_zoom"], "crop · 1.2x")

        worldview = deepcopy(copernicus)
        worldview.update(
            source={"provider": "worldview"},
            sources={"worldview": {
                "area": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
                "product": "2026-09-12", "resolution": "8192x4096",
            }},
            view={"fit_mode": "fit", "zoom": 1},
        )
        self.assertEqual(
            _profile_selection(worldview),
            "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
        )
        self.assertEqual(_profile_time(worldview, {}), "Fixed · 2026-09-12")
        details = self.ui._detail_values({
            "id": FIRST, "name": "NASA true color", "settings": worldview,
        })
        self.assertEqual(details["configuration"], "NASA GIBS / Worldview")
        self.assertEqual(details["highlight"], "No map overlays")
        self.assertEqual(details["area"], "Global (EPSG:4326)")
        self.assertIn("Fixed · 2026-09-12", details["product"])
        self.assertIn("VIIRS_NOAA20", details["product"])
        self.assertEqual(details["source_resolution"], "8192x4096")

    def test_frame_destruction_cleans_status_timer_and_layout_fits(self):
        self.assertLessEqual(self.ui.frame.winfo_reqwidth(), 740)
        timer = self.ui._after_id
        self.ui.frame.destroy()
        self.root.update()
        self.assertTrue(self.ui._closed)
        self.assertNotIn(timer, self.root.tk.call("after", "info"))


if __name__ == "__main__":
    unittest.main()
