import json

import pytest
from typer.testing import CliRunner

from griphook.config import load_config
from griphook.main import app
from griphook.proxy import (
    BlockedQueryError,
    QueryResult,
    _select_profile_columns,
    explore_table,
    inject_limit,
    parse_table_name,
    validate_select_only,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# validate_select_only
# ---------------------------------------------------------------------------

def test_delete_is_blocked():
    with pytest.raises(BlockedQueryError, match="DELETE"):
        validate_select_only("DELETE FROM users WHERE id = 1")


def test_insert_is_blocked():
    with pytest.raises(BlockedQueryError, match="INSERT"):
        validate_select_only("INSERT INTO t VALUES (1)")


def test_select_is_allowed():
    validate_select_only("SELECT 1")  # must not raise


def test_drop_table_is_blocked():
    with pytest.raises(BlockedQueryError, match="DROP"):
        validate_select_only("DROP TABLE users")


def test_valid_cte_is_allowed():
    validate_select_only("WITH cte AS (SELECT 1 AS n) SELECT * FROM cte")


def test_nested_write_in_cte_is_blocked():
    # sqlglot parses this as a top-level Select whose AST walk contains a Delete node
    sql = "WITH cte AS (DELETE FROM t OUTPUT deleted.id) SELECT id FROM cte"
    with pytest.raises(BlockedQueryError):
        validate_select_only(sql)


def test_empty_sql_raises_error():
    with pytest.raises(BlockedQueryError, match="Empty query"):
        validate_select_only("")


def test_whitespace_only_sql_raises_error():
    with pytest.raises(BlockedQueryError, match="Empty query"):
        validate_select_only("   ")


def test_multiple_statements_with_write_blocked():
    # sqlglot splits into [Select, Delete]; the Delete triggers the guard
    with pytest.raises(BlockedQueryError):
        validate_select_only("SELECT 1; DELETE FROM t")


def test_exec_xp_cmdshell_is_blocked():
    # sqlglot.exp.Execute covers EXEC / stored-procedure calls
    with pytest.raises(BlockedQueryError):
        validate_select_only("EXEC xp_cmdshell 'dir'")


# ---------------------------------------------------------------------------
# inject_limit
# ---------------------------------------------------------------------------

def test_inject_limit_adds_top():
    assert "TOP 100" in inject_limit("SELECT * FROM t")


def test_inject_limit_skips_when_top_present():
    sql = "SELECT TOP 10 * FROM t"
    assert inject_limit(sql) == sql


def test_inject_limit_skips_offset_fetch():
    result = inject_limit("SELECT * FROM t OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY")
    assert "TOP 100" not in result
    assert "FETCH NEXT 10" in result


def test_inject_limit_select_distinct():
    result = inject_limit("SELECT DISTINCT col FROM t")
    assert "TOP 100" in result
    # sqlglot serializes per T-SQL spec: SELECT DISTINCT TOP N ...
    assert result.index("DISTINCT") < result.index("TOP")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_load_config_from_explicit_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SQL_SERVER=myserver\n"
        "SQL_DATABASE=mydb\n"
        "SQL_USER=myuser\n"
        "SQL_PASSWORD=mypass\n"
    )
    cfg = load_config(env_file=env_file)
    assert cfg.server == "myserver"
    assert cfg.database == "mydb"
    assert cfg.user == "myuser"


def test_parse_table_name_defaults_to_dbo():
    parsed = parse_table_name("produtos")
    assert parsed.schema == "dbo"
    assert parsed.table == "produtos"


def test_parse_table_name_with_schema():
    parsed = parse_table_name("onshore.produtos")
    assert parsed.schema == "onshore"
    assert parsed.table == "produtos"


def test_select_profile_columns_prefers_identity_and_common_fields():
    columns = [
        {
            "column_id": 1,
            "column_name": "produto_id",
            "data_type": "int",
            "is_nullable": False,
            "is_identity": True,
            "is_computed": False,
        },
        {
            "column_id": 2,
            "column_name": "descricao",
            "data_type": "nvarchar",
            "is_nullable": True,
            "is_identity": False,
            "is_computed": False,
        },
    ]
    selected = _select_profile_columns(columns, limit=2)
    assert [column["column_name"] for column in selected] == ["produto_id", "descricao"]


def test_explore_table_builds_compact_payload(monkeypatch):
    columns_rows = [
        {
            "column_id": 1,
            "column_name": "produto_id",
            "data_type": "int",
            "max_length": 4,
            "precision": 10,
            "scale": 0,
            "is_nullable": False,
            "is_identity": True,
            "is_computed": False,
        },
        {
            "column_id": 2,
            "column_name": "status",
            "data_type": "nvarchar",
            "max_length": 50,
            "precision": 0,
            "scale": 0,
            "is_nullable": True,
            "is_identity": False,
            "is_computed": False,
        },
    ]
    constraints_rows = [
        {
            "constraint_name": "PK_produtos",
            "type_desc": "PRIMARY_KEY_CONSTRAINT",
            "column_name": "produto_id",
            "key_ordinal": 1,
        },
        {
            "constraint_name": "UQ_produtos_status",
            "type_desc": "UNIQUE_CONSTRAINT",
            "column_name": "status",
            "key_ordinal": 1,
        },
    ]
    indexes_rows = [
        {
            "index_name": "PK_produtos",
            "type_desc": "CLUSTERED",
            "is_unique": True,
            "is_primary_key": True,
            "filter_definition": None,
            "column_name": "produto_id",
            "key_ordinal": 1,
            "is_included_column": False,
        },
        {
            "index_name": "IX_produtos_status",
            "type_desc": "NONCLUSTERED",
            "is_unique": False,
            "is_primary_key": False,
            "filter_definition": None,
            "column_name": "status",
            "key_ordinal": 1,
            "is_included_column": False,
        },
    ]
    fk_rows = [
        {
            "fk_name": "FK_produtos_categoria",
            "parent_schema": "onshore",
            "parent_table": "categorias",
            "child_column": "categoria_id",
            "parent_column": "categoria_id",
            "constraint_column_id": 1,
        }
    ]
    incoming_fk_rows = [
        {
            "fk_name": "FK_itens_produto",
            "child_schema": "onshore",
            "child_table": "itens",
            "child_column": "produto_id",
            "parent_column": "produto_id",
            "constraint_column_id": 1,
        }
    ]
    sample_rows = [{"produto_id": 1, "status": "ativo"}]
    numeric_profile_rows = [{"min_value": 1, "max_value": 10, "avg_value": 5.5, "non_null_count": 10}]
    top_value_profile_rows = [{"value": "ativo", "count": 8}, {"value": "inativo", "count": 2}]

    def fake_execute_query(sql, config, params=()):
        if "FROM sys.columns" in sql:
            return QueryResult([], columns_rows, 1, len(columns_rows), False)
        if "FROM sys.key_constraints" in sql:
            return QueryResult([], constraints_rows, 1, len(constraints_rows), False)
        if "FROM sys.check_constraints" in sql:
            return QueryResult([], [], 1, 0, False)
        if "FROM sys.indexes" in sql:
            return QueryResult([], indexes_rows, 1, len(indexes_rows), False)
        if "OBJECT_SCHEMA_NAME(fk.referenced_object_id)" in sql:
            return QueryResult([], fk_rows, 1, len(fk_rows), False)
        if "OBJECT_SCHEMA_NAME(fk.parent_object_id)" in sql:
            return QueryResult([], incoming_fk_rows, 1, len(incoming_fk_rows), False)
        if "SUM(p.rows)" in sql:
            return QueryResult([], [{"row_count": 42}], 1, 1, False)
        if "AVG(CAST" in sql:
            return QueryResult([], numeric_profile_rows, 1, len(numeric_profile_rows), False)
        if "GROUP BY" in sql:
            return QueryResult([], top_value_profile_rows, 1, len(top_value_profile_rows), False)
        if "SELECT TOP 1 * FROM" in sql:
            return QueryResult([], sample_rows, 1, len(sample_rows), False)
        raise AssertionError(f"Unexpected SQL: {sql}")

    monkeypatch.setattr("griphook.proxy.execute_query", fake_execute_query)
    result = explore_table(
        "onshore.produtos",
        object(),
        depth=0,
        sample_rows=1,
        profile_columns=2,
        top_values=5,
    )

    assert result["table"]["qualified_name"] == "onshore.produtos"
    assert result["row_count"] == 42
    assert result["summary"]["column_count"] == 2
    assert result["summary"]["table_role"] == "entity"
    assert result["schema"]["columns"][0]["name"] == "produto_id"
    assert result["constraints"]["primary_key"][0]["columns"] == ["produto_id"]
    assert result["relationships"]["outgoing"][0]["references"]["columns"] == ["categoria_id"]
    assert result["relationships"]["incoming"][0]["referenced_by"]["columns"] == ["produto_id"]
    assert result["profiles"][0]["statistics"][0]["avg_value"] == 5.5
    assert result["samples"] == sample_rows


def test_explore_table_expands_neighbor_summaries(monkeypatch):
    catalog = {
        ("onshore", "produtos"): {
            "columns": [
                {
                    "column_id": 1,
                    "column_name": "id_produto",
                    "data_type": "int",
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "is_nullable": False,
                    "is_identity": True,
                    "is_computed": False,
                },
                {
                    "column_id": 2,
                    "column_name": "id_tipo",
                    "data_type": "int",
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "is_nullable": False,
                    "is_identity": False,
                    "is_computed": False,
                },
            ],
            "primary_key": [
                {
                    "constraint_name": "PK_produtos",
                    "type_desc": "PRIMARY_KEY_CONSTRAINT",
                    "column_name": "id_produto",
                    "key_ordinal": 1,
                }
            ],
            "indexes": [],
            "outgoing": [
                {
                    "fk_name": "FK_produtos_tipos",
                    "parent_schema": "onshore",
                    "parent_table": "produtos_tipos",
                    "child_column": "id_tipo",
                    "parent_column": "id_tipo",
                    "constraint_column_id": 1,
                }
            ],
            "incoming": [],
            "row_count": 10,
        },
        ("onshore", "produtos_tipos"): {
            "columns": [
                {
                    "column_id": 1,
                    "column_name": "id_tipo",
                    "data_type": "int",
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "is_nullable": False,
                    "is_identity": True,
                    "is_computed": False,
                }
            ],
            "primary_key": [
                {
                    "constraint_name": "PK_produtos_tipos",
                    "type_desc": "PRIMARY_KEY_CONSTRAINT",
                    "column_name": "id_tipo",
                    "key_ordinal": 1,
                }
            ],
            "indexes": [],
            "outgoing": [],
            "incoming": [
                {
                    "fk_name": "FK_produtos_tipos",
                    "child_schema": "onshore",
                    "child_table": "produtos",
                    "child_column": "id_tipo",
                    "parent_column": "id_tipo",
                    "constraint_column_id": 1,
                }
            ],
            "row_count": 8,
        },
    }

    def fake_execute_query(sql, config, params=()):
        schema, table = params
        entry = catalog[(schema, table)]
        if "FROM sys.columns" in sql:
            return QueryResult([], entry["columns"], 1, len(entry["columns"]), False)
        if "FROM sys.key_constraints" in sql:
            return QueryResult([], entry["primary_key"], 1, len(entry["primary_key"]), False)
        if "FROM sys.check_constraints" in sql:
            return QueryResult([], [], 1, 0, False)
        if "FROM sys.indexes" in sql:
            return QueryResult([], entry["indexes"], 1, len(entry["indexes"]), False)
        if "OBJECT_SCHEMA_NAME(fk.referenced_object_id)" in sql:
            return QueryResult([], entry["outgoing"], 1, len(entry["outgoing"]), False)
        if "OBJECT_SCHEMA_NAME(fk.parent_object_id)" in sql:
            return QueryResult([], entry["incoming"], 1, len(entry["incoming"]), False)
        if "SUM(p.rows)" in sql:
            return QueryResult([], [{"row_count": entry["row_count"]}], 1, 1, False)
        if "SELECT TOP 3 * FROM" in sql or "SELECT TOP 1 * FROM" in sql:
            return QueryResult([], [{"id_produto": 1}], 1, 1, False)
        if "AVG(CAST" in sql:
            return QueryResult([], [{"min_value": 1, "max_value": 1, "avg_value": 1, "non_null_count": 1}], 1, 1, False)
        if "GROUP BY" in sql:
            return QueryResult([], [{"value": 1, "count": 1}], 1, 1, False)
        raise AssertionError(f"Unexpected SQL: {sql}")

    monkeypatch.setattr("griphook.proxy.execute_query", fake_execute_query)
    result = explore_table("onshore.produtos", object(), depth=1, sample_rows=0, profile_columns=0)

    assert result["relationships"]["neighbors"][0]["table"]["qualified_name"] == "onshore.produtos_tipos"
    assert result["relationships"]["neighbors"][0]["summary"]["column_count"] == 1


# ---------------------------------------------------------------------------
# CLI — dry-run (no DB required)
# ---------------------------------------------------------------------------

def test_dry_run_valid_query():
    result = runner.invoke(app, ["query", "--dry-run", "SELECT * FROM agents"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert data["valid"] is True
    assert "TOP 100" in data["sql"]


def test_dry_run_blocked_query_exits_1():
    result = runner.invoke(app, ["query", "--dry-run", "DELETE FROM agents"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert "error" in data


# ---------------------------------------------------------------------------
# CLI — exit code contract (no DB required)
# ---------------------------------------------------------------------------

def test_missing_config_exits_2(monkeypatch):
    def _raise(_):
        raise RuntimeError("Missing required config: SQL_SERVER")

    monkeypatch.setattr("griphook.main.load_config", _raise)
    result = runner.invoke(app, ["query", "SELECT 1"])
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert "error" in data


def test_explore_command_prints_structured_json(monkeypatch):
    monkeypatch.setattr("griphook.main.load_config", lambda _: object())
    monkeypatch.setattr(
        "griphook.main.explore_table",
        lambda table_name, cfg, **kwargs: {"table": {"qualified_name": table_name}, "rows": [], "kwargs": kwargs},
    )
    result = runner.invoke(app, ["explore", "onshore.produtos"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["table"]["qualified_name"] == "onshore.produtos"
    assert data["kwargs"] == {
        "depth": 0,
        "verbose": False,
        "sample_rows": 2,
        "profile_columns": 3,
        "top_values": 3,
    }


def test_explore_help_mentions_compact_and_knobs(capsys):
    from click import Context
    from typer.main import get_command

    command = get_command(app).commands["explore"]
    command.get_help(Context(command))
    output = capsys.readouterr().out
    assert "--depth" in output
    assert "--sample-rows" in output
    assert "--profile-columns" in output
    assert "--top-values" in output
    assert "--verbose" in output
    assert "[default: 0]" in output
    assert "[default: 2]" in output
    assert "[default: 3]" in output
