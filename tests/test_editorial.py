import unittest
from dashboard.facts.editorial import ALIASES, ENERGY, energy_candidate, render
from dashboard.facts.width import both_lines_fit


class EditorialTests(unittest.TestCase):
    def test_entire_energy_catalogue(self):
        self.assertEqual((len(ENERGY), len(ALIASES)), (76, 27))
        for row in ENERGY.values():
            for energy in (0.1, 0.5, 1, 5, 25, 80, 150, 250, 300):
                candidate = energy_candidate(row, energy, energy)
                if candidate:
                    for variant in (0, 1):
                        actual, lines = render(candidate, "slot", variant)
                        self.assertEqual(actual, variant, (row["id"], energy))
                        self.assertTrue(both_lines_fit(lines, 510), row["id"])
                        self.assertNotIn("{", "".join(lines))

    def test_each_one_use_variant_and_its_grammar(self):
        expected = {
            "washing_machine": ("1 Waschgang", "1 Ladung"),
            "dishwasher": ("wäre rund 1 Spülgang", "1 Eco-Gang"),
            "dryer": ("1 Tumblerladung", "etwa einmal"),
            "fridge": ("1 Kühlschranktag", "1 Tag aus"),
            "espresso": ("liesse sich rund 1 Espresso", "1 Espresso bei"),
            "kettle": ("liesse sich rund 1 Liter", "1 Liter Wasser"),
            "toast": ("1 Scheibe rösten", "1 geröstete Toastscheibe"),
            "raclette": ("1 Racletteportion", "1 Portion Raclette"),
            "soup": ("liesse sich rund 1 Suppenportion", "1 Suppenportion"),
            "oven_pizza": ("1 Ofengang", "wäre mit"),
            "ice_cubes": ("1 Eiswürfel", "1 Würfel"),
            "phone_charge": ("1 Smartphone-Ladung", "etwa einmal"),
            "tablet_charge": ("etwa einmal", "1 Tablet-Ladung"),
            "ereader_charge": ("1 E-Reader-Ladung", "etwa einmal"),
            "powerbank_charge": (
                "1 Powerbank-Ladung",
                "liesse sich rund 1 Powerbank mit",
            ),
            "lift_trip": ("1 kurze Liftfahrt", "1 Fahrt."),
            "lift_tonne": ("etwa einmal", "1 Hub"),
            "sprint_car": ("etwa einmal", "1 Start von"),
            "lift_person_alps": ("etwa einmal", "1 Aufstieg"),
        }
        self.assertEqual(
            set(expected), {r["id"] for r in ENERGY.values() if r["type"] == "uses"}
        )
        for ident, fragments in expected.items():
            row = ENERGY[ident]
            # The hourly eligibility anchor and the live display value can differ.
            candidate = energy_candidate(
                row, max(row["min_kwh"], row["kwh_per_unit"]), row["kwh_per_unit"]
            )
            self.assertEqual(candidate.values["count"], "1")
            for preferred, fragment in enumerate(fragments):
                index, lines = render(candidate, "test", preferred)
                self.assertEqual(index, preferred)
                self.assertIn(fragment, " ".join(lines))
                self.assertTrue(both_lines_fit(lines, 510))

    def test_physical_assumptions(self):
        self.assertEqual(
            energy_candidate(ENERGY["goesgen"], 104.1, 104.1).values["duration"],
            "0,37 Sekunden",
        )
        self.assertAlmostEqual(ENERGY["lhc"]["power_w"] * 8760 / 1e9, 600)
        self.assertEqual(
            energy_candidate(ENERGY["battery_equivalent"], 15.795, 15.795).values[
                "quantity"
            ],
            "1,0",
        )


class MigrationAndStateTests(unittest.TestCase):
    def test_populated_copy_concurrent_initialization_and_lifetime_crossing(self):
        import sqlite3
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime, timedelta
        from dataclasses import replace
        from pathlib import Path
        from threading import Barrier

        from dashboard.facts.milestones import active_events
        from dashboard.facts.models import FactContext
        from dashboard.preview import synthetic
        from solar_data.storage import SolarDatabase
        from solar_data.timezones import ZURICH

        now = datetime(2026, 6, 1, 10, tzinfo=ZURICH)
        sample, _ = synthetic(now.date(), now, "sunny-summer")
        ctx = FactContext(now, None, None, 2, None, 1, None)
        config = {"SOLAR_COMMISSIONING_TOTAL_KWH": "0"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "copy.db"
            with (
                SolarDatabase(str(Path(folder) / "original.db")) as db,
                sqlite3.connect(path) as target,
            ):
                db.store_fact_selection("legacy", now, "N01", "legacy", 2)
                db.store_snapshot(sample)
                db.connection.backup(target)
                target.executescript(
                    "DROP TABLE insight_state; DROP TABLE milestone_events;"
                )
            with SolarDatabase(str(path)) as db:
                active_events(
                    db,
                    ctx,
                    {},
                    True,
                    replace(sample, energy_total_kwh=999.9),
                    config=config,
                )
            barrier = Barrier(2)

            def publish(_):
                with SolarDatabase(str(path)) as db:
                    barrier.wait(timeout=5)
                    later = now + timedelta(seconds=30)
                    events, _ = active_events(
                        db,
                        replace(ctx, now_local=later),
                        {},
                        True,
                        replace(
                            sample, timestamp=later.isoformat(), energy_total_kwh=1000
                        ),
                        config=config,
                    )
                    return [c.reference for c in events]

            with ThreadPoolExecutor(2) as pool:
                for result in pool.map(publish, range(2)):
                    self.assertEqual(result, ["LIFETIME_ENERGY:1000"])
            with SolarDatabase(str(path), read_only=True) as db:
                self.assertEqual(db.get_fact_selection("legacy").fact_id, "N01")
                self.assertEqual(
                    db.latest_snapshot().energy_today_kwh, sample.energy_today_kwh
                )
                self.assertEqual(
                    db.connection.execute(
                        "SELECT count(*) FROM milestone_events"
                    ).fetchone()[0],
                    1,
                )
