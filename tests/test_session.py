import tempfile
import unittest
from pathlib import Path

from blendk.session import paths_for, read_descriptor, write_descriptor


class SessionTests(unittest.TestCase):
    def test_project_identity_is_stable_and_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            first.mkdir()
            second.mkdir()

            self.assertEqual(paths_for(first).root, paths_for(first / ".").root)
            self.assertNotEqual(paths_for(first).root, paths_for(second).root)

    def test_descriptor_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            paths = paths_for(project)
            self.addCleanup(lambda: paths.descriptor.unlink(missing_ok=True))
            self.addCleanup(lambda: paths.lock.unlink(missing_ok=True))
            value = {"host": "127.0.0.1", "port": 1234, "token": "secret"}

            write_descriptor(paths, value)

            self.assertEqual(read_descriptor(paths), value)


if __name__ == "__main__":
    unittest.main()

