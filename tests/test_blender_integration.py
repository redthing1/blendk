import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from blendk.blender import discover
from blendk.client import request
from blendk.errors import BlendkError
from blendk.protocol import PROTOCOL_VERSION, receive_frame, send_frame
from blendk.session import paths_for, read_descriptor


class BlenderIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.blender = discover()
        except BlendkError as error:
            self.skipTest(error.message)
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.supervisor: subprocess.Popen[str] | None = None

    def tearDown(self) -> None:
        if self.supervisor is not None and self.supervisor.poll() is None:
            try:
                request(self.project, "close", {"discard": True}, timeout=5)
            except BlendkError:
                self.supervisor.terminate()
            try:
                self.supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.supervisor.kill()
        if self.supervisor is not None and self.supervisor.stdout is not None:
            self.supervisor.stdout.close()
        paths = paths_for(self.project)
        shutil.rmtree(paths.root, ignore_errors=True)
        self.temporary.cleanup()

    def test_headless_stateful_visual_workflow(self) -> None:
        self._start(headed=False)
        status = request(self.project, "status")
        self.assertEqual(status["mode"], "headless")
        self.assertEqual(status["state"], "ready")
        evaluated = request(self.project, "eval", {"source": "len(bpy.data.objects)"})
        self.assertEqual(evaluated["value"], 3)
        multiline = request(
            self.project,
            "eval",
            {"source": "names = sorted(obj.name for obj in bpy.data.objects)\nnames"},
        )
        self.assertEqual(multiline["value"], ["Camera", "Cube", "Light"])
        self._assert_wrong_token_rejected()

        events: list[dict[str, object]] = []
        streamed = request(
            self.project,
            "run",
            {"source": 'print("first", flush=True)\nprint("second")'},
            on_event=events.append,
        )
        self.assertEqual(streamed["stdout"], "first\nsecond\n")
        self.assertEqual(
            "".join(str(event.get("text", "")) for event in events),
            "first\nsecond\n",
        )

        with self.assertRaises(BlendkError) as raised:
            request(
                self.project,
                "run",
                {"source": "value = 1\nraise RuntimeError('broken')"},
            )
        self.assertIn("line 2", raised.exception.details or "")
        self.assertIn("raise RuntimeError('broken')", raised.exception.details or "")

        oversized = request(
            self.project,
            "run",
            {"source": "print('x' * (256 * 1024 + 1))"},
        )
        self.assertTrue(oversized["truncated"])
        self.assertEqual(len(oversized["stdout"]), 256 * 1024)

        self._disconnect_during_run()
        deadline = time.monotonic() + 5
        observed_busy = False
        while time.monotonic() < deadline:
            current = request(self.project, "status")
            if current["state"] == "busy":
                observed_busy = True
            if observed_busy and current["state"] == "ready":
                break
            time.sleep(0.02)
        self.assertTrue(observed_busy)
        changed = request(
            self.project,
            "eval",
            {"source": 'bpy.data.objects["Cube"].location.x'},
        )
        self.assertEqual(changed["value"], 2.0)

        request(
            self.project,
            "run",
            {"source": 'bpy.data.objects["Cube"].location.z = 1.0'},
        )
        inspection = request(self.project, "inspect", {"pattern": "Cube"})
        self.assertTrue(inspection["dirty"])
        self.assertEqual(inspection["listed"], 1)
        self.assertEqual(inspection["object_list"][0]["location"], [2.0, 0.0, 1.0])
        self.assertIn("BLENDER_EEVEE", inspection["registered_engines"])
        self.assertIn("backend", inspection["graphics"])
        self.assertIn("scene_device", inspection["cycles"])

        preview = request(
            self.project,
            "preview",
            {"path": None, "camera": None, "frame": None, "size": [160, 120]},
            timeout=None,
        )
        image = Path(preview["path"])
        self.assertEqual(self._png_size(image), (160, 120))
        restored = request(self.project, "status")
        self.assertEqual(restored["engine"], "BLENDER_EEVEE")
        self.assertEqual(restored["resolution"], [1920, 1080])

        scene_preview = request(
            self.project,
            "preview",
            {
                "path": None,
                "camera": None,
                "frame": None,
                "size": [160, 120],
                "engine": "scene",
            },
            timeout=None,
        )
        self.assertEqual(scene_preview["engine"], "BLENDER_EEVEE")

        render = request(
            self.project,
            "render",
            {"path": None, "camera": None, "frame": None, "size": [160, 120]},
            timeout=None,
        )
        self.assertEqual(render["engine"], "BLENDER_EEVEE")
        self.assertEqual(self._png_size(Path(render["path"])), (160, 120))

        existing = self.project / "existing.png"
        existing.write_bytes(b"keep")
        with self.assertRaisesRegex(BlendkError, "destination already exists"):
            request(
                self.project,
                "preview",
                {"path": str(existing), "camera": None, "frame": None, "size": [16, 16]},
            )
        self.assertEqual(existing.read_bytes(), b"keep")

        with self.assertRaisesRegex(BlendkError, "unsaved changes"):
            request(self.project, "close")
        save_path = self.project / "scene.blend"
        request(
            self.project,
            "run",
            {"source": 'bpy.data.worlds.new("Unlinked Sunset")'},
        )
        saved = request(self.project, "save", {"path": str(save_path)}, timeout=None)
        self.assertEqual(Path(saved["path"]), save_path)
        self.assertTrue(save_path.is_file())
        warnings = saved.get("warnings", [])
        self.assertTrue(
            any(
                item.get("name") == "Unlinked Sunset"
                for warning in warnings
                for item in warning.get("items", [])
            )
        )

        request(
            self.project,
            "run",
            {"source": 'bpy.data.objects["Cube"].location.x = 99'},
        )
        current_mtime = save_path.stat().st_mtime_ns
        os.utime(save_path, ns=(current_mtime + 1_000_000_000,) * 2)
        self.assertTrue(request(self.project, "status")["file_changed_on_disk"])
        reloaded = request(self.project, "reload", {"path": str(save_path)}, timeout=None)
        self.assertTrue(reloaded["reloaded"])
        self.assertFalse(reloaded["dirty"])
        self.assertFalse(reloaded["file_changed_on_disk"])
        self.assertEqual(
            request(
                self.project,
                "eval",
                {"source": 'bpy.data.objects["Cube"].location.x'},
            )["value"],
            2.0,
        )
        request(self.project, "close")
        self.supervisor.wait(timeout=10)

    def test_automatic_open_starts_project_supervisor(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "blendk",
                "--project",
                str(self.project),
                "open",
                "--blender",
                str(self.blender),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(request(self.project, "status")["state"], "ready")
        request(self.project, "close")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and paths_for(self.project).descriptor.exists():
            time.sleep(0.05)
        self.assertFalse(paths_for(self.project).descriptor.exists())
        self.assertIn("session ended: closed cleanly", paths_for(self.project).log.read_text())

    def test_headless_survives_bridge_connect_timeout(self) -> None:
        self._start(headed=False, bridge_connect_timeout=0.1)

        time.sleep(0.25)

        status = request(self.project, "status")
        self.assertEqual(status["state"], "ready")
        self.assertIsNone(status["exit_code"])

    def test_simultaneous_open_reuses_one_supervisor(self) -> None:
        command = [
            sys.executable,
            "-m",
            "blendk",
            "--project",
            str(self.project),
            "open",
            "--blender",
            str(self.blender),
        ]
        processes = [
            subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        try:
            results = [process.communicate(timeout=30) for process in processes]
            for process, (_, stderr) in zip(processes, results, strict=True):
                self.assertEqual(process.returncode, 0, stderr)
            status = request(self.project, "status")
            self.assertEqual(status["state"], "ready")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            try:
                request(self.project, "close", {"discard": True}, timeout=5)
            except BlendkError:
                pass
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and paths_for(self.project).descriptor.exists():
                time.sleep(0.05)

    @unittest.skipUnless(os.environ.get("BLENDK_TEST_HEADED") == "1", "headed test is opt-in")
    def test_headed_uses_live_window_and_visual_workflow(self) -> None:
        self._start(headed=True)
        status = request(self.project, "status")
        self.assertEqual(status["mode"], "headed")
        evaluated = request(
            self.project,
            "eval",
            {"source": "bpy.context.window is not None"},
        )
        self.assertTrue(evaluated["value"])
        preview = request(
            self.project,
            "preview",
            {"path": None, "camera": None, "frame": None, "size": [160, 120]},
            timeout=None,
        )
        self.assertEqual(self._png_size(Path(preview["path"])), (160, 120))
        request(self.project, "close")
        self.supervisor.wait(timeout=10)

    def _start(
        self,
        *,
        headed: bool,
        bridge_connect_timeout: float | None = None,
    ) -> None:
        command = [
            sys.executable,
            "-m",
            "blendk",
            "--project",
            str(self.project),
            "serve",
            "--blender",
            str(self.blender),
        ]
        if headed:
            command.append("--headed")
        environment = os.environ.copy()
        if bridge_connect_timeout is not None:
            environment["BLENDK_BRIDGE_CONNECT_TIMEOUT"] = str(bridge_connect_timeout)
        self.supervisor = subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.supervisor.poll() is not None:
                output = self.supervisor.stdout.read() if self.supervisor.stdout else ""
                self.fail(f"supervisor exited during startup:\n{output}")
            try:
                if request(self.project, "status").get("state") == "ready":
                    return
            except BlendkError:
                pass
            time.sleep(0.1)
        self.fail("supervisor did not become ready")

    def _assert_wrong_token_rejected(self) -> None:
        descriptor = read_descriptor(paths_for(self.project))
        with socket.create_connection((descriptor["host"], descriptor["port"])) as connection:
            send_frame(
                connection,
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "hello",
                    "role": "client",
                    "token": "wrong",
                },
            )
            response = receive_frame(connection)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "unauthorized")

        with socket.create_connection((descriptor["host"], descriptor["port"])) as connection:
            send_frame(
                connection,
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "hello",
                    "role": ["client"],
                    "token": "wrong",
                },
            )
            response = receive_frame(connection)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "protocol_mismatch")

    def _disconnect_during_run(self) -> None:
        descriptor = read_descriptor(paths_for(self.project))
        connection = socket.create_connection((descriptor["host"], descriptor["port"]))
        send_frame(
            connection,
            {
                "v": PROTOCOL_VERSION,
                "kind": "hello",
                "role": "client",
                "token": descriptor["token"],
            },
        )
        self.assertTrue(receive_frame(connection)["ok"])
        send_frame(
            connection,
            {
                "v": PROTOCOL_VERSION,
                "kind": "request",
                "id": "disconnected-run",
                "method": "run",
                "params": {
                    "source": (
                        "import time\n"
                        "time.sleep(0.5)\n"
                        'bpy.data.objects["Cube"].location.x = 2.0\n'
                    )
                },
            },
        )
        connection.close()
        time.sleep(0.05)

    @staticmethod
    def _png_size(path: Path) -> tuple[int, int]:
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise AssertionError("artifact is not PNG")
        return struct.unpack(">II", data[16:24])


if __name__ == "__main__":
    unittest.main()
