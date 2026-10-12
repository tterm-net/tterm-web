"""Settings: defaults that fit the laptop, the .env file, the environment."""
import tempfile
from pathlib import Path

from ..core.config import ROOT, Settings, read_env_file


def _env_file(text: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / ".env"
    tmp.write_text(text, encoding="utf-8")
    return tmp


def test_defaults_fit_the_laptop():
    s = Settings.load(environ={"TTERM_OWNER_NAME": "Denis"},
                      env_file=Path("/nonexistent/.env"))
    assert s.database_url == "postgresql://localhost:5433/tterm_web", s.database_url
    assert (s.page_host, s.page_port) == ("127.0.0.1", 8090)
    assert (s.connect_host, s.connect_port) == ("127.0.0.1", 8091)
    assert s.connect_url == "https://connect-dev.tterm.net"
    assert s.web_dist == ROOT / "web" / "dist"
    assert s.data_dir == ROOT / "data"


def test_env_file_is_read_plainly():
    path = _env_file(
        "# comment\n"
        "\n"
        "TTERM_PAGE_PORT=9001\n"
        "TTERM_OWNER_NAME=\"Denis Gerasimenko\"\n"
        "TTERM_CONNECT_URL='https://example.test/'\n"
        "TTERM_DATA_DIR=$HOME/x\n"
        "not a setting\n")
    values = read_env_file(path)
    assert values["TTERM_PAGE_PORT"] == "9001"
    assert values["TTERM_OWNER_NAME"] == "Denis Gerasimenko"
    # No interpolation: what the file says is what the program gets.
    assert values["TTERM_DATA_DIR"] == "$HOME/x"
    s = Settings.load(environ={}, env_file=path)
    assert s.page_port == 9001
    assert s.connect_url == "https://example.test", "trailing slash dropped"


def test_environment_wins_over_the_file():
    path = _env_file("TTERM_PAGE_PORT=9001\n")
    s = Settings.load(environ={"TTERM_PAGE_PORT": "9002"}, env_file=path)
    assert s.page_port == 9002


def test_bad_port_is_a_clear_message():
    for value, words in (("http", "must be a port number"),
                         ("70000", "between 1 and 65535")):
        try:
            Settings.load(environ={"TTERM_PAGE_PORT": value},
                          env_file=Path("/nonexistent/.env"))
        except SystemExit as exc:
            assert words in str(exc), exc
        else:
            raise AssertionError(f"{value} accepted")


def test_relative_paths_are_from_the_repository_root():
    s = Settings.load(environ={"TTERM_WEB_DIST": "elsewhere/dist",
                               "TTERM_DATA_DIR": "/abs/data"},
                      env_file=Path("/nonexistent/.env"))
    assert s.web_dist == ROOT / "elsewhere" / "dist"
    assert s.data_dir == Path("/abs/data")
