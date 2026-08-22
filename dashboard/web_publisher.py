"""Build and atomically publish the allowlisted public dashboard contract."""

from datetime import datetime, time, timedelta, timezone
import json
import math
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from solar_data.storage import _aware_utc
from solar_data.timezones import ZURICH
from .facts.history import (eligible_historical_candidates, historical_difference,
                            render_historical)
from .live_view import build_fact_context


SCHEMA_VERSION = "1.0"
GRID_IMPORT_PLOT_THRESHOLD_KW = 0.5


def _number(value):
    """Return only JSON-safe real numbers; optional invalid values become null."""
    if value is None or isinstance(value, bool):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _flow(value, positive, negative, available=True):
    value = _number(value)
    if not available or value is None:
        return {"power_kw": None, "magnitude_kw": None, "direction": "unavailable"}
    if math.isclose(value, 0.0, abs_tol=1e-9):
        direction = "idle"
    else:
        direction = positive if value > 0 else negative
    return {"power_kw": value, "magnitude_kw": abs(value), "direction": direction}


def _historical(database, now, day_yield, solar_power, sun,
                insight_fact_id=None, insight_statement=None, candidates=None):
    context = build_fact_context(database, now, day_yield, solar_power, sun)
    if candidates is None:
        candidates = eligible_historical_candidates(database, context)
    # The average is the tile's canonical non-record statement.  Even when it
    # covers the record day, it only compares production with an average and
    # cannot claim a record by construction.  Suppressing HIST_RECORD below is
    # therefore the semantic redundancy rule; text inequality alone is not.
    # Keep other non-record candidates as fallbacks for older persisted views.
    ordered = sorted(candidates, key=lambda item: item.fact_id != "HIST_AVERAGE")
    for candidate in ordered:
        if candidate.fact_id == insight_fact_id:
            continue
        details = candidate.context
        rendered = render_historical(candidate.fact_id, context, details)
        if rendered is None:
            continue
        statement = " ".join((rendered.line_1, rendered.line_2))
        if statement == insight_statement:
            continue
        value = _number(details.get("comparison_energy_kwh", context.today_energy_kwh))
        baseline = _number(details.get("baseline_energy_kwh"))
        if value is not None and baseline is not None and baseline > 0:
            difference_percent, direction = historical_difference(value, baseline)
        else:
            difference_percent, direction = None, None
        return {
            "type": candidate.fact_id,
            "statement": statement,
            "direction": direction,
            "difference_percent": difference_percent,
            "comparison_value_kwh": value,
            "baseline_kwh": baseline,
            "reference_period": details.get("period", "today"),
            "reference_day": details.get("reference_day"),
            "comparison_day": details.get("comparison_day"),
            "baseline_days": details.get("baseline_days"),
        }
    return None


def _sun_from_view(view, local_day):
    sunrise = view["display"].get("sunrise")
    sunset = view["display"].get("sunset")
    if not sunrise or not sunset:
        return None
    try:
        return SimpleNamespace(
            sunrise=datetime.combine(local_day, time.fromisoformat(sunrise), ZURICH),
            sunset=datetime.combine(local_day, time.fromisoformat(sunset), ZURICH),
            weather_code=view.get("weather_code"),
        )
    except ValueError:
        return None


def build_web_payload(database, view, snapshot, now=None):
    """Map the shared live view and aggregates to an explicit public allowlist."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    display = view["display"]
    story = view["story_status"]
    snapshot_time = _aware_utc(snapshot.timestamp) if snapshot else None
    snapshot_text = snapshot_time.isoformat() if snapshot_time else None
    age = max(0.0, (now_utc - snapshot_time).total_seconds()) if snapshot_time else None

    optional_degraded = []
    if view.get("sun_data_status") not in ("fresh", "cached"):
        optional_degraded.append("weather")
    freshness = view["freshness"]
    overall = ("missing" if freshness == "missing" else
               "stale" if freshness == "stale" else
               "degraded" if optional_degraded else "fresh")

    now_local = now_utc.astimezone(ZURICH)
    local_day = now_local.date()
    series = []
    for row in database.aggregates_for_local_day(local_day):
        start = _aware_utc(row.bucket_start)
        end = start + timedelta(minutes=5)
        if end > now_utc:
            continue
        series.append({
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "solar_power_kw": _number(row.solar_power_kw),
            "house_consumption_kw": _number(row.house_power_kw),
            "grid_import_kw": (
                _number(-row.grid_power_kw)
                if row.grid_power_kw <= -GRID_IMPORT_PLOT_THRESHOLD_KW else None
            ),
            "battery_state_of_charge_percent": (
                _number(row.battery_soc_pct) if row.battery_available else None
            ),
            "battery_power_kw": (_number(row.battery_power_kw)
                                  if row.battery_available else None),
            "sample_count": row.sample_count,
        })

    battery_available = bool(snapshot and snapshot.battery_available)
    heat_available = bool(snapshot and snapshot.heat_available)
    insight_statement = " ".join(
        part for part in (display.get("story_line_1"), display.get("story_line_2"))
        if part
    )
    historical = _historical(
        database, now_utc, display.get("day_yield_kwh"),
        snapshot.solar_power_kw if snapshot else None,
        _sun_from_view(view, local_day), story.get("fact_id"), insight_statement,
        view.get("_historical_candidates"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_utc.isoformat(),
        "data": {
            "timestamp": snapshot_text,
            "latest_sample_at": snapshot_text,
            "age_seconds": age,
        },
        "status": {
            "overall": overall,
            "freshness": freshness,
            "affected_components": optional_degraded,
            "components": {
                "solar_data": ("offline" if freshness == "missing" else freshness),
                "weather": view.get("sun_data_status", "missing"),
                "battery": "available" if battery_available else "missing",
                "heat": "available" if heat_available else "missing",
                "chart": "available" if series else "missing",
                "insight": "available" if story.get("fact_id") else "missing",
                "historical_comparison": "available" if historical else "missing",
            },
        },
        "header": {
            "local_date": local_day.isoformat(),
            "local_time": now_local.isoformat(),
            "weather_condition": view.get("weather_icon_variant") if
                                 view.get("sun_data_status") in ("fresh", "cached") else None,
            "sunshine_hours": _number(display.get("sun_hours")),
            "sunrise": display.get("sunrise"),
            "sunset": display.get("sunset"),
        },
        "live": {
            "solar_power_kw": _number(snapshot.solar_power_kw) if snapshot else None,
            "house_consumption_kw": (_number(max(0.0, snapshot.house_power_kw -
                                                       max(0.0, snapshot.heat_power_kw)))
                                     if snapshot else None),
            "heat_power_kw": (_number(max(0.0, snapshot.heat_power_kw))
                              if heat_available else None),
            "battery_state_of_charge_percent": (_number(snapshot.battery_soc_pct)
                                                  if battery_available else None),
            "battery_flow": _flow(snapshot.battery_power_kw if snapshot else None,
                                  "charging", "discharging",
                                  battery_available),
            "grid_flow": _flow(snapshot.grid_power_kw if snapshot else None,
                               "exporting", "importing"),
        },
        "today": {
            "yield_kwh": _number(display.get("day_yield_kwh")),
            "self_consumption_percent": _number(display.get("self_consumption_percent")),
            "co2_avoided_kg": _number(display.get("co2_savings_kg")),
        },
        "chart": {
            "interval_minutes": 5,
            "local_date": local_day.isoformat(),
            "series": series,
        },
        "insight": {
            "fact_id": story.get("fact_id"),
            "family": story.get("family"),
            "line_1": display.get("story_line_1"),
            "line_2": display.get("story_line_2"),
            "selection_hour": story.get("selection_hour"),
            "persisted": bool(story.get("selection_persisted")),
        },
        "historical_comparison": historical,
    }
    # Enforce standard JSON here as well as during publication.
    json.dumps(payload, allow_nan=False)
    return payload


def publish_web_payload(payload, output_path):
    """Atomically replace the public JSON so Nginx never observes partial data."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(output.parent),
                                         prefix=".dashboard-", suffix=".json.tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(str(temporary), str(output))
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        raise
    return output
