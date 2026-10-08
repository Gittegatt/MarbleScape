"""Every image source keeps all of its non-default choices through each transfer path.

Defaults would hide a dropped field (it would silently come back as the default),
so each source uses values that differ from the shipped configuration.
"""

from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
from marblescape_image_metadata import SOURCE_LABELS, embed_png_metadata
from marblescape_profile_transfer import export_profiles, import_profiles, portable_settings


EUMETSAT_LAYER = "eps:m01_avhrr_ch4"
SOURCE_CHOICES = {
    "eumetsat": {
        "theme": "marine", "satellite": "Metop-B", "mission": "EPS",
        "product_type": "Single channel", "layer": EUMETSAT_LAYER, "orbit_type": "LEO",
        "fill_gaps": True, "gap_fill_lookback_hours": 24,
    },
    "goes_east": {"area": "conus", "product": "Sandwich", "resolution": "2500x1500"},
    "goes_west": {"area": "pacific_us", "product": "AirMass", "resolution": "largest"},
    "solar": {"area": "sun", "product": "Fe304", "resolution": "1024x1024"},
    "himawari": {"area": "nict_full_disk_bands", "product": "B13", "resolution": "largest",
                 "shorelines": True, "shoreline_color": "#00FF00",
                 "center": True, "latitude": 35.68, "longitude": 139.77},
    "slider": {"area": "himawari---full_disk", "product": "band_13", "resolution": "2712x2712"},
    "worldview": {"area": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
                  "product": "2026-09-30", "resolution": "4096x2048"},
    "copernicus": {
        "latitude": 47.25, "longitude": 11.4, "map_zoom": 9, "map_labels": False,
        "map_label_color": "#FF0000", "map_borders": True, "map_border_color": "#00FF00",
        "brightness": 120, "contrast": 110,
        "auto_brightness": True, "auto_contrast": True, "image_size": "2560x1440",
        "no_data_color": "transparent", "date_mode": "relative_quarter", "quarter_offset": 2,
    },
}
EUMETSAT_LAYERS = [
    {"kind": "basemap", "name": "OSM Dark", "enabled": True, "opacity": 0.5, "style": ""},
    {"kind": "wms", "name": EUMETSAT_LAYER, "enabled": True, "opacity": 0.75,
     "style": "custom_style", "time": "2026-09-30T12:00:00Z"},
    {"kind": "overlay", "name": "Coastlines", "enabled": False, "opacity": 0.4, "style": ""},
]


def source_settings(provider):
    """A complete Image profile whose source, view and render quality differ from defaults.

    The device output settings of an older profile are given too: they must be dropped.
    """
    settings = app.default_import_settings()
    settings["source"]["provider"] = provider
    settings["sources"][provider].update(SOURCE_CHOICES[provider])
    settings["view"].update(fit_mode="crop", zoom=1.75, truecolor_black_night=False)
    settings["output"].update(width=1600, height=0, aspect_ratio="16:10",
                              background_color="#102030", render_scale=2.0)
    if provider == "eumetsat":
        settings["view"].update(projection="Geographic", preset="custom", bbox=[-10.0, 30.0, 40.0, 70.0])
        settings["layers"] = deepcopy(EUMETSAT_LAYERS)
    return app.normalize_image_settings_snapshot(settings)


def png_bytes(size=(8, 6)):
    raw = io.BytesIO()
    Image.new("RGB", size).save(raw, format="PNG")
    return raw.getvalue()


class SourceRoundtripTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def read(self, paths):
        return import_profiles(paths, [], app.default_import_settings,
                               app.normalize_image_settings_snapshot)

    def assert_choices_kept(self, provider, settings):
        source = settings["sources"][provider]
        for key, value in SOURCE_CHOICES[provider].items():
            self.assertEqual(source[key], value, f"{provider} lost {key}")
        self.assertEqual(settings["view"]["fit_mode"], "crop")
        self.assertEqual(settings["view"]["zoom"], 1.75)
        self.assertIs(settings["view"]["truecolor_black_night"], False)
        self.assertEqual(settings["output"], {"render_scale": 2.0},
                         "Output size, background and Latest folder are device settings")
        if provider == "eumetsat":
            self.assertEqual(settings["view"]["projection"], "Geographic")
            self.assertEqual(settings["view"]["preset"], "custom")
            self.assertEqual(settings["view"]["bbox"], [-10.0, 30.0, 40.0, 70.0])
            self.assertEqual(settings["layers"], EUMETSAT_LAYERS)

    def test_fixture_choices_survive_normalization(self):
        # Guards the test itself: a rejected or reset choice would weaken every roundtrip.
        for provider in SOURCE_CHOICES:
            with self.subTest(source=provider):
                self.assert_choices_kept(provider, source_settings(provider))

    def test_profile_json_export_and_import_keep_every_choice(self):
        for index, provider in enumerate(SOURCE_CHOICES, 1):
            with self.subTest(source=provider):
                settings = source_settings(provider)
                item = {"id": f"{index:032x}", "name": f"{provider} roundtrip", "settings": settings}
                path = export_profiles(self.root, [item], app.normalize_image_settings_snapshot)[0]
                restored, failures = self.read([path])
                self.assertFalse(failures)
                self.assertEqual(restored[0]["id"], item["id"])
                self.assertEqual(restored[0]["settings"],
                                 portable_settings(settings, app.normalize_image_settings_snapshot))
                self.assert_choices_kept(provider, restored[0]["settings"])

    def test_generated_png_metadata_imports_every_choice(self):
        # Copernicus provenance needs a resolved catalogue frame; test_profile_transfer covers it.
        for index, provider in enumerate((p for p in SOURCE_CHOICES if p != "copernicus"), 1):
            with self.subTest(source=provider):
                settings = source_settings(provider)
                item = {"id": f"{index:032x}", "name": f"{provider} 海 image", "settings": settings}
                with patch.object(app, "IMAGE_SOURCE", provider), \
                     patch.object(app, "SOURCE_PROFILES", deepcopy(settings["sources"])), \
                     patch.object(app, "LAYER_CONFIG", deepcopy(settings["layers"])), \
                     patch.object(app, "IMAGE_PROFILE_LIBRARY", {"items": [item]}), \
                     patch.object(app, "image_settings_snapshot", return_value=deepcopy(settings)):
                    record = app.image_provenance(provider, [], (8, 6),
                                                  source_time="2026-09-30T12:00:00Z",
                                                  profile_id=item["id"])
                self.assertEqual(record["source"], SOURCE_LABELS[provider])
                self.assertEqual(record["source_time_utc"], "2026-09-30T12:00:00Z")
                for key in ("area", "product", "resolution", "layer", "theme", "satellite", "mission"):
                    if key in SOURCE_CHOICES[provider]:
                        self.assertEqual(record[key], SOURCE_CHOICES[provider][key])
                if provider == "eumetsat":
                    self.assertEqual(record["layers"], ["OSM Dark", EUMETSAT_LAYER])
                path = self.root / f"{provider}.png"
                path.write_bytes(embed_png_metadata(png_bytes(), record))
                restored, failures = self.read([path])
                self.assertFalse(failures)
                self.assertEqual((restored[0]["id"], restored[0]["name"]), (item["id"], item["name"]))
                self.assertEqual(restored[0]["settings"], record["profile_settings"])
                self.assert_choices_kept(provider, restored[0]["settings"])
                # A PNG records the dimensions it was actually rendered at,
                # outside the profile settings.
                self.assertEqual((record["width"], record["height"]), (8, 6))

    def test_full_settings_backup_restores_every_source_profile(self):
        items = [{"id": f"{index:032x}", "name": f"{provider} backup",
                  "settings": source_settings(provider)}
                 for index, provider in enumerate(SOURCE_CHOICES, 1)]
        config = self.root / "marblescape_config.toml"
        config.write_bytes(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_bytes())
        profiles = self.root / "profiles.toml"
        profiles.write_text(app.serialize_library(app.normalize_library({"items": items})),
                            encoding="utf-8")
        with patch.object(app, "ACTIVE_CONFIG_PATH", config), \
             patch.object(app, "ACTIVE_PROFILE_LIBRARY_PATH", profiles), \
             patch.object(app, "is_windows_startup_enabled", return_value=False):
            saved = app.export_settings_backup(self.root / "backup.json")
            stored = app.read_profile_library_file()
        _config, library, _startup = app.import_settings_backup(saved)
        self.assertEqual(library["items"], stored["items"])
        for item, provider in zip(library["items"], SOURCE_CHOICES):
            with self.subTest(source=provider):
                self.assertEqual(item["settings"]["source"]["provider"], provider)
                self.assert_choices_kept(provider, item["settings"])


if __name__ == "__main__":
    unittest.main()
