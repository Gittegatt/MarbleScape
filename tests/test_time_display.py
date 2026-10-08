"""Time-zone display formatting without changing persisted UTC instants."""

import datetime as dt
import tomllib
import unittest

import marblescape_download as app
from marblescape_time import format_display_datetime, format_utc_datetime, normalize_time_zone


class DisplayTimeTests(unittest.TestCase):
    def test_utc_format_is_explicit_and_stable(self):
        value = "2026-09-13T10:20:45Z"
        self.assertEqual(format_display_datetime(value, "utc"), "2026-09-13 10:20 UTC")
        self.assertEqual(
            format_utc_datetime(value, include_seconds=True),
            "2026-09-13 10:20:45 UTC",
        )

    def test_system_format_uses_the_effective_os_offset(self):
        value = dt.datetime(2026, 9, 13, 10, 20, tzinfo=dt.timezone.utc)
        local = value.astimezone()
        total_minutes = int(local.utcoffset().total_seconds() // 60)
        if total_minutes:
            sign = "+" if total_minutes >= 0 else "-"
            hours, minutes = divmod(abs(total_minutes), 60)
            label = f"UTC{sign}{hours:02d}:{minutes:02d}"
        else:
            label = "UTC"
        self.assertEqual(
            format_display_datetime(value, "system"),
            f"{local:%Y-%m-%d %H:%M} {label}",
        )

    def test_only_system_and_utc_are_accepted(self):
        self.assertEqual(normalize_time_zone(" System "), "system")
        self.assertEqual(normalize_time_zone("UTC"), "utc")
        for value in ("Europe/Berlin", "UTC+02:00", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_time_zone(value)

    def test_configuration_without_display_section_gets_one_when_saved(self):
        existing = "[windows]\nposition = \"fit\"\n"
        updated = app.ensure_display_configuration_section(existing)
        updated = app.replace_toml_values(updated, (("display", "time_zone", "utc"),))
        self.assertEqual(tomllib.loads(updated)["display"]["time_zone"], "utc")


if __name__ == "__main__":
    unittest.main()
