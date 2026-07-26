import unittest
from datetime import datetime

from dashboard.facts.catalog import FACTS, FACTS_BY_ID, render_d01
from dashboard.facts.engine import _render, build_story_from_context, determine_phase
from dashboard.facts.formatting import format_fact_number
from dashboard.facts.models import FactContext
from solar_data.timezones import ZURICH


class FactsEngineTest(unittest.TestCase):
    def context(self, hour=12, today=25, yesterday=20, power=1,
                sunrise_hour=6, sunset_hour=21, weather=None):
        now = datetime(2026, 7, 25, hour, tzinfo=ZURICH)
        sunrise = now.replace(hour=sunrise_hour) if sunrise_hour is not None else None
        sunset = now.replace(hour=sunset_hour) if sunset_hour is not None else None
        return FactContext(now, sunrise, sunset, today, yesterday, power, weather)

    def test_daily_phases_and_energy_periods(self):
        cases = (
            (self.context(5), ("pre_sunrise", "yesterday")),
            (self.context(7, today=0, power=0), ("morning_waiting", "yesterday")),
            (self.context(8, today=0, power=0), ("zero_production_day", None)),
            (self.context(8, today=.1, power=0), ("active_production", "today")),
            (self.context(8, today=0, power=.1), ("active_production", "today")),
            (self.context(22), ("after_sunset", "today")),
        )
        for context, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(determine_phase(context), expected)

    def test_missing_sun_uses_clock_fallback(self):
        self.assertEqual(determine_phase(self.context(5, sunrise_hour=None))[1], "yesterday")
        self.assertEqual(determine_phase(self.context(12, today=0, power=0,
                                                       sunrise_hour=None))[0], "morning_waiting")
        self.assertEqual(determine_phase(self.context(22, sunrise_hour=None))[0], "after_sunset")

    def test_selection_is_stable_and_period_word_is_correct(self):
        first = build_story_from_context(self.context())
        self.assertEqual(first, build_story_from_context(self.context()))
        self.assertIn("heutigen", first.line_1)
        self.assertIn("heutigen", FACTS_BY_ID["D01"].render(25, "heutigen")[0])
        self.assertIn("gestrigen", FACTS_BY_ID["D01"].render(25, "gestrigen")[0])

    def test_selection_does_not_use_changing_energy_in_seed(self):
        # These values have the same eligible set; only their rendered number differs.
        first = build_story_from_context(self.context(today=75))
        changed = build_story_from_context(self.context(today=76))
        self.assertEqual(first.fact_id, changed.fact_id)

    def test_selection_remains_stable_across_activation_thresholds(self):
        for before, after in ((.9, 1.1), (4.9, 5.1), (9.9, 10.1),
                              (24.9, 25.1), (49.9, 50.1), (99.9, 100.1),
                              (119.9, 120.1)):
            first = build_story_from_context(self.context(today=before))
            later = build_story_from_context(self.context(today=after))
            selected = FACTS_BY_ID.get(first.fact_id)
            if selected and selected.min_kwh <= after <= selected.max_kwh:
                with self.subTest(before=before, after=after, fact=first.fact_id):
                    self.assertEqual(first.fact_id, later.fact_id)

    def test_zero_energy_never_creates_conversion_fact(self):
        story = build_story_from_context(self.context(12, today=0, power=1))
        self.assertIn(story.fact_id, {"TECH"})

    def test_late_hours_do_not_repeat_one_exhausted_selection_forever(self):
        stories = [build_story_from_context(self.context(hour, today=75))
                   for hour in range(18, 24)]
        self.assertGreater(len({story.fact_id for story in stories}), 1)

    def test_adjacent_hours_avoid_same_fact_and_family(self):
        stories = [build_story_from_context(self.context(hour=h, today=75))
                   for h in range(6, 24)]
        for previous, current in zip(stories, stories[1:]):
            if previous.family != "technical" and current.family != "technical":
                self.assertNotEqual(previous.fact_id, current.fact_id)
                self.assertNotEqual(previous.family, current.family)

    def test_adjacent_hours_with_changing_ranges_use_alternatives(self):
        energies = (1, 5, 10, 25, 50, 75, 100, 121)
        stories = [build_story_from_context(self.context(hour=hour, today=energy))
                   for hour, energy in zip(range(8, 16), energies)]
        for previous, current in zip(stories, stories[1:]):
            if previous.family != "technical" and current.family != "technical":
                self.assertNotEqual(previous.fact_id, current.fact_id)

    def test_energy_ranges_filter_catalog(self):
        at_five = {fact.fact_id for fact in FACTS if fact.min_kwh <= 5 <= fact.max_kwh}
        self.assertIn("D01", at_five)
        self.assertNotIn("M01", at_five)
        self.assertNotIn("X01", at_five)
        at_150 = {fact.fact_id for fact in FACTS if fact.min_kwh <= 150 <= fact.max_kwh}
        self.assertIn("X01", at_150)
        self.assertNotIn("D01", at_150)

    def test_every_catalog_template_fits_at_representative_energies(self):
        for energy in (5, 25, 75, 150):
            for fact in FACTS:
                with self.subTest(energy=energy, fact=fact.fact_id):
                    self.assertIsNotNone(_render(fact, energy, "heutigen"))

    def test_swiss_number_formatting_and_large_numbers(self):
        expected = ((1.4, "1,4"), (4401, "4’400"), (125049, "125’000"),
                    (1_200_000, "1,2 Millionen"),
                    (1_100_000_000, "1,1 Milliarden"))
        for value, text in expected:
            with self.subTest(value=value):
                self.assertEqual(format_fact_number(value), text)

    def test_all_conversion_factors_at_75_kwh(self):
        expected = {
            "D01": ("4’400",), "D02": ("6’750", "km"),
            "K01": ("5’000",), "K03": ("15’000",), "K08": ("250",),
            "M01": ("600",), "M03": ("1,08 Milliarden",),
            "V01": ("10’700",), "V03": ("577",), "V07": ("2’500",),
            "V09": ("21",), "H01": ("204",), "U01": ("2,1 Jahre",),
            "U02": ("78 Tage",), "X01": ("0,27 Sekunden",),
            "X03": ("18 %",), "X11": ("3,94 Sekunden",),
            "CMB07": ("103 Jahre", "0,27 Sekunden"),
        }
        for fact_id, fragments in expected.items():
            text = " ".join(FACTS_BY_ID[fact_id].render(75, "heutigen"))
            with self.subTest(fact=fact_id):
                for fragment in fragments:
                    self.assertIn(fragment, text)

    def test_iphone_rounding_at_small_and_large_yields(self):
        for energy, expected in ((1, "59"), (5, "290"), (25, "1’500"),
                                 (75, "4’400"), (120, "7’100")):
            with self.subTest(energy=energy):
                self.assertIn(expected, " ".join(render_d01(energy, "heutigen")))

    def test_catalog_has_correct_grammar_and_no_punchlines(self):
        catalog_text = " ".join(" ".join(fact.render(75, "heutigen")) for fact in FACTS)
        for forbidden in ("Der heutigen Solarertrag", "Der gestrigen Solarertrag",
                          "Eiswürfeln", "Der Daumen hätte", "ausdauernde Party",
                          "Teilchenphysik spielt", "Saubere Wäsche", "Elektrisch unterwegs"):
            self.assertNotIn(forbidden, catalog_text)

    def test_long_dynamic_hamster_text_fits(self):
        lines, used_short = _render(FACTS_BY_ID["M03"], 150, "heutigen")
        self.assertFalse(used_short)
        self.assertEqual(lines, ("Für die heutigen 150 kWh wären rund",
                                 "2,16 Milliarden Hamsterrad-Runden nötig."))

    def test_cloud_status_does_not_claim_weather_causation(self):
        story = build_story_from_context(self.context(9, today=0, power=0, weather=3))
        self.assertEqual(story.fact_id, "ZERO_WEATHER")
        self.assertNotIn("wegen", story.line_1 + story.line_2)

    def test_fog_has_its_own_status(self):
        story = build_story_from_context(self.context(9, today=0, power=0, weather=45))
        self.assertIn("neblig", story.line_1)

    def test_zero_production_after_sunset_is_a_completed_day(self):
        context = self.context(22, today=0, power=0)
        self.assertEqual(determine_phase(context), ("after_sunset", "today"))
        story = build_story_from_context(context)
        self.assertEqual(story.fact_id, "ZERO_DAY_COMPLETE")
        text = story.line_1 + story.line_2
        self.assertNotIn("Morgen ist", text)
        self.assertNotIn("noch nicht angelaufen", text)


if __name__ == "__main__":
    unittest.main()
