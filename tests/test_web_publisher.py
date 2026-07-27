import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.live_view import build_live_view
from dashboard.publisher import publish_view
from dashboard.web_publisher import build_web_payload, publish_web_payload
from fronius.model import LiveData
from solar_data.storage import SolarDatabase, _utc_text


class WebPublisherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.database = SolarDatabase(str(self.directory / "solar.db"))
        self.now = datetime(2026, 7, 25, 11, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.database.close()
        self.temporary.cleanup()

    def snapshot(self, age=0, battery=True, heat=True):
        timestamp = self.now - timedelta(seconds=age)
        self.database.store_snapshot(LiveData(
            timestamp=timestamp.isoformat(), solar_power_kw=8, house_power_kw=6,
            heat_power_kw=2 if heat else 0, battery_soc_pct=72,
            battery_power_kw=1.5 if battery else 0, grid_power_kw=-0.5,
            energy_today_kwh=12, energy_total_kwh=112,
            battery_available=battery, heat_available=heat))

    def aggregate(self, minute=5, battery=True):
        start = self.now.replace(minute=minute, second=0, microsecond=0)
        self.database.connection.execute(
            "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_utc_text(start), 8, 6, 2, 72, 1.5, -0.5, 12, 4,
             int(battery), 1))
        self.database.connection.commit()

    def payload(self, **view_kwargs):
        view = build_live_view(self.database, self.now, **view_kwargs)
        return build_web_payload(self.database, view, self.now), view

    def test_contract_uses_shared_values_flows_and_completed_series(self):
        self.snapshot()
        self.aggregate(0)
        self.aggregate(5)
        # This bucket is not completed at 11:12 and must not be public yet.
        self.aggregate(10)
        payload, view = self.payload()

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["status"]["overall"], "degraded")
        self.assertEqual(payload["live"]["solar_power_kw"],
                         view["display"]["solar_power_kw"])
        self.assertEqual(payload["live"]["house_consumption_kw"], 4)
        self.assertEqual(payload["live"]["heat_power_kw"], 2)
        self.assertEqual(payload["live"]["battery_flow"]["direction"], "charging")
        self.assertEqual(payload["live"]["battery_flow"]["power_kw"], 1.5)
        self.assertEqual(payload["live"]["grid_flow"]["direction"], "importing")
        self.assertEqual(payload["live"]["grid_flow"]["magnitude_kw"], .5)
        self.assertEqual(len(payload["chart"]["series"]), 2)
        self.assertEqual(payload["chart"]["series"][0]["sample_count"], 4)
        self.assertIn("fact_id", payload["insight"])
        self.assertIn("historical_comparison", payload)
        json.dumps(payload, allow_nan=False)

    def test_fresh_degraded_stale_and_missing_statuses(self):
        missing, _ = self.payload()
        self.assertEqual(missing["status"]["overall"], "missing")
        self.assertEqual(missing["status"]["components"]["solar_data"], "offline")

        self.snapshot(age=181)
        stale, _ = self.payload()
        self.assertEqual(stale["status"]["overall"], "stale")

        self.database.close()
        self.database = SolarDatabase(str(self.directory / "fresh.db"))
        self.snapshot()
        self.aggregate(5)
        degraded, _ = self.payload()
        self.assertEqual(degraded["status"]["overall"], "degraded")
        self.assertIn("weather", degraded["status"]["affected_components"])

        view = build_live_view(self.database, self.now)
        view["sun_data_status"] = "fresh"
        fresh = build_web_payload(self.database, view, self.now)
        self.assertEqual(fresh["status"]["overall"], "fresh")

    def test_optional_battery_and_invalid_number_are_safe_nulls(self):
        self.snapshot(battery=False, heat=False)
        payload, view = self.payload()
        view["display"]["solar_power_kw"] = float("nan")
        payload = build_web_payload(self.database, view, self.now)
        self.assertIsNone(payload["live"]["solar_power_kw"])
        self.assertIsNone(payload["live"]["heat_power_kw"])
        self.assertEqual(payload["live"]["battery_flow"]["direction"], "unavailable")
        self.assertIsNone(payload["live"]["battery_state_of_charge_percent"])
        json.dumps(payload, allow_nan=False)

    def test_historical_comparison_is_structured_from_shared_story(self):
        self.snapshot()
        _, view = self.payload()
        view["story_status"].update({
            "fact_id": "HIST_RECORD", "historical_fact": True,
            "energy_kwh": 20, "historical_baseline_kwh": 16,
            "historical_reference_day": "2026-07-24",
        })
        view["display"]["story_line_1"] = "Neuer Rekord."
        view["display"]["story_line_2"] = "Der alte Wert war 16 kWh."
        comparison = build_web_payload(self.database, view, self.now)[
            "historical_comparison"]
        self.assertEqual(comparison["direction"], "higher")
        self.assertEqual(comparison["difference_percent"], 25)
        self.assertEqual(comparison["reference_day"], "2026-07-24")
        self.assertIn("Neuer Rekord", comparison["statement"])

    def test_atomic_write_permissions_privacy_and_svg_regression(self):
        self.snapshot()
        payload, view = self.payload()
        output = self.directory / "dashboard.json"
        output.write_text("old", encoding="utf-8")
        publish_web_payload(payload, output)
        decoded = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(decoded, payload)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
        serialized = output.read_text(encoding="utf-8").lower()
        for forbidden in ("fronius", "sqlite", "solar.db", "/data/", "192.168.",
                          "latitude", "longitude", "token", "traceback"):
            self.assertNotIn(forbidden, serialized)
        old = output.read_text(encoding="utf-8")
        with patch("dashboard.web_publisher.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                publish_web_payload(payload, output)
        self.assertEqual(output.read_text(encoding="utf-8"), old)
        self.assertEqual(list(self.directory.glob("*.json.tmp")), [])

        svg = self.directory / "dashboard.svg"
        publish_view(view, svg)
        self.assertIn("<svg", svg.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
