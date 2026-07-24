import argparse
import json
import os
import sys

from .adapter import FroniusDataError, normalize_live_data
from .client import FroniusClient, FroniusClientError


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch one normalized Fronius live snapshot")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("FRONIUS_BASE_URL"),
        help="Fronius v1 base URL (or set FRONIUS_BASE_URL)",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or FRONIUS_BASE_URL is required")

    try:
        payloads = FroniusClient(args.base_url, args.timeout).get_live_payloads()
        snapshot = normalize_live_data(**payloads)
    except (FroniusClientError, FroniusDataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
