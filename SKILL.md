---
name: mssql-query-skill
description: "Execute read-only SQL queries against a SQL Server database. Use when the user asks to query, inspect, explore, or retrieve data from a SQL Server database. Supports SELECT statements only — all write operations are blocked."
---

# SQL Server Read-Only Query

Run validated, read-only SQL queries against a SQL Server database using `execute_query.py`.

## Setup

Before first use, install dependencies using uv:

```bash
cd {baseDir}
uv sync
```

This installs `pymssql` and creates a virtual environment based on `pyproject.toml`.

## Environment Variables

The script reads connection details from a `.env` file at the **project root** (the directory where Claude Code is running). If the file does not exist, ask the user to create one based on `{baseDir}/.env.example`.

Required variables:

| Variable    | Description                          |
|-------------|--------------------------------------|
| `DB_HOST`   | SQL Server hostname or IP            |
| `DB_PORT`   | Port (default `1433`)                |
| `DB_NAME`   | Database name                        |
| `DB_USER`   | Login username                       |
| `DB_PASS`   | Login password                       |

**Never hardcode credentials. Never print or log the password.**

## Running a Query

```bash
cd {baseDir}
uv run python execute_query.py "SELECT TOP 10 * FROM dbo.Customers"
```

The script:

1. Validates that the SQL is read-only (blocks DELETE, UPDATE, INSERT, DROP, ALTER, TRUNCATE, EXEC, MERGE, GRANT, REVOKE, CREATE, SET, and more).
2. Automatically appends `TOP 100` if no TOP or OFFSET/FETCH clause is present, to prevent unbounded result sets.
3. Returns results as a JSON array printed to stdout.
4. Prints errors to stderr with a non-zero exit code.

## Output Format

On success the script prints a JSON object to stdout:

```json
{
  "row_count": 3,
  "columns": ["id", "name", "email"],
  "rows": [
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    {"id": 2, "name": "Bob",   "email": "bob@example.com"},
    {"id": 3, "name": "Carol", "email": "carol@example.com"}
  ]
}
```

On failure it prints a JSON error object to stderr and exits with code 1:

```json
{"error": "Blocked keyword detected: DELETE"}
```

## Guidelines

- **Always use this script** to run SQL. Do not open a raw database connection yourself.
- **Prefer specific column lists** over `SELECT *` when the user's question targets known fields.
- If the user needs more than 100 rows, pass an explicit `TOP N` (up to 1000) or use `OFFSET/FETCH`. The script will not inject a limit when one is already present.
- If a query fails with a connection error, confirm that the `.env` file exists and has correct values.
- Present results to the user as a formatted markdown table or summary — do not dump raw JSON unless asked.
- For schema exploration, use queries like:
  - `SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES ORDER BY TABLE_SCHEMA, TABLE_NAME`
  - `SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'MyTable'`