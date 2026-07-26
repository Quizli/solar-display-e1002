import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dashboard.facts.history import (eligible_historical_candidates,
                                     render_historical)
from dashboard.facts.models import FactContext
from dashboard.live_view import build_story
from solar_data.storage import DailyYield, SolarDatabase, _utc_text
from solar_data.timezones import ZURICH


def context(hour=12, today=60, power=1, day=25):
    now = datetime(2026, 7, day, hour, tzinfo=ZURICH)
    return FactContext(now, now.replace(hour=6), now.replace(hour=21), today,
                       None, power, None)


class FakeDatabase:
    def __init__(self, values):
        self.values = values

    def daily_yield_from_aggregates(self, day):
        return DailyYield(day, self.values[day]) if day in self.values else None

    def completed_daily_yields(self, before, limit=None):
        rows = [DailyYield(day, value) for day, value in sorted(self.values.items())
                if day < before]
        return rows[-limit:] if limit else rows


class HistoricalFactsTest(unittest.TestCase):
    def ids(self, database, item):
        return {candidate.fact_id: candidate for candidate in
                eligible_historical_candidates(database, item)}

    def test_record_requires_two_days_and_preserves_prior_record(self):
        one = FakeDatabase({date(2026, 7, 24): 50})
        self.assertNotIn("HIST_RECORD", self.ids(one, context(today=60)))
        two = FakeDatabase({date(2026, 7, 23): 40, date(2026, 7, 24): 50})
        candidate = self.ids(two, context(today=50.2))["HIST_RECORD"]
        self.assertEqual(candidate.context["baseline_energy_kwh"], 50)
        self.assertNotIn("HIST_RECORD", self.ids(two, context(today=50.1)))

    def test_yesterday_higher_lower_and_neutral_wording(self):
        base = {date(2026, 7, 24): 50}
        for value, fragment in ((60, "mehr"), (40, "weniger"), (52, "fast gleich")):
            candidate = self.ids(FakeDatabase(base), context(hour=22, today=value))["HIST_YESTERDAY"]
            story = render_historical(candidate.fact_id, context(hour=22, today=value),
                                      candidate.context)
            self.assertIn(fragment, story.line_2)

    def test_morning_uses_yesterday_and_active_skips_yesterday_comparison(self):
        values = {date(2026, 7, 23): 40, date(2026, 7, 24): 60}
        morning = context(hour=5, today=0, power=0)
        candidate = self.ids(FakeDatabase(values), morning)["HIST_YESTERDAY"]
        story = render_historical(candidate.fact_id, morning, candidate.context)
        self.assertTrue(story.line_1.startswith("Gestern"))
        self.assertIn("Vortag", story.line_2)
        self.assertNotIn("HIST_YESTERDAY", self.ids(FakeDatabase(values), context()))

    def test_average_needs_three_and_uses_only_latest_seven_nonmissing_days(self):
        values = {date(2026, 7, day): float(day) for day in (10, 12, 14, 16, 18, 20, 22, 24)}
        candidate = self.ids(FakeDatabase(values), context(hour=22, today=30))["HIST_AVERAGE"]
        self.assertEqual(candidate.context["baseline_days"], 7)
        self.assertAlmostEqual(candidate.context["baseline_energy_kwh"],
                               sum((12, 14, 16, 18, 20, 22, 24)) / 7)
        too_few = FakeDatabase({date(2026, 7, 23): 20, date(2026, 7, 24): 30})
        self.assertNotIn("HIST_AVERAGE", self.ids(too_few, context(hour=22)))


class HistoricalStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "solar.db")
        self.db = SolarDatabase(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def aggregate(self, local, energy):
        utc = local.astimezone(timezone.utc)
        self.db.connection.execute(
            "INSERT INTO aggregates_5m VALUES (?,0,0,0,50,0,0,?,1,1,1)",
            (_utc_text(utc), energy))
        self.db.connection.commit()

    def test_daily_queries_use_latest_skip_gaps_and_exclude_current_day(self):
        self.aggregate(datetime(2026, 3, 28, 10, tzinfo=ZURICH), 5)
        self.aggregate(datetime(2026, 3, 28, 20, tzinfo=ZURICH), 12)
        self.aggregate(datetime(2026, 3, 30, 20, tzinfo=ZURICH), -4)
        self.aggregate(datetime(2026, 3, 31, 1, tzinfo=ZURICH), 99)
        rows = self.db.completed_daily_yields(date(2026, 3, 31))
        self.assertEqual([(item.local_day, item.energy_kwh) for item in rows],
                         [(date(2026, 3, 28), 12), (date(2026, 3, 30), 0)])

    def test_context_is_first_writer_wins_and_survives_restart(self):
        hour = datetime(2026, 7, 25, 12, tzinfo=ZURICH)
        first = json.dumps({"reference_day": "2026-07-24", "baseline_energy_kwh": 50})
        self.db.store_fact_selection("key", hour, "HIST_RECORD", "history", 60,
                                     context_json=first)
        self.db.store_fact_selection("key", hour, "HIST_RECORD", "history", 60,
                                     context_json="{}")
        self.db.close()
        self.db = SolarDatabase(self.path)
        self.assertEqual(self.db.get_fact_selection("key").context_json, first)
