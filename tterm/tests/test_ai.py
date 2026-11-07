"""AI through MCP (tTerm Web 0.1.29; core/ai.py, api/ai.py, api/mcp.py).

The owner's decision of 05.11: Claude, ChatGPT or any AI app with MCP
connects to tTerm as the person allows it on /connect-ai, and reads what
it was allowed, nothing more: the machines ticked, their terminals or their
log, the machines under the keys only while the keys are open on the
device it was allowed from, secrets cut, and, since 06.11, for every
account, with nothing set on the server. These checks go through it the
way an AI app does: register, authorize, the person allows, the code for
tokens, MCP of both kinds, the refresh tokens, and what the person
changes.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import http.client
import json
import secrets
import shutil
import time
import uuid
import zlib
from urllib.parse import parse_qs, urlencode, urlsplit

from ..api import ai as api_ai
from ..api import mcp
from ..core import ai, auth, leaks, logs
from .harness import OWNER_EMAIL, page_socket, request, shell_server, sign_in_as
from .test_feed import _frames, _op
from .test_keys_here import _made_vault
from .test_terminals import (CHANGE, _ca_public, _home, _login_shell,
                             _prompts, _until)
from .test_terminals import _machine as _live_machine
from .test_vault import _heard_one, _proven, _pw

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
NOBODY = {"cookie": ""}
JSON_BODY = {"content-type": "application/json"}


# ---------------------------------------------------------------- helpers

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, auth.pkce_challenge(verifier)


def _resource(core) -> str:
    return f"http://localhost:{core.page_port}/mcp"


async def _register(core, *, redirect: str = CLAUDE, **extra) -> dict:
    body = {"client_name": "Claude", "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
            **extra}
    r = await request(core.page_port, "POST", "/oauth/register",
                      headers={**NOBODY, **JSON_BODY},
                      body=json.dumps(body).encode())
    assert r.status == 201, r.text
    return r.json()


async def _authorize(core, client: dict, challenge: str, *,
                     redirect: str = CLAUDE, state: str = "st-1",
                     **extra) -> str:
    query = {"response_type": "code", "client_id": client["client_id"],
             "redirect_uri": redirect, "code_challenge": challenge,
             "code_challenge_method": "S256", "state": state,
             "resource": _resource(core), "scope": "tterm", **extra}
    query = {k: v for k, v in query.items() if v is not None}
    r = await request(core.page_port, "GET",
                      f"/oauth/authorize?{urlencode(query)}", headers=NOBODY)
    assert r.status == 302, r.text
    return r.headers["location"]


def _query(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def _allow(core, request_id: str, machines, *, term: str = "forever",
                 cookie: str | None = None, password: str | None = None,
                 can: dict | None = None):
    body = {"machines": [{"id": str(m), "terminals": t, "log": lg}
                         for m, t, lg in machines],
            "can": can or {"see": True}, "term": term,
            "zone": "Europe/Lisbon"}
    if password is not None:
        body["auth"] = password
    headers = dict(CHANGE)
    if cookie is not None:
        headers["cookie"] = cookie
    return await request(core.page_port, "POST",
                         f"/api/ai/requests/{request_id}/allow",
                         headers=headers, body=json.dumps(body).encode())


async def _token(core, form: dict, headers: dict | None = None):
    return await request(
        core.page_port, "POST", "/oauth/token",
        headers={**NOBODY,
                 "content-type": "application/x-www-form-urlencoded",
                 **(headers or {})},
        body=urlencode(form).encode())


async def _asked(core, *, redirect: str = CLAUDE, **register) -> tuple[dict, str, str]:
    """A registered app that asked to connect: it, its verifier, and the
    request /connect-ai shows."""
    client = await _register(core, redirect=redirect, **register)
    verifier, challenge = _pkce()
    location = await _authorize(core, client, challenge, redirect=redirect)
    assert location.startswith("/connect-ai?request="), location
    return client, verifier, _query(location)["request"]


async def _connect(core, machines, *, term: str = "forever",
                   cookie: str | None = None, password: str | None = None,
                   redirect: str = CLAUDE, can: dict | None = None) -> dict:
    """An app connected as the person allows it: its tokens, and the app."""
    client, verifier, request_id = await _asked(core, redirect=redirect)
    r = await _allow(core, request_id, machines, term=term, cookie=cookie,
                     password=password, can=can)
    assert r.status == 200, r.text
    back = _query(r.json()["redirect"])
    assert back["state"] == "st-1" and back["iss"] == f"http://localhost:{core.page_port}"
    r = await _token(core, {"grant_type": "authorization_code",
                            "code": back["code"], "redirect_uri": redirect,
                            "client_id": client["client_id"],
                            "code_verifier": verifier,
                            "resource": _resource(core)})
    assert r.status == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    return {**r.json(), "client": client}


async def _mcp(core, token: str | None, method: str, params: dict | None = None,
               *, msg_id=1, modern: bool = False, headers: dict | None = None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    h = {**NOBODY, **JSON_BODY, "accept": "application/json, text/event-stream"}
    if token:
        h["authorization"] = f"Bearer {token}"
    if modern:
        meta = body.setdefault("params", {}).setdefault("_meta", {})
        meta.update({"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                     "io.modelcontextprotocol/clientCapabilities": {},
                     "io.modelcontextprotocol/clientInfo": {"name": "check",
                                                            "version": "1"}})
        h.update({"mcp-protocol-version": "2026-07-28", "mcp-method": method})
        if method == "tools/call":
            h["mcp-name"] = body["params"]["name"]
    else:
        h["mcp-protocol-version"] = "2025-06-18"
    h.update(headers or {})
    return await request(core.page_port, "POST", "/mcp", headers=h,
                         body=json.dumps(body).encode())


async def _tool(core, token: str, name: str, args: dict | None = None,
                **kw) -> tuple[dict | str, bool]:
    r = await _mcp(core, token, "tools/call",
                   {"name": name, "arguments": args or {}}, **kw)
    assert r.status == 200, r.text
    result = r.json()["result"]
    text = result["content"][0]["text"]
    try:
        return json.loads(text), result["isError"]
    except ValueError:
        return text, result["isError"]


async def _project(core) -> uuid.UUID:
    return await core.pool.fetchval(
        "SELECT id FROM projects WHERE owner_id = $1 ORDER BY created_at "
        "LIMIT 1", core.owner.id)


async def _machine(core, project, name: str, kind: str = "server") -> uuid.UUID:
    """A machine as rows: a server on tTerm's line (not under the keys), or
    a computer (under them)."""
    return await core.pool.fetchval(
        "INSERT INTO machines (project_id, kind, name, host, port, login, "
        "host_keys, created_by) VALUES ($1, $2, $3, $4, $5, $6, '{}', $7) "
        "RETURNING id", project, kind, name,
        "203.0.113.7" if kind == "server" else None,
        22 if kind == "server" else None,
        "tterm" if kind == "server" else None, core.owner.id)


async def _terminal(core, machine, owner=None, *, ended: bool = True) -> uuid.UUID:
    return await core.pool.fetchval(
        "INSERT INTO terminals (machine_id, owner_id, name, closed_at) "
        "VALUES ($1, $2, '1', CASE WHEN $3 THEN now() END) RETURNING id",
        machine, owner or core.owner.id, ended)


async def _ran(core, project, machine, terminal, seq: int, command: str,
               output: bytes = b"done\n", *, user=None, exit_code: int = 0,
               at: dt.datetime | None = None) -> int:
    """A command that ended, as a terminal saves it: its block, with what it
    printed, and its line in the machine's log."""
    user = user or core.owner.id
    at = at or dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    async with core.pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO blocks (terminal_id, seq_start, seq_end, command, "
            "started_by, started_at, finished_at, exit_code, cwd, branch, "
            "output, output_text, cols, expires_at) VALUES ($1, $2, $3, $4, $5, "
            "$6, $7, $8, '/srv/app', 'main', $9, $10, 120, "
            "now() + interval '30 days')",
            terminal, seq, seq + 3, command, user, at,
            at + dt.timedelta(seconds=2), exit_code, zlib.compress(output),
            output.decode("utf-8", "replace"))
        return await logs.command_ran(
            conn, project_id=project, user_id=user, machine_id=machine,
            terminal_id=terminal, command=command, seq=seq, started_at=at,
            finished_at=at + dt.timedelta(seconds=2), exit_code=exit_code,
            cwd="/srv/app", branch="main", home="/root")


def _names(listed: dict) -> dict[str, dict]:
    return {m["name"]: m for p in listed["projects"] for m in p["machines"]}


# --------------------------------------------------------- every account

async def test_every_account_connects_its_ai(h):
    """The owner's decision of 06.11: AI is on for every account, with
    nothing set on the server. An account nothing names connects its AI,
    and the AI reads that account's machines."""
    async with h.core() as core:
        anna = await sign_in_as(core, "anna@example.com")
        project = await core.pool.fetchval(
            "SELECT id FROM projects WHERE owner_id = (SELECT id FROM users "
            "WHERE email = 'anna@example.com')")
        hers = await _machine(core, project, "anna-01")
        r = await request(core.page_port, "GET", "/api/state",
                          headers={"cookie": anna})
        assert r.json()["ai"] == {"watching": [], "proposals": []}, r.text
        t = await _connect(core, [(hers, True, True)], cookie=anna)
        listed, failed = await _tool(core, t["access_token"], "list_machines")
        assert not failed and set(_names(listed)) == {"anna-01"}, listed
        r = await request(core.page_port, "GET", "/api/ai",
                          headers={"cookie": anna})
        assert [c["name"] for c in r.json()["connections"]] == ["Claude"], r.text


# ---------------------------------------------------------- the secrets

def test_secrets_are_cut_and_the_rest_stays():
    token = "ghp_" + "a" * 36
    cases = {
        f"export GITHUB_TOKEN={token}": f"export GITHUB_TOKEN={leaks.CUT}",
        "DB_PASSWORD=hunter22 ./run": f"DB_PASSWORD={leaks.CUT} ./run",
        '{"password": "hunter2", "user": "x"}':
            f'{{"password": "{leaks.CUT}", "user": "x"}}',
        "mysql -u root -p'S3cret' shop": f"mysql -u root -p{leaks.CUT} shop",
        "mysql -u root -p shop": "mysql -u root -p shop",
        "curl -u admin:pa55word https://x.test/api":
            f"curl -u admin:{leaks.CUT} https://x.test/api",
        'curl -H "Authorization: Bearer abc.def" https://a.test/v1':
            f'curl -H "Authorization: Bearer {leaks.CUT}" https://a.test/v1',
        "git clone https://denis:tok3n@github.com/x/y.git":
            f"git clone https://denis:{leaks.CUT}@github.com/x/y.git",
        "sshpass -p 'x y' ssh host": f"sshpass -p {leaks.CUT} ssh host",
        "--api-key=abcdef123 --verbose": f"--api-key={leaks.CUT} --verbose",
        "tterm_at_" + "B" * 43: leaks.CUT,
        "AKIAIOSFODNN7EXAMPLE and more": f"{leaks.CUT} and more",
        "ls -la /srv/app && git status": "ls -la /srv/app && git status",
        "SSH_AUTH_SOCK=/tmp/ssh-x/agent.1": "SSH_AUTH_SOCK=/tmp/ssh-x/agent.1",
        "npm ERR! code ELIFECYCLE": "npm ERR! code ELIFECYCLE",
    }
    for text, cut in cases.items():
        assert leaks.cut(text) == cut, (text, leaks.cut(text))
    key = ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n"
           "-----END OPENSSH PRIVATE KEY-----\nafter")
    assert "b3BlbnNz" not in leaks.cut(key) and leaks.cut(key).endswith("after")
    # A key whose end the output did not keep: cut to the end.
    assert "MIIE" not in leaks.cut("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nMIIE")


# ---------------------------------------------------- what apps read first

async def test_an_app_reads_where_to_sign_in(h):
    """No token: 401 that says where the metadata is (RFC 9728); the
    metadata of the resource and of the server, as MCP and Claude want
    them; any other /.well-known/ address is not the page."""
    async with h.core() as core:
        base = f"http://localhost:{core.page_port}"
        r = await _mcp(core, None, "initialize", {"protocolVersion": "2025-06-18"})
        assert r.status == 401, r.text
        assert r.headers["www-authenticate"] == (
            f'Bearer resource_metadata="{base}/.well-known/'
            'oauth-protected-resource/mcp"'), r.headers
        r = await _mcp(core, "tterm_at_" + "x" * 43, "tools/list")
        assert r.status == 401 and 'error="invalid_token"' in r.headers[
            "www-authenticate"], r.headers
        for path in ("/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/mcp"):
            r = await request(core.page_port, "GET", path, headers=NOBODY)
            assert r.status == 200, r.text
            assert r.json()["resource"] == f"{base}/mcp"
            assert r.json()["authorization_servers"] == [base]
        r = await request(core.page_port, "GET",
                          "/.well-known/oauth-authorization-server",
                          headers=NOBODY)
        meta = r.json()
        assert meta["issuer"] == base
        assert meta["registration_endpoint"] == f"{base}/oauth/register"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert "none" in meta["token_endpoint_auth_methods_supported"]
        assert meta["authorization_response_iss_parameter_supported"] is True
        # DCR, not CIMD: Claude takes CIMD only when told it is there.
        assert meta["client_id_metadata_document_supported"] is False
        r = await request(core.page_port, "GET",
                          "/.well-known/openid-configuration", headers=NOBODY)
        assert r.status == 404 and r.json() == {"error": "not_found"}, r.text
        # /mcp answers POST only; GET and DELETE are 405, a stream there
        # is not.
        for method in ("GET", "DELETE"):
            r = await request(core.page_port, method, "/mcp", headers=NOBODY)
            assert r.status == 405 and r.headers["allow"] == "POST", (method, r.status)
        # Another site's page may not post to it, nor to the token; the
        # page's own API still wants the page's header.
        r = await _mcp(core, None, "tools/list",
                       headers={"origin": "https://evil.example"})
        assert r.status == 403, r.status
        r = await request(core.page_port, "POST", "/api/ai/forget-all",
                          headers=JSON_BODY, body=b"{}")
        assert r.status == 403, r.status


# ------------------------------------------------------------ registering

async def test_an_app_registers_only_places_it_can_go_back_to(h):
    async with h.core() as core:
        for bad in ("http://claude.ai/cb", "javascript:alert(1)",
                    "https://claude.ai/cb#x", "https://user@evil.example/cb",
                    "file:///etc/passwd", "data:text/html,x", "relative/cb",
                    "https://claude.ai/c b", "ftp://x/cb"):
            r = await request(core.page_port, "POST", "/oauth/register",
                              headers={**NOBODY, **JSON_BODY},
                              body=json.dumps({"redirect_uris": [bad]}).encode())
            assert r.status == 400, (bad, r.status, r.text)
            assert r.json()["error"] == "invalid_redirect_uri", r.text
        for good in (CLAUDE, "http://localhost/callback",
                     "http://127.0.0.1:3118/callback",
                     "cursor://anysphere.cursor-mcp/oauth/callback",
                     "com.example.app:/oauth2redirect"):
            client = await _register(core, redirect=good)
            assert client["redirect_uris"] == [good], client
            assert "client_secret" not in client, "a public client"
        # One that authenticates at the token endpoint gets a secret, once.
        secret_client = await _register(core,
                                        token_endpoint_auth_method="client_secret_post")
        assert secret_client["client_secret"].startswith("tterm_cs_")
        row = await core.pool.fetchrow(
            "SELECT secret_hash, auth_method FROM ai_clients WHERE id = $1",
            uuid.UUID(secret_client["client_id"]))
        assert bytes(row["secret_hash"]) == auth.token_hash(
            secret_client["client_secret"]), "only its hash is kept"
        for bad in ({"redirect_uris": [CLAUDE], "grant_types": ["client_credentials"]},
                    {"redirect_uris": [CLAUDE], "response_types": ["token"]},
                    {"redirect_uris": [CLAUDE],
                     "token_endpoint_auth_method": "private_key_jwt"},
                    {"redirect_uris": []}, [CLAUDE]):
            r = await request(core.page_port, "POST", "/oauth/register",
                              headers={**NOBODY, **JSON_BODY},
                              body=json.dumps(bad).encode())
            assert r.status == 400, (bad, r.text)
        # What an app calls itself, on one line.
        named = await _register(core, client_name="Claude‮\nCode\x07")
        assert named["client_name"] == "Claude Code", named


# ------------------------------------------------------------ authorizing

async def test_authorize_sends_the_person_to_allow_or_the_app_its_error(h):
    async with h.core() as core:
        client = await _register(core, redirect="http://127.0.0.1/callback")
        verifier, challenge = _pkce()
        # Not an app tTerm knows, or a place it did not register: the page,
        # never the place.
        loc = await _authorize(core, {"client_id": str(uuid.uuid4())}, challenge)
        assert loc == "/connect-ai?problem=client", loc
        loc = await _authorize(core, client, challenge,
                               redirect="https://evil.example/cb")
        assert loc == "/connect-ai?problem=redirect", loc
        # This computer on another port is the same place (RFC 8252).
        loc = await _authorize(core, client, challenge,
                               redirect="http://127.0.0.1:54321/callback")
        assert loc.startswith("/connect-ai?request="), loc
        # Errors go back to the app, with its state and who answers.
        for extra, error in (({"code_challenge_method": "plain"}, "invalid_request"),
                             ({"code_challenge": "short"}, "invalid_request"),
                             ({"response_type": "token"}, "unsupported_response_type"),
                             ({"resource": "https://other.example/mcp"},
                              "invalid_target")):
            loc = await _authorize(core, client, challenge,
                                   redirect="http://127.0.0.1:5000/callback",
                                   **extra)
            assert loc.startswith("http://127.0.0.1:5000/callback?"), loc
            q = _query(loc)
            assert q["error"] == error and q["state"] == "st-1", q
            assert q["iss"] == f"http://localhost:{core.page_port}", q
        # A field twice is not a request.
        r = await request(core.page_port, "GET",
                          f"/oauth/authorize?client_id={client['client_id']}"
                          f"&client_id={client['client_id']}", headers=NOBODY)
        assert r.headers["location"] == "/connect-ai?problem=request", r.headers


async def test_the_person_sees_who_asks_and_allows(h):
    """/connect-ai shows the app by where it goes back to, warns of one that
    calls itself Claude and is not, lists the person's machines, and
    allows only with a sign-in made just now, for machines of the
    person's."""
    async with h.core() as core:
        project = await _project(core)
        web = await _machine(core, project, "web-01")
        client, verifier, request_id = await _asked(core)
        r = await request(core.page_port, "GET", f"/api/ai/requests/{request_id}")
        assert r.status == 200, r.text
        view = r.json()
        assert "enabled" not in view and view["me"]["email"] == OWNER_EMAIL
        assert view["request"]["name"] == "Claude"
        assert view["request"]["place"] == {"kind": "web", "host": "claude.ai",
                                            "known": "Claude"}
        assert view["request"]["warning"] is None
        machines = {m["name"]: m for p in view["projects"] for m in p["machines"]}
        assert machines["web-01"]["guarded"] is False
        # Calls itself Claude, goes back elsewhere.
        _, _, fake = await _asked(core, redirect="https://claude-ai.example/cb")
        r = await request(core.page_port, "GET", f"/api/ai/requests/{fake}")
        assert r.json()["request"]["name"] == "Claude", "its own words"
        assert r.json()["request"]["warning"] == {
            "claims": "Claude", "real": "claude.ai", "host": "claude-ai.example"}
        # Not a machine of the person's; nothing ticked; proposing or notes
        # without Look (0.1.30); no Look.
        stranger = await sign_in_as(core, "anna@example.com")
        other = await _machine(core, await core.pool.fetchval(
            "SELECT id FROM projects WHERE owner_id = (SELECT id FROM users "
            "WHERE email = 'anna@example.com')"), "anna-01")
        r = await _allow(core, request_id, [(other, True, True)])
        assert r.status == 409, r.text
        r = await _allow(core, request_id, [(web, False, False)])
        assert r.status == 422, r.text
        r = await _allow(core, request_id, [(web, True, True)],
                         can={"see": False, "propose": True, "notes": True})
        assert r.status == 422 and "sees too" in r.json()["error"], r.text
        r = await _allow(core, request_id, [(web, True, True)], can={"see": False})
        assert r.status == 422, r.text
        r = await _allow(core, request_id, [(web, True, True)], term="365")
        assert r.status == 422, r.text
        # Anna allows hers on her own request; the owner's stays open.
        _, _, hers = await _asked(core)
        r = await _allow(core, hers, [(other, True, True)], cookie=stranger)
        assert r.status == 200, r.text
        # A sign-in of long ago confirms first.
        await core.pool.execute(
            "UPDATE sessions SET created_at = now() - interval '1 hour' "
            "WHERE user_id = $1", core.owner.id)
        r = await _allow(core, request_id, [(web, True, True)])
        assert r.status == 403 and r.json().get("confirm") is True, r.text
        await core.pool.execute(
            "UPDATE sessions SET created_at = now() WHERE user_id = $1",
            core.owner.id)
        r = await _allow(core, request_id, [(web, True, False)], term="7")
        assert r.status == 200, r.text
        back = _query(r.json()["redirect"])
        assert back["code"].startswith("tterm_ac_") and back["state"] == "st-1"
        # Answered: not again.
        r = await _allow(core, request_id, [(web, True, False)])
        assert r.status == 410, r.text
        # Not connected until the app trades the code.
        r = await request(core.page_port, "GET", "/api/ai")
        assert r.json()["connections"] == [], r.text
        r = await _token(core, {"grant_type": "authorization_code",
                                "code": back["code"], "redirect_uri": CLAUDE,
                                "client_id": client["client_id"],
                                "code_verifier": verifier})
        assert r.status == 200, r.text
        listed = (await request(core.page_port, "GET", "/api/ai")).json()
        assert "enabled" not in listed and listed["url"] == _resource(core)
        [c] = listed["connections"]
        assert c["name"] == "Claude" and c["returnsTo"] == "claude.ai"
        assert c["can"] == {"see": True, "propose": False, "notes": False}
        assert c["machines"] == [{
            "id": str(web), "name": "web-01", "kind": "server",
            "projectId": str(project), "project": "Personal",
            "code": c["machines"][0]["code"], "terminals": True, "log": False,
            "guarded": False}], c
        assert 6 * 86400_000 < c["until"] - c["connectedAt"] <= 7 * 86400_000 + 5000
        # Cancel: the app hears access_denied.
        _, _, refused = await _asked(core)
        r = await request(core.page_port, "POST",
                          f"/api/ai/requests/{refused}/deny", headers=CHANGE,
                          body=b"{}")
        assert _query(r.json()["redirect"])["error"] == "access_denied", r.text


# ------------------------------------------------------------ the tokens

async def test_a_code_once_with_its_secret_then_refresh_tokens_once(h):
    async with h.core() as core:
        project = await _project(core)
        web = await _machine(core, project, "web-01")
        client, verifier, request_id = await _asked(core)
        back = _query((await _allow(core, request_id, [(web, True, True)])).json()["redirect"])
        form = {"grant_type": "authorization_code", "code": back["code"],
                "redirect_uri": CLAUDE, "client_id": client["client_id"],
                "code_verifier": verifier}
        # A wrong secret spends the code; the connection it was for ends.
        r = await _token(core, {**form, "code_verifier": secrets.token_urlsafe(48)})
        assert r.status == 400 and r.json()["error"] == "invalid_grant", r.text
        r = await _token(core, form)
        assert r.status == 400 and r.json()["error"] == "invalid_grant", r.text
        assert (await request(core.page_port, "GET", "/api/ai")).json()["connections"] == []
        # Another app's id: invalid_client; another place: invalid_grant.
        tokens = await _connect(core, [(web, True, True)])
        assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
        assert tokens["access_token"].startswith("tterm_at_")
        assert tokens["refresh_token"].startswith("tterm_rt_")
        r = await _token(core, {"grant_type": "authorization_code", "code": "x",
                                "client_id": str(uuid.uuid4()),
                                "code_verifier": verifier})
        assert r.status == 400, r.text
        # Only hashes are kept.
        kept = {bytes(x["token_hash"]) for x in await core.pool.fetch(
            "SELECT token_hash FROM ai_tokens")}
        assert auth.token_hash(tokens["access_token"]) in kept
        assert not any(tokens["access_token"].encode() in k for k in kept)
        # A code used a second time ends the connection it made.
        c2, v2, rq2 = await _asked(core)
        b2 = _query((await _allow(core, rq2, [(web, True, True)])).json()["redirect"])
        f2 = {"grant_type": "authorization_code", "code": b2["code"],
              "redirect_uri": CLAUDE, "client_id": c2["client_id"],
              "code_verifier": v2}
        first = await _token(core, f2)
        assert first.status == 200, first.text
        assert (await _mcp(core, first.json()["access_token"], "ping")).status == 200
        assert (await _token(core, f2)).status == 400
        assert (await _mcp(core, first.json()["access_token"], "ping")).status == 401

        # The refresh tokens: each once; the next is the same if asked again
        # at once (a lost answer, two at once); past that, the end.
        refresh = {"grant_type": "refresh_token",
                   "refresh_token": tokens["refresh_token"],
                   "client_id": tokens["client"]["client_id"]}
        r1 = await _token(core, refresh)
        assert r1.status == 200, r1.text
        nxt = r1.json()["refresh_token"]
        assert nxt != tokens["refresh_token"]
        assert r1.json()["access_token"] != tokens["access_token"]
        r2 = await _token(core, refresh)
        assert r2.status == 200 and r2.json()["refresh_token"] == nxt, r2.text
        # The next one used: the first one again is someone else's.
        r3 = await _token(core, {**refresh, "refresh_token": nxt})
        assert r3.status == 200, r3.text
        r4 = await _token(core, refresh)
        assert r4.status == 400 and r4.json()["error"] == "invalid_grant", r4.text
        for t in (tokens["access_token"], r3.json()["access_token"]):
            assert (await _mcp(core, t, "ping")).status == 401
        ended = await core.pool.fetchval(
            "SELECT end_reason FROM ai_connections WHERE id = (SELECT id FROM "
            "ai_connections ORDER BY created_at LIMIT 1 OFFSET 1)")
        assert ended == "refresh used again", ended
        # Within the minute, but the next was used already: the end too.
        t = await _connect(core, [(web, True, True)])
        base = {"grant_type": "refresh_token", "refresh_token": t["refresh_token"],
                "client_id": t["client"]["client_id"]}
        n1 = (await _token(core, base)).json()["refresh_token"]
        assert (await _token(core, {**base, "refresh_token": n1})).status == 200
        assert (await _token(core, base)).status == 400
        # Another app's refresh token is not this one's.
        t = await _connect(core, [(web, True, True)])
        other = await _register(core)
        r = await _token(core, {"grant_type": "refresh_token",
                                "refresh_token": t["refresh_token"],
                                "client_id": other["client_id"]})
        assert r.status == 400, r.text
        # The app gives its token back: the connection ends.
        r = await request(core.page_port, "POST", "/oauth/revoke",
                          headers={**NOBODY, "content-type":
                                   "application/x-www-form-urlencoded"},
                          body=urlencode({"token": t["access_token"],
                                          "client_id": t["client"]["client_id"]}).encode())
        assert r.status == 200, r.text
        assert (await _token(core, {"grant_type": "refresh_token",
                                    "refresh_token": t["refresh_token"],
                                    "client_id": t["client"]["client_id"]})).status == 400


async def test_a_client_with_a_secret_shows_it(h):
    """client_secret_post and client_secret_basic, as the app registered;
    without the secret, invalid_client."""
    async with h.core() as core:
        web = await _machine(core, await _project(core), "web-01")
        client, verifier, request_id = await _asked(
            core, token_endpoint_auth_method="client_secret_basic")
        back = _query((await _allow(core, request_id, [(web, True, True)])).json()["redirect"])
        form = {"grant_type": "authorization_code", "code": back["code"],
                "redirect_uri": CLAUDE, "code_verifier": verifier}
        r = await _token(core, {**form, "client_id": client["client_id"]})
        assert r.status == 401 and r.json()["error"] == "invalid_client", r.text
        assert r.headers["www-authenticate"].startswith("Basic")
        basic = base64.b64encode(
            f"{client['client_id']}:{client['client_secret']}".encode()).decode()
        r = await _token(core, form, headers={"authorization": f"Basic {basic}"})
        assert r.status == 200, r.text


# ------------------------------------------------------------------- MCP

async def test_mcp_of_both_kinds(h):
    """initialize and then the rest, as before 2026-07-28; and requests that
    carry their version, as from 2026-07-28: what each asks of the
    headers, the version, the methods there are."""
    async with h.core() as core:
        web = await _machine(core, await _project(core), "web-01")
        token = (await _connect(core, [(web, True, True)]))["access_token"]
        r = await _mcp(core, token, "initialize",
                       {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "claude-ai", "version": "1"}})
        assert r.status == 200, r.text
        res = r.json()["result"]
        assert res["protocolVersion"] == "2025-06-18"
        assert res["serverInfo"]["name"] == "tterm"
        assert res["capabilities"] == {"tools": {"listChanged": False}}
        assert "Denis Gerasimenko" in res["instructions"]
        assert "mcp-session-id" not in r.headers, "no sessions"
        r = await _mcp(core, token, "initialize", {"protocolVersion": "1999-01-01"})
        assert r.json()["result"]["protocolVersion"] == "2025-11-25"
        # A notification: 202 and nothing.
        r = await request(core.page_port, "POST", "/mcp",
                          headers={**NOBODY, **JSON_BODY,
                                   "authorization": f"Bearer {token}"},
                          body=json.dumps({"jsonrpc": "2.0",
                                           "method": "notifications/initialized"}).encode())
        assert r.status == 202 and r.body == b"", (r.status, r.body)
        r = await _mcp(core, token, "tools/list")
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert names == ["list_machines", "read_terminal", "read_output",
                         "read_log", "search", "wait_for_command"], names
        assert all(t["annotations"]["readOnlyHint"] for t in r.json()["result"]["tools"])
        assert "resultType" not in r.json()["result"]
        assert (await _mcp(core, token, "ping")).json()["result"] == {}
        r = await _mcp(core, token, "resources/list")
        assert r.status == 200 and r.json()["error"]["code"] == -32601
        r = await _mcp(core, token, "tools/list",
                       headers={"mcp-protocol-version": "1999-01-01"})
        assert r.status == 400, r.text
        # Modern.
        r = await _mcp(core, token, "server/discover", modern=True)
        assert r.status == 200, r.text
        res = r.json()["result"]
        assert res["resultType"] == "complete"
        assert res["supportedVersions"] == ["2026-07-28"]
        assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "tterm"
        r = await _mcp(core, token, "tools/list", modern=True)
        res = r.json()["result"]
        assert res["cacheScope"] == "private" and res["ttlMs"] > 0
        assert len(res["tools"]) == 6
        found, error = await _tool(core, token, "list_machines", modern=True)
        assert not error and "web-01" in _names(found), found
        # The headers must say what the body does.
        r = await _mcp(core, token, "tools/list", modern=True,
                       headers={"mcp-method": "tools/call"})
        assert r.status == 400 and r.json()["error"]["code"] == -32020, r.text
        r = await _mcp(core, token, "tools/call",
                       {"name": "list_machines", "arguments": {}}, modern=True,
                       headers={"mcp-name": "search"})
        assert r.status == 400 and r.json()["error"]["code"] == -32020, r.text
        encoded = "=?base64?" + base64.b64encode(b"list_machines").decode() + "?="
        r = await _mcp(core, token, "tools/call",
                       {"name": "list_machines", "arguments": {}}, modern=True,
                       headers={"mcp-name": encoded})
        assert r.status == 200, r.text
        r = await _mcp(core, token, "tools/list", modern=True,
                       headers={"mcp-protocol-version": "2025-06-18"})
        assert r.status == 400 and r.json()["error"]["code"] == -32020, r.text
        r = await _mcp(core, token, "tools/list",
                       {"_meta": {"io.modelcontextprotocol/protocolVersion": "2030-01-01",
                                  "io.modelcontextprotocol/clientCapabilities": {}}},
                       headers={"mcp-protocol-version": "2030-01-01",
                                "mcp-method": "tools/list"})
        assert r.status == 400 and r.json()["error"]["code"] == -32022, r.text
        assert r.json()["error"]["data"]["requested"] == "2030-01-01"
        assert "2026-07-28" in r.json()["error"]["data"]["supported"]
        r = await _mcp(core, token, "ping", modern=True)
        assert r.status == 404 and r.json()["error"]["code"] == -32601, r.text
        # Not JSON-RPC: 400.
        for body in (b"[]", b"{", json.dumps({"id": 1, "method": "ping"}).encode(),
                     json.dumps({"jsonrpc": "2.0", "id": None,
                                 "method": "ping"}).encode()):
            r = await request(core.page_port, "POST", "/mcp",
                              headers={**NOBODY, **JSON_BODY,
                                       "authorization": f"Bearer {token}"},
                              body=body)
            assert r.status == 400, (body, r.status)
        # Unknown tool, bad arguments: a protocol error and a tool's error.
        r = await _mcp(core, token, "tools/call", {"name": "run_command",
                                                   "arguments": {}})
        assert r.json()["error"]["code"] == -32602, r.text
        found, error = await _tool(core, token, "read_output",
                                   {"terminal_id": "x", "seq": 1})
        assert error and "id" in found, found


def _chunked(port: int, path: str, headers: dict, size: int) -> tuple[int, bytes]:
    """A body that says no length (Transfer-Encoding: chunked), of `size`
    bytes of JSON-ish filler: what a request without Content-Length sends."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    piece = 8 * 1024

    def parts():
        yield b'{"client_name": "' + b"x" * max(0, min(piece, size) - 18)
        sent = min(piece, size)
        while sent < size:
            n = min(piece, size - sent)
            yield b"x" * n
            sent += n
        yield b'"}'

    try:
        conn.request("POST", path, body=parts(), encode_chunked=True,
                     headers={"host": f"localhost:{port}", **headers})
        resp = conn.getresponse()
        return resp.status, resp.read()
    except (BrokenPipeError, ConnectionResetError):
        return 0, b""
    finally:
        conn.close()


async def test_what_an_ai_sends_wrong_is_answered(h):
    """The security review of 0.1.29 (06.11): a tool's name that is not a
    string, a date before the first moment in the person's zone, and a big
    body that says no length all get an answer, never a 500 or the whole
    body held first."""
    async with h.core() as core:
        web = await _machine(core, await _project(core), "web-01")
        token = (await _connect(core, [(web, True, True)]))["access_token"]
        for name in ([], {}, 5, None, True):
            r = await _mcp(core, token, "tools/call", {"name": name, "arguments": {}})
            assert r.status == 200, (name, r.status, r.text)
            assert r.json()["error"]["code"] == -32602, (name, r.text)
        r = await _mcp(core, token, "tools/call", {"name": "x" * 5000, "arguments": {}})
        assert r.status == 200 and len(r.json()["error"]["message"]) < 200, r.text

        # East of UTC, year 1 at midnight is before the first moment there is.
        await core.pool.execute("UPDATE ai_connections SET time_zone = 'Asia/Kolkata'")
        for since, until in (("0001-01-01T00:00:00", None),
                             (None, "9999-12-31T23:59:59-12:00"),
                             ("0001-01-01T00:00:00+14:00", None)):
            args = {"machine_id": str(web)}
            if since:
                args["since"] = since
            if until:
                args["until"] = until
            found, error = await _tool(core, token, "read_log", args)
            assert error and "out of range" in found, (args, found)
        logged, error = await _tool(core, token, "read_log", {"machine_id": str(web)})
        assert not error, logged

        # A body with no length: measured as it comes.
        status, _ = await asyncio.to_thread(
            _chunked, core.page_port, "/oauth/register",
            {**JSON_BODY}, api_ai.REGISTER_MAX + 4096)
        assert status in (413, 0), status
        status, body = await asyncio.to_thread(
            _chunked, core.page_port, "/mcp",
            {**JSON_BODY, "authorization": f"Bearer {token}"}, mcp.BODY_MAX + 4096)
        assert status in (413, 0), (status, body)
        # A small one is read as any other.
        r = await asyncio.to_thread(
            _chunked_json, core.page_port, "/oauth/register",
            {"client_name": "Chunked", "redirect_uris": [CLAUDE],
             "token_endpoint_auth_method": "none"})
        assert r[0] == 201, r
        # Nothing of it broke the core: it still answers.
        found, error = await _tool(core, token, "list_machines")
        assert not error and "web-01" in _names(found), found


def _chunked_json(port: int, path: str, data: dict) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    raw = json.dumps(data).encode()
    try:
        conn.request("POST", path, body=iter([raw[:10], raw[10:]]),
                     encode_chunked=True,
                     headers={"host": f"localhost:{port}", **JSON_BODY})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


async def test_the_ai_sees_only_what_it_was_allowed(h):
    """The machines ticked, each its terminals or its log as ticked; the
    person's own terminals there, the whole log; search over those only;
    secrets cut; what it read marked for the page; a machine moved to
    another project, and new machines, not."""
    async with h.core() as core:
        project = await _project(core)
        web = await _machine(core, project, "web-01")
        db = await _machine(core, project, "db-01")
        shop = await _machine(core, project, "shop-01")
        term_web = await _terminal(core, web)
        term_db = await _terminal(core, db)
        term_shop = await _terminal(core, shop)
        anna = await sign_in_as(core, "anna@example.com")
        anna_id = await core.pool.fetchval(
            "SELECT id FROM users WHERE email = 'anna@example.com'")
        await core.pool.execute(
            "INSERT INTO project_members (project_id, user_id, role) "
            "VALUES ($1, $2, 'member')", project, anna_id)
        term_anna = await _terminal(core, web, anna_id)
        await _ran(core, project, web, term_web, 10, "git pull",
                   b"Already up to date.\r\n")
        await _ran(core, project, web, term_web, 20,
                   "export API_TOKEN=s3cr3t-value && deploy",
                   b"\x1b[32mdeployed\x1b[0m with ghp_" + b"z" * 36 + b"\r\n",
                   exit_code=1)
        await _ran(core, project, web, term_anna, 30, "anna-only-command",
                   user=anna_id)
        await _ran(core, project, db, term_db, 40, "psql -c 'select 1'")
        await _ran(core, project, shop, term_shop, 50, "shop-secret-word")
        entry = await core.pool.fetchval(
            "SELECT id FROM journal WHERE kind = 'command.ran' AND "
            "(details->>'seq')::bigint = 10")
        await logs.add_note(core.pool, core.owner.id, "pulled before deploy",
                            entry_id=entry)
        await logs.add_note(core.pool, core.owner.id,
                            "the password is hunter22: password=hunter22",
                            entry_id=entry)
        # web-01: terminals and log; db-01: only its log; shop-01: nothing.
        token = (await _connect(core, [(web, True, True), (db, False, True)]))["access_token"]

        listed, _ = await _tool(core, token, "list_machines")
        names = _names(listed)
        assert set(names) == {"web-01", "db-01"}, names
        assert names["web-01"]["may"] == {"terminals": True, "log": True}
        assert names["db-01"]["may"] == {"terminals": False, "log": True}
        assert "terminals" not in names["db-01"], "not its terminals"
        ids = {t["id"] for t in names["web-01"]["terminals"]}
        assert ids == {str(term_web)}, "the person's own terminals, not Anna's"
        assert listed["you"]["language"] == "en"
        assert listed["ai"]["may"]["see"] is True

        read, error = await _tool(core, token, "read_terminal",
                                  {"terminal_id": str(term_web)})
        assert not error, read
        commands = read["commands"]
        assert [c["seq"] for c in commands] == [10, 20]
        assert commands[0]["output"] == "Already up to date."
        assert commands[0]["notes"][0]["text"] == "pulled before deploy"
        assert commands[0]["notes"][1]["text"] == (
            f"the password is hunter22: password={leaks.CUT}"), commands[0]["notes"]
        assert commands[1]["command"] == f"export API_TOKEN={leaks.CUT} && deploy"
        assert commands[1]["output"] == f"deployed with {leaks.CUT}", commands[1]
        assert commands[1]["exitCode"] == 1 and commands[1]["seconds"] == 2.0
        assert commands[1]["startedAt"].endswith(("+00:00", "Z", "+01:00")), commands[1]
        reads = await core.pool.fetch(
            "SELECT terminal_id, seq_start FROM ai_reads ORDER BY seq_start")
        assert [(r["terminal_id"], r["seq_start"]) for r in reads] == [
            (term_web, 10), (term_web, 20)], reads
        seen = await ai.reads_of(core.pool, term_web, [10, 20, 99])
        assert set(seen) == {10, 20} and seen[10][0]["name"] == "Claude"

        out, error = await _tool(core, token, "read_output",
                                 {"terminal_id": str(term_web), "seq": 20})
        assert not error and out["text"] == f"deployed with {leaks.CUT}", out
        assert out["lines"] == 1 and out["fromLine"] == 1

        for terminal in (term_db, term_shop, term_anna):
            found, error = await _tool(core, token, "read_terminal",
                                       {"terminal_id": str(terminal)})
            assert error, (terminal, found)
        found, error = await _tool(core, token, "read_output",
                                   {"terminal_id": str(term_shop), "seq": 50})
        assert error, found

        logged, error = await _tool(core, token, "read_log",
                                    {"machine_id": str(web)})
        assert not error, logged
        commands = [x["command"] for x in logged["lines"] if x["what"] == "command"]
        assert commands == ["git pull", f"export API_TOKEN={leaks.CUT} && deploy",
                            "anna-only-command"], "the whole log, as the person reads it"
        assert logged["lines"][2]["by"] == "Anna"
        logged, error = await _tool(core, token, "read_log",
                                    {"machine_id": str(db), "newest_first": True,
                                     "since": "2020-01-01T00:00:00"})
        assert error, "more than a month"
        logged, error = await _tool(core, token, "read_log", {"machine_id": str(db)})
        assert not error and logged["lines"][0]["command"] == "psql -c 'select 1'"
        for machine in (shop, uuid.uuid4()):
            found, error = await _tool(core, token, "read_log",
                                       {"machine_id": str(machine)})
            assert error, found

        found, error = await _tool(core, token, "search", {"query": "secret"})
        assert not error, found
        everything = json.dumps(found)
        assert "shop-secret-word" not in everything, "not a machine it sees"
        found, _ = await _tool(core, token, "search", {"query": "select"})
        assert [x["command"] for x in found["logs"]] == ["psql -c 'select 1'"], found
        assert found["commands"] == [], "db-01's terminals it does not see"
        found, _ = await _tool(core, token, "search", {"query": "git pull"})
        assert found["commands"][0]["terminalId"] == str(term_web), found

        # Moved to another project: none of it any more. New machines in
        # the project: none until ticked.
        other = await core.pool.fetchval(
            "INSERT INTO projects (owner_id, name, code, color) VALUES ($1, "
            "'Client X', 'CX', 'sky') RETURNING id", core.owner.id)
        await core.pool.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES "
            "($1, $2, 'owner')", other, core.owner.id)
        await core.pool.execute("UPDATE machines SET project_id = $2 WHERE id = $1",
                                db, other)
        await _machine(core, project, "new-01")
        listed, _ = await _tool(core, token, "list_machines")
        assert set(_names(listed)) == {"web-01"}, _names(listed)
        found, error = await _tool(core, token, "read_log", {"machine_id": str(db)})
        assert error, found


async def test_machines_under_the_keys_only_while_open_where_allowed(h):
    """A computer (or a server with the person's key, or through their Mac)
    the AI sees only while the keys are open on the device it was allowed
    from: allowing it takes the keys open there or the vault's password;
    open on another device is not enough; with no vault, as before."""
    async with h.core() as core:
        project = await _project(core)
        mac = await _machine(core, project, "MacBook-Pro", kind="computer")
        web = await _machine(core, project, "web-01")
        term_mac = await _terminal(core, mac)
        await _ran(core, project, mac, term_mac, 7, "brew update")
        # No vault: nothing is closed, as before 0.1.28.
        free = (await _connect(core, [(mac, True, True)]))["access_token"]
        listed, _ = await _tool(core, free, "list_machines")
        assert "locked" not in _names(listed)["MacBook-Pro"], listed
        await _made_vault(core)
        listed, _ = await _tool(core, free, "list_machines")
        assert _names(listed)["MacBook-Pro"]["locked"] is True, listed
        # Allowing it now: the keys open here, or the password.
        client, verifier, request_id = await _asked(core)
        r = await _allow(core, request_id, [(mac, True, True), (web, True, True)])
        assert r.status == 423, r.text
        assert r.json()["machines"] == ["MacBook-Pro"] and r.json()["password"] is True
        wrong = base64.urlsafe_b64encode(b"w" * 32).rstrip(b"=").decode()
        r = await _allow(core, request_id, [(mac, True, True)], password=wrong)
        assert r.status in (403, 422), r.text
        r = await _allow(core, request_id, [(mac, True, True), (web, True, True)],
                         password=_pw()["auth"])
        assert r.status == 200, r.text
        back = _query(r.json()["redirect"])
        token = (await _token(core, {
            "grant_type": "authorization_code", "code": back["code"],
            "redirect_uri": CLAUDE, "client_id": client["client_id"],
            "code_verifier": verifier})).json()["access_token"]
        listed, _ = await _tool(core, token, "list_machines")
        assert _names(listed)["MacBook-Pro"]["locked"] is True, "the keys are closed now"
        assert "terminals" not in _names(listed)["MacBook-Pro"]
        assert "locked" not in _names(listed)["web-01"]
        found, error = await _tool(core, token, "read_terminal",
                                   {"terminal_id": str(term_mac)})
        assert error and "keys" in found, found
        found, error = await _tool(core, token, "read_log", {"machine_id": str(mac)})
        assert error, found
        found, _ = await _tool(core, token, "search", {"query": "brew"})
        assert found["commands"] == [] and found["logs"] == [], found
        # With the keys open here, no password is asked.
        async with _proven(core, auth=_pw()["auth"]):
            _, _, open_here = await _asked(core)
            r = await _allow(core, open_here, [(mac, True, True)])
            assert r.status == 200, r.text
        # The keys open on another device of the person's: still closed.
        elsewhere = await sign_in_as(core, OWNER_EMAIL)
        async with _proven(core, auth=_pw()["auth"], cookie=elsewhere):
            listed, _ = await _tool(core, token, "list_machines")
            assert _names(listed)["MacBook-Pro"]["locked"] is True
        # Open where it was allowed: it sees the computer. What it read is
        # told only to the pages that see the computer.
        async with _proven(core, auth=_pw()["auth"]) as here, \
                page_socket(core.page_port, cookie=elsewhere) as there:
            listed, _ = await _tool(core, token, "list_machines")
            assert "locked" not in _names(listed)["MacBook-Pro"], listed
            read, error = await _tool(core, token, "read_terminal",
                                      {"terminal_id": str(term_mac)})
            assert not error and read["commands"][0]["command"] == "brew update"
            heard = await _heard_one(here, ("ai.read",))
            assert heard and heard["seqs"] == [7], heard
            assert await _heard_one(there, ("ai.read",), wait=1.0) is None
            state = (await request(core.page_port, "GET", "/api/ai")).json()
            assert all(c["keysOpen"] for c in state["connections"]
                       if c["keysSession"]), state
        listed, _ = await _tool(core, token, "list_machines")
        assert _names(listed)["MacBook-Pro"]["locked"] is True, "closed again"


async def test_a_trusted_device_keeps_its_ai_across_its_sessions(h):
    """Allowed from a trusted browser: the keys open in a later session of
    that browser (eight hours later, with its quick code) count too."""
    async with h.core() as core:
        project = await _project(core)
        mac = await _machine(core, project, "MacBook-Pro", kind="computer")
        await _made_vault(core)
        device = await core.pool.fetchval(
            "INSERT INTO devices (user_id, key_hash, pin_hash, label) VALUES "
            "($1, $2, $3, 'Chrome on macOS') RETURNING id", core.owner.id,
            secrets.token_bytes(32), secrets.token_bytes(32))

        async def session_on_device() -> str:
            token = auth.new_token()
            await core.pool.execute(
                "INSERT INTO sessions (user_id, token_hash, device_id, method) "
                "VALUES ($1, $2, $3, 'pin')", core.owner.id,
                auth.token_hash(token), device)
            return f"{auth.SESSION_COOKIE}={token}"

        first = await session_on_device()
        token = (await _connect(core, [(mac, True, True)], cookie=first,
                                password=_pw()["auth"]))["access_token"]
        later = await session_on_device()
        async with _proven(core, auth=_pw()["auth"], cookie=later):
            listed, _ = await _tool(core, token, "list_machines")
            assert "locked" not in _names(listed)["MacBook-Pro"], listed


# ------------------------------------------------- what the person changes

async def test_change_forget_and_disconnect(h):
    async with h.core() as core:
        project = await _project(core)
        web = await _machine(core, project, "web-01")
        db = await _machine(core, project, "db-01")
        mac = await _machine(core, project, "MacBook-Pro", kind="computer")
        await _made_vault(core)
        t = await _connect(core, [(web, True, True)], term="30")
        cid = (await request(core.page_port, "GET", "/api/ai")).json()["connections"][0]["id"]

        async def change(machines, term="keep", password=None):
            body = {"machines": [{"id": str(m), "terminals": a, "log": b}
                                 for m, a, b in machines],
                    "can": {"see": True}, "term": term, "zone": "Asia/Tokyo"}
            if password:
                body["auth"] = password
            return await request(core.page_port, "PATCH",
                                 f"/api/ai/connections/{cid}", headers=CHANGE,
                                 body=json.dumps(body).encode())

        r = await change([(web, False, True), (db, True, True)])
        assert r.status == 200, r.text
        assert {m["name"]: (m["terminals"], m["log"]) for m in r.json()["machines"]} == {
            "web-01": (False, True), "db-01": (True, True)}
        assert r.json()["zone"] == "Asia/Tokyo"
        listed, _ = await _tool(core, t["access_token"], "list_machines")
        assert set(_names(listed)) == {"web-01", "db-01"}
        assert listed["you"]["timeZone"] == "Asia/Tokyo"
        # Adding a machine under the keys takes them open, or the password.
        r = await change([(web, False, True), (mac, True, False)])
        assert r.status == 423 and r.json()["machines"] == ["MacBook-Pro"], r.text
        r = await change([(web, False, True), (mac, True, False)],
                         password=_pw()["auth"], term="forever")
        assert r.status == 200 and r.json()["until"] is None, r.text
        # Disconnect from the project: none of its machines; the connection
        # stays.
        r = await request(core.page_port, "DELETE",
                          f"/api/ai/connections/{cid}/projects/{project}",
                          headers=CHANGE)
        assert r.status == 200, r.text
        listed, _ = await _tool(core, t["access_token"], "list_machines")
        assert listed["projects"] == [], listed
        # Forget: its tokens answer nothing.
        r = await request(core.page_port, "DELETE", f"/api/ai/connections/{cid}",
                          headers=CHANGE)
        assert r.status == 200, r.text
        assert (await _mcp(core, t["access_token"], "tools/list")).status == 401
        assert (await _token(core, {"grant_type": "refresh_token",
                                    "refresh_token": t["refresh_token"],
                                    "client_id": t["client"]["client_id"]})).status == 400
        r = await request(core.page_port, "DELETE", f"/api/ai/connections/{cid}",
                          headers=CHANGE)
        assert r.status == 404
        # Disconnect all AI; Forget all devices ends every AI too.
        a = await _connect(core, [(web, True, True)])
        b = await _connect(core, [(db, True, True)])
        r = await request(core.page_port, "POST", "/api/ai/forget-all",
                          headers=CHANGE, body=b"{}")
        assert r.json() == {"forgotten": 2}, r.text
        for x in (a, b):
            assert (await _mcp(core, x["access_token"], "ping")).status == 401
        c = await _connect(core, [(web, True, True)])
        r = await request(core.page_port, "POST", "/api/auth/signout-everywhere",
                          headers=CHANGE, body=b"{}")
        assert r.status == 200, r.text
        assert (await _mcp(core, c["access_token"], "ping")).status == 401
        assert await core.pool.fetchval(
            "SELECT count(*) FROM ai_connections WHERE ended_at IS NULL") == 0


async def test_time_up_answers_nothing(h):
    async with h.core() as core:
        web = await _machine(core, await _project(core), "web-01")
        t = await _connect(core, [(web, True, True)], term="1")
        assert (await _mcp(core, t["access_token"], "ping")).status == 200
        await core.pool.execute(
            "UPDATE ai_connections SET expires_at = now() - interval '1 second'")
        assert (await _mcp(core, t["access_token"], "ping")).status == 401
        assert (await _token(core, {"grant_type": "refresh_token",
                                    "refresh_token": t["refresh_token"],
                                    "client_id": t["client"]["client_id"]})).status == 400
        await ai.sweep(core.pool)
        assert await core.pool.fetchval(
            "SELECT end_reason FROM ai_connections") == "its time was up"


async def test_the_page_hears_what_the_ai_read(h):
    """While the AI reads a terminal, the person's pages hear which blocks
    (ai.read), to put its eye on them; /api/state says which AI may see the
    terminals of which machines."""
    async with h.core() as core:
        project = await _project(core)
        web = await _machine(core, project, "web-01")
        term = await _terminal(core, web)
        await _ran(core, project, web, term, 5, "uptime")
        t = await _connect(core, [(web, True, False)])
        state = (await request(core.page_port, "GET", "/api/state")).json()
        [watching] = state["ai"]["watching"]
        assert watching["name"] == "Claude" and watching["machines"] == [str(web)]
        async with page_socket(core.page_port) as ws:
            await _tool(core, t["access_token"], "read_terminal",
                        {"terminal_id": str(term)})
            async with asyncio.timeout(5):
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("op") == "ai.read":
                        break
        assert msg["terminalId"] == str(term) and msg["seqs"] == [5], msg
        assert msg["ai"]["name"] == "Claude"
        r = await request(core.page_port, "GET", f"/api/terminals/{term}/blocks")
        [block] = r.json()["blocks"]
        assert [x["name"] for x in block["aiRead"]] == ["Claude"], block
        r = await request(core.page_port, "GET", "/api/ai")
        assert r.json()["connections"][0]["lastCall"] is not None


CAN_ALL = {"see": True, "propose": True, "notes": True}


async def test_an_ai_proposes_and_the_person_runs_it(h):
    """0.1.30: an AI allowed to propose puts a command in one of the
    person's open terminals; it waits there (/api/state, ai.proposal) until
    the person runs it from its block, puts it into the line or removes it;
    the command run so says which AI proposed it, in its block, its line of
    the log and to the AI. Only one line, nothing that does not show; only
    in an open terminal it sees; only while it may."""
    login, full = _login_shell()
    if not full:
        return
    home = _home()
    settle = ai.PROPOSAL_SETTLE_SECONDS
    try:
        async with h.core() as core, \
                shell_server(await _ca_public(core), home, login) as (port, host_key):
            machine = await _live_machine(core, port, host_key)
            live = await core.terminals.open(core.owner, machine, 90, 25)
            await _until(lambda: _prompts(live) >= 1, what="the first prompt")
            tid = str(live.id)
            looks = await _connect(core, [(machine, True, True)])
            t = await _connect(core, [(machine, True, True)], can=CAN_ALL)
            token = t["access_token"]
            # The tools and the instructions are what it may do.
            r = await _mcp(core, looks["access_token"], "tools/list")
            assert "propose_command" not in str(r.json()), r.text
            r = await _mcp(core, looks["access_token"], "tools/call", {
                "name": "propose_command",
                "arguments": {"terminal_id": tid, "command": "ls"}})
            assert "Unknown tool" in r.text, r.text
            r = await _mcp(core, token, "tools/list")
            names = [x["name"] for x in r.json()["result"]["tools"]]
            assert "propose_command" in names and "write_notes" in names, names
            r = await _mcp(core, token, "initialize",
                           {"protocolVersion": "2025-06-18"})
            said = r.json()["result"]["instructions"]
            assert "propose_command" in said and "write_notes" in said, said
            # Not a line, not shown, not open.
            for bad in ("ls\nrm -rf /", "ls\x1b[2J", "echo ‮hi", "", " "):
                found, failed = await _tool(core, token, "propose_command",
                                            {"terminal_id": tid, "command": bad})
                assert failed, (bad, found)
            # Proposed: it waits, the pages hear it.
            async with page_socket(core.page_port) as ws:
                found, failed = await _tool(core, token, "propose_command", {
                    "terminal_id": tid, "command": "echo proposed-$((40+2))",
                    "why": "Shows the sum.\nOn one line."})
                assert not failed, found
                got = await _frames(ws, _op("ai.proposal"))
            heard = next(f for f in got if isinstance(f, dict)
                         and f.get("op") == "ai.proposal")
            assert heard["proposal"]["command"] == "echo proposed-$((40+2))"
            assert heard["proposal"]["why"] == "Shows the sum. On one line."
            state = (await request(core.page_port, "GET", "/api/state")).json()
            [waiting] = state["ai"]["proposals"]
            assert waiting["id"] == found["proposed"]["id"] and waiting["ai"] == "Claude"
            # A new one takes its place.
            again, failed = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo proposed-$((40+2))"})
            assert not failed and again["proposed"]["id"] != waiting["id"]
            state = (await request(core.page_port, "GET", "/api/state")).json()
            [waiting] = state["ai"]["proposals"]
            assert waiting["id"] == again["proposed"]["id"]
            async with page_socket(core.page_port) as ws:
                await ws.send(json.dumps({"op": "attach", "ch": 1, "id": tid,
                                          "after": None, "cols": 90, "rows": 25}))
                await _frames(ws, _op("replayed"))
                # Just come: not run yet, it waits on (the security review:
                # a click meant for the one before never lands on it).
                ai.PROPOSAL_SETTLE_SECONDS = 600
                await ws.send(json.dumps({"op": "run", "ch": 1,
                                          "command": waiting["command"],
                                          "proposal": waiting["id"]}))
                refused = await _refused(ws)
                assert refused["code"] == "early" and refused["over"] is False, refused
                ai.PROPOSAL_SETTLE_SECONDS = 0
                # Not the command proposed: refused, and it stays.
                await ws.send(json.dumps({"op": "run", "ch": 1, "command": "id",
                                          "proposal": waiting["id"]}))
                refused = await _refused(ws)
                assert refused["proposal"] == waiting["id"], refused
                assert live._block is None, "nothing ran"
                # The shell does not take it after all (a command came
                # first): it waits again, and the page hears which it was.
                live.run = lambda command: "A command came first."
                try:
                    await ws.send(json.dumps({"op": "run", "ch": 1,
                                              "command": waiting["command"],
                                              "proposal": waiting["id"]}))
                    refused = await _refused(ws)
                finally:
                    del live.run
                assert refused["proposal"] == waiting["id"], refused
                assert refused["over"] is False and refused["reason"], refused
                assert await core.pool.fetchval(
                    "SELECT state FROM ai_proposals WHERE id = $1",
                    uuid.UUID(waiting["id"])) == "waiting"
                # Run from its block.
                await ws.send(json.dumps({"op": "run", "ch": 1,
                                          "command": waiting["command"],
                                          "proposal": waiting["id"]}))
                got = await _frames(ws, _op("ai.proposal"))
                gone = next(f for f in got if isinstance(f, dict)
                            and f.get("op") == "ai.proposal")
                assert gone["proposal"] is None and gone["was"] == waiting["id"]
                await _until(lambda: live._block is None and not live._typed
                             and live.markers, what="back at the prompt")
            await _became_block(core, waiting["id"])
            row = await core.pool.fetchrow(
                "SELECT state, seq FROM ai_proposals WHERE id = $1",
                uuid.UUID(waiting["id"]))
            assert row["state"] == "run", row
            block = await core.pool.fetchrow(
                "SELECT command, proposed_by FROM blocks WHERE terminal_id = $1 "
                "AND seq_start = $2", live.id, row["seq"])
            assert block["proposed_by"] == "Claude", block
            line = await core.pool.fetchval(
                "SELECT details->>'proposed' FROM journal WHERE kind = 'command.ran' "
                "AND terminal_id = $1 AND (details->>'seq')::bigint = $2",
                live.id, row["seq"])
            assert line == "Claude", line
            read, _ = await _tool(core, token, "read_terminal", {"terminal_id": tid})
            [ran] = [c for c in read["commands"] if c["seq"] == row["seq"]]
            assert ran["proposedBy"] == "Claude" and "proposed-42" in ran["output"]
            assert read["proposals"][0]["state"] == "run", read["proposals"]
            assert read["proposals"][1]["state"] == "replaced", read["proposals"]
            r = await request(core.page_port, "GET", f"/api/terminals/{tid}/blocks")
            [b] = [x for x in r.json()["blocks"] if x["seqStart"] == row["seq"]]
            assert b["proposedBy"] == "Claude", b
            # Put into the line, then run as it was: it is the proposal's too.
            # Not in the moment it came.
            found, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo inserted"})
            pid = found["proposed"]["id"]
            ai.PROPOSAL_SETTLE_SECONDS = 600
            r = await request(core.page_port, "POST",
                              f"/api/ai/proposals/{pid}/insert", headers=CHANGE,
                              body=b"{}")
            assert r.status == 425, r.text
            ai.PROPOSAL_SETTLE_SECONDS = 0
            r = await request(core.page_port, "POST",
                              f"/api/ai/proposals/{pid}/insert", headers=CHANGE,
                              body=b"{}")
            assert r.status == 200 and r.json()["proposal"]["command"] == "echo inserted"
            assert live.run("echo inserted") is None
            await _became_block(core, pid)
            await _until(lambda: live._block is None and not live._typed,
                         what="back at the prompt")
            # A shell not told what is pasted gets it as typed keys: a
            # command beyond ASCII is not run from its block (it can go
            # into the line).
            found, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo café"})
            pid = found["proposed"]["id"]
            pasting = live.modes.state.get(2004)
            live.modes.state[2004] = False
            try:
                async with page_socket(core.page_port) as ws:
                    await ws.send(json.dumps({"op": "attach", "ch": 1, "id": tid,
                                              "after": None, "cols": 90,
                                              "rows": 25}))
                    await _frames(ws, _op("replayed"))
                    await ws.send(json.dumps({"op": "run", "ch": 1,
                                              "command": "echo café",
                                              "proposal": pid}))
                    refused = await _refused(ws)
            finally:
                if pasting is None:
                    live.modes.state.pop(2004, None)
                else:
                    live.modes.state[2004] = pasting
            assert refused["code"] == "typed" and refused["over"] is False, refused
            assert await core.pool.fetchval(
                "SELECT state FROM ai_proposals WHERE id = $1",
                uuid.UUID(pid)) == "waiting"
            # Removed: nothing waits; once over, it cannot be run.
            found, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo removed"})
            pid = found["proposed"]["id"]
            r = await request(core.page_port, "POST",
                              f"/api/ai/proposals/{pid}/remove", headers=CHANGE,
                              body=b"{}")
            assert r.status == 200 and r.json() == {"removed": True}, r.text
            r = await request(core.page_port, "POST",
                              f"/api/ai/proposals/{pid}/remove", headers=CHANGE,
                              body=b"{}")
            assert r.status == 410, r.text
            state = (await request(core.page_port, "GET", "/api/state")).json()
            assert state["ai"]["proposals"] == []
            # Someone else's proposal is nothing to another account.
            found, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo mine"})
            pid = found["proposed"]["id"]
            anna = await sign_in_as(core, "anna@example.com")
            r = await request(core.page_port, "POST",
                              f"/api/ai/proposals/{pid}/remove",
                              headers={**CHANGE, "cookie": anna}, body=b"{}")
            assert r.status == 410, r.text
            # It may not propose any more: what waits is over, and the tool
            # is gone.
            cid = await core.pool.fetchval(
                "SELECT connection_id FROM ai_proposals WHERE id = $1",
                uuid.UUID(pid))
            r = await request(core.page_port, "PATCH", f"/api/ai/connections/{cid}",
                              headers=CHANGE, body=json.dumps({
                                  "machines": [{"id": str(machine), "terminals": True,
                                                "log": True}],
                                  "can": {"see": True, "notes": True},
                                  "term": "keep", "zone": "UTC"}).encode())
            assert r.status == 200, r.text
            assert await core.pool.fetchval(
                "SELECT state FROM ai_proposals WHERE id = $1", uuid.UUID(pid)) == "over"
            r = await _mcp(core, token, "tools/call", {
                "name": "propose_command",
                "arguments": {"terminal_id": tid, "command": "ls"}})
            assert "Unknown tool" in r.text, r.text
            await live.close()
            # In a terminal that ended, nothing waits.
            t2 = await _connect(core, [(machine, True, True)], can=CAN_ALL)
            found, failed = await _tool(core, t2["access_token"], "propose_command",
                                        {"terminal_id": tid, "command": "ls"})
            assert failed and "ended" in str(found), found
    finally:
        ai.PROPOSAL_SETTLE_SECONDS = settle
        shutil.rmtree(home, ignore_errors=True)


async def _refused(ws) -> dict:
    """The page's next "refused"."""
    got = await _frames(ws, _op("refused"))
    return next(f for f in got if isinstance(f, dict) and f.get("op") == "refused")


async def _became_block(core, proposal_id: str, timeout: float = 15) -> int:
    """The block a proposal became, once its block is saved."""
    async with asyncio.timeout(timeout):
        while True:
            seq = await core.pool.fetchval(
                "SELECT seq FROM ai_proposals WHERE id = $1", uuid.UUID(proposal_id))
            if seq is not None:
                return seq
            await asyncio.sleep(0.05)


async def test_an_ai_writes_notes_under_the_lines(h):
    """0.1.30: an AI allowed to write notes puts them under the lines it
    sees, many at once, each on its own: under any line of a log it sees,
    under the person's own commands where it sees only their terminals;
    signed as the AI on the person's behalf, with where it lives and in
    which language, cut of secrets; the pages hear them."""
    async with h.core() as core:
        project = await _project(core)
        web = await _machine(core, project, "web-01")
        db = await _machine(core, project, "db-01")
        hidden = await _machine(core, project, "hidden-01")
        anna = (await sign_in_as(core, "anna@example.com"), await core.pool.fetchval(
            "SELECT id FROM users WHERE email = 'anna@example.com'"))
        mine = await _terminal(core, web)
        await _ran(core, project, web, mine, 3, "npm ci", b"ENOSPC\n", exit_code=1)
        hers = await _terminal(core, web, owner=anna[1])
        await _ran(core, project, web, hers, 4, "df -h", user=anna[1])
        on_db = await _terminal(core, db, owner=anna[1])
        await _ran(core, project, db, on_db, 2, "psql -l", user=anna[1])
        off = await _terminal(core, hidden)
        await _ran(core, project, hidden, off, 1, "uptime")
        await core.pool.execute("UPDATE users SET language = NULL WHERE id = $1",
                                core.owner.id)
        # web-01: its terminals only; db-01: its log.
        looks = await _connect(core, [(web, True, False), (db, False, True)])
        r = await _mcp(core, looks["access_token"], "tools/call", {
            "name": "write_notes",
            "arguments": {"notes": [{"terminal_id": str(mine), "seq": 3,
                                     "text": "x"}]}})
        assert "Unknown tool" in r.text, r.text
        client, verifier, request_id = await _asked(core)
        body = {"machines": [{"id": str(web), "terminals": True, "log": False},
                             {"id": str(db), "terminals": False, "log": True}],
                "can": {"see": True, "notes": True}, "term": "forever",
                "zone": "Europe/Lisbon", "language": "de"}
        r = await request(core.page_port, "POST",
                          f"/api/ai/requests/{request_id}/allow", headers=CHANGE,
                          body=json.dumps(body).encode())
        assert r.status == 200, r.text
        back = _query(r.json()["redirect"])
        r = await _token(core, {"grant_type": "authorization_code",
                                "code": back["code"], "redirect_uri": CLAUDE,
                                "client_id": client["client_id"],
                                "code_verifier": verifier})
        token = r.json()["access_token"]
        r = await _mcp(core, token, "initialize", {"protocolVersion": "2025-06-18"})
        assert "write in German" in r.json()["result"]["instructions"], r.text
        secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        async with page_socket(core.page_port) as ws:
            found, failed = await _tool(core, token, "write_notes", {"notes": [
                {"terminal_id": str(mine), "seq": 3,
                 "text": f"Kein Platz auf der Platte. Token {secret}"},
                {"terminal_id": str(hers), "seq": 4, "text": "Not mine."},
                {"terminal_id": str(on_db), "seq": 2, "text": "Listet die Datenbanken."},
                {"terminal_id": str(off), "seq": 1, "text": "Not allowed."},
                {"terminal_id": str(mine), "seq": 99, "text": "No such line."},
                {"terminal_id": str(mine), "seq": 3, "text": "x" * 2001},
                {"terminal_id": "nope", "seq": 3, "text": "Bad id."},
                "not a note"]})
            assert not failed, found
            got = await _frames(ws, _op("ai.notes"))
        assert found["written"] == 2, found
        errors = [n for n in found["notes"] if "error" in n]
        assert len(errors) == 6, found
        heard = [f for f in got if isinstance(f, dict) and f.get("op") == "ai.notes"]
        assert {n["seq"] for f in heard for n in f["notes"]} == {3}, heard
        rows = await core.pool.fetch(
            "SELECT j.author, j.ai, j.user_id, j.details, x.text FROM journal j "
            "JOIN log_texts x ON x.id = j.text_id WHERE j.kind = 'log.note' "
            "ORDER BY j.id")
        assert [r["author"] for r in rows] == ["ai", "ai"]
        assert all(r["ai"] == "Claude" and r["user_id"] == core.owner.id for r in rows)
        details = json.loads(rows[0]["details"])
        assert details["lang"] == "de" and details["from"] == "claude.ai", details
        assert secret not in rows[0]["text"] and "Kein Platz" in rows[0]["text"]
        # The log shows it signed, with where the AI lives.
        r = await request(core.page_port, "GET",
                          f"/api/terminals/{mine}/blocks")
        [b] = r.json()["blocks"]
        [note] = b["notes"]
        assert note["author"] == "ai" and note["ai"] == "Claude"
        assert note["from"] == "claude.ai", note
        # Too many at once.
        found, failed = await _tool(core, token, "write_notes", {"notes": [
            {"terminal_id": str(mine), "seq": 3, "text": "n"}] * 201})
        assert failed and "200" in str(found), found


async def _state_of(core, proposal_id: str) -> str:
    return await core.pool.fetchval(
        "SELECT state FROM ai_proposals WHERE id = $1", uuid.UUID(proposal_id))


async def test_a_proposal_ends_where_its_ai_may_not_propose(h):
    """The security review of 0.1.30: what an AI proposed is over once it
    may not see the terminals of that machine (unticked in Change, the
    project left, the machine removed): not listed, not put into the line;
    one proposed as its right goes is refused. Not run or put in the moment
    it came. A run the shell did not take waits again, or, if another came
    meanwhile, was replaced. Why, cut longer than it came, is kept."""
    settle = ai.PROPOSAL_SETTLE_SECONDS
    ai.PROPOSAL_SETTLE_SECONDS = 0
    try:
        async with h.core() as core:
            project = await _project(core)
            web = await _machine(core, project, "web-01")
            db = await _machine(core, project, "db-01")
            on_web = await _terminal(core, web, ended=False)
            on_db = await _terminal(core, db, ended=False)
            t = await _connect(core, [(web, True, False), (db, True, False)],
                               can=CAN_ALL)
            c = await ai.connection_of(core.pool, t["access_token"])
            # Unticked in Change.
            p = await ai.propose(core.pool, c, on_web, "uptime", "The load.")
            r = await request(core.page_port, "PATCH", f"/api/ai/connections/{c.id}",
                              headers=CHANGE, body=json.dumps({
                                  "machines": [{"id": str(db), "terminals": True,
                                                "log": False}],
                                  "can": CAN_ALL, "term": "keep",
                                  "zone": "UTC"}).encode())
            assert r.status == 200, r.text
            assert await _state_of(core, p["id"]) == "over"
            assert await ai.waiting(core.pool, core.owner.id) == []
            r = await request(core.page_port, "POST",
                              f"/api/ai/proposals/{p['id']}/insert",
                              headers=CHANGE, body=b"{}")
            assert r.status == 410, r.text
            try:
                await ai.propose(core.pool, c, on_web, "uptime", None)
            except ai.Refused as exc:
                assert exc.status == 403, exc
            else:
                raise AssertionError("proposed where it does not see")
            # Its grant gone some other way: not listed, over when put in.
            p = await ai.propose(core.pool, c, on_db, "df -h", None)
            await core.pool.execute(
                "UPDATE ai_grants SET terminals = false, log = true "
                "WHERE connection_id = $1", c.id)
            assert await ai.waiting(core.pool, core.owner.id) == []
            try:
                await ai.decide(core.pool, core.owner.id, uuid.UUID(p["id"]),
                                "inserted")
            except ai.Refused as exc:
                assert exc.status == 410, exc
            else:
                raise AssertionError("put in where it does not see")
            assert await _state_of(core, p["id"]) == "over"
            # The project left.
            await core.pool.execute(
                "UPDATE ai_grants SET terminals = true, log = false "
                "WHERE connection_id = $1", c.id)
            p = await ai.propose(core.pool, c, on_db, "df -h", None)
            assert await ai.leave_project(core.pool, core.owner.id, c.id, project)
            assert await _state_of(core, p["id"]) == "over"
            # The machine removed: the sweep ends it.
            t = await _connect(core, [(web, True, False), (db, True, False)],
                               can=CAN_ALL)
            c = await ai.connection_of(core.pool, t["access_token"])
            p = await ai.propose(core.pool, c, on_db, "df -h", None)
            await core.pool.execute(
                "UPDATE machines SET removed_at = now() WHERE id = $1", db)
            await ai.sweep(core.pool)
            assert await _state_of(core, p["id"]) == "over"
            # Not in the moment it came.
            p = await ai.propose(core.pool, c, on_web, "uptime", None)
            ai.PROPOSAL_SETTLE_SECONDS = 600
            try:
                await ai.decide(core.pool, core.owner.id, uuid.UUID(p["id"]),
                                "inserted")
            except ai.Refused as exc:
                assert exc.status == 425, exc
            else:
                raise AssertionError("put in the moment it came")
            assert await _state_of(core, p["id"]) == "waiting"
            ai.PROPOSAL_SETTLE_SECONDS = 0
            await ai.decide(core.pool, core.owner.id, uuid.UUID(p["id"]), "inserted")
            # A run the shell did not take: it waits again, or was replaced.
            one = await ai.propose(core.pool, c, on_web, "echo one", None)
            await ai.decide(core.pool, core.owner.id, uuid.UUID(one["id"]), "run")
            assert await ai.undecide(core.pool, uuid.UUID(one["id"])) is False
            assert await _state_of(core, one["id"]) == "waiting"
            await ai.decide(core.pool, core.owner.id, uuid.UUID(one["id"]), "run")
            two = await ai.propose(core.pool, c, on_web, "echo two", None)
            assert await ai.undecide(core.pool, uuid.UUID(one["id"])) is True
            assert await _state_of(core, one["id"]) == "replaced"
            assert await _state_of(core, two["id"]) == "waiting"
            # Why: its length checked as it came, then cut, longer, kept.
            p = await ai.propose(core.pool, c, on_web, "uptime", "token=abc " * 30)
            assert leaks.CUT in p["why"] and "abc" not in p["why"], p["why"]
            assert len(p["why"]) <= ai.WHY_KEPT_MAX
            # It may not propose any more, as it proposes: refused.
            await core.pool.execute(
                "UPDATE ai_connections SET can_propose = false WHERE id = $1", c.id)
            try:
                await ai.propose(core.pool, c, on_web, "uptime", None)
            except ai.Refused as exc:
                assert exc.status == 403, exc
            else:
                raise AssertionError("proposed with the right gone")
    finally:
        ai.PROPOSAL_SETTLE_SECONDS = settle


def test_what_an_ai_writes_shows_as_it_is():
    """The security review of 0.1.30: a command an AI proposes has nothing
    that does not show as itself (a space the shell does not take for one,
    a mark that draws as nothing); its why and its notes show as they are,
    and a secret split by something unseen is whole for the cut."""
    for bad in ("ls\u00a0-la", "ls -la\ufe0f", "ls\u200b -la", "ls \u3164",
                "ls \u2800", "ls\u2028-la", "ls\u00ad", "echo \U000e0041",
                "ls\u034f -la", "ls\u3000-la"):
        try:
            ai.clean_command(bad)
        except ai.Refused:
            continue
        raise AssertionError(f"{bad!r} went through")
    for good in ("ls -la", "echo привет", "git commit -m 'café'", "echo 日本語"):
        assert ai.clean_command(good) == good
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    for unseen in ("\x01", "\u200b", "\ufe0f", "\u2060", "\u00ad", "\u034f",
                   "\u202e"):
        split = secret[:6] + unseen + secret[6:]
        note = ai.clean_ai_note(f"Token {split} here.")
        assert secret not in note and leaks.CUT in note, (unseen, note)
        why = ai.clean_why(f"Token {split}.")
        assert secret not in why and leaks.CUT in why, (unseen, why)
    assert ai.as_shown("a\u00a0b\u2028c\u200bd") == "a b\ncd"
    assert ai.clean_ai_note("Line one\nline two") == "Line one\nline two"
    for bad in ("", " ", "\u200b", "x" * (ai.AI_NOTE_MAX + 1)):
        try:
            ai.clean_ai_note(bad)
        except ai.Refused:
            continue
        raise AssertionError(f"{bad[:20]!r} went through")


async def test_notes_keep_their_hour_and_their_rights(h):
    """The security review of 0.1.30: the notes an hour hold for requests
    at once and after a restart (counted from the log); a request writing
    many asks again what the AI may do, and stops when the person turns
    notes off; a request with as many notes as the tool takes fits."""
    hour, at_once, recheck = ai.NOTES_PER_HOUR, ai.NOTES_AT_ONCE, mcp.RECHECK_SECONDS
    add_note = logs.add_note
    try:
        async with h.core() as core:
            project = await _project(core)
            web = await _machine(core, project, "web-01")
            mine = await _terminal(core, web)
            await _ran(core, project, web, mine, 3, "npm ci")
            t = await _connect(core, [(web, True, False)],
                               can={"see": True, "notes": True})
            token = t["access_token"]
            c = await ai.connection_of(core.pool, token)
            ai.NOTES_PER_HOUR, ai.NOTES_AT_ONCE = 30, 20
            batch = {"notes": [{"terminal_id": str(mine), "seq": 3, "text": f"n{i}"}
                               for i in range(20)]}
            got = await asyncio.gather(*(_tool(core, token, "write_notes", batch)
                                         for _ in range(3)))
            assert sum(found["written"] for found, failed in got
                       if not failed) == 30, got
            assert await core.pool.fetchval(
                "SELECT count(*) FROM journal WHERE kind = 'log.note'") == 30
            # After a restart, the hour is counted again from the log.
            fresh = mcp.Mcp(core.pool, None, page_url="http://localhost",
                            keys_open_on=lambda *_: False)
            assert await fresh._notes_left(c.id) == 0
            ai.NOTES_PER_HOUR, ai.NOTES_AT_ONCE = hour, at_once
            assert await fresh._notes_left(c.id) == hour - 30
            # Turned off while it writes: the rest is refused.
            mcp.RECHECK_SECONDS = 0

            async def turned_off(*args, **kw):
                found = await add_note(*args, **kw)
                await core.pool.execute(
                    "UPDATE ai_connections SET can_note = false WHERE id = $1", c.id)
                return found

            logs.add_note = turned_off
            found, failed = await _tool(core, token, "write_notes", {"notes": [
                {"terminal_id": str(mine), "seq": 3, "text": f"m{i}"}
                for i in range(3)]})
            logs.add_note = add_note
            assert not failed and found["written"] == 1, found
            assert all("turned notes off" in n["error"] for n in found["notes"][1:]), found
            # As many as the tool takes, in one request.
            await core.pool.execute(
                "UPDATE ai_connections SET can_note = true WHERE id = $1", c.id)
            mcp.RECHECK_SECONDS = recheck
            found, failed = await _tool(core, token, "write_notes", {"notes": [
                {"terminal_id": str(mine), "seq": 3, "text": "x" * ai.AI_NOTE_MAX}]
                * 150})
            assert not failed and found["written"] == 150, str(found)[:300]
    finally:
        ai.NOTES_PER_HOUR, ai.NOTES_AT_ONCE = hour, at_once
        mcp.RECHECK_SECONDS = recheck
        logs.add_note = add_note


async def test_proposals_under_the_keys_answer_only_where_they_are_open(h):
    """The security review of 0.1.30: from a device whose keys are closed,
    a proposal on a machine under them is neither put into the line nor
    removed: the answer would say whether it waits there."""
    settle = ai.PROPOSAL_SETTLE_SECONDS
    ai.PROPOSAL_SETTLE_SECONDS = 0
    try:
        async with h.core() as core:
            project = await _project(core)
            mac = await _machine(core, project, "MacBook-Pro", kind="computer")
            term = await _terminal(core, mac, ended=False)
            t = await _connect(core, [(mac, True, True)], can=CAN_ALL)
            c = await ai.connection_of(core.pool, t["access_token"])
            p = await ai.propose(core.pool, c, term, "uptime", None)
            await _made_vault(core)
            for what in ("insert", "remove"):
                r = await request(core.page_port, "POST",
                                  f"/api/ai/proposals/{p['id']}/{what}",
                                  headers=CHANGE, body=b"{}")
                assert r.status == 410, (what, r.text)
            assert await _state_of(core, p["id"]) == "waiting"
            async with _proven(core, auth=_pw()["auth"]):
                r = await request(core.page_port, "POST",
                                  f"/api/ai/proposals/{p['id']}/remove",
                                  headers=CHANGE, body=b"{}")
                assert r.status == 200, r.text
            assert await _state_of(core, p["id"]) == "removed"
    finally:
        ai.PROPOSAL_SETTLE_SECONDS = settle


async def test_the_sweep_takes_what_apps_left(h):
    async with h.core() as core:
        web = await _machine(core, await _project(core), "web-01")
        await _register(core)
        client, verifier, request_id = await _asked(core)
        await _allow(core, request_id, [(web, True, True)])
        await core.pool.execute(
            "UPDATE ai_clients SET created_at = now() - interval '2 days'")
        await core.pool.execute(
            "UPDATE ai_requests SET created_at = now() - interval '2 days'")
        await core.pool.execute(
            "UPDATE ai_codes SET created_at = now() - interval '2 days'")
        await core.pool.execute(
            "UPDATE ai_connections SET created_at = now() - interval '2 days'")
        await ai.sweep(core.pool)
        for table in ("ai_requests", "ai_codes", "ai_connections", "ai_clients"):
            assert await core.pool.fetchval(f"SELECT count(*) FROM {table}") == 0, table


async def test_an_ai_waits_for_the_command_to_end(h):
    """0.1.31: wait_for_command waits, a little at a time, for a command in
    a terminal the AI sees to end: its proposal, once the person runs it;
    the one running; the one it names. Then what it printed, at once, so
    that a note can go under it; a proposal removed meanwhile leaves
    nothing to wait for, at once; what the AI may see is asked again after
    the wait; a few waits of one connection at once."""
    login, full = _login_shell()
    if not full:
        return
    home = _home()
    settle = ai.PROPOSAL_SETTLE_SECONDS
    try:
        async with h.core() as core, \
                shell_server(await _ca_public(core), home, login) as (port, host_key):
            machine = await _live_machine(core, port, host_key)
            live = await core.terminals.open(core.owner, machine, 90, 25)
            await _until(lambda: _prompts(live) >= 1, what="the first prompt")
            tid = str(live.id)
            looks = (await _connect(core, [(machine, True, True)]))["access_token"]
            token = (await _connect(core, [(machine, True, True)], can=CAN_ALL))["access_token"]
            # Looking is enough to wait.
            r = await _mcp(core, looks, "tools/list")
            assert "wait_for_command" in [x["name"] for x in r.json()["result"]["tools"]]
            found, failed = await _tool(core, looks, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 1})
            assert not failed and found["state"] == "nothing", found
            assert "proposals" not in found, "it may not propose"
            for bad in ({"seq": 10 ** 9}, {"seconds": 26}, {"seconds": 0}):
                found, failed = await _tool(core, looks, "wait_for_command",
                                            {"terminal_id": tid, **bad})
                assert failed, (bad, found)
            # Its proposal, once the person runs it: what it printed.
            ai.PROPOSAL_SETTLE_SECONDS = 0
            proposed, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo waited-$((40+2))"})
            pid = proposed["proposed"]["id"]

            async def run_soon():
                await asyncio.sleep(0.5)
                async with page_socket(core.page_port) as ws:
                    await ws.send(json.dumps({"op": "attach", "ch": 1, "id": tid,
                                              "after": None, "cols": 90, "rows": 25}))
                    await _frames(ws, _op("replayed"))
                    await ws.send(json.dumps({"op": "run", "ch": 1,
                                              "command": "echo waited-$((40+2))",
                                              "proposal": pid}))
                    await _frames(ws, _op("ai.proposal"))

            runner = asyncio.create_task(run_soon())
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 8})
            await runner
            assert not failed and found["state"] == "ended", found
            ran = found["command"]
            assert ran["command"] == "echo waited-$((40+2))", ran
            assert "waited-42" in ran["output"] and ran["exitCode"] == 0, ran
            assert ran["proposedBy"] == "Claude", ran
            assert found["proposals"][0]["state"] == "run", found
            assert "write_notes" in found["next"], found
            assert await core.pool.fetchval(
                "SELECT count(*) FROM ai_reads WHERE terminal_id = $1 "
                "AND seq_start = $2", live.id, ran["seq"]) == 1, "the eye"
            # Run before the AI began to wait (it said something in
            # between): the wait gives that command at once, once; then the
            # next one.
            proposed, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo quick"})
            async with page_socket(core.page_port) as ws:
                await ws.send(json.dumps({"op": "attach", "ch": 1, "id": tid,
                                          "after": None, "cols": 90, "rows": 25}))
                await _frames(ws, _op("replayed"))
                await ws.send(json.dumps({"op": "run", "ch": 1,
                                          "command": "echo quick",
                                          "proposal": proposed["proposed"]["id"]}))
                await _frames(ws, _op("ai.proposal"))
            await _until(lambda: live._block is None and not live._typed
                         and live.markers, what="back at the prompt")
            began = time.monotonic()
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 8})
            assert not failed and found["state"] == "ended", found
            assert found["command"]["command"] == "echo quick", found
            assert "not your proposal" not in found["next"], found
            assert time.monotonic() - began < 4, "it did not wait for it"
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 1})
            assert not failed and found["state"] == "nothing", found
            # Put into the line and run as it is: its own, and run; then
            # nothing waits. Put in and changed: nothing of it comes.
            for runs, ends in (("echo inserted", "ended"), ("echo changed", "nothing")):
                proposed, _ = await _tool(core, token, "propose_command", {
                    "terminal_id": tid, "command": "echo inserted"})
                pid = proposed["proposed"]["id"]
                r = await request(core.page_port, "POST",
                                  f"/api/ai/proposals/{pid}/insert",
                                  headers=CHANGE, body=b"{}")
                assert r.status == 200, r.text
                assert live.run(runs) is None
                await _until(lambda: live._block is None and not live._typed
                             and live.markers, what="back at the prompt")
                found, failed = await _tool(core, token, "wait_for_command",
                                            {"terminal_id": tid, "seconds": 2})
                assert not failed and found["state"] == ends, (runs, found)
                if ends == "ended":
                    assert found["command"]["command"] == "echo inserted", found
                    assert "not your proposal" not in found["next"], found
                    assert found["proposals"][0]["state"] == "run", found
                    found, failed = await _tool(core, token, "wait_for_command",
                                                {"terminal_id": tid, "seconds": 1})
                    assert not failed and found["state"] == "nothing", found
            # The note goes under it at once; named by its seq, the command
            # comes back at once, with it.
            wrote, failed = await _tool(core, token, "write_notes", {"notes": [
                {"terminal_id": tid, "seq": ran["seq"], "text": "Напечатала 42."}]})
            assert not failed and wrote["written"] == 1, wrote
            began = time.monotonic()
            found, failed = await _tool(core, looks, "wait_for_command",
                                        {"terminal_id": tid, "seq": ran["seq"]})
            assert not failed and found["state"] == "ended", found
            assert found["command"]["notes"][0]["text"] == "Напечатала 42.", found
            assert time.monotonic() - began < 3, "no wait for what ended"
            # Removed meanwhile: nothing to wait for, at once.
            proposed, _ = await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo never"})
            pid = proposed["proposed"]["id"]

            async def remove_soon():
                await asyncio.sleep(0.5)
                return await request(core.page_port, "POST",
                                     f"/api/ai/proposals/{pid}/remove",
                                     headers=CHANGE, body=b"{}")

            remover = asyncio.create_task(remove_soon())
            began = time.monotonic()
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 8})
            assert (await remover).status == 200
            assert not failed and found["state"] == "nothing", found
            assert "removed" in found["next"], found
            assert time.monotonic() - began < 4, "it woke at once"
            # Not run yet when the time is up: it says the proposal waits.
            await _tool(core, token, "propose_command", {
                "terminal_id": tid, "command": "echo later"})
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 1})
            assert not failed and found["state"] == "waiting", found
            # The person's own command comes first: that one ends, and the
            # proposal waits on.

            async def mine_soon():
                await asyncio.sleep(0.5)
                assert live.run("echo mine-first") is None

            runner = asyncio.create_task(mine_soon())
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 8})
            await runner
            assert not failed and found["state"] == "ended", found
            assert found["command"]["command"] == "echo mine-first", found
            assert "not your proposal" in found["next"], found
            await _until(lambda: live._block is None and not live._typed,
                         what="back at the prompt")
            # Running on: what it printed so far; then, by its seq, its end.
            assert live.run("sh -c 'echo first-part; sleep 3; echo end-of-it'") is None
            await _until(lambda: live._block is not None, what="it runs")
            found, failed = await _tool(core, looks, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 1})
            assert not failed and found["state"] == "running", found
            assert "first-part" in found["command"]["output"], found
            seq = found["command"]["seq"]
            assert "seq " + str(seq) in found["next"], found
            found, failed = await _tool(core, looks, "wait_for_command",
                                        {"terminal_id": tid, "seq": seq, "seconds": 8})
            assert not failed and found["state"] == "ended", found
            assert "end-of-it" in found["command"]["output"], found
            # Three waits of one connection at once, not four.
            got = await asyncio.gather(*(
                _tool(core, looks, "wait_for_command",
                      {"terminal_id": tid, "seconds": 2}) for _ in range(4)))
            refused = [x for x, failed in got if failed]
            assert len(refused) == 1 and "already" in refused[0], got
            # Its terminals taken away while it waits: it gets nothing.
            cid = (await request(core.page_port, "GET", "/api/ai")).json()[
                "connections"][0]["id"]
            waiter = asyncio.create_task(_tool(core, looks, "wait_for_command",
                                               {"terminal_id": tid, "seconds": 3}))
            await asyncio.sleep(0.5)
            r = await request(core.page_port, "PATCH", f"/api/ai/connections/{cid}",
                              headers=CHANGE, body=json.dumps({
                                  "machines": [{"id": str(machine), "terminals": False,
                                                "log": True}],
                                  "can": {"see": True}, "term": "keep",
                                  "zone": "UTC"}).encode())
            assert r.status == 200, r.text
            found, failed = await waiter
            assert failed and "No such terminal" in found, found
            # The terminal ends while it waits.
            waiter = asyncio.create_task(_tool(core, token, "wait_for_command",
                                               {"terminal_id": tid, "seconds": 8}))
            await asyncio.sleep(0.5)
            await live.close()
            found, failed = await waiter
            assert not failed and found["state"] == "terminal ended", found
    finally:
        ai.PROPOSAL_SETTLE_SECONDS = settle
        shutil.rmtree(home, ignore_errors=True)


async def test_a_command_still_being_saved_is_read_in_the_next_wait(h):
    """0.1.31, the security review: a command that ended while tTerm still
    saves it is said to be so, with its seq, and is not marked read; the
    next wait with that seq reads it, once it is saved."""
    login, full = _login_shell()
    if not full:
        return
    home = _home()
    try:
        async with h.core() as core, \
                shell_server(await _ca_public(core), home, login) as (port, host_key):
            machine = await _live_machine(core, port, host_key)
            live = await core.terminals.open(core.owner, machine, 90, 25)
            await _until(lambda: _prompts(live) >= 1, what="the first prompt")
            tid = str(live.id)
            token = (await _connect(core, [(machine, True, True)]))["access_token"]
            real = live._save_block

            async def slow(*args, **kw):
                await asyncio.sleep(4)          # a busy database
                return await real(*args, **kw)

            live._save_block = slow

            async def run_soon():
                await asyncio.sleep(0.3)
                assert live.run("echo slow-$((3+4))") is None

            runner = asyncio.create_task(run_soon())
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 2})
            await runner
            assert not failed and found["state"] == "ended", found
            assert found["command"]["saved"] is False, found
            assert "output" not in found["command"], found
            seq = found["command"]["seq"]
            assert f"seq {seq}" in found["next"], found
            assert await core.pool.fetchval(
                "SELECT count(*) FROM ai_reads WHERE terminal_id = $1", live.id) == 0
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seq": seq, "seconds": 8})
            assert not failed and found["state"] == "ended", found
            assert "slow-7" in found["command"]["output"], found
            await live.close()
    finally:
        shutil.rmtree(home, ignore_errors=True)


async def test_a_wait_knows_the_ai_proposal_whatever_came_between(h):
    """0.1.31, the second look of the security review: the AI's proposal is
    its own by the proposal, not by its name or its state. Read while it
    ran, its end is still given; a new proposal meanwhile does not make the
    one ending someone else's; a Run the shell never got leaves nothing to
    wait for; a command tTerm could not keep is said so, once."""
    login, full = _login_shell()
    if not full:
        return
    home = _home()
    settle = ai.PROPOSAL_SETTLE_SECONDS
    try:
        async with h.core() as core, \
                shell_server(await _ca_public(core), home, login) as (port, host_key):
            machine = await _live_machine(core, port, host_key)
            live = await core.terminals.open(core.owner, machine, 90, 25)
            await _until(lambda: _prompts(live) >= 1, what="the first prompt")
            tid = str(live.id)
            token = (await _connect(core, [(machine, True, True)], can=CAN_ALL))["access_token"]
            ai.PROPOSAL_SETTLE_SECONDS = 0

            async def propose(command):
                found, failed = await _tool(core, token, "propose_command",
                                            {"terminal_id": tid, "command": command})
                assert not failed, found
                return found["proposed"]["id"]

            async def run_it(pid, command):
                async with page_socket(core.page_port) as ws:
                    await ws.send(json.dumps({"op": "attach", "ch": 1, "id": tid,
                                              "after": None, "cols": 90, "rows": 25}))
                    await _frames(ws, _op("replayed"))
                    await ws.send(json.dumps({"op": "run", "ch": 1, "command": command,
                                              "proposal": pid}))
                    await _frames(ws, _op("ai.proposal"))

            async def idle():
                await _until(lambda: live._block is None and not live._typed
                             and live.markers, what="back at the prompt")

            # Read while it ran: its end is given all the same, its own.
            command = "sh -c 'echo p-start; sleep 2; echo p-end; exit 3'"
            pid = await propose(command)
            await run_it(pid, command)
            await _until(lambda: live._block is not None, what="it runs")
            seen, _ = await _tool(core, token, "read_terminal", {"terminal_id": tid})
            assert seen["running"]["command"] == command, seen
            ran = await _became_block(core, pid)
            await idle()
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 3})
            assert not failed and found["state"] == "ended", found
            assert found["command"]["seq"] == ran and found["command"]["exitCode"] == 3
            assert found["command"]["yours"] is True, found
            # A new proposal while it waits for the one running: that one
            # is still its own.
            command = "sh -c 'sleep 1.5; echo A-done'"
            pid = await propose(command)
            await run_it(pid, command)
            await _until(lambda: live._block is not None, what="it runs")
            waiter = asyncio.create_task(_tool(core, token, "wait_for_command",
                                               {"terminal_id": tid, "seconds": 8}))
            await asyncio.sleep(0.3)
            later = await propose("echo B")
            found, failed = await waiter
            assert not failed and found["command"]["command"] == command, found
            assert found["command"]["yours"] is True, found
            assert "not your proposal" not in found["next"], found
            # The person's own command first: not its own, and it says
            # its proposal waits.
            await idle()
            assert live.run("echo person-own") is None
            await idle()
            found, failed = await _tool(core, token, "wait_for_command", {
                "terminal_id": tid, "seq": live.commands_since(0)[-1]["seqStart"]})
            assert found["command"]["yours"] is False, found
            assert "which still waits" in found["next"], found
            r = await request(core.page_port, "POST", f"/api/ai/proposals/{later}/remove",
                              headers=CHANGE, body=b"{}")
            assert r.status == 200, r.text
            # Run pressed, and the shell never got it: nothing to wait for,
            # soon.
            command = "echo never-started"
            pid = await propose(command)
            await ai.decide(core.pool, core.owner.id, uuid.UUID(pid), "run",
                            terminal_id=live.id, command=command)
            live.expect_proposal(uuid.UUID(pid), "Claude", command, 0.1)
            began = time.monotonic()
            found, failed = await _tool(core, token, "wait_for_command",
                                        {"terminal_id": tid, "seconds": 5})
            assert not failed and found["state"] == "nothing", found
            assert "never got it" in found["next"], found
            assert time.monotonic() - began < 3, "it did not wait the time out"
            # A command tTerm could not keep: said so, at once, with or
            # without its seq; not "saved: false" over and over.
            real = live._save_block

            async def failed_save(*args, **kw):
                return None                     # as after a database error

            live._save_block = failed_save
            try:
                command = "echo lost-$((5+5))"
                pid = await propose(command)
                await run_it(pid, command)
                await idle()
                lost = live.commands_since(0)[-1]["seqStart"]
                await _until(lambda: not live.saving(lost), what="the save gave up")
                found, failed = await _tool(core, token, "wait_for_command",
                                            {"terminal_id": tid, "seq": lost})
                assert failed and "could not keep" in found, found
                found, failed = await _tool(core, token, "wait_for_command",
                                            {"terminal_id": tid, "seconds": 1})
                assert not failed and found["state"] == "nothing", found
            finally:
                live._save_block = real
            await live.close()
    finally:
        ai.PROPOSAL_SETTLE_SECONDS = settle
        shutil.rmtree(home, ignore_errors=True)
