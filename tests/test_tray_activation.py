"""Windows tray activation without downloads or changes to user settings."""

import inspect
import os
import threading
import unittest
from unittest.mock import Mock, patch

import marblescape_download as app


class SettingsActivationTests(unittest.TestCase):
    def test_restore_and_focus_settings(self):
        root = Mock()
        app.activate_settings_window(root)
        self.assertEqual([call[0] for call in root.mock_calls],
                         ["deiconify", "lift", "focus_force"])


@unittest.skipUnless(os.name == "nt", "Windows tray backend")
class WindowsTrayActivationTests(unittest.TestCase):
    def tearDown(self):
        app.APPLICATION_STOP_EVENT.clear()
        app.NETWORK_ACTIVITY.reset()

    def test_native_double_click_message_invokes_default_action(self):
        import pystray
        from pystray._util import win32

        activated = []

        def settings(icon, item):
            activated.append(item.text)
            icon.stop()

        icon = app.create_windows_tray_icon(
            "MarbleScapeActivationTest",
            menu=pystray.Menu(pystray.MenuItem("Settings...", settings, default=True)),
        )
        # Run the real Windows message loop with an invisible tray icon.
        watchdog = threading.Timer(5, icon.stop)
        watchdog.start()
        try:
            icon.run(setup=lambda tray: win32.PostMessage(tray._hwnd, win32.WM_NOTIFY, 0, 0x0203))
        finally:
            watchdog.cancel()
            watchdog.join()
        self.assertEqual(activated, ["Settings..."])

    def test_other_notifications_keep_original_backend_behavior(self):
        import pystray

        icon = app.create_windows_tray_icon("MarbleScapeActivationTest")
        with patch.object(pystray.Icon, "_on_notify", return_value=17) as original:
            for event in (0x0202, 0x0205, 0x0400):  # Left up, right up, other.
                self.assertEqual(icon._on_notify(123, event), 17)
                original.assert_called_with(123, event)

    def test_repeated_activation_requests_reuse_settings_worker(self):
        import pystray

        test = self

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def run(self, setup):
                setup(self)
                action = self.menu.items[1]._action
                state = inspect.getclosurevars(action).nonlocals["settings_dialog_state"]
                with patch.object(app.threading, "Thread") as worker:
                    action(self, None)
                    action(self, None)
                    action(self, None)
                    test.assertEqual(worker.call_count, 1)

                    worker.return_value.start.assert_called_once()
                    test.assertTrue(state["activate_requested"])
                    app.APPLICATION_STOP_EVENT.set()
                    state["activate_requested"] = False
                    action(self, None)
                    test.assertFalse(state["activate_requested"])
                    test.assertEqual(worker.call_count, 1)

            def stop(self):
                pass

        with patch.object(pystray, "Icon", FakeIcon), \
             patch.object(app, "load_configuration"), \
             patch.object(app, "run_application", return_value=0), \
             patch.object(app, "warm_public_catalogues"), \
             patch.object(app, "check_github_update", return_value={"update_available": False}), \
             patch.object(app, "create_windows_tray_image", return_value=None):
            self.assertEqual(app.run_with_windows_tray(["--background"]), 0)


    def test_activity_texts_share_the_symbol_for_every_source(self):
        import pystray
        from marblescape_download_progress import ACTIVITY_SYMBOL

        test = self

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def run(self, setup):
                setup(self)
                item = next(item for item in self.menu.items
                            if getattr(item._text, "__name__", "") == "update_activity_status_text")
                text = item._text
                snapshot = inspect.getclosurevars(text).nonlocals["get_tray_status_snapshot"]
                status = inspect.getclosurevars(snapshot).nonlocals["tray_status"]
                for source in ("eumetsat", "goes_east", "himawari", "slider", "worldview", "copernicus"):
                    with patch.object(app, "IMAGE_SOURCE", source):
                        status["state"] = "fetching"
                        test.assertEqual(text(None), ACTIVITY_SYMBOL + " Fetching new image...")
                        status["state"] = "checking"
                        test.assertEqual(text(None), ACTIVITY_SYMBOL + " Checking for new image...")
                        status["state"] = "waiting"
                        test.assertFalse(text(None).startswith(ACTIVITY_SYMBOL))
                # A refresh of all catalogues follows the image activity after a pipe.
                refreshing = Mock(catalogue_refresh_status={"running": True, "done": 1, "total": 5})
                with patch.dict(app.CATALOGUE_CLIENTS, {("test",): refreshing}, clear=True), \
                     patch.object(app, "CATALOGUE_SCHEDULE", None):
                    status["state"] = "fetching"
                    test.assertEqual(text(None), ACTIVITY_SYMBOL + " Fetching new image... | Refreshing catalogues 2/5")
                    status["state"] = "waiting"
                    test.assertTrue(text(None).endswith("... | Refreshing catalogues 2/5"))
                    refreshing.catalogue_refresh_status = {"running": False, "done": 5, "total": 5}
                    test.assertNotIn("|", text(None))
                    # The Copernicus dates after the public catalogues show no count.
                    schedule = Mock()
                    schedule.status.return_value = {"running": True}
                    with patch.object(app, "CATALOGUE_SCHEDULE", schedule):
                        test.assertTrue(text(None).endswith(" | Refreshing catalogues"))

            def stop(self):
                pass

        with patch.object(pystray, "Icon", FakeIcon), \
             patch.object(app, "load_configuration"), \
             patch.object(app, "run_application", return_value=0), \
             patch.object(app, "warm_public_catalogues"), \
             patch.object(app, "check_github_update", return_value={"update_available": False}), \
             patch.object(app, "create_windows_tray_image", return_value=None):
            self.assertEqual(app.run_with_windows_tray(["--background"]), 0)


if __name__ == "__main__":
    unittest.main()
