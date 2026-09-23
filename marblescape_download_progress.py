"""Thread-safe progress accounting for wallpaper image transfers."""

from __future__ import annotations

from collections import deque
import threading
import time


READ_CHUNK_SIZE = 64 * 1024
COMPLETED_VISIBILITY_SECONDS = 3.0


class ResponseTooLargeError(RuntimeError):
    """A response exceeds the caller's explicit byte limit."""


class DownloadCancelledError(RuntimeError):
    """The user cancelled the active wallpaper image download."""


class DownloadProgressTracker:
    """Aggregate streamed response bytes for one wallpaper update."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._generation = 0
        self._reset()

    def _reset(self):
        self._active = False
        self._successful = False
        self._cancel_requested = False
        self._cancelled = False
        self._cancellable = False
        self._started_at = 0.0
        self._finished_at = None
        self._expected_requests = None
        self._started_requests = 0
        self._finished_requests = 0
        self._unknown_active = 0
        self._known_total = 0
        self._transferred = 0
        self._samples = deque()
        self._final_speed = 0.0
        self._responses = {}
        self._next_response_id = 0
        self._cancel_event.clear()

    def begin(self, expected_requests=None):
        if expected_requests is not None:
            expected_requests = int(expected_requests)
            if expected_requests <= 0:
                expected_requests = None
        now = self._clock()
        with self._lock:
            self._generation += 1
            self._reset()
            self._active = True
            self._cancellable = True
            self._started_at = now
            self._expected_requests = expected_requests
            self._samples.append((now, 0))
            return self._generation

    def restart_attempt(self, expected_requests=None):
        """Reset partial-byte accounting without losing a pending cancellation."""
        with self._lock:
            if not self._active:
                raise RuntimeError("No active download to retry.")
            if self._cancel_requested:
                raise DownloadCancelledError("Download cancelled by user.")
            return self.begin(expected_requests)

    def request_cancel(self):
        """Request cancellation and close active responses to unblock reads."""
        with self._lock:
            if not self._active or not self._cancellable or self._cancel_requested:
                return False
            self._cancel_requested = True
            self._cancel_event.set()
            responses = tuple(self._responses.values())
        for response in responses:
            try:
                response.close()
            except Exception:
                pass
        return True

    def raise_if_cancelled(self):
        with self._lock:
            cancelled = self._active and self._cancel_requested
        if cancelled:
            raise DownloadCancelledError("Download cancelled by user.")

    def wait_or_raise(self, timeout):
        """Wait interruptibly during a tracked request retry delay."""
        if self._cancel_event.wait(max(0.0, float(timeout))):
            self.raise_if_cancelled()

    def seal_cancellation(self):
        """Finish the cancellable phase before committing files to storage."""
        with self._lock:
            if self._active and self._cancel_requested:
                raise DownloadCancelledError("Download cancelled by user.")
            self._cancellable = False

    def response_started(self, declared_size, response=None):
        with self._lock:
            if not self._active:
                return None
            if self._cancel_requested:
                raise DownloadCancelledError("Download cancelled by user.")
            if declared_size is not None:
                try:
                    declared_size = int(declared_size)
                except (TypeError, ValueError, OverflowError):
                    declared_size = None
                if declared_size is not None and declared_size < 0:
                    declared_size = None
            self._started_requests += 1
            if declared_size is None:
                self._unknown_active += 1
            else:
                self._known_total += declared_size
            self._next_response_id += 1
            response_id = self._next_response_id
            if response is not None:
                self._responses[response_id] = response
            return self._generation, declared_size, response_id

    def advance(self, token, byte_count):
        if token is None or byte_count <= 0:
            return
        now = self._clock()
        with self._lock:
            if not self._active or token[0] != self._generation:
                return
            self._transferred += int(byte_count)
            self._samples.append((now, self._transferred))
            self._trim_samples(now)

    def response_finished(self, token, received_size):
        if token is None:
            return
        with self._lock:
            if len(token) > 2:
                self._responses.pop(token[2], None)
            if not self._active or token[0] != self._generation:
                return
            declared_size = token[1]
            if declared_size is None:
                self._unknown_active = max(0, self._unknown_active - 1)
                self._known_total += max(0, int(received_size))
            self._finished_requests += 1

    def finish(self, successful=True, cancelled=False):
        now = self._clock()
        with self._lock:
            if not self._active:
                return
            self._final_speed = self._speed(now)
            self._active = False
            self._cancellable = False
            self._cancelled = bool(cancelled or self._cancel_requested)
            self._successful = bool(successful) and not self._cancelled
            self._finished_at = now
            self._responses.clear()
            if self._successful:
                self._known_total = self._transferred
                self._unknown_active = 0

    def _trim_samples(self, now):
        cutoff = now - 3.0
        while len(self._samples) > 2 and self._samples[1][0] < cutoff:
            self._samples.popleft()

    def _speed(self, now):
        self._trim_samples(now)
        if not self._samples:
            return 0.0
        sample_time, sample_bytes = self._samples[0]
        elapsed = now - sample_time
        if elapsed < 0.05:
            elapsed = now - self._started_at
            sample_bytes = 0
        return max(0.0, (self._transferred - sample_bytes) / elapsed) if elapsed > 0 else 0.0

    def snapshot(self, keep_completed_visible=False):
        now = self._clock()
        with self._lock:
            recently_finished = (
                self._finished_at is not None
                and now - self._finished_at < COMPLETED_VISIBILITY_SECONDS
            )
            completed_persistently_visible = (
                bool(keep_completed_visible)
                and self._finished_at is not None
                and self._successful
            )
            visible = (
                self._active
                or recently_finished
                or completed_persistently_visible
            )
            total_is_final = (
                self._active
                and self._expected_requests is not None
                and self._started_requests >= self._expected_requests
                and self._unknown_active == 0
            ) or (not self._active and self._successful) or (
                not self._active
                and self._cancelled
                and self._expected_requests is not None
                and self._started_requests >= self._expected_requests
                and self._unknown_active == 0
            )
            total = self._known_total if total_is_final else None
            percent = None
            if total is not None:
                percent = 100.0 if total == 0 else min(100.0, self._transferred * 100.0 / total)
            return {
                "visible": visible,
                "active": self._active,
                "successful": self._successful,
                "cancel_requested": self._cancel_requested,
                "cancelled": self._cancelled,
                "cancellable": self._cancellable,
                "transferred": self._transferred,
                "total": total,
                "percent": percent,
                "speed": self._speed(now) if self._active else self._final_speed,
                "started_requests": self._started_requests,
                "finished_requests": self._finished_requests,
            }


DOWNLOAD_PROGRESS = DownloadProgressTracker()


def response_content_length(response):
    """Return a valid non-negative Content-Length, or None when unavailable."""
    try:
        value = response.headers.get("Content-Length")
        parsed = int(value) if value is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed is not None and parsed >= 0 else None


def read_response(response, maximum=None, track=False):
    """Read a response in chunks and optionally report exact transferred bytes."""
    if track:
        DOWNLOAD_PROGRESS.raise_if_cancelled()
    declared = response_content_length(response)
    if maximum is not None and declared is not None and declared > maximum:
        raise ResponseTooLargeError
    token = DOWNLOAD_PROGRESS.response_started(declared, response) if track else None
    result = bytearray()
    try:
        while True:
            if track:
                DOWNLOAD_PROGRESS.raise_if_cancelled()
            if maximum is None:
                request_size = READ_CHUNK_SIZE
            else:
                request_size = min(READ_CHUNK_SIZE, maximum + 1 - len(result))
                if request_size <= 0:
                    raise ResponseTooLargeError
            try:
                chunk = response.read(request_size)
            except Exception as exc:
                if track:
                    try:
                        DOWNLOAD_PROGRESS.raise_if_cancelled()
                    except DownloadCancelledError as cancelled:
                        raise cancelled from exc
                raise
            if not chunk:
                break
            result.extend(chunk)
            DOWNLOAD_PROGRESS.advance(token, len(chunk))
            if track:
                DOWNLOAD_PROGRESS.raise_if_cancelled()
            if maximum is not None and len(result) > maximum:
                raise ResponseTooLargeError
    finally:
        DOWNLOAD_PROGRESS.response_finished(token, len(result))
    if track:
        DOWNLOAD_PROGRESS.raise_if_cancelled()
    return bytes(result)
