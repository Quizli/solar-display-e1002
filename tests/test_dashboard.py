import argparse
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.live_view import build_live_view
from dashboard.live_view import validate_co2_factor
from dashboard.__main__ import _positive, _sun_coordinates
from sun_data.cache import SunDataResult
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

    def test_old_aggregates_fall_back_to_fresh_raw_snapshot(self):
        self.snapshot()
        self.aggregate(self.now-timedelta(hours=3), 2, 3, 1, 0, 1, 2)
        view = build_live_view(self.db, self.now)
        self.assertEqual(view["power_source"], "raw_snapshot")
        self.assertEqual(view["display"]["solar_power_kw"], 9)
        self.assertIn("too_old", view["power_selection_reason"])

    def test_previous_evening_aggregate_is_not_morning_power(self):
        morning = datetime(2026, 7, 26, 6, 0, tzinfo=timezone.utc)
        self.snapshot(morning)
        self.aggregate(datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc), 15, 3, 1, 0, 12, 2)
        view = build_live_view(self.db, morning)
        self.assertEqual(view["power_source"], "raw_snapshot")
        self.assertEqual(view["display"]["solar_power_kw"], 9)

    def test_gap_uses_only_newest_recent_bucket(self):
        self.snapshot()
        self.aggregate(self.now-timedelta(minutes=15), 2, 4, 1, 0, 1, 5)
        self.aggregate(self.now-timedelta(minutes=5), 8, 10, 3, 0, 2, 1)
        view = build_live_view(self.db, self.now)
        self.assertEqual(view["power_bucket_count"], 1)
        self.assertEqual(view["display"]["solar_power_kw"], 8)
        self.assertEqual(view["power_selection_reason"], "newest_recent_aggregate_after_gap")

    def test_aggregate_power_timestamp_is_bucket_end_and_separate_from_snapshot(self):
        self.snapshot(self.now + timedelta(minutes=2))
        self.aggregate(self.now-timedelta(minutes=5), 8, 10, 3, 0, 2, 1)
        view = build_live_view(self.db, self.now + timedelta(minutes=2))
        self.assertEqual(view["latest_snapshot_timestamp"], (self.now+timedelta(minutes=2)).isoformat())
        self.assertEqual(view["power_timestamp"], self.now.isoformat())
        self.assertEqual(view["display"]["current_time"], "13:05 Uhr")

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
        self.assertAlmostEqual(view["display"]["co2_savings_kg"], (10/6) * .128)
        self.assertAlmostEqual(view["co2"]["factor_kg_per_kwh"], .128)

    def test_co2_missing_zero_negative_and_invalid_factor(self):
        self.assertIsNone(build_live_view(self.db, self.now)["display"]["co2_savings_kg"])
        self.snapshot()
        self.aggregate(self.now-timedelta(minutes=5), 0, 2, 1, 0, -2, 1)
        self.assertEqual(build_live_view(self.db, self.now)["display"]["co2_savings_kg"], 0)
        for factor in (-1, float("nan"), float("inf"), "bad", True):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                validate_co2_factor(factor)

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
        self.assertNotIn("—%", svg)

    def test_self_consumption_unit_only_appears_with_number(self):
        data = {"self_consumption_percent": 42}
        self.assertIn(">42</tspan><tspan dx=\"6\" font-size=\"14\" font-weight=\"400\">%</tspan>",
                      render_dashboard(data))
        self.assertNotIn("—%", render_dashboard({"self_consumption_percent": None}))

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
            mode = stat.S_IMODE(output.stat().st_mode)
            self.assertEqual(mode, 0o644)
            self.assertTrue(mode & stat.S_IROTH, "Nginx must be able to read the published SVG")
            self.assertTrue(os.access(output, os.R_OK))
            old = output.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                publish_view(build_live_view(SolarDatabase(":memory:"), self.now), output)
            self.assertEqual(output.read_text(encoding="utf-8"), old)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_invalid_sun_data_does_not_block_solar_dashboard(self):
        self.snapshot()
        result = SunDataResult(None, "invalid", "none", "bad forecast")
        view = build_live_view(self.db, self.now, sun_result=result)
        self.assertEqual(view["sun_data_status"], "invalid")
        self.assertEqual(view["sun_data_error"], "bad forecast")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dashboard.svg"
            publish_view(view, output)
            self.assertTrue(output.is_file())

    def test_positive_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                _positive(value)

    def test_partial_sun_coordinates_are_a_configuration_error(self):
        self.assertIsNone(_sun_coordinates({}))
        self.assertEqual(_sun_coordinates({"SOLAR_LAT": "47", "SOLAR_LON": "8"}), ("47", "8"))
        for environment in ({"SOLAR_LAT": "47"}, {"SOLAR_LON": "8"}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                _sun_coordinates(environment)

    def test_sample_cli_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sample.svg"
            self.assertEqual(render_main(["--output", str(output)]), 0)
            self.assertIn("<svg", output.read_text(encoding="utf-8"))


class IgnoreFilesTest(unittest.TestCase):
    def test_runtime_data_and_environment_are_excluded(self):
        docker_rules = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
        for required in (".env", "data/", "publish/"):
            self.assertIn(required, docker_rules)
        git_rules = Path(".gitignore").read_text(encoding="utf-8").splitlines()
        for required in ("publish/", "renderer/output/", "data/", ".env"):
            self.assertIn(required, git_rules)

    def test_compose_uses_persistent_data_mount_for_sun_cache(self):
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        publisher = compose.split("  dashboard-publisher:", 1)[1].split("  web:", 1)[0]
        self.assertIn("SUN_DATA_CACHE_PATH: /data/sun-data.json", publisher)
        self.assertIn("- ./data:/data", publisher)


if __name__ == "__main__":
    unittest.main()
