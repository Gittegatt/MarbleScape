"""Small activity indicator shared by catalogue controls in Settings."""

import time
import tkinter as tk
from tkinter import ttk


class CatalogueActivity:
    """Animate an unknown-length catalogue request and retain its success label."""

    def __init__(self, parent, row, columnspan=2):
        self.frame = ttk.Frame(parent)
        self.frame.grid(row=row, column=0, columnspan=columnspan, pady=(3, 0), sticky="ew")
        self.frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(self.frame, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.completion = tk.StringVar(self.frame, value="")
        self.completion_label = ttk.Label(self.frame, textvariable=self.completion)
        self.completion_label.grid(row=0, column=0, sticky="e")
        self.completion_label.grid_remove()
        self.frame.grid_remove()
        self.active = False
        self._tick_id = None

    def _tick(self):
        self._tick_id = None
        if self.active:
            # Match the download indicator's left-to-right cycle.
            self.progress.configure(value=(time.monotonic() * 35.0) % 100.0)
            self._tick_id = self.frame.after(200, self._tick)

    def _stop_animation(self):
        self.active = False
        if self._tick_id is not None:
            try:
                self.frame.after_cancel(self._tick_id)
            except tk.TclError:
                pass
            self._tick_id = None

    def start(self):
        if self.active:
            return
        self.completion.set("")
        self.completion_label.grid_remove()
        self.progress.configure(mode="indeterminate")
        self.progress.grid()
        self.frame.grid()
        self.active = True
        self._tick()

    def set_progress(self, done, total):
        """Show completed catalogue stages when their count is known."""
        if not self.active or total <= 0:
            return
        if self._tick_id is not None:
            self.frame.after_cancel(self._tick_id)
            self._tick_id = None
        self.progress.configure(mode="determinate", value=min(100.0, 100.0 * done / total))

    def finish(self, success=False):
        self._stop_animation()
        self.progress.grid_remove()
        self.completion.set("Completed." if success else "")
        if success:
            self.completion_label.grid()
            self.frame.grid()
        else:
            self.completion_label.grid_remove()
            self.frame.grid_remove()

    def close(self):
        self._stop_animation()
