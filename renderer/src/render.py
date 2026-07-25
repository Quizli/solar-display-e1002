import argparse
from pathlib import Path
import json
import math
import re
import sys
from xml.sax.saxutils import escape

BLACK = "#000000"
WHITE = "#FFFFFF"
YELLOW = "#FFD400"
RED = "#E02020"
GREEN = "#149B24"
BLUE = "#0057B8"
SOLAR_MAX_KW = 20.0
SEGMENT_COUNT = 20
MISSING = "—"


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

    solar_segments = segment_count(solar_power or 0, SOLAR_MAX_KW)
    battery_segments = segment_count(battery_percent or 0, 100) if battery_available else 0
    pct = _number(data, "self_consumption_percent")
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
        "STORY_LINE_1": data.get("story_line_1", ""), "STORY_LINE_2": data.get("story_line_2", ""),
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
    chart_svg += chart_path("chart_battery_line", GREEN, 2.5)
    for i in range(1, SEGMENT_COUNT + 1):
        values[f"SOLAR_SEG_{i:02d}_FILL"] = YELLOW if i <= solar_segments else WHITE
        values[f"BATTERY_SEG_{i:02d}_FILL"] = GREEN if i <= battery_segments else WHITE
    rendered = template
    for key, value in {"CHART_SERIES_SVG": chart_svg,
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
