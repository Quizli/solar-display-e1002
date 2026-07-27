import argparse
import json
import logging
import math
import os
import signal
import threading
import time
from datetime import datetime, timezone

from solar_data.storage import SolarDatabase
from sun_data import get_sun_data
from .live_view import build_live_view, validate_co2_factor
from .publisher import publish_view
from .web_publisher import build_web_payload, publish_web_payload
from .health import check_health, format_health_text

_SNAPSHOT_NOT_SUPPLIED = object()


def _positive(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _sun_coordinates(environment):
    latitude = environment.get("SOLAR_LAT")
    longitude = environment.get("SOLAR_LON")
    latitude_set = bool(latitude)
    longitude_set = bool(longitude)
    if latitude_set != longitude_set:
        raise ValueError("SOLAR_LAT and SOLAR_LON must either both be set or both be empty")
    return (latitude, longitude) if latitude_set else None


def parser():
    result = argparse.ArgumentParser(description="Publish the SQLite-backed SVG dashboard")
    result.add_argument("--db-path", default=os.environ.get("SOLAR_DB_PATH", "data/solar.db"))
    result.add_argument("--output", default=os.environ.get("DASHBOARD_OUTPUT_PATH", "publish/dashboard.svg"))
    result.add_argument("--web-output", default=os.environ.get(
        "DASHBOARD_JSON_OUTPUT_PATH", "publish/dashboard.json"))
    result.add_argument("--stale-seconds", type=_positive, default=float(os.environ.get("DASHBOARD_STALE_SECONDS", "180")))
    result.add_argument("--sun-cache", default=os.environ.get("SUN_DATA_CACHE_PATH", "data/sun-data.json"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("once")
    commands.add_parser("status")
    health = commands.add_parser("health")
    health.add_argument("--text", action="store_true")
    loop = commands.add_parser("loop")
    loop.add_argument("--interval", type=_positive, default=float(os.environ.get("DASHBOARD_REFRESH_SECONDS", "300")))
    loop.add_argument("--web-interval", type=_positive, default=float(os.environ.get(
        "DASHBOARD_JSON_REFRESH_SECONDS", "15")))
    return result


def main():
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    def request_stop(signum, _frame):
        logging.info("received signal %s; stopping dashboard publisher", signum)
        stop.set()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        factor = validate_co2_factor(os.environ.get("CO2_AVOIDED_KG_PER_KWH", "0.128"))
        refresh = _positive(os.environ.get("SUN_DATA_REFRESH_SECONDS", "1800"))
        max_age = _positive(os.environ.get("SUN_DATA_MAX_AGE_SECONDS", "21600"))
        if args.command == "health":
            report, exit_code = check_health(
                args.db_path, args.output, stale_seconds=args.stale_seconds,
                refresh_seconds=_positive(os.environ.get("DASHBOARD_REFRESH_SECONDS", "300")),
                sun_cache=args.sun_cache, sun_refresh_seconds=refresh,
                sun_max_age_seconds=max_age, co2_factor=factor)
            print(format_health_text(report) if args.text else
                  json.dumps(report, separators=(",", ":"), ensure_ascii=False))
            return exit_code
        coordinates = _sun_coordinates(os.environ)

        def make_view(database, now=None, snapshot=_SNAPSHOT_NOT_SUPPLIED):
            sun_result = None
            if coordinates:
                sun_result = get_sun_data(coordinates[0], coordinates[1], args.sun_cache,
                                          refresh_seconds=refresh, max_age_seconds=max_age)
            options = ({} if snapshot is _SNAPSHOT_NOT_SUPPLIED else
                       {"snapshot": snapshot})
            return build_live_view(database, now=now, stale_seconds=args.stale_seconds,
                                   sun_result=sun_result, co2_factor=factor,
                                   **options)

        with SolarDatabase(args.db_path) as database:
            if args.command == "status":
                print(json.dumps(make_view(database), indent=2, ensure_ascii=False))
                return 0
            next_svg = 0.0
            while True:
                cycle_now = datetime.now(timezone.utc)
                snapshot = database.latest_snapshot()
                view = make_view(database, cycle_now, snapshot)
                monotonic_now = time.monotonic()
                if view["freshness"] != "missing" and monotonic_now >= next_svg:
                    publish_view(view, args.output)
                    logging.info("published dashboard from %s data", view["freshness"])
                    next_svg = monotonic_now + (args.interval if args.command == "loop" else 0)
                else:
                    if view["freshness"] == "missing":
                        logging.warning("no solar data; last good SVG dashboard is unchanged")
                try:
                    publish_web_payload(
                        build_web_payload(database, view, snapshot, cycle_now),
                        args.web_output)
                    logging.info("published public dashboard JSON from %s data",
                                 view["freshness"])
                except Exception:
                    logging.exception("public dashboard JSON publishing failed")
                    if args.command == "once":
                        return 1
                if args.command == "once":
                    return 1 if view["freshness"] == "missing" else 0
                if stop.wait(args.web_interval):
                    return 0
    except Exception:
        logging.exception("dashboard command failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
