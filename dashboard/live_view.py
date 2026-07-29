from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Dict, Optional

from solar_data.storage import POWER_FIELDS, SolarDatabase, _aware_utc
from solar_data.timezones import ZURICH
from .chart import build_chart
from .weather import weather_icon_variant
from .facts import (FactContext, Story, build_story_from_context,
                    eligible_facts_for_hour, hour_key, story_for_catalog_fact)
from .facts.catalog import FACTS_BY_ID, has_eligible_fact_for_energy
from .facts.history import (HISTORICAL_IDS, decode_and_render,
                            eligible_historical_candidates, render_historical)

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


def build_fact_context(database, now, today_energy_kwh, solar_power_kw, sun=None):
    """Collect the shared inputs for facts and historical comparisons."""
    now_local = now.astimezone(ZURICH)
    yesterday = database.daily_energy(now_local.date() - timedelta(days=1))
    yesterday_energy = yesterday.energy_today_kwh if yesterday and yesterday.energy_today_kwh >= 0 else None
    anchors = {}
    anchor_buckets = {}
    for aggregate in database.aggregates_for_local_day(now_local.date()):
        if not has_eligible_fact_for_energy(aggregate.energy_today_kwh):
            continue
        bucket_local = _aware_utc(aggregate.bucket_start).astimezone(ZURICH)
        key = hour_key(bucket_local)
        if key not in anchors:
            anchors[key] = aggregate.energy_today_kwh
            anchor_buckets[key] = aggregate.bucket_start
    current_hour = now_local.replace(minute=0, second=0, microsecond=0)
    previous_hour = (current_hour.astimezone(timezone.utc) - timedelta(hours=1)).astimezone(ZURICH)
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
        selection_energy_by_hour=anchors,
        selection_bucket_by_hour=anchor_buckets,
    )


def build_story(database, now, today_energy_kwh, solar_power_kw, sun=None,
                persist_selection=True):
    """Select the renderer-independent, hourly persisted dashboard story."""
    context = build_fact_context(database, now, today_energy_kwh, solar_power_kw, sun)
    now_local = context.now_local
    current_hour = now_local.replace(minute=0, second=0, microsecond=0)
    previous_hour = (current_hour.astimezone(timezone.utc) - timedelta(hours=1)).astimezone(ZURICH)
    anchors = context.selection_energy_by_hour
    initial = build_story_from_context(context)
    morning_history = initial.phase in ("pre_sunrise", "morning_waiting")
    if initial.family == "status" and not morning_history:
        return initial
    if not persist_selection:
        return initial

    key = hour_key(current_hour)
    pinned_records = [item for item in eligible_historical_candidates(database, context)
                      if item.fact_id == "HIST_RECORD"]
    if pinned_records:
        current = next((item for item in pinned_records
                        if item.context.get("period") == "today"), None)
        rendered = render_historical(
            "HIST_RECORD", context, (current or pinned_records[0]).context)
        if rendered:
            candidate = current or pinned_records[0]
            database.store_fact_selection(
                key, current_hour, "HIST_RECORD", "history",
                context.selection_energy_kwh,
                context.selection_bucket_by_hour.get(key),
                context_json=candidate.context_json)
            return Story(**{**rendered.__dict__,
                            "selection_energy_kwh": context.selection_energy_kwh,
                            "selection_bucket_start": context.selection_bucket_by_hour.get(
                                hour_key(current_hour)),
                            "selection_source": "pinned_record"})

    previous = database.latest_fact_selection_before(current_hour)
    previous_energy = (previous.selection_energy_kwh if previous else
                       anchors.get(hour_key(previous_hour)))
    stored = database.get_fact_selection(key)
    if stored:
        fact = FACTS_BY_ID.get(stored.fact_id)
        if fact:
            reused = story_for_catalog_fact(
                context, fact, stored.selection_energy_kwh, previous_energy,
                stored.selection_bucket_start, persisted=True, source="persisted")
            if reused:
                return reused
        if stored.fact_id in HISTORICAL_IDS:
            reused = decode_and_render(stored.fact_id, context, stored.context_json)
            if reused:
                return Story(**{**reused.__dict__,
                                "selection_energy_kwh": stored.selection_energy_kwh,
                                "selection_bucket_start": stored.selection_bucket_start,
                                "selection_persisted": True,
                                "selection_source": "persisted"})
            LOGGER.warning("persisted historical fact %s has invalid context",
                           stored.fact_id)
            return initial

    # A completed bucket for the new hour arrives a few minutes after the hour.
    # Keep showing the immediately preceding real (UTC) hour in that short gap;
    # using UTC here distinguishes both folds of the autumn clock change.
    current_anchor = anchors.get(key)
    bridge_age = now.astimezone(timezone.utc) - current_hour.astimezone(timezone.utc)
    if (initial.phase == "active_production" and current_anchor is None and
            timedelta(0) <= bridge_age <= timedelta(minutes=10)):
        bridge_record = database.get_fact_selection(hour_key(previous_hour))
        bridge_fact = FACTS_BY_ID.get(bridge_record.fact_id) if bridge_record else None
        if bridge_fact:
            bridged = story_for_catalog_fact(
                context, bridge_fact, bridge_record.selection_energy_kwh,
                bridge_record.selection_energy_kwh,
                bridge_record.selection_bucket_start, persisted=True,
                source="previous_hour_bridge")
            if bridged:
                return bridged
        elif bridge_record and bridge_record.fact_id in HISTORICAL_IDS:
            bridged = decode_and_render(
                bridge_record.fact_id, context, bridge_record.context_json)
            if bridged:
                return Story(**{
                    **bridged.__dict__,
                    "selection_energy_kwh": bridge_record.selection_energy_kwh,
                    "selection_bucket_start": bridge_record.selection_bucket_start,
                    "selection_persisted": True,
                    "selection_source": "previous_hour_bridge",
                })

    used_history = any(item.fact_id in HISTORICAL_IDS for item in
                       database.fact_selections_for_local_day(now_local.date()))
    if not used_history:
        historical = eligible_historical_candidates(database, context)
        if historical:
            candidate = historical[0]
            rendered = render_historical(candidate.fact_id, context, candidate.context)
            if rendered:
                record = database.store_fact_selection(
                    key, current_hour, candidate.fact_id, "history",
                    (initial.selection_energy_kwh if initial.selection_energy_kwh is not None
                     else context.yesterday_energy_kwh), initial.selection_bucket_start,
                    context_json=candidate.context_json)
                selected = decode_and_render(record.fact_id, context, record.context_json)
                if selected:
                    return Story(**{**selected.__dict__,
                                    "selection_energy_kwh": record.selection_energy_kwh,
                                    "selection_bucket_start": record.selection_bucket_start,
                                    "selection_persisted": True,
                                    "selection_source": "new"})

    if initial.family in ("status", "technical"):
        return initial

    selection_energy = initial.selection_energy_kwh
    candidates = eligible_facts_for_hour(
        context, current_hour, selection_energy, initial.energy_kwh)
    if not candidates:
        return initial
    used_today = {item.fact_id for item in
                  database.fact_selections_for_local_day(now_local.date())
                  if item.fact_id in FACTS_BY_ID}
    used_yesterday = {item.fact_id for item in
                      database.fact_selections_for_local_day(
                          now_local.date() - timedelta(days=1))
                      if item.fact_id in FACTS_BY_ID}
    unused_both_other_family = [item for item in candidates
                                if item[0].fact_id not in used_today
                                and item[0].fact_id not in used_yesterday
                                and (previous is None or
                                     item[0].family != previous.family)]
    unused_both = [item for item in candidates
                   if item[0].fact_id not in used_today
                   and item[0].fact_id not in used_yesterday]
    unused_today_other_family = [item for item in candidates
                                 if item[0].fact_id not in used_today
                                 and (previous is None or
                                      item[0].family != previous.family)]
    unused_today = [item for item in candidates
                    if item[0].fact_id not in used_today]
    recent = database.recent_catalog_fact_selections(tuple(FACTS_BY_ID), 12)
    recent_ids = {item.fact_id for item in recent}
    previous_catalog = recent[0] if recent else None
    def other_family(item):
        return previous_catalog is None or item[0].family != previous_catalog.family
    stages = (
        [item for item in candidates if item[0].fact_id not in used_today
         and item[0].fact_id not in recent_ids and other_family(item)],
        [item for item in candidates if item[0].fact_id not in used_today
         and item[0].fact_id not in recent_ids],
        [item for item in candidates if item[0].fact_id not in used_today
         and other_family(item)],
        [item for item in candidates if item[0].fact_id not in used_today],
    )
    selected_stage = next((stage for stage in stages if stage), None)
    if selected_stage:
        fact = next((item[0] for item in selected_stage
                     if item[0].fact_id not in used_yesterday), selected_stage[0][0])
    else:
        all_history = database.recent_catalog_fact_selections(tuple(FACTS_BY_ID), 100000)
        rank = {item.fact_id: index for index, item in enumerate(all_history)}
        pool = [item for item in candidates if other_family(item)] or candidates
        fact = max(pool, key=lambda item: rank.get(item[0].fact_id, len(rank) + 1))[0]
    record = database.store_fact_selection(
        key, current_hour, fact.fact_id, fact.family, selection_energy,
        initial.selection_bucket_start)
    # INSERT OR IGNORE makes the first concurrent writer authoritative.
    fact = FACTS_BY_ID.get(record.fact_id)
    if fact is None:
        return initial
    return (story_for_catalog_fact(
        context, fact, record.selection_energy_kwh, previous_energy,
        record.selection_bucket_start, persisted=True, source="new") or initial)


def build_live_view(database: SolarDatabase, now: Optional[datetime] = None,
                    stale_seconds: float = 180.0, sun_result=None,
                    co2_factor: float = 0.128,
                    persist_fact_selection: bool = True,
                    snapshot=_SNAPSHOT_UNSET) -> Dict[str, object]:
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
    story = build_story(database, now_utc, day_yield, power["solar_power_kw"], sun,
                        persist_selection=(freshness == "fresh" and
                                           persist_fact_selection))
    if freshness == "stale":
        story = Story("STALE", "status", "Datenstand {} Uhr".format(local_timestamp.strftime("%H:%M")),
                      "Aktualisierung der Solardaten prüfen", story.phase,
                      story.energy_period, story.energy_kwh)
    elif freshness == "missing":
        story = Story("MISSING", "status", "Keine Solardaten verfügbar",
                      "Datenerfassung prüfen", story.phase, story.energy_period,
                      story.energy_kwh)
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
        "chart_battery_line": chart.battery_line,
    })
    return {
        "freshness": freshness,
        "latest_snapshot_timestamp": snapshot_timestamp.isoformat() if snapshot_timestamp else None,
        "power_timestamp": power_timestamp.isoformat() if power_timestamp else None,
        "power_source": power_source,
        "power_selection_reason": selection_reason,
        "power_bucket_count": len(rows),
        "daily_energy": ({"source": daily.source, "complete": daily.complete} if daily else None),
        "story_status": {
            "fact_id": story.fact_id, "family": story.family, "phase": story.phase,
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
            "number_of_battery_points": chart.battery_points,
        },
        "display": display,
    }
