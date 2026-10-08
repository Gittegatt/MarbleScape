import unittest
from copy import deepcopy

from PIL import Image

import marblescape_copernicus as cop
import marblescape_mosaic_adjustments as tones
from marblescape_copernicus import CopernicusClient, get_layer, get_product, tone_profile, tone_rule
from marblescape_mosaic_adjustments import TONE_RULES, adjust, apply_rule, validate

AUTO = dict(cop.DEFAULT_PROFILE, auto_brightness=True, auto_contrast=True, brightness=100, contrast=100)


def layer(product_name, layer_id, theme="DEFAULT-THEME"):
    product = next(item for item in cop.products(theme) if item["name"] == product_name)
    return get_layer(get_product(theme, product["id"]), layer_id)


def picture(*parts, size=(40, 40)):
    """Valid pixels in bands of grey values: parts = ((value, share), ...)."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    total = size[0] * size[1]
    pixels, start = [], 0
    for value, share in parts:
        count = round(total * share)
        pixels += [(value, value, value, 255)] * count
    pixels += [(0, 0, 0, 0)] * (total - len(pixels))
    image.putdata(pixels[:total])
    return image


class ToneRuleLookupTests(unittest.TestCase):
    def test_each_tuned_layer_has_its_rule_and_all_others_none(self):
        expected = {
            ("Sentinel-2 L2A", "1_TRUE_COLOR"): "e", ("Sentinel-2 L2A", "2_TONEMAPPED_NATURAL_COLOR"): "b",
            ("Sentinel-2 L1C", "1_TRUE_COLOR"): "b", ("Sentinel-2 L1C", "2_TONEMAPPED_NATURAL_COLOR"): "b",
            ("Sentinel-2 Quarterly Mosaics", "TRUE_COLOR_CLOUDLESS"): "c",
            ("WorldCover Annual Cloudless Mosaics", "TRUE_COLOR_CLOUDLESS"): "c",
            ("Landsat-8/9 L1", "1_TRUE_COLOR"): "f", ("Landsat-8/9 L1", "2_TRUE_COLOR_PANSHARPENED"): "f",
            ("Landsat-8/9 L1", "3_TONEMAPPED_NATURAL_COLOR"): "a",
            ("Sentinel-3 OLCI", "1_TRUE_COLOR"): "b", ("Sentinel-3 OLCI", "1_TRUE_COLOR_ENHANCED"): "b",
            ("Sentinel-3 OLCI", "6_TRUE-COLOR-HIGLIGHT-OPTIMIZED"): "d",
        }
        for (product, layer_id), rule in expected.items():
            self.assertEqual(tone_rule(layer(product, layer_id)), rule, (product, layer_id))
        for product, layer_id in (("Sentinel-2 L2A", "2_FALSE_COLOR"), ("Sentinel-2 L2A", "6-SWIR"),
                                  ("Sentinel-2 L1C", "2_FALSE_COLOR"), ("Sentinel-2 Quarterly Mosaics",
                                                                        "FALSE_COLOR_CLOUDLESS"),
                                  ("Landsat-8/9 L1", "4_FALSE_COLOR"), ("Sentinel-2 L2A", "3_NDVI"),
                                  ("Sentinel-1 IW Monthly Mosaics", "RGB_RATIO")):
            found = next((item for item in cop.products("DEFAULT-THEME") if item["name"] == product), None)
            candidate = get_layer(found, layer_id) if found else None
            if candidate is not None:
                self.assertIsNone(tone_rule(candidate), (product, layer_id))
        self.assertIsNone(tone_rule(None))

    def test_a_rule_needs_the_evalscript_it_was_tuned_for(self):
        tuned = layer("Sentinel-3 OLCI", "1_TRUE_COLOR")
        self.assertIsNone(tone_rule(dict(tuned, evalscript=tuned["evalscript"] + "\n// changed")))
        # Another theme's layer with the same id but another script gets no rule.
        others = [item for theme in ("FORESTRY",) for product in cop.products(theme)
                  for item in product["layers"]
                  if item.get("data_type") == "sentinel-3-olci" and item["id"] == "1_TRUE_COLOR"]
        self.assertTrue(others)
        self.assertTrue(all(tone_rule(item) is None for item in others))

    def test_a_layer_without_rule_renders_stock_tones(self):
        profile = dict(AUTO, brightness=130, contrast=140)
        stock = tone_profile(profile, layer("Sentinel-2 L2A", "2_FALSE_COLOR"))
        self.assertEqual((stock["brightness"], stock["contrast"], stock["auto_brightness"], stock["auto_contrast"]),
                         (100, 100, False, False))
        self.assertIs(tone_profile(profile, layer("Sentinel-2 L2A", "1_TRUE_COLOR")), profile)


class ToneRuleTests(unittest.TestCase):
    def test_every_rule_letter_has_its_building_blocks(self):
        self.assertEqual(sorted(TONE_RULES), list("abcdef"))
        self.assertFalse(TONE_RULES["a"]["brightness"])
        self.assertEqual(TONE_RULES["c"]["shoulder"], "clipped")
        self.assertTrue(TONE_RULES["d"]["bright_area"] and not TONE_RULES["d"]["soft_script"])
        self.assertEqual(TONE_RULES["e"]["shoulder"], "adaptive")
        self.assertTrue(TONE_RULES["f"]["bright_area"] and TONE_RULES["f"]["soft_script"])

    def test_rule_b_is_the_mosaic_rule(self):
        image = picture((40, 0.5), (90, 0.3), (150, 0.2))
        expected, _ = adjust(image, AUTO)
        result, record = apply_rule(image, AUTO, "b")
        self.assertEqual(list(result.getdata()), list(expected.getdata()))
        self.assertEqual((record["algorithm"], record["rule"], record["highlight_knee"]),
                         ("tone-rules-v1", "b", tones.HIGHLIGHT_KNEE))
        validate(record, AUTO)

    def test_rule_a_keeps_auto_brightness_neutral(self):
        dark = picture((10, 0.7), (40, 0.28), (90, 0.02))
        _, lifted = apply_rule(dark, AUTO, "b")
        self.assertLess(lifted["midtone_gamma"], 1.0)
        result, record = apply_rule(dark, AUTO, "a")
        self.assertEqual(record["midtone_gamma"], 1.0)
        self.assertTrue(record["auto_brightness"])
        validate(record, AUTO)

    def test_rule_e_keeps_scattered_cloud_and_large_bright_areas_apart(self):
        cloud = picture((40, 0.6), (80, 0.35), (160, 0.05))
        _, record = apply_rule(cloud, AUTO, "e")
        self.assertEqual(record["highlight_knee"], tones.ADAPTIVE_KNEE_RANGE[0])
        surface = picture((40, 0.4), (160, 0.6))
        _, record = apply_rule(surface, AUTO, "e")
        self.assertEqual(record["highlight_knee"], tones.ADAPTIVE_KNEE_RANGE[1])
        self.assertGreaterEqual(record["bright_share"], tones.BRIGHT_AREA_RANGE[1])
        validate(record, AUTO)

    def test_rule_e_reaches_white_only_at_the_brightest_value(self):
        image = picture((40, 0.6), (80, 0.35), (140, 0.04), (255, 0.01))
        fixed, _ = apply_rule(image, AUTO, "b")
        adaptive, _ = apply_rule(image, AUTO, "e")
        # The brightest source value reaches the target white, the others stay below
        # the fixed shoulder's level, so bright steps remain.
        self.assertEqual(max(adaptive.convert("L").getdata()), round(tones.WHITE_POINT * 255))
        self.assertLessEqual(
            sorted(set(adaptive.convert("L").getdata()))[-2], sorted(set(fixed.convert("L").getdata()))[-2])

    def test_rule_c_takes_the_adaptive_shoulder_only_for_a_nearly_clipped_large_bright_area(self):
        salt = picture((60, 0.45), (245, 0.55))
        rule_c, record_c = apply_rule(salt, AUTO, "c")
        rule_e, _ = apply_rule(salt, AUTO, "e")
        self.assertGreaterEqual(record_c["stretch_high"], tones.CLIPPED_TOP)
        self.assertEqual(list(rule_c.getdata()), list(rule_e.getdata()))
        sand = picture((40, 0.45), (180, 0.55))
        rule_c, record_c = apply_rule(sand, AUTO, "c")
        rule_b, _ = apply_rule(sand, AUTO, "b")
        self.assertLess(record_c["stretch_high"], tones.CLIPPED_TOP)
        self.assertEqual(list(rule_c.getdata()), list(rule_b.getdata()))
        self.assertEqual(record_c["highlight_knee"], tones.HIGHLIGHT_KNEE)

    def test_rules_d_and_f_halve_the_black_point_and_stop_darkening_for_a_large_bright_area(self):
        bright = picture((50, 0.3), (120, 0.1), (200, 0.6))
        _, plain = apply_rule(bright, AUTO, "b")
        self.assertGreater(plain["stretch_low"], 0)
        self.assertGreater(plain["midtone_gamma"], 1.0)
        for rule in ("d", "f"):
            _, record = apply_rule(bright, AUTO, rule)
            self.assertEqual(record["stretch_low"], round(plain["stretch_low"] / 2), rule)
            self.assertEqual(record["midtone_gamma"], 1.0, rule)
            validate(record, AUTO)
        # Scattered bright spots: d equals b.
        cloud = picture((50, 0.6), (90, 0.36), (200, 0.04))
        rule_d, _ = apply_rule(cloud, AUTO, "d")
        rule_b, _ = apply_rule(cloud, AUTO, "b")
        self.assertEqual(list(rule_d.getdata()), list(rule_b.getdata()))

    def test_a_clipped_source_is_not_stretched_by_any_rule(self):
        clipped = picture((60, 0.6), (255, 0.4))
        for rule in TONE_RULES:
            _, record = apply_rule(clipped, AUTO, rule)
            self.assertEqual((record["stretch_low"], record["stretch_high"]), (0, 255), rule)

    def test_regular_layers_get_their_manual_brightness_locally(self):
        manual = dict(AUTO, auto_brightness=False, auto_contrast=False, brightness=125)
        image = picture((100, 1.0))
        mosaic, mosaic_record = apply_rule(image, manual, "c", mosaic=True)
        scene, scene_record = apply_rule(image, manual, "e", mosaic=False)
        self.assertEqual(mosaic.getpixel((0, 0))[0], 100)  # The provider applies it.
        self.assertEqual(mosaic_record["evalscript_brightness_percent"], 125)
        self.assertGreater(scene.getpixel((0, 0))[0], 100)
        self.assertEqual((scene_record["midtone_gamma"], scene_record["evalscript_brightness_percent"]), (0.8, 100))
        validate(mosaic_record, manual)
        validate(scene_record, manual)

    def test_rule_records_are_validated(self):
        _, record = apply_rule(picture((40, 0.5), (150, 0.5)), AUTO, "e")
        for change in ({"rule": "z"}, {"highlight_knee": 2.0}, {"bright_share": -0.1}, {"midtone_gamma": "x"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(dict(record, **change), AUTO)
        with self.assertRaises(ValueError):
            validate({key: value for key, value in record.items() if key != "rule"}, AUTO)


class ToneRequestTests(unittest.TestCase):
    def frame(self, product, layer_id, **profile):
        target = layer(product, layer_id)
        return {"profile": dict(AUTO, **profile), "layer": target}, target

    def test_rule_f_requests_soft_highlights_only_with_auto_contrast(self):
        frame, target = self.frame("Landsat-8/9 L1", "1_TRUE_COLOR")
        self.assertIn("marblescapeSoft(val[0])", CopernicusClient._evalscript(frame, target))
        frame, target = self.frame("Landsat-8/9 L1", "1_TRUE_COLOR", auto_contrast=False)
        self.assertNotIn("marblescapeSoft", CopernicusClient._evalscript(frame, target))
        frame, target = self.frame("Landsat-8/9 L1", "3_TONEMAPPED_NATURAL_COLOR")
        self.assertNotIn("marblescapeSoft", CopernicusClient._evalscript(frame, target))

    def test_olci_enhanced_natural_color_keeps_its_data_mask_exact(self):
        frame, target = self.frame("Sentinel-3 OLCI", "1_TRUE_COLOR_ENHANCED")
        script = CopernicusClient._evalscript(frame, target)
        self.assertIn("viz.processList(values.slice(0, 3))", script)
        self.assertIn("samples.dataMask]", script)
        frame, target = self.frame("Sentinel-3 OLCI", "1_TRUE_COLOR")
        self.assertNotIn("values.slice(0, 3)", CopernicusClient._evalscript(frame, target))

    def test_a_mosaic_without_rule_is_requested_at_stock_brightness(self):
        manual = {"auto_brightness": False, "auto_contrast": False, "brightness": 150}
        frame, target = self.frame("Sentinel-2 Quarterly Mosaics", "FALSE_COLOR_CLOUDLESS", **manual)
        stock = CopernicusClient._evalscript(frame, target)
        frame, target = self.frame("Sentinel-2 Quarterly Mosaics", "TRUE_COLOR_CLOUDLESS", **manual)
        tuned = CopernicusClient._evalscript(frame, target)
        self.assertIn("brightness = 1.50", tuned)
        self.assertNotIn("brightness = 1.50", stock)


class ToneSignatureTests(unittest.TestCase):
    def test_cache_keys_change_only_where_the_tones_differ(self):
        import marblescape_download as app
        tuned = layer("Sentinel-2 L2A", "1_TRUE_COLOR")
        stock = dict(AUTO, auto_brightness=False, auto_contrast=False)
        self.assertEqual(app.copernicus_tone_rule_signature(AUTO, tuned), (("tone_rule", "tone-rules-v1", "e"),))
        self.assertEqual(app.copernicus_tone_rule_signature(stock, tuned), ())
        # A regular layer without a rule rendered no tones before either.
        self.assertEqual(app.copernicus_tone_rule_signature(AUTO, layer("Sentinel-2 L2A", "2_FALSE_COLOR")), ())
        mosaic = layer("Sentinel-2 Quarterly Mosaics", "FALSE_COLOR_CLOUDLESS")
        self.assertEqual(app.copernicus_tone_rule_signature(AUTO, mosaic), (("tone_rule", "tone-rules-v1", "stock"),))
        enhanced = layer("Sentinel-3 OLCI", "1_TRUE_COLOR_ENHANCED")
        self.assertIn(("data_mask", "exact"), app.copernicus_tone_rule_signature(stock, enhanced))


if __name__ == "__main__":
    unittest.main()
