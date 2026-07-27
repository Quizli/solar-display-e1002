import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "dashboard.html"
CSS = ROOT / "web" / "assets" / "dashboard.css"
ICONS = ROOT / "web" / "assets" / "icons.svg"
NGINX = ROOT / "deploy" / "nginx" / "default.conf"


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
        self.assertIn(".battery{min-height:112px", css)
        self.assertIn(".hero,.battery{min-height:180px}", css)

    def test_fresh_status_remains_visible_and_degraded_is_component_scoped(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertNotIn("[data-state=fresh] .status-pill{display:none}", css)
        self.assertIn("[data-state=fresh] .status-pill{display:flex", css)
        self.assertIn("[data-component]:not(.dashboard)[data-state=degraded]", css)
        self.assertNotIn(".dashboard[data-state=degraded]", css)

    def test_loading_segments_are_neutral_and_have_twenty_slots_each(self):
        css = CSS.read_text(encoding="utf-8")
        for component in ("solar", "battery"):
            match = re.search(
                rf'<article[^>]+data-component="{component}".*?</article>',
                self.source, re.DOTALL)
            self.assertIsNotNone(match)
            self.assertEqual(match.group(0).count("data-segment="), 20)
            self.assertNotIn("data-active=\"true\"", match.group(0))
        self.assertNotIn("nth-child", css)
        self.assertIn("span[data-active=true]", css)

    def test_direction_indicators_use_explicit_direction_states(self):
        css = CSS.read_text(encoding="utf-8")
        for icon in ("arrow-down", "arrow-up", "trend-up", "trend-down", "move-right"):
            self.assertIn("icons.svg#" + icon, self.source)
        for direction in ("charging", "discharging", "importing", "exporting",
                          "higher", "lower", "similar"):
            self.assertIn("data-direction=" + direction, css)
        self.assertIn('data-direction="unavailable"', self.source)
        self.assertNotRegex(self.source + css, r"power_kw\s*[<>]=?\s*0")

    def test_insight_contract_fields_have_distinct_elements(self):
        insight = re.search(
            r'<aside[^>]+data-component="insight".*?</aside>', self.source,
            re.DOTALL).group(0)
        self.assertRegex(insight, r'<h2[^>]+data-field="insight\.line_1"')
        self.assertRegex(insight, r'<p[^>]+data-field="insight\.line_2"')
        self.assertNotIn("Daten werden geladen.", insight)

    def test_compose_serves_shell_alongside_publisher_outputs(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("./publish:/usr/share/nginx/html:ro", compose)
        self.assertIn("./web:/usr/share/nginx/dashboard-shell:ro", compose)
        self.assertNotIn("./web/dashboard.html:/usr/share/nginx/html/", compose)
        self.assertNotIn("./web/assets:/usr/share/nginx/html/", compose)
        self.assertIn("DASHBOARD_OUTPUT_PATH: /publish/dashboard.svg", compose)
        self.assertIn("DASHBOARD_JSON_OUTPUT_PATH: /publish/dashboard.json", compose)

    def test_nginx_routes_separate_read_only_mounts(self):
        nginx = NGINX.read_text(encoding="utf-8")
        self.assertIn("root /usr/share/nginx/html;", nginx)
        self.assertIn("location = /dashboard.html", nginx)
        self.assertIn(
            "alias /usr/share/nginx/dashboard-shell/dashboard.html;", nginx)
        self.assertIn("location /assets/", nginx)
        self.assertIn("alias /usr/share/nginx/dashboard-shell/assets/;", nginx)


if __name__ == "__main__":
    unittest.main()
