"""The core as a running thing: the database, the page, the machines' side.

Two listeners, as on the server later with its two addresses: the page and
its API for people, and the side machines talk to. They are separate apps on
separate ports, so the tunnel, which points at the machines' port, cannot
lead anyone to the page. The signer is a separate process; the core only
talks to it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket

import asyncpg
import uvicorn

from .api.connect import create_connect_app
from .api.page import create_page_app
from .core import accounts, db, machines
from .core.terminals import Terminals
from .core.accounts import Person
from .core.config import Settings
from .core.signer_client import SignerClient, SignerRefused, SignerUnavailable

log = logging.getLogger("tterm")


class PortBusy(Exception):
    """A port we need is taken; the message says which and what to check."""


class _Server(uvicorn.Server):
    """uvicorn without its own signal handling.

    Each uvicorn server takes over Ctrl+C for itself; with two of them in one
    process only one would hear it. The core stops both together instead.
    """

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def listen(host: str, port: int) -> socket.socket:
    """Takes the port before anything else starts.

    A busy port is then a plain sentence, not a stack trace from deep inside
    the server.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        raise PortBusy(
            f"Port {port} on {host} is taken ({exc.strerror}). Is tTerm Web "
            "already running in another window?") from None
    sock.listen(128)
    return sock


class Core:
    """Starts and stops everything the core runs, in the right order."""

    def __init__(self, settings: Settings,
                 signer: SignerClient | None = None) -> None:
        self.settings = settings
        self.signer = signer or SignerClient(settings.signer_socket)
        self.pool: asyncpg.Pool | None = None
        self.owner: Person | None = None
        self.active_ca: dict | None = None
        self.terminals: Terminals | None = None
        self._servers: list[_Server] = []
        self._tasks: list[asyncio.Task] = []
        self._sockets: list[socket.socket] = []

    @property
    def page_port(self) -> int:
        return self._sockets[0].getsockname()[1]

    @property
    def connect_port(self) -> int:
        return self._sockets[1].getsockname()[1]

    async def start(self, page_socket: socket.socket | None = None,
                    connect_socket: socket.socket | None = None) -> None:
        s = self.settings
        page_socket = page_socket or listen(s.page_host, s.page_port)
        try:
            connect_socket = connect_socket or listen(s.connect_host,
                                                      s.connect_port)
        except BaseException:
            page_socket.close()
            raise
        self._sockets = [page_socket, connect_socket]
        try:
            self.pool = await db.connect(s.database_url)
            await db.migrate(self.pool)
            self.owner = await accounts.ensure_local_owner(self.pool, s.owner_name)
            await self._meet_signer()
            self.terminals = Terminals(self.pool, self.signer, self.owner)
            await self.terminals.start()
        except BaseException:
            await self.stop()
            raise

        page = create_page_app(self.pool, self.owner, s.web_dist,
                               self.page_port, s.connect_url, self.terminals)
        connect = create_connect_app(self.pool, self.signer, s.connect_url)
        for app, sock, proxied in ((page, self._sockets[0], False),
                                   (connect, self._sockets[1], True)):
            config = uvicorn.Config(
                app, lifespan="off", log_level="warning", access_log=False,
                server_header=False,
                # The core reads the proxy's headers itself, and only when the
                # proxy is on this machine: see client_ip in api/connect.py.
                proxy_headers=False)
            server = _Server(config)
            self._servers.append(server)
            self._tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
        # Wait until both answer, so that whoever started us can use them.
        while not all(server.started for server in self._servers):
            for task in self._tasks:
                if task.done():
                    await task          # raises what killed it
                    raise RuntimeError("a server stopped while starting")
            await asyncio.sleep(0.02)

    async def _meet_signer(self) -> None:
        """Learns the CA keys. Without the signer the page still works;
        connecting machines does not, and says so when tried."""
        try:
            cas = await self.signer.keys()
        except (SignerUnavailable, SignerRefused) as exc:
            log.warning("%s Connecting machines will not work until it does.",
                        exc)
            return
        await machines.remember_cas(self.pool, cas)
        self.active_ca = next((ca for ca in cas if ca["active"]), None)

    async def wait(self) -> None:
        """Returns when any server stops on its own, which it should not."""
        if self._tasks:
            await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)

    async def stop(self, grace: float = 5.0) -> None:
        # Terminals first: their ends go to the database, which closes last.
        if self.terminals is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.terminals.stop(), grace)
            self.terminals = None
        for server in self._servers:
            server.should_exit = True
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=grace)
            if pending:
                for server in self._servers:
                    server.force_exit = True
                await asyncio.wait(pending, timeout=grace)
        if self.pool is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.pool.close(), grace)
            self.pool = None
        for sock in self._sockets:
            with contextlib.suppress(OSError):
                sock.close()
        self._servers, self._tasks, self._sockets = [], [], []
