"""Index existing published PNGs, never treating a profile cache as a download."""

import datetime as dt
from copy import deepcopy
from pathlib import Path
import threading
import struct
import time

from marblescape_image_naming import _read_record


class PublishedImageIndex:
    def __init__(self):
        self._lock = threading.Lock()
        self._files = {}
        self._result = {}
        self._records = {}
        self._scanned_records = {}
        self._running = False
        self._next_scan = 0

    def scan(self, folders):
        found, newest, records = {}, {}, {}
        for folder in set(map(Path, folders)):
            try:
                paths = list(folder.glob("*.png"))
            except OSError:
                continue
            for path in paths:
                try:
                    stat = path.stat()
                    marker = (stat.st_mtime_ns, stat.st_size)
                    previous = self._files.get(path)
                    if previous and previous[0] == marker:
                        record = previous[1]
                    else:
                        with path.open("rb") as handle:
                            record = _read_record(handle)
                    found[path] = (marker, record)
                    if not record or not record.get("profile_id"):
                        continue
                    timestamp = dt.datetime.fromisoformat(record["generated_at_utc"].replace("Z", "+00:00"))
                    if timestamp.tzinfo is None:
                        continue
                    timestamp = timestamp.astimezone(dt.timezone.utc)
                    identifier = record["profile_id"]
                    if identifier not in newest or timestamp > newest[identifier]:
                        newest[identifier] = timestamp
                        records[identifier] = record
                except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, struct.error, RecursionError):
                    continue
        self._files = found
        self._scanned_records = records
        return {key: value.isoformat() for key, value in newest.items()}

    def snapshot(self, folders):
        with self._lock:
            if not self._running and time.monotonic() >= self._next_scan:
                self._running = True
                def work():
                    try:
                        result = self.scan(folders)
                        with self._lock:
                            self._result = result
                            self._records = self._scanned_records
                    finally:
                        with self._lock:
                            self._running = False
                            self._next_scan = time.monotonic() + 2
                threading.Thread(target=work, name="MarbleScapePublishedImageIndex", daemon=True).start()
            return dict(self._result)

    def records_snapshot(self, folders):
        self.snapshot(folders)
        with self._lock:
            return deepcopy(self._records)
