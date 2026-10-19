"""Machines: the one-command install, registration and the server passport.

How a server joins:

1. The person asks for a command. The core makes a one-time token that
   works for ten minutes and keeps only its hash.
2. On the server, `curl …/s/TOKEN | sudo sh` fetches the installer. It
   creates the user tterm, trusts our CA for the command's own principal
   (trust.py) and posts back the server's host keys, hostname and SSH port.
3. The core signs in over SSH to the address the post came from, with a
   fresh certificate for that principal and with the host keys just
   received pinned: only a server where the command ran takes it. If that
   works, it reads the passport and the machine is in. If not, the person
   sees why, and the same command can be run again until it expires.

A computer joins through its agent instead (from 0.0.6): the command starts
the agent with a token, and the agent's connection does the rest, see
agents.py. Started by hand, the token then stays the computer's key, so it
is longer. From 0.0.8 the command is an installer, `curl …/c/TOKEN | sh`: it
puts the agent in ~/.tterm-web with autostart and makes the computer a key
of its own, which never leaves it; the token only lets it join, once.
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

from . import accounts, passport, ssh, trust
from .signer_client import SignerClient

log = logging.getLogger("tterm.machines")

INSTALL_MINUTES = 10
#: Twelve characters for a server's command, thirty-two for a computer's,
#: whose token stays its key.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{12,64}$")
#: A key a computer made for itself (from tTerm Web's installer): as long as
#: the ones tTerm Web hands out, never shorter.
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
#: A CA's name as the signer makes them, ca-20261012-ffcd: safe to put in a
#: command for a person to run.
CA_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LOGIN = trust.LOGIN
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "install.sh"
COMPUTER_TEMPLATE = (Path(__file__).resolve().parent.parent / "templates"
                     / "install_computer.sh")
#: Where a computer's installer takes the agent from, unless TTERM_SRC says
#: otherwise when it runs: the released one, as the bot's installer does.
AGENT_SOURCE = "https://raw.githubusercontent.com/tterm-net/tterm-agent/main/agent.py"
#: Everything the core may spend on checking a fresh server.
CHECK_TIMEOUT = 40


class LimitReached(PermissionError):
    """A limit of the plan; the message says which, and what to do."""


async def room_problem(pool: asyncpg.Pool | asyncpg.Connection,
                       project_id: uuid.UUID) -> str | None:
    """Why no machine can be added to the project now, or None. The machines
    of all the projects of its owner count, up to what the owner's plan
    allows ("machines" in its limits)."""
    owner_id = await pool.fetchval("SELECT owner_id FROM projects WHERE id = $1",
                                   project_id)
    if owner_id is None:
        return None
    plan, most = await accounts.limit(pool, owner_id, "machines")
    if most is None:
        return None
    count = await pool.fetchval(
        """
        SELECT count(*) FROM machines m JOIN projects p ON p.id = m.project_id
        WHERE p.owner_id = $1 AND m.removed_at IS NULL
        """, owner_id)
    if count < most:
        return None
    # Said to whoever adds the machine, who may be an admin of someone
    # else's project: the owner's machines are what counts.
    return (f"The {plan} plan allows {most} machines, all in use. Remove "
            "one in tTerm Web to add another.")


async def _make_room(conn: asyncpg.Connection, project_id: uuid.UUID) -> None:
    """In the transaction that adds a machine: one at a time per owner, and
    only while there is room. The project's row is held meanwhile: a
    project is not deleted with a machine coming in (projects.delete), and
    one deleted already takes none."""
    project = await conn.fetchrow(
        "SELECT owner_id, deleted_at FROM projects WHERE id = $1 FOR SHARE",
        project_id)
    if project is None or project["deleted_at"] is not None:
        raise LimitReached("The project was deleted in tTerm Web meanwhile.")
    owner_id = project["owner_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                       f"machines-of:{owner_id}")
    problem = await room_problem(conn, project_id)
    if problem is not None:
        raise LimitReached(problem)


def new_token(kind: str = "server") -> str:
    """For a server, twelve characters, 72 random bits: short enough to
    read, far too many to guess in the ten minutes the command works. For a
    computer the token stays its key for good, so 192 bits.

    Never one that starts with "-": pasted after an option, as in
    `--token -x…`, it would read as another option."""
    while True:
        token = secrets.token_urlsafe(24 if kind == "computer" else 9)
        if not token.startswith("-"):
            return token


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def command(connect_url: str, token: str) -> str:
    return f"curl -sSL {connect_url}/s/{token} | sudo sh"


def hub_url(connect_url: str) -> str:
    """Where agents connect: the machines' side, over a WebSocket."""
    if connect_url.startswith("https://"):
        return "wss://" + connect_url[len("https://"):] + "/agent"
    if connect_url.startswith("http://"):
        return "ws://" + connect_url[len("http://"):] + "/agent"
    return connect_url + "/agent"


def agent_command(connect_url: str, token: str) -> str:
    """The agent started by hand, in a copy of tterm-agent 0.11.0 or newer:
    for trying a new agent before it is released. The token then stays the
    computer's key.

    The token goes in the environment: on the command line any user of the
    computer could read it in the list of processes."""
    return f"TTERM_TOKEN={token} python3 agent.py --hub {hub_url(connect_url)}"


def computer_command(connect_url: str, token: str) -> str:
    """What a computer runs, as its person, not root: the installer puts the
    agent in ~/.tterm-web with autostart, next to the bot's agent in
    ~/.tterm if there is one and without touching it."""
    return f"curl -fsSL {connect_url}/c/{token} | sh"


def render_computer_installer(*, token: str, connect_url: str) -> str:
    if not TOKEN_RE.match(token):
        raise ValueError("bad token")
    return (COMPUTER_TEMPLATE.read_text(encoding="utf-8")
            .replace("{{HUB}}", hub_url(connect_url))
            .replace("{{SRC}}", AGENT_SOURCE)
            .replace("{{TOKEN}}", token))


def server_cleanup(principal: str | None, ca_id: str | None = None,
                   own_line: bool = True) -> str | None:
    """The command that takes tTerm Web's access back on a server that was
    removed, when tTerm Web could not do it there itself: the server's line
    in tterm's authorized_keys, by its principal. For a server that still
    has the line from before 0.0.12, that line of its CA, which has no
    principal; the lines of the same server in other projects stay. The
    user and the sudo rule stay too, the bot may use them; the installer's
    header says how to remove those."""
    if own_line:
        if not principal or not trust.PRINCIPAL_RE.fullmatch(principal):
            return None
        return (f"sudo sed -i '/^cert-authority,principals=\"{principal}\" /d' "
                f"~{LOGIN}/.ssh/authorized_keys")
    if not ca_id or not CA_ID_RE.match(ca_id):
        return None
    return (f"sudo sed -i '/^cert-authority [^,]* tterm-web {ca_id}$/d' "
            f"~{LOGIN}/.ssh/authorized_keys")


def machine_name(hostname: str, default: str = "server") -> str:
    """The short hostname, cleaned up: web-01.example.com → web-01."""
    short = hostname.strip().split(".")[0]
    short = re.sub(r"[^A-Za-z0-9_-]+", "-", short).strip("-")[:40]
    return short or default


def free_name(wanted: str, taken: set[str]) -> str:
    lower = {t.lower() for t in taken}
    if wanted.lower() not in lower:
        return wanted
    for n in range(2, 1000):
        candidate = f"{wanted[:36]}-{n}"
        if candidate.lower() not in lower:
            return candidate
    return f"{wanted[:24]}-{secrets.token_hex(4)}"


def render_installer(*, token: str, ca_key: str, ca_id: str, principal: str,
                     connect_url: str) -> str:
    if not TOKEN_RE.match(token):
        raise ValueError("bad token")
    line = trust.line(principal, ca_key, ca_id)  # checks both, too
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{SSH_USER}}", LOGIN)
            .replace("{{CA_ID}}", ca_id)
            .replace("{{PRINCIPAL}}", principal)
            .replace("{{LINE}}", line)
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
    #: For a computer: the agent started by hand with the same token.
    by_hand: str | None = None


async def create_install(pool: asyncpg.Pool, *, project_id: uuid.UUID,
                         user_id: uuid.UUID, kind: str,
                         connect_url: str) -> Install:
    if kind not in ("server", "computer"):
        raise ValueError("a machine is a server or a computer")
    async with pool.acquire() as conn, conn.transaction():
        role = await conn.fetchval(
            "SELECT role FROM project_members WHERE project_id = $1 "
            "AND user_id = $2 AND (until IS NULL OR until > now())",
            project_id, user_id)
        if role not in ("owner", "admin"):
            raise PermissionError("only the owner and admins add machines")
        problem = await room_problem(conn, project_id)
        if problem is not None:
            raise LimitReached(problem)
        token = new_token(kind)
        row = await conn.fetchrow(
            "INSERT INTO install_tokens (project_id, kind, token_hash, "
            "created_by, expires_at, principal) VALUES ($1, $2, $3, $4, "
            "now() + make_interval(mins => $5), $6) RETURNING id, expires_at",
            project_id, kind, token_hash(token), user_id, INSTALL_MINUTES,
            trust.new_principal() if kind == "server" else None)
        await accounts.journal(conn, "install.command", project_id=project_id,
                               user_id=user_id, details={"kind": kind})
    if kind == "computer":
        return Install(row["id"], token, computer_command(connect_url, token),
                       row["expires_at"], agent_command(connect_url, token))
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
        RETURNING id, project_id, created_by, kind, principal
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
                                 "this or another machine. Wait a minute.")
    raise EnrollRefused(410, "This command has expired: it works for 10 "
                             "minutes. Get a new one in tTerm Web.")


async def check_token(pool: asyncpg.Pool, token: str,
                      kind: str = "server") -> str | None:
    """None if the command can still work for this kind of machine;
    otherwise why not."""
    if not TOKEN_RE.match(token):
        return "This command is not valid. Get a new one in tTerm Web."
    row = await pool.fetchrow(
        "SELECT state, kind, expires_at <= now() AS expired FROM install_tokens "
        "WHERE token_hash = $1", token_hash(token))
    if row is None:
        return "This command is not valid. Get a new one in tTerm Web."
    if row["kind"] != kind:
        if kind == "server":
            return ("This command is for a computer: run it there, as "
                    "yourself, without sudo.")
        return ("This command is for a server: it runs there with sudo. For "
                "this computer, choose Computer under Add a machine.")
    if row["state"] == "connected":
        return ("This command was already used. For another machine, get a "
                "new command in tTerm Web.")
    if row["expired"]:
        return ("This command has expired: it works for 10 minutes. Get a "
                "new one in tTerm Web.")
    return None


async def install_principal(pool: asyncpg.Pool, token: str) -> str:
    """The principal a server's command puts in its line."""
    return await pool.fetchval(
        "UPDATE install_tokens SET principal = COALESCE(principal, $2) "
        "WHERE token_hash = $1 RETURNING principal",
        token_hash(token), trust.new_principal())


async def fail_install(pool: asyncpg.Pool, install_id: uuid.UUID,
                       error: str) -> None:
    """The attempt failed; the same command works again until it expires."""
    await pool.execute(
        "UPDATE install_tokens SET state = 'failed', error = $2, "
        "updated_at = now() WHERE id = $1", install_id, error[:500])


async def add_computer(pool: asyncpg.Pool, claimed: asyncpg.Record,
                       reported: str, details: dict,
                       key: str | None = None) -> tuple[uuid.UUID, str]:
    """Registers a computer whose agent came with a fresh token. Its key is
    the one it made for itself, if it brought one (tTerm Web's installer);
    otherwise the token becomes its key, as with an agent started by hand.

    Always a new machine, even when one of the same name is there: a name
    is whatever the computer calls itself, and another computer could call
    itself the same and so take over that machine's place, its terminals
    and its history. The old one, if it was this computer, is removed with
    Remove machine."""
    project_id = claimed["project_id"]
    owner_id = claimed["created_by"]
    token_id = claimed["id"]
    name = machine_name(reported, default="computer")
    async with pool.acquire() as conn, conn.transaction():
        # The project first, then its owner: as a server's registration does.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           str(project_id))
        await _make_room(conn, project_id)
        key = token_hash(key) if key else await conn.fetchval(
            "SELECT token_hash FROM install_tokens WHERE id = $1", token_id)
        taken = {r["name"] for r in await conn.fetch(
            "SELECT name FROM machines WHERE project_id = $1 AND "
            "removed_at IS NULL", project_id)}
        final = free_name(name, taken)
        machine_id = await conn.fetchval(
            """
            INSERT INTO machines (project_id, kind, name, agent_token_hash,
                                  passport, created_by, last_seen_at)
            VALUES ($1, 'computer', $2, $3, $4::jsonb, $5, now())
            RETURNING id
            """, project_id, final, key, json.dumps(details), owner_id)
        await conn.execute(
            "UPDATE install_tokens SET state = 'connected', used_at = now(), "
            "machine_id = $2, updated_at = now() WHERE id = $1",
            token_id, machine_id)
        await accounts.journal(conn, "machine.added", project_id=project_id,
                               user_id=owner_id, machine_id=machine_id,
                               details={"name": final, "kind": "computer",
                                        "os": details.get("os"),
                                        "agent": details.get("agent")})
    log.info("machine.added: computer %s", final)
    return machine_id, final


async def confirm_install(pool: asyncpg.Pool, token: str,
                          machine_id: uuid.UUID) -> bool:
    """The command from Add a machine, run on a computer that is in tTerm Web
    already: the installer kept the computer's key and updated its agent.
    The command counts as used by this computer, so that the dialog says it
    is connected. Only a live command of the same project; True if it was
    one. A command of another project fails, saying where the computer is:
    a computer is in one project at a time."""
    if not TOKEN_RE.match(token):
        return False
    async with pool.acquire() as conn, conn.transaction():
        elsewhere = await conn.fetchrow(
            """
            SELECT t.id
            FROM install_tokens t, machines m
            WHERE t.token_hash = $1 AND t.kind = 'computer'
              AND t.expires_at > now() AND t.state IN ('waiting', 'failed')
              AND m.id = $2 AND m.project_id <> t.project_id
            """, token_hash(token), machine_id)
        if elsewhere is not None:
            await conn.execute(
                "UPDATE install_tokens SET state = 'failed', error = $2, "
                "updated_at = now() WHERE id = $1", elsewhere["id"],
                "This computer is in tTerm Web already, in another project. "
                "Remove it there first, or run ~/.tterm-web/uninstall.sh on it "
                "and then this command.")
            return False
        row = await conn.fetchrow(
            """
            UPDATE install_tokens t
            SET state = 'connected', used_at = now(), machine_id = $2,
                error = NULL, updated_at = now()
            FROM machines m
            WHERE t.token_hash = $1 AND t.kind = 'computer'
              AND t.expires_at > now() AND t.state IN ('waiting', 'failed')
              AND m.id = $2 AND m.project_id = t.project_id
              AND m.removed_at IS NULL
            RETURNING t.project_id, t.created_by, m.name
            """, token_hash(token), machine_id)
        if row is None:
            return False
        await accounts.journal(conn, "machine.agent_installed",
                               project_id=row["project_id"],
                               user_id=row["created_by"], machine_id=machine_id,
                               details={"name": row["name"]})
    log.info("machine.agent_installed: %s", row["name"])
    return True


@dataclass
class Removal:
    """A machine just taken out of tTerm Web."""
    #: What the page is told: id, name, kind.
    answer: dict
    #: A server: how to sign in there and take its line out (take_back).
    server: trust.Server | None = None
    #: The command that does it on the server itself, if tTerm Web cannot.
    cleanup: str | None = None
    #: A server with the line from before 0.0.12, which the same server in
    #: another project still goes by: the line stays.
    shared: bool = False


async def remove(pool: asyncpg.Pool, user_id: uuid.UUID,
                 machine_id: uuid.UUID) -> Removal | None:
    """Takes a machine out of tTerm Web: it is no longer listed, and a
    computer's key stops working for good. Its terminals' blocks stay, so
    does the history. None if there is no such machine for this person;
    only the owner and admins may remove one. A server's line on the server
    itself is take_back's."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT m.id, m.name, m.kind, m.project_id, m.ca_id, m.host_keys,
                   m.host, m.port, m.login, m.principal, m.own_line, p.code,
                   pm.role
            FROM machines m
            JOIN projects p ON p.id = m.project_id
            JOIN project_members pm ON pm.project_id = m.project_id
             AND pm.user_id = $2 AND (pm.until IS NULL OR pm.until > now())
            WHERE m.id = $1 AND m.removed_at IS NULL
            FOR UPDATE OF m
            """, machine_id, user_id)
        if row is None:
            return None
        if row["role"] not in ("owner", "admin"):
            raise PermissionError("only the owner and admins remove machines")
        # A computer's key stays with the removed machine, so that it never
        # works again: an agent that comes back with it is told the computer
        # is not here any more, and stops; one that tries to join with it is
        # refused (see key_retired).
        await conn.execute(
            "UPDATE machines SET removed_at = now() WHERE id = $1", machine_id)
        # A server with a line of its own takes only that line along. The
        # line from before 0.0.12 is shared with the same server in another
        # project still on it: then it stays. Removals of such servers go
        # one at a time, so that the last one gives the command.
        if row["kind"] == "server" and row["ca_id"]:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                               f"server-ca:{row['ca_id']}")
        shared = (row["kind"] == "server" and not row["own_line"]
                  and await conn.fetchval(
                      "SELECT EXISTS (SELECT 1 FROM machines WHERE "
                      "removed_at IS NULL AND kind = 'server' AND NOT own_line "
                      "AND ca_id = $1 AND host_keys && $2::text[])",
                      row["ca_id"], row["host_keys"]))
        await accounts.journal(conn, "machine.removed",
                               project_id=row["project_id"], user_id=user_id,
                               machine_id=machine_id,
                               details={"name": row["name"], "kind": row["kind"]})
    log.info("machine.removed: %s %s", row["kind"], row["name"])
    removal = Removal({"id": str(machine_id), "name": row["name"],
                       "kind": row["kind"]})
    if row["kind"] == "server":
        removal.server = trust.Server(
            row["id"], f"{row['code']}/{row['name']}", row["host"], row["port"],
            list(row["host_keys"]), row["ca_id"], row["principal"],
            row["own_line"], row["login"] or LOGIN)
        removal.cleanup = server_cleanup(row["principal"], row["ca_id"],
                                         row["own_line"])
        removal.shared = bool(shared)
    return removal


async def take_back(pool: asyncpg.Pool, signer: SignerClient | None,
                    removal: Removal, who: str) -> dict:
    """After a server is removed, tTerm Web takes its line out of the
    server's authorized_keys itself, if the server answers within seconds
    (from 0.0.12). If not, the answer has the command that does it there,
    unless the line is shared. What the page is told."""
    out = dict(removal.answer)
    server = removal.server
    if server is None:
        return out
    if (signer is not None and server.host and server.port and server.ca_id
            and await trust.take_back(pool, signer, server, who)):
        out["accessRemoved"] = True
    elif removal.shared:
        out["shared"] = True
    else:
        out["cleanup"] = removal.cleanup
    return out


async def key_retired(pool: asyncpg.Pool, key: str) -> bool:
    """Whether a computer's own key belonged to a computer that was removed:
    such a key never works again, even if the removal was because it
    leaked."""
    return bool(await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM machines WHERE agent_token_hash = $1 "
        "AND removed_at IS NOT NULL)", token_hash(key)))


async def fail_token(pool: asyncpg.Pool, token: str, error: str) -> None:
    """A computer's command that cannot work, so that its dialog says why:
    by its token, while it is live."""
    await pool.execute(
        "UPDATE install_tokens SET state = 'failed', error = $2, "
        "updated_at = now() WHERE token_hash = $1 AND kind = 'computer' "
        "AND expires_at > now() AND state IN ('waiting', 'failed')",
        token_hash(token), error[:500])


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
    something to overwrite. A CA the signer retired is marked so."""
    async with pool.acquire() as conn:
        for ca in cas:
            stored = await conn.fetchval(
                "INSERT INTO cas (id, public_key, retired_at) VALUES ($1, $2, "
                "CASE WHEN $3::boolean THEN now() END) "
                "ON CONFLICT (id) DO UPDATE SET retired_at = CASE WHEN "
                "$3::boolean THEN COALESCE(cas.retired_at, now()) END "
                "RETURNING public_key", ca["id"], ca["public_key"],
                bool(ca.get("retired")))
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
    if claimed["kind"] != "server":
        await fail_install(pool, install_id, "This token is for a computer: "
                           "start the agent with it instead.")
        raise EnrollRefused(400, "This token is for a computer: it is used by "
                                 "the agent, not by this installer.")
    owner = await accounts.person(pool, claimed["created_by"])
    project_id = claimed["project_id"]
    principal = claimed["principal"] or await install_principal(pool, e.token)
    name = machine_name(e.hostname) if e.hostname else "server"
    failure = "The check was interrupted. Run the command again."
    try:
        if e.ssh_user != LOGIN:
            raise EnrollRefused(400, f"Unexpected user {e.ssh_user!r}.")
        # A server already here only connects again; a new one needs room.
        here = await pool.fetchval(
            "SELECT id FROM machines WHERE project_id = $1 AND "
            "removed_at IS NULL AND host_keys && $2::text[]",
            project_id, e.host_keys)
        problem = None if here else await room_problem(pool, project_id)
        if problem is not None:
            raise EnrollRefused(403, problem)
        try:
            conn, details = await asyncio.wait_for(
                _check_server(signer, e, ca_id, owner, name, principal),
                CHECK_TIMEOUT)
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

    try:
        try:
            message, replaced = await _register_server(
                pool, e, project_id, install_id, owner, name, ca_id, details,
                principal)
        except LimitReached as exc:
            # Another machine came in while this one was being checked.
            await fail_install(pool, install_id, str(exc))
            raise EnrollRefused(403, str(exc)) from None
        # The machine goes by this command's line now. Still signed in, the
        # lines are put in order (trust.adopt): the one it had before goes,
        # only now, so that it works until the database knows the new one;
        # the same server in other projects gets lines of its own if it
        # still had the old one, and the old one goes.
        try:
            await trust.adopt(pool, conn, ca_id=ca_id, drop=[replaced]
                              if replaced and replaced != principal else [])
        except Exception as exc:   # noqa: BLE001 - the server is in all the same
            log.warning("could not put the lines in order on %s: %s",
                        name, exc)
        return message
    finally:
        conn.close()


async def _register_server(pool: asyncpg.Pool, e: Enrollment,
                           project_id: uuid.UUID, install_id: uuid.UUID,
                           owner: accounts.Person, name: str, ca_id: str,
                           details: dict, principal: str
                           ) -> tuple[str, str | None]:
    """The machine in the database: the line for the installer, and the
    principal the machine had before if it was here already."""
    async with pool.acquire() as conn, conn.transaction():
        # One registration at a time per project, so that the same server
        # run twice at once still ends up as one machine.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           str(project_id))
        machine = await conn.fetchrow(
            "SELECT id, name, principal FROM machines WHERE project_id = $1 "
            "AND removed_at IS NULL AND host_keys && $2::text[]",
            project_id, e.host_keys)
        replaced = None
        if machine is not None:
            machine_id, final = machine["id"], machine["name"]
            replaced = machine["principal"]
            # The line of this command from now on.
            await conn.execute(
                """
                UPDATE machines SET host = $2, port = $3, login = $4,
                       host_keys = $5, ca_id = $6, passport = $7::jsonb,
                       principal = $8, own_line = true, last_seen_at = now()
                WHERE id = $1
                """, machine_id, e.ip, e.ssh_port, LOGIN, e.host_keys, ca_id,
                json.dumps(details), principal)
            kind = "machine.reconnected"
        else:
            await _make_room(conn, project_id)
            taken = {r["name"] for r in await conn.fetch(
                "SELECT name FROM machines WHERE project_id = $1 AND "
                "removed_at IS NULL", project_id)}
            final = free_name(name, taken)
            machine_id = await conn.fetchval(
                """
                INSERT INTO machines (project_id, kind, name, host, port, login,
                                      host_keys, ca_id, passport, created_by,
                                      last_seen_at, principal, own_line)
                VALUES ($1, 'server', $2, $3, $4, $5, $6, $7, $8::jsonb, $9,
                        now(), $10, true)
                RETURNING id
                """, project_id, final, e.ip, e.ssh_port, LOGIN, e.host_keys,
                ca_id, json.dumps(details), owner.id, principal)
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
    return f"{final} is connected. Go back to tTerm Web.", replaced


async def _check_server(signer: SignerClient, e: Enrollment, ca_id: str,
                        owner: accounts.Person, name: str, principal: str
                        ) -> tuple[asyncssh.SSHClientConnection, dict]:
    """Signs in once, the way terminals will, with a certificate for the
    command's principal: a server where the command did not run, even at
    the same address, refuses it. Reads the passport. The connection stays
    open for the lines to be put in order once the machine is in."""
    conn = await ssh.connect(signer, host=e.ip, port=e.ssh_port, login=LOGIN,
                             host_keys=e.host_keys, ca=ca_id,
                             principal=principal,
                             who=ssh.key_id(owner.name, str(owner.id), name))
    try:
        result = await conn.run(passport.SCRIPT, check=False, timeout=20)
    except BaseException:
        conn.close()
        raise
    output = result.stdout if isinstance(result.stdout, str) else ""
    details = passport.parse(output)
    details["checked_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    if e.os and "os" not in details:
        details["os"] = e.os[:200]
    return conn, details


async def machine_details(pool: asyncpg.Pool, user_id: uuid.UUID,
                          machine_id: uuid.UUID) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT m.id, m.name, m.kind, m.host, m.port, m.passport, m.created_at,
               m.last_seen_at, p.code, p.name AS project
        FROM machines m
        JOIN projects p ON p.id = m.project_id
        JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = $1
         AND (pm.until IS NULL OR pm.until > now())
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
