"""NOAA's Active storms between the daily catalogue refreshes, and incomplete refreshes."""

from pathlib import Path
import tempfile
import time
import unittest

from marblescape_catalogues import CatalogueClient, NOAA_STORM_CHECK_SECONDS
from marblescape_noaa import BASE_URL, NOAAError, STORM_CATEGORY, STRONGEST_STORM_ID, UnavailableError

from test_noaa import CDN, INDEX, FakeClient, image_url, product_page

EAST_STORM = image_url(CDN + "FLOATER/AL092026/GEOCOLOR/", size="1000x1000")
WEST_STORM = image_url(CDN + "FLOATER/EP182026/GEOCOLOR/", satellite="18", size="1000x1000")
OLD_STORM = image_url(CDN + "FLOATER/EP142026/GEOCOLOR/", satellite="18", size="1000x1000")
BOX = image_url(CDN + "WFO/box/GEOCOLOR/", size="600x600")


def storm_links(*storms):
    return "".join(f"<a href='floater.php?stormid={storm_id}'>{label}</a>" for storm_id, label in storms)


def catalogue_pages(storms=(("AL092026", "Tropical Depression Nine"), ("EP142026", "Norbert"))):
    """NOAA's pages for a whole catalogue: GOES index, one WFO and the given storms."""
    return {
        BASE_URL + "index.php": INDEX,
        BASE_URL + "meso.php": "",
        BASE_URL + "wfo_index.php": "<a href='wfo.php?wfo=box'>Boston</a>",
        BASE_URL + "floater_index.php": storm_links(*storms),
        BASE_URL + "wfo.php?wfo=box": product_page(BOX),
        BASE_URL + "floater.php?stormid=AL092026": product_page(EAST_STORM),
        BASE_URL + "floater.php?stormid=EP142026": product_page(OLD_STORM),
        BASE_URL + "floater.php?stormid=EP182026": product_page(WEST_STORM),
        BASE_URL + "fulldisk.php?sat=G19": product_page(image_url()),
        BASE_URL + "conus.php?sat=G19": product_page(image_url(CDN + "GOES19/ABI/CONUS/GEOCOLOR/")),
        BASE_URL + "sector.php?sat=G19&sector=sp": product_page(
            image_url(CDN + "GOES19/ABI/SECTOR/sp/GEOCOLOR/")),
        BASE_URL + "meso.php?sat=G19&lat=38N&lon=75W": product_page(
            image_url(CDN + "GOES19/ABI/MESO/38N-75W/GEOCOLOR/")),
        BASE_URL + "fulldisk.php?sat=G18": product_page(
            image_url(CDN + "GOES18/ABI/FD/GEOCOLOR/", satellite="18")),
        BASE_URL + "conus.php?sat=G18": product_page(
            image_url(CDN + "GOES18/ABI/CONUS/GEOCOLOR/", satellite="18")),
        BASE_URL + "SUVI.php?sat=G19": product_page(
            CDN + "GOES19/SUVI/FD/Fe171/20262541430001_GOES19-SUVI-Fe171-300x300.jpg"),
    }


NEW_STORMS = (("AL092026", "Tropical Storm Isaias"), ("EP182026", "Hurricane Rachel"))


def storm_labels(areas):
    """The named storms; Strongest active storm is always listed besides them."""
    return sorted(area["label"] for area in areas if area.get("category") == STORM_CATEGORY
                  and area["id"] != STRONGEST_STORM_ID)


class StormListTests(unittest.TestCase):
    def test_storm_check_reads_only_the_storm_pages(self):
        client = FakeClient(catalogue_pages(NEW_STORMS))
        storms, products, failed = client.list_storms()
        self.assertEqual(storm_labels(storms["goes_east"]), ["Tropical Storm Isaias"])
        self.assertEqual(storm_labels(storms["goes_west"]), ["Hurricane Rachel"])
        self.assertEqual(products[("goes_east", "storm_AL092026")][0]["resolutions"], ["1000x1000"])
        self.assertEqual(failed, set())
        read = {url for url, _ in client.requests}
        self.assertFalse(read & {BASE_URL + "meso.php", BASE_URL + "wfo_index.php",
                                 BASE_URL + "wfo.php?wfo=box"})

    def test_storm_check_updates_renamed_and_ended_storms_in_memory(self):
        pages = catalogue_pages()
        client = FakeClient(pages)
        self.assertEqual(storm_labels(client.list_areas("goes_west")), ["Norbert"])
        pages[BASE_URL + "floater_index.php"] = storm_links(*NEW_STORMS)
        client.list_storms()
        self.assertEqual(storm_labels(client.list_areas("goes_east")), ["Tropical Storm Isaias"])
        self.assertEqual(storm_labels(client.list_areas("goes_west")), ["Hurricane Rachel"])

    def test_failed_storm_page_is_reported_and_failed_index_raises(self):
        pages = catalogue_pages(NEW_STORMS)
        pages[BASE_URL + "floater.php?stormid=EP182026"] = UnavailableError("Temporary outage")
        storms, _products, failed = FakeClient(pages).list_storms()
        self.assertEqual(failed, {"storm_EP182026"})
        self.assertEqual(storm_labels(storms["goes_west"]), [])
        pages[BASE_URL + "floater_index.php"] = UnavailableError("Temporary outage")
        with self.assertRaises(NOAAError):
            FakeClient(pages).list_storms()

    def test_discovery_reports_what_it_could_not_read(self):
        pages = catalogue_pages()
        pages[BASE_URL + "wfo.php?wfo=box"] = UnavailableError("Temporary WFO outage")
        client = FakeClient(pages)
        client.list_areas("goes_east")
        self.assertEqual(client.catalogue_missing_categories, frozenset())
        self.assertEqual(client.catalogue_failed_areas, {"wfo_box"})
        pages[BASE_URL + "floater_index.php"] = UnavailableError("Temporary storm outage")
        client.list_areas("goes_east", refresh=True)
        self.assertEqual(client.catalogue_missing_categories, {STORM_CATEGORY})


class StrongestStormTests(unittest.TestCase):
    def test_strength_order_then_the_newest(self):
        from marblescape_noaa import storm_rank, strongest_storm
        ranks = [storm_rank({"label": label}) for label in (
            "Super Typhoon A", "Hurricane B", "Tropical Storm C", "Subtropical Storm D",
            "Tropical Depression E", "Post-Tropical Cyclone F", "Invest 94L", "Something else")]
        self.assertEqual(ranks, [5, 4, 3, 3, 2, 1, 1, 0])
        storms = [{"id": "storm_EP142026", "label": "Tropical Storm Norbert"},
                  {"id": "storm_EP182026", "label": "Tropical Storm Rachel"},
                  {"id": "storm_AL092026", "label": "Tropical Depression Nine"}]
        self.assertEqual(strongest_storm(storms)["id"], "storm_EP182026")
        storms[0]["label"] = "Hurricane Norbert"
        self.assertEqual(strongest_storm(storms)["id"], "storm_EP142026")
        self.assertIsNone(strongest_storm([{"id": STRONGEST_STORM_ID, "label": "Strongest active storm"}]))

    def test_the_entry_is_listed_first_and_shows_the_strongest_storm(self):
        pages = catalogue_pages((("EP142026", "Tropical Storm Norbert"), ("EP182026", "Hurricane Rachel")))
        pages[CDN + "FLOATER/EP182026/GEOCOLOR/"] = product_page(WEST_STORM)
        client = FakeClient(pages)
        west = [area for area in client.list_areas("goes_west") if area["category"] == STORM_CATEGORY]
        self.assertEqual([area["id"] for area in west][0], STRONGEST_STORM_ID)
        storms, products, _failed = client.list_storms()
        self.assertEqual(products[("goes_west", STRONGEST_STORM_ID)], products[("goes_west", "storm_EP182026")])
        frame = client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        self.assertEqual(frame["area"], STRONGEST_STORM_ID)
        self.assertEqual(frame["area_label"], "Strongest active storm (Hurricane Rachel)")
        self.assertIn("/FLOATER/EP182026/", frame["url"])

    @staticmethod
    def west_storm_pages():
        pages = catalogue_pages((("EP142026", "Tropical Storm Norbert"), ("EP182026", "Hurricane Rachel")))
        pages[CDN + "FLOATER/EP182026/GEOCOLOR/"] = product_page(WEST_STORM)
        pages[CDN + "FLOATER/EP142026/GEOCOLOR/"] = product_page(OLD_STORM)
        return pages

    @staticmethod
    def storm_index_reads(client):
        return sum(url == BASE_URL + "floater_index.php" for url, _headers in client.requests)

    def test_the_strongest_storm_is_chosen_hourly_not_at_every_check(self):
        from marblescape_noaa import STRONGEST_STORM_TTL
        client = FakeClient(self.west_storm_pages())
        for _ in range(3):
            frame = client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        # Every check asks for a newer picture; the storm pages are read once.
        self.assertIn("/FLOATER/EP182026/", frame["url"])
        self.assertEqual(sum(url == CDN + "FLOATER/EP182026/GEOCOLOR/" for url, _ in client.requests), 3)
        self.assertEqual(self.storm_index_reads(client), 1)
        # An hour later the choice is renewed.
        chosen_at, best, number = client._strongest["goes_west"]
        client._strongest["goes_west"] = (chosen_at - STRONGEST_STORM_TTL, best, number)
        client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        self.assertEqual(self.storm_index_reads(client), 2)

    def test_a_remembered_storm_that_ended_is_replaced_at_once(self):
        pages = self.west_storm_pages()
        client = FakeClient(pages)
        client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        # Rachel ends within the hour: NOAA drops her from the index and her pictures.
        pages[BASE_URL + "floater_index.php"] = storm_links(("EP142026", "Tropical Storm Norbert"))
        pages[CDN + "FLOATER/EP182026/GEOCOLOR/"] = UnavailableError("HTTP 404")
        frame = client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        self.assertEqual(frame["area_label"], "Strongest active storm (Tropical Storm Norbert)")
        self.assertIn("/FLOATER/EP142026/", frame["url"])

    def test_a_storm_chosen_in_the_same_check_is_not_chosen_again(self):
        pages = self.west_storm_pages()
        pages[CDN + "FLOATER/EP182026/GEOCOLOR/"] = UnavailableError("HTTP 503")
        client = FakeClient(pages)
        with self.assertRaises(UnavailableError):
            client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        # One storm list read: no loop over NOAA's storm pages on an outage.
        self.assertEqual(self.storm_index_reads(client), 1)

    def test_while_noaa_pages_fail_the_known_storm_and_regions_are_kept(self):
        from marblescape_noaa import STRONGEST_STORM_RETRY, STRONGEST_STORM_TTL
        pages = self.west_storm_pages()
        client = FakeClient(pages)
        client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        # NOAA cuts its index pages off, and the storm's own page too: the hour of
        # the choice and the 5 minutes of the regions are over.
        for page in ("index.php", "floater_index.php", "floater.php?stormid=EP182026"):
            pages[BASE_URL + page] = UnavailableError("NOAA is currently unreachable: IncompleteRead(11599 bytes read)")
        chosen_at, best, number = client._strongest["goes_west"]
        client._strongest["goes_west"] = (chosen_at - STRONGEST_STORM_TTL, best, number)
        client._base_time -= 300
        page_time, products = client._product_pages[(best["url"], best["satellite"])]
        client._product_pages[(best["url"], best["satellite"])] = (page_time - 2 * 24 * 3600, products)
        frame = client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        self.assertEqual(frame["area_label"], "Strongest active storm (Hurricane Rachel)")
        self.assertIn("/FLOATER/EP182026/", frame["url"])
        # The storm list is read again in 10 minutes, the index page in 5, not at every check.
        self.assertAlmostEqual(STRONGEST_STORM_TTL - (time.monotonic() - client._strongest["goes_west"][0]),
                               STRONGEST_STORM_RETRY, delta=5)
        reads = len(client.requests)
        client.latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        self.assertEqual([url for url, _ in client.requests[reads:]], [CDN + "FLOATER/EP182026/GEOCOLOR/"])
        # A full disk check keeps working on the known regions too.
        pages[CDN + "GOES18/ABI/FD/GEOCOLOR/"] = product_page(image_url(CDN + "GOES18/ABI/FD/GEOCOLOR/", satellite="18"))
        self.assertIn("/GOES18/ABI/FD/", client.latest("goes_west", "full_disk", "GEOCOLOR", "largest")["url"])

    def test_without_a_known_storm_a_failing_storm_list_is_reported(self):
        pages = self.west_storm_pages()
        pages[BASE_URL + "floater_index.php"] = UnavailableError("NOAA is currently unreachable: IncompleteRead")
        with self.assertRaises(UnavailableError):
            FakeClient(pages).latest("goes_west", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")

    def test_without_a_storm_it_is_unavailable_not_lost(self):
        import marblescape_download as app
        from marblescape_noaa import SelectionLostError
        client = FakeClient(catalogue_pages(()))
        self.assertIn(STRONGEST_STORM_ID, [area["id"] for area in client.list_areas("goes_east")])
        with self.assertRaises(UnavailableError) as raised:
            client.latest("goes_east", STRONGEST_STORM_ID, "GEOCOLOR", "1000x1000")
        self.assertNotIsInstance(raised.exception, SelectionLostError)
        self.assertEqual(app.profile_failure_state(raised.exception), "UNAVAIL")

    def test_a_catalogue_saved_before_the_entry_existed_lists_it_at_once(self):
        import marblescape_himawari as himawari
        with tempfile.TemporaryDirectory() as folder:
            catalogue = CatalogueClient(noaa=FakeClient({}), cache_path=Path(folder) / "catalogues.json")
            old = [{"id": "full_disk", "label": "Full Disk", "category": "Full Disk"},
                   {"id": "storm_AL092026", "label": "Hurricane Isaias", "category": STORM_CATEGORY}]
            catalogue._cache.store_areas("goes_east", old)
            listed = catalogue._with_strongest_storm("goes_east", catalogue._cache.areas("goes_east"))
            self.assertEqual([area["id"] for area in listed], ["full_disk", STRONGEST_STORM_ID, "storm_AL092026"])
            himawari_old = [{"id": "nict_full_disk", "label": "Full Disk", "category": "NICT True Color"},
                            {"id": "nict_target_area", "label": "Target", "category": STORM_CATEGORY,
                             "storm": "target"},
                            {"id": "jma_fd_", "label": "JMA - Full Disk", "category": "JMA Full Disk"}]
            listed = catalogue._with_strongest_storm("himawari", himawari_old)
            self.assertEqual([area["id"] for area in listed],
                             ["nict_full_disk", himawari.STRONGEST_STORM_ID, "nict_target_area", "jma_fd_"])
            # Other sources and an already complete list stay as they are.
            self.assertIs(catalogue._with_strongest_storm("himawari", listed), listed)
            self.assertEqual(catalogue._with_strongest_storm("slider", old), old)

    def test_the_cached_catalogue_stays_complete_without_a_storm(self):
        with tempfile.TemporaryDirectory() as folder:
            catalogue = CatalogueClient(noaa=FakeClient(catalogue_pages(())),
                                        cache_path=Path(folder) / "catalogues.json", retries=1)
            self.assertTrue(catalogue.noaa.refresh_all_catalogues(refresh=True)["complete"])
            for provider in ("goes_east", "goes_west", "solar"):
                catalogue._cache_provider(provider)
            self.assertIsNone(catalogue._cache.products("goes_east", STRONGEST_STORM_ID))
            self.assertTrue(catalogue._cache.mark_noaa_checked())
            self.assertTrue(catalogue._cache.noaa_fresh())


class StormCatalogueCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "catalogues.json"

    def cached_catalogue(self):
        """A complete NOAA catalogue in the cache, read with the old storms."""
        first = CatalogueClient(noaa=FakeClient(catalogue_pages()), cache_path=self.path, retries=1)
        self.assertTrue(first.noaa.refresh_all_catalogues(refresh=True)["complete"])
        for provider in ("goes_east", "goes_west", "solar"):
            first._cache_provider(provider)
        self.assertTrue(first._cache.mark_noaa_checked())
        self.assertTrue(first._cache.noaa_fresh())
        return first

    def test_image_tab_refresh_keeps_new_storms_when_one_page_fails(self):
        self.cached_catalogue()
        pages = catalogue_pages(NEW_STORMS)
        pages[BASE_URL + "wfo.php?wfo=box"] = UnavailableError("Temporary WFO outage")
        client = CatalogueClient(noaa=FakeClient(pages), cache_path=self.path, retries=1)
        areas = client.list_areas("goes_east", refresh=True)
        self.assertEqual(storm_labels(areas), ["Tropical Storm Isaias"])
        # The unreadable WFO keeps its cached entry, and the note says so.
        self.assertIn("wfo_box", [area["id"] for area in areas])
        self.assertIn("cached", client._fallback_warnings["goes_east"])
        self.assertEqual(storm_labels(client._cache.areas("goes_east")), ["Tropical Storm Isaias"])

    def test_image_tab_refresh_keeps_cached_storms_when_the_storm_index_fails(self):
        self.cached_catalogue()
        pages = catalogue_pages(NEW_STORMS)
        pages[BASE_URL + "floater_index.php"] = UnavailableError("Temporary storm outage")
        client = CatalogueClient(noaa=FakeClient(pages), cache_path=self.path, retries=1)
        self.assertEqual(storm_labels(client.list_areas("goes_west", refresh=True)), ["Norbert"])

    def test_storm_list_is_checked_hourly_without_the_whole_catalogue(self):
        self.cached_catalogue()
        noaa = FakeClient(catalogue_pages(NEW_STORMS))
        client = CatalogueClient(noaa=noaa, cache_path=self.path, retries=1)
        # Just checked by the complete refresh: the cache answers.
        self.assertEqual(storm_labels(client.list_areas("goes_west")), ["Norbert"])
        self.assertEqual(noaa.requests, [])

        client._cache._data["noaa_storms_checked_at"] -= NOAA_STORM_CHECK_SECONDS + 1
        self.assertEqual(storm_labels(client.list_areas("goes_east")), ["Tropical Storm Isaias"])
        self.assertEqual(storm_labels(client.list_areas("goes_west")), ["Hurricane Rachel"])
        read = {url for url, _ in noaa.requests}
        self.assertIn(BASE_URL + "floater_index.php", read)
        self.assertFalse(read & {BASE_URL + "meso.php", BASE_URL + "wfo_index.php"})
        # New storms bring their products, ended ones leave with theirs: the
        # catalogue stays complete and is still used without the network.
        self.assertTrue(client._cache.noaa_fresh())
        self.assertEqual(client.list_products("goes_west", "storm_EP182026")[0]["resolutions"],
                         ["1000x1000"])
        self.assertIsNone(client._cache.products("goes_west", "storm_EP142026"))
        count = len(noaa.requests)
        client.list_areas("goes_east")
        self.assertEqual(len(noaa.requests), count)

        # A restarted app reads the check time from disk.
        again = CatalogueClient(noaa=FakeClient({}), cache_path=self.path, retries=1)
        self.assertEqual(storm_labels(again.list_areas("goes_west")), ["Hurricane Rachel"])

    def test_failed_storm_check_keeps_the_list_and_waits_before_retrying(self):
        self.cached_catalogue()
        pages = catalogue_pages(NEW_STORMS)
        pages[BASE_URL + "floater_index.php"] = UnavailableError("Temporary storm outage")
        noaa = FakeClient(pages)
        client = CatalogueClient(noaa=noaa, cache_path=self.path, retries=1)
        client._cache._data["noaa_storms_checked_at"] -= NOAA_STORM_CHECK_SECONDS + 1
        self.assertEqual(storm_labels(client.list_areas("goes_west")), ["Norbert"])
        count = len(noaa.requests)
        self.assertTrue(count)
        client.list_areas("goes_west")
        self.assertEqual(len(noaa.requests), count)
        client._cache._data["noaa_storms_retry_after"] = time.time() - 1
        pages[BASE_URL + "floater_index.php"] = storm_links(*NEW_STORMS)
        self.assertEqual(storm_labels(client.list_areas("goes_west")), ["Hurricane Rachel"])

    def test_incomplete_full_refresh_stores_what_it_read(self):
        self.cached_catalogue()

        class Complete:
            catalogue_warning = ""

            def refresh_all_catalogues(self, refresh=True, progress=None):
                return {"providers": 1, "areas": 1, "products": 1, "resolution_options": 1,
                        "errors": [], "warning": "", "complete": True}

            def list_areas(self, provider, refresh=False):
                return [{"id": "area"}]

            def list_products(self, provider, area_id, refresh=False):
                return [{"id": "product", "resolutions": ["1x1"]}]

            def catalogue(self, refresh=True):
                return [{"satellite": "MTG"}]

        pages = catalogue_pages(NEW_STORMS)
        pages[BASE_URL + "wfo.php?wfo=box"] = UnavailableError("Temporary WFO outage")
        other = Complete()
        client = CatalogueClient(noaa=FakeClient(pages), himawari=other, slider=other,
                                 worldview=other, eumetsat=other, cache_path=self.path, retries=1)
        result = client.refresh_all_catalogues(refresh=True)
        self.assertFalse(result["complete"])
        # The Settings note names the source that was not read completely.
        self.assertEqual(result["incomplete_sources"], ["NOAA"])
        self.assertEqual(storm_labels(client._cache.areas("goes_east")), ["Tropical Storm Isaias"])
        self.assertEqual(storm_labels(client._cache.areas("goes_west")), ["Hurricane Rachel"])
        # The unreadable WFO stays with its cached products; the catalogue stays usable.
        self.assertIn("wfo_box", [area["id"] for area in client._cache.areas("goes_east")])
        self.assertEqual(client._cache.products("goes_east", "wfo_box")[0]["resolutions"], ["600x600"])
        self.assertTrue(client._cache.noaa_retry_pending())
        self.assertEqual(storm_labels(client.list_areas("goes_east")), ["Tropical Storm Isaias"])


if __name__ == "__main__":
    unittest.main()
