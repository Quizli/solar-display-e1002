import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sun_data.cache import get_sun_data
from sun_data.client import SunDataError, parse_response


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)


def payload(duration=27360):
    return {"daily": {"time": ["2026-07-25"], "sunrise": ["2026-07-25T05:50"],
                      "sunset": ["2026-07-25T21:10"], "sunshine_duration": [duration]}}


class SunClientTest(unittest.TestCase):
    def test_valid_response_converts_seconds_and_local_times(self):
        result = parse_response(payload(), NOW, NOW.date())
        self.assertEqual(result.sunshine_hours, 7.6)
        self.assertEqual(result.sunrise.strftime("%H:%M"), "05:50")
        self.assertEqual(result.sunset.strftime("%H:%M"), "21:10")
        self.assertIsNotNone(result.sunrise.utcoffset())

    def test_wrong_local_day_and_bad_fields_are_rejected(self):
        bad = payload()
        bad["daily"]["time"] = ["2026-07-24"]
        with self.assertRaises(SunDataError):
            parse_response(bad, NOW, NOW.date())
        for field in ("sunrise", "sunset", "sunshine_duration"):
            bad = payload()
            del bad["daily"][field]
            with self.subTest(field=field), self.assertRaises(SunDataError):
                parse_response(bad, NOW, NOW.date())

    def test_invalid_durations_are_rejected(self):
        for duration in (-1, True, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(SunDataError):
                parse_response(payload(duration), NOW, NOW.date())


class SunCacheTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "sun-data.json"
        self.calls = 0

    def tearDown(self):
        self.directory.cleanup()

    def fetch(self, latitude, longitude, now):
        self.calls += 1
        return parse_response(payload(), now, now.date())

    def test_success_is_atomic_and_fresh_cache_skips_request(self):
        first = get_sun_data(47, 8, self.path, NOW, fetcher=self.fetch)
        self.assertEqual(first.source, "open-meteo")
        self.assertEqual(json.loads(self.path.read_text())["sunshine_hours"], 7.6)
        second = get_sun_data(47, 8, self.path, NOW + timedelta(minutes=5), fetcher=self.fetch)
        self.assertEqual(second.source, "cache")
        self.assertEqual(self.calls, 1)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_api_error_uses_only_current_and_young_cache(self):
        get_sun_data(47, 8, self.path, NOW, fetcher=self.fetch)
        failing = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout"))
        cached = get_sun_data(47, 8, self.path, NOW + timedelta(hours=1),
                              refresh_seconds=1, fetcher=failing)
        self.assertEqual(cached.status, "cached")
        old = get_sun_data(47, 8, self.path, NOW + timedelta(hours=7),
                           refresh_seconds=1, max_age_seconds=21600, fetcher=failing)
        self.assertIsNone(old.data)

    def test_previous_day_cache_is_not_used(self):
        get_sun_data(47, 8, self.path, NOW, fetcher=self.fetch)
        tomorrow = NOW + timedelta(days=1)
        missing = get_sun_data(47, 8, self.path, tomorrow, fetcher=lambda *a, **k: (_ for _ in ()).throw(OSError()))
        self.assertIsNone(missing.data)
        self.assertEqual(missing.status, "missing")


if __name__ == "__main__":
    unittest.main()
