from __future__ import annotations

import importlib.resources
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from blendk.errors import BlendkError
from blendk.process import popen_options
from blendk.session import SessionPaths, prepare


@dataclass(frozen=True)
class LaunchOptions:
    executable: Path
    file: Path | None
    headed: bool
    profile: str
    online: bool
    autoexec: bool


def discover(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    configured = os.environ.get("BLENDK_BLENDER")
    if configured:
        candidates.append(Path(configured).expanduser())
    on_path = shutil.which("blender")
    if on_path:
        candidates.append(Path(on_path))
    candidates.extend(_standard_locations())

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    raise BlendkError(
        "blender_not_found",
        "Blender was not found; use --blender or set BLENDK_BLENDER",
    )


def probe(executable: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlendkError("blender_unavailable", f"cannot run Blender: {executable}") from error
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not output:
        raise BlendkError("blender_unavailable", "Blender version probe failed")
    return {
        "executable": str(executable),
        "version": output.splitlines()[0],
    }


def materialize_bridge(paths: SessionPaths) -> None:
    prepare(paths)
    source = importlib.resources.files("blendk").joinpath("_bridge.py").read_bytes()
    temporary = paths.bridge.with_suffix(".tmp")
    temporary.write_bytes(source)
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(paths.bridge)


def launch(
    paths: SessionPaths,
    options: LaunchOptions,
    *,
    bridge_host: str,
    bridge_port: int,
    bridge_token: str,
) -> subprocess.Popen[str]:
    materialize_bridge(paths)
    environment = os.environ.copy()
    environment.update(
        {
            "BLENDK_BRIDGE_HOST": bridge_host,
            "BLENDK_BRIDGE_PORT": str(bridge_port),
            "BLENDK_BRIDGE_TOKEN": bridge_token,
            "BLENDK_PROJECT": str(paths.project),
            "BLENDK_ARTIFACTS": str(paths.artifacts),
            "BLENDK_MODE": "headed" if options.headed else "headless",
        }
    )

    arguments = [str(options.executable)]
    if not options.headed:
        arguments.append("--background")
    if options.profile == "isolated":
        _prepare_isolated_profile(paths.profile)
        environment["BLENDER_USER_RESOURCES"] = str(paths.profile)
        arguments.append("--factory-startup")
    arguments.append("--enable-autoexec" if options.autoexec else "--disable-autoexec")
    if not options.online:
        arguments.append("--offline-mode")
    arguments.extend(["--python-exit-code", "70"])
    if options.file is not None:
        arguments.append(str(options.file))
    arguments.extend(["--python", str(paths.bridge)])

    return subprocess.Popen(
        arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
        **popen_options(),
    )


def _prepare_isolated_profile(root: Path) -> None:
    for child in ("config", "scripts", "extensions", "datafiles"):
        (root / child).mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(0o700)


def _standard_locations() -> list[Path]:
    if sys.platform == "darwin":
        return [Path("/Applications/Blender.app/Contents/MacOS/Blender")]
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]
        found: list[Path] = []
        for root in roots:
            if not root:
                continue
            blender_root = Path(root) / "Blender Foundation"
            if blender_root.is_dir():
                found.extend(sorted(blender_root.glob("Blender */blender.exe"), reverse=True))
        return found
    return [Path("/usr/bin/blender"), Path("/usr/local/bin/blender"), Path("/snap/bin/blender")]
