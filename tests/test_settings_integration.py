"""Exercise the real Settings dialog without showing windows or changing wallpaper."""

import copy
import gc
import inspect
import os
from pathlib import Path
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch

import marblescape_download as app


@unittest.skipUnless(os.name == "nt", "Windows tray settings integration")
class SettingsIntegrationTests(unittest.TestCase):
    def tearDown(self):
        app.NETWORK_ACTIVITY.reset()

    def test_background_start_is_silent_and_migrates_only_enabled_registration(self):
        import tkinter as tk
        import pystray

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu
            def run(self, setup):
                setup(self)
                self.menu.items[-1]._action(self, None)
            def stop(self):
                pass

        for enabled in (True, False):
            with self.subTest(enabled=enabled), \
                 patch.object(pystray, "Icon", FakeIcon), \
                 patch.object(tk, "Tk", side_effect=AssertionError("Background startup must not open a window")), \
                 patch.object(app, "load_configuration"), \
                 patch.object(app, "run_application", return_value=0), \
                 patch.object(app, "warm_public_catalogues"), \
                 patch.object(app, "check_github_update") as check_update, \
                 patch.object(app, "create_windows_tray_image", return_value=None), \
                 patch.object(app, "is_windows_startup_enabled", return_value=enabled), \
                 patch.object(app, "set_windows_startup_enabled") as set_startup:
                try:
                    self.assertEqual(app.run_with_windows_tray(["--background"], migrate_startup=True), 0)
                    check_update.assert_not_called()
                    if enabled:
                        set_startup.assert_called_once_with(True)
                    else:
                        set_startup.assert_not_called()
                finally:
                    app.APPLICATION_STOP_EVENT.clear()

    def test_manual_start_opens_real_settings_and_exit_joins_gui_thread(self):
        import tkinter as tk
        import pystray
        from test_settings_profiles_integration import FakeCatalogue

        previous = app.capture_loaded_configuration()
        opened, destroyed = threading.Event(), threading.Event()
        elapsed = []
        real_tk = tk.Tk

        def hidden_tk():
            root = real_tk()
            root.withdraw()
            root.deiconify = lambda: None
            root.lift = lambda: None
            root.attributes = lambda *args: None
            root.bind("<Destroy>", lambda event: destroyed.set() if event.widget is root else None, add="+")
            root.after(0, opened.set)
            return root

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu
            def run(self, setup):
                setup(self)
                if not opened.wait(8):
                    app.APPLICATION_STOP_EVENT.set()
                    raise AssertionError("Manual startup did not open Settings")
                start = time.monotonic()
                self.menu.items[-1]._action(self, None)
                elapsed.append(start)
            def stop(self):
                pass
            def update_menu(self):
                pass
            def notify(self, *args):
                raise AssertionError(args)

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "settings.toml"
            text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            text = app.replace_toml_values(text, [("output", "windows_root", directory),
                                                ("source", "provider", "goes_east")])
            config.write_text(text, encoding="utf-8")
            try:
                with patch.object(pystray, "Icon", FakeIcon), \
                     patch.object(tk, "Tk", side_effect=hidden_tk), \
                     patch.object(app, "run_application", side_effect=lambda *a, **k: app.APPLICATION_STOP_EVENT.wait(10) and 0), \
                     patch.object(app, "warm_public_catalogues"), \
                     patch.object(app, "check_github_update", return_value={"update_available": False}), \
                     patch.object(app, "get_catalogue_client", return_value=FakeCatalogue()), \
                     patch.object(app, "get_noaa_client", return_value=FakeCatalogue()), \
                     patch.object(app, "is_windows_startup_enabled", return_value=False), \
                     patch.object(app, "PROFILE_LIBRARY_PATH", Path(directory) / "profiles.toml"), \
                     patch.object(app, "create_windows_tray_image", return_value=None):
                    self.assertEqual(app.run_with_windows_tray(["--config", str(config)]), 0)
                self.assertTrue(destroyed.is_set())
                self.assertLess(time.monotonic() - elapsed[0], 3)
                self.assertFalse(any(t.name == "MarbleScapeSettings" and t.is_alive() for t in threading.enumerate()))
            finally:
                app.restore_loaded_configuration(previous)
                app.APPLICATION_STOP_EVENT.clear()

    def test_restart_cancels_download_and_waits_for_worker(self):
        import pystray

        worker_finished = threading.Event()

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def run(self, setup):
                setup(self)
                self.menu.items[-2]._action(self, None)

            def stop(self):
                pass

        def run_worker(*args, **kwargs):
            app.APPLICATION_STOP_EVENT.wait(2)
            time.sleep(0.03)
            worker_finished.set()
            return 0

        try:
            with patch.object(pystray, "Icon", FakeIcon), \
                 patch.object(app, "load_configuration"), \
                 patch.object(app, "run_application", side_effect=run_worker), \
                 patch.object(app, "warm_public_catalogues"), \
                 patch.object(app, "check_github_update", return_value={"update_available": False}), \
                 patch.object(app, "create_windows_tray_image", return_value=None), \
                 patch.object(app, "get_application_launch_arguments", return_value=["marblescape.exe"]), \
                 patch.object(app.subprocess, "Popen") as launch, \
                 patch.object(app.DOWNLOAD_PROGRESS, "request_cancel") as cancel, \
                 patch.object(app.NETWORK_ACTIVITY, "cancel", wraps=app.NETWORK_ACTIVITY.cancel) as network_cancel:
                self.assertEqual(app.run_with_windows_tray(["--background"]), 0)
            self.assertTrue(worker_finished.is_set())
            self.assertTrue(cancel.called)
            self.assertTrue(network_cancel.called)
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(launch.call_args.kwargs["env"]["MARBLESCAPE_RESTART_WAIT"], "1")
        finally:
            app.APPLICATION_STOP_EVENT.clear()

    def test_tray_exit_closes_an_open_tk_window(self):
        import tkinter as tk
        import pystray

        fallback_used = []

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def run(self, setup):
                setup(self)
                action = self.menu.items[1]._action
                dialog = inspect.getclosurevars(action).nonlocals["run_settings_dialog"]
                create_root = inspect.getclosurevars(dialog).nonlocals["create_tray_dialog_root"]
                root = create_root(tk)
                root.withdraw()
                root.after(20, lambda: self.menu.items[-1]._action(self, None))

                def fallback():
                    fallback_used.append(True)
                    root.destroy()

                root.after(500, fallback)
                root.mainloop()

            def stop(self):
                pass

        try:
            with patch.object(pystray, "Icon", FakeIcon), \
                 patch.object(app, "load_configuration"), \
                 patch.object(app, "run_application", return_value=0), \
                 patch.object(app, "warm_public_catalogues"):
                self.assertEqual(app.run_with_windows_tray(["--background"]), 0)
            self.assertFalse(fallback_used, "The tray-owned Tk window did not close on Exit")
        finally:
            app.APPLICATION_STOP_EVENT.clear()

    def test_real_dialog_switches_source_and_saves_without_changing_wms_settings(self):
        import tkinter as tk
        from tkinter import ttk
        import pystray
        import marblescape_source_settings as source_ui

        class Finished(Exception):
            pass

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def list_areas(self, provider, refresh=False):
                return [{"id": "full_disk", "label": "Full Disk", "category": "Full Disk"}]

            def list_products(self, provider, area, refresh=False):
                return [{"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["339x339", "1808x1808"]}]

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def update_menu(self):
                pass

            def run(self, setup):
                # Enter the actual dialog synchronously, without starting the
                # application's network/wallpaper worker or registering a tray icon.
                action = self.menu.items[1]._action
                dialog = inspect.getclosurevars(action).nonlocals["run_settings_dialog"]
                dialog(self)
                raise Finished()

        state = app.capture_loaded_configuration()
        original_tk = tk.Tk
        original_controller = source_ui.SourceSettings
        controllers, errors = [], []
        original_status = copy.deepcopy(app.IMAGE_STATUS)

        def controller_factory(*args, **kwargs):
            controller = original_controller(*args, **kwargs)
            controllers.append(controller)
            return controller

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        with tempfile.TemporaryDirectory(prefix="marblescape-settings-test-") as directory:
            config = Path(directory) / "config.toml"
            # Keep the profile library, cache and images in the temporary folder.
            text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            config.write_text(app.replace_toml_values(text, [
                ("output", "windows_root", Path(directory).as_posix()),
                ("output", "linux_root", Path(directory).as_posix()),
            ]), encoding="utf-8")
            before = tomllib.loads(config.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 8

            def hidden_tk():
                root = original_tk()
                root.withdraw()
                root.deiconify = lambda: None
                root.lift = lambda: None

                def callback_error(_kind, error, _traceback):
                    errors.append(error)
                    root.destroy()

                root.report_callback_exception = callback_error

                def inspect_saved():
                    controller = controllers[0]
                    if controller._loading:
                        if time.monotonic() > deadline:
                            raise AssertionError("The Settings catalogue did not finish loading")
                        root.after(100, inspect_saved)
                        return
                    widgets = list(descendants(root))
                    time_zone_label = next(
                        w for w in widgets if isinstance(w, ttk.Label)
                        and w.cget("text") == "Time zone"
                    )
                    time_zone_combo = next(
                        w for w in time_zone_label.master.winfo_children()
                        if isinstance(w, ttk.Combobox)
                        and "System time (recommended)" in w["values"]
                    )
                    self.assertEqual(time_zone_combo.get(), "System time (recommended)")
                    time_zone_combo.set("UTC")
                    notebook = next(w for w in widgets if isinstance(w, ttk.Notebook))
                    image_tab = next(tab for tab in notebook.tabs()
                                     if notebook.tab(tab, "text") == "Image")
                    general_tab = next(tab for tab in notebook.tabs()
                                       if notebook.tab(tab, "text") == "General")
                    notebook.select(image_tab)
                    root.update()
                    apply_image_button = next(
                        w for w in widgets
                        if isinstance(w, ttk.Button) and w.cget("text") == "Apply Image"
                    )
                    apply_image_button.invoke()
                    self.assertFalse(errors)
                    after = tomllib.loads(config.read_text(encoding="utf-8"))
                    self.assertEqual(after["source"]["provider"], "goes_west")
                    self.assertEqual(after["sources"]["goes_west"]["product"], "GEOCOLOR")
                    self.assertEqual(after["display"]["time_zone"], before["display"]["time_zone"])
                    notebook.select(general_tab)
                    root.update()
                    apply_button = next(
                        w for w in widgets
                        if isinstance(w, ttk.Button) and w.cget("text") == "Save"
                    )
                    # The pending time-zone change enables Save.
                    self.assertEqual(str(apply_button["state"]), "normal")
                    apply_button.invoke()
                    after = tomllib.loads(config.read_text(encoding="utf-8"))
                    self.assertEqual(after["display"]["time_zone"], "utc")
                    self.assertEqual(after["layers"], before["layers"])
                    self.assertEqual(after["view"]["projection"], before["view"]["projection"])
                    # The hidden EUMETSAT choice did not overwrite its saved render quality.
                    self.assertEqual(after["output"]["render_scale"], before["output"]["render_scale"])
                    controller._provider_var.set("EUMETSAT")
                    controller._select_provider()
                    self.assertTrue(
                        controller.eumetsat_frame.grid_info(),
                        "EUMETSAT must show its catalogue controls",
                    )
                    self.assertTrue(
                        controller.eumetsat_settings._layer_combo.grid_info(),
                        "EUMETSAT must show its catalogue layer field",
                    )
                    quality_combo = quality_combos[0]
                    quality_combo.set("Auto (max useful)")
                    quality_combo.event_generate("<<ComboboxSelected>>")
                    notebook.select(image_tab)
                    root.update()
                    apply_image_button.invoke()
                    final_config = tomllib.loads(config.read_text(encoding="utf-8"))
                    self.assertEqual(final_config["source"]["provider"], "eumetsat")
                    self.assertTrue(any(layer["name"] == "mtg_fd:rgb_truecolour" for layer in final_config["layers"]))
                    root.destroy()

                def switch_source():
                    widgets = list(descendants(root))
                    quality_label = next(w for w in widgets if isinstance(w, ttk.Label)
                                         and w.cget("text") == "Render quality factor"
                                         and w.master is controllers[0].eumetsat_rendering_frame)
                    # EUMETSAT's render quality sits in the Rendering section, as a
                    # dropdown only: no field can hold an empty or invalid factor.
                    self.assertEqual(controllers[0].rendering_frame.cget("text"), "Rendering")
                    quality_combo = next(w for w in quality_label.master.winfo_children()
                                         if isinstance(w, ttk.Combobox) and w.grid_info().get("row") == 0)
                    self.assertEqual(str(quality_combo.cget("state")), "readonly")
                    self.assertFalse(any(isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox)
                                         for w in quality_label.master.winfo_children()))
                    quality_combos.append(quality_combo)
                    quality_combo.set("Ultra (2.0×)")
                    quality_combo.event_generate("<<ComboboxSelected>>")
                    controller = controllers[0]
                    controller.select_eumetsat_layer("mtg_fd:rgb_truecolour")
                    controller._provider_var.set("NOAA GOES")
                    controller._select_provider()
                    controller._goes_var.set("GOES-West")
                    controller._select_goes_satellite()
                    root.after(150, inspect_saved)

                root.after(150, switch_source)
                return root

            quality_combos = []
            try:
                with patch.object(pystray, "Icon", FakeIcon), \
                     patch.object(tk, "Tk", side_effect=hidden_tk), \
                     patch.object(source_ui, "NOAAClient", FakeClient), \
                     patch.object(app, "get_noaa_client", return_value=FakeClient()), \
                     patch.object(app, "get_catalogue_client", return_value=FakeClient()), \
                     patch.object(source_ui, "SourceSettings", side_effect=controller_factory), \
                     patch.object(app, "is_windows_startup_enabled", return_value=False), \
                     patch.object(app, "SCRIPT_DIR", Path(directory)), \
                     patch.object(app, "PROFILE_LIBRARY_PATH", Path(directory) / "profiles.toml"), \
                     patch("tkinter.messagebox.showerror", side_effect=lambda *args, **kwargs: errors.append(args)):
                    with self.assertRaises(Finished):
                        app.run_with_windows_tray(["--config", str(config)])
                self.assertEqual(errors, [])
                self.assertTrue(controllers[0]._closed)
            finally:
                for controller in controllers:
                    controller.close()
                controllers.clear()
                controller = None
                gc.collect()
                app.restore_loaded_configuration(state)
                app.IMAGE_STATUS.clear()
                app.IMAGE_STATUS.update(original_status)
                app.APPLICATION_STOP_EVENT.clear()
                app.CONFIGURATION_RELOAD_EVENT.clear()
                app.SETTINGS_ONLY_RELOAD_EVENT.clear()
                app.FORCE_UPDATE_EVENT.clear()


if __name__ == "__main__":
    unittest.main()
