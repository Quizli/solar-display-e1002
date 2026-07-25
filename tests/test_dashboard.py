import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.live_view import build_live_view
from dashboard.publisher import publish_view
from fronius.model import LiveData
from renderer.src.render import main as render_main, render_dashboard
from solar_data.storage import SolarDatabase, _utc_text


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.db = SolarDatabase(":memory:")
        self.now = datetime(2026, 7, 25, 11, 5, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def snapshot(self, timestamp=None, battery=False):
        self.db.store_snapshot(LiveData(timestamp=(timestamp or self.now).isoformat(), solar_power_kw=9,
            house_power_kw=7, heat_power_kw=2, battery_soc_pct=0, battery_power_kw=0,
            grid_power_kw=2, energy_today_kwh=0, battery_available=battery,
            heat_available=True, energy_total_kwh=None))

    def aggregate(self, start, solar, house, heat, battery, grid, count):
        self.db.connection.execute("INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_utc_text(start), solar, house, heat, 0, battery, grid, 0, count, 0, 1))
        self.db.connection.commit()

    def test_two_buckets_are_sample_weighted_and_house_includes_heat(self):
        self.snapshot()
        self.aggregate(self.now-timedelta(minutes=10), 2, 4, 1, 0, 1, 1)
        self.aggregate(self.now-timedelta(minutes=5), 8, 10, 3, 0, 2, 3)
        view = build_live_view(self.db, self.now)
        self.assertEqual(view["power_bucket_count"], 2)
        self.assertEqual(view["display"]["solar_power_kw"], 6.5)
        self.assertEqual(view["display"]["house_power_kw"], 8.5)
        self.assertEqual(view["display"]["heat_power_kw"], 2.5)

    def test_one_bucket_then_raw_fallback(self):
        self.snapshot()
        self.aggregate(self.now-timedelta(minutes=5), 4, 5, 2, 0, 1, 2)
        self.assertEqual(build_live_view(self.db, self.now)["display"]["solar_power_kw"], 4)
        self.db.connection.execute("DELETE FROM aggregates_5m")
        view = build_live_view(self.db, self.now)
        self.assertEqual(view["power_source"], "raw_snapshot")
        self.assertEqual(view["display"]["solar_power_kw"], 9)

    def test_daily_energy_and_self_consumption(self):
        self.snapshot()
        self.aggregate(self.now-timedelta(minutes=10), 10, 8, 2, 0, 4, 2)
        self.aggregate(self.now-timedelta(minutes=5), 10, 8, 2, 0, 2, 2)
        view = build_live_view(self.db, self.now)
        self.assertEqual(view["daily_energy"]["source"], "solar_integration")
        self.assertAlmostEqual(view["display"]["day_yield_kwh"], 10/6)
        self.assertAlmostEqual(view["display"]["self_consumption_percent"], 70)

    def test_zero_production_is_unavailable(self):
        self.snapshot()
        self.aggregate(self.now-timedelta(minutes=5), 0, 2, 1, 0, -2, 1)
        self.assertIsNone(build_live_view(self.db, self.now)["display"]["self_consumption_percent"])

    def test_battery_unavailable_and_optional_fields_render_as_dashes(self):
        self.snapshot()
        svg = render_dashboard(build_live_view(self.db, self.now)["display"])
        self.assertIn("Batterie folgt", svg)
        self.assertNotIn("{{", svg)
        self.assertIn("—", svg)

    def test_fresh_stale_missing_and_zurich_time(self):
        self.assertEqual(build_live_view(self.db, self.now)["freshness"], "missing")
        self.snapshot()
        fresh = build_live_view(self.db, self.now)
        self.assertEqual(fresh["freshness"], "fresh")
        self.assertEqual(fresh["display"]["current_time"], "13:05 Uhr")
        self.assertEqual(fresh["display"]["date_text"], "Samstag, 25. Juli 2026")
        stale = build_live_view(self.db, self.now+timedelta(minutes=4))
        self.assertEqual(stale["freshness"], "stale")
        self.assertIn("Datenstand", stale["display"]["story_line_1"])

    def test_atomic_publish_and_failed_publish_preserves_file(self):
        self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dashboard.svg"
            output.write_text("old", encoding="utf-8")
            publish_view(build_live_view(self.db, self.now), output)
            self.assertIn("<svg", output.read_text(encoding="utf-8"))
            old = output.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                publish_view(build_live_view(SolarDatabase(":memory:"), self.now), output)
            self.assertEqual(output.read_text(encoding="utf-8"), old)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_sample_cli_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sample.svg"
            self.assertEqual(render_main(["--output", str(output)]), 0)
            self.assertIn("<svg", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
