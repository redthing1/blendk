from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from blendk.errors import BlendkError


@dataclass(frozen=True)
class SessionPaths:
    project: Path
    root: Path
    descriptor: Path
    lock: Path
    log: Path
    bridge: Path
    artifacts: Path
    profile: Path


def canonical_project(path: Path) -> Path:
    try:
        project = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlendkError("invalid_project", f"project directory is unavailable: {path}") from error
    if not project.is_dir():
        raise BlendkError("invalid_project", f"project is not a directory: {project}")
    return project


def paths_for(project: Path) -> SessionPaths:
    canonical = canonical_project(project)
    digest = hashlib.sha256(os.fsencode(canonical)).hexdigest()[:24]
    root = _runtime_root() / digest
    return SessionPaths(
        project=canonical,
        root=root,
        descriptor=root / "session.json",
        lock=root / "session.lock",
        log=root / "supervisor.log",
        bridge=root / "bridge.py",
        artifacts=root / "artifacts",
        profile=root / "profile",
    )


def prepare(paths: SessionPaths) -> None:
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.artifacts.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        paths.root.chmod(0o700)
        paths.artifacts.chmod(0o700)


def trim_log(paths: SessionPaths, *, keep_bytes: int = 1024 * 1024) -> None:
    """Retain a bounded tail before a new supervisor appends another session."""
    try:
        size = paths.log.stat().st_size
    except OSError:
        return
    if size <= keep_bytes:
        return
    with paths.log.open("rb") as source:
        source.seek(-keep_bytes, os.SEEK_END)
        tail = source.read()
    handle, temporary = tempfile.mkstemp(prefix="log-", suffix=".tmp", dir=paths.root)
    temporary_path = Path(temporary)
    try:
        if os.name != "nt":
            os.fchmod(handle, 0o600)
        with os.fdopen(handle, "wb") as stream:
            stream.write(tail)
        temporary_path.replace(paths.log)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def read_descriptor(paths: SessionPaths) -> dict[str, object]:
    try:
        if os.name != "nt" and stat.S_IMODE(paths.descriptor.stat().st_mode) & 0o077:
            raise BlendkError("unsafe_session", "session descriptor permissions are too broad")
        value = json.loads(paths.descriptor.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        message = f"no blendk session is running for project {paths.project}"
        if paths.log.is_file():
            message += f"; log: {paths.log}"
        raise BlendkError("not_running", message) from error
    except json.JSONDecodeError as error:
        raise BlendkError("invalid_session", "session descriptor is invalid") from error
    if not isinstance(value, dict):
        raise BlendkError("invalid_session", "session descriptor is invalid")
    return value


def write_descriptor(paths: SessionPaths, value: dict[str, object]) -> None:
    prepare(paths)
    handle, temporary = tempfile.mkstemp(prefix="session-", suffix=".json", dir=paths.root)
    temporary_path = Path(temporary)
    try:
        if os.name != "nt":
            os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(paths.descriptor)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def acquire_lock(paths: SessionPaths) -> int:
    prepare(paths)
    try:
        descriptor = os.open(paths.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise BlendkError(
            "already_running",
            "a blendk supervisor already owns this project",
        ) from error
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    return descriptor


def release_lock(paths: SessionPaths, descriptor: int) -> None:
    os.close(descriptor)
    paths.lock.unlink(missing_ok=True)


def lock_owner(paths: SessionPaths) -> int | None:
    try:
        value = paths.lock.read_text(encoding="ascii").strip()
        return int(value)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _runtime_root() -> Path:
    if sys.platform == "darwin":
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / "Library" / "Caches"))
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(
            os.environ.get("XDG_RUNTIME_DIR")
            or os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        )
    return base / "blendk"
