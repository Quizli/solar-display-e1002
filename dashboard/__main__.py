import argparse
import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone

from solar_data.storage import SolarDatabase
from sun_data import get_sun_data
from .live_view import build_live_view, validate_co2_factor
from .publisher import publish_view


def _positive(value):
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser():
    result = argparse.ArgumentParser(description="Publish the SQLite-backed SVG dashboard")
    result.add_argument("--db-path", default=os.environ.get("SOLAR_DB_PATH", "data/solar.db"))
    result.add_argument("--output", default=os.environ.get("DASHBOARD_OUTPUT_PATH", "publish/dashboard.svg"))
    result.add_argument("--stale-seconds", type=_positive, default=float(os.environ.get("DASHBOARD_STALE_SECONDS", "180")))
    result.add_argument("--sun-cache", default=os.environ.get("SUN_DATA_CACHE_PATH", "data/sun-data.json"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("once")
    commands.add_parser("status")
    loop = commands.add_parser("loop")
    loop.add_argument("--interval", type=_positive, default=float(os.environ.get("DASHBOARD_REFRESH_SECONDS", "300")))
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
        latitude, longitude = os.environ.get("SOLAR_LAT"), os.environ.get("SOLAR_LON")

        def make_view(database):
            sun_result = None
            if latitude and longitude:
                sun_result = get_sun_data(latitude, longitude, args.sun_cache,
                                          refresh_seconds=refresh, max_age_seconds=max_age)
            return build_live_view(database, stale_seconds=args.stale_seconds,
                                   sun_result=sun_result, co2_factor=factor)

        with SolarDatabase(args.db_path) as database:
            if args.command == "status":
                print(json.dumps(make_view(database), indent=2, ensure_ascii=False))
                return 0
            while True:
                view = make_view(database)
                if view["freshness"] != "missing":
                    publish_view(view, args.output)
                    logging.info("published dashboard from %s data", view["freshness"])
                else:
                    logging.warning("no solar data; last good dashboard is unchanged")
                    if args.command == "once":
                        return 1
                if args.command == "once":
                    return 0
                if stop.wait(args.interval):
                    return 0
    except Exception:
        logging.exception("dashboard command failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
