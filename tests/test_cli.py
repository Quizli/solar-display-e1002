import os
import subprocess
import sys
import unittest


class CliTest(unittest.TestCase):
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
