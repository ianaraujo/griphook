"""Read-only SQL Server query tool for Claude Code."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer

from griphook.config import CONFIG_DIR, CONFIG_FILE, load_config
from griphook.proxy import (
    BlockedQueryError,
    DatabaseConnectionError,
    QueryTimeoutError,
    execute,
    inject_limit,
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
        result = execute(sql, cfg)
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
def configure() -> None:
    """Configure database connection credentials."""
    typer.echo(f"Settings will be saved to [bold]{CONFIG_FILE}[/bold]\n")

    server = typer.prompt("SQL Server hostname")
    database = typer.prompt("Database name")
    user = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)
    port = typer.prompt("Port", default="1433")
    trust_cert = typer.confirm("Trust server certificate?", default=True)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w") as f:
        f.write(f"SQL_SERVER={server}\n")
        f.write(f"SQL_DATABASE={database}\n")
        f.write(f"SQL_USER={user}\n")
        f.write(f"SQL_PASSWORD={password}\n")
        f.write(f"SQL_PORT={port}\n")
        f.write(f"SQL_TRUST_CERT={'true' if trust_cert else 'false'}\n")

    typer.echo(f"\nConfiguration saved to {CONFIG_FILE}")


if __name__ == "__main__":
    app()
