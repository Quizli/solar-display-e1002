import unittest
from datetime import datetime, timedelta

from dashboard.facts.catalog import FACTS
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
        yesterday = build_story_from_context(self.context(5))
        self.assertIn("gestrigen", yesterday.line_1)

    def test_zero_energy_never_creates_conversion_fact(self):
        story = build_story_from_context(self.context(12, today=0, power=1))
        self.assertIn(story.fact_id, {"TECH"})

    def test_plan_has_no_repeated_fact_or_adjacent_family(self):
        stories = [build_story_from_context(self.context(hour)) for hour in range(6, 18)]
        self.assertEqual(len({story.fact_id for story in stories}), len(stories))
        for previous, current in zip(stories, stories[1:]):
            self.assertNotEqual(previous.family, current.family)

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

    def test_cloud_status_does_not_claim_weather_causation(self):
        story = build_story_from_context(self.context(9, today=0, power=0, weather=3))
        self.assertEqual(story.fact_id, "ZERO_WEATHER")
        self.assertNotIn("wegen", story.line_1 + story.line_2)


if __name__ == "__main__":
    unittest.main()
