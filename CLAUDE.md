# Griphook

Read-only SQL Server CLI query tool designed to be invoked by AI agents (Claude Code, etc.).

## Two ways to run

### Dev / testing — `uv run` with a project `.env`

```bash
cp .env.example .env   # fill in credentials once
uv run griphook query "SELECT TOP 10 * FROM orders"
```

The CLI auto-discovers `.env` in the current working directory, so `uv run` from the project root picks it up automatically. No installation needed.

### Installed CLI — `griphook` from anywhere

```bash
uv tool install .               # installs to ~/.local/bin/griphook
griphook configure              # interactive credential setup → ~/.config/griphook/.env
griphook query "SELECT TOP 10 * FROM orders"
```

After `configure`, credentials persist at `~/.config/griphook/.env` and are used whenever no local `.env` is found.

### Explicit env file (override)

```bash
griphook --env-file /path/to/.env query "SELECT 1"
```

`--env-file` takes priority over both auto-discovery and the user config.

## Config priority

`env vars` > `--env-file` > `cwd .env` > `~/.config/griphook/.env`

Required vars: `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD`.

## Output

Always JSON on stdout. Errors are JSON on stderr with exit code 1.
If no `TOP N` or `OFFSET/FETCH` clause is present, `TOP 100` is injected automatically.

## Project layout

```
src/griphook/
  __init__.py   # exports app
  main.py       # Typer CLI + query execution logic
  config.py     # Config dataclass, env loading
pyproject.toml
.env.example
```

## Dev

```bash
uv run griphook --help
uv run griphook query "SELECT 1"
uv run python -c "from griphook.main import validate_query; validate_query('SELECT 1')"
```
