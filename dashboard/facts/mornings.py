from datetime import datetime
from dashboard.weather import weather_icon_variant
from .calendar_rules import SEASONS
from .editorial import Candidate, MORNING, number
from .statistics import DAY


def daylight_duration(seconds):
    hours, minutes = divmod(int(seconds / 60 + 0.5), 60)
    parts = []
    if hours:
        parts.append(f"{hours} Stunde" + ("n" if hours != 1 else ""))
    if minutes or not hours:
        parts.append(f"{minutes} Minute" + ("n" if minutes != 1 else ""))
    return " ".join(parts)


def technical_context(ctx):
    return Candidate(
        "TECH_CONTEXT",
        "morning",
        MORNING["TECH_CONTEXT"],
        branch="default" if ctx.has_buckets else "empty_chart",
        reference=ctx.now_local.date().isoformat(),
    )


def morning_candidates(ctx, history, values, phase, afterglow):
    now = ctx.now_local
    day = now.date()
    result = []

    def add(ident, branch="default", **values):
        result.append(
            Candidate(ident, "morning", MORNING[ident], values, branch, day.isoformat())
        )

    if phase == "after_sunset":
        if (
            ctx.complete_yield
            and ctx.today_energy_kwh is not None
            and 0 <= ctx.today_energy_kwh < 0.1
        ):
            add("ZERO_DAY_COMPLETE")
        else:
            result.append(technical_context(ctx))
        return result
    yesterday = day - DAY
    if yesterday in values:
        energy = number(values[yesterday])
        add("YESTERDAY_RECAP", "neutral", energy=energy)
        older = [v for d, v in values.items() if d < yesterday]
        if afterglow and len(older) >= 2 and values[yesterday] > max(older) + 0.1:
            add("RECORD_AFTERGLOW", energy=energy, old_record=number(max(older)))
    for item in history:
        if item.ident == "HIST_YESTERDAY" and item.branch != "absolute":
            add("YESTERDAY_RECAP", item.branch, **item.values)
        elif (
            item.ident == "HIST_RECENT_RANK" and item.reference == yesterday.isoformat()
        ):
            add("YESTERDAY_RANK", item.branch, **item.values)
        elif item.ident == "HIST_3DAY_TREND":
            add("RECENT_TREND", item.branch, **item.values)
        elif item.ident == "HIST_STREAK" or (
            item.ident == "HIST_STREAK_END" and values[yesterday] < 100
        ):
            add(
                "STREAK_RECAP",
                "running" if item.ident == "HIST_STREAK" else "ended",
                **item.values,
            )
    sunrise = ctx.sunrise
    if sunrise and ctx.sunset:
        seconds = ctx.sunset.timestamp() - sunrise.timestamp()
        if seconds > 0:
            add(
                "DAYLIGHT",
                duration=daylight_duration(seconds),
                sunrise=sunrise.strftime("%H:%M"),
                sunset=ctx.sunset.strftime("%H:%M"),
            )
            if ctx.previous_daylight_seconds:
                change = seconds - ctx.previous_daylight_seconds
                minutes = round(abs(change) / 60)
                direction = "longer" if change > 0 else "shorter"
                if minutes:
                    add(
                        "DAYLIGHT_CHANGE",
                        direction,
                        delta_minutes=f"{minutes} Minute"
                        + ("n" if minutes != 1 else ""),
                    )
                if change:
                    near = any(
                        abs(
                            (
                                day
                                - datetime.strptime(
                                    f"{day.year}-{d}", "%Y-%m-%d"
                                ).date()
                            ).days
                        )
                        <= 7
                        for d in SEASONS.get(day.year, ())[1::2]
                    )
                    add("SEASONAL_DIRECTION", "near_solstice" if near else direction)
    if ctx.sunshine_hours is not None:
        branch = ("very_low", "low", "medium", "high", "very_high")[
            sum(ctx.sunshine_hours >= x for x in (2, 5, 8, 11))
        ]
        add("SUNSHINE_OUTLOOK", branch, sunshine=number(ctx.sunshine_hours))
    if ctx.weather_code is not None:
        add("WEATHER_OUTLOOK", weather_icon_variant(ctx.weather_code))
    if phase == "pre_sunrise" and (sunrise or ctx.today_energy_kwh is not None):
        add(
            "NIGHT_STATUS",
            "sunrise_known" if sunrise else "generic",
            sunrise=sunrise.strftime("%H:%M") if sunrise else "",
        )
    elif sunrise and now >= sunrise and ctx.today_energy_kwh is not None:
        add(
            "WAITING_STATUS",
            "late"
            if now.timestamp() - sunrise.timestamp() >= 5400
            else "sunrise_known",
            sunrise=sunrise.strftime("%H:%M"),
        )
    result.append(technical_context(ctx))
    return result
