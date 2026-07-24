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

SOLAR_MAX_KW = 20.0
SEGMENT_COUNT = 20


def round_half_up(value: float) -> int:
    """Round positive values conventionally: 13.5 -> 14."""
    return int(math.floor(value + 0.5))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def format_1(value: float) -> str:
    return f"{value:.1f}"


def segment_count(value: float, maximum: float) -> int:
    value = clamp(value, 0.0, maximum)
    scaled = (value / maximum) * SEGMENT_COUNT
    return max(0, min(SEGMENT_COUNT, round_half_up(scaled)))


def down_arrow(y_top: int, color: str) -> str:
    return (
        f'<polygon points="218,{y_top} 222,{y_top} '
        f'222,{y_top + 9} 226,{y_top + 9} '
        f'220,{y_top + 16} 214,{y_top + 9} 218,{y_top + 9}" '
        f'fill="{color}"/>'
    )


def up_arrow(y_top: int, color: str) -> str:
    return (
        f'<polygon points="218,{y_top + 16} 222,{y_top + 16} '
        f'222,{y_top + 7} 226,{y_top + 7} '
        f'220,{y_top} 214,{y_top + 7} 218,{y_top + 7}" '
        f'fill="{color}"/>'
    )


def main() -> None:
    renderer_dir = Path(__file__).resolve().parent.parent
    template_path = renderer_dir / "template" / "dashboard_template.svg"
    data_path = renderer_dir / "data" / "sample_data.json"
    output_dir = renderer_dir / "output"
    output_path = output_dir / "dashboard.svg"

    template = template_path.read_text(encoding="utf-8")
    data = json.loads(data_path.read_text(encoding="utf-8"))

    # Raw numeric values
    solar_power = float(data["solar_power_kw"])
    battery_percent = float(data["battery_percent"])
    house_power = float(data["house_power_kw"])
    heat_power = float(data["heat_power_kw"])
    battery_power = float(data["battery_power_kw"])
    grid_power = float(data["grid_power_kw"])

    # Display semantics:
    # battery_power > 0 = charging, < 0 = discharging / battery supply
    if battery_power > 0.05:
        battery_label = "Batterieladung"
        battery_arrow_svg = down_arrow(391, RED)
    elif battery_power < -0.05:
        battery_label = "Batteriebezug"
        battery_arrow_svg = up_arrow(391, GREEN)
    else:
        battery_label = "Batterie"
        battery_arrow_svg = ""

    # grid_power > 0 = export, < 0 = import
    if grid_power > 0.05:
        grid_label = "Einspeisung"
        grid_arrow_svg = down_arrow(435, GREEN)
    elif grid_power < -0.05:
        grid_label = "Netzbezug"
        grid_arrow_svg = up_arrow(435, RED)
    else:
        grid_label = "Netz"
        grid_arrow_svg = ""

    # Dynamic segmented bars
    solar_segments = segment_count(solar_power, SOLAR_MAX_KW)
    battery_segments = segment_count(battery_percent, 100.0)

    values = {
        "SOLAR_POWER": format_1(max(0.0, solar_power)),
        "CURRENT_TIME": str(data["current_time"]),
        "DATE_TEXT": str(data["date_text"]),
        "SUN_HOURS": f'{float(data["sun_hours"]):.1f} h',
        "BATTERY_PERCENT": str(round_half_up(clamp(battery_percent, 0.0, 100.0))),
        "HOUSE_POWER": f"{format_1(abs(house_power))} kW",
        "HEAT_POWER": f"{format_1(abs(heat_power))} kW",
        "BATTERY_FLOW_LABEL": battery_label,
        "BATTERY_FLOW_POWER": f"{format_1(abs(battery_power))} kW",
        "GRID_LABEL": grid_label,
        "GRID_POWER": f"{format_1(abs(grid_power))} kW",
        "SUNRISE": str(data["sunrise"]),
        "SUNSET": str(data["sunset"]),
        "STORY_LINE_1": str(data["story_line_1"]),
        "STORY_LINE_2": str(data["story_line_2"]),
        "DAY_YIELD": format_1(float(data["day_yield_kwh"])),
        "SELF_CONSUMPTION": str(round_half_up(clamp(float(data["self_consumption_percent"]), 0.0, 100.0))),
        "CO2_SAVINGS": format_1(max(0.0, float(data["co2_savings_kg"]))),
    }

    # Segment fill placeholders
    for i in range(1, SEGMENT_COUNT + 1):
        values[f"SOLAR_SEG_{i:02d}_FILL"] = YELLOW if i <= solar_segments else WHITE
        values[f"BATTERY_SEG_{i:02d}_FILL"] = GREEN if i <= battery_segments else WHITE

    rendered = template

    # Raw SVG fragments first; intentionally not XML-escaped.
    rendered = rendered.replace("{{BATTERY_ARROW_SVG}}", battery_arrow_svg)
    rendered = rendered.replace("{{GRID_ARROW_SVG}}", grid_arrow_svg)

    # Normal text / attribute placeholders are escaped.
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", escape(str(value)))

    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", rendered)))
    if unresolved:
        print("Error: Unresolved placeholders found:", file=sys.stderr)
        for item in unresolved:
            print(f"  - {item}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    print(f"Solar segments:   {solar_segments}/20")
    print(f"Battery segments: {battery_segments}/20")
    print(f"Battery state:    {battery_label}")
    print(f"Grid state:       {grid_label}")
    print(f"Generated:        {output_path}")


if __name__ == "__main__":
    main()
