"""The log file, the forced exit and the restart that ends a hung previous MarbleScape."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import marblescape_download as app


class LogFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="marblescape-log-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        content = patch.object(app, "CONTENT_DIR", self.directory)
        content.start()
        self.addCleanup(content.stop)

    def enabled(self, **values):
        state = patch.dict(app.LOG_FILE_STATE, {"enabled": True, "handler": None})
        state.start()
        self.addCleanup(state.stop)
        self.addCleanup(app.close_log_file)
        for name, value in values.items():
            setting = patch.object(app, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def lines(self):
        return app.log_file_path().read_text(encoding="utf-8").splitlines()

    def test_only_a_real_run_writes_the_log(self):
        self.assertFalse(app.LOG_FILE_STATE["enabled"])
        with patch("builtins.print"):
            app.log("Nothing to write.")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_lines_are_written_with_their_time(self):
        self.enabled()
        with patch("builtins.print") as printed:
            app.log("Update cycle took 0.8 s.")
        self.assertEqual(app.log_file_path(), self.directory / "marblescape.log")
        self.assertRegex(self.lines()[-1], r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] Update cycle took 0.8 s.$")
        # The console shows the same line.
        self.assertEqual(printed.call_args.args[0], self.lines()[-1])

    def test_the_log_starts_anew_and_keeps_two_files_before(self):
        self.enabled(LOG_FILE_MAX_BYTES=300)
        with patch("builtins.print"):
            for number in range(60):
                app.log(f"Line {number} " + "x" * 20)
        app.close_log_file()
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                         ["marblescape.log", "marblescape.log.1", "marblescape.log.2"])
        self.assertTrue(self.lines()[-1].endswith("Line 59 " + "x" * 20))
        self.assertTrue(all(path.stat().st_size <= 400 for path in self.directory.iterdir()))

    def test_credentials_and_tokens_never_reach_the_log(self):
        self.enabled(COPERNICUS_CLIENT_ID="sh-client-1234", COPERNICUS_CLIENT_SECRET="s3cr3t-value",
                     COPERNICUS_CLIENT_SECRET_PROTECTED="AQAAANCMnd8BFdER")
        messages = ("Login sh-client-1234 with s3cr3t-value failed (AQAAANCMnd8BFdER)",
                    "Authorization: Bearer abc.DEF-123 rejected",
                    '{"access_token": "eyJhbGciOi", "expires_in": 600}',
                    "grant_type=client_credentials&client_secret=other&client_id=x",
                    "password=hunter22; refresh_token: r-456")
        with patch("builtins.print"):
            for message in messages:
                app.log(message)
        text = "\n".join(self.lines())
        for secret in ("sh-client-1234", "s3cr3t-value", "AQAAANCMnd8BFdER", "abc.DEF-123", "eyJhbGciOi",
                       "other", "hunter22", "r-456"):
            self.assertNotIn(secret, text)
        self.assertIn("Authorization: Bearer *** rejected", text)
        self.assertIn('"expires_in": 600', text)

    def test_a_failing_log_file_never_stops_marblescape(self):
        self.enabled()
        (self.directory / "marblescape.log").mkdir()
        with patch("builtins.print"):
            app.log("Still running.")


class ForcedExitTests(unittest.TestCase):
    def test_only_a_real_run_arms_the_forced_exit(self):
        with patch.object(app.threading, "Timer") as timer:
            app.start_forced_exit_timer()
            timer.assert_not_called()
            with patch.dict(app.FORCED_EXIT, {"armed": True}):
                app.start_forced_exit_timer()
        self.assertEqual(timer.call_args.args[0], app.FORCED_EXIT_SECONDS)
        timer.return_value.start.assert_called_once()
        self.assertTrue(timer.return_value.daemon)
        with patch.object(app.os, "_exit") as exit_now, patch.object(app, "log"):
            timer.call_args.args[1]()
        exit_now.assert_called_once_with(0)


class RestartTests(unittest.TestCase):
    def test_a_normal_start_waits_briefly(self):
        with patch.object(app, "acquire_windows_single_instance", return_value=False) as acquire, \
                patch.object(app, "should_use_windows_tray", return_value=True), \
                patch.object(app, "end_previous_instance") as end:
            self.assertFalse(app.acquire_instance_for_start({}))
        acquire.assert_called_once_with(wait_seconds=3)
        end.assert_not_called()

    def test_a_restart_ends_the_previous_instance_that_still_hangs(self):
        environment = {"MARBLESCAPE_RESTART_WAIT": "1", "MARBLESCAPE_RESTART_FROM_PID": "4242"}
        with patch.object(app, "acquire_windows_single_instance", side_effect=[False, True]) as acquire, \
                patch.object(app, "end_previous_instance", return_value=True) as end:
            self.assertTrue(app.acquire_instance_for_start(environment))
        self.assertEqual([call.kwargs["wait_seconds"] for call in acquire.call_args_list],
                         [app.RESTART_WAIT_SECONDS, 5])
        end.assert_called_once_with("4242")
        # The previous one stopped in time: nothing is ended.
        with patch.object(app, "acquire_windows_single_instance", return_value=True), \
                patch.object(app, "end_previous_instance") as end:
            self.assertTrue(app.acquire_instance_for_start(environment))
        end.assert_not_called()
        self.assertGreater(app.RESTART_WAIT_SECONDS, app.FORCED_EXIT_SECONDS)

    @unittest.skipUnless(os.name == "nt", "Windows processes")
    def test_only_a_previous_marblescape_is_ended(self):
        with patch.object(app.os, "kill") as kill, patch.object(app, "log"):
            for pid_text, name in (("", "pythonw.exe"), ("x", "pythonw.exe"), (str(os.getpid()), "pythonw.exe"),
                                   ("4242", None), ("4242", "notepad.exe")):
                with patch.object(app, "windows_process_image_name", return_value=name):
                    self.assertFalse(app.end_previous_instance(pid_text), (pid_text, name))
            kill.assert_not_called()
            for name in ("pythonw.exe", "PYTHON.EXE"):
                with patch.object(app, "windows_process_image_name", return_value=name):
                    self.assertTrue(app.end_previous_instance("4242"))
        self.assertEqual(kill.call_args.args, (4242, app.signal.SIGTERM))

    @unittest.skipUnless(os.name == "nt", "Windows processes")
    def test_the_program_name_of_a_process_is_read(self):
        self.assertIn(app.windows_process_image_name(os.getpid()).lower(),
                      {"python.exe", "pythonw.exe", Path(app.sys.executable).name.lower()})
        self.assertIsNone(app.windows_process_image_name(0))


if __name__ == "__main__":
    unittest.main()
