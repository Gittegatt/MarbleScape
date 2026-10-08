"""Profiles > Now showing: the profile on screen first, then the rotation."""

import time
import unittest
from types import SimpleNamespace

import marblescape_download as app


class NowShowingTests(unittest.TestCase):
    def state(self, **values):
        return dict({"text": "Rotation is disabled.", "deadline": None, "preload": None,
                     "next_profile_name": None}, **values)

    def test_without_rotation_the_rotation_line_is_last(self):
        # Rotation keeps the third line; the second stays empty.
        self.assertEqual(app.now_showing_text("Bahamas", self.state(), "utc"),
                         "Active profile: Bahamas\n\nRotation: disabled")
        self.assertEqual(app.now_showing_text(None, self.state(), "utc"),
                         "No picture yet\n\nRotation: disabled")
        # A running step gets the middle line; an ellipsis stays.
        loading = self.state(text="Loading Fiji (attempt 1/3)...")
        self.assertEqual(app.now_showing_text("Bahamas", loading, "utc"),
                         "Active profile: Bahamas\nLoading Fiji (attempt 1/3)...\nRotation: enabled")

    def test_with_rotation_the_next_profile_shows_once_with_its_preload_result(self):
        now = time.monotonic()
        deadline = now + 600
        state = self.state(text="Active profile: Bahamas", deadline=deadline, next_profile_name="Fiji",
                           preload={"deadline": deadline, "name": "Fiji", "outcome": "unchanged",
                                    "checked_at": app.dt.datetime(2026, 10, 4, 14, 32, 47,
                                                                  tzinfo=app.dt.timezone.utc)})
        lines = app.now_showing_text("Bahamas", state, "utc", now=now).split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "Active profile: Bahamas")
        self.assertTrue(lines[1].startswith("Upcoming profile: Fiji at "), lines[1])
        self.assertTrue(lines[1].endswith(" | already current (14:32:47 UTC)"), lines[1])
        self.assertEqual(lines[2], "Rotation: enabled")
        # The next profile's name appears only once.
        self.assertEqual("\n".join(lines).count("Fiji"), 1)
        # Without a preload result or a known next profile, only what is known shows.
        waiting = self.state(text="Waiting for the next rotation interval.", deadline=deadline)
        lines = app.now_showing_text("Bahamas", waiting, "utc", now=now).split("\n")
        self.assertTrue(lines[1].startswith("Upcoming profile: ") and lines[1].endswith(" UTC"), lines[1])
        self.assertEqual(lines[2], "Rotation: enabled")
        # A cancelled step stays on the rotation line, without the closing period.
        cancelled = self.state(text="Cancelled Fiji; waiting for the next rotation interval.",
                               deadline=deadline, next_profile_name="Chile")
        lines = app.now_showing_text("Bahamas", cancelled, "utc", now=now).split("\n")
        self.assertEqual(lines[2], "Rotation: enabled | Cancelled Fiji; waiting for the next rotation interval")
        for line in lines:
            self.assertFalse(line.endswith(".") and not line.endswith("..."), line)

    def test_the_worker_publishes_the_next_profile(self):
        saved = dict(app.ROTATION_STATUS)
        self.addCleanup(app.ROTATION_STATUS.update, saved)
        rotation = SimpleNamespace(upcoming=lambda: ({"id": "1" * 32, "name": "Fiji"}, 100.0))
        app.publish_next_rotation_profile(rotation)
        self.assertEqual(app.ROTATION_STATUS["next_profile_name"], "Fiji")
        app.publish_next_rotation_profile(SimpleNamespace(upcoming=lambda: None))
        self.assertIsNone(app.ROTATION_STATUS["next_profile_name"])


if __name__ == "__main__":
    unittest.main()
