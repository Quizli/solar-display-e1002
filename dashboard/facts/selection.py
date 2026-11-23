"""Select and persist independent shared-insight and web-history slots."""

import json
from datetime import datetime, timedelta, timezone

from .calendar_rules import calendar_candidates
from .editorial import (
    ALIASES,
    ENERGY,
    MORNING,
    digest,
    energy_candidate,
    render,
)
from .engine import hour_key
from .models import Story
from .mornings import morning_candidates
from .width import text_width_px


def phase_for(ctx):
    started = (ctx.today_energy_kwh or 0) >= 0.1 or (ctx.solar_power_kw or 0) >= 0.1
    now = ctx.now_local
    after_sunset = now >= ctx.sunset if ctx.sunset else now.hour >= 22
    if after_sunset:
        return "after_sunset"
    if started:
        return "active_production"
    before_sunrise = now < ctx.sunrise if ctx.sunrise else now.hour < 6
    if before_sunrise:
        return "pre_sunrise"
    if ctx.sunrise and now.timestamp() - ctx.sunrise.timestamp() >= 5400:
        return "zero_production_day"
    return "morning_waiting"


def stored_match(record, pool):
    if not record:
        return None
    ident = ALIASES.get(record.fact_id, record.fact_id)
    try:
        data = json.loads(record.context_json or "{}")
        for c in pool:
            if (
                ident == c.ident
                and c.branch == data.get("branch", "default")
                and c.reference == data.get("reference", c.reference)
            ):
                index = data.get("variant")
                return c, index if type(index) is int and index >= 0 else None
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def pick(db, ctx, slot, pool, write, anchor=None, bucket=None):
    if not pool:
        return None
    record = db.latest_slot_revision(slot)
    match = stored_match(record, pool)
    choices = ([match] if match else []) + [(c, None) for c in pool]
    for candidate, preferred in choices:
        seed = candidate.reference if candidate.family == "milestone" else slot
        result = render(candidate, seed, preferred)
        if result is None:
            continue
        index, lines = result
        if match and candidate is match[0] and preferred in (None, index):
            return candidate, index, lines, record
        if not write:
            return candidate, index, lines, None
        # Concurrent refreshes replacing the same revision contend on one key.
        revision = slot if not record else f"{slot}:v2:{digest(record.hour_key)}"
        data = dict(
            variant=index, branch=candidate.branch, reference=candidate.reference
        )
        winner = db.store_fact_selection(
            revision,
            ctx.now_local,
            candidate.ident,
            candidate.family,
            anchor,
            bucket,
            created_at=ctx.now_local,
            context_json=json.dumps(data),
        )
        persisted = stored_match(winner, pool)
        if persisted:
            candidate, index = persisted
            rendered = render(candidate, seed, index)
            if rendered:
                index, lines = rendered
                return candidate, index, lines, winner
        # A concurrent publisher had different input. Return this evaluation's
        # truthful text; the following refresh can revise the winning selection.
        return candidate, index, lines, None


def choose_insight(db, ctx, history, values, events, afterglow, write):
    now = ctx.now_local
    phase = phase_for(ctx)
    hour = now.replace(minute=0, second=0, microsecond=0)
    key = hour_key(hour)
    morning = phase not in ("active_production", "after_sunset")
    rows = db.fact_selections_for_local_day(now.date())
    morning_slot = morning and now.hour < 12
    special_slot = key + (":morning" if morning_slot else ":day")

    def available(c):
        return not any(
            (r.fact_id == c.ident or (r.family == c.family == "calendar"))
            and not r.hour_key.startswith(key + ":")
            and (
                c.row.get("phase") == "morning"
                or r.hour_key.split(":v2:")[0].endswith(special_slot.split(":")[-1])
            )
            for r in rows
        )

    anchor = ctx.selection_energy_kwh
    bucket = ctx.selection_bucket_by_hour.get(key)
    source = "new"
    if events:
        ordered = sorted(events, key=lambda c: (-c.row["priority"], c.reference))
        first = ordered[0]
        elapsed = max(
            0,
            int(hour.timestamp() // 3600)
            - int(datetime.fromisoformat(first.meta["trigger"]).timestamp() // 3600),
        )
        offset = elapsed % len(ordered)
        pool = ordered[offset:] + ordered[:offset]
        slot = key + ":event:" + first.reference
    else:
        calendar = sorted(
            (c for c in calendar_candidates(now.date(), morning_slot) if available(c)),
            key=lambda c: (-c.row["priority"], c.ident),
        )
        special = [
            c for c in calendar if c.row["phase"] == "day" and phase != "after_sunset"
        ]
        pool = (
            morning_candidates(ctx, history, values, phase, afterglow and morning_slot)
            if morning
            or (
                phase == "after_sunset"
                and ctx.today_energy_kwh is not None
                and ctx.today_energy_kwh < 0.1
            )
            else []
        )
        after = [c for c in pool if c.ident == "RECORD_AFTERGLOW" and available(c)]
        if after or special:
            pool = after + special
            slot = special_slot
        elif pool:
            pool = [c for c in pool if c.ident != "RECORD_AFTERGLOW"] + [
                c for c in calendar if c.row["phase"] == "morning"
            ]
            recent = db.recent_catalog_fact_selections(tuple(MORNING), 24)
            previous = next(
                (r for r in recent if r.hour_key != key + ":" + phase), None
            )

            def order(c):
                priority = c.row.get("priority", 30)
                return (
                    priority <= 30,
                    previous is not None and previous.fact_id == c.ident,
                    sum(r.fact_id == c.ident for r in recent[:8]),
                    digest(key, c.ident, c.branch) % 100 - priority,
                )

            pool.sort(key=order)
            slot = key + ":" + phase
        else:
            slot = key
            previous_hour = (
                hour.astimezone(timezone.utc) - timedelta(hours=1)
            ).astimezone(now.tzinfo)
            previous = db.latest_slot_revision(hour_key(previous_hour))
            if (
                anchor is None
                and previous
                and previous_hour.date() == now.date()
                and now.minute < 10
            ):
                ident = ALIASES.get(previous.fact_id, previous.fact_id)
                if ident in ENERGY:
                    c = energy_candidate(
                        ENERGY[ident],
                        previous.selection_energy_kwh,
                        ctx.today_energy_kwh,
                    )
                    if c:
                        slot, anchor, bucket = (
                            hour_key(previous_hour),
                            previous.selection_energy_kwh,
                            previous.selection_bucket_start,
                        )
                        source = "previous_hour_bridge"
            stored = db.latest_slot_revision(slot)
            if (
                stored
                and source == "new"
                and ALIASES.get(stored.fact_id, stored.fact_id) in ENERGY
            ):
                source = "persisted"
            if (
                anchor is None
                and not stored
                and any(ALIASES.get(r.fact_id, r.fact_id) in ENERGY for r in rows)
            ):
                # Between anchors a provisional equivalent may update, but cannot
                # take the slot away from the next completed five-minute bucket.
                write = False
            anchor = (
                stored.selection_energy_kwh
                if stored
                and ALIASES.get(stored.fact_id, stored.fact_id) in ENERGY
                and stored.selection_energy_kwh is not None
                else anchor
                if anchor is not None
                else ctx.today_energy_kwh
            )
            recent = db.recent_catalog_fact_selections(
                tuple(ENERGY) + tuple(ALIASES), 96
            )
            ids = [ALIASES.get(r.fact_id, r.fact_id) for r in recent]
            used = {ALIASES.get(r.fact_id, r.fact_id) for r in rows}
            previous_family = ENERGY[ids[0]]["family"] if ids else None

            def rank(row):
                ident = row["id"]
                distance = ids.index(ident) if ident in ids else len(ids) + 1
                return (
                    distance == 0,
                    ident in used,
                    distance < 6,
                    distance < 12,
                    distance < 24,
                    row["family"] == previous_family,
                    -distance,
                    digest(key, ident),
                )

            pool = [
                c
                for row in sorted(ENERGY.values(), key=rank)
                for c in [energy_candidate(row, anchor, ctx.today_energy_kwh)]
                if c
            ]
            if not pool:
                # At first light PV can cross 0.1 kW before any equivalent applies.
                # Give qualified history one separate slot; leave the energy slot free.
                slot = key + ":shared-history"
                used_history = any(
                    ":shared-history" in r.hour_key and not r.hour_key.startswith(slot)
                    for r in rows
                )
                if not used_history:
                    pool = sorted(
                        history, key=lambda c: digest(slot, c.ident, c.reference)
                    )
    selected = pick(db, ctx, slot, pool, write, anchor, bucket)
    if selected is None:
        return Story(None, None, "", "", phase, None, ctx.today_energy_kwh)
    c, index, lines, record = selected
    return Story(
        c.ident,
        c.family,
        *lines,
        phase,
        "today" if c.ident in ENERGY else None,
        ctx.today_energy_kwh,
        line_1_width_px=text_width_px(lines[0]),
        line_2_width_px=text_width_px(lines[1]),
        variant=index,
        branch=c.branch,
        semantic=c.semantic,
        event_key=c.reference if events else None,
        selection_hour=record.local_hour if record else hour.isoformat(),
        selection_source=source,
        selection_persisted=bool(record),
        selection_energy_kwh=anchor,
        previous_hour_selection_energy_kwh=ctx.previous_hour_selection_energy_kwh,
        selection_bucket_start=bucket,
    )


def choose_history(db, ctx, history, story, write):
    now = ctx.now_local
    key = f"{now.date()}:history:{now.hour // 3}"
    recent = db.recent_catalog_fact_selections(tuple(c.ident for c in history), 3)
    pool = sorted(
        (c for c in history if c.semantic != story.semantic),
        key=lambda c: (
            any(r.fact_id == c.ident and r.hour_key != key for r in recent),
            digest(key, c.ident),
        ),
    )
    selected = pick(db, ctx, key, pool, write)
    if selected:
        c, index, lines, _ = selected
        return dict(type=c.ident, statement=" ".join(filter(None, lines)), **c.meta)
