import argparse
import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone

from solar_data.storage import SolarDatabase
from .live_view import build_live_view
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
        with SolarDatabase(args.db_path) as database:
            if args.command == "status":
                print(json.dumps(build_live_view(database, stale_seconds=args.stale_seconds), indent=2, ensure_ascii=False))
                return 0
            while True:
                view = build_live_view(database, stale_seconds=args.stale_seconds)
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
