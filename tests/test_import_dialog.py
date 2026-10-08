import tkinter as tk
from tkinter import ttk
import unittest

from marblescape_import_dialog import confirm_import_repair, confirm_transfer_conflict, review_import_preflight


class ImportDialogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def decide(self, label, allow_skip=True):
        errors = []

        def select():
            window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                buttons = {child.cget("text"): child for frame in window.winfo_children()
                           for child in frame.winfo_children() if isinstance(child, ttk.Button)}
                self.assertEqual("Skip" in buttons, allow_skip)
                if label == "close":
                    window.tk.call(window.protocol("WM_DELETE_WINDOW"))
                else:
                    buttons[label].invoke()
            except Exception as exc:
                errors.append(exc)
                window.destroy()
        self.root.after(10, select)
        result = confirm_import_repair(self.root, "Test profile", ["date_mode = catalogue"], allow_skip)
        if errors:
            raise errors[0]
        return result

    def test_buttons_and_close_return_safe_decisions(self):
        for label, expected in (("Yes", "yes"), ("Yes to all", "yes_all"), ("Cancel", "no"),
                                ("Skip", "skip"), ("Skip all", "skip_all"), ("close", "no")):
            with self.subTest(label=label):
                self.assertEqual(self.decide(label), expected)

    def test_full_backup_has_no_skip(self):
        self.assertEqual(self.decide("Cancel", allow_skip=False), "no")

    def test_preflight_overview_and_safe_cancel(self):
        for count, proceed in ((0, False), (1, False), (1, True)):
            errors = []
            def inspect():
                window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                try:
                    children = list(descendants(window))
                    buttons = {child.cget("text"): child for child in children if isinstance(child, ttk.Button)}
                    text = next(child for child in children if isinstance(child, tk.Text)).get("1.0", "end")
                    self.assertIn("Broken.png: invalid CRC", text)
                    self.assertIn("Legacy: missing background", text)
                    self.assertEqual(buttons["Continue with valid candidates"].instate(["disabled"]), count == 0)
                    buttons["Continue with valid candidates" if proceed else "Cancel"].invoke()
                except Exception as exc:
                    errors.append(exc)
                    window.destroy()
            self.root.after(10, inspect)
            self.assertEqual(review_import_preflight(self.root, {
                "valid_count": count, "failures": ["Broken.png: invalid CRC"],
                "repairs": [("Legacy", ["Legacy: missing background"])],
            }), proceed)
            self.assertFalse(errors, errors)

    def test_transfer_conflict_buttons_and_safe_window_close(self):
        for multiple in (False, True):
            for label, expected in (("Overwrite", "overwrite"), ("Skip", "skip"),
                                    ("Save as copy", "rename"), ("close", "cancel")):
                errors = []
                def select():
                    window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
                    try:
                        buttons = {child.cget("text"): child for frame in window.winfo_children()
                                   for child in frame.winfo_children() if isinstance(child, ttk.Button)}
                        self.assertEqual("Overwrite all" in buttons, multiple)
                        self.assertEqual("Skip all" in buttons, multiple)
                        if label == "close":
                            window.tk.call(window.protocol("WM_DELETE_WINDOW"))
                        else:
                            buttons[label].invoke()
                    except Exception as exc:
                        errors.append(exc)
                        window.destroy()
                self.root.after(10, select)
                result = confirm_transfer_conflict(self.root, {"kind": "export", "label": "Existing.json", "multiple": multiple})
                self.assertFalse(errors, errors)
                self.assertEqual(result, expected)

    def test_ambiguous_import_disables_overwrite_buttons(self):
        states = []
        def inspect():
            window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
            buttons = {child.cget("text"): child for frame in window.winfo_children()
                       for child in frame.winfo_children() if isinstance(child, ttk.Button)}
            states.extend(buttons[key].instate(["disabled"]) for key in ("Overwrite", "Overwrite all"))
            states.append(("Import as copy" in buttons, "Rename (Copy)" in buttons, "Save as copy" in buttons))
            buttons["Cancel"].invoke()
        self.root.after(10, inspect)
        result = confirm_transfer_conflict(self.root, {"kind": "import", "label": "Profile", "reason": "name",
            "incoming": {"id": "1" * 32}, "existing": [{"name": "Profile", "id": "2" * 32}],
            "multiple": True, "overwrite_allowed": False})
        self.assertEqual(result, "cancel")
        self.assertEqual(states, [True, True, (True, False, False)])
