import json
import math
import unittest
from pathlib import Path

from fronius.adapter import FroniusDataError, normalize_live_data


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    with (FIXTURES / name).open(encoding="utf-8") as source:
        return json.load(source)


class NormalizeLiveDataTest(unittest.TestCase):
    def setUp(self):
        self.power_flow = fixture("powerflow_night.json")
        self.storage = fixture("storage_empty.json")
        self.ohmpilot = fixture("ohmpilot_empty.json")

    def test_night_snapshot_and_missing_optional_components(self):
        result = normalize_live_data(
            self.power_flow,
            fixture("inverter_day_energy_null.json"),
            self.storage,
            self.ohmpilot,
        )

        self.assertEqual(result.timestamp, "2026-07-23T23:15:00+02:00")
        self.assertEqual(result.solar_power_kw, 0.0)
        self.assertAlmostEqual(result.house_power_kw, 2.7002)
        self.assertAlmostEqual(result.grid_power_kw, -2.7002)
        self.assertFalse(result.battery_available)
        self.assertEqual(result.battery_soc_pct, 0.0)
        self.assertEqual(result.battery_power_kw, 0.0)
        self.assertFalse(result.heat_available)
        self.assertEqual(result.heat_power_kw, 0.0)
        self.assertEqual(result.energy_today_kwh, 0.0)

    def test_day_energy_is_converted_from_wh_to_kwh(self):
        result = normalize_live_data(
            self.power_flow, fixture("inverter_day_energy_value.json")
        )
        self.assertAlmostEqual(result.energy_today_kwh, 48.321)

    def test_day_energy_sums_valid_values_for_multiple_inverters(self):
        payload = fixture("inverter_day_energy_value.json")
        payload["Body"]["Data"]["DAY_ENERGY"]["Values"].update(
            {"2": 1679, "3": None}
        )
        result = normalize_live_data(self.power_flow, payload)
        self.assertAlmostEqual(result.energy_today_kwh, 50.0)

    def test_missing_day_energy_payload_falls_back_to_zero(self):
        self.assertEqual(normalize_live_data(self.power_flow).energy_today_kwh, 0.0)

    def test_missing_or_invalid_critical_values_raise(self):
        for value in (None, "0", float("nan"), float("inf")):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(self.power_flow))
                payload["Body"]["Data"]["Site"]["P_PV"] = value
                with self.assertRaises(FroniusDataError):
                    normalize_live_data(payload)

        payload = json.loads(json.dumps(self.power_flow))
        del payload["Body"]["Data"]["Site"]["P_Grid"]
        with self.assertRaises(FroniusDataError):
            normalize_live_data(payload)

    def test_non_finite_day_energy_raises(self):
        payload = fixture("inverter_day_energy_value.json")
        payload["Body"]["Data"]["DAY_ENERGY"]["Values"]["1"] = math.inf
        with self.assertRaises(FroniusDataError):
            normalize_live_data(self.power_flow, payload)

    def test_non_numeric_day_energy_raises(self):
        payload = fixture("inverter_day_energy_value.json")
        payload["Body"]["Data"]["DAY_ENERGY"]["Values"]["1"] = "48321"
        with self.assertRaises(FroniusDataError):
            normalize_live_data(self.power_flow, payload)


if __name__ == "__main__":
    unittest.main()
