import unittest
from dashboard.facts.editorial import ALIASES, ENERGY, energy_candidate, render
from dashboard.facts.width import both_lines_fit


class EditorialTests(unittest.TestCase):
    def test_entire_energy_catalogue(self):
        self.assertEqual((len(ENERGY), len(ALIASES)), (76, 27))
        for row in ENERGY.values():
            for energy in (.1, .5, 1, 5, 25, 80, 150, 250, 300):
                candidate = energy_candidate(row, energy, energy)
                if candidate:
                    for variant in (0, 1):
                        actual, lines = render(candidate, "slot", variant)
                        self.assertTrue(both_lines_fit(lines, 510), row["id"])
                        self.assertNotIn("{", "".join(lines))

    def test_physical_assumptions(self):
        self.assertEqual(energy_candidate(ENERGY["goesgen"], 104.1, 104.1).values["duration"], "0,37 Sekunden")
        self.assertAlmostEqual(ENERGY["lhc"]["power_w"] * 8760 / 1e9, 600)
        self.assertEqual(energy_candidate(ENERGY["battery_equivalent"], 15.795, 15.795).values["quantity"], "1,0")
