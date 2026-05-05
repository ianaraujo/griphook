import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class QualifiedTableName:
    schema: str
    table: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True)
class TableMetadata:
    table: QualifiedTableName
    row_count: int
    columns: list[dict]
    primary_key: list[dict]
    unique_constraints: list[dict]
    check_constraints: list[dict]
    indexes: list[dict]
    outgoing_fks: list[dict]
    incoming_fks: list[dict]


@dataclass(frozen=True)
class ExploreOptions:
    depth: int = 0
    verbose: bool = False
    sample_rows: int = 2
    profile_columns: int = 3
    top_values: int = 3


def _quote_identifier(identifier: str) -> str:
    return f"[{identifier.replace(']', ']]')}]"


def parse_table_name(table_name: str, default_schema: str = "dbo") -> QualifiedTableName:
    """Parse a table name in `schema.table` or `table` form."""
    cleaned = table_name.strip()
    if not cleaned:
        raise ValueError("Table name cannot be empty.")

    parts = cleaned.split(".")
    if len(parts) == 1:
        return QualifiedTableName(schema=default_schema, table=parts[0])
    if len(parts) == 2:
        return QualifiedTableName(schema=parts[0], table=parts[1])

    raise ValueError("Table name must be in `schema.table` or `table` form.")


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


def _run_query(
    connection_string: str, sql: str, params: tuple[Any, ...] = ()
) -> tuple[list[str], list[tuple]]:
    """Execute query and return (columns, rows). Runs in a worker thread."""
    try:
        conn = pyodbc.connect(connection_string, timeout=5)
    except pyodbc.Error as exc:
        raise DatabaseConnectionError(f"Could not connect to database: {exc}") from exc
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return columns, rows
    finally:
        conn.close()


def execute_query(sql: str, config: Config, params: tuple[Any, ...] = ()) -> QueryResult:
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
        future = executor.submit(_run_query, config.connection_string, sql, params)

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


def _group_index_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["index_name"],
            {
                "name": row["index_name"],
                "type": row["type_desc"],
                "unique": row["is_unique"],
                "primary_key": row["is_primary_key"],
                "filter_definition": row.get("filter_definition"),
                "columns": [],
                "included_columns": [],
            },
        )
        if row["is_included_column"]:
            entry["included_columns"].append(row["column_name"])
        else:
            entry["columns"].append(row["column_name"])
    return list(grouped.values())


def _group_foreign_key_rows(rows: list[dict], direction: str) -> list[dict]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["fk_name"],
            {
                "name": row["fk_name"],
                "columns": [],
            },
        )
        entry["columns"].append(row["child_column"] if direction == "outgoing" else row["parent_column"])
        if direction == "outgoing":
            references = entry.setdefault(
                "references",
                {
                    "schema": row["parent_schema"],
                    "table": row["parent_table"],
                    "columns": [],
                },
            )
            references["columns"].append(row["parent_column"])
        else:
            referenced_by = entry.setdefault(
                "referenced_by",
                {
                    "schema": row["child_schema"],
                    "table": row["child_table"],
                    "columns": [],
                },
            )
            referenced_by["columns"].append(row["child_column"])
    return list(grouped.values())


def _compact_column(column: dict) -> dict[str, Any]:
    return {
        "name": column["column_name"],
        "data_type": column["data_type"],
        "nullable": column["is_nullable"],
        "identity": column["is_identity"],
        "computed": column["is_computed"],
    }


def _compact_relationship_table(table_name: QualifiedTableName) -> dict[str, str]:
    return {
        "schema": table_name.schema,
        "name": table_name.table,
        "qualified_name": table_name.qualified_name,
    }


def _table_role(metadata: TableMetadata) -> str:
    outgoing = len(_group_foreign_key_rows(metadata.outgoing_fks, "outgoing"))
    incoming = len(_group_foreign_key_rows(metadata.incoming_fks, "incoming"))
    column_names = {column["column_name"].lower() for column in metadata.columns}
    has_measure_like_columns = any(
        token in column_names
        for token in ("amount", "price", "value", "total", "qty", "quantity", "balance")
    )

    if outgoing >= 2 and incoming == 0:
        return "junction"
    if incoming >= 2 and outgoing <= 1:
        return "core_entity"
    if outgoing == 0 and incoming == 0:
        if len(metadata.columns) <= 10 and metadata.row_count <= 1000:
            return "lookup"
        return "standalone"
    if has_measure_like_columns and incoming >= 1:
        return "fact"
    return "entity"


def _build_base_summary(metadata: TableMetadata, profile_columns: list[dict]) -> dict[str, Any]:
    outgoing_groups = _group_foreign_key_rows(metadata.outgoing_fks, "outgoing")
    incoming_groups = _group_foreign_key_rows(metadata.incoming_fks, "incoming")
    return {
        "table_role": _table_role(metadata),
        "row_count": metadata.row_count,
        "column_count": len(metadata.columns),
        "primary_key_columns": metadata.primary_key[0]["columns"] if metadata.primary_key else [],
        "unique_constraint_count": len(metadata.unique_constraints),
        "check_constraint_count": len(metadata.check_constraints),
        "index_count": len(_group_index_rows(metadata.indexes)),
        "outgoing_foreign_key_count": len(outgoing_groups),
        "incoming_foreign_key_count": len(incoming_groups),
        "profiled_columns": [column["column_name"] for column in profile_columns],
    }


def _load_table_metadata(table: QualifiedTableName, config: Config) -> TableMetadata:
    columns_sql = (
        "SELECT "
        "  c.column_id, "
        "  c.name AS column_name, "
        "  ty.name AS data_type, "
        "  c.max_length, "
        "  c.precision, "
        "  c.scale, "
        "  c.is_nullable, "
        "  c.is_identity, "
        "  c.is_computed "
        "FROM sys.columns c "
        "JOIN sys.types ty ON c.user_type_id = ty.user_type_id "
        "JOIN sys.tables t ON c.object_id = t.object_id "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "WHERE s.name = ? AND t.name = ? "
        "ORDER BY c.column_id"
    )
    columns = execute_query(columns_sql, config, (table.schema, table.table)).rows
    if not columns:
        raise RuntimeError(f"Table not found: {table.qualified_name}")

    constraints_sql = (
        "SELECT "
        "  kc.name AS constraint_name, "
        "  kc.type_desc, "
        "  c.name AS column_name, "
        "  ic.key_ordinal "
        "FROM sys.key_constraints kc "
        "JOIN sys.index_columns ic ON kc.parent_object_id = ic.object_id AND kc.unique_index_id = ic.index_id "
        "JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id "
        "JOIN sys.tables t ON kc.parent_object_id = t.object_id "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "WHERE s.name = ? AND t.name = ? "
        "ORDER BY kc.type_desc, kc.name, ic.key_ordinal"
    )
    constraints_rows = execute_query(constraints_sql, config, (table.schema, table.table)).rows

    check_constraints_sql = (
        "SELECT "
        "  cc.name AS check_constraint_name, "
        "  cc.definition "
        "FROM sys.check_constraints cc "
        "JOIN sys.tables t ON cc.parent_object_id = t.object_id "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "WHERE s.name = ? AND t.name = ? "
        "ORDER BY cc.name"
    )
    check_constraints = execute_query(check_constraints_sql, config, (table.schema, table.table)).rows

    indexes_sql = (
        "SELECT "
        "  i.name AS index_name, "
        "  i.type_desc, "
        "  i.is_unique, "
        "  i.is_primary_key, "
        "  i.filter_definition, "
        "  c.name AS column_name, "
        "  ic.key_ordinal, "
        "  ic.is_included_column "
        "FROM sys.indexes i "
        "JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id "
        "JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id "
        "JOIN sys.tables t ON i.object_id = t.object_id "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "WHERE s.name = ? AND t.name = ? AND i.index_id > 0 "
        "ORDER BY i.name, ic.is_included_column, ic.key_ordinal, c.name"
    )
    indexes = execute_query(indexes_sql, config, (table.schema, table.table)).rows

    outgoing_fk_sql = (
        "SELECT "
        "  fk.name AS fk_name, "
        "  OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema, "
        "  OBJECT_NAME(fk.referenced_object_id) AS parent_table, "
        "  pc.name AS child_column, "
        "  rc.name AS parent_column, "
        "  fkc.constraint_column_id "
        "FROM sys.foreign_keys fk "
        "JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id "
        "JOIN sys.columns pc ON fkc.parent_object_id = pc.object_id AND fkc.parent_column_id = pc.column_id "
        "JOIN sys.columns rc ON fkc.referenced_object_id = rc.object_id AND fkc.referenced_column_id = rc.column_id "
        "JOIN sys.tables t ON fk.parent_object_id = t.object_id "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "WHERE s.name = ? AND t.name = ? "
        "ORDER BY fk.name, fkc.constraint_column_id"
    )
    outgoing_fks = execute_query(outgoing_fk_sql, config, (table.schema, table.table)).rows

    incoming_fk_sql = (
        "SELECT "
        "  fk.name AS fk_name, "
        "  OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema, "
        "  OBJECT_NAME(fk.parent_object_id) AS child_table, "
        "  pc.name AS child_column, "
        "  rc.name AS parent_column, "
        "  fkc.constraint_column_id "
        "FROM sys.foreign_keys fk "
        "JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id "
        "JOIN sys.columns pc ON fkc.parent_object_id = pc.object_id AND fkc.parent_column_id = pc.column_id "
        "JOIN sys.columns rc ON fkc.referenced_object_id = rc.object_id AND fkc.referenced_column_id = rc.column_id "
        "JOIN sys.tables t ON fk.referenced_object_id = t.object_id "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "WHERE s.name = ? AND t.name = ? "
        "ORDER BY fk.name, fkc.constraint_column_id"
    )
    incoming_fks = execute_query(incoming_fk_sql, config, (table.schema, table.table)).rows

    row_count_sql = (
        "SELECT SUM(p.rows) AS row_count "
        "FROM sys.tables t "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "JOIN sys.partitions p ON t.object_id = p.object_id "
        "WHERE s.name = ? AND t.name = ? AND p.index_id IN (0, 1) "
        "GROUP BY t.object_id"
    )
    row_count_rows = execute_query(row_count_sql, config, (table.schema, table.table)).rows
    row_count = row_count_rows[0]["row_count"] if row_count_rows else 0

    primary_key: list[dict[str, Any]] = []
    unique_constraints: list[dict[str, Any]] = []
    grouped_constraints = defaultdict(list)
    for row in constraints_rows:
        grouped_constraints[row["constraint_name"]].append(row)

    for name, rows in grouped_constraints.items():
        entry = {
            "name": name,
            "columns": [row["column_name"] for row in sorted(rows, key=lambda r: r["key_ordinal"])],
        }
        if rows[0]["type_desc"] == "PRIMARY_KEY_CONSTRAINT":
            primary_key.append(entry)
        else:
            unique_constraints.append(entry)

    return TableMetadata(
        table=table,
        row_count=row_count,
        columns=columns,
        primary_key=primary_key,
        unique_constraints=unique_constraints,
        check_constraints=check_constraints,
        indexes=indexes,
        outgoing_fks=outgoing_fks,
        incoming_fks=incoming_fks,
    )


def _select_profile_columns(columns: list[dict], limit: int = 4) -> list[dict]:
    """Pick the most informative columns for light-weight profiling."""

    numeric_types = {
        "bigint",
        "decimal",
        "float",
        "int",
        "money",
        "numeric",
        "smallint",
        "smallmoney",
        "tinyint",
        "real",
    }
    temporal_types = {"date", "datetime", "datetime2", "datetimeoffset", "smalldatetime", "time"}
    text_types = {"char", "nchar", "nvarchar", "varchar", "text", "ntext", "uniqueidentifier", "bit"}
    name_boosts = (
        "id",
        "_id",
        "code",
        "name",
        "status",
        "type",
        "date",
        "time",
        "created",
        "updated",
        "amount",
        "price",
        "total",
        "qty",
        "quantity",
        "flag",
    )

    scored: list[tuple[int, dict]] = []
    for column in columns:
        name = column["column_name"].lower()
        data_type = column["data_type"].lower()
        score = 0
        if column["is_identity"]:
            score += 50
        if not column["is_nullable"]:
            score += 5
        if any(token == name or name.endswith(token) or token in name for token in name_boosts):
            score += 20
        if data_type in numeric_types:
            score += 15
        elif data_type in temporal_types:
            score += 15
        elif data_type in text_types:
            score += 10
        if not column["is_computed"]:
            score += 3
        scored.append((score, column))

    selected = [column for _, column in sorted(scored, key=lambda item: (-item[0], item[1]["column_id"]))[:limit]]
    return selected


def _profile_column_query(
    table: QualifiedTableName,
    column: dict,
    top_values: int,
) -> tuple[str, str]:
    column_name = column["column_name"]
    quoted_table = f"{_quote_identifier(table.schema)}.{_quote_identifier(table.table)}"
    quoted_column = _quote_identifier(column_name)
    data_type = column["data_type"].lower()

    if data_type in {"date", "datetime", "datetime2", "datetimeoffset", "smalldatetime", "time"}:
        return (
            "temporal_range",
            (
                f"SELECT MIN({quoted_column}) AS min_value, "
                f"MAX({quoted_column}) AS max_value, "
                f"COUNT({quoted_column}) AS non_null_count "
                f"FROM {quoted_table}"
            ),
        )
    if data_type in {
        "bigint",
        "decimal",
        "float",
        "int",
        "money",
        "numeric",
        "smallint",
        "smallmoney",
        "tinyint",
        "real",
    }:
        return (
            "numeric_range",
            (
                f"SELECT MIN({quoted_column}) AS min_value, "
                f"MAX({quoted_column}) AS max_value, "
                f"AVG(CAST({quoted_column} AS DECIMAL(38, 10))) AS avg_value, "
                f"COUNT({quoted_column}) AS non_null_count "
                f"FROM {quoted_table}"
            ),
        )

    return (
        "top_values",
        (
            f"SELECT TOP {top_values} {quoted_column} AS value, COUNT(*) AS count "
            f"FROM {quoted_table} "
            f"GROUP BY {quoted_column} "
            f"ORDER BY count DESC, value"
        ),
    )


def _related_tables(metadata: TableMetadata) -> list[QualifiedTableName]:
    related: dict[str, QualifiedTableName] = {}
    for row in metadata.outgoing_fks:
        table = QualifiedTableName(schema=row["parent_schema"], table=row["parent_table"])
        related.setdefault(table.qualified_name, table)
    for row in metadata.incoming_fks:
        table = QualifiedTableName(schema=row["child_schema"], table=row["child_table"])
        related.setdefault(table.qualified_name, table)
    return sorted(related.values(), key=lambda item: item.qualified_name)


def _compact_relationships(metadata: TableMetadata) -> dict[str, list[dict]]:
    return {
        "outgoing": _group_foreign_key_rows(metadata.outgoing_fks, "outgoing"),
        "incoming": _group_foreign_key_rows(metadata.incoming_fks, "incoming"),
    }


def _build_table_exploration(
    table_name: str,
    config: Config,
    options: ExploreOptions,
    visited: set[str] | None = None,
) -> dict[str, Any]:
    table = parse_table_name(table_name)
    if visited is None:
        visited = set()
    if table.qualified_name in visited:
        return {
            "table": _compact_relationship_table(table),
            "summary": {"cycle": True, "table_role": "cycle"},
        }
    visited.add(table.qualified_name)

    metadata = _load_table_metadata(table, config)
    profile_columns = _select_profile_columns(metadata.columns, limit=max(0, options.profile_columns))

    schema: dict[str, Any] = {
        "columns": metadata.columns if options.verbose else [_compact_column(column) for column in metadata.columns],
        "primary_key": metadata.primary_key,
        "unique_constraints": metadata.unique_constraints,
    }
    if options.verbose:
        schema["check_constraints"] = metadata.check_constraints
        schema["indexes"] = _group_index_rows(metadata.indexes)

    payload: dict[str, Any] = {
        "table": _compact_relationship_table(table),
        "row_count": metadata.row_count,
        "summary": _build_base_summary(metadata, profile_columns),
        "schema": schema,
        "constraints": {
            "primary_key": metadata.primary_key,
            "unique_constraints": metadata.unique_constraints,
        },
        "relationships": _compact_relationships(metadata),
    }
    if options.verbose:
        payload["constraints"]["check_constraints"] = metadata.check_constraints

    sample_rows = max(0, options.sample_rows)
    if sample_rows:
        sample_sql = f"SELECT TOP {sample_rows} * FROM {_quote_identifier(table.schema)}.{_quote_identifier(table.table)}"
        payload["samples"] = execute_query(sample_sql, config).rows

    if profile_columns:
        profiles: list[dict[str, Any]] = []
        for column in profile_columns:
            kind, profile_sql = _profile_column_query(metadata.table, column, max(1, options.top_values))
            profile_rows = execute_query(profile_sql, config).rows
            profiles.append(
                {
                    "column": column["column_name"],
                    "kind": kind,
                    "data_type": column["data_type"],
                    "statistics": profile_rows,
                }
            )
        payload["profiles"] = profiles

    if options.depth > 0:
        child_options = ExploreOptions(
            depth=options.depth - 1,
            verbose=False,
            sample_rows=0,
            profile_columns=0,
            top_values=options.top_values,
        )
        payload["relationships"]["neighbors"] = [
            _build_table_exploration(child.qualified_name, config, child_options, visited)
            for child in _related_tables(metadata)
        ]

    return payload


def explore_table(
    table_name: str,
    config: Config,
    *,
    depth: int = 1,
    verbose: bool = False,
    sample_rows: int = 3,
    profile_columns: int = 4,
    top_values: int = 5,
) -> dict[str, Any]:
    options = ExploreOptions(
        depth=max(0, depth),
        verbose=verbose,
        sample_rows=max(0, sample_rows),
        profile_columns=max(0, profile_columns),
        top_values=max(1, top_values),
    )
    return _build_table_exploration(table_name, config, options)
