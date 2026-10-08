"""Small activity indicator shared by catalogue controls in Settings."""

import time
import tkinter as tk
from tkinter import ttk

from marblescape_theme import on_theme_change

COMPLETED_TEXT = "Completed."
ISSUES_TEXT = "Finished with issues."


def reserve_text_lines(label, lines=2):
    """Keep at least ``lines`` text lines of room in ``label``'s grid row.

    The catalogue sections then keep their height when a one-line message
    becomes two lines. Call it after gridding the label; the room follows the
    theme's font.
    """
    info = label.grid_info()
    row, master = int(info["row"]), label.master
    pady = info.get("pady", 0)  # An int, a (top, bottom) tuple or Tcl text.
    pady = [int(float(str(value))) for value in
            (pady if isinstance(pady, (tuple, list)) else str(pady).split())]
    padding = sum(pady) if len(pady) > 1 else 2 * pady[0] if pady else 0

    def apply():
        # Measure a label like this one with ``lines`` lines, so the room
        # includes the style's own padding and font.
        options = {"style": str(label.cget("style"))} if str(label.cget("style")) else {}
        if str(label.cget("font")):
            options["font"] = label.cget("font")
        probe = ttk.Label(master, text="\n".join(["Xg"] * lines), **options)
        try:
            height = probe.winfo_reqheight()
        finally:
            probe.destroy()
        master.rowconfigure(row, minsize=height + padding)

    apply()
    on_theme_change(label, apply)


class CatalogueActivity:
    """Animate a catalogue request, then show Completed. or Finished with issues.

    Its row keeps its height while idle, so the section does not change size
    when a refresh starts or ends.
    """

    def __init__(self, parent, row, columnspan=2):
        self.frame = ttk.Frame(parent)
        self.frame.grid(row=row, column=0, columnspan=columnspan, pady=(3, 0), sticky="ew")
        self.frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(self.frame, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.completion = tk.StringVar(self.frame, value="")
        self.completion_label = ttk.Label(self.frame, textvariable=self.completion)
        self.completion_label.grid(row=0, column=0, sticky="w")
        self.completion_label.grid_remove()
        self.progress.grid_remove()
        # An empty spacer keeps the row as tall as the taller of the progress
        # bar and the result text while neither is shown (grid ignores row
        # sizes of a frame without visible slaves).
        self._spacer = ttk.Frame(self.frame, width=1)
        self._spacer.grid(row=0, column=1, sticky="ns")

        def reserve_height():
            self._spacer.configure(height=max(self.progress.winfo_reqheight(),
                                              self.completion_label.winfo_reqheight()))

        reserve_height()
        on_theme_change(self._spacer, reserve_height)
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

    def finish(self, success=False, issues=False):
        """End the request: Completed., Finished with issues. or (reset) nothing."""
        self._stop_animation()
        self.progress.grid_remove()
        text = COMPLETED_TEXT if success else ISSUES_TEXT if issues else ""
        self.completion.set(text)
        if text:
            self.completion_label.grid()
        else:
            self.completion_label.grid_remove()

    def close(self):
        self._stop_animation()


class CatalogueRefreshPanel:
    """Refresh-all control with progress for Downloads & Updates > Catalogue refresh.

    ``request_refresh`` starts the shared refresh. ``client_status`` and
    ``schedule_status`` return copied status dictionaries (or None) of the
    catalogue client and the daily schedule; the panel polls both, so it also
    shows startup and scheduled runs.
    """

    def __init__(self, parent, row, request_refresh, client_status, schedule_status, poll_ms=200):
        self._request_refresh = request_refresh
        self._client_status = client_status
        self._schedule_status = schedule_status
        self._poll_ms = poll_ms
        self._seen_running = False
        self._client_seen_running = False
        self._closed = False
        self.frame = ttk.Frame(parent)
        self.frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.frame.columnconfigure(0, weight=1)
        self.button = ttk.Button(self.frame, text="Refresh all catalogues", command=self.refresh)
        self.button.grid(row=0, column=0, sticky="w")
        ttk.Label(self.frame, text="Loading products and sizes for all public catalogues can take several minutes.",
                  wraplength=630, justify="left").grid(row=1, column=0, pady=(4, 0), sticky="w")
        self.status = tk.StringVar(self.frame, value="")
        self.status_label = ttk.Label(self.frame, textvariable=self.status, wraplength=630, justify="left")
        self.status_label.grid(row=2, column=0, pady=(4, 0), sticky="w")
        self.progress = ttk.Progressbar(self.frame, maximum=100, mode="indeterminate")
        self.progress.grid(row=3, column=0, pady=(4, 0), sticky="ew")
        self.completion = tk.StringVar(self.frame, value="")
        ttk.Label(self.frame, textvariable=self.completion).grid(row=4, column=0, pady=(3, 0), sticky="w")
        self.separator = ttk.Separator(self.frame, orient="horizontal")
        self.separator.grid(row=5, column=0, pady=(6, 4), sticky="ew")
        self.details = tk.StringVar(self.frame, value="")
        self.details_label = ttk.Label(self.frame, textvariable=self.details, wraplength=630, justify="left")
        self.details_label.grid(row=6, column=0, sticky="w")
        for widget in (self.status_label, self.progress, self.separator, self.details_label):
            widget.grid_remove()
        self._after_id = None
        self.poll()

    def refresh(self):
        if not self._running():
            self._request_refresh()
            self.poll(reschedule=False)

    def _running(self):
        client = self._client_status() or {}
        schedule = self._schedule_status() or {}
        return bool(client.get("running") or schedule.get("running"))

    def _set_details(self, message):
        message = str(message or "").strip()
        self.details.set(message)
        for widget in (self.separator, self.details_label):
            widget.grid() if message else widget.grid_remove()

    def poll(self, reschedule=True):
        if self._closed:
            return
        client = self._client_status() or {}
        schedule = self._schedule_status() or {}
        running = bool(client.get("running") or schedule.get("running"))
        self.button.configure(state="disabled" if running else "normal")
        if running:
            if not self._seen_running:
                self._seen_running = True
                self._client_seen_running = False
                self.completion.set("")
                self._set_details("")
            if client.get("running"):
                self._client_seen_running = True
                done, total = max(0.0, float(client.get("done") or 0)), max(0, int(client.get("total") or 0))
                count = f" ({min(total, int(done))}/{total})" if total else ""
                self.status.set(f"Refreshing all catalogues{count}: {client.get('message') or 'Loading...'}")
            else:
                done, total = 0.0, 0
                # After the public sources, saved Copernicus locations follow.
                self.status.set("Refreshing all catalogues: Copernicus dates for saved locations..."
                                if self._client_seen_running else "Refreshing all catalogues: Preparing...")
            # A reported substep shows its real fraction; otherwise animate.
            if total and done != int(done):
                self.progress.configure(mode="determinate", value=min(100.0, 100.0 * done / total))
            else:
                self.progress.configure(mode="indeterminate", value=(time.monotonic() * 35.0) % 100.0)
            self.status_label.grid()
            self.progress.grid()
        elif self._seen_running:
            self._seen_running = False
            error = str(client.get("error") or schedule.get("error") or "").strip()
            self.status.set(str(client.get("message") or "")
                            or ("Catalogue refresh could not be completed." if error else "All catalogues refreshed."))
            self.progress.configure(mode="determinate", value=100 if not error else self.progress["value"])
            self.completion.set("Finished with issues." if error else "Completed.")
            self._set_details("Catalogue update finished with unavailable entries:\n" + error if error else "")
            self.status_label.grid()
            self.progress.grid()
        if reschedule:
            self._after_id = self.frame.after(self._poll_ms, self.poll)

    def close(self):
        self._closed = True
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
