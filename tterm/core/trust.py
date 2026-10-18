"""tTerm Web's lines in a server's authorized_keys, from 0.0.12.

Every server in tTerm Web has a principal of its own, m-<uuid>, and the
line its command puts in ~tterm/.ssh/authorized_keys trusts tTerm Web's CA
only for certificates made out to that principal:

    cert-authority,principals="m-…" ssh-ed25519 AAAA… tterm-web ca-…

The core asks the signer for the principal of the server it signs in to, so
a certificate made for one server is of no use on any other: not even a
fresh server's check, which takes the principal of its command, signs in
where that command did not run (the security audit of 18.10). The same
server in two projects is two machines, with a line each.

A server connected before 0.0.12 has a line without principals=, which
takes any certificate of the CA for the user tterm. The core signs in there
once more that way, puts in a line of its own for each machine that is this
server, in any project, and takes the old line out (adopt): from then on
the machines have own_line.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import re
import shlex
import uuid
from dataclasses import dataclass

import asyncpg
import asyncssh

from . import ssh
from .signer_client import SignerClient

log = logging.getLogger("tterm.trust")

#: The user the installer makes on a server, and so also the principal that
#: the old lines take.
LOGIN = "tterm"
PRINCIPAL_RE = re.compile(r"m-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                          r"[0-9a-f]{4}-[0-9a-f]{12}")
CA_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
#: What the script prints when the file is in place.
DONE = "tterm-web-lines-ok"
#: Seconds for the script that changes the lines, and for all of taking a
#: removed server's line out, signing in included: the person waits.
SCRIPT_TIMEOUT = 15
TAKE_BACK_TIMEOUT = 10


class LinesNotChanged(Exception):
    """The script on the server did not finish; the message says why."""


@dataclass(frozen=True)
class Server:
    """A server machine, as the core signs in to it."""
    id: uuid.UUID
    label: str
    host: str
    port: int
    host_keys: list[str]
    ca_id: str
    principal: str | None
    #: Its own line is on the server; otherwise the old one, from before
    #: 0.0.12.
    own_line: bool
    login: str = LOGIN


def new_principal() -> str:
    return f"m-{uuid.uuid4()}"


def line(principal: str, ca_key: str, ca_id: str) -> str:
    """The line that trusts the CA for this one principal."""
    if not PRINCIPAL_RE.fullmatch(principal) or not CA_ID_RE.fullmatch(ca_id):
        raise ValueError("not a principal or a CA of ours")
    key = " ".join(ca_key.split()[:2])
    return f'cert-authority,principals="{principal}" {key} tterm-web {ca_id}'


def script(*, ca_key: str, ca_id: str, add: list[str],
           drop: list[str]) -> str:
    """The script the core runs on a server, as tterm: a line for each
    principal of `add` that has none, the lines of `drop` out, and the old
    line of this CA out. Every other line stays as it was, the bot's and the
    person's own keys included. The file is written anew next to the old
    one and moved into its place, so sshd never reads half of it."""
    for principal in add + drop:
        if not PRINCIPAL_RE.fullmatch(principal):
            raise ValueError(f"not a principal: {principal!r}")
    key = " ".join(ca_key.split()[:2])
    # The old line of this CA, from before 0.0.12, starts so.
    old = "cert-authority " + key + " "
    adds = "".join(
        "grep -qF " + shlex.quote(f'cert-authority,principals="{p}" ')
        + ' "$t" || printf \'%s\\n\' ' + shlex.quote(line(p, ca_key, ca_id))
        + ' >> "$t"\n' for p in add)
    return ("set -eu\n"
            'f="$HOME/.ssh/authorized_keys"\n'
            't="$HOME/.ssh/authorized_keys.tterm-web.$$"\n'
            "umask 077\n"
            'mkdir -p "$HOME/.ssh"\n'
            '[ -f "$f" ] || : > "$f"\n'
            f"awk -v old={shlex.quote(old)} -v drop={shlex.quote(' '.join(drop))} '\n"
            'BEGIN { n = split(drop, gone, " ") }\n'
            "index($0, old) == 1 { next }\n"
            "{\n"
            "  for (i = 1; i <= n; i++)\n"
            '    if (index($0, "cert-authority,principals=\\"" gone[i] "\\" ") == 1) next\n'
            "  print\n"
            """}' "$f" > "$t"\n"""
            + adds +
            'chmod 600 "$t"\n'
            'mv -f "$t" "$f"\n'
            f"echo {DONE}\n")


# One change at a time per server: two at once could each write the file
# from what it read before the other.
_locks: dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)


def proven_key(conn: asyncssh.SSHClientConnection) -> str | None:
    """The host key the server proved it holds in this connection, as
    "type base64": the server is the machines whose host keys have it. The
    others a server's installer reported are only its word."""
    key = conn.get_server_host_key()
    if key is None:
        return None
    return " ".join(key.export_public_key().decode().split()[:2])


async def _connect(signer: SignerClient, server: Server, who: str,
                   principal: str) -> asyncssh.SSHClientConnection:
    return await ssh.connect(signer, host=server.host, port=server.port,
                             login=server.login, host_keys=server.host_keys,
                             ca=server.ca_id, who=who, principal=principal)


async def _come_in(signer: SignerClient, server: Server,
                   who: str) -> asyncssh.SSHClientConnection:
    """With a certificate for this server alone; to a server that has the
    old line still, the old way."""
    if server.own_line and server.principal:
        return await _connect(signer, server, who, server.principal)
    try:
        return await _connect(signer, server, who, LOGIN)
    except asyncssh.PermissionDenied:
        # The old line is gone already, its own may be there: two terminals
        # opened at once, or a change the database did not hear of.
        if not server.principal:
            raise
        return await _connect(signer, server, who, server.principal)


async def sign_in(pool: asyncpg.Pool, signer: SignerClient, server: Server,
                  who: str) -> asyncssh.SSHClientConnection:
    """Signs in with a certificate for this server alone. A server that has
    the old line still is signed in to the old way, once: then the core
    puts in its own lines, and the old one goes."""
    conn = await _come_in(signer, server, who)
    if not server.own_line:
        try:
            await adopt(pool, conn, ca_id=server.ca_id)
        except Exception as exc:   # noqa: BLE001 - the terminal opens anyway
            log.warning("could not give %s a line of its own: %s",
                        server.label, exc)
    return conn


async def adopt(pool: asyncpg.Pool, conn: asyncssh.SSHClientConnection, *,
                ca_id: str, exclude: list[uuid.UUID] | None = None,
                drop: list[str] | None = None) -> list[uuid.UUID]:
    """While signed in to a server: a line of its own for each machine that
    is this server and goes by the old line of this CA, still in tTerm Web,
    in any project (but those of `exclude`); then the old line out, and the
    lines of the principals in `drop`. The machines that got their line."""
    key = proven_key(conn)
    if key is None:
        raise LinesNotChanged("the server's host key is not known")
    async with _locks[f"{ca_id} {key}"]:
        ca_key = await pool.fetchval("SELECT public_key FROM cas WHERE id = $1",
                                     ca_id)
        if ca_key is None:
            raise LinesNotChanged(f"no CA {ca_id} in the database")
        # A machine of the old kind without a principal (made by hand) gets
        # one here, in the same breath.
        rows = await pool.fetch(
            """
            UPDATE machines
            SET principal = COALESCE(principal, 'm-' || gen_random_uuid())
            WHERE kind = 'server' AND removed_at IS NULL AND NOT own_line
              AND ca_id = $1 AND $2 = ANY(host_keys)
              AND NOT (id = ANY($3::uuid[]))
            RETURNING id, principal
            """, ca_id, key, list(exclude or []))
        text = script(ca_key=ca_key, ca_id=ca_id,
                      add=[r["principal"] for r in rows],
                      drop=[p for p in (drop or []) if p])
        # Read by sh from its input, whatever tterm's login shell is.
        result = await conn.run("sh -s", input=text, check=False,
                                timeout=SCRIPT_TIMEOUT)
        out = result.stdout if isinstance(result.stdout, str) else ""
        if result.exit_status != 0 or DONE not in out:
            err = result.stderr if isinstance(result.stderr, str) else ""
            raise LinesNotChanged((err.strip() or out.strip()
                                   or f"exit {result.exit_status}")[-300:])
        ids = [r["id"] for r in rows]
        if ids:
            await pool.execute("UPDATE machines SET own_line = true "
                               "WHERE id = ANY($1::uuid[])", ids)
            log.info("own lines on the server of %d machine(s) of CA %s",
                     len(ids), ca_id)
        return ids


async def take_back(pool: asyncpg.Pool, signer: SignerClient, server: Server,
                    who: str) -> bool:
    """A server that was removed: its line comes out, from the server
    itself, if the server answers soon. True if it did."""
    async def go() -> None:
        conn = await _come_in(signer, server, who)
        try:
            await adopt(pool, conn, ca_id=server.ca_id, exclude=[server.id],
                        drop=[server.principal] if server.principal else [])
        finally:
            conn.close()
    try:
        await asyncio.wait_for(go(), TAKE_BACK_TIMEOUT)
    except Exception as exc:       # noqa: BLE001 - the command is the way then
        log.info("could not take %s's line out: %s", server.label,
                 exc if isinstance(exc, LinesNotChanged)
                 else ssh.explain(exc, server.host, server.port))
        return False
    return True
