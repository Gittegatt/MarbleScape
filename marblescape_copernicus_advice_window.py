"""The Recommend window: two priorities and every compared variant for a Copernicus view."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from marblescape_copernicus_advice import (
    CLEAR_CLOUDS,
    FEWEST_CLOUDS_COVERAGE,
    FULL_COVERAGE,
    REPLAY_DAYS,
    age_text,
    newest_row,
    percent,
)
from PIL import Image, ImageDraw, ImageTk

from marblescape_copernicus import PREVIEW_BOX
from marblescape_location_search import coordinate_text, parse_coordinates
from marblescape_theme import SECTION_LABEL_STYLE, current_mode, on_theme_change, palette

PREVIEW_SIZE = PREVIEW_BOX
SHOW_PREVIEW = "Show preview"
# The dots column before Gap fill: one dot per priority that chose the row.
DOT_SIZE, DOT_GAP = 9, 4
DOTS_WIDTH = 3 * DOT_SIZE + 2 * DOT_GAP
# Room for three dots: the item padding (5 px) on the left, a margin on the right.
DOTS_COLUMN_WIDTH = DOTS_WIDTH + 13
# The table's items without the expander indicator, which would push the dots right.
DOTS_STYLE = "MarbleScapeDots.Treeview"


def _without_indicator(layout):
    result = []
    for name, options in layout:
        if name.endswith("indicator"):
            continue
        options = dict(options)
        if "children" in options:
            options["children"] = _without_indicator(options["children"])
        result.append((name, options))
    return result
READY_TEXT = "Press Check to compare the variants for this place and zoom."
FOOTER_TEXT = ("Check is free: it reads the Copernicus catalogue.\n"
               "Precise check and Load preview use processing units.\n"
               "Details: Info > Recommendation.")
LOADING_PREVIEW = "Loading preview..."
# Each priority keeps one palette color: its box heading and its row in the table.
# For mosaics the first box is Newest, orange like Newest everywhere.
PRIORITY_COLORS = {"fewest": "link", "full": "success"}
MOSAIC_PRIORITY_COLORS = {"fewest": "newest", "full": "success"}
GREY = "#8b8b8b"
COLUMNS = (
    ("gap", "Gap fill", 160, "w"), ("cloud", "Max. cloud cover", 120, "e"),
    ("newest", "Newest date", 95, "e"), ("age", "Age", 60, "e"),
    ("coverage", "Coverage", 75, "e"), ("clouds", "Clouds (tiles)", 105, "e"),
    ("dates", "Dates used", 80, "e"), ("history", f"Last {REPLAY_DAYS} days", 95, "e"),
)


def units_text(units):
    """'about 0.4 processing units'; without an estimate just that it uses some."""
    if units is None:
        return "uses processing units"
    return f"about {units:.2g} processing units"


def preview_text(units):
    return f"Load preview ({units_text(units)})"


def precise_text(units, mosaic=False):
    if mosaic:
        return f"Precise check: measure each period's coverage of the view ({units_text(units)} per period)"
    return f"Precise check: measure clouds and coverage in the view ({units_text(units)} per variant)"


def sort_value(row, column, today):
    """A comparable value of ``row`` in ``column``; rows without a result sort last."""
    outcome = row.outcome
    variant = row.variant
    if column == "gap":
        return (variant.coverage_mode != "single", variant.lookback_days or 0)
    if column == "cloud":
        return -1 if variant.cloud_limit is None else variant.cloud_limit
    if column == "history":
        return row.complete_share
    if outcome is None:
        return None
    return {
        "newest": outcome.newest.toordinal(),
        "age": (today - outcome.newest).days,
        "coverage": outcome.coverage,
        "clouds": -1 if outcome.clouds is None else outcome.clouds,
        "dates": outcome.dates_used,
    }[column]


def sorted_rows(rows, column, descending, today):
    """The rows sorted by ``column``; Current stays first, rows without a result last."""
    current = [row for row in rows if row.current]
    others = [row for row in rows if not row.current]
    known = [row for row in others if sort_value(row, column, today) is not None]
    unknown = [row for row in others if sort_value(row, column, today) is None]
    # Python's sort is stable: equal values keep the recommended order.
    known.sort(key=lambda row: sort_value(row, column, today), reverse=descending)
    return current + known + unknown


def row_values(row, today):
    outcome = row.outcome
    gap = row.variant.gap_fill_text()
    if row.current:
        gap = "Current: " + gap
    cloud = percent(row.variant.cloud_limit)
    if outcome is None:
        return (gap, cloud, "none", "-", "-", "-", "-", "0%")
    return (gap, cloud, outcome.newest.isoformat(), age_text(outcome.newest, today),
            percent(outcome.coverage), percent(outcome.clouds), str(outcome.dates_used),
            percent(row.complete_share))


class RecommendationWindow:
    """Shows the advice for a place and map zoom; nothing here is saved.

    ``run_check(latitude, longitude, zoom, progress, precise)`` runs in a worker thread
    and returns an ``Advice``; ``progress(page, pages or None, stage="catalogue")`` may be
    called there (stage "masks" while a Precise check reads its masks).
    ``apply(variant, latitude, longitude, zoom)`` takes a row into the Image tab.
    ``find_coordinates()`` returns the Find location coordinates or None;
    ``place_name(latitude, longitude)`` names a place taken from its list, else None;
    ``image_coordinates()`` returns the Image tab's latitude and longitude or None;
    ``load_preview(variant, newest, latitude, longitude, zoom)`` renders a small RGB
    picture in a worker thread (None: no previews for this selection);
    ``estimate(latitude, longitude, zoom)`` gives the processing units of one preview
    and one Precise check mask, or None. With ``mosaic`` the rows are periods and the
    first box is Newest. Precise check starts on for mosaics and with ``precise_default``
    (the profile's Always use precise check).
    """

    def __init__(self, master, *, title, selection, current, latitude, longitude, zoom,
                 zoom_values, run_check, apply, find_coordinates, unavailable="", place_name=None,
                 image_coordinates=None, load_preview=None, estimate=None, precise_available=False,
                 mosaic=False, precise_default=False):
        self.window = tk.Toplevel(master)
        self.window.title(title)
        self._run_check = run_check
        self._apply = apply
        self._find_coordinates = find_coordinates
        self._place_name = place_name
        self._image_coordinates = image_coordinates
        self._load_preview = load_preview
        self._estimate = estimate
        self._estimates = {}
        self._precise_available = precise_available
        self._mosaic = mosaic
        # Previews by (variant, checked place), kept while the window is open.
        self._previews = {}
        self._photos = {}
        self._loading = set()
        self._preview_results = queue.Queue()
        self._preview_after = None
        self._window_wanted = None
        self.preview_window = None
        self._results = queue.Queue()
        self._generation = 0
        self._after_id = None
        self._closed = False
        self._running = False
        self._advice = None
        self._checked = None
        self._row_by_item = {}
        self._unavailable = unavailable
        # Column sorting lives only as long as this window: (column, descending) or None.
        self._sort = None

        frame = ttk.Frame(self.window, padding=14)
        frame.grid(sticky="nsew")
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text=selection, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        place = ttk.Frame(frame)
        place.grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.latitude_var = tk.StringVar(self.window, coordinate_text(latitude))
        self.longitude_var = tk.StringVar(self.window, coordinate_text(longitude))
        self.zoom_var = tk.StringVar(self.window, str(zoom))
        ttk.Label(place, text="Latitude").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(place, textvariable=self.latitude_var, width=11).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(place, text="Longitude").grid(row=0, column=2, padx=(0, 6))
        ttk.Entry(place, textvariable=self.longitude_var, width=12).grid(row=0, column=3, padx=(0, 12))
        ttk.Label(place, text="Map zoom").grid(row=0, column=4, padx=(0, 6))
        ttk.Combobox(place, textvariable=self.zoom_var, values=tuple(zoom_values),
                     state="readonly", width=4).grid(row=0, column=5, padx=(0, 12))
        self.check_button = ttk.Button(place, text="Check", command=self.check)
        self.check_button.grid(row=0, column=6, padx=(0, 6))
        ttk.Label(place, text="Get location from:").grid(row=0, column=7, padx=(6, 6))
        self.find_button = ttk.Button(place, text="Find", command=self.get_from_find)
        self.find_button.grid(row=0, column=8)
        self.image_button = ttk.Button(place, text="Source", command=self.get_from_image)
        self.image_button.grid(row=0, column=9, padx=(6, 0))
        # The Find location place name, while the coordinates are still that place's.
        self.place_name_var = tk.StringVar(self.window)
        self.place_name_label = ttk.Label(place, textvariable=self.place_name_var, wraplength=640)
        self.place_name_label.grid(row=1, column=0, columnspan=10, pady=(4, 0), sticky="w")
        self._show_place_name()
        options = ttk.Frame(frame)
        options.grid(row=2, column=0, pady=(6, 0), sticky="w")
        # Chosen per Check and never saved. On for mosaics when the window opens, whose
        # coverage only it can tell; off for scenes.
        self.precise_var = tk.BooleanVar(self.window, bool((mosaic or precise_default) and precise_available))
        self.precise_check = ttk.Checkbutton(options, text=precise_text(None, mosaic), variable=self.precise_var)
        self.precise_check.grid(row=0, column=0, sticky="w")
        if not precise_available:
            self.precise_check.state(["disabled"])

        progress = ttk.Frame(frame)
        progress.grid(row=3, column=0, pady=(8, 0), sticky="ew")
        progress.columnconfigure(0, weight=1)
        self.state_var = tk.StringVar(self.window)
        self.state_label = ttk.Label(progress, textvariable=self.state_var)
        self.state_label.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(progress, mode="indeterminate", length=220)
        self.progress.grid(row=0, column=1, padx=(12, 0), sticky="e")
        self.progress.grid_remove()
        ttk.Label(frame, text=current, justify="left").grid(row=4, column=0, pady=(6, 0), sticky="w")

        # Regular layers: what the third priority, Newest, would take; its row in the
        # table is orange and previews like any row. Mosaics show Newest as a box.
        # "Priority: " plain and the name orange, like the boxes below.
        newest_label = ttk.Frame(frame)
        ttk.Label(newest_label, text="Priority: ", style=SECTION_LABEL_STYLE).grid(row=0, column=0)
        self.newest_title = ttk.Label(newest_label, text="Newest", style=SECTION_LABEL_STYLE)
        self.newest_title.grid(row=0, column=1)
        self.newest_box = ttk.LabelFrame(frame, labelwidget=newest_label, padding=10)
        self.newest_box.columnconfigure(0, weight=1)
        self.newest_text = ttk.Label(self.newest_box, justify="left", wraplength=780)
        self.newest_text.grid(row=0, column=0, sticky="w")
        if not mosaic:
            self.newest_box.grid(row=5, column=0, pady=(12, 0), sticky="ew")
        boxes = ttk.Frame(frame)
        boxes.grid(row=6, column=0, pady=(12, 0), sticky="ew")
        self.boxes = {}
        first_title = "Newest" if mosaic else "Fewest clouds"
        for column, (key, title_text) in enumerate((("fewest", first_title), ("full", "Data coverage"))):
            boxes.columnconfigure(column, weight=1, uniform="priority")
            # "Priority: " plain, the name in the priority's color (_color_rows).
            title = ttk.Frame(boxes)
            ttk.Label(title, text="Priority: ", style=SECTION_LABEL_STYLE).grid(row=0, column=0)
            name = ttk.Label(title, text=title_text, style=SECTION_LABEL_STYLE)
            name.grid(row=0, column=1)
            box = ttk.LabelFrame(boxes, labelwidget=title, padding=10)
            box.grid(row=0, column=column, padx=(0, 10) if column == 0 else 0, sticky="nsew")
            box.columnconfigure(0, weight=1)
            area = tk.Frame(box, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1], highlightthickness=1,
                            borderwidth=0)
            area.grid_propagate(False)
            area.columnconfigure(0, weight=1)
            area.rowconfigure(0, weight=1)
            area.grid(row=0, column=0, columnspan=2, sticky="w")
            on_theme_change(area, lambda area=area: self._paint_area(area))
            preview = ttk.Button(area, text=preview_text(None),
                                 command=lambda key=key: self._request_preview(self._box_row(key)))
            preview.grid(row=0, column=0)
            picture = tk.Label(area, borderwidth=0, highlightthickness=0)
            picture.grid(row=0, column=0)
            picture.grid_remove()
            heading = ttk.Label(box, font=("Segoe UI", 10, "bold"), wraplength=330, justify="left")
            heading.grid(row=1, column=0, columnspan=2, pady=(8, 2), sticky="w")
            details = ttk.Label(box, justify="left", wraplength=260)
            details.grid(row=2, column=0, sticky="nw")
            apply_button = ttk.Button(box, text="Apply", command=lambda key=key: self._apply_box(key))
            apply_button.grid(row=2, column=1, padx=(8, 0), sticky="se")
            self.boxes[key] = {"heading": heading, "details": details, "apply": apply_button,
                               "preview": preview, "picture": picture, "name": name}

        others = ttk.LabelFrame(frame, text="Compared variants", padding=10)
        others.grid(row=7, column=0, pady=(12, 0), sticky="nsew")
        frame.rowconfigure(7, weight=1)
        others.columnconfigure(0, weight=1)
        others.rowconfigure(0, weight=1)
        self._style_dots_items()
        self.tree = ttk.Treeview(others, columns=[key for key, *_ in COLUMNS], height=8,
                                 selectmode="browse", show="tree headings", style=DOTS_STYLE)
        # A fixed column of dots, one per priority that chose the row; it never
        # grows into Gap fill, and its border cannot be dragged.
        self.tree.column("#0", width=DOTS_COLUMN_WIDTH, minwidth=DOTS_COLUMN_WIDTH, stretch=False)
        self.tree.heading("#0", text="")
        self._dot_images = {}
        # Layouts belong to a theme: the light and dark themes each need it.
        on_theme_change(self.tree, self._style_dots_items)
        self._row_dots = {}  # item -> palette keys of its dots, in priority order
        for key, text, width, anchor in COLUMNS:
            self.tree.heading(key, text=text, anchor=anchor, command=lambda key=key: self.sort_by(key))
            self.tree.column(key, width=width, minwidth=width, anchor=anchor, stretch=key == "gap")
        if mosaic:
            # A period has no cloud limit, cloud estimate or 90-day replay.
            self.tree.configure(displaycolumns=("gap", "newest", "age", "coverage"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(others, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_buttons())
        self.tree.bind("<Double-1>", self._row_double_clicked)
        self.tree.bind("<Button-1>", self._keep_dots_column, add="+")
        on_theme_change(self.tree, self._color_rows)
        row_actions = ttk.Frame(others)
        row_actions.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="ew")
        row_actions.columnconfigure(0, weight=1)
        ttk.Label(row_actions, text="Select a row to preview or apply it.").grid(row=0, column=0, sticky="w")
        self.row_preview = ttk.Button(row_actions, text=preview_text(None), command=self._row_preview)
        self.row_preview.grid(row=0, column=1, padx=(0, 6))
        self.row_apply = ttk.Button(row_actions, text="Apply", command=self._apply_row)
        self.row_apply.grid(row=0, column=2)

        footer = ttk.Frame(frame)
        footer.grid(row=8, column=0, pady=(10, 0), sticky="ew")
        footer.columnconfigure(0, weight=1)
        note = ttk.Label(
            footer, justify="left", wraplength=600,
            # Key facts only; the details are in Info > Recommendation and the user guide.
            text=FOOTER_TEXT,
        )
        note.grid(row=0, column=0, sticky="w")  # In the normal text color.
        ttk.Button(footer, text="Close", command=self.close).grid(row=0, column=1, padx=(12, 0), sticky="se")

        for variable in (self.latitude_var, self.longitude_var, self.zoom_var):
            variable.trace_add("write", lambda *_args: self._refresh_state())
        for variable in (self.latitude_var, self.longitude_var):
            variable.trace_add("write", lambda *_args: self._show_place_name())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._fit_minsize()
        self.window.bind("<Destroy>", self._destroyed, add="+")
        self._show_advice(None)
        if unavailable:
            self._set_state(unavailable, "warning")
            self.check_button.state(["disabled"])
        else:
            # Nothing is asked before Check: place, zoom and Precise check can be set first.
            self._set_state(READY_TEXT, "link")

    # Place and zoom -------------------------------------------------------

    def place(self):
        """(latitude, longitude, zoom) from the fields, or None while one is invalid."""
        coordinates = parse_coordinates(f"{self.latitude_var.get()}, {self.longitude_var.get()}")
        try:
            zoom = int(self.zoom_var.get())
        except ValueError:
            return None
        return (*coordinates, zoom) if coordinates else None

    def set_place(self, latitude, longitude):
        """Find location's Transfer fills the fields; the results then wait for Check."""
        self.latitude_var.set(coordinate_text(latitude))
        self.longitude_var.set(coordinate_text(longitude))
        self.window.lift()

    def _show_place_name(self):
        coordinates = parse_coordinates(f"{self.latitude_var.get()}, {self.longitude_var.get()}")
        name = self._place_name(*coordinates) if coordinates and self._place_name else None
        self.place_name_var.set(f"Place: {name}" if name else "")
        if name:
            self.place_name_label.grid()
        else:
            self.place_name_label.grid_remove()

    def get_from_find(self):
        coordinates = self._find_coordinates() if self._find_coordinates else None
        if coordinates is None:
            self._set_state("Select a place in Find location first.", "warning")
            return
        self.set_place(*coordinates)

    def get_from_image(self):
        """The Image tab's latitude and longitude, including unsaved edits."""
        coordinates = self._image_coordinates() if self._image_coordinates else None
        if coordinates is None:
            self._set_state("Enter a valid latitude and longitude in the Image tab first.", "warning")
            return
        self.set_place(*coordinates)

    # Checking ----------------------------------------------------------------

    def check(self):
        place = self.place()
        if place is None or self._unavailable:
            if place is None:
                self._set_state("Enter latitude and longitude as decimal degrees.", "warning")
            return
        self._generation += 1
        generation = self._generation
        self._checked = place
        self._running = True
        self.check_button.state(["disabled"])
        self._set_state("Fetching data...", "link")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.grid()
        self.progress.start(12)
        run_check, results = self._run_check, self._results
        precise = bool(self._precise_available and self.precise_var.get())

        def progress(page, pages, stage="catalogue"):
            results.put((generation, "progress", (page, pages, stage)))

        def work():
            try:
                results.put((generation, "done", run_check(*place, progress, precise)))
            except Exception as exc:  # Shown in the window; nothing is saved.
                results.put((generation, "error", str(exc) or "The check failed."))

        threading.Thread(target=work, name="MarbleScape-Copernicus-advice", daemon=True).start()
        if self._after_id is None:
            self._after_id = self.window.after(100, self._poll)

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        while True:
            try:
                generation, kind, value = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            if kind == "progress":
                page, pages, stage = value
                if pages:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=pages, value=page)
                    self._set_state(f"Measuring the view {page}/{pages}..." if stage == "masks"
                                    else f"Fetching data {page}/{pages}...", "link")
                else:
                    self._set_state(f"Fetching data, page {page}...", "link")
                continue
            self._running = False
            self.progress.stop()
            self.progress.grid_remove()
            self.check_button.state(["!disabled"])
            if kind == "error":
                self._advice = None
                self._show_advice(None)
                self._set_state(value, "warning")
            else:
                self._advice = value
                self._show_advice(value)
                self._refresh_state()
        if self._running:
            self._after_id = self.window.after(100, self._poll)

    # Results -------------------------------------------------------------------

    def _show_advice(self, result):
        self.tree.delete(*self.tree.get_children())
        self._row_by_item = {}
        texts = {"fewest": ("-", ""), "full": ("-", "")}
        if result is not None:
            rows = (sorted_rows(result.rows, self._sort[0], self._sort[1], result.today)
                    if self._sort else result.rows)
            for row in rows:
                item = self.tree.insert("", "end", values=row_values(row, result.today))
                self._row_by_item[item] = row
            texts["fewest"] = self._fewest_text(result)
            texts["full"] = self._full_text(result)
        for key, (heading, details) in texts.items():
            self.boxes[key]["heading"].configure(text=heading)
            self.boxes[key]["details"].configure(text=details)
        self.newest_text.configure(text=self._newest_text(result))
        self._set_headings()
        self._color_rows()
        self._refresh_buttons()
        self._fit_minsize()

    def _newest_row(self):
        if self._advice is None or self._mosaic:
            return None
        return newest_row(self._advice)

    def _newest_text(self, result):
        row = newest_row(result) if result is not None else None
        if row is None:
            return ("The newest acquisition on top, whatever its clouds. Press Check to find it."
                    if result is None else "No acquisition at the centre of this view.")
        return (f"{row.variant.settings_text()} · newest {row.outcome.newest.isoformat()}, "
                f"coverage {percent(row.outcome.coverage)}\n"
                f"Preview: select its row with the orange dot in the table and press Load preview, "
                f"or double-click it.")

    def _fit_minsize(self):
        """Never smaller than its content, which grows with the results; larger is fine."""
        self.window.update_idletasks()
        width, height = self.window.minsize()
        self.window.minsize(max(width, self.window.winfo_reqwidth()),
                            max(height, self.window.winfo_reqheight()))

    def sort_by(self, column):
        """First click ascending, the next descending; Current stays on top."""
        descending = self._sort == (column, False)
        self._sort = (column, descending)
        selected = self._selected_row()
        self._show_advice(self._advice)
        for item, row in self._row_by_item.items():
            if row is selected:
                self.tree.selection_set(item)
                self.tree.see(item)

    def _set_headings(self):
        """Column headings with the sort arrow; measured clouds are clouds in the view."""
        measured = self._advice is not None and any(
            row.outcome is not None and row.outcome.measured_clouds for row in self._advice.rows)
        for key, text, _width, _anchor in COLUMNS:
            if key == "clouds" and measured:
                text = "Clouds in view"
            if self._mosaic:
                text = {"gap": "Period", "newest": "Date"}.get(key, text)
            if self._sort is not None and self._sort[0] == key:
                text += " ▼" if self._sort[1] else " ▲"
            self.tree.heading(key, text=text)

    @staticmethod
    def _clouds_line(outcome):
        if outcome.measured_clouds:
            return f"Clouds {percent(outcome.clouds)} of the view (measured)"
        return f"Clouds about {percent(outcome.clouds)} (tile estimate)"

    def _fewest_text(self, result):
        row = result.fewest_clouds
        if result.mosaic:
            if row is None:
                return "No period", "The catalogue lists no period of this mosaic for the view."
            return row.variant.settings_text(), "\n".join((
                self._coverage_line(row.outcome), f"Catalogue date {row.outcome.newest.isoformat()}"))
        if not result.cloud_estimates:
            return "Not applicable", "This layer has no cloud estimates (for example radar)."
        if row is None:
            return ("No variant reaches the coverage",
                    f"None covers at least {FEWEST_CLOUDS_COVERAGE:g}% of the view.")
        outcome = row.outcome
        return row.variant.settings_text(), "\n".join((
            self._clouds_line(outcome),
            self._coverage_line(outcome),
            f"Newest {outcome.newest.isoformat()} ({age_text(outcome.newest, result.today)} old)",
            f"Last {REPLAY_DAYS} days: at least {FEWEST_CLOUDS_COVERAGE:g}% covered on "
            f"{percent(row.covered_share)} of the days, also clear (at most {CLEAR_CLOUDS:g}% "
            f"clouds) on {percent(row.clear_share)}",
        ))

    def _full_text(self, result):
        row = result.full_coverage
        if result.mosaic:
            if row is None:
                return ("Needs Precise check", "Tick Precise check and press Check: it measures "
                        "each period's coverage of the view.")
            lines = [self._coverage_line(row.outcome), f"Catalogue date {row.outcome.newest.isoformat()}"]
            if not result.full_coverage_reached:
                lines.insert(0, f"No period reaches {FULL_COVERAGE:g}%; this one covers the most.")
            return row.variant.settings_text(), "\n".join(lines)
        if row is None:
            return "No acquisition", "The catalogue lists no acquisition at the centre of this view."
        outcome = row.outcome
        lines = [self._coverage_line(outcome)]
        if not result.full_coverage_reached:
            lines.insert(0, f"No variant reaches {FULL_COVERAGE:g}%; this one covers the most.")
        if outcome.clouds is not None:
            lines.append(self._clouds_line(outcome))
        lines += [f"Newest {outcome.newest.isoformat()} ({age_text(outcome.newest, result.today)} old)",
                  f"Last {REPLAY_DAYS} days: complete on {percent(row.complete_share)} of the days"]
        return row.variant.settings_text(), "\n".join(lines)

    def _coverage_line(self, outcome):
        if outcome.coverage is None:
            return "Coverage: measured by Precise check"
        measured = " (measured)" if outcome.measured else ""
        if self._mosaic:
            return f"Coverage {percent(outcome.coverage)}{measured}"
        count = outcome.dates_used
        return (f"Coverage {percent(outcome.coverage)}{measured} from {count} "
                f"acquisition{'' if count == 1 else 's'}")

    def _color_rows(self):
        colors = palette(self.tree)
        priority_colors = MOSAIC_PRIORITY_COLORS if self._mosaic else PRIORITY_COLORS
        self.newest_title.configure(foreground=colors["newest"])
        newest = self._newest_row()
        self.tree.tag_configure("current", foreground=GREY)
        self.tree.tag_configure("outdated", foreground=GREY)
        outdated = self._outdated()
        self._row_dots = {}
        for item, row in self._row_by_item.items():
            # Rows keep the normal text color; a dot per priority that chose the
            # row, in the order of the boxes: blue, green, then Newest's orange.
            dots = [priority_colors[key] for key in ("fewest", "full") if self._box_row(key) is row]
            if row is newest:
                dots.append("newest")
            if row.current:
                dots = []
            self._row_dots[item] = dots
            self.tree.item(item, image=self._dots_image(dots, outdated),
                           tags=("outdated",) if outdated else ("current",) if row.current else ())
        for key, box in self.boxes.items():
            # The heading, a recommendation or a notice in its place ("No variant ..."),
            # takes the priority's color; the lines below keep the normal text color.
            box["heading"].configure(foreground=GREY if outdated else colors[priority_colors[key]])
            box["name"].configure(foreground=colors[priority_colors[key]])
            box["details"].configure(foreground=GREY if outdated else "")

    def _outdated(self):
        return self._checked is not None and self.place() != self._checked

    def _refresh_state(self):
        outdated = self._outdated()
        if self._advice is not None or outdated:
            self._set_state("Place or zoom changed: press Check for new results." if outdated else
                            f"Results for {self._checked[0]:g}, {self._checked[1]:g} at map zoom "
                            f"{self._checked[2]} ({self._advice.acquisitions} catalogue tiles"
                            + (f", {self._advice.masks} view masks" if self._advice.masks else "")
                            + ").",
                            "warning" if outdated else "link")
        self._color_rows()
        self._refresh_buttons()

    def _units(self):
        """(preview, mask) processing units for the place in the fields, or (None, None)."""
        place = self.place()
        if self._estimate is None or place is None:
            return None, None
        if place not in self._estimates:
            self._estimates[place] = self._estimate(*place) or (None, None)
        return self._estimates[place]

    def _refresh_buttons(self):
        usable = self._advice is not None and not self._outdated()
        for key, box in self.boxes.items():
            row = self._box_row(key)
            box["apply"].state(["!disabled"] if usable and row is not None else ["disabled"])
        selected = self._selected_row()
        self.row_apply.state(["!disabled"] if usable and selected is not None
                             and not selected.current and selected.outcome is not None else ["disabled"])
        self._refresh_previews()

    def _set_state(self, text, color_key):
        self.state_var.set(text)
        self.state_label.configure(foreground=palette(self.state_label)[color_key])

    def _paint_area(self, area):
        dark = current_mode(area) == "dark"
        area.configure(background="#2b2b2b" if dark else "#e6e6e6",
                       highlightbackground="#454545" if dark else "#c4c4c4")
        for child in area.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(background=area.cget("background"))

    # Applying --------------------------------------------------------------------

    def _box_row(self, key):
        if self._advice is None:
            return None
        return self._advice.fewest_clouds if key == "fewest" else self._advice.full_coverage

    def _selected_row(self):
        return self._row_by_item.get(next(iter(self.tree.selection()), None))

    def _apply_box(self, key):
        self._apply_row_value(self._box_row(key))

    def _apply_row(self):
        self._apply_row_value(self._selected_row())

    def _apply_row_value(self, row):
        if row is None or self._outdated() or self._checked is None:
            return
        latitude, longitude, zoom = self._checked
        self._apply(row.variant, latitude, longitude, zoom)
        self._set_state(f"Applied {row.variant.settings_text()} to the Image tab.",
                        "success")

    # Previews --------------------------------------------------------------------

    def _preview_key(self, row):
        return (row.variant, self._checked)

    def _can_preview(self, row):
        return (self._load_preview is not None and row is not None and row.outcome is not None
                and self._advice is not None and not self._outdated())

    def _style_dots_items(self):
        style = ttk.Style(self.window)
        style.layout(f"{DOTS_STYLE}.Item", _without_indicator(style.layout("Treeview.Item")))

    def _keep_dots_column(self, event):
        """The dots column keeps its width: its border does not drag."""
        if (self.tree.identify_region(event.x, event.y) == "separator"
                and self.tree.identify_column(event.x) == "#0"):
            return "break"
        return None

    def _dots_image(self, keys, outdated):
        colors = palette(self.tree)
        fills = tuple(GREY if outdated else colors[key] for key in keys)
        cache_key = (fills, current_mode(self.tree))
        if cache_key not in self._dot_images:
            image = Image.new("RGBA", (DOTS_WIDTH, DOT_SIZE + 2), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            for index, fill in enumerate(fills):
                left = index * (DOT_SIZE + DOT_GAP)
                draw.ellipse((left, 1, left + DOT_SIZE - 1, DOT_SIZE), fill=fill)
            self._dot_images[cache_key] = ImageTk.PhotoImage(image, master=self.window)
        return self._dot_images[cache_key]

    def _row_double_clicked(self, event):
        """A double-click on a row loads or shows its preview, like Load preview."""
        item = self.tree.identify_row(event.y)
        if item in self._row_by_item:
            self.tree.selection_set(item)
            self._refresh_buttons()
            self._row_preview()
        return "break"

    def _row_preview(self):
        row = self._selected_row()
        if row is None:
            return
        title = f"{row.variant.settings_text()} · {row.outcome.newest.isoformat()}" if row.outcome else ""
        if self._preview_key(row) in self._previews:
            self._show_preview_window(self._preview_key(row), title)
            return
        if self._can_preview(row):
            self._window_wanted = (self._preview_key(row), title)
            self._request_preview(row)

    def _request_preview(self, row):
        """Render ``row``'s picture in a worker thread; a loaded one is reused."""
        if not self._can_preview(row):
            return
        key = self._preview_key(row)
        if key in self._previews or key in self._loading:
            self._refresh_previews()
            return
        self._loading.add(key)
        latitude, longitude, zoom = self._checked
        variant, newest = row.variant, row.outcome.newest
        load, results = self._load_preview, self._preview_results

        def work():
            try:
                results.put((key, "done", load(variant, newest, latitude, longitude, zoom)))
            except Exception as exc:  # Shown in the window; nothing is saved.
                results.put((key, "error", str(exc) or "The preview failed."))

        threading.Thread(target=work, name="MarbleScape-Copernicus-preview", daemon=True).start()
        self._refresh_previews()
        if self._preview_after is None:
            self._preview_after = self.window.after(100, self._poll_previews)

    def _poll_previews(self):
        self._preview_after = None
        if self._closed:
            return
        while True:
            try:
                key, kind, value = self._preview_results.get_nowait()
            except queue.Empty:
                break
            self._loading.discard(key)
            wanted = self._window_wanted
            if wanted is not None and wanted[0] == key:
                self._window_wanted = None
            if kind == "done":
                self._previews[key] = value
                if wanted is not None and wanted[0] == key:
                    self._show_preview_window(key, wanted[1])
            else:
                self._set_state(value, "warning")
        self._refresh_previews()
        if self._loading:
            self._preview_after = self.window.after(100, self._poll_previews)

    def _photo(self, key):
        if key not in self._photos:
            self._photos[key] = ImageTk.PhotoImage(self._previews[key], master=self.window)
        return self._photos[key]

    def _refresh_previews(self):
        preview_units, mask_units = self._units()
        if self._precise_available:
            self.precise_check.configure(text=precise_text(mask_units, self._mosaic))
        for key, box in self.boxes.items():
            row = self._box_row(key)
            preview_key = self._preview_key(row) if row is not None else None
            if preview_key in self._previews:
                box["picture"].configure(image=self._photo(preview_key))
                box["picture"].grid()
                box["preview"].grid_remove()
                continue
            box["picture"].grid_remove()
            box["picture"].configure(image="")
            box["preview"].grid()
            loading = preview_key in self._loading
            box["preview"].configure(text=LOADING_PREVIEW if loading else preview_text(preview_units))
            box["preview"].state(["!disabled"] if self._can_preview(row) and not loading else ["disabled"])
        row = self._selected_row()
        preview_key = self._preview_key(row) if row is not None else None
        if preview_key in self._previews:
            # Like Apply, it waits for Check after the place changed.
            self.row_preview.configure(text=SHOW_PREVIEW)
            self.row_preview.state(["disabled"] if self._outdated() else ["!disabled"])
        else:
            loading = preview_key in self._loading
            self.row_preview.configure(text=LOADING_PREVIEW if loading else preview_text(preview_units))
            self.row_preview.state(["!disabled"] if self._can_preview(row) and not loading else ["disabled"])

    def _show_preview_window(self, key, title):
        """One small window shows the table row previews, the latest on top."""
        if self.preview_window is None or not self.preview_window.winfo_exists():
            self.preview_window = tk.Toplevel(self.window)
            self.preview_window.resizable(False, False)
            frame = ttk.Frame(self.preview_window, padding=10)
            frame.grid(sticky="nsew")
            self.preview_picture = ttk.Label(frame)
            self.preview_picture.grid(row=0, column=0)
            self.preview_caption = ttk.Label(frame)
            self.preview_caption.grid(row=1, column=0, pady=(6, 0), sticky="w")
        self.preview_window.title("Preview")
        self.preview_picture.configure(image=self._photo(key))
        self.preview_caption.configure(text=title)
        self.preview_window.lift()

    # Closing ---------------------------------------------------------------------

    def _destroyed(self, event):
        if event.widget is self.window:
            self._closed = True

    def close(self):
        self._closed = True
        self._generation += 1
        for name in ("_after_id", "_preview_after"):
            if getattr(self, name) is not None:
                try:
                    self.window.after_cancel(getattr(self, name))
                except tk.TclError:
                    pass
                setattr(self, name, None)
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    @property
    def is_open(self):
        try:
            return not self._closed and bool(self.window.winfo_exists())
        except tk.TclError:
            return False
