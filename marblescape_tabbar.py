"""Responsive, horizontally scrollable controls for a ttk.Notebook."""

import tkinter as tk
from tkinter import ttk

from marblescape_theme import TAB_BUTTON_STYLE, keep_background, on_theme_change

# Pixels of window background between two tabs, so each tab stands out as its
# own card instead of the tabs forming one band.
TAB_GAP = 4


def tab_widths(minimums, available):
    """Fill available width without making any tab narrower than its label."""
    minimums = tuple(max(1, int(width)) for width in minimums)
    if not minimums:
        return ()
    available = max(0, int(available))
    if available <= sum(minimums):
        return minimums
    count = len(minimums)
    if available >= count * max(minimums):
        width, remainder = divmod(available, count)
        return tuple(width + (index < remainder) for index in range(count))
    extra, remainder = divmod(available - sum(minimums), count)
    return tuple(width + extra + (index < remainder)
                 for index, width in enumerate(minimums))


class ResponsiveNotebookTabs:
    """Show all Notebook pages with full-width tabs and overflow scrolling."""

    def __init__(self, parent, notebook):
        self.notebook = notebook
        style = ttk.Style(notebook)
        # Style layouts belong to one ttk theme: hide the built-in tabs and the
        # pages' outer border again after every appearance change, or they
        # reappear under this bar. Without a border element the pages fill
        # the whole notebook.
        def hide_tabs_and_border():
            style.layout("MarbleScapeTabless.TNotebook.Tab", [])
            style.layout("MarbleScapeTabless.TNotebook", [])

        on_theme_change(notebook, hide_tabs_and_border)
        notebook.configure(style="MarbleScapeTabless.TNotebook")

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self.frame, highlightthickness=0, borderwidth=0,
            background=style.lookup("TFrame", "background") or "#f0f0f0",
        )
        keep_background(self.canvas)
        self.canvas.grid(row=0, column=0, sticky="ew")
        self.scrollbar = ttk.Scrollbar(
            self.frame, orient="horizontal", command=self.canvas.xview,
        )
        self.scrollbar.grid(row=1, column=0, sticky="ew")
        self.scrollbar.grid_remove()
        self.canvas.configure(xscrollcommand=self.scrollbar.set)

        self._selection = tk.IntVar(self.frame, value=notebook.index("current"))
        # Measure with the tab buttons' own font (larger in the Sun Valley theme),
        # in this window's own interpreter rather than Tk's default root.
        font = style.lookup(TAB_BUTTON_STYLE, "font") or "TkDefaultFont"
        self._minimums = []
        self._windows = []
        self._positions = []
        self._buttons = []
        for index in range(notebook.index("end")):
            title = notebook.tab(index, "text")
            button = ttk.Radiobutton(
                self.canvas, text=title, value=index, variable=self._selection,
                style=TAB_BUTTON_STYLE, command=lambda tab=index: notebook.select(tab),
                takefocus=True,
            )
            self._buttons.append(button)
            self._minimums.append(max(52, int(notebook.tk.call("font", "measure", font, title)) + 22))
            self._windows.append(self.canvas.create_window(
                0, 0, window=button, anchor="nw",
            ))
        self.canvas.configure(height=max(
            (button.winfo_reqheight() for button in self._buttons), default=24,
        ))
        self.canvas.bind("<Configure>", self._layout)
        self.canvas.bind("<Shift-MouseWheel>", self._scroll_wheel)
        notebook.bind("<<NotebookTabChanged>>", self._sync_selection, add="+")
        self.frame.after_idle(self._layout)

    def _layout(self, _event=None):
        if not self.canvas.winfo_exists():
            return
        available = max(1, self.canvas.winfo_width())
        gaps = TAB_GAP * max(0, len(self._windows) - 1)
        widths = tab_widths(self._minimums, max(1, available - gaps))
        height = int(self.canvas["height"])
        position = 0
        self._positions = []
        for index, (window, width) in enumerate(zip(self._windows, widths)):
            if index:
                position += TAB_GAP
            self.canvas.coords(window, position, 0)
            self.canvas.itemconfigure(window, width=width, height=height)
            self._positions.append((position, position + width))
            position += width
        self.canvas.configure(scrollregion=(0, 0, position, height))
        if position > available:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()
            self.canvas.xview_moveto(0)
        self._show_selected()

    def _sync_selection(self, _event=None):
        self._selection.set(self.notebook.index("current"))
        self.frame.after_idle(self._show_selected)

    def _show_selected(self):
        index = self._selection.get()
        if not 0 <= index < len(self._positions):
            return
        left, right = self._positions[index]
        visible_left = self.canvas.canvasx(0)
        visible_right = visible_left + self.canvas.winfo_width()
        total = max(1, self._positions[-1][1])
        if left < visible_left:
            self.canvas.xview_moveto(left / total)
        elif right > visible_right:
            self.canvas.xview_moveto((right - self.canvas.winfo_width()) / total)

    def _scroll_wheel(self, event):
        if self.scrollbar.winfo_ismapped():
            self.canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
