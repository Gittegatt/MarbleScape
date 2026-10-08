"""EUMETSAT catalogue normalization tests without live network access."""

import unittest

from marblescape_eumetsat import (
    DEFAULT_LAYER,
    DEFAULT_PROFILE,
    EumetsatCatalogueError,
    EumetsatSettings,
    _preferred,
    build_catalogue,
    normalize_profile,
    supports_gap_fill,
)


class EumetsatCatalogueTests(unittest.TestCase):
    def test_natural_color_selection_handles_spaced_names_and_preserves_saved_layer(self):
        products = [
            {"label": "Vegetation index", "layer": "example:ndvi"},
            {"label": "Natural Colour RGB", "layer": "example:natural"},
            {"label": "True Colour RGB", "layer": "example:true"},
        ]
        self.assertEqual(_preferred(products)["layer"], "example:true")
        self.assertEqual(_preferred(products, "example:ndvi")["layer"], "example:ndvi")

    def test_official_product_metadata_builds_dependent_choices_and_themes(self):
        decorations = {
            "EO:EUM:DAT:TEST": {"wmsConfig": {"layer": "mtg_fd:rgb_geocolour"}},
            "EO:EUM:DAT:NO-WMS": {},
        }
        search = {"hits": {"hits": [
            {"_source": {
                "id": "EO:EUM:DAT:TEST",
                "datasetTitle": "GeoColour RGB",
                "orbitType": "GEO",
                "satellite": "MTG",
                "hierarchyLevelName": [
                    "view.Satellite.MTG 0DEG.RGBs",
                    "view.Theme.Atmospheric Composition",
                    "theme.par.Thematic_Climate_Data_Record",
                    "theme.par.Fire",
                    "view.Theme.Ocean",
                    "view.Theme.Weather",
                ],
            }},
            {"_source": {
                "id": "EO:EUM:DAT:NO-WMS",
                "datasetTitle": "Not visualizable",
                "hierarchyLevelName": ["view.Satellite.MSG 0DEG.Products"],
            }},
        ]}}

        catalogue = build_catalogue(decorations, search)

        self.assertEqual(len(catalogue), 1)
        self.assertEqual(catalogue[0]["satellite"], "MTG - 0 Degree")
        self.assertEqual(catalogue[0]["mission"], "MTG")
        self.assertEqual(catalogue[0]["product_type"], "RGB Composites")
        self.assertEqual(catalogue[0]["layer"], DEFAULT_LAYER)
        # Only the viewer's Themes menu counts; Data Store themes are ignored.
        self.assertEqual(set(catalogue[0]["themes"]), {
            "atmospheric_composition", "marine", "weather_monitoring",
        })
        self.assertFalse(catalogue[0]["single_overpass"])

    def test_themes_match_the_viewer_and_announced_products_are_not_selectable(self):
        from marblescape_eumetsat import THEMES, announced_label, infer_orbit_type, is_available

        self.assertEqual([label for _key, label in THEMES],
                         ["All data themes", "Atmosphere", "Ocean", "Weather"])
        decorations = {
            "EO:EUM:DAT:CLOUDS": {"wmsConfig": {"layer": "mtg_fd:rgb_cloudphase"}},
            "EO:EUM:DAT:EPSSG01": {"wmsConfig": {"layer": "epssg:m01_metimage_ir1069"}},
        }
        search = {"hits": {"hits": [{"_source": {
            "id": "EO:EUM:DAT:CLOUDS",
            "datasetTitle": "Cloud Phase RGB - MTG - 0 degree",
            "hierarchyLevelName": ["view.Satellite.MTG 0DEG.RGBs", "theme.par.Clouds"],
        }}]}}
        catalogue = build_catalogue(decorations, search)
        selectable = [item for item in catalogue if is_available(item)]
        announced = [item for item in catalogue if not is_available(item)]
        # Without a viewer theme a product is found only under All data themes, as in the viewer.
        self.assertEqual([item["themes"] for item in selectable], [()])
        self.assertEqual([item["layer"] for item in announced], ["epssg:m01_metimage_ir1069"])
        self.assertEqual(announced_label(announced[0]), "Metop-SG m01_metimage_ir1069")
        self.assertEqual(infer_orbit_type("epssg:m01_metimage_ir1069"), "LEO")
        # Announced products alone are no usable catalogue.
        with self.assertRaises(EumetsatCatalogueError):
            build_catalogue({"EO:EUM:DAT:EPSSG01": decorations["EO:EUM:DAT:EPSSG01"]},
                            {"hits": {"hits": []}})
        # Saved themes the viewer no longer offers fall back to all themes.
        for theme in ("climate", "emergency"):
            self.assertEqual(normalize_profile({"theme": theme})["theme"], "all")
        self.assertEqual(normalize_profile({"theme": "marine"})["theme"], "marine")

    def test_invalid_or_empty_catalogue_is_rejected(self):
        with self.assertRaises(EumetsatCatalogueError):
            build_catalogue([], {})
        with self.assertRaises(EumetsatCatalogueError):
            build_catalogue({}, {"hits": {"hits": []}})

    def test_catalogue_infers_known_leo_orbit_when_metadata_omits_it(self):
        decorations = {
            "EO:EUM:DAT:S3": {"wmsConfig": {
                "layer": "copernicus:sentinel3a_olci_l1_rgb_fullres"
            }},
        }
        search = {"hits": {"hits": [{"_source": {
            "id": "EO:EUM:DAT:S3",
            "datasetTitle": "OLCI Level 1B RGB - Sentinel-3A",
            "hierarchyLevelName": [
                "view.Satellite.Sentinel 3A.RGBs",
                "view.Theme.Ocean",
            ],
        }}]}}

        item = build_catalogue(decorations, search)[0]

        self.assertEqual(item["orbit_type"], "LEO")
        self.assertTrue(item["single_overpass"])

    def test_profile_defaults_to_mtg_geocolour_and_preserves_saved_layer(self):
        self.assertEqual(normalize_profile({}), DEFAULT_PROFILE)
        profile = normalize_profile({
            "theme": "marine",
            "satellite": "Sentinel-3A",
            "mission": "Sentinel-3",
            "product_type": "Visualized Products",
            "layer": "sentinel_test:sea_surface_temperature",
            "orbit_type": "LEO",
            "fill_gaps": True,
            "gap_fill_lookback_hours": 24,
        })
        self.assertEqual(profile["theme"], "marine")
        self.assertEqual(profile["layer"], "sentinel_test:sea_surface_temperature")
        self.assertTrue(profile["fill_gaps"])
        self.assertEqual(profile["gap_fill_lookback_hours"], 24)

    def test_new_configurations_default_to_mtg_geocolour_in_the_rss_view(self):
        import marblescape_download as app

        expected_source = {"theme": "all", "satellite": "MTG - 0 Degree", "mission": "MTG",
                           "product_type": "RGB Composites", "layer": "mtg_fd:rgb_geocolour"}
        expected_view = {"projection": "GEOS: MSG RSS", "fit_mode": "fit", "zoom": 1.0,
                         "preset": "full_earth", "truecolor_black_night": True}
        defaults = app.default_import_settings()
        self.assertEqual(defaults["source"]["provider"], "eumetsat")
        for key, value in expected_source.items():
            self.assertEqual(DEFAULT_PROFILE[key], value)
            self.assertEqual(defaults["sources"]["eumetsat"][key], value)
        for key, value in expected_view.items():
            self.assertEqual(defaults["view"][key], value)
        # Choosing Full Earth again returns to the same view.
        self.assertEqual(app.VIEW_PRESET_PROFILES["full_earth"]["projection"], "GEOS: MSG RSS")
        self.assertIn("GEOS: MSG RSS", app.PROJECTIONS)

    def test_gap_fill_is_limited_to_non_accumulated_leo_passes(self):
        self.assertTrue(supports_gap_fill(
            "copernicus:sentinel3a_olci_l1_rgb_fullres", "LEO",
            "OLCI Level 1B RGB - Sentinel-3A",
        ))
        self.assertTrue(supports_gap_fill(
            "eps:m02_ascat_wind", "LEO",
            "ASCAT Coastal Winds at 12.5 km Swath Grid - Metop-A",
        ))
        self.assertFalse(supports_gap_fill(
            "copernicus:daily_sentinel3ab_olci_l1_rgb_fulres", "LEO",
            "OLCI Level 1B RGB Daily Accumulated - Sentinel-3",
        ))
        self.assertFalse(supports_gap_fill(
            "mtg_fd:rgb_geocolour", "GEO", "GeoColour RGB",
        ))
        self.assertFalse(supports_gap_fill(
            "copernicus:sentinel3a_slstr_level2_frp", "LEO",
            "SLSTR Level 2 Fire Radiative Power (FRP) - Sentinel-3A",
        ))

    def test_invalid_gap_fill_values_are_rejected_and_geo_disables_it(self):
        with self.assertRaises(ValueError):
            normalize_profile({"fill_gaps": "yes"})
        with self.assertRaises(ValueError):
            normalize_profile({"gap_fill_lookback_hours": 18})
        profile = normalize_profile({"fill_gaps": True})
        self.assertFalse(profile["fill_gaps"])

    def test_catalogue_relationships_exclude_incompatible_dropdown_choices(self):
        settings = EumetsatSettings.__new__(EumetsatSettings)
        settings._profile = dict(DEFAULT_PROFILE)
        settings._catalogue = [
            {
                "themes": ("marine",), "satellite": "Sentinel-3A",
                "mission": "Sentinel-3", "product_type": "RGB Composites",
            },
            {
                "themes": ("weather_monitoring",), "satellite": "Metop A",
                "mission": "Metop", "product_type": "Visualized Products",
            },
        ]
        matches = settings._matching(
            theme="marine", satellite="Sentinel-3A", mission="Sentinel-3",
            product_type="RGB Composites",
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            settings._matching(
                theme="marine", satellite="Sentinel-3A", mission="Metop"
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
