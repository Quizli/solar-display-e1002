import json
import math
import unittest
from pathlib import Path

from dashboard.web_publisher import _flow
from fronius.adapter import FroniusDataError, normalize_live_data
from renderer.src.render import render_dashboard


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
        self.assertAlmostEqual(result.energy_total_kwh, 1833.0094658333333)

    def test_active_reserva_discharging_uses_site_power_with_inverted_sign(self):
        self.power_flow["Body"]["Data"]["Site"]["P_Akku"] = 299.418884
        result = normalize_live_data(
            self.power_flow, storage=fixture("storage_reserva.json")
        )
        self.assertTrue(result.battery_available)
        self.assertAlmostEqual(result.battery_soc_pct, 35.400001525878906)
        self.assertAlmostEqual(result.battery_power_kw, -0.299418884)

        web_flow = _flow(result.battery_power_kw, "charging", "discharging")
        self.assertEqual(web_flow["direction"], "discharging")
        self.assertAlmostEqual(web_flow["magnitude_kw"], 0.299418884)
        svg = render_dashboard({
            "battery_available": result.battery_available,
            "battery_percent": result.battery_soc_pct,
            "battery_power_kw": result.battery_power_kw,
        })
        self.assertIn("Batterie liefert", svg)
        self.assertNotIn("Batterieladung", svg)

    def test_active_reserva_charging_and_idle_power(self):
        for fronius_w, expected_kw in ((-2500, 2.5), (0, 0.0)):
            with self.subTest(fronius_w=fronius_w):
                self.power_flow["Body"]["Data"]["Site"]["P_Akku"] = fronius_w
                result = normalize_live_data(
                    self.power_flow, storage=fixture("storage_reserva.json")
                )
                self.assertTrue(result.battery_available)
                self.assertEqual(result.battery_power_kw, expected_kw)
                if fronius_w < 0:
                    web_flow = _flow(result.battery_power_kw, "charging", "discharging")
                    self.assertEqual(web_flow["direction"], "charging")
                    self.assertIn("Batterieladung", render_dashboard({
                        "battery_available": True,
                        "battery_percent": result.battery_soc_pct,
                        "battery_power_kw": result.battery_power_kw,
                    }))

    def test_missing_or_empty_storage_is_unavailable(self):
        self.power_flow["Body"]["Data"]["Site"]["P_Akku"] = 100
        for payload in (None, {}, {"Body": {}}, {"Body": {"Data": {}}}):
            with self.subTest(payload=payload):
                result = normalize_live_data(self.power_flow, storage=payload)
                self.assertFalse(result.battery_available)
                self.assertEqual(result.battery_soc_pct, 0.0)
                self.assertEqual(result.battery_power_kw, 0.0)

    def test_invalid_reserva_soc_is_unavailable(self):
        invalid_values = (None, "35.4", True, math.nan, math.inf, -0.1, 100.1)
        for value in invalid_values:
            with self.subTest(value=value):
                storage = fixture("storage_reserva.json")
                storage["Body"]["Data"]["0"]["Controller"][
                    "StateOfCharge_Relative"] = value
                self.power_flow["Body"]["Data"]["Site"]["P_Akku"] = 100
                result = normalize_live_data(self.power_flow, storage=storage)
                self.assertFalse(result.battery_available)
                self.assertEqual(result.battery_soc_pct, 0.0)
                self.assertEqual(result.battery_power_kw, 0.0)

    def test_invalid_or_missing_site_battery_power_is_unavailable(self):
        for value in (None, "299", True, math.nan, math.inf):
            with self.subTest(value=value):
                self.power_flow["Body"]["Data"]["Site"]["P_Akku"] = value
                result = normalize_live_data(
                    self.power_flow, storage=fixture("storage_reserva.json")
                )
                self.assertFalse(result.battery_available)
                self.assertEqual(result.battery_soc_pct, 0.0)
                self.assertEqual(result.battery_power_kw, 0.0)

    def test_active_ohmpilot_power_is_normalized_without_changing_house_power(self):
        result = normalize_live_data(
            self.power_flow, ohmpilot=fixture("ohmpilot_active.json")
        )
        self.assertTrue(result.heat_available)
        self.assertAlmostEqual(result.heat_power_kw, 2.681)
        self.assertAlmostEqual(result.house_power_kw, 2.7002)

    def test_multiple_valid_ohmpilot_devices_are_summed(self):
        payload = {
            "Body": {"Data": {
                "0": {"PowerReal_PAC_Sum": 1000},
                "1": {"PowerReal_PAC_Sum": 1681.0},
            }}
        }
        result = normalize_live_data(self.power_flow, ohmpilot=payload)
        self.assertTrue(result.heat_available)
        self.assertAlmostEqual(result.heat_power_kw, 2.681)

    def test_invalid_optional_ohmpilot_values_are_ignored(self):
        payload = {
            "Body": {"Data": {
                "0": {"PowerReal_PAC_Sum": None},
                "1": {"PowerReal_PAC_Sum": True},
                "2": {"PowerReal_PAC_Sum": "2681"},
                "3": {"PowerReal_PAC_Sum": -1},
                "4": {"PowerReal_PAC_Sum": math.inf},
                "5": {"PowerReal_PAC_Sum": math.nan},
                "6": "invalid device",
            }}
        }
        result = normalize_live_data(self.power_flow, ohmpilot=payload)
        self.assertFalse(result.heat_available)
        self.assertEqual(result.heat_power_kw, 0.0)
        self.assertAlmostEqual(result.house_power_kw, 2.7002)

    def test_missing_or_invalid_optional_ohmpilot_data_is_unavailable(self):
        for payload in (None, {}, {"Body": {}}, {"Body": {"Data": {}}}):
            with self.subTest(payload=payload):
                result = normalize_live_data(self.power_flow, ohmpilot=payload)
                self.assertFalse(result.heat_available)
                self.assertEqual(result.heat_power_kw, 0.0)

    def test_site_total_energy_is_preferred_and_converted_to_kwh(self):
        self.power_flow["Body"]["Data"]["Site"]["E_Total"] = 2_500_000
        result = normalize_live_data(self.power_flow)
        self.assertEqual(result.energy_total_kwh, 2500)

    def test_total_energy_falls_back_to_sum_of_valid_inverters(self):
        self.power_flow["Body"]["Data"]["Inverters"] = {
            "1": {"E_Total": 1_000_000},
            "2": {"E_Total": 250_000},
            "3": {"E_Total": None},
            "4": {"E_Total": "invalid"},
            "5": {"E_Total": -10},
            "6": {"E_Total": math.inf},
        }
        result = normalize_live_data(self.power_flow)
        self.assertEqual(result.energy_total_kwh, 1250)

    def test_total_energy_is_unavailable_when_no_valid_counter_exists(self):
        self.power_flow["Body"]["Data"]["Inverters"] = {"1": {"E_Total": None}}
        self.assertIsNone(normalize_live_data(self.power_flow).energy_total_kwh)

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
