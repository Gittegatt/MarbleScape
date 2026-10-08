import unittest
from unittest.mock import Mock

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - Linux --once environments
    tk = None


@unittest.skipIf(tk is None, "Tkinter is not installed")
class CatalogueRefreshPanelTests(unittest.TestCase):
    def setUp(self):
        from marblescape_catalogue_activity import CatalogueRefreshPanel

        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.client = {"running": False, "done": 0, "total": 0, "message": "", "error": ""}
        self.schedule = {"running": False, "message": "Not refreshed yet.", "error": ""}
        self.request = Mock()
        self.panel = CatalogueRefreshPanel(
            self.root, 0, self.request, lambda: dict(self.client), lambda: dict(self.schedule),
        )
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.panel.close)

    def shown(self, widget):
        return bool(widget.grid_info())

    def test_idle_panel_offers_refresh_without_old_messages(self):
        self.assertEqual(self.panel.button.cget("text"), "Refresh all catalogues")
        self.assertEqual(str(self.panel.button["state"]), "normal")
        self.assertFalse(self.shown(self.panel.status_label))
        self.assertFalse(self.shown(self.panel.progress))
        self.assertEqual(self.panel.completion.get(), "")
        self.panel.refresh()
        self.request.assert_called_once_with()

    def test_running_refresh_shows_progress_and_completes(self):
        self.client.update(running=True, done=1, total=5, message="Loading Himawari catalogues...")
        self.schedule["running"] = True
        self.panel.poll(reschedule=False)
        self.assertIn("(1/5): Loading Himawari", self.panel.status.get())
        self.assertTrue(self.shown(self.panel.progress))
        self.assertEqual(str(self.panel.button["state"]), "disabled")
        self.panel.refresh()
        self.request.assert_not_called()
        self.client.update(running=True, done=2.5, total=5, message="NASA Worldview: layers (1/2)")
        self.panel.poll(reschedule=False)
        self.assertEqual(str(self.panel.progress["mode"]), "determinate")
        self.assertEqual(float(self.panel.progress["value"]), 50.0)
        # Public sources done; saved Copernicus locations still load.
        self.client.update(running=False, done=5, total=5, message="Catalogue update completed.")
        self.panel.poll(reschedule=False)
        self.assertIn("Copernicus", self.panel.status.get())
        self.assertEqual(self.panel.completion.get(), "")
        self.schedule.update(running=False, message="All catalogues refreshed.")
        self.panel.poll(reschedule=False)
        self.assertEqual(self.panel.status.get(), "Catalogue update completed.")
        self.assertEqual(self.panel.completion.get(), "Completed.")
        self.assertEqual(float(self.panel.progress["value"]), 100.0)
        self.assertEqual(str(self.panel.button["state"]), "normal")
        self.assertFalse(self.shown(self.panel.details_label))

    def test_incomplete_refresh_reports_details(self):
        self.schedule["running"] = True
        self.panel.poll(reschedule=False)
        self.assertIn("Preparing", self.panel.status.get())
        self.client.update(message="Catalogue refresh completed with unavailable entries.",
                           error="NOAA: offline")
        self.schedule.update(running=False, error="NOAA: offline")
        self.panel.poll(reschedule=False)
        self.assertEqual(self.panel.completion.get(), "Finished with issues.")
        self.assertIn("NOAA: offline", self.panel.details.get())
        self.assertTrue(self.shown(self.panel.details_label))
        self.assertTrue(self.shown(self.panel.separator))
        # A new run clears the previous result.
        self.schedule.update(running=True, error="")
        self.panel.poll(reschedule=False)
        self.assertEqual(self.panel.completion.get(), "")
        self.assertFalse(self.shown(self.panel.details_label))


@unittest.skipIf(tk is None, "Tkinter is not installed")
class CatalogueActivityTests(unittest.TestCase):
    def setUp(self):
        from tkinter import ttk
        from marblescape_catalogue_activity import CatalogueActivity, reserve_text_lines

        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.section = ttk.Frame(self.root)
        self.section.pack(fill="x")
        self.message = tk.StringVar(self.root, value="")
        label = ttk.Label(self.section, textvariable=self.message, wraplength=200, justify="left")
        label.grid(row=0, column=0, pady=(4, 0), sticky="nw")
        reserve_text_lines(label)
        self.activity = CatalogueActivity(self.section, row=1)
        self.addCleanup(self.activity.close)

    def height(self):
        self.root.update_idletasks()
        return self.section.winfo_reqheight()

    def test_section_keeps_its_height_through_every_state(self):
        idle = self.height()
        self.message.set("Loading the catalogue...")
        self.activity.start()
        self.assertEqual(self.height(), idle)
        self.message.set("Catalogue loaded: 12 products, a second line of text follows here.")
        self.activity.finish(success=True)
        self.assertEqual(self.height(), idle)
        self.assertEqual(self.activity.completion.get(), "Completed.")
        self.assertEqual(self.activity.completion_label.grid_info()["sticky"], "w")
        self.activity.finish()
        self.assertEqual(self.height(), idle)
        self.assertEqual(self.activity.completion.get(), "")

    def test_issues_are_reported_left_aligned(self):
        self.activity.start()
        self.activity.finish(issues=True)
        self.assertEqual(self.activity.completion.get(), "Finished with issues.")
        self.assertTrue(self.activity.completion_label.grid_info())
        self.assertFalse(self.activity.progress.grid_info())
        # Success wins over issues; a new start clears the result.
        self.activity.finish(success=True, issues=True)
        self.assertEqual(self.activity.completion.get(), "Completed.")
        self.activity.start()
        self.assertEqual(self.activity.completion.get(), "")


if __name__ == "__main__":
    unittest.main()
