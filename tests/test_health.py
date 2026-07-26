import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.health import check_health, format_health_text
from fronius.model import LiveData
from solar_data.storage import SolarDatabase, _utc_text
from sun_data.cache import SunDataResult
from sun_data.client import SunData


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.db_path = self.directory / "solar.db"
        self.svg_path = self.directory / "dashboard.svg"
        self.now = datetime(2026, 7, 25, 11, 5, tzinfo=timezone.utc)
        self.db = SolarDatabase(str(self.db_path))
        self.svg_path.write_text("<svg></svg>", encoding="utf-8")
        os.utime(self.svg_path, (self.now.timestamp(), self.now.timestamp()))

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def snapshot(self, age=0):
        timestamp = self.now - timedelta(seconds=age)
        self.db.store_snapshot(LiveData(
            timestamp=timestamp.isoformat(), solar_power_kw=9, house_power_kw=7,
            heat_power_kw=2, battery_soc_pct=0, battery_power_kw=0,
            grid_power_kw=2, energy_today_kwh=12, battery_available=False,
            heat_available=True, energy_total_kwh=100))

    def aggregate(self):
        start = self.now - timedelta(minutes=5)
        self.db.connection.execute(
            "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_utc_text(start), 9, 7, 2, 0, 0, 2, 12, 3, 0, 1))
        self.db.connection.commit()

    def sun_result(self):
        local = self.now.astimezone()
        data = SunData(local.date(), local, local + timedelta(hours=8), 7.0,
                       self.now, 1, "valid")
        return SunDataResult(data, "fresh", "cache")

    def check(self, **kwargs):
        with patch("dashboard.health._offline_sun_result", return_value=self.sun_result()):
            return check_health(str(self.db_path), str(self.svg_path), now=self.now,
                                **kwargs)

    def test_healthy_result_and_exit_code_zero(self):
        self.snapshot()
        self.aggregate()
        report, code = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["database"], "ok")
        self.assertEqual(report["schema_version"], 4)

    def test_stale_data_is_unhealthy(self):
        self.snapshot(age=181)
        report, code = self.check()
        self.assertEqual((report["status"], report["freshness"], code),
                         ("unhealthy", "stale", 2))

    def test_raw_snapshot_fallback_is_degraded(self):
        self.snapshot()
        report, code = self.check()
        self.assertEqual((report["status"], report["power_source"], code),
                         ("degraded", "raw_snapshot", 1))

    def test_missing_svg_is_unhealthy(self):
        self.snapshot()
        self.aggregate()
        self.svg_path.unlink()
        report, code = self.check()
        self.assertEqual((report["published_svg"], code), ("missing", 2))

    def test_unresolved_svg_placeholder_is_unhealthy(self):
        self.snapshot()
        self.aggregate()
        self.svg_path.write_text("<svg>{{value}}</svg>", encoding="utf-8")
        report, code = self.check()
        self.assertEqual((report["published_svg"], code), ("invalid", 2))

    def test_unavailable_sun_data_alone_is_degraded(self):
        self.snapshot()
        self.aggregate()
        with patch("dashboard.health._offline_sun_result",
                   return_value=SunDataResult(None, "missing", "none")):
            report, code = check_health(str(self.db_path), str(self.svg_path), now=self.now)
        self.assertEqual((report["status"], code), ("degraded", 1))

    def test_text_output(self):
        self.snapshot()
        self.aggregate()
        report, _ = self.check()
        text = format_health_text(report)
        self.assertIn("Solar Display: HEALTHY", text)
        self.assertIn("Database: OK, schema 4", text)
        self.assertIn("Power source: aggregates_5m", text)

    def test_health_does_not_modify_database_or_fact_history(self):
        self.snapshot()
        self.aggregate()
        before = self.db_path.read_bytes()
        before_history = self.db.connection.execute(
            "SELECT COUNT(*) FROM fact_history").fetchone()[0]
        self.check()
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM fact_history").fetchone()[0], before_history)


if __name__ == "__main__":
    unittest.main()
