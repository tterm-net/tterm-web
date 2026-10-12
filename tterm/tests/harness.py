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
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from ..core import db
from ..core.config import Settings
from ..server import Core


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
    async def core(self, *, dist: Path | None = None, owner: str = "Denis Gerasimenko"):
        """A running core on free ports, with a small fake page."""
        with tempfile.TemporaryDirectory() as tmp:
            if dist is None:
                dist = Path(tmp) / "dist"
                (dist / "assets").mkdir(parents=True)
                (dist / "index.html").write_text(
                    "<!doctype html><title>tTerm</title><div id=root></div>")
                (dist / "assets" / "index.js").write_text("console.log(1)")
            settings = Settings.load(environ={
                "TTERM_DATABASE_URL": await self.database(),
                "TTERM_OWNER_NAME": owner,
                "TTERM_WEB_DIST": str(dist),
            }, env_file=Path(tmp) / "no.env")
            core = Core(settings)
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
