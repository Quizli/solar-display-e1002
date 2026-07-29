import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fronius.model import LiveData
from .timezones import ZURICH


UTC = timezone.utc
POWER_FIELDS = (
    "solar_power_kw",
    "house_power_kw",
    "heat_power_kw",
    "battery_power_kw",
    "grid_power_kw",
)


@dataclass(frozen=True)
class Aggregate5m:
    bucket_start: str
    solar_power_kw: float
    house_power_kw: float
    heat_power_kw: float
    battery_soc_pct: float
    battery_power_kw: float
    grid_power_kw: float
    energy_today_kwh: float
    sample_count: int
    battery_available: bool
    heat_available: bool


@dataclass(frozen=True)
class DailyEnergy:
    energy_today_kwh: float
    source: str
    complete: bool


@dataclass(frozen=True)
class DailyYield:
    local_day: date
    energy_kwh: float


@dataclass(frozen=True)
class FactSelection:
    hour_key: str
    local_day: str
    local_hour: str
    dst_fold: int
    fact_id: str
    family: str
    selection_energy_kwh: Optional[float]
    selection_bucket_start: Optional[str]
    created_at_utc: str
    context_json: Optional[str] = None


def _aware_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be a valid ISO-8601 value") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def bucket_start(value: datetime) -> datetime:
    """Return the UTC instant for the containing Zurich-local five-minute bucket."""
    local = value.astimezone(ZURICH)
    local_start = local.replace(minute=(local.minute // 5) * 5, second=0, microsecond=0)
    return local_start.astimezone(UTC)


class SolarDatabase:
    def __init__(self, path: str, read_only: bool = False) -> None:
        self.path = path
        if read_only and path == ":memory:":
            raise ValueError("an in-memory database cannot be opened read-only")
        if path != ":memory:" and not read_only:
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        if read_only:
            uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        else:
            self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        if not read_only:
            self.initialize()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );
                INSERT OR IGNORE INTO schema_version(version) VALUES (1);
                CREATE TABLE IF NOT EXISTS raw_samples (
                    timestamp_utc TEXT PRIMARY KEY,
                    bucket_start_utc TEXT NOT NULL,
                    solar_power_kw REAL NOT NULL,
                    house_power_kw REAL NOT NULL,
                    heat_power_kw REAL NOT NULL,
                    battery_soc_pct REAL NOT NULL,
                    battery_power_kw REAL NOT NULL,
                    grid_power_kw REAL NOT NULL,
                    energy_today_kwh REAL NOT NULL,
                    energy_total_kwh REAL,
                    battery_available INTEGER NOT NULL CHECK (battery_available IN (0, 1)),
                    heat_available INTEGER NOT NULL CHECK (heat_available IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS raw_samples_bucket_idx
                    ON raw_samples(bucket_start_utc);
                CREATE TABLE IF NOT EXISTS aggregates_5m (
                    bucket_start_utc TEXT PRIMARY KEY,
                    solar_power_kw REAL NOT NULL,
                    house_power_kw REAL NOT NULL,
                    heat_power_kw REAL NOT NULL,
                    battery_soc_pct REAL NOT NULL,
                    battery_power_kw REAL NOT NULL,
                    grid_power_kw REAL NOT NULL,
                    energy_today_kwh REAL NOT NULL,
                    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
                    battery_available INTEGER NOT NULL CHECK (battery_available IN (0, 1)),
                    heat_available INTEGER NOT NULL CHECK (heat_available IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS fact_history (
                    hour_key TEXT PRIMARY KEY,
                    local_day TEXT NOT NULL,
                    local_hour TEXT NOT NULL,
                    dst_fold INTEGER NOT NULL CHECK (dst_fold IN (0, 1)),
                    fact_id TEXT NOT NULL,
                    family TEXT NOT NULL,
                    selection_energy_kwh REAL,
                    selection_bucket_start TEXT,
                    created_at_utc TEXT NOT NULL,
                    context_json TEXT
                );
                """
            )
            columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(raw_samples)")
            }
            if "energy_total_kwh" not in columns:
                self.connection.execute(
                    "ALTER TABLE raw_samples ADD COLUMN energy_total_kwh REAL"
                )
            history_columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(fact_history)")
            }
            if "context_json" not in history_columns:
                self.connection.execute(
                    "ALTER TABLE fact_history ADD COLUMN context_json TEXT"
                )
            self.connection.execute("DELETE FROM schema_version")
            self.connection.execute("INSERT INTO schema_version(version) VALUES (4)")

    @staticmethod
    def _fact_selection(row) -> Optional[FactSelection]:
        return FactSelection(**dict(row)) if row is not None else None

    def get_fact_selection(self, hour_key: str) -> Optional[FactSelection]:
        row = self.connection.execute(
            "SELECT * FROM fact_history WHERE hour_key = ?", (hour_key,)
        ).fetchone()
        return self._fact_selection(row)

    def store_fact_selection(self, hour_key: str, local_hour: datetime,
                             fact_id: str, family: str,
                             selection_energy_kwh: Optional[float],
                             selection_bucket_start: Optional[str] = None,
                             created_at: Optional[datetime] = None,
                             context_json: Optional[str] = None) -> FactSelection:
        if local_hour.tzinfo is None or local_hour.utcoffset() is None:
            raise ValueError("local_hour must be timezone-aware")
        local_hour = local_hour.astimezone(ZURICH).replace(minute=0, second=0,
                                                           microsecond=0)
        created_at = created_at or datetime.now(UTC)
        if selection_bucket_start is not None:
            selection_bucket_start = _utc_text(_aware_utc(selection_bucket_start))
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO fact_history (
                       hour_key, local_day, local_hour, dst_fold, fact_id, family,
                       selection_energy_kwh, selection_bucket_start, created_at_utc,
                       context_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (hour_key, local_hour.date().isoformat(), local_hour.isoformat(),
                 local_hour.fold, fact_id, family, selection_energy_kwh,
                 selection_bucket_start, _utc_text(created_at), context_json),
            )
        return self.get_fact_selection(hour_key)

    def fact_selections_for_local_day(self, local_day: date) -> List[FactSelection]:
        rows = self.connection.execute(
            "SELECT * FROM fact_history WHERE local_day = ? ORDER BY local_hour, dst_fold",
            (local_day.isoformat(),),
        ).fetchall()
        return [self._fact_selection(row) for row in rows]

    def latest_fact_selection_before(self, local_hour: datetime) -> Optional[FactSelection]:
        if local_hour.tzinfo is None or local_hour.utcoffset() is None:
            raise ValueError("local_hour must be timezone-aware")
        target = local_hour.astimezone(UTC)
        rows = self.connection.execute("SELECT * FROM fact_history").fetchall()
        earlier = [row for row in rows
                   if datetime.fromisoformat(row["local_hour"]).astimezone(UTC) < target]
        if not earlier:
            return None
        row = max(earlier, key=lambda item:
                  datetime.fromisoformat(item["local_hour"]).astimezone(UTC))
        return self._fact_selection(row)

    def recent_catalog_fact_selections(self, fact_ids, limit: int = 12):
        """Return latest actually persisted catalogue selections, newest first."""
        if limit <= 0 or not fact_ids:
            return []
        placeholders = ",".join("?" for _ in fact_ids)
        rows = self.connection.execute(
            f"""SELECT * FROM fact_history WHERE fact_id IN ({placeholders})
                ORDER BY created_at_utc DESC, local_hour DESC, dst_fold DESC LIMIT ?""",
            (*fact_ids, limit),
        ).fetchall()
        return [self._fact_selection(row) for row in rows]

    def store_snapshot(self, snapshot: LiveData) -> bool:
        timestamp = _aware_utc(snapshot.timestamp)
        values = [getattr(snapshot, field) for field in POWER_FIELDS]
        values += [snapshot.battery_soc_pct, snapshot.energy_today_kwh]
        if snapshot.energy_total_kwh is not None:
            if snapshot.energy_total_kwh < 0:
                raise ValueError("energy_total_kwh must not be negative")
            values.append(snapshot.energy_total_kwh)
        if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ValueError("snapshot contains an invalid numeric value")
        with self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO raw_samples (
                       timestamp_utc, bucket_start_utc, solar_power_kw,
                       house_power_kw, heat_power_kw, battery_soc_pct,
                       battery_power_kw, grid_power_kw, energy_today_kwh,
                       energy_total_kwh, battery_available, heat_available
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _utc_text(timestamp),
                    _utc_text(bucket_start(timestamp)),
                    snapshot.solar_power_kw,
                    snapshot.house_power_kw,
                    snapshot.heat_power_kw,
                    snapshot.battery_soc_pct,
                    snapshot.battery_power_kw,
                    snapshot.grid_power_kw,
                    snapshot.energy_today_kwh,
                    snapshot.energy_total_kwh,
                    int(snapshot.battery_available),
                    int(snapshot.heat_available),
                ),
            )
        return cursor.rowcount == 1

    def latest_snapshot(self) -> Optional[LiveData]:
        row = self.connection.execute(
            "SELECT * FROM raw_samples ORDER BY timestamp_utc DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return LiveData(
            timestamp=row["timestamp_utc"],
            **{field: row[field] for field in POWER_FIELDS},
            battery_soc_pct=row["battery_soc_pct"],
            energy_today_kwh=row["energy_today_kwh"],
            energy_total_kwh=row["energy_total_kwh"],
            battery_available=bool(row["battery_available"]),
            heat_available=bool(row["heat_available"]),
        )

    def aggregate_completed(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current_bucket = _utc_text(bucket_start(now))
        buckets = self.connection.execute(
            """SELECT raw.bucket_start_utc
               FROM raw_samples AS raw
               LEFT JOIN aggregates_5m AS aggregate
                 ON aggregate.bucket_start_utc = raw.bucket_start_utc
               WHERE raw.bucket_start_utc < ?
               GROUP BY raw.bucket_start_utc, aggregate.sample_count
               HAVING aggregate.sample_count IS NULL
                  OR COUNT(*) <> aggregate.sample_count
               ORDER BY raw.bucket_start_utc""",
            (current_bucket,),
        ).fetchall()
        affected_days = set()
        with self.connection:
            for item in buckets:
                key = item[0]
                affected_days.add(_aware_utc(key).astimezone(ZURICH).date())
                rows = self.connection.execute(
                    """SELECT * FROM raw_samples WHERE bucket_start_utc = ?
                       ORDER BY timestamp_utc""",
                    (key,),
                ).fetchall()
                last = rows[-1]
                means = {
                    field: sum(row[field] for row in rows) / len(rows)
                    for field in POWER_FIELDS
                }
                self.connection.execute(
                    """INSERT INTO aggregates_5m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(bucket_start_utc) DO UPDATE SET
                       solar_power_kw=excluded.solar_power_kw,
                       house_power_kw=excluded.house_power_kw,
                       heat_power_kw=excluded.heat_power_kw,
                       battery_soc_pct=excluded.battery_soc_pct,
                       battery_power_kw=excluded.battery_power_kw,
                       grid_power_kw=excluded.grid_power_kw,
                       energy_today_kwh=excluded.energy_today_kwh,
                       sample_count=excluded.sample_count,
                       battery_available=excluded.battery_available,
                       heat_available=excluded.heat_available""",
                    (key, means["solar_power_kw"], means["house_power_kw"],
                     means["heat_power_kw"], last["battery_soc_pct"],
                     means["battery_power_kw"], means["grid_power_kw"],
                     last["energy_today_kwh"], len(rows),
                     last["battery_available"], last["heat_available"]),
                )
            for local_day in affected_days:
                self.repair_aggregate_daily_energy(local_day)
        return len(buckets)

    def delete_expired_raw(self, retention_days: float = 7,
                           now: Optional[datetime] = None) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must not be negative")
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        cutoff_bucket = _utc_text(bucket_start(now - timedelta(days=retention_days)))
        with self.connection:
            cursor = self.connection.execute(
                """DELETE FROM raw_samples
                   WHERE bucket_start_utc < ? AND EXISTS (
                       SELECT 1 FROM aggregates_5m AS aggregate
                       WHERE aggregate.bucket_start_utc = raw_samples.bucket_start_utc
                   )""",
                (cutoff_bucket,),
            )
        return cursor.rowcount

    def latest_aggregates(self, limit: int = 2) -> List[Aggregate5m]:
        """Return the newest persisted, completed five-minute aggregates oldest first."""
        if limit <= 0:
            return []
        rows = self.connection.execute(
            "SELECT * FROM aggregates_5m ORDER BY bucket_start_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Aggregate5m(
            bucket_start=row["bucket_start_utc"],
            **{field: row[field] for field in POWER_FIELDS},
            battery_soc_pct=row["battery_soc_pct"],
            energy_today_kwh=row["energy_today_kwh"],
            sample_count=row["sample_count"],
            battery_available=bool(row["battery_available"]),
            heat_available=bool(row["heat_available"]),
        ) for row in reversed(rows)]

    def aggregates_for_local_day(self, local_day: date) -> List[Aggregate5m]:
        start = datetime.combine(local_day, datetime.min.time(), tzinfo=ZURICH)
        end = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), tzinfo=ZURICH)
        rows = self.connection.execute(
            """SELECT * FROM aggregates_5m
               WHERE bucket_start_utc >= ? AND bucket_start_utc < ?
               ORDER BY bucket_start_utc""",
            (_utc_text(start), _utc_text(end)),
        ).fetchall()
        return [Aggregate5m(
            bucket_start=row["bucket_start_utc"],
            **{field: row[field] for field in POWER_FIELDS},
            battery_soc_pct=row["battery_soc_pct"],
            energy_today_kwh=row["energy_today_kwh"],
            sample_count=row["sample_count"],
            battery_available=bool(row["battery_available"]),
            heat_available=bool(row["heat_available"]),
        ) for row in rows]

    def daily_yield_from_aggregates(self, local_day: date) -> Optional[DailyYield]:
        """Return the final cumulative aggregate for a Zurich-local day."""
        start, end = self._local_day_bounds(local_day)
        row = self.connection.execute(
            """SELECT energy_today_kwh FROM aggregates_5m
               WHERE bucket_start_utc >= ? AND bucket_start_utc < ?
               ORDER BY bucket_start_utc DESC LIMIT 1""",
            (_utc_text(start), _utc_text(end)),
        ).fetchone()
        if row is None:
            return None
        value = row[0]
        if not math.isfinite(value):
            return None
        return DailyYield(local_day, max(0.0, value))

    def completed_daily_yields(self, before_local_day: date,
                               limit: Optional[int] = None) -> List[DailyYield]:
        """Return completed aggregate-backed days chronologically, skipping gaps."""
        if limit is not None and limit <= 0:
            return []
        before, _ = self._local_day_bounds(before_local_day)
        sql = """SELECT bucket_start_utc, energy_today_kwh FROM aggregates_5m
                 WHERE bucket_start_utc < ? ORDER BY bucket_start_utc DESC"""
        rows = self.connection.execute(sql, (_utc_text(before),)).fetchall()
        found = {}
        for row in rows:
            day = _aware_utc(row[0]).astimezone(ZURICH).date()
            if day >= before_local_day or day in found:
                continue
            value = row[1]
            if math.isfinite(value):
                found[day] = DailyYield(day, max(0.0, value))
                if limit is not None and len(found) >= limit:
                    break
        return [found[day] for day in sorted(found)]

    @staticmethod
    def _local_day_bounds(local_day: date):
        local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=ZURICH)
        local_end = datetime.combine(
            local_day + timedelta(days=1), datetime.min.time(), tzinfo=ZURICH
        )
        return local_start.astimezone(UTC), local_end.astimezone(UTC)

    def _daily_energy_through(self, local_day: date,
                              timestamp_utc: datetime) -> DailyEnergy:
        """Derive cumulative local-day energy through an aware UTC instant."""
        if timestamp_utc.tzinfo is None or timestamp_utc.utcoffset() is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        local_start, local_end = self._local_day_bounds(local_day)
        cutoff = min(max(timestamp_utc.astimezone(UTC), local_start), local_end)
        start, end = _utc_text(local_start), _utc_text(cutoff)
        last_before = self.connection.execute(
            """SELECT energy_total_kwh FROM raw_samples
               WHERE timestamp_utc < ? AND energy_total_kwh IS NOT NULL
               ORDER BY timestamp_utc DESC LIMIT 1""",
            (start,),
        ).fetchone()
        day_values = self.connection.execute(
            """SELECT energy_total_kwh FROM raw_samples
               WHERE timestamp_utc >= ? AND timestamp_utc < ?
                 AND energy_total_kwh IS NOT NULL
               ORDER BY timestamp_utc""",
            (start, end),
        ).fetchall()

        valid_day_values = [row[0] for row in day_values
                            if math.isfinite(row[0]) and row[0] >= 0]
        valid_baseline = (last_before[0] if last_before and
                          math.isfinite(last_before[0]) and last_before[0] >= 0 else None)
        zero_counter_result = None
        if valid_day_values:
            baseline = valid_baseline if valid_baseline is not None else valid_day_values[0]
            complete = valid_baseline is not None
            counter_values = ([baseline] if complete else []) + valid_day_values
            monotonic = all(
                current >= previous
                for previous, current in zip(counter_values, counter_values[1:])
            )
            difference = valid_day_values[-1] - baseline
            enough_values = complete or len(valid_day_values) >= 2
            if monotonic and difference > 0 and enough_values:
                return DailyEnergy(difference, "total_counter", complete)
            if monotonic and difference == 0 and enough_values:
                zero_counter_result = DailyEnergy(0.0, "total_counter", complete)

        direct_values = self.connection.execute(
            """SELECT energy_today_kwh FROM raw_samples
               WHERE timestamp_utc >= ? AND timestamp_utc < ?
                 AND energy_today_kwh > 0
               ORDER BY timestamp_utc""",
            (start, end),
        ).fetchall()
        direct = [row[0] for row in direct_values
                  if math.isfinite(row[0]) and row[0] > 0]
        if direct and all(current >= previous
                          for previous, current in zip(direct, direct[1:])):
            return DailyEnergy(direct[-1], "day_energy", True)
        if zero_counter_result is not None:
            return zero_counter_result

        # Counter reset, no usable baseline, or no counter: integrate each
        # persisted bucket over its actual overlap with this UTC day interval.
        energy = 0.0
        for aggregate in self.aggregates_for_local_day(local_day):
            bucket = _aware_utc(aggregate.bucket_start)
            if bucket >= cutoff:
                break
            overlap_start = max(bucket, local_start)
            overlap_end = min(bucket + timedelta(minutes=5), cutoff)
            hours = max(0.0, (overlap_end - overlap_start).total_seconds() / 3600.0)
            energy += max(0.0, aggregate.solar_power_kw) * hours
        return DailyEnergy(max(0.0, energy), "solar_integration", False)

    def daily_energy(self, local_day: date) -> DailyEnergy:
        """Derive a Zurich-local day's yield from the shared cumulative model."""
        _, local_end = self._local_day_bounds(local_day)
        return self._daily_energy_through(local_day, local_end)

    def repair_aggregate_daily_energy(self, local_day: date) -> int:
        """Idempotently repair cumulative energy only for a day's aggregates."""
        rows = self.aggregates_for_local_day(local_day)
        changed = 0
        previous_energy = 0.0
        with self.connection:
            for aggregate in rows:
                bucket_end = _aware_utc(aggregate.bucket_start) + timedelta(minutes=5)
                derived = self._daily_energy_through(local_day, bucket_end).energy_today_kwh
                energy = max(previous_energy, derived)
                previous_energy = energy
                if not math.isclose(aggregate.energy_today_kwh, energy,
                                    rel_tol=1e-12, abs_tol=1e-9):
                    self.connection.execute(
                        """UPDATE aggregates_5m SET energy_today_kwh = ?
                           WHERE bucket_start_utc = ?""",
                        (energy, aggregate.bucket_start),
                    )
                    changed += 1
        return changed
