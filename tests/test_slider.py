import io
import json
import unittest

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
        rendered = client.fetch_image(frame, (100, 100))
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (100, 100))
            self.assertEqual(image.getpixel((25, 25)), colors[(0, 0)][:3])
            self.assertEqual(image.getpixel((75, 25)), colors[(1, 0)][:3])
            self.assertEqual(image.getpixel((25, 75)), colors[(0, 1)][:3])
            self.assertEqual(image.getpixel((75, 75)), colors[(1, 1)][:3])
        tile_calls = [call for call in client.calls if "/imagery/" in call[0]]
        self.assertEqual(len(tile_calls), 4)
        self.assertTrue(all(call[2] for call in tile_calls))

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
