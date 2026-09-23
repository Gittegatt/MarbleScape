"""Conditional catalogue requests without live provider access."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from marblescape_catalogues import CatalogueClient, _CatalogueHttpCache
from marblescape_eumetsat import EumetsatCatalogueClient, DECORATIONS_URL
from marblescape_slider import SliderClient, CATALOGUE_URL
from marblescape_worldview import WorldviewClient, CAPABILITIES_URL


class _Response(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers

    def geturl(self):
        return self.url


class CatalogueHttpCacheTests(unittest.TestCase):
    def test_noaa_outage_uses_disk_cache_without_repeating_network_calls(self):
        class OfflineNOAA:
            def __init__(self):
                self.calls = []

            def list_areas(self, provider, refresh=False):
                self.calls.append(("areas", provider, refresh))
                raise OSError("NOAA offline")

            def list_products(self, provider, area_id, refresh=False):
                self.calls.append(("products", provider, area_id, refresh))
                raise OSError("NOAA offline")

        with tempfile.TemporaryDirectory() as directory:
            noaa = OfflineNOAA()
            client = CatalogueClient(
                noaa=noaa, cache_path=Path(directory) / "catalogues.json", retries=1,
            )
            areas = [{"id": "full_disk", "label": "Full Disk", "category": "Global"}]
            products = [{"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["678x678"]}]
            client._cache.store_areas("goes_east", areas)
            client._cache.store_products("goes_east", "full_disk", products)
            self.assertEqual(client.list_areas("goes_east", refresh=True), areas)
            call_count = len(noaa.calls)
            self.assertTrue(client.catalogue_offline("goes_east"))
            self.assertEqual(client.list_areas("goes_east"), areas)
            self.assertEqual(client.list_products("goes_east", "full_disk"), products)
            self.assertEqual(len(noaa.calls), call_count)

    def test_public_catalogue_clients_reuse_unchanged_metadata(self):
        for client_type, url in ((SliderClient, CATALOGUE_URL),
                                 (WorldviewClient, CAPABILITIES_URL)):
            with self.subTest(client=client_type.__name__), \
                    tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "catalogue_http.json"
                response = _Response(b"catalogue body", {"Last-Modified": "Wed, 23 Sep 2026 12:00:00 GMT"})
                response.url = url
                first_opener = mock.Mock()
                first_opener.open.return_value = response
                first = client_type(opener=first_opener)
                first.metadata_cache = _CatalogueHttpCache(path)
                self.assertEqual(first._request(url, 100)[0], b"catalogue body")

                def unchanged(request, timeout):
                    self.assertEqual(request.get_header("If-modified-since"),
                                     "Wed, 23 Sep 2026 12:00:00 GMT")
                    raise HTTPError(request.full_url, 304, "Not Modified", {}, None)

                second_opener = mock.Mock()
                second_opener.open.side_effect = unchanged
                second = client_type(opener=second_opener)
                second.metadata_cache = _CatalogueHttpCache(path)
                self.assertEqual(second._request(url, 100)[0], b"catalogue body")

    def test_etag_reuses_saved_body_after_restart_on_http_304(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue_http.json"
            first = EumetsatCatalogueClient()
            first.metadata_cache = _CatalogueHttpCache(path)
            with mock.patch("marblescape_eumetsat.urlopen", return_value=_Response(
                    b'{"products": [1]}', {"ETag": '"revision-1"'})):
                self.assertEqual(first._json_get(DECORATIONS_URL), {"products": [1]})

            second = EumetsatCatalogueClient()
            second.metadata_cache = _CatalogueHttpCache(path)

            def unchanged(request, timeout):
                self.assertEqual(request.get_header("If-none-match"), '"revision-1"')
                raise HTTPError(request.full_url, 304, "Not Modified", {}, None)

            with mock.patch("marblescape_eumetsat.urlopen", side_effect=unchanged):
                self.assertEqual(second._json_get(DECORATIONS_URL), {"products": [1]})

    def test_response_without_validator_is_not_reused(self):
        cache = _CatalogueHttpCache()
        cache.store(DECORATIONS_URL, b'{"a": 1}', {"ETag": '"old"'})
        cache.store(DECORATIONS_URL, b'{"a": 2}', {})
        self.assertEqual(cache.headers(DECORATIONS_URL), {})
        self.assertIsNone(cache.response(DECORATIONS_URL, 100))


if __name__ == "__main__":
    unittest.main()
