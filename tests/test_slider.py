import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_slider as slider


CATALOGUE = {
    "defaults": {"zoom_level_adjust": 0},
    "satellites": {
        "goes-19": {
            "satellite_title": "GOES-19 (East; 75.2W)",
            "default_sector": "full_disk",
            "sectors": {
                "full_disk": {
                    "sector_title": "Full Disk",
                    "tile_size": 64,
                    "max_zoom_level": 2,
                    "default_product": "geocolor",
                    "defaults": {"minutes_between_images": 10},
                    "missing_products": ["hidden"],
                    "products": {"geocolor": {"zoom_level_adjust": 0}},
                }
            },
            "products": {
                "heading": {"product_title": "----------VISIBLE BANDS----------"},
                "geocolor": {"product_title": "Geo&amp;Color", "zoom_level_adjust": 1},
                "band_01": {"product_title": "Band 1: 0.47 &micro;m", "zoom_level_adjust": 1},
                "hidden": {"product_title": "Hidden", "zoom_level_adjust": 0},
            },
        }
    },
}


def catalogue_script():
    return ("// fixture\nvar json =\n" + json.dumps(CATALOGUE) + ";\n").encode()


def png(color):
    output = io.BytesIO()
    with Image.new("RGBA", (64, 64), color) as image:
        image.save(output, "PNG")
    return output.getvalue()


class FixtureClient(slider.SliderClient):
    def __init__(self):
        super().__init__()
        self.responses = {slider.CATALOGUE_URL: catalogue_script()}
        self.calls = []

    def _request(self, url, limit, track=False):
        self.calls.append((url, limit, track))
        if url not in self.responses:
            raise slider.UnavailableError("missing fixture: " + url)
        return self.responses[url], {}


class SliderTests(unittest.TestCase):
    def setUp(self):
        # Measured content boxes are process-wide; keep tests independent.
        self.addCleanup(slider._CONTENT_BOXES.clear)
        slider._CONTENT_BOXES.clear()
        store = dict(slider._CONTENT_BOX_STORE)
        self.addCleanup(slider._CONTENT_BOX_STORE.update, store)
        slider._CONTENT_BOX_STORE["path"] = None
        self.addCleanup(slider._UNDECIDABLE_AREAS.clear)
        slider._UNDECIDABLE_AREAS.clear()

    def test_catalogue_lists_satellites_sectors_products_and_product_sizes(self):
        client = FixtureClient()
        areas = client.list_areas("slider")
        self.assertEqual(areas[0]["id"], "goes-19---full_disk")
        self.assertEqual(areas[0]["category"], "GOES-19 (East; 75.2W)")
        products = client.list_products("slider", areas[0]["id"])
        self.assertEqual([item["id"] for item in products], ["geocolor", "band_01"])
        self.assertEqual(products[0]["label"], "Geo&Color")
        self.assertEqual(products[1]["label"], "Band 1: 0.47 µm")
        self.assertEqual(products[0]["resolutions"], ["64x64", "128x128", "256x256"])
        self.assertEqual(products[1]["resolutions"], ["64x64", "128x128"])

    def test_latest_uses_newest_timestamp_and_current_slash_date_tile_layout(self):
        client = FixtureClient()
        client.list_areas("slider")
        metadata = slider.DATA_BASE + "json/goes-19/full_disk/geocolor/latest_times.json"
        client.responses[metadata] = b'{"timestamps_int":[20260913195000,20260913200000]}'
        frame = client.latest("slider", "goes-19---full_disk", "geocolor", "128x128")
        self.assertEqual(frame["timestamp"], "2026-09-13T20:00:00Z")
        self.assertEqual(frame["count"], 2)
        self.assertEqual(frame["level"], 1)
        self.assertEqual(
            frame["url"],
            slider.DATA_BASE + "imagery/2026/09/13/goes-19---full_disk/geocolor/20260913200000/01/000_000.png",
        )

    def test_tiles_render_directly_into_a_still_png_without_map_overlays(self):
        client = FixtureClient()
        client.list_areas("slider")
        metadata = slider.DATA_BASE + "json/goes-19/full_disk/geocolor/latest_times.json"
        client.responses[metadata] = b'{"timestamps_int":[20260913200000]}'
        frame = client.latest("slider", "goes-19---full_disk", "geocolor", "128x128")
        colors = {(0, 0): (255, 0, 0, 255), (1, 0): (0, 255, 0, 255),
                  (0, 1): (0, 0, 255, 255), (1, 1): (255, 255, 0, 255)}
        _document, area = client._area(frame["area"])
        for (x, y), color in colors.items():
            url = client._tile_url(area, frame["product"], frame["timestamp_code"],
                                   frame["level"], x, y)
            client.responses[url] = png(color)
        with patch.object(slider.DOWNLOAD_PROGRESS, "set_expected_requests") as expected:
            rendered = client.fetch_image(frame, (100, 100))
        expected.assert_called_once_with(4)
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (100, 100))
            self.assertEqual(image.getpixel((25, 25)), colors[(0, 0)][:3])
            self.assertEqual(image.getpixel((75, 25)), colors[(1, 0)][:3])
            self.assertEqual(image.getpixel((25, 75)), colors[(0, 1)][:3])
            self.assertEqual(image.getpixel((75, 75)), colors[(1, 1)][:3])
        tile_calls = [call for call in client.calls if "/imagery/" in call[0] and call[2]]
        self.assertEqual(len(tile_calls), 4)
        # One untracked level-0 tile measures SLIDER's padding of the sector.
        measured = [call for call in client.calls if "/imagery/" in call[0] and not call[2]]
        self.assertEqual(len(measured), 1)
        self.assertTrue(measured[0][0].endswith("/00/000_000.png"))

    def test_padding_measurement_accepts_only_centred_black_edges(self):
        def tile(content, size=(100, 100), night=None):
            image = Image.new("RGBA", size, (0, 0, 0, 255))
            image.paste((40, 80, 120, 255), content)
            if night:
                image.paste((0, 0, 0, 255), night)
            return image

        # A 5:3 sector centred in its square grid, as SLIDER pads GOES CONUS.
        self.assertEqual(slider.content_box_from_tile(tile((0, 20, 100, 80))),
                         (0.0, 0.2, 1.0, 0.8))
        self.assertEqual(slider.content_box_from_tile(tile((0, 0, 100, 100))),
                         slider.FULL_CONTENT_BOX)
        # A black night side at one edge is not padding: the edges differ.
        self.assertEqual(slider.content_box_from_tile(tile((0, 0, 100, 100), night=(70, 0, 100, 100))),
                         slider.FULL_CONTENT_BOX)
        # The thin space margin around a full disk stays part of the image.
        self.assertEqual(slider.content_box_from_tile(tile((1, 1, 99, 99))),
                         slider.FULL_CONTENT_BOX)
        self.assertIsNone(slider.content_box_from_tile(tile((0, 0, 0, 0))))

    def test_padded_sector_is_framed_and_sized_without_its_padding(self):
        client = FixtureClient()
        client.list_areas("slider")
        metadata = slider.DATA_BASE + "json/goes-19/full_disk/geocolor/latest_times.json"
        client.responses[metadata] = b'{"timestamps_int":[20260913200000]}'
        _document, area = client._area("goes-19---full_disk")
        padded = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
        padded.paste((40, 80, 120, 255), (0, 16, 64, 48))
        output = io.BytesIO()
        padded.save(output, "PNG")
        client.responses[client._tile_url(area, "geocolor", "20260913200000", 0, 0, 0)] = output.getvalue()
        frame = client.latest("slider", "goes-19---full_disk", "geocolor", "128x128")
        self.assertEqual(frame["content_box"], [0.0, 0.25, 1.0, 0.75])
        self.assertEqual(slider.effective_resolution("goes-19---full_disk", "128x128"), "128x64")
        products = client.list_products("slider", "goes-19---full_disk")
        self.assertEqual(products[0]["effective_resolutions"]["256x256"], "256x128")
        colors = {(0, 0): (255, 0, 0, 255), (1, 0): (0, 255, 0, 255),
                  (0, 1): (0, 0, 255, 255), (1, 1): (255, 255, 0, 255)}
        for (x, y), color in colors.items():
            client.responses[client._tile_url(area, "geocolor", frame["timestamp_code"], 1, x, y)] = png(color)
        # The 2:1 visible sector fills a 2:1 output with fit; padding is not drawn.
        rendered = client.fetch_image(frame, (100, 50), fit_mode="fit", background="#FF00FF")
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.getpixel((25, 10)), (255, 0, 0))
            self.assertEqual(image.getpixel((75, 10)), (0, 255, 0))
            self.assertEqual(image.getpixel((25, 40)), (0, 0, 255))
            self.assertEqual(image.getpixel((75, 40)), (255, 255, 0))
        # Wider than 2:1 keeps background bars beside the sector, never padding.
        rendered = client.fetch_image(frame, (200, 50), fit_mode="fit", background="#FF00FF")
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.getpixel((10, 25)), (255, 0, 255))
            self.assertEqual(image.getpixel((60, 10)), (255, 0, 0))

    def test_a_black_newest_frame_is_measured_on_the_oldest_listed_one(self):
        client = FixtureClient()
        client.list_areas("slider")
        metadata = slider.DATA_BASE + "json/goes-19/full_disk/geocolor/latest_times.json"
        client.responses[metadata] = b'{"timestamps_int":[20260913200000,20260913080000,20260913140000]}'
        _document, area = client._area("goes-19---full_disk")
        padded = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
        padded.paste((40, 80, 120, 255), (0, 16, 64, 48))
        output = io.BytesIO()
        padded.save(output, "PNG")
        newest = client._tile_url(area, "geocolor", "20260913200000", 0, 0, 0)
        oldest = client._tile_url(area, "geocolor", "20260913080000", 0, 0, 0)
        client.responses[newest] = png((0, 0, 0, 255))  # Night: no edge to measure.
        client.responses[oldest] = output.getvalue()
        self.assertEqual(client.content_box("goes-19---full_disk", "geocolor"), (0.0, 0.25, 1.0, 0.75))
        tiles = [url for url, _limit, _track in client.calls if url in (newest, oldest)]
        self.assertEqual(tiles, [newest, oldest])
        self.assertEqual(slider.cached_content_box("goes-19---full_disk"), (0.0, 0.25, 1.0, 0.75))

    def test_a_sector_no_frame_measures_is_not_measured_again_from_settings(self):
        client = FixtureClient()
        client.list_areas("slider")
        metadata = slider.DATA_BASE + "json/goes-19/full_disk/geocolor/latest_times.json"
        client.responses[metadata] = b'{"timestamps_int":[20260913200000,20260913080000]}'
        _document, area = client._area("goes-19---full_disk")
        for code in ("20260913200000", "20260913080000"):
            client.responses[client._tile_url(area, "geocolor", code, 0, 0, 0)] = png((0, 0, 0, 255))
        self.assertEqual(client.content_box("goes-19---full_disk", "geocolor"), slider.FULL_CONTENT_BOX)
        self.assertIsNone(slider.cached_content_box("goes-19---full_disk"))
        requests = len(client.calls)
        self.assertEqual(client.content_box("goes-19---full_disk", "geocolor"), slider.FULL_CONTENT_BOX)
        self.assertEqual(len(client.calls), requests)
        # A download still measures its own frame.
        client.content_box("goes-19---full_disk", "geocolor", "20260913200000")
        self.assertGreater(len(client.calls), requests)

    def test_measured_boxes_survive_a_restart_in_their_store(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "slider_content_boxes.json"
            slider.configure_content_box_store(path)
            slider._remember_content_box("goes-19---conus", (0.0, 0.2, 1.0, 0.8))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"goes-19---conus": [0.0, 0.2, 1.0, 0.8]})
            slider._CONTENT_BOXES.clear()
            slider._CONTENT_BOX_STORE["path"] = None
            path.write_text(json.dumps({"goes-19---conus": [0.0, 0.2, 1.0, 0.8],
                                        "broken": [0.5, 0.0, 0.2, 1.0]}), encoding="utf-8")
            slider.configure_content_box_store(path)
            self.assertEqual(slider.cached_content_box("goes-19---conus"), (0.0, 0.2, 1.0, 0.8))
            self.assertIsNone(slider.cached_content_box("broken"))
            self.assertEqual(slider.effective_resolution("goes-19---conus", "10000x10000"), "10000x6000")
            self.assertEqual(slider.effective_resolution("goes-19---full_disk", "678x678"), "678x678")

    def test_invalid_hosts_and_frame_tampering_are_rejected(self):
        for url in ("http://slider.cira.colostate.edu/data/image.png",
                    "https://evil.example/image.png",
                    "https://slider.cira.colostate.edu.evil.example/image.png",
                    "https://user:secret@slider.cira.colostate.edu/image.png",
                    "https://slider.cira.colostate.edu:8443/image.png"):
            with self.subTest(url=url), self.assertRaises(slider.SliderError):
                slider._checked_url(url)
        client = FixtureClient()
        client.list_areas("slider")
        metadata = slider.DATA_BASE + "json/goes-19/full_disk/geocolor/latest_times.json"
        client.responses[metadata] = b'{"timestamps_int":[20260913200000]}'
        frame = client.latest("slider", "goes-19---full_disk", "geocolor", "64x64")
        frame["url"] = slider.DATA_BASE + "not-the-selected-tile.png"
        with self.assertRaises(slider.SliderError):
            client.fetch_image(frame, (20, 20))

    def test_refresh_summary_counts_only_supported_still_products(self):
        client = FixtureClient()
        summary = client.refresh_all_catalogues()
        self.assertEqual(summary["providers"], 1)
        self.assertEqual(summary["areas"], 1)
        self.assertEqual(summary["products"], 2)
        self.assertEqual(summary["resolution_options"], 5)
        self.assertTrue(summary["complete"])


if __name__ == "__main__":
    unittest.main()
