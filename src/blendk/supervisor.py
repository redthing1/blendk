from __future__ import annotations

import argparse
import collections
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from blendk import __version__
from blendk.blender import LaunchOptions, discover, launch
from blendk.errors import BlendkError
from blendk.process import terminate
from blendk.protocol import PROTOCOL_VERSION, receive_frame, send_frame
from blendk.session import (
    SessionPaths,
    acquire_lock,
    paths_for,
    release_lock,
    write_descriptor,
)


@dataclass(frozen=True)
class SupervisorOptions:
    project: Path
    blender: Path | None = None
    file: Path | None = None
    headed: bool = False
    profile: str = "isolated"
    online: bool = False
    autoexec: bool = False


class Supervisor:
    def __init__(self, options: SupervisorOptions, *, foreground: bool):
        self.options = options
        self.foreground = foreground
        self.paths: SessionPaths = paths_for(options.project)
        self.client_token = secrets.token_urlsafe(32)
        self.bridge_token = secrets.token_urlsafe(32)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.bridge: socket.socket | None = None
        self.bridge_ready = threading.Event()
        self.stop = threading.Event()
        self.operation = threading.Lock()
        self.state_lock = threading.Lock()
        self.active_method: str | None = None
        self.active_since: float | None = None
        self.process: subprocess.Popen[str] | None = None
        self.exit_code: int | None = None
        self.logs: collections.deque[str] = collections.deque(maxlen=200)
        self.outcomes: collections.OrderedDict[str, dict[str, object]] = collections.OrderedDict()

    def run(self) -> None:
        lock_descriptor = acquire_lock(self.paths)
        try:
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen()
            self.listener.settimeout(0.5)
            host, port = self.listener.getsockname()
            write_descriptor(
                self.paths,
                {
                    "v": PROTOCOL_VERSION,
                    "blendk_version": __version__,
                    "project": str(self.paths.project),
                    "host": host,
                    "port": port,
                    "token": self.client_token,
                    "pid": os.getpid(),
                },
            )
            executable = discover(self.options.blender)
            selected_file = self.options.file
            if selected_file is not None:
                selected_file = selected_file.expanduser().resolve(strict=True)
            self.process = launch(
                self.paths,
                LaunchOptions(
                    executable=executable,
                    file=selected_file,
                    headed=self.options.headed,
                    profile=self.options.profile,
                    online=self.options.online,
                    autoexec=self.options.autoexec,
                ),
                bridge_host=host,
                bridge_port=port,
                bridge_token=self.bridge_token,
            )
            threading.Thread(target=self._read_logs, daemon=True).start()
            threading.Thread(target=self._watch_process, daemon=True).start()
            while not self.stop.is_set():
                try:
                    self._accept()
                except KeyboardInterrupt:
                    if not self.foreground or self._foreground_interrupt():
                        break
        finally:
            self.stop.set()
            self.listener.close()
            if self.bridge is not None:
                self.bridge.close()
            if self.process is not None and self.process.poll() is None and not self.options.headed:
                terminate(self.process, force=True)
            self.paths.descriptor.unlink(missing_ok=True)
            release_lock(self.paths, lock_descriptor)

    def _foreground_interrupt(self) -> bool:
        if not self.operation.acquire(blocking=False):
            self._log(f"Blender is busy with {self.active_method}; interrupt ignored")
            return False
        try:
            request_id = secrets.token_hex(12)
            status = self._call_bridge(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": request_id,
                    "method": "status",
                    "params": {},
                }
            )
            result = status.get("result")
            if isinstance(result, dict) and result.get("dirty") is True:
                self._log("scene is dirty; save it or use blendk close --discard")
                return False
            self._call_bridge(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": secrets.token_hex(12),
                    "method": "close",
                    "params": {},
                }
            )
            self.stop.set()
            return True
        except BlendkError as error:
            self._log(f"{error.code}: {error.message}")
            return False
        finally:
            self.operation.release()

    def _accept(self) -> None:
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stop.is_set():
                    return
                raise
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                daemon=True,
            ).start()

    def _handle_connection(self, connection: socket.socket) -> None:
        connection.settimeout(10)
        request: dict[str, object] | None = None
        try:
            hello = receive_frame(connection)
            role = hello.get("role")
            token = hello.get("token")
            if (
                hello.get("v") != PROTOCOL_VERSION
                or not isinstance(role, str)
                or not isinstance(token, str)
            ):
                raise BlendkError("protocol_mismatch", "unsupported blendk protocol")
            expected = self.bridge_token if role == "bridge" else self.client_token
            if role not in {"bridge", "client"} or not secrets.compare_digest(token, expected):
                raise BlendkError("unauthorized", "invalid session credential")
            send_frame(connection, {"v": PROTOCOL_VERSION, "kind": "hello", "ok": True})
            if role == "bridge":
                connection.settimeout(None)
                self._adopt_bridge(connection)
                return
            request = receive_frame(connection)
            self._handle_client_request(connection, request)
        except BlendkError as error:
            request_id = request.get("id") if request is not None else None
            self._send_error(connection, request_id if isinstance(request_id, str) else None, error)
        except (OSError, ValueError):
            pass
        finally:
            if connection is not self.bridge:
                connection.close()

    def _adopt_bridge(self, connection: socket.socket) -> None:
        with self.state_lock:
            if self.bridge is not None:
                connection.close()
                return
            self.bridge = connection
            self.bridge_ready.set()
        self._log("bridge ready")

    def _handle_client_request(
        self,
        connection: socket.socket,
        request: dict[str, object],
    ) -> None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params")
        if (
            request.get("v") != PROTOCOL_VERSION
            or request.get("kind") != "request"
            or not isinstance(request_id, str)
            or not isinstance(method, str)
            or not isinstance(params, dict)
        ):
            raise BlendkError("invalid_request", "invalid request envelope")

        cached = self.outcomes.get(request_id)
        if cached is not None:
            send_frame(connection, cached)
            return
        if method == "status" and self.operation.locked():
            self._send_result(connection, request_id, self._local_status())
            return
        if method == "close" and params.get("kill") is True:
            if self.process is not None:
                terminate(self.process, force=True)
            result = {"closing": True, "forced": True}
            self._remember(request_id, self._response(request_id, result))
            self._send_result(connection, request_id, result)
            self.stop.set()
            return
        if not self.operation.acquire(blocking=False):
            raise BlendkError("busy", f"Blender is busy with {self.active_method}")
        try:
            self.active_method = method
            self.active_since = time.monotonic()
            if method == "status":
                result = self._call_bridge(request)
                if result.get("ok") is True and isinstance(result.get("result"), dict):
                    result["result"] = {**result["result"], **self._local_status(reporting=True)}
            elif method == "close":
                result = self._close_request(request)
            elif method in {"preview", "render"}:
                result = self._visual_request(request)
            elif method == "save":
                result = self._save_request(request)
            else:
                result = self._call_bridge(request)
            self._remember(request_id, result)
            try:
                send_frame(connection, result)
            except OSError:
                pass
            if method == "close" and result.get("ok") is True:
                self.stop.set()
        finally:
            self.active_method = None
            self.active_since = None
            self.operation.release()

    def _close_request(self, request: dict[str, object]) -> dict[str, object]:
        params = request["params"]
        assert isinstance(params, dict)
        if params.get("discard") is not True:
            status_request = {
                "v": PROTOCOL_VERSION,
                "kind": "request",
                "id": secrets.token_hex(12),
                "method": "status",
                "params": {},
            }
            status_response = self._call_bridge(status_request)
            status = status_response.get("result")
            if isinstance(status, dict) and status.get("dirty") is True:
                reasons = ", ".join(status.get("dirty_reasons", []))
                return self._error_response(
                    str(request["id"]),
                    BlendkError("dirty", f"scene has unsaved changes: {reasons}"),
                )
        return self._call_bridge(request)

    def _visual_request(self, request: dict[str, object]) -> dict[str, object]:
        params = request["params"]
        assert isinstance(params, dict)
        requested = params.get("path")
        if requested is None:
            method = str(request["method"])
            name = f"{method}-{time.time_ns()}-{secrets.token_hex(4)}.png"
            destination = self.paths.artifacts / name
        elif isinstance(requested, str):
            destination = Path(requested)
            if not destination.is_absolute():
                raise BlendkError("invalid_path", "artifact destination must be absolute")
        else:
            raise BlendkError("invalid_path", "artifact destination must be a path")
        if not destination.parent.is_dir():
            raise BlendkError(
                "invalid_path",
                f"artifact directory does not exist: {destination.parent}",
            )
        if destination.exists():
            raise BlendkError("destination_exists", f"destination already exists: {destination}")

        staging = self.paths.artifacts / f".render-{secrets.token_hex(12)}.png"
        forwarded = {**request, "params": {**params, "path": str(staging)}}
        try:
            response = self._call_bridge(forwarded)
            if response.get("ok") is not True:
                return response
            with staging.open("rb") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            result = response.get("result")
            if isinstance(result, dict):
                result["path"] = str(destination)
            return response
        finally:
            staging.unlink(missing_ok=True)

    def _save_request(self, request: dict[str, object]) -> dict[str, object]:
        params = request["params"]
        assert isinstance(params, dict)
        destination = params.get("path")
        if destination is None:
            return self._call_bridge(request)
        if not isinstance(destination, str) or not Path(destination).is_absolute():
            raise BlendkError("invalid_path", "save destination must be absolute")
        path = Path(destination)
        if not path.parent.is_dir():
            raise BlendkError("invalid_path", f"save directory does not exist: {path.parent}")
        if path.exists():
            raise BlendkError("destination_exists", f"destination already exists: {path}")
        return self._call_bridge(request)

    def _call_bridge(self, request: dict[str, object]) -> dict[str, object]:
        if not self.bridge_ready.wait(timeout=15):
            raise BlendkError("blender_unavailable", "Blender bridge is not ready")
        bridge = self.bridge
        if bridge is None:
            raise BlendkError("blender_unavailable", "Blender bridge disconnected")
        try:
            send_frame(bridge, request)
            while True:
                response = receive_frame(bridge)
                if response.get("kind") == "event":
                    self._log(str(response.get("message", "")))
                    continue
                if response.get("id") != request.get("id"):
                    raise BlendkError(
                        "protocol_error",
                        "Blender returned an unexpected request ID",
                    )
                return response
        except (OSError, BlendkError):
            with self.state_lock:
                self.bridge = None
                self.bridge_ready.clear()
            raise

    def _local_status(self, *, reporting: bool = False) -> dict[str, object]:
        elapsed = None
        if self.active_since is not None and not reporting:
            elapsed = round(time.monotonic() - self.active_since, 3)
        process = self.process
        if self.active_method and not reporting:
            state = "busy"
        elif self.bridge_ready.is_set():
            state = "ready"
        else:
            state = "starting"
        return {
            "supervisor_pid": os.getpid(),
            "blender_pid": process.pid if process is not None else None,
            "state": state,
            "active_command": None if reporting else self.active_method,
            "active_seconds": elapsed,
            "exit_code": self.exit_code,
            "profile": self.options.profile,
            "headed": self.options.headed,
        }

    def _read_logs(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self._log(line.rstrip())

    def _watch_process(self) -> None:
        process = self.process
        if process is None:
            return
        self.exit_code = process.wait()
        self._log(f"Blender exited with code {self.exit_code}")
        self.stop.set()

    def _log(self, message: str) -> None:
        if not message:
            return
        self.logs.append(message[:4000])
        print(message, flush=True)

    def _remember(self, request_id: str, response: dict[str, object]) -> None:
        self.outcomes[request_id] = response
        self.outcomes.move_to_end(request_id)
        while len(self.outcomes) > 32:
            self.outcomes.popitem(last=False)

    @staticmethod
    def _response(request_id: str, result: dict[str, object]) -> dict[str, object]:
        return {
            "v": PROTOCOL_VERSION,
            "kind": "response",
            "id": request_id,
            "ok": True,
            "result": result,
        }

    def _send_result(
        self,
        connection: socket.socket,
        request_id: str,
        result: dict[str, object],
    ) -> None:
        send_frame(connection, self._response(request_id, result))

    @staticmethod
    def _error_response(request_id: str | None, error: BlendkError) -> dict[str, object]:
        return {
            "v": PROTOCOL_VERSION,
            "kind": "response",
            "id": request_id,
            "ok": False,
            "error": {"code": error.code, "message": error.message},
        }

    def _send_error(
        self,
        connection: socket.socket,
        request_id: str | None,
        error: BlendkError,
    ) -> None:
        try:
            send_frame(connection, self._error_response(request_id, error))
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--profile", choices=("isolated", "native"), default="isolated")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--autoexec", action="store_true")
    options = parser.parse_args()
    try:
        Supervisor(
            SupervisorOptions(
                project=options.project,
                blender=options.blender,
                file=options.file,
                headed=options.headed,
                profile=options.profile,
                online=options.online,
                autoexec=options.autoexec,
            ),
            foreground=False,
        ).run()
    except BlendkError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
