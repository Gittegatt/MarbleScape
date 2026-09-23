"""Offline contract and safety tests for the NOAA still-image provider."""

import io
import threading
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PIL import Image

from marblescape_noaa import (
    BASE_URL, NOAAClient, NOAAError, UnavailableError, _Redirects,
    _area_from_link, _checked_url, _image_identity, _parse_products, _render_still,
    _timestamp, _unpack_still,
)

CDN = "https://cdn.star.nesdis.noaa.gov/"
EAST = CDN + "GOES19/ABI/FD/GEOCOLOR/"
INDEX = """<h2 title='GOES-19'>GOES-East</h2>
<a href='fulldisk.php?sat=G19'>Full Disk</a>
<a href='conus.php?sat=G19'>CONUS</a>
<a href='sector.php?sat=G19&amp;sector=sp'>Southern Plains</a>
<a id='G19M1' href='meso.php?sat=G19&amp;lat=38N&amp;lon=75W'>Meso M1</a>
<h2 title='GOES-18'>GOES-West</h2>
<a href='fulldisk.php?sat=G18'>Full Disk</a>
<a href='conus.php?sat=G18'>PACUS</a>"""


def image_url(directory=EAST, stamp="20262541430", satellite="19", size="1808x1808", product="GEOCOLOR", suffix="jpg"):
    return directory + f"{stamp}_GOES{satellite}-ABI-FD-{product}-{size}.{suffix}"


def product_page(*urls):
    return "<h2>GeoColor</h2>" + "".join(f"<a href='{url}'>image</a>" for url in urls)


def encoded_image(size=(80, 40), format="JPEG", color="red"):
    with Image.new("RGB", size, color) as image:
        stream = io.BytesIO()
        image.save(stream, format)
        return stream.getvalue()


def all_catalogue_pages():
    west = CDN + "GOES18/ABI/FD/GEOCOLOR/"
    solar = CDN + "GOES19/SUVI/FD/Fe171/"
    return {
        BASE_URL + "index.php": "<h2 title='GOES-19'>GOES-East</h2><a href='fulldisk.php?sat=G19'>Full Disk</a>"
                                "<h2 title='GOES-18'>GOES-West</h2><a href='fulldisk.php?sat=G18'>Full Disk</a>",
        BASE_URL + "meso.php": "", BASE_URL + "wfo_index.php": "", BASE_URL + "floater_index.php": "",
        BASE_URL + "fulldisk.php?sat=G19": product_page(image_url(size="339x339"), image_url()),
        BASE_URL + "fulldisk.php?sat=G18": product_page(image_url(west, satellite="18")),
        BASE_URL + "SUVI.php?sat=G19": product_page(
            solar + "20262541430001_GOES19-SUVI-Fe171-300x300.jpg",
            solar + "20262541430001_GOES19-SUVI-Fe171-1200x1200.jpg"),
    }


class FakeClient(NOAAClient):
    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.requests = []

    def _request(self, url, limit, headers=None, track=False):
        del track
        _checked_url(url)
        self.requests.append((url, headers or {}))
        if url not in self.pages:
            raise AssertionError("Unexpected HTTP request: " + url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            value = value.encode()
        return value, {"ETag": "test-version"}


class TimestampTests(unittest.TestCase):
    def test_abi_meso_and_suvi_capture_times(self):
        samples = (("20262541330", "2026-09-11T13:30:00Z"),
                   ("2026254135125", "2026-09-11T13:51:25Z"),
                   ("20262541347076", "2026-09-11T13:47:07.600000Z"))
        for stamp, expected in samples:
            with self.subTest(stamp=stamp):
                self.assertEqual(_timestamp(image_url(stamp=stamp)), (expected, "G19"))

    def test_bad_dates_aliases_and_animation_names_rejected(self):
        for stamp in ("20260001430", "20263661430", "20262542530", "20262541499", "2026254"):
            self.assertIsNone(_timestamp(image_url(stamp=stamp)))
        self.assertIsNone(_timestamp(EAST + "1808x1808.jpg"))
        self.assertIsNone(_timestamp(EAST + "20262541030-20262541430-GOES19-ABI-FD-GEOCOLOR-1808x1808.gif"))
        self.assertIsNotNone(_timestamp(image_url(stamp="20243661430")))


class CatalogueTests(unittest.TestCase):
    def test_products_filter_foreign_regions_animations_and_sizes(self):
        area = {"id": "full_disk", "url": BASE_URL + "fulldisk.php?sat=G19"}
        urls = (image_url(size="339x339"), image_url(size="1808x1808"),
                image_url(size="21696x21696", suffix="jpg.zip"),
                image_url(suffix="gif"), image_url(suffix="mp4"),
                image_url(directory=CDN + "GOES19/ABI/SECTOR/sp/GEOCOLOR/"),
                image_url(directory=CDN + "GOES18/ABI/FD/GEOCOLOR/", satellite="18"))
        result = _parse_products(product_page(*urls), area, "G19")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "GeoColor")
        self.assertEqual(result[0]["resolutions"], ["339x339", "1808x1808", "21696x21696"])

    def test_wfo_hidden_stills_and_channel_labels(self):
        directory = CDN + "WFO/box/GEOCOLOR/"
        url = image_url(directory=directory, size="600x600")
        html = f"""<a id='BOX_GEOCOLOR' onclick='channelSwitcher("BOX_GEOCOLOR")'>GeoColor</a>
        <input type='hidden' id='WFOStaticGEOCOLOR' value='{url}'>
        <input type='hidden' value='{url.replace('.jpg', '.gif')}'>"""
        products = _parse_products(html, {"id": "wfo_box", "url": BASE_URL + "wfo.php?wfo=box"}, "G19")
        self.assertEqual(products[0]["label"], "GeoColor")
        self.assertEqual(products[0]["resolutions"], ["600x600"])

    def test_moving_meso_slot_has_stable_id(self):
        first = _area_from_link("meso.php?sat=G19&lat=38N&lon=75W", "Meso M1", {"id": "G19M1"}, "G19")
        second = _area_from_link("meso.php?sat=G19&lat=42N&lon=94W", "Meso M1", {"id": "G19M1"}, "G19")
        self.assertEqual(first["id"], "meso_m1")
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["url"], second["url"])

    def test_all_area_sources_are_discovered_and_wfo_storm_ownership_is_verified(self):
        box_url = image_url(CDN + "WFO/box/GEOCOLOR/", size="600x600")
        sew_url = image_url(CDN + "WFO/sew/GEOCOLOR/", satellite="18", size="600x600")
        storm_url = image_url(CDN + "FLOATER/EP142026/GEOCOLOR/", satellite="18", size="1000x1000")
        pages = {
            BASE_URL + "index.php": INDEX,
            BASE_URL + "meso.php": "<a href='meso.php?sat=G18&lat=16N&lon=123W'>North Pacific</a>",
            BASE_URL + "wfo_index.php": "<a href='wfo.php?wfo=box'>Boston</a><a href='wfo.php?wfo=sew'>Seattle</a>",
            BASE_URL + "floater_index.php": "<a href='floater.php?stormid=EP142026'>Norbert</a>",
            BASE_URL + "wfo.php?wfo=box": product_page(box_url),
            BASE_URL + "wfo.php?wfo=sew": product_page(sew_url),
            BASE_URL + "floater.php?stormid=EP142026": product_page(storm_url),
        }
        client = FakeClient(pages)
        east = {item["id"] for item in client.list_areas("goes_east")}
        west = {item["id"] for item in client.list_areas("goes_west")}
        self.assertTrue({"full_disk", "conus", "sector_sp", "meso_m1", "wfo_box"} <= east)
        self.assertTrue({"full_disk", "conus", "meso_16N-123W", "wfo_sew", "storm_EP142026"} <= west)
        self.assertNotIn("wfo_sew", east)
        self.assertNotIn("storm_EP142026", east)
        self.assertNotIn("wfo_box", west)
        self.assertEqual(client.list_areas("solar")[0]["id"], "sun")
        self.assertEqual(sum(url.endswith("wfo.php?wfo=box") for url, _ in client.requests), 1)

    def test_missing_satellite_mapping_is_explicit_failure(self):
        client = FakeClient({BASE_URL + "index.php": "<h1>Service unavailable</h1>"})
        with self.assertRaises(NOAAError):
            client.list_products("goes_east", "full_disk")

    def test_caller_cannot_mutate_cached_product_options(self):
        client = FakeClient({BASE_URL + "index.php": INDEX,
                             BASE_URL + "fulldisk.php?sat=G19": product_page(image_url())})
        client.list_products("goes_east", "full_disk")[0]["resolutions"].clear()
        self.assertEqual(client.list_products("goes_east", "full_disk")[0]["resolutions"], ["1808x1808"])

    def test_meso_refresh_invalidates_products_of_old_slot_location(self):
        first_page = BASE_URL + "meso.php?sat=G19&lat=38N&lon=75W"
        second_page = BASE_URL + "meso.php?sat=G19&lat=42N&lon=94W"
        first_url = image_url(CDN + "GOES19/ABI/MESO/38N-75W/GEOCOLOR/", size="1000x1000")
        second_url = image_url(CDN + "GOES19/ABI/MESO/42N-94W/GEOCOLOR/", size="2000x2000")
        client = FakeClient({BASE_URL + "index.php": INDEX, first_page: product_page(first_url),
                             second_page: product_page(second_url)})
        self.assertEqual(client.list_products("goes_east", "meso_m1")[0]["resolutions"], ["1000x1000"])
        client.pages[BASE_URL + "index.php"] = INDEX.replace("lat=38N&amp;lon=75W", "lat=42N&amp;lon=94W")
        client._ensure_base(refresh=True)
        self.assertEqual(client.list_products("goes_east", "meso_m1")[0]["resolutions"], ["2000x2000"])

    def test_failed_optional_catalogue_keeps_core_regions_and_reports_partial_result(self):
        client = FakeClient({BASE_URL + "index.php": INDEX,
                             BASE_URL + "meso.php": UnavailableError("Temporary meso outage"),
                             BASE_URL + "wfo_index.php": "",
                             BASE_URL + "floater_index.php": ""})
        areas = client.list_areas("goes_east")
        self.assertIn("full_disk", [area["id"] for area in areas])
        self.assertIn("incomplete", client.catalogue_warning)
        self.assertIn("meso.php", client.catalogue_warning)

    def test_single_unavailable_wfo_does_not_hide_other_wfos_or_assign_a_satellite(self):
        box = image_url(CDN + "WFO/box/GEOCOLOR/", size="600x600")
        client = FakeClient({BASE_URL + "index.php": INDEX,
                             BASE_URL + "meso.php": "", BASE_URL + "floater_index.php": "",
                             BASE_URL + "wfo_index.php": "<a href='wfo.php?wfo=box'>Boston</a><a href='wfo.php?wfo=sew'>Seattle</a>",
                             BASE_URL + "wfo.php?wfo=box": product_page(box),
                             BASE_URL + "wfo.php?wfo=sew": UnavailableError("Temporary WFO outage")})
        east = [area["id"] for area in client.list_areas("goes_east")]
        west = [area["id"] for area in client.list_areas("goes_west")]
        self.assertIn("wfo_box", east)
        self.assertNotIn("wfo_sew", east + west)
        self.assertIn("Seattle", client.catalogue_warning)


class LatestTests(unittest.TestCase):
    def make_client(self, listing):
        return FakeClient({BASE_URL + "index.php": INDEX,
                           BASE_URL + "fulldisk.php?sat=G19": product_page(image_url(stamp="20262541000")),
                           EAST: listing})

    def test_latest_is_newest_matching_size_not_page_timestamp_or_directory_order(self):
        older = image_url(stamp="20262541410")
        newest = image_url(stamp="20262541430")
        other_size = image_url(stamp="20262541440", size="339x339")
        other_satellite = image_url(stamp="20262541450", satellite="18")
        foreign_directory = image_url(directory=CDN + "GOES19/ABI/SECTOR/sp/GEOCOLOR/", stamp="20262541450")
        listing = product_page(newest, other_size, older, EAST + "1808x1808.jpg", other_satellite, foreign_directory)
        client = self.make_client(listing)
        frame = client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        self.assertEqual(frame["url"], newest)
        self.assertEqual(frame["timestamp"], "2026-09-11T14:30:00Z")
        self.assertEqual(frame["source"], "goes_east")
        self.assertEqual(frame["area"], "full_disk")
        client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        self.assertEqual(sum(url == EAST for url, _ in client.requests), 2)

    def test_missing_product_size_and_directory_frame_are_explicit_failures(self):
        client = self.make_client(product_page(image_url(size="339x339")))
        for product, size in (("MISSING", "1808x1808"), ("GEOCOLOR", "999x999"), ("GEOCOLOR", "1808x1808")):
            with self.subTest(product=product, size=size), self.assertRaises(UnavailableError):
                client.latest("goes_east", "full_disk", product, size)

    def test_fractional_timestamp_wins_over_exact_same_second(self):
        exact = image_url(stamp="2026254143000")
        fractional = image_url(stamp="20262541430001")
        client = self.make_client(product_page(exact, fractional))
        frame = client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        self.assertEqual(frame["url"], fractional)

    def test_http_validators_reuse_parsed_unchanged_directory(self):
        client = self.make_client(product_page(image_url()))
        first = client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        old = client._directories[EAST]
        client._directories[EAST] = (old[0] - 60, old[1], old[2])
        client.pages[EAST] = None  # HTTP 304 contract of _request.
        second = client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        self.assertEqual(first, second)
        self.assertEqual(client.requests[-1][1]["If-None-Match"], "test-version")

    def test_immediate_next_update_revalidates_and_finds_a_new_image(self):
        first_url = image_url(stamp="20262541430")
        newest_url = image_url(stamp="20262541440")
        client = self.make_client(product_page(first_url))
        first = client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        client.pages[EAST] = product_page(first_url, newest_url)
        second = client.latest("goes_east", "full_disk", "GEOCOLOR", "1808x1808")
        self.assertEqual(first["url"], first_url)
        self.assertEqual(second["url"], newest_url)
        self.assertEqual(client.requests[-1][1]["If-None-Match"], "test-version")

    def test_largest_uses_pixel_count_and_keeps_explicit_sizes_available(self):
        wide = image_url(size="2000x1000")
        square = image_url(size="1800x1800")
        client = self.make_client(product_page(wide, square))
        client.pages[BASE_URL + "fulldisk.php?sat=G19"] = product_page(wide, square)
        largest = client.latest("goes_east", "full_disk", "GEOCOLOR", "largest")
        self.assertEqual(largest["resolution"], "1800x1800")
        self.assertEqual(largest["url"], square)
        explicit = client.latest("goes_east", "full_disk", "GEOCOLOR", "2000x1000")
        self.assertEqual(explicit["resolution"], "2000x1000")

    def test_largest_is_resolved_again_after_product_catalogue_refresh(self):
        client = self.make_client(product_page(image_url()))
        self.assertEqual(client.latest("goes_east", "full_disk", "GEOCOLOR", "largest")["resolution"], "1808x1808")
        bigger = image_url(size="5424x5424")
        client.pages[BASE_URL + "fulldisk.php?sat=G19"] = product_page(image_url(), bigger)
        client.pages[EAST] = product_page(image_url(), bigger)
        client.list_products("goes_east", "full_disk", refresh=True)
        self.assertEqual(client.latest("goes_east", "full_disk", "GEOCOLOR", "largest")["resolution"], "5424x5424")


class SharedCatalogueTests(unittest.TestCase):
    def test_full_refresh_loads_only_metadata_and_warms_all_product_caches(self):
        client = FakeClient(all_catalogue_pages())
        updates = []
        summary = client.refresh_all_catalogues(progress=lambda done, total, message: updates.append((done, total, message)))
        self.assertEqual(summary, {"providers": 3, "areas": 3, "products": 3,
                                   "resolution_options": 5, "errors": [], "warning": "", "complete": True})
        self.assertEqual(updates[-1][:2], (3, 3))
        self.assertFalse(client.catalogue_refresh_status["running"])
        client.catalogue_refresh_status["message"] = "cannot mutate internal status"
        self.assertNotEqual(client.catalogue_refresh_status["message"], "cannot mutate internal status")
        count = len(client.requests)
        for provider, area in (("goes_east", "full_disk"), ("goes_west", "full_disk"), ("solar", "sun")):
            self.assertTrue(client.list_products(provider, area))
        self.assertEqual(client.refresh_all_catalogues(refresh=False), summary)
        self.assertEqual(len(client.requests), count)
        self.assertTrue(all(url.startswith(BASE_URL) and ".php" in url for url, _ in client.requests))

    def test_forced_refresh_reloads_all_products_without_repeating_discovery_per_provider(self):
        client = FakeClient(all_catalogue_pages())
        client.refresh_all_catalogues(refresh=False)
        client.requests.clear()
        client.refresh_all_catalogues(refresh=True)
        for page in ("index.php", "fulldisk.php?sat=G19", "fulldisk.php?sat=G18", "SUVI.php?sat=G19"):
            self.assertEqual(sum(url == BASE_URL + page for url, _ in client.requests), 1, page)

    def test_product_refresh_does_not_repeat_area_discovery(self):
        client = FakeClient(all_catalogue_pages())
        client.list_areas("goes_east", refresh=True)
        client.requests.clear()
        client.list_products("goes_east", "full_disk", refresh=True)
        self.assertEqual([url for url, _ in client.requests],
                         [BASE_URL + "fulldisk.php?sat=G19"])

    def test_partial_product_failure_is_reported_and_other_sources_remain_usable(self):
        pages = all_catalogue_pages()
        pages[BASE_URL + "SUVI.php?sat=G19"] = UnavailableError("Solar maintenance")
        client = FakeClient(pages)
        result = client.refresh_all_catalogues()
        self.assertFalse(result["complete"])
        self.assertEqual(result["products"], 2)
        self.assertIn("Solar maintenance", result["warning"])
        self.assertIn("Solar maintenance", client.catalogue_refresh_status["error"])
        self.assertTrue(client.list_products("goes_east", "full_disk"))

    def test_callbacks_cannot_abort_the_shared_refresh(self):
        client = FakeClient(all_catalogue_pages())
        def disposed_ui(*args):
            raise RuntimeError("UI was closed")
        self.assertTrue(client.refresh_all_catalogues(progress=disposed_ui)["complete"])

    def test_repeated_network_failures_skip_remaining_product_pages(self):
        class OfflineClient(FakeClient):
            def __init__(self):
                super().__init__({})
                self.attempts = 0

            def list_areas(self, provider, refresh=False):
                return [{"id": str(index), "label": str(index), "url": BASE_URL + str(index),
                         "satellite": "G19"} for index in range(30)]

            def _products_for_area(self, provider, area, after=None):
                self.attempts += 1
                raise UnavailableError("NOAA is currently unreachable: offline fixture")

        client = OfflineClient()
        summary = client.refresh_all_catalogues()
        self.assertFalse(summary["complete"])
        self.assertIn("skipped after repeated network failures", summary["warning"])
        self.assertLess(client.attempts, 90)
        self.assertFalse(client.catalogue_refresh_status["running"])

    def test_unavailable_base_index_is_requested_once_for_all_noaa_sources(self):
        client = FakeClient({
            BASE_URL + "index.php": UnavailableError(
                "NOAA is currently unreachable: read operation timed out"
            )
        })
        summary = client.refresh_all_catalogues()
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["providers"], 0)
        self.assertEqual(summary["areas"], 0)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(
            sum(url == BASE_URL + "index.php" for url, _headers in client.requests),
            1,
        )

    def test_simultaneous_refreshes_share_one_job_and_both_receive_progress(self):
        entered = threading.Event()
        release = threading.Event()
        joined = threading.Event()
        class BlockingClient(FakeClient):
            def _request(self, url, limit, headers=None):
                if url == BASE_URL + "index.php":
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("Test did not release the request")
                return super()._request(url, limit, headers)
        client = BlockingClient(all_catalogue_pages())
        progress = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.refresh_all_catalogues)
            self.assertTrue(entered.wait(3))
            self.assertTrue(client.catalogue_refresh_status["running"])
            def callback(done, total, message):
                progress.append((done, total))
                joined.set()
            second = pool.submit(client.refresh_all_catalogues, True, callback)
            self.assertTrue(joined.wait(3))
            release.set()
            self.assertEqual(first.result(5), second.result(5))
        self.assertEqual(sum(url == BASE_URL + "index.php" for url, _ in client.requests), 1)
        self.assertEqual(progress[-1], (3, 3))

    def test_concurrent_product_requests_share_network_work(self):
        entered = threading.Event()
        release = threading.Event()
        joined = threading.Event()
        class BlockingClient(FakeClient):
            def _request(self, url, limit, headers=None):
                if url == BASE_URL + "fulldisk.php?sat=G19":
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("Test did not release the request")
                return super()._request(url, limit, headers)
            def _coordinated(self, key, operation):
                with self._lock:
                    if key[0] == "products" and key in self._inflight:
                        joined.set()
                return super()._coordinated(key, operation)
        client = BlockingClient(all_catalogue_pages())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.list_products, "goes_east", "full_disk")
            self.assertTrue(entered.wait(3))
            second = pool.submit(client.list_products, "goes_east", "full_disk")
            self.assertTrue(joined.wait(3))
            release.set()
            self.assertEqual(first.result(5), second.result(5))
        self.assertEqual(sum(url == BASE_URL + "fulldisk.php?sat=G19" for url, _ in client.requests), 1)

    def test_product_cache_last_day_and_active_area_catalogue_still_expires(self):
        client = FakeClient(all_catalogue_pages())
        client.refresh_all_catalogues(refresh=False)
        count = len(client.requests)
        with patch("marblescape_noaa.time.monotonic", return_value=time.monotonic() + 3601):
            client.list_products("goes_east", "full_disk")
        new_urls = [url for url, _ in client.requests[count:]]
        self.assertIn(BASE_URL + "index.php", new_urls)
        self.assertNotIn(BASE_URL + "fulldisk.php?sat=G19", new_urls)


class DownloadTests(unittest.TestCase):
    def test_host_allowlist_including_redirects(self):
        for url in ("http://cdn.star.nesdis.noaa.gov/image.jpg", "https://evil.example/image.jpg",
                    "https://cdn.star.nesdis.noaa.gov.evil.example/image.jpg",
                    "https://user:secret@cdn.star.nesdis.noaa.gov/image.jpg",
                    "https://cdn.star.nesdis.noaa.gov:8443/image.jpg"):
            with self.subTest(url=url), self.assertRaises(NOAAError):
                _checked_url(url)
        with self.assertRaises(NOAAError):
            _Redirects().redirect_request(None, None, 302, "Found", {}, "https://evil.example/image.jpg")

    def test_fit_adds_background_and_crop_preserves_aspect(self):
        body = encoded_image()
        fit = _render_still(body, (80, 80), "80x40", "fit", 1, (0, 0, 255))
        crop = _render_still(body, (80, 80), "80x40", "crop", 1, (0, 0, 255))
        for data in (fit, crop):
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(fit)) as image:
            self.assertEqual(image.size, (80, 80))
            self.assertEqual(image.getpixel((40, 0)), (0, 0, 255))
            self.assertGreater(image.getpixel((40, 40))[0], 240)
        with Image.open(io.BytesIO(crop)) as image:
            self.assertGreater(image.getpixel((40, 0))[0], 240)

    def test_zoom_crops_before_resize_without_large_intermediate(self):
        data = _render_still(encoded_image(), (80, 80), "80x40", "crop", 20, (0, 0, 0))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (80, 80))

    def test_crop_keeps_square_landmark_square(self):
        stream = io.BytesIO()
        with Image.new("RGB", (80, 40), "black") as original:
            original.paste((255, 0, 0), (30, 10, 50, 30))
            original.save(stream, "PNG")
        data = _render_still(stream.getvalue(), (80, 80), "80x40", "crop", 1, (0, 0, 0))
        with Image.open(io.BytesIO(data)) as image:
            red = image.getchannel("R").point(lambda value: 255 if value > 127 else 0)
            with red:
                left, top, right, bottom = red.getbbox()
                self.assertEqual(right - left, 40)
                self.assertEqual(bottom - top, 40)

    def test_explicit_source_pixel_limit_is_enforced(self):
        with patch("marblescape_noaa.MAX_SOURCE_PIXELS", 100), self.assertRaises(NOAAError):
            _render_still(encoded_image(), (80, 80), "80x40", "fit", 1, (0, 0, 0))

    def test_fetch_identity_dimensions_and_animation_are_checked(self):
        url = image_url(size="80x40")
        frame = _image_identity(url)
        client = FakeClient({url: encoded_image()})
        self.assertTrue(client.fetch_image(frame, (160, 90)).startswith(b"\x89PNG"))
        with self.assertRaises(NOAAError):
            client.fetch_image(dict(frame, satellite="G18"), (160, 90))
        with self.assertRaises(NOAAError):
            client.fetch_image(frame, (0, 90))
        with self.assertRaises(NOAAError):
            client.fetch_image(frame, (160, 90), zoom=float("nan"))
        with self.assertRaises(NOAAError):
            _render_still(encoded_image(format="GIF"), (80, 80), "80x40", "fit", 1, (0, 0, 0))
        with self.assertRaises(NOAAError):
            _render_still(encoded_image(), (80, 80), "80x41", "fit", 1, (0, 0, 0))

    def test_animated_png_is_rejected(self):
        stream = io.BytesIO()
        with Image.new("RGB", (80, 40), "red") as first, Image.new("RGB", (80, 40), "blue") as second:
            first.save(stream, "PNG", save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaises(NOAAError):
            _render_still(stream.getvalue(), (80, 80), "80x40", "fit", 1, (0, 0, 0))

    def test_jpeg_decoder_does_not_change_global_pillow_limits(self):
        limit = Image.MAX_IMAGE_PIXELS
        with patch("PIL.Image.MAX_IMAGE_PIXELS", 10):
            result = _render_still(encoded_image(), (80, 80), "80x40", "fit", 1, (0, 0, 0))
            self.assertEqual(Image.MAX_IMAGE_PIXELS, 10)
        self.assertEqual(Image.MAX_IMAGE_PIXELS, limit)
        self.assertTrue(result.startswith(b"\x89PNG"))

    def test_zip_requires_exactly_one_expected_file_and_never_extracts_paths(self):
        filename = "20262541430_GOES19-ABI-FD-GEOCOLOR-80x40.jpg"
        body = encoded_image()
        def archive(names):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zipped:
                for name in names:
                    zipped.writestr(name, body)
            return out.getvalue()
        self.assertEqual(_unpack_still(archive([filename]), filename), body)
        for names in (["../" + filename], [filename, "extra.txt"], ["different.jpg"]):
            with self.subTest(names=names), self.assertRaises(NOAAError):
                _unpack_still(archive(names), filename)
        with self.assertRaises(NOAAError):
            _unpack_still(b"not a zip", filename)


if __name__ == "__main__":
    unittest.main()
