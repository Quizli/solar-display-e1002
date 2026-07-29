import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Mapping, Optional

from .engine import determine_phase
from .formatting import format_fact_number
from .models import Story
from .width import both_lines_fit, text_width_px


HISTORICAL_IDS = ("HIST_RECORD", "HIST_YESTERDAY", "HIST_AVERAGE")
MINIMUM_KWH = 0.1


@dataclass(frozen=True)
class HistoricalCandidate:
    fact_id: str
    context: Mapping[str, object]

    @property
    def context_json(self):
        return json.dumps(self.context, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))


def _yield(database, day):
    item = database.daily_yield_from_aggregates(day)
    return item.energy_kwh if item else None


def _record_yields(database, before):
    method = getattr(database, "completed_record_daily_yields", None)
    return method(before) if method else database.completed_daily_yields(before)


def eligible_historical_candidates(database, context):
    phase, period = determine_phase(context)
    today = context.now_local.date()
    result = []
    earlier = _record_yields(database, today)
    if period == "today" and len(earlier) >= 2 and context.today_energy_kwh is not None:
        prior_record = max(item.energy_kwh for item in earlier)
        if context.today_energy_kwh > prior_record + MINIMUM_KWH:
            record_day = max(item.local_day for item in earlier
                             if item.energy_kwh == prior_record)
            result.append(HistoricalCandidate("HIST_RECORD", {
                "reference_day": record_day.isoformat(),
                "baseline_energy_kwh": prior_record,
                "period": "today",
            }))

    yesterday = today - timedelta(days=1)
    yesterday_item = next((item for item in earlier
                           if item.local_day == yesterday), None)
    yesterday_yield = yesterday_item.energy_kwh if yesterday_item else None
    older = [item for item in earlier if item.local_day < yesterday]
    if yesterday_yield is not None and len(older) >= 2:
        prior_record = max(item.energy_kwh for item in older)
        if yesterday_yield > prior_record + MINIMUM_KWH:
            record_day = max(item.local_day for item in older
                             if item.energy_kwh == prior_record)
            result.append(HistoricalCandidate("HIST_RECORD", {
                "reference_day": yesterday.isoformat(),
                "previous_record_day": record_day.isoformat(),
                "baseline_energy_kwh": prior_record,
                "comparison_energy_kwh": yesterday_yield,
                "period": "yesterday",
            }))

    selected_day = None
    label = None
    if phase == "after_sunset":
        selected_day, label = today, "today"
    elif phase in ("pre_sunrise", "morning_waiting"):
        selected_day, label = today - timedelta(days=1), "yesterday"
    if selected_day is None:
        return result
    selected = (context.today_energy_kwh if selected_day == today else
                _yield(database, selected_day))
    previous_day = selected_day - timedelta(days=1)
    previous = _yield(database, previous_day)
    if selected is not None and previous is not None and min(selected, previous) >= MINIMUM_KWH:
        result.append(HistoricalCandidate("HIST_YESTERDAY", {
            "reference_day": selected_day.isoformat(), "period": label,
            "comparison_day": previous_day.isoformat(),
            "comparison_energy_kwh": selected,
            "baseline_energy_kwh": previous,
        }))
    baselines = database.completed_daily_yields(selected_day, limit=7)
    if selected is not None and selected >= MINIMUM_KWH and len(baselines) >= 3:
        average = sum(item.energy_kwh for item in baselines) / len(baselines)
        if average >= MINIMUM_KWH:
            result.append(HistoricalCandidate("HIST_AVERAGE", {
                "reference_day": selected_day.isoformat(), "period": label,
                "comparison_energy_kwh": selected,
                "baseline_energy_kwh": average, "baseline_days": len(baselines),
            }))
    return result


def historical_difference(value, baseline):
    difference_percent = (value - baseline) / baseline * 100
    direction = ("similar" if abs(difference_percent) < 5 else
                 "higher" if difference_percent > 0 else "lower")
    return difference_percent, direction


def _comparison_line(value, baseline, similar, suffix):
    difference, direction = historical_difference(value, baseline)
    if direction == "similar":
        return similar
    comparison = "mehr" if direction == "higher" else "weniger"
    return f"Das sind {round(abs(difference))} % {comparison} {suffix}."


def render_historical(fact_id, context, payload) -> Optional[Story]:
    try:
        baseline = float(payload["baseline_energy_kwh"])
        reference_day = str(payload["reference_day"])
        phase, period = determine_phase(context)
        if baseline < MINIMUM_KWH:
            return None
        if fact_id == "HIST_RECORD":
            if payload.get("period") == "yesterday":
                value = float(payload["comparison_energy_kwh"])
                lines = (f"Gestern entstanden {format_fact_number(value, 'energy')} kWh Solarstrom.",
                         "Neuer Tagesrekord seit Beginn der Aufzeichnung.")
            else:
                value = context.today_energy_kwh
                if value is None:
                    return None
                lines = (f"Heute sind bereits {format_fact_number(value, 'energy')} kWh Solarstrom entstanden.",
                         f"Der bisherige Rekord lag bei {format_fact_number(baseline, 'energy')} kWh.")
        else:
            value = float(payload["comparison_energy_kwh"])
            morning = payload.get("period") == "yesterday"
            if fact_id == "HIST_YESTERDAY":
                lines = (("Gestern" if morning else "Heute") +
                         f" kamen {format_fact_number(value, 'energy')} kWh vom Dach.",
                         _comparison_line(value, baseline,
                                          "Das ist fast gleich viel wie am Vortag." if morning else
                                          "Das ist fast gleich viel wie gestern.",
                                          "als am Vortag" if morning else "als gestern"))
            elif fact_id == "HIST_AVERAGE":
                lines = (f"Der letzte Solartag brachte {format_fact_number(value, 'energy')} kWh.",
                         _comparison_line(value, baseline,
                                          "Das liegt fast genau im bisherigen Schnitt.",
                                          "als im Schnitt"))
            else:
                return None
        if not both_lines_fit(lines, 510):
            return None
        return Story(fact_id, "history", lines[0], lines[1], phase, period, value,
                     line_1_width_px=text_width_px(lines[0]),
                     line_2_width_px=text_width_px(lines[1]),
                     historical_fact=True, historical_reference_day=reference_day,
                     historical_baseline_kwh=baseline)
    except (KeyError, TypeError, ValueError):
        return None


def decode_and_render(fact_id, context, context_json):
    if fact_id not in HISTORICAL_IDS or not context_json:
        return None
    try:
        payload = json.loads(context_json)
        if not isinstance(payload, dict):
            return None
    except (TypeError, json.JSONDecodeError):
        return None
    return render_historical(fact_id, context, payload)
