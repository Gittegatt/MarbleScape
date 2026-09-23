"""Shared geometry for controls inside the Image > Source section."""

SOURCE_COMBO_WIDTH = 42
SOURCE_LABEL_COLUMN_MINSIZE = 130


def configure_source_columns(frame):
    """Align source labels and fields across nested provider frames."""
    frame.columnconfigure(0, minsize=SOURCE_LABEL_COLUMN_MINSIZE)
    frame.columnconfigure(1, weight=1)
