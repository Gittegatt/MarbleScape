"""Himawari catalogue and still-rendering tests without live requests."""

import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import marblescape_himawari as himawari
from marblescape_catalogues import CatalogueClient


def png(size, color, mode="RGB"):
    output = io.BytesIO()
    with Image.new(mode, size, color) as image:
        image.save(output, "PNG")
    return output.getvalue()


def jpeg(size, color=(30, 90, 140)):
    output = io.BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(output, "JPEG")
    return output.getvalue()


class FixtureClient(himawari.HimawariClient):
    def __init__(self):
        super().__init__(timeout=1)
        self.responses = {}
        self.requests = []

    def _request(self, url, limit, track=False):
        del track
        del limit
        self.requests.append(url)
        if url not in self.responses:
            raise AssertionError("Unexpected URL: " + url)
        return self.responses[url], {}


class HimawariTests(unittest.TestCase):
    def test_catalogue_contains_nict_bands_and_all_published_jma_regions(self):
        client = FixtureClient()
        areas = client.list_areas("himawari")
        identifiers = {area["id"] for area in areas}
        self.assertEqual(len(identifiers), len(areas))
        self.assertTrue({"nict_full_disk", "nict_japan", "nict_full_disk_bands",
                         "jma_fd_", "jma_jpn", "jma_r2w", "jma_tga",
                         "jma_ho1", "jma_hoa", "jma_target_position"}.issubset(identifiers))
        bands = client.list_products("himawari", "nict_full_disk_bands")
        self.assertEqual([value["id"] for value in bands], [f"B{i:02d}" for i in range(1, 17)])
        full_disk = client.list_products("himawari", "nict_full_disk")
        self.assertEqual(full_disk[0]["resolutions"][-1], "11000x11000")
        japan = client.list_products("himawari", "nict_japan")
        self.assertEqual(japan[0]["resolutions"][-1], "3000x2400")

    def test_jma_outage_keeps_static_areas_and_skips_repeated_product_requests(self):
        class OfflineJMA(FixtureClient):
            def __init__(self):
                super().__init__()
                self.jma_requests = 0

            def _refresh_nict_base(self):
                return self._nict_base

            def _html(self, url):
                if url.startswith(himawari.JMA_BASE):
                    self.jma_requests += 1
                    raise himawari.UnavailableError("Himawari is currently unreachable: offline fixture")
                return super()._html(url)

        client = OfflineJMA()
        self.assertTrue(client.list_areas("himawari", refresh=True))
        self.assertIn("last known Himawari areas", client.catalogue_warning)
        result = client.refresh_all_catalogues(refresh=True)
        self.assertFalse(result["complete"])
        self.assertEqual(client.jma_requests, 2)
        self.assertFalse(client.catalogue_refresh_status["running"])

    def test_nict_latest_uses_timestamped_png_tiles_and_selected_size(self):
        client = FixtureClient()
        url = himawari.NICT_IMAGE_BASE + "img/D531106/latest.json"
        client.responses[url] = json.dumps({
            "date": "2026-09-13 16:00:00",
            "file": "PI_H09_20260913_1600_TRC_FLDK_R10_PGPFD.png",
        }).encode()
        frame = client.latest("himawari", "nict_full_disk", "true_color", "1100x1100")
        self.assertEqual(frame["timestamp"], "2026-09-13T16:00:00Z")
        self.assertEqual(frame["count"], 2)
        self.assertEqual(frame["date_code"], "20260913160000")
        self.assertTrue(frame["url"].endswith(
            "/D531106/2d/550/2026/09/13/160000_0_0.png"
        ))

    def test_jma_latest_uses_first_official_time_option(self):
        client = FixtureClient()
        page = himawari.JMA_BASE + "sat_img.php?area=fd_"
        client.responses[page] = b'''<select name="slt_time">
          <option value="1600">16:10 UTC 13 September 2026</option>
          <option value="1550">16:00 UTC 13 September 2026</option>
        </select>'''
        frame = client.latest("himawari", "jma_fd_", "trm", "largest")
        self.assertEqual(frame["timestamp"], "2026-09-13T16:10:00Z")
        self.assertEqual(frame["resolution"], "601x601")
        self.assertEqual(frame["url"], himawari.JMA_BASE + "img/fd_/fd__trm_1600.jpg")

        high_page = himawari.JMA_BASE + "sat_hox.php?area=ho1"
        client.responses[high_page] = client.responses[page]
        high = client.latest("himawari", "jma_ho1", "hrp", "largest")
        self.assertEqual(high["resolution"], "601x1001")
        self.assertEqual(high["url"], himawari.JMA_BASE + "img/hox/ho1_hrp_1600.jpg")

    def test_nict_tiles_render_directly_into_output_without_full_mosaic(self):
        client = FixtureClient()
        latest_url = himawari.NICT_IMAGE_BASE + "img/D531106/latest.json"
        client.responses[latest_url] = b'{"date":"2026-09-13 16:00:00","file":"still.png"}'
        frame = client.latest("himawari", "nict_full_disk", "true_color", "1100x1100")
        colors = {(0, 0): (255, 0, 0), (1, 0): (0, 255, 0),
                  (0, 1): (0, 0, 255), (1, 1): (255, 255, 0)}
        area = client._area("nict_full_disk")
        for (x, y), color in colors.items():
            url = client._nict_tile_url(area, "true_color", 2, frame["date_code"], x, y)
            client.responses[url] = png((550, 550), color)
        rendered = client.fetch_image(frame, (100, 100), fit_mode="fit")
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (100, 100))
            self.assertEqual(image.getpixel((25, 25)), colors[(0, 0)])
            self.assertEqual(image.getpixel((75, 25)), colors[(1, 0)])
            self.assertEqual(image.getpixel((25, 75)), colors[(0, 1)])
            self.assertEqual(image.getpixel((75, 75)), colors[(1, 1)])

    def test_band_layer_is_composited_over_nict_blue_marble(self):
        client = FixtureClient()
        latest_url = himawari.NICT_IMAGE_BASE + "img/FULL_24h/latest.json"
        client.responses[latest_url] = b'{"date":"2026-09-13 16:00:00","file":"still.png"}'
        frame = client.latest("himawari", "nict_full_disk_bands", "B13", "550x550")
        area = client._area("nict_full_disk_bands")
        foreground = client._nict_tile_url(area, "B13", 1, frame["date_code"], 0, 0)
        background = himawari.NICT_IMAGE_BASE + "img/FULL_24h/BlueMarble/1d/550/BlueMarble_0_0.png"
        client.responses[foreground] = png((550, 550), (255, 0, 0, 128), "RGBA")
        client.responses[background] = png((550, 550), (0, 0, 255, 255), "RGBA")
        rendered = client.fetch_image(frame, (20, 20))
        with Image.open(io.BytesIO(rendered)) as image:
            red, green, blue = image.getpixel((10, 10))
            self.assertGreaterEqual(red, 127)
            self.assertEqual(green, 0)
            self.assertGreaterEqual(blue, 126)

    def test_jma_download_validates_native_dimensions_and_excludes_other_formats(self):
        client = FixtureClient()
        page = himawari.JMA_BASE + "sat_img.php?area=fd_"
        client.responses[page] = b'<select name="slt_time"><option value="1600">16:10 UTC 13 September 2026</option></select>'
        frame = client.latest("himawari", "jma_fd_", "trm", "largest")
        client.responses[frame["url"]] = jpeg((601, 601))
        rendered = client.fetch_image(frame, (32, 18), fit_mode="crop")
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.size, (32, 18))
        client.responses[frame["url"]] = jpeg((600, 600))
        with self.assertRaises(himawari.HimawariError):
            client.fetch_image(frame, (32, 18))
        client.responses[frame["url"]] = png((601, 601), (1, 2, 3))
        with self.assertRaises(himawari.HimawariError):
            client.fetch_image(frame, (32, 18))

    def test_frame_and_host_validation_reject_tampering(self):
        for url in ("http://himawari8.nict.go.jp/image.png",
                    "https://evil.example/image.png",
                    "https://himawari8.nict.go.jp.evil.example/image.png",
                    "https://user:secret@himawari8.nict.go.jp/image.png",
                    "https://himawari8.nict.go.jp:8443/image.png"):
            with self.subTest(url=url), self.assertRaises(himawari.HimawariError):
                himawari._checked_url(url)
        client = FixtureClient()
        latest_url = himawari.NICT_IMAGE_BASE + "img/D531106/latest.json"
        client.responses[latest_url] = b'{"date":"2026-09-13 16:00:00","file":"still.png"}'
        frame = client.latest("himawari", "nict_full_disk", "true_color", "550x550")
        frame["url"] = "https://himawari8.nict.go.jp/not-the-selected-tile.png"
        with self.assertRaises(himawari.HimawariError):
            client.fetch_image(frame, (20, 20))

    def test_combined_catalogue_routes_himawari_and_sums_refresh_results(self):
        class CatalogueFixture:
            def __init__(self, summary):
                self.summary = summary
                self.calls = []
                self.catalogue_warning = ""

            def list_areas(self, provider, refresh=False):
                self.calls.append(("areas", provider, refresh))
                return [{"id": provider}]

            def list_products(self, provider, area, refresh=False):
                self.calls.append(("products", provider, area, refresh))
                return [{"id": area}]

            def refresh_all_catalogues(self, refresh=True, progress=None):
                self.calls.append(("refresh", refresh))
                if progress:
                    progress(1, 2, "First metadata page loaded.")
                    progress(2, 2, "All metadata pages loaded.")
                return dict(self.summary)

        noaa = CatalogueFixture({"providers": 3, "areas": 5, "products": 7,
                                 "resolution_options": 11, "errors": [],
                                 "warning": "", "complete": True})
        nict_jma = CatalogueFixture({"providers": 1, "areas": 47, "products": 607,
                                     "resolution_options": 679, "errors": [],
                                     "warning": "", "complete": True})
        slider = CatalogueFixture({"providers": 1, "areas": 20, "products": 574,
                                   "resolution_options": 2100, "errors": [],
                                   "warning": "", "complete": True})
        worldview = CatalogueFixture({"providers": 1, "areas": 18, "products": 1312,
                                       "resolution_options": 5248, "errors": [],
                                       "warning": "", "complete": True})
        eumetsat = type("EumetsatFixture", (), {
            "catalogue": lambda self, refresh=True: [
                {"satellite": "MTG - 0 Degree"},
                {"satellite": "Sentinel-3A"},
            ],
        })()
        client = CatalogueClient(noaa=noaa, himawari=nict_jma, slider=slider,
                                 worldview=worldview, eumetsat=eumetsat)
        noaa.catalogue_warning = "NOAA maintenance"
        nict_jma.catalogue_warning = "Himawari partial catalogue"
        self.assertEqual(client.catalogue_warning_for("goes_east"), "NOAA maintenance")
        self.assertEqual(client.catalogue_warning_for("solar"), "NOAA maintenance")
        self.assertEqual(
            client.catalogue_warning_for("himawari"),
            "Himawari partial catalogue",
        )
        self.assertIn("NOAA maintenance", client.catalogue_warning)
        self.assertIn("Himawari partial catalogue", client.catalogue_warning)
        self.assertEqual(client.list_areas("himawari"), [{"id": "himawari"}])
        self.assertEqual(client.list_areas("slider"), [{"id": "slider"}])
        self.assertEqual(client.list_areas("worldview"), [{"id": "worldview"}])
        self.assertEqual(client.list_products("goes_east", "full_disk"),
                         [{"id": "full_disk"}])
        updates = []
        result = client.refresh_all_catalogues(refresh=True, progress=lambda *args: updates.append(args))
        self.assertEqual(result["providers"], 7)
        self.assertEqual(result["areas"], 92)
        self.assertEqual(result["products"], 2502)
        self.assertEqual(result["resolution_options"], 8038)
        self.assertTrue(result["complete"])
        self.assertEqual(client.catalogue_refresh_status["running"], False)
        self.assertTrue(any(0 < done < 1 and "NOAA" in message
                            for done, total, message in updates))
        self.assertTrue(any(1 < done < 2 and "Himawari" in message
                            for done, total, message in updates))
        self.assertEqual(updates[-1][:2], (5, 5))

        noaa.summary = {
            "providers": 0, "areas": 0, "products": 0,
            "resolution_options": 0,
            "errors": ["NOAA front end timed out"],
            "warning": (
                "NOAA catalogue refresh is incomplete. "
                "NOAA front end timed out"
            ),
            "complete": False,
        }
        partial = client.refresh_all_catalogues(refresh=True)
        self.assertFalse(partial["complete"])
        self.assertEqual(partial["warning"].count("NOAA front end timed out"), 1)

    def test_combined_catalogue_persists_and_reuses_last_successful_metadata(self):
        class PublicFixture:
            def __init__(self, fail=False):
                self.fail = fail
                self.calls = []
                self.catalogue_warning = ""

            def list_areas(self, provider, refresh=False):
                self.calls.append(("areas", provider, refresh))
                if self.fail:
                    raise OSError("provider offline")
                return [{"id": "full_disk", "label": "Full Disk", "category": "Global"}]

            def list_products(self, provider, area_id, refresh=False):
                self.calls.append(("products", provider, area_id, refresh))
                if self.fail:
                    raise OSError("provider offline")
                return [{"id": "true_color", "label": "True Color",
                         "resolutions": ["550x550"]}]

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "catalogues.json"
            online = PublicFixture()
            first = CatalogueClient(
                noaa=online, himawari=online, slider=online, worldview=online,
                eumetsat=object(), cache_path=cache_path, retries=2,
            )
            expected_areas = first.list_areas("himawari", refresh=True)
            expected_products = first.list_products("himawari", "full_disk", refresh=True)
            self.assertTrue(cache_path.is_file())

            offline = PublicFixture(fail=True)
            second = CatalogueClient(
                noaa=offline, himawari=offline, slider=offline, worldview=offline,
                eumetsat=object(), cache_path=cache_path, retries=2,
            )
            self.assertEqual(second.list_areas("himawari", refresh=True), expected_areas)
            self.assertEqual(
                second.list_products("himawari", "full_disk", refresh=True),
                expected_products,
            )
            self.assertEqual(
                sum(call[0] == "areas" for call in offline.calls), 3,
                "Two retries plus the first catalogue attempt are required",
            )
            self.assertIn("using cached catalogue data", second.catalogue_warning_for("himawari"))


if __name__ == "__main__":
    unittest.main()
