import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fronius.client import FroniusClient, FroniusClientError


class _Handler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.requests.append(self.path)
        if self.path.startswith("/api/GetPowerFlowRealtimeData.fcgi"):
            body = {"Head": {"Status": {"Code": 0}}, "Body": {"Data": {}}}
            self.send_response(200)
        elif self.path.startswith("/api/GetInverterRealtimeData.cgi"):
            body = {"Head": {"Status": {"Code": 0}}, "Body": {"Data": {}}}
            self.send_response(200)
        elif self.path.startswith("/api/GetMeterRealtimeData.cgi"):
            body = {
                "Head": {"Status": {"Code": 0}},
                "Body": {"Data": {"0": {"Model": "Smart Meter TS 65A-3"}}},
            }
            self.send_response(200)
        else:
            body = {"error": "offline"}
            self.send_response(503)
        encoded = json.dumps(body).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


class FroniusClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/api/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        _Handler.requests = []

    def test_live_request_uses_expected_query_and_tolerates_optional_failures(self):
        payloads = FroniusClient(self.base_url, timeout=1).get_live_payloads()
        self.assertIsNotNone(payloads["power_flow"])
        self.assertIsNotNone(payloads["inverter_realtime"])
        self.assertIsNone(payloads["storage"])
        self.assertIsNone(payloads["ohmpilot"])
        self.assertFalse(
            any("GetMeterRealtimeData.cgi" in request for request in _Handler.requests)
        )
        self.assertIn(
            "/api/GetInverterRealtimeData.cgi?Scope=System&DataCollection=CumulationInverterData",
            _Handler.requests,
        )

    def test_required_request_failure_is_reported(self):
        with self.assertRaises(FroniusClientError):
            FroniusClient(self.base_url).get("missing.cgi")

    def test_meter_data_has_explicit_system_scope_access(self):
        payload = FroniusClient(self.base_url).get_meter_realtime_data()
        self.assertEqual(
            payload["Body"]["Data"]["0"]["Model"], "Smart Meter TS 65A-3"
        )
        self.assertEqual(
            _Handler.requests, ["/api/GetMeterRealtimeData.cgi?Scope=System"]
        )

    def test_base_url_and_timeout_are_validated(self):
        with self.assertRaises(ValueError):
            FroniusClient("")
        with self.assertRaises(ValueError):
            FroniusClient(self.base_url, timeout=0)


if __name__ == "__main__":
    unittest.main()
