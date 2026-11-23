import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dashboard.facts.calendar_rules import calendar_candidates, easter
from dashboard.facts.editorial import (
    ALIASES,
    ENERGY,
    HISTORY,
    duration,
    energy_candidate,
    number,
    render,
    wrap,
)
from dashboard.facts.engine import hour_key
from dashboard.facts.milestones import active_events
from dashboard.facts.models import FactContext
from dashboard.facts.mornings import morning_candidates
from dashboard.facts.selection import choose_insight, phase_for
from dashboard.facts.statistics import history_candidates
from dashboard.preview import replay, synthetic
from solar_data.storage import SolarDatabase
from solar_data.timezones import ZURICH

DAY = timedelta(days=1)


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 25, 8, 5, tzinfo=ZURICH)
        self.ctx = FactContext(
            self.now,
            self.now.replace(hour=6),
            self.now.replace(hour=20),
            0,
            None,
            0,
            None,
        )
        self.db = SolarDatabase(":memory:")
        self.addCleanup(self.db.close)

    def choose(self, ctx):
        return choose_insight(self.db, ctx, [], {}, [], False, True)

    def test_phase_transition_creates_two_stable_slots(self):
        morning = self.choose(self.ctx)
        active = replace(self.ctx, today_energy_kwh=0.5, solar_power_kw=0.1)
        first = self.choose(active)
        later = self.choose(replace(active, today_energy_kwh=5))
        self.assertEqual(morning.family, "morning")
        self.assertIn(first.fact_id, ENERGY)
        self.assertEqual((first.fact_id, first.variant), (later.fact_id, later.variant))
        self.assertNotEqual((first.line_1, first.line_2), (later.line_1, later.line_2))
        self.assertEqual(len(self.db.fact_selections_for_local_day(self.now.date())), 2)

    def test_late_waiting_and_zero(self):
        for minutes, phase in (
            (-1, "pre_sunrise"),
            (89, "morning_waiting"),
            (90, "zero_production_day"),
        ):
            ctx = replace(
                self.ctx, now_local=self.ctx.sunrise + timedelta(minutes=minutes)
            )
            self.assertEqual(phase_for(ctx), phase)
        ctx = replace(self.ctx, now_local=self.ctx.sunset)
        self.assertEqual(self.choose(ctx).fact_id, "ZERO_DAY_COMPLETE")

    def test_alias_and_retired_cutover(self):
        for ident in ("N01", "M03"):
            now = self.now if ident == "N01" else self.now + DAY
            key = hour_key(now)
            self.db.store_fact_selection(key, now, ident, "old", 30)
            ctx = replace(self.ctx, now_local=now, today_energy_kwh=30)
            selected = self.choose(ctx)
            self.assertIn(selected.fact_id, ENERGY)
            if ident == "N01":
                self.assertEqual(selected.fact_id, ALIASES[ident])
            self.assertEqual(self.db.get_fact_selection(key).fact_id, ident)

    def test_recent_24_and_previous_family(self):
        stories = [
            self.choose(
                replace(
                    self.ctx,
                    now_local=self.now + timedelta(hours=h),
                    today_energy_kwh=25,
                    selection_energy_kwh=25,
                )
            )
            for h in range(36)
        ]
        for i, story in enumerate(stories):
            self.assertNotIn(
                story.fact_id, [s.fact_id for s in stories[max(0, i - 24) : i]]
            )
            if i:
                self.assertNotEqual(story.family, stories[i - 1].family)

    def test_dates_and_fold(self):
        for year, expected in ((2026, date(2026, 4, 5)), (2027, date(2027, 3, 28))):
            self.assertEqual(easter(year), expected)
            ids = lambda day: {c.ident for c in calendar_candidates(day, True)}
            self.assertIn("GOOD_FRIDAY", ids(expected - 2 * DAY))
            self.assertIn("ASCENSION", ids(expected + 39 * DAY))
        for day, ident in (
            (date(2026, 3, 29), "DST_START"),
            (date(2027, 10, 31), "DST_END"),
            (date(2026, 9, 23), "AUTUMN_EQUINOX"),
            (date(2027, 12, 22), "WINTER_SOLSTICE"),
        ):
            self.assertIn(ident, ids(day))
            self.assertNotIn(ident, ids(day - DAY))
        fold = datetime(2026, 10, 25, 2, tzinfo=ZURICH)
        self.assertNotEqual(hour_key(fold), hour_key(fold.replace(fold=1)))

    def test_forecast_and_daylight_are_distinct(self):
        ctx = replace(
            self.ctx,
            sunshine_hours=1.5,
            weather_code=45,
            previous_daylight_seconds=14 * 3600 + 180,
        )
        candidates = {
            c.ident: c
            for c in morning_candidates(ctx, [], {}, "morning_waiting", False)
        }
        self.assertEqual(candidates["DAYLIGHT"].values["duration"], "14 Stunden")
        self.assertEqual(
            candidates["DAYLIGHT_CHANGE"].values["delta_minutes"], "3 Minuten"
        )
        self.assertEqual(candidates["WEATHER_OUTLOOK"].branch, "fog")
        self.assertEqual(candidates["SUNSHINE_OUTLOOK"].branch, "very_low")

    def test_formatting_and_cutoffs(self):
        self.assertEqual(number(1234.25, 2), "1’234,25")
        self.assertEqual(duration(0.003), "3 Millisekunden")
        self.assertEqual(duration(86400), "1 Tag")
        self.assertEqual(duration(2 * 365 * 86400), "2 Jahre")
        for energy in (None, 0, -1, float("nan"), float("inf")):
            self.assertIsNone(energy_candidate(ENERGY["phone_charge"], energy, energy))
        self.assertEqual(wrap("Ein Satz.", 490), ("Ein Satz.", ""))
        self.assertIsNone(wrap("M" * 46, 510))
        c = energy_candidate(ENERGY["washing_machine"], 0.5, 0.5)
        self.assertIn("1 Waschgang", " ".join(render(c, "slot", 0)[1]))

    def test_history_handlers_and_denominators(self):
        day = date(2026, 7, 25)
        data = {day - i * DAY: float((i * 17) % 130) for i in range(1, 65)}
        seen = set()
        for recent in (
            [115, 123, 125, 106, 93, 28, 51],
            [9, 110, 111, 112, 2, 1, 3],
            [0, 0, 10, 12, 16, 18, 19],
        ):
            for i, v in enumerate(recent, 1):
                data[day - i * DAY] = v
            for c in history_candidates(data, day):
                seen.add(c.ident)
                self.assertIsNotNone(render(c, "slot"), c.ident)
                if c.ident == "HIST_7DAY_TOTAL":
                    self.assertEqual(c.values["energy"], number(sum(recent)))
                if c.ident == "HIST_YESTERDAY" and recent[1] == 0:
                    self.assertEqual(c.meta["direction"], "neutral")
                    self.assertIsNone(c.meta["difference_percent"])
        self.assertEqual(seen, set(HISTORY))
        del data[day - DAY]
        ids = {c.ident for c in history_candidates(data, day)}
        self.assertFalse(
            ids & {"HIST_YESTERDAY", "HIST_STREAK_END", "HIST_MONTH_TO_DATE"}
        )

    def test_calendar_quota(self):
        now = datetime(2026, 8, 1, 4, tzinfo=ZURICH)
        selected = [
            self.choose(
                replace(
                    self.ctx,
                    now_local=now + timedelta(hours=h),
                    sunrise=None,
                    sunset=None,
                    today_energy_kwh=0 if h < 4 else 20,
                )
            ).fact_id
            for h in range(18)
        ]
        self.assertEqual(selected.count("NATIONAL_DAY"), 2)

    def events(self, now, energy=0, total=None, history=None, reliable=True):
        history = (
            history
            if history is not None
            else {self.now.date() - i * DAY: 20 * i for i in (1, 2)}
        )
        ctx = replace(self.ctx, now_local=now, today_energy_kwh=energy)
        sample, _ = synthetic(now.date(), now, "sunny-summer")
        return {
            c.ident: c
            for c in active_events(
                self.db,
                ctx,
                history,
                True,
                replace(sample, energy_total_kwh=total),
                reliable,
            )[0]
        }

    def test_running_record_and_barrier(self):
        self.assertFalse(self.events(self.now, 30))
        self.assertFalse(self.events(self.now, 150, reliable=False))
        active = self.events(self.now + timedelta(minutes=35), 41)
        self.assertEqual(active["DAILY_RECORD"].values["old_record"], "40,0")
        later = self.events(self.now + timedelta(hours=5), 121)
        self.assertEqual(later["DAILY_RECORD"].values["energy"], "121,0")
        self.assertEqual(later["PRODUCTION_BARRIER"].values["threshold"], 120)
        self.assertFalse(self.events(self.now + DAY, 0))

    def test_lifetime_real_crossing_gap_and_reset(self):
        with patch.dict("os.environ", SOLAR_COMMISSIONING_TOTAL_KWH="500"):
            self.assertFalse(self.events(self.now, total=2499.9))
            now = self.now + timedelta(seconds=30)
            self.assertIn("LIFETIME_ENERGY", self.events(now, total=2500))
            self.assertEqual(
                self.events(now, total=2500)["LIFETIME_ENERGY"].reference,
                "LIFETIME_ENERGY:2000",
            )
            self.assertFalse(self.events(now + 8 * DAY, total=5510))
            self.assertFalse(self.events(now + 9 * DAY, total=500))
            self.assertFalse(self.events(now + 10 * DAY, total=10500))

    def test_completed_day_milestones_use_only_yesterday(self):
        today = self.now.date()
        history = {today - i * DAY: 110 for i in range(1, 11)}
        self.assertFalse(self.events(self.now - DAY, history={}))
        active = self.events(self.now, history=history)
        self.assertEqual(active["HIGH_YIELD_COUNT"].values["count"], 10)
        self.assertEqual(active["STREAK_RECORD"].values["days"], 10)
        self.assertNotIn("HIGH_YIELD_STREAK", active)
        self.assertFalse(self.events(self.now + DAY, history=history))

    def test_streak_threshold_after_a_longer_run(self):
        day = self.now.date()
        history = {day - i * DAY: 110 for i in (1, 2, 3, 9, 10, 11, 12)}
        self.events(self.now - DAY, history={})
        active = self.events(self.now, history=history)
        self.assertEqual(active["HIGH_YIELD_STREAK"].branch, "d3")
        self.assertNotIn("STREAK_RECORD", active)

    def test_lifetime_first_valid_sample_and_impossible_jump(self):
        with patch.dict("os.environ", SOLAR_COMMISSIONING_TOTAL_KWH="100"):
            self.events(self.now, total=None)
            self.assertFalse(self.events(self.now + timedelta(seconds=30), total=1099))
            with self.assertLogs("dashboard.facts.milestones", level="WARNING"):
                self.assertFalse(
                    self.events(self.now + timedelta(seconds=31), total=5100)
                )
            self.assertFalse(self.events(self.now + DAY, total=10100))

    def test_lifetime_first_reading_across_midnight_is_silent(self):
        with patch.dict("os.environ", SOLAR_COMMISSIONING_TOTAL_KWH="0"):
            before = self.now.replace(hour=23, minute=59, second=55)
            self.events(before, total=999.99)
            self.assertFalse(self.events(before + timedelta(seconds=15), total=1000.01))

    def test_leap_anniversary(self):
        with patch.dict("os.environ", SOLAR_COMMISSIONED_DATE="2024-02-29"):
            now = datetime(2025, 2, 28, 12, tzinfo=ZURICH)
            self.assertEqual(self.events(now)["ANNIVERSARY"].branch, "first")

    def test_replay_read_only_and_no_future_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.db"
            with SolarDatabase(str(path)) as db:
                sample, _ = synthetic(self.now.date(), self.now, "sunny-summer")
                db.store_snapshot(sample)
            before = path.read_bytes()
            slots = list(replay(self.now.date(), self.now.date(), None, path))
            self.assertEqual(slots[7]["insight"], "MISSING")
            self.assertEqual(slots[9]["insight"], "STALE")
            self.assertEqual(before, path.read_bytes())
