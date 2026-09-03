# Rendering

## Preview

`blendk preview` produces a fast Workbench camera render in headed and headless
sessions. It is useful for composition, scale, silhouette, contact, and occlusion.
Workbench presents geometry clearly while leaving shader, scene-light, and world-light
evaluation to the active render engine.

The default image is 512×512. Camera, frame, and image size can be selected per pass:

```sh
blendk preview --size 800x450
blendk preview --camera Camera_Close --frame 24
```

## Render

`blendk render` uses the scene's active registered engine and its existing materials,
lighting, color management, quality, and device configuration:

```sh
blendk render --size 1280x720
blendk render final-review.png --camera Camera_Final
```

This same command surface covers EEVEE, Cycles, Workbench, and installed third-party
engines. `blendk --json inspect` reports available engines and device state.

Preview and render restore the active engine, camera, frame, resolution, image format,
and temporary Workbench settings. Each command returns an absolute PNG path for visual
inspection.

## Iteration

Choose the least expensive image that answers the current question:

1. Workbench preview for layout and geometry.
2. EEVEE or another fast configured engine for materials and lighting.
3. Cycles or the production engine for milestone and final evaluation.

Treat the resulting pixels as scene evidence. Adjust one coherent aspect, render again,
and preserve strong milestones with `blendk save`.
