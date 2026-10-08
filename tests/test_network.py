import threading
import time
import unittest
from urllib.request import urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from marblescape_network import NetworkActivity, NetworkCancelledError


class NetworkShutdownTests(unittest.TestCase):
    def test_response_close_is_nonblocking_even_when_raw_close_is_blocked(self):
        activity = NetworkActivity()
        reading, release, done = (threading.Event() for _ in range(3))
        responses = []

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()
            def read(self, _size):
                reading.set()
                release.wait(3)
                return b""
            def close(self):
                release.wait(3)

        def worker():
            try:
                with activity.opening(Response) as response:
                    responses.append(response)
                    response.read(100)
            except NetworkCancelledError:
                pass
            finally:
                done.set()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(reading.wait(1))
            before = time.monotonic()
            responses[0].close()
            self.assertLess(time.monotonic() - before, .2)
            self.assertTrue(done.wait(1))
            self.assertFalse(activity.event.is_set(), "Cancel download must not cancel all catalogue work")
        finally:
            release.set()
            thread.join(2)

    def test_pending_connection_is_abandoned_and_late_response_closed(self):
        activity = NetworkActivity()
        started, release, closed, finished = (threading.Event() for _ in range(4))

        class Response:
            def close(self):
                closed.set()

        def opener():
            started.set()
            release.wait(3)
            return Response()

        def worker():
            try:
                with activity.opening(opener):
                    self.fail("Cancelled connection must not return a response")
            except NetworkCancelledError:
                finished.set()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            activity.cancel()
            self.assertTrue(finished.wait(1))
        finally:
            release.set()
            thread.join(2)
        self.assertTrue(closed.wait(1))

    def test_cancellation_unblocks_real_http_body_read(self):
        activity = NetworkActivity()
        reading, release, finished = (threading.Event() for _ in range(3))

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "10000")
                self.end_headers()
                self.wfile.write(b"x")
                self.wfile.flush()
                release.wait(3)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()

        def worker():
            try:
                with activity.opening(urlopen, f"http://127.0.0.1:{server.server_port}", timeout=10) as response:
                    reading.set()
                    response.read(10000)
            except Exception:
                pass  # Closing an HTTP stream may produce an incomplete-read error.
            finally:
                finished.set()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(reading.wait(2))
            before = time.monotonic()
            activity.cancel()
            self.assertTrue(finished.wait(1), "HTTP body read remained blocked after Exit")
            self.assertLess(time.monotonic() - before, 1)
            with self.assertRaises(NetworkCancelledError):
                with activity.opening(urlopen, "http://127.0.0.1:1"):
                    pass
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
