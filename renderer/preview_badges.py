"""Generate native-size ePaper badge previews from the production template."""

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from dashboard.facts.editorial import (
    CALENDAR,
    EVENTS,
    energy_candidate,
    ENERGY,
    render,
    wrap,
)
from renderer.src.render import BLACK, WHITE, render_dashboard

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def comparison(examples):
    """Mount each rendered Insight strip at 1:1 scale on a single 800×480 sheet."""
    root = ET.Element(
        "svg",
        width="800",
        height="480",
        viewBox="0 0 800 480",
        attrib={"font-family": "Arial,Helvetica,sans-serif"},
    )
    ET.SubElement(root, "rect", width="800", height="480", fill=WHITE)
    ET.SubElement(
        root,
        "text",
        x="32",
        y="44",
        fill=BLACK,
        attrib={"font-size": "24", "font-weight": "700"},
    ).text = "Besondere Solarereignisse"
    for index, (label, dashboard) in enumerate(examples):
        y = 72 + index * 100
        ET.SubElement(
            root,
            "text",
            x="32",
            y=str(y + 41),
            fill=BLACK,
            attrib={"font-size": "17", "font-weight": "700"},
        ).text = label
        strip = ET.SubElement(
            root,
            "svg",
            x="232",
            y=str(y),
            width="540",
            height="72",
            viewBox="250 324 540 72",
        )
        for element in ET.fromstring(dashboard):
            if (
                element.get("data-story-badge")
                or (element.get("x") == "255" and 325 < int(element.get("y")) < 395)
                or (element.get("x1") == "255" and element.get("y1") in ("325", "395"))
            ):
                strip.append(element)
    return ET.tostring(root, encoding="unicode")


def generate(directory):
    sample = json.loads((Path(__file__).parent / "data/sample_data.json").read_text())
    energy = sample["day_yield_kwh"]
    regular = energy_candidate(ENERGY["phone_charge"], energy, energy)
    _, ordinary_lines = render(regular, "preview", preferred=0)
    cases = [
        ("default", "", regular.ident, regular.family, ordinary_lines),
        (
            "record",
            "Rekord",
            "DAILY_RECORD",
            "milestone",
            wrap(
                EVENTS["DAILY_RECORD"]["variants"]["default"][2].format(
                    energy="135,7", old_record="128,4"
                ),
                510,
            ),
        ),
        (
            "milestone",
            "Meilenstein",
            "OPERATING_DAY",
            "milestone",
            wrap(EVENTS["OPERATING_DAY"]["variants"]["d1000"][0], 510),
        ),
        (
            "calendar",
            "Kalender",
            "NATIONAL_DAY",
            "calendar",
            wrap(CALENDAR["NATIONAL_DAY"]["variants"][0], 510),
        ),
        (
            "warning",
            "Warnung",
            "STALE",
            "status",
            ("Datenstand 14:06 Uhr", "Aktualisierung der Solardaten prüfen"),
        ),
    ]
    directory.mkdir(parents=True, exist_ok=True)
    examples = []
    for name, label, fact_id, family, lines in cases:
        data = dict(
            sample,
            story_fact_id=fact_id,
            story_family=family,
            story_line_1=lines[0],
            story_line_2=lines[1],
        )
        if name == "calendar":
            data["date_text"] = "Samstag, 1. August 2026"
        if name == "warning":
            data["current_time"] = "14:06 Uhr"
        svg = render_dashboard(data)
        (directory / f"preview-{name}.svg").write_text(svg, encoding="utf-8")
        if label:
            examples.append((label, svg))
    (directory / "badge-preview.svg").write_text(comparison(examples), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("renderer/output/badge-previews")
    )
    generate(parser.parse_args().output_dir)
