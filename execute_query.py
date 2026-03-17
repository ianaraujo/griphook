#!/usr/bin/env python3
"""
Read-only SQL Server query tool for Claude Code.

Usage:
    python execute_query.py "SELECT TOP 10 * FROM dbo.Customers"

Reads connection details from a .env file in the current working directory.
Validates that the query is read-only before execution.
Automatically limits results to 100 rows when no explicit limit is present.
"""

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PORT = 1433
DEFAULT_ROW_LIMIT = 100

# Keywords that indicate a write / dangerous operation.
# Matched case-insensitive against a normalised version of the query
# (comments and string literals stripped out).
BLOCKED_KEYWORDS: list[str] = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "EXEC",
    "EXECUTE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "DENY",
    "CREATE",
    "SET",
    "BACKUP",
    "RESTORE",
    "DBCC",
    "BULK",
    "OPENROWSET",
    "OPENQUERY",
    "OPENDATASOURCE",
    "SHUTDOWN",
    "KILL",
    "RECONFIGURE",
    "INTO",  # SELECT … INTO
]

# Prefixes — matched with \b only on the left side so SP_HELP, XP_CMDSHELL etc.
# are caught, but the trailing underscore doesn't require a word boundary.
BLOCKED_PREFIXES: list[str] = [
    "SP_",
    "XP_",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env(path: str) -> dict[str, str]:
    """Parse a simple .env file (KEY=VALUE per line) and return a dict."""
    env: dict[str, str] = {}
    if not os.path.isfile(path):
        return env
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            env[key] = value
    return env


def _strip_sql_noise(sql: str) -> str:
    """Remove string literals and comments so keyword search isn't fooled."""
    # Remove single-line comments (-- …)
    sql = re.sub(r"--[^\n]*", " ", sql)
    # Remove block comments (/* … */)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove string literals ('…')
    sql = re.sub(r"'[^']*'", " ", sql)
    # Collapse whitespace
    sql = re.sub(r"\s+", " ", sql).strip()
    return sql


def validate_query(sql: str) -> None:
    """Raise ValueError if the query contains blocked keywords."""
    normalised = _strip_sql_noise(sql).upper()

    for kw in BLOCKED_KEYWORDS:
        # \b on both sides ensures we don't match substrings in identifiers
        # (e.g. 'settings' should not trigger SET, 'updated_at' should not trigger UPDATE).
        pattern = rf"\b{re.escape(kw)}\b"
        if re.search(pattern, normalised):
            raise ValueError(f"Blocked keyword detected: {kw}")

    for prefix in BLOCKED_PREFIXES:
        pattern = rf"\b{re.escape(prefix)}"
        if re.search(pattern, normalised):
            raise ValueError(f"Blocked keyword detected: {prefix}")

    # Extra: reject anything that doesn't start with SELECT / WITH (CTEs)
    first_word = normalised.lstrip("( ").split()[0] if normalised.strip() else ""
    if first_word not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only SELECT (and WITH … SELECT) queries are allowed. "
            f"Got: {first_word}"
        )


def _has_row_limit(sql: str) -> bool:
    """Return True if the query already contains a TOP or OFFSET/FETCH clause."""
    upper = _strip_sql_noise(sql).upper()
    if re.search(r"\bTOP\s+\d+", upper):
        return True
    if re.search(r"\bOFFSET\b.*\bFETCH\b", upper, flags=re.DOTALL):
        return True
    return False


def inject_limit(sql: str, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """If no explicit limit exists, inject TOP N after the first SELECT."""
    if _has_row_limit(sql):
        return sql

    # Find the first SELECT keyword (case-insensitive) and insert TOP N.
    pattern = re.compile(r"\bSELECT\b", re.IGNORECASE)
    match = pattern.search(sql)
    if match:
        pos = match.end()
        sql = f"{sql[:pos]} TOP {limit}{sql[pos:]}"
    return sql


def get_connection_params() -> dict:
    """Resolve connection parameters from .env + environment variables."""
    # .env at the current working directory (where Claude Code runs)
    env_file = os.path.join(os.getcwd(), ".env")
    file_env = _load_env(env_file)

    def _get(key: str, default: str | None = None) -> str:
        # Environment variables take precedence over .env file values.
        return os.environ.get(key, file_env.get(key, default or ""))

    host = _get("DB_HOST")
    port = _get("DB_PORT", str(DEFAULT_PORT))
    name = _get("DB_NAME")
    user = _get("DB_USER")
    password = _get("DB_PASS")

    missing = []
    if not host:
        missing.append("DB_HOST")
    if not name:
        missing.append("DB_NAME")
    if not user:
        missing.append("DB_USER")
    if not password:
        missing.append("DB_PASS")

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Create a .env file in {os.getcwd()} or export them."
        )

    return {
        "server": host,
        "port": int(port),
        "database": name,
        "user": user,
        "password": password,
    }


def execute_query(sql: str) -> dict:
    """Validate, limit, execute the query and return structured results."""
    try:
        import pymssql
    except ImportError:
        raise ImportError(
            "pymssql is not installed. Run: uv sync from the skill directory."
        )

    validate_query(sql)
    sql = inject_limit(sql)
    params = get_connection_params()

    conn = pymssql.connect(
        server=params["server"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        login_timeout=10,
        timeout=30,
        as_dict=True,
    )

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
    finally:
        conn.close()

    return {
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        _fail("Usage: python execute_query.py \"SELECT ...\"")

    sql = sys.argv[1]

    try:
        result = execute_query(sql)
    except (ValueError, EnvironmentError, ImportError) as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"Query execution failed: {exc}")

    print(json.dumps(result, indent=2, default=str))


def _fail(message: str) -> None:
    print(json.dumps({"error": message}), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()