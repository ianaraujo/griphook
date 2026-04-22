import json

import pytest
from typer.testing import CliRunner

from griphook.config import load_config
from griphook.main import app
from griphook.proxy import BlockedQueryError, inject_limit, validate_select_only

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
