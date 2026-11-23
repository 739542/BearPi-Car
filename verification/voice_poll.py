"""Exercise the gateway's car polling against a local HTTP server."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
from threading import Event, Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_gateway


class CarPollingTest(unittest.TestCase):
    def test_empty_and_bad_responses_do_not_dispatch_and_repeated_ids_do(self):
        poll = getattr(voice_gateway, "read_car_tasks", None)
        self.assertTrue(callable(poll), "The gateway needs a car task reader")
        shutdown = Event()
        received = []
        requests = []
        responses = iter((
            (204, b""), (200, b'{"command_id":true}'), (200, b'{"command_id":0}'),
            (200, b'{"command_id":22}'), (200, b'{"command_id":22}'),
            (200, b'{"command_id":24}'),
        ))

        class Car(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("X-Car-Control")))
                status, body = next(responses, (204, b""))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Gateway:
            def submit(self, command_id):
                received.append(command_id)
                if command_id == 24:
                    shutdown.set()
                return 202, {"accepted": True}

        with HTTPServer(("127.0.0.1", 0), Car) as car:
            server = Thread(target=car.serve_forever)
            server.start()
            try:
                url = f"http://127.0.0.1:{car.server_port}/tasks/next"
                thread = Thread(target=poll, args=(url, Gateway(), shutdown))
                thread.start()
                self.assertTrue(shutdown.wait(5), "The stop event should reach the gateway")
            finally:
                shutdown.set()
                thread.join()
                car.shutdown()
                server.join()

        self.assertEqual(received, [22, 22, 24])
        self.assertEqual(requests, [("/tasks/next", "1")] * 6)


if __name__ == "__main__":
    unittest.main()
