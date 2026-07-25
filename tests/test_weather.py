import unittest

from dashboard.weather import weather_icon_variant
from renderer.src.render import render_dashboard, weather_icon_svg


class WeatherIconTest(unittest.TestCase):
    def test_wmo_mapping(self):
        cases = {
            0: "sunny", 1: "mainly_clear", 2: "partly_cloudy", 3: "overcast",
            45: "fog", 48: "fog", 51: "rain", 67: "rain", 80: "rain", 82: "rain",
            71: "snow", 77: "snow", 85: "snow", 86: "snow",
            95: "thunderstorm", 99: "thunderstorm",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(weather_icon_variant(code), expected)

    def test_invalid_codes_use_sunny_fallback(self):
        for code in (None, True, -1, 4, 100, "0"):
            with self.subTest(code=code):
                self.assertEqual(weather_icon_variant(code), "sunny")

    def test_clear_cloud_variants_are_visibly_distinct(self):
        clear = weather_icon_svg("sunny")
        small = weather_icon_svg("mainly_clear")
        large = weather_icon_svg("partly_cloudy")
        overcast = weather_icon_svg("overcast")
        self.assertNotEqual(clear, small)
        self.assertNotEqual(small, large)
        self.assertIn('cx="589"', clear)
        self.assertIn('cx="589"', small)
        self.assertIn('cx="589"', large)
        self.assertNotIn('cx="589"', overcast)

    def test_sample_and_rendered_svg_have_icon_without_placeholders(self):
        svg = render_dashboard({"weather_icon_variant": "partly_cloudy", "sun_hours": 8})
        self.assertIn('data-weather-icon="partly_cloudy"', svg)
        self.assertNotIn("{{", svg)
