# Development

- Use `uv sync --locked`; do not install packages with pip or unpinned uv commands.
- Keep Typer at the CLI boundary and use the Python standard library elsewhere.
- Do not add dependencies without an explicit supply-chain review.
- Keep headed and headless behavior on the same command and handler surface.
- Run `uv run --locked python -m unittest discover -s tests -v` after code changes.
- Set `BLENDK_TEST_HEADED=1` when a desktop is available to exercise headed integration.
- Do not commit or publish unless the user explicitly asks.

Use [`skills/blendk/SKILL.md`](skills/blendk/SKILL.md) for the operator workflow.
