import socket
import tempfile
import unittest
from pathlib import Path

from blendk.client import request
from blendk.errors import BlendkError
from blendk.session import (
    paths_for,
    read_descriptor,
    trim_log,
    write_descriptor,
)


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

    def test_missing_descriptor_reports_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            paths = paths_for(project)
            paths.root.mkdir(parents=True)
            paths.log.touch()

            with self.assertRaises(BlendkError) as raised:
                read_descriptor(paths)

            self.assertEqual(raised.exception.code, "not_running")
            self.assertIn(str(project), raised.exception.message)
            self.assertIn(str(paths.log), raised.exception.message)

    def test_log_retention_is_bounded_to_its_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = paths_for(Path(temporary))
            paths.root.mkdir(parents=True, exist_ok=True)
            paths.log.write_bytes(b"0123456789")

            trim_log(paths, keep_bytes=4)

            self.assertEqual(paths.log.read_bytes(), b"6789")

    def test_unreachable_supervisor_reports_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            paths = paths_for(project)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            write_descriptor(paths, {"host": "127.0.0.1", "port": port, "token": "x"})
            paths.log.touch()

            with self.assertRaises(BlendkError) as raised:
                request(project, "status")

            self.assertEqual(raised.exception.code, "not_running")
            self.assertIn(str(paths.log), raised.exception.message)


if __name__ == "__main__":
    unittest.main()
