"""Read-only production health assessment for the published dashboard."""

from datetime import datetime, timezone
from pathlib import Path

from solar_data.storage import SolarDatabase, _aware_utc
from sun_data import get_sun_data

from .live_view import build_live_view
from .publisher import validate_svg


EXPECTED_TABLES = {"schema_version", "raw_samples", "aggregates_5m", "fact_history"}
EXPECTED_SCHEMA_VERSION = 4


def _offline_sun_result(cache_path, now, refresh_seconds, max_age_seconds):
    def offline_fetcher(*_args, **_kwargs):
        raise RuntimeError("network disabled during health check")

    return get_sun_data("unused", "unused", cache_path, now=now,
                        refresh_seconds=refresh_seconds,
                        max_age_seconds=max_age_seconds,
                        fetcher=offline_fetcher)


def check_health(db_path, output_path, stale_seconds=180.0, refresh_seconds=300.0,
                 sun_cache="data/sun-data.json", sun_refresh_seconds=1800.0,
                 sun_max_age_seconds=21600.0, co2_factor=0.128, now=None):
    """Return ``(report, exit_code)`` without writing files or database rows."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report = {
        "status": "unhealthy", "database": "error", "schema_version": None,
        "freshness": "missing", "latest_snapshot_age_seconds": None,
        "power_source": "missing", "published_svg": "missing",
        "published_svg_age_seconds": None, "fact_id": None,
    }
    degraded = False
    try:
        with SolarDatabase(db_path, read_only=True) as database:
            tables = {row[0] for row in database.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not EXPECTED_TABLES.issubset(tables):
                return report, 2
            row = database.connection.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
            if row is None:
                return report, 2
            report["database"] = "ok"
            report["schema_version"] = row[0]
            if report["schema_version"] != EXPECTED_SCHEMA_VERSION:
                return report, 2
            sun_result = _offline_sun_result(
                sun_cache, now, sun_refresh_seconds, sun_max_age_seconds)
            view = build_live_view(
                database, now=now, stale_seconds=stale_seconds,
                sun_result=sun_result, co2_factor=co2_factor,
                persist_fact_selection=False)
    except Exception:
        return report, 2

    report["freshness"] = view["freshness"]
    report["power_source"] = view["power_source"]
    report["fact_id"] = view["story_status"]["fact_id"]
    if view["latest_snapshot_timestamp"]:
        age = (now - _aware_utc(view["latest_snapshot_timestamp"])).total_seconds()
        report["latest_snapshot_age_seconds"] = max(0, int(age))
    if view["freshness"] != "fresh":
        return report, 2

    degraded |= view["power_source"] == "raw_snapshot"
    degraded |= view["sun_data_status"] != "fresh"
    degraded |= view["weather_code_status"] != "valid"
    degraded |= report["fact_id"] in (None, "TECH")

    output = Path(output_path)
    try:
        svg_age = max(0, int(now.timestamp() - output.stat().st_mtime))
        report["published_svg_age_seconds"] = svg_age
        validate_svg(output.read_text(encoding="utf-8"))
        report["published_svg"] = "ok"
        degraded |= svg_age > refresh_seconds
    except Exception:
        report["published_svg"] = "invalid" if output.exists() else "missing"
        return report, 2

    report["status"] = "degraded" if degraded else "healthy"
    return report, 1 if degraded else 0


def format_health_text(report):
    age = report["latest_snapshot_age_seconds"]
    svg_age = report["published_svg_age_seconds"]
    return "\n".join((
        "Solar Display: {}".format(report["status"].upper()),
        "Database: {}, schema {}".format(report["database"].upper(),
                                         report["schema_version"] or "—"),
        "Solar data: {}, {} seconds old".format(
            report["freshness"], age if age is not None else "—"),
        "Power source: {}".format(report["power_source"]),
        "SVG: {}, {} seconds old".format(
            report["published_svg"].upper(), svg_age if svg_age is not None else "—"),
        "Current fact: {}".format(report["fact_id"] or "—"),
    ))
