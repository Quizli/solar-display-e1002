import logging
import unittest
from datetime import datetime, timezone

from collector.service import Collector
from solar_data.storage import SolarDatabase


class Client:
    def __init__(self, payloads=None, error=None):
        self.payloads = payloads
        self.error = error

    def get_live_payloads(self):
        if self.error:
            raise self.error
        return self.payloads


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.database = SolarDatabase(":memory:")

    def tearDown(self):
        self.database.close()

    def test_api_error_does_not_store_fake_zero_snapshot(self):
        collector = Collector(Client(error=RuntimeError("offline")), self.database)
        with self.assertLogs("collector.service", logging.ERROR):
            self.assertFalse(collector.collect_once())
        self.assertIsNone(self.database.latest_snapshot())

    def test_valid_normalized_cycle_is_stored(self):
        payload = {
            "Head": {"Timestamp": "2026-07-24T22:07:46+00:00"},
            "Body": {"Data": {"Site": {"P_PV": 1000, "P_Load": -2000, "P_Grid": 1000}}},
        }
        collector = Collector(
            Client({"power_flow": payload}), self.database,
            clock=lambda: datetime(2026, 7, 24, 22, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(collector.collect_once())
        self.assertEqual(self.database.latest_snapshot().solar_power_kw, 1)


if __name__ == "__main__":
    unittest.main()
