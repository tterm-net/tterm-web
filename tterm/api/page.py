"""The page and its API, for people. Answers only on this computer.

The built page lives in web/dist. Every path that is not the API or a file of
the page gets index.html, so that addresses inside the page survive a reload.
"""
from __future__ import annotations

from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import __version__
from ..core import accounts
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


def create_page_app(pool: asyncpg.Pool, owner: Person, dist: Path,
                    port: int):
    """The ASGI app for the page, wrapped in the local-only guard."""
    app = FastAPI(title="tTerm Web", version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None)
    dist = dist.resolve()

    @app.get("/health")
    async def health() -> Response:
        return JSONResponse({"ok": True, "service": "tterm-web page",
                             "version": __version__}, headers=NO_STORE)

    @app.get("/api/state")
    async def state() -> Response:
        """Everything the page needs on start."""
        me = await accounts.person(pool, owner.id)
        projects = await accounts.projects_of(pool, owner.id)
        return JSONResponse({
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
        }, headers=NO_STORE)

    @app.patch("/api/me")
    async def patch_me(change: MePatch) -> Response:
        try:
            await accounts.set_theme(pool, owner.id, change.theme)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return Response(status_code=204, headers=NO_STORE)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH",
                                                "DELETE"])
    async def api_missing(rest: str) -> Response:
        return JSONResponse({"error": "no such API"}, status_code=404,
                            headers=NO_STORE)

    @app.get("/{path:path}")
    async def page(path: str, request: Request) -> Response:
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
