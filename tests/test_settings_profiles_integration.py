"""Real Settings profile/scroll integration using hidden Tk and offline fixtures."""

from contextlib import ExitStack
from copy import deepcopy
import gc
import inspect
import io
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import tomllib
import unittest
from unittest.mock import patch

from PIL import Image

import marblescape_download as app
from marblescape_profiles import read_library


def source_png(size):
    output = io.BytesIO()
    Image.new("RGB", size, (20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


class FakeCatalogue:
    catalogue_refresh_status = {"running": False, "done": 0, "total": 0, "message": "", "error": ""}
    catalogue_warning = ""

    def list_areas(self, provider, refresh=False):
        if provider == "solar":
            return [{"id": "sun", "label": "Sun", "category": "Solar"}]
        return [
            {"id": "full_disk", "label": "Full Disk", "category": "Full Disk"},
            {"id": "storm_alpha", "label": "Storm Alpha", "category": "Active storms"},
            {"id": "storm_beta", "label": "Storm Beta", "category": "Active storms"},
        ]

    def list_products(self, provider, area, refresh=False):
        if provider == "solar":
            return [{"id": "Fe171", "label": "171 Angstrom", "resolutions": ["300x300", "1200x1200"]}]
        return [
            {"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["339x339", "1808x1808"]},
            {"id": "13", "label": "Infrared", "resolutions": ["678x678", "5424x5424"]},
        ]


@unittest.skipUnless(os.name == "nt", "Windows tray Settings integration")
class SettingsProfilesIntegrationTests(unittest.TestCase):
    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from SettingsProfilesIntegrationTests.descendants(child)

    def wait_for_source(self, context):
        deadline = time.monotonic() + 5
        while context.source._loading:
            if time.monotonic() > deadline:
                self.fail("Fixture source catalogue did not finish")
            context.root.update()
            self.assertEqual(context.errors, [])
            time.sleep(0.01)
        context.root.update()
        self.assertEqual(context.errors, [])

    def apply(self, context):
        from tkinter import ttk
        button = next(widget for widget in self.descendants(context.root)
                      if isinstance(widget, ttk.Button) and widget.cget("text") == "Apply")
        button.invoke()
        self.assertEqual(context.errors, [])
        return tomllib.loads(context.config.read_text(encoding="utf-8"))

    def run_dialog(self, scenario):
        import tkinter as tk
        from tkinter import ttk
        import pystray
        import marblescape_source_settings as source_ui
        import marblescape_profile_settings as profile_ui

        class Finished(Exception):
            pass

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def update_menu(self):
                pass

            def run(self, setup):
                action = self.menu.items[1]._action
                inspect.getclosurevars(action).nonlocals["run_settings_dialog"](self)
                raise Finished()

        saved_configuration = app.capture_loaded_configuration()
        saved_image_status = deepcopy(app.IMAGE_STATUS)
        saved_rotation_status = deepcopy(app.ROTATION_STATUS)
        saved_rotation_deadline = app.NEXT_ROTATION_DEADLINE
        events = (app.APPLICATION_STOP_EVENT, app.CONFIGURATION_RELOAD_EVENT, app.FORCE_UPDATE_EVENT)
        event_states = [event.is_set() for event in events]
        real_tk = tk.Tk
        real_source = source_ui.SourceSettings
        real_profiles = profile_ui.ProfilesSettings
        sources, profiles, errors, roots = [], [], [], []

        def capture_source(*args, **kwargs):
            result = real_source(*args, **kwargs)
            sources.append(result)
            return result

        def capture_profiles(*args, **kwargs):
            result = real_profiles(*args, **kwargs)
            profiles.append(result)
            return result

        with tempfile.TemporaryDirectory(prefix="marblescape-image-ui-") as directory, ExitStack() as stack:
            config = Path(directory) / "settings.toml"
            stack.enter_context(patch.object(
                app, "PROFILE_LIBRARY_PATH", Path(directory) / "profiles.toml"
            ))
            text = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            text = app.replace_toml_values(text, [
                ("output", "windows_root", Path(directory).as_posix()),
                ("output", "linux_root", Path(directory).as_posix()),
            ])
            config.write_text(text, encoding="utf-8")

            def hidden_tk():
                root = real_tk()
                roots.append(root)
                root.withdraw()
                root.deiconify = lambda: None
                root.lift = lambda: None
                root.attributes = lambda *args, **kwargs: None
                root.report_callback_exception = lambda kind, error, traceback: errors.append(error)

                def run_scenario(*args, **kwargs):
                    try:
                        form = inspect.getclosurevars(profiles[0]._capture_settings).nonlocals
                        notebook = next(widget for widget in self.descendants(root) if isinstance(widget, ttk.Notebook))
                        context = SimpleNamespace(root=root, source=sources[0], profiles=profiles[0],
                                                  variables=form["variables"], form=form, notebook=notebook,
                                                  config=config, directory=Path(directory), errors=errors)
                        scenario(context)
                        self.assertEqual(errors, [])
                    finally:
                        root.destroy()
                root.mainloop = run_scenario
                return root

            stack.enter_context(patch.object(pystray, "Icon", FakeIcon))
            stack.enter_context(patch.object(tk, "Tk", side_effect=hidden_tk))
            stack.enter_context(patch.object(source_ui, "SourceSettings", side_effect=capture_source))
            stack.enter_context(patch.object(profile_ui, "ProfilesSettings", side_effect=capture_profiles))
            stack.enter_context(patch.object(app, "get_noaa_client", return_value=FakeCatalogue()))
            stack.enter_context(patch.object(app, "get_catalogue_client", return_value=FakeCatalogue()))
            stack.enter_context(patch.object(app, "migrate_legacy_windows_startup"))
            stack.enter_context(patch.object(app, "is_windows_startup_enabled", return_value=False))
            stack.enter_context(patch.object(app, "set_windows_startup_enabled", side_effect=AssertionError("Unexpected startup change")))
            stack.enter_context(patch.object(app, "create_windows_tray_image", return_value=None))
            stack.enter_context(patch.object(app, "set_windows_wallpaper", side_effect=AssertionError("Unexpected wallpaper change")))
            stack.enter_context(patch.object(app, "urlopen", side_effect=AssertionError("Unexpected live network request")))
            stack.enter_context(patch("tkinter.messagebox.showerror", side_effect=lambda *args, **kwargs: errors.append(args)))
            stack.enter_context(patch.object(app, "log"))
            try:
                with self.assertRaises(Finished):
                    app.run_with_windows_tray(["--config", str(config)])
                self.assertEqual(errors, [])
                self.assertTrue(sources[0]._closed)
                self.assertTrue(profiles[0]._closed)
            finally:
                for controller in [*sources, *profiles]:
                    controller.close()
                for root in roots:
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass
                sources.clear()
                profiles.clear()
                roots.clear()
                controller = root = None
                gc.collect()
                app.restore_loaded_configuration(saved_configuration)
                app.IMAGE_STATUS.clear()
                app.IMAGE_STATUS.update(saved_image_status)
                app.ROTATION_STATUS.clear()
                app.ROTATION_STATUS.update(saved_rotation_status)
                app.NEXT_ROTATION_DEADLINE = saved_rotation_deadline
                for event, was_set in zip(events, event_states):
                    event.set() if was_set else event.clear()

    def test_complete_image_profile_restores_form_and_survives_apply_and_backup(self):
        def scenario(context):
            variables = context.variables
            saved_sources = deepcopy(app.DEFAULT_SOURCE_PROFILES)
            saved_sources["goes_west"]["resolution"] = "339x339"
            context.source.set_selection("eumetsat", saved_sources)
            values = {
                "satellite_layer": "MTG TrueColor (day)", "projection": "North Polar",
                "show_extended_projections": True, "fit_mode": "crop", "zoom": "1.65",
                "truecolor_black_night": True, "width": "1024", "height": "768",
                "aspect_ratio": "4:3", "render_scale": "1.5", "background_color": "#123456",
                "latest_folder": (context.directory / "profile-latest").as_posix(),
            }
            for name, value in values.items():
                variables[name].set(value)
            expected = context.profiles._capture_settings()
            before = context.config.read_text(encoding="utf-8")
            context.profiles.name_var.set("Detailed Earth")
            context.profiles.add_current()
            self.assertEqual(context.errors, [])
            saved_library = context.profiles.get_library()
            self.assertEqual(len(saved_library["items"]), 1)
            self.assertEqual(saved_library["items"][0]["settings"], expected)
            self.assertEqual(context.config.read_text(encoding="utf-8"), before, "Profile drafts must wait for Apply")

            context.source.set_selection("solar", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            context.source.select_eumetsat_layer("mtg_fd:rgb_geocolour")
            changed = {
                "projection": "Geographic",
                "show_extended_projections": False, "fit_mode": "fit", "zoom": "2.8",
                "truecolor_black_night": False, "width": "800", "height": "450",
                "aspect_ratio": "16:9", "render_scale": "auto", "background_color": "#FFFFFF",
                "latest_folder": (context.directory / "changed-latest").as_posix(),
            }
            for name, value in changed.items():
                variables[name].set(value)
            context.profiles.load_selected()
            self.wait_for_source(context)
            self.assertEqual(context.source.provider, "eumetsat")
            self.assertEqual(context.profiles._capture_settings(), expected)
            self.assertEqual(variables["render_scale"].get(), "1.5")
            self.assertEqual(variables["projection"].get(), "North Polar")
            self.assertEqual(context.source.get_selection()[1], saved_sources)

            after = self.apply(context)
            self.assertNotIn("image_profiles", after)
            self.assertEqual(
                app.read_profile_library_file(context.directory / "profiles.toml"),
                saved_library,
            )
            for section in ("source", "sources", "layers"):
                self.assertEqual(after[section], expected[section])
            for section in ("view", "output"):
                for name, value in expected[section].items():
                    self.assertEqual(after[section][name], value, f"{section}.{name}")
            payload = app.create_settings_backup_payload()
            restored, restored_profiles, startup = app.parse_settings_backup_payload(
                json.loads(json.dumps(payload))
            )
            self.assertFalse(startup)
            self.assertNotIn("image_profiles", tomllib.loads(restored))
            self.assertEqual(restored_profiles, saved_library)
            self.assertEqual(tomllib.loads(restored)["view"], after["view"])
            self.assertEqual(tomllib.loads(restored)["output"], after["output"])
        self.run_dialog(scenario)

    def test_download_tab_persists_speed_progress_and_bar_preferences(self):
        from tkinter import ttk

        def scenario(context):
            tabs = [context.notebook.tab(tab, "text") for tab in context.notebook.tabs()]
            self.assertEqual(
                tabs,
                ["General", "Image", "Download", "Profiles & Rotation",
                 "History & Storage", "Backup", "Sources", "About"],
            )
            source_urls = {
                str(widget.cget("text")) for widget in self.descendants(context.root)
                if isinstance(widget, tk.Label)
                and str(widget.cget("text")).startswith("https://")
            }
            self.assertEqual(source_urls, {
                "https://view.eumetsat.int/productviewer",
                "https://www.star.nesdis.noaa.gov/goes/index.php",
                "https://www.star.nesdis.noaa.gov/goes/SUVI.php?sat=G19",
                "https://himawari8.nict.go.jp/",
                "https://ds.data.jma.go.jp/mscweb/data/himawari/index.html",
                "https://slider.cira.colostate.edu/",
                "https://worldview.earthdata.nasa.gov/",
                "https://browser.dataspace.copernicus.eu/",
            })
            section = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Download display"
            )
            checkboxes = {
                widget.cget("text") for widget in section.winfo_children()
                if isinstance(widget, ttk.Checkbutton)
            }
            self.assertEqual(checkboxes, {
                "Show download speed",
                "Show percentage and downloaded size",
                "Show progress bar",
                "Keep completed download visible until next download",
            })
            cancel_download = next(
                widget for widget in self.descendants(context.root)
                if isinstance(widget, ttk.Button)
                and widget.cget("text") == "Cancel download"
            )
            self.assertTrue(cancel_download.instate(["disabled"]))
            context.variables["show_download_speed"].set(False)
            context.variables["download_speed_unit"].set("Mbit/s")
            context.variables["show_download_progress"].set(True)
            context.variables["show_download_progress_bar"].set(False)
            context.variables["keep_completed_download_visible"].set(True)
            saved = self.apply(context)
            self.assertEqual(saved["download"], {
                "show_speed": False,
                "speed_unit": "Mbit/s",
                "show_progress": True,
                "show_progress_bar": False,
                "keep_completed_visible": True,
            })

        self.run_dialog(scenario)

    def test_storage_clear_buttons_are_positioned_and_delete_only_managed_files(self):
        from tkinter import ttk

        def scenario(context):
            app.ensure_directories()
            latest = app.LATEST_DIR / "keep.png"
            latest.write_bytes(source_png((32, 18)))
            history = app.HISTORY_DIR / "marblescape_2026-09-13_120000.png"
            history.write_bytes(source_png((32, 18)))
            unrelated_history = app.HISTORY_DIR / "keep.png"
            unrelated_history.write_bytes(source_png((32, 18)))
            cached, _previous = app.get_profile_cache().install(
                "f" * 32, {"configuration": "same"}, {"source": "same"},
                source_png((32, 18)), (32, 18),
            )
            widgets = list(self.descendants(context.root))
            cache_section = next(
                widget for widget in widgets
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Profile image cache"
            )
            status_section = next(
                widget for widget in widgets
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Status and storage"
            )
            self.assertGreater(cache_section.grid_info()["row"], status_section.grid_info()["row"])
            button = next(
                widget for widget in cache_section.winfo_children()
                if isinstance(widget, ttk.Button) and widget.cget("text") == "Clear cache"
            )
            button.invoke()
            context.root.update_idletasks()
            self.assertFalse(cached.exists())
            self.assertTrue(latest.exists())
            self.assertEqual(app.get_profile_cache().status()["files"], 0)

            clear_history = next(
                widget for widget in status_section.winfo_children()
                if isinstance(widget, ttk.Button) and widget.cget("text") == "Clear history"
            )
            next_check = next(
                widget for widget in status_section.winfo_children()
                if isinstance(widget, ttk.Label) and widget.cget("text") == "Next check"
            )
            self.assertGreater(
                clear_history.grid_info()["row"], next_check.grid_info()["row"]
            )
            clear_history.invoke()
            context.root.update_idletasks()
            self.assertFalse(history.exists())
            self.assertTrue(unrelated_history.exists())
            self.assertTrue(latest.exists())

        self.run_dialog(scenario)

    def test_active_storm_profile_loads_and_normal_apply_persists_actual_area(self):
        def scenario(context):
            context.source.set_selection("goes_east", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            context.source._category_var.set("Active storms")
            context.source._select_category()
            self.wait_for_source(context)
            self.assertEqual(context.source.get_selection()[1]["goes_east"]["area"], "storm_alpha")
            context.variables["zoom"].set("1.4")
            context.variables["fit_mode"].set("crop")
            context.profiles.name_var.set("Current storm")
            context.profiles.add_current()
            self.assertEqual(context.errors, [])
            expected = context.profiles.get_library()["items"][0]["settings"]
            context.source.set_selection("solar", app.DEFAULT_SOURCE_PROFILES)
            self.wait_for_source(context)
            context.variables["zoom"].set("2.0")
            context.variables["fit_mode"].set("fit")
            context.profiles.load_selected()
            self.wait_for_source(context)
            self.assertEqual(context.profiles._capture_settings(), expected)
            self.assertEqual(context.source._area_var.get(), "Storm Alpha [storm_alpha]")
            after = self.apply(context)
            self.assertEqual(after["source"]["provider"], "goes_east")
            self.assertEqual(after["sources"]["goes_east"],
                             {"area": "storm_alpha", "product": "GEOCOLOR", "resolution": "auto"})
            self.assertEqual(after["view"]["zoom"], 1.4)
            self.assertEqual(after["view"]["fit_mode"], "crop")
            self.assertNotIn("image_profiles", after)
            self.assertEqual(
                app.read_profile_library_file(context.directory / "profiles.toml")
                ["items"][0]["settings"],
                expected,
            )
        self.run_dialog(scenario)

    def test_eumetsat_preset_is_under_source_and_mouse_focus_keeps_viewport(self):
        from tkinter import ttk
        def scenario(context):
            source = context.source
            preset = next(widget for widget in source.eumetsat_view_frame.winfo_children()
                          if isinstance(widget, ttk.Combobox))
            self.assertIs(source.eumetsat_frame.master, source.frame)
            self.assertIs(preset.master, source.eumetsat_view_frame)
            self.assertGreater(source.eumetsat_frame.grid_info()["row"], source._provider_combo.grid_info()["row"])
            general_id = next(tab for tab in context.notebook.tabs() if context.notebook.tab(tab, "text") == "General")
            general = context.root.nametowidget(general_id)
            general_text = [str(widget.cget("text")) for widget in self.descendants(general)
                            if "text" in widget.keys()]
            self.assertNotIn("Preset", general_text)
            content = source.frame.master
            canvas, page = content.master, content.master.master
            context.notebook.select(page)
            context.root.update_idletasks()
            zoom_label = next(widget for widget in self.descendants(content)
                              if isinstance(widget, ttk.Label) and widget.cget("text") == "Zoom")
            row = zoom_label.grid_info()["row"]
            zoom_entry = next(widget for widget in zoom_label.master.winfo_children()
                              if isinstance(widget, ttk.Entry) and widget.grid_info().get("row") == row)
            canvas.configure(scrollregion=(0, 0, 800, 5000))
            canvas.yview_moveto(0.45)
            before = canvas.yview()
            self.assertGreater(before[0], 0)
            self.assertEqual(context.root.bind("<FocusIn>"), "", "Mouse focus must not trigger viewport reveal")
            zoom_entry.event_generate("<Button-1>", x=3, y=3, when="now")
            zoom_entry.event_generate("<FocusIn>", when="now")
            source._provider_combo.event_generate("<FocusIn>", when="now")
            context.root.update()
            self.assertEqual(canvas.yview(), before, "Mouse/combobox focus moved the Image viewport")
        self.run_dialog(scenario)


if __name__ == "__main__":
    unittest.main()
