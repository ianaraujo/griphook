---
name: griphook
description: Query Microsoft SQL Server databases read-only through the `griphook` CLI. Use whenever the user asks to inspect data, explore a schema, count rows, join tables, or otherwise answer a question backed by a SQL Server instance. Do NOT use for writes — griphook blocks them at the AST level.
---

# Griphook — read-only MS SQL Server CLI

`griphook` is a Typer CLI that runs a single SELECT statement against a configured SQL Server and prints results as JSON on stdout. It is the preferred tool whenever the user needs data from a SQL Server database.

## When to use

- User asks a question that can only be answered by querying a SQL Server database ("how many orders…", "what's in the `agents` table…", "show me the schema of X").
- User mentions a database name, table name, or hands you credentials `.env`.
- You need to discover schema (tables, columns, keys) before writing application code against a database.

Do **not** use griphook for: writes, DDL, stored-proc execution, or any database that isn't SQL Server. It will refuse all of those.

## How to run

```bash
griphook query "SELECT TOP 10 name FROM sys.tables ORDER BY name"
```

Always pass the SQL as a single quoted argument. Prefer double quotes outside and single quotes for SQL string literals (`griphook query "SELECT * FROM t WHERE name = 'Alice'"`).

### Useful flags

- `--dry-run` — validate and show the rewritten SQL (with `TOP 100` injection) without hitting the DB. Use this when building a complex query or when the user hasn't confirmed they want it run.
- `--env-file /path/.env` — point at a specific credentials file. Only use when the user explicitly gives you a path; otherwise rely on auto-discovery.

### Output contract

Stdout on success (exit 0):
```json
{"row_count": 2, "columns": ["id","name"], "rows": [{"id":1,"name":"Alice"}], "duration_ms": 42, "warned": false}
```

Stderr on error (exit 1 = query/timeout/SQL error, exit 2 = config/connection error):
```json
{"error": "..."}
```

A slow-query warning may appear on stderr with exit 0 — the result on stdout is still valid.

## Rules the CLI enforces (don't fight them)

1. **SELECT-only.** `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`, `DROP`, `ALTER`, `CREATE`, `EXEC`/`EXECUTE`, and writes nested inside CTEs are all blocked by sqlglot AST analysis. Don't try to smuggle them.
2. **Automatic `TOP 100` injection.** If your query has no `TOP N` and no `OFFSET/FETCH`, 100 is added to the outer SELECT. To see more rows, write `TOP 1000` explicitly. To see fewer, write `TOP 10`.
3. **Two-tier timeout.** Warn at `SQL_WARN_TIMEOUT` (default 5s), hard kill at `SQL_KILL_TIMEOUT` (default 20s). If you get `warned: true`, tighten or review the query before re-running.
4. **Single statement per call.** Multi-statement batches where any statement is a write will be rejected outright.

## Working effectively

### Exploring an unfamiliar database

Start by discovering schema, not by guessing table names. Prefer `INFORMATION_SCHEMA` / `sys` catalogs:

```bash
# List user tables
griphook query "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"

# Columns of a table
griphook query "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='orders' ORDER BY ORDINAL_POSITION"

# Primary / foreign keys
griphook query "SELECT name, type_desc FROM sys.key_constraints WHERE parent_object_id = OBJECT_ID('dbo.orders')"

# Sample data (griphook will cap at TOP 100 automatically)
griphook query "SELECT * FROM dbo.orders"
```

### Iterating toward an answer

1. Dry-run first for complex queries: `griphook query --dry-run "..."`. Check the injected SQL is what you expect.
2. Start narrow. Use `TOP 5` or aggregate (`COUNT(*)`, `MIN`, `MAX`) to confirm shape before pulling rows.
3. If you get a timeout warning, add filters, indexes-friendly predicates, or switch to aggregates instead of scanning rows.
4. Re-check column names from `INFORMATION_SCHEMA.COLUMNS` rather than guessing — griphook returns a clear SQL error on typos but round-trips are slow.

### Parsing the output

The stdout JSON has `row_count`, `columns`, `rows`, `duration_ms`, `warned`. Parse it; don't regex it. If you pipe through `jq`, remember `jq` reads stdout and griphook writes errors to stderr — check exit codes.

### Error handling

- Exit 1 `BlockedQueryError` — rewrite the query as a plain SELECT. Don't retry the same statement.
- Exit 1 `QueryTimeoutError` — narrow the query. Add WHERE, reduce joins, aggregate.
- Exit 2 config error — the user hasn't configured credentials. Tell them to run `griphook configure` (interactive) or provide `--env-file`. Do **not** run `configure` yourself; it prompts for a password.
- Exit 2 connection error — the DB is unreachable or the credentials are wrong. Surface the error verbatim to the user; don't retry blindly.

## Things to avoid

- Don't chain queries into a single call with `;` — one statement per invocation.
- Don't use `SELECT *` on wide tables without `TOP`. You'll burn the timeout budget.
- Don't attempt write operations "just to check" — they're blocked and waste a round trip.
- Don't invoke `griphook configure` on behalf of the user; it's interactive and needs their password.
- Don't fabricate table or column names. Always confirm via catalog views first.
- Don't echo credentials or connection strings back to the user.

## Quick reference

| Task | Command |
|------|---------|
| Run a query | `griphook query "SELECT ..."` |
| Validate only | `griphook query --dry-run "SELECT ..."` |
| Use specific creds | `griphook --env-file /path/.env query "SELECT ..."` |
| List tables | `griphook query "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"` |
| Describe table | `griphook query "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='X'"` |
| Row count | `griphook query "SELECT COUNT(*) AS n FROM dbo.X"` |
