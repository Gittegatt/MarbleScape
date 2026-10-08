"""Himawari's shorelines, a full disk centred on coordinates, and storms to follow."""

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
import marblescape_himawari as himawari
from marblescape_catalogues import CatalogueClient, NOAA_STORM_CHECK_SECONDS
from marblescape_profile_settings import _profile_latitude, _profile_shorelines
from marblescape_profile_transfer import _check_png_profile_metadata

from test_himawari import FixtureClient, png

LATEST = himawari.NICT_IMAGE_BASE + "img/D531106/latest.json"
CYCLONES = himawari.JMA_TYPHOON_BASE + "targetTc.json"


def cyclone_list(*entries):
    return json.dumps([{"tropicalCyclone": cyclone, "category": category}
                       for cyclone, category in entries]).encode()


def cyclone_details(name, latitude, longitude):
    return json.dumps([
        {"part": "title", "name": {"jp": "", "en": name}, "category": {"en": "STS"}},
        {"part": {"en": "Analysis"}, "advancedHours": 0, "position": {"deg": [latitude, longitude]}},
        {"part": {"en": "Forecast"}, "advancedHours": 12, "position": {"deg": [latitude + 1, longitude]}},
    ]).encode()


def an_hour_passes(client):
    """Age the remembered strongest cyclone past its hour."""
    chosen_at, choice, number = client._strongest
    if chosen_at is not None:
        client._strongest = (chosen_at - himawari.STRONGEST_STORM_TTL, choice, number)


def storm_client(*cyclones):
    """A fixture client listing the given (id, category, name, latitude, longitude) storms."""
    client = FixtureClient()
    client.responses[CYCLONES] = cyclone_list(*((cyclone, category) for cyclone, category, *_ in cyclones))
    for cyclone, _category, name, latitude, longitude in cyclones:
        client.responses[f"{himawari.JMA_TYPHOON_BASE}{cyclone}/specifications.json"] = \
            cyclone_details(name, latitude, longitude)
    client.responses[LATEST] = b'{"date":"2026-10-08 03:00:00","file":"still.png"}'
    return client


class ProfileTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        self.assertEqual(himawari.normalize_profile({"area": "jma_jpn"})["shorelines"], False)
        normalized = himawari.normalize_profile({"shoreline_color": "#ff00aa", "latitude": 35, "center": True})
        self.assertEqual((normalized["shoreline_color"], normalized["latitude"]), ("#FF00AA", 35.0))
        for bad in ({"shorelines": "yes"}, {"center": 1}, {"shoreline_color": "yellow"},
                    {"latitude": 91}, {"longitude": -181}, {"latitude": "35"}, {"latitude": True},
                    {"resolution": "big"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                himawari.normalize_profile(bad)

    def test_disk_projection_matches_nicts_full_disk(self):
        # The sub-satellite point is the middle at every size.
        for size in (550, 2200, 11000):
            x, y = himawari.disk_pixel(0, himawari.SUB_SATELLITE_LONGITUDE, size)
            self.assertAlmostEqual(x, size / 2, delta=0.01)
            self.assertAlmostEqual(y, size / 2, delta=0.01)
        # Cape Inubo, checked against NICT's own coastline tiles at 11000 pixels.
        x, y = himawari.disk_pixel(35.707, 140.869, 11000)
        self.assertEqual((int(x), int(y)), (5514, 1929))
        x2, y2 = himawari.disk_pixel(35.707, 140.869, 5500)
        self.assertAlmostEqual(x2, x / 2, delta=0.5)
        for latitude, longitude in ((0, -40), (0, 140.7 + 85), (89, 140.7)):
            with self.subTest(place=(latitude, longitude)), self.assertRaises(ValueError):
                himawari.disk_pixel(latitude, longitude, 11000)


class RenderTests(unittest.TestCase):
    COLORS = {(0, 0): (255, 0, 0), (1, 0): (0, 255, 0), (0, 1): (0, 0, 255), (1, 1): (255, 255, 0)}

    def client_with_tiles(self, area_id="nict_full_disk", latest=LATEST):
        client = FixtureClient()
        client.responses[latest] = b'{"date":"2026-09-13 16:00:00","file":"still.png"}'
        product = "B13" if area_id == "nict_full_disk_bands" else "true_color"
        frame = client.latest("himawari", area_id, product, "1100x1100")
        area = client._area(area_id)
        for (x, y), color in self.COLORS.items():
            client.responses[client._nict_tile_url(area, product, 2, frame["date_code"], x, y)] = png((550, 550), color)
        return client, frame, area

    @staticmethod
    def pixel(data, point):
        with Image.open(io.BytesIO(data)) as image:
            return image.getpixel(point)

    def test_shorelines_draw_nicts_coastline_in_the_chosen_color_and_stay_on_disk(self):
        client, frame, area = self.client_with_tiles()
        with tempfile.TemporaryDirectory() as folder:
            client.shoreline_cache_dir = Path(folder)
            for x, y in self.COLORS:
                # A coastline across the top half of every tile, in NICT's yellow.
                with Image.new("RGBA", (550, 550), (255, 255, 0, 0)) as overlay:
                    overlay.paste((255, 255, 0, 255), (0, 0, 550, 275))
                    raw = io.BytesIO()
                    overlay.save(raw, "PNG")
                client.responses[himawari.NICT_IMAGE_BASE + client._shoreline_path(area, 2, x, y)] = raw.getvalue()
            with patch.object(himawari.DOWNLOAD_PROGRESS, "set_expected_requests") as expected:
                data = client.fetch_image(frame, (100, 100), shorelines="#FF00FF")
            expected.assert_called_once_with(8)  # Four picture tiles and four coastline tiles.
            self.assertEqual(self.pixel(data, (25, 10)), (255, 0, 255))
            self.assertEqual(self.pixel(data, (25, 40)), (255, 0, 0))
            self.assertEqual(len(list(Path(folder).iterdir())), 4)
            # The next picture reads the coastline from disk.
            client.requests.clear()
            with patch.object(himawari.DOWNLOAD_PROGRESS, "set_expected_requests") as expected:
                again = client.fetch_image(frame, (100, 100), shorelines="#00FFFF")
            expected.assert_called_once_with(4)
            self.assertFalse(any("coastline" in url for url in client.requests))
            self.assertEqual(self.pixel(again, (25, 10)), (0, 255, 255))
        # Off: no coastline at all.
        client.requests.clear()
        self.assertEqual(self.pixel(client.fetch_image(frame, (100, 100)), (25, 10)), (255, 0, 0))
        self.assertFalse(any("coastline" in url for url in client.requests))
        with self.assertRaises(himawari.HimawariError):
            client.fetch_image(frame, (100, 100), shorelines="yellow")

    def test_band_shorelines_use_one_coastline_for_every_band(self):
        client = FixtureClient()
        area = client._area("nict_full_disk_bands")
        self.assertIn("/FULL_24h/B13/4d/550/coastline/", client._shoreline_path(area, 4, 1, 2))

    def test_a_centred_view_puts_the_place_exactly_in_the_middle(self):
        client, frame, _area = self.client_with_tiles()
        # A place in the top right quarter is in the middle, zoomed in or not.
        frame["center"] = [30.0, 165.0]
        for zoom in (2, 1):
            with self.subTest(zoom=zoom):
                data = client.fetch_image(frame, (100, 100), zoom=zoom)
                self.assertEqual(self.pixel(data, (50, 50)), self.COLORS[(1, 0)])
        # Also near the eastern limb: space beyond the disk shows the background.
        frame["center"] = [0.0, -144.3]
        data = client.fetch_image(frame, (100, 100), zoom=2)
        self.assertEqual(self.pixel(data, (40, 30)), self.COLORS[(1, 0)])
        self.assertEqual(self.pixel(data, (99, 50)), (0, 0, 0))
        # A place Himawari cannot see is refused.
        frame["center"] = [0.0, -40.0]
        with self.assertRaises(himawari.HimawariError):
            client.fetch_image(frame, (100, 100), zoom=2)

    def test_only_the_full_disk_can_be_centred(self):
        client = FixtureClient()
        client.responses[himawari.NICT_IMAGE_BASE + "img/D531107/latest.json"] = \
            b'{"date":"2026-09-13 16:00:00","file":"still.png"}'
        frame = client.latest("himawari", "nict_japan", "true_color", "600x480")
        frame["center"] = [35.0, 139.0]
        with self.assertRaises(himawari.HimawariError):
            client.fetch_image(frame, (100, 100))


class StormViewTests(unittest.TestCase):
    def test_a_storm_is_shown_storm_sized_and_centred(self):
        # About 3000 km across at Zoom 1: the full disk (11000 km) zoomed about 3.7 times in a square.
        self.assertAlmostEqual(himawari.storm_view_zoom((100, 100), 1), 11000 / 3000)
        self.assertAlmostEqual(himawari.storm_view_zoom((1920, 1080), 2), 2 * 1920 * 11000 / (3000 * 1080))
        self.assertTrue(himawari.is_storm_area("jma_storm_TC2634"))
        self.assertTrue(himawari.is_storm_area("nict_target_area"))
        self.assertFalse(himawari.is_storm_area("nict_full_disk"))
        client = storm_client(("TC2634", "STS", "Koguma", -20.0, 160.0))
        frame = client.latest("himawari", "jma_storm_TC2634", "true_color", "1100x1100")
        area = client._area("jma_storm_TC2634")
        colors = RenderTests.COLORS
        for (x, y), color in colors.items():
            client.responses[client._nict_tile_url(area, "true_color", 2, frame["date_code"], x, y)] = \
                png((550, 550), color)
        with patch.object(himawari.DOWNLOAD_PROGRESS, "set_expected_requests") as expected:
            data = client.fetch_image(frame, (100, 100), fit_mode="crop", zoom=1)
        # The storm (south-east quarter) fills the view; only its own tile is loaded.
        self.assertEqual(expected.call_args.args[0], 1)
        for point in ((0, 0), (50, 50), (99, 99)):
            self.assertEqual(RenderTests.pixel(data, point), colors[(1, 1)])

    def test_automatic_resolution_is_sharp_for_the_storm_view(self):
        client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3))
        profile = dict(himawari.DEFAULT_PROFILE, area="jma_storm_TC2634")
        with patch.object(app, "VIEW_MODE", "fit"), patch.object(app, "ZOOM", 1.0), \
                patch.object(app, "automatic_source_output_size", side_effect=lambda size: size):
            self.assertEqual(app._resolved_profile_resolution(client, "himawari", profile, (1920, 1080)),
                             "8800x8800")
            profile["area"] = "nict_full_disk"
            self.assertEqual(app._resolved_profile_resolution(client, "himawari", profile, (1920, 1080)),
                             "1100x1100")


class StormTests(unittest.TestCase):
    def test_storms_are_listed_with_names_after_the_target_area(self):
        client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3), ("TC2633", "TD", "", 12.0, 130.0))
        storms, products, failed = client.list_storms()
        labels = [area["label"] for area in storms["himawari"]]
        self.assertEqual(labels, ["Strongest active storm", "Himawari target area (rapid scan)",
                                  "Severe Tropical Storm Koguma",
                                  "Tropical Depression (TC2633)"])
        self.assertEqual(failed, set())
        self.assertEqual(products[("himawari", "jma_storm_TC2634")][0]["resolutions"][-1], "11000x11000")
        listed = client.list_areas("himawari")
        self.assertIn("jma_storm_TC2634", [area["id"] for area in listed])
        self.assertTrue(all(area["category"] == himawari.STORM_CATEGORY for area in storms["himawari"]))

    def test_a_storm_frame_is_centred_on_its_latest_position_and_ends_as_lost(self):
        client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3))
        frame = client.latest("himawari", "jma_storm_TC2634", "true_color", "2200x2200")
        self.assertEqual(frame["center"], [17.5, 160.3])
        self.assertEqual(frame["dataset"], "D531106")
        # JMA moved it: a later picture follows.
        client.responses[f"{himawari.JMA_TYPHOON_BASE}TC2634/specifications.json"] = \
            cyclone_details("Koguma", 18.0, 158.0)
        client._storm_details.clear()
        self.assertEqual(client.latest("himawari", "jma_storm_TC2634", "true_color", "2200x2200")["center"],
                         [18.0, 158.0])
        # JMA no longer lists it: the profile shows LOST.
        client.responses[CYCLONES] = cyclone_list()
        client._storm_list = (0.0, None)
        with self.assertRaises(himawari.SelectionLostError):
            client.latest("himawari", "jma_storm_TC2634", "true_color", "2200x2200")
        self.assertEqual(app.profile_failure_state(himawari.SelectionLostError("x")), "LOST")

    def test_strongest_active_storm_follows_the_strongest_cyclone(self):
        client = storm_client(("TC2633", "TD", "", 12.0, 130.0), ("TC2634", "STS", "Koguma", 17.5, 160.3),
                              ("TC2635", "STS", "Nolo", 20.0, 150.0))
        # Equal strength: the newest cyclone.
        frame = client.latest("himawari", himawari.STRONGEST_STORM_ID, "true_color", "2200x2200")
        self.assertEqual(frame["center"], [20.0, 150.0])
        self.assertEqual(frame["area_label"], "Strongest active storm (Severe Tropical Storm Nolo)")
        self.assertTrue(himawari.is_storm_area(himawari.STRONGEST_STORM_ID))
        # An hour later Koguma, now a typhoon, is chosen.
        client.responses[CYCLONES] = cyclone_list(("TC2634", "TY"), ("TC2635", "STS"))
        client._storm_list = (0.0, None)
        an_hour_passes(client)
        self.assertEqual(client.latest("himawari", himawari.STRONGEST_STORM_ID, "true_color", "2200x2200")["center"],
                         [17.5, 160.3])
        # Without a cyclone it is unavailable (UNAVAIL), not lost.
        client.responses[CYCLONES] = cyclone_list()
        client._storm_list = (0.0, None)
        an_hour_passes(client)
        with self.assertRaises(himawari.UnavailableError) as raised:
            client.latest("himawari", himawari.STRONGEST_STORM_ID, "true_color", "2200x2200")
        self.assertNotIsInstance(raised.exception, himawari.SelectionLostError)

    def test_the_strongest_cyclone_is_chosen_hourly_and_still_followed(self):
        client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3), ("TC2635", "STS", "Nolo", 20.0, 150.0))

        def latest():
            return client.latest("himawari", himawari.STRONGEST_STORM_ID, "true_color", "2200x2200")

        self.assertEqual(latest()["center"], [20.0, 150.0])
        # Koguma becomes a typhoon and Nolo moves: within the hour Nolo stays
        # chosen, and the view still follows Nolo's newest position.
        client.responses[CYCLONES] = cyclone_list(("TC2634", "TY"), ("TC2635", "STS"))
        client.responses[f"{himawari.JMA_TYPHOON_BASE}TC2635/specifications.json"] = \
            cyclone_details("Nolo", 21.0, 149.0)
        client._storm_list = (0.0, None)
        client._storm_details.clear()
        self.assertEqual(latest()["center"], [21.0, 149.0])
        an_hour_passes(client)
        self.assertEqual(latest()["center"], [17.5, 160.3])

    def test_a_remembered_cyclone_jma_no_longer_lists_is_replaced_at_once(self):
        client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3), ("TC2635", "STS", "Nolo", 20.0, 150.0))

        def latest():
            return client.latest("himawari", himawari.STRONGEST_STORM_ID, "true_color", "2200x2200")

        self.assertEqual(latest()["center"], [20.0, 150.0])
        # Nolo ends within the hour: the next check chooses Koguma instead of failing.
        client.responses[CYCLONES] = cyclone_list(("TC2634", "STS"))
        client._storm_list = (0.0, None)
        frame = latest()
        self.assertEqual(frame["center"], [17.5, 160.3])
        self.assertEqual(frame["area_label"], "Strongest active storm (Severe Tropical Storm Koguma)")

    def test_a_storm_saved_before_a_restart_is_found_again(self):
        client = storm_client(("TC2634", "TY", "Koguma", 17.5, 160.3))
        self.assertEqual(client._area("jma_storm_TC2634")["label"], "Typhoon Koguma")
        with self.assertRaises(himawari.SelectionLostError):
            client._area("jma_storm_TC9999")

    def test_the_target_area_follows_nicts_rapid_scan_position(self):
        client = storm_client()
        client.responses[himawari.NICT_SITE_URL + "json/D531108/2026/10/08/030000.json"] = json.dumps(
            {"center": [27.289, 154.332], "type": "TY"}).encode()
        frame = client.latest("himawari", "nict_target_area", "true_color", "1100x1100")
        self.assertEqual(frame["center"], [27.289, 154.332])

    def test_jma_outage_keeps_the_last_storms_and_reports_the_missing_category(self):
        client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3))
        client.responses[himawari.JMA_BASE + "sat_img.php?area=fd_"] = \
            b'<select name="slt_area"><option value="fd_">Full Disk</option></select>'
        self.assertIn("jma_storm_TC2634", [area["id"] for area in client.list_areas("himawari", refresh=True)])
        self.assertEqual(client.catalogue_missing_categories, frozenset())
        client.responses[CYCLONES] = himawari.UnavailableError("offline")

        def request(url, limit, track=False):
            value = client.responses[url]
            if isinstance(value, Exception):
                raise value
            return value, {}

        with patch.object(client, "_request", side_effect=request):
            areas = client.list_areas("himawari", refresh=True)
        self.assertIn("jma_storm_TC2634", [area["id"] for area in areas])
        self.assertEqual(client.catalogue_missing_categories, {himawari.STORM_CATEGORY})
        self.assertIn("Active storms", client.catalogue_warning)

    def test_the_catalogue_checks_himawari_storms_hourly(self):
        with tempfile.TemporaryDirectory() as folder:
            client = storm_client(("TC2634", "STS", "Koguma", 17.5, 160.3))
            catalogue = CatalogueClient(himawari=client, cache_path=Path(folder) / "catalogues.json")
            areas = client.list_areas("himawari")
            catalogue._cache.store_provider("himawari", areas, {
                area["id"]: client.list_products("himawari", area["id"]) for area in areas})
            self.assertTrue(catalogue._cache.mark_source_checked("himawari"))
            client.requests.clear()
            self.assertNotIn("jma_storm_TC2634", [a["id"] for a in catalogue.list_areas("himawari")])
            self.assertEqual(client.requests, [])
            client.responses[CYCLONES] = cyclone_list(("TC2634", "STS"), ("TC2635", "TS"))
            client.responses[f"{himawari.JMA_TYPHOON_BASE}TC2635/specifications.json"] = \
                cyclone_details("Nolo", 20.0, 150.0)
            catalogue._cache._data["himawari_storms_checked_at"] -= NOAA_STORM_CHECK_SECONDS + 1
            changes = []
            catalogue.on_storms_changed = lambda *values: changes.append(values)
            listed = catalogue.list_areas("himawari")
            # The Settings note: Koguma and Nolo are new, none ended.
            self.assertEqual(changes, [("himawari", 2, 0)])
            storms = [area["label"] for area in listed if area["category"] == himawari.STORM_CATEGORY]
            self.assertEqual(storms, ["Strongest active storm", "Himawari target area (rapid scan)",
                                      "Severe Tropical Storm Koguma", "Tropical Storm Nolo"])
            # The other areas keep their place before the storms.
            self.assertEqual([a["id"] for a in listed][:3], ["nict_full_disk", "nict_japan", "nict_full_disk_bands"])
            self.assertTrue(catalogue._cache.source_fresh("himawari"))
            self.assertTrue(catalogue.list_products("himawari", "jma_storm_TC2635"))
            # The category order stays the same after the storm check: Active storms third.
            categories = list(dict.fromkeys(area["category"] for area in listed))
            self.assertEqual(categories, list(himawari.CATEGORY_ORDER))
            self.assertEqual(categories[2], himawari.STORM_CATEGORY)

    def test_category_order_is_fixed_missing_ones_are_left_out_and_new_ones_follow(self):
        areas = [{"id": "jma_a", "category": "JMA Regions", "label": "B"},
                 {"id": "new", "category": "Brand new", "label": "N"},
                 {"id": "jma_storm_X", "category": himawari.STORM_CATEGORY, "label": "Typhoon Zed", "storm": "X"},
                 {"id": "nict", "category": "NICT True Color", "label": "Full"},
                 {"id": "jma_b", "category": "JMA Regions", "label": "A"},
                 {"id": "nict_target_area", "category": himawari.STORM_CATEGORY, "label": "Target",
                  "storm": "target"},
                 {"id": "newer", "category": "Newer", "label": "M"}]
        self.assertEqual([area["id"] for area in himawari.ordered_areas(areas)],
                         ["nict", "nict_target_area", "jma_storm_X", "jma_a", "jma_b", "new", "newer"])


class AppTests(unittest.TestCase):
    def profile(self, **values):
        return himawari.normalize_profile(dict(himawari.DEFAULT_PROFILE, **values))

    def configuration(self, profile):
        configuration = app.capture_loaded_configuration()
        configuration["IMAGE_SOURCE"] = "himawari"
        configuration["SOURCE_PROFILES"] = dict(deepcopy(configuration["SOURCE_PROFILES"]), himawari=profile)
        return configuration

    def test_unused_settings_keep_the_cache_key_of_earlier_pictures(self):
        plain = app.image_configuration_key(self.configuration(self.profile()))
        self.assertEqual(plain["selection"], {"area": "nict_full_disk", "product": "true_color",
                                              "resolution": "auto"})
        # A color or coordinates that are not used do not count either.
        unused = self.profile(shoreline_color="#FF0000", latitude=35.0, longitude=139.0)
        self.assertEqual(app.image_configuration_key(self.configuration(unused)), plain)
        for changed in (self.profile(shorelines=True), self.profile(center=True, latitude=35.0)):
            with self.subTest(changed=changed):
                self.assertNotEqual(app.image_configuration_key(self.configuration(changed)), plain)

    def test_frame_centre_signature_and_shoreline_color(self):
        client = FixtureClient()
        client.responses[LATEST] = b'{"date":"2026-09-13 16:00:00","file":"still.png"}'
        client.responses[himawari.NICT_IMAGE_BASE + "img/D531107/latest.json"] = client.responses[LATEST]
        for area, centred in (("nict_full_disk", True), ("nict_japan", False)):
            profile = self.profile(area=area, resolution="largest", center=True, latitude=35.0,
                                   longitude=139.0, shorelines=True, shoreline_color="#FF0000")
            with self.subTest(area=area), \
                    patch.dict(app.SOURCE_PROFILES, {"himawari": profile}), \
                    patch.object(app, "get_himawari_client", return_value=client):
                frame = app.get_himawari_frame((32, 18))
                self.assertEqual(frame.get("center"), [35.0, 139.0] if centred else None)
                self.assertEqual(app.himawari_shoreline_color(), "#FF0000")
                signature = app.himawari_frame_signature(frame)
                profile["shorelines"] = False
                self.assertIsNone(app.himawari_shoreline_color())
                self.assertNotEqual(app.himawari_frame_signature(frame), signature)

    def test_png_record_names_the_view_and_conflicts_are_found(self):
        profile = self.profile(shorelines=True, shoreline_color="#00FF00", center=True,
                               latitude=35.0, longitude=139.0)
        settings = app.default_import_settings()
        settings["source"]["provider"] = "himawari"
        settings["sources"]["himawari"] = profile
        settings = app.normalize_image_settings_snapshot(settings)
        frame = {"center": [35.0, 139.0]}
        with patch.object(app, "IMAGE_SOURCE", "himawari"), \
             patch.object(app, "SOURCE_PROFILES", deepcopy(settings["sources"])), \
             patch.object(app, "image_settings_snapshot", return_value=deepcopy(settings)):
            record = app.image_provenance("himawari", [{"frame": frame}], (8, 6))
        self.assertEqual((record["shorelines"], record["shoreline_color"]), (True, "#00FF00"))
        self.assertEqual((record["center"], record["latitude"], record["longitude"]), (True, 35.0, 139.0))
        self.assertEqual((record["center_latitude"], record["center_longitude"]), (35.0, 139.0))
        _check_png_profile_metadata(record, settings)
        for field, value in (("shorelines", False), ("shoreline_color", "#FFFFFF"),
                             ("latitude", 1.0), ("center", None)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                _check_png_profile_metadata(dict(record, **{field: value}), settings)
        other = app.normalize_image_settings_snapshot(app.default_import_settings())
        with self.assertRaises(ValueError):
            _check_png_profile_metadata({"source": record["source"], "shorelines": True}, other)

    def test_profile_table_shows_shorelines_and_the_centre(self):
        settings = {"source": {"provider": "himawari"},
                    "sources": {"himawari": self.profile(shorelines=True, shoreline_color="#00ff00",
                                                         center=True, latitude=35.5)}}
        self.assertEqual(_profile_shorelines(settings), "On · #00FF00")
        self.assertEqual(_profile_latitude(settings), "35.5")
        settings["sources"]["himawari"]["shorelines"] = False
        self.assertEqual(_profile_shorelines(settings), "Off")
        # JMA's stills have no shoreline layer, and only the full disk is centred.
        settings["sources"]["himawari"]["area"] = "jma_jpn"
        self.assertEqual((_profile_shorelines(settings), _profile_latitude(settings)), ("-", "-"))
        self.assertEqual(_profile_shorelines({"source": {"provider": "goes_east"}}), "-")


if __name__ == "__main__":
    unittest.main()
