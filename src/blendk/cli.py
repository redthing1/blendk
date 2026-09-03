from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Never

import typer

from blendk import __version__
from blendk.blender import discover, probe
from blendk.client import request
from blendk.errors import BlendkError
from blendk.process import is_running, popen_options
from blendk.session import SessionPaths, canonical_project, lock_owner, paths_for, prepare
from blendk.supervisor import Supervisor, SupervisorOptions


@dataclass(frozen=True)
class Context:
    project: Path
    json: bool


app = typer.Typer(
    name="blendk",
    help="Stateful Blender control for agents and humans.",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def root(
    context: typer.Context,
    project: Annotated[
        Path,
        typer.Option("--project", help="Project directory that identifies the session."),
    ] = Path.cwd(),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the blendk version.", is_eager=True),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    try:
        selected_project = canonical_project(project)
    except BlendkError as error:
        _fail(error, json_output=json_output)
    context.obj = Context(project=selected_project, json=json_output)


@app.command()
def doctor(
    context: typer.Context,
    blender: Annotated[
        Path | None,
        typer.Option("--blender", help="Path to the Blender executable."),
    ] = None,
) -> None:
    """Find Blender and report the selected executable and version."""
    _execute(context, lambda state: probe(discover(blender)))


@app.command()
def serve(
    context: typer.Context,
    file: Annotated[Path | None, typer.Argument(help="Initial .blend file.")] = None,
    blender: Annotated[
        Path | None,
        typer.Option("--blender", help="Path to the Blender executable."),
    ] = None,
    headed: Annotated[bool, typer.Option("--headed", help="Open Blender's interface.")] = False,
    profile: Annotated[
        str,
        typer.Option(help="Blender profile: isolated or native."),
    ] = "isolated",
    online: Annotated[
        bool,
        typer.Option("--online", help="Permit Blender-managed online services."),
    ] = False,
    autoexec: Annotated[
        bool,
        typer.Option("--autoexec", help="Permit scripts embedded in .blend files."),
    ] = False,
) -> None:
    """Run the project supervisor in the foreground."""
    state = _context(context)
    try:
        _validate_profile(profile)
        selected = _existing_file(file)
        Supervisor(
            SupervisorOptions(
                project=state.project,
                blender=blender,
                file=selected,
                headed=headed,
                profile=profile,
                online=online,
                autoexec=autoexec,
            ),
            foreground=True,
        ).run()
    except BlendkError as error:
        _fail(error, json_output=state.json)


@app.command("open")
def open_session(
    context: typer.Context,
    file: Annotated[Path | None, typer.Argument(help="Initial .blend file.")] = None,
    blender: Annotated[
        Path | None,
        typer.Option("--blender", help="Path to the Blender executable."),
    ] = None,
    headed: Annotated[bool, typer.Option("--headed", help="Open Blender's interface.")] = False,
    profile: Annotated[
        str,
        typer.Option(help="Blender profile: isolated or native."),
    ] = "isolated",
    online: Annotated[
        bool,
        typer.Option("--online", help="Permit Blender-managed online services."),
    ] = False,
    autoexec: Annotated[
        bool,
        typer.Option("--autoexec", help="Permit scripts embedded in .blend files."),
    ] = False,
) -> None:
    """Start or reuse the project's Blender session."""
    state = _context(context)
    try:
        _validate_profile(profile)
        selected = _existing_file(file)
    except BlendkError as error:
        _fail(error, json_output=state.json)
    try:
        existing = request(state.project, "status")
    except BlendkError as error:
        if error.code != "not_running":
            _fail(error, json_output=state.json)
    else:
        if selected is not None and existing.get("file") != str(selected):
            _fail(
                BlendkError(
                    "different_file",
                    "the project session already has another file open",
                ),
                json_output=state.json,
            )
        _print(state, existing)
        return

    paths = paths_for(state.project)
    if paths.lock.exists():
        owner = lock_owner(paths)
        if owner is None:
            _fail(
                BlendkError("invalid_session", f"session lock has no valid owner: {paths.lock}"),
                json_output=state.json,
            )
        if is_running(owner):
            _await_session(state, paths)
            return
        paths.lock.unlink()
    paths.descriptor.unlink(missing_ok=True)
    prepare(paths)
    command = [sys.executable, "-m", "blendk.supervisor", "--project", str(state.project)]
    if blender is not None:
        command.extend(["--blender", str(blender.expanduser().resolve())])
    if selected is not None:
        command.extend(["--file", str(selected)])
    if headed:
        command.append("--headed")
    command.extend(["--profile", profile])
    if online:
        command.append("--online")
    if autoexec:
        command.append("--autoexec")

    with paths.log.open("w", encoding="utf-8") as log:
        supervisor = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **popen_options(detached=True),
        )
    _await_session(state, paths, supervisor)


def _await_session(
    state: Context,
    paths: SessionPaths,
    supervisor: subprocess.Popen[bytes] | None = None,
) -> None:
    deadline = time.monotonic() + 20
    last_error: BlendkError | None = None
    while time.monotonic() < deadline:
        try:
            result = request(state.project, "status")
            if result.get("state") == "ready":
                _print(state, result)
                return
        except BlendkError as error:
            last_error = error
        if supervisor is not None and supervisor.poll() is not None:
            owner = lock_owner(paths)
            if owner is None or owner == supervisor.pid or not is_running(owner):
                _fail(
                    BlendkError(
                        "launch_failed",
                        f"supervisor exited during launch; see {paths.log}",
                    ),
                    json_output=state.json,
                )
            supervisor = None
        time.sleep(0.1)
    message = last_error.message if last_error is not None else "Blender did not become ready"
    _fail(
        BlendkError("launch_failed", f"{message}; see {paths.log}"),
        json_output=state.json,
    )


@app.command()
def status(context: typer.Context) -> None:
    """Report the current project session."""
    _remote(context, "status")


@app.command()
def inspect(
    context: typer.Context,
    pattern: Annotated[str | None, typer.Argument(help="Optional object-name glob.")] = None,
) -> None:
    """Inspect the scene and a bounded set of objects."""
    _remote(context, "inspect", {"pattern": pattern})


@app.command("eval")
def evaluate(
    context: typer.Context,
    expression: Annotated[str, typer.Argument(help="Python expression, or - for stdin.")],
) -> None:
    """Evaluate one observation expression in Blender."""
    source = sys.stdin.read() if expression == "-" else expression
    _remote(context, "eval", {"source": source}, timeout=None)


@app.command("run")
def run_script(
    context: typer.Context,
    script: Annotated[str, typer.Argument(help="Python file, or - for stdin.")],
) -> None:
    """Execute a Python script in Blender."""
    try:
        source = sys.stdin.read() if script == "-" else Path(script).read_text(encoding="utf-8")
    except OSError:
        _fail(
            BlendkError("script_unavailable", f"cannot read script: {script}"),
            json_output=_context(context).json,
        )
    _remote(context, "run", {"source": source}, timeout=None)


@app.command()
def preview(
    context: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Optional PNG destination.")] = None,
    camera: Annotated[
        str | None,
        typer.Option("--camera", help="Camera object name."),
    ] = None,
    frame: Annotated[int | None, typer.Option("--frame", help="Frame to render.")] = None,
    size: Annotated[
        str,
        typer.Option("--size", help="Image size as WIDTHxHEIGHT."),
    ] = "512x512",
) -> None:
    """Create a fast Workbench camera render."""
    state = _context(context)
    try:
        dimensions = _parse_size(size)
    except BlendkError as error:
        _fail(error, json_output=state.json)
    _remote(
        context,
        "preview",
        {
            "path": str(path.expanduser().resolve()) if path is not None else None,
            "camera": camera,
            "frame": frame,
            "size": dimensions,
        },
        timeout=None,
    )


@app.command()
def render(
    context: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Optional PNG destination.")] = None,
    camera: Annotated[
        str | None,
        typer.Option("--camera", help="Camera object name."),
    ] = None,
    frame: Annotated[int | None, typer.Option("--frame", help="Frame to render.")] = None,
    size: Annotated[
        str | None,
        typer.Option("--size", help="Image size as WIDTHxHEIGHT."),
    ] = None,
) -> None:
    """Render through the scene's active engine."""
    state = _context(context)
    try:
        dimensions = _parse_size(size) if size is not None else None
    except BlendkError as error:
        _fail(error, json_output=state.json)
    _remote(
        context,
        "render",
        {
            "path": str(path.expanduser().resolve()) if path is not None else None,
            "camera": camera,
            "frame": frame,
            "size": dimensions,
        },
        timeout=None,
    )


@app.command()
def save(
    context: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Optional new .blend path.")] = None,
) -> None:
    """Save the current file or to a new path."""
    destination = None
    if path is not None:
        destination_path = path.expanduser().resolve()
        if destination_path.exists():
            _fail(
                BlendkError(
                    "destination_exists",
                    f"destination already exists: {destination_path}",
                ),
                json_output=_context(context).json,
            )
        destination = str(destination_path)
    _remote(context, "save", {"path": destination}, timeout=None)


@app.command()
def close(
    context: typer.Context,
    discard: Annotated[
        bool,
        typer.Option("--discard", help="Discard unsaved scene changes."),
    ] = False,
    kill: Annotated[
        bool,
        typer.Option("--kill", help="Forcibly terminate Blender."),
    ] = False,
) -> None:
    """Close Blender without silently discarding work."""
    _remote(context, "close", {"discard": discard, "kill": kill})


def _remote(
    context: typer.Context,
    method: str,
    params: dict[str, object] | None = None,
    *,
    timeout: float | None = 30,
) -> None:
    _execute(context, lambda state: request(state.project, method, params, timeout=timeout))


def _execute(
    context: typer.Context,
    operation: Callable[[Context], dict[str, object]],
) -> None:
    state = _context(context)
    try:
        _print(state, operation(state))
    except BlendkError as error:
        _fail(error, json_output=state.json)


def _print(state: Context, result: dict[str, object]) -> None:
    if state.json:
        typer.echo(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return
    if "stdout" in result:
        stdout = result.get("stdout")
        stderr = result.get("stderr")
        if isinstance(stdout, str) and stdout:
            typer.echo(stdout, nl=not stdout.endswith("\n"))
        if isinstance(stderr, str) and stderr:
            typer.echo(stderr, err=True, nl=not stderr.endswith("\n"))
        if not stdout and not stderr:
            typer.echo("ok")
        return
    if set(result) == {"value"}:
        typer.echo(json.dumps(result["value"], ensure_ascii=False))
        return
    typer.echo(" ".join(f"{key}={_text(value)}" for key, value in result.items()))


def _text(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _context(context: typer.Context) -> Context:
    state = context.obj
    if not isinstance(state, Context):
        raise RuntimeError("blendk CLI context is unavailable")
    return state


def _existing_file(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlendkError("file_unavailable", f"file does not exist: {path}") from error
    if not resolved.is_file():
        raise BlendkError("file_unavailable", f"not a file: {resolved}")
    return resolved


def _validate_profile(profile: str) -> None:
    if profile not in {"isolated", "native"}:
        raise BlendkError("invalid_profile", "profile must be isolated or native")


def _parse_size(value: str) -> list[int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except ValueError as error:
        raise BlendkError("invalid_size", "size must be WIDTHxHEIGHT") from error
    if not (1 <= width <= 16384 and 1 <= height <= 16384):
        raise BlendkError("invalid_size", "image dimensions must be from 1 to 16384")
    return [width, height]


def _fail(error: BlendkError, *, json_output: bool = False) -> Never:
    if json_output:
        typer.echo(
            json.dumps(
                {"ok": False, "error": {"code": error.code, "message": error.message}},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            err=True,
        )
    else:
        typer.echo(f"{error.code}: {error.message}", err=True)
    raise typer.Exit(1)


def main() -> None:
    app()
