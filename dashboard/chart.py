"""Generate the paths for the frozen dashboard's 24-hour chart."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from typing import Iterable, Optional

from solar_data.storage import Aggregate5m, _aware_utc
from solar_data.timezones import ZURICH


PLOT_LEFT = 280.0
PLOT_RIGHT = 756.0
PLOT_TOP = 88.0
PLOT_BOTTOM = 276.0
POWER_MAX_KW = 24.0
BATTERY_MAX_PCT = 100.0
GRID_IMPORT_PLOT_THRESHOLD_KW = 0.5


@dataclass(frozen=True)
class ChartPaths:
    solar_area: str
    solar_line: str
    house_line: str
    grid_import_line: str
    battery_line: str
    battery_available: bool
    solar_points: int
    house_points: int
    grid_import_points: int
    battery_points: int
    latest_timestamp: Optional[str]


def x_for_local_time(value: datetime) -> float:
    """Map Zurich wall-clock time to the unchanged 00:00--24:00 plot."""
    local = value.astimezone(ZURICH)
    minutes = local.hour * 60 + local.minute + local.second / 60.0
    return PLOT_LEFT + (minutes / (24.0 * 60.0)) * (PLOT_RIGHT - PLOT_LEFT)


def power_y(value: float) -> float:
    clipped = min(POWER_MAX_KW, max(0.0, value))
    return PLOT_BOTTOM - clipped / POWER_MAX_KW * (PLOT_BOTTOM - PLOT_TOP)


def battery_y(value: float) -> float:
    clipped = min(BATTERY_MAX_PCT, max(0.0, value))
    return PLOT_BOTTOM - clipped / BATTERY_MAX_PCT * (PLOT_BOTTOM - PLOT_TOP)


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _segments(points):
    result = []
    current = []
    previous_bucket = None
    for bucket, x, y in points:
        if previous_bucket is None or bucket - previous_bucket == 5:
            current.append((x, y))
        else:
            if current:
                result.append(current)
            current = [(x, y)]
        previous_bucket = bucket
    if current:
        result.append(current)
    return result


def _line_path(segments) -> str:
    return " ".join(
        "M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in segment)
        for segment in segments
    )


def _area_path(segments) -> str:
    paths = []
    for segment in segments:
        first_x, _ = segment[0]
        last_x, _ = segment[-1]
        upper = " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in segment)
        paths.append(
            f"M {_fmt(first_x)} {_fmt(PLOT_BOTTOM)} L {upper} "
            f"L {_fmt(last_x)} {_fmt(PLOT_BOTTOM)} Z"
        )
    return " ".join(paths)


def build_chart(rows: Iterable[Aggregate5m], local_day: date,
                now: Optional[datetime] = None) -> ChartPaths:
    """Build paths without interpolating missing or not-yet-observed buckets."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    solar = []
    house = []
    grid_import = []
    battery = []
    used_timestamps = []
    seen_wall_buckets = set()
    for row in rows:
        bucket = _aware_utc(row.bucket_start)
        local = bucket.astimezone(ZURICH)
        wall_bucket = (local.hour, local.minute)
        if local.date() != local_day or bucket > now_utc or wall_bucket in seen_wall_buckets:
            continue
        seen_wall_buckets.add(wall_bucket)
        wall_minutes = local.hour * 60 + local.minute
        x = x_for_local_time(bucket)
        if math.isfinite(row.solar_power_kw):
            solar.append((wall_minutes, x, power_y(row.solar_power_kw)))
        if math.isfinite(row.house_power_kw):
            house.append((wall_minutes, x, power_y(row.house_power_kw)))
        # Internal grid flow is negative for import and positive for export.
        # Omit export and negligible import to avoid red points/line fragments.
        if (math.isfinite(row.grid_power_kw) and
                row.grid_power_kw <= -GRID_IMPORT_PLOT_THRESHOLD_KW):
            grid_import.append((wall_minutes, x, power_y(abs(row.grid_power_kw))))
        if row.battery_available and math.isfinite(row.battery_soc_pct):
            battery.append((wall_minutes, x, battery_y(row.battery_soc_pct)))
        used_timestamps.append(bucket)

    solar_segments = _segments(solar)
    return ChartPaths(
        solar_area=_area_path(solar_segments),
        solar_line=_line_path(solar_segments),
        house_line=_line_path(_segments(house)),
        grid_import_line=_line_path(_segments(grid_import)),
        battery_line=_line_path(_segments(battery)),
        battery_available=bool(battery),
        solar_points=len(solar),
        house_points=len(house),
        grid_import_points=len(grid_import),
        battery_points=len(battery),
        latest_timestamp=max(used_timestamps).isoformat() if used_timestamps else None,
    )
