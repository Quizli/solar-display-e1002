import hashlib
from datetime import timedelta

from .catalog import FACTS
from .formatting import format_fact_number
from .models import FactContext, Story
from .width import both_lines_fit, text_width_px

FALLBACKS = (
    ("Die Messwerte werden alle zehn Sekunden abgefragt.", "Der Tageschart verwendet abgeschlossene Fünf-Minuten-Werte."),
    ("Der Tageschart endet absichtlich an der Gegenwart.", "Die Zukunft bleibt vorerst leer."),
    ("Die Sonne wurde nicht direkt an das System angeschlossen.", "Sonnenaufgang und Sonnenuntergang kennt es trotzdem."),
)


def determine_phase(context):
    started = ((context.today_energy_kwh is not None and context.today_energy_kwh >= .1) or
               (context.solar_power_kw is not None and context.solar_power_kw >= .1))
    now, sunrise, sunset = context.now_local, context.sunrise, context.sunset
    if sunrise is None:
        if now.hour < 6:
            return "pre_sunrise", "yesterday"
        if now.hour >= 22:
            return "after_sunset", "today"
        if started:
            return "active_production", "today"
        return "morning_waiting", "yesterday"
    if now < sunrise:
        return "pre_sunrise", "yesterday"
    if sunset is not None and now > sunset:
        return "after_sunset", "today"
    if not started:
        if now < sunrise + timedelta(hours=2):
            return "morning_waiting", "yesterday"
        return "zero_production_day", None
    return "active_production", "today"


def _digest(text):
    return int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")


def _render(fact, energy, period):
    normal = fact.render(energy, period)
    if both_lines_fit(normal, 490):
        return normal, False
    short = fact.render_short(energy, period) if fact.render_short else normal
    if both_lines_fit(short, 510):
        return short, True
    return None


def _special(context, phase, period, energy):
    seed = f"{context.now_local.date()}:{context.now_local.hour:02d}:{phase}:0.0"
    if phase == "after_sunset" and (energy is None or energy < .1):
        return "ZERO_DAY_COMPLETE", (
            "Der heutige Tag endet ohne messbare Solarproduktion.",
            "Morgen beginnt der Zähler wieder bei null.",
        )
    if phase == "morning_waiting":
        options = []
        if energy is not None and energy >= .1:
            options.append(("Die Anlage wartet noch auf genügend Licht.",
                            f"Gestern kamen insgesamt {format_fact_number(energy, 'energy')} kWh vom Dach."))
        if context.sunrise is not None:
            options.append((f"Sonnenaufgang war heute um {context.sunrise.strftime('%H:%M')} Uhr.",
                            "Die Produktion hat heute noch nicht begonnen."))
        if options:
            return "MORNING", options[_digest(seed) % len(options)]
    if phase == "zero_production_day":
        if context.weather_code == 3:
            return "ZERO_WEATHER", ("Der Morgen ist stark bewölkt.",
                                    "Auf dem Produktionszähler stehen noch 0,0 kWh.")
        if context.weather_code in (45, 48):
            return "ZERO_WEATHER", ("Der Morgen startet heute neblig.",
                                    "Auf dem Produktionszähler stehen noch 0,0 kWh.")
        return "ZERO", ("Die Produktion ist heute noch nicht angelaufen.",
                        "Die Panels könnten etwas mehr Helligkeit vertragen.")
    return None


def _ordered_facts(local_date, hour):
    """Return a fair, fully hashed order independent of energy ranges."""
    date_text = local_date.isoformat()
    return sorted(FACTS, key=lambda fact: _digest(
        f"{date_text}:{hour:02d}:{fact.fact_id}"
    ))


def select_fact_for_hour(context, hour, selection_energy, display_energy,
                         excluded_family=None, excluded_id=None):
    if selection_energy is None or selection_energy < .1 or display_energy is None:
        return None
    period = "heutigen" if determine_phase(context)[1] == "today" else "gestrigen"
    candidates = []
    for fact in _ordered_facts(context.now_local.date(), hour):
        if not fact.min_kwh <= selection_energy <= fact.max_kwh:
            continue
        rendered = _render(fact, display_energy, period)
        if rendered is not None:
            candidates.append((fact, rendered))
    alternatives = [(fact, rendered) for fact, rendered in candidates
                    if fact.family != excluded_family and fact.fact_id != excluded_id]
    if alternatives:
        return alternatives[0]
    return candidates[0] if candidates and excluded_family is None and excluded_id is None else None


def build_story_from_context(context):
    phase, period = determine_phase(context)
    energy = context.today_energy_kwh if period == "today" else context.yesterday_energy_kwh
    special = _special(context, phase, period, energy)
    if special:
        fact_id, lines = special
        return _story(fact_id, "status", lines, phase, period, energy)
    if energy is not None and energy >= .1:
        hour = context.now_local.hour
        selection_energy = (context.selection_energy_kwh if period == "today"
                            else energy)
        previous_energy = (context.previous_hour_selection_energy_kwh
                           if period == "today" else energy)
        previous = select_fact_for_hour(
            context, (hour - 1) % 24, previous_energy, previous_energy
        )
        selected = select_fact_for_hour(
            context, hour, selection_energy, energy,
            excluded_family=previous[0].family if previous else None,
            excluded_id=previous[0].fact_id if previous else None,
        )
        if selected:
            fact, (lines, short) = selected
            return _story(fact.fact_id, fact.family, lines, phase, period, energy, short)
    seed = f"{context.now_local.date()}:{context.now_local.hour:02d}:{phase}:0.0"
    lines = FALLBACKS[_digest(seed) % len(FALLBACKS)]
    return _story("TECH", "technical", lines, phase, period, energy)


def _story(fact_id, family, lines, phase, period, energy, short=False):
    return Story(fact_id, family, lines[0], lines[1], phase, period, energy, short,
                 text_width_px(lines[0]), text_width_px(lines[1]))
