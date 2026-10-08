"""Shared geometry for controls inside the Image > Source section."""

import re

SOURCE_COMBO_WIDTH = 42


def size_text(value):
    """A size as shown: "5424x5424" becomes "5424 × 5424"; other texts stay."""
    match = re.fullmatch(r"\s*([1-9][0-9]*)\s*[x×]\s*([1-9][0-9]*)\s*", str(value))
    return f"{match.group(1)} × {match.group(2)}" if match else str(value)
# Color values (#RRGGBB, transparent, blur) share one width, so the rows line up.
COLOR_VALUE_WIDTH = 10  # Fits "Transparent".
# Keep the shared value column beyond every current source label. A common
# minimum prevents a long provider-specific label from shifting only its own
# dropdown while still allowing the dropdowns to grow with the Settings window.
SOURCE_LABEL_COLUMN_MINSIZE = 180
# The widest control in the label column (a checkbox with its indicator) and the
# room kept beside it; larger theme fonts and indicators widen the column.
SOURCE_LABEL_COLUMN_WIDEST = "Labels (places, roads, POIs)"
SOURCE_LABEL_COLUMN_MARGIN = 15


def source_label_column_minsize(widget):
    """The shared label-column width for the theme of ``widget``'s window."""
    from tkinter import ttk

    root = widget._root()
    cached = getattr(root, "_marblescape_source_label_minsize", None)
    if cached is None:
        probe = ttk.Checkbutton(widget, text=SOURCE_LABEL_COLUMN_WIDEST)
        try:
            cached = max(SOURCE_LABEL_COLUMN_MINSIZE,
                         probe.winfo_reqwidth() + SOURCE_LABEL_COLUMN_MARGIN)
        finally:
            probe.destroy()
        root._marblescape_source_label_minsize = cached
    return cached


def configure_source_columns(frame):
    """Align source labels and fields across nested provider frames."""
    frame.columnconfigure(0, minsize=source_label_column_minsize(frame))
    frame.columnconfigure(1, weight=1)
