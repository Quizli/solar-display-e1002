import argparse
from pathlib import Path
import json
import math
import re
import sys
from xml.sax.saxutils import escape

# scripts/render.sh also runs this file directly from a source checkout.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboard.facts.width import both_lines_fit, text_width_px

BLACK = "#000000"
WHITE = "#FFFFFF"
YELLOW = "#FFD400"
RED = "#E02020"
GREEN = "#149B24"
BLUE = "#0057B8"
# Visual calibration for the segmented bar; readings and the 24 kW chart are uncapped.
SOLAR_PROGRESS_MAX_KW = 18.0
SEGMENT_COUNT = 20
MISSING = "—"


# All badge artwork is renderer-owned, in a local 48 px square.
_BADGES = {
    "record": (YELLOW, f'<g fill="{BLACK}">'
               '<path d="M13 9h22v7c0 8-3 12-8 14v5h7v4H14v-4h7v-5c-5-2-8-6-8-14z"/>'
               '<path d="M14 13H7v4c0 6 4 10 11 10m16-14h7v4c0 6-4 10-11 10" '
               f'fill="none" stroke="{BLACK}" stroke-width="4" stroke-linejoin="round"/></g>'),
    "milestone": (GREEN, f'<polygon fill="{WHITE}" points="24,6 29,18 42,19 32,28 '
                         '35,41 24,34 13,41 16,28 6,19 19,18"/>'),
    "calendar": (BLUE, f'<rect x="7" y="12" width="34" height="29" rx="3" fill="{WHITE}"/>'
                       f'<path d="M14 7h5v9h-5z M29 7h5v9h-5z" fill="{WHITE}"/>'
                       f'<rect x="12" y="22" width="24" height="14" fill="{BLUE}"/>'),
    "warning": (RED, f'<rect x="20" y="7" width="8" height="22" rx="3" fill="{WHITE}"/>'
                     f'<circle cx="24" cy="37" r="4" fill="{WHITE}"/>'),
}


def badge_category(fact_id, family):
    if fact_id in ("STALE", "MISSING"):
        return "warning"
    if fact_id in ("DAILY_RECORD", "STREAK_RECORD", "RECORD_AFTERGLOW"):
        return "record"
    if fact_id and family in ("milestone", "calendar"):
        return family
    return None


def story_layout(data):
    line1, line2 = data.get("story_line_1", ""), data.get("story_line_2", "")
    category = badge_category(data.get("story_fact_id"), data.get("story_family"))
    if category:
        # Status stories have two independent fields: a timestamp and an instruction.
        if category == "warning" and line1 and line2 and both_lines_fit((line1, line2), 455):
            return line1, line2, 15, 354, category
        text = " ".join(f"{line1} {line2}".split())
        # Keep measured quantities together but allow a break after a standalone number.
        protected = {match.start() for match in re.finditer(
            r"(?<=\d) (?=(?:kWh|MWh|Wh|kW|W|kg|km|Uhr|Stunden?|Minuten?|Tage[n]?|Jahre[n]?|%)(?!\w))",
            text)}
        splits = [(text[:i], text[i + 1:]) for i, char in enumerate(text)
                  if char == " " and i not in protected]
        widths = [(text_width_px(a), text_width_px(b), a, b) for a, b in splits]
        full_width = text_width_px(text)
        for size in (15, 14, 13):
            limit = 455 * 15 / size  # the shared font metrics are measured at 15 px
            if text and full_width <= limit:
                return text, "", size, 365, category
            fits = [(abs(left - right), a, b) for left, right, a, b in widths
                    if max(left, right) <= limit]
            if fits:
                _, first, second = min(fits)
                return first, second, size, 354, category
    return line1, line2, 15, 354, None


def story_badge_svg(category):
    if category is None:
        return ""
    background, icon = _BADGES[category]
    return (f'<g data-story-badge="{category}" transform="translate(727 336)">'
            f'<rect width="48" height="48" rx="4" fill="{background}"/>{icon}</g>')


def weather_icon_svg(variant):
    """Render a bold weather mark at the frozen sunshine-icon position."""
    variants = {"sunny", "mainly_clear", "partly_cloudy", "overcast", "fog",
                "rain", "snow", "thunderstorm"}
    variant = variant if variant in variants else "sunny"
    sun = ('<circle cx="589" cy="36" r="8" fill="#FFD400" stroke="#000000" stroke-width="1.6"/>'
           '<path d="M589 19v5 M589 48v5 M572 36h5 M601 36h5 M577 24l4 4 M597 44l4 4 '
           'M577 48l4-4 M597 28l4-4" fill="none" stroke="#000000" stroke-width="1.6" '
           'stroke-linecap="round"/>')
    small_cloud = ('<path d="M584 45 C580 45 578 42 579 39 C580 36 583 35 586 37 '
                   'C588 32 596 33 597 39 C602 38 604 45 598 45 Z" fill="#FFFFFF" '
                   'stroke="#000000" stroke-width="1.7" stroke-linejoin="round"/>')
    large_cloud = ('<path d="M576 46 C571 46 569 42 571 38 C572 35 576 34 579 36 '
                   'C582 29 593 30 595 38 C602 36 606 46 598 46 Z" fill="#FFFFFF" '
                   'stroke="#000000" stroke-width="1.8" stroke-linejoin="round"/>')
    clouds = ('<path d="M574 44 C569 44 568 39 571 36 C574 33 578 34 580 37 '
              'C582 29 594 30 596 38 C604 36 608 47 598 47 H574 C568 47 568 44 574 44 Z" '
              'fill="#FFFFFF" stroke="#000000" stroke-width="1.8" stroke-linejoin="round"/>')
    if variant == "sunny": drawing = sun
    elif variant == "mainly_clear": drawing = sun + small_cloud
    elif variant == "partly_cloudy": drawing = sun + large_cloud
    elif variant == "overcast": drawing = clouds
    elif variant == "fog":
        drawing = clouds + '<path d="M572 51h32 M576 56h24" fill="none" stroke="#000000" stroke-width="1.8"/>'
    elif variant == "rain":
        drawing = clouds + '<path d="M578 51l-2 5 M588 51l-2 5 M598 51l-2 5" stroke="#0057B8" stroke-width="2"/>'
    elif variant == "snow":
        drawing = clouds + '<path d="M578 51v6 M575 54h6 M588 51v6 M585 54h6 M598 51v6 M595 54h6" stroke="#0057B8" stroke-width="1.4"/>'
    else:
        drawing = clouds + '<path d="M591 48l-6 8h5l-3 7 10-11h-5l3-4z" fill="#FFD400" stroke="#000000" stroke-width="1.2"/>'
    return f'<g data-weather-icon="{variant}">{drawing}</g>'


def round_half_up(value):
    return int(math.floor(value + 0.5))


def clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def format_1(value):
    return f"{value:.1f}"


def segment_count(value, maximum):
    value = clamp(value, 0.0, maximum)
    return max(0, min(SEGMENT_COUNT, round_half_up((value / maximum) * SEGMENT_COUNT)))


def down_arrow(y_top, color):
    return (f'<polygon points="218,{y_top} 222,{y_top} 222,{y_top + 9} 226,{y_top + 9} '
            f'220,{y_top + 16} 214,{y_top + 9} 218,{y_top + 9}" fill="{color}"/>')


def up_arrow(y_top, color):
    return (f'<polygon points="218,{y_top + 16} 222,{y_top + 16} 222,{y_top + 7} 226,{y_top + 7} '
            f'220,{y_top} 214,{y_top + 7} 218,{y_top + 7}" fill="{color}"/>')


def _number(data, name):
    value = data.get(name)
    if value is None or value == MISSING:
        return None
    return float(value)


def _optional(value, suffix="", decimals=None):
    if value is None or value == MISSING:
        return MISSING
    if decimals is not None:
        return f"{float(value):.{decimals}f}{suffix}"
    return f"{value}{suffix}"


def format_day_yield(value):
    if value is None or value == MISSING:
        return MISSING
    value = float(value)
    rounded_tenth = math.floor(value * 10.0 + 0.5) / 10.0
    if rounded_tenth >= 100.0:
        return str(round_half_up(value))
    return f"{rounded_tenth:.1f}"


def render_dashboard(data, template=None):
    renderer_dir = Path(__file__).resolve().parent.parent
    template = template or (renderer_dir / "template" / "dashboard_template.svg").read_text(encoding="utf-8")
    solar_power = _number(data, "solar_power_kw")
    battery_percent = _number(data, "battery_percent")
    house_power = _number(data, "display_house_power_kw")
    if house_power is None and "display_house_power_kw" not in data:
        house_power = _number(data, "house_power_kw")
    heat_power = _number(data, "heat_power_kw")
    battery_power = _number(data, "battery_power_kw")
    grid_power = _number(data, "grid_power_kw")
    battery_available = bool(data.get("battery_available", True))

    def consumer(value, y):
        if value is None:
            return MISSING, ""
        if abs(value) < .05:
            return "0.0", ""
        return format_1(abs(value)), down_arrow(y, RED)

    house_display, house_arrow_svg = consumer(house_power, 303)
    heat_display, heat_arrow_svg = consumer(heat_power, 347)
    if not battery_available:
        battery_label, battery_arrow_svg, battery_flow = "Batterie folgt", "", MISSING
    elif battery_power is None:
        battery_label, battery_arrow_svg, battery_flow = "Batterie", "", MISSING
    elif battery_power > .05:
        battery_label, battery_arrow_svg, battery_flow = "Batterieladung", down_arrow(391, RED), format_1(abs(battery_power))
    elif battery_power < -.05:
        battery_label, battery_arrow_svg, battery_flow = "Batteriebezug", up_arrow(391, GREEN), format_1(abs(battery_power))
    else:
        battery_label, battery_arrow_svg, battery_flow = "Batterie", "", "0.0"

    if grid_power is None:
        grid_label, grid_arrow_svg, grid_display = "Netz", "", MISSING
    elif grid_power > .05:
        grid_label, grid_arrow_svg, grid_display = "Einspeisung", down_arrow(435, GREEN), format_1(abs(grid_power))
    elif grid_power < -.05:
        grid_label, grid_arrow_svg, grid_display = "Netzbezug", up_arrow(435, RED), format_1(abs(grid_power))
    else:
        grid_label, grid_arrow_svg, grid_display = "Netz", "", "0.0"

    solar_segments = segment_count(solar_power or 0, SOLAR_PROGRESS_MAX_KW)
    battery_segments = segment_count(battery_percent or 0, 100) if battery_available else 0
    pct = _number(data, "self_consumption_percent")
    line1, line2, font_size, first_y, badge = story_layout(data)
    values = {
        "SOLAR_POWER": format_1(max(0, solar_power)) if solar_power is not None else MISSING,
        "CURRENT_TIME": data.get("current_time", MISSING), "DATE_TEXT": data.get("date_text", MISSING),
        "SUN_HOURS": _optional(data.get("sun_hours"), " h", 1),
        "BATTERY_PERCENT": str(round_half_up(clamp(battery_percent, 0, 100))) if battery_available and battery_percent is not None else MISSING,
        "HOUSE_POWER": MISSING if house_display == MISSING else house_display + " kW",
        "HEAT_POWER": MISSING if heat_display == MISSING else heat_display + " kW",
        "BATTERY_FLOW_LABEL": battery_label, "BATTERY_FLOW_POWER": MISSING if battery_flow == MISSING else battery_flow + " kW",
        "GRID_LABEL": grid_label, "GRID_POWER": MISSING if grid_display == MISSING else grid_display + " kW",
        "SUNRISE": data.get("sunrise") or MISSING, "SUNSET": data.get("sunset") or MISSING,
        "STORY_LINE_1": line1, "STORY_LINE_2": line2,
        "STORY_FONT_SIZE": font_size, "STORY_FIRST_Y": first_y,
        "DAY_YIELD": format_day_yield(data.get("day_yield_kwh")),
        "SELF_CONSUMPTION": str(round_half_up(clamp(pct, 0, 100))) if pct is not None else MISSING,
        "SELF_CONSUMPTION_UNIT": "%" if pct is not None else "",
        "CO2_SAVINGS": _optional(data.get("co2_savings_kg"), decimals=1),
    }
    def chart_path(name, color, width, fill="none"):
        path = str(data.get(name, ""))
        if not path:
            return ""
        return (f'<path d="{escape(path)}" fill="{fill}" stroke="{color}" '
                f'stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>')

    chart_svg = chart_path("chart_solar_area", "none", 0, YELLOW)
    chart_svg += chart_path("chart_solar_line", BLACK, 2.2)
    chart_svg += chart_path("chart_house_line", BLUE, 2.5)
    chart_svg += chart_path("chart_grid_import_line", RED, 1.7)
    chart_svg += chart_path("chart_battery_line", GREEN, 2.5)
    for i in range(1, SEGMENT_COUNT + 1):
        values[f"SOLAR_SEG_{i:02d}_FILL"] = YELLOW if i <= solar_segments else WHITE
        values[f"BATTERY_SEG_{i:02d}_FILL"] = GREEN if i <= battery_segments else WHITE
    rendered = template
    for key, value in {"CHART_SERIES_SVG": chart_svg, "STORY_BADGE_SVG": story_badge_svg(badge),
                       "WEATHER_ICON_SVG": weather_icon_svg(data.get("weather_icon_variant")),
                       "HOUSE_ARROW_SVG": house_arrow_svg, "HEAT_ARROW_SVG": heat_arrow_svg,
                       "BATTERY_ARROW_SVG": battery_arrow_svg, "GRID_ARROW_SVG": grid_arrow_svg}.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", escape(str(value)))
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", rendered)))
    if unresolved:
        raise ValueError("Unresolved placeholders: " + ", ".join(unresolved))
    return rendered


def main(argv=None):
    renderer_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Render the frozen SVG dashboard")
    parser.add_argument("--data", type=Path, default=renderer_dir / "data" / "sample_data.json")
    parser.add_argument("--output", type=Path, default=renderer_dir / "output" / "dashboard.svg")
    args = parser.parse_args(argv)
    data = json.loads(args.data.read_text(encoding="utf-8"))
    rendered = render_dashboard(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Generated:        {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
