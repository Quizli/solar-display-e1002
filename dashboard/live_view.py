from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from solar_data.storage import POWER_FIELDS, SolarDatabase, _aware_utc
from solar_data.timezones import ZURICH

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")


def _weighted(rows, field):
    total = sum(row.sample_count for row in rows)
    return sum(getattr(row, field) * row.sample_count for row in rows) / total


def _self_consumption(rows):
    produced = 0.0
    consumed = 0.0
    hours = 5.0 / 60.0
    for row in rows:
        production = max(0.0, row.solar_power_kw)
        export = max(0.0, row.grid_power_kw)
        produced += production * hours
        consumed += min(production, max(0.0, production - export)) * hours
    if produced <= 1e-9:
        return None
    return consumed / produced * 100.0


def build_live_view(database: SolarDatabase, now: Optional[datetime] = None,
                    stale_seconds: float = 180.0) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    snapshot = database.latest_snapshot()
    rows = database.latest_aggregates(2)
    timestamp = _aware_utc(snapshot.timestamp) if snapshot else None
    if timestamp is None and rows:
        timestamp = _aware_utc(rows[-1].bucket_start)
    if timestamp is None:
        freshness = "missing"
    elif (now.astimezone(timezone.utc) - timestamp).total_seconds() > stale_seconds:
        freshness = "stale"
    else:
        freshness = "fresh"

    local_timestamp = timestamp.astimezone(ZURICH) if timestamp else None
    local_day = local_timestamp.date() if local_timestamp else now.astimezone(ZURICH).date()
    daily = database.daily_energy(local_day) if timestamp else None
    if rows:
        power = {field: _weighted(rows, field) for field in POWER_FIELDS}
        power_source = "aggregates_5m"
    elif snapshot:
        power = {field: getattr(snapshot, field) for field in POWER_FIELDS}
        power_source = "raw_snapshot"
    else:
        power = {field: None for field in POWER_FIELDS}
        power_source = "missing"

    if freshness == "stale":
        story_1 = "Datenstand {} Uhr".format(local_timestamp.strftime("%H:%M"))
        story_2 = "Aktualisierung der Solardaten prüfen"
    elif freshness == "fresh":
        story_1 = "Live-Daten der Solaranlage"
        story_2 = "Wetter, Charts und weitere Fakten folgen."
    else:
        story_1 = "Keine Solardaten verfügbar"
        story_2 = "Datenerfassung prüfen"

    display = dict(power)
    display.update({
        "battery_percent": snapshot.battery_soc_pct if snapshot and snapshot.battery_available else None,
        "battery_available": bool(snapshot and snapshot.battery_available),
        "current_time": local_timestamp.strftime("%H:%M Uhr") if local_timestamp else "—",
        "date_text": ("{}, {}. {} {}".format(WEEKDAYS[local_timestamp.weekday()], local_timestamp.day,
                                              MONTHS[local_timestamp.month], local_timestamp.year)
                      if local_timestamp else "—"),
        "sun_hours": None, "sunrise": None, "sunset": None, "co2_savings_kg": None,
        "story_line_1": story_1, "story_line_2": story_2,
        "day_yield_kwh": daily.energy_today_kwh if daily else None,
        "self_consumption_percent": _self_consumption(database.aggregates_for_local_day(local_day)),
    })
    return {
        "freshness": freshness,
        "timestamp_utc": timestamp.isoformat() if timestamp else None,
        "power_source": power_source,
        "power_bucket_count": len(rows),
        "daily_energy": ({"source": daily.source, "complete": daily.complete} if daily else None),
        "display": display,
    }
