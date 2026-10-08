"""Responsive settings tab layout tests."""

import unittest

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None

from marblescape_tabbar import ResponsiveNotebookTabs, tab_widths


class TabWidthTests(unittest.TestCase):
    def test_equal_width_when_all_labels_fit(self):
        self.assertEqual(tab_widths((60, 90, 70), 360), (120, 120, 120))

    def test_surplus_is_shared_without_clipping_long_labels(self):
        widths = tab_widths((60, 120, 70), 300)
        self.assertEqual(sum(widths), 300)
        self.assertTrue(all(width >= minimum for width, minimum
                            in zip(widths, (60, 120, 70))))

    def test_narrow_window_keeps_minimums_for_scrolling(self):
        self.assertEqual(tab_widths((60, 120, 70), 160), (60, 120, 70))

    def test_all_pixels_are_allocated(self):
        self.assertEqual(sum(tab_widths((50, 50, 50), 302)), 302)


@unittest.skipIf(tk is None, "Tkinter is not installed")
class ResponsiveNotebookTabsTests(unittest.TestCase):
    def test_tabs_fill_wide_window_and_scroll_in_narrow_window(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        try:
            root.withdraw()
            root.geometry("1000x160")
            container = ttk.Frame(root)
            container.pack(fill="both", expand=True)
            container.columnconfigure(0, weight=1)
            container.rowconfigure(1, weight=1)
            notebook = ttk.Notebook(container)
            notebook.grid(row=1, column=0, sticky="nsew")
            for title in ("General", "Image", "Downloads & Updates", "Profiles",
                          "History & Storage", "Backup", "Sources", "Access", "About", "Info"):
                notebook.add(ttk.Frame(notebook), text=title)
            tabs = ResponsiveNotebookTabs(container, notebook)
            tabs.frame.grid(row=0, column=0, sticky="ew")
            root.deiconify()
            root.update()
            self.assertEqual(tabs._positions[-1][1], tabs.canvas.winfo_width())
            self.assertFalse(tabs.scrollbar.winfo_ismapped())
            # Each tab is its own card: window background shows between two tabs.
            from marblescape_tabbar import TAB_GAP
            for (_left, right), (left, _right) in zip(tabs._positions, tabs._positions[1:]):
                self.assertEqual(left - right, TAB_GAP)

            root.geometry("440x160")
            root.update()
            self.assertGreater(tabs._positions[-1][1], tabs.canvas.winfo_width())
            self.assertTrue(tabs.scrollbar.winfo_ismapped())
            notebook.select(8)
            root.update()
            self.assertEqual(tabs._selection.get(), 8)
            self.assertGreater(tabs.canvas.xview()[0], 0)
        finally:
            root.destroy()

    def test_builtin_tabs_stay_hidden_after_appearance_change(self):
        import marblescape_theme as theme

        if theme.sv_ttk is None:
            self.skipTest("sv_ttk is not installed")
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        try:
            root.withdraw()
            theme.apply_appearance(root, "dark")
            notebook = ttk.Notebook(root)
            notebook.add(ttk.Frame(notebook, height=10), text="General")
            notebook.pack()
            ResponsiveNotebookTabs(root, notebook)
            root.update()
            hidden_height = notebook.winfo_reqheight()

            for mode in ("light", "dark"):
                theme.apply_appearance(root, mode)
                root.update()
                self.assertEqual(notebook.winfo_reqheight(), hidden_height, mode)
                self.assertEqual(
                    ttk.Style(root).layout("MarbleScapeTabless.TNotebook.Tab"),
                    [("null", {"sticky": "nswe"})], mode,
                )
                # No outer border around the pages below the tab bar.
                self.assertEqual(
                    ttk.Style(root).layout("MarbleScapeTabless.TNotebook"),
                    [("null", {"sticky": "nswe"})], mode,
                )
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
