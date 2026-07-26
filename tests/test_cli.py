import json
import os
import subprocess
import sys
import tempfile
import unittest


class CliTest(unittest.TestCase):
    def test_collector_repair_energy_outputs_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "collector", "--db-path",
                 os.path.join(directory, "solar.db"), "repair-energy",
                 "--day", "2026-07-26"],
                capture_output=True, check=False, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "local_day": "2026-07-26", "updated_aggregates": 0,
        })

    def test_missing_base_url_is_rejected(self):
        environment = os.environ.copy()
        environment.pop("FRONIUS_BASE_URL", None)
        result = subprocess.run(
            [sys.executable, "-m", "fronius"],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--base-url or FRONIUS_BASE_URL is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
