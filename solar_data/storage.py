import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from fronius.model import LiveData


ZURICH = ZoneInfo("Europe/Zurich")
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
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
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
                """
            )

    def store_snapshot(self, snapshot: LiveData) -> bool:
        timestamp = _aware_utc(snapshot.timestamp)
        values = [getattr(snapshot, field) for field in POWER_FIELDS]
        values += [snapshot.battery_soc_pct, snapshot.energy_today_kwh]
        if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ValueError("snapshot contains an invalid numeric value")
        with self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO raw_samples VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            battery_available=bool(row["battery_available"]),
            heat_available=bool(row["heat_available"]),
        )

    def aggregate_completed(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current_bucket = _utc_text(bucket_start(now))
        buckets = self.connection.execute(
            """SELECT DISTINCT bucket_start_utc FROM raw_samples
               WHERE bucket_start_utc < ? ORDER BY bucket_start_utc""",
            (current_bucket,),
        ).fetchall()
        with self.connection:
            for item in buckets:
                key = item[0]
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
        return len(buckets)

    def delete_expired_raw(self, retention_days: float = 7,
                           now: Optional[datetime] = None) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must not be negative")
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        cutoff = _utc_text(now - timedelta(days=retention_days))
        with self.connection:
            cursor = self.connection.execute(
                """DELETE FROM raw_samples
                   WHERE timestamp_utc < ? AND EXISTS (
                       SELECT 1 FROM aggregates_5m AS aggregate
                       WHERE aggregate.bucket_start_utc = raw_samples.bucket_start_utc
                   )""",
                (cutoff,),
            )
        return cursor.rowcount

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
