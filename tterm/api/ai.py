"""AI through MCP on the page's server (0.1.29; core/ai.py, api/mcp.py).

What an AI app reads to sign in, where it signs in, and /mcp itself:

    GET  /.well-known/oauth-protected-resource[/mcp]   RFC 9728
    GET  /.well-known/oauth-authorization-server[/mcp] RFC 8414
    POST /oauth/register     RFC 7591: a client of its own
    GET  /oauth/authorize    to /connect-ai, where the person allows it
    POST /oauth/token        its code, then its refresh tokens, for tokens
    POST /oauth/revoke       RFC 7009: it gives a token back
    POST /mcp                MCP (api/mcp.py); GET and DELETE: 405

None of these carry the page's header or take its cookies: an AI app has
none, only its tokens (PageGuard lets them through, a foreign Origin
apart). Any other /.well-known/ address is 404, not the page.

And what the page asks, within a session, as all of the API:

    GET    /api/ai                          Settings › AI: the person's AI
    GET    /api/ai/requests/{id}            /connect-ai: who asks, the machines
    POST   /api/ai/requests/{id}/allow      Allow
    POST   /api/ai/requests/{id}/deny       Cancel
    PATCH  /api/ai/connections/{id}         Change
    DELETE /api/ai/connections/{id}         Forget
    DELETE /api/ai/connections/{id}/projects/{project}   Disconnect from the project
    POST   /api/ai/forget-all               Disconnect all AI
"""
from __future__ import annotations

import base64
import binascii
import uuid
from types import SimpleNamespace
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, unquote_plus

import asyncpg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..core import ai, auth, vault
from ..core.terminals import Terminals
from .connect import client_ip
from .mcp import Mcp, capped_body
from .ws import page_json

NO_STORE = {"cache-control": "no-store"}
TOKEN_HEADERS = {"cache-control": "no-store", "pragma": "no-cache"}
#: What the metadata may be kept for by those who read it.
METADATA_CACHE = {"cache-control": "public, max-age=300"}
#: Bodies, at most: a registration, a token request, a choice of the page.
REGISTER_MAX = 64 * 1024
FORM_MAX = 16 * 1024
CHOICE_MAX = 128 * 1024

#: The paths that answer AI apps, which carry no page header (PageGuard).
OPEN_PATHS = frozenset({"/mcp", "/mcp/", "/oauth/register", "/oauth/token",
                        "/oauth/revoke"})


class _Bad(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def _body(request: Request, most: int) -> bytes:
    raw = await capped_body(request, most)
    if raw is None:
        raise _Bad(413, "The request is too big.")
    return raw


async def _form(request: Request) -> dict[str, str]:
    """A form as OAuth sends it (application/x-www-form-urlencoded), each
    field once (RFC 6749 3.2); JSON as well, for apps that send that."""
    raw = await _body(request, FORM_MAX)
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind == "application/json":
        try:
            data = page_json(raw)
        except ValueError:
            raise ai.OAuthError("invalid_request", "That is not JSON.") from None
        if not isinstance(data, dict):
            raise ai.OAuthError("invalid_request", "Send the fields as an object.")
        return {k: v for k, v in data.items() if isinstance(v, str)}
    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True)
    except (UnicodeDecodeError, ValueError):
        raise ai.OAuthError("invalid_request", "Send the fields as a form.") from None
    form: dict[str, str] = {}
    for key, value in pairs:
        if key in form:
            raise ai.OAuthError("invalid_request", f"{key} is there twice.")
        form[key] = value
    return form


def _client_auth(request: Request, form: dict[str, str]) -> ai.ClientAuth:
    """Who the app says it is: HTTP Basic (RFC 6749 2.3.1, both halves
    form-encoded) or client_id and client_secret in the form."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "basic" and value.strip():
        try:
            pair = base64.b64decode(value.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise ai.OAuthError("invalid_client", "The Basic credentials are "
                                "not readable.", 401) from None
        cid, sep, secret = pair.partition(":")
        if not sep:
            raise ai.OAuthError("invalid_client", "The Basic credentials are "
                                "not readable.", 401)
        cid, secret = unquote_plus(cid), unquote_plus(secret)
        if form.get("client_id", cid) != cid:
            raise ai.OAuthError("invalid_request", "Two different client_id.")
        return ai.ClientAuth(cid, secret)
    return ai.ClientAuth(form.get("client_id"), form.get("client_secret"))


def _oauth_error(exc: ai.OAuthError) -> Response:
    headers = dict(TOKEN_HEADERS)
    if exc.error == "invalid_client":
        headers["www-authenticate"] = 'Basic realm="tTerm"'
    return JSONResponse(exc.body(), status_code=exc.status, headers=headers)


def _json(data, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def add_ai(app: FastAPI, *, pool: asyncpg.Pool, terminals: Terminals | None,
           page_url: str, secret: bytes, signed_in: Callable,
           keys_open: Callable[[auth.Session], bool],
           keys_open_on: Callable[[uuid.UUID | None, uuid.UUID | None], bool],
           guarding: Callable[[auth.Session], Awaitable[bool]],
           notify: Callable[[uuid.UUID, dict, object], Awaitable[None]]) -> Mcp:
    """The routes of AI through MCP, on the page's app: before its last
    ones, which answer whatever is left with the page. The MCP server."""
    issuer = page_url
    resource = f"{page_url}/mcp"

    def resource_ok(value: str) -> bool:
        """The MCP address as the app has it, or tTerm's own."""
        return value.rstrip("/") in (resource, issuer)

    mcp = Mcp(pool, terminals, page_url=page_url, keys_open_on=keys_open_on,
              notify=notify)

    # ------------------------------------------------ what apps read first

    def protected_resource() -> Response:
        return JSONResponse({
            "resource": resource,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
            "resource_name": "tTerm",
        }, headers=METADATA_CACHE)

    def authorization_server() -> Response:
        return JSONResponse({
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "revocation_endpoint": f"{issuer}/oauth/revoke",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": [
                "none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": [
                "none", "client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": False,
        }, headers=METADATA_CACHE)

    for path in ("/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/mcp"):
        app.add_api_route(path, protected_resource, methods=["GET"])
    for path in ("/.well-known/oauth-authorization-server",
                 "/.well-known/oauth-authorization-server/mcp"):
        app.add_api_route(path, authorization_server, methods=["GET"])

    @app.get("/.well-known/{rest:path}")
    async def well_known_missing(rest: str) -> Response:
        return _json({"error": "not_found"}, 404)

    # ------------------------------------------------------ signing apps in

    @app.post("/oauth/register")
    async def register(request: Request) -> Response:
        try:
            raw = await _body(request, REGISTER_MAX)
            try:
                body = page_json(raw)
            except ValueError:
                raise ai.OAuthError("invalid_client_metadata",
                                    "Send the client's metadata as JSON.") from None
            answer = await ai.register(pool, body,
                                       net=auth.network_of(client_ip(request)))
        except _Bad as exc:
            return _json({"error": "invalid_client_metadata",
                          "error_description": str(exc)}, exc.status)
        except ai.OAuthError as exc:
            return _json(exc.body(), exc.status)
        return _json(answer, 201)

    @app.get("/oauth/authorize")
    async def authorize(request: Request) -> Response:
        query = request.query_params
        if any(len(query.getlist(k)) > 1 for k in query.keys()):
            return RedirectResponse("/connect-ai?problem=request", 302,
                                    headers=NO_STORE)
        target = await ai.authorize(pool, dict(query), issuer=issuer,
                                    resource_ok=resource_ok,
                                    net=auth.network_of(client_ip(request)))
        return RedirectResponse(target, 302, headers=NO_STORE)

    @app.post("/oauth/token")
    async def token(request: Request) -> Response:
        try:
            form = await _form(request)
            given = _client_auth(request, form)
            grant = form.get("grant_type")
            if grant == "authorization_code":
                answer = await ai.exchange(pool, form, given,
                                           resource_ok=resource_ok)
            elif grant == "refresh_token":
                answer = await ai.refresh(pool, secret, form, given,
                                          resource_ok=resource_ok)
            else:
                raise ai.OAuthError("unsupported_grant_type", "Only "
                                    "authorization_code and refresh_token.")
        except _Bad as exc:
            return _oauth_error(ai.OAuthError("invalid_request", str(exc),
                                              exc.status))
        except ai.OAuthError as exc:
            return _oauth_error(exc)
        return JSONResponse(answer, headers=TOKEN_HEADERS)

    @app.post("/oauth/revoke")
    async def revoke(request: Request) -> Response:
        try:
            form = await _form(request)
            await ai.revoke(pool, form.get("token"), _client_auth(request, form))
        except _Bad as exc:
            return _oauth_error(ai.OAuthError("invalid_request", str(exc),
                                              exc.status))
        except ai.OAuthError as exc:
            return _oauth_error(exc)
        return Response(status_code=200, headers=NO_STORE)

    # ------------------------------------------------------------------ MCP

    async def mcp_post(request: Request) -> Response:
        return await mcp.handle(request)

    async def mcp_other() -> Response:
        return Response(status_code=405, headers={**NO_STORE, "allow": "POST"})

    for path in ("/mcp", "/mcp/"):
        app.add_api_route(path, mcp_post, methods=["POST"])
        app.add_api_route(path, mcp_other, methods=["GET", "DELETE", "PUT",
                                                    "PATCH"])

    # ------------------------------------------------------ for the page

    def refused(exc: ai.Refused) -> Response:
        return _json({"error": str(exc), **exc.details}, exc.status)

    async def choice_body(request: Request) -> dict:
        raw = await _body(request, CHOICE_MAX)
        try:
            data = page_json(raw)
        except ValueError:
            raise ai.Refused("That is not JSON.") from None
        if not isinstance(data, dict):
            raise ai.Refused("Say what the AI may do.")
        return data

    async def keys_proof(request: Request, session: auth.Session,
                         body: dict, guarded: list[str]) -> Response | None:
        """For machines under the keys: None to go on, when the keys are
        open on this device or nothing is closed, or the vault's password
        proof in the body is right; 423 to ask for it otherwise, or the
        vault's refusal."""
        if not guarded or keys_open(session) or not await guarding(session):
            return None
        if body.get("auth") is None:
            return _json({"error": "These machines are behind your keys: open "
                          "the keys on this device to let the AI see them.",
                          "locked": True, "machines": guarded,
                          "password": await ai.vault_password(
                              pool, session.person.id)}, 423)
        try:
            await vault.open_with_password(
                pool, session.person.id, auth=body.get("auth"),
                device=auth.socket_label(session,
                                         request.headers.get("user-agent", "")),
                ip=client_ip(request))
        except vault.Refused as exc:
            return _json({"error": str(exc), **exc.details}, exc.status)
        return None

    async def with_keys(listed: list[dict], session: auth.Session) -> list[dict]:
        """Each connection with whether its machines under the keys are
        open to it now (all of them are while the person's keys guard
        nothing), and whether they follow this device."""
        guards = await guarding(session)
        for c in listed:
            device = uuid.UUID(c["keysDevice"]["id"]) if c["keysDevice"] else None
            keys_session = uuid.UUID(c["keysSession"]) if c["keysSession"] else None
            c["keysOpen"] = not guards or keys_open_on(device, keys_session)
            c["keysHere"] = ((device is not None and device == session.device_id)
                             or keys_session == session.id)
        return listed

    @app.get("/api/ai")
    async def ai_state(session: auth.Session = Depends(signed_in)) -> Response:
        me = session.person
        return _json({
            "url": resource,
            "connections": await with_keys(await ai.connections_of(pool, me.id),
                                           session),
            "keys": {"guarding": await guarding(session),
                     "open": keys_open(session)},
            "here": {"device": str(session.device_id) if session.device_id else None,
                     "session": str(session.id)},
        })

    @app.get("/api/ai/requests/{request_id}")
    async def ai_request(request_id: uuid.UUID,
                         session: auth.Session = Depends(signed_in)) -> Response:
        me = session.person
        try:
            view = await ai.request_view(pool, request_id, me)
        except ai.Refused as exc:
            return refused(exc)
        if view["same"] is not None:
            await with_keys([view["same"]], session)
        view.update({
            "me": {"id": str(me.id), "name": me.name, "email": me.email},
            "keys": {"guarding": await guarding(session),
                     "open": keys_open(session),
                     "password": await ai.vault_password(pool, me.id)},
        })
        return _json(view)

    @app.post("/api/ai/requests/{request_id}/allow")
    async def ai_allow(request_id: uuid.UUID, request: Request,
                       session: auth.Session = Depends(signed_in)) -> Response:
        me = session.person
        try:
            body = await choice_body(request)
            account = body.get("account")
            if account is not None and account != str(me.id):
                return _json({"error": "This browser is signed in to another "
                              "account now. Check which one, then allow.",
                              "changed": True}, 409)
            if session.age > auth.APP_ALLOW_FRESH:
                return _json({"error": "Confirm it is you first: an AI you "
                              "allow stays connected.", "confirm": True}, 403)
            choice = ai.parse_choice(body)
            machines = await ai.choosable(pool, me.id, choice.machines)
            guarded = sorted(m.name for m in machines.values()
                             if vault.guarded(m))
            if (stop := await keys_proof(request, session, body, guarded)) is not None:
                return stop
            redirect = await ai.allow(pool, request_id, session, choice,
                                      issuer=issuer)
        except _Bad as exc:
            return _json({"error": str(exc)}, exc.status)
        except ai.Refused as exc:
            return refused(exc)
        return _json({"redirect": redirect})

    @app.post("/api/ai/requests/{request_id}/deny")
    async def ai_deny(request_id: uuid.UUID,
                      session: auth.Session = Depends(signed_in)) -> Response:
        try:
            redirect = await ai.deny(pool, request_id, issuer=issuer)
        except ai.Refused as exc:
            return refused(exc)
        return _json({"redirect": redirect})

    @app.patch("/api/ai/connections/{connection_id}")
    async def ai_change(connection_id: uuid.UUID, request: Request,
                        session: auth.Session = Depends(signed_in)) -> Response:
        me = session.person
        try:
            body = await choice_body(request)
            choice = ai.parse_choice(body, change=True)
            before = await ai.grants_now(pool, me.id, connection_id)
            if before is None:
                return _json({"error": "This AI is not connected any more."}, 404)
            machines = await ai.choosable(pool, me.id, choice.machines)
            # Machines under the keys it did not see before, or saw less of,
            # take the keys open here or the password, as at /connect-ai.
            # Saved with the keys open here, or so, the machines under them
            # follow this device from now: where they were allowed last.
            guarded = [mid for mid, m in machines.items() if vault.guarded(m)]
            added = sorted(
                machines[mid].name for mid in guarded
                if any(new and not old for new, old in zip(
                    choice.machines[mid], before.get(mid, (False, False)))))
            if (stop := await keys_proof(request, session, body, added)) is not None:
                return stop
            follow = bool(guarded) and (bool(added) or keys_open(session))
            changed = await ai.change(pool, me.id, connection_id, choice,
                                      keys_from=session if follow else None)
        except _Bad as exc:
            return _json({"error": str(exc)}, exc.status)
        except ai.Refused as exc:
            return refused(exc)
        return _json((await with_keys([changed], session))[0])

    @app.delete("/api/ai/connections/{connection_id}")
    async def ai_forget(connection_id: uuid.UUID,
                        session: auth.Session = Depends(signed_in)) -> Response:
        if not await ai.forget(pool, session.person.id, connection_id):
            return _json({"error": "This AI is not connected any more."}, 404)
        return _json({"forgotten": True})

    @app.delete("/api/ai/connections/{connection_id}/projects/{project_id}")
    async def ai_leave(connection_id: uuid.UUID, project_id: uuid.UUID,
                       session: auth.Session = Depends(signed_in)) -> Response:
        if not await ai.leave_project(pool, session.person.id, connection_id,
                                      project_id):
            return _json({"error": "This AI is not connected any more."}, 404)
        return _json({"left": True})

    @app.post("/api/ai/forget-all")
    async def ai_forget_all(session: auth.Session = Depends(signed_in)) -> Response:
        return _json({"forgotten": await ai.forget_all(pool, session.person.id)})

    async def closed_here(session: auth.Session, machine) -> bool:
        """As the page's closed_to: a machine under the keys, the keys not
        open on this device (0.1.28)."""
        if (keys_open(session) or not vault.guarded(machine)
                or vault.exempt(session.computer_id, machine)):
            return False
        return await guarding(session)

    @app.post("/api/ai/proposals/{proposal_id}/{what}")
    async def ai_proposal(proposal_id: uuid.UUID, what: str,
                          session: auth.Session = Depends(signed_in)) -> Response:
        """A command an AI proposed (0.1.30): the person put it into the
        input line (`insert`, the page puts it there) or removed it
        (`remove`). Running it goes through the terminal's socket, as any
        command typed."""
        if what not in ("insert", "remove"):
            return _json({"error": "Put it into the line, or remove it."}, 404)
        me = session.person
        # Not from a device its machine is closed to (0.1.28), either way:
        # the answer would say whether it waits there.
        terminal_id = await ai.proposal_terminal(pool, me.id, proposal_id)
        t = (terminals.get(terminal_id, me.id)
             if terminals is not None and terminal_id is not None else None)
        machine = t.machine if t is not None else None
        if machine is None and terminal_id is not None:
            row = await pool.fetchrow(
                "SELECT m.id, m.name, m.kind, m.login_method, m.via_machine_id "
                "FROM terminals t JOIN machines m ON m.id = t.machine_id "
                "WHERE t.id = $1", terminal_id)
            machine = SimpleNamespace(**dict(row)) if row is not None else None
        if (terminal_id is None or (what == "insert" and t is None)
                or machine is None or await closed_here(session, machine)):
            return _json({"error": "This proposal is over."}, 410)
        try:
            taken = await ai.decide(pool, me.id, proposal_id,
                                    "inserted" if what == "insert" else "removed")
        except ai.Refused as exc:
            return refused(exc)
        t = (terminals.get(uuid.UUID(taken["terminalId"]), me.id)
             if terminals is not None else None)
        if t is not None:
            if what == "insert":
                t.expect_proposal(proposal_id, taken["ai"], taken["command"],
                                  ai.PROPOSAL_INSERT_SECONDS)
            # The person's other pages put it away too.
            await notify(me.id, {"op": "ai.proposal",
                                 "terminalId": taken["terminalId"],
                                 "proposal": None, "was": taken["id"]}, t.machine)
        # Removed: nothing of it goes back (a page that does not see the
        # terminal never had it anyway).
        return _json({"proposal": taken} if what == "insert" else {"removed": True})

    return mcp
