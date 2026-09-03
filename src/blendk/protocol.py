from __future__ import annotations

import json
import socket
import struct
from collections.abc import Mapping

from blendk.errors import BlendkError

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024
_HEADER = struct.Struct(">I")


def send_frame(sock: socket.socket, message: Mapping[str, object]) -> None:
    payload = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise BlendkError("frame_too_large", "protocol frame exceeds the size limit")
    sock.sendall(_HEADER.pack(len(payload)) + payload)


def receive_frame(sock: socket.socket) -> dict[str, object]:
    size = _HEADER.unpack(_receive_exact(sock, _HEADER.size))[0]
    if size == 0 or size > MAX_FRAME_BYTES:
        raise BlendkError("invalid_frame", "invalid protocol frame size")
    try:
        value = json.loads(_receive_exact(sock, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BlendkError("invalid_frame", "protocol frame is not valid JSON") from error
    if not isinstance(value, dict):
        raise BlendkError("invalid_frame", "protocol frame must be a JSON object")
    return value


def _receive_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise BlendkError("connection_lost", "connection closed")
        chunks.extend(chunk)
    return bytes(chunks)

