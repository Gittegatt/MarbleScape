from copy import deepcopy
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
from marblescape_copernicus import DEFAULT_PROFILE, normalize_profile, resolve_image_size
from marblescape_mosaic_adjustments import TONE_REVISION, WHITE_POINT, adjust, validate


# A saved selection without the Auto tones (new selections start with both on).
MANUAL = dict(DEFAULT_PROFILE, auto_brightness=False, auto_contrast=False)


def gradient():
    image = Image.new("RGBA", (256, 1))
    image.putdata([(value, value, value, 255) for value in range(256)])
    return image


class MosaicAdjustmentTests(unittest.TestCase):
    def test_neutral_is_pixel_identical_and_manual_contrast_is_deterministic(self):
        image = Image.new("RGBA", (2, 2), (60, 100, 140, 255))
        neutral, record = adjust(image, MANUAL)
        self.assertEqual(neutral.tobytes(), image.tobytes())
        validate(record, MANUAL)
        # 0 % contrast flattens every tone to the image's own median.
        result, record = adjust(image, dict(MANUAL, contrast=0))
        self.assertEqual(result.getpixel((0, 0)), (93, 93, 93, 255))
        validate(record, dict(MANUAL, contrast=0))

    def test_manual_contrast_never_clips_and_keeps_black_and_white(self):
        for contrast in (50, 150, 200):
            result, record = adjust(gradient(), dict(MANUAL, contrast=contrast))
            values = [pixel[0] for pixel in result.getdata()]
            self.assertEqual((values[0], values[-1]), (0, 255), contrast)
            self.assertEqual(values, sorted(values), contrast)
            # Only black and white reach the ends; shadows keep at least half their distance.
            self.assertEqual((values.count(0), values.count(255)), (1, 1), contrast)
            self.assertGreaterEqual(values[30], 30 // 2 - 1, contrast)
            validate(record, dict(MANUAL, contrast=contrast))

    def test_automatic_statistics_exclude_transparent_pixels(self):
        image = Image.new("RGBA", (20, 10), (255, 255, 255, 0))
        image.putpixel((0, 0), (64, 64, 64, 255))
        profile = dict(DEFAULT_PROFILE, auto_brightness=True, auto_contrast=True)
        result, record = adjust(image, profile)
        # One grey pixel cannot be stretched; at 25.1 % it is not dark enough to lift.
        self.assertEqual(result.getpixel((0, 0)), (64, 64, 64, 255))
        self.assertEqual(result.getpixel((1, 0))[3], 0)
        self.assertEqual(record["evaluated_pixels"], 1)
        self.assertEqual((record["stretch_low"], record["stretch_high"]), (0, 255))
        self.assertEqual(record["midtone_gamma"], 1.0)
        self.assertEqual(record["algorithm"], "mosaic-tone-v2")
        validate(record, profile)
        # A darker one is lifted until 65 % of the pixels reach 25 % (64).
        image.putpixel((0, 0), (48, 48, 48, 255))
        result, record = adjust(image, profile)
        self.assertEqual(result.getpixel((0, 0)), (64, 64, 64, 255))
        self.assertAlmostEqual(record["midtone_gamma"], 0.8301, places=4)
        validate(record, profile)
        image.putpixel((0, 0), (64, 64, 64, 255))
        # Auto brightness alone moves the median toward 42 % (107).
        alone = dict(profile, auto_contrast=False)
        result, record = adjust(image, alone)
        self.assertEqual(result.getpixel((0, 0)), (107, 107, 107, 255))
        self.assertAlmostEqual(record["midtone_gamma"], 0.627, places=3)
        validate(record, alone)

    def test_auto_contrast_stretches_by_channel_and_auto_brightness_darkens_little(self):
        image = Image.new("RGBA", (100, 1))
        # Turquoise water: blue and green stay brighter than the luminance.
        image.putdata([(20 + value, 60 + value, 80 + value, 255) for value in range(100)])
        profile = dict(MANUAL, auto_contrast=True)
        result, record = adjust(image, profile)
        # 21-177 would be stretched beyond 1.5-fold; the range widens to 170 levels.
        self.assertEqual((record["stretch_low"], record["stretch_high"]), (14, 184))
        channels = list(zip(*[pixel[:3] for pixel in result.getdata()]))
        # The brightest channel ends at the white point, below pure white.
        self.assertEqual(sum(value == 255 for value in channels[2]), 0)
        validate(record, profile)
        bright = Image.new("RGBA", (2, 2), (230, 230, 230, 255))
        _, record = adjust(bright, dict(MANUAL, auto_brightness=True))
        self.assertEqual(record["midtone_gamma"], 1.15)

    def test_auto_contrast_stretches_at_most_one_and_a_half_fold(self):
        # An overcast scene: nothing but bright cloud between 220 and 242 (not yet
        # clipped by the source, which would leave it unstretched).
        image = Image.new("RGBA", (23, 1))
        image.putdata([(value, value, value, 255) for value in range(220, 243)])
        profile = dict(MANUAL, auto_contrast=True)
        result, record = adjust(image, profile)
        self.assertEqual(record["stretch_high"] - record["stretch_low"], 170)
        self.assertEqual(record["stretch_high"], 255)
        values = [pixel[0] for pixel in result.getdata()]
        # The cloud stays bright and its steps stay small.
        self.assertGreater(min(values), 190)
        self.assertLessEqual(max(b - a for a, b in zip(values, values[1:])), 2)
        validate(record, profile)

    def test_auto_contrast_keeps_the_black_point_when_the_source_clips_to_white(self):
        # Landsat-like: dark land and cloud that the source already clipped to white.
        values = [value for value in range(30, 90) for _ in range(3)] + [255] * 60
        image = Image.new("RGBA", (len(values), 1))
        image.putdata([(value, value, value, 255) for value in values])
        profile = dict(MANUAL, auto_contrast=True, auto_brightness=True)
        result, record = adjust(image, profile)
        self.assertEqual((record["stretch_low"], record["stretch_high"]), (0, 255))
        output = dict(zip(values, (pixel[0] for pixel in result.getdata())))
        # The picture stays exactly as the source rendered it.
        self.assertEqual(result.tobytes(), image.tobytes())
        self.assertEqual(output[255], 255)
        validate(record, profile)
        # Just below the clipping level the stretch still applies.
        values[-60:] = [240] * 60
        image.putdata([(value, value, value, 255) for value in values])
        _, record = adjust(image, profile)
        self.assertNotEqual((record["stretch_low"], record["stretch_high"]), (0, 255))

    def test_large_bright_surfaces_keep_highlight_texture(self):
        def picture(*ranges):
            values = [value for low, high, repeat in ranges for value in range(low, high) for _ in range(repeat)]
            image = Image.new("RGBA", (len(values), 1))
            image.putdata([(value, value, value, 255) for value in values])
            return image, values

        profile = dict(MANUAL, auto_contrast=True)
        # A few bright spots (scattered cloud, sand): they roll off, none turns white.
        image, _values = picture((50, 150, 19), (240, 256, 6))
        result, record = adjust(image, profile)
        self.assertLess(max(pixel[0] for pixel in result.getdata()), 255)
        validate(record, profile)
        # A large bright surface (salt flat): the straight stretch keeps its texture.
        image, values = picture((60, 120, 2), (200, 250, 6))
        result, record = adjust(image, profile)
        output = dict(zip(values, (pixel[0] for pixel in result.getdata())))
        # Rolled off, these two salt tones would end 19 levels apart; straight, 26.
        self.assertGreater(output[240] - output[220], 24)
        # Its brightest tones reach the white point, slightly below pure white.
        top = round(255 * WHITE_POINT)
        self.assertEqual(max(pixel[0] for pixel in result.getdata()), top)
        self.assertGreater(sum(pixel[0] == top for pixel in result.getdata()), 0)
        validate(record, profile)

    def test_manual_brightness_with_auto_contrast_is_a_midtone_gamma(self):
        profile = dict(MANUAL, auto_contrast=True, brightness=150)
        # A gradient up to 240: one reaching pure white counts as clipped by the source.
        image = Image.new("RGBA", (241, 1))
        image.putdata([(value, value, value, 255) for value in range(241)])
        result, record = adjust(image, profile)
        self.assertEqual(record["evalscript_brightness_percent"], 100)
        self.assertEqual(record["midtone_gamma"], round(100 / 150, 4))
        values = [pixel[0] for pixel in result.getdata()]
        self.assertGreater(values[64], 64)
        # The stretched highlights roll off toward white instead of clipping.
        self.assertEqual(values, sorted(values))
        self.assertEqual(values.count(255), 0)
        self.assertGreater(values[-1], 235)
        validate(record, profile)
        # Without an Auto option the provider applies it, as before.
        _, record = adjust(gradient(), dict(MANUAL, brightness=150))
        self.assertEqual((record["evalscript_brightness_percent"], record["midtone_gamma"]), (150, 1.0))

    def test_with_auto_contrast_auto_brightness_lifts_only_dark_land_and_tames_bright_pictures(self):
        def picture(*ranges):
            values = [value for low, high, repeat in ranges for value in range(low, high) for _ in range(repeat)]
            image = Image.new("RGBA", (len(values), 1))
            image.putdata([(value, value, value, 255) for value in values])
            return image

        both = dict(MANUAL, auto_brightness=True, auto_contrast=True)
        cases = (
            # A dark sea next to well-lit land: Auto contrast alone is right.
            ("sea and land", picture((10, 40, 3), (120, 200, 1)), lambda gamma: gamma == 1.0),
            # Dark land under bright clouds: lifted, at most to gamma 0.65.
            ("land and clouds", picture((20, 60, 5), (240, 256, 2)), lambda gamma: 0.65 <= gamma < 1),
            # A bright picture: darkened a little, keeping detail in its highlights.
            ("bright", picture((100, 230, 1)), lambda gamma: gamma == 1.15),
        )
        for name, image, expected in cases:
            with self.subTest(name):
                _, record = adjust(image, both)
                self.assertTrue(expected(record["midtone_gamma"]), record["midtone_gamma"])
                validate(record, both)

    def test_pictures_made_with_the_first_algorithm_still_validate(self):
        record = {
            "algorithm": "mosaic-tone-v1", "auto_brightness": False, "auto_contrast": False,
            "contrast_percent": 150, "brightness_percent": 100, "evalscript_brightness_percent": 100,
            "brightness_gain": 1.0, "contrast_factor": 1.5, "contrast_offset": -64.0,
            "evaluated_pixels": 4, "scope": "valid_satellite_pixels_before_fill_and_overlays",
        }
        validate(record, dict(MANUAL, contrast=150))
        with self.assertRaises(ValueError):
            validate(dict(record, brightness_gain=float("nan")), dict(MANUAL, contrast=150))

    def test_new_selections_start_with_auto_tones_and_saved_ones_keep_theirs(self):
        # Layers with a tone rule tone new selections automatically.
        self.assertTrue(normalize_profile({})["auto_brightness"])
        self.assertTrue(normalize_profile({})["auto_contrast"])
        saved_on = dict(DEFAULT_PROFILE, auto_brightness=True, auto_contrast=True)
        self.assertTrue(normalize_profile(saved_on)["auto_brightness"])
        self.assertTrue(normalize_profile(saved_on)["auto_contrast"])
        saved_before = {key: value for key, value in DEFAULT_PROFILE.items()
                        if key not in {"auto_brightness", "auto_contrast"}}
        self.assertFalse(normalize_profile(saved_before)["auto_brightness"])
        self.assertFalse(normalize_profile(saved_before)["auto_contrast"])
        self.assertFalse(normalize_profile(MANUAL)["auto_contrast"])

    def test_empty_or_flat_images_are_safe_and_preserve_alpha(self):
        for color in ((0, 0, 0, 255), (255, 255, 255, 255), (0, 0, 0, 0)):
            profile = dict(DEFAULT_PROFILE, auto_brightness=True, auto_contrast=True)
            image = Image.new("RGBA", (2, 2), color)
            result, record = adjust(image, profile)
            validate(record, profile)
            self.assertEqual(result.getchannel("A").tobytes(), image.getchannel("A").tobytes())

    def test_invalid_tone_metadata_and_settings_are_rejected(self):
        _, record = adjust(Image.new("RGBA", (2, 2)), MANUAL)
        for field, value in (("midtone_gamma", float("nan")), ("midtone_gamma", 2.0),
                             ("stretch_low", 300), ("stretch_high", 10), ("contrast_pivot", 0.95),
                             ("auto_contrast", True), ("evaluated_pixels", -1),
                             ("algorithm", "mosaic-tone-v9")):
            broken = dict(record, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(broken, MANUAL)
        for field, value in (("contrast", 3), ("auto_contrast", 1), ("auto_brightness", "yes"), ("image_size", "99x99")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                normalize_profile({field: value})

    def test_image_size_auto_and_explicit_8k_do_not_use_wms_limits(self):
        monitor = {"id": "display", "rect": (0, 0, 3840, 2160)}
        outputs = {"display": {"width": 7680, "height": 4320, "aspect_ratio": "16:9"},
                   "detached": {"width": 32768, "height": 1024, "aspect_ratio": "32:1"}}
        self.assertEqual(app.copernicus_auto_size((3840, 2160), outputs, [monitor]), (7680, 4320))
        self.assertEqual(app.copernicus_auto_size((3840, 2160), outputs, []), (3840, 2160))
        with patch.object(app, "IMAGE_SOURCE", "copernicus"), \
             patch.object(app, "get_output_dimensions", return_value=(3840, 2160)), \
             patch.object(app, "SOURCE_PROFILES", {"copernicus": dict(DEFAULT_PROFILE, image_size="7680x4320")}), \
             patch.object(app, "get_render_dimensions", side_effect=AssertionError("WMS cap")):
            plan = app.prepare_runtime_render_plan({})
        self.assertEqual(plan[:4], (7680, 4320, 7680, 4320))
        self.assertEqual(resolve_image_size({"image_size": "1920x1080"}, (7680, 4320)), (1920, 1080))
        # Auto stops at the largest picture Copernicus allows, keeping the aspect ratio:
        # a 9:16 output 3840 wide needs a 16:9 picture 6827 tall (83 megapixels).
        for target in ((16000, 9000), (3840, 6827)):
            width, height = resolve_image_size({"image_size": "auto"}, (3840, 2160), [target])
            self.assertLessEqual(width * height, 33_554_432)
            self.assertGreater(width * height, 33_400_000)
            self.assertAlmostEqual(width / height, 16 / 9, places=2)
        self.assertEqual(resolve_image_size({"image_size": "auto"}, (3840, 2160), [(2560, 1440)]), (3840, 2160))
        # Portrait sizes are valid profile values and resolve as given.
        from marblescape_copernicus import geographic_bbox
        portrait = normalize_profile(dict(DEFAULT_PROFILE, image_size="1440x2560"))
        self.assertEqual(resolve_image_size(portrait, (3840, 2160)), (1440, 2560))
        west, south, east, north = geographic_bbox(portrait, 1440, 2560)
        self.assertGreater(north - south, east - west)  # Taller than wide near the equator.
        with self.assertRaises(ValueError):
            normalize_profile(dict(DEFAULT_PROFILE, image_size="1000x2000"))
        # A fixed size above the limit still says so.
        with self.assertRaises(ValueError):
            resolve_image_size({"image_size": "40000x1000"}, (3840, 2160))


class MosaicToneCacheTests(unittest.TestCase):
    def frame(self, layer, **changes):
        return {"profile": dict(MANUAL, **changes), "layer": layer, "timestamp": "2026-09-30T00:00:00Z"}

    def test_only_selections_that_v2_renders_differently_get_a_new_cache_key(self):
        from marblescape_copernicus_mosaics import MOSAIC_PRODUCTS
        optical = next(layer for product in MOSAIC_PRODUCTS for layer in product["layers"]
                       if "var maxR" in layer["evalscript"])
        grey = next(layer for product in MOSAIC_PRODUCTS for layer in product["layers"]
                    if "soft(" in layer["evalscript"])
        tone = ("tone", "mosaic-tone-v2")
        # Auto contrast pictures also carry the tuning revision.
        retuned = ("tone", "mosaic-tone-v2", TONE_REVISION)
        unchanged = (self.frame(optical), self.frame(optical, brightness=150),
                     self.frame(grey, brightness=100), self.frame({}, auto_contrast=True))
        for frame in unchanged:
            signature = app.copernicus_frame_signature(frame)
            self.assertNotIn(tone, signature)
            self.assertNotIn(retuned, signature)
        changed = ((self.frame(optical, auto_brightness=True), tone),
                   (self.frame(optical, auto_contrast=True), retuned),
                   (self.frame(optical, auto_brightness=True, auto_contrast=True), retuned),
                   (self.frame(optical, contrast=150), tone), (self.frame(grey, brightness=150), tone))
        for frame, expected in changed:
            self.assertIn(expected, app.copernicus_frame_signature(frame))

    def test_switched_off_overlay_colors_keep_the_cache_keys(self):
        black = dict(map_labels=False, map_label_color="#000000",
                     map_borders=False, map_border_color="#000000")
        white = dict(black, map_label_color="#FFFFFF", map_border_color="#FFFFFF")
        # Older profiles stored no border color; they sign like the black ones.
        legacy = {name: value for name, value in black.items() if name != "map_border_color"}
        signatures = {app.copernicus_frame_signature(self.frame({}, **colors))
                      for colors in (black, white, legacy)}
        self.assertEqual(len(signatures), 1)
        saved = app.capture_loaded_configuration()
        try:
            keys = []
            for colors in (black, white):
                configuration = app.capture_loaded_configuration()
                configuration["IMAGE_SOURCE"] = "copernicus"
                configuration["SOURCE_PROFILES"] = dict(configuration["SOURCE_PROFILES"],
                                                        copernicus=dict(MANUAL, **colors))
                keys.append(app.image_configuration_key(configuration))
            self.assertEqual(keys[0], keys[1])
        finally:
            app.restore_loaded_configuration(saved)
        # Drawn overlays still sign their color.
        shown = [app.copernicus_frame_signature(self.frame({}, **dict(colors, map_labels=True)))
                 for colors in (black, white)]
        self.assertNotEqual(shown[0], shown[1])

    def test_grey_radar_layers_render_as_before_up_to_100_percent(self):
        import math
        from marblescape_copernicus_mosaics import MOSAIC_PRODUCTS, SOFT_HIGHLIGHTS_JS
        greys = [layer for product in MOSAIC_PRODUCTS for layer in product["layers"]
                 if "soft(" in layer["evalscript"]]
        self.assertEqual(len(greys), 2)
        for layer in greys:
            script = layer["evalscript"]
            band = "VV" if "s.VV" in script else "HH"
            # Up to 100 % the former formula min(1, max(0, v * brightness / 0.3)) is kept.
            self.assertIn(f"var x=Math.max(0,s.{band}*brightness/0.3);", script)
            self.assertIn("var v=brightness>1?soft(x):Math.min(1,x);", script)
            self.assertIn(SOFT_HIGHLIGHTS_JS.strip(), script)

        def soft(value):  # The JavaScript soft() above, in Python.
            return value if value <= 0.8 else 0.8 + 0.2 * (1 - math.exp((0.8 - value) / 0.2))
        values = [soft(value / 0.3 * 2) for value in (0.03, 0.15, 0.3, 0.6, 1.2)]
        # Above 100 % bright backscatter keeps detail instead of turning white.
        self.assertEqual(values, sorted(values))
        # Formerly white from 0.15 at 200 %; now 0.15 and 0.3 still differ and stay below white.
        self.assertLess(values[1], values[2])
        self.assertLess(values[2], 1)
        self.assertAlmostEqual(soft(0.5), 0.5)
