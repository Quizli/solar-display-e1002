import unittest
from datetime import date, datetime, timezone

from dashboard.chart import (
    PLOT_BOTTOM, PLOT_LEFT, PLOT_RIGHT, PLOT_TOP,
    battery_y, build_chart, power_y, x_for_local_time,
)
from renderer.src.render import render_dashboard
from solar_data.storage import Aggregate5m


def aggregate(timestamp, solar=4.0, house=2.0, soc=60.0, battery=True):
    return Aggregate5m(
        bucket_start=timestamp, solar_power_kw=solar, house_power_kw=house,
        heat_power_kw=0.0, battery_soc_pct=soc, battery_power_kw=0.0,
        grid_power_kw=0.0, energy_today_kwh=1.0, sample_count=1,
        battery_available=battery, heat_available=False,
    )


class DailyChartTest(unittest.TestCase):
    day = date(2026, 7, 25)
    now = datetime(2026, 7, 25, 14, 40, tzinfo=timezone.utc)

    def test_fixed_axis_scaling_and_clipping(self):
        midnight = datetime(2026, 7, 24, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(x_for_local_time(midnight), PLOT_LEFT)
        almost_midnight = datetime(2026, 7, 25, 21, 55, tzinfo=timezone.utc)
        self.assertAlmostEqual(x_for_local_time(almost_midnight),
                               PLOT_RIGHT - (PLOT_RIGHT - PLOT_LEFT) / 288)
        self.assertEqual(power_y(0), PLOT_BOTTOM)
        self.assertEqual(power_y(24), PLOT_TOP)
        self.assertEqual(power_y(30), PLOT_TOP)
        self.assertEqual(battery_y(0), PLOT_BOTTOM)
        self.assertEqual(battery_y(100), PLOT_TOP)

    def test_gaps_create_separate_paths_and_areas(self):
        chart = build_chart([
            aggregate("2026-07-25T08:00:00+00:00"),
            aggregate("2026-07-25T08:05:00+00:00"),
            aggregate("2026-07-25T08:15:00+00:00"),
        ], self.day, self.now)
        self.assertEqual(chart.solar_line.count("M "), 2)
        self.assertEqual(chart.solar_area.count(" Z"), 2)
        self.assertEqual(chart.house_line.count("M "), 2)

    def test_future_is_omitted_without_inventing_zeroes(self):
        chart = build_chart([
            aggregate("2026-07-25T08:00:00+00:00", solar=3),
            aggregate("2026-07-25T15:00:00+00:00", solar=9),
        ], self.day, datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(chart.solar_points, 1)
        self.assertNotIn(str(PLOT_BOTTOM), chart.solar_line)

    def test_battery_series_is_strictly_optional(self):
        absent = build_chart([
            aggregate("2026-07-25T08:00:00+00:00", battery=False),
        ], self.day, self.now)
        self.assertFalse(absent.battery_available)
        self.assertEqual(absent.battery_line, "")
        present = build_chart([
            aggregate("2026-07-25T08:00:00+00:00", soc=75),
        ], self.day, self.now)
        self.assertTrue(present.battery_available)
        self.assertEqual(present.battery_points, 1)
        self.assertTrue(present.battery_line.startswith("M "))

    def test_svg_has_dynamic_paths_and_no_placeholders(self):
        chart = build_chart([
            aggregate("2026-07-25T08:00:00+00:00", battery=False),
        ], self.day, self.now)
        svg = render_dashboard({
            "chart_solar_area": chart.solar_area,
            "chart_solar_line": chart.solar_line,
            "chart_house_line": chart.house_line,
            "chart_battery_line": chart.battery_line,
        })
        self.assertNotIn("{{", svg)
        self.assertIn(f'd="{chart.solar_line}"', svg)
        self.assertNotIn('<path d=""', svg)


if __name__ == "__main__":
    unittest.main()
