"""Historical stories from one ordered set of qualified Zurich calendar days."""

from datetime import timedelta
from statistics import mean

from .editorial import Candidate, HISTORY, number

DAY = timedelta(days=1)
MONTH = "– Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split()


def month_label(day, today):
    return MONTH[day.month] + (f" {day.year}" if day.year != today.year else "")


def streak(values, end):
    days = 0
    while values.get(end - days * DAY, 0) > 100:
        days += 1
    return days


def history_candidates(values, today):
    if not values:
        return []
    dates = sorted(values)
    latest = dates[-1]
    yesterday = today - DAY
    results = []

    def add(
        ident,
        branch="default",
        ref=latest,
        previous=None,
        baseline=None,
        comparison=None,
        pct=None,
        n=None,
        **values,
    ):
        ident = "HIST_" + ident
        date = f"{ref.day}. {month_label(ref, today)}"
        values.update(
            date=date, day_label="Gestern" if ref == yesterday else "Am " + date
        )
        meta = dict(
            reference_day=ref.isoformat(),
            comparison_day=previous.isoformat() if previous else None,
            baseline_days=n,
            baseline_kwh=baseline,
            comparison_value_kwh=comparison,
            difference_percent=pct,
            direction=branch if branch in ("higher", "lower", "similar") else "neutral",
            reference_period="yesterday" if ref == yesterday else "historical",
        )
        if n:
            values["n"] = n
        formatted = {
            k: number(v) if isinstance(v, float) else v for k, v in values.items()
        }
        results.append(
            Candidate(
                ident,
                "history",
                HISTORY[ident],
                formatted,
                branch,
                ref.isoformat(),
                meta,
            )
        )

    def compare(ident, energy, baseline, previous=None, n=None):
        pct = 100 * (energy - baseline) / baseline if baseline >= 0.1 else None
        branch = (
            "absolute"
            if pct is None
            else "similar"
            if abs(pct) < 5
            else "higher"
            if pct > 0
            else "lower"
        )
        if ident == "3DAY_TREND" and (
            pct is None or abs(pct) < 10 or abs(energy - baseline) < 2
        ):
            return
        if branch in HISTORY["HIST_" + ident]["variants"]:
            add(
                ident,
                branch,
                previous=previous,
                baseline=baseline,
                comparison=energy,
                pct=pct,
                n=n,
                energy=float(energy),
                delta=float(abs(energy - baseline)),
            )
            results[-1].values.update(
                baseline=number(baseline), pct=number(abs(pct or 0), 0)
            )

    if latest == yesterday:
        for offset, ident in ((1, "YESTERDAY"), (7, "WEEKDAY")):
            prior = latest - offset * DAY
            if prior in values:
                compare(ident, values[latest], values[prior], prior, 1)
    previous = dates[-8:-1]
    if len(previous) >= 2:
        compare(
            "AVERAGE",
            values[latest],
            mean(values[d] for d in previous),
            n=len(previous),
        )
    series = [values[d] for d in dates]
    if len(series) >= 6:
        compare("3DAY_TREND", mean(series[-3:]), mean(series[-6:-3]), n=3)
    for size in (7, 30):
        if len(series) >= size:
            window = series[-size:]
            add(
                f"{size}DAY_TOTAL",
                energy=float(sum(window)),
                average=float(mean(window)),
            )
    first = today.replace(day=1)
    monday = today - DAY * today.weekday()
    for ident, start, end in (
        ("LAST_WEEK", monday - 7 * DAY, monday),
        ("LAST_MONTH", (first - DAY).replace(day=1), first),
        ("MONTH_TO_DATE", first, today),
    ):
        window = [values.get(start + i * DAY) for i in range((end - start).days)]
        if len(window) >= 5 and None not in window:
            add(
                ident,
                ref=end - DAY,
                energy=float(sum(window)),
                average=float(mean(window)),
                month=month_label(start, today),
            )
    if len(series) >= 7:
        week = series[-7:]
        ranks = [round(v, 1) for v in week]
        rank = 1 + sum(v > ranks[-1] for v in ranks)
        if ranks.count(ranks[-1]) == 1 and rank in (1, 2, 3, 6, 7):
            add(
                "RECENT_RANK",
                "high" if rank <= 3 else "low",
                rank=rank,
                energy=float(week[-1]),
            )
        if ranks.count(max(ranks)) == 1:
            best = ranks.index(max(ranks))
            add("RECENT_BEST", ref=dates[-7:][best], energy=float(week[best]))
        delta = max(week) - min(week)
        if delta >= 10:
            add(
                "RECENT_RANGE",
                low=float(min(week)),
                high=float(max(week)),
                delta=float(delta),
            )
        for threshold in HISTORY["HIST_THRESHOLD_COUNT"]["thresholds_kwh"]:
            count = sum(v > threshold for v in week)
            if 2 <= count <= 6 or (count == 7 and threshold >= 50):
                add("THRESHOLD_COUNT", count=count, threshold=threshold)
                break
        count = sum(v < 10 for v in week)
        if count >= 2:
            add("LOW_YIELD", count=count)
    high = max(series)
    if len(series) >= 2 and sum(round(v, 1) == round(high, 1) for v in series) == 1:
        best = dates[series.index(high)]
        if (today - best).days >= 3:
            add("RECORD_AGE", ref=best, days=(today - best).days, energy=float(high))
    if latest == yesterday:
        ended = values[latest] <= 100
        length = streak(values, latest - DAY if ended else latest)
        if length >= 3:
            add(
                "STREAK_END" if ended else "STREAK",
                days=length,
                energy=float(values[latest]),
            )
    return results
