"""Find location: OpenStreetMap search rules, coordinates and the Tk section.

No test reaches the network; searches use a fake opener or a fake search.
"""

import io
import json
import re
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.parse

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None

if tk is not None:
    import marblescape_location_search as location


NOMINATIM_ANSWER = [
    {"lat": "53.5503410", "lon": "10.0006540", "category": "boundary", "type": "administrative",
     "name": "Hamburg", "display_name": "Hamburg, Germany",
     "address": {"state": "Hamburg", "country": "Germany"}},
    {"lat": "40.5559310", "lon": "-75.9821500", "category": "place", "type": "town",
     "name": "Hamburg", "display_name": "Hamburg, Berks County, Pennsylvania, United States",
     "address": {"county": "Berks County", "state": "Pennsylvania", "country": "United States"}},
    {"lat": "53.6304", "lon": "9.9882", "category": "aeroway", "type": "aerodrome",
     "name": "Hamburg Airport", "display_name": "Hamburg Airport, Hamburg, Germany",
     "address": {"state": "Hamburg", "country": "Germany"}},
    {"lat": "not a number", "lon": "1"},
    {"lat": "95", "lon": "1", "name": "Off the globe"},
]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@unittest.skipIf(tk is None, "tkinter is not installed")
class SearchRulesTests(unittest.TestCase):
    def setUp(self):
        location._CACHE.clear()
        location._LAST_REQUEST[0] = None
        self.requests = []

    def opener(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(json.dumps(NOMINATIM_ANSWER).encode("utf-8"))

    def test_results_name_the_place_its_region_and_kind(self):
        places = location.search_places(" Hamburg ", user_agent="MarbleScapeTest/1.0",
                                        timeout=12, opener=self.opener, wait=lambda seconds: None)
        self.assertEqual([place["label"] for place in places], [
            "Hamburg - Germany",
            "Hamburg - Pennsylvania, United States",
            "Hamburg Airport (aerodrome) - Hamburg, Germany",
        ])
        self.assertEqual((places[1]["latitude"], places[1]["longitude"]), (40.555931, -75.98215))
        request, timeout = self.requests[0]
        self.assertEqual(timeout, 12)
        # The policy asks for an identifying User-Agent; names in the UI's language.
        self.assertEqual(request.get_header("User-agent"), "MarbleScapeTest/1.0")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(request.full_url.split("?")[0], location.DEFAULT_SEARCH_ENDPOINT)
        self.assertEqual(query["q"], ["Hamburg"])
        self.assertEqual(query["format"], ["jsonv2"])
        self.assertEqual(query["accept-language"], ["en"])
        self.assertEqual(query["limit"], ["40"])

    def test_a_repeated_search_comes_from_the_cache(self):
        first = location.search_places("Hamburg", opener=self.opener, wait=lambda seconds: None)
        again = location.search_places("  hamburg", opener=self.opener, wait=lambda seconds: None)
        self.assertEqual(first, again)
        self.assertEqual(len(self.requests), 1)
        location.search_places("Bremen", opener=self.opener, wait=lambda seconds: None)
        self.assertEqual(len(self.requests), 2)

    def test_at_most_one_request_per_second(self):
        now = [100.0]
        waits = []

        def wait(seconds):
            waits.append(round(seconds, 3))
            now[0] += seconds

        for query, step in (("A", 0.0), ("B", 0.25), ("C", 2.0)):
            now[0] += step
            location.search_places(query, opener=self.opener, clock=lambda: now[0], wait=wait)
        self.assertEqual(waits, [1.0 - 0.25])

    def test_a_configured_endpoint_keeps_its_own_query(self):
        location.search_places("Hamburg", endpoint="https://example.invalid/search?key=1",
                               opener=self.opener, wait=lambda seconds: None)
        url = self.requests[0][0].full_url
        self.assertTrue(url.startswith("https://example.invalid/search?key=1&q=Hamburg"))

    def test_refusals_and_failures_read_plainly(self):
        def refusing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

        def offline(request, timeout):
            raise urllib.error.URLError("no route")

        def garbage(request, timeout):
            return FakeResponse(b"<html>")

        for opener, text in ((refusing, "refused the request"), (offline, "not reachable: no route"),
                             (garbage, "unexpected answer")):
            with self.subTest(text=text), self.assertRaises(location.LocationSearchError) as caught:
                location.search_places("Hamburg " + text, opener=opener, wait=lambda seconds: None)
            self.assertIn(text, str(caught.exception))

    def test_coordinates_are_recognized_in_common_spellings(self):
        for text, expected in (
            ("53.55, 9.99", (53.55, 9.99)),
            ("53.5511 9.9937", (53.5511, 9.9937)),
            ("-33.86;151.21", (-33.86, 151.21)),
            ("53.55° N, 9.99° E", (53.55, 9.99)),
            ("33.86 S 151.21 E", (-33.86, 151.21)),
            ("9.99 E, 53.55 N", (53.55, 9.99)),
            ("40.7 N 74.0 W", (40.7, -74.0)),
        ):
            with self.subTest(text=text):
                self.assertEqual(location.parse_coordinates(text), expected)
        for text in ("Hamburg", "", "91, 10", "10, 181", "53.55", "1, 2, 3", "10 N 20 S"):
            with self.subTest(text=text):
                self.assertIsNone(location.parse_coordinates(text))

    def test_copernicus_browser_opens_at_the_place_and_zoom(self):
        self.assertEqual(location.copernicus_browser_url(53.5503410, -9.99, 12.0),
                         "https://browser.dataspace.copernicus.eu/?zoom=12&lat=53.550341&lng=-9.99")


@unittest.skipIf(tk is None, "tkinter is not installed")
class LocationSectionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.transfers = []
        self.searches = []
        self.opened = []
        self.previews = []
        self.answer = [
            {"label": "Hamburg - Germany", "latitude": 53.550341, "longitude": 10.000654},
            {"label": "Hamburg - Pennsylvania, United States", "latitude": 40.555931, "longitude": -75.98215},
        ]
        frame = ttk.LabelFrame(self.root, text="Find location")
        frame.grid(row=0, column=0, sticky="ew")
        self.section = location.LocationSearch(
            frame, lambda latitude, longitude: self.transfers.append((latitude, longitude)),
            latitude_limit=85.05112878, search=self.search, open_url=self.opened.append,
            preview=self.preview,
        )

    def tearDown(self):
        self.section.close()
        self.root.destroy()
        self.assertEqual(self.errors, [], "An exception escaped a Tk callback")

    def preview(self, coordinates):
        self.previews.append(coordinates)
        return "https://example.invalid/viewer", "Opened the viewer."

    def search(self, text):
        self.searches.append(text)
        if text == "fail":
            raise location.LocationSearchError("The OpenStreetMap search is not reachable: offline")
        return self.answer

    def wait_for_search(self):
        deadline = time.monotonic() + 5
        while str(self.section.search_button.cget("state")) == "disabled":
            self.assertLess(time.monotonic(), deadline, "The search did not finish")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def rows(self):
        tree = self.section.results
        return [(tree.item(item, "text"), *tree.item(item, "values")) for item in tree.get_children()]

    def test_search_click_fills_coordinates_and_transfer_hands_them_over(self):
        self.section.query_var.set("Hamburg")
        self.section.start_search()
        self.wait_for_search()
        self.assertEqual(self.searches, ["Hamburg"])
        self.assertEqual(self.rows(), [("Hamburg - Germany", "53.550341", "10.000654"),
                                       ("Hamburg - Pennsylvania, United States", "40.555931", "-75.98215")])
        self.assertEqual(self.section.status_var.get(), "2 places found.")
        # A click only selects: the coordinates land in the editable field.
        second = self.section.results.get_children()[1]
        self.section.results.selection_set(second)
        self.root.update()
        self.assertEqual(self.section.coordinates_var.get(), "40.555931, -75.98215")
        self.assertEqual(self.transfers, [])
        # Edited by hand, then transferred.
        self.section.coordinates_var.set("40.56, -75.98")
        self.section.transfer_button.invoke()
        self.assertEqual(self.transfers, [(40.56, -75.98)])
        self.assertEqual(self.section.status_var.get(), "Transferred to Latitude and Longitude.")

    def test_double_click_transfers_the_place_at_once(self):
        self.section.query_var.set("Hamburg")
        self.section.start_search()
        self.wait_for_search()
        first = self.section.results.get_children()[0]
        self.root.update()
        with mock.patch.object(self.section.results, "identify_row", return_value=first):
            self.section._result_double_clicked(mock.Mock(y=5))
        self.assertEqual(self.transfers, [(53.550341, 10.000654)])
        self.assertEqual(self.section.coordinates_var.get(), "53.550341, 10.000654")
        # A double-click beside the rows does nothing.
        with mock.patch.object(self.section.results, "identify_row", return_value=""):
            self.section._result_double_clicked(mock.Mock(y=500))
        self.assertEqual(len(self.transfers), 1)

    def test_a_listed_or_transferred_place_is_named_by_its_coordinates(self):
        self.section.query_var.set("Hamburg")
        self.section.start_search()
        self.wait_for_search()
        self.assertEqual(self.section.place_name(53.550341, 10.000654), "Hamburg - Germany")
        self.assertIsNone(self.section.place_name(53.55, 10.0))
        second = self.section.results.get_children()[1]
        self.section.results.selection_set(second)
        self.root.update()
        self.section.transfer_button.invoke()
        # The transferred place keeps its name after the list is emptied.
        self.section.clear()
        self.assertEqual(self.section.place_name(40.555931, -75.98215),
                         "Hamburg - Pennsylvania, United States")
        self.assertIsNone(self.section.place_name(53.550341, 10.000654))
        # Coordinates edited by hand name no place.
        self.section.coordinates_var.set("40.56, -75.98")
        self.section.transfer_button.invoke()
        self.assertIsNone(self.section.place_name(40.56, -75.98))

    def test_enter_searches_and_transfers(self):
        # Generated key events do not reach a hidden window: run the bound script.
        def press_enter(widget):
            script = widget.bind("<Return>").replace("%W", str(widget))
            # The handler ends in "break"; catch keeps that from becoming an error.
            self.root.tk.call("catch", re.sub(r"%[#\w]", "0", script))

        self.section.query_var.set("Hamburg")
        press_enter(self.section.query_entry)
        self.wait_for_search()
        self.assertEqual(self.searches, ["Hamburg"])
        self.section.coordinates_var.set("1, 2")
        press_enter(self.section.coordinates_entry)
        self.assertEqual(self.transfers, [(1.0, 2.0)])

    def test_coordinates_typed_into_find_need_no_search(self):
        self.section.query_var.set("53.55° N, 9.99° E")
        self.section.start_search()
        self.root.update()
        self.assertEqual(self.searches, [])
        self.assertEqual(self.section.coordinates_var.get(), "53.55, 9.99")
        self.assertIn("Coordinates recognized", self.section.status_var.get())

    def test_invalid_or_out_of_range_coordinates_are_not_transferred(self):
        for text, message in (("Hamburg", "latitude, longitude"),
                              ("86, 10", "from -85.051129 to 85.051129")):
            with self.subTest(text=text):
                self.section.coordinates_var.set(text)
                self.section.transfer()
                self.assertIn(message, self.section.status_var.get())
        self.assertEqual(self.transfers, [])

    def test_failures_empty_the_list_and_say_why(self):
        self.section.query_var.set("Hamburg")
        self.section.start_search()
        self.wait_for_search()
        self.section.query_var.set("fail")
        self.section.start_search()
        self.wait_for_search()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.section.status_var.get(),
                         "The OpenStreetMap search is not reachable: offline")
        self.section.query_var.set("")
        self.section.start_search()
        self.assertEqual(self.section.status_var.get(), "Enter a place, address or coordinates.")

    def test_clear_empties_the_search_and_drops_a_late_answer(self):
        gate = threading.Event()
        self.section._search = lambda text: (gate.wait(5), self.answer)[1]
        self.section.query_var.set("Hamburg")
        self.section.start_search()
        self.section.clear_button.invoke()
        gate.set()
        self.wait_for_search()
        time.sleep(0.1)
        self.root.update()
        self.section._poll()
        self.assertEqual((self.section.query_var.get(), self.rows(), self.section.status_var.get()),
                         ("", [], ""))

    def test_preview_opens_the_viewer_at_the_coordinates_field_or_the_saved_place(self):
        self.assertEqual(self.section.preview_button.cget("text"), "Preview")
        # An empty or unreadable field leaves the place to the source (its saved one).
        self.section.preview_button.invoke()
        self.section.coordinates_var.set("53.55, 9.99")
        self.section.preview_button.invoke()
        self.assertEqual(self.previews, [None, (53.55, 9.99)])
        self.assertEqual(self.opened, ["https://example.invalid/viewer"] * 2)
        self.assertEqual(self.section.status_var.get(), "Opened the viewer.")
        # A source without a viewer for places greys it out and opens nothing.
        self.section.set_preview_available(False)
        self.assertTrue(self.section.preview_button.instate(["disabled"]))
        self.section._preview = lambda coordinates: None
        self.section.preview()
        self.assertEqual(len(self.opened), 2)
        self.assertEqual(self.section.status_var.get(), "No preview is available for this image source.")

    def test_fields_have_no_labels_and_the_search_field_a_grey_hint(self):
        from marblescape_theme import entry_field_color, palette
        section = self.section
        texts = [str(widget.cget("text")) for widget in section.frame.grid_slaves(column=0)
                 if "text" in widget.keys()]
        self.assertNotIn("Find", texts)
        self.assertNotIn("Coordinates", texts)
        self.root.update()
        # The Coordinates field has no hint.
        self.assertFalse(any(isinstance(child, tk.Label) for child in section.coordinates_entry.winfo_children()))
        for entry, variable, hint, text in (
            (section.query_entry, section.query_var, section.query_placeholder, "type..."),
        ):
            with self.subTest(text=text):
                self.assertEqual(hint.cget("text"), text)
                self.assertEqual(hint.winfo_manager(), "place")
                self.assertEqual(str(hint.cget("background")), entry_field_color(entry))
                self.assertEqual(str(hint.cget("foreground")), palette(entry)["placeholder"])
                variable.set("x")
                self.root.update()
                self.assertEqual(hint.winfo_manager(), "")
                variable.set("")
                self.root.update()
                self.assertEqual(hint.winfo_manager(), "place")
                # A click into the field (focus) hides it; leaving it empty brings it back.
                entry.state(["focus"])
                entry.event_generate("<Configure>")
                self.root.update()
                self.assertEqual(hint.winfo_manager(), "")
                entry.state(["!focus"])
                entry.event_generate("<Configure>")
                self.root.update()
                self.assertEqual(hint.winfo_manager(), "place")
        # A click on the hint puts the cursor into its field (the bound script
        # runs directly: generated mouse events do not reach a hidden window).
        script = section.query_placeholder.bind("<Button-1>")
        with mock.patch.object(section.query_entry, "focus_set") as focus:
            self.root.tk.call("catch", re.sub(r"%[#\w]", "0", script.replace(
                "%W", str(section.query_placeholder))))
        focus.assert_called_once_with()

    def test_hint_takes_the_field_color_of_each_state_and_mode(self):
        import marblescape_theme as theme
        entry = self.section.query_entry
        hint = self.section.query_placeholder
        # Shown states only: with focus the hint is hidden.
        expected = {"dark": {"": "#292929", "hover": "#2f2f2f", "disabled": "#262626"},
                    "light": {"": "#fdfdfd", "hover": "#f9f9f9", "disabled": "#fafafa"}}
        for mode, colors in expected.items():
            theme.apply_appearance(self.root, mode)
            self.root.update()
            for state, color in colors.items():
                with self.subTest(mode=mode, state=state):
                    entry.state(["!hover", "!focus", "!disabled"])
                    if state:
                        entry.state([state])
                    self.assertEqual(theme.entry_field_color(entry), color)
                    entry.event_generate("<Configure>")
                    self.root.update()
                    self.assertEqual(str(hint.cget("background")), color)
            entry.state(["!hover", "!focus", "!disabled"])

    def test_status_messages_are_blue_in_both_modes(self):
        import marblescape_theme as theme
        for mode in ("light", "dark"):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            self.assertEqual(str(self.section.status_label.cget("foreground")),
                             theme.PALETTES[mode]["link"], mode)

    def test_attribution_is_always_shown(self):
        labels = [str(widget.cget("text")) for widget in self.descendants(self.section.frame)
                  if isinstance(widget, ttk.Label)]
        self.assertIn("© OpenStreetMap contributors", labels)

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)


class EndpointConfigurationTests(unittest.TestCase):
    def setUp(self):
        import marblescape_download as app
        self.app = app
        self.saved = app.capture_loaded_configuration()
        self.addCleanup(app.restore_loaded_configuration, self.saved)

    def load(self, text):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="marblescape-location-") as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(self.app, "log"):
                self.app.load_configuration(path)

    def test_the_search_endpoint_is_configurable_and_must_be_https(self):
        template = self.app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.load(template)
        self.assertEqual(self.app.LOCATION_SEARCH_ENDPOINT, "https://nominatim.openstreetmap.org/search")
        self.load(self.app.replace_toml_values(
            template, [("service", "location_search_endpoint", "https://example.invalid/search")]))
        self.assertEqual(self.app.LOCATION_SEARCH_ENDPOINT, "https://example.invalid/search")
        # A file without the key gets the default again, not the previous value.
        self.load(template.replace('location_search_endpoint = "https://nominatim.openstreetmap.org/search"', ""))
        self.assertEqual(self.app.LOCATION_SEARCH_ENDPOINT, self.app.DEFAULT_LOCATION_SEARCH_ENDPOINT)
        with self.assertRaises(ValueError):
            self.load(self.app.replace_toml_values(
                template, [("service", "location_search_endpoint", "http://example.invalid/search")]))


if __name__ == "__main__":
    unittest.main()
