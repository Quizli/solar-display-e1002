import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import date, datetime, timezone

from fronius.client import FroniusClient
from solar_data.storage import SolarDatabase
from solar_data.timezones import ZURICH

from .service import Collector


def _positive(value: str) -> float:
    result = float(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Collect and inspect normalized solar data")
    result.add_argument("--db-path", default=os.environ.get("SOLAR_DB_PATH", "data/solar.db"))
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("once", "loop"):
        command = subparsers.add_parser(name)
        command.add_argument("--base-url", default=os.environ.get("FRONIUS_BASE_URL"))
        command.add_argument("--timeout", type=_positive, default=5.0)
        command.add_argument(
            "--retention-days", type=float,
            default=float(os.environ.get("SOLAR_RAW_RETENTION_DAYS", "7")),
        )
        if name == "loop":
            command.add_argument(
                "--interval", type=_positive,
                default=float(os.environ.get("SOLAR_POLL_INTERVAL_SECONDS", "10")),
            )
    subparsers.add_parser("status")
    subparsers.add_parser("aggregate")
    repair = subparsers.add_parser("repair-energy")
    repair.add_argument("--day", type=date.fromisoformat)
    return result


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SolarDatabase(args.db_path) as database:
        if args.command == "status":
            snapshot = database.latest_snapshot()
            print(json.dumps(snapshot.to_dict() if snapshot else None, indent=2))
            return 0
        if args.command == "aggregate":
            count = database.aggregate_completed(datetime.now(timezone.utc))
            print(json.dumps({"processed_buckets": count}))
            return 0
        if args.command == "repair-energy":
            local_day = args.day or datetime.now(ZURICH).date()
            count = database.repair_aggregate_daily_energy(local_day)
            print(json.dumps({"local_day": local_day.isoformat(),
                              "updated_aggregates": count}))
            return 0
        if not args.base_url:
            parser().error("--base-url or FRONIUS_BASE_URL is required")
        collector = Collector(
            FroniusClient(args.base_url, args.timeout), database,
            interval=getattr(args, "interval", 10), retention_days=args.retention_days,
        )
        if args.command == "once":
            return 0 if collector.collect_once() else 1

        stop_event = threading.Event()

        def stop(signum, _frame):
            logging.info("received signal %s; stopping collector", signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        collector.run(stop_event)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
