# MS SQL Query Skill for Claude Code

A Claude Code skill that enables AI agents to execute **read-only** SQL queries against Microsoft SQL Server databases safely.

## Features

- **Read-only enforcement**: Blocks all write operations (INSERT, UPDATE, DELETE, DROP, etc.)
- **Automatic query limits**: Adds TOP 100 to queries without explicit limits
- **Safe execution**: Validates queries before execution, strips comments/strings to prevent injection
- **JSON output**: Returns structured results for easy processing
- **Environment-based config**: Credentials stored in `.env` files (never hardcoded)

## Setup

1. **Install dependencies:**
   ```bash
   uv sync
   ```

2. **Configure database connection:**
   
Create a `.env` file in your project root (copy from `.env.example`):

```bash
DB_HOST=your-server.database.windows.net
DB_PORT=1433
DB_NAME=your_database
DB_USER=your_username
DB_PASS=your_password
```

## Usage

Run queries using the CLI:

```bash
uv run python execute_query.py "SELECT TOP 10 * FROM dbo.Customers WHERE Country = 'USA'"
```

Or let the AI use it automatically when you ask database questions.

### Output Format

**Success:**
```json
{
  "row_count": 2,
  "columns": ["id", "name", "email"],
  "rows": [
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    {"id": 2, "name": "Bob", "email": "bob@example.com"}
  ]
}
```

**Error:**

```json
{"error": "Blocked keyword detected: DELETE"}
```

## Safety

This tool is designed for **exploration and analysis only**. All destructive operations are blocked:

- Write operations: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`
- Schema changes: `DROP`, `ALTER`, `CREATE`
- Dangerous procedures: `EXEC`, `SP_*`, `XP_*`

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- SQL Server database access

## License

MIT
