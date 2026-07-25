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
from dashboard.chart import power_y
from dashboard.__main__ import _positive, _sun_coordinates
from sun_data.cache import SunDataResult
from sun_data.client import SunData
from dashboard.publisher import publish_view
from fronius.model import LiveData
from renderer.src.render import format_day_yield, main as render_main, render_dashboard
from solar_data.storage import SolarDatabase, _utc_text


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.db = SolarDatabase(":memory:")
        self.now = datetime(2026, 7, 25, 11, 5, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def snapshot(self, timestamp=None, battery=False, heat_available=True):
        self.db.store_snapshot(LiveData(timestamp=(timestamp or self.now).isoformat(), solar_power_kw=9,
            house_power_kw=7, heat_power_kw=2 if heat_available else 0,
            battery_soc_pct=0, battery_power_kw=0,
            grid_power_kw=2, energy_today_kwh=0, battery_available=battery,
            heat_available=heat_available, energy_total_kwh=None))

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
        self.assertEqual(view["display"]["display_house_power_kw"], 6.0)

    def test_small_house_kpi_uses_displayed_aggregate_heat(self):
        self.snapshot(heat_available=False)
        self.aggregate(self.now-timedelta(minutes=5), 8, 5, 2, 0, 2, 1)
        view = build_live_view(self.db, self.now)
        self.assertEqual(view["display"]["house_power_kw"], 5)
        self.assertEqual(view["display"]["display_house_power_kw"], 3)
        self.assertFalse(self.db.latest_snapshot().heat_available)
        self.assertEqual(self.db.latest_snapshot().house_power_kw, 7)
        svg = render_dashboard(view["display"])
        self.assertIn(">3.0 kW</text>", svg)
        self.assertIn(">2.0 kW</text>", svg)
        self.assertEqual(view["display"]["chart_house_line"].count("M "), 1)
        self.assertIn(f"{power_y(5):.2f}".rstrip("0").rstrip("."),
                      view["display"]["chart_house_line"])
        self.assertEqual(view["display"]["self_consumption_percent"], 75)

        self.db.connection.execute("DELETE FROM aggregates_5m")
        self.db.connection.commit()
        self.assertEqual(build_live_view(self.db, self.now)["display"]["display_house_power_kw"], 7)

    def test_small_house_kpi_handles_unavailable_and_excess_heat(self):
        self.snapshot(heat_available=False)
        unavailable = build_live_view(self.db, self.now)
        self.assertEqual(unavailable["display"]["display_house_power_kw"], 7)
        self.db.connection.execute("DELETE FROM raw_samples")
        self.snapshot(heat_available=True)
        self.aggregate(self.now-timedelta(minutes=5), 8, 1, 2, 0, 2, 1)
        self.assertEqual(build_live_view(self.db, self.now)["display"]["display_house_power_kw"], 0)

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

    def test_self_consumption_uses_power_data_day_while_chart_uses_today(self):
        now = datetime(2026, 7, 26, 6, 0, tzinfo=timezone.utc)
        previous_snapshot = datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc)
        self.snapshot(previous_snapshot)
        self.aggregate(previous_snapshot-timedelta(minutes=5), 10, 6, 1, 0, 5, 1)
        self.aggregate(now-timedelta(minutes=5), 10, 6, 1, 0, 0, 1)
        view = build_live_view(self.db, now)
        self.assertEqual(view["display"]["self_consumption_percent"], 50)
        self.assertEqual(view["chart_status"]["local_day"], "2026-07-26")
        self.assertEqual(view["chart_status"]["aggregate_count"], 1)

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

    def test_legacy_weatherless_sun_data_uses_sunny_view_fallback(self):
        sun = SunData(self.now.date(), self.now, self.now + timedelta(hours=12),
                      7.6, self.now, None, "missing")
        result = SunDataResult(sun, "fresh", "cache")
        view = build_live_view(self.db, self.now, sun_result=result)
        self.assertEqual(view["weather_code_status"], "missing")
        self.assertEqual(view["weather_icon_variant"], "sunny")
        self.assertEqual(view["display"]["sun_hours"], 7.6)
        self.assertIn('data-weather-icon="sunny"', render_dashboard(view["display"]))

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
            svg = output.read_text(encoding="utf-8")
            self.assertIn("<svg", svg)
            self.assertIn('<path d="M 280 276', svg)

    def test_day_yield_formatting(self):
        for value, expected in ((4.902, "4.9"), (99.4, "99.4"),
                                (99.96, "100"), (100.0, "100"),
                                (135.7, "136"), (None, "—")):
            with self.subTest(value=value):
                self.assertEqual(format_day_yield(value), expected)

    def test_requested_template_geometry_adjustments_only(self):
        template = Path("renderer/template/dashboard_template.svg").read_text()
        for y in (315, 359, 403, 447):
            self.assertIn(f'<text x="205" y="{y}"', template)
            self.assertNotIn(f'<text x="202" y="{y}"', template)
        self.assertIn(">Wärme</text>", template)
        self.assertIn('<g transform="translate(-9 0)">{{WEATHER_ICON_SVG}}', template)
        self.assertIn('x="22" y="390" width="12" height="19"', template)
        self.assertNotIn('x="22" y="389" width="12" height="19"', template)
        self.assertIn('x="26" y="387" width="4" height="4"', template)
        self.assertIn('<line x1="632" y1="443" x2="645" y2="426"', template)
        svg = render_dashboard({
            "chart_solar_line": "M 1 1", "chart_house_line": "M 2 2",
            "chart_battery_line": "M 3 3",
        })
        self.assertIn('d="M 1 1" fill="none" stroke="#000000" stroke-width="2.2"', svg)
        self.assertIn('d="M 2 2" fill="none" stroke="#0057B8" stroke-width="2.5"', svg)
        self.assertIn('d="M 3 3" fill="none" stroke="#149B24" stroke-width="2.5"', svg)


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
