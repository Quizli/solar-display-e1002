import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVASCRIPT = ROOT / "web" / "assets" / "dashboard.js"


class WebDashboardJavaScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = r'''
const fs = require("fs"), vm = require("vm");
const context = {window: {}, Intl, Date, Object, Array, Math, Number, String};
vm.createContext(context);
vm.runInContext(fs.readFileSync("web/assets/dashboard.js", "utf8"), context);
const api = context.window.SolarDashboard;
const normal = api.chartModel([
  {start_at:"2026-07-27T08:00:00Z",solar_power_kw:2,house_consumption_kw:1,battery_power_kw:-1},
  {start_at:"2026-07-27T08:05:00Z",solar_power_kw:3,house_consumption_kw:null,battery_power_kw:.5},
  {start_at:"2026-07-27T08:20:00Z",solar_power_kw:4,house_consumption_kw:2,battery_power_kw:null}
]);
const x = value => value, y = value => value;
process.stdout.write(JSON.stringify({
  constants:[api.DATA_URL,api.SCHEMA_VERSION,api.REFRESH_MS,api.SOLAR_CAPACITY_KWP,api.TIME_ZONE],
  numbers:[api.formatNumber(12.34,"decimal"),api.formatNumber(null,"decimal"),api.formatNumber(NaN,"integer")],
  times:[api.formatDate("2026-07-27"),api.formatTime("2026-07-27T17:04:00Z"),api.formatTime("06:02")],
  segments:[api.segmentCount(-1,100),api.segmentCount(50,100),api.segmentCount(500,100),api.segmentCount(null,100),api.segmentCount(21.78,21.78)],
  battery:["charging","discharging","idle","unavailable","bad"].map(v=>api.safeDirection(v,{charging:1,discharging:1,idle:1,unavailable:1})),
  grid:["exporting","importing","idle","unavailable","bad"].map(v=>api.safeDirection(v,{exporting:1,importing:1,idle:1,unavailable:1})),
  chart:{points:normal.points.length,bottom:normal.bottom,solarSequences:api.pathSequences(normal,"solar",x,y).length,houseSequences:api.pathSequences(normal,"house",x,y).length,batterySequences:api.pathSequences(normal,"battery",x,y).length,empty:api.chartModel([]).points.length}
}));
'''
        result = subprocess.run(["node", "-e", probe], cwd=ROOT, text=True,
                                capture_output=True, check=True)
        cls.result = json.loads(result.stdout)

    def test_constants_and_only_relative_data_source(self):
        self.assertEqual(self.result["constants"],
                         ["dashboard.json", "1.0", 15000, 21.78, "Europe/Zurich"])

    def test_swiss_numbers_dates_times_and_invalid_values(self):
        self.assertEqual(self.result["numbers"], ["12.3", "—", "—"])
        self.assertEqual(self.result["times"],
                         ["MONTAG, 27. JULI 2026", "19:04", "06:02"])

    def test_bounded_solar_and_battery_segments(self):
        self.assertEqual(self.result["segments"], [0, 10, 20, 0, 20])

    def test_all_explicit_direction_states_and_invalid_fallback(self):
        self.assertEqual(self.result["battery"],
                         ["charging", "discharging", "idle", "unavailable", "unavailable"])
        self.assertEqual(self.result["grid"],
                         ["exporting", "importing", "idle", "unavailable", "unavailable"])

    def test_chart_gaps_nulls_negative_battery_and_empty_series(self):
        chart = self.result["chart"]
        self.assertEqual(chart["points"], 3)
        self.assertLess(chart["bottom"], 0)
        self.assertEqual(chart["solarSequences"], 2)
        self.assertEqual(chart["houseSequences"], 2)
        self.assertEqual(chart["batterySequences"], 1)
        self.assertEqual(chart["empty"], 0)

    def test_failure_is_atomic_and_recovery_paths_exist(self):
        source = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertLess(source.index("if (!validPayload(payload))"),
                        source.rindex("render(payload)"))
        self.assertLess(source.rindex("render(payload)"), source.index("lastGood = payload"))
        self.assertIn('statusText("offline", lastGood', source)
        self.assertIn("lastGood = payload", source)
        for state in ("fresh", "degraded", "stale", "missing", "offline", "loading"):
            self.assertIn(state, source)


if __name__ == "__main__":
    unittest.main()
