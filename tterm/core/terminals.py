"""Terminals: real shells on machines, kept by the core, shown by pages.

A terminal lives in the core, not in the browser. Closing or reloading the
page leaves the shell, and whatever runs in it, alone; the page comes back
and takes up where it was. Only the shell's own end, or the core stopping,
ends a terminal.

Everything a terminal sends is numbered: each piece of output and each
marker gets the next number. The core keeps the last few megabytes, so a
page that lost its connection for a moment asks for everything after the
last number it has and the screen goes on as if nothing happened. A page
that comes back empty, after a reload, gets all that is kept, drawn again
from a clean screen; a full-screen program is then asked to redraw itself.

Each command becomes a block in the database: its text, the directory and
branch it ran in, how long it took, how it ended, and its output, compressed,
the beginning and the end of it if it was long.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import itertools
import json
import logging
import time
import uuid
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import asyncpg
import asyncssh

from . import accounts, shell, ssh
from .accounts import Person
from .signer_client import SignerClient

log = logging.getLogger("tterm.terminals")

#: Output kept for pages that come back after a moment away: they get all
#: they missed, if it is still kept.
KEEP_BYTES = 4 * 1024 * 1024
#: What a page that starts over draws: the last of the output, more than its
#: ten thousand lines of scrollback hold. After a flood (`yes`, a huge log)
#: the page catches up in a moment instead of drawing megabytes no one will
#: scroll back to.
REPLAY_BYTES = 1024 * 1024
#: Of a long command's output, the block keeps this much from the start and
#: this much from the end.
BLOCK_HEAD = 512 * 1024
BLOCK_TAIL = 512 * 1024
#: An ended terminal stays in memory this long, so a page can still show how
#: it ended; its blocks stay in the database.
KEEP_ENDED = 15 * 60
#: Typed before the shell is up, kept until it is.
PENDING_MAX = 64 * 1024
#: Roles that may open terminals; observers only watch.
CAN_OPEN = ("owner", "admin", "member")

#: What deploying the shell scripts may take, at most.
DEPLOY_TIMEOUT = 20

MIN_COLS, MAX_COLS = 10, 1000
MIN_ROWS, MAX_ROWS = 3, 500


@dataclass
class Event:
    seq: int
    #: "out" is bytes for the screen; "ev" is a marker, as a dict.
    kind: str
    data: bytes | dict


@dataclass
class Machine:
    id: uuid.UUID
    project_id: uuid.UUID
    code: str
    name: str
    host: str
    port: int
    login: str
    host_keys: list[str]
    ca_id: str | None

    @property
    def label(self) -> str:
        return f"{self.code}/{self.name}"


@dataclass
class Block:
    """A command while it runs."""
    command: str
    cwd: str
    branch: str
    seq_start: int
    started_at: dt.datetime
    started: float = field(default_factory=time.monotonic)
    head: bytearray = field(default_factory=bytearray)
    tail: deque = field(default_factory=deque)
    tail_bytes: int = 0
    total: int = 0

    def add(self, data: bytes) -> None:
        self.total += len(data)
        room = BLOCK_HEAD - len(self.head)
        if room > 0:
            self.head += data[:room]
            data = data[room:]
        if data:
            self.tail.append(data)
            self.tail_bytes += len(data)
            while self.tail_bytes - len(self.tail[0]) >= BLOCK_TAIL:
                self.tail_bytes -= len(self.tail.popleft())

    def output(self) -> tuple[bytes, bool]:
        tail = b"".join(self.tail)
        cut = len(self.head) + len(tail) < self.total
        if len(tail) > BLOCK_TAIL:
            tail, cut = tail[-BLOCK_TAIL:], True
        return bytes(self.head) + tail, cut


def clamp_size(cols: int, rows: int) -> tuple[int, int]:
    return (max(MIN_COLS, min(MAX_COLS, int(cols))),
            max(MIN_ROWS, min(MAX_ROWS, int(rows))))


class Terminal:
    """One shell on one machine."""

    def __init__(self, *, pool: asyncpg.Pool, signer: SignerClient,
                 owner: Person, machine: Machine, name: str,
                 cols: int, rows: int) -> None:
        self.id = uuid.uuid4()
        self.pool = pool
        self.signer = signer
        self.owner = owner
        self.machine = machine
        self.name = name
        self.cols, self.rows = clamp_size(cols, rows)
        #: connecting, open or ended.
        self.state = "connecting"
        self.reason: str | None = None
        self.exit_code: int | None = None
        self.opened_at = dt.datetime.now(dt.UTC)
        self.ended_at: float | None = None
        self.shell: str | None = None
        #: Whether this shell prints our markers; set by the first one seen.
        self.markers = False
        self.cwd = ""
        self.home = ""
        self.branch = ""

        self.events: deque[Event] = deque()
        self.first_seq = 1
        self.last_seq = 0
        self._kept = 0
        self.modes = shell.Modes()
        self._base_modes = shell.Modes()
        self.trimmed = False

        self._key = shell.new_key()
        self._parser = shell.MarkerParser(self._key)
        self._conn: asyncssh.SSHClientConnection | None = None
        self._proc: asyncssh.SSHClientProcess | None = None
        self._pending = bytearray()
        self._block: Block | None = None
        self._saving: set[asyncio.Task] = set()
        self._tick = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._nudging = False
        self._closing = False
        #: The page typing into this terminal; see Watcher.
        self.active: "Watcher | None" = None

    # ------------------------------------------------------------- facts

    @property
    def label(self) -> str:
        return (self.machine.label if self.name == "1"
                else f"{self.machine.label} · {self.name}")

    def describe(self) -> dict:
        return {
            "id": str(self.id),
            "machineId": str(self.machine.id),
            "machine": self.machine.name,
            "label": self.label,
            "name": self.name,
            "state": self.state,
            "reason": self.reason,
            "shell": self.shell,
            "markers": self.markers,
            "cwd": self.cwd,
            "home": self.home,
            "branch": self.branch,
            "openedAt": self.opened_at.isoformat(timespec="seconds"),
        }

    @property
    def tick(self) -> asyncio.Event:
        """Set once when anything changes; take it before looking, then wait."""
        return self._tick

    def _changed(self) -> None:
        self._tick.set()
        self._tick = asyncio.Event()

    # ------------------------------------------------------------ start

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"terminal {self.id}")

    async def _run(self) -> None:
        m = self.machine
        try:
            self._conn = await ssh.connect(
                self.signer, host=m.host, port=m.port, login=m.login,
                host_keys=m.host_keys, ca=m.ca_id,
                who=ssh.key_id(self.owner.name, str(self.owner.id), m.name))
            how = await self._deploy()
            self.shell = how.shell
            self._proc = await self._conn.create_process(
                how.command, term_type="xterm-256color",
                term_size=(self.cols, self.rows),
                # Echo starts off so the key never shows; the shell turns
                # it back on as soon as it has read the key.
                term_modes={asyncssh.PTY_ECHO: 0} if how.markers else {},
                encoding=None, stderr=asyncssh.STDOUT)
        except asyncio.CancelledError:
            raise
        except Exception as exc:        # noqa: BLE001 - every reason is shown
            log.info("terminal on %s did not open: %s", m.label, exc)
            await self._end(ssh.explain(exc, m.host, m.port))
            return
        if how.markers:
            self._proc.stdin.write(self._key.encode() + b"\n")
        if self._pending:
            self._proc.stdin.write(bytes(self._pending))
            self._pending.clear()
        self.state = "open"
        self._changed()
        await self._journal("terminal.opened", {"shell": self.shell})
        log.info("terminal opened on %s (%s)", m.label, self.shell)
        await self._read()

    async def _deploy(self) -> shell.Plan:
        """Puts the shell scripts on the machine and picks the shell.

        If that fails the login shell starts without them: the terminal
        works, only without blocks. Sending the key to a shell that does not
        expect it would type it in as a command.
        """
        assert self._conn is not None
        try:
            result = await self._conn.run(
                "sh -s", input=shell.deploy_script(), check=False,
                timeout=DEPLOY_TIMEOUT)
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            log.warning("could not put the shell scripts on %s: %s",
                        self.machine.label, exc)
            return shell.plan("")
        output = result.stdout if isinstance(result.stdout, str) else ""
        if result.exit_status != 0 or "shell=" not in output:
            errors = result.stderr if isinstance(result.stderr, str) else ""
            log.warning("could not put the shell scripts on %s: %s",
                        self.machine.label, errors.strip()[-300:])
            return shell.plan("")
        return shell.plan(output)

    # ------------------------------------------------------------ stream

    async def _read(self) -> None:
        proc = self._proc
        assert proc is not None
        reason = None
        try:
            while True:
                data = await proc.stdout.read(65536)
                if not data:
                    break
                self._take(data)
        except asyncio.CancelledError:
            raise
        except (asyncssh.Error, OSError) as exc:
            reason = f"The connection to {self.machine.name} was lost: {exc}"
        held = self._parser.flush()
        if held:
            self._output(held)
        code = None
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait_closed(), 2)
            code = proc.exit_status
        if self._closing:
            reason = "Closed."
        elif reason is None:
            if code is None:
                reason = f"The connection to {self.machine.name} was closed."
            else:
                reason = f"The shell ended with code {code}."
        await self._end(reason, code)

    def _take(self, data: bytes) -> None:
        for piece in self._parser.feed(data):
            if isinstance(piece, bytes):
                self._output(piece)
            else:
                self._marker(piece)
        self._changed()

    def _output(self, data: bytes) -> None:
        self.last_seq += 1
        self.events.append(Event(self.last_seq, "out", data))
        self._kept += len(data)
        self.modes.feed(data)
        if self._block is not None:
            self._block.add(data)
        while self._kept > KEEP_BYTES and len(self.events) > 1:
            old = self.events.popleft()
            self.first_seq = old.seq + 1
            self.trimmed = True
            if old.kind == "out":
                self._kept -= len(old.data)
                self._base_modes.feed(old.data)

    def _event(self, data: dict) -> None:
        self.last_seq += 1
        self.events.append(Event(self.last_seq, "ev", data))

    def _marker(self, m: shell.Marker) -> None:
        if m.kind == "P":
            code = m.field(0)
            exit_code = int(code) if code.isdigit() else None
            if self._block is not None:
                self._finish_block(exit_code)
            self.markers = True
            self.cwd, self.home, self.branch = m.field(1), m.field(2), m.field(3)
            self._event({"t": "P", "code": exit_code, "cwd": self.cwd,
                         "home": self.home, "branch": self.branch,
                         "venv": m.field(4)})
        elif m.kind == "C":
            if self._block is not None:
                self._finish_block(None)
            self._event({"t": "C", "command": m.field(0)})
            self._block = Block(command=m.field(0), cwd=self.cwd,
                                branch=self.branch, seq_start=self.last_seq,
                                started_at=dt.datetime.now(dt.UTC))

    def _finish_block(self, exit_code: int | None) -> None:
        block, self._block = self._block, None
        if block is None:
            return
        output, cut = block.output()
        if self.shell == "zsh":
            output = shell.strip_eol_mark(output, self.cols)
        task = asyncio.create_task(self._save_block(
            block, exit_code, output, cut, self.last_seq,
            dt.datetime.now(dt.UTC)))
        self._saving.add(task)
        task.add_done_callback(self._saving.discard)

    async def _save_block(self, block: Block, exit_code: int | None,
                          output: bytes, cut: bool, seq_end: int,
                          finished_at: dt.datetime) -> None:
        try:
            await self.pool.execute(
                """
                INSERT INTO blocks (terminal_id, kind, seq_start, seq_end,
                                    command, started_by, started_at,
                                    finished_at, exit_code, cwd, branch,
                                    output, output_cut, expires_at)
                VALUES ($1, 'command', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12,
                        now() + make_interval(days => (
                            SELECT output_days FROM projects WHERE id = $13)))
                """,
                self.id, block.seq_start, seq_end, block.command,
                self.owner.id, block.started_at, finished_at, exit_code,
                block.cwd or None, block.branch or None,
                zlib.compress(output, 6), cut, self.machine.project_id)
        except Exception:               # noqa: BLE001 - the screen goes on
            log.exception("could not save a block of terminal %s", self.id)

    # ------------------------------------------------------------ input

    def write(self, data: bytes) -> None:
        if self.state == "open" and self._proc is not None:
            self._proc.stdin.write(data)
        elif self.state == "connecting":
            room = PENDING_MAX - len(self._pending)
            if room > 0:
                self._pending += data[:room]

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = clamp_size(cols, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        if self.state == "open" and self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.change_terminal_size(cols, rows)

    def nudge(self) -> None:
        """Asks a full-screen program to draw itself again: the window
        narrows by a column and back, which it hears as two resizes."""
        if self._nudging or self.state != "open" or self._proc is None:
            return
        self._nudging = True

        async def go() -> None:
            try:
                proc = self._proc
                if proc is None:
                    return
                proc.change_terminal_size(max(MIN_COLS, self.cols - 1), self.rows)
                await asyncio.sleep(0.08)
                if self._proc is proc and self.state == "open":
                    proc.change_terminal_size(self.cols, self.rows)
            except Exception:            # noqa: BLE001 - only a redraw
                pass
            finally:
                self._nudging = False

        asyncio.create_task(go())

    # ------------------------------------------------------------ replay

    def snapshot(self) -> tuple[int, bytes, list[Event]]:
        """What a page that starts over draws: the number the replay
        follows, the sequences that bring a clean screen to the modes the
        stream was in at that point, and the last REPLAY_BYTES of it."""
        events = list(self.events)
        start, size = 0, 0
        for i in range(len(events) - 1, -1, -1):
            if events[i].kind == "out":
                size += len(events[i].data)
                if size > REPLAY_BYTES:
                    start = i + 1
                    break
        modes = self._base_modes.copy()
        for e in events[:start]:
            if e.kind == "out":
                modes.feed(e.data)                # type: ignore[arg-type]
        cut = self.trimmed or start > 0
        events = events[start:]
        base = events[0].seq - 1 if events else self.last_seq
        if cut and events and events[0].kind == "out":
            # The replay may start inside a line or an escape sequence: it
            # starts at the next line instead.
            first = events[0].data
            assert isinstance(first, bytes)
            nl = first.find(b"\n", 0, 4096)
            if nl >= 0:
                modes.feed(first[:nl + 1])
                events[0] = Event(events[0].seq, "out", first[nl + 1:])
        return base, modes.restore(), events

    def events_after(self, seq: int) -> list[Event]:
        start = seq - self.first_seq + 1
        if start < 0:
            raise LookupError("no longer kept")
        return list(itertools.islice(self.events, start, None))

    # ------------------------------------------------------------ end

    async def close(self) -> None:
        """Ends the terminal: the shell gets a hangup, as when a window
        closes."""
        if self.state == "ended":
            return
        self._closing = True
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.close()
        if self._task is not None and self.state == "connecting":
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
            await self._end("Closed before it opened.")
            return
        if self._task is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(self._task), 3)
        if self.state != "ended":
            await self._end("Closed.")

    async def stop(self, reason: str) -> None:
        """The core is stopping: the shell ends with it."""
        if self.state == "ended":
            return
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        await self._end(reason)

    async def _end(self, reason: str, code: int | None = None) -> None:
        if self.state == "ended":
            return
        if self._block is not None:
            self._finish_block(code)
        self.state = "ended"
        self.reason = reason
        self.exit_code = code
        self.ended_at = time.monotonic()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._proc = None
        self._changed()
        if self._saving:
            await asyncio.wait(set(self._saving), timeout=5)
        try:
            await self.pool.execute(
                "UPDATE terminals SET closed_at = now(), last_seq = $2, "
                "end_reason = $3, shell = $4 WHERE id = $1",
                self.id, self.last_seq, reason[:500], self.shell)
        except Exception:               # noqa: BLE001
            log.exception("could not record the end of terminal %s", self.id)
        await self._journal("terminal.closed", {"reason": reason[:500]})
        log.info("terminal on %s ended: %s", self.machine.label, reason)

    async def _journal(self, kind: str, details: dict) -> None:
        try:
            async with self.pool.acquire() as conn:
                await accounts.journal(
                    conn, kind, project_id=self.machine.project_id,
                    user_id=self.owner.id, machine_id=self.machine.id,
                    terminal_id=self.id, details=details)
        except Exception:               # noqa: BLE001
            log.exception("could not write %s to the journal", kind)


class NotAllowed(Exception):
    """No such machine, or not one this person may open terminals on."""


class Terminals:
    """The live terminals of this core."""

    def __init__(self, pool: asyncpg.Pool, signer: SignerClient,
                 owner: Person) -> None:
        self.pool = pool
        self.signer = signer
        self.owner = owner
        self._live: dict[uuid.UUID, Terminal] = {}
        self._sweeper: asyncio.Task | None = None

    async def start(self) -> None:
        """Terminals do not outlive the core: any left open by the last run
        ended with it."""
        await self.pool.execute(
            "UPDATE terminals SET closed_at = now(), end_reason = $1 "
            "WHERE closed_at IS NULL", "tTerm Web stopped.")
        self._sweeper = asyncio.create_task(self._sweep())

    async def open(self, machine_id: uuid.UUID, cols: int, rows: int) -> Terminal:
        machine = await self._machine(machine_id)
        used = {t.name for t in self._live.values()
                if t.machine.id == machine.id and t.state != "ended"}
        name = str(next(n for n in itertools.count(1) if str(n) not in used))
        terminal = Terminal(pool=self.pool, signer=self.signer,
                            owner=self.owner, machine=machine, name=name,
                            cols=cols, rows=rows)
        await self.pool.execute(
            "INSERT INTO terminals (id, machine_id, owner_id, name, kind) "
            "VALUES ($1, $2, $3, $4, 'term')",
            terminal.id, machine.id, self.owner.id, name)
        self._live[terminal.id] = terminal
        terminal.start()
        return terminal

    async def _machine(self, machine_id: uuid.UUID) -> Machine:
        row = await self.pool.fetchrow(
            """
            SELECT m.id, m.project_id, p.code, m.name, m.host, m.port, m.login,
                   m.host_keys, m.ca_id
            FROM machines m
            JOIN projects p ON p.id = m.project_id
            JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = $2
            WHERE m.id = $1 AND m.removed_at IS NULL AND m.kind = 'server'
              AND pm.role = ANY($3::text[])
              AND (pm.until IS NULL OR pm.until > now())
            """, machine_id, self.owner.id, list(CAN_OPEN))
        if row is None:
            raise NotAllowed("No such machine.")
        return Machine(row["id"], row["project_id"], row["code"], row["name"],
                       row["host"], row["port"], row["login"],
                       list(row["host_keys"]), row["ca_id"])

    def get(self, terminal_id: uuid.UUID) -> Terminal | None:
        t = self._live.get(terminal_id)
        if t is None or t.owner.id != self.owner.id:
            return None
        return t

    def live(self) -> list[Terminal]:
        return sorted((t for t in self._live.values() if t.state != "ended"),
                      key=lambda t: t.opened_at)

    async def ended(self, terminal_id: uuid.UUID) -> dict | None:
        """A terminal no longer in memory, as the database remembers it."""
        row = await self.pool.fetchrow(
            """
            SELECT t.id, t.name, t.closed_at, t.end_reason, m.id AS machine_id,
                   m.name AS machine, p.code
            FROM terminals t
            JOIN machines m ON m.id = t.machine_id
            JOIN projects p ON p.id = m.project_id
            WHERE t.id = $1 AND t.owner_id = $2
            """, terminal_id, self.owner.id)
        if row is None:
            return None
        label = f"{row['code']}/{row['machine']}"
        if row["name"] and row["name"] != "1":
            label += f" · {row['name']}"
        return {"id": str(row["id"]), "machineId": str(row["machine_id"]),
                "machine": row["machine"], "label": label,
                "name": row["name"], "state": "gone",
                "reason": row["end_reason"] or "This terminal is no longer running."}

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(BaseException):
                await self._sweeper
        await asyncio.gather(*(t.stop("tTerm Web stopped.")
                               for t in list(self._live.values())),
                             return_exceptions=True)

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for tid, t in list(self._live.items()):
                if t.ended_at is not None and now - t.ended_at > KEEP_ENDED:
                    del self._live[tid]


# ------------------------------------------------------------- watching

Send = Callable[[bytes | str], Awaitable[None]]

#: Binary frames: one byte of kind, four of channel, eight of number.
OUTPUT, INPUT = 1, 2
#: Output sent in one frame, at most.
FRAME_MAX = 256 * 1024


def output_frame(channel: int, seq: int, data: bytes) -> bytes:
    return (bytes([OUTPUT]) + channel.to_bytes(4, "big") + seq.to_bytes(8, "big")
            + data)


class Watcher:
    """One page's view of one terminal, over that page's socket.

    It sends what the page has not seen yet, in order, at the page's pace:
    nothing piles up in memory for a slow page. If it falls so far behind
    that the oldest of what it needs is no longer kept, it starts over from
    what is kept, the way a terminal catches up after a flood.

    Of several pages on one terminal, only the active one types: the one
    the person last clicked into. The page's terminal answers questions
    programs ask it (where is the cursor, what colours), and two pages would
    answer twice; in vim, a second answer reads as keys.
    """

    def __init__(self, terminal: Terminal, channel: int, send: Send,
                 after: int | None) -> None:
        self.terminal = terminal
        self.channel = channel
        self._send = send
        self._after = after
        self.sent = -1
        self._state_sent: tuple | None = None
        self._resume = asyncio.Event()
        self._resume.set()
        if terminal.active is None:
            terminal.active = self
        self.task = asyncio.create_task(self._run())

    def focus(self) -> None:
        self.terminal.active = self

    def write(self, data: bytes) -> None:
        t = self.terminal
        if t.active is None:
            t.active = self
        if t.active is self:
            t.write(data)

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    async def stop(self) -> None:
        if self.terminal.active is self:
            self.terminal.active = None
        self.task.cancel()
        with contextlib.suppress(BaseException):
            await self.task

    async def _run(self) -> None:
        try:
            await self._watch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:        # noqa: BLE001 - the page went away
            log.debug("watcher of %s stopped: %r", self.terminal.id, exc)

    async def _watch(self) -> None:
        t = self.terminal
        after = self._after
        if after is not None and t.first_seq - 1 <= after <= t.last_seq:
            self.sent = after
            await self._json({"op": "attached", "ch": self.channel,
                              "terminal": t.describe(), "resumed": True})
        else:
            await self._json({"op": "attached", "ch": self.channel,
                              "terminal": t.describe(), "resumed": False})
            await self._replay()
        while True:
            tick = t.tick
            await self._resume.wait()
            if self.sent < t.first_seq - 1:
                await self._replay()
                continue
            new = t.events_after(self.sent)
            if new:
                await self._events(new)
                continue
            await self._state()
            await tick.wait()

    async def _replay(self) -> None:
        t = self.terminal
        base, prefix, events = t.snapshot()
        await self._json({"op": "reset", "ch": self.channel, "seq": base})
        if prefix:
            await self._send(output_frame(self.channel, base, prefix))
        self.sent = base
        await self._events(events)
        # The page draws history now, not live output: whatever its terminal
        # answers to old questions in it must not reach the shell.
        await self._json({"op": "replayed", "ch": self.channel,
                          "seq": self.sent})
        if t.modes.alt_screen:
            t.nudge()

    async def _events(self, events: list[Event]) -> None:
        run: list[bytes] = []
        size = 0
        seq = self.sent
        for e in events:
            if e.kind == "out":
                assert isinstance(e.data, bytes)
                if run and size + len(e.data) > FRAME_MAX:
                    await self._send(output_frame(self.channel, seq,
                                                  b"".join(run)))
                    self.sent = seq
                    run, size = [], 0
                run.append(e.data)
                size += len(e.data)
                seq = e.seq
            else:
                if run:
                    await self._send(output_frame(self.channel, seq,
                                                  b"".join(run)))
                    self.sent = seq
                    run, size = [], 0
                await self._json({"op": "ev", "ch": self.channel, "seq": e.seq,
                                  **e.data})
                seq = self.sent = e.seq
        if run:
            await self._send(output_frame(self.channel, seq, b"".join(run)))
            self.sent = seq

    async def _state(self) -> None:
        t = self.terminal
        now = (t.state, t.reason, t.markers, t.shell)
        if now != self._state_sent:
            self._state_sent = now
            await self._json({"op": "state", "ch": self.channel,
                              "terminal": t.describe()})

    async def _json(self, message: dict) -> None:
        await self._send(json.dumps(message, ensure_ascii=False,
                                    separators=(",", ":")))
