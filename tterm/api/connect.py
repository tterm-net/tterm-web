"""The machines' side: what servers and agents talk to.

Reachable from outside through the tunnel to connect-dev.tterm.net, and on
the server later as connect.tterm.net. Nothing of the page and none of its
API is here: a person's browser has no business on this address. From 0.0.3
it serves the installer and registration, from 0.0.6 the agents.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from .. import __version__
from .security import CONNECT_HEADERS, AddHeaders


def create_connect_app():
    app = FastAPI(title="tTerm Web connect", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> Response:
        return JSONResponse({"ok": True, "service": "tterm-web connect",
                             "version": __version__},
                            headers={"cache-control": "no-store"})

    return AddHeaders(app, CONNECT_HEADERS)
