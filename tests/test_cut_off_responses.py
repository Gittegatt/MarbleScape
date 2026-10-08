"""A response cut off midway is a failure of the source (Status SOURCE), in every provider."""

import contextlib
import http.client
import unittest
from unittest.mock import MagicMock, patch

import marblescape_download as app
import marblescape_himawari as himawari
import marblescape_noaa as noaa
import marblescape_slider as slider
import marblescape_worldview as worldview


class CutOffResponseTests(unittest.TestCase):
    def test_a_cut_off_response_is_the_providers_unavailable_error(self):
        cases = ((noaa, noaa.NOAAClient(), noaa.BASE_URL + "index.php"),
                 (himawari, himawari.HimawariClient(), himawari.NICT_SITE_URL + "img/latest.json"),
                 (slider, slider.SliderClient(), slider.CATALOGUE_URL),
                 (worldview, worldview.WorldviewClient(), worldview.CAPABILITIES_URL))
        for module, client, url in cases:
            with self.subTest(module=module.__name__):
                response = MagicMock()
                response.geturl.return_value = url
                with patch.object(module, "open_response", return_value=contextlib.nullcontext(response)), \
                        patch.object(module, "read_response",
                                     side_effect=http.client.IncompleteRead(b"x" * 11599)):
                    with self.assertRaises(module.UnavailableError) as raised:
                        client._request(url, 1024 * 1024)
                self.assertIn("IncompleteRead", str(raised.exception))
                # Not LOST and not a vague UNAVAIL: the source failed while the internet works.
                with patch.object(app, "internet_connected", return_value=True):
                    self.assertEqual(app.profile_failure_state(raised.exception), "SOURCE")


if __name__ == "__main__":
    unittest.main()
