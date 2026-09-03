"""Read-only SQL Server query tool for Claude Code."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer

from griphook.config import (
    AUTH_SQL,
    AUTH_WINDOWS,
    CONFIG_DIR,
    CONFIG_FILE,
    Config,
    MissingDriverError,
    available_drivers,
    detect_driver,
    load_config,
    normalize_auth,
)
from griphook.proxy import (
    BlockedQueryError,
    DatabaseConnectionError,
    QueryTimeoutError,
    explore_table,
    inject_limit,
    execute_query,
    validate_select_only,
)


@dataclass
class _State:
    env_file: Path | None = None


_state = _State()

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


@app.callback()
def _callback(
    env_file: Annotated[
        Optional[Path],
        typer.Option("--env-file", "-e", help="Explicit .env file (overrides auto-discovery)"),
    ] = None,
) -> None:
    """Griphook — read-only SQL Server query tool for AI agents."""
    _state.env_file = env_file


@app.command()
def query(
    sql: Annotated[str, typer.Argument(help="SQL [bold]SELECT[/bold] query to execute")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate and show what would be executed without running"),
    ] = False,
) -> None:
    """Execute a read-only SQL Server query and print results as JSON.

    [bold]Exit codes:[/bold]
      0 — success
      1 — query error (blocked statement, timeout, SQL error)
      2 — config or connection error
    """
    if dry_run:
        try:
            validate_select_only(sql)
            executed_sql = inject_limit(sql)
        except BlockedQueryError as exc:
            typer.echo(json.dumps({"error": str(exc)}), err=True)
            raise typer.Exit(1)
        typer.echo(json.dumps({"dry_run": True, "valid": True, "sql": executed_sql}, indent=2))
        return

    try:
        cfg = load_config(_state.env_file)
    except RuntimeError as exc:
        typer.echo(json.dumps({"error": str(exc)}), err=True)
        raise typer.Exit(2)

    try:
        result = execute_query(sql, cfg)
    except BlockedQueryError as exc:
        typer.echo(json.dumps({"error": str(exc)}), err=True)
        raise typer.Exit(1)
    except QueryTimeoutError as exc:
        typer.echo(json.dumps({"error": str(exc), "duration_ms": exc.duration_ms}), err=True)
        raise typer.Exit(1)
    except DatabaseConnectionError as exc:
        typer.echo(json.dumps({"error": str(exc)}), err=True)
        raise typer.Exit(2)
    except Exception as exc:
        typer.echo(json.dumps({"error": f"Query execution failed: {exc}"}), err=True)
        raise typer.Exit(1)

    if result.warned:
        typer.echo(
            json.dumps({"warning": f"Query exceeded warn threshold ({cfg.warn_timeout}s)"}),
            err=True,
        )

    typer.echo(
        json.dumps(
            {
                "row_count": result.row_count,
                "columns": result.columns,
                "rows": result.rows,
                "duration_ms": result.duration_ms,
                "warned": result.warned,
            },
            indent=2,
            default=str,
        )
    )


@app.command()
def explore(
    table_name: Annotated[str, typer.Argument(help="Table name to inspect, in schema.table form")],
    depth: Annotated[
        int,
        typer.Option(
            "--depth",
            help="How many hops of related tables to include. `0` keeps the output to the current table only.",
        ),
    ] = 0,
    sample_rows: Annotated[
        int,
        typer.Option(
            "--sample-rows",
            help="How many sample rows to include for the current table. Set to `0` to skip samples.",
        ),
    ] = 2,
    profile_columns: Annotated[
        int,
        typer.Option(
            "--profile-columns",
            help="How many informative columns to profile for the current table. Set to `0` to skip profiles.",
        ),
    ] = 3,
    top_values: Annotated[
        int,
        typer.Option(
            "--top-values",
            help="How many most-common values to return for text profiles.",
        ),
    ] = 3,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose/--no-verbose",
            help="Include full column metadata, checks, and index detail instead of the compact default.",
        ),
    ] = False,
) -> None:
    """Explore a SQL Server table and print a structured JSON summary.

    The default output is compact and tuned for agents. Use ``--verbose`` for
    full table metadata, ``--depth`` to expand related tables, and the other
    options to trim or expand sample/profile data. The zero-config default keeps
    the current table only.
    """
    try:
        cfg = load_config(_state.env_file)
    except RuntimeError as exc:
        typer.echo(json.dumps({"error": str(exc)}), err=True)
        raise typer.Exit(2)

    try:
        result = explore_table(
            table_name,
            cfg,
            depth=depth,
            verbose=verbose,
            sample_rows=sample_rows,
            profile_columns=profile_columns,
            top_values=top_values,
        )
    except BlockedQueryError as exc:
        typer.echo(json.dumps({"error": str(exc)}), err=True)
        raise typer.Exit(1)
    except QueryTimeoutError as exc:
        typer.echo(json.dumps({"error": str(exc), "duration_ms": exc.duration_ms}), err=True)
        raise typer.Exit(1)
    except DatabaseConnectionError as exc:
        typer.echo(json.dumps({"error": str(exc)}), err=True)
        raise typer.Exit(2)
    except Exception as exc:
        typer.echo(json.dumps({"error": f"Table exploration failed: {exc}"}), err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps(result, indent=2, default=str))


def _write_config_file(
    *,
    server: str,
    database: str,
    auth: str,
    user: str,
    password: str,
    port: str,
    trust_cert: bool,
    driver: str,
) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"SQL_SERVER={server}",
        f"SQL_DATABASE={database}",
        f"SQL_AUTH={auth}",
    ]
    if auth == AUTH_SQL:
        lines += [f"SQL_USER={user}", f"SQL_PASSWORD={password}"]
    lines += [
        f"SQL_PORT={port}",
        f"SQL_TRUST_CERT={'true' if trust_cert else 'false'}",
    ]
    if driver:
        lines.append(f"SQL_DRIVER={driver}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


def _build_config(
    *,
    server: str,
    database: str,
    auth: str,
    user: str,
    password: str,
    port: int,
    trust_cert: bool,
    driver: str,
) -> Config:
    return Config(
        server=server,
        database=database,
        user=user,
        password=password,
        port=port,
        trust_cert=trust_cert,
        warn_timeout=10,
        kill_timeout=20,
        auth=auth,
        driver=driver,
    )


@app.command()
def configure(
    server: Annotated[Optional[str], typer.Option("--server", help="SQL Server hostname")] = None,
    database: Annotated[Optional[str], typer.Option("--database", help="Database name")] = None,
    auth: Annotated[
        Optional[str],
        typer.Option("--auth", help="Authentication mode: [bold]windows[/bold] or [bold]sql[/bold]"),
    ] = None,
    user: Annotated[Optional[str], typer.Option("--user", help="Username (SQL auth only)")] = None,
    password: Annotated[
        Optional[str], typer.Option("--password", help="Password (SQL auth only)")
    ] = None,
    port: Annotated[Optional[str], typer.Option("--port", help="TCP port")] = None,
    trust_cert: Annotated[
        Optional[bool],
        typer.Option("--trust-cert/--no-trust-cert", help="Trust the server certificate"),
    ] = None,
    driver: Annotated[
        Optional[str],
        typer.Option("--driver", help="ODBC driver name (auto-detected when omitted)"),
    ] = None,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", "-y", help="Never prompt; requires the options above"),
    ] = False,
) -> None:
    """Configure the database connection.

    Run with no options for an interactive setup, or pass every value plus
    [bold]--non-interactive[/bold] to write the configuration unattended
    (this is what the Windows installer does).
    """
    if non_interactive:
        auth_mode = normalize_auth(auth or AUTH_SQL)
        missing = [
            name
            for name, value in (("--server", server), ("--database", database))
            if not value
        ]
        if auth_mode == AUTH_SQL:
            missing += [
                name for name, value in (("--user", user), ("--password", password)) if not value
            ]
        if missing:
            typer.echo(
                json.dumps({"error": f"Missing options: {', '.join(missing)}"}),
                err=True,
            )
            raise typer.Exit(2)
    else:
        typer.echo(f"Settings will be saved to {CONFIG_FILE}\n")
        if auth is None:
            use_windows = typer.confirm(
                "Use Windows Authentication (current Windows account)?",
                default=os.name == "nt",
            )
            auth_mode = AUTH_WINDOWS if use_windows else AUTH_SQL
        else:
            auth_mode = normalize_auth(auth)

        server = server or typer.prompt("SQL Server hostname")
        database = database or typer.prompt("Database name")
        if auth_mode == AUTH_SQL:
            user = user or typer.prompt("Username")
            password = password or typer.prompt("Password", hide_input=True)
        port = port or typer.prompt("Port", default="1433")
        if trust_cert is None:
            trust_cert = typer.confirm("Trust server certificate?", default=True)

    resolved_driver = driver or ""
    if not resolved_driver:
        try:
            resolved_driver = detect_driver()
        except MissingDriverError:
            resolved_driver = ""

    _write_config_file(
        server=server or "",
        database=database or "",
        auth=auth_mode,
        user=user or "",
        password=password or "",
        port=port or "1433",
        trust_cert=True if trust_cert is None else trust_cert,
        driver=resolved_driver,
    )

    if non_interactive:
        typer.echo(json.dumps({"saved": str(CONFIG_FILE), "auth": auth_mode}))
    else:
        typer.echo(f"\nConfiguration saved to {CONFIG_FILE}")
        if not resolved_driver:
            typer.echo(
                "Warning: no SQL Server ODBC driver was detected on this machine.", err=True
            )


@app.command(name="test-connection")
def test_connection(
    server: Annotated[Optional[str], typer.Option("--server", help="Override the server")] = None,
    database: Annotated[
        Optional[str], typer.Option("--database", help="Override the database")
    ] = None,
    auth: Annotated[
        Optional[str], typer.Option("--auth", help="Override the auth mode: windows or sql")
    ] = None,
    user: Annotated[Optional[str], typer.Option("--user", help="Override the username")] = None,
    password: Annotated[
        Optional[str], typer.Option("--password", help="Override the password")
    ] = None,
    port: Annotated[Optional[int], typer.Option("--port", help="Override the TCP port")] = None,
    driver: Annotated[
        Optional[str], typer.Option("--driver", help="Override the ODBC driver name")
    ] = None,
) -> None:
    """Check that the database can be reached and print who we connect as.

    With no options it tests the saved configuration. The Windows installer
    passes the wizard values as options so it can validate them before saving.

    [bold]Exit codes:[/bold]
      0 — connection succeeded
      2 — driver missing, config missing, or connection failed
    """
    overrides_complete = bool(server and database)
    try:
        if overrides_complete:
            auth_mode = normalize_auth(auth or (AUTH_WINDOWS if os.name == "nt" else AUTH_SQL))
            cfg = _build_config(
                server=server or "",
                database=database or "",
                auth=auth_mode,
                user=user or "",
                password=password or "",
                port=port or 1433,
                trust_cert=True,
                driver=driver or "",
            )
        else:
            cfg = load_config(_state.env_file)
            if server:
                cfg.server = server
            if database:
                cfg.database = database
            if auth:
                cfg.auth = normalize_auth(auth)
            if user:
                cfg.user = user
            if password:
                cfg.password = password
            if port:
                cfg.port = port
            if driver:
                cfg.driver = driver
    except RuntimeError as exc:
        typer.echo(json.dumps({"ok": False, "error": str(exc)}), err=True)
        raise typer.Exit(2)

    try:
        resolved_driver = cfg.resolved_driver
    except MissingDriverError as exc:
        typer.echo(
            json.dumps({"ok": False, "error": str(exc), "drivers": available_drivers()}), err=True
        )
        raise typer.Exit(2)

    try:
        # ORIGINAL_LOGIN() rather than SUSER_SNAME(): sqlglot rewrites the latter
        # to CURRENT_USER(), which is not valid T-SQL syntax.
        result = execute_query(
            "SELECT ORIGINAL_LOGIN() AS login_name, DB_NAME() AS database_name", cfg
        )
    except (DatabaseConnectionError, QueryTimeoutError) as exc:
        typer.echo(json.dumps({"ok": False, "error": str(exc), "driver": resolved_driver}), err=True)
        raise typer.Exit(2)
    except Exception as exc:
        typer.echo(
            json.dumps({"ok": False, "error": f"Connection test failed: {exc}"}), err=True
        )
        raise typer.Exit(2)

    row = result.rows[0] if result.rows else {}
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "login_name": row.get("login_name"),
                "database": row.get("database_name"),
                "auth": cfg.auth,
                "driver": resolved_driver,
                "duration_ms": result.duration_ms,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    app()
