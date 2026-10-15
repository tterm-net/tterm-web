"""The page and its API, for people. Answers only on this computer.

The built page lives in web/dist. Every path that is not the API or a file of
the page gets index.html, so that addresses inside the page survive a reload.
"""
from __future__ import annotations

import html
import secrets
import uuid
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import __version__
from ..core import accounts, machines
from ..core.accounts import Person
from ..core.terminals import NotAllowed, NotOpen, Terminal, Terminals
from .security import PAGE_HEADERS, AddHeaders, LocalOnly, page_csp
from .ws import PageSocket

NO_STORE = {"cache-control": "no-store"}
#: Stage 1 builds the page with stable file names, so the browser must ask
#: every time whether a file changed. Cheap on localhost.
NO_CACHE = {"cache-control": "no-cache"}

PAGE_MISSING = """<!doctype html>
<meta charset="utf-8"><title>tTerm</title>
<p style="font: 15px sans-serif; margin: 40px">
The page is not built: <code>web/dist</code> is missing.
It comes ready in the release archive; unpack the archive again.
</p>
"""


class MePatch(BaseModel):
    theme: str


class InstallRequest(BaseModel):
    projectId: uuid.UUID
    kind: str = "server"


class TerminalRequest(BaseModel):
    machineId: uuid.UUID
    cols: int = 80
    rows: int = 24


class CompleteRequest(BaseModel):
    line: str
    cursor: int


#: A line the input line may ask Tab about, at most.
MAX_LINE = 64 * 1024
NOT_OPEN = "The terminal is not open."


def _with_nonce(page: str, nonce: str) -> str:
    """The page with its one-time style number in a meta tag, right after
    the charset, which must stay first."""
    meta = f'<meta name="tt-style-nonce" content="{html.escape(nonce)}">'
    at = page.find("<meta charset")
    end = page.find(">", at) if at >= 0 else -1
    if end >= 0:
        return page[:end + 1] + "\n    " + meta + page[end + 1:]
    at = page.lower().find("<head>")
    if at >= 0:
        return page[:at + 6] + meta + page[at + 6:]
    # No head written out: after the doctype, never before it, or the
    # browser would fall back to its old quirks.
    end = page.find(">") if page.lower().startswith("<!doctype") else -1
    return page[:end + 1] + meta + page[end + 1:]


def _json(data, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def create_page_app(pool: asyncpg.Pool, owner: Person, dist: Path,
                    port: int, connect_url: str,
                    terminals: Terminals | None = None, agents=None):
    """The ASGI app for the page, wrapped in the local-only guard."""

    def with_agents(machine: dict) -> dict:
        """A computer is online while its agent is connected; the page shows
        it and which agent it has."""
        if machine.get("kind") == "computer":
            agent = agents.describe(machine["id"]) if agents is not None else None
            machine["online"] = agent is not None
            machine["agent"] = agent
        return machine
    app = FastAPI(title="tTerm Web", version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None)
    dist = dist.resolve()

    @app.get("/health")
    async def health() -> Response:
        return _json({"ok": True, "service": "tterm-web page",
                      "version": __version__})

    @app.get("/api/state")
    async def state() -> Response:
        """Everything the page needs on start."""
        me = await accounts.person(pool, owner.id)
        projects = await accounts.projects_of(pool, owner.id)
        for project in projects:
            project["machines"] = [with_agents(m) for m in project["machines"]]
        return _json({
            "version": __version__,
            "me": {
                "id": str(me.id),
                "name": me.name,
                "firstName": me.first_name,
                "initials": me.initials,
                "theme": me.theme,
                "plan": me.plan,
            },
            "projects": projects,
            "terminals": ([t.describe() for t in terminals.live()]
                          if terminals else []),
        })

    @app.patch("/api/me")
    async def patch_me(change: MePatch) -> Response:
        try:
            await accounts.set_theme(pool, owner.id, change.theme)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return Response(status_code=204, headers=NO_STORE)

    @app.post("/api/installs")
    async def new_install(request: InstallRequest) -> Response:
        """A one-time command that connects a server, for ten minutes; for a
        computer, the command that starts its agent with a new key."""
        try:
            install = await machines.create_install(
                pool, project_id=request.projectId, user_id=owner.id,
                kind=request.kind, connect_url=connect_url)
        except PermissionError as exc:
            return _json({"error": str(exc)}, 403)
        except ValueError as exc:
            return _json({"error": str(exc)}, 422)
        return _json({"id": str(install.id), "command": install.command,
                      "byHand": install.by_hand,
                      "expiresAt": install.expires_at.isoformat(),
                      "minutes": machines.INSTALL_MINUTES}, 201)

    @app.get("/api/installs/{install_id}")
    async def install_status(install_id: uuid.UUID) -> Response:
        status = await machines.install_status(pool, install_id, owner.id)
        if status is None:
            return _json({"error": "no such install"}, 404)
        return _json(status)

    @app.get("/api/machines/{machine_id}")
    async def machine(machine_id: uuid.UUID) -> Response:
        details = await machines.machine_details(pool, owner.id, machine_id)
        if details is None:
            return _json({"error": "no such machine"}, 404)
        return _json(with_agents(details))

    @app.delete("/api/machines/{machine_id}")
    async def remove_machine(machine_id: uuid.UUID) -> Response:
        """Takes the machine out of tTerm Web. Its terminals end, a
        computer's agent hears it is removed and stops; the history stays.
        For a server, the answer carries the command that takes tTerm
        Web's access back on the server itself."""
        try:
            removed = await machines.remove(pool, owner.id, machine_id)
        except PermissionError as exc:
            return _json({"error": str(exc)}, 403)
        if removed is None:
            return _json({"error": "no such machine"}, 404)
        ending = f"{removed['name']} was removed from tTerm Web."
        if agents is not None and removed["kind"] == "computer":
            agents.revoke(machine_id, ending)
        if terminals is not None:
            await terminals.end_machine(machine_id, ending)
        return _json(removed)

    @app.post("/api/terminals")
    async def open_terminal(request: TerminalRequest) -> Response:
        """A new terminal on a machine. It connects in the background; the
        page attaches to it over the socket and watches it come up."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        try:
            terminal = await terminals.open(request.machineId, request.cols,
                                            request.rows)
        except NotAllowed as exc:
            return _json({"error": str(exc)}, 404)
        return _json(terminal.describe(), 201)

    @app.get("/api/terminals/{terminal_id}")
    async def terminal_info(terminal_id: uuid.UUID) -> Response:
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        terminal = terminals.get(terminal_id)
        if terminal is not None:
            return _json(terminal.describe())
        ended = await terminals.ended(terminal_id)
        if ended is None:
            return _json({"error": "no such terminal"}, 404)
        return _json(ended)

    @app.delete("/api/terminals/{terminal_id}")
    async def end_terminal(terminal_id: uuid.UUID) -> Response:
        """Ends a terminal: its shell gets a hangup, as when a window
        closes. One whose computer is away stops waiting for it, and the
        shell there ends when the computer is back."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        terminal = terminals.get(terminal_id)
        if terminal is None:
            return _json({"error": "no such terminal"}, 404)
        await terminal.close()
        return _json(terminal.describe())

    @app.get("/api/terminals/{terminal_id}/blocks")
    async def terminal_blocks(terminal_id: uuid.UUID, before: int | None = None,
                              limit: int = 60) -> Response:
        """The feed: finished blocks, the latest first page of them, each
        with the end of its output."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        feed = await terminals.feed(terminal_id, before, limit)
        if feed is None:
            return _json({"error": "no such terminal"}, 404)
        return _json(feed)

    @app.get("/api/terminals/{terminal_id}/blocks/{seq}/output")
    async def block_output(terminal_id: uuid.UUID, seq: int) -> Response:
        """Everything a block kept of its output, as the terminal printed it."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        got = await terminals.block_output(terminal_id, seq)
        if got is None:
            return _json({"error": "no such block"}, 404)
        output, cut = got
        return Response(output, media_type="application/octet-stream",
                        headers={**NO_STORE, "x-tterm-cut": "1" if cut else "0"})

    @app.get("/api/terminals/{terminal_id}/history")
    async def terminal_history(terminal_id: uuid.UUID) -> Response:
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        commands = await terminals.history(terminal_id)
        if commands is None:
            return _json({"error": "no such terminal"}, 404)
        return _json({"commands": commands})

    def _open(terminal_id: uuid.UUID) -> Terminal | None:
        terminal = terminals.get(terminal_id) if terminals else None
        if terminal is None or terminal.state != "open":
            return None
        return terminal

    # Tab and the two lists run a small script on the machine: changes in
    # the HTTP sense or not, they carry the page's header like every POST.

    @app.post("/api/terminals/{terminal_id}/complete")
    async def complete_word(terminal_id: uuid.UUID,
                            request: CompleteRequest) -> Response:
        terminal = _open(terminal_id)
        if terminal is None:
            return _json({"error": NOT_OPEN}, 409)
        if len(request.line) > MAX_LINE:
            return _json({"error": "The line is too long."}, 422)
        try:
            return _json(await terminal.complete(request.line, request.cursor))
        except NotOpen:
            return _json({"error": NOT_OPEN}, 409)

    @app.post("/api/terminals/{terminal_id}/dirs")
    async def terminal_dirs(terminal_id: uuid.UUID) -> Response:
        terminal = _open(terminal_id)
        if terminal is None:
            return _json({"error": NOT_OPEN}, 409)
        try:
            return _json(await terminal.dirs())
        except NotOpen:
            return _json({"error": NOT_OPEN}, 409)

    @app.post("/api/terminals/{terminal_id}/branches")
    async def terminal_branches(terminal_id: uuid.UUID) -> Response:
        terminal = _open(terminal_id)
        if terminal is None:
            return _json({"error": NOT_OPEN}, 409)
        try:
            return _json(await terminal.branches())
        except NotOpen:
            return _json({"error": NOT_OPEN}, 409)

    @app.websocket("/api/ws")
    async def socket(websocket: WebSocket) -> None:
        if terminals is None:
            await websocket.close(code=1013)
            return
        await PageSocket(websocket, terminals).run()

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH",
                                                "DELETE"])
    async def api_missing(rest: str) -> Response:
        return _json({"error": "no such API"}, 404)

    @app.get("/{path:path}")
    async def page(path: str) -> Response:
        if path:
            candidate = (dist / path).resolve()
            # Only files inside dist; `..` and absolute paths end up elsewhere.
            if candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate, headers=NO_CACHE)
            if Path(path).suffix:
                # Looks like a file of the page that is not there.
                return Response("Not found\n", status_code=404,
                                media_type="text/plain", headers=NO_CACHE)
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(PAGE_MISSING, status_code=503, headers=NO_STORE)
        # A fresh number for the page's own style elements, every time.
        nonce = secrets.token_urlsafe(18)
        text = _with_nonce(index.read_text(encoding="utf-8"), nonce)
        return HTMLResponse(text, headers={
            **NO_STORE, "content-security-policy": page_csp(nonce)})

    # The guard inside the headers, so that its refusals carry them too.
    return AddHeaders(LocalOnly(app, port), PAGE_HEADERS)
