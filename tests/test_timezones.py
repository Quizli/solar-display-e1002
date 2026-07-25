import unittest

from solar_data.timezones import ZURICH, ZoneInfo


class TimezoneCompatibilityTest(unittest.TestCase):
    def test_zurich_is_loaded_through_compatibility_module(self):
        self.assertIsInstance(ZURICH, ZoneInfo)
        self.assertEqual(ZURICH.key, "Europe/Zurich")


if __name__ == "__main__":
    unittest.main()
