import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "griphook"
CONFIG_FILE = CONFIG_DIR / ".env"


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
    registry_path: Path

    @property
    def connection_string(self) -> str:
        trust = "yes" if self.trust_cert else "no"
        return (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={self.server},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.user};"
            f"PWD={self.password};"
            f"TrustServerCertificate={trust};"
            f"ConnectRetryCount=3;"
            f"ConnectRetryInterval=5;"
        )


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

    missing = [k for k in ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD") if not _get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required config: {', '.join(missing)}\n"
            "  Installed CLI  →  run 'griphook configure'\n"
            "  Dev/testing    →  copy .env.example to .env in the project root\n"
            "  Explicit file  →  griphook --env-file /path/to/.env query '...'"
        )

    registry_path = Path(
        _get("SQL_PROXY_REGISTRY_PATH", "~/.sql-proxy/registry.db")
    ).expanduser()

    return Config(
        server=_get("SQL_SERVER"),
        database=_get("SQL_DATABASE"),
        user=_get("SQL_USER"),
        password=_get("SQL_PASSWORD"),
        port=int(_get("SQL_PORT", "1433")),
        trust_cert=_get("SQL_TRUST_CERT", "true").lower() == "true",
        warn_timeout=int(_get("SQL_WARN_TIMEOUT", "5")),
        kill_timeout=int(_get("SQL_KILL_TIMEOUT", "20")),
        registry_path=registry_path,
    )
