"""Find a place by name and transfer its latitude/longitude to a source.

The search uses OpenStreetMap's Nominatim service. Its usage policy
(https://operations.osmfoundation.org/policies/nominatim/) asks for at most one
request per second, an identifying User-Agent, cached results, no
search-as-you-type and visible attribution; the endpoint is configurable
(``[service] location_search_endpoint``) so it can change without an update.
"""

from collections import OrderedDict
import http.client
import json
import queue
import re
import ssl
import threading
import time
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from marblescape_network import NETWORK_ACTIVITY
from marblescape_source_layout import configure_source_columns, source_label_column_minsize
from marblescape_theme import entry_placeholder, keep_palette_color

DEFAULT_SEARCH_ENDPOINT = "https://nominatim.openstreetmap.org/search"
# Preview opens the Copernicus Browser at the place and zoom (zoom, lat, lng);
# its product/layer/date links need a parameter the Browser encrypts itself.
# The EUMETSAT viewer takes only a layer, so EUMETSAT has no Preview.
COPERNICUS_BROWSER_URL = "https://browser.dataspace.copernicus.eu/"
ATTRIBUTION = "© OpenStreetMap contributors"
# How to use the section, in the label column beside the fields.
STEPS = (
    "1. Search a place.\n"
    "2. Click a result: its coordinates appear below, where you can edit them.\n"
    "3. Transfer (or a double-click) fills Latitude and Longitude.\n\n"
    "Preview opens the place in the Copernicus Browser. Coordinates copied from a map "
    "can be pasted."
)
FIND_PLACEHOLDER = "type..."
# The most Nominatim returns for one search.
RESULT_LIMIT = 40
# Names in the language of the user interface.
RESULT_LANGUAGE = "en"
MIN_REQUEST_INTERVAL_SECONDS = 1.0
CACHE_SIZE = 64
COORDINATE_DECIMALS = 6

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = [None]
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()

_NUMBER = r"([+-]?\d{1,3}(?:\.\d+)?)\s*°?\s*([NSEWnsew])?"
_COORDINATES = re.compile(rf"^\s*{_NUMBER}\s*(?:[,;]\s*|\s+){_NUMBER}\s*$")
# Categories whose type adds nothing to the name ("Hamburg (city)").
_PLAIN_CATEGORIES = {"place", "boundary"}


class LocationSearchError(RuntimeError):
    pass


def parse_coordinates(text):
    """(latitude, longitude) from text such as "53.55, 9.99" or "53.55° N 9.99° E".

    Returns None for anything else, including values outside the globe.
    """
    match = _COORDINATES.match(str(text or ""))
    if match is None:
        return None
    first, first_side, second, second_side = match.groups()
    first, second = float(first), float(second)
    first_side = (first_side or "").upper()
    second_side = (second_side or "").upper()
    if first_side in {"E", "W"} and second_side in {"", "N", "S"}:
        first, second = second, first
        first_side, second_side = second_side, first_side
    if first_side in {"E", "W"} or second_side in {"N", "S"}:
        return None
    if first_side == "S":
        first = -abs(first)
    if second_side == "W":
        second = -abs(second)
    if not (-90 <= first <= 90 and -180 <= second <= 180):
        return None
    return first, second


def format_coordinates(latitude, longitude):
    return f"{coordinate_text(latitude)}, {coordinate_text(longitude)}"


def coordinate_text(value):
    return f"{float(value):.{COORDINATE_DECIMALS}f}".rstrip("0").rstrip(".")


def copernicus_browser_url(latitude, longitude, zoom):
    """The Copernicus Browser at this place and map zoom."""
    return COPERNICUS_BROWSER_URL + "?" + urllib.parse.urlencode({
        "zoom": int(zoom), "lat": coordinate_text(latitude), "lng": coordinate_text(longitude)})


def _place_label(item):
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    display = str(item.get("display_name") or "").strip()
    name = str(item.get("name") or "").strip() or display.split(",")[0].strip()
    region = next((str(address[key]) for key in ("state", "region", "province", "county")
                   if address.get(key)), "")
    parts = []
    for part in (region, str(address.get("country") or "")):
        if part and part != name and part not in parts:
            parts.append(part)
    kind = str(item.get("type") or "").replace("_", " ")
    if item.get("category") in _PLAIN_CATEGORIES or kind in {"", "yes"}:
        kind = ""
    label = name + (f" ({kind})" if kind else "")
    return label + (" - " + ", ".join(parts) if parts else "")


def parse_results(payload):
    """Places of a Nominatim jsonv2 answer: dicts with label, latitude, longitude."""
    if not isinstance(payload, list):
        raise LocationSearchError("The place search returned an unexpected answer.")
    places = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            latitude, longitude = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        places.append({"label": _place_label(item), "latitude": latitude, "longitude": longitude})
    return places


_HTTPS_CONTEXT = []


def _https_context():
    """Current Mozilla roots plus the system trust store, as for Copernicus."""
    if not _HTTPS_CONTEXT:
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
            context.load_default_certs()
        except Exception:
            context = ssl.create_default_context()
        _HTTPS_CONTEXT.append(context)
    return _HTTPS_CONTEXT[0]


def _urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_https_context())


def _wait_for_turn(clock, wait):
    """At most one request per MIN_REQUEST_INTERVAL_SECONDS, across all searches."""
    last = _LAST_REQUEST[0]
    if last is not None:
        remaining = MIN_REQUEST_INTERVAL_SECONDS - (clock() - last)
        if remaining > 0:
            wait(remaining)
    _LAST_REQUEST[0] = clock()


def search_places(query, endpoint=DEFAULT_SEARCH_ENDPOINT, user_agent="MarbleScape",
                  timeout=30, opener=None, clock=time.monotonic, wait=None):
    """Places matching ``query``; repeated searches come from the cache."""
    query = " ".join(str(query or "").split())
    if not query:
        return []
    endpoint = str(endpoint or DEFAULT_SEARCH_ENDPOINT)
    key = (endpoint, query.casefold())
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return [dict(place) for place in _CACHE[key]]
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "addressdetails": 1,
        "limit": RESULT_LIMIT, "accept-language": RESULT_LANGUAGE,
    })
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with _REQUEST_LOCK:
        _wait_for_turn(clock, wait or NETWORK_ACTIVITY.wait)
        try:
            with NETWORK_ACTIVITY.opening(opener or _urlopen, request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in {403, 429}:
                raise LocationSearchError(
                    "The OpenStreetMap search refused the request for now. Try again later."
                ) from exc
            raise LocationSearchError(f"The OpenStreetMap search failed (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            raise LocationSearchError(f"The OpenStreetMap search is not reachable: {reason}") from exc
        except ValueError as exc:
            raise LocationSearchError("The place search returned an unexpected answer.") from exc
    places = parse_results(payload)
    with _CACHE_LOCK:
        _CACHE[key] = [dict(place) for place in places]
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return places


class LocationSearch:
    """Find, results, Coordinates and Transfer inside ``parent`` (a section frame).

    A click on a result copies its coordinates to the Coordinates field, where
    they can still be edited; Transfer (or a double-click on a result) hands
    them to ``on_transfer(latitude, longitude)``. Preview asks
    ``preview(coordinates or None)`` for (url, status) and opens the url in the
    browser. Nothing here is saved.
    """

    def __init__(self, parent, on_transfer, endpoint=DEFAULT_SEARCH_ENDPOINT,
                 user_agent="MarbleScape", timeout=30, latitude_limit=90.0,
                 search=None, open_url=None, preview=None):
        self.frame = parent
        configure_source_columns(self.frame)
        self._on_transfer = on_transfer
        self._preview = preview
        # The host sets it for the selected source.
        self.latitude_limit = float(latitude_limit)
        self._search = search or (lambda text: search_places(
            text, endpoint=endpoint, user_agent=user_agent, timeout=timeout))
        self._open_url = open_url or webbrowser.open_new_tab
        self._results = queue.Queue()
        self._generation = 0
        self._after_id = None
        self._closed = False
        self._places = {}
        # (coordinates text, place label) of the last transfer, kept after the list changes.
        self._transferred = None

        self.query_var = tk.StringVar(self.frame)
        self.coordinates_var = tk.StringVar(self.frame)
        self.status_var = tk.StringVar(self.frame)

        # No labels in front: the fields keep the value column; the search field
        # says in grey what it takes while it is empty.
        find_frame = ttk.Frame(self.frame)
        find_frame.grid(row=0, column=1, pady=3, sticky="ew")
        find_frame.columnconfigure(0, weight=1)
        self.query_entry = ttk.Entry(find_frame, textvariable=self.query_var, width=1)
        self.query_entry.grid(row=0, column=0, sticky="ew")
        self.query_placeholder = entry_placeholder(self.query_entry, self.query_var, FIND_PLACEHOLDER)
        self.query_entry.bind("<Return>", lambda _event: self.start_search())
        self.query_entry.bind("<KP_Enter>", lambda _event: self.start_search())
        self.search_button = ttk.Button(find_frame, text="Search", command=self.start_search)
        self.search_button.grid(row=0, column=1, padx=(6, 0))
        self.clear_button = ttk.Button(find_frame, text="Clear", command=self.clear)
        self.clear_button.grid(row=0, column=2, padx=(6, 0))
        self.preview_button = ttk.Button(find_frame, text="Preview", command=self.preview)
        self.preview_button.grid(row=0, column=3, padx=(6, 0))

        list_frame = ttk.Frame(self.frame)
        list_frame.grid(row=1, column=1, pady=3, sticky="ew")
        list_frame.columnconfigure(0, weight=1)
        self.results = ttk.Treeview(list_frame, columns=("latitude", "longitude"),
                                    height=5, selectmode="browse")
        self.results.heading("#0", text="Place", anchor="w")
        self.results.heading("latitude", text="Latitude", anchor="e")
        self.results.heading("longitude", text="Longitude", anchor="e")
        self.results.column("#0", width=200, minwidth=120, stretch=True)
        self.results.column("latitude", width=90, minwidth=70, stretch=False, anchor="e")
        self.results.column("longitude", width=90, minwidth=70, stretch=False, anchor="e")
        self.results.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.results.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results.configure(yscrollcommand=scrollbar.set)
        self.results.bind("<<TreeviewSelect>>", self._result_selected)
        self.results.bind("<Double-1>", self._result_double_clicked)
        self.results.bind("<Return>", lambda _event: self.transfer())

        notes = ttk.Frame(self.frame)
        notes.grid(row=2, column=1, sticky="ew")
        notes.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(notes, textvariable=self.status_var, wraplength=380, justify="left")
        self.status_label.grid(row=0, column=0, sticky="w")
        # Messages in the notice blue, like "This tab saves automatically.".
        keep_palette_color(self.status_label, "foreground", "link")
        ttk.Label(notes, text=ATTRIBUTION).grid(row=0, column=1, padx=(6, 0), sticky="ne")

        transfer_frame = ttk.Frame(self.frame)
        transfer_frame.grid(row=3, column=1, pady=3, sticky="ew")
        transfer_frame.columnconfigure(0, weight=1)
        self.coordinates_entry = ttk.Entry(transfer_frame, textvariable=self.coordinates_var, width=1)
        self.coordinates_entry.grid(row=0, column=0, sticky="ew")
        self.coordinates_entry.bind("<Return>", lambda _event: self.transfer())
        self.coordinates_entry.bind("<KP_Enter>", lambda _event: self.transfer())
        self.transfer_button = ttk.Button(transfer_frame, text="Transfer", command=self.transfer)
        self.transfer_button.grid(row=0, column=1, padx=(6, 0))
        # The fields have no labels: the label column holds the steps instead,
        # wrapped to its width so it never widens the column.
        self.steps_label = ttk.Label(
            self.frame, text=STEPS, justify="left",
            wraplength=max(120, source_label_column_minsize(self.frame) - 12),
        )
        self.steps_label.grid(row=0, column=0, rowspan=4, padx=(0, 10), pady=3, sticky="nw")
        # Settings widens every other label's wrap to the window; this one keeps its own.
        self.steps_label._marblescape_own_wrap = True
        self.frame.bind("<Destroy>", self._destroyed, add="+")

    def start_search(self):
        text = self.query_var.get().strip()
        if not text:
            self.status_var.set("Enter a place, address or coordinates.")
            return "break"
        coordinates = parse_coordinates(text)
        if coordinates is not None:
            # Coordinates need no search.
            self._generation += 1
            self._show_places([])
            self.coordinates_var.set(format_coordinates(*coordinates))
            self.status_var.set("Coordinates recognized; use Transfer to fill Latitude and Longitude.")
            return "break"
        self._generation += 1
        generation = self._generation
        self.status_var.set("Searching…")
        self.search_button.configure(state="disabled")

        # The worker holds no Tk object: Tcl must never be freed in its thread.
        search, results = self._search, self._results

        def work():
            try:
                result = (True, search(text))
            except Exception as exc:
                result = (False, str(exc) or "The place search failed.")
            results.put((generation, result))

        threading.Thread(target=work, name="MarbleScape-location-search", daemon=True).start()
        if self._after_id is None:
            self._after_id = self.frame.after(50, self._poll)
        return "break"

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        while True:
            try:
                generation, (success, value) = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            self.search_button.configure(state="normal")
            if success:
                self._show_places(value)
                self.status_var.set(
                    "No places found." if not value else
                    "1 place found." if len(value) == 1 else f"{len(value)} places found.")
            else:
                self._show_places([])
                self.status_var.set(value)
        if str(self.search_button.cget("state")) == "disabled":
            self._after_id = self.frame.after(50, self._poll)

    def _show_places(self, places):
        self.results.delete(*self.results.get_children())
        self._places = {}
        for place in places:
            item = self.results.insert("", "end", text=place["label"], values=(
                coordinate_text(place["latitude"]), coordinate_text(place["longitude"])))
            self._places[item] = place

    def _result_selected(self, _event=None):
        place = self._places.get(next(iter(self.results.selection()), None))
        if place is not None:
            self.coordinates_var.set(format_coordinates(place["latitude"], place["longitude"]))

    def _result_double_clicked(self, event):
        item = self.results.identify_row(event.y)
        if item in self._places:
            self.results.selection_set(item)
            self._result_selected()
            self.transfer()
        return "break"

    def transfer(self):
        coordinates = parse_coordinates(self.coordinates_var.get())
        if coordinates is None:
            self.status_var.set("Enter coordinates as latitude, longitude, e.g. 53.55, 9.99.")
            return "break"
        latitude, longitude = coordinates
        if abs(latitude) > self.latitude_limit:
            limit = coordinate_text(self.latitude_limit)
            self.status_var.set(f"This image source supports latitudes from -{limit} to {limit}.")
            return "break"
        key = format_coordinates(latitude, longitude)
        self._transferred = (key, self._listed_name(key))
        self._on_transfer(latitude, longitude)
        self.status_var.set("Transferred to Latitude and Longitude.")
        return "break"

    def place_name(self, latitude, longitude):
        """The label of the listed or last transferred place at these coordinates, else None."""
        key = format_coordinates(latitude, longitude)
        if self._transferred is not None and self._transferred[0] == key and self._transferred[1]:
            return self._transferred[1]
        return self._listed_name(key)

    def _listed_name(self, key):
        selected = [self._places[item] for item in self.results.selection() if item in self._places]
        for place in selected + list(self._places.values()):
            if format_coordinates(place["latitude"], place["longitude"]) == key:
                return place["label"]
        return None

    def clear(self):
        self._generation += 1
        self.query_var.set("")
        self._show_places([])
        self.status_var.set("")
        self.search_button.configure(state="normal")
        self.query_entry.focus_set()

    def set_preview_available(self, available):
        self.preview_button.state(["!disabled"] if available else ["disabled"])

    def preview(self):
        """Open the source's viewer at the Coordinates field's place, else at the saved one."""
        result = self._preview(parse_coordinates(self.coordinates_var.get())) if self._preview else None
        if result is None:
            self.status_var.set("No preview is available for this image source.")
            return
        url, status = result
        self._open_url(url)
        self.status_var.set(status)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        self._closed = True
        self._generation += 1
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
