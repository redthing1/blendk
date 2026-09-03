# Security model

## Code authority

`run` and `eval` execute inside Blender with the operating-system permissions of the
Blender process. Their Python code can use Blender data, files, processes, and the
network. The operating-system sandbox or container is the isolation boundary for
untrusted work; run Blender there as an unprivileged user.

## Profiles

The default isolated profile starts from private Blender resources and factory startup,
with embedded-file auto-execution and Blender-managed online services disabled.

The native profile brings the operator's Blender environment into the session,
including enabled add-ons, third-party renderers, preferences, credentials, and device
configuration. Select it when those capabilities are part of the trusted workflow.

`--online` enables Blender-managed online services. `--autoexec` enables scripts stored
inside loaded `.blend` files. These choices are fixed when the session starts and are
reported by its launch configuration.

## Local control

The supervisor binds an authenticated ephemeral loopback endpoint. Blender connects
outward with a separate credential, and session credentials live in private per-user
runtime files. blendk and Blender operate within the same host or container.

Explicit image destinations use exclusive publication, and managed destinations are
unique. Save As validates a new destination before Blender writes it. A normal close
keeps dirty work available.

## Supply chain

The host uses the Python standard library beyond its Typer CLI boundary. The complete
Python dependency graph is locked, hashed, cooled down, and constrained during tool
installation. The other executable input is the operator-selected Blender installation.

Runtime operation installs no packages, Blender add-ons, renderers, or assets. The
bridge is first-party source shipped inside the blendk package.
