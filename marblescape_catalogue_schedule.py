"""Daily local-wall-clock catalogue scheduling with durable missed-run recovery."""

import datetime as dt
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

DEFAULT_REFRESH_TIME = "03:00:00"


def normalize_refresh_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]", value):
        raise ValueError("Daily catalogue refresh time must be HH:MM:SS (00:00:00-23:59:59).")
    return value


def latest_due_slot(now, clock_time):
    """Calendar arithmetic: DST repetition runs once, a skipped hour catches up."""
    hour, minute, second = map(int, normalize_refresh_time(clock_time).split(":"))
    slot = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if slot > now:
        slot -= dt.timedelta(days=1)
    return slot.isoformat(timespec="seconds")


class CatalogueSchedule:
    def __init__(self, state_path, get_time, refresh, stop_event):
        self.path = Path(state_path)
        self.get_time, self.refresh, self.stop = get_time, refresh, stop_event
        self._lock = threading.Lock()
        self._running = False
        self._retry_at = 0.0
        self._message = "Not refreshed yet."
        self._error = ""
        self._completed = ""
        # When the last refresh actually finished (local system time); the slot
        # above only plans the schedule and moves with a newly saved time.
        self._completed_at = ""
        # The clock seen by the previous request. None until the first request,
        # so a run missed while MarbleScape was closed is still caught up.
        self._clock = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = data.get("completed_slot", "")
            if value:
                dt.datetime.fromisoformat(value)
                self._completed = value
            finished = data.get("completed_at", "")
            if finished:
                dt.datetime.fromisoformat(finished)
                self._completed_at = finished
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def status(self):
        with self._lock:
            return {"running": self._running, "message": self._message, "error": self._error,
                    "completed_slot": self._completed, "completed_at": self._completed_at}

    def _save(self, slot, completed_at=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".catalogue-schedule-", delete=False) as handle:
                temporary = Path(handle.name)
                with self._lock:
                    finished = self._completed_at if completed_at is None else completed_at
                json.dump({"completed_slot": slot, **({"completed_at": finished} if finished else {})}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def request(self, force=False, now=None):
        if self.stop.is_set():
            return False
        now = now or dt.datetime.now()
        clock = self.get_time()
        slot = latest_due_slot(now, clock)
        rebased = None
        with self._lock:
            if self._clock is not None and clock != self._clock and slot > self._completed:
                # A newly saved time only moves the schedule: today's already
                # passed slot at that time is not a missed run.
                self._completed = rebased = slot
            self._clock = clock
        if rebased is not None:
            try:
                self._save(rebased)
            except OSError:
                pass
        with self._lock:
            if self._running or (not force and (slot <= self._completed or time.monotonic() < self._retry_at)):
                return False
            self._running = True
            self._message = "Refreshing all catalogues…"
            self._error = ""

        def work():
            try:
                self.refresh()
                if self.stop.is_set():
                    return
                finished = dt.datetime.now().replace(microsecond=0).isoformat()
                self._save(slot, finished)
                with self._lock:
                    self._completed = slot
                    self._completed_at = finished
                    self._message = "All catalogues refreshed."
                    self._retry_at = 0.0
            except Exception as exc:
                with self._lock:
                    self._message = f"Refresh incomplete: {exc}"
                    self._error = str(exc)
                    self._retry_at = time.monotonic() + 300
            finally:
                with self._lock:
                    self._running = False

        threading.Thread(target=work, name="MarbleScapeScheduledCatalogueRefresh", daemon=True).start()
        return True

    def start(self):
        def monitor():
            while not self.stop.is_set():
                self.request()
                self.stop.wait(1)
        threading.Thread(target=monitor, name="MarbleScapeCatalogueSchedule", daemon=True).start()
