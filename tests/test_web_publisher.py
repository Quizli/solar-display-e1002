import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.live_view import build_live_view
from dashboard.facts.catalog import FACTS
from dashboard.facts.engine import hour_key
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
                 heat_power=2, battery_power=1.5, grid=-0.5, soc=72,
                 energy=12):
        timestamp = self.now - timedelta(seconds=age)
        self.database.store_snapshot(LiveData(
            timestamp=timestamp.isoformat(), solar_power_kw=solar, house_power_kw=house,
            heat_power_kw=heat_power if heat else 0, battery_soc_pct=soc,
            battery_power_kw=battery_power if battery else 0, grid_power_kw=grid,
            energy_today_kwh=energy, energy_total_kwh=100 + energy,
            battery_available=battery, heat_available=heat))

    def aggregate(self, minute=5, battery=True, grid=-0.5):
        start = self.now.replace(minute=minute, second=0, microsecond=0)
        self.database.connection.execute(
            "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_utc_text(start), 8, 6, 2, 72, 1.5, grid, 12, 4,
             int(battery), 1))
        self.database.connection.commit()

    def complete_day(self, local_day, energy):
        start, end = self.database._local_day_bounds(local_day)
        duration = (end - start).total_seconds()
        rows = []
        instant = start
        while instant < end:
            elapsed = (instant - start).total_seconds() + 300
            rows.append((_utc_text(instant), 0, 0, 0, 50, 0, 0,
                         energy * min(1, elapsed / duration), 1, 1, 1))
            instant += timedelta(minutes=5)
        self.database.connection.executemany(
            "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
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
        self.assertEqual(payload["chart"]["series"][0]["grid_import_kw"], .5)
        self.assertEqual(
            payload["chart"]["series"][0]["battery_state_of_charge_percent"], 72)
        self.assertIn("fact_id", payload["insight"])
        self.assertIn("historical_comparison", payload)
        json.dumps(payload, allow_nan=False)

    def test_chart_grid_import_uses_negative_internal_grid_flow(self):
        self.now = self.now.replace(minute=20)
        self.aggregate(0, grid=-4)
        self.aggregate(5, grid=0)
        self.aggregate(10, grid=4)
        self.snapshot()

        payload, _ = self.payload()

        self.assertEqual(
            [row["grid_import_kw"] for row in payload["chart"]["series"]],
            [4, 0, 0])

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
        self.assertEqual(
            payload["chart"]["series"][0]["battery_state_of_charge_percent"], 72)

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

    def test_invalid_weather_publisher_payload_is_accepted_by_web_client(self):
        self.snapshot()
        snapshot = self.database.latest_snapshot()
        view = build_live_view(self.database, self.now, snapshot=snapshot)
        view["sun_data_status"] = "invalid"
        payload = build_web_payload(self.database, view, snapshot, self.now)

        self.assertEqual(payload["status"]["overall"], "degraded")
        self.assertEqual(payload["status"]["affected_components"], ["weather"])
        self.assertEqual(payload["status"]["components"]["weather"], "invalid")
        probe = r'''
const fs = require("fs"), vm = require("vm");
const context = {window: {}, Intl, Date, Object, Array, Math, Number, String};
vm.createContext(context);
vm.runInContext(fs.readFileSync("web/assets/dashboard.js", "utf8"), context);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const api = context.window.SolarDashboard;
const view = api.buildViewModel(payload);
process.stdout.write(JSON.stringify({
    valid: api.validPayload(payload),
    weather_state: view && view.states.weather,
    solar_value: view && view.fields["live.solar_power_kw"],
    sunshine_hours: view && view.fields["header.sunshine_hours"],
}));
'''
        result = subprocess.run(
            ["node", "-e", probe], input=json.dumps(payload), text=True,
            cwd=Path(__file__).parents[1], capture_output=True, check=True)
        client_result = json.loads(result.stdout)
        self.assertEqual(client_result, {
            "valid": True,
            "weather_state": "degraded",
            "solar_value": "8.0",
            "sunshine_hours": "—",
        })

    def test_optional_battery_values_are_safe_nulls(self):
        self.snapshot(battery=False, heat=False)
        self.aggregate(5, battery=False)
        payload, _ = self.payload()
        self.assertIsNone(payload["live"]["heat_power_kw"])
        self.assertEqual(payload["live"]["battery_flow"]["direction"], "unavailable")
        self.assertIsNone(payload["live"]["battery_state_of_charge_percent"])
        self.assertIsNone(
            payload["chart"]["series"][0]["battery_state_of_charge_percent"])
        json.dumps(payload, allow_nan=False)

    def test_incomplete_history_does_not_create_comparison_or_record(self):
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
        self.assertIsNone(comparison)


    def test_active_today_record_keeps_real_average_comparison(self):
        for day, energy in ((21, 10), (22, 20), (23, 30), (24, 40)):
            self.complete_day(date(2026, 7, day), energy)
        self.snapshot(energy=40.2)
        payload, _ = self.payload()

        comparison = payload["historical_comparison"]
        insight_text = " ".join((payload["insight"]["line_1"],
                                 payload["insight"]["line_2"]))
        self.assertEqual(comparison["type"], "HIST_AVERAGE")
        self.assertEqual(comparison["reference_day"], "2026-07-24")
        self.assertEqual(comparison["baseline_days"], 3)
        self.assertAlmostEqual(comparison["comparison_value_kwh"], 40)
        self.assertAlmostEqual(comparison["baseline_kwh"], 20)
        self.assertNotEqual(comparison["type"], payload["insight"]["fact_id"])
        self.assertNotEqual(comparison["statement"], insight_text)

    def test_yesterday_record_and_average_cover_entire_following_day(self):
        for day, energy in ((21, 10), (22, 20), (23, 30), (24, 40)):
            self.complete_day(date(2026, 7, day), energy)
        for hour, minute in ((0, 5), (8, 0), (23, 55)):
            with self.subTest(hour=hour, minute=minute):
                self.now = datetime(2026, 7, 25, hour, minute, tzinfo=ZURICH)
                self.snapshot(solar=0, energy=0)
                payload, _ = self.payload()
                comparison = payload["historical_comparison"]
                self.assertEqual(payload["insight"]["fact_id"], "HIST_RECORD")
                self.assertIn("Tagesrekord", payload["insight"]["line_2"])
                self.assertEqual(comparison["type"], "HIST_AVERAGE")
                self.assertEqual(comparison["reference_day"], "2026-07-24")
                self.assertEqual(comparison["baseline_days"], 3)
                self.assertAlmostEqual(comparison["comparison_value_kwh"], 40)
                self.assertAlmostEqual(comparison["baseline_kwh"], 20)
                self.assertNotIn("Rekord", comparison["statement"])

        self.now = datetime(2026, 7, 26, 8, tzinfo=ZURICH)
        self.snapshot(solar=0, energy=0)
        payload, _ = self.payload()
        self.assertNotEqual(payload["insight"]["fact_id"], "HIST_RECORD")

    def test_average_payload_survives_database_restart_without_record(self):
        for day, energy in ((21, 20), (22, 30), (23, 25), (24, 27)):
            self.complete_day(date(2026, 7, day), energy)
        self.snapshot(energy=10)
        local_hour = self.now.astimezone(ZURICH).replace(
            minute=0, second=0, microsecond=0)
        fact = next(item for item in FACTS if item.min_kwh <= 10 <= item.max_kwh)
        self.database.store_fact_selection(
            hour_key(local_hour), local_hour, fact.fact_id, fact.family, 10)
        before, _ = self.payload()
        self.database.close()
        self.database = SolarDatabase(str(self.directory / "solar.db"))
        after, _ = self.payload()
        self.assertNotEqual(before["insight"]["fact_id"], "HIST_RECORD")
        self.assertEqual(before["historical_comparison"]["type"], "HIST_AVERAGE")
        self.assertEqual(before["historical_comparison"],
                         after["historical_comparison"])

    def test_publisher_reuses_candidates_from_single_live_view_build(self):
        self.snapshot()
        snapshot = self.database.latest_snapshot()
        from dashboard.facts.history import eligible_historical_candidates
        with patch("dashboard.live_view.eligible_historical_candidates",
                   wraps=eligible_historical_candidates) as live_candidates, patch(
                       "dashboard.web_publisher.eligible_historical_candidates",
                       wraps=eligible_historical_candidates) as web_candidates:
            view = build_live_view(self.database, self.now, snapshot=snapshot)
            build_web_payload(self.database, view, snapshot, self.now)
        self.assertEqual(live_candidates.call_count, 1)
        self.assertEqual(web_candidates.call_count, 0)

    def test_full_payload_query_count_is_constant_for_30_and_300_days(self):
        from dashboard.facts.history import eligible_historical_candidates
        measurements = []
        for days in (30, 300):
            self.database.close()
            self.database = SolarDatabase(
                str(self.directory / f"query-{days}.db"))
            base = date(2025, 1, 1)
            for offset in range(days):
                self.complete_day(base + timedelta(days=offset),
                                  10 + offset % 5)
            self.now = datetime.combine(base + timedelta(days=days),
                                        datetime.min.time(),
                                        tzinfo=ZURICH).replace(hour=12)
            self.snapshot(energy=15.2)
            statements = []
            self.database.connection.set_trace_callback(
                lambda sql: statements.append(sql) if
                sql.lstrip().upper().startswith("SELECT") else None)
            with patch("dashboard.live_view.eligible_historical_candidates",
                       wraps=eligible_historical_candidates) as candidates:
                snapshot = self.database.latest_snapshot()
                view = build_live_view(self.database, self.now, snapshot=snapshot)
                payload = build_web_payload(
                    self.database, view, snapshot, self.now)
            self.database.connection.set_trace_callback(None)
            aggregate_selects = [sql for sql in statements
                                 if "AGGREGATES_5M" in sql.upper()]
            self.assertIsNotNone(payload["historical_comparison"])
            self.assertEqual(candidates.call_count, 1)
            measurements.append((len(statements), len(aggregate_selects)))
        self.assertEqual(measurements, [(20, 9), (20, 9)])

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
