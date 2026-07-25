from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

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


def _select_power_rows(rows, reference):
    """Select only recent, completed and contiguous UTC aggregate buckets."""
    if not rows:
        return [], "no_aggregates"
    newest = rows[-1]
    newest_start = _aware_utc(newest.bucket_start)
    newest_end = newest_start + timedelta(minutes=5)
    age = reference - newest_end
    if age.total_seconds() < 0:
        return [], "newest_bucket_not_completed"
    if age > timedelta(minutes=10):
        return [], "newest_bucket_too_old"
    selected = [newest]
    if len(rows) > 1:
        previous = rows[-2]
        previous_start = _aware_utc(previous.bucket_start)
        if previous_start + timedelta(minutes=5) == newest_start:
            selected.insert(0, previous)
            return selected, "two_recent_contiguous_aggregates"
        return selected, "newest_recent_aggregate_after_gap"
    return selected, "one_recent_aggregate"


def build_live_view(database: SolarDatabase, now: Optional[datetime] = None,
                    stale_seconds: float = 180.0) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    snapshot = database.latest_snapshot()
    snapshot_timestamp = _aware_utc(snapshot.timestamp) if snapshot else None
    reference = snapshot_timestamp or now_utc
    candidate_rows = database.latest_aggregates(2)
    rows, selection_reason = _select_power_rows(candidate_rows, reference)

    if snapshot_timestamp is None:
        freshness = "missing"
    elif (now_utc - snapshot_timestamp).total_seconds() > stale_seconds:
        freshness = "stale"
    else:
        freshness = "fresh"

    if rows:
        power = {field: _weighted(rows, field) for field in POWER_FIELDS}
        power_source = "aggregates_5m"
        power_timestamp = _aware_utc(rows[-1].bucket_start) + timedelta(minutes=5)
    elif snapshot:
        power = {field: getattr(snapshot, field) for field in POWER_FIELDS}
        power_source = "raw_snapshot"
        power_timestamp = snapshot_timestamp
        selection_reason += "_raw_snapshot_fallback"
    else:
        power = {field: None for field in POWER_FIELDS}
        power_source = "missing"
        power_timestamp = None

    local_timestamp = power_timestamp.astimezone(ZURICH) if power_timestamp else None
    local_day = local_timestamp.date() if local_timestamp else now.astimezone(ZURICH).date()
    daily = database.daily_energy(local_day) if power_timestamp else None

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
        "latest_snapshot_timestamp": snapshot_timestamp.isoformat() if snapshot_timestamp else None,
        "power_timestamp": power_timestamp.isoformat() if power_timestamp else None,
        "power_source": power_source,
        "power_selection_reason": selection_reason,
        "power_bucket_count": len(rows),
        "daily_energy": ({"source": daily.source, "complete": daily.complete} if daily else None),
        "display": display,
    }
