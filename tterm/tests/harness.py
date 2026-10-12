"""What the checks stand on: throwaway databases, a running core, HTTP.

Each run creates its own databases next to the real one and drops them at
the end. The real database, tterm_web, is never opened.
"""
from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import asyncssh

from ..core import db, passport
from ..core.config import Settings
from ..core.signer_client import SignerClient
from ..server import Core
from ..signer.service import Signer, serve

#: What a small Ubuntu server answers to the passport script.
PASSPORT_OUTPUT = """os=Ubuntu 24.04.1 LTS
os_id=ubuntu
os_version=24.04
kernel=6.8.0-45-generic
arch=x86_64
hostname=tterm-lab-01
package_manager=apt-get
shell=/bin/bash
shells=bash
cpus=1
memory_kb=1003456
disk_total_kb=25215872
disk_free_kb=22000000
sudo=yes
systemd=yes
--commands--
apt
git
ls
systemctl
"""


@contextlib.asynccontextmanager
async def ssh_server(ca_public_key: str, *, host_key=None):
    """A small SSH server in this process that trusts our CA, the way the
    installer sets a real one up, and answers the passport script.

    Yields (port, host_key, sign_ins): sign_ins collects the user name of
    every session that got in.
    """
    host_key = host_key or asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(
        f"cert-authority {ca_public_key}\n")
    sign_ins: list[str] = []

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        sign_ins.append(process.get_extra_info("username") or "")
        if passport.MARKER in (process.command or ""):
            process.stdout.write(PASSPORT_OUTPUT)
        process.exit(0)

    server = await asyncssh.create_server(
        asyncssh.SSHServer, "127.0.0.1", 0, server_host_keys=[host_key],
        authorized_client_keys=authorized, process_factory=handle,
        encoding="utf-8")
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, host_key, sign_ins
    finally:
        server.close()
        await server.wait_closed()


def admin_url(database_url: str) -> str:
    """The same server, the maintenance database `postgres`."""
    parts = urlsplit(database_url)
    return urlunsplit(parts._replace(path="/postgres"))


def with_database(database_url: str, name: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit(parts._replace(path=f"/{name}"))


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)


def _request(port: int, method: str, path: str, headers: dict[str, str],
             body: bytes | None) -> Response:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True,
                        skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        if body is not None:
            conn.putheader("content-length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return Response(resp.status,
                        {k.lower(): v for k, v in resp.getheaders()},
                        resp.read())
    finally:
        conn.close()


async def request(port: int, method: str, path: str, *,
                  host: str | None = None, headers: dict[str, str] | None = None,
                  body: bytes | None = None) -> Response:
    """A plain HTTP request with full control over Host and the headers."""
    all_headers = {"host": host or f"localhost:{port}"}
    all_headers.update(headers or {})
    return await asyncio.to_thread(_request, port, method, path, all_headers,
                                   body)


def free_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    return sock


@dataclass
class Harness:
    base_url: str
    _template: str | None = None
    _created: list[str] = field(default_factory=list)
    _admin: asyncpg.Connection | None = None
    _counter: int = 0

    async def start(self) -> None:
        try:
            self._admin = await asyncpg.connect(admin_url(self.base_url))
        except Exception as exc:
            raise db.DatabaseUnavailable(
                f"The checks need PostgreSQL at "
                f"{db.describe(admin_url(self.base_url))}: {exc}. Is "
                "Postgres.app running?") from None
        # One migrated template; every check gets a copy of it, which is
        # much faster than migrating each time.
        self._template = await self._create("tmpl")
        pool = await db.connect(with_database(self.base_url, self._template),
                                max_size=2)
        try:
            await db.migrate(pool)
        finally:
            await pool.close()

    async def _create(self, kind: str, template: str | None = None) -> str:
        assert self._admin is not None
        self._counter += 1
        name = f"tterm_test_{os.getpid()}_{kind}_{self._counter}"
        sql = f'CREATE DATABASE "{name}"'
        if template:
            sql += f' TEMPLATE "{template}"'
        await self._admin.execute(sql)
        self._created.append(name)
        return name

    async def empty_database(self) -> str:
        """The URL of a new, empty database, not migrated."""
        return with_database(self.base_url, await self._create("empty"))

    async def database(self) -> str:
        """The URL of a new database with the schema in place."""
        return with_database(self.base_url,
                             await self._create("db", self._template))

    @contextlib.asynccontextmanager
    async def pool(self):
        pool = await db.connect(await self.database(), max_size=4)
        try:
            yield pool
        finally:
            await pool.close()

    @contextlib.asynccontextmanager
    async def signer(self):
        """A signer in this process, on a socket in a short temporary path
        (Unix socket paths may be only about a hundred bytes long)."""
        tmp = Path(tempfile.mkdtemp(prefix="tt"))
        try:
            signer = Signer(tmp / "signer")
            signer.load_or_create()
            server = await serve(signer, tmp / "s.sock")
            try:
                yield signer, SignerClient(tmp / "s.sock")
            finally:
                server.close()
                await server.wait_closed()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @contextlib.asynccontextmanager
    async def core(self, *, dist: Path | None = None,
                   owner: str = "Denis Gerasimenko", with_signer: bool = True):
        """A running core on free ports, with a small fake page and a signer."""
        async with contextlib.AsyncExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            if dist is None:
                dist = Path(tmp) / "dist"
                (dist / "assets").mkdir(parents=True)
                (dist / "index.html").write_text(
                    "<!doctype html><title>tTerm</title><div id=root></div>")
                (dist / "assets" / "index.js").write_text("console.log(1)")
            client = SignerClient(Path(tmp) / "nobody-here.sock", timeout=0.5)
            if with_signer:
                _, client = await stack.enter_async_context(self.signer())
            settings = Settings.load(environ={
                "TTERM_DATABASE_URL": await self.database(),
                "TTERM_OWNER_NAME": owner,
                "TTERM_WEB_DIST": str(dist),
                "TTERM_CONNECT_URL": "https://connect.test",
            }, env_file=Path(tmp) / "no.env")
            core = Core(settings, signer=client)
            await core.start(page_socket=free_socket(),
                             connect_socket=free_socket())
            try:
                yield core
            finally:
                await core.stop()

    async def close(self) -> None:
        if self._admin is None:
            return
        for name in reversed(self._created):
            with contextlib.suppress(Exception):
                await self._admin.execute(
                    f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await self._admin.close()
        self._admin = None
