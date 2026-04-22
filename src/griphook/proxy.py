import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

import pyodbc
import sqlglot

from griphook.config import Config

DEFAULT_ROW_LIMIT = 100

BLOCKED_STATEMENT_TYPES = (
    sqlglot.exp.Insert,
    sqlglot.exp.Update,
    sqlglot.exp.Delete,
    sqlglot.exp.Drop,
    sqlglot.exp.Create,
    sqlglot.exp.Alter,
    sqlglot.exp.Execute,  # EXEC / stored procedure calls
    sqlglot.exp.Command,  # unrecognised DDL/DML fallback
)


class BlockedQueryError(Exception):
    """Raised when a non-SELECT query is attempted."""


class QueryTimeoutError(Exception):
    """Raised when a query exceeds the kill timeout."""

    def __init__(self, message: str, duration_ms: int):
        super().__init__(message)
        self.duration_ms = duration_ms


class DatabaseConnectionError(Exception):
    """Raised when the database connection cannot be established."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict]
    duration_ms: int
    row_count: int
    warned: bool  # True if query exceeded warn threshold


def validate_select_only(sql: str) -> None:
    """Parse SQL and raise BlockedQueryError if any non-SELECT statement is found."""
    try:
        statements = sqlglot.parse(sql, dialect="tsql")
    except sqlglot.errors.ParseError as e:
        raise BlockedQueryError(f"Could not parse SQL: {e}")

    valid_stmts = [s for s in statements if s is not None]
    if not valid_stmts:
        raise BlockedQueryError("Empty query.")

    for stmt in valid_stmts:
        if isinstance(stmt, BLOCKED_STATEMENT_TYPES):
            kind = type(stmt).__name__.upper()
            raise BlockedQueryError(
                f"Query contains a blocked statement type: {kind}. "
                "Only SELECT queries are permitted."
            )
        # Walk the full AST to catch writes nested inside CTEs, subqueries, etc.
        for node in stmt.walk():
            if isinstance(node, BLOCKED_STATEMENT_TYPES):
                kind = type(node).__name__.upper()
                raise BlockedQueryError(
                    f"Query contains a nested blocked statement: {kind}."
                )


def inject_limit(sql: str, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Add TOP N to the outermost SELECT if no row limit is already present."""
    tree = sqlglot.parse_one(sql, dialect="tsql")
    if isinstance(tree, sqlglot.exp.Select) and tree.args.get("limit") is None:
        tree.set("limit", sqlglot.exp.Limit(expression=sqlglot.exp.Literal.number(limit)))
    return tree.sql(dialect="tsql")


def _run_query(connection_string: str, sql: str) -> tuple[list[str], list[tuple]]:
    """Execute query and return (columns, rows). Runs in a worker thread."""
    try:
        conn = pyodbc.connect(connection_string, timeout=5)
    except pyodbc.Error as exc:
        raise DatabaseConnectionError(f"Could not connect to database: {exc}") from exc
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return columns, rows
    finally:
        conn.close()


def execute(sql: str, config: Config) -> QueryResult:
    """
    Full proxy pipeline:
      1. Validate SELECT-only via sqlglot AST
      2. Inject TOP N if no row limit present
      3. Execute with two-tier timeout (warn at warn_timeout, kill at kill_timeout)
    """
    validate_select_only(sql)
    sql = inject_limit(sql)

    warned = False
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_query, config.connection_string, sql)

        try:
            columns, raw_rows = future.result(timeout=config.warn_timeout)
        except FuturesTimeoutError:
            warned = True
            try:
                columns, raw_rows = future.result(
                    timeout=config.kill_timeout - config.warn_timeout
                )
            except FuturesTimeoutError:
                duration_ms = int((time.monotonic() - start) * 1000)
                future.cancel()
                raise QueryTimeoutError(
                    f"Query exceeded kill timeout of {config.kill_timeout}s and was terminated.",
                    duration_ms=duration_ms,
                )

    duration_ms = int((time.monotonic() - start) * 1000)
    rows = [dict(zip(columns, row)) for row in raw_rows]

    return QueryResult(
        columns=columns,
        rows=rows,
        duration_ms=duration_ms,
        row_count=len(rows),
        warned=warned,
    )
