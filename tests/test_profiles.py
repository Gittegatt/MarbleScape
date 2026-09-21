"""Profile persistence and rotation tests; all data and clocks stay in memory."""
from copy import deepcopy
import datetime
import math
import tomllib
import unittest
from unittest.mock import patch
from marblescape_profiles import (MAX_INPUT_BYTES, RotationScheduler, new_profile_id,
                                 normalize_library, read_library, remove_library,
                                 replace_library, serialize_library, toml_value)

FIRST, SECOND, THIRD = "1" * 32, "2" * 32, "3" * 32


def profile(identifier=FIRST, name="Earth", provider="eumetsat"):
    return {"id": identifier, "name": name, "settings": {
        "source": {"provider": provider},
        "sources": {"solar": {"area": "sun", "product": "Fe171", "resolution": "1200x1200"}},
        "view": {"fit_mode": "fit", "zoom": 1.1, "bbox": []},
        "output": {"width": 2560, "height": 0, "aspect_ratio": "16:9"},
        "layers": [{"name": "mtg_fd:rgb_geocolour", "enabled": True, "opacity": 0.75,
                    "time": "2026-09-12T12:00:00Z", "style": ""}],
        "service": {"time": "", "render_mode": "auto"}}}


def library(enabled=True, order=None, interval=15, unit="minutes"):
    result = {"items": [profile(), profile(SECOND, "Sun", "solar")],
              "rotation": {"enabled": enabled, "interval": interval, "unit": unit}}
    if order is not None:
        result["rotation"]["order"] = order
    return normalize_library(result)


class FakeClock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class ProfileLibraryTests(unittest.TestCase):
    def test_old_config_defaults_and_independent_snapshots(self):
        expected = {"version": 1, "items": [], "rotation": {
            "enabled": False, "interval": 15, "unit": "minutes", "order": []}}
        self.assertEqual(read_library({"source": {"provider": "eumetsat"}}), expected)
        original = library()
        copied = normalize_library(original)
        original["items"][0]["settings"]["layers"][0]["opacity"] = 0.1
        self.assertEqual(copied["items"][0]["settings"]["layers"][0]["opacity"], 0.75)
        self.assertEqual(copied["rotation"]["order"], [FIRST, SECOND])

    def test_names_ids_and_count_are_bounded_and_unique(self):
        for name in ("", "  ", "x" * 81, "line\nbreak"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                normalize_library({"items": [profile(name=name)]})
        for names in (("Earth", "eArTh"), ("Straße", "STRASSE")):
            with self.subTest(names=names), self.assertRaises(ValueError):
                normalize_library({"items": [profile(name=names[0]), profile(SECOND, names[1])]})
        self.assertEqual(normalize_library({"items": [profile(name="  Earth  ")]})["items"][0]["name"], "Earth")
        upper = "ABCDEF12" * 4
        self.assertEqual(normalize_library({"items": [profile(upper)]})["items"][0]["id"], upper.lower())
        with self.assertRaises(ValueError):
            normalize_library({"items": [profile(upper), profile(upper.lower(), "Other")]})
        for identifier in ("id", "g" * 32, "1" * 31, None, 42):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                normalize_library({"items": [profile(identifier)]})
        self.assertRegex(new_profile_id(), r"^[0-9a-f]{32}$")
        items = [profile(f"{index:032x}", f"Profile {index}") for index in range(100)]
        self.assertEqual(len(normalize_library({"items": items})["items"]), 100)
        with self.assertRaises(ValueError):
            normalize_library({"items": items + [profile(THIRD, "Extra")]})

    def test_unknown_fields_and_invalid_section_shapes_are_rejected(self):
        for value in (None, [], {"version": 2}, {"version": True}, {"unknown": 1}, {"items": {}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_library(value)
        for settings in ({"windows": {}}, {"source": []}, {"layers": {}}, {"layers": [1]}, {"view": None}):
            item = profile()
            item["settings"] = settings
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                normalize_library({"items": [item]})

    def test_values_reject_nonfinite_unsupported_oversized_and_cyclic_data(self):
        for value in (None, math.nan, math.inf, -math.inf, 2**63, -(2**63)-1,
                      (1, 2), datetime.date(2026, 9, 12), "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                toml_value({"value": value})
        with self.assertRaises(ValueError):
            toml_value("x" * (MAX_INPUT_BYTES + 1))
        item = profile()
        item["settings"]["view"]["description"] = "x" * MAX_INPUT_BYTES
        with self.assertRaises(ValueError):
            normalize_library({"items": [item]})
        cyclic = {}
        cyclic["self"] = cyclic
        with self.assertRaises(ValueError):
            toml_value(cyclic)

    def test_rotation_values_and_subset_order(self):
        for rotation in ({"enabled": 1}, {"interval": 0}, {"interval": True}, {"interval": 1.5},
                         {"interval": 525601}, {"unit": "hours"}, {"order": FIRST},
                         {"order": [FIRST, FIRST]}, {"order": [THIRD]}, {"extra": False}):
            with self.subTest(rotation=rotation), self.assertRaises(ValueError):
                normalize_library({"items": [profile()], "rotation": rotation})
        self.assertEqual(library(order=[SECOND])["rotation"]["order"], [SECOND])
        self.assertEqual(library(order=[])["rotation"]["order"], [])

    def test_inline_serializer_preserves_escaped_keys_values_and_layer_arrays(self):
        value = {"with.dot": {"text": 'Quotes " slash \\ newline\n☀', "bool": True,
                              "off": False, "number": -0.25, "max": 2**63-1,
                              "min": -(2**63), "list": [1, 2, "latest"]}}
        encoded = toml_value(value)
        self.assertNotIn("\n", encoded)
        self.assertEqual(tomllib.loads("value = " + encoded)["value"], value)
        layers = profile()["settings"]["layers"]
        self.assertEqual(tomllib.loads("layers = " + toml_value(layers))["layers"], layers)

    def test_append_and_replace_keep_unrelated_settings_and_comments(self):
        original = '# Keep\n[service]\nendpoint = "https://example.invalid/wms"\n\n[[layers]]\nname = "original:layer"\n'
        result = replace_library(original, library())
        self.assertTrue(result.startswith(original))
        self.assertEqual(read_library(tomllib.loads(result)), library())
        self.assertEqual(tomllib.loads(result)["layers"], [{"name": "original:layer"}])
        repeated = replace_library(result, library())
        self.assertEqual(read_library(tomllib.loads(repeated)), library())
        self.assertEqual(repeated.count("[image_profiles]"), 1)

    def test_standalone_document_and_legacy_removal_roundtrip(self):
        standalone = serialize_library(library())
        self.assertEqual(set(tomllib.loads(standalone)), {"image_profiles"})
        self.assertEqual(read_library(tomllib.loads(standalone)), library())
        original = (
            '# Keep\n[source]\nprovider = "eumetsat"\n\n' + standalone
            + '\n[output]\nwidth = 2560\n'
        )
        updated = remove_library(original)
        self.assertNotIn("image_profiles", tomllib.loads(updated))
        self.assertEqual(
            tomllib.loads(updated),
            {"source": {"provider": "eumetsat"}, "output": {"width": 2560}},
        )
        self.assertIn("# Keep", updated)

    def test_crlf_preserved_outside_and_inside_replacement(self):
        before = '# Before\r\n[source]\r\nprovider = "eumetsat"\r\n\r\n'
        after = '[output]\r\nwidth = 3840 # Keep\r\n'
        old = before + '[image_profiles]\r\nversion = 1\r\nitems = []\r\n\r\n' + after
        result = replace_library(old, library())
        self.assertTrue(result.startswith(before))
        self.assertTrue(result.endswith(after))
        self.assertNotIn("\n", result.replace("\r\n", ""))
        self.assertEqual(read_library(tomllib.loads(result)), library())

    def test_nested_profile_tables_and_interleaved_unrelated_tables(self):
        old = ('[image_profiles]\nversion = 1\n[output]\nwidth = 100 # Keep\n\n'
               '[[image_profiles.items]]\nid = "' + FIRST + '"\nname = "Before"\n'
               '[image_profiles.items.settings.source]\nprovider = "solar"\n'
               '[image_profiles.rotation]\nenabled = false\n')
        result = replace_library(old, library())
        self.assertIn('[output]\nwidth = 100 # Keep\n\n', result)
        self.assertNotIn("[[image_profiles.items]]", result)
        self.assertEqual(read_library(tomllib.loads(result)), library())

    def test_table_text_in_strings_or_arrays_is_not_a_table(self):
        original = ('[service]\nnote = """Keep\n[image_profiles]\nitems = []\n"""\n'
                    'values = [\n ["image_profiles"],\n [1, 2],\n]\n'
                    '["image_profiles"] # Actual\nversion = 1\nitems = []\n')
        result = replace_library(original, library())
        self.assertEqual(tomllib.loads(result)["service"], tomllib.loads(original)["service"])
        self.assertEqual(read_library(tomllib.loads(result)), library())
        for quote in ('"', "'"):
            for count in (3, 4, 5):
                original = '[service]\nnote = ' + quote*3 + 'text' + quote*count + '\n[image_profiles]\nitems = []\n'
                result = replace_library(original, library())
                self.assertEqual(tomllib.loads(result)["service"], tomllib.loads(original)["service"])

    def test_invalid_or_excessive_toml_rejected_and_no_file_io(self):
        for text in ('[invalid', 'image_profiles = {}\n', '# ' + 'x' * MAX_INPUT_BYTES):
            with self.subTest(text=text[:30]), self.assertRaises(ValueError):
                replace_library(text, library())
        with patch("builtins.open", side_effect=AssertionError("Unexpected file I/O")):
            self.assertEqual(read_library(tomllib.loads(replace_library('', library()))), library())


class RotationSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.library = library(order=[SECOND, FIRST])
        self.scheduler = RotationScheduler(self.library, clock=self.clock)

    def test_disabled_or_empty_schedule_never_due(self):
        for value in (library(enabled=False), library(order=[]), {}):
            scheduler = RotationScheduler(value, clock=self.clock)
            self.assertFalse(scheduler.due())
            self.assertIsNone(scheduler.deadline)
            self.assertIsNone(scheduler.start_next())
            self.clock.advance(100000)
            self.assertFalse(scheduler.due())

    def test_order_and_full_interval_begin_after_success(self):
        self.assertTrue(self.scheduler.due())
        self.assertEqual(self.scheduler.start_next()["id"], SECOND)
        self.assertEqual(self.scheduler.current, SECOND)
        self.assertFalse(self.scheduler.due())
        self.assertIsNone(self.scheduler.start_next())
        self.clock.advance(30)
        self.scheduler.success()
        self.assertEqual(self.scheduler.deadline, 1030)
        self.assertIsNone(self.scheduler.active_profile)
        self.clock.advance(899)
        self.assertFalse(self.scheduler.due())
        self.clock.advance(1)
        self.assertEqual(self.scheduler.start_next()["id"], FIRST)
        self.scheduler.success()
        self.clock.advance(900)
        self.assertEqual(self.scheduler.start_next()["id"], SECOND)

    def test_three_failures_skip_then_all_failed_pass_waits(self):
        for identifier, last_result in ((SECOND, "skip"), (FIRST, "wait")):
            pending = self.scheduler.start_next()
            self.assertEqual(pending["id"], identifier)
            for attempt in (1, 2):
                self.assertEqual(self.scheduler.failure(), "retry")
                self.assertEqual(self.scheduler.attempts, attempt)
                self.assertEqual(self.scheduler.active_profile, pending)
                self.assertIsNone(self.scheduler.start_next())
            self.assertEqual(self.scheduler.failure(), last_result)
            self.assertEqual(self.scheduler.attempts, 3)
            self.assertIsNone(self.scheduler.active_profile)
        self.clock.advance(899)
        self.assertFalse(self.scheduler.due())
        self.clock.advance(1)
        self.assertEqual(self.scheduler.start_next()["id"], SECOND)

    def test_single_profile_waits_after_third_failure(self):
        scheduler = RotationScheduler(library(order=[FIRST]), clock=self.clock)
        scheduler.start_next()
        self.assertEqual([scheduler.failure() for _ in range(3)], ["retry", "retry", "wait"])
        self.assertFalse(scheduler.due())

    def test_user_cancel_does_not_count_as_failure_or_retry_immediately(self):
        pending = self.scheduler.start_next()
        self.scheduler.failure()
        self.assertEqual(self.scheduler.attempts, 1)
        self.scheduler.cancel()
        self.assertEqual(self.scheduler.attempts, 0)
        self.assertIsNone(self.scheduler.active_profile)
        self.assertFalse(self.scheduler.due())
        self.clock.advance(900)
        self.assertEqual(self.scheduler.start_next()["id"], pending["id"])

    def test_success_clears_previous_failures(self):
        self.scheduler.start_next()
        self.assertEqual([self.scheduler.failure() for _ in range(3)][-1], "skip")
        self.scheduler.start_next()
        self.scheduler.success()
        self.clock.advance(900)
        self.scheduler.start_next()
        self.assertEqual([self.scheduler.failure() for _ in range(3)][-1], "skip")
        self.scheduler.start_next()
        self.assertEqual([self.scheduler.failure() for _ in range(3)][-1], "wait")

    def test_snapshots_are_not_shared_with_caller(self):
        pending = self.scheduler.start_next()
        pending["settings"]["source"]["provider"] = "goes_east"
        self.library["items"][1]["settings"]["source"]["provider"] = "goes_west"
        exposed = self.scheduler.library
        exposed["rotation"]["order"].clear()
        self.assertEqual(self.scheduler.active_profile["settings"]["source"]["provider"], "solar")
        self.assertEqual(self.scheduler.library["rotation"]["order"], [SECOND, FIRST])

    def test_cosmetic_changes_preserve_deadline_retry_state_and_current(self):
        self.scheduler.start_next()
        self.scheduler.failure()
        cosmetic = deepcopy(self.library)
        cosmetic["items"][1]["name"] = "Renamed Sun"
        cosmetic["items"].reverse()
        deadline = self.scheduler.deadline
        self.assertFalse(self.scheduler.configure(cosmetic))
        self.assertEqual(self.scheduler.current_id, SECOND)
        self.assertEqual(self.scheduler.attempts, 1)
        self.assertEqual(self.scheduler.deadline, deadline)
        self.scheduler.success()
        deadline = self.scheduler.deadline
        self.clock.advance(25)
        self.assertFalse(self.scheduler.configure(cosmetic))
        self.assertEqual(self.scheduler.deadline, deadline)

    def test_meaningful_changes_reset_but_invalid_change_is_atomic(self):
        variants = []
        for key, value in (("enabled", False), ("interval", 2), ("unit", "days"), ("order", [FIRST, SECOND])):
            changed = deepcopy(self.library)
            changed["rotation"][key] = value
            variants.append(changed)
        changed = deepcopy(self.library)
        changed["items"][0]["settings"]["output"]["width"] = 1920
        variants.append(changed)
        for changed in variants:
            scheduler = RotationScheduler(self.library, clock=self.clock)
            scheduler.start_next()
            scheduler.failure()
            self.assertTrue(scheduler.configure(changed))
            self.assertIsNone(scheduler.current)
            self.assertIsNone(scheduler.active_profile)
            self.assertEqual(scheduler.attempts, 0)
            self.assertEqual(scheduler.due(), changed["rotation"]["enabled"])
        self.scheduler.start_next()
        self.scheduler.failure()
        invalid = deepcopy(self.library)
        invalid["rotation"]["interval"] = 0
        with self.assertRaises(ValueError):
            self.scheduler.configure(invalid)
        self.assertEqual(self.scheduler.current, SECOND)
        self.assertEqual(self.scheduler.attempts, 1)

    def test_unit_conversion_and_missing_active_result(self):
        for unit, seconds in (("minutes", 60), ("days", 86400), ("weeks", 604800)):
            scheduler = RotationScheduler(library(interval=2, unit=unit), clock=self.clock)
            scheduler.start_next()
            scheduler.success()
            self.assertEqual(scheduler.deadline, self.clock.now + 2 * seconds)
        with self.assertRaises(RuntimeError):
            self.scheduler.success()
        with self.assertRaises(RuntimeError):
            self.scheduler.failure()


if __name__ == "__main__":
    unittest.main()
