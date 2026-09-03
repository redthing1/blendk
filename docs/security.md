# Security

`blendk run` and `blendk eval` execute Python inside Blender with Blender's operating-
system permissions. Use an OS sandbox or container for untrusted work and do not run
Blender as root or administrator.

The default isolated profile uses private Blender resources, factory startup, disabled
embedded-file auto-execution, and Blender offline mode. Select `--profile native` only
when operator-installed settings, add-ons, renderers, or devices are required.

`--online` and `--autoexec` are explicit launch options. Offline mode does not constrain
arbitrary Python or a malicious add-on.

The supervisor uses authenticated ephemeral loopback connections. Blender connects
outward with a separate token and never opens a command listener. Session credentials
are stored in private per-user runtime files.

blendk contains no telemetry, provider integrations, runtime installer, plugin loader,
or automatic updater. Typer is its only direct dependency; the complete uv resolution
is committed with hashes and constrained during tool installation.

Visual artifacts never overwrite an existing explicit destination. Save As checks the
destination before asking Blender to write it. Normal close refuses unsaved work;
discard and forced termination are explicit.
