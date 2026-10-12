"""Settings, read from the environment.

A `.env` file in the repository root is read too, as a convenience: it fills
in only what the real environment leaves unset. Every setting has a default
that fits the laptop setup, so on the owner's machine no `.env` is needed.
"""
from __future__ import annotations

import os
import pwd
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: The repository root: tterm-web/, two levels above this file's package.
ROOT = Path(__file__).resolve().parents[2]


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Blank lines and # comments are skipped.

    Deliberately small: no interpolation and no `export`, so what you read
    in the file is exactly what the program gets. Quotes around a value are
    removed, as a shell would.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def account_name() -> str:
    """The full name of the person logged in to this computer.

    On a Mac it is the name shown in System Settings, which is what the owner
    expects to see in "Welcome to tTerm, Denis". Falls back to the login.
    """
    try:
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (KeyError, OSError):
        full = ""
    return full or os.environ.get("USER") or os.environ.get("LOGNAME") or "Owner"


@dataclass(frozen=True)
class Settings:
    #: Postgres.app on the owner's laptop listens on 5433: 5432 belongs to
    #: another project's PostgreSQL, which we leave alone.
    database_url: str
    #: The page and its API. Only this computer may open it: there is no
    #: sign-in before stage 2.
    page_host: str
    page_port: int
    #: Where machines talk to us: the installer, registration, agents. The
    #: tunnel to connect-dev.tterm.net points here, and nothing of the page
    #: is reachable through it.
    connect_host: str
    connect_port: int
    #: The public address of the above, as machines see it.
    connect_url: str
    #: The person this laptop belongs to, until accounts arrive.
    owner_name: str
    #: Local state that never goes to git.
    data_dir: Path
    #: The built page.
    web_dist: Path
    #: The signer's own directory: CA keys, its journal. Only it reads them.
    signer_dir: Path
    #: How the core reaches the signer.
    signer_socket: Path

    @classmethod
    def load(cls, environ: Mapping[str, str] | None = None,
             env_file: Path | None = None) -> "Settings":
        env = dict(read_env_file(env_file or ROOT / ".env"))
        env.update(os.environ if environ is None else environ)

        def get(key: str, default: str) -> str:
            value = env.get(key, "").strip()
            return value or default

        def port(key: str, default: int) -> int:
            raw = get(key, str(default))
            try:
                value = int(raw)
            except ValueError:
                raise SystemExit(f"{key} must be a port number, got {raw!r}")
            if not 1 <= value <= 65535:
                raise SystemExit(f"{key} must be between 1 and 65535, got {value}")
            return value

        def path(key: str, default: str) -> Path:
            value = Path(get(key, default)).expanduser()
            return value if value.is_absolute() else ROOT / value

        data_dir = path("TTERM_DATA_DIR", "data")
        signer_dir = path("TTERM_SIGNER_DIR", str(data_dir / "signer"))
        signer_socket = path("TTERM_SIGNER_SOCKET", str(signer_dir / "signer.sock"))
        if not env.get("TTERM_SIGNER_SOCKET") and len(str(signer_socket)) > 100:
            # A Unix socket path may be at most about a hundred bytes. In a
            # deep folder the default is longer, so it moves to a private
            # folder of this user in the system's temporary directory.
            signer_socket = (Path(tempfile.gettempdir())
                             / f"tterm-web-{os.getuid()}" / "signer.sock")
        return cls(
            database_url=get("TTERM_DATABASE_URL",
                             "postgresql://localhost:5433/tterm_web"),
            page_host=get("TTERM_PAGE_HOST", "127.0.0.1"),
            page_port=port("TTERM_PAGE_PORT", 8090),
            connect_host=get("TTERM_CONNECT_HOST", "127.0.0.1"),
            connect_port=port("TTERM_CONNECT_PORT", 8091),
            connect_url=get("TTERM_CONNECT_URL",
                            "https://connect-dev.tterm.net").rstrip("/"),
            owner_name=get("TTERM_OWNER_NAME", account_name()),
            data_dir=data_dir,
            web_dist=path("TTERM_WEB_DIST", "web/dist"),
            signer_dir=signer_dir,
            signer_socket=signer_socket,
        )
