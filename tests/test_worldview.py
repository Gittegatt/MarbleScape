"""NASA Worldview/GIBS catalogue and still-rendering tests without live requests."""

import io
import unittest

from PIL import Image

import marblescape_worldview as worldview


CAPABILITIES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Title>Corrected Reflectance (True Color, VIIRS, NOAA-20)</ows:Title>
      <ows:Identifier>VIIRS_NOAA20_CorrectedReflectance_TrueColor</ows:Identifier>
      <Format>image/png</Format>
      <Dimension>
        <ows:Identifier>Time</ows:Identifier>
        <Default>2026-09-13</Default>
        <Value>2026-09-10/2026-09-13/P1D</Value>
      </Dimension>
      <TileMatrixSetLink><TileMatrixSet>250m</TileMatrixSet></TileMatrixSetLink>
    </Layer>
    <Layer>
      <ows:Title>Blue Marble</ows:Title>
      <ows:Identifier>BlueMarble_ShadedRelief</ows:Identifier>
      <Format>image/jpeg</Format>
      <TileMatrixSetLink><TileMatrixSet>500m</TileMatrixSet></TileMatrixSetLink>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>250m</ows:Identifier>
      <TileMatrix><TileWidth>512</TileWidth><TileHeight>512</TileHeight><MatrixWidth>2</MatrixWidth><MatrixHeight>1</MatrixHeight></TileMatrix>
      <TileMatrix><TileWidth>512</TileWidth><TileHeight>512</TileHeight><MatrixWidth>4</MatrixWidth><MatrixHeight>2</MatrixHeight></TileMatrix>
      <TileMatrix><TileWidth>512</TileWidth><TileHeight>512</TileHeight><MatrixWidth>8</MatrixWidth><MatrixHeight>4</MatrixHeight></TileMatrix>
    </TileMatrixSet>
    <TileMatrixSet>
      <ows:Identifier>500m</ows:Identifier>
      <TileMatrix><TileWidth>512</TileWidth><TileHeight>512</TileHeight><MatrixWidth>2</MatrixWidth><MatrixHeight>1</MatrixHeight></TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>'''

DOMAINS = b'''<Domains xmlns="http://gibs.earthdata.nasa.gov/wmts-geo/1.0"
  xmlns:ows="http://www.opengis.net/ows/1.1">
  <DimensionDomain><ows:Identifier>Time</ows:Identifier>
    <Domain>2026-09-07/2026-09-14/P1D</Domain>
  </DimensionDomain>
</Domains>'''


def png(size=(1024, 512), color=(30, 90, 140, 255)):
    output = io.BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(output, "PNG")
    return output.getvalue()


class FixtureClient(worldview.WorldviewClient):
    def __init__(self):
        super().__init__(timeout=1)
        self.calls = []
        self.image = png()

    def _request(self, url, limit, track=False):
        self.calls.append((url, limit, track))
        if url == worldview.CAPABILITIES_URL:
            return CAPABILITIES, {"Content-Type": "application/xml"}
        if "--" in url and url.endswith(".xml"):
            return DOMAINS, {"Content-Type": "application/xml"}
        if url.startswith(worldview.WMS_URL + "?"):
            return self.image, {"Content-Type": "image/png"}
        raise AssertionError("Unexpected URL: " + url)


class WorldviewTests(unittest.TestCase):
    def test_catalogue_exposes_layers_categories_dates_and_native_render_sizes(self):
        client = FixtureClient()
        areas = client.list_areas("worldview", refresh=True)
        by_id = {item["id"]: item for item in areas}
        layer = by_id[worldview.DEFAULT_LAYER]
        self.assertEqual(layer["category"], "Corrected Reflectance")
        self.assertEqual(layer["resolutions"], ["1024x512", "2048x1024", "4096x2048"])
        products = client.list_products("worldview", worldview.DEFAULT_LAYER)
        self.assertEqual(products[0]["id"], "latest")
        self.assertIn("2026-09-13", products[0]["label"])
        self.assertEqual(
            [item["id"] for item in products[1:]],
            ["2026-09-13", "2026-09-12", "2026-09-11", "2026-09-10"],
        )
        timeless = client.list_products("worldview", "BlueMarble_ShadedRelief")
        self.assertEqual(timeless, [{
            "id": "timeless", "label": "Timeless", "resolutions": ["1024x512"]
        }])

    def test_latest_uses_describe_domains_and_wms_130_axis_order(self):
        client = FixtureClient()
        client.list_areas("worldview", refresh=True)
        frame = client.latest("worldview", worldview.DEFAULT_LAYER, "latest", "1024x512")
        self.assertEqual(frame["timestamp"], "2026-09-14T00:00:00Z")
        self.assertFalse(frame["fixed_time"])
        self.assertIn("BBOX=-90%2C-180%2C90%2C180", frame["url"])
        self.assertIn("TIME=2026-09-14", frame["url"])
        self.assertIn("WIDTH=1024", frame["url"])
        self.assertTrue(any("--" in call[0] for call in client.calls))

    def test_fixed_date_skips_latest_lookup_and_image_is_framed_as_png(self):
        client = FixtureClient()
        client.list_areas("worldview", refresh=True)
        frame = client.latest(
            "worldview", worldview.DEFAULT_LAYER, "2026-09-12", "1024x512"
        )
        self.assertTrue(frame["fixed_time"])
        before = len(client.calls)
        rendered = client.fetch_image(frame, (100, 100), fit_mode="fit", background="#010203")
        self.assertEqual(len(client.calls), before + 1)
        self.assertTrue(client.calls[-1][2])
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (100, 100))
            self.assertEqual(image.getpixel((50, 50)), (30, 90, 140))
            self.assertEqual(image.getpixel((50, 5)), (1, 2, 3))

    def test_timeless_layer_omits_time_and_keeps_explicit_profile_identity(self):
        client = FixtureClient()
        client.list_areas("worldview", refresh=True)
        frame = client.latest(
            "worldview", "BlueMarble_ShadedRelief", "timeless", "largest"
        )
        self.assertEqual(frame["timestamp"], "timeless")
        self.assertEqual(frame["product"], "timeless")
        self.assertNotIn("TIME=", frame["url"])
        client.fetch_image(frame, (32, 18))

    def test_invalid_hosts_xml_and_frame_tampering_are_rejected(self):
        for url in (
            "http://gibs.earthdata.nasa.gov/image.png",
            "https://evil.example/image.png",
            "https://gibs.earthdata.nasa.gov.evil.example/image.png",
            "https://user:secret@gibs.earthdata.nasa.gov/image.png",
            "https://gibs.earthdata.nasa.gov:8443/image.png",
        ):
            with self.subTest(url=url), self.assertRaises(worldview.WorldviewError):
                worldview._checked_url(url)
        with self.assertRaises(worldview.WorldviewError):
            worldview._parse_xml(b"<!DOCTYPE x><x/>", "fixture")
        client = FixtureClient()
        client.list_areas("worldview", refresh=True)
        frame = client.latest(
            "worldview", worldview.DEFAULT_LAYER, "2026-09-12", "1024x512"
        )
        frame["url"] = worldview.WMS_URL + "?tampered=true"
        with self.assertRaises(worldview.WorldviewError):
            client.fetch_image(frame, (20, 20))

    def test_refresh_summary_counts_supported_layers(self):
        summary = FixtureClient().refresh_all_catalogues()
        self.assertEqual(summary["providers"], 1)
        self.assertEqual(summary["areas"], 2)
        self.assertEqual(summary["products"], 2)
        self.assertEqual(summary["resolution_options"], 4)
        self.assertTrue(summary["complete"])


if __name__ == "__main__":
    unittest.main()
