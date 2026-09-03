import pytest
from typer.testing import CliRunner

from griphook import config as config_mod
from griphook import main as main_mod
from griphook.config import AUTH_SQL, AUTH_WINDOWS, Config, detect_driver, load_config
from griphook.main import app

runner = CliRunner()


def _config(**overrides) -> Config:
    base = dict(
        server="srv",
        database="db",
        user="u",
        password="p",
        port=1433,
        trust_cert=True,
        warn_timeout=5,
        kill_timeout=20,
        driver="ODBC Driver 18 for SQL Server",
    )
    base.update(overrides)
    return Config(**base)


# ---------------------------------------------------------------------------
# connection_string
# ---------------------------------------------------------------------------

def test_sql_auth_connection_string_carries_credentials():
    cs = _config(auth=AUTH_SQL).connection_string
    assert "UID=u;" in cs
    assert "PWD=p;" in cs
    assert "Trusted_Connection" not in cs


def test_windows_auth_connection_string_omits_credentials():
    cs = _config(auth=AUTH_WINDOWS).connection_string
    assert "Trusted_Connection=yes;" in cs
    assert "UID=" not in cs
    assert "PWD=" not in cs


def test_explicit_driver_is_used_verbatim():
    cs = _config(driver="ODBC Driver 17 for SQL Server").connection_string
    assert cs.startswith("DRIVER={ODBC Driver 17 for SQL Server};")


# ---------------------------------------------------------------------------
# driver detection
# ---------------------------------------------------------------------------

def test_detect_driver_prefers_newest(monkeypatch):
    monkeypatch.setattr(
        config_mod,
        "available_drivers",
        lambda: ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"],
    )
    assert detect_driver() == "ODBC Driver 18 for SQL Server"


def test_detect_driver_falls_back_to_installed(monkeypatch):
    monkeypatch.setattr(config_mod, "available_drivers", lambda: ["ODBC Driver 17 for SQL Server"])
    assert detect_driver() == "ODBC Driver 17 for SQL Server"


def test_detect_driver_raises_when_none_installed(monkeypatch):
    monkeypatch.setattr(config_mod, "available_drivers", lambda: [])
    with pytest.raises(config_mod.MissingDriverError):
        detect_driver()


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """No cwd .env, no user config, no inherited SQL_* vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "absent.env")
    for key in (
        "SQL_SERVER",
        "SQL_DATABASE",
        "SQL_USER",
        "SQL_PASSWORD",
        "SQL_AUTH",
        "SQL_PORT",
        "SQL_DRIVER",
        "SQL_TRUST_CERT",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_windows_auth_does_not_require_credentials(isolated_env):
    isolated_env.setenv("SQL_SERVER", "srv")
    isolated_env.setenv("SQL_DATABASE", "db")
    isolated_env.setenv("SQL_AUTH", "windows")

    cfg = load_config()

    assert cfg.auth == AUTH_WINDOWS
    assert cfg.user == ""


def test_sql_auth_still_requires_credentials(isolated_env):
    isolated_env.setenv("SQL_SERVER", "srv")
    isolated_env.setenv("SQL_DATABASE", "db")

    with pytest.raises(RuntimeError, match="SQL_USER, SQL_PASSWORD"):
        load_config()


def test_auth_aliases_normalize_to_windows(isolated_env):
    isolated_env.setenv("SQL_SERVER", "srv")
    isolated_env.setenv("SQL_DATABASE", "db")
    isolated_env.setenv("SQL_AUTH", "Integrated")

    assert load_config().auth == AUTH_WINDOWS


# ---------------------------------------------------------------------------
# configure --non-interactive (this is what the Windows installer calls)
# ---------------------------------------------------------------------------

@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config" / ".env"
    monkeypatch.setattr(main_mod, "CONFIG_DIR", path.parent)
    monkeypatch.setattr(main_mod, "CONFIG_FILE", path)
    return path


def test_non_interactive_windows_auth_writes_config(config_file, monkeypatch):
    monkeypatch.setattr(main_mod, "detect_driver", lambda: "ODBC Driver 18 for SQL Server")

    result = runner.invoke(
        app,
        ["configure", "--non-interactive", "--server", "srv", "--database", "db",
         "--auth", "windows"],
    )

    assert result.exit_code == 0
    written = config_file.read_text()
    assert "SQL_AUTH=windows" in written
    assert "SQL_SERVER=srv" in written
    assert "SQL_DRIVER=ODBC Driver 18 for SQL Server" in written
    assert "SQL_USER" not in written
    assert "SQL_PASSWORD" not in written


def test_non_interactive_sql_auth_requires_credentials(config_file):
    result = runner.invoke(
        app,
        ["configure", "--non-interactive", "--server", "srv", "--database", "db", "--auth", "sql"],
    )

    assert result.exit_code == 2
    assert "--user" in result.stderr
    assert not config_file.exists()


def test_non_interactive_survives_missing_driver(config_file, monkeypatch):
    def _boom():
        raise config_mod.MissingDriverError("none installed")

    monkeypatch.setattr(main_mod, "detect_driver", _boom)

    result = runner.invoke(
        app,
        ["configure", "--non-interactive", "--server", "srv", "--database", "db",
         "--auth", "windows"],
    )

    assert result.exit_code == 0
    assert "SQL_DRIVER" not in config_file.read_text()


def test_test_connection_without_config_exits_2(isolated_env):
    result = runner.invoke(app, ["test-connection"])

    assert result.exit_code == 2
    assert "Missing required config" in result.stderr
