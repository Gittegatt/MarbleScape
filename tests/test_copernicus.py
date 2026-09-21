"""Offline contract tests for the Copernicus Catalog and Process provider."""

import datetime as dt
import io
import json
import time
import tomllib
import unittest
import urllib.error
from unittest import mock

from PIL import Image

import marblescape_copernicus as copernicus
import marblescape_download as app


class _Response:
    def __init__(self, data, content_type):
        self._stream = io.BytesIO(data)
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, maximum=-1):
        return self._stream.read(maximum)


def _png(size, color=(10, 20, 30, 255)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


class CatalogueTests(unittest.TestCase):
    def test_cdse_management_and_api_endpoints_are_kept_separate(self):
        self.assertEqual(
            copernicus.ACCOUNT_SETTINGS_URL,
            "https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings",
        )
        self.assertEqual(copernicus.PROCESS_URL, "https://sh.dataspace.copernicus.eu/process/v1")
        self.assertEqual(copernicus.CATALOG_URL, "https://sh.dataspace.copernicus.eu/catalog/v1/search")
        self.assertIn("identity.dataspace.copernicus.eu", copernicus.TOKEN_URL)

    def test_bundled_browser_catalogue_is_complete_and_process_compatible(self):
        catalogue = copernicus.load_catalogue()
        products = [product for theme in catalogue["themes"] for product in theme["products"]]
        layers = [layer for product in products for layer in product["layers"]]
        highlights = [item for theme in catalogue["themes"] for item in theme.get("highlights", [])]
        self.assertEqual(catalogue["source_revision"],
                         "1a1724c42b04e8a0953a410016676daea8ee5d33")
        self.assertEqual((len(catalogue["themes"]), len(products), len(layers), len(highlights)),
                         (13, 56, 359, 103))
        self.assertEqual(set(copernicus.missions()), {
            "Sentinel-1", "Sentinel-2", "Sentinel-3", "Sentinel-5P",
            "Copernicus DEM", "Landsat 8/9",
        })
        self.assertEqual({layer["data_type"] for layer in layers}, {
            "dem", "landsat-ot-l1", "sentinel-1-grd", "sentinel-2-l1c",
            "sentinel-2-l2a", "sentinel-3-olci", "sentinel-3-olci-l2",
            "sentinel-3-slstr", "sentinel-3-slstr-l2",
            "sentinel-3-synergy-l2", "sentinel-5p-l2",
        })
        self.assertTrue(all("//VERSION=3" in layer["evalscript"].replace(" ", "")
                            and "dataMask" in layer["evalscript"] for layer in layers))
        for layer in layers:
            self.assertLessEqual(set(layer.get("data_filter", {})), {
                "mosaickingOrder", "maxCloudCoverage", "acquisitionMode",
                "polarization", "resolution", "orbitDirection", "timeliness",
                "view", "demInstance",
            })
            self.assertLessEqual(set(layer.get("processing", {})), {
                "upsampling", "downsampling", "orthorectify", "demInstance",
                "backCoeff", "speckleFilter", "clampNegative", "egm",
            })
            if layer["data_type"] == "sentinel-1-grd":
                self.assertNotIn("demInstance", layer.get("data_filter", {}))
                self.assertIn("demInstance", layer.get("processing", {}))
            if layer["data_type"] == "sentinel-3-slstr":
                self.assertEqual(layer.get("data_filter", {}).get("view"), "NADIR")
                self.assertNotIn("view", layer.get("processing", {}))
        for product in products:
            for layer in product["layers"]:
                if layer["data_type"] == "dem":
                    expected = ("COPERNICUS_30" if "COPERNICUS_30" in product["name"]
                                else "COPERNICUS_90")
                    self.assertEqual(layer.get("data_filter", {}).get("demInstance"), expected)

    def test_profile_normalization_validates_catalogue_location_date_and_flags(self):
        value = copernicus.normalize_profile({})
        self.assertEqual(value, copernicus.DEFAULT_PROFILE)
        self.assertIsNot(value, copernicus.DEFAULT_PROFILE)
        value = copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE,
                                              "date": "2026-09-01",
                                              "latitude": 52, "longitude": 13})
        self.assertEqual(value["date"], "2026-09-01")
        self.assertEqual(value["latitude"], 52.0)
        self.assertEqual(value["coverage_mode"], "fill_gaps")
        self.assertEqual(value["lookback_days"], 14)
        self.assertNotIn("black_nodata", value)
        self.assertEqual(copernicus.LOOKBACK_DAYS, (3, 7, 14, 30, 45, 60, 90))
        for days in copernicus.LOOKBACK_DAYS:
            with self.subTest(lookback_days=days):
                normalized = copernicus.normalize_profile({
                    **copernicus.DEFAULT_PROFILE, "lookback_days": days,
                })
                self.assertEqual(normalized["lookback_days"], days)
        for update in (
            {"mission": "Sentinel-1"}, {"product": "missing"}, {"layer": "missing"},
            {"date": "today"}, {"latitude": 90}, {"longitude": 181},
            {"map_zoom": 7.0}, {"map_labels": 1}, {"black_nodata": 0},
            {"coverage_mode": "missing"}, {"lookback_days": 5}, {"lookback_days": True},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE, **update})

        legacy = {key: value for key, value in copernicus.DEFAULT_PROFILE.items()
                  if key not in {"coverage_mode", "lookback_days"}}
        self.assertEqual(
            copernicus.normalize_profile({**legacy, "black_nodata": False})["coverage_mode"],
            "single",
        )
        migrated_black = copernicus.normalize_profile({**legacy, "black_nodata": True})
        self.assertEqual(migrated_black["coverage_mode"], "black")
        self.assertNotIn("black_nodata", migrated_black)

    def test_default_matches_browser_sentinel_2_l2a_and_zoom_ranges(self):
        product = copernicus.get_product(
            copernicus.DEFAULT_PROFILE["configuration"], copernicus.DEFAULT_PROFILE["product"]
        )
        layer = copernicus.get_layer(product, copernicus.DEFAULT_PROFILE["layer"])
        self.assertEqual(product["name"], "Sentinel-2 L2A")
        self.assertEqual(layer["data_type"], "sentinel-2-l2a")
        expected = {
            "sentinel-1-grd": (7, 18),
            "sentinel-2-l1c": (10, 18),
            "sentinel-2-l2a": (7, 18),
            "sentinel-3-olci": (6, 18),
            "sentinel-3-olci-l2": (6, 18),
            "sentinel-3-slstr": (6, 18),
            "sentinel-3-slstr-l2": (5, 18),
            "sentinel-3-synergy-l2": (6, 18),
            "sentinel-5p-l2": (3, 19),
            "landsat-ot-l1": (7, 18),
            "dem": (7, 25),
        }
        layers = [item for theme in copernicus.themes() for product in theme["products"]
                  for item in product["layers"]]
        for data_type, bounds in expected.items():
            with self.subTest(data_type=data_type):
                matching = next(item for item in layers if item["data_type"] == data_type)
                zooms = copernicus.map_zooms(matching)
                self.assertEqual((zooms[0], zooms[-1]), bounds)

        self.assertEqual(
            copernicus.map_zooms_for_view(layer, 51.1657, (14400, 8640)),
            tuple(range(7, 19)),
        )
        s5p_layer = next(item for item in layers if item["data_type"] == "sentinel-5p-l2")
        self.assertEqual(
            copernicus.map_zooms_for_view(s5p_layer, 51.1657, (3840, 2160))[0], 4
        )

        l1c_product = next(
            product for product in copernicus.products("DEFAULT-THEME", "Sentinel-2")
            if product["name"] == "Sentinel-2 L1C"
        )
        with self.assertRaisesRegex(ValueError, "10 through 18"):
            copernicus.normalize_profile({
                **copernicus.DEFAULT_PROFILE,
                "product": l1c_product["id"],
                "layer": l1c_product["layers"][0]["id"],
                "map_zoom": 7,
            })

    def test_catalogue_date_parser_accepts_distinct_strings_and_stac_features(self):
        result = {"features": [
            "2026-09-12",
            {"properties": {"datetime": "2026-09-11T10:20:30.123Z"}},
            {"properties": {"date": "2026-09-10"}},
            None,
        ]}
        values = list(copernicus._catalogue_dates(result))
        self.assertEqual([value.date().isoformat() for value in values],
                         ["2026-09-12", "2026-09-11", "2026-09-10"])
        self.assertTrue(all(value.tzinfo == dt.timezone.utc for value in values))

    def test_catalogue_search_uses_location_collection_and_supported_filters(self):
        client = copernicus.CopernicusClient("id", "secret")
        theme = copernicus.get_theme("DEFAULT-THEME")
        product = next(item for item in theme["products"] if "Sentinel-1" in item["missions"])
        layer = product["layers"][0]
        profile = dict(copernicus.DEFAULT_PROFILE, mission="Sentinel-1",
                       product=product["id"], layer=layer["id"])
        payload = client._catalog_payload(profile, product, layer, 1920, 1080,
                                          "2026-09-01T00:00:00Z",
                                          "2026-09-12T00:00:00Z")
        self.assertEqual(payload["intersects"], {"type": "Point", "coordinates": [10.4515, 51.1657]})
        self.assertEqual(payload["collections"], ["sentinel-1-grd"])
        self.assertIn("sar:instrument_mode", payload["filter"])
        self.assertIn("s1:polarization", payload["filter"])

    def test_vector_tile_geometry_decoder_handles_line_features(self):
        def varint(value):
            result = bytearray()
            while value > 0x7F:
                result.append((value & 0x7F) | 0x80)
                value >>= 7
            result.append(value)
            return bytes(result)

        def field(number, wire, value):
            tag = varint((number << 3) | wire)
            return tag + (varint(len(value)) + value if wire == 2 else varint(value))

        # MoveTo (10, 20), then LineTo (15, 17); deltas use zig-zag coding.
        geometry = b"".join(varint(value) for value in (9, 20, 40, 10, 10, 5))
        feature = field(3, 0, 2) + field(4, 2, geometry)
        layer = field(2, 2, feature) + field(5, 0, 4096)
        tile = field(3, 2, layer)
        self.assertEqual(copernicus._decode_vector_tile_lines(tile),
                         [(4096, [(10, 20), (15, 17)])])
        with self.assertRaises(ValueError):
            copernicus._decode_vector_tile_lines(field(3, 2, field(2, 2, feature[:-1])))


class ClientTests(unittest.TestCase):
    def test_transient_network_failures_are_retried_three_times(self):
        attempts = [
            urllib.error.URLError(ConnectionResetError(10054, "reset")),
            urllib.error.URLError(TimeoutError("timed out")),
            _Response(b"ok", "text/plain"),
        ]

        def opener(_request, timeout):
            self.assertEqual(timeout, 17)
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        client = copernicus.CopernicusClient(timeout=17, opener=opener)
        with mock.patch("marblescape_copernicus.time.sleep") as sleep:
            value = client._open("request", 20)
        self.assertEqual(value, (b"ok", "text/plain"))
        self.assertEqual(sleep.call_args_list, [mock.call(0.5), mock.call(1.0)])

    def test_bad_request_is_not_retried_and_preserves_service_detail(self):
        error = urllib.error.HTTPError(
            "https://example.invalid", 400, "Bad Request", {},
            io.BytesIO(b'{"description":"Output width exceeds the pixel limit"}'),
        )
        opener = mock.Mock(side_effect=error)
        client = copernicus.CopernicusClient(opener=opener)
        with self.assertRaisesRegex(RuntimeError, "Output width exceeds the pixel limit"):
            client._open("request", 20)
        self.assertEqual(opener.call_count, 1)

    def test_default_opener_uses_current_ca_bundle_and_system_trust(self):
        response = _Response(b"ok", "text/plain")
        with mock.patch.object(
            copernicus.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            client = copernicus.CopernicusClient()
            raw, content_type = client._open(
                copernicus.urllib.request.Request("https://example.test/value"), 10
            )
        self.assertEqual(raw, b"ok")
        self.assertEqual(content_type, "text/plain")
        self.assertIn("context", urlopen.call_args.kwargs)
        self.assertGreater(urlopen.call_args.kwargs["context"].cert_store_stats()["x509_ca"], 0)

    def test_gisco_network_failure_is_not_reported_as_copernicus_failure(self):
        opener = mock.Mock(side_effect=urllib.error.URLError("certificate failure"))
        client = copernicus.CopernicusClient(opener=opener)
        request = copernicus.urllib.request.Request(
            copernicus.OSM_BACKGROUND_URL.format(z=7, x=1, y=1)
        )
        with mock.patch("marblescape_copernicus.time.sleep"), \
             self.assertRaisesRegex(RuntimeError, "GISCO map service network request failed"):
            client._open(request, 20)
        self.assertEqual(opener.call_count, copernicus.NETWORK_ATTEMPTS)

    def test_catalog_request_accepts_stac_geojson(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response(b'{"type":"FeatureCollection","features":[]}',
                             "application/geo+json;charset=utf-8")

        client = copernicus.CopernicusClient("id", "secret", timeout=17, opener=opener)
        client._token = "token"
        client._token_deadline = time.monotonic() + 60
        result = client._json_request(copernicus.CATALOG_URL, {"collections": ["sentinel-1-grd"]})

        self.assertEqual(result["features"], [])
        request, timeout = requests[0]
        self.assertEqual(timeout, 17)
        self.assertEqual(request.get_header("Accept"), "application/geo+json")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_latest_and_date_refresh_parse_distinct_response_and_paginate(self):
        client = copernicus.CopernicusClient("id", "secret")
        responses = [
            {"features": ["2026-09-10", "2026-09-12", "2026-09-11"], "context": {}},
            {"features": [
                {"properties": {"datetime": "2026-09-12T08:20:00Z"}},
                {"properties": {"datetime": "2026-09-12T10:40:30.123Z"}},
            ], "context": {}},
        ]
        payloads = []

        def request(_url, payload):
            payloads.append(dict(payload))
            return responses.pop(0)

        client._json_request = request
        frame = client.latest(copernicus.DEFAULT_PROFILE, (1920, 1080))
        self.assertEqual(frame["date"], "2026-09-12")
        self.assertEqual(frame["timestamp"], "2026-09-12T10:40:30.123000Z")
        self.assertTrue(frame["latest"])
        self.assertEqual(payloads[0]["distinct"], "date")
        self.assertNotIn("distinct", payloads[1])

        responses.extend([
            {"features": ["2026-09-10", "2026-09-12"], "context": {"next": 2}},
            {"features": [{"properties": {"datetime": "2026-09-11T10:00:00Z"}}],
             "context": {}},
        ])
        self.assertEqual(client.list_dates(copernicus.DEFAULT_PROFILE, (1920, 1080)),
                         ["2026-09-12", "2026-09-11", "2026-09-10"])
        self.assertEqual(payloads[-1]["next"], 2)

    def test_process_request_uses_gap_fill_window_rgba_evalscript_and_documented_size(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response(_png((3, 2)), "image/png")

        client = copernicus.CopernicusClient("id", "secret", timeout=17, opener=opener)
        client._token = "token"
        client._token_deadline = time.monotonic() + 60
        profile = copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE, "date": "2026-09-01"})
        frame = client.latest(profile, (3, 2))
        image, downloaded = client._process_tile(frame, [1, 2, 3, 4], 3, 2)
        self.assertEqual(image.size, (3, 2))
        self.assertEqual(downloaded, len(_png((3, 2))))
        request, timeout = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(timeout, 17)
        self.assertEqual(payload["output"]["width"], 3)
        self.assertEqual(payload["output"]["height"], 2)
        self.assertEqual(payload["input"]["bounds"]["bbox"], [1, 2, 3, 4])
        data_filter = payload["input"]["data"][0]["dataFilter"]
        self.assertEqual(data_filter["timeRange"], {
            "from": "2026-08-19T00:00:00Z", "to": "2026-09-01T23:59:59Z"
        })
        self.assertEqual(data_filter["mosaickingOrder"], "mostRecent")
        self.assertIn("dataMask", payload["evalscript"])

        for mode in ("single", "black"):
            mode_profile = copernicus.normalize_profile({
                **profile, "coverage_mode": mode, "black_nodata": mode == "black"
            })
            mode_frame = client.latest(mode_profile, (3, 2))
            client._process_tile(mode_frame, [1, 2, 3, 4], 3, 2)
            mode_filter = json.loads(requests[-1][0].data)["input"]["data"][0]["dataFilter"]
            self.assertEqual(mode_filter["timeRange"], {
                "from": "2026-09-01T00:00:00Z", "to": "2026-09-01T23:59:59Z"
            })

    def test_process_request_rejects_oversized_tile_before_network_access(self):
        client = copernicus.CopernicusClient(opener=mock.Mock())
        frame = {
            "layer": copernicus.get_layer(
                copernicus.get_product("DEFAULT-THEME", copernicus.DEFAULT_PROFILE["product"]),
                copernicus.DEFAULT_PROFILE["layer"],
            ),
            "date": "2026-09-01",
        }
        with self.assertRaisesRegex(ValueError, "between 1 and 2500"):
            client._process_tile(frame, [1, 2, 3, 4], 2501, 10)
        client._opener.assert_not_called()

    def test_large_process_output_is_split_at_2500_and_stitched_exactly(self):
        client = copernicus.CopernicusClient("id", "secret")
        profile = copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE, "date": "2026-09-01"})
        frame = client.latest(profile, (2601, 2501))

        def tile(_frame, _bbox, width, height):
            return Image.new("RGBA", (width, height), (1, 2, 3, 255)), 1

        with mock.patch.object(client, "_process_tile", side_effect=tile) as process:
            image, downloaded = client._render_satellite(frame)
        self.assertEqual(image.size, (2601, 2501))
        self.assertEqual(downloaded, 4)
        self.assertEqual(process.call_count, 4)
        self.assertTrue(all(call.args[2] <= 2500 and call.args[3] <= 2500
                            for call in process.call_args_list))
        self.assertEqual(image.getpixel((2600, 2500)), (1, 2, 3, 255))

    def test_14400_by_8640_output_is_partitioned_within_process_limit(self):
        client = copernicus.CopernicusClient("id", "secret")
        profile = copernicus.normalize_profile({
            **copernicus.DEFAULT_PROFILE, "date": "2026-09-01"
        })
        frame = client.latest(profile, (14400, 8640))
        parts = list(client._process_parts(frame))
        self.assertEqual(len(parts), 24)
        self.assertEqual(sum(width * height for _x, _y, width, height, _bbox in parts),
                         14400 * 8640)
        self.assertTrue(all(width <= 2500 and height <= 2500
                            for _x, _y, width, height, _bbox in parts))

    def test_date_line_view_wraps_without_out_of_range_process_bboxes(self):
        client = copernicus.CopernicusClient("id", "secret")
        product = next(item for item in copernicus.products("DEFAULT-THEME", "Sentinel-5P"))
        profile = copernicus.normalize_profile({
            **copernicus.DEFAULT_PROFILE, "mission": "Sentinel-5P",
            "product": product["id"], "layer": product["layers"][0]["id"],
            "date": "2026-09-01", "longitude": 179.9, "map_zoom": 4,
        })
        frame = client.latest(profile, (100, 40))
        bboxes = []

        def tile(_frame, bbox, width, height):
            bboxes.append((bbox, width, height))
            return Image.new("RGBA", (width, height), (1, 2, 3, 255)), 1

        with mock.patch.object(client, "_process_tile", side_effect=tile):
            image, _ = client._render_satellite(frame)
        self.assertEqual(image.size, (100, 40))
        self.assertEqual(sum(item[1] for item in bboxes), 100)
        self.assertTrue(all(
            -copernicus.WEB_MERCATOR_HALF_WORLD <= bbox[0] < bbox[2]
            <= copernicus.WEB_MERCATOR_HALF_WORLD for bbox, _width, _height in bboxes
        ))

    def test_gisco_tiles_above_native_zoom_are_overzoomed_without_invalid_url(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            return _Response(_png((256, 256)), "image/png")

        client = copernicus.CopernicusClient(opener=opener)
        image, downloaded = client._cached_map_tile(copernicus.OSM_BACKGROUND_URL, 19, 2, 3)
        self.assertEqual(image.size, (256, 256))
        self.assertGreater(downloaded, 0)
        self.assertIn("/18/1/1.png", requests[0])
        sibling, sibling_downloaded = client._cached_map_tile(
            copernicus.OSM_BACKGROUND_URL, 19, 3, 3
        )
        self.assertEqual(sibling.size, (256, 256))
        self.assertEqual(sibling_downloaded, 0)
        self.assertEqual(len(requests), 1)

        border_requests = []

        def border_opener(request, timeout):
            border_requests.append(request.full_url)
            return _Response(b"", "application/vnd.mapbox-vector-tile")

        border_client = copernicus.CopernicusClient(opener=border_opener)
        border, border_downloaded = border_client._cached_border_tile(
            copernicus.GISCO_BORDERS_URL, 19, 2, 3
        )
        sibling_border, sibling_border_downloaded = border_client._cached_border_tile(
            copernicus.GISCO_BORDERS_URL, 19, 3, 3
        )
        self.assertEqual(border.size, (256, 256))
        self.assertEqual(sibling_border.size, (256, 256))
        self.assertEqual(border_downloaded, 0)
        self.assertEqual(sibling_border_downloaded, 0)
        self.assertEqual(len(border_requests), 1)
        self.assertIn("/18/1/1.pbf", border_requests[0])

    def test_nodata_background_and_labels_are_independent(self):
        client = copernicus.CopernicusClient("id", "secret")
        satellite = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        satellite.putpixel((0, 0), (255, 0, 0, 255))

        def render(_frame):
            return satellite.copy(), 10

        def map_overlay(_frame, template):
            color = (0, 180, 0, 255) if template == copernicus.OSM_BACKGROUND_URL else (0, 0, 255, 64)
            return Image.new("RGBA", (4, 4), color), 20

        with mock.patch.object(client, "_render_satellite", side_effect=render), \
             mock.patch.object(client, "_map_overlay", side_effect=map_overlay) as overlay, \
             mock.patch.object(client, "_draw_attribution"):
            black_frame = {"profile": dict(copernicus.DEFAULT_PROFILE,
                                             coverage_mode="black", black_nodata=True,
                                             map_labels=False)}
            black_png, black_downloaded = client.fetch_image(black_frame)
            self.assertEqual(overlay.call_count, 0)
            self.assertEqual(black_downloaded, 10)
            with Image.open(io.BytesIO(black_png)) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(image.getpixel((3, 3)), (0, 0, 0))

            map_frame = {"profile": dict(copernicus.DEFAULT_PROFILE,
                                           coverage_mode="single", black_nodata=False,
                                           map_labels=True)}
            map_png, map_downloaded = client.fetch_image(map_frame)
            self.assertEqual(overlay.call_count, 3)
            self.assertEqual(map_downloaded, 70)
            self.assertEqual(overlay.call_args_list[0].args[1], copernicus.OSM_BACKGROUND_URL)
            self.assertEqual(overlay.call_args_list[1].args[1], copernicus.GISCO_BORDERS_URL)
            self.assertEqual(overlay.call_args_list[2].args[1], copernicus.OSM_LABELS_URL)
            with Image.open(io.BytesIO(map_png)) as image:
                self.assertNotEqual(image.getpixel((3, 3)), (0, 0, 0))

    def test_optional_map_failure_keeps_satellite_in_every_coverage_mode(self):
        client = copernicus.CopernicusClient("id", "secret")
        satellite = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        satellite.putpixel((0, 0), (255, 0, 0, 255))

        for mode in ("single", "fill_gaps", "black"):
            with self.subTest(mode=mode), \
                 mock.patch.object(
                     client, "_render_satellite", return_value=(satellite.copy(), 10)
                 ), \
                 mock.patch.object(
                     client, "_map_overlay", side_effect=RuntimeError("certificate failure")
                 ) as overlay:
                frame = {"profile": dict(
                    copernicus.DEFAULT_PROFILE, coverage_mode=mode, map_labels=True
                )}
                rendered, downloaded = client.fetch_image(frame)
                self.assertEqual(downloaded, 10)
                self.assertEqual(overlay.call_count, 1)
                self.assertEqual(len(client.last_render_warnings), 1)
                self.assertIn("satellite imagery was kept", client.last_render_warnings[0])
                with Image.open(io.BytesIO(rendered)) as image:
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                    self.assertEqual(image.getpixel((3, 3)), (0, 0, 0))


class RuntimeIntegrationTests(unittest.TestCase):
    def test_example_configuration_contains_valid_default_copernicus_profile(self):
        with app.DEFAULT_CONFIG_TEMPLATE_PATH.open("rb") as handle:
            config = tomllib.load(handle)
        provider, profiles = app.normalize_source_configuration(
            config["source"]["provider"], config["sources"]
        )
        self.assertEqual(provider, "eumetsat")
        self.assertEqual(profiles["copernicus"], copernicus.DEFAULT_PROFILE)
        self.assertEqual(config["copernicus"], {
            "client_id": "", "client_secret_protected": ""
        })

    def test_copernicus_render_plan_uses_exact_output_without_wms(self):
        with mock.patch.multiple(app, IMAGE_SOURCE="copernicus", WIDTH=3840,
                                 HEIGHT=2160, ASPECT_RATIO="16:9"):
            plan = app.prepare_runtime_render_plan({})
        self.assertEqual(plan[0:5], (3840, 2160, 3840, 2160, 1.0))
        self.assertEqual(plan[9], "copernicus")
        self.assertEqual(plan[10], [])

    def test_auth_serialization_never_writes_plaintext_secret(self):
        old = "[source]\nprovider = \"eumetsat\"\n"
        with mock.patch.object(app, "COPERNICUS_CLIENT_SECRET", "old"), \
             mock.patch.object(app, "COPERNICUS_CLIENT_SECRET_PROTECTED", "dpapi:old"), \
             mock.patch.object(app, "protect_client_secret", return_value="dpapi:encrypted") as protect:
            result = app.replace_copernicus_auth_configuration(
                old, {"client_id": "client", "client_secret": "plain-secret"}
            )
        protect.assert_called_once_with("plain-secret")
        self.assertNotIn("plain-secret", result)
        parsed = tomllib.loads(result)
        self.assertEqual(parsed["copernicus"], {
            "client_id": "client", "client_secret_protected": "dpapi:encrypted"
        })

    def test_copernicus_image_key_ignores_unused_wms_placement_fields(self):
        state = app.capture_loaded_configuration()
        state["IMAGE_SOURCE"] = "copernicus"
        state["COPERNICUS_CLIENT_ID"] = "client"
        state["COPERNICUS_CLIENT_SECRET"] = "secret"
        first = app.image_configuration_key(state)
        changed = dict(state, VIEW_MODE="crop", ZOOM=9, BACKGROUND_COLOR="#123456")
        self.assertEqual(first, app.image_configuration_key(changed))
        changed = dict(state)
        changed["SOURCE_PROFILES"] = {key: dict(value)
                                      for key, value in state["SOURCE_PROFILES"].items()}
        changed["SOURCE_PROFILES"]["copernicus"] = dict(
            changed["SOURCE_PROFILES"]["copernicus"], longitude=11.0
        )
        self.assertNotEqual(first, app.image_configuration_key(changed))


if __name__ == "__main__":
    unittest.main()
