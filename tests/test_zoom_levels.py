"""Zoom dropdown steps, kept saved values and the sharp-zoom hint of still images."""

import unittest

import marblescape_download as app

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk is not None:
    import marblescape_source_settings as source_settings


class ZoomStepTests(unittest.TestCase):
    def labels(self, value, mode):
        return [label for _number, label in app.zoom_choices(value, mode)]

    def test_views_and_still_images_offer_the_same_steps_with_the_default_marked(self):
        self.assertEqual(app.DEFAULT_ZOOM, 1.0)
        expected = ["0.5", "0.75", "0.9", "1 (Default)", "1.1", "1.25", "1.5", "2", "2.5",
                    "3", "4", "5", "6", "8", "10"]
        self.assertEqual(self.labels(1.0, "view"), expected)
        self.assertEqual(self.labels(1.0, "still"), expected)

    def test_custom_area_steps_reach_50_with_two_size_examples(self):
        self.assertEqual(self.labels(1.0, "custom"), [
            "1", "1.5", "2", "3", "4", "5 (about Europe)", "6", "8", "10", "12", "15", "20",
            "25 (about Germany)", "30", "40", "50"])

    def test_a_saved_value_between_the_steps_stays_at_its_place(self):
        self.assertEqual(self.labels(1.3, "view")[5:8], ["1.25", "1.3 (saved)", "1.5"])
        self.assertEqual(self.labels(120.0, "custom")[-2:], ["50", "120 (saved)"])
        # A step is never doubled.
        self.assertEqual(self.labels(1.1, "view").count("1.1"), 1)
        self.assertNotIn("1.1 (saved)", self.labels(1.1, "view"))

    def test_each_mode_has_its_valid_range(self):
        for value, mode, valid in (
            (0.05, "still", True), (20, "still", True), (0.04, "still", False), (25, "still", False),
            (1000, "custom", True), (1001, "custom", False), (300, "view", True),
            (0, "view", False), (-1, "custom", False), (float("nan"), "view", False), (None, "view", False),
        ):
            with self.subTest(value=value, mode=mode):
                self.assertEqual(app.zoom_in_range(value, mode), valid)

    def test_custom_area_hint_names_the_view_and_blur(self):
        sharp = app.custom_area_quality_text(20)
        self.assertNotIn("blurry", sharp)
        self.assertIn("outside the satellites' view (the Americas, East Asia, the poles)", sharp)
        self.assertTrue(app.custom_area_quality_text(25).startswith(
            "Above about zoom 20 EUMETSAT imagery gets blurry."))


@unittest.skipIf(tk is None, "tkinter is not installed")
class SharpZoomTests(unittest.TestCase):
    def test_the_limit_follows_the_source_image_and_the_output(self):
        limit = source_settings.sharp_zoom_limit
        # Full disk on a 1920x1080 output (fit: the height decides).
        self.assertEqual(limit((10848, 10848), (1920, 1080)), 10.04)
        # The height decides: 3000 / 1080 = 2.78, the last 0.01 step within is 2.77.
        self.assertEqual(limit((5000, 3000), (1920, 1080)), 2.77)
        # Crop fills the output: the width decides.
        self.assertEqual(limit((10848, 10848), (1920, 1080), "crop"), 5.65)
        # Smaller than the output: enlarged already at zoom 1.
        self.assertLess(limit((1000, 1000), (1920, 1080)), 1)
        self.assertEqual(limit((50000, 50000), (1920, 1080)), 20)

    def test_noaa_jpeg_draft_caps_the_sharp_zoom(self):
        # The NOAA renderer decodes a 10848 px JPEG reduced: a quarter while that
        # covers the output, at most half once it needs 4000 px or more.
        self.assertEqual(source_settings._drafted_size((10848, 10848), (1920, 1080), 1), (2712, 2712))
        self.assertEqual(source_settings._drafted_size((10848, 10848), (1920, 1080), 5), (5424, 5424))
        self.assertEqual(source_settings._drafted_size((1000, 1000), (1920, 1080), 1), (1000, 1000))
        self.assertEqual(source_settings.sharp_zoom_limit((10848, 10848), (1920, 1080), draft=True), 5.02)

    def test_hint_lines_put_the_result_first(self):
        lines = source_settings.still_zoom_lines
        self.assertIsNone(lines(None, (1920, 1080), "fit"))
        self.assertIsNone(lines((5000, 3000, "largest", False), None, "fit"))
        # The result for the chosen zoom, then the facts; orange (True) only when enlarged.
        self.assertEqual(lines((5000, 3000, "largest", False), (1920, 1080), "fit", zoom="2"),
                         ("Zoom 2: sharp (up to zoom 2.8)", "Largest source 5000×3000 on 1920×1080", False))
        self.assertEqual(lines((5000, 3000, "largest", False), (1920, 1080), "fit", zoom="4"),
                         ("Zoom 4: enlarged 1.4× (sharp up to zoom 2.8)",
                          "Largest source 5000×3000 on 1920×1080", True))
        self.assertEqual(lines((10848, 10848, "selected", False), (1920, 1080), "fit", zoom="1")[0],
                         "Zoom 1: sharp (up to zoom 10)")
        self.assertEqual(lines((10848, 10848, "largest", True), (1920, 1080), "fit", zoom="1")[0],
                         "Zoom 1: sharp (up to zoom 5)")
        self.assertEqual(lines((1000, 1000, "selected", False), (1920, 1080), "fit", zoom="1")[0],
                         "Zoom 1: enlarged 1.1× (sharp up to zoom 0.92)")
        # Without a zoom only the limit; as one text, two lines.
        self.assertEqual(lines((5000, 3000, "largest", False), (1920, 1080), "fit")[0], "Sharp up to zoom 2.8")
        self.assertEqual(source_settings.still_zoom_hint((5000, 3000, "largest", False), (1920, 1080), "fit",
                                                         zoom="2"),
                         "Zoom 2: sharp (up to zoom 2.8)\nLargest source 5000×3000 on 1920×1080")

    def test_a_himawari_storm_view_counts_its_own_enlargement(self):
        from marblescape_himawari import STORM_VIEW_KM, storm_view_zoom
        output = (3840, 2160)
        scale = storm_view_zoom(output, 1)
        # The full disk at 11000 px stays sharp to zoom 5.1 on 3840 x 2160; the storm
        # view (3000 km across) only to about 0.78, and zoom 1.5 enlarges it about 1.9 times.
        self.assertEqual(source_settings.sharp_zoom_limit((11000, 11000), output), 5.09)
        self.assertEqual(source_settings.sharp_zoom_limit((11000, 11000), output, view_scale=scale), 0.78)
        self.assertEqual(
            source_settings.still_zoom_lines((11000, 11000, "selected", False), output, "fit", zoom="1.5",
                                             storm_view_km=STORM_VIEW_KM, view_scale=scale),
            ("Zoom 1.5: enlarged 1.9× (sharp up to zoom 0.78)",
             "Storm view, 3000 km across at zoom 1 · source 11000×11000 on 3840×2160", True))
        # On a smaller output the storm view stays sharp a little further.
        self.assertEqual(
            source_settings.still_zoom_lines((11000, 11000, "selected", False), (1920, 1080), "fit", zoom="1",
                                             storm_view_km=STORM_VIEW_KM,
                                             view_scale=storm_view_zoom((1920, 1080), 1))[0],
            "Zoom 1: sharp (up to zoom 1.6)")


if __name__ == "__main__":
    unittest.main()
