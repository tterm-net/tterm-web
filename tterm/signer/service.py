"""The signer's work: CA keys, certificates, limits, its own journal.

Talks over a Unix socket only, one JSON object per line each way:

    {"op": "keys"}
        → {"ok": true, "cas": [{"id": …, "public_key": …, "active": true,
                                "retired": false}]}
    {"op": "sign", "public_key": …, "principal": "m-<uuid>", "key_id": …,
     "ttl": 120, "ca": optional}
        → {"ok": true, "certificate": …, "serial": 7, "ca": …,
           "valid_before": unix time}
    {"op": "ping"} → {"ok": true}

Anything else gets {"ok": false, "error": …}.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import grp
import json
import logging
import os
import re
import secrets
import socket
import time
from collections import deque
from pathlib import Path

import asyncssh

log = logging.getLogger("tterm.signer")

#: How long a certificate lives by default, and at most: what the core asks
#: for. It is needed only at the moment of connecting: an open connection
#: outlives it.
DEFAULT_TTL = 120
MAX_TTL = 120
#: Slack for clocks that disagree a little.
CLOCK_SKEW = 60
#: Whom a certificate is for: one server in tTerm Web, by the principal of
#: its own that its line in authorized_keys names (from 0.0.12, the security
#: audit of 18.10), so that a certificate for one server is useless on any
#: other. Nothing else is signed, except for now the user tterm: the line a
#: server got before 0.0.12 takes only that, and the core signs in with it
#: once, to put in the server's own line. In 0.0.13 that goes too.
PRINCIPAL_RE = re.compile(r"^m-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                          r"[0-9a-f]{4}-[0-9a-f]{12}$")
OLD_PRINCIPAL = "tterm"
#: Signatures allowed per minute. A burst of terminals opening is fine; a
#: loop asking for thousands is not.
PER_MINUTE = 120
MAX_KEY_ID = 200
MAX_LINE = 64 * 1024


class Refused(Exception):
    """A request the signer will not do; the message says why."""


class Signer:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.cas: dict[str, asyncssh.SSHKey] = {}
        #: CAs that sign no more: their ids, from the file "retired".
        self.retired: set[str] = set()
        self.active: str | None = None
        self._recent: deque[float] = deque()

    # ------------------------------------------------------------- the keys

    def load_or_create(self) -> None:
        """Reads the CA keys; on the very first start makes the first one,
        and so it does when every CA there is has been retired.

        A CA is retired by its id in the file "retired", one per line, and a
        restart: it signs nothing from then on, not even for the servers
        that still trust it. For a key that may have leaked; those servers
        are then connected again, with the CA that signs now."""
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        cas_dir = self.dir / "cas"
        cas_dir.mkdir(exist_ok=True)
        os.chmod(cas_dir, 0o700)
        for path in sorted(cas_dir.glob("*.key")):
            self.cas[path.stem] = asyncssh.read_private_key(str(path))
        retired_file = self.dir / "retired"
        if retired_file.exists():
            self.retired = {line.split("#")[0].strip() for line in
                            retired_file.read_text().splitlines()} - {""}
        if not self.usable():
            self.create_ca()
        active_file = self.dir / "active"
        wanted = (active_file.read_text().strip()
                  if active_file.exists() else "")
        self.active = wanted if wanted in self.usable() else self.usable()[-1]

    def usable(self) -> list[str]:
        """The CAs that may sign, oldest first."""
        return sorted(ca_id for ca_id in self.cas if ca_id not in self.retired)

    def create_ca(self, ca_id: str | None = None) -> str:
        """A new CA, named by its date and a few random letters:
        ca-20261012-3f9a. The random part keeps a CA made again the same
        day, after its keys were deleted, from taking an old name."""
        cas_dir = self.dir / "cas"
        while True:
            candidate = ca_id or ("ca-" + dt.date.today().strftime("%Y%m%d")
                                  + "-" + secrets.token_hex(2))
            if not (cas_dir / f"{candidate}.key").exists():
                break
            if ca_id:
                raise Refused(f"CA {ca_id} already exists")
        ca_id = candidate
        key = asyncssh.generate_private_key("ssh-ed25519",
                                            comment=f"tterm-web {ca_id}")
        path = cas_dir / f"{ca_id}.key"
        # Created with no rights for anyone else, before the key is written.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.export_private_key())
        (cas_dir / f"{ca_id}.pub").write_bytes(key.export_public_key())
        self.cas[ca_id] = key
        self.journal({"event": "ca.created", "ca": ca_id,
                      "fingerprint": key.get_fingerprint()})
        log.info("created CA %s (%s)", ca_id, key.get_fingerprint())
        return ca_id

    def public_keys(self) -> list[dict]:
        return [{
            "id": ca_id,
            "public_key": key.export_public_key().decode().strip(),
            "fingerprint": key.get_fingerprint(),
            "active": ca_id == self.active,
            "retired": ca_id in self.retired,
        } for ca_id, key in sorted(self.cas.items())]

    # ------------------------------------------------------------- signing

    def sign(self, public_key: str, principal: str, key_id: str,
             ttl: int = DEFAULT_TTL, ca: str | None = None) -> dict:
        if not (PRINCIPAL_RE.fullmatch(principal) or principal == OLD_PRINCIPAL):
            raise Refused(f"principal {principal!r} is not one we sign for")
        if not isinstance(ttl, int) or not 1 <= ttl <= MAX_TTL:
            raise Refused(f"ttl must be 1 to {MAX_TTL} seconds")
        if not key_id or len(key_id) > MAX_KEY_ID or not key_id.isprintable():
            raise Refused("key_id must be printable, 1 to 200 characters")
        ca_id = ca or self.active
        if ca_id not in self.cas:
            raise Refused(f"no such CA: {ca_id!r}")
        if ca_id in self.retired:
            raise Refused(f"CA {ca_id} is retired: it signs nothing")
        try:
            user_key = asyncssh.import_public_key(public_key)
        except Exception:
            raise Refused("public_key is not an OpenSSH public key") from None
        self._limit()

        now = int(time.time())
        serial = self._next_serial()
        # The method belongs to the CA key and takes the key being certified
        # first. The other way round signs the CA key with the throwaway one,
        # and the server rejects it ("certificate key mismatch").
        cert = self.cas[ca_id].generate_user_certificate(
            user_key, key_id,
            serial=serial,
            principals=[principal],
            valid_after=now - CLOCK_SKEW,
            valid_before=now + ttl,
            # A shell and a terminal, nothing more.
            permit_pty=True,
            permit_agent_forwarding=False,
            permit_port_forwarding=False,
            permit_x11_forwarding=False,
            permit_user_rc=False,
        )
        self.journal({"event": "signed", "ca": ca_id, "serial": serial,
                      "principal": principal, "key_id": key_id,
                      "valid_before": now + ttl,
                      "key": user_key.get_fingerprint()})
        return {"certificate": cert.export_certificate().decode().strip(),
                "serial": serial, "ca": ca_id, "valid_before": now + ttl}

    def _limit(self) -> None:
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if len(self._recent) >= PER_MINUTE:
            raise Refused("too many certificates this minute, try again shortly")
        self._recent.append(now)

    def _next_serial(self) -> int:
        """Serials go up by one and survive restarts: sshd writes them into
        the server's own log next to the key id, so they should be readable."""
        path = self.dir / "serial"
        last = int(path.read_text().strip() or 0) if path.exists() else 0
        serial = last + 1
        tmp = path.with_suffix(".tmp")
        tmp.write_text(f"{serial}\n")
        os.replace(tmp, path)
        return serial

    def journal(self, entry: dict) -> None:
        """The signer's own journal: every key made and every certificate."""
        entry = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                 **entry}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        fd = os.open(self.dir / "journal.jsonl",
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    # ------------------------------------------------------------- talking

    def answer(self, raw: bytes) -> dict:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError
        except ValueError:
            return {"ok": False, "error": "not a JSON object"}
        op = request.get("op")
        try:
            if op == "ping":
                return {"ok": True}
            if op == "keys":
                return {"ok": True, "cas": self.public_keys()}
            if op == "sign":
                return {"ok": True, **self.sign(
                    str(request.get("public_key", "")),
                    str(request.get("principal", "")),
                    str(request.get("key_id", "")),
                    request.get("ttl", DEFAULT_TTL),
                    request.get("ca"))}
            return {"ok": False, "error": f"unknown op: {op!r}"}
        except Refused as exc:
            log.warning("refused: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\n")
                except asyncio.IncompleteReadError:
                    break
                except asyncio.LimitOverrunError:
                    writer.write(b'{"ok": false, "error": "line too long"}\n')
                    break
                writer.write(json.dumps(self.answer(raw)).encode() + b"\n")
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()


def socket_in_use(path: Path) -> bool:
    """True if another signer already answers on this socket."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1)
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def private_dir(directory: Path, group: str | None = None) -> None:
    """The socket's folder: made private if new, refused if someone else's.

    It may sit in a shared temporary directory, where another user could
    have made a folder of that name first to listen in. With `group`, as
    on the server, where the core runs as a user of its own, the members of
    that group may come in as well, and no one else.
    """
    gid = grp.getgrnam(group).gr_gid if group else None
    directory.mkdir(mode=0o750 if group else 0o700, parents=True, exist_ok=True)
    info = directory.stat()
    if gid is not None and info.st_uid == os.getuid() and info.st_gid != gid:
        os.chown(directory, -1, gid)
        info = directory.stat()
    # A group may look in and reach the socket, never put anything there.
    let_in = 0o050 if gid is not None else 0
    if (info.st_uid != os.getuid() or info.st_mode & 0o077 & ~let_in
            or (gid is not None and info.st_gid != gid)):
        raise RuntimeError(
            f"{directory} must belong to this user and be closed to others "
            f"({'chmod 750, group ' + group if group else 'chmod 700'}) "
            "before the signer will put its socket there")


async def serve(signer: Signer, path: Path,
                group: str | None = None) -> asyncio.AbstractServer:
    """Listens on the socket, readable and writable by this user only, and
    by the members of `group` if one is given."""
    private_dir(path.parent, group)
    if path.exists():
        if socket_in_use(path):
            raise RuntimeError(f"another signer is already running on {path}")
        path.unlink()
    old = os.umask(0o117 if group else 0o177)
    try:
        server = await asyncio.start_unix_server(signer.handle, path=str(path),
                                                 limit=MAX_LINE)
    finally:
        os.umask(old)
    if group and path.stat().st_gid != grp.getgrnam(group).gr_gid:
        os.chown(path, -1, grp.getgrnam(group).gr_gid)
    os.chmod(path, 0o660 if group else 0o600)
    return server
