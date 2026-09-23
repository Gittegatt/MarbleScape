"""Exercise the real Settings dialog without showing windows or changing wallpaper."""

import copy
import gc
import inspect
import os
from pathlib import Path
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

import marblescape_download as app


@unittest.skipUnless(os.name == "nt", "Windows tray settings integration")
class SettingsIntegrationTests(unittest.TestCase):
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
                self.assertEqual(app.run_with_windows_tray([]), 0)
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
            config.write_text(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
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
                    apply_button = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "Apply")
                    apply_button.invoke()
                    self.assertFalse(errors)
                    after = tomllib.loads(config.read_text(encoding="utf-8"))
                    self.assertEqual(after["source"]["provider"], "goes_west")
                    self.assertEqual(after["sources"]["goes_west"]["product"], "GEOCOLOR")
                    self.assertEqual(after["display"]["time_zone"], "utc")
                    self.assertEqual(after["layers"], before["layers"])
                    self.assertEqual(after["view"]["projection"], before["view"]["projection"])
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
                    quality_entry = next(w for w in widgets if isinstance(w, ttk.Entry)
                                         and str(w.cget("textvariable")) == quality_variable[0])
                    quality_entry.delete(0, "end")
                    quality_entry.insert(0, "auto")
                    apply_button.invoke()
                    final_config = tomllib.loads(config.read_text(encoding="utf-8"))
                    self.assertEqual(final_config["source"]["provider"], "eumetsat")
                    self.assertTrue(any(layer["name"] == "mtg_fd:rgb_truecolour" for layer in final_config["layers"]))
                    root.destroy()

                def switch_source():
                    widgets = list(descendants(root))
                    quality_label = next(w for w in widgets if isinstance(w, ttk.Label)
                                         and w.cget("text") == "Render quality factor")
                    quality_entry = next(w for w in quality_label.master.winfo_children()
                                         if isinstance(w, ttk.Entry) and w.grid_info().get("row") == 6)
                    quality_variable.append(str(quality_entry.cget("textvariable")))
                    quality_entry.delete(0, "end")
                    quality_entry.insert(0, "invalid hidden WMS input")
                    controller = controllers[0]
                    controller.select_eumetsat_layer("mtg_fd:rgb_truecolour")
                    controller._provider_var.set("NOAA GOES")
                    controller._select_provider()
                    controller._goes_var.set("GOES-West")
                    controller._select_goes_satellite()
                    root.after(150, inspect_saved)

                root.after(150, switch_source)
                return root

            quality_variable = []
            try:
                with patch.object(pystray, "Icon", FakeIcon), \
                     patch.object(tk, "Tk", side_effect=hidden_tk), \
                     patch.object(source_ui, "NOAAClient", FakeClient), \
                     patch.object(app, "get_noaa_client", return_value=FakeClient()), \
                     patch.object(app, "get_catalogue_client", return_value=FakeClient()), \
                     patch.object(source_ui, "SourceSettings", side_effect=controller_factory), \
                     patch.object(app, "is_windows_startup_enabled", return_value=False), \
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
                app.FORCE_UPDATE_EVENT.clear()


if __name__ == "__main__":
    unittest.main()
