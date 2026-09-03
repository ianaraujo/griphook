import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "griphook"
CONFIG_FILE = CONFIG_DIR / ".env"

AUTH_SQL = "sql"
AUTH_WINDOWS = "windows"

_WINDOWS_AUTH_ALIASES = {"windows", "integrated", "trusted", "sspi"}

# Preferred ODBC drivers, best first. Used when SQL_DRIVER is not set.
PREFERRED_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13.1 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)


class MissingDriverError(RuntimeError):
    """Raised when no usable SQL Server ODBC driver is installed."""


def available_drivers() -> list[str]:
    """Installed ODBC drivers that can talk to SQL Server."""
    import pyodbc

    return [d for d in pyodbc.drivers() if "SQL Server" in d]


def detect_driver() -> str:
    """Pick the best installed SQL Server ODBC driver."""
    installed = available_drivers()
    for candidate in PREFERRED_DRIVERS:
        if candidate in installed:
            return candidate
    if installed:
        return installed[0]
    raise MissingDriverError(
        "No SQL Server ODBC driver found. Install the Microsoft ODBC Driver 18 for "
        "SQL Server, or set SQL_DRIVER to the exact name of an installed driver."
    )


@dataclass
class Config:
    server: str
    database: str
    user: str
    password: str
    port: int
    trust_cert: bool
    warn_timeout: int
    kill_timeout: int
    auth: str = AUTH_SQL
    driver: str = ""

    @property
    def resolved_driver(self) -> str:
        return self.driver or detect_driver()

    @property
    def connection_string(self) -> str:
        trust = "yes" if self.trust_cert else "no"
        parts = [
            f"DRIVER={{{self.resolved_driver}}}",
            f"SERVER={self.server},{self.port}",
            f"DATABASE={self.database}",
        ]
        if self.auth == AUTH_WINDOWS:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.user}")
            parts.append(f"PWD={self.password}")
        parts.append(f"TrustServerCertificate={trust}")
        parts.append("ConnectRetryCount=3")
        parts.append("ConnectRetryInterval=5")
        return ";".join(parts) + ";"


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("\"'")
    return env


def normalize_auth(value: str) -> str:
    return AUTH_WINDOWS if value.strip().lower() in _WINDOWS_AUTH_ALIASES else AUTH_SQL


def load_config(env_file: Path | None = None) -> Config:
    # Priority: process env vars > explicit --env-file > cwd .env > ~/.config/griphook/.env
    explicit_env = _load_env_file(env_file) if env_file else {}
    cwd_env = _load_env_file(Path(os.getcwd()) / ".env")
    user_env = _load_env_file(CONFIG_FILE)

    def _get(key: str, default: str = "") -> str:
        return (
            os.environ.get(key)
            or explicit_env.get(key)
            or cwd_env.get(key)
            or user_env.get(key)
            or default
        )

    auth = normalize_auth(_get("SQL_AUTH", AUTH_SQL))

    required = ["SQL_SERVER", "SQL_DATABASE"]
    if auth == AUTH_SQL:
        required += ["SQL_USER", "SQL_PASSWORD"]

    missing = [k for k in required if not _get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required config: {', '.join(missing)}\n"
            "  Installed CLI  →  run 'griphook configure'\n"
            "  Dev/testing    →  copy .env.example to .env in the project root\n"
            "  Explicit file  →  griphook --env-file /path/to/.env query '...'"
        )

    return Config(
        server=_get("SQL_SERVER"),
        database=_get("SQL_DATABASE"),
        user=_get("SQL_USER"),
        password=_get("SQL_PASSWORD"),
        port=int(_get("SQL_PORT", "1433")),
        trust_cert=_get("SQL_TRUST_CERT", "true").lower() == "true",
        warn_timeout=int(_get("SQL_WARN_TIMEOUT", "5")),
        kill_timeout=int(_get("SQL_KILL_TIMEOUT", "20")),
        auth=auth,
        driver=_get("SQL_DRIVER"),
    )
