"""The Recommend window with a fake check: results, progress, outdated places and Apply."""

import datetime as dt
import threading
import time
import unittest

try:
    import tkinter as tk
except ImportError:
    tk = None

import marblescape_copernicus_advice as advice
import test_copernicus_advice as fixtures

if tk is not None:
    import marblescape_copernicus_advice_window as window_module


def sample_advice():
    left, right, whole = fixtures.halves()
    today = fixtures.TODAY
    items = advice.acquisitions_from_features([
        fixtures.feature(today - dt.timedelta(days=1), 70, whole),
        fixtures.feature(today - dt.timedelta(days=4), 3, left),
        fixtures.feature(today - dt.timedelta(days=9), 2, right),
    ], fixtures.profile(), fixtures.SIZE)
    return advice.advise(items, advice.grid_size(*fixtures.SIZE), fixtures.CLOUD_LAYER,
                         fixtures.profile(), today)


@unittest.skipIf(tk is None, "tkinter is not installed")
class RecommendationWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.checks, self.applied, self.precise = [], [], []
        self.find = None
        self.result = sample_advice()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [], "An exception escaped a Tk callback")

    def run_check(self, latitude, longitude, zoom, progress, precise=False):
        self.checks.append((latitude, longitude, zoom))
        self.precise.append(precise)
        progress(1, None)
        if precise:
            progress(1, 3, "masks")
        return self.result

    def open(self, **changes):
        options = dict(title="Recommend - Copernicus", selection="Sentinel-2 L2A · True color",
                       current="Current: Latest available · Max. cloud cover 100%",
                       latitude=19.589555, longitude=-155.448698, zoom=10,
                       zoom_values=[str(value) for value in range(7, 19)], run_check=self.run_check,
                       apply=lambda *values: self.applied.append(values),
                       find_coordinates=lambda: self.find)
        options.update(changes)
        self.window = window_module.RecommendationWindow(self.root, **options)
        self.window.window.withdraw()
        self.addCleanup(self.window.close)
        if not options.get("unavailable"):
            self.window.check()  # The window waits for Check; these tests press it at once.
        return self.window

    def test_always_use_precise_check_starts_the_window_with_precise_check(self):
        window = self.open(precise_available=True, precise_default=True)
        self.wait(window)
        self.assertTrue(window.precise_var.get())
        self.assertEqual(self.precise, [True])
        window.close()
        window = self.open(precise_available=True)
        self.wait(window)
        self.assertFalse(window.precise_var.get())

    def test_newest_has_a_full_width_section_and_an_orange_row(self):
        from marblescape_theme import palette
        window = self.open()
        self.wait(window)
        colors = palette(window.tree)
        self.assertEqual(window.newest_box.winfo_manager(), "grid")
        self.assertEqual(str(window.newest_title.cget("foreground")), colors["newest"])
        self.assertEqual([label.cget("text") for label in window.newest_title.master.winfo_children()],
                         ["Priority: ", "Newest"])
        row = advice.newest_row(self.result)
        self.assertIn(row.variant.settings_text(), window.newest_text.cget("text"))
        self.assertIn("orange dot", window.newest_text.cget("text"))
        item = next(item for item, value in window._row_by_item.items() if value is row)
        # Its row has an orange dot, after the dots of boxes that chose it too; no row color.
        self.assertEqual(window._row_dots[item][-1], "newest")
        self.assertFalse(window.tree.item(item, "tags"))
        self.assertEqual(window.tree.column("#0", "width"), window_module.DOTS_COLUMN_WIDTH)
        # Three dots always fit: no expander indicator before them, room reserved for all three.
        from tkinter import ttk
        layout = repr(ttk.Style(window.window).layout(f"{window_module.DOTS_STYLE}.Item"))
        self.assertNotIn("indicator", layout)
        self.assertIn("image", layout)
        self.assertGreaterEqual(window_module.DOTS_COLUMN_WIDTH, window_module.DOTS_WIDTH + 5)
        self.assertEqual(int(window.tree.column("#0", "stretch")), 0)
        # Its border does not drag: a click on that separator does nothing.
        from unittest import mock
        with mock.patch.object(window.tree, "identify_region", return_value="separator"),                 mock.patch.object(window.tree, "identify_column", return_value="#0"):
            self.assertEqual(window._keep_dots_column(mock.Mock(x=44, y=5)), "break")
        with mock.patch.object(window.tree, "identify_region", return_value="separator"),                 mock.patch.object(window.tree, "identify_column", return_value="#1"):
            self.assertIsNone(window._keep_dots_column(mock.Mock(x=200, y=5)))
        # The section spans the window (sticky east-west) in the row above both boxes.
        boxes = window.boxes["fewest"]["heading"].master.master
        self.assertEqual(int(window.newest_box.grid_info()["row"]) + 1, int(boxes.grid_info()["row"]))
        self.assertEqual(window.newest_box.grid_info()["sticky"], "ew")
        window.close()
        # Mosaics show Newest as their first box instead.
        import test_copernicus_advice as advice_tests
        self.result = advice.advise_mosaic(
            advice_tests.period_features(*advice_tests.MosaicAdviceTests.QUARTERS),
            advice_tests.QUARTER_LAYER, advice_tests.mosaic_profile(), fixtures.TODAY)
        window = self.open(mosaic=True)
        self.wait(window)
        self.assertFalse(window.newest_box.winfo_manager())

    def test_the_window_waits_for_check(self):
        window = window_module.RecommendationWindow(
            self.root, title="Recommendation - Copernicus", selection="Sentinel-2 L2A · True color",
            current="Current: Latest available", latitude=19.6, longitude=-155.5, zoom=10,
            zoom_values=["10"], run_check=self.run_check, apply=lambda *values: None,
            find_coordinates=lambda: None)
        window.window.withdraw()
        self.addCleanup(window.close)
        self.root.update()
        self.assertEqual(self.checks, [])
        self.assertEqual(window.state_var.get(), window_module.READY_TEXT)
        self.assertTrue(window.check_button.instate(["!disabled"]))
        self.assertTrue(window.boxes["full"]["apply"].instate(["disabled"]))
        window.check_button.invoke()
        self.wait(window)
        self.assertEqual(self.checks, [(19.6, -155.5, 10)])

    def wait(self, window):
        deadline = time.monotonic() + 5
        while window.check_button.instate(["disabled"]) and not window._unavailable:
            self.assertLess(time.monotonic(), deadline, "The check did not finish")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def test_opening_checks_the_place_and_shows_both_priorities_and_every_row(self):
        window = self.open()
        self.wait(window)
        self.assertEqual(self.checks, [(19.589555, -155.448698, 10)])
        fewest, full = self.result.fewest_clouds, self.result.full_coverage
        self.assertEqual(window.boxes["fewest"]["heading"].cget("text"), fewest.variant.settings_text())
        self.assertEqual(window.boxes["full"]["heading"].cget("text"), full.variant.settings_text())
        self.assertIn("Clouds about", window.boxes["fewest"]["details"].cget("text"))
        self.assertIn("complete on", window.boxes["full"]["details"].cget("text"))
        rows = [window.tree.item(item, "values") for item in window.tree.get_children()]
        self.assertEqual(len(rows), len(self.result.rows))
        self.assertTrue(rows[0][0].startswith("Current: "))
        self.assertTrue(window.state_var.get().startswith("Results for 19.5896, -155.449 at map zoom 10"))
        self.assertFalse(window.progress.winfo_manager())
        self.assertTrue(window.precise_check.instate(["disabled"]))

    def test_the_window_cannot_shrink_below_its_content(self):
        window = self.open()
        self.wait(window)
        width, height = window.window.minsize()
        self.assertGreater(width, 600)
        self.assertGreater(height, 400)
        self.assertEqual((width, height), (window.window.winfo_reqwidth(), window.window.winfo_reqheight()))

    def test_progress_shows_pages_while_fetching(self):
        gate = threading.Event()

        def slow(latitude, longitude, zoom, progress, precise=False):
            progress(2, 5)
            gate.wait(5)
            return self.result

        window = self.open(run_check=slow)
        deadline = time.monotonic() + 5
        while "2/5" not in window.state_var.get():
            self.assertLess(time.monotonic(), deadline)
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(window.state_var.get(), "Fetching data 2/5...")
        self.assertEqual(str(window.progress.cget("mode")), "determinate")
        self.assertEqual(window.progress.winfo_manager(), "grid")
        gate.set()
        self.wait(window)
        self.assertFalse(window.progress.winfo_manager())

    def test_a_changed_place_greys_out_the_results_until_check(self):
        window = self.open()
        self.wait(window)
        self.assertTrue(window.boxes["full"]["apply"].instate(["!disabled"]))
        window.zoom_var.set("12")
        self.root.update()
        self.assertTrue(window.state_var.get().startswith("Place or zoom changed"))
        self.assertTrue(window.boxes["full"]["apply"].instate(["disabled"]))
        self.assertTrue(all("outdated" in window.tree.item(item, "tags")
                            for item in window.tree.get_children()))
        window.check()
        self.wait(window)
        self.assertEqual(self.checks[-1], (19.589555, -155.448698, 12))
        self.assertTrue(window.boxes["full"]["apply"].instate(["!disabled"]))

    def test_apply_takes_a_box_or_a_selected_row_with_the_checked_place(self):
        window = self.open()
        self.wait(window)
        window.boxes["full"]["apply"].invoke()
        self.assertEqual(self.applied, [(self.result.full_coverage.variant, 19.589555, -155.448698, 10)])
        self.assertTrue(window.state_var.get().startswith("Applied "))
        items = window.tree.get_children()
        window.tree.selection_set(items[0])
        self.root.update()
        self.assertTrue(window.row_apply.instate(["disabled"]), "The current row changes nothing")
        window.tree.selection_set(items[-1])
        self.root.update()
        window.row_apply.invoke()
        self.assertEqual(self.applied[-1][0], window._row_by_item[items[-1]].variant)

    def test_columns_sort_both_ways_with_current_on_top_until_the_window_closes(self):
        window = self.open()
        self.wait(window)
        default = [window.tree.item(item, "values") for item in window.tree.get_children()]

        def column(index):
            return [window.tree.item(item, "values")[index] for item in window.tree.get_children()]

        def number(text):
            return float(text.rstrip("%"))

        window.sort_by("coverage")
        self.assertTrue(column(0)[0].startswith("Current: "))
        coverage = [number(value) for value in column(4)[1:]]
        self.assertEqual(coverage, sorted(coverage))
        self.assertEqual(window.tree.heading("coverage", "text"), "Coverage ▲")
        window.sort_by("coverage")
        coverage = [number(value) for value in column(4)[1:]]
        self.assertEqual(coverage, sorted(coverage, reverse=True))
        self.assertEqual(window.tree.heading("coverage", "text"), "Coverage ▼")
        # Another column starts ascending; the arrow moves there.
        window.sort_by("cloud")
        limits = [number(value) for value in column(1)[1:]]
        self.assertEqual(limits, sorted(limits))
        self.assertEqual(window.tree.heading("coverage", "text"), "Coverage")
        # A new check keeps the sorting; a new window starts in the default order.
        window.check()
        self.wait(window)
        self.assertEqual([number(value) for value in column(1)[1:]], sorted(limits))
        window.close()
        window = self.open()
        self.wait(window)
        self.assertEqual([window.tree.item(item, "values") for item in window.tree.get_children()], default)
        self.assertEqual(window.tree.heading("cloud", "text"), "Max. cloud cover")

    def test_find_location_fills_the_place(self):
        window = self.open()
        self.wait(window)
        window.get_from_find()
        self.assertEqual(window.state_var.get(), "Select a place in Find location first.")
        self.find = (53.55, 9.99)
        window.get_from_find()
        self.root.update()
        self.assertEqual((window.latitude_var.get(), window.longitude_var.get()), ("53.55", "9.99"))
        self.assertTrue(window.state_var.get().startswith("Place or zoom changed"))
        window.set_place(21.3, -157.8)
        self.assertEqual(window.place(), (21.3, -157.8, 10))

    def test_image_location_fills_the_place(self):
        image = []
        window = self.open(image_coordinates=lambda: image[0] if image else None)
        self.wait(window)
        self.assertEqual((window.find_button.cget("text"), window.image_button.cget("text")),
                         ("Find", "Source"))
        window.image_button.invoke()
        self.assertEqual(window.state_var.get(), "Enter a valid latitude and longitude in the Image tab first.")
        image.append((48.137, 11.575))
        window.image_button.invoke()
        self.root.update()
        self.assertEqual(window.place(), (48.137, 11.575, 10))
        self.assertTrue(window.state_var.get().startswith("Place or zoom changed"))

    def test_a_place_from_the_find_list_is_named_beside_its_coordinates(self):
        names = {(19.589555, -155.448698): "Hawaii - United States"}
        window = self.open(place_name=lambda latitude, longitude: names.get((latitude, longitude)))
        self.wait(window)
        self.assertEqual(window.place_name_var.get(), "Place: Hawaii - United States")
        self.assertTrue(window.place_name_label.winfo_manager())
        # Other coordinates name no place and hide the row.
        window.set_place(53.55, 9.99)
        self.assertEqual(window.place_name_var.get(), "")
        self.assertFalse(window.place_name_label.winfo_manager())
        window.set_place(19.589555, -155.448698)
        self.assertEqual(window.place_name_var.get(), "Place: Hawaii - United States")
        # Without a lookup, the row stays hidden.
        window.close()
        window = self.open()
        self.wait(window)
        self.assertFalse(window.place_name_label.winfo_manager())

    def test_fewest_clouds_is_blue_and_full_coverage_green(self):
        from marblescape_theme import palette
        window = self.open()
        self.wait(window)
        colors = palette(window.tree)
        fewest, full = window.boxes["fewest"], window.boxes["full"]
        self.assertEqual(str(fewest["heading"].cget("foreground")), colors["link"])
        self.assertEqual(str(full["heading"].cget("foreground")), colors["success"])
        self.assertEqual(str(fewest["details"].cget("foreground")), "")
        dots = {id(window._row_by_item[item]): window._row_dots[item] for item in window.tree.get_children()}
        advice = window._advice
        # The recommended rows carry a dot per priority (blue, green); none is colored.
        self.assertEqual(dots[id(advice.fewest_clouds)][0], "link")
        self.assertIn("success", dots[id(advice.full_coverage)])
        self.assertFalse(any(window.tree.item(item, "tags") for item in window.tree.get_children()
                             if not window._row_by_item[item].current))
        window.close()
        # A notice in place of a recommendation keeps its box's color.
        self.result.fewest_clouds = None
        window = self.open()
        self.wait(window)
        fewest = window.boxes["fewest"]
        self.assertEqual(fewest["heading"].cget("text"), "No variant reaches the coverage")
        self.assertEqual(str(fewest["heading"].cget("foreground")), colors["link"])
        self.assertTrue(fewest["apply"].instate(["disabled"]))

    def wait_for_previews(self, window):
        deadline = time.monotonic() + 5
        while window._loading:
            self.assertLess(time.monotonic(), deadline, "The preview did not finish")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def test_previews_load_on_request_and_are_kept(self):
        from PIL import Image
        calls = []

        def load_preview(variant, newest, latitude, longitude, zoom):
            calls.append((variant, newest, latitude, longitude, zoom))
            return Image.new("RGB", (384, 216), "red")

        window = self.open(load_preview=load_preview)
        self.wait(window)
        fewest = window.boxes["fewest"]
        self.assertEqual(fewest["preview"].cget("text"), window_module.preview_text(None))
        self.assertTrue(fewest["preview"].instate(["!disabled"]))
        self.assertFalse(fewest["picture"].winfo_manager())
        self.assertEqual(calls, [])  # nothing loads by itself
        fewest["preview"].invoke()
        self.assertEqual(fewest["preview"].cget("text"), window_module.LOADING_PREVIEW)
        self.wait_for_previews(window)
        row = window._advice.fewest_clouds
        self.assertEqual(calls, [(row.variant, row.outcome.newest, 19.589555, -155.448698, 10)])
        self.assertTrue(fewest["picture"].winfo_manager())
        self.assertFalse(fewest["preview"].winfo_manager())
        # The table row of that variant shows the loaded picture without a new request.
        item = next(item for item, value in window._row_by_item.items()
                    if value.variant == row.variant and not value.current)
        window.tree.selection_set(item)
        self.root.update()
        self.assertEqual(window.row_preview.cget("text"), window_module.SHOW_PREVIEW)
        window.row_preview.invoke()
        self.assertEqual(len(calls), 1)
        self.assertTrue(window.preview_window.winfo_exists())
        self.assertIn(row.variant.settings_text(), window.preview_caption.cget("text"))
        # Another row loads its own picture into the preview window.
        other = next(item for item, value in window._row_by_item.items()
                     if value.outcome is not None and value.variant != row.variant)
        window.tree.selection_set(other)
        self.root.update()
        self.assertEqual(window.row_preview.cget("text"), window_module.preview_text(None))
        window.row_preview.invoke()
        self.wait_for_previews(window)
        self.assertEqual(len(calls), 2)
        self.assertIn(window._row_by_item[other].variant.settings_text(),
                      window.preview_caption.cget("text"))
        # A double-click on a row loads its preview too; beside the rows it does nothing.
        from unittest import mock
        third = next(item for item, value in window._row_by_item.items()
                     if value.outcome is not None and (value.variant, window._checked) not in window._previews)
        with mock.patch.object(window.tree, "identify_row", return_value=""):
            window._row_double_clicked(mock.Mock(y=500))
        self.assertEqual(len(calls), 2)
        with mock.patch.object(window.tree, "identify_row", return_value=third):
            window._row_double_clicked(mock.Mock(y=5))
        self.wait_for_previews(window)
        self.assertEqual(window.tree.selection(), (third,))
        self.assertEqual(len(calls), 3)
        self.assertIn(window._row_by_item[third].variant.settings_text(), window.preview_caption.cget("text"))
        # A changed place waits for Check; a new place needs new previews.
        window.set_place(21.3, -157.8)
        self.root.update()
        self.assertTrue(window.row_preview.instate(["disabled"]))
        window.check()
        self.wait(window)
        self.assertFalse(window.boxes["fewest"]["picture"].winfo_manager())
        self.assertTrue(window.boxes["fewest"]["preview"].instate(["!disabled"]))

    def test_precise_check_is_chosen_per_check_and_shows_measured_values(self):
        from dataclasses import replace
        window = self.open(precise_available=True, estimate=lambda *_place: (0.42, 0.018))
        self.wait(window)
        self.assertEqual(self.precise, [False])  # off when the window opens
        self.assertTrue(window.precise_check.instate(["!disabled"]))
        self.assertEqual(window.precise_check.cget("text"), "Precise check: measure clouds and coverage "
                         "in the view (about 0.018 processing units per variant)")
        self.assertEqual(window.boxes["fewest"]["preview"].cget("text"),
                         "Load preview (about 0.42 processing units)")
        self.assertEqual(window.tree.heading("clouds", "text"), "Clouds (tiles)")
        for row in self.result.rows:
            if row.outcome is not None:
                row.outcome = replace(row.outcome, coverage=61.0, clouds=12.0, measured=True,
                                      measured_clouds=True)
        self.result.masks = 3
        window.precise_var.set(True)
        progress = []
        original = window._set_state
        window._set_state = lambda text, color: (progress.append(text), original(text, color))
        window.check()
        self.wait(window)
        self.assertEqual(self.precise, [False, True])
        self.assertIn("Measuring the view 1/3...", progress)
        self.assertIn("3 view masks", window.state_var.get())
        self.assertEqual(window.tree.heading("clouds", "text"), "Clouds in view")
        details = window.boxes["fewest"]["details"].cget("text")
        self.assertIn("Clouds 12% of the view (measured)", details)
        self.assertIn("Coverage 61% (measured)", details)
        window.sort_by("clouds")
        self.assertEqual(window.tree.heading("clouds", "text"), "Clouds in view ▲")

    def test_without_precise_check_the_box_stays_disabled(self):
        window = self.open()
        self.wait(window)
        self.assertTrue(window.precise_check.instate(["disabled"]))
        window.precise_var.set(True)
        window.check()
        self.wait(window)
        self.assertEqual(self.precise, [False, False])

    def test_mosaics_compare_periods_with_newest_and_full_coverage(self):
        import test_copernicus_advice as advice_tests
        from dataclasses import replace
        self.result = advice.advise_mosaic(
            advice_tests.period_features(*advice_tests.MosaicAdviceTests.QUARTERS),
            advice_tests.QUARTER_LAYER, advice_tests.mosaic_profile(), fixtures.TODAY)
        window = self.open(mosaic=True, precise_available=True)
        self.wait(window)
        self.assertTrue(window.precise_var.get())  # on for mosaics when the window opens
        self.assertEqual(self.precise, [True])
        fewest, full = window.boxes["fewest"], window.boxes["full"]
        self.assertEqual(fewest["name"].cget("text"), "Newest")
        self.assertEqual(full["name"].cget("text"), "Data coverage")
        # Newest is orange everywhere, also as the first box of a mosaic.
        from marblescape_theme import palette
        self.assertEqual(str(fewest["heading"].cget("foreground")), palette(window.tree)["newest"])
        self.assertEqual(str(fewest["name"].cget("foreground")), palette(window.tree)["newest"])
        self.assertEqual(str(full["name"].cget("foreground")), palette(window.tree)["success"])
        self.assertEqual(fewest["heading"].cget("text"), "Latest available (2026 Q2)")
        self.assertIn("Coverage: measured by Precise check", fewest["details"].cget("text"))
        self.assertEqual(full["heading"].cget("text"), "Needs Precise check")
        self.assertTrue(full["apply"].instate(["disabled"]))
        self.assertEqual(tuple(window.tree.cget("displaycolumns")), ("gap", "newest", "age", "coverage"))
        self.assertEqual(window.tree.heading("gap", "text"), "Period")
        self.assertIn("per period", window.precise_check.cget("text"))
        values = [window.tree.item(item, "values") for item in window.tree.get_children()]
        self.assertEqual(values[1][0], "2026 Q2")
        self.assertEqual(values[1][4], "-")
        # Measured: Data coverage names the newest complete period.
        for row in self.result.rows:
            if row.outcome is not None:
                row.outcome = replace(row.outcome, coverage=100.0 if row.variant.period.month == 1 else 80.0,
                                      measured=True)
        self.result.full_coverage = next(row for row in self.result.rows[1:] if row.outcome.coverage == 100.0)
        self.result.full_coverage_reached = True
        window.check()
        self.wait(window)
        self.assertEqual(full["heading"].cget("text"), "Relative to now, 3 quarters ago (2026 Q1)")
        self.assertIn("Coverage 100% (measured)", full["details"].cget("text"))
        full["apply"].invoke()
        self.assertEqual(self.applied[-1][0].offset, 3)

    def test_a_failed_preview_says_why_and_without_a_loader_none_load(self):
        def failing(*_values):
            raise RuntimeError("Copernicus service returned HTTP 400: pixel size")

        window = self.open(load_preview=failing)
        self.wait(window)
        window.boxes["full"]["preview"].invoke()
        self.wait_for_previews(window)
        self.assertEqual(window.state_var.get(), "Copernicus service returned HTTP 400: pixel size")
        self.assertTrue(window.boxes["full"]["preview"].instate(["!disabled"]))
        self.assertEqual(window.boxes["full"]["preview"].cget("text"), window_module.preview_text(None))
        window.close()
        window = self.open()
        self.wait(window)
        self.assertTrue(window.boxes["fewest"]["preview"].instate(["disabled"]))
        self.assertTrue(window.row_preview.instate(["disabled"]))

    def test_unavailable_layers_and_failures_say_why(self):
        window = self.open(unavailable="This layer has no acquisition dates.")
        self.root.update()
        self.assertEqual(self.checks, [])
        self.assertEqual(window.state_var.get(), "This layer has no acquisition dates.")
        self.assertTrue(window.check_button.instate(["disabled"]))
        window.close()

        def failing(*_args):
            raise RuntimeError("Copernicus catalogue returned invalid JSON.")

        window = self.open(run_check=failing)
        self.wait(window)
        self.assertEqual(window.state_var.get(), "Copernicus catalogue returned invalid JSON.")
        self.assertEqual(window.tree.get_children(), ())


if __name__ == "__main__":
    unittest.main()
