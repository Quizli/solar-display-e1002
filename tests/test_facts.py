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
from dashboard.live_view import build_live_view
from dashboard.preview import replay, synthetic
from solar_data.storage import SolarDatabase, _utc_text
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
        ctx = replace(self.ctx, now_local=self.ctx.sunset, complete_yield=True)
        self.assertEqual(self.choose(ctx).fact_id, "ZERO_DAY_COMPLETE")

    def test_first_light_keeps_energy_slot_free_with_or_without_used_history(self):
        for used in (False, True):
            with SolarDatabase(":memory:") as db:
                history = (
                    history_candidates(
                        {self.now.date() - DAY: 2.0, self.now.date() - 2 * DAY: 1.0},
                        self.now.date(),
                    )
                    if used
                    else []
                )
                if used:
                    earlier = replace(
                        self.ctx,
                        now_local=self.now - timedelta(hours=1),
                        solar_power_kw=0.15,
                    )
                    self.assertEqual(
                        choose_insight(
                            db, earlier, history, {}, [], False, True
                        ).family,
                        "history",
                    )
                for energy in (0, 0.05, None):
                    ctx = replace(
                        self.ctx, today_energy_kwh=energy, solar_power_kw=0.15
                    )
                    story = choose_insight(db, ctx, history, {}, [], False, True)
                    self.assertEqual(story.fact_id, "TECH_CONTEXT")
                    self.assertTrue(story.line_1)
                    self.assertIsNone(db.get_fact_selection(hour_key(self.now)))
                active = replace(ctx, today_energy_kwh=0.2, selection_energy_kwh=0.12)
                story = choose_insight(db, active, history, {}, [], False, True)
                self.assertIn(story.fact_id, ENERGY)
                self.assertEqual(
                    db.get_fact_selection(hour_key(self.now)).fact_id, story.fact_id
                )

    def test_incomplete_evening_and_unavailable_yield_do_not_claim_zero(self):
        for energy in (0, 0.05, None):
            ctx = replace(self.ctx, now_local=self.ctx.sunset, today_energy_kwh=energy)
            self.assertEqual(self.choose(ctx).fact_id, "TECH_CONTEXT")
        ctx = replace(self.ctx, today_energy_kwh=None)
        self.assertNotIn(
            "WAITING_STATUS",
            {
                c.ident
                for c in morning_candidates(ctx, [], {}, "morning_waiting", False)
            },
        )

    def test_daylight_elapsed_minutes_and_dst(self):
        for start, end, expected in (
            (
                datetime(2026, 6, 1, 6, 24, tzinfo=ZURICH),
                datetime(2026, 6, 1, 21, 14, tzinfo=ZURICH),
                "14 Stunden 50 Minuten",
            ),
            (
                datetime(2026, 3, 29, 1, 59, tzinfo=ZURICH),
                datetime(2026, 3, 29, 4, 0, tzinfo=ZURICH),
                "1 Stunde 1 Minute",
            ),
            (
                datetime(2026, 10, 25, 2, 3, tzinfo=ZURICH, fold=0),
                datetime(2026, 10, 25, 2, 5, tzinfo=ZURICH, fold=1),
                "1 Stunde 2 Minuten",
            ),
        ):
            ctx = replace(
                self.ctx, now_local=start, sunrise=start, sunset=end, sunshine_hours=2
            )
            items = {
                c.ident: c
                for c in morning_candidates(ctx, [], {}, "morning_waiting", False)
            }
            self.assertEqual(items["DAYLIGHT"].values["duration"], expected)
            self.assertEqual(
                items["DAYLIGHT"].values["sunrise"], start.strftime("%H:%M")
            )
            self.assertEqual(items["DAYLIGHT"].values["sunset"], end.strftime("%H:%M"))
            self.assertEqual(items["SUNSHINE_OUTLOOK"].values["sunshine"], "2,0")

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

    def events(
        self, now, energy=0, total=None, history=None, reliable=True, verified=False
    ):
        history = (
            history
            if history is not None
            else {self.now.date() - i * DAY: 20 * i for i in (1, 2)}
        )
        ctx = replace(
            self.ctx, now_local=now, today_energy_kwh=energy, complete_yield=verified
        )
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

    def test_verified_correction_invalidation_and_recovery_across_restart(self):
        history = {self.now.date() - DAY: 100.04, self.now.date() - 2 * DAY: 98.0}
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "review.db")
            self.db = SolarDatabase(path)
            self.events(self.now, 99, history=history)
            peak = self.now + timedelta(hours=2)
            event = self.events(peak, 142, history=history)["DAILY_RECORD"]
            self.assertEqual(event.values["old_record"], "100,0")
            # A partial integration can neither change the record nor invalidate it.
            self.assertEqual(
                self.events(
                    peak + timedelta(minutes=1), 90, history=history, reliable=False
                )["DAILY_RECORD"].values["energy"],
                "142,0",
            )
            revised = self.events(
                peak + timedelta(minutes=2),
                125,
                history=history,
                reliable=False,
                verified=True,
            )
            self.assertEqual(revised["DAILY_RECORD"].values["energy"], "125,0")
            self.assertEqual(revised["PRODUCTION_BARRIER"].values["threshold"], 120)
            self.assertEqual(
                self.events(
                    peak + timedelta(minutes=3),
                    90,
                    history=history,
                    reliable=False,
                    verified=True,
                ),
                {},
            )
            self.db.close()
            with SolarDatabase(path) as self.db:
                self.assertEqual(
                    self.events(
                        peak + timedelta(minutes=5),
                        90,
                        history=history,
                        reliable=False,
                        verified=True,
                    ),
                    {},
                )
                # Frozen full-precision baseline: 100.13 is below 100.04 + 0.1.
                self.assertNotIn(
                    "DAILY_RECORD",
                    self.events(peak + timedelta(hours=1), 100.13, history=history),
                )
                resumed = self.events(
                    peak + timedelta(hours=3),
                    142.1,
                    history=history,
                    reliable=False,
                    verified=True,
                )
                self.assertEqual(resumed["DAILY_RECORD"].reference, event.reference)
                self.assertEqual(
                    resumed["PRODUCTION_BARRIER"].reference, "PRODUCTION_BARRIER:140"
                )
                self.assertEqual(
                    self.db.connection.execute(
                        "SELECT count(*) FROM milestone_events WHERE event_key='PRODUCTION_BARRIER:140'"
                    ).fetchone()[0],
                    1,
                )

    def test_publisher_reconciles_a_verified_counter_with_an_inflated_chart_bucket(
        self,
    ):
        midnight = self.now.replace(hour=0, minute=0)
        bucket = midnight - 2 * DAY
        peak = self.now + timedelta(hours=2)
        while bucket <= peak:
            value = 100 if bucket < midnight else 142 if bucket == peak else 0
            self.db.connection.execute(
                "INSERT INTO aggregates_5m VALUES (?,0,0,0,0,0,0,?,1,0,0)",
                (_utc_text(bucket), value),
            )
            bucket += timedelta(minutes=5)

        def publish(now, daily, total):
            sample, sun = synthetic(now.date(), now, "sunny-summer")
            self.db.store_snapshot(
                replace(sample, energy_today_kwh=daily, energy_total_kwh=total)
            )
            return build_live_view(self.db, now, sun_result=sun)

        publish(midnight - timedelta(minutes=5), 100, 1000)
        publish(self.now, 99, None)
        event = publish(peak, 142, None)
        self.assertEqual(event["story_status"]["fact_id"], "DAILY_RECORD")
        corrected = publish(peak + timedelta(minutes=5), 90, 1090)
        self.assertEqual(corrected["display"]["day_yield_kwh"], 90)
        self.assertIsNone(corrected["story_status"]["event_id"])
        self.assertEqual(
            self.db.aggregates_for_local_day(self.now.date())[-1].energy_today_kwh, 142
        )

    def test_publisher_requires_complete_zero_yield_after_sunset(self):
        evening = self.now.replace(hour=22)
        sample, sun = synthetic(evening.date(), evening, "zero-production")
        self.db.store_snapshot(replace(sample, energy_total_kwh=None))
        partial = build_live_view(self.db, evening, sun_result=sun)
        self.assertEqual(
            partial["daily_energy"], {"source": "solar_integration", "complete": False}
        )
        self.assertEqual(partial["story_status"]["fact_id"], "TECH_CONTEXT")
        rows = list(replay(evening.date(), evening.date(), "zero-production", None))
        self.assertEqual(rows[-1]["insight"], "ZERO_DAY_COMPLETE")

    def test_cold_start_replay_has_no_empty_first_light_slot(self):
        rows = list(replay(date(2026, 6, 2), date(2026, 6, 2), "cold-start", None))
        first_light = [
            r
            for r in rows
            if r["phase"] == "active_production" and "T05:" in r["timestamp"]
        ]
        self.assertTrue(all(r["statement"].strip() and r["insight"] for r in rows))
        self.assertTrue(all(r["history"] is None for r in rows))
        self.assertEqual(first_light[0]["yield_kwh"], 0)
        self.assertEqual(first_light[0]["insight"], "TECH_CONTEXT")
        self.assertTrue(
            any(
                r["yield_kwh"] == 0.05 and r["insight"] == "TECH_CONTEXT"
                for r in first_light
            )
        )
        self.assertIn(first_light[-1]["insight"], ENERGY)

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
