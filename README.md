# blendk

Stateful Blender control for agents and humans, in a terminal or a shared Blender
window.

Requires [uv](https://docs.astral.sh/uv/) and Blender 5.2 LTS.

```sh
uv tool install --constraints constraints.txt .
blendk doctor
blendk open scene.blend
blendk --json inspect
blendk preview
```

See [setup](docs/setup.md) and [security](docs/security.md).

Agent guidance lives in [`skills/blendk/SKILL.md`](skills/blendk/SKILL.md).

