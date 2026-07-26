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
    if not started:
        if now < sunrise + timedelta(hours=2):
            return "morning_waiting", "yesterday"
        return "zero_production_day", None
    if sunset is None or now <= sunset:
        return "active_production", "today"
    return "after_sunset", "today"


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
        if context.weather_code in (3, 45, 48):
            return "ZERO_WEATHER", ("Der Morgen ist stark bewölkt.",
                                    "Auf dem Produktionszähler stehen noch 0,0 kWh.")
        return "ZERO", ("Die Produktion ist heute noch nicht angelaufen.",
                        "Die Panels könnten etwas mehr Helligkeit vertragen.")
    return None


def build_story_from_context(context):
    phase, period = determine_phase(context)
    energy = context.today_energy_kwh if period == "today" else context.yesterday_energy_kwh
    special = _special(context, phase, period, energy)
    if special:
        fact_id, lines = special
        return _story(fact_id, "status", lines, phase, period, energy)
    if energy is not None and energy >= .1:
        rendered = [(fact, _render(fact, energy, "heutigen" if period == "today" else "gestrigen"))
                    for fact in FACTS if fact.min_kwh <= energy <= fact.max_kwh]
        rendered = [(fact, text) for fact, text in rendered if text is not None]
        # Build the hours up to now as one deterministic plan. This makes daily
        # uniqueness and adjacent-family exclusion independent of process state.
        used, previous, selected = set(), None, None
        for hour in range(context.now_local.hour + 1):
            choices = [(fact, text) for fact, text in rendered
                       if fact.fact_id not in used and fact.family != previous]
            if not choices:
                break
            seed = f"{context.now_local.date().isoformat()}:{hour:02d}:{phase}:{round(energy, 1):.1f}"
            total = sum(f.weight for f, _ in choices)
            point = (_digest(seed) / (1 << 256)) * total
            selected = choices[-1]
            for choice in choices:
                point -= choice[0].weight
                if point < 0:
                    selected = choice
                    break
            used.add(selected[0].fact_id)
            previous = selected[0].family
        if selected:
            fact, (lines, short) = selected
            return _story(fact.fact_id, fact.family, lines, phase, period, energy, short)
    seed = f"{context.now_local.date()}:{context.now_local.hour:02d}:{phase}:0.0"
    lines = FALLBACKS[_digest(seed) % len(FALLBACKS)]
    return _story("TECH", "technical", lines, phase, period, energy)


def _story(fact_id, family, lines, phase, period, energy, short=False):
    return Story(fact_id, family, lines[0], lines[1], phase, period, energy, short,
                 text_width_px(lines[0]), text_width_px(lines[1]))
