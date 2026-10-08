"""Cooperative application-wide cancellation for image and catalogue requests."""

from contextlib import contextmanager
import queue
import socket
import threading


class NetworkCancelledError(RuntimeError):
    pass


class _ResponseReader:
    """One daemon reader per response; Windows recv/close can outlive cancellation."""
    def __init__(self, response, event):
        self._response = response
        self._event = event
        self._closed = threading.Event()
        self._requests = queue.Queue()
        self._thread = None

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _run(self):
        while True:
            request = self._requests.get()
            if request is None or self._event.is_set() or self._closed.is_set():
                return
            size, result = request
            try:
                value = self._response.read(size)
                result.put((True, value))
            except Exception as exc:
                result.put((False, exc))

    def read(self, size=-1):
        if self._event.is_set() or self._closed.is_set():
            raise NetworkCancelledError("Network read cancelled because MarbleScape is stopping.")
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="MarbleScape-network-read", daemon=True)
            self._thread.start()
        result = queue.Queue(maxsize=1)
        self._requests.put((size, result))
        while not self._event.is_set() and not self._closed.is_set():
            try:
                success, value = result.get(timeout=0.05)
            except queue.Empty:
                continue
            if success:
                return value
            raise value
        raise NetworkCancelledError("Network read cancelled because MarbleScape is stopping.")

    def stop(self):
        self._requests.put(None)

    def close(self):
        self._closed.set()
        self.stop()
        threading.Thread(target=NetworkActivity.close_response, args=(self._response,), daemon=True).start()


class NetworkActivity:
    def __init__(self):
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._responses = {}

    def reset(self):
        # Keep the old event set for a connection still finishing in a daemon.
        self.event = threading.Event()

    def check(self):
        if self.event.is_set():
            raise NetworkCancelledError("Network operation cancelled because MarbleScape is stopping.")

    def wait(self, seconds):
        self.event.wait(max(0, seconds))
        self.check()

    @staticmethod
    def close_response(response):
        try:
            sock = response.fp.raw._sock
            sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        try:
            response.close()
        except Exception:
            pass

    def cancel(self):
        self.event.set()
        with self._lock:
            responses = list(self._responses.values())
            self._responses.clear()
        # Closing a buffered HTTP response can itself wait for its reader.
        for response in responses:
            threading.Thread(target=self.close_response, args=(response,), daemon=True,
                             name="MarbleScape-network-close").start()

    @contextmanager
    def opening(self, opener, *args, **kwargs):
        self.check()
        event = self.event
        result = queue.Queue(maxsize=1)

        def connect():
            try:
                response = opener(*args, **kwargs)
            except Exception as exc:
                result.put((False, exc))
                return
            with self._lock:
                if not event.is_set():
                    self._responses[id(response)] = response
            if event.is_set():
                self.close_response(response)
            result.put((True, response))

        threading.Thread(target=connect, name="MarbleScape-network-connect", daemon=True).start()
        while not event.is_set():
            try:
                success, response = result.get(timeout=0.05)
                break
            except queue.Empty:
                continue
        else:
            raise NetworkCancelledError("Network connection cancelled because MarbleScape is stopping.")
        if not success:
            raise response
        reader = None
        try:
            if event.is_set():
                raise NetworkCancelledError("Network request cancelled because MarbleScape is stopping.")
            reader = _ResponseReader(response.__enter__(), event)
            yield reader
        finally:
            if reader is not None:
                reader.stop()
            with self._lock:
                self._responses.pop(id(response), None)
            if event.is_set() or (reader is not None and reader._closed.is_set()):
                threading.Thread(target=self.close_response, args=(response,), daemon=True).start()
            else:
                response.__exit__(None, None, None)


NETWORK_ACTIVITY = NetworkActivity()
open_response = NETWORK_ACTIVITY.opening
