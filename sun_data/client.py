"""Small, strictly validating Open-Meteo client using only the stdlib."""

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urlencode
from urllib.request import urlopen

from solar_data.timezones import ZURICH

API_URL = "https://api.open-meteo.com/v1/forecast"


class SunDataError(ValueError):
    """The configuration, transport, or response is not usable."""


@dataclass(frozen=True)
class SunData:
    local_date: date
    sunrise: datetime
    sunset: datetime
    sunshine_hours: float
    fetched_at: datetime
    weather_code: object = None
    weather_code_status: str = "missing"

    def to_dict(self):
        return {
            "local_date": self.local_date.isoformat(),
            "sunrise": self.sunrise.isoformat(),
            "sunset": self.sunset.isoformat(),
            "sunshine_hours": self.sunshine_hours,
            "fetched_at": self.fetched_at.astimezone(timezone.utc).isoformat(),
            "weather_code": self.weather_code,
            "weather_code_status": self.weather_code_status,
        }


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SunDataError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise SunDataError(f"{name} must be finite")
    return value


def _valid_weather_code(value):
    return (not isinstance(value, bool) and isinstance(value, int) and
            (0 <= value <= 3 or value in (45, 48) or 51 <= value <= 67 or
             71 <= value <= 77 or 80 <= value <= 82 or 85 <= value <= 86 or
             95 <= value <= 99))


def validate_coordinates(latitude, longitude):
    try:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError) as exc:
        raise SunDataError("latitude and longitude must be numeric") from exc
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise SunDataError("latitude and longitude must be finite")
    if not -90 <= latitude <= 90:
        raise SunDataError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise SunDataError("longitude must be between -180 and 180")
    return latitude, longitude


def _local_datetime(value, name):
    if not isinstance(value, str):
        raise SunDataError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SunDataError(f"{name} is not valid ISO-8601") from exc
    # Open-Meteo returns wall-clock values in the requested timezone.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZURICH)
    else:
        parsed = parsed.astimezone(ZURICH)
    return parsed


def parse_response(payload, fetched_at: datetime, expected_date: date) -> SunData:
    if not isinstance(payload, dict) or not isinstance(payload.get("daily"), dict):
        raise SunDataError("response.daily must be an object")
    daily = payload["daily"]
    values = {}
    for field in ("time", "sunrise", "sunset", "sunshine_duration"):
        item = daily.get(field)
        if not isinstance(item, list) or len(item) != 1:
            raise SunDataError(f"daily.{field} must contain exactly one value")
        values[field] = item[0]
    try:
        local_date = date.fromisoformat(values["time"])
    except (TypeError, ValueError) as exc:
        raise SunDataError("daily.time is not a valid date") from exc
    if local_date != expected_date:
        raise SunDataError("forecast does not belong to today's Zurich date")
    sunrise = _local_datetime(values["sunrise"], "sunrise")
    sunset = _local_datetime(values["sunset"], "sunset")
    if sunrise.date() != local_date or sunset.date() != local_date:
        raise SunDataError("sunrise and sunset must belong to the forecast date")
    duration = _finite_number(values["sunshine_duration"], "sunshine_duration")
    if duration < 0:
        raise SunDataError("sunshine_duration must not be negative")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise SunDataError("fetched_at must be timezone-aware")
    weather = daily.get("weather_code")
    if weather is None:
        weather_code, weather_status = None, "missing"
    elif (not isinstance(weather, list) or len(weather) != 1 or
          not _valid_weather_code(weather[0])):
        weather_code, weather_status = None, "invalid"
    else:
        weather_code, weather_status = weather[0], "valid"
    return SunData(local_date, sunrise, sunset, duration / 3600.0, fetched_at,
                   weather_code, weather_status)


def fetch_sun_data(latitude, longitude, now: Optional[datetime] = None,
                   timeout: float = 5.0, opener=urlopen) -> SunData:
    latitude, longitude = validate_coordinates(latitude, longitude)
    now = now or datetime.now(timezone.utc)
    expected_date = now.astimezone(ZURICH).date()
    query = urlencode({"latitude": latitude, "longitude": longitude,
                       "daily": "sunrise,sunset,sunshine_duration,weather_code",
                       "timezone": "Europe/Zurich", "forecast_days": 1})
    try:
        with opener(f"{API_URL}?{query}", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SunDataError(f"Open-Meteo request failed: {exc}") from exc
    return parse_response(payload, now, expected_date)
