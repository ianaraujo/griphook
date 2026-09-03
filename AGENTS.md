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

Required vars: `SQL_SERVER`, `SQL_DATABASE`, plus `SQL_USER`/`SQL_PASSWORD` unless
`SQL_AUTH=windows` (Windows Authentication, which needs neither). `SQL_DRIVER`
overrides ODBC driver auto-detection (18 → 17 → older).

## Windows

`griphook test-connection` verifies a connection and prints the login it
connected as. The Windows installer (`packaging/griphook.iss`, built by
`.github/workflows/windows-installer.yml`) is a per-user Inno Setup wizard that
calls `griphook configure --non-interactive` at the end; see
`docs/instalacao-windows.md` for the end-user guide.

## Output

Always JSON on stdout. Errors are JSON on stderr with exit code 1.
If no `TOP N` or `OFFSET/FETCH` clause is present, `TOP 100` is injected automatically.

## Project layout

```
src/griphook/
  __init__.py   # exports app
  main.py       # Typer CLI + query execution logic
  config.py     # Config dataclass, env loading, auth mode, ODBC driver detection
  proxy.py      # AST validation, limit injection, query execution, explore
packaging/
  launcher.py       # PyInstaller entry point
  griphook.spec     # PyInstaller build (onedir)
  version_info.txt  # Windows version resource
  griphook.iss      # Inno Setup installer (per-user, no admin)
pyproject.toml
.env.example
```

## Dev

```bash
uv run griphook --help
uv run griphook query "SELECT 1"
uv run python -c "from griphook.main import validate_query; validate_query('SELECT 1')"
```
