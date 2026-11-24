import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from dashboard.facts.editorial import CALENDAR, ENERGY, EVENTS, HISTORY, MORNING
from dashboard.facts.width import text_width_px
from dashboard.live_view import build_live_view
from dashboard.preview import synthetic
from dashboard.publisher import publish_view
from dashboard.web_publisher import build_web_payload
from renderer.preview_badges import generate
from renderer.src.render import badge_category, render_dashboard, story_layout
from solar_data.storage import SolarDatabase
from solar_data.timezones import ZURICH


def story_nodes(root):
    return [
        e for e in root if e.get("x") == "255" and e.get("y") in ("354", "365", "374")
    ]


class BadgeTests(unittest.TestCase):
    def test_selected_id_family_and_precedence(self):
        records = {"DAILY_RECORD", "STREAK_RECORD", "RECORD_AFTERGLOW"}
        for ident in EVENTS:
            self.assertEqual(
                badge_category(ident, "milestone"),
                "record" if ident in records else "milestone",
            )
        for ident in CALENDAR:
            self.assertEqual(badge_category(ident, "calendar"), "calendar")
        for catalogue, family in (
            (ENERGY, "energy"),
            (MORNING, "morning"),
            (HISTORY, "history"),
        ):
            for ident in catalogue:
                self.assertEqual(
                    badge_category(ident, family),
                    "record" if ident in records else None,
                )
        for ident in ("MISSING", "STALE"):
            self.assertEqual(badge_category(ident, "milestone"), "warning")
        self.assertEqual(badge_category("STREAK_RECORD", "calendar"), "record")
        self.assertIsNone(badge_category(None, "milestone"))

    def test_every_special_template_fits_at_representative_values(self):
        values = dict(
            energy="142,3",
            old_record="139,9",
            days="10’000",
            count="1’000",
            years="25",
            kwh="1’000’000",
            mwh="1’000",
            threshold="200",
        )
        for rows, family in (
            (EVENTS.values(), "milestone"),
            (CALENDAR.values(), "calendar"),
            ([MORNING["RECORD_AFTERGLOW"]], "morning"),
        ):
            for row in rows:
                variants = row["variants"]
                for branch in (
                    variants.values() if isinstance(variants, dict) else [variants]
                ):
                    for template in branch:
                        with self.subTest(ident=row["id"], template=template):
                            text = template.format_map(values)
                            root = ET.fromstring(
                                render_dashboard(
                                    dict(
                                        story_fact_id=row["id"],
                                        story_family=family,
                                        story_line_1=text,
                                    )
                                )
                            )
                            self.assertIsNotNone(root.find("*[@data-story-badge]"))
                            lines = story_nodes(root)
                            self.assertEqual(
                                " ".join(e.text for e in lines if e.text), text
                            )
                            for node in lines:
                                self.assertEqual(node.get("font-size"), "15")
                                self.assertLessEqual(
                                    text_width_px(node.text or ""), 455
                                )
                            if lines[1].text:
                                self.assertNotRegex(
                                    lines[0].text + "|" + lines[1].text,
                                    r"\d\|(?:kWh|MWh|Tage|Jahre|Uhr)\b",
                                )

    def test_original_layout_font_steps_and_lossless_fallback(self):
        # M is 12.5 px wide in the conservative 15 px metrics.
        for length, size, category in (
            (36, 15, "record"),
            (39, 14, "record"),
            (42, 13, "record"),
            (43, 15, None),
        ):
            token = "M" * length
            original = dict(story_line_1=token, story_line_2=token)
            lines = story_layout(dict(original, story_fact_id="DAILY_RECORD"))
            self.assertEqual(lines, (token, token, size, 354, category))
            if category is None:
                self.assertEqual(
                    render_dashboard(dict(original, story_fact_id="DAILY_RECORD")),
                    render_dashboard(original),
                )
        original = dict(story_line_1="Rekord & <Kalender>", story_line_2="1’000 kWh")
        svg = render_dashboard(original)
        self.assertEqual(
            svg,
            render_dashboard(
                dict(original, story_fact_id="TECH_CONTEXT", story_family="morning")
            ),
        )
        self.assertNotIn("{{", svg)
        lines = story_nodes(ET.fromstring(svg))
        self.assertEqual(
            [(e.get("y"), e.get("font-size"), e.text) for e in lines],
            [("354", "15", original["story_line_1"]), ("374", "15", "1’000 kWh")],
        )
        marked = ET.fromstring(
            render_dashboard(dict(original, story_fact_id="DAILY_RECORD"))
        )
        self.assertEqual(story_nodes(marked)[0].get("y"), "365")
        self.assertEqual(story_nodes(marked)[0].text, "Rekord & <Kalender> 1’000 kWh")
        ordinary = ET.fromstring(svg)
        for root in (ordinary, marked):
            for node in story_nodes(root) + root.findall("*[@data-story-badge]"):
                root.remove(node)
        self.assertEqual(ET.tostring(ordinary), ET.tostring(marked))

    def test_svg_publication_and_web_contract(self):
        instant = datetime(2026, 6, 2, 10, 0, tzinfo=ZURICH)
        sample, sun = synthetic(instant.date(), instant, "sunny-summer")
        with tempfile.TemporaryDirectory() as folder, SolarDatabase(":memory:") as db:
            output = Path(folder) / "dashboard.svg"
            db.store_snapshot(sample)
            now = instant + timedelta(minutes=20)
            view = build_live_view(db, now, sun_result=sun)
            self.assertEqual(view["display"]["story_fact_id"], "STALE")
            self.assertEqual(
                view["display"]["story_family"], view["story_status"]["family"]
            )
            payload = build_web_payload(db, view, sample, now)
            publish_view(view, output)
            previous = output.read_bytes()
            self.assertIn(b'data-story-badge="warning"', previous)
            display = view["display"].copy()
            del display["story_family"], display["story_fact_id"]
            self.assertEqual(
                payload, build_web_payload(db, dict(view, display=display), sample, now)
            )
            self.assertEqual(payload["insight"]["line_2"], display["story_line_2"])
            with SolarDatabase(":memory:") as empty:
                missing = build_live_view(empty, now, sun_result=sun)
                self.assertEqual(missing["display"]["story_fact_id"], "MISSING")
                with self.assertRaisesRegex(ValueError, "preserving"):
                    publish_view(missing, output)
            self.assertEqual(output.read_bytes(), previous)

    def test_native_previews_and_fixed_badge_geometry(self):
        colors = {
            "record": ("#FFD400", "#000000"),
            "milestone": ("#149B24", "#FFFFFF"),
            "calendar": ("#0057B8", "#FFFFFF"),
            "warning": ("#E02020", "#FFFFFF"),
        }
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            generate(directory)
            self.assertEqual(
                {p.stem for p in directory.iterdir()},
                {"badge-preview", "preview-default", *(f"preview-{k}" for k in colors)},
            )
            for path in directory.iterdir():
                svg = path.read_text()
                self.assertNotIn("{{", svg)
                root = ET.fromstring(svg)
                self.assertEqual(
                    (root.get("width"), root.get("height"), root.get("viewBox")),
                    ("800", "480", "0 0 800 480"),
                )
                badges = root.findall(".//*[@data-story-badge]")
                expected = (
                    list(colors)
                    if path.stem == "badge-preview"
                    else []
                    if path.stem == "preview-default"
                    else [path.stem.removeprefix("preview-")]
                )
                self.assertEqual([b.get("data-story-badge") for b in badges], expected)
                for badge in badges:
                    self.assertEqual(badge.get("transform"), "translate(727 336)")
                    background, foreground = colors[badge.get("data-story-badge")]
                    self.assertEqual(
                        badge[0].attrib,
                        dict(width="48", height="48", rx="4", fill=background),
                    )
                    paints = {
                        e.get(k) for e in badge.iter() for k in ("stroke", "fill")
                    } - {None, "none"}
                    self.assertEqual(paints, {background, foreground})
