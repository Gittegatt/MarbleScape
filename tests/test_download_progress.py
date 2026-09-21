"""Download progress accounting and presentation tests."""

import io
import tomllib
import unittest
from unittest import mock

import marblescape_download as app
import marblescape_download_progress as progress


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class _Response:
    def __init__(self, payload, declared=True, clock=None):
        self.stream = io.BytesIO(payload)
        self.headers = ({"Content-Length": str(len(payload))} if declared else {})
        self.clock = clock

    def read(self, size=-1):
        if self.clock is not None:
            self.clock.value += 0.25
        return self.stream.read(size)

    def close(self):
        self.stream.close()


class _CancelOnReadResponse(_Response):
    def read(self, size=-1):
        progress.DOWNLOAD_PROGRESS.request_cancel()
        return super().read(size)


class DownloadProgressTests(unittest.TestCase):
    def test_exact_content_length_produces_percentage_size_and_speed(self):
        clock = _Clock()
        tracker = progress.DownloadProgressTracker(clock=clock)
        payload = b"x" * (progress.READ_CHUNK_SIZE + 123)
        tracker.begin(expected_requests=1)
        with mock.patch.object(progress, "DOWNLOAD_PROGRESS", tracker):
            self.assertEqual(progress.read_response(
                _Response(payload, declared=True, clock=clock), track=True
            ), payload)
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["transferred"], len(payload))
        self.assertEqual(snapshot["total"], len(payload))
        self.assertEqual(snapshot["percent"], 100.0)
        self.assertGreater(snapshot["speed"], 0)
        text = app.format_download_progress(snapshot, speed_unit="MB/s")
        self.assertIn("100%", text)
        self.assertIn("/", text)
        self.assertIn("MB/s", text)

    def test_missing_total_stays_indeterminate_until_update_finishes(self):
        clock = _Clock()
        tracker = progress.DownloadProgressTracker(clock=clock)
        payload = b"unknown-size"
        tracker.begin()
        with mock.patch.object(progress, "DOWNLOAD_PROGRESS", tracker):
            progress.read_response(_Response(payload, declared=False, clock=clock), track=True)
        self.assertIsNone(tracker.snapshot()["total"])
        tracker.finish(True)
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["total"], len(payload))
        self.assertEqual(snapshot["percent"], 100.0)

    def test_successful_result_can_remain_visible_until_next_download(self):
        clock = _Clock()
        tracker = progress.DownloadProgressTracker(clock=clock)
        tracker.begin(expected_requests=1)
        with mock.patch.object(progress, "DOWNLOAD_PROGRESS", tracker):
            progress.read_response(
                _Response(b"complete", declared=True, clock=clock), track=True
            )
        tracker.finish(True)
        clock.value += progress.COMPLETED_VISIBILITY_SECONDS + 1.0
        self.assertFalse(tracker.snapshot()["visible"])
        retained = tracker.snapshot(keep_completed_visible=True)
        self.assertTrue(retained["visible"])
        self.assertEqual(retained["percent"], 100.0)
        tracker.begin(expected_requests=1)
        replacement = tracker.snapshot(keep_completed_visible=True)
        self.assertTrue(replacement["visible"])
        self.assertTrue(replacement["active"])
        self.assertEqual(replacement["transferred"], 0)

    def test_declared_oversize_is_rejected_before_reading(self):
        response = _Response(b"small")
        response.headers["Content-Length"] = "100"
        with self.assertRaises(progress.ResponseTooLargeError):
            progress.read_response(response, maximum=10, track=True)

    def test_user_cancellation_closes_response_and_resets_on_next_download(self):
        tracker = progress.DownloadProgressTracker()
        response = _Response(b"partial")
        tracker.begin(expected_requests=1)
        token = tracker.response_started(len(b"partial"), response)
        self.assertTrue(tracker.request_cancel())
        self.assertTrue(response.stream.closed)
        with self.assertRaises(progress.DownloadCancelledError):
            tracker.raise_if_cancelled()
        tracker.response_finished(token, 0)
        tracker.finish(False, cancelled=True)
        cancelled = tracker.snapshot()
        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["successful"])
        self.assertIn("Download cancelled", app.format_download_progress(cancelled))

        tracker.begin(expected_requests=1)
        replacement = tracker.snapshot()
        self.assertFalse(replacement["cancel_requested"])
        self.assertFalse(replacement["cancelled"])
        self.assertTrue(replacement["cancellable"])

    def test_cancellation_during_a_blocking_read_becomes_a_cancel_result(self):
        tracker = progress.DownloadProgressTracker()
        tracker.begin(expected_requests=1)
        with mock.patch.object(progress, "DOWNLOAD_PROGRESS", tracker):
            with self.assertRaises(progress.DownloadCancelledError):
                progress.read_response(_CancelOnReadResponse(b"partial"), track=True)
        tracker.finish(False, cancelled=True)
        snapshot = tracker.snapshot()
        self.assertTrue(snapshot["cancelled"])
        self.assertEqual(snapshot["transferred"], 0)
        self.assertEqual(snapshot["total"], len(b"partial"))

    def test_committing_result_cannot_be_cancelled(self):
        tracker = progress.DownloadProgressTracker()
        tracker.begin()
        tracker.seal_cancellation()
        self.assertFalse(tracker.request_cancel())
        self.assertFalse(tracker.snapshot()["cancellable"])

    def test_supported_speed_units_are_unambiguous(self):
        self.assertEqual(app.format_download_speed(2_000_000, "automatic"), "2.00 MB/s")
        self.assertEqual(app.format_download_speed(2_000_000, "KB/s"), "2000.0 KB/s")
        self.assertEqual(app.format_download_speed(2_000_000, "Mbit/s"), "16.00 Mbit/s")
        with self.assertRaisesRegex(ValueError, "unit"):
            app.normalize_download_speed_unit("frames/s")

    def test_saving_adds_missing_download_section_for_older_configuration(self):
        legacy = "[service]\nupdate_interval_minutes = 10\n"
        updated = app.ensure_download_configuration_section(legacy)
        updated = app.replace_toml_values(updated, (
            ("download", "keep_completed_visible", True),
        ))
        self.assertTrue(
            tomllib.loads(updated)["download"]["keep_completed_visible"]
        )


if __name__ == "__main__":
    unittest.main()
