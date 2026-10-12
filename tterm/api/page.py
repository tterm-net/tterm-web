"""The page and its API, for people. Answers only on this computer.

The built page lives in web/dist. Every path that is not the API or a file of
the page gets index.html, so that addresses inside the page survive a reload.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import __version__
from ..core import accounts, machines
from ..core.accounts import Person
from .security import PAGE_HEADERS, AddHeaders, LocalOnly

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


def _json(data, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def create_page_app(pool: asyncpg.Pool, owner: Person, dist: Path,
                    port: int, connect_url: str):
    """The ASGI app for the page, wrapped in the local-only guard."""
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
        """A one-time command that connects a server, for ten minutes."""
        try:
            install = await machines.create_install(
                pool, project_id=request.projectId, user_id=owner.id,
                kind=request.kind, connect_url=connect_url)
        except PermissionError as exc:
            return _json({"error": str(exc)}, 403)
        except ValueError as exc:
            return _json({"error": str(exc)}, 422)
        return _json({"id": str(install.id), "command": install.command,
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
        return _json(details)

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
        return FileResponse(index, media_type="text/html", headers=NO_CACHE)

    # The guard inside the headers, so that its refusals carry them too.
    return AddHeaders(LocalOnly(app, port), PAGE_HEADERS)
