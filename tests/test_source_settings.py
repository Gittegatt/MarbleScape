"""Hidden-Tk regression tests with no live NOAA requests.

Run with ``python -m unittest discover -s tests``. Tk tests skip on systems
without Tk or a display server.
"""

from copy import deepcopy
import gc
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk is not None:
    import marblescape_copernicus_settings as source_settings_copernicus
    import marblescape_source_settings as source_settings


class FakeNOAAClient:
    def __init__(self, **kwargs):
        self.calls = []
        self.fail = False
        self.all_refresh_gate = None
        self.all_refresh_started = threading.Event()
        self.all_summary = None
        self.catalogue_refresh_status = {"running": False, "done": 0, "total": 0, "message": "", "error": ""}
        self.retries = 2
        self.copernicus_dates = None
        self.areas = {
            "goes_east": [
                {"id": "full_disk", "label": "Full Disk", "category": "Global"},
                {"id": "test_a", "label": "Austin", "category": "Local"},
                {"id": "test_b", "label": "Boston", "category": "Local"},
                {"id": "storm_one", "label": "Storm One", "category": "Active storms"},
                {"id": "storm_two", "label": "Storm Two", "category": "Active storms"},
            ],
            "goes_west": [
                {"id": "full_disk", "label": "Full Disk", "category": "Global"},
            ],
            "solar": [{"id": "sun", "label": "Sun", "category": "Solar"}],
            "himawari": [
                {"id": "nict_full_disk", "label": "NICT - Full Disk (True Color)",
                 "category": "NICT True Color"},
                {"id": "jma_jpn", "label": "JMA - Japan", "category": "JMA Regions"},
            ],
            "slider": [
                {"id": "goes-19---full_disk", "label": "Full Disk",
                 "category": "GOES-19 (East; 75.2W)"},
                {"id": "gk2a---full_disk", "label": "Full Disk",
                 "category": "GEO-KOMPSAT-2A (128E)"},
            ],
            "worldview": [
                {"id": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
                 "label": "Corrected Reflectance (True Color, VIIRS, NOAA-20)",
                 "category": "Corrected Reflectance"},
            ],
        }

    def list_areas(self, provider, refresh=False):
        self.calls.append(("areas", provider, refresh))
        if self.fail:
            raise OSError("offline fixture")
        return deepcopy(self.areas[provider])

    def list_products(self, provider, area_id, refresh=False):
        self.calls.append(("products", provider, area_id, refresh))
        if self.fail:
            raise OSError("offline fixture")
        if provider == "solar":
            return [{"id": "Fe171", "label": "171 Angstrom", "resolutions": ["300x300", "1200x1200"]}]
        if provider == "himawari":
            return [{"id": "true_color", "label": "True Color",
                     "resolutions": ["550x550", "11000x11000"]}]
        if provider == "slider":
            return [{"id": "geocolor", "label": "GeoColor",
                     "resolutions": ["678x678", "5424x5424", "10848x10848"]}]
        if provider == "worldview":
            return [
                {"id": "latest", "label": "Latest available (currently 2026-09-14)",
                 "resolutions": ["1024x512", "4096x2048", "8192x4096"]},
                {"id": "2026-09-13", "label": "Fixed · 2026-09-13",
                 "resolutions": ["1024x512", "4096x2048", "8192x4096"]},
            ]
        return [
            {"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["678x678", "1808x1808"]},
            {"id": "13", "label": "Infrared", "resolutions": ["678x678", "5424x5424"]},
        ]

    def refresh_all_catalogues(self, refresh=True, progress=None):
        self.calls.append(("all", refresh))
        self.catalogue_refresh_status = {"running": True, "done": 1, "total": 4,
                                          "message": "Fixture areas", "error": ""}
        if progress:
            progress(1, 4, "Fixture areas")
        self.all_refresh_started.set()
        if self.all_refresh_gate is not None:
            if not self.all_refresh_gate.wait(5):
                raise RuntimeError("Fixture release timed out")
        if self.fail:
            self.catalogue_refresh_status = {"running": False, "done": 1, "total": 4,
                                              "message": "Refresh failed", "error": "offline fixture"}
            raise OSError("offline fixture")
        summary = self.all_summary or {"providers": 3, "areas": 7, "products": 13,
                                       "resolution_options": 26, "errors": [], "warning": "", "complete": True}
        self.catalogue_refresh_status = {"running": False, "done": 4, "total": 4,
                                          "message": "All NOAA catalogues are ready.", "error": summary["warning"]}
        if progress:
            progress(4, 4, "All NOAA catalogues are ready.")
        return deepcopy(summary)

    def cached_copernicus_dates(self, _profile, _output_size):
        return deepcopy(self.copernicus_dates)

    def store_copernicus_dates(self, _profile, _output_size, dates):
        self.copernicus_dates = deepcopy(dates)


@unittest.skipIf(tk is None, "Tkinter is not installed")
class SourceSettingsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.patch_client = mock.patch.object(source_settings, "NOAAClient", FakeNOAAClient)
        self.patch_client.start()
        self.controllers = []
        self.threads_before = set(threading.enumerate())

    def tearDown(self):
        for controller in self.controllers:
            controller.close()
            gate = getattr(controller._client, "all_refresh_gate", None)
            if gate is not None:
                gate.set()
        try:
            self._join_workers()
        finally:
            self.root.destroy()
            self.controllers.clear()
            controller = None
            gc.collect()
            self.patch_client.stop()
        self.assertEqual(self.callback_errors, [], "An exception escaped a Tk callback")

    def _join_workers(self):
        for thread in set(threading.enumerate()) - self.threads_before:
            if thread.name.startswith("MarbleScape-catalogue"):
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive(), "A fixture catalogue worker did not finish")

    def make_settings(self, provider="eumetsat", profiles=None, **kwargs):
        if profiles is None:
            profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        kwargs.setdefault("client", FakeNOAAClient())
        controller = source_settings.SourceSettings(self.root, provider, profiles, **kwargs)
        self.controllers.append(controller)
        return controller

    def wait_for_catalogue(self, controller):
        deadline = time.monotonic() + 5
        while controller._loading:
            if time.monotonic() > deadline:
                self.fail("Timed out waiting for the fixture catalogue")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    @staticmethod
    def select_provider(controller, provider):
        controller._provider_var.set(source_settings.image_source_label(provider))
        controller._select_provider()
        if provider in source_settings.GOES_SATELLITES:
            controller._goes_var.set(source_settings.GOES_SATELLITES[provider])
            controller._select_goes_satellite()

    def test_goes_source_groups_satellites_without_losing_selections(self):
        settings = self.make_settings("goes_west")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._provider_combo["values"].count("NOAA GOES"), 1)
        self.assertNotIn("GOES-East", settings._provider_combo["values"])
        self.assertNotIn("GOES-West", settings._provider_combo["values"])
        self.assertEqual(settings._provider_var.get(), "NOAA GOES")
        self.assertEqual(settings._goes_var.get(), "GOES-West")
        self.assertTrue(settings._goes_combo.grid_info())
        settings._goes_var.set("GOES-East")
        settings._select_goes_satellite()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[0], "goes_east")
        self.select_provider(settings, "solar")
        self.wait_for_catalogue(settings)
        self.assertFalse(settings._goes_combo.grid_info())
        settings._provider_var.set("NOAA GOES")
        settings._select_provider()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[0], "goes_east")
        settings.set_selection("goes_west", source_settings.DEFAULT_PROFILES)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._goes_var.get(), "GOES-West")
        self.assertEqual(settings.get_selection()[0], "goes_west")

    def test_eumetsat_needs_no_catalogue_and_init_does_not_call_on_change(self):
        changes = []
        settings = self.make_settings(on_change=changes.append)
        self.assertEqual(changes, [])
        self.assertEqual(settings._client.calls, [])
        self.assertEqual(settings.get_selection()[0], "eumetsat")
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(changes, ["goes_east"])

    def test_hidden_eumetsat_update_does_not_restore_its_view(self):
        changes = []
        settings = self.make_settings(on_change=changes.append)
        self.select_provider(settings, "copernicus")
        self.assertEqual(changes, ["copernicus"])
        settings._eumetsat_changed()
        self.assertEqual(changes, ["copernicus"])
        self.select_provider(settings, "eumetsat")
        settings._eumetsat_changed()
        self.assertEqual(changes, ["copernicus", "eumetsat", "eumetsat"])

    def test_default_new_source_waits_for_catalogue_validation(self):
        settings = self.make_settings("goes_east", profiles={})
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "auto")

    def test_every_new_resolution_source_starts_on_automatic(self):
        settings = self.make_settings("goes_east", profiles={})
        for provider in ("goes_east", "goes_west", "solar", "himawari", "slider", "worldview"):
            with self.subTest(provider=provider):
                if provider != "goes_east":
                    self.select_provider(settings, provider)
                self.wait_for_catalogue(settings)
                self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
                self.assertEqual(settings.get_selection()[1][provider]["resolution"], "auto")

    def test_himawari_auto_default_and_catalogue_activity(self):
        settings = self.make_settings("himawari")
        self.assertTrue(settings._catalogue_activity.active)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["himawari"]["resolution"], "auto")
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
        self.assertEqual(settings._catalogue_activity.completion.get(), "Completed.")
        settings._refresh()
        self.assertTrue(settings._catalogue_activity.active)
        self.assertEqual(settings._catalogue_activity.completion.get(), "")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._catalogue_activity.completion.get(), "Completed.")

    def test_eumetsat_catalogue_activity_completes_without_network(self):
        from marblescape_eumetsat import _FALLBACK_ITEM

        client = FakeNOAAClient()
        client.eumetsat = SimpleNamespace(catalogue=lambda refresh=False: [dict(_FALLBACK_ITEM)])
        settings = self.make_settings(client=client)
        eumetsat = settings.eumetsat_settings
        self.assertTrue(eumetsat._activity.active)
        deadline = time.monotonic() + 3
        while eumetsat._activity.active:
            if time.monotonic() > deadline:
                self.fail("EUMETSAT catalogue fixture did not complete")
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(eumetsat._activity.completion.get(), "Completed.")

    def test_eumetsat_lists_announced_products_only_in_the_info_line(self):
        from marblescape_eumetsat import _FALLBACK_ITEM

        announced = {"id": "EO:EUM:DAT:EPSSG01", "label": "epssg:m01_metimage_ir1069",
                     "layer": "epssg:m01_metimage_ir1069", "satellite": "", "mission": "",
                     "product_type": "", "themes": (), "orbit_type": "LEO",
                     "single_overpass": False, "available": False}
        catalogue = [dict(_FALLBACK_ITEM), announced]
        client = FakeNOAAClient()
        client.eumetsat = SimpleNamespace(catalogue=lambda refresh=False: [dict(item) for item in catalogue])
        eumetsat = self.make_settings(client=client).eumetsat_settings

        def loaded():
            deadline = time.monotonic() + 3
            while eumetsat._activity.active:
                if time.monotonic() > deadline:
                    self.fail("EUMETSAT catalogue fixture did not complete")
                self.root.update()
                time.sleep(0.01)
            self.root.update()

        loaded()
        self.assertEqual(eumetsat._announced_var.get(),
                         "Listed by EUMETSAT, not available yet: Metop-SG m01_metimage_ir1069")
        self.assertTrue(eumetsat._announced_label.grid_info())
        self.assertIn("MTG - 0 Degree", eumetsat._satellite_combo.cget("values"))
        self.assertNotIn("", eumetsat._satellite_combo.cget("values"))
        self.assertFalse(any("epssg" in value for value in eumetsat._layer_combo.cget("values")))
        self.assertIn("EUMETSAT catalogue loaded: 1 product.", eumetsat._status_var.get())
        # Once EUMETSAT publishes it, the line disappears.
        catalogue.pop()
        eumetsat.refresh(True)
        loaded()
        self.assertEqual(eumetsat._announced_var.get(), "")
        self.assertFalse(eumetsat._announced_label.grid_info())

    def test_area_filter_does_not_change_the_selected_area(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        settings._category_var.set("Local")
        settings._select_category()
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        settings._filter_var.set("bOsToN")
        self.assertEqual(list(settings._area_by_label), ["Boston [test_b]"])
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_a")
        # Clear, as in the Profiles tab, empties the filter and lists every area again.
        self.assertFalse(settings._filter_clear_button.instate(["disabled"]))
        settings._filter_clear_button.invoke()
        self.assertEqual(settings._filter_var.get(), "")
        self.assertGreater(len(settings._area_by_label), 1)
        settings._filter_var.set("bOsToN")
        settings._area_var.set("Boston [test_b]")
        settings._select_area()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_b")

    def test_product_sizes_and_profiles_are_retained_without_mutating_input(self):
        profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        original = deepcopy(profiles)
        settings = self.make_settings("goes_east", profiles=profiles)
        self.wait_for_catalogue(settings)
        settings._product_var.set("Infrared [13]")
        settings._select_product()
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "auto")
        settings._resolution_var.set("5424x5424")
        settings._select_resolution()
        self.select_provider(settings, "solar")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._product_label["text"], "Channel")
        self.assertFalse(settings._area_combo.winfo_manager())
        self.assertEqual(settings.get_selection()[1]["solar"]["resolution"], "auto")
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"],
                         {"area": "full_disk", "product": "13", "resolution": "5424x5424"})
        self.assertEqual(profiles, original)
        result = settings.get_selection()[1]
        result["goes_east"]["product"] = "mutated"
        self.assertEqual(settings.get_selection()[1]["goes_east"]["product"], "13")

    def test_cached_goes_catalogue_is_usable_during_offline_refresh(self):
        class CachedClient(FakeNOAAClient):
            def cached_areas(self, provider):
                return deepcopy(self.areas[provider])

            def cached_products(self, provider, _area_id):
                return [{"id": "GEOCOLOR", "label": "GeoColor",
                         "resolutions": ["678x678", "1808x1808"]}]

            def catalogue_offline(self, _provider):
                return True

        client = CachedClient()
        client.fail = True
        client.catalogue_refresh_status["running"] = True
        settings = self.make_settings("goes_east", client=client)
        self.assertEqual(client.calls, [])
        for combo in (settings._area_combo, settings._product_combo,
                      settings._resolution_combo):
            self.assertEqual(str(combo["state"]), "readonly")
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")

        self.select_provider(settings, "goes_west")
        self.assertEqual(client.calls, [])
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
        self.assertEqual(str(settings._area_combo["state"]), "readonly")
        self.assertEqual(str(settings._product_combo["state"]), "readonly")

        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertEqual(str(settings._area_combo["state"]), "readonly")
        self.assertEqual(str(settings._product_combo["state"]), "readonly")
        self.assertEqual(str(settings._resolution_combo["state"]), "readonly")

    def test_himawari_new_area_prefers_true_color_reproduction(self):
        settings = self.make_settings("himawari")
        self.wait_for_catalogue(settings)
        settings._profiles["himawari"]["product"] = ""
        settings._receive_products([
            {"id": "dnc", "label": "Natural Color RGB", "resolutions": ["800x600"]},
            {"id": "b13", "label": "Infrared", "resolutions": ["800x600"]},
            {"id": "trm", "label": "True Color Reproduction Image", "resolutions": ["800x600"]},
        ])
        self.assertEqual(settings._profiles["himawari"]["product"], "trm")

    def test_worldview_new_layer_category_prefers_true_color(self):
        client = FakeNOAAClient()
        client.areas["worldview"].extend([
            {"id": "VIIRS_NDVI", "label": "Vegetation index", "category": "Other"},
            {"id": "MODIS_TrueColor", "label": "Corrected Reflectance (True Color)", "category": "Other"},
        ])
        settings = self.make_settings("worldview", client=client)
        self.wait_for_catalogue(settings)
        settings._category_var.set("Other")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._profiles["worldview"]["area"], "MODIS_TrueColor")

    def test_single_document_sources_refresh_metadata_only_once(self):
        for provider in ("slider", "worldview"):
            with self.subTest(provider=provider):
                client = FakeNOAAClient()
                settings = self.make_settings(provider, client=client)
                self.wait_for_catalogue(settings)
                client.calls.clear()
                settings._refresh()
                self.wait_for_catalogue(settings)
                self.assertIn(("areas", provider, True), client.calls)
                self.assertIn(("products", provider, settings._profiles[provider]["area"], False), client.calls)
                settings.close()

    def test_copernicus_configuration_defaults_to_true_color_layer(self):
        settings = self.make_settings("copernicus")
        copernicus = settings.copernicus_settings
        copernicus._configuration_var.set("Wildfires")
        copernicus._select_configuration()
        self.assertEqual(copernicus._mission_var.get(), "Sentinel-2")
        self.assertEqual(copernicus._selected_product()["name"], "Wildfires (S2L2A)")
        self.assertEqual(copernicus._selected_layer()["name"].casefold(), "true color")

    def test_switching_auto_recommendation_off_never_leaves_controls_greyed_out(self):
        """From every kind of layer with the rule on, to every kind with it off: the
        date, period, Gap fill and cloud controls are as if the rule had never been on."""
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        missions = ("Sentinel-2 Mosaics", "Sentinel-1 Mosaics", "Sentinel-2", "Sentinel-1",
                    "Sentinel-5P", "Copernicus DEM")

        def select(mission):
            cop._mission_var.set(mission)
            cop._select_mission()

        def auto(on):
            cop._auto_var.set("Yes" if on else "No")
            cop._select_auto()

        def states():
            widgets = {"date": cop._date_combo, "period": cop._quarter_mode_combo,
                       "periods back": cop._quarter_offset_combo, "gap fill": cop._coverage_combo,
                       "lookback": cop._lookback_combo, "cloud": cop._cloud_scale}
            return {name: str(widget.cget("state")) for name, widget in widgets.items()}

        for end in missions:
            auto(False)
            select(end)
            expected = states()
            for start in missions:
                with self.subTest(start=start, end=end):
                    select(start)
                    auto(True)
                    select(end)
                    auto(False)
                    self.assertEqual(states(), expected)
                    # And back on: what the rule chooses is greyed out again.
                    if str(cop._auto_combo.cget("state")) != "disabled":
                        auto(True)
                        locked = states()
                        for name in ("date", "period", "periods back", "gap fill", "lookback"):
                            self.assertEqual(locked[name], "disabled", name)
                        auto(False)
                        self.assertEqual(states(), expected)

    def test_every_way_into_the_form_ends_unlocked_once_the_rule_is_off(self):
        """Period modes, more missions, Example scene, Find location and Compare
        variants' Apply, each done while the rule is on, then the rule switched off:
        the controls are as if the rule had never been on."""
        import datetime as dt
        from marblescape_copernicus import DEFAULT_PROFILE
        from marblescape_copernicus_advice import Variant
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings

        def mission(name):
            cop._mission_var.set(name)
            cop._select_mission()

        def mode(label):
            cop._quarter_mode_var.set(label)
            cop._select_quarter_mode()

        def auto(on):
            cop._auto_var.set("Yes" if on else "No")
            cop._select_auto()

        def scene():
            cop._configuration_var.set("Wildfires")
            cop._select_configuration()
            cop._highlight_var.set(tuple(cop._highlight_combo.cget("values"))[1])
            cop._select_highlight()

        def apply(variant):
            cop.apply_recommendation(variant, 48.1, 11.6, 11)

        def states():
            widgets = {"date": cop._date_combo, "period": cop._quarter_mode_combo,
                       "periods back": cop._quarter_offset_combo, "gap fill": cop._coverage_combo,
                       "lookback": cop._lookback_combo, "cloud": cop._cloud_scale}
            return {name: str(widget.cget("state")) for name, widget in widgets.items()}

        quarter = dt.date(2026, 4, 1)
        ways = {
            "quarterly, specific quarter": (lambda: mission("Sentinel-2 Mosaics"),
                                            lambda: mode("Specific quarter")),
            "quarterly, relative to now": (lambda: mission("Sentinel-2 Mosaics"),
                                           lambda: mode("Relative to now")),
            "monthly, relative to now": (lambda: mission("Sentinel-1 Mosaics"),
                                         lambda: mode("Relative to now")),
            "Sentinel-3": (lambda: mission("Sentinel-3"),),
            "Landsat 8/9": (lambda: mission("Landsat 8/9"),),
            "Example scene": (scene,),
            "Find location": (lambda: mission("Sentinel-2"), lambda: cop.set_location(48.1, 11.6)),
            "Compare variants, Gap fill": (lambda: mission("Sentinel-2"),
                                           lambda: apply(Variant(30, "fill_gaps", 14))),
            "Compare variants, older quarter": (
                lambda: mission("Sentinel-2 Mosaics"),
                lambda: apply(Variant(None, "single", None, quarter, "quarter", 2))),
        }
        for name, actions in ways.items():
            with self.subTest(way=name):
                cop.set_profile(dict(DEFAULT_PROFILE, auto_recommendation=False))
                for action in actions:
                    action()
                expected = states()
                cop.set_profile(dict(DEFAULT_PROFILE, auto_recommendation=False))
                auto(True)
                for action in actions:
                    action()
                auto(False)
                self.assertEqual(states(), expected)
                # The chosen period mode can be used: its list or "back" is selectable.
                if "relative" in name:
                    self.assertEqual(states()["periods back"], "readonly")
                if "specific" in name or "older quarter" in name:
                    self.assertEqual(states()["period"], "readonly")

    def test_date_and_zoom_lists_follow_place_zoom_size_and_cloud_limit(self):
        """A fixed date the new cloud limit or place does not offer becomes Latest
        available with a note, instead of rendering a picture without data."""
        client = FakeNOAAClient()
        lists = {100: ["2026-09-28", "2026-09-20"], 20: ["2026-09-20"]}
        asked = []

        def cached(profile, _size):
            asked.append((profile["max_cloud_cover"], profile["latitude"]))
            return list(lists.get(profile["max_cloud_cover"], []))

        client.cached_copernicus_dates = cached
        settings = self.make_settings("copernicus", client=client)
        cop = settings.copernicus_settings
        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        cop._update_date_choices(lists[100], "2026-09-28")
        cop._last_date_inputs = cop._date_inputs()
        self.assertEqual(cop._date_var.get(), "2026-09-28")
        changes = []
        cop._on_change = lambda *_args: changes.append(cop.get_profile()["date"])

        # The slider is let go at 20%: the list reloads; 2026-09-28 is not in it.
        cop._cloud_var.set(20)
        cop._schedule_inputs_refresh()
        cop._refresh_for_inputs()
        self.assertEqual(asked[-1][0], 20)
        self.assertEqual(cop._date_var.get(), "Latest available")
        self.assertIn("2026-09-28 is not available", cop._status_var.get())
        self.assertEqual(tuple(cop._date_combo.cget("values")), ("Latest available", "2026-09-20"))
        self.assertEqual(changes[-1], "latest")
        # A date the new list still offers stays.
        cop._update_date_choices(lists[20], "2026-09-20")
        cop._latitude_var.set("35.7")
        cop._refresh_for_inputs()
        self.assertEqual(asked[-1], (20, 35.7))
        self.assertEqual(cop._date_var.get(), "2026-09-20")
        # Unchanged inputs reload nothing, so a refresh never repeats itself.
        count = len(asked)
        cop._refresh_for_inputs()
        cop._refresh_for_inputs()
        self.assertEqual(len(asked), count)
        # The zoom list follows the latitude: near the pole a low zoom no longer fits.
        cop._zoom_var.set("10")
        cop._latitude_var.set("84.9")
        cop._refresh_for_inputs()
        self.assertIn(cop._zoom_var.get(), tuple(cop._zoom_combo.cget("values")))
        # Typing, dragging and the picture size all schedule the reload.
        for trigger in (lambda: cop._latitude_var.set("35.8"), lambda: cop._image_size_var.set("2560x1440")):
            cop._date_refresh_after = None
            trigger()
            self.assertIsNotNone(cop._date_refresh_after)
        self.assertIn("<ButtonRelease-1>", cop._cloud_scale.bind())

    def test_cloud_hint_explains_the_limit_for_a_fixed_date(self):
        from marblescape_copernicus_settings import FIXED_DATE_CLOUD_HINT, LATEST_LABEL
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        cop._date_var.set(LATEST_LABEL)
        self.assertNotIn(FIXED_DATE_CLOUD_HINT, cop._cloud_hint.cget("text"))
        cop._date_var.set("2026-10-03")
        self.assertIn(FIXED_DATE_CLOUD_HINT, cop._cloud_hint.cget("text"))
        # Layers without a cloud filter keep their own note.
        cop._mission_var.set("Sentinel-2 Mosaics")
        cop._select_mission()
        self.assertNotIn(FIXED_DATE_CLOUD_HINT, cop._cloud_hint.cget("text"))

    def test_loading_a_profile_without_auto_recommendation_unlocks_its_controls(self):
        """Load or Apply of a profile with the rule off, after one with it on."""
        from marblescape_copernicus import DEFAULT_PROFILE, products
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        profiles = []
        for mission in ("Sentinel-2 Mosaics", "Sentinel-1 Mosaics", "Sentinel-2", "Sentinel-1"):
            product = products("DEFAULT-THEME", mission)[0]
            profiles.append(dict(DEFAULT_PROFILE, configuration="DEFAULT-THEME", mission=mission,
                                 product=product["id"], layer=product["layers"][0]["id"],
                                 date="latest", date_mode="catalogue", map_zoom=11))

        def states():
            widgets = (cop._date_combo, cop._quarter_mode_combo, cop._quarter_offset_combo,
                       cop._coverage_combo, cop._lookback_combo, cop._cloud_scale)
            return [str(widget.cget("state")) for widget in widgets]

        for end in profiles:
            cop.set_profile(dict(end, auto_recommendation=False))
            expected = states()
            for start in profiles:
                with self.subTest(start=start["mission"], end=end["mission"]):
                    cop.set_profile(dict(start, auto_recommendation=True))
                    self.assertEqual(states()[3:5], ["disabled", "disabled"])
                    cop.set_profile(dict(end, auto_recommendation=False))
                    self.assertEqual(states(), expected)

    def test_example_scene_row_shows_only_with_scenes_and_loads_one(self):
        settings = self.make_settings("copernicus")
        copernicus = settings.copernicus_settings
        combo = copernicus._highlight_combo
        self.assertEqual(combo.label_widget.cget("text"), "Example scene")
        copernicus._configuration_var.set("Default")
        copernicus._select_configuration()
        # Default has no scenes: the row is hidden, its value None.
        self.assertFalse(combo.winfo_manager() or combo.label_widget.winfo_manager())
        self.assertEqual(copernicus._highlight_var.get(), "None")
        copernicus._configuration_var.set("Wildfires")
        copernicus._select_configuration()
        self.assertTrue(combo.winfo_manager() and combo.label_widget.winfo_manager())
        values = tuple(combo.cget("values"))
        self.assertEqual(values[0], "None")
        copernicus._highlight_var.set(values[1])
        copernicus._select_highlight()
        self.assertEqual(copernicus.get_profile()["highlight"], copernicus._highlight_by_label[values[1]]["id"])
        self.assertIn("Example scene loaded", copernicus._status_var.get())
        self.assertIn("Latest available", copernicus._status_var.get())

    def test_copernicus_new_configuration_avoids_gas_and_keeps_saved_false_color(self):
        settings = self.make_settings("copernicus")
        copernicus = settings.copernicus_settings
        copernicus._mission_var.set("Sentinel-5P")
        copernicus._select_mission()
        copernicus._configuration_var.set("Wildfires")
        copernicus._select_configuration()
        self.assertEqual(copernicus._mission_var.get(), "Sentinel-2")
        false_color = next(item for item in copernicus._selected_product()["layers"]
                           if item["name"].casefold() == "false color")
        saved = dict(source_settings.DEFAULT_COPERNICUS_PROFILE)
        saved.update(configuration="WILDFIRES", mission="Sentinel-2",
                     product=copernicus._selected_product()["id"], layer=false_color["id"])
        copernicus.set_profile(saved)
        self.assertEqual(copernicus._selected_layer()["id"], false_color["id"])

    def test_himawari_uses_shared_area_product_and_resolution_controls(self):
        settings = self.make_settings("himawari")
        self.wait_for_catalogue(settings)
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "himawari")
        self.assertEqual(profiles["himawari"], {
            "area": "nict_full_disk", "product": "true_color", "resolution": "auto",
            "shorelines": False, "shoreline_color": "#FFFF00",
            "center": False, "latitude": 0.0, "longitude": 140.7,
        })
        self.assertTrue(settings._area_combo.winfo_manager())
        self.assertIn("Largest available (11000 × 11000)", settings._resolution_combo["values"])
        self.assertEqual(settings._product_label["text"], "Product / layer")

    def test_himawari_shoreline_and_centre_controls(self):
        client = FakeNOAAClient()
        client.areas["himawari"].append({"id": "jma_storm_TC2634", "label": "Severe Tropical Storm Koguma",
                                         "category": "Active storms"})
        settings = self.make_settings("himawari", client=client)
        self.wait_for_catalogue(settings)

        def shown(widget):
            return widget.winfo_manager() == "grid"

        # Plot shorelines in Rendering and Center on coordinates for the NICT full disk.
        self.assertTrue(shown(settings.himawari_rendering_frame))
        self.assertTrue(shown(settings.himawari_center_frame))
        self.assertEqual(str(settings._shoreline_button["state"]), "disabled")
        self.assertEqual(str(settings._center_entries[0]["state"]), "disabled")
        settings._shorelines_var.set(True)
        settings._himawari_option_changed()
        self.assertEqual(str(settings._shoreline_button["state"]), "normal")
        # Find location's Transfer fills the coordinates and switches the centre on.
        settings._transfer_location(35.68, 139.77)
        self.assertEqual(str(settings._center_entries[0]["state"]), "normal")
        profile = settings.get_selection()[1]["himawari"]
        self.assertEqual({key: profile[key] for key in ("shorelines", "shoreline_color", "center",
                                                        "latitude", "longitude")},
                         {"shorelines": True, "shoreline_color": "#FFFF00", "center": True,
                          "latitude": 35.68, "longitude": 139.77})
        settings._latitude_var.set("35,5")  # A decimal comma works too.
        self.assertEqual(settings.get_selection()[1]["himawari"]["latitude"], 35.5)
        settings._latitude_var.set("north")
        with self.assertRaisesRegex(ValueError, "latitude"):
            settings.get_selection()
        settings._latitude_var.set("0")
        settings._longitude_var.set("-40")
        with self.assertRaisesRegex(ValueError, "cannot see"):
            settings.get_selection()
        settings._longitude_var.set("139")
        settings.get_selection()
        # The saved values come back into the controls.
        settings.set_selection("himawari", settings.get_selection()[1])
        self.wait_for_catalogue(settings)
        self.assertTrue(settings._center_var.get())
        self.assertEqual(settings._longitude_var.get(), "139")
        # A storm keeps the shorelines; its own position is the centre.
        settings._category_var.set("Active storms")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertTrue(shown(settings.himawari_rendering_frame))
        self.assertFalse(shown(settings.himawari_center_frame))
        self.assertTrue(shown(settings._storm_note))
        self.assertIn("centred on the storm", settings._storm_note.cget("text"))
        # JMA's regional stills have neither.
        settings._category_var.set("JMA Regions")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertFalse(shown(settings.himawari_rendering_frame))
        self.assertFalse(shown(settings.himawari_center_frame))
        self.assertFalse(shown(settings._storm_note))
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertFalse(shown(settings.himawari_rendering_frame))
        self.assertFalse(shown(settings.himawari_center_frame))

    def test_refresh_catalogue_reports_how_it_went(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        reports = []
        settings.add_catalogue_listener(lambda label, problem: reports.append((label, problem)))
        # Loading a catalogue without Refresh catalogue reports nothing.
        settings.set_selection("goes_west", settings.get_selection()[1])
        self.wait_for_catalogue(settings)
        self.assertEqual(reports, [])
        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertEqual(reports, [(source_settings.image_source_label("goes_west"), "")])
        settings._client.catalogue_warning = "One WFO page failed"
        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertEqual(reports[-1][1], "One WFO page failed")
        settings._client.fail = True
        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertIn("offline fixture", reports[-1][1])
        self.assertEqual(len(reports), 3)

    def test_eumetsat_and_copernicus_refresh_catalogue_report_too(self):
        settings = self.make_settings("eumetsat")
        reports = []
        settings.add_catalogue_listener(lambda label, problem: reports.append((label, problem)))
        eumetsat = settings.eumetsat_settings

        def started(widget):
            def refresh(*_args):
                widget._generation += 1
            return refresh

        with mock.patch.object(eumetsat, "refresh", side_effect=started(eumetsat)):
            eumetsat._refresh_clicked()
        eumetsat._queue.put((eumetsat._generation, None, "EUMETSAT is offline"))
        eumetsat._poll()
        self.assertEqual(reports, [("EUMETSAT", "EUMETSAT is offline")])
        # An automatic load reports nothing.
        eumetsat._generation += 1
        eumetsat._queue.put((eumetsat._generation, None, "offline again"))
        eumetsat._poll()
        self.assertEqual(len(reports), 1)

        copernicus = settings.copernicus_settings
        with mock.patch.object(copernicus, "refresh_dates", side_effect=started(copernicus)):
            copernicus._refresh_clicked()
        copernicus._results.put((copernicus._generation, ["2026-10-01"], None, False))
        copernicus._poll()
        self.assertEqual(reports[-1], (source_settings.image_source_label("copernicus"), ""))
        with mock.patch.object(copernicus, "refresh_dates", side_effect=started(copernicus)):
            copernicus._refresh_clicked()
        copernicus._results.put((copernicus._generation, None, "Copernicus is offline", False))
        copernicus._poll()
        self.assertEqual(reports[-1][1], "Copernicus is offline")
        copernicus._generation += 1
        copernicus._results.put((copernicus._generation, ["2026-10-02"], None, False))
        copernicus._poll()
        self.assertEqual(len(reports), 3)

    def test_slider_uses_satellite_sector_product_and_clean_tiles_note(self):
        settings = self.make_settings("slider")
        self.wait_for_catalogue(settings)
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "slider")
        self.assertEqual(profiles["slider"], {
            "area": "goes-19---full_disk", "product": "geocolor",
            "resolution": "auto",
        })
        self.assertEqual(settings._category_label["text"], "Satellite")
        self.assertEqual(settings._area_label["text"], "Sector")
        self.assertTrue(settings._slider_note.winfo_manager())
        self.assertIn("without map borders", settings._slider_note["text"])

    def test_slider_products_show_before_the_first_padding_measurement(self):
        import marblescape_slider as slider_module
        area = "goes-19---full_disk"
        store = dict(slider_module._CONTENT_BOX_STORE)
        self.addCleanup(slider_module._CONTENT_BOX_STORE.update, store)
        slider_module._CONTENT_BOX_STORE["path"] = None  # Never write the real store.
        self.addCleanup(slider_module._CONTENT_BOXES.pop, area, None)
        slider_module._CONTENT_BOXES.pop(area, None)
        self.addCleanup(source_settings._SLIDER_MEASURE_FAILED.clear)
        gate = threading.Event()
        measured = []

        def content_box(area_id, product_id):
            measured.append((area_id, product_id))
            gate.wait(5)
            slider_module._remember_content_box(area_id, (0.0, 0.25, 1.0, 0.75))

        client = FakeNOAAClient()
        client.content_box = content_box
        settings = self.make_settings("slider", client=client)
        self.wait_for_catalogue(settings)
        # The products are listed while the sector is still being measured.
        self.assertIn("Largest available (10848 × 10848)", settings._resolution_combo["values"])
        gate.set()
        deadline = time.monotonic() + 5
        while "Largest available (10848 × 5424)" not in settings._resolution_combo["values"]:
            self.assertLess(time.monotonic(), deadline, "The measured sizes did not arrive")
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(measured, [(area, "geocolor")])
        self.assertEqual(settings.get_selection()[1]["slider"]["resolution"], "auto")

    def test_a_failed_slider_measurement_waits_before_it_is_tried_again(self):
        import marblescape_slider as slider_module
        area = "goes-19---conus"
        self.addCleanup(source_settings._SLIDER_MEASURE_FAILED.clear)
        self.addCleanup(slider_module._CONTENT_BOXES.pop, area, None)
        calls = []

        def content_box(area_id, product_id):
            calls.append(area_id)
            raise OSError("offline")

        client = SimpleNamespace(content_box=content_box)
        products = [{"id": "geocolor", "resolutions": ["5000x3000"]}]
        for _attempt in range(3):
            self.assertEqual(source_settings._with_slider_sizes(client, area, products), products)
        self.assertEqual(calls, [area])
        with mock.patch.object(source_settings.time, "monotonic",
                               return_value=time.monotonic() + source_settings.SLIDER_MEASURE_RETRY_SECONDS + 1):
            source_settings._with_slider_sizes(client, area, products)
        self.assertEqual(calls, [area, area])

    def test_worldview_category_filter_narrows_only_the_category_list(self):
        client = FakeNOAAClient()
        client.areas["worldview"] += [
            {"id": "OMPS_Ozone", "label": "Ozone (OMPS)", "category": "Ozone"},
            {"id": "MODIS_AOD", "label": "Aerosol Optical Depth", "category": "Aerosol"},
        ]
        settings = self.make_settings("worldview", client=client)
        self.wait_for_catalogue(settings)
        every = ["Corrected Reflectance", "Ozone", "Aerosol"]
        self.assertEqual(list(settings._category_combo["values"]), every)
        settings._category_filter_var.set("OZ")
        self.assertEqual(list(settings._category_combo["values"]), ["Ozone"])
        # The shown category and layer stay until another one is chosen.
        self.assertEqual(settings._category_var.get(), "Corrected Reflectance")
        self.assertEqual(settings.get_selection()[1]["worldview"]["area"],
                         "VIIRS_NOAA20_CorrectedReflectance_TrueColor")
        settings._category_filter_clear_button.invoke()
        self.assertEqual(list(settings._category_combo["values"]), every)
        # Above Layer category, and only for NASA Worldview.
        self.assertLess(int(settings._category_filter_label.grid_info()["row"]),
                        int(settings._category_label.grid_info()["row"]))
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._category_filter_label.winfo_manager(), "")
        self.assertEqual(settings._category_filter_var.get(), "")

    def test_worldview_uses_layer_date_and_render_resolution_controls(self):
        settings = self.make_settings("worldview")
        self.wait_for_catalogue(settings)
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "worldview")
        self.assertEqual(profiles["worldview"], {
            "area": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
            "product": "latest", "resolution": "auto",
        })
        self.assertEqual(settings._category_label["text"], "Layer category")
        # Two filters, each named after the list it narrows.
        self.assertEqual(settings._category_filter_label["text"], "Filter categories")
        self.assertTrue(settings._category_filter_label.winfo_manager())
        self.assertEqual(settings._filter_label["text"], "Filter imagery layers")
        self.assertEqual(settings._area_label["text"], "Imagery layer")
        self.assertEqual(settings._product_label["text"], "Date / time")
        self.assertEqual(settings._resolution_label["text"], "Render resolution")
        self.assertIn("Largest available (8192 × 4096)", settings._resolution_combo["values"])
        self.assertIn("latest available acquisition", settings._status_var.get())
        # One pattern for every source: source, "catalogue loaded", what it lists.
        self.assertTrue(settings._status_var.get().startswith(
            f"NASA Worldview catalogue loaded: {len(settings._areas)} layer"))

    def test_offline_refresh_preserves_a_saved_complete_selection(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._status_var.get(), (
            f"NOAA GOES catalogue loaded: {len(settings._areas)} area{'' if len(settings._areas) == 1 else 's'}, "
            f"{len(settings._products)} product{'' if len(settings._products) == 1 else 's'} "
            "for the selected area."))
        before = settings.get_selection()
        settings._client.fail = True
        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection(), before)
        self.assertEqual(settings._status_var.get(),
                         "NOAA GOES catalogue unavailable: offline fixture. The saved selection stays usable.")
        self.assertEqual(str(settings._refresh_button["state"]), "normal")
        self.assertIn(("areas", "goes_east", True), settings._client.calls)

    def test_failed_changed_selection_cannot_save_or_corrupt_inactive_profile(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        before = settings.get_selection()[1]["goes_east"]
        settings._client.fail = True
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.select_provider(settings, "eumetsat")
        self.assertEqual(settings.get_selection()[1]["goes_east"], before)

    def test_missing_saved_area_requires_explicit_replacement(self):
        profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        profiles["goes_east"]["area"] = "retired_storm"
        settings = self.make_settings("goes_east", profiles=profiles)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._area_var.get(), "retired_storm")
        self.assertEqual(settings.get_selection()[1]["goes_east"], profiles["goes_east"])
        self.assertIn("no longer listed", settings._status_var.get())
        self.assertIn("new images may be unavailable", settings._status_var.get())
        self.assertEqual(str(settings._area_combo["state"]), "readonly")
        self.assertTrue(settings._area_combo["values"])
        self.assertEqual(settings._client.calls, [("areas", "goes_east", True)])
        settings._category_var.set("Local")
        settings._select_category()
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_a")

    def test_superseded_queued_requests_do_not_call_noaa(self):
        settings = self.make_settings()
        settings._client_lock.acquire()
        try:
            for provider in ("goes_east", "goes_west", "solar"):
                self.select_provider(settings, provider)
        finally:
            settings._client_lock.release()
        self.wait_for_catalogue(settings)
        self._join_workers()
        self.assertEqual(settings._client.calls,
                         [("areas", "solar", True), ("products", "solar", "sun", True)])
        self.assertEqual(settings.provider, "solar")
        self.assertEqual([area["id"] for area in settings._areas], ["sun"])

    def test_old_completed_results_cannot_replace_new_provider(self):
        settings = self.make_settings()
        old_generation = settings._generation
        self.select_provider(settings, "solar")
        settings._results.put((old_generation, "goes_east", "areas",
                               [{"id": "wrong", "label": "Wrong", "category": "Wrong"}],
                               None, False))
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.provider, "solar")
        self.assertEqual([area["id"] for area in settings._areas], ["sun"])

    def test_partial_catalogue_warning_is_visible_and_selection_stays_usable(self):
        settings = self.make_settings()
        settings._client.catalogue_warning = "Some local NOAA areas could not be loaded."
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertIn(settings._client.catalogue_warning, settings._status_var.get())
        self.assertEqual(settings.get_selection()[0], "goes_east")

    def test_active_storms_show_a_note_only_while_chosen(self):
        settings = self.make_settings()

        def shown():
            return settings._storm_note.winfo_manager() == "grid"

        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertFalse(shown())
        settings._category_var.set("Active storms")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertTrue(shown())
        self.assertIn("LOST", settings._storm_note.cget("text"))
        # Indented below the area list like the Zoom note, in the normal text color.
        self.assertEqual(int(settings._storm_note.grid_info()["column"]), 1)
        self.assertEqual(str(settings._storm_note.cget("foreground")), "")
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertFalse(shown())
        settings._category_var.set("Active storms")
        settings._select_category()
        self.wait_for_catalogue(settings)
        for provider in ("himawari", "goes_west", "solar"):
            with self.subTest(provider=provider):
                self.select_provider(settings, provider)
                self.wait_for_catalogue(settings)
                self.assertFalse(shown())

    def test_close_cancels_queued_requests_and_poll_timer(self):
        settings = self.make_settings()
        settings._client_lock.acquire()
        try:
            self.select_provider(settings, "goes_east")
            settings.close()
        finally:
            settings._client_lock.release()
        self._join_workers()
        self.assertEqual(settings._client.calls, [])
        self.assertIsNone(settings._after_id)
        settings.close()

    def test_largest_option_tracks_product_size_and_keeps_concrete_saved_size(self):
        profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        profiles["goes_east"]["resolution"] = "678x678"
        settings = self.make_settings("goes_east", profiles)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._resolution_var.get(), "678 × 678")
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "678x678")
        self.assertIn("Largest available (1808 × 1808)", settings._resolution_combo["values"])
        settings._product_var.set("Infrared [13]")
        settings._select_product()
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "auto")
        # Automatic names its size once the host's rule is known.
        self.assertEqual(settings._resolution_label.cget("text"), "Source resolution")
        offered = []
        settings.set_automatic_resolution(lambda choices: offered.append(choices) or min(
            choices, key=lambda item: item[1] * item[2])[0])
        smallest = min(offered[-1], key=lambda item: item[1] * item[2])
        self.assertEqual(settings._resolution_label.cget("text"),
                         f"Source resolution ({smallest[1]} × {smallest[2]})")
        settings._resolution_var.set("678x678")
        settings._select_resolution()
        self.assertEqual(settings._resolution_label.cget("text"), "Source resolution (678 × 678)")
        settings._resolution_var.set("Largest available (5424 × 5424)")
        settings._select_resolution()
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "largest")
        self.assertEqual(settings._resolution_label.cget("text"), "Source resolution (5424 × 5424)")
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")

    def test_shared_client_and_eumetsat_preset_container(self):
        client = FakeNOAAClient()
        settings = self.make_settings(client=client)
        self.assertIs(settings._client, client)
        self.assertEqual(settings.eumetsat_frame.grid_info()["row"], 1)
        self.assertTrue(settings.eumetsat_frame.winfo_manager())
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertFalse(settings.eumetsat_frame.winfo_manager())
        self.assertEqual(settings._filter_label["text"], "Filter areas")
        # The help text is the empty field's grey placeholder, not a line below.
        self.assertIn("selected category", settings._filter_placeholder["text"])
        self.assertIs(settings._filter_placeholder.master, settings._filter_entry)
        self.select_provider(settings, "eumetsat")
        self.assertTrue(settings.eumetsat_frame.winfo_manager())

    def test_copernicus_dropdowns_location_flags_and_credentials_are_saved(self):
        settings = self.make_settings(
            "copernicus", copernicus_auth={"client_id": "client", "client_secret": "secret"}
        )
        cop = settings.copernicus_settings
        self.assertTrue(cop.frame.winfo_manager())
        self.assertEqual(cop.frame.grid_slaves(row=9, column=0)[0].cget("text"), "Gap fill")
        self.assertFalse(settings.eumetsat_frame.winfo_manager())
        self.assertFalse(settings._area_combo.winfo_manager())
        self.assertEqual(len(cop._configuration_combo["values"]), 13)
        self.assertEqual(cop._mission_var.get(), "Sentinel-2 Mosaics")
        self.assertEqual(cop.get_profile()["product"], "MARBLESCAPE::S2-QUARTERLY")
        self.assertEqual(cop.get_profile()["layer"], "TRUE_COLOR_CLOUDLESS")
        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        self.assertEqual(cop.get_profile()["product"], "DEFAULT-THEME::a91f72")
        cop._coverage_var.set("Fill gaps with earlier imagery (use latest imagery of valid lookback)")
        cop._select_coverage()
        self.assertEqual(cop._zoom_combo["values"], tuple(str(value) for value in range(7, 19)))
        self.assertEqual(cop.get_profile()["date"], "latest")
        self.assertEqual(cop.get_profile()["coverage_mode"], "fill_gaps")
        self.assertEqual(cop.get_profile()["lookback_days"], 14)
        self.assertEqual(cop.get_profile()["max_cloud_cover"], 100)
        self.assertEqual(str(cop._cloud_scale["state"]), "normal")
        cop._cloud_scale.set(15)
        self.assertEqual(cop.get_profile()["max_cloud_cover"], 15)
        lookback_values = cop._lookback_combo["values"]
        self.assertEqual(lookback_values[:4], ("3 days", "7 days", "14 days", "21 days"))
        self.assertEqual(lookback_values[4], "30 days (1 month)")
        self.assertEqual(lookback_values[7], "90 days (3 months)")
        self.assertEqual(lookback_values[8], "120 days (4 months)")
        self.assertEqual(lookback_values[-1], "1095 days (3 years)")
        self.assertTrue(all("|" not in value for value in lookback_values))
        cop._lookback_var.set(lookback_values[-1])
        self.assertEqual(cop.get_profile()["lookback_days"], 1095)
        cop._lookback_var.set(lookback_values[2])
        self.assertEqual(str(cop._lookback_combo["state"]), "readonly")
        cop._coverage_var.set("Single latest acquisition")
        cop._select_coverage()
        self.assertEqual(str(cop._cloud_scale["state"]), "normal")
        self.assertEqual(str(cop._lookback_combo["state"]), "disabled")
        l1c_label = next(label for label, product in cop._product_by_label.items()
                         if product["name"] == "Sentinel-2 L1C")
        cop._zoom_var.set("7")
        cop._product_var.set(l1c_label)
        cop._select_product()
        self.assertEqual(cop._zoom_combo["values"], tuple(str(value) for value in range(10, 19)))
        self.assertEqual(cop._zoom_var.get(), "10")
        with mock.patch(
            "marblescape_copernicus_settings.webbrowser.open_new_tab", return_value=True
        ) as open_portal:
            cop._oauth_button.invoke()
        open_portal.assert_called_once_with(
            "https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings"
        )
        self.assertIn("Opened the free Copernicus OAuth", cop._status_var.get())

        cop._mission_var.set("Sentinel-1")
        cop._select_mission()
        self.assertEqual(str(cop._cloud_scale["state"]), "disabled")
        self.assertEqual(str(cop._brightness_scale["state"]), "disabled")
        # Disabled sliders look disabled: flat grey thumb, greyed value.
        self.assertEqual(str(cop._cloud_scale["sliderrelief"]), "flat")
        self.assertIn("disabled", cop._cloud_scale.master.grid_slaves(row=0, column=2)[0].state())
        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        self.assertEqual(str(cop._cloud_scale["state"]), "normal")
        self.assertEqual(str(cop._cloud_scale["sliderrelief"]), "raised")
        # The cloud hint wraps at the frame width, so a short hint stays on one line.
        cop.frame.event_generate("<Configure>", width=800, height=600)
        self.assertEqual(int(cop._cloud_hint.cget("wraplength")), 790)
        self.assertEqual(cop.get_profile()["product"], "DEFAULT-THEME::a91f72")
        cop._mission_var.set("Sentinel-1")
        cop._select_mission()
        cop._latitude_var.set("52.52")
        cop._longitude_var.set("13.405")
        cop._labels_var.set(False)
        self.assertNotIn("Fill areas without image data with black", cop._coverage_combo["values"])
        cop._coverage_var.set("Single latest acquisition")
        cop._select_coverage()
        self.assertEqual(str(cop._lookback_combo["state"]), "disabled")
        # A regular layer has its own No-data color: Blur by default, or the map background.
        self.assertEqual(cop._no_data_display_var.get(), "Blur")
        # Swatch, Choose color..., the value, then Transparent, Blur and Edge
        # Blur equally wide; the value has the fixed width of the overlay
        # rows' values above.
        from marblescape_source_layout import COLOR_VALUE_WIDTH
        row = cop._no_data_color_button.master.pack_slaves()
        self.assertEqual([child.winfo_class() for child in row], ["Label", "TButton", "TLabel", "TFrame"])
        choices = (cop._transparent_button, cop._blur_button, cop._edge_blur_button)
        self.assertEqual([button.cget("text") for button in choices], ["Transparent", "Blur", "Edge Blur"])
        self.assertTrue(all(button.master is row[3] for button in choices))
        self.assertEqual({row[3].columnconfigure(column)["uniform"] for column in (0, 1, 2)}, {"no_data_choices"})
        self.assertEqual(str(row[2].cget("textvariable")), str(cop._no_data_display_var))
        overlay_row = cop._overlays["labels"]["button"].master.pack_slaves()
        self.assertEqual([child.winfo_class() for child in overlay_row], ["Label", "TButton", "TLabel"])
        for value in (row[2], overlay_row[2]):
            self.assertEqual(int(value.cget("width")), COLOR_VALUE_WIDTH)
        cop._transparent_button.invoke()
        self.assertEqual(cop._no_data_display_var.get(), "Transparent")
        cop._blur_button.invoke()
        self.assertEqual(cop._no_data_display_var.get(), "Blur")
        cop._edge_blur_button.invoke()
        self.assertEqual(cop._no_data_display_var.get(), "Edge Blur")
        provider, profiles = settings.get_selection()
        self.assertEqual(profiles["copernicus"]["scene_no_data_color"], "blur_edge")
        # The mosaic choice was not touched: Blur, the default.
        self.assertEqual(profiles["copernicus"]["no_data_color"], "blur")
        self.assertEqual(provider, "copernicus")
        self.assertEqual(profiles["copernicus"]["mission"], "Sentinel-1")
        self.assertEqual(profiles["copernicus"]["latitude"], 52.52)
        self.assertEqual(profiles["copernicus"]["longitude"], 13.405)
        self.assertFalse(profiles["copernicus"]["map_labels"])
        self.assertEqual(profiles["copernicus"]["coverage_mode"], "single")
        self.assertEqual(profiles["copernicus"]["lookback_days"], 14)
        self.assertEqual(profiles["copernicus"]["max_cloud_cover"], 15)
        self.assertEqual(settings.get_copernicus_auth(),
                         {"client_id": "client", "client_secret": "secret"})

    def test_copernicus_catalogue_activity_reports_completion(self):
        settings = self.make_settings(
            "copernicus", copernicus_auth={"client_id": "client", "client_secret": "secret"}
        )
        copernicus = settings.copernicus_settings
        with mock.patch("marblescape_copernicus_settings.CopernicusClient") as client:
            client.return_value.list_dates.return_value = ["2026-09-21"]
            copernicus.refresh_dates()
            self.assertTrue(copernicus._activity.active)
            deadline = time.monotonic() + 3
            while copernicus._activity.active:
                if time.monotonic() > deadline:
                    self.fail("Copernicus catalogue fixture did not complete")
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(copernicus._activity.completion.get(), "Completed.")
        self.assertIn("2026 Q3", copernicus._date_combo["values"])

        missing = self.make_settings("copernicus")
        with self.assertRaises(ValueError):
            missing.get_selection()

    def test_copernicus_period_rows_keep_the_same_gap_as_the_other_rows(self):
        from marblescape_copernicus_settings import CopernicusSettings
        root = tk.Tk()
        root.withdraw()
        try:
            cop = CopernicusSettings(root)
            cop.frame.grid(row=0, column=0, sticky="nsew")

            def top(widget):
                y = 0
                while widget is not cop.frame:
                    y += widget.winfo_y()
                    widget = widget.nametowidget(widget.winfo_parent())
                return y

            def gaps(widgets):
                root.update_idletasks()
                root.update()
                return [top(lower) - top(upper) - upper.winfo_height()
                        for upper, lower in zip(widgets, widgets[1:])]

            quarterly = {"date_granularity": "quarter"}
            with mock.patch.object(cop, "_selected_layer", return_value=quarterly):
                for unit in ("Specific quarter", "Relative to now"):
                    cop._quarter_mode_var.set(unit)
                    cop._refresh_quarter_controls()
                    second = cop._date_combo if unit.startswith("Specific") else cop._quarter_offset_combo
                    rows = [row for row in (cop._layer_combo, cop._highlight_combo, cop._quarter_mode_combo, second)
                            if row.winfo_manager()]
                    if second is cop._date_combo:
                        rows.append(cop._latitude_entry)
                    with self.subTest(unit=unit):
                        self.assertEqual(set(gaps(rows)), {6})
                    if unit == "Relative to now":
                        # One line: the resolved period and its note, split by a pipe.
                        self.assertRegex(cop._quarter_resolution_var.get(),
                                         r"^Resolved quarter: \d{4} Q[1-4] \| The selected period must be "
                                         r"published for this location\.$")
                        self.assertNotIn("\n", cop._quarter_resolution_var.get())
                # Back from a specific quarter to a year or date choice.
                cop._quarter_mode_var.set("Specific quarter")
                cop._refresh_quarter_controls()
            for granularity in ("year", None):
                with mock.patch.object(cop, "_selected_layer", return_value={"date_granularity": granularity}):
                    cop._refresh_quarter_controls()
                    rows = [row for row in (cop._layer_combo, cop._highlight_combo, cop._date_combo,
                                            cop._latitude_entry) if row.winfo_manager()]
                    with self.subTest(granularity=granularity):
                        self.assertEqual(set(gaps(rows)), {6})
        finally:
            root.destroy()

    def test_copernicus_color_swatches_show_their_color_in_both_themes(self):
        import marblescape_theme as theme
        from marblescape_copernicus_settings import CopernicusSettings
        if theme.sv_ttk is None:
            self.skipTest("sv-ttk is not installed")
        root = tk.Tk()
        root.withdraw()
        try:
            cop = CopernicusSettings(root)
            cop._map_label_color_var.set("#FFFFFF")
            cop._no_data_color_var.set("#123456")
            for mode in ("dark", "light", "dark"):
                # Loading the theme resets plain tk colors once the event loop runs.
                theme.apply_appearance(root, mode)
                root.update()
                self.assertEqual(str(cop._overlays["labels"]["preview"].cget("background")).upper(), "#FFFFFF", mode)
                if cop._active_no_data_var() is cop._no_data_color_var:
                    self.assertEqual(str(cop._no_data_preview.cget("background")).upper(), "#123456", mode)
        finally:
            root.destroy()

    def test_tone_controls_follow_the_layer_tone_rule(self):
        from marblescape_copernicus import tone_rule

        def copernicus(**values):
            profiles = deepcopy(source_settings.DEFAULT_PROFILES)
            profiles["copernicus"].update(auto_brightness=True, auto_contrast=True, brightness=120, contrast=130,
                                          **values)
            settings = self.make_settings("copernicus", profiles)
            return settings.copernicus_settings

        # No rule (False Color Cloudless): stock values, no auto; saved values stay saved.
        cop = copernicus(configuration="DEFAULT-THEME", mission="Sentinel-2 Mosaics",
                         product="MARBLESCAPE::S2-QUARTERLY", layer="FALSE_COLOR_CLOUDLESS")
        self.assertIsNone(tone_rule(cop._selected_layer()))
        for button in (cop._auto_brightness_button, cop._auto_contrast_button):
            self.assertEqual(str(button.cget("state")), "disabled")
        for scale, value in ((cop._brightness_scale, cop._brightness_value_var),
                             (cop._contrast_scale, cop._contrast_value_var)):
            self.assertEqual(str(scale["state"]), "disabled")
            self.assertEqual(int(scale.get()), 100)
            self.assertEqual(value.get(), "100%")
        profile = cop.get_profile()
        self.assertEqual((profile["brightness"], profile["contrast"], profile["auto_brightness"]), (120, 130, True))
        # A regular layer with a rule (Sentinel-2 L2A True color): auto available.
        cop = copernicus(configuration="DEFAULT-THEME", mission="Sentinel-2", product="DEFAULT-THEME::a91f72",
                         layer="1_TRUE_COLOR")
        self.assertEqual(tone_rule(cop._selected_layer()), "e")
        for button in (cop._auto_brightness_button, cop._auto_contrast_button):
            self.assertEqual(str(button.cget("state")), "normal")
        cop._auto_brightness_var.set(False)
        cop._tone_mode_changed()
        self.assertEqual(str(cop._brightness_scale["state"]), "normal")
        self.assertEqual(cop._brightness_value_var.get(), "120%")

        # Switching layers of the same product updates the controls at once.
        def select(layer_id):
            cop._layer_var.set(next(label for label, item in cop._layer_by_label.items() if item["id"] == layer_id))
            cop._select_layer()

        cop._auto_brightness_var.set(True)
        cop._tone_mode_changed()
        select("2_FALSE_COLOR")
        for button in (cop._auto_brightness_button, cop._auto_contrast_button):
            self.assertEqual(str(button.cget("state")), "disabled")
        for scale, value in ((cop._brightness_scale, cop._brightness_value_var),
                             (cop._contrast_scale, cop._contrast_value_var)):
            self.assertEqual(str(scale["state"]), "disabled")
            self.assertEqual(value.get(), "100%")
        select("1_TRUE_COLOR")
        for button in (cop._auto_brightness_button, cop._auto_contrast_button):
            self.assertEqual(str(button.cget("state")), "normal")
        # Both auto options still on: the sliders follow auto, unchecking frees them.
        self.assertEqual(str(cop._brightness_scale["state"]), "disabled")
        cop._auto_brightness_var.set(False)
        cop._tone_mode_changed()
        self.assertEqual(str(cop._brightness_scale["state"]), "normal")
        self.assertEqual(cop._brightness_value_var.get(), "120%")

    def test_copernicus_mosaic_controls_and_account_credits(self):
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        cop._mission_var.set("Sentinel-2 Mosaics")
        cop._select_mission()
        self.assertEqual(cop._selected_product()["name"], "Sentinel-2 Quarterly Mosaics")
        self.assertEqual(cop._quarter_mode_label["text"], "Quarter selection")
        self.assertEqual(int(cop._quarter_mode_frame.grid_info()["columnspan"]), 2)
        self.assertEqual(cop._quarter_mode_combo.cget("width"), cop._mission_combo.cget("width"))
        self.assertEqual(cop._quarter_offset_combo.cget("width"), cop._mission_combo.cget("width"))
        self.assertEqual(str(cop._cloud_scale["state"]), "disabled")
        # A new selection of a layer with a tone rule starts with both auto options;
        # unchecking them allows manual values.
        self.assertTrue(cop.get_profile()["auto_brightness"])
        self.assertTrue(cop.get_profile()["auto_contrast"])
        self.assertEqual(str(cop._brightness_scale["state"]), "disabled")
        cop._auto_brightness_var.set(False)
        cop._auto_contrast_var.set(False)
        cop._tone_mode_changed()
        self.assertEqual(str(cop._brightness_scale["state"]), "normal")
        # Each row: its label, then "auto" left of the slider and the value right of it.
        # Image resolution, then both corrections, in the Rendering section.
        self.assertIs(cop._image_size_combo.master, cop.rendering_frame)
        self.assertIs(cop.rendering_frame.master, settings.rendering_frame)
        labels = {child.cget("text"): child for child in cop.rendering_frame.grid_slaves(column=0)
                  if child.winfo_class() == "TLabel"}
        # The label names the pixel size the choice gives, e.g. "Image resolution (1920 × 1080)".
        width, height = cop._effective_output_size()
        self.assertEqual(int(labels[f"Image resolution ({width} × {height})"].grid_info()["row"]), 0)
        cop._image_size_var.set("2560x1440")
        self.assertEqual(cop._image_size_combo.label_widget.cget("text"), "Image resolution (2560 × 1440)")
        cop._image_size_var.set("Auto (recommended)")
        # The map overlays and the No-data color close the Rendering rows.
        rows = {str(child.cget("text")): int(child.grid_info()["row"])
                for child in cop.rendering_frame.grid_slaves(column=0)
                if child.winfo_class() in ("TLabel", "TCheckbutton")}
        self.assertEqual(rows["Labels (places, roads, POIs)"], 3)
        self.assertEqual(rows["Country borders"], 4)
        self.assertEqual(rows["No-data color"], 5)
        self.assertIs(cop._overlays["labels"]["button"].master.master, cop.rendering_frame)
        self.assertIs(cop._no_data_color_button.master.master, cop.rendering_frame)
        self.assertNotIn("Image size (saved PNG)", labels)
        for text, button, scale in (("Brightness correction", cop._auto_brightness_button, cop._brightness_scale),
                                    ("Contrast correction", cop._auto_contrast_button, cop._contrast_scale)):
            self.assertEqual(int(labels[text].grid_info()["row"]), int(scale.master.grid_info()["row"]))
            self.assertEqual(button.cget("text"), "auto")
            self.assertIs(button.master, scale.master)
            self.assertEqual(int(button.grid_info()["column"]), 0)
            self.assertEqual(int(scale.grid_info()["column"]), 1)
            self.assertEqual(scale.master.grid_slaves(row=0, column=2)[0].winfo_class(), "TLabel")
        self.assertNotIn("Mosaic brightness", labels)
        self.assertNotIn("Mosaic contrast", labels)
        # The three sliders start at one line, the cloud cover row too.
        cop.frame.update_idletasks()
        reserved = {int(frame.columnconfigure(0)["minsize"])
                    for frame in (cop._cloud_scale.master, cop._brightness_scale.master, cop._contrast_scale.master)}
        self.assertEqual(len(reserved), 1)
        self.assertGreater(reserved.pop(), 0)
        cop._brightness_var.set(75)
        self.assertEqual(cop.get_profile()["brightness"], 75)
        cop._contrast_var.set(125)
        cop._auto_brightness_var.set(True)
        cop._auto_contrast_var.set(True)
        cop._tone_mode_changed()
        self.assertEqual(str(cop._brightness_scale["state"]), "disabled")
        self.assertEqual(str(cop._contrast_scale["state"]), "disabled")
        self.assertEqual(cop.get_profile()["contrast"], 125)
        self.assertTrue(cop.get_profile()["auto_brightness"])
        self.assertTrue(cop.get_profile()["auto_contrast"])
        # No picture of this selection yet: the values read "auto".
        self.assertEqual(cop._brightness_value_var.get(), "auto")
        self.assertEqual(cop._contrast_value_var.get(), "auto")
        # The picture on screen: brightness as the equal manual percentage,
        # contrast as the stretch factor; the manual values stay saved.
        shown = cop.get_profile()
        cop.show_picture(shown, {"auto_brightness": True, "auto_contrast": True, "midtone_gamma": 0.8,
                                 "stretch_low": 1, "stretch_high": 171})
        self.assertEqual(cop._brightness_value_var.get(), "125%")
        self.assertEqual(int(cop._brightness_scale.get()), 125)
        self.assertEqual(cop._contrast_value_var.get(), "×1.50")
        self.assertEqual(int(cop._contrast_scale.get()), 100)
        self.assertEqual((cop.get_profile()["brightness"], cop.get_profile()["contrast"]), (75, 125))
        # Another selection than the picture's: back to "auto".
        cop._cloud_var.set(55)
        cop._changed()
        self.assertEqual(cop._brightness_value_var.get(), "auto")
        cop._cloud_var.set(shown["max_cloud_cover"])
        cop._changed()
        self.assertEqual(cop._brightness_value_var.get(), "125%")
        # auto off: the slider returns to the own value.
        cop._auto_brightness_var.set(False)
        cop._tone_mode_changed()
        self.assertEqual(cop._brightness_value_var.get(), "75%")
        self.assertEqual(int(cop._brightness_scale.get()), 75)
        self.assertEqual(str(cop._brightness_scale["state"]), "normal")
        cop._auto_brightness_var.set(True)
        cop._tone_mode_changed()
        self.assertEqual(cop.get_profile()["image_size"], "auto")
        # Portrait sizes for a vertical view of the place.
        self.assertEqual(tuple(cop._image_size_combo.cget("values"))[-4:],
                         ("1080 × 1920 (Full HD, portrait)", "1440 × 2560 (QHD, portrait)",
                          "2160 × 3840 (4K UHD, portrait)", "4320 × 7680 (8K UHD, portrait)"))
        self.assertEqual(tuple(cop._image_size_combo.cget("values"))[:2],
                         ("Auto (recommended)", "1920 × 1080 (Full HD)"))
        cop._image_size_var.set("2160x3840")
        cop._image_size_changed()
        self.assertEqual(cop._effective_output_size(), (2160, 3840))
        self.assertEqual(cop.get_profile()["image_size"], "2160x3840")
        self.assertEqual(cop._image_size_combo.label_widget.cget("text"), "Image resolution (2160 × 3840)")
        cop._image_size_var.set("7680x4320")
        cop._image_size_changed()
        self.assertEqual(cop._effective_output_size(), (7680, 4320))
        self.assertEqual(cop.get_profile()["image_size"], "7680x4320")
        self.assertEqual(str(cop._no_data_color_button["state"]), "normal")
        with mock.patch("marblescape_copernicus_settings.colorchooser.askcolor",
                        return_value=((255, 0, 136), "#ff0088")):
            cop._choose_no_data_color()
        self.assertEqual(cop.get_profile()["no_data_color"], "#FF0088")
        self.assertEqual(str(cop._transparent_button["state"]), "normal")
        cop._choose_transparent()
        self.assertEqual(cop.get_profile()["no_data_color"], "transparent")
        with mock.patch("marblescape_copernicus_settings.colorchooser.askcolor", return_value=(None, None)) as picker:
            cop._choose_no_data_color()
        self.assertEqual(picker.call_args.kwargs["color"], "#FFFFFF")
        self.assertEqual(cop.get_profile()["no_data_color"], "transparent")
        # Labels and country borders: separate switches and colors, all off and white at first.
        profile = cop.get_profile()
        self.assertEqual((profile["map_labels"], profile["map_label_color"],
                          profile["map_borders"], profile["map_border_color"]),
                         (False, "#FFFFFF", False, "#FFFFFF"))
        self.assertEqual(str(cop._map_label_color_button["state"]), "disabled")
        self.assertEqual(str(cop._map_border_color_button["state"]), "disabled")
        cop._labels_var.set(True)
        cop._map_labels_changed()
        self.assertEqual(str(cop._map_label_color_button["state"]), "normal")
        self.assertEqual(str(cop._map_border_color_button["state"]), "disabled")
        with mock.patch("marblescape_copernicus_settings.colorchooser.askcolor",
                        return_value=((18, 52, 86), "#123456")):
            cop._choose_overlay_color("labels")
            cop._choose_overlay_color("borders")
        self.assertEqual(cop.get_profile()["map_label_color"], "#123456")
        self.assertEqual(cop.get_profile()["map_border_color"], "#FFFFFF", "A switched-off overlay keeps its color")
        cop._borders_var.set(True)
        cop._map_labels_changed()
        with mock.patch("marblescape_copernicus_settings.colorchooser.askcolor",
                        return_value=((0, 255, 0), "#00ff00")):
            cop._choose_overlay_color("borders")
        profile = cop.get_profile()
        self.assertEqual((profile["map_labels"], profile["map_label_color"],
                          profile["map_borders"], profile["map_border_color"]),
                         (True, "#123456", True, "#00FF00"))
        cop._labels_var.set(False)
        cop._map_labels_changed()
        self.assertEqual(str(cop._map_label_color_button["state"]), "disabled")
        self.assertEqual(str(cop._map_border_color_button["state"]), "normal")
        self.assertEqual(str(cop._coverage_combo["state"]), "disabled")
        cop._update_date_choices(["2026-04-01"], "latest")
        self.assertIn("2026 Q2", cop._date_combo["values"])

        annual = next(label for label, product in cop._product_by_label.items()
                      if product["name"] == "WorldCover Annual Cloudless Mosaics")
        cop._product_var.set(annual)
        cop._select_product()
        self.assertEqual(cop._date_label["text"], "Year")
        self.assertEqual(cop._zoom_combo["values"][0], "9")
        cop._update_date_choices(["2021-01-01"], "latest")
        self.assertIn("2021", cop._date_combo["values"])

        usage = {"role": "copernicus-general-quota"}
        for category in ("processingUnitsMonthly", "requestsMonthly"):
            usage[category] = {"configuration": "30000", "consumed": "123",
                               "remaining": "29877"}
        cop._usage_results.put((cop._usage_generation, usage, ""))
        cop.frame.after_cancel(cop._after_id)
        cop._after_id = None
        cop._poll()
        self.assertEqual(cop._credits_role_var.get(), "Role: copernicus-general-quota")
        self.assertEqual(cop._credits_values[("requestsMonthly", "remaining")].get(), "29877")
        cop._client_id_var.set("different client")
        self.assertEqual(cop._credits_role_var.get(), "Role: -")
        self.assertEqual(cop._credits_values[("requestsMonthly", "remaining")].get(), "-")

    def test_copernicus_quarters_return_after_switching_missions(self):
        client = FakeNOAAClient()
        from marblescape_catalogues import _CatalogueDiskCache
        cache = _CatalogueDiskCache()
        client.cached_copernicus_dates = cache.copernicus_dates
        client.store_copernicus_dates = cache.store_copernicus_dates
        settings = self.make_settings("copernicus", client=client)
        cop = settings.copernicus_settings
        cop._mission_var.set("Sentinel-2 Mosaics")
        cop._select_mission()
        size = cop._output_size() if callable(cop._output_size) else cop._output_size
        client.store_copernicus_dates(cop.get_profile(), size,
                                      ["2026-01-01", "2025-10-01"])
        cop._update_date_choices(["2026-01-01", "2025-10-01"], "latest")
        self.assertIn("2026 Q1", cop._date_combo["values"])

        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        self.assertNotIn("2026 Q1", cop._date_combo["values"])
        cop._mission_var.set("Sentinel-2 Mosaics")
        cop._select_mission()
        self.assertIn("2026 Q1", cop._date_combo["values"])
        self.assertIn("2025 Q4", cop._date_combo["values"])

        self.select_provider(settings, "goes_east")
        self.select_provider(settings, "copernicus")
        self.assertIn("2026 Q1", cop._date_combo["values"])

    def test_copernicus_rolling_quarter_disables_fixed_list_and_roundtrips(self):
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        self.assertEqual(str(cop._date_combo.cget("state")), "readonly")
        cop._quarter_mode_var.set("Relative to now")
        cop._select_quarter_mode()
        cop._quarter_offset_var.set("3 quarters ago")
        cop._select_quarter_offset()
        self.assertFalse(cop._date_combo.winfo_manager())
        cop._update_date_choices(["2026-01-01"], "latest")
        self.assertFalse(cop._date_combo.winfo_manager())
        profile = cop.get_profile()
        self.assertEqual((profile["date_mode"], profile["quarter_offset"], profile["date"]),
                         ("relative_quarter", 3, "latest"))
        cop.set_profile(profile)
        self.assertEqual(cop.get_profile(), profile)
        cop._update_date_choices(["2026-01-01"], "latest")
        cop._quarter_mode_var.set("Specific quarter")
        cop._select_quarter_mode()
        self.assertEqual(str(cop._quarter_offset_combo.cget("state")), "disabled")
        self.assertEqual(cop.get_profile()["quarter_offset"], 0)

    def test_copernicus_uncached_selection_refreshes_when_authenticated(self):
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        cop._client_id_var.set("test-client")
        cop._secret_var.set("test-secret")
        with mock.patch.object(cop, "refresh_dates") as refresh:
            cop._mission_var.set("Sentinel-2")
            cop._select_mission()
        refresh.assert_called_once_with()

    def test_period_selection_has_three_exclusive_modes_and_monthly_equivalent(self):
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        self.assertEqual(cop._quarter_mode_combo["values"],
                         ("Specific quarter", "Relative to now", "Latest available"))
        self.assertFalse(cop._date_combo.winfo_manager())
        self.assertFalse(cop._quarter_offset_combo.winfo_manager())
        cop._update_date_choices(["2026-07-01", "2026-04-01"], "latest")
        cop._quarter_mode_var.set("Specific quarter")
        cop._select_quarter_mode()
        self.assertEqual(cop.get_profile()["date"], "2026-07-01")
        self.assertNotIn("Latest available", cop._date_combo["values"])
        cop._quarter_mode_var.set("Relative to now")
        cop._select_quarter_mode()
        cop._quarter_offset_var.set("Current quarter")
        cop._select_quarter_offset()
        self.assertEqual(cop.get_profile()["quarter_offset"], 0)
        self.assertFalse(cop._date_combo.winfo_manager())
        self.assertEqual(str(cop._quarter_offset_combo["state"]), "readonly")
        cop._mission_var.set("Sentinel-1 Mosaics")
        cop._select_mission()
        cop._quarter_mode_var.set("Relative to now")
        cop._quarter_offset_var.set("35 months ago")
        cop._select_quarter_offset()
        profile = cop.get_profile()
        self.assertEqual((profile["date_mode"], profile["month_offset"], profile["quarter_offset"]),
                         ("relative_month", 35, 0))
        cop.set_profile(profile)
        self.assertEqual(cop.get_profile(), profile)
        self.assertEqual(cop._quarter_mode_label["text"], "Month selection")
        self.assertEqual(cop._quarter_offset_label["text"], "Months back")

    def test_copernicus_catalogue_retries_then_uses_cached_dates(self):
        client = FakeNOAAClient()
        client.copernicus_dates = ["2026-09-20"]
        with mock.patch("marblescape_copernicus_settings.CopernicusClient") as cop_client:
            cop_client.return_value.list_dates.side_effect = OSError("catalogue offline")
            settings = self.make_settings(
                "copernicus", client=client,
                copernicus_auth={"client_id": "client", "client_secret": "secret"},
            )
            deadline = time.monotonic() + 3
            while settings.copernicus_settings._activity.active:
                if time.monotonic() > deadline:
                    self.fail("Copernicus cached catalogue fixture did not complete")
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(cop_client.return_value.list_dates.call_count, 3)
        self.assertIn("2026 Q3", settings.copernicus_settings._date_combo["values"])
        self.assertIn("using cached catalogue data", settings.copernicus_settings._status_var.get())

    def _wait_for_copernicus_dates(self, cop):
        deadline = time.monotonic() + 3
        while cop._activity.active:
            if time.monotonic() > deadline:
                self.fail("Copernicus catalogue fixture did not complete")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def test_copernicus_date_survives_layer_and_product_changes_while_available(self):
        client = FakeNOAAClient()
        client.copernicus_dates = ["2026-09-16", "2026-09-08"]
        settings = self.make_settings("copernicus", client=client)
        cop = settings.copernicus_settings

        def layer_label(name):
            return next(label for label, layer in cop._layer_by_label.items()
                        if layer["name"] == name)

        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        cop._date_var.set("2026-09-16")
        cop._custom_date_changed()
        cop._layer_var.set(layer_label("False color"))
        cop._select_layer()
        self.assertEqual(cop.get_profile()["date"], "2026-09-16")
        product = next(label for label, item in cop._product_by_label.items()
                       if item["name"] == "Sentinel-2 L1C")
        cop._product_var.set(product)
        cop._select_product()
        self.assertEqual(cop.get_profile()["date"], "2026-09-16")

        # A date missing from the new selection's list falls back with a notice.
        client.copernicus_dates = ["2026-09-08"]
        cop._layer_var.set(next(iter(cop._layer_by_label)))
        cop._select_layer()
        self.assertEqual(cop.get_profile()["date"], "latest")
        self.assertIn("2026-09-16 is not available for this selection", cop._status_var.get())

        # A daily date is not carried over to a quarterly mosaic.
        client.copernicus_dates = ["2026-09-16", "2026-07-01"]
        cop._date_var.set("2026-09-16")
        cop._custom_date_changed()
        cop._mission_var.set("Sentinel-2 Mosaics")
        cop._select_mission()
        self.assertEqual(cop.get_profile()["date"], "latest")
        self.assertIsNone(cop._kept_date)

    def test_copernicus_kept_date_is_checked_against_the_refreshed_catalogue(self):
        client = FakeNOAAClient()
        client.copernicus_dates = ["2026-09-16"]
        with mock.patch("marblescape_copernicus_settings.CopernicusClient") as cop_client:
            cop_client.return_value.list_dates.return_value = ["2026-09-16"]
            settings = self.make_settings(
                "copernicus", client=client,
                copernicus_auth={"client_id": "client", "client_secret": "secret"},
            )
            cop = settings.copernicus_settings
            wait_for_dates = self._wait_for_copernicus_dates
            wait_for_dates(cop)
            cop._mission_var.set("Landsat 8/9")
            cop._select_mission()
            wait_for_dates(cop)
            cop._date_var.set("2026-09-16")
            cop._custom_date_changed()
            client.copernicus_dates = None
            cop_client.return_value.list_dates.return_value = ["2026-09-08"]
            cop._layer_var.set(next(label for label, layer in cop._layer_by_label.items()
                                    if layer["name"] == "Thermal"))
            cop._select_layer()
            self.assertEqual(cop.get_profile()["date"], "2026-09-16")
            wait_for_dates(cop)
        self.assertEqual(cop.get_profile()["date"], "latest")
        self.assertIn("2026-09-16 is not available", cop._status_var.get())

    def test_copernicus_date_list_stays_usable_while_dates_load(self):
        client = FakeNOAAClient()
        client.copernicus_dates = ["2026-09-16", "2026-09-08"]
        release = threading.Event()

        def list_dates(*_args, **_kwargs):
            release.wait(5)
            return ["2026-09-16", "2026-09-08"]

        with mock.patch("marblescape_copernicus_settings.CopernicusClient") as cop_client:
            cop_client.return_value.list_dates.side_effect = list_dates
            settings = self.make_settings(
                "copernicus", client=client,
                copernicus_auth={"client_id": "client", "client_secret": "secret"},
            )
            cop = settings.copernicus_settings
            release.set()
            self._wait_for_copernicus_dates(cop)
            release.clear()
            cop._mission_var.set("Landsat 8/9")
            cop._select_mission()
            self._wait_for_copernicus_dates(cop)
            cop._date_var.set("2026-09-08")
            cop._custom_date_changed()
            client.copernicus_dates = None
            cop._layer_var.set(next(label for label, layer in cop._layer_by_label.items()
                                    if layer["name"] == "Thermal"))
            cop._select_layer()
            # The layer's dates stay listed and a placeholder shows the refresh.
            self.assertEqual(cop._date_combo["values"],
                             (source_settings_copernicus.LATEST_LABEL,
                              source_settings_copernicus.LOADING_DATES_LABEL,
                              "2026-09-16", "2026-09-08"))
            cop._date_var.set(source_settings_copernicus.LOADING_DATES_LABEL)
            cop._custom_date_changed()
            self.assertEqual(cop._date_var.get(), "2026-09-08")
            release.set()
            self._wait_for_copernicus_dates(cop)
        self.assertNotIn(source_settings_copernicus.LOADING_DATES_LABEL, cop._date_combo["values"])
        self.assertEqual(cop.get_profile()["date"], "2026-09-08")

    def test_active_storm_category_selects_and_commits_first_storm(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        settings._category_var.set("Active storms")
        settings._select_category()
        self.assertEqual(settings._area_var.get(), "Storm One [storm_one]")
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"],
                         {"area": "storm_one", "product": "GEOCOLOR", "resolution": "auto"})
        self.select_provider(settings, "solar")
        self.wait_for_catalogue(settings)
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "storm_one")

    def test_empty_selected_category_clears_the_previous_area(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        settings._areas = [area for area in settings._areas if area["category"] != "Active storms"]
        settings._category_var.set("Active storms")
        settings._select_category()
        self.assertEqual(settings._area_var.get(), "")
        self.assertEqual(settings._product_var.get(), "")
        self.assertEqual(settings._profiles["goes_east"]["area"], "")
        with self.assertRaises(ValueError):
            settings.get_selection()
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_a")

    def test_set_selection_loads_a_copy_and_rejects_obsolete_catalogue_results(self):
        changes = []
        settings = self.make_settings(on_change=changes.append)
        saved = deepcopy(source_settings.DEFAULT_PROFILES)
        saved["solar"]["resolution"] = "300x300"
        self.select_provider(settings, "goes_east")
        settings.set_selection("solar", saved)
        saved["solar"]["resolution"] = "largest"
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[0], "solar")
        self.assertEqual(settings.get_selection()[1]["solar"]["resolution"], "300x300")
        self.assertEqual(settings._resolution_var.get(), "300 × 300")
        self.assertEqual(changes, ["goes_east", "solar"])
        self.assertEqual([area["id"] for area in settings._areas], ["sun"])

    def test_set_selection_from_worker_calls_back_only_on_tk_thread(self):
        callback_threads = []
        settings = self.make_settings(on_change=lambda provider: callback_threads.append(threading.get_ident()))
        saved = deepcopy(source_settings.DEFAULT_PROFILES)
        worker = threading.Thread(target=settings.set_selection, args=("solar", saved))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(settings.provider, "eumetsat")
        deadline = time.monotonic() + 5
        while settings.provider != "solar":
            if time.monotonic() > deadline:
                self.fail("Queued source profile was not applied")
            self.root.update()
            time.sleep(0.01)
        self.wait_for_catalogue(settings)
        self.assertEqual(callback_threads, [threading.get_ident()])

    @staticmethod
    def _descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from SourceSettingsTests._descendants(child)

    @staticmethod
    def _shown(widget, section):
        """Gridded up to ``section``; withdrawn test windows are never mapped."""
        while widget is not section:
            if not widget.grid_info():
                return False
            widget = widget.master
        return True

    def test_catalogue_section_shows_only_the_selected_source_with_one_refresh_button(self):
        from tkinter import ttk
        settings = self.make_settings("eumetsat")
        section = settings.catalogue_frame
        self.assertIsInstance(section, ttk.LabelFrame)
        self.assertIs(section.master, settings.frame.master)
        for provider in ("eumetsat", "goes_east", "himawari", "worldview", "copernicus"):
            with self.subTest(provider=provider):
                self.select_provider(settings, provider)
                self.root.update_idletasks()
                self.assertEqual(section.cget("text"),
                                 "Catalogue refresh - " + source_settings.image_source_label(provider))
                buttons = [widget for widget in self._descendants(section)
                           if isinstance(widget, ttk.Button) and self._shown(widget, section)]
                self.assertEqual([button.cget("text") for button in buttons], ["Refresh catalogue"])
        everything = [*self._descendants(settings.frame), *self._descendants(section)]
        self.assertFalse(any(isinstance(widget, ttk.Button)
                             and widget.cget("text") == "Refresh all catalogues" for widget in everything))
        self.assertFalse(any(isinstance(widget, ttk.Button) and self._shown(widget, settings.frame)
                             and "catalogue" in str(widget.cget("text")).casefold()
                             for widget in self._descendants(settings.frame)))

    def test_finished_refresh_of_all_catalogues_reloads_the_shown_catalogue(self):
        client = FakeNOAAClient()
        settings = self.make_settings("goes_east", client=client)
        self.wait_for_catalogue(settings)
        client.calls.clear()
        client.catalogue_refresh_status = {"running": True, "done": 1, "total": 5,
                                           "message": "Loading NOAA catalogues...", "error": ""}
        settings._sync_global_refresh()
        self.assertEqual(client.calls, [])
        client.catalogue_refresh_status = {"running": False, "done": 5, "total": 5,
                                           "message": "Catalogue update completed.", "error": ""}
        settings._sync_global_refresh()
        self.wait_for_catalogue(settings)
        self.assertIn(("areas", "goes_east", False), client.calls)
        client.calls.clear()
        settings._sync_global_refresh()
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
