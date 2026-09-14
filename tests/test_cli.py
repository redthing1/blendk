import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blendk.cli import Context, _print


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

    def test_text_run_output_marks_truncation(self) -> None:
        with patch("blendk.cli.typer.echo") as echo:
            _print(
                Context(project=Path.cwd(), json=False),
                {"stdout": "partial", "stderr": "", "truncated": True},
            )

        self.assertTrue(
            any(
                call.args == ("blendk: script output was truncated",)
                and call.kwargs.get("err") is True
                for call in echo.call_args_list
            )
        )

    def test_text_warnings_use_stderr(self) -> None:
        with patch("blendk.cli.typer.echo") as echo:
            _print(
                Context(project=Path.cwd(), json=False),
                {"path": "/scene.blend", "warnings": [{"message": "check datablock"}]},
            )

        echo.assert_any_call("warning: check datablock", err=True)

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
