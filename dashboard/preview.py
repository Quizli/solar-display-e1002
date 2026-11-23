"""As-of hourly replay with an isolated SQLite database and selection history."""

import json
import math
from dataclasses import replace
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from contextlib import ExitStack

from fronius.model import LiveData
from solar_data.storage import SolarDatabase, _utc_text
from solar_data.timezones import ZURICH
from sun_data.cache import SunDataResult
from sun_data.client import SunData
from .facts.editorial import digest, PREVIEW_COUNTS
from .live_view import build_live_view

UTC = timezone.utc
STEP = timedelta(hours=1)
BUCKET = timedelta(minutes=5)


def bounds(day):
    return datetime.combine(day, time.min, ZURICH).astimezone(UTC)


def synthetic(day, instant, scenario):
    summer = scenario == "sunny-summer"
    start, end = (5, 21) if summer else (8, 16)
    now = instant.astimezone(ZURICH)
    hour = now.hour + now.minute / 60
    progress = min(1, max(0, (hour - start) / (end - start)))
    maximum = 65 + digest(day) % 70 if summer else 2 + digest(day) % 17
    if scenario == "zero-production":
        maximum = 0
    energy = maximum * (1 - math.cos(progress * math.pi)) / 2
    power = maximum * math.sin(progress * math.pi) * math.pi / (2 * (end - start))
    forecast = SunData(
        day,
        now.replace(hour=start, minute=0),
        now.replace(hour=end, minute=0),
        11 if summer else 1,
        instant,
        0 if summer else 3,
        "valid",
    )
    return LiveData(
        instant.isoformat(), power, 2, 0, 0, 0, power - 2, energy, None, False, False
    ), SunDataResult(forecast, "fresh", "synthetic")


def replay(first, last, scenario, path):
    if last < first:
        raise ValueError("--to must be on or after --from")
    with ExitStack() as stack:
        db = stack.enter_context(SolarDatabase(":memory:"))
        con = db.connection
        if not scenario:
            source = stack.enter_context(
                SolarDatabase(str(path), read_only=True)
            ).connection
            source.execute("BEGIN")
        # Evaluate once the first five-minute bucket of each real hour has closed.
        cursor = bounds(first) + BUCKET
        previous = None
        commissioning = {}
        if scenario:
            # Explicit fixture values stay inside this simulation.
            commissioned = (
                (first - timedelta(days=100)).isoformat()
                if scenario == "milestone-collision"
                else ""
            )
            commissioning = dict(
                SOLAR_COMMISSIONED_DATE=commissioned,
                SOLAR_COMMISSIONING_TOTAL_KWH="0"
                if scenario in ("milestone-collision", "counter-reset")
                else "",
            )
            bucket = bounds(first - timedelta(days=40))
        while cursor < bounds(last + timedelta(days=1)):
            local = cursor.astimezone(ZURICH)
            day = local.date()
            sun = None
            if scenario:
                while bucket + BUCKET <= cursor:
                    if scenario != "missing-data":
                        sample, _ = synthetic(
                            bucket.astimezone(ZURICH).date(), bucket, scenario
                        )
                        con.execute(
                            "INSERT INTO aggregates_5m VALUES (?,?,2,0,0,0,?,?,1,0,0)",
                            (
                                _utc_text(bucket),
                                sample.solar_power_kw,
                                sample.grid_power_kw,
                                sample.energy_today_kwh,
                            ),
                        )
                    bucket += BUCKET
                if scenario != "missing-data":
                    sample, sun = synthetic(day, cursor, scenario)
                    if scenario in ("milestone-collision", "counter-reset"):
                        # Two observations 30 s apart date the crossing to this hour.
                        counter = 999.85 if local.hour < 12 else 1000.05
                        if scenario == "counter-reset" and local.hour >= 10:
                            counter = 20
                        if local.hour == 12:
                            earlier = cursor - timedelta(seconds=30)
                            prior, _ = synthetic(day, earlier, scenario)
                            db.store_snapshot(
                                replace(prior, energy_total_kwh=counter - 0.1)
                            )
                            con.commit()
                            build_live_view(
                                db, earlier, sun_result=sun, commissioning=commissioning
                            )
                        sample = replace(sample, energy_total_kwh=counter)
                    db.store_snapshot(sample)
            else:
                for table, col, lag in (
                    ("raw_samples", "timestamp_utc", timedelta(0)),
                    ("aggregates_5m", "bucket_start_utc", BUCKET),
                ):
                    columns = [
                        r[1] for r in source.execute(f"PRAGMA table_info({table})")
                    ]
                    names = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
                    rows = source.execute(
                        f"SELECT {names} FROM {table} WHERE {col}>? AND {col}<=?",
                        (
                            _utc_text(previous - lag) if previous else "",
                            _utc_text(cursor - lag),
                        ),
                    )
                    placeholders = ",".join("?" for _ in columns)
                    con.executemany(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders})", rows
                    )
            con.commit()
            view = build_live_view(
                db, cursor, sun_result=sun, commissioning=commissioning
            )
            story = view["story_status"]
            history = view["_historical_comparison"] or {}
            yield dict(
                timestamp=local.isoformat(),
                phase=story["phase"],
                family=story["family"],
                insight=story["fact_id"],
                branch=story["branch"],
                variant=story["variant"],
                statement=" ".join(
                    filter(
                        None,
                        [
                            view["display"]["story_line_1"],
                            view["display"]["story_line_2"],
                        ],
                    )
                ),
                history=history.get("type"),
                history_statement=history.get("statement"),
                event=story["event_id"],
            )
            previous, cursor = cursor, cursor + STEP


def preview(args):
    counts = Counter()
    token = PREVIEW_COUNTS.set(counts)
    try:
        rows = list(replay(args.first, args.last, args.scenario, args.db_path))
    finally:
        PREVIEW_COUNTS.reset(token)
    last = {}
    distances = []
    longest = run = 0
    prev = None
    for i, r in enumerate(rows):
        ident = r["insight"]
        if r["event"]:
            prev = None
            continue
        if ident in last:
            distances.append(i - last[ident])
        last[ident] = i
        run = run + 1 if ident == prev else 1
        longest = max(run, longest)
        prev = ident
    print(
        json.dumps(
            dict(
                scenario=args.scenario or "read-only replay",
                slots=rows,
                summary=dict(
                    ineligible_energy=counts["ineligible_energy"],
                    overflow=counts["overflow"],
                    slots=len(rows),
                    concepts=len({r["insight"] for r in rows}),
                    variants=len(
                        {(r["insight"], r["branch"], r["variant"]) for r in rows}
                    ),
                    families=Counter(r["family"] for r in rows),
                    history_types=len({r["history"] for r in rows if r["history"]}),
                    repeat_distances=Counter(distances),
                    longest_repeat=longest,
                    fallbacks=sum(
                        r["insight"] in ("TECH_CONTEXT", None)
                        or r["family"] == "history"
                        for r in rows
                    ),
                ),
            ),
            ensure_ascii=False,
        )
    )
    return 0
