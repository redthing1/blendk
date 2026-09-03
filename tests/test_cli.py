import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_json_invalid_project_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            completed = self._run("--json", "--project", str(missing), "status")

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        error = json.loads(completed.stderr)
        self.assertEqual(error["error"]["code"], "invalid_project")

    def test_json_invalid_size_is_structured(self) -> None:
        completed = self._run("--json", "preview", "--size", "wide")

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        error = json.loads(completed.stderr)
        self.assertEqual(error["error"]["code"], "invalid_size")

    @staticmethod
    def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "blendk", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
