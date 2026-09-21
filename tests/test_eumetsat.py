"""EUMETView catalogue normalization tests without live network access."""

import unittest

from marblescape_eumetsat import (
    DEFAULT_LAYER,
    DEFAULT_PROFILE,
    EumetsatCatalogueError,
    EumetsatSettings,
    build_catalogue,
    normalize_profile,
    supports_gap_fill,
)


class EumetsatCatalogueTests(unittest.TestCase):
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
        self.assertEqual(set(catalogue[0]["themes"]), {
            "atmospheric_composition", "climate", "emergency", "marine",
            "weather_monitoring",
        })
        self.assertFalse(catalogue[0]["single_overpass"])

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
