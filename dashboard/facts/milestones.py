"""Detect verified crossings and retain their factual context through local midnight."""

import calendar
import json
import logging
import math
import os
from datetime import date, datetime, timezone

from .editorial import Candidate, EVENTS, number
from .statistics import DAY, streak

LOG = logging.getLogger(__name__)


def lifetime(state, timestamp, total, emit, config):
    try:
        baseline = float(config["SOLAR_COMMISSIONING_TOTAL_KWH"])
    except (KeyError, ValueError):
        return
    if not math.isfinite(baseline) or baseline < 0:
        return
    previous = state.get("meter", {})
    if total is None or not math.isfinite(total):
        if previous:
            previous["interrupted"] = True
        return
    value = total - baseline
    stamp = timestamp.timestamp()
    if previous.get("baseline") != baseline:
        previous = {}
    if previous.get("blocked") or (previous and stamp <= previous["stamp"]):
        return
    elapsed = stamp - previous.get("stamp", stamp)
    delta = value - previous.get("value", value)
    invalid = value < 0 or delta < 0 or delta > 24 * elapsed / 3600 + 0.1
    if invalid and not previous.get("blocked"):
        LOG.warning(
            "Lifetime meter continuity lost; verify commissioning configuration"
        )
    if invalid:
        state["meter"] = dict(previous, baseline=baseline, blocked=True)
        return
    thresholds = EVENTS["LIFETIME_ENERGY"]["thresholds_kwh"]
    crossed = [t for t in thresholds if previous.get("value", value) < t <= value]
    multiple = int(value // 1000000) * 1000000
    if multiple > thresholds[-1] and previous.get("value", value) < multiple:
        crossed.append(multiple)
    if (
        previous
        and not previous.get("interrupted")
        and previous["day"] == timestamp.date().isoformat()
        and elapsed <= 600
        and crossed
    ):
        t = max(crossed)
        branch = {1000: "mwh1", 10000: "mwh10", 100000: "mwh100"}.get(t, "default")
        emit(
            "LIFETIME_ENERGY", str(t), branch, kwh=number(t, 0), mwh=number(t / 1000, 0)
        )
    state["meter"] = dict(
        value=value,
        baseline=baseline,
        stamp=stamp,
        day=timestamp.date().isoformat(),
        acknowledged=max((t for t in thresholds + [multiple] if t <= value), default=0),
        blocked=False,
    )


def active_events(
    database, ctx, history, write, snapshot=None, reliable=False, config=None
):
    config = os.environ if config is None else config
    day = ctx.now_local.date()
    db = database.connection
    if (
        not write
        and not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='insight_state'"
        ).fetchone()
    ):
        return [], False
    with db:
        if write and not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT value FROM insight_state WHERE key='observations'"
        ).fetchone()
        state = json.loads(row[0]) if row else {}
        initial = state.get("installed", day.isoformat())

        def emit(ident, key, branch="default", **values):
            db.execute(
                "INSERT OR IGNORE INTO milestone_events VALUES (?,?,?,?,?,?)",
                (
                    ident + ":" + key,
                    day.isoformat(),
                    ctx.now_local.astimezone(timezone.utc).isoformat(),
                    ident,
                    branch,
                    json.dumps(values),
                ),
            )

        if write:
            state["installed"] = initial
            try:
                start = date.fromisoformat(config.get("SOLAR_COMMISSIONED_DATE", ""))
            except ValueError:
                start = None
            if start and start < day:
                days = (day - start).days
                if days in EVENTS["OPERATING_DAY"]["thresholds"] or days % 1000 == 0:
                    emit(
                        "OPERATING_DAY",
                        str(days),
                        f"d{days}" if days in (100, 1000) else "default",
                        days=number(days, 0),
                    )
                anniversary = start.replace(
                    year=day.year,
                    day=min(start.day, calendar.monthrange(day.year, start.month)[1]),
                )
                years = day.year - start.year
                if years and day == anniversary:
                    emit(
                        "ANNIVERSARY",
                        str(years),
                        "first" if years == 1 else "later",
                        years=years,
                    )
            yesterday = day - DAY
            if state.get("checked_day") != day.isoformat() and yesterday in history:
                state["checked_day"] = day.isoformat()
                if initial < day.isoformat() and history[yesterday] > 100:
                    count = sum(v > 100 for v in history.values())
                    if count in EVENTS["HIGH_YIELD_COUNT"]["thresholds"]:
                        emit(
                            "HIGH_YIELD_COUNT",
                            str(count),
                            "c10" if count == 10 else "default",
                            count=count,
                        )
                    length = streak(history, yesterday)
                    start_day = day - length * DAY
                    previous_best = running = 0
                    last = None
                    for d in sorted(d for d in history if d < start_day):
                        running = (
                            (running + 1 if last == d - DAY else 1)
                            if history[d] > 100
                            else 0
                        )
                        previous_best = max(previous_best, running)
                        last = d
                    key = f"{start_day}:{length}"
                    if length >= 3 and length > previous_best:
                        emit("STREAK_RECORD", key, days=length)
                    elif length in EVENTS["HIGH_YIELD_STREAK"]["thresholds"]:
                        emit(
                            "HIGH_YIELD_STREAK",
                            key,
                            "d3" if length == 3 else "default",
                            days=length,
                        )
            if snapshot:
                instant = datetime.fromisoformat(snapshot.timestamp).astimezone(
                    ctx.now_local.tzinfo
                )
                lifetime(state, instant, snapshot.energy_total_kwh, emit, config)
            energy = ctx.today_energy_kwh
            if reliable and len(history) >= 2 and energy is not None:
                baseline = max(history.values())
                old = state.get("running", {})
                prior = old.get("energy") if old.get("day") == day.isoformat() else 0
                stamp = ctx.now_local.timestamp()
                midnight = ctx.now_local.replace(
                    hour=0, minute=0, second=0, microsecond=0, fold=0
                ).timestamp()
                prior_stamp = (
                    old.get("stamp", midnight)
                    if old.get("day") == day.isoformat()
                    else midnight
                )
                plausible = (
                    prior
                    <= energy
                    <= prior + 24 * max(0, stamp - prior_stamp) / 3600 + 0.1
                )
                if plausible:
                    if (
                        state.get("running") is not None
                        and prior <= baseline + 0.1 < energy
                    ):
                        emit(
                            "DAILY_RECORD",
                            day.isoformat(),
                            old_record=number(baseline),
                            energy=number(energy),
                        )
                    highest = max(baseline, state.get("barrier", 0))
                    crossed = [
                        t
                        for t in EVENTS["PRODUCTION_BARRIER"]["thresholds_kwh"]
                        if highest < t <= energy
                    ]
                    if crossed and state.get("running") is not None:
                        emit(
                            "PRODUCTION_BARRIER",
                            str(max(crossed)),
                            threshold=max(crossed),
                        )
                    state["barrier"] = max(highest, energy)
                    state["running"] = dict(
                        day=day.isoformat(), energy=energy, stamp=stamp
                    )
                    db.execute(
                        "UPDATE milestone_events SET values_json=json_set(values_json,'$.energy',?) "
                        "WHERE event_key=?",
                        (number(energy), "DAILY_RECORD:" + day.isoformat()),
                    )
            encoded = json.dumps(state)
            if not row or row[0] != encoded:
                db.execute(
                    "INSERT OR REPLACE INTO insight_state VALUES ('observations',?)",
                    (encoded,),
                )
        events = list(
            db.execute(
                "SELECT * FROM milestone_events WHERE local_day=? ORDER BY triggered_utc,event_key",
                (day.isoformat(),),
            )
        )
    candidates = [
        Candidate(
            r["fact_id"],
            "milestone",
            EVENTS[r["fact_id"]],
            json.loads(r["values_json"]),
            r["branch"],
            r["event_key"],
            {"trigger": r["triggered_utc"]},
        )
        for r in events
    ]
    barrier = max(
        (c.values["threshold"] for c in candidates if c.ident == "PRODUCTION_BARRIER"),
        default=0,
    )
    return [
        c
        for c in candidates
        if c.ident != "PRODUCTION_BARRIER" or c.values["threshold"] == barrier
    ], initial < day.isoformat()
