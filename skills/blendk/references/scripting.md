# Scripting

## State and execution

`blendk run <file>` reads source on the client and executes it in Blender. `run -` reads
source from standard input. The Blender process and scene persist across calls.

Each call receives a fresh namespace:

- `bpy`: Blender's Python API;
- `blendk.project`: canonical project directory;
- `blendk.artifacts`: managed artifact directory.

Store lasting state in Blender data or project files. Standard output and errors stream
to the invoking client and remain bounded; text output says explicitly when that bound
truncates a result.

## Scene changes

Prefer Blender's data API when it expresses the change directly. Stable names and
idempotent construction make a script easy to refine and run again:

```python
import bpy

cube = bpy.data.objects.get("BlockoutCube")
if cube is None:
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    cube.name = "BlockoutCube"

cube.location = (0.0, 0.0, 1.0)
cube.scale = (1.0, 2.0, 1.0)
```

Group related edits into a coherent step, inspect the result, and checkpoint valuable
progress. Long scripts occupy Blender's main thread, so print and flush meaningful
milestones when live progress helps the operator understand the current stage.

## Observation

Use `eval` for compact queries:

```sh
blendk eval 'bpy.context.scene.render.engine'
blendk eval 'sorted(object.name for object in bpy.data.objects)'
```

For a multi-line observation, use stdin. Setup statements run in a fresh namespace and
the final expression becomes the result:

```sh
blendk eval - <<'PY'
names = sorted(object.name for object in bpy.data.objects)
names[:10]
PY
```

Expressions have the same Python authority as scripts. Keeping mutations in `run`
preserves meaningful dirty tracking.

## API discovery

Blender evolves, and remembered examples may describe an older release. The live
runtime supports discovery through Python introspection and RNA metadata.

When an API is unfamiliar, `eval` and `run` can examine the relevant live objects and
scene state: types and current values, available attributes, docstrings where present,
and RNA properties. A small probe can also reveal behavior that static metadata does
not. Official documentation matching `bpy.app.version` can provide another source of
evidence when available. Carry only the relevant findings into the working script.

## Errors

Python exceptions return a bounded traceback with script line context. Changes made
before an exception remain in the scene. Inspect the current state, compare it with the
last checkpoint, and continue from evidence.
