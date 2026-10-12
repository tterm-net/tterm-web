"""Machines: the one-command install, registration and the server passport.

How a server joins:

1. The person asks for a command. The core makes a one-time token that
   works for ten minutes and keeps only its hash.
2. On the server, `curl …/s/TOKEN | sudo sh` fetches the installer. It
   creates the user tterm, trusts our CA and posts back the server's host
   keys, hostname and SSH port.
3. The core signs in over SSH to the address the post came from, with a
   fresh certificate and with the host keys just received pinned. If that
   works, it reads the passport and the machine is in. If not, the person
   sees why, and the same command can be run again until it expires.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import asyncssh

from . import accounts, passport, ssh
from .signer_client import SignerClient

log = logging.getLogger("tterm.machines")

INSTALL_MINUTES = 10
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
LOGIN = "tterm"
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "install.sh"
#: Everything the core may spend on checking a fresh server.
CHECK_TIMEOUT = 40


def new_token() -> str:
    """Twelve characters, 72 random bits: short enough to read, far too many
    to guess in ten minutes."""
    return secrets.token_urlsafe(9)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def command(connect_url: str, token: str) -> str:
    return f"curl -sSL {connect_url}/s/{token} | sudo sh"


def machine_name(hostname: str) -> str:
    """The short hostname, cleaned up: web-01.example.com → web-01."""
    short = hostname.strip().split(".")[0]
    short = re.sub(r"[^A-Za-z0-9_-]+", "-", short).strip("-")[:40]
    return short or "server"


def free_name(wanted: str, taken: set[str]) -> str:
    lower = {t.lower() for t in taken}
    if wanted.lower() not in lower:
        return wanted
    for n in range(2, 1000):
        candidate = f"{wanted[:36]}-{n}"
        if candidate.lower() not in lower:
            return candidate
    return f"{wanted[:24]}-{secrets.token_hex(4)}"


def render_installer(*, token: str, ca_key: str, ca_id: str,
                     connect_url: str) -> str:
    if not TOKEN_RE.match(token):
        raise ValueError("bad token")
    key = " ".join(ca_key.split()[:2])         # type and key, no comment
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{SSH_USER}}", LOGIN)
            .replace("{{CA_KEY}}", key)
            .replace("{{CA_ID}}", ca_id)
            .replace("{{API_URL}}", connect_url)
            .replace("{{TOKEN}}", token))


def refusal_script(message: str) -> str:
    """What /s/TOKEN serves when the command cannot work: says so and stops
    before touching anything."""
    safe = message.replace("'", "")
    return f"#!/bin/sh\nprintf '\\n  x %s\\n\\n' '{safe}' >&2\nexit 1\n"


# ------------------------------------------------------------ install tokens

@dataclass(frozen=True)
class Install:
    id: uuid.UUID
    token: str
    command: str
    expires_at: dt.datetime


async def create_install(pool: asyncpg.Pool, *, project_id: uuid.UUID,
                         user_id: uuid.UUID, kind: str,
                         connect_url: str) -> Install:
    if kind != "server":
        raise ValueError("only servers connect this way for now")
    async with pool.acquire() as conn, conn.transaction():
        role = await conn.fetchval(
            "SELECT role FROM project_members WHERE project_id = $1 "
            "AND user_id = $2 AND (until IS NULL OR until > now())",
            project_id, user_id)
        if role not in ("owner", "admin"):
            raise PermissionError("only the owner and admins add machines")
        token = new_token()
        row = await conn.fetchrow(
            "INSERT INTO install_tokens (project_id, kind, token_hash, "
            "created_by, expires_at) VALUES ($1, $2, $3, $4, "
            "now() + make_interval(mins => $5)) RETURNING id, expires_at",
            project_id, kind, token_hash(token), user_id, INSTALL_MINUTES)
        await accounts.journal(conn, "install.command", project_id=project_id,
                               user_id=user_id, details={"kind": kind})
    return Install(row["id"], token, command(connect_url, token),
                   row["expires_at"])


async def install_status(pool: asyncpg.Pool, install_id: uuid.UUID,
                         user_id: uuid.UUID) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT t.state, t.error, t.expires_at, t.expires_at <= now() AS expired,
               m.id AS machine_id, m.name, m.host, m.passport, p.code
        FROM install_tokens t
        JOIN projects p ON p.id = t.project_id
        LEFT JOIN machines m ON m.id = t.machine_id
        WHERE t.id = $1 AND t.created_by = $2
        """, install_id, user_id)
    if row is None:
        return None
    state = row["state"]
    if state != "connected" and row["expired"]:
        state = "expired"
    out = {"state": state, "error": row["error"],
           "expiresAt": row["expires_at"].isoformat(), "machine": None}
    if row["machine_id"] is not None:
        pp = json.loads(row["passport"]) if isinstance(row["passport"], str) \
            else (row["passport"] or {})
        out["machine"] = {"id": str(row["machine_id"]), "name": row["name"],
                          "host": row["host"], "os": pp.get("os"),
                          "label": f"{row['code']}/{row['name']}"}
    return out


class EnrollRefused(Exception):
    """The installer's post was refused; status and message for the script."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def claim(pool: asyncpg.Pool, token: str) -> asyncpg.Record:
    """Takes the token for one attempt, or says why it cannot be used."""
    if not TOKEN_RE.match(token):
        raise EnrollRefused(404, "This command is not valid. Get a new one in tTerm Web.")
    # 'connecting' for minutes means the attempt died with the core: the
    # command is usable again rather than stuck until it expires.
    row = await pool.fetchrow(
        """
        UPDATE install_tokens SET state = 'connecting', error = NULL,
                                  updated_at = now()
        WHERE token_hash = $1 AND expires_at > now()
          AND (state IN ('waiting', 'failed')
               OR (state = 'connecting'
                   AND updated_at < now() - interval '2 minutes'))
        RETURNING id, project_id, created_by, kind
        """, token_hash(token))
    if row is not None:
        return row
    why = await pool.fetchrow(
        "SELECT state, expires_at <= now() AS expired FROM install_tokens "
        "WHERE token_hash = $1", token_hash(token))
    if why is None:
        raise EnrollRefused(404, "This command is not valid. Get a new one in tTerm Web.")
    if why["state"] == "connected":
        raise EnrollRefused(409, "This command was already used. For another "
                                 "machine, get a new command in tTerm Web.")
    if why["state"] == "connecting":
        raise EnrollRefused(409, "This command is being used right now, on "
                                 "this or another server. Wait a minute.")
    raise EnrollRefused(410, "This command has expired: it works for 10 "
                             "minutes. Get a new one in tTerm Web.")


async def check_token(pool: asyncpg.Pool, token: str) -> str | None:
    """None if the command can still work; otherwise why not."""
    if not TOKEN_RE.match(token):
        return "This command is not valid. Get a new one in tTerm Web."
    row = await pool.fetchrow(
        "SELECT state, expires_at <= now() AS expired FROM install_tokens "
        "WHERE token_hash = $1", token_hash(token))
    if row is None:
        return "This command is not valid. Get a new one in tTerm Web."
    if row["state"] == "connected":
        return ("This command was already used. For another machine, get a "
                "new command in tTerm Web.")
    if row["expired"]:
        return ("This command has expired: it works for 10 minutes. Get a "
                "new one in tTerm Web.")
    return None


def clean_host_keys(keys: list[str]) -> list[str]:
    """The keys that parse, each as "type base64", in the order given."""
    out: list[str] = []
    for key in keys:
        parts = key.split()
        if len(parts) < 2:
            continue
        candidate = f"{parts[0]} {parts[1]}"
        try:
            asyncssh.import_public_key(candidate)
        except Exception:
            continue
        if candidate not in out:
            out.append(candidate)
    return out


@dataclass
class Enrollment:
    token: str
    hostname: str
    os: str
    ssh_port: int
    ssh_user: str
    host_keys: list[str]
    ip: str


async def remember_cas(pool: asyncpg.Pool, cas: list[dict]) -> None:
    """Copies the signer's CA public keys into the database: machines refer
    to the CA they trust. A known id with a different key is an alarm, not
    something to overwrite."""
    async with pool.acquire() as conn:
        for ca in cas:
            stored = await conn.fetchval(
                "INSERT INTO cas (id, public_key) VALUES ($1, $2) "
                "ON CONFLICT (id) DO UPDATE SET id = cas.id "
                "RETURNING public_key", ca["id"], ca["public_key"])
            if stored.split()[:2] != ca["public_key"].split()[:2]:
                raise RuntimeError(
                    f"CA {ca['id']} in the database has a different key than "
                    "the signer holds. Refusing to go on.")


async def enroll(pool: asyncpg.Pool, signer: SignerClient, e: Enrollment,
                 ca: dict) -> str:
    """Registers a server; returns the line the installer prints.

    Raises EnrollRefused with a message for the installer when it cannot.
    """
    ca_id = ca["id"]
    await remember_cas(pool, [ca])
    # Only keys that are keys, as "type base64", without comments.
    e.host_keys = clean_host_keys(e.host_keys)
    claimed = await claim(pool, e.token)
    install_id = claimed["id"]
    owner = await accounts.person(pool, claimed["created_by"])
    project_id = claimed["project_id"]
    name = machine_name(e.hostname) if e.hostname else "server"
    failure = "The check was interrupted. Run the command again."
    try:
        if e.ssh_user != LOGIN:
            raise EnrollRefused(400, f"Unexpected user {e.ssh_user!r}.")
        try:
            details = await asyncio.wait_for(
                _check_server(signer, e, ca_id, owner, name), CHECK_TIMEOUT)
        except (EnrollRefused, asyncio.CancelledError):
            raise
        except Exception as exc:
            reason = ssh.explain(exc, e.ip, e.ssh_port)
            log.info("server at %s:%s did not pass the check: %s",
                     e.ip, e.ssh_port, reason)
            raise EnrollRefused(502, reason) from None
        failure = ""
    except EnrollRefused as refused:
        failure = str(refused)
        raise
    finally:
        if failure:
            # The same command works again after a failure, until it expires.
            await pool.execute(
                "UPDATE install_tokens SET state = 'failed', error = $2, "
                "updated_at = now() WHERE id = $1", install_id, failure)

    async with pool.acquire() as conn, conn.transaction():
        # One registration at a time per project, so that the same server
        # run twice at once still ends up as one machine.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           str(project_id))
        machine = await conn.fetchrow(
            "SELECT id, name FROM machines WHERE project_id = $1 AND "
            "removed_at IS NULL AND host_keys && $2::text[]",
            project_id, e.host_keys)
        if machine is not None:
            machine_id, final = machine["id"], machine["name"]
            await conn.execute(
                """
                UPDATE machines SET host = $2, port = $3, login = $4,
                       host_keys = $5, ca_id = $6, passport = $7::jsonb,
                       last_seen_at = now()
                WHERE id = $1
                """, machine_id, e.ip, e.ssh_port, LOGIN, e.host_keys, ca_id,
                json.dumps(details))
            kind = "machine.reconnected"
        else:
            taken = {r["name"] for r in await conn.fetch(
                "SELECT name FROM machines WHERE project_id = $1 AND "
                "removed_at IS NULL", project_id)}
            final = free_name(name, taken)
            machine_id = await conn.fetchval(
                """
                INSERT INTO machines (project_id, kind, name, host, port, login,
                                      host_keys, ca_id, passport, created_by,
                                      last_seen_at)
                VALUES ($1, 'server', $2, $3, $4, $5, $6, $7, $8::jsonb, $9, now())
                RETURNING id
                """, project_id, final, e.ip, e.ssh_port, LOGIN, e.host_keys,
                ca_id, json.dumps(details), owner.id)
            kind = "machine.added"
        await conn.execute(
            "UPDATE install_tokens SET state = 'connected', used_at = now(), "
            "machine_id = $2, updated_at = now() WHERE id = $1",
            install_id, machine_id)
        await accounts.journal(conn, kind, project_id=project_id,
                               user_id=owner.id, machine_id=machine_id,
                               details={"name": final, "host": e.ip,
                                        "port": e.ssh_port,
                                        "os": details.get("os")})
    log.info("%s: %s at %s:%s", kind, final, e.ip, e.ssh_port)
    return f"{final} is connected. Go back to tTerm Web."


async def _check_server(signer: SignerClient, e: Enrollment, ca_id: str,
                        owner: accounts.Person, name: str) -> dict:
    """Signs in once, the way terminals will, and reads the passport."""
    conn = await ssh.connect(signer, host=e.ip, port=e.ssh_port, login=LOGIN,
                             host_keys=e.host_keys, ca=ca_id,
                             who=ssh.key_id(owner.name, str(owner.id), name))
    try:
        result = await conn.run(passport.SCRIPT, check=False, timeout=20)
    finally:
        conn.close()
    output = result.stdout if isinstance(result.stdout, str) else ""
    details = passport.parse(output)
    details["checked_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    if e.os and "os" not in details:
        details["os"] = e.os[:200]
    return details


async def machine_details(pool: asyncpg.Pool, user_id: uuid.UUID,
                          machine_id: uuid.UUID) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT m.id, m.name, m.kind, m.host, m.port, m.passport, m.created_at,
               m.last_seen_at, p.code, p.name AS project
        FROM machines m
        JOIN projects p ON p.id = m.project_id
        JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = $1
        WHERE m.id = $2 AND m.removed_at IS NULL
        """, user_id, machine_id)
    if row is None:
        return None
    pp = json.loads(row["passport"]) if isinstance(row["passport"], str) \
        else (row["passport"] or {})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"],
            "host": row["host"], "port": row["port"],
            "label": f"{row['code']}/{row['name']}", "project": row["project"],
            "passport": passport.summary(pp),
            "addedAt": row["created_at"].isoformat()}
