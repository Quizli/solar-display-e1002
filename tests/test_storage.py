import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from fronius.model import LiveData
from solar_data.storage import SolarDatabase, _utc_text, bucket_start
from solar_data.timezones import ZURICH


UTC = timezone.utc


def snapshot(timestamp, value=1.0, soc=50.0, energy=10.0, total=1000.0):
    return LiveData(
        timestamp=timestamp, solar_power_kw=value, house_power_kw=value + 1,
        heat_power_kw=value + 2, battery_soc_pct=soc,
        battery_power_kw=value + 3, grid_power_kw=value + 4,
        energy_today_kwh=energy, battery_available=True, heat_available=False,
        energy_total_kwh=total,
    )


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SolarDatabase(str(Path(self.temporary.name) / "solar.db"))

    def tearDown(self):
        self.database.close()
        self.temporary.cleanup()

    def test_store_and_read_latest_valid_snapshot_in_canonical_utc(self):
        item = snapshot("2026-07-25T00:07:46+02:00")
        self.assertTrue(self.database.store_snapshot(item))
        self.assertFalse(self.database.store_snapshot(item))
        latest = self.database.latest_snapshot()
        self.assertEqual(latest.timestamp, "2026-07-24T22:07:46.000000+00:00")
        latest_values = latest.to_dict()
        latest_values["timestamp"] = item.timestamp
        self.assertEqual(latest_values, item.to_dict())

    def test_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            self.database.store_snapshot(snapshot("2026-07-25T00:00:00"))

    def test_rejects_invalid_total_energy_at_storage_boundary(self):
        for total in (-1, float("nan"), float("inf")):
            with self.subTest(total=total), self.assertRaises(ValueError):
                self.database.store_snapshot(
                    snapshot("2026-07-25T00:00:00+00:00", total=total)
                )

    def test_completed_bucket_means_and_last_values_and_idempotency(self):
        self.database.store_snapshot(snapshot("2026-07-25T10:01:00+02:00", 1, 20, 5))
        self.database.store_snapshot(snapshot("2026-07-25T10:01:10+02:00", 3, 80, 9))
        now = datetime(2026, 7, 25, 8, 5, tzinfo=UTC)
        self.assertEqual(self.database.aggregate_completed(now), 1)
        self.assertEqual(self.database.aggregate_completed(now), 0)
        rows = self.database.aggregates_for_local_day(date(2026, 7, 25))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].solar_power_kw, 2)
        self.assertEqual(rows[0].house_power_kw, 3)
        self.assertEqual(rows[0].sample_count, 2)
        self.assertEqual(rows[0].battery_soc_pct, 80)
        self.assertEqual(rows[0].energy_today_kwh, 9)
        count = self.database.connection.execute("SELECT count(*) FROM aggregates_5m").fetchone()[0]
        self.assertEqual(count, 1)

    def test_late_sample_reaggregates_only_its_changed_bucket(self):
        now = datetime(2026, 7, 25, 8, 10, tzinfo=UTC)
        self.database.store_snapshot(snapshot("2026-07-25T10:01:00+02:00", value=1))
        self.database.store_snapshot(snapshot("2026-07-25T10:06:00+02:00", value=9))
        self.assertEqual(self.database.aggregate_completed(now), 2)

        self.database.store_snapshot(snapshot("2026-07-25T10:01:30+02:00", value=5))
        self.assertEqual(self.database.aggregate_completed(now), 1)
        rows = self.database.aggregates_for_local_day(date(2026, 7, 25))
        self.assertEqual(rows[0].sample_count, 2)
        self.assertEqual(rows[0].solar_power_kw, 3)
        self.assertEqual(rows[1].sample_count, 1)
        self.assertEqual(rows[1].solar_power_kw, 9)
        self.assertEqual(self.database.aggregate_completed(now), 0)

    def test_current_bucket_is_not_aggregated(self):
        self.database.store_snapshot(snapshot("2026-07-25T10:04:59+02:00"))
        self.assertEqual(
            self.database.aggregate_completed(datetime(2026, 7, 25, 8, 4, 59, tzinfo=UTC)), 0
        )

    def test_zurich_bucket_and_dst_fallback_are_timezone_aware(self):
        summer = datetime(2026, 7, 24, 22, 7, 46, tzinfo=UTC)
        self.assertEqual(bucket_start(summer).isoformat(), "2026-07-24T22:05:00+00:00")
        self.assertEqual(summer.astimezone(ZURICH).date(), date(2026, 7, 25))

        first_0230 = bucket_start(datetime(2026, 10, 25, 0, 32, tzinfo=UTC))
        second_0230 = bucket_start(datetime(2026, 10, 25, 1, 32, tzinfo=UTC))
        self.assertEqual(first_0230.astimezone(ZURICH).strftime("%H:%M"), "02:30")
        self.assertEqual(second_0230.astimezone(ZURICH).strftime("%H:%M"), "02:30")
        self.assertNotEqual(first_0230, second_0230)

    def test_retention_keeps_unaggregated_rows(self):
        self.database.store_snapshot(snapshot("2026-01-01T00:01:00+00:00"))
        now = datetime(2026, 1, 10, tzinfo=UTC)
        self.assertEqual(self.database.delete_expired_raw(7, now), 0)
        self.database.aggregate_completed(now)
        self.assertEqual(self.database.delete_expired_raw(7, now), 1)

    def test_retention_never_partially_deletes_a_bucket(self):
        for timestamp in ("2026-01-03T10:00:30+00:00", "2026-01-03T10:03:30+00:00"):
            self.database.store_snapshot(snapshot(timestamp))
        self.database.aggregate_completed(datetime(2026, 1, 3, 10, 5, tzinfo=UTC))

        # With zero retention, 10:02 lies inside the 10:00 bucket.
        cutoff_inside = datetime(2026, 1, 3, 10, 2, tzinfo=UTC)
        self.assertEqual(self.database.delete_expired_raw(0, cutoff_inside), 0)
        raw_count = self.database.connection.execute(
            "SELECT COUNT(*) FROM raw_samples"
        ).fetchone()[0]
        self.assertEqual(raw_count, 2)
        self.assertEqual(self.database.aggregate_completed(cutoff_inside), 0)

        aggregate_before = self.database.connection.execute(
            "SELECT * FROM aggregates_5m"
        ).fetchone()
        cutoff_after = datetime(2026, 1, 3, 10, 5, tzinfo=UTC)
        self.assertEqual(self.database.delete_expired_raw(0, cutoff_after), 2)
        self.assertEqual(self.database.aggregate_completed(cutoff_after), 0)
        aggregate_after = self.database.connection.execute(
            "SELECT * FROM aggregates_5m"
        ).fetchone()
        self.assertEqual(tuple(aggregate_after), tuple(aggregate_before))

    def test_local_day_query_excludes_adjacent_days_and_handles_dst_day(self):
        # Zurich fall-back day is 25 hours: both repeated 02:30 instants belong to it.
        for timestamp in (
            "2026-10-24T21:55:00+00:00",  # previous local day
            "2026-10-24T22:05:00+00:00",
            "2026-10-25T00:32:00+00:00",
            "2026-10-25T01:32:00+00:00",
            "2026-10-25T23:05:00+00:00",  # next local day
        ):
            self.database.store_snapshot(snapshot(timestamp))
        self.database.aggregate_completed(datetime(2026, 10, 26, 1, tzinfo=UTC))
        rows = self.database.aggregates_for_local_day(date(2026, 10, 25))
        self.assertEqual(len(rows), 3)

    def test_daily_energy_uses_counter_across_zurich_day_boundary(self):
        # Local 2026-07-25 begins at 22:00 UTC on the previous date.
        self.database.store_snapshot(snapshot("2026-07-24T21:59:00+00:00", total=100))
        self.database.store_snapshot(snapshot("2026-07-24T22:01:00+00:00", total=100.1))
        self.database.store_snapshot(snapshot("2026-07-25T21:59:00+00:00", total=112.5))
        self.database.store_snapshot(snapshot("2026-07-25T22:01:00+00:00", total=113))
        result = self.database.daily_energy(date(2026, 7, 25))
        self.assertAlmostEqual(result.energy_today_kwh, 12.5)
        self.assertEqual(result.source, "total_counter")
        self.assertTrue(result.complete)

    def test_missing_prior_baseline_returns_partial_counter_result(self):
        self.database.store_snapshot(snapshot("2026-07-25T08:00:00+00:00", total=105))
        self.database.store_snapshot(snapshot("2026-07-25T18:00:00+00:00", total=111))
        result = self.database.daily_energy(date(2026, 7, 25))
        self.assertEqual(result.energy_today_kwh, 6)
        self.assertEqual(result.source, "total_counter")
        self.assertFalse(result.complete)

    def test_counter_reset_falls_back_to_five_minute_power_integration(self):
        self.database.store_snapshot(snapshot("2026-07-24T21:59:00+00:00", total=100))
        self.database.store_snapshot(
            snapshot("2026-07-25T10:01:00+00:00", value=12, energy=0, total=2)
        )
        self.database.store_snapshot(
            snapshot("2026-07-25T10:06:00+00:00", value=12, energy=0, total=3)
        )
        self.database.aggregate_completed(datetime(2026, 7, 25, 10, 15, tzinfo=UTC))
        result = self.database.daily_energy(date(2026, 7, 25))
        self.assertAlmostEqual(result.energy_today_kwh, 2)
        self.assertEqual(result.source, "solar_integration")
        self.assertFalse(result.complete)

    def test_single_counter_without_baseline_uses_non_negative_power_fallback(self):
        self.database.store_snapshot(
            snapshot("2026-07-25T10:01:00+00:00", value=-3, energy=0, total=5)
        )
        self.database.aggregate_completed(datetime(2026, 7, 25, 10, 10, tzinfo=UTC))
        result = self.database.daily_energy(date(2026, 7, 25))
        self.assertEqual(result.energy_today_kwh, 0)
        self.assertEqual(result.source, "solar_integration")

    def test_counter_reset_falls_back_to_day_energy_for_daily_and_aggregates(self):
        day = date(2026, 7, 25)
        self.database.store_snapshot(
            snapshot("2026-07-24T21:59:00+00:00", energy=0, total=100)
        )
        self.database.store_snapshot(
            snapshot("2026-07-25T10:01:00+00:00", energy=4, total=2)
        )
        self.database.store_snapshot(
            snapshot("2026-07-25T10:06:00+00:00", energy=5, total=3)
        )

        self.database.aggregate_completed(datetime(2026, 7, 25, 10, 15, tzinfo=UTC))
        result = self.database.daily_energy(day)
        self.assertEqual(result.source, "day_energy")
        self.assertEqual(result.energy_today_kwh, 5)
        self.assertEqual(
            [row.energy_today_kwh for row in self.database.aggregates_for_local_day(day)],
            [4, 5],
        )

    def test_single_total_counter_value_does_not_block_day_energy(self):
        day = date(2026, 7, 25)
        self.database.store_snapshot(
            snapshot("2026-07-25T10:01:00+00:00", energy=4.25, total=5)
        )
        self.database.aggregate_completed(datetime(2026, 7, 25, 10, 5, tzinfo=UTC))

        result = self.database.daily_energy(day)
        self.assertEqual(result.source, "day_energy")
        self.assertEqual(result.energy_today_kwh, 4.25)
        self.assertEqual(
            self.database.aggregates_for_local_day(day)[0].energy_today_kwh, 4.25
        )

    def test_decreasing_day_energy_falls_back_to_power_integration(self):
        day = date(2026, 7, 25)
        self.database.store_snapshot(
            snapshot("2026-07-24T21:59:00+00:00", energy=0, total=100)
        )
        self.database.store_snapshot(
            snapshot("2026-07-25T10:01:00+00:00", value=6, energy=5, total=2)
        )
        self.database.store_snapshot(
            snapshot("2026-07-25T10:06:00+00:00", value=6, energy=4, total=3)
        )
        self.database.aggregate_completed(datetime(2026, 7, 25, 10, 15, tzinfo=UTC))

        result = self.database.daily_energy(day)
        self.assertEqual(result.source, "solar_integration")
        self.assertEqual(result.energy_today_kwh, 1)

    def test_aggregate_energy_uses_total_counter_and_is_cumulative(self):
        self.database.store_snapshot(
            snapshot("2026-07-24T21:59:00+00:00", energy=0, total=1940.916744)
        )
        for timestamp, total in (
            ("2026-07-25T08:01:00+00:00", 1950.0),
            ("2026-07-25T08:04:00+00:00", 1951.0),
            ("2026-07-25T08:06:00+00:00", 1997.0),
            ("2026-07-25T08:09:00+00:00", 1997.735259),
        ):
            self.database.store_snapshot(snapshot(timestamp, value=6, energy=0, total=total))

        self.assertEqual(
            self.database.aggregate_completed(datetime(2026, 7, 25, 8, 10, tzinfo=UTC)), 3
        )
        rows = self.database.aggregates_for_local_day(date(2026, 7, 25))
        values = [row.energy_today_kwh for row in rows]
        self.assertEqual(len(values), 2)
        self.assertAlmostEqual(values[-1], 56.818515)
        self.assertGreater(values[0], 0)
        self.assertEqual(values, sorted(values))

    def test_repair_existing_aggregates_is_idempotent_and_preserves_other_fields(self):
        self.database.store_snapshot(snapshot("2026-07-24T21:59:00+00:00", energy=0, total=100))
        self.database.store_snapshot(snapshot("2026-07-25T08:01:00+00:00", value=3,
                                              soc=72, energy=0, total=102))
        self.database.aggregate_completed(datetime(2026, 7, 25, 8, 5, tzinfo=UTC))
        day = date(2026, 7, 25)
        self.database.connection.execute(
            "UPDATE aggregates_5m SET energy_today_kwh = 0 WHERE bucket_start_utc >= ?",
            (_utc_text(datetime(2026, 7, 24, 22, tzinfo=UTC)),),
        )
        self.database.connection.commit()
        before = self.database.connection.execute(
            "SELECT * FROM aggregates_5m WHERE bucket_start_utc >= ?",
            (_utc_text(datetime(2026, 7, 24, 22, tzinfo=UTC)),),
        ).fetchone()

        self.assertEqual(self.database.repair_aggregate_daily_energy(day), 1)
        after = self.database.connection.execute(
            "SELECT * FROM aggregates_5m WHERE bucket_start_utc = ?",
            (before["bucket_start_utc"],),
        ).fetchone()
        self.assertEqual(after["energy_today_kwh"], 2)
        preserved = set(after.keys()) - {"energy_today_kwh"}
        for field in preserved:
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.database.repair_aggregate_daily_energy(day), 0)

    def test_positive_day_energy_is_used_when_total_counter_is_absent(self):
        self.database.store_snapshot(snapshot("2026-07-25T08:01:00+00:00",
                                              energy=4.25, total=None))
        self.database.aggregate_completed(datetime(2026, 7, 25, 8, 5, tzinfo=UTC))
        row = self.database.aggregates_for_local_day(date(2026, 7, 25))[0]
        self.assertEqual(row.energy_today_kwh, 4.25)
        self.assertEqual(self.database.daily_energy(date(2026, 7, 25)).source, "day_energy")

    def test_repaired_energy_obeys_dst_fallback_day_boundaries(self):
        self.database.store_snapshot(snapshot("2026-10-24T21:59:00+00:00", energy=0, total=10))
        self.database.store_snapshot(snapshot("2026-10-25T00:32:00+00:00", energy=0, total=11))
        self.database.store_snapshot(snapshot("2026-10-25T01:32:00+00:00", energy=0, total=12))
        self.database.aggregate_completed(datetime(2026, 10, 25, 2, tzinfo=UTC))
        rows = self.database.aggregates_for_local_day(date(2026, 10, 25))
        self.assertEqual([row.energy_today_kwh for row in rows], [1, 2])


if __name__ == "__main__":
    unittest.main()
