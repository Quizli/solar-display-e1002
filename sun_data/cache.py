"""Atomic daily cache and resilient Open-Meteo refresh policy."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from solar_data.timezones import ZURICH
from .client import SunData, SunDataError, _local_datetime, _finite_number, fetch_sun_data


@dataclass(frozen=True)
class SunDataResult:
    data: object
    status: str
    source: str
    error: object = None


def _read_cache(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SunDataError("cache must be an object")
    local_date = datetime.strptime(payload["local_date"], "%Y-%m-%d").date()
    fetched_at = datetime.fromisoformat(payload["fetched_at"])
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise SunDataError("cached fetched_at must be timezone-aware")
    hours = _finite_number(payload["sunshine_hours"], "sunshine_hours")
    if hours < 0:
        raise SunDataError("cached sunshine_hours must not be negative")
    sunrise = _local_datetime(payload["sunrise"], "sunrise")
    sunset = _local_datetime(payload["sunset"], "sunset")
    if sunrise.date() != local_date or sunset.date() != local_date:
        raise SunDataError("cached dates do not match")
    return SunData(local_date, sunrise, sunset, hours, fetched_at)


def _write_cache(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         prefix=".sun-data-", suffix=".json.tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data.to_dict(), handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        _read_cache(temporary)
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def get_sun_data(latitude, longitude, cache_path="data/sun-data.json", now=None,
                 refresh_seconds=1800, max_age_seconds=21600, fetcher=fetch_sun_data):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZURICH).date()
    cached = None
    cache_invalid = False
    try:
        cached = _read_cache(cache_path)
    except FileNotFoundError:
        pass
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        cache_invalid = True
    age = (now - cached.fetched_at.astimezone(timezone.utc)).total_seconds() if cached else None
    valid_day = bool(cached and cached.local_date == today and age >= 0)
    if valid_day and age <= refresh_seconds:
        return SunDataResult(cached, "fresh", "cache")
    try:
        fresh = fetcher(latitude, longitude, now=now)
        _write_cache(cache_path, fresh)
        return SunDataResult(fresh, "fresh", "open-meteo")
    except Exception as exc:  # External service failure must never stop publishing.
        if valid_day and age <= max_age_seconds:
            return SunDataResult(cached, "cached", "cache", str(exc))
        return SunDataResult(None, "invalid" if cache_invalid else "missing", "none", str(exc))
