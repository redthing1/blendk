---
name: blendk
description: >-
  Operate persistent headless or headed Blender sessions through blendk. Use for scene
  inspection, bpy editing, visual iteration, rendering, and saving.
---

# blendk

blendk keeps one Blender process alive across commands, so inspection, editing, and
rendering all act on the same scene.

## Start

Install a reviewed checkout and confirm the selected Blender:

```sh
uv tool install --constraints constraints.txt .
blendk doctor
```

For development in the checkout, use `uv run --locked blendk` in place of `blendk`.

Open a persistent headless session, or a headed session the operator can watch:

```sh
blendk open [scene.blend]
blendk serve --headed [scene.blend]
```

Keep `serve` in a persistent foreground terminal. Sessions use the isolated profile by
default. Add `--profile native` when the work relies on operator-approved preferences,
add-ons, renderers, or devices.

The current directory identifies the session. From elsewhere, place
`--project <project-root>` before the command. Launch options carry across later calls.

## Iterate

Observe the scene, make a coherent change, checkpoint useful progress, and look at the
result:

```sh
blendk --json inspect
blendk run edit_scene.py
blendk save working-scene.blend
blendk preview
blendk render
```

`open` reuses the live scene; `open --fresh [scene.blend]` explicitly discards it and
reloads from disk. Use `blendk logs` to diagnose an ended session.

After the first checkpoint, bare `blendk save` updates the current file. Use `eval` for
focused observations such as `blendk eval 'bpy.context.scene.render.engine'`. Save
reports zero-user datablocks that may not survive reopening.

Preview and render return absolute PNG paths. Inspect the image, adjust from visible
evidence, and repeat at the fidelity appropriate to the current question.

## Finish

```sh
blendk save
blendk close
```

`close` keeps a dirty scene open. `close --discard` confirms an intentional discard;
`close --kill` terminates an unresponsive Blender process.

## References

- Read [scripting.md](references/scripting.md) when authoring or investigating `bpy`
  behavior.
- Read [rendering.md](references/rendering.md) when choosing previews, render engines,
  cameras, image sizes, or devices.
- Read [security.md](references/security.md) for profiles, trusted-code boundaries,
  containers, online mode, and embedded-file auto-execution.
