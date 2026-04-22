# Griphook — MS SQL Query Skill for Claude Code

A Claude Code skill that enables AI agents to execute **read-only** SQL queries against Microsoft SQL Server databases safely.

## Features

- **Read-only enforcement**: Blocks all write operations via sqlglot AST parsing (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, EXEC, and nested writes inside CTEs)
- **Automatic row limit**: Injects `TOP 100` when no explicit limit is present
- **Two-tier timeout**: Warning at 5 s, hard kill at 20 s
- **JSON output**: Clean stdout JSON for agent consumption; warnings and errors go to stderr
- **Flexible config**: Credentials via env vars, `.env` file, or `griphook configure`

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

## Usage

```bash
# Execute a query
griphook query "SELECT * FROM agents"

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

### Test inventory (18 tests, zero DB required)

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
