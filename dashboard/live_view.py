from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Dict, Optional

from solar_data.storage import POWER_FIELDS, SolarDatabase, _aware_utc
from solar_data.timezones import ZURICH
from .chart import build_chart
from .weather import weather_icon_variant
from .facts import FactContext, Story, hour_key
from .facts.milestones import active_events
from .facts.selection import choose_history, choose_insight
from .facts.statistics import history_candidates

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")
LOGGER = logging.getLogger(__name__)
_SNAPSHOT_UNSET = object()


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


CO2_BASIS = "Swiss average consumer electricity mix"


def validate_co2_factor(value):
    if isinstance(value, bool):
        raise ValueError("CO2 factor must be a finite non-negative number")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("CO2 factor must be a finite non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("CO2 factor must be a finite non-negative number")
    return value


def build_fact_context(
    database,
    now,
    today_energy_kwh,
    solar_power_kw,
    sun=None,
    values=None,
    aggregates=None,
):
    """Collect the shared inputs for facts and historical comparisons."""
    now_local = now.astimezone(ZURICH)
    if values is None:
        values = {
            item.local_day: item.energy_kwh
            for item in database.completed_record_daily_yields(now_local.date())
        }
    yesterday_energy = values.get(now_local.date() - timedelta(days=1))
    if aggregates is None:
        aggregates = database.aggregates_for_local_day(now_local.date())
    anchors = {}
    anchor_buckets = {}
    for aggregate in aggregates:
        if (
            aggregate.energy_today_kwh < 0.1
            or _aware_utc(aggregate.bucket_start) + timedelta(minutes=5) > now
        ):
            continue
        bucket_local = _aware_utc(aggregate.bucket_start).astimezone(ZURICH)
        key = hour_key(bucket_local)
        if key not in anchors:
            anchors[key] = aggregate.energy_today_kwh
            anchor_buckets[key] = aggregate.bucket_start
    current_hour = now_local.replace(minute=0, second=0, microsecond=0)
    previous_hour = (
        current_hour.astimezone(timezone.utc) - timedelta(hours=1)
    ).astimezone(ZURICH)
    return FactContext(
        now_local=now_local,
        sunrise=sun.sunrise if sun else None,
        sunset=sun.sunset if sun else None,
        today_energy_kwh=today_energy_kwh,
        yesterday_energy_kwh=yesterday_energy,
        solar_power_kw=solar_power_kw,
        weather_code=sun.weather_code if sun else None,
        selection_energy_kwh=anchors.get(hour_key(current_hour)),
        previous_hour_selection_energy_kwh=anchors.get(hour_key(previous_hour)),
        sunshine_hours=getattr(sun, "sunshine_hours", None),
        previous_daylight_seconds=getattr(sun, "previous_daylight_seconds", None),
        has_buckets=bool(aggregates),
        selection_energy_by_hour=anchors,
        selection_bucket_by_hour=anchor_buckets,
    )


def build_story(
    database,
    now,
    today_energy_kwh,
    solar_power_kw,
    sun=None,
    persist_selection=True,
    context=None,
    historical_candidates=None,
):
    day = now.astimezone(ZURICH).date()
    values = {
        r.local_day: r.energy_kwh for r in database.completed_record_daily_yields(day)
    }
    context = context or build_fact_context(
        database, now, today_energy_kwh, solar_power_kw, sun, values
    )
    history = history_candidates(values, day)
    persist_selection = persist_selection and not database.read_only
    events, after = active_events(database, context, values, persist_selection)
    return choose_insight(
        database, context, history, values, events, after, persist_selection
    )


def build_live_view(
    database: SolarDatabase,
    now: Optional[datetime] = None,
    stale_seconds: float = 180.0,
    sun_result=None,
    co2_factor: float = 0.128,
    persist_fact_selection: bool = True,
    snapshot=_SNAPSHOT_UNSET,
    commissioning=None,
) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    if snapshot is _SNAPSHOT_UNSET:
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
    chart_local_day = now_utc.astimezone(ZURICH).date()
    day_aggregates = database.aggregates_for_local_day(chart_local_day)
    chart = build_chart(day_aggregates, chart_local_day, now_utc)

    factor = validate_co2_factor(co2_factor)
    day_yield = daily.energy_today_kwh if daily else None
    co2_savings = max(0.0, day_yield) * factor if day_yield is not None else None
    sun = sun_result.data if sun_result else None
    sun_age = ((now_utc - sun.fetched_at.astimezone(timezone.utc)).total_seconds()
               if sun else None)
    weather_code = sun.weather_code if sun else None
    weather_variant = weather_icon_variant(weather_code)
    values = {r.local_day: r.energy_kwh for r in database.completed_record_daily_yields(chart_local_day)}
    histories = history_candidates(values, chart_local_day)
    same_day = (
        snapshot_timestamp
        and snapshot_timestamp.astimezone(ZURICH).date() == chart_local_day
    )
    valid = freshness == "fresh" and same_day and snapshot_timestamp <= now_utc
    write = bool(valid and persist_fact_selection and not database.read_only)
    current_daily = (
        daily
        if local_day == chart_local_day
        else database.daily_energy(chart_local_day)
    )
    current_energy = (
        current_daily.energy_today_kwh if same_day and current_daily else None
    )
    forecast = (
        sun
        if sun
        and sun.local_date == chart_local_day
        and 0 <= sun_age <= getattr(sun_result, "max_age_seconds", 21600)
        and sun_result.status in ("fresh", "cached")
        else None
    )
    fact_context = build_fact_context(
        database,
        now_utc,
        current_energy,
        snapshot.solar_power_kw if valid else None,
        forecast,
        values,
        day_aggregates,
    )
    if valid:
        start, _ = database._local_day_bounds(chart_local_day)
        end = snapshot_timestamp.replace(
            minute=snapshot_timestamp.minute // 5 * 5, second=0, microsecond=0
        )
        completed = [
            (r.bucket_start, r.energy_today_kwh)
            for r in day_aggregates
            if _aware_utc(r.bucket_start) < end
        ]
        reliable = bool(
            current_daily
            and current_daily.complete
            and completed
            and database._qualify_record_day(chart_local_day, completed, start, end)
            and all(a[1] <= b[1] for a, b in zip(completed, completed[1:]))
            and current_energy >= completed[-1][1]
        )
        events, after = active_events(
            database, fact_context, values, write, snapshot, reliable, commissioning
        )
        story = choose_insight(
            database, fact_context, histories, values, events, after, write
        )
    elif not snapshot:
        story = Story(
            "MISSING",
            "status",
            "Keine Solardaten verfügbar",
            "Datenerfassung prüfen",
            "missing",
            None,
        )
    else:
        story = Story(
            "STALE",
            "status",
            f"Datenstand {local_timestamp:%H:%M} Uhr",
            "Aktualisierung der Solardaten prüfen",
            "stale",
            None,
        )
    historical = choose_history(database,fact_context,histories,story,write)
    display = dict(power)
    displayed_heat = max(0.0, power["heat_power_kw"] or 0.0)
    display.update({
        "display_house_power_kw": (max(0.0, power["house_power_kw"] - displayed_heat)
                                   if power["house_power_kw"] is not None else None),
        "battery_percent": snapshot.battery_soc_pct if snapshot and snapshot.battery_available else None,
        "battery_available": bool(snapshot and snapshot.battery_available),
        "heat_available": bool(snapshot and snapshot.heat_available),
        "current_time": local_timestamp.strftime("%H:%M Uhr") if local_timestamp else "—",
        "date_text": ("{}, {}. {} {}".format(WEEKDAYS[local_timestamp.weekday()], local_timestamp.day,
                                              MONTHS[local_timestamp.month], local_timestamp.year)
                      if local_timestamp else "—"),
        "sun_hours": sun.sunshine_hours if sun else None,
        "sunrise": sun.sunrise.strftime("%H:%M") if sun else None,
        "sunset": sun.sunset.strftime("%H:%M") if sun else None,
        "weather_icon_variant": weather_variant,
        "co2_savings_kg": co2_savings,
        "story_line_1": story.line_1, "story_line_2": story.line_2,
        "day_yield_kwh": day_yield,
        "self_consumption_percent": _self_consumption(
            database.aggregates_for_local_day(local_day)
        ),
        "chart_solar_area": chart.solar_area,
        "chart_solar_line": chart.solar_line,
        "chart_house_line": chart.house_line,
        "chart_grid_import_line": chart.grid_import_line,
        "chart_battery_line": chart.battery_line,
    })
    return {
        "_historical_comparison": historical,
        "freshness": freshness,
        "latest_snapshot_timestamp": snapshot_timestamp.isoformat() if snapshot_timestamp else None,
        "power_timestamp": power_timestamp.isoformat() if power_timestamp else None,
        "power_source": power_source,
        "power_selection_reason": selection_reason,
        "power_bucket_count": len(rows),
        "daily_energy": ({"source": daily.source, "complete": daily.complete} if daily else None),
        "story_status": {
            "fact_id": story.fact_id, "family": story.family, "phase": story.phase,
            "variant": story.variant, "branch": story.branch, "event_id": story.event_key,
            "energy_period": story.energy_period, "energy_kwh": story.energy_kwh,
            "used_short_template": story.used_short_template,
            "line_1_width_px": story.line_1_width_px,
            "line_2_width_px": story.line_2_width_px,
            "selection_energy_kwh": story.selection_energy_kwh,
            "previous_hour_selection_energy_kwh": story.previous_hour_selection_energy_kwh,
            "selection_hour": story.selection_hour,
            "selection_bucket_start": story.selection_bucket_start,
            "selection_persisted": story.selection_persisted,
            "selection_source": story.selection_source,
            "historical_fact": story.historical_fact,
            "historical_reference_day": story.historical_reference_day,
            "historical_baseline_kwh": story.historical_baseline_kwh,
        },
        "sun_data_status": sun_result.status if sun_result else "missing",
        "sun_data_source": sun_result.source if sun_result else "none",
        "sun_data_fetched_at": sun.fetched_at.isoformat() if sun else None,
        "sun_data_age_seconds": max(0.0, sun_age) if sun_age is not None else None,
        "sun_data_error": sun_result.error if sun_result else None,
        "weather_code": weather_code,
        "weather_code_status": sun.weather_code_status if sun else "missing",
        "weather_icon_variant": weather_variant,
        "co2": {"savings_kg": co2_savings, "factor_kg_per_kwh": factor, "basis": CO2_BASIS},
        "chart_status": {
            "chart_source": "aggregates_5m", "local_day": chart_local_day.isoformat(),
            "aggregate_count": len(day_aggregates),
            "latest_aggregate_timestamp": chart.latest_timestamp,
            "battery_chart_available": chart.battery_available,
            "number_of_solar_points": chart.solar_points,
            "number_of_house_points": chart.house_points,
            "number_of_grid_import_points": chart.grid_import_points,
            "number_of_battery_points": chart.battery_points,
        },
        "display": display,
    }
