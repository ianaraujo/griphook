# Griphook — MS SQL Query CLI for AI agents

A command line interface application built to safely execute read-only SQL queries against Microsoft SQL Server. It enforces strict read-only policies, injects automatic row limits, and provides structured JSON output for seamless integration with AI agents.

## Features

- **Read-only enforcement**: Blocks all write operations via sqlglot AST parsing (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, EXEC, and nested writes inside CTEs)
- **Automatic row limit**: Injects `TOP 100` when no explicit limit is present
- **Two-tier timeout**: Warning at 5 seconds, hard kill at 20 seconds
- **JSON output**: Clean stdout JSON for agent consumption; warnings and errors go to stderr
- **Flexible config**: Credentials via env vars, `.env` file, or `griphook configure`
- **Windows Authentication**: Connect as the current Windows account, with no password stored anywhere
- **Windows installer**: A per-user setup wizard that needs no admin rights, no Python, and no terminal

## Setup

### Dev / testing

```bash
cp .env.example .env   # fill in credentials
uv sync
uv run griphook query "SELECT TOP 10 * FROM orders"
```

### Installed CLI

```bash
uv tool install .
griphook configure
griphook query "SELECT TOP 10 * FROM orders"
```

### Windows

End users install from the setup wizard — see
[docs/instalacao-windows.md](docs/instalacao-windows.md) (in Portuguese, written
for non-technical users). The installer is per-user, needs no administrator
rights, bundles its own Python runtime, writes the configuration, and drops the
Claude Code skill into `%USERPROFILE%\.claude\skills\griphook`.

Building it is automated by the `Windows installer` GitHub Actions workflow —
push a `v*` tag or run it manually. To build locally on Windows:

```powershell
uv sync --group build
uv run pyinstaller packaging/griphook.spec --noconfirm --distpath dist --workpath build
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 packaging\griphook.iss
```

Dropping `packaging\msodbcsql18.msi` in place before compiling makes the
installer able to install the Microsoft ODBC driver itself (the one step that
asks for elevation) on machines that lack it. Set the repository variable
`ODBC_MSI_URL` to have CI fetch it automatically.

### Authentication modes

| Mode | `SQL_AUTH` | Requires |
|---|---|---|
| SQL Server login (default) | `sql` | `SQL_USER`, `SQL_PASSWORD` |
| Windows Authentication | `windows` | Nothing — connects as the current Windows account |

```bash
griphook configure --non-interactive --server srv01 --database vendas --auth windows
griphook test-connection    # prints the login name it connected as
```

The ODBC driver is auto-detected (preferring 18, falling back to 17). Override
it with `SQL_DRIVER` when needed.

## Usage

```bash
# Execute a query
griphook query "SELECT * FROM agents"

# Explore a table with compact defaults
griphook explore onshore.produtos

# Expand related tables and switch to verbose metadata
griphook explore onshore.produtos --depth 2 --verbose

# Tune token footprint
griphook explore onshore.produtos --sample-rows 1 --profile-columns 2 --top-values 3

# Validate without executing
griphook query --dry-run "SELECT * FROM agents"

# Use an explicit env file
griphook --env-file /path/to/.env query "SELECT 1"
```

### Output

**Success** (`exit 0`):
```json
{
  "row_count": 2,
  "columns": ["id", "name"],
  "rows": [{"id": 1, "name": "Alice"}],
  "duration_ms": 42,
  "warned": false
}
```

The `explore` command returns a compact table report by default with:

- table identity and row count
- compact column metadata
- primary and unique constraints
- direct incoming and outgoing foreign keys
- a small sample set and targeted profiles
- no neighbor expansion unless `--depth` is set above `0`

Use `--verbose` to include full column metadata, check constraints, and index detail.
Use `--sample-rows`, `--profile-columns`, and `--top-values` to reduce token usage further.

**Error** (`exit 1` — blocked/timeout/SQL error, `exit 2` — config/connection error):
```json
{"error": "Query contains a blocked statement type: DELETE. Only SELECT queries are permitted."}
```

**Slow query warning** on stderr (`exit 0`):
```json
{"warning": "Query exceeded warn threshold (5s)"}
```

## Safety

All destructive operations are blocked by sqlglot AST analysis — not just keyword matching:

- Write operations: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`
- Schema changes: `DROP`, `ALTER`, `CREATE`
- Procedure execution: `EXEC`, `EXECUTE`, `xp_cmdshell`
- Nested writes inside CTEs and subqueries

## Testing

```bash
uv run pytest           # run all tests
uv run pytest -v        # verbose output
```

### Test inventory (38 tests, zero DB required)

**`validate_select_only`**
- `DELETE` statement is blocked
- `INSERT` statement is blocked
- `DROP TABLE` is blocked
- `EXEC xp_cmdshell` is blocked (`sqlglot.exp.Execute` type)
- Multi-statement input containing a write is blocked
- Nested `DELETE` inside a CTE is blocked via AST walk
- Plain `SELECT` is allowed
- Valid CTE (`WITH … SELECT`) is allowed
- Empty string raises `BlockedQueryError`
- Whitespace-only string raises `BlockedQueryError`

**`inject_limit`**
- `TOP 100` is injected when no limit is present
- Injection is skipped when `TOP N` is already present
- Injection is skipped when `OFFSET/FETCH` is present
- `TOP` is placed before `DISTINCT` for `SELECT DISTINCT` queries

**`load_config`**
- Reads credentials from an explicit env file (`tmp_path` fixture, no subprocess)
- Windows Authentication does not require `SQL_USER`/`SQL_PASSWORD`
- SQL authentication still requires them
- `SQL_AUTH` aliases (`integrated`, `trusted`, `sspi`) normalise to `windows`

**Connection string and driver detection**
- SQL auth emits `UID`/`PWD`; Windows auth emits `Trusted_Connection=yes` and no credentials
- An explicit `SQL_DRIVER` is used verbatim
- Auto-detection prefers Driver 18, falls back to 17, and raises when none is installed

**`configure --non-interactive`** (the path the Windows installer drives)
- Windows auth writes `SQL_AUTH=windows` with no credentials in the file
- SQL auth without `--user`/`--password` exits 2 and writes nothing
- A missing ODBC driver degrades to a config file without `SQL_DRIVER`

**CLI (`typer.testing.CliRunner`)**
- `--dry-run` with a valid query exits 0 and returns `{"dry_run": true, "sql": "...TOP 100..."}`
- `--dry-run` with a blocked query exits 1 with an `error` key
- Missing config exits 2 with an `error` key

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- SQL Server with ODBC Driver 18

## License

MIT
