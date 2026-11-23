import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.facts import hour_key
from dashboard.facts.statistics import history_candidates
from dashboard.facts.models import FactContext
from dashboard.live_view import build_story
from solar_data.storage import SolarDatabase, _utc_text
from solar_data.timezones import ZURICH


def candidates_for(database, ctx):
    day = ctx.now_local.date()
    return history_candidates({r.local_day:r.energy_kwh for r in database.completed_record_daily_yields(day)},day)


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

    def yield_aggregate(self, local, energy):
        """Store one five-minute bucket whose integrated yield is ``energy``."""
        utc = local.astimezone(timezone.utc)
        self.db.connection.execute(
            "INSERT INTO aggregates_5m VALUES (?, ?,0,0,50,0,0,?,1,1,1)",
            (_utc_text(utc), energy * 12, energy))
        self.db.connection.commit()

    def complete_day(self, local_day, energy):
        start = datetime.combine(local_day, datetime.min.time(), tzinfo=ZURICH)
        end = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), tzinfo=ZURICH)
        instant = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        rows = []
        while instant < end_utc:
            elapsed = (instant - start.astimezone(timezone.utc)).total_seconds()
            duration = (end_utc - start.astimezone(timezone.utc)).total_seconds()
            value = energy * min(1, (elapsed + 300) / duration)
            rows.append((_utc_text(instant), 0, 0, 0, 50, 0, 0, value, 1, 1, 1))
            instant += timedelta(minutes=5)
        self.db.connection.executemany(
            "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.connection.commit()

    def test_single_bucket_is_not_a_complete_record_day(self):
        self.yield_aggregate(datetime(2026, 7, 24, 12, tzinfo=ZURICH), 40)
        self.assertIsNone(self.db.complete_daily_yield(date(2026, 7, 24)))

    def test_fragmented_day_is_not_a_complete_record_day(self):
        for hour in (0, 1, 12, 23):
            self.aggregate(datetime(2026, 7, 24, hour, tzinfo=ZURICH), hour)
        self.assertIsNone(self.db.complete_daily_yield(date(2026, 7, 24)))

    def test_every_second_bucket_is_only_half_coverage(self):
        local_day = date(2026, 7, 24)
        start = datetime.combine(local_day, datetime.min.time(), tzinfo=ZURICH)
        for index in range(0, 288, 2):
            self.aggregate(start + timedelta(minutes=5 * index), index)
        self.assertEqual(len(self.db.aggregates_for_local_day(local_day)), 144)
        self.assertIsNone(self.db.complete_daily_yield(local_day))

    def test_duplicate_rows_do_not_increase_coverage(self):
        local_day = date(2026, 7, 24)
        start, end = self.db._local_day_bounds(local_day)
        rows = []
        for index in range(0, 288, 2):
            row = (_utc_text(start + timedelta(minutes=5 * index)), float(index))
            rows.extend((row, row))
        self.assertEqual(len(rows), 288)
        self.assertEqual(len({row[0] for row in rows}), 144)
        self.assertIsNone(self.db._qualify_record_day(local_day, rows, start, end))

    def test_bucket_coverage_threshold_is_rounded_up(self):
        local_day = date(2026, 7, 24)
        start = datetime.combine(local_day, datetime.min.time(), tzinfo=ZURICH)
        minimum = math.ceil(288 * .95)
        # Spread missing buckets without violating the ten-minute gap rule.
        missing = set(range(9, 288, 20))
        for index in range(288):
            if index not in missing:
                self.aggregate(start + timedelta(minutes=5 * index), index)
        self.assertGreaterEqual(288 - len(missing), minimum)
        self.assertIsNotNone(self.db.complete_daily_yield(local_day))

        self.db.connection.execute("DELETE FROM aggregates_5m")
        self.db.connection.commit()
        keep = minimum - 1
        # Deterministically retain exactly one fewer than the minimum, including bounds.
        retained = {0, 287} | set(range(1, 287))
        for index in sorted(retained)[:keep - 1] + [287]:
            self.aggregate(start + timedelta(minutes=5 * index), index)
        self.assertEqual(len(self.db.aggregates_for_local_day(local_day)), keep)
        self.assertIsNone(self.db.complete_daily_yield(local_day))

    def test_record_history_query_count_is_constant_for_30_and_300_days(self):
        counts = []
        story_counts = []
        for days in (30, 300):
            self.db.connection.execute("DELETE FROM aggregates_5m")
            base = date(2025, 1, 1)
            rows = []
            for offset in range(days):
                local = datetime.combine(base + timedelta(days=offset),
                                         datetime.min.time(), tzinfo=ZURICH)
                rows.append((_utc_text(local), 0, 0, 0, 50, 0, 0,
                             float(offset), 1, 1, 1))
            self.db.connection.executemany(
                "INSERT INTO aggregates_5m VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.db.connection.commit()
            selects = []
            self.db.connection.set_trace_callback(
                lambda sql: selects.append(sql) if
                sql.lstrip().upper().startswith("SELECT") and
                "AGGREGATES_5M" in sql.upper() else None)
            self.db.completed_record_daily_yields(base + timedelta(days=days))
            self.db.connection.set_trace_callback(None)
            counts.append(len(selects))
            selects = []
            self.db.connection.set_trace_callback(
                lambda sql: selects.append(sql) if
                sql.lstrip().upper().startswith("SELECT") and
                "AGGREGATES_5M" in sql.upper() else None)
            now = datetime.combine(base + timedelta(days=days),
                                   datetime.min.time(), tzinfo=ZURICH).replace(hour=12)
            build_story(self.db, now, 10, 2)
            self.db.connection.set_trace_callback(None)
            story_counts.append(len(selects))
        self.assertEqual(counts, [1, 1])
        self.assertEqual(story_counts, [2, 2])

    def test_expected_bucket_minimum_respects_real_dst_day_length(self):
        expected = {}
        for local_day in (date(2026, 3, 29), date(2026, 7, 24),
                          date(2026, 10, 25)):
            start, end = self.db._local_day_bounds(local_day)
            buckets = int((end - start).total_seconds() // 300)
            expected[buckets] = math.ceil(buckets * .95)
        self.assertEqual(expected, {276: 263, 288: 274, 300: 285})

    def test_complete_normal_and_dst_days_are_accepted(self):
        for local_day in (date(2026, 7, 24), date(2026, 3, 29), date(2026, 10, 25)):
            with self.subTest(local_day=local_day):
                self.complete_day(local_day, 40)
                self.assertAlmostEqual(self.db.complete_daily_yield(local_day).energy_kwh, 40)

    def test_dst_days_can_be_average_comparison_days(self):
        cases = ((date(2026, 3, 27), date(2026, 3, 29)),
                 (date(2026, 10, 23), date(2026, 10, 25)))
        for first, comparison_day in cases:
            with self.subTest(comparison_day=comparison_day):
                self.db.connection.execute("DELETE FROM aggregates_5m")
                self.db.connection.commit()
                self.complete_day(first, 10)
                self.complete_day(first + timedelta(days=1), 20)
                self.complete_day(comparison_day, 30)
                now = datetime.combine(comparison_day + timedelta(days=1),
                                       datetime.min.time(), tzinfo=ZURICH).replace(hour=12)
                item = next(candidate for candidate in
                            candidates_for(
                                self.db, FactContext(now, None, None, 0, None, 0, None))
                            if candidate.ident == "HIST_AVERAGE")
                self.assertEqual(item.meta["reference_day"],
                                 comparison_day.isoformat())
                self.assertEqual(item.meta["baseline_days"], 2)
                self.assertAlmostEqual(item.meta["baseline_kwh"], 15)

    def test_average_skips_incomplete_days_and_allows_calendar_gaps(self):
        self.complete_day(date(2026, 7, 20), 10)
        self.complete_day(date(2026, 7, 22), 20)
        self.yield_aggregate(datetime(2026, 7, 23, 12, tzinfo=ZURICH), 99)
        self.complete_day(date(2026, 7, 24), 30)
        now = datetime(2026, 7, 25, 12, tzinfo=ZURICH)
        item = next(candidate for candidate in candidates_for(
            self.db, FactContext(now, None, None, 0, None, 0, None))
                    if candidate.ident == "HIST_AVERAGE")
        self.assertEqual(item.meta["reference_day"], "2026-07-24")
        self.assertEqual(item.meta["baseline_days"], 2)
        self.assertEqual(item.meta["baseline_kwh"], 15)

    def test_yesterday_record_gets_one_morning_slot(self):
        for local_day, energy in ((date(2026, 7, 22), 20),
                                  (date(2026, 7, 23), 30),
                                  (date(2026, 7, 24), 40)):
            self.complete_day(local_day, energy)
        build_story(self.db,datetime(2026,7,24,12,tzinfo=ZURICH),5,1)
        for hour, minute in ((0, 5), (1, 5), (8, 0), (23, 55)):
            story = build_story(self.db, datetime(2026, 7, 25, hour, minute,
                                                   tzinfo=ZURICH), 0, 0)
            self.assertEqual(story.fact_id=="RECORD_AFTERGLOW", hour==0)

    def test_incomplete_days_cannot_create_yesterday_record(self):
        for local_day, energy in ((date(2026, 7, 22), 20),
                                  (date(2026, 7, 23), 30),
                                  (date(2026, 7, 24), 40)):
            self.yield_aggregate(datetime.combine(local_day, datetime.min.time(),
                                                   tzinfo=ZURICH).replace(hour=12), energy)
        story = build_story(self.db, datetime(2026, 7, 25, 8, tzinfo=ZURICH), 0, 0)
        self.assertNotEqual(story.fact_id, "HIST_RECORD")

    def test_unqualified_current_day_cannot_set_record(self):
        self.complete_day(date(2026, 7, 23), 20)
        self.complete_day(date(2026, 7, 24), 30)
        morning = build_story(self.db, datetime(2026, 7, 25, 12, tzinfo=ZURICH),
                              30.2, 2)
        evening = build_story(self.db, datetime(2026, 7, 25, 23, 59,
                                                tzinfo=ZURICH), 42, 1)
        self.assertNotIn("DAILY_RECORD", (morning.fact_id, evening.fact_id))

    def test_past_yields_alone_do_not_pin_record(self):
        for local_day, energy in ((date(2026, 7, 22), 20),
                                  (date(2026, 7, 23), 30),
                                  (date(2026, 7, 24), 40)):
            self.complete_day(local_day, energy)
        current = build_story(self.db, datetime(2026, 7, 25, 14, tzinfo=ZURICH),
                              40.2, 2)
        expired = build_story(self.db, datetime(2026, 7, 26, 8, tzinfo=ZURICH),
                              0, 0)
        self.assertNotEqual(current.fact_id, "DAILY_RECORD")
        self.assertEqual(current.energy_period, "today")
        self.assertNotEqual(expired.fact_id, "HIST_RECORD")

    def test_record_result_survives_database_restart(self):
        for local_day, energy in ((date(2026, 7, 22), 20),
                                  (date(2026, 7, 23), 30),
                                  (date(2026, 7, 24), 40)):
            self.complete_day(local_day, energy)
        now = datetime(2026, 7, 25, 8, tzinfo=ZURICH)
        before = build_story(self.db, now, 0, 0)
        self.db.close()
        self.db = SolarDatabase(self.path)
        after = build_story(self.db, now, 0, 0)
        self.assertEqual((before.fact_id, before.line_1, before.line_2),
                         (after.fact_id, after.line_1, after.line_2))

    def test_historical_candidates_are_computed_once_per_story(self):
        self.complete_day(date(2026, 7, 23), 20)
        self.complete_day(date(2026, 7, 24), 30)
        from dashboard.facts.statistics import history_candidates
        with patch("dashboard.live_view.history_candidates",
                   wraps=history_candidates) as candidates:
            build_story(self.db, datetime(2026, 7, 25, 12, tzinfo=ZURICH),
                        10, 2)
        self.assertEqual(candidates.call_count, 1)

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

    def test_midnight_morning_omits_incomplete_yesterday(self):
        now = datetime(2026, 7, 26, 0, 45, tzinfo=ZURICH)
        self.yield_aggregate(datetime(2026, 7, 24, 23, 50, tzinfo=ZURICH), 40)
        self.yield_aggregate(datetime(2026, 7, 25, 23, 50, tzinfo=ZURICH), 60)

        story = build_story(self.db, now, 0, 0)

        self.assertEqual(now.date().isoformat(), "2026-07-26")
        self.assertIn(story.fact_id, {"NIGHT_STATUS","TECH_CONTEXT"})
        self.assertIsNone(story.energy_period)

    def test_incomplete_record_day_uses_first_energy_anchor(self):
        now = datetime(2026, 7, 26, 8, 45, tzinfo=ZURICH)
        self.complete_day(date(2026, 7, 24), 20)
        self.complete_day(date(2026, 7, 25), 30)
        anchor = now.replace(minute=35).astimezone(timezone.utc)
        self.db.connection.execute(
            "INSERT INTO aggregates_5m VALUES (?,1,0,0,50,0,0,34,1,1,1)",
            (_utc_text(anchor),))
        self.db.connection.commit()

        story = build_story(self.db, now, 35, 1)

        self.assertNotEqual(story.fact_id, "DAILY_RECORD")
        self.assertEqual(story.energy_period, "today")
        self.assertEqual(story.selection_energy_kwh, 34)
        self.assertEqual(self.db.get_fact_selection(
            hour_key(now.replace(minute=0))).selection_energy_kwh, 34)
