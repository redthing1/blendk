# Setup

## Requirements

- uv
- An official Blender 5.2 LTS installation

Install uv through a trusted operating-system package or a verified official release.
blendk does not use a network-fetched bootstrap script.

## Install

From a reviewed checkout:

```sh
uv tool install --constraints constraints.txt .
blendk doctor
```

The constraints file fixes Typer's complete dependency graph. Reinstall explicitly to
adopt repository changes:

```sh
uv tool install --force --constraints constraints.txt .
```

For repository development:

```sh
uv sync --locked
uv run --locked blendk doctor
uv run --locked python -m unittest discover -s tests -v
```

## Blender discovery

blendk checks, in order:

1. `--blender <path>` on `open`, `serve`, or `doctor`;
2. `BLENDK_BLENDER` when the supervisor starts;
3. `blender` on `PATH`;
4. conservative standard locations for the current operating system.

The selected path is stored for the session. Later commands need no launch options or
environment variables.

## Sessions

Headless:

```sh
blendk open [scene.blend]
```

Headed and foreground:

```sh
blendk serve --headed [scene.blend]
```

Add `--profile native` when the session needs operator-installed settings, add-ons,
renderers, or devices.
Use `blendk open --headed` when a visible window should outlive the starting terminal.
Commands identify a session by the current directory. Add `--project <dir>` before a
command when invoking it elsewhere.

## Verify

```sh
blendk status
blendk --json inspect
blendk preview --size 320x240
blendk render --size 320x240
```

Both visual commands return an absolute PNG path.
