"""Build and atomically publish the allowlisted public dashboard contract."""

from datetime import datetime, timedelta, timezone
import json
import math
import os
import tempfile
from pathlib import Path

from solar_data.storage import _aware_utc
from solar_data.timezones import ZURICH


SCHEMA_VERSION = "1.0"


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


def _historical(story):
    if not story.get("historical_fact"):
        return None
    value = _number(story.get("energy_kwh"))
    baseline = _number(story.get("historical_baseline_kwh"))
    direction = None
    difference_percent = None
    if value is not None and baseline is not None and baseline > 0:
        difference_percent = (value - baseline) / baseline * 100
        direction = ("similar" if abs(difference_percent) < 5 else
                     "higher" if difference_percent > 0 else "lower")
    return {
        "fact_id": story.get("fact_id"),
        "statement": None,
        "reference_day": story.get("historical_reference_day"),
        "value_kwh": value,
        "baseline_kwh": baseline,
        "direction": direction,
        "difference_percent": difference_percent,
    }


def build_web_payload(database, view, now=None):
    """Map the shared live view and aggregates to an explicit public allowlist."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    display = view["display"]
    story = view["story_status"]
    snapshot_text = view.get("latest_snapshot_timestamp")
    snapshot_time = _aware_utc(snapshot_text) if snapshot_text else None
    age = max(0.0, (now_utc - snapshot_time).total_seconds()) if snapshot_time else None

    optional_degraded = []
    if view.get("sun_data_status") not in ("fresh", "cached"):
        optional_degraded.append("weather")
    if view.get("power_source") == "raw_snapshot":
        optional_degraded.append("live_power_aggregation")
    freshness = view["freshness"]
    overall = ("missing" if freshness == "missing" else
               "stale" if freshness == "stale" else
               "degraded" if optional_degraded else "fresh")

    local_day = now_utc.astimezone(ZURICH).date()
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
            "battery_power_kw": (_number(row.battery_power_kw)
                                  if row.battery_available else None),
            "sample_count": row.sample_count,
        })

    battery_available = bool(display.get("battery_available"))
    heat_available = bool(display.get("heat_available"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_utc.isoformat(),
        "data": {
            "timestamp": view.get("power_timestamp"),
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
                "historical_comparison": ("available" if story.get("historical_fact")
                                            else "missing"),
            },
        },
        "header": {
            "local_date": local_day.isoformat(),
            "local_time": (view.get("power_timestamp") and
                           _aware_utc(view["power_timestamp"]).astimezone(ZURICH).isoformat()),
            "weather_condition": view.get("weather_icon_variant") if
                                 view.get("sun_data_status") in ("fresh", "cached") else None,
            "sunshine_hours": _number(display.get("sun_hours")),
            "sunrise": display.get("sunrise"),
            "sunset": display.get("sunset"),
        },
        "live": {
            "solar_power_kw": _number(display.get("solar_power_kw")),
            "house_consumption_kw": _number(display.get("display_house_power_kw")),
            "heat_power_kw": _number(display.get("heat_power_kw")) if heat_available else None,
            "battery_state_of_charge_percent": (_number(display.get("battery_percent"))
                                                  if battery_available else None),
            "battery_flow": _flow(display.get("battery_power_kw"), "charging", "discharging",
                                  battery_available),
            "grid_flow": _flow(display.get("grid_power_kw"), "exporting", "importing"),
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
        "historical_comparison": _historical(story),
    }
    if payload["historical_comparison"] is not None:
        payload["historical_comparison"]["statement"] = " ".join(
            part for part in (display.get("story_line_1"), display.get("story_line_2"))
            if part
        )
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
