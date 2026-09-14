from __future__ import annotations

import secrets
import socket
from collections.abc import Callable
from pathlib import Path

from blendk.errors import BlendkError
from blendk.protocol import PROTOCOL_VERSION, receive_frame, send_frame
from blendk.session import paths_for, read_descriptor


def request(
    project: Path,
    method: str,
    params: dict[str, object] | None = None,
    *,
    timeout: float | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    paths = paths_for(project)
    descriptor = read_descriptor(paths)
    host = _string(descriptor, "host")
    port = _integer(descriptor, "port")
    token = _string(descriptor, "token")
    request_id = secrets.token_hex(12)

    try:
        with socket.create_connection((host, port), timeout=5.0) as connection:
            connection.settimeout(5.0)
            send_frame(
                connection,
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "hello",
                    "role": "client",
                    "token": token,
                },
            )
            hello = receive_frame(connection)
            if hello.get("ok") is not True:
                _raise_remote(hello)
            connection.settimeout(timeout)
            send_frame(
                connection,
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                },
            )
            while True:
                response = receive_frame(connection)
                if response.get("kind") == "event":
                    if on_event is not None:
                        on_event(response)
                    continue
                if response.get("id") != request_id:
                    raise BlendkError("protocol_error", "received a response for another request")
                if response.get("ok") is not True:
                    _raise_remote(response)
                result = response.get("result")
                return result if isinstance(result, dict) else {"value": result}
    except OSError as error:
        message = f"cannot reach the blendk supervisor for project {paths.project}"
        if paths.log.is_file():
            message += f"; log: {paths.log}"
        raise BlendkError("not_running", message) from error


def _raise_remote(message: dict[str, object]) -> None:
    error = message.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        text = error.get("message")
        if isinstance(code, str) and isinstance(text, str):
            details = error.get("details")
            raise BlendkError(
                code,
                text,
                details=details if isinstance(details, str) else None,
            )
    raise BlendkError("remote_error", "blendk request failed")


def _string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise BlendkError("invalid_session", f"session descriptor has no valid {key}")
    return item


def _integer(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int):
        raise BlendkError("invalid_session", f"session descriptor has no valid {key}")
    return item
