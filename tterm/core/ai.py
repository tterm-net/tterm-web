"""AI through MCP (0.1.29; PROJECT.md, «ИИ через MCP»).

Claude, ChatGPT, Claude Code, Cursor or any AI app with MCP connects to
tTerm as the person allows it, and asks tTerm, at /mcp, about the machines
it was allowed: what ran in the person's terminals there and what they
printed, what the machines' logs say, and it searches them. It types
nothing, runs nothing, opens no terminal, reaches no machine and gets no
key (api/ai.py serves it; what it is told is cut of secrets, leaks.py).

How it connects, OAuth 2.1 as MCP has it:

* the app registers itself (RFC 7591, `register`): a client of its own,
  with where it takes the person back to;
* it opens /oauth/authorize in the person's browser (`authorize`): the
  request waits there, ten minutes, for the person on /connect-ai;
* the person, signed in, sees who asks and where it goes back to, ticks the
  machines (their terminals, their log), what it may do and how long, and
  allows it (`allow`): a code goes back to the app, once, for five minutes,
  with the secret only the app has (PKCE, S256);
* the app trades the code for its tokens (`exchange`): to ask for an hour,
  and to get the next ones as long as the connection lasts, each of those
  only once (`refresh`; one used again past a minute ends the connection,
  as a stolen one would be).

What it sees is what the connection allows and the person may, then and
there (`seen`): the machines ticked, while each is in the project it was
allowed in and the person is too; the person's own terminals there, the
whole log as the person reads it. A machine under the person's keys (one
with their key, one through their Mac, a computer: vault.guarded) it sees
only while the keys are open on the device it was allowed from: allowing
one takes the keys open there, or the vault's password on /connect-ai.

Every account may connect its AI: the AI is the person's own, on their
plan with it, and costs tTerm nothing.
"""
from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hmac
import re
import secrets
import uuid
import zoneinfo
from types import SimpleNamespace
from typing import Callable, Iterable, Mapping
from urllib.parse import urlencode, urlsplit

import asyncpg

from . import accounts, auth, vault

#: An access token works this long; the app gets the next with its refresh
#: token, before or after.
ACCESS_SECONDS = 3600
#: The code /connect-ai gives the app: this long, once.
CODE_MINUTES = 5
#: A request waits this long for the person on /connect-ai.
REQUEST_MINUTES = 10
#: A refresh token used again this soon after it made the next gets that
#: next again: the answer was lost, or the app asked twice at once. Later,
#: someone else has it, and the connection ends.
REFRESH_GRACE = dt.timedelta(seconds=60)
#: How long an AI may stay, as /connect-ai offers it: until the person takes
#: it back, or days.
TERMS = {"forever": None, "30": 30, "7": 7, "1": 1}
#: What an AI may do in this release: see. Proposing commands and writing
#: notes in the log come with 0.1.30, and only then can they be allowed.
ABILITIES = ("see",)
#: Registrations an hour from one network, and from everywhere.
CLIENTS_PER_NET_HOUR = 60
CLIENTS_PER_HOUR = 2000
#: Requests to connect an hour from one network.
REQUESTS_PER_NET_HOUR = 120
#: What an app says of itself, at most.
NAME_MAX = 100
REDIRECTS_MAX = 10
URI_MAX = 2000
STATE_MAX = 2000
SCOPE_MAX = 1000
#: Machines one connection may be allowed, at most.
MACHINES_MAX = 500

ACCESS_PREFIX = "tterm_at_"
REFRESH_PREFIX = "tterm_rt_"
CODE_PREFIX = "tterm_ac_"
SECRET_PREFIX = "tterm_cs_"
_TOKEN_TAIL = re.compile(r"[A-Za-z0-9_-]{32,64}")

#: Where an app takes the person back to tells which it is, for those whose
#: place is known; what an app calls itself is only its own words.
KNOWN = {"claude.ai": "Claude", "claude.com": "Claude",
         "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT"}
#: A name that says it is one of these goes back to one of their places, or
#: /connect-ai warns that it does not.
BRANDS = {"claude": ("claude.ai", "claude.com"),
          "anthropic": ("claude.ai", "claude.com"),
          "chatgpt": ("chatgpt.com", "chat.openai.com"),
          "openai": ("chatgpt.com", "chat.openai.com")}
LOOPBACK = ("127.0.0.1", "localhost", "::1")
#: Schemes no app takes a person back with.
BAD_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob",
               "filesystem", "ws", "wss", "ftp", "view-source", "jar",
               "intent", "chrome", "chrome-extension", "moz-extension",
               "ms-browser-extension", "resource", "http", "https"}
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]{0,63}")
_ZONE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")


class Refused(Exception):
    """Not done, for the page: the message says why, `status` is the HTTP
    answer, `details` go along in it."""

    def __init__(self, message: str, status: int = 422, **details) -> None:
        super().__init__(message)
        self.status = status
        self.details = details


class OAuthError(Exception):
    """An OAuth answer of refusal (RFC 6749 5.2, RFC 7591 3.2.2)."""

    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description}


def _ms(when: dt.datetime | None) -> int | None:
    return int(when.timestamp() * 1000) if when is not None else None


def _new(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _shaped(token: object, prefix: str) -> bool:
    return (isinstance(token, str) and token.startswith(prefix)
            and bool(_TOKEN_TAIL.fullmatch(token[len(prefix):])))


def next_refresh(secret: bytes, token: str) -> str:
    """The refresh token that follows `token`: made from it with the core's
    secret, so that the same one can be given again within REFRESH_GRACE
    without keeping it anywhere."""
    mac = hmac.new(secret, b"ai-refresh-next\0" + token.encode(), "sha256")
    tail = base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()
    return REFRESH_PREFIX + tail


def time_zone(name: object) -> str:
    """A time zone the page named, or UTC."""
    if isinstance(name, str) and _ZONE.fullmatch(name):
        try:
            zoneinfo.ZoneInfo(name)
            return name
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return "UTC"


# ------------------------------------------------------- where it goes back

def _loopback(parts) -> bool:
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK


def clean_redirect(uri: object) -> str | None:
    """A place an app may take the person back to, or None: https anywhere;
    http only on this computer (127.0.0.1, localhost, [::1]); an app's own
    scheme (cursor://…, com.example.app:/…), not one a browser runs or
    reads files with. No fragment, no user in it, nothing unprintable."""
    if not isinstance(uri, str) or not 1 <= len(uri) <= URI_MAX:
        return None
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in uri) or "#" in uri:
        return None
    try:
        parts = urlsplit(uri)
        parts.port          # a port that is not a number is no address
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if parts.username is not None or parts.password is not None:
        return None
    if scheme == "https":
        return uri if parts.hostname else None
    if scheme == "http":
        return uri if _loopback(parts) else None
    if not _SCHEME.fullmatch(scheme) or scheme in BAD_SCHEMES:
        return None
    rest = uri[len(scheme) + 1:]
    return uri if rest.strip("/") else None


def redirect_matches(registered: Iterable[str], given: str) -> bool:
    """Whether `given` is one of the places the app registered: exactly, or
    on this computer on another port (RFC 8252, 7.3; Claude Code takes the
    port it gets)."""
    registered = list(registered)
    if given in registered:
        return True
    try:
        g = urlsplit(given)
        g.port
    except ValueError:
        return False
    if not _loopback(g):
        return False
    for r in registered:
        try:
            p = urlsplit(r)
            p.port
        except ValueError:
            continue
        if (_loopback(p) and p.hostname == g.hostname and p.path == g.path
                and p.query == g.query):
            return True
    return False


def place(uri: str) -> dict:
    """Where an app takes the person back to, as /connect-ai shows it: a
    site (`web`, with its host, and the app's name if the host is known),
    this computer (`local`), or an app on it by its scheme (`app`)."""
    parts = urlsplit(uri)
    if parts.scheme.lower() == "https":
        host = (parts.hostname or "").lower()
        bare = host[4:] if host.startswith("www.") else host
        return {"kind": "web", "host": host, "known": KNOWN.get(bare)}
    if _loopback(parts):
        return {"kind": "local", "host": parts.hostname, "port": parts.port}
    return {"kind": "app", "scheme": parts.scheme.lower()}


def place_text(p: dict) -> str:
    """The place in one word, as the connection keeps it."""
    if p["kind"] == "web":
        return p["host"]
    if p["kind"] == "local":
        return p["host"] or "127.0.0.1"
    return f"{p['scheme']}:"


def shown_name(said: str, p: dict) -> str:
    """How tTerm calls the app: by its known place (claude.ai is Claude),
    otherwise by what it says it is."""
    if p["kind"] == "web" and p.get("known"):
        return p["known"]
    return said or "AI"


def warning(said: str, p: dict) -> dict | None:
    """An app that calls itself Claude or ChatGPT but takes the person back
    to a site that is not theirs: /connect-ai says so. Not for an app on
    this computer (127.0.0.1, its own scheme): the code goes no further than
    this computer, Claude Code and Claude for desktop rightly say "Claude"
    there, and the page says it is an app on this computer (the security
    review of 06.11, point 4)."""
    if p["kind"] != "web" or p.get("known"):
        return None
    low = said.lower()
    for brand, hosts in BRANDS.items():
        if brand in low:
            return {"claims": said, "real": hosts[0], "host": p["host"]}
    return None


def clean_said(raw: object) -> str:
    """What an app says it is, one line, as lists show names."""
    if not isinstance(raw, str):
        return ""
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return accounts.one_line(raw)[:NAME_MAX].strip()


# ---------------------------------------------------------- registering

async def register(pool: asyncpg.Pool, body: object, *, net: str) -> dict:
    """A new client, as RFC 7591 registers one: the answer, its client_id
    and, for one that authenticates at /oauth/token, its secret, which is
    not kept."""
    if not isinstance(body, dict):
        raise OAuthError("invalid_client_metadata", "Send the client's "
                         "metadata as a JSON object.")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= REDIRECTS_MAX:
        raise OAuthError("invalid_redirect_uri", "Give one to "
                         f"{REDIRECTS_MAX} redirect_uris.")
    cleaned: list[str] = []
    for uri in uris:
        good = clean_redirect(uri)
        if good is None:
            raise OAuthError("invalid_redirect_uri", "A redirect URI is https, "
                             "http on 127.0.0.1 or localhost, or an app's own "
                             "scheme, with no fragment.")
        if good not in cleaned:
            cleaned.append(good)
    grants = body.get("grant_types", ["authorization_code"])
    if (not isinstance(grants, list) or not grants
            or not set(grants) <= {"authorization_code", "refresh_token"}):
        raise OAuthError("invalid_client_metadata", "Only authorization_code "
                         "and refresh_token are granted.")
    responses = body.get("response_types", ["code"])
    if not isinstance(responses, list) or set(responses) - {"code"}:
        raise OAuthError("invalid_client_metadata", "Only the code response "
                         "type is supported.")
    method = body.get("token_endpoint_auth_method", "client_secret_basic")
    if method not in ("none", "client_secret_basic", "client_secret_post"):
        raise OAuthError("invalid_client_metadata", "token_endpoint_auth_method "
                         "is none, client_secret_basic or client_secret_post.")
    kind = body.get("application_type", "web")
    kind = kind if kind in ("web", "native") else "web"
    said = clean_said(body.get("client_name")) or shown_name("", place(cleaned[0]))
    secret = _new(SECRET_PREFIX) if method != "none" else None
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('ai-clients'))")
        counts = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE net = $1) AS here, count(*) AS all "
            "FROM ai_clients WHERE created_at > now() - interval '1 hour'", net)
        if (counts["here"] >= CLIENTS_PER_NET_HOUR
                or counts["all"] >= CLIENTS_PER_HOUR):
            raise OAuthError("temporarily_unavailable", "Too many apps "
                             "registered in the last hour. Try again later.",
                             429)
        row = await conn.fetchrow(
            "INSERT INTO ai_clients (name, redirect_uris, auth_method, "
            "secret_hash, application_type, net) VALUES ($1, $2, $3, $4, $5, "
            "$6) RETURNING id, created_at",
            said, cleaned, method,
            auth.token_hash(secret) if secret else None, kind, net)
    answer = {
        "client_id": str(row["id"]),
        "client_id_issued_at": int(row["created_at"].timestamp()),
        "client_name": said,
        "redirect_uris": cleaned,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
        "application_type": kind,
    }
    if secret is not None:
        answer.update({"client_secret": secret, "client_secret_expires_at": 0})
    return answer


async def _client(conn: asyncpg.Connection, client_id: object):
    try:
        cid = uuid.UUID(str(client_id))
    except ValueError:
        return None
    return await conn.fetchrow(
        "SELECT id, name, redirect_uris, auth_method, secret_hash "
        "FROM ai_clients WHERE id = $1", cid)


# ----------------------------------------------------------- authorizing

def _with(uri: str, params: dict) -> str:
    """`uri` with `params` added to its query."""
    joint = "&" if urlsplit(uri).query else ("" if uri.endswith("?") else "?")
    return uri + joint + urlencode(params)


async def authorize(pool: asyncpg.Pool, query: Mapping[str, str], *,
                    issuer: str, resource_ok: Callable[[str], bool],
                    net: str) -> str:
    """Where GET /oauth/authorize sends the browser: /connect-ai with the
    request, for the person; back to the app with an error, once where it
    goes back to is known to be the app's; or /connect-ai with the problem,
    when it is not (an unknown app, a place it did not register)."""
    async with pool.acquire() as conn:
        client = await _client(conn, query.get("client_id"))
    if client is None:
        return "/connect-ai?problem=client"
    redirect = query.get("redirect_uri")
    if redirect is None:
        if len(client["redirect_uris"]) != 1:
            return "/connect-ai?problem=redirect"
        redirect = client["redirect_uris"][0]
    elif not redirect_matches(client["redirect_uris"], redirect):
        return "/connect-ai?problem=redirect"
    state = query.get("state")

    def back(error: str, description: str) -> str:
        params = {"error": error, "error_description": description}
        if state is not None:
            params["state"] = state
        params["iss"] = issuer
        return _with(redirect, params)

    if state is not None and len(state) > STATE_MAX:
        return back("invalid_request", "The state is too long.")
    if query.get("response_type") != "code":
        return back("unsupported_response_type", "Only response_type=code.")
    challenge = query.get("code_challenge")
    if (query.get("code_challenge_method") != "S256"
            or not auth.is_challenge(challenge)):
        return back("invalid_request", "PKCE with S256 is required.")
    resource = query.get("resource")
    if resource is not None and not resource_ok(resource):
        return back("invalid_target", "tTerm is the resource here: "
                    "its MCP address.")
    scope = (query.get("scope") or "")[:SCOPE_MAX] or None
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           f"ai-requests:{net}")
        recent = await conn.fetchval(
            "SELECT count(*) FROM ai_requests WHERE net = $1 "
            "AND created_at > now() - interval '1 hour'", net)
        if recent >= REQUESTS_PER_NET_HOUR:
            return back("temporarily_unavailable", "Too many requests to "
                        "connect. Try again in an hour.")
        request_id = await conn.fetchval(
            "INSERT INTO ai_requests (client_id, redirect_uri, challenge, "
            "state, scope, net, expires_at) VALUES ($1, $2, $3, $4, $5, $6, "
            "now() + make_interval(mins => $7)) RETURNING id",
            client["id"], redirect, challenge, state, scope, net,
            REQUEST_MINUTES)
    return f"/connect-ai?request={request_id}"


_REQUEST = """
    SELECT r.id, r.client_id, r.redirect_uri, r.challenge, r.state, r.scope,
           r.expires_at, r.answered_at, r.expires_at <= now() AS over,
           c.name AS said
    FROM ai_requests r JOIN ai_clients c ON c.id = r.client_id
    WHERE r.id = $1
"""


def _request_over(row) -> Refused | None:
    if row is None:
        return Refused("This request to connect is not there. Connect tTerm "
                       "in the AI app again.", 404)
    if row["answered_at"] is not None or row["over"]:
        return Refused("This request to connect is over. Connect tTerm in the "
                       "AI app again.", 410, over=True)
    return None


def _guarded_row(row) -> bool:
    return vault.guarded(SimpleNamespace(kind=row["kind"],
                                         login_method=row["login_method"]))


async def request_view(pool: asyncpg.Pool, request_id: uuid.UUID,
                       person: accounts.Person) -> dict:
    """What /connect-ai shows of a request: the app, where it goes back to
    (and a warning if it calls itself what it is not), the person's
    projects with their machines, and the choices of the same app's latest
    connection, to start from."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_REQUEST, request_id)
    if (refusal := _request_over(row)) is not None:
        raise refusal
    p = place(row["redirect_uri"])
    name = shown_name(row["said"], p)
    projects = await accounts.projects_of(pool, person.id)
    async with pool.acquire() as conn:
        same = await conn.fetchval(
            "SELECT id FROM ai_connections WHERE user_id = $1 AND name = $2 "
            "AND returns_to = $3 AND ended_at IS NULL AND connected_at IS NOT "
            "NULL ORDER BY created_at DESC LIMIT 1",
            person.id, name, place_text(p))
    listed = [{
        "id": pr["id"], "name": pr["name"], "code": pr["code"],
        "color": pr["color"], "role": pr["role"],
        "machines": [{"id": m["id"], "name": m["name"], "kind": m["kind"],
                      "os": m.get("os"), "method": m.get("method"),
                      "guarded": vault.guarded(SimpleNamespace(
                          kind=m["kind"], login_method=m.get("method"))),
                      "mine": m.get("mine", True)}
                     for m in pr["machines"]],
    } for pr in projects]
    found = (await connections_of(pool, person.id, only=same)) if same else []
    return {
        "request": {"id": str(row["id"]), "name": name, "said": row["said"],
                    "place": p, "warning": warning(row["said"], p),
                    "scope": row["scope"], "expiresAt": _ms(row["expires_at"])},
        "projects": listed,
        "same": found[0] if found else None,
    }


@dataclasses.dataclass
class Choice:
    """What the person ticked on /connect-ai or in Change."""
    #: Machine → (its terminals, its log).
    machines: dict[uuid.UUID, tuple[bool, bool]]
    see: bool
    propose: bool
    notes: bool
    #: A key of TERMS; for a change also "keep".
    term: str
    zone: str


def parse_choice(body: object, *, change: bool = False) -> Choice:
    """The person's choice from the page's request, checked."""
    if not isinstance(body, dict):
        raise Refused("Say what the AI may do.")
    raw = body.get("machines")
    if not isinstance(raw, list) or len(raw) > MACHINES_MAX:
        raise Refused("Say which machines the AI may see.")
    machines: dict[uuid.UUID, tuple[bool, bool]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise Refused("Say which machines the AI may see.")
        try:
            machine = uuid.UUID(str(item.get("id")))
        except ValueError:
            raise Refused("Say which machines the AI may see.") from None
        terminals, log = item.get("terminals") is True, item.get("log") is True
        if terminals or log:
            machines[machine] = (terminals, log)
    can = body.get("can")
    if not isinstance(can, dict):
        raise Refused("Say what the AI may do.")
    see, propose, notes = (can.get("see") is True, can.get("propose") is True,
                           can.get("notes") is True)
    if (propose and "propose" not in ABILITIES) or (notes and "notes" not in ABILITIES):
        raise Refused("This tTerm does not let an AI propose commands or "
                      "write notes yet.")
    if (propose or notes) and not see:
        raise Refused("An AI that proposes commands or writes notes sees too.")
    if not see:
        raise Refused("Tick Look: without it the AI can do nothing.")
    if not machines:
        raise Refused("Tick at least one machine: its terminals, its log, or "
                      "both.")
    term = body.get("term")
    if term not in TERMS and not (change and term == "keep"):
        raise Refused("Say how long the AI may stay.")
    return Choice(machines, see, propose, notes, term, time_zone(body.get("zone")))


async def choosable(pool: asyncpg.Pool, user_id: uuid.UUID,
                    machine_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, SimpleNamespace]:
    """Of these machines, those of the person's projects now, each with its
    project and what the lock needs to know: by id."""
    rows = await pool.fetch(
        """
        SELECT m.id, m.name, m.kind, m.login_method, m.via_machine_id,
               m.created_by, m.project_id
        FROM machines m
        JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL
        JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = $2
             AND (pm.until IS NULL OR pm.until > now())
        WHERE m.id = ANY($1::uuid[]) AND m.removed_at IS NULL
        """, list(machine_ids), user_id)
    return {r["id"]: SimpleNamespace(**dict(r)) for r in rows}


def _until(term: str) -> dt.timedelta | None:
    days = TERMS[term]
    return dt.timedelta(days=days) if days is not None else None


async def allow(pool: asyncpg.Pool, request_id: uuid.UUID,
                session: auth.Session, choice: Choice, *, issuer: str) -> str:
    """The person allows the request: the connection as they chose it, not
    connected until the app trades the code that goes back to it. Where
    the browser goes: back to the app, with the code. The machines are
    checked to be the person's; the caller checked the keys (a machine
    under them takes them open on this device, or the vault's password)."""
    person = session.person
    machines = await choosable(pool, person.id, choice.machines)
    if set(machines) != set(choice.machines):
        raise Refused("One of the machines is not in your projects any more. "
                      "Load the page again.", 409)
    code = _new(CODE_PREFIX)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(_REQUEST + " FOR UPDATE OF r", request_id)
        if (refusal := _request_over(row)) is not None:
            raise refusal
        p = place(row["redirect_uri"])
        until = _until(choice.term)
        connection = await conn.fetchval(
            """
            INSERT INTO ai_connections (user_id, client_id, kind, name, said,
                returns_to, can_see, can_propose, can_note, expires_at,
                keys_device_id, keys_session_id, time_zone)
            VALUES ($1, $2, 'oauth', $3, $4, $5, $6, $7, $8,
                    CASE WHEN $9::interval IS NULL THEN NULL
                         ELSE now() + $9::interval END, $10, $11, $12)
            RETURNING id
            """, person.id, row["client_id"], shown_name(row["said"], p),
            row["said"], place_text(p), choice.see, choice.propose,
            choice.notes, until, session.device_id, session.id, choice.zone)
        await _put_grants(conn, connection, choice, machines)
        await conn.execute(
            "INSERT INTO ai_codes (code_hash, connection_id, client_id, "
            "redirect_uri, challenge, session_id, expires_at) VALUES ($1, $2, "
            "$3, $4, $5, $6, now() + make_interval(mins => $7))",
            auth.token_hash(code), connection, row["client_id"],
            row["redirect_uri"], row["challenge"], session.id, CODE_MINUTES)
        await conn.execute("UPDATE ai_requests SET answered_at = now() "
                           "WHERE id = $1", request_id)
    params = {"code": code}
    if row["state"] is not None:
        params["state"] = row["state"]
    params["iss"] = issuer
    return _with(row["redirect_uri"], params)


async def deny(pool: asyncpg.Pool, request_id: uuid.UUID, *,
               issuer: str) -> str:
    """The person refuses: back to the app, which hears access_denied."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(_REQUEST + " FOR UPDATE OF r", request_id)
        if (refusal := _request_over(row)) is not None:
            raise refusal
        await conn.execute("UPDATE ai_requests SET answered_at = now() "
                           "WHERE id = $1", request_id)
    params = {"error": "access_denied",
              "error_description": "The person did not allow it."}
    if row["state"] is not None:
        params["state"] = row["state"]
    params["iss"] = issuer
    return _with(row["redirect_uri"], params)


async def _put_grants(conn: asyncpg.Connection, connection: uuid.UUID,
                      choice: Choice, machines: dict) -> None:
    await conn.executemany(
        "INSERT INTO ai_grants (connection_id, machine_id, project_id, "
        "terminals, log) VALUES ($1, $2, $3, $4, $5)",
        [(connection, mid, machines[mid].project_id, t, lg)
         for mid, (t, lg) in choice.machines.items()])


# -------------------------------------------------------------- the tokens

@dataclasses.dataclass(frozen=True)
class ClientAuth:
    """How the app said who it is at /oauth/token: client_id, and the secret
    in the form or in HTTP Basic."""
    client_id: str | None
    secret: str | None = None


async def _client_checked(conn: asyncpg.Connection, given: ClientAuth):
    """The client, if it is who it says: a public one by its id; one with a
    secret by that too. invalid_client otherwise."""
    client = await _client(conn, given.client_id)
    if client is None:
        raise OAuthError("invalid_client", "No such client.", 401)
    if client["auth_method"] == "none":
        return client
    if not isinstance(given.secret, str) or not hmac.compare_digest(
            bytes(client["secret_hash"]), auth.token_hash(given.secret)):
        raise OAuthError("invalid_client", "The client's secret is wrong.", 401)
    return client


def _tokens(access: str, refresh: str) -> dict:
    return {"access_token": access, "token_type": "Bearer",
            "expires_in": ACCESS_SECONDS, "refresh_token": refresh,
            "scope": "tterm"}


async def _issue(conn: asyncpg.Connection, connection: uuid.UUID,
                 refresh: str | None) -> str:
    """A new access token for the connection, and `refresh` kept as its
    refresh token unless None; the access token."""
    access = _new(ACCESS_PREFIX)
    await conn.execute(
        "INSERT INTO ai_tokens (connection_id, kind, token_hash, expires_at) "
        "VALUES ($1, 'access', $2, now() + make_interval(secs => $3))",
        connection, auth.token_hash(access), ACCESS_SECONDS)
    if refresh is not None:
        await conn.execute(
            "INSERT INTO ai_tokens (connection_id, kind, token_hash) "
            "VALUES ($1, 'refresh', $2)", connection, auth.token_hash(refresh))
    return access


async def _end(conn: asyncpg.Connection, connections: list[uuid.UUID],
               reason: str) -> list[uuid.UUID]:
    """Ends connections: their tokens go at once. The ones that were on."""
    ended = [r["id"] for r in await conn.fetch(
        "UPDATE ai_connections SET ended_at = now(), end_reason = $2 "
        "WHERE id = ANY($1::uuid[]) AND ended_at IS NULL RETURNING id",
        connections, reason)]
    await conn.execute("DELETE FROM ai_tokens WHERE connection_id = ANY($1::uuid[])",
                       connections)
    return ended


_LIVE = ("c.ended_at IS NULL AND (c.expires_at IS NULL OR c.expires_at > now())")


async def exchange(pool: asyncpg.Pool, form: Mapping[str, str],
                   given: ClientAuth, *,
                   resource_ok: Callable[[str], bool]) -> dict:
    """grant_type=authorization_code: the app's code and its secret (PKCE)
    for its first tokens; the connection is on from now. One try per code:
    a wrong secret spends it, and a code used again ends the connection it
    made, as RFC 6749 4.1.2 asks."""
    code = form.get("code")
    verifier = form.get("code_verifier")
    resource = form.get("resource")
    if resource is not None and not resource_ok(resource):
        raise OAuthError("invalid_target", "tTerm is the resource here: its "
                         "MCP address.")
    if not _shaped(code, CODE_PREFIX) or not auth.is_verifier(verifier):
        raise OAuthError("invalid_grant", "This code is not one of tTerm's.")
    refusal: OAuthError | None = None
    answer: dict | None = None
    async with pool.acquire() as conn, conn.transaction():
        client = await _client_checked(conn, given)
        row = await conn.fetchrow(
            """
            UPDATE ai_codes SET used_at = now()
            WHERE code_hash = $1 AND used_at IS NULL
            RETURNING connection_id, client_id, redirect_uri, challenge,
                      session_id, expires_at > now() AS fresh
            """, auth.token_hash(code))
        if row is None:
            again = await conn.fetchval(
                "SELECT connection_id FROM ai_codes WHERE code_hash = $1",
                auth.token_hash(code))
            if again is not None:
                await _end(conn, [again], "code used again")
            refusal = OAuthError("invalid_grant", "This code was used or is "
                                 "not there.")
        elif (row["client_id"] != client["id"] or not row["fresh"]
              or form.get("redirect_uri", row["redirect_uri"]) != row["redirect_uri"]
              or not hmac.compare_digest(auth.pkce_challenge(verifier).encode(),
                                         row["challenge"].encode())):
            await _end(conn, [row["connection_id"]], "code refused")
            refusal = OAuthError("invalid_grant", "This code does not go "
                                 "with this app, this place or this secret, "
                                 "or it is over.")
        else:
            c = await conn.fetchrow(
                f"""
                SELECT c.id, c.user_id, c.name, c.returns_to, c.expires_at,
                       EXISTS (SELECT 1 FROM sessions s WHERE s.id = $2
                               AND s.ended_at IS NULL
                               AND s.last_seen_at > now() - $3::interval)
                           AS signed_in
                FROM ai_connections c
                WHERE c.id = $1 AND {_LIVE}
                """, row["connection_id"], row["session_id"], auth.IDLE)
            if c is None or not c["signed_in"]:
                await _end(conn, [row["connection_id"]], "signed out")
                refusal = OAuthError("invalid_grant", "The sign-in that allowed "
                                     "this ended. Connect again.")
            else:
                await conn.execute("UPDATE ai_connections SET connected_at = now() "
                                   "WHERE id = $1", c["id"])
                first = _new(REFRESH_PREFIX)
                access = await _issue(conn, c["id"], first)
                await accounts.journal(
                    conn, "ai.connected", user_id=c["user_id"],
                    details={"connection": str(c["id"]), "ai": c["name"],
                             "returnsTo": c["returns_to"],
                             "until": (c["expires_at"].isoformat()
                                       if c["expires_at"] else None)})
                answer = _tokens(access, first)
    if refusal is not None:
        raise refusal
    assert answer is not None
    return answer


async def refresh(pool: asyncpg.Pool, secret: bytes, form: Mapping[str, str],
                  given: ClientAuth, *,
                  resource_ok: Callable[[str], bool]) -> dict:
    """grant_type=refresh_token: the next tokens, each refresh token once.
    Used again within REFRESH_GRACE while the one it made is not used yet,
    it gives that one again; otherwise someone else has it, and the
    connection ends."""
    token = form.get("refresh_token")
    resource = form.get("resource")
    if resource is not None and not resource_ok(resource):
        raise OAuthError("invalid_target", "tTerm is the resource here: its "
                         "MCP address.")
    if not _shaped(token, REFRESH_PREFIX):
        raise OAuthError("invalid_grant", "This refresh token is not one of "
                         "tTerm's.")
    refusal: OAuthError | None = None
    answer: dict | None = None
    following = next_refresh(secret, token)
    async with pool.acquire() as conn, conn.transaction():
        client = await _client_checked(conn, given)
        row = await conn.fetchrow(
            f"""
            SELECT t.id, t.connection_id, t.used_at,
                   t.used_at > now() - $2::interval AS lately,
                   c.client_id, ({_LIVE}) AS live
            FROM ai_tokens t
            JOIN ai_connections c ON c.id = t.connection_id
            WHERE t.token_hash = $1 AND t.kind = 'refresh'
            FOR UPDATE OF t
            """, auth.token_hash(token), REFRESH_GRACE)
        if row is None or row["client_id"] != client["id"] or not row["live"]:
            refusal = OAuthError("invalid_grant", "This refresh token is over. "
                                 "Connect again.")
        elif row["used_at"] is None:
            await conn.execute("UPDATE ai_tokens SET used_at = now() WHERE id = $1",
                               row["id"])
            access = await _issue(conn, row["connection_id"], following)
            answer = _tokens(access, following)
        else:
            child = await conn.fetchrow(
                "SELECT used_at FROM ai_tokens WHERE token_hash = $1 "
                "AND kind = 'refresh' AND connection_id = $2",
                auth.token_hash(following), row["connection_id"])
            if row["lately"] and child is not None and child["used_at"] is None:
                access = await _issue(conn, row["connection_id"], None)
                answer = _tokens(access, following)
            else:
                person = await conn.fetchval(
                    "SELECT user_id FROM ai_connections WHERE id = $1",
                    row["connection_id"])
                if await _end(conn, [row["connection_id"]], "refresh used again"):
                    await accounts.journal(
                        conn, "ai.ended", user_id=person,
                        details={"connection": str(row["connection_id"]),
                                 "why": "refresh used again"})
                refusal = OAuthError("invalid_grant", "This refresh token was "
                                     "used already. Connect again.")
    if refusal is not None:
        raise refusal
    assert answer is not None
    return answer


async def revoke(pool: asyncpg.Pool, token: object, given: ClientAuth) -> None:
    """The app gives its token back (RFC 7009): the connection it is of
    ends. Whatever it gives, the answer is the same."""
    if not isinstance(token, str) or not (_shaped(token, ACCESS_PREFIX)
                                          or _shaped(token, REFRESH_PREFIX)):
        return
    async with pool.acquire() as conn, conn.transaction():
        client = await _client_checked(conn, given)
        row = await conn.fetchrow(
            "SELECT t.connection_id, c.client_id, c.user_id FROM ai_tokens t "
            "JOIN ai_connections c ON c.id = t.connection_id "
            "WHERE t.token_hash = $1", auth.token_hash(token))
        if row is None or row["client_id"] != client["id"]:
            return
        if await _end(conn, [row["connection_id"]], "the app took it back"):
            await accounts.journal(conn, "ai.ended", user_id=row["user_id"],
                                   details={"connection": str(row["connection_id"]),
                                            "why": "the app took it back"})


# ------------------------------------------------------ asking, at /mcp

@dataclasses.dataclass(frozen=True)
class Connection:
    """A connection as /mcp knows the AI by its access token."""
    id: uuid.UUID
    user_id: uuid.UUID
    person: str
    email: str | None
    language: str | None
    name: str
    said: str
    returns_to: str
    can_see: bool
    can_propose: bool
    can_note: bool
    expires_at: dt.datetime | None
    keys_device_id: uuid.UUID | None
    keys_session_id: uuid.UUID | None
    time_zone: str


async def connection_of(pool: asyncpg.Pool, token: object) -> Connection | None:
    """The connection of a live access token."""
    if not _shaped(token, ACCESS_PREFIX):
        return None
    row = await pool.fetchrow(
        f"""
        SELECT c.id, c.user_id, c.name, c.said, c.returns_to, c.can_see,
               c.can_propose, c.can_note, c.expires_at, c.keys_device_id,
               c.keys_session_id, c.time_zone, u.name AS person, u.email,
               u.language
        FROM ai_tokens t
        JOIN ai_connections c ON c.id = t.connection_id
        JOIN users u ON u.id = c.user_id
        WHERE t.token_hash = $1 AND t.kind = 'access' AND t.expires_at > now()
          AND c.connected_at IS NOT NULL AND {_LIVE}
        """, auth.token_hash(token))
    if row is None:
        return None
    return Connection(row["id"], row["user_id"], row["person"], row["email"],
                      row["language"], row["name"], row["said"],
                      row["returns_to"], row["can_see"], row["can_propose"],
                      row["can_note"], row["expires_at"], row["keys_device_id"],
                      row["keys_session_id"], row["time_zone"])


@dataclasses.dataclass(frozen=True)
class Seen:
    """A machine a connection was allowed, as it is now."""
    id: uuid.UUID
    name: str
    kind: str
    os: str | None
    project_id: uuid.UUID
    project: str
    code: str
    terminals: bool
    log: bool
    #: Under the person's keys (vault.guarded).
    guarded: bool
    #: Its terminals closed to the AI now: the keys are not open on the
    #: device it was allowed from.
    locked: bool
    #: Its log closed too: only the person's own machine's, as for them.
    log_locked: bool

    @property
    def sees_terminals(self) -> bool:
        return self.terminals and not self.locked

    @property
    def sees_log(self) -> bool:
        return self.log and not self.log_locked


_GRANTED = """
    SELECT g.connection_id, g.machine_id, g.project_id, g.terminals, g.log,
           m.name, m.kind,
           m.login_method, m.created_by, m.passport->>'os' AS os,
           p.name AS project, p.code
    FROM ai_grants g
    JOIN machines m ON m.id = g.machine_id AND m.project_id = g.project_id
         AND m.removed_at IS NULL
    JOIN projects p ON p.id = g.project_id AND p.deleted_at IS NULL
    JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = $2
         AND (pm.until IS NULL OR pm.until > now())
    WHERE g.connection_id = ANY($1::uuid[])
"""


def _seen(row, user_id: uuid.UUID, guarding: bool, keys_open: bool) -> Seen:
    guarded = _guarded_row(row)
    locked = guarding and guarded and not keys_open
    return Seen(row["machine_id"], row["name"], row["kind"], row["os"],
                row["project_id"], row["project"], row["code"],
                row["terminals"], row["log"], guarded, locked,
                locked and row["created_by"] in (None, user_id))


async def seen(pool: asyncpg.Pool, connection: Connection,
               keys_open: bool) -> list[Seen]:
    """The machines the connection sees now, by project and name: those it
    was allowed, still in that project, with the person still there; locked
    while its keys are not open (`keys_open`, on the device it was allowed
    from) and the person's keys guard something (vault.guarding)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _GRANTED + " ORDER BY p.created_at, p.name, lower(m.name)",
            [connection.id], connection.user_id)
        guarding = await vault.guarding(conn, connection.user_id)
    return [_seen(r, connection.user_id, guarding, keys_open) for r in rows]


async def all_machines(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every machine of the person's: in their projects, or with a terminal
    of theirs. What search keeps from an AI is all of these but the ones it
    sees."""
    rows = await pool.fetch(
        "SELECT id FROM machines WHERE project_id IN (SELECT project_id FROM "
        "project_members WHERE user_id = $1) OR id IN (SELECT machine_id FROM "
        "terminals WHERE owner_id = $1)", user_id)
    return [r["id"] for r in rows]


async def mark_read(pool: asyncpg.Pool, connection_id: uuid.UUID,
                    terminal_id: uuid.UUID, seqs: list[int]) -> None:
    """The AI read these blocks of a terminal: the page marks them."""
    seqs = sorted({s for s in seqs if isinstance(s, int) and 0 < s < 2 ** 63})
    if not seqs:
        return
    await pool.execute(
        "INSERT INTO ai_reads (connection_id, terminal_id, seq_start) "
        "SELECT $1, $2, unnest($3::bigint[]) "
        "ON CONFLICT (connection_id, terminal_id, seq_start) "
        "DO UPDATE SET at = now()", connection_id, terminal_id, seqs)


async def reads_of(pool: asyncpg.Pool, terminal_id: uuid.UUID,
                   seqs: list[int]) -> dict[int, list[dict]]:
    """Which AI read which of these blocks, for the feed."""
    if not seqs:
        return {}
    rows = await pool.fetch(
        "SELECT r.seq_start, c.id, c.name FROM ai_reads r "
        "JOIN ai_connections c ON c.id = r.connection_id "
        "WHERE r.terminal_id = $1 AND r.seq_start = ANY($2::bigint[]) "
        "ORDER BY r.seq_start, c.created_at", terminal_id, seqs)
    found: dict[int, list[dict]] = {}
    for r in rows:
        found.setdefault(r["seq_start"], []).append(
            {"id": str(r["id"]), "name": r["name"]})
    return found


async def called(pool: asyncpg.Pool, connection_id: uuid.UUID) -> None:
    """The AI asked something: Settings shows when it did last."""
    await pool.execute("UPDATE ai_connections SET last_call_at = now() "
                       "WHERE id = $1", connection_id)


# ------------------------------------------------------- in the settings

async def connections_of(pool: asyncpg.Pool, user_id: uuid.UUID, *,
                         only: uuid.UUID | None = None) -> list[dict]:
    """The person's AI, as Settings › AI and the projects' pages list it:
    each connection with what it may do, its machines (those that count:
    still in the project it was allowed in), how long, when it asked last
    and the device it follows for the machines under the keys."""
    rows = await pool.fetch(
        f"""
        SELECT c.id, c.kind, c.name, c.said, c.returns_to, c.can_see,
               c.can_propose, c.can_note, c.expires_at, c.created_at,
               c.connected_at, c.last_call_at, c.keys_device_id,
               c.keys_session_id, c.time_zone, d.label AS device
        FROM ai_connections c
        LEFT JOIN devices d ON d.id = c.keys_device_id
        WHERE c.user_id = $1 AND c.connected_at IS NOT NULL AND {_LIVE}
          AND ($2::uuid IS NULL OR c.id = $2)
        ORDER BY c.created_at
        """, user_id, only)
    grants = await pool.fetch(
        _GRANTED + " ORDER BY p.created_at, p.name, lower(m.name)",
        [r["id"] for r in rows], user_id) if rows else []
    per: dict[uuid.UUID, list[dict]] = {r["id"]: [] for r in rows}
    for g in grants:
        per[g["connection_id"]].append({
            "id": str(g["machine_id"]), "name": g["name"], "kind": g["kind"],
            "projectId": str(g["project_id"]), "project": g["project"],
            "code": g["code"], "terminals": g["terminals"], "log": g["log"],
            "guarded": _guarded_row(g)})
    return [{
        "id": str(c["id"]), "kind": c["kind"], "name": c["name"],
        "said": c["said"], "returnsTo": c["returns_to"],
        "can": {"see": c["can_see"], "propose": c["can_propose"],
                "notes": c["can_note"]},
        "until": _ms(c["expires_at"]), "createdAt": _ms(c["created_at"]),
        "connectedAt": _ms(c["connected_at"]),
        "lastCall": _ms(c["last_call_at"]),
        "keysDevice": ({"id": str(c["keys_device_id"]), "label": c["device"]}
                       if c["keys_device_id"] is not None else None),
        "keysSession": (str(c["keys_session_id"])
                        if c["keys_session_id"] is not None else None),
        "zone": c["time_zone"],
        "machines": per[c["id"]],
    } for c in rows]


async def grants_now(pool: asyncpg.Pool, user_id: uuid.UUID,
                     connection_id: uuid.UUID) -> dict[uuid.UUID, tuple[bool, bool]] | None:
    """A connection's machines as they count now: machine → (terminals,
    log); None if it is not one of the person's live ones."""
    async with pool.acquire() as conn:
        live = await conn.fetchval(
            f"SELECT true FROM ai_connections c WHERE c.id = $1 AND c.user_id = $2 "
            f"AND c.connected_at IS NOT NULL AND {_LIVE}", connection_id, user_id)
        if not live:
            return None
        rows = await conn.fetch(_GRANTED, [connection_id], user_id)
    return {r["machine_id"]: (r["terminals"], r["log"]) for r in rows}


async def change(pool: asyncpg.Pool, user_id: uuid.UUID,
                 connection_id: uuid.UUID, choice: Choice, *,
                 keys_from: auth.Session | None) -> dict:
    """Change: the connection's machines and what it may do as now chosen,
    for as long as chosen ("keep": as it was). With `keys_from`, the
    machines under the keys follow that session's device from now (the
    caller checked its keys)."""
    machines = await choosable(pool, user_id, choice.machines)
    if set(machines) != set(choice.machines):
        raise Refused("One of the machines is not in your projects any more. "
                      "Load the page again.", 409)
    async with pool.acquire() as conn, conn.transaction():
        found = await conn.fetchval(
            f"SELECT c.id FROM ai_connections c WHERE c.id = $1 AND c.user_id = $2 "
            f"AND c.connected_at IS NOT NULL AND {_LIVE} FOR UPDATE",
            connection_id, user_id)
        if found is None:
            raise Refused("This AI is not connected any more.", 404)
        await conn.execute("DELETE FROM ai_grants WHERE connection_id = $1",
                           connection_id)
        await _put_grants(conn, connection_id, choice, machines)
        await conn.execute(
            "UPDATE ai_connections SET can_see = $2, can_propose = $3, "
            "can_note = $4, time_zone = $5 WHERE id = $1",
            connection_id, choice.see, choice.propose, choice.notes, choice.zone)
        if choice.term != "keep":
            await conn.execute(
                "UPDATE ai_connections SET expires_at = CASE WHEN "
                "$2::interval IS NULL THEN NULL ELSE now() + $2::interval END "
                "WHERE id = $1", connection_id, _until(choice.term))
        if keys_from is not None:
            await conn.execute(
                "UPDATE ai_connections SET keys_device_id = $2, "
                "keys_session_id = $3 WHERE id = $1", connection_id,
                keys_from.device_id, keys_from.id)
        await accounts.journal(conn, "ai.changed", user_id=user_id,
                               details={"connection": str(connection_id),
                                        "machines": len(choice.machines)})
    return (await connections_of(pool, user_id, only=connection_id))[0]


async def forget(pool: asyncpg.Pool, user_id: uuid.UUID,
                 connection_id: uuid.UUID) -> bool:
    """Forget: the connection ends, its tokens go at once."""
    async with pool.acquire() as conn, conn.transaction():
        own = await conn.fetchval(
            "SELECT id FROM ai_connections WHERE id = $1 AND user_id = $2 "
            "AND ended_at IS NULL", connection_id, user_id)
        if own is None:
            return False
        await _end(conn, [connection_id], "forgotten")
        await accounts.journal(conn, "ai.forgotten", user_id=user_id,
                               details={"connection": str(connection_id)})
    return True


async def forget_all(pool: asyncpg.Pool, user_id: uuid.UUID,
                     reason: str = "all AI disconnected",
                     conn: asyncpg.Connection | None = None) -> int:
    """Disconnect all AI (and Forget all devices, auth.sign_out_everywhere):
    every connection of the person's ends. How many were on."""
    if conn is None:
        async with pool.acquire() as conn, conn.transaction():
            return await forget_all(pool, user_id, reason, conn)
    ids = [r["id"] for r in await conn.fetch(
        "SELECT id FROM ai_connections WHERE user_id = $1 AND ended_at IS NULL",
        user_id)]
    ended = await _end(conn, ids, reason) if ids else []
    if ended:
        await accounts.journal(conn, "ai.forgotten_all", user_id=user_id,
                               details={"connections": len(ended),
                                        "why": reason})
    return len(ended)


async def leave_project(pool: asyncpg.Pool, user_id: uuid.UUID,
                        connection_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    """Disconnect from the project: the connection sees none of its machines
    any more; in other projects it stays."""
    async with pool.acquire() as conn, conn.transaction():
        own = await conn.fetchval(
            f"SELECT c.id FROM ai_connections c WHERE c.id = $1 AND c.user_id = $2 "
            f"AND {_LIVE}", connection_id, user_id)
        if own is None:
            return False
        await conn.execute("DELETE FROM ai_grants WHERE connection_id = $1 "
                           "AND project_id = $2", connection_id, project_id)
        await accounts.journal(conn, "ai.left_project", user_id=user_id,
                               project_id=project_id,
                               details={"connection": str(connection_id)})
    return True


async def watching(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[dict]:
    """For the page's terminals: each live connection of the person's with
    the machines whose terminals it may see, as allowed; `_keys`, the
    device and the session it follows for the machines under the keys,
    for the caller to say whether they are open to it now."""
    rows = await pool.fetch(
        f"""
        SELECT c.id, c.name, c.can_see, c.can_propose, c.can_note,
               c.expires_at, c.keys_device_id, c.keys_session_id, g.machine_id
        FROM ai_connections c
        JOIN ai_grants g ON g.connection_id = c.id AND g.terminals
        JOIN machines m ON m.id = g.machine_id AND m.project_id = g.project_id
             AND m.removed_at IS NULL
        WHERE c.user_id = $1 AND c.connected_at IS NOT NULL AND {_LIVE}
        ORDER BY c.created_at
        """, user_id)
    out: dict[uuid.UUID, dict] = {}
    for r in rows:
        item = out.setdefault(r["id"], {
            "id": str(r["id"]), "name": r["name"],
            "can": {"see": r["can_see"], "propose": r["can_propose"],
                    "notes": r["can_note"]},
            "until": _ms(r["expires_at"]), "machines": [],
            # For the caller: whether its keys are open (not sent on).
            "_keys": (r["keys_device_id"], r["keys_session_id"])})
        item["machines"].append(str(r["machine_id"]))
    return list(out.values())


async def vault_password(pool: asyncpg.Pool, user_id: uuid.UUID) -> bool:
    """Whether the person's vault has a password to type on /connect-ai."""
    return bool(await pool.fetchval(
        "SELECT password_check IS NOT NULL FROM vaults WHERE user_id = $1",
        user_id))


# ------------------------------------------------------------- sweeping

async def sweep(pool: asyncpg.Pool) -> None:
    """Requests and codes after a day; spent and old tokens; connections
    whose time is up end, those never connected go after a day; apps that
    registered and never connected after a day; what an AI read, with the
    blocks (they are kept 30 days); grants of machines moved or removed."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM ai_requests "
                           "WHERE created_at < now() - interval '1 day'")
        await conn.execute("DELETE FROM ai_codes "
                           "WHERE created_at < now() - interval '1 day'")
        await conn.execute(
            "DELETE FROM ai_tokens WHERE (kind = 'access' AND expires_at < now()) "
            "OR (kind = 'refresh' AND used_at < now() - interval '1 day')")
        over = [r["id"] for r in await conn.fetch(
            "SELECT id FROM ai_connections WHERE ended_at IS NULL "
            "AND expires_at <= now()")]
        if over:
            await _end(conn, over, "its time was up")
        await conn.execute(
            "DELETE FROM ai_connections WHERE connected_at IS NULL "
            "AND created_at < now() - interval '1 day'")
        await conn.execute(
            "DELETE FROM ai_clients cl WHERE cl.created_at < now() - interval "
            "'1 day' AND NOT EXISTS (SELECT 1 FROM ai_connections c "
            "WHERE c.client_id = cl.id)")
        await conn.execute("DELETE FROM ai_reads "
                           "WHERE at < now() - interval '31 days'")
        await conn.execute(
            "DELETE FROM ai_grants g USING machines m WHERE m.id = g.machine_id "
            "AND (m.removed_at IS NOT NULL OR m.project_id <> g.project_id)")
