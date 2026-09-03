# Scripting

## State and execution

`blendk run <file>` reads source on the client and executes it in Blender. `run -` reads
source from standard input. The Blender process and scene persist across calls.

Each call receives a fresh namespace:

- `bpy`: Blender's Python API;
- `blendk.project`: canonical project directory;
- `blendk.artifacts`: managed artifact directory.

Store lasting state in Blender data or project files. Standard output and errors return
to the client with bounded size.

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
progress. Long scripts occupy Blender's main thread, so several observable stages are
usually easier to evaluate than one large execution.

## Observation

Use `eval` for compact queries:

```sh
blendk eval 'bpy.context.scene.render.engine'
blendk eval 'sorted(object.name for object in bpy.data.objects)'
```

Expressions have the same Python authority as scripts. Keeping mutations in `run`
preserves meaningful dirty tracking.

## Errors

Python exceptions return a bounded traceback. Changes made before an exception remain
in the scene. Inspect the current state, compare it with the last checkpoint, and
continue from evidence.
