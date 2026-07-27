import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "dashboard.html"
CSS = ROOT / "web" / "assets" / "dashboard.css"
ICONS = ROOT / "web" / "assets" / "icons.svg"


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.components = set()
        self.fields = set()
        self.ids = set()
        self.scripts = []
        self.remote_urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("data-component"):
            self.components.add(values["data-component"])
        if values.get("data-field"):
            self.fields.add(values["data-field"])
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "script":
            self.scripts.append(values)
        for value in values.values():
            if value and re.match(r"(?:https?:)?//", value):
                self.remote_urls.append(value)


class WebDashboardShellTests(unittest.TestCase):
    def setUp(self):
        self.source = HTML.read_text(encoding="utf-8")
        self.parser = DashboardParser()
        self.parser.feed(self.source)
        self.parser.close()

    def test_production_assets_exist_and_html_parses(self):
        self.assertTrue(CSS.is_file())
        self.assertTrue(ICONS.is_file())
        self.assertIn("<!doctype html>", self.source.lower())
        self.assertIn("</html>", self.source.lower())

    def test_required_components_and_contract_hooks_exist(self):
        required_components = {
            "dashboard", "header", "data-status", "solar", "battery",
            "energy-flows", "house-consumption", "heat", "battery-flow",
            "grid-flow", "today", "chart", "insight",
            "historical-comparison", "system-status",
        }
        required_fields = {
            "header.local_date", "header.local_time", "header.sunshine_hours",
            "header.sunrise", "header.sunset", "live.solar_power_kw",
            "live.house_consumption_kw", "live.heat_power_kw",
            "live.battery_state_of_charge_percent",
            "live.battery_flow.magnitude_kw", "live.grid_flow.magnitude_kw",
            "today.yield_kwh", "today.self_consumption_percent",
            "today.co2_avoided_kg", "chart.series", "insight.line_1",
            "historical_comparison.statement", "data.timestamp",
        }
        self.assertTrue(required_components <= self.parser.components)
        self.assertTrue(required_fields <= self.parser.fields)
        self.assertIn("local-time", self.parser.ids)

    def test_loading_state_has_no_javascript_or_external_requests(self):
        self.assertIn('data-state="loading"', self.source)
        self.assertIn('aria-busy="true"', self.source)
        self.assertEqual(self.parser.scripts, [])
        self.assertEqual(self.parser.remote_urls, [])
        self.assertNotIn("dashboard.json", self.source)
        self.assertNotRegex(self.source, r"\b(?:fetch|XMLHttpRequest)\s*\(")

    def test_all_prepared_states_and_responsive_rules_are_styled(self):
        css = CSS.read_text(encoding="utf-8")
        for state in ("loading", "fresh", "degraded", "stale", "missing", "offline"):
            self.assertIn("data-state=" + state, css)
        self.assertIn("@media(min-width:700px)", css)
        self.assertIn("@media(min-width:1050px)", css)
        self.assertIn("env(safe-area-inset", css)
        self.assertIn("-webkit-backdrop-filter", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_compose_serves_shell_alongside_publisher_outputs(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("./publish:/usr/share/nginx/html:ro", compose)
        self.assertIn("./web/dashboard.html:/usr/share/nginx/html/dashboard.html:ro", compose)
        self.assertIn("./web/assets:/usr/share/nginx/html/assets:ro", compose)
        self.assertIn("DASHBOARD_OUTPUT_PATH: /publish/dashboard.svg", compose)
        self.assertIn("DASHBOARD_JSON_OUTPUT_PATH: /publish/dashboard.json", compose)


if __name__ == "__main__":
    unittest.main()
