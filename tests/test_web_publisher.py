import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dashboard.live_view import build_live_view
from dashboard.facts.catalog import FACTS
from dashboard.facts.engine import hour_key
from dashboard.facts.history import HistoricalCandidate
from dashboard.publisher import publish_view
from dashboard.web_publisher import build_web_payload, publish_web_payload
from fronius.model import LiveData
from solar_data.storage import SolarDatabase, _utc_text
from solar_data.timezones import ZURICH


class WebPublisherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.database = SolarDatabase(str(self.directory / "solar.db"))
        self.now = datetime(2026, 7, 25, 11, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.database.close()
        self.temporary.cleanup()

    def snapshot(self, age=0, battery=True, heat=True, solar=8, house=6,
                 heat_power=2, battery_power=1.5, grid=-0.5, soc=72):
        timestamp = self.now - timedelta(seconds=age)
        self.database.store_snapshot(LiveData(
            timestamp=timestamp.isoformat(), solar_power_kw=solar, house_power_kw=house,
            heat_power_kw=heat_power if heat else 0, battery_soc_pct=soc,
            battery_power_kw=battery_power if battery else 0, grid_power_kw=grid,
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
        snapshot = self.database.latest_snapshot()
        view = build_live_view(self.database, self.now, snapshot=snapshot, **view_kwargs)
        return build_web_payload(self.database, view, snapshot, self.now), view

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

    def test_live_values_come_from_snapshot_while_chart_uses_aggregates(self):
        self.aggregate(0)
        self.aggregate(5)
        self.snapshot(solar=3.2, house=4.8, heat_power=1.1,
                      battery_power=-2.4, grid=1.7, soc=44)
        payload, view = self.payload()

        # The eInk view remains aggregate-smoothed, but public live values do not.
        self.assertEqual(view["display"]["solar_power_kw"], 8)
        self.assertEqual(payload["live"]["solar_power_kw"], 3.2)
        self.assertAlmostEqual(payload["live"]["house_consumption_kw"], 3.7)
        self.assertEqual(payload["live"]["heat_power_kw"], 1.1)
        self.assertEqual(payload["live"]["battery_state_of_charge_percent"], 44)
        self.assertEqual(payload["live"]["battery_flow"]["power_kw"], -2.4)
        self.assertEqual(payload["live"]["battery_flow"]["direction"], "discharging")
        self.assertEqual(payload["live"]["grid_flow"]["direction"], "exporting")
        self.assertEqual(payload["data"]["timestamp"], payload["data"]["latest_sample_at"])
        self.assertEqual(payload["data"]["age_seconds"], 0)
        self.assertNotIn("live_power_aggregation",
                         payload["status"]["affected_components"])
        self.assertEqual(payload["chart"]["series"][0]["solar_power_kw"], 8)
        self.assertEqual(payload["chart"]["series"][0]["house_consumption_kw"], 6)

    def test_one_snapshot_is_used_for_the_entire_publisher_cycle(self):
        self.snapshot(age=10, solar=1.2, house=2.5, heat_power=.5, battery=False)
        cycle_snapshot = self.database.latest_snapshot()
        view = build_live_view(self.database, self.now, snapshot=cycle_snapshot)

        # A concurrently collected sample belongs to the next publisher cycle.
        self.snapshot(solar=9.8, house=8, heat_power=2)
        with patch.object(self.database, "latest_snapshot",
                          side_effect=AssertionError("must not re-read snapshot")):
            payload = build_web_payload(self.database, view, cycle_snapshot, self.now)

        self.assertEqual(payload["status"]["freshness"], "fresh")
        self.assertEqual(datetime.fromisoformat(payload["data"]["timestamp"]),
                         datetime.fromisoformat(cycle_snapshot.timestamp))
        self.assertEqual(payload["data"]["age_seconds"], 10)
        self.assertEqual(payload["live"]["solar_power_kw"], 1.2)
        self.assertEqual(payload["live"]["house_consumption_kw"], 2)
        self.assertEqual(payload["status"]["components"]["battery"], "missing")
        self.assertEqual(payload["live"]["battery_flow"]["direction"], "unavailable")
        self.assertEqual(self.database.latest_snapshot().solar_power_kw, 9.8)

    def test_header_clock_uses_now_even_when_solar_data_is_missing(self):
        payload, _ = self.payload()
        expected = self.now.astimezone(ZURICH)
        self.assertEqual(payload["header"]["local_date"], expected.date().isoformat())
        self.assertEqual(payload["header"]["local_time"], expected.isoformat())
        self.assertEqual(payload["status"]["overall"], "missing")
        self.assertIsNone(payload["data"]["timestamp"])

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

        snapshot = self.database.latest_snapshot()
        view = build_live_view(self.database, self.now, snapshot=snapshot)
        view["sun_data_status"] = "fresh"
        fresh = build_web_payload(self.database, view, snapshot, self.now)
        self.assertEqual(fresh["status"]["overall"], "fresh")

    def test_optional_battery_values_are_safe_nulls(self):
        self.snapshot(battery=False, heat=False)
        payload, _ = self.payload()
        self.assertIsNone(payload["live"]["heat_power_kw"])
        self.assertEqual(payload["live"]["battery_flow"]["direction"], "unavailable")
        self.assertIsNone(payload["live"]["battery_state_of_charge_percent"])
        json.dumps(payload, allow_nan=False)

    def test_normal_insight_and_independent_historical_comparison_coexist(self):
        self.snapshot()
        self.aggregate(5)
        local_hour = self.now.astimezone(ZURICH).replace(minute=0, second=0, microsecond=0)
        fact = next(item for item in FACTS if item.min_kwh <= 12 <= item.max_kwh)
        self.database.store_fact_selection(hour_key(local_hour), local_hour,
                                           fact.fact_id, fact.family, 12)
        for day, energy in ((23, 8), (24, 10)):
            timestamp = datetime(2026, 7, day, 18, tzinfo=timezone.utc)
            self.database.connection.execute(
                "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_utc_text(timestamp), 1, 1, 0, 0, 0, 0, energy, 1, 0, 0))
        self.database.connection.commit()

        payload, _ = self.payload()
        comparison = payload["historical_comparison"]
        self.assertEqual(payload["insight"]["fact_id"], fact.fact_id)
        self.assertNotEqual(payload["insight"]["fact_id"], comparison["type"])
        self.assertEqual(comparison["direction"], "higher")
        self.assertEqual(comparison["difference_percent"], 20)
        self.assertEqual(comparison["comparison_value_kwh"], 12)
        self.assertEqual(comparison["baseline_kwh"], 10)
        self.assertEqual(comparison["reference_day"], "2026-07-24")
        self.assertEqual(comparison["reference_period"], "today")
        self.assertNotEqual(comparison["statement"], " ".join(
            (payload["insight"]["line_1"], payload["insight"]["line_2"])))

    def test_historical_insight_is_not_duplicated_by_comparison(self):
        self.snapshot()
        snapshot = self.database.latest_snapshot()
        view = build_live_view(self.database, self.now, snapshot=snapshot)
        view["story_status"]["fact_id"] = "HIST_RECORD"
        view["display"]["story_line_1"] = "Aktueller Rekord."
        view["display"]["story_line_2"] = "Bisheriger Rekord."
        candidates = [
            HistoricalCandidate("HIST_RECORD", {
                "reference_day": "2026-07-24", "baseline_energy_kwh": 10,
            }),
            HistoricalCandidate("HIST_AVERAGE", {
                "reference_day": "2026-07-25", "period": "today",
                "comparison_energy_kwh": 12, "baseline_energy_kwh": 8,
                "baseline_days": 4,
            }),
        ]

        def rendered(fact_id, _context, _details):
            lines = (("Aktueller Rekord.", "Bisheriger Rekord.")
                     if fact_id == "HIST_RECORD" else
                     ("Vergleich zum Schnitt.", "Heute liegt darüber."))
            return SimpleNamespace(line_1=lines[0], line_2=lines[1])

        with patch("dashboard.web_publisher.eligible_historical_candidates",
                   return_value=candidates), patch(
                       "dashboard.web_publisher.render_historical",
                       side_effect=rendered):
            payload = build_web_payload(
                self.database, view, snapshot, self.now)

        comparison = payload["historical_comparison"]
        insight_text = " ".join((payload["insight"]["line_1"],
                                 payload["insight"]["line_2"]))
        self.assertEqual(comparison["type"], "HIST_AVERAGE")
        self.assertNotEqual(comparison["type"], payload["insight"]["fact_id"])
        self.assertNotEqual(comparison["statement"], insight_text)

        with patch("dashboard.web_publisher.eligible_historical_candidates",
                   return_value=candidates[:1]):
            without_alternative = build_web_payload(
                self.database, view, snapshot, self.now)
        self.assertIsNone(without_alternative["historical_comparison"])

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

    def test_dashboard_once_creates_svg_and_json(self):
        self.snapshot()
        svg = self.directory / "once.svg"
        web = self.directory / "once.json"
        result = subprocess.run([
            sys.executable, "-m", "dashboard", "--db-path",
            str(self.directory / "solar.db"), "--output", str(svg),
            "--web-output", str(web), "once",
        ], cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<svg", svg.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(web.read_text(encoding="utf-8"))["schema_version"],
                         "1.0")


if __name__ == "__main__":
    unittest.main()
