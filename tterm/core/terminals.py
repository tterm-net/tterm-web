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

From 0.0.5 the page can also type a whole command at the prompt (run), and
ask the machine, over a quiet side channel of the same SSH connection, what
a word under Tab can become and which directories and branches there are.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import itertools
import json
import logging
import re
import time
import uuid
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import asyncpg
import asyncssh

from . import accounts, complete, shell, ssh
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
#: A side script (Tab, the directory and branch lists) may take this long,
#: print this much, and this many may run at once on one terminal.
SIDE_TIMEOUT = 5
SIDE_MAX = 1024 * 1024
SIDE_AT_ONCE = 2
#: Of each block in the feed's first answer, the end of its output, this
#: much at most; the rest comes when the person asks for it.
FEED_TAIL = 64 * 1024
#: Blocks in one answer of the feed, at most.
FEED_MAX = 200
#: Commands in the history the input line gets.
HISTORY_MAX = 1000

MIN_COLS, MAX_COLS = 10, 1000
MIN_ROWS, MAX_ROWS = 3, 500

#: Control characters a command typed by the page may not carry. Escape
#: above all: it could end the bracketed paste the command is wrapped in
#: and type keys of its own.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
#: A command typed by run() that the shell has not answered after this long
#: never started: a quote or a heredoc left open, or a history expansion
#: that failed (bash then shows its prompt again without a marker).
TYPED_STALE = 3.0


def clean_command(command: str) -> str:
    """A command from the page as it will be typed: lines joined by \\n,
    control characters out. Checked for danger in this form, so that what
    is checked is what runs."""
    text = command.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return _CONTROL.sub("", text)


class NotOpen(Exception):
    """The terminal is not running: nothing can be asked of its machine."""


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
    #: The width it ran at: the page draws its output again at this width.
    cols: int = 80
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


def _code(text: str) -> int | None:
    return int(text) if text.isdigit() else None


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
        #: Whether it marks where each command starts too, so that the page
        #: can show blocks and type commands of its own (bash 4.4+, zsh).
        self.blocks = False
        self.cwd = ""
        self.home = ""
        self.branch = ""
        self.venv = ""
        #: The shell's PATH at its last prompt, for Tab on a command.
        self.path = ""

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
        #: The last commands, to say which one a replay starts inside of.
        self._recent: deque[dict] = deque(maxlen=64)
        #: A command was typed by run() and the shell has not answered with a
        #: marker yet: no second one goes in on top of it.
        self._typed = False
        self._typed_at = 0.0
        self._side = asyncio.Semaphore(SIDE_AT_ONCE)
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
            "blocks": self.blocks,
            "cwd": self.cwd,
            "home": self.home,
            "branch": self.branch,
            "venv": self.venv,
            "running": self._running(),
            "openedAt": self.opened_at.isoformat(timespec="seconds"),
        }

    def _running(self) -> dict | None:
        """The command running now."""
        b = self._block
        if b is None:
            return None
        return {"command": b.command, "cwd": b.cwd, "branch": b.branch,
                "seqStart": b.seq_start, "cols": b.cols,
                "at": int(b.started_at.timestamp() * 1000)}

    def block_at(self, seq: int) -> dict | None:
        """The command whose output goes on past the number `seq`: a replay
        that starts there starts inside it, after its start marker, and the
        feed still has to know what it was."""
        for entry in reversed(self._recent):
            if entry["seqStart"] <= seq and (entry["seqEnd"] is None
                                             or entry["seqEnd"] > seq):
                return dict(entry)
        return None

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
            self.blocks = how.blocks
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
        # Markers carry the core's clock, so that a page drawing them again
        # later still knows when each command started and ended.
        at = int(time.time() * 1000)
        if m.kind == "P":
            exit_code = _code(m.field(0))
            trim = 0
            if self._block is not None:
                trim = self._finish_block(exit_code)
            self._typed = False
            self.markers = True
            self.cwd, self.home, self.branch = m.field(1), m.field(2), m.field(3)
            self.venv, self.path = m.field(4), m.field(5)
            # trim: how much of the end of the block's output was zsh's
            # partial-line mark, which the block does not keep.
            self._event({"t": "P", "code": exit_code, "cwd": self.cwd,
                         "home": self.home, "branch": self.branch,
                         "venv": self.venv, "trim": trim, "at": at})
        elif m.kind == "C":
            prev = _code(m.field(1))
            if self._block is not None:
                # One line of several commands: the one before ended here.
                self._finish_block(prev)
            self._typed = False
            self._event({"t": "C", "command": m.field(0), "prev": prev,
                         "at": at})
            self._block = Block(command=m.field(0), cwd=self.cwd,
                                branch=self.branch, seq_start=self.last_seq,
                                started_at=dt.datetime.now(dt.UTC),
                                cols=self.cols)
            self._recent.append({
                "command": m.field(0), "cwd": self.cwd, "branch": self.branch,
                "seqStart": self.last_seq, "seqEnd": None, "cols": self.cols,
                "at": at})

    def _finish_block(self, exit_code: int | None) -> int:
        """Saves the running command's block; returns how many bytes of
        zsh's partial-line mark came off the end of its output."""
        block, self._block = self._block, None
        if block is None:
            return 0
        for entry in reversed(self._recent):
            if entry["seqStart"] == block.seq_start:
                entry["seqEnd"] = self.last_seq
                break
        output, cut = block.output()
        trim = 0
        if self.shell == "zsh":
            stripped = shell.strip_eol_mark(output, self.cols)
            trim = len(output) - len(stripped)
            output = stripped
        task = asyncio.create_task(self._save_block(
            block, exit_code, output, cut, self.last_seq,
            dt.datetime.now(dt.UTC)))
        self._saving.add(task)
        task.add_done_callback(self._saving.discard)
        return trim

    async def _save_block(self, block: Block, exit_code: int | None,
                          output: bytes, cut: bool, seq_end: int,
                          finished_at: dt.datetime) -> None:
        try:
            await self.pool.execute(
                """
                INSERT INTO blocks (terminal_id, kind, seq_start, seq_end,
                                    command, started_by, started_at,
                                    finished_at, exit_code, cwd, branch,
                                    output, output_cut, cols, expires_at)
                VALUES ($1, 'command', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $14,
                        now() + make_interval(days => (
                            SELECT output_days FROM projects WHERE id = $13)))
                """,
                self.id, block.seq_start, seq_end, block.command,
                self.owner.id, block.started_at, finished_at, exit_code,
                block.cwd or None, block.branch or None,
                zlib.compress(output, 6), cut, self.machine.project_id,
                block.cols)
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

    def run(self, command: str) -> str | None:
        """Types a whole command at the shell's prompt and presses Enter, the
        way tTerm's own input line hands it over. Returns why not, or None.

        The command goes in as a bracketed paste when the shell has that on
        (bash 5.1+ and zsh do): the line editor then takes it as text, every
        line of it, with no key in it read as a binding, and Enter runs it
        all. Without it the text is typed as is.

        Ctrl+U goes first and empties the line. Keys typed into the previous
        command that it never read wait in the terminal, and the line editor
        takes them in at the prompt; they would end up in front of this
        command.
        """
        if self.state != "open" or self._proc is None:
            return "The terminal is not open."
        if not self.blocks:
            return ("This shell does not mark where commands start, so it "
                    "runs in the classic mode only.")
        if self._block is not None:
            return "A command is still running."
        if self._typed:
            if time.monotonic() - self._typed_at < TYPED_STALE:
                return "A command is still running."
            # The last one never started. Ctrl+C sets the shell back to an
            # empty prompt; whatever follows it in the same moment would be
            # thrown away by the terminal, so this command waits for Enter
            # again.
            self._proc.stdin.write(b"\x03")
            self._typed_at = time.monotonic()
            return ("The shell had not taken the last command (an open quote, "
                    "or a history expansion with ! that failed). It is "
                    "cancelled now: press Enter again.")
        text = clean_command(command)
        if not text.strip():
            return "There is no command to run."
        if self.modes.state.get(2004):
            data = b"\x15\x1b[200~" + text.encode() + b"\x1b[201~\r"
        else:
            data = b"\x15" + text.replace("\t", " ").encode() + b"\r"
        self._typed = True
        self._typed_at = time.monotonic()
        self._proc.stdin.write(data)
        return None

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = clamp_size(cols, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        if self.state == "open" and self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.change_terminal_size(cols, rows)

    # -------------------------------------------------------- side channel

    async def side(self, script: str) -> list[str]:
        """Runs a small sh script on the machine on a channel of its own in
        the same SSH connection, and returns the lines it listed. The
        terminal itself never sees it. An empty list when it fails: Tab
        then simply finds nothing."""
        conn = self._conn
        if self.state != "open" or conn is None:
            raise NotOpen("The terminal is not open.")
        async with self._side:
            try:
                result = await conn.run("sh -s", input=script.encode(),
                                        encoding=None, check=False,
                                        timeout=SIDE_TIMEOUT)
            except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
                log.info("side script on %s failed: %s", self.machine.label, exc)
                return []
        out = result.stdout if isinstance(result.stdout, bytes) else b""
        return complete.listed(out[:SIDE_MAX])

    async def complete(self, line: str, cursor: int) -> dict:
        """What the word under the cursor can become."""
        cursor = max(0, min(len(line), int(cursor)))
        words, word = complete.split(line[:cursor])
        ctx = complete.context(words, word)
        items: list[dict] = []
        if ctx.kind == "command":
            names = await self.side(complete.commands_script(self.path,
                                                             word.value))
            items = complete.command_items(word, names)
        elif ctx.kind == "git":
            items = complete.git_items(word)
        if ctx.kind in ("branch", "branch+file"):
            refs = await self.side(complete.branches_script(self.cwd))
            items = complete.branch_items(word, refs, ctx.switch)
        if ctx.kind in ("file", "dir", "branch+file"):
            where, _, _ = complete.dir_part(word.value, self.home)
            entries = await self.side(complete.list_script(self.cwd, where))
            items += complete.file_items(word, entries, self.home,
                                         dirs_only=ctx.kind == "dir")
        return {"start": word.start, "end": cursor, "quote": word.quote,
                "kind": ctx.kind, "items": items[:complete.MAX_ITEMS],
                "more": len(items) > complete.MAX_ITEMS}

    async def dirs(self) -> dict:
        """The directories in the terminal's current one."""
        entries = await self.side(complete.list_script(self.cwd, "."))
        return {"cwd": self.cwd, "home": self.home,
                "dirs": complete.dirs_of(entries)}

    async def branches(self) -> dict:
        """The branches of the repository the terminal is in."""
        if not self.branch:
            return {"current": "", "local": [], "remote": []}
        refs = await self.side(complete.branches_script(self.cwd))
        return {"current": self.branch, **complete.branches_of(refs)}

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
                "end_reason = $3, shell = $4, home = $5 WHERE id = $1",
                self.id, self.last_seq, reason[:500], self.shell,
                self.home or None)
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


def _ms(when: dt.datetime | None) -> int | None:
    return int(when.timestamp() * 1000) if when else None


def _block_json(row: asyncpg.Record) -> dict:
    """A finished block for the page: the end of its output only, starting
    at a line, if it is long."""
    output = zlib.decompress(row["output"]) if row["output"] else b""
    size = len(output)
    tail, partial = output, False
    if size > FEED_TAIL:
        start = size - FEED_TAIL
        nl = output.find(b"\n", start)
        tail = output[nl + 1:] if 0 <= nl < size - 1 else output[start:]
        partial = True
    return {
        "id": row["id"],
        "seqStart": row["seq_start"],
        "seqEnd": row["seq_end"],
        "command": row["command"] or "",
        "cwd": row["cwd"] or "",
        "branch": row["branch"] or "",
        "startedAt": _ms(row["started_at"]),
        "finishedAt": _ms(row["finished_at"]),
        "exitCode": row["exit_code"],
        "cols": row["cols"],
        "cut": row["output_cut"],
        "partial": partial,
        "size": size,
        "output": base64.b64encode(tail).decode("ascii"),
    }


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
            SELECT t.id, t.name, t.closed_at, t.end_reason, t.home,
                   m.id AS machine_id, m.name AS machine, p.code
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
                "name": row["name"], "state": "gone", "home": row["home"] or "",
                "reason": row["end_reason"] or "This terminal is no longer running."}

    # ------------------------------------------------- blocks and history

    async def _owned(self, terminal_id: uuid.UUID) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            SELECT t.id, t.machine_id, m.project_id
            FROM terminals t JOIN machines m ON m.id = t.machine_id
            WHERE t.id = $1 AND t.owner_id = $2
            """, terminal_id, self.owner.id)

    async def feed(self, terminal_id: uuid.UUID, before: int | None,
                   limit: int) -> dict | None:
        """The terminal's finished blocks, the latest `limit` of those that
        started before the number `before`, oldest first. Each with the end
        of its output; None if the terminal is not this person's."""
        if await self._owned(terminal_id) is None:
            return None
        limit = max(1, min(FEED_MAX, int(limit)))
        rows = await self.pool.fetch(
            """
            SELECT id, seq_start, seq_end, command, cwd, branch, started_at,
                   finished_at, exit_code, output, output_cut, cols
            FROM blocks
            WHERE terminal_id = $1 AND kind = 'command' AND expires_at > now()
              AND ($2::bigint IS NULL OR seq_start < $2)
            ORDER BY seq_start DESC
            LIMIT $3
            """, terminal_id, before, limit + 1)
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        # Unpacking a few megabytes is work: not in the event loop.
        blocks = await asyncio.to_thread(lambda: [_block_json(r) for r in rows])
        return {"blocks": blocks, "more": more}

    async def block_output(self, terminal_id: uuid.UUID,
                           seq_start: int) -> tuple[bytes, bool] | None:
        """All a block kept of its output, and whether its middle was cut."""
        row = await self.pool.fetchrow(
            """
            SELECT b.output, b.output_cut
            FROM blocks b JOIN terminals t ON t.id = b.terminal_id
            WHERE b.terminal_id = $1 AND t.owner_id = $2 AND b.seq_start = $3
              AND b.expires_at > now()
            """, terminal_id, self.owner.id, seq_start)
        if row is None:
            return None
        data = row["output"] or b""
        output = await asyncio.to_thread(zlib.decompress, data) if data else b""
        return output, row["output_cut"]

    async def history(self, terminal_id: uuid.UUID) -> list[dict] | None:
        """The commands for the input line's history, newest first: those
        run on this terminal's machine, then the rest of its project's."""
        owned = await self._owned(terminal_id)
        if owned is None:
            return None
        rows = await self.pool.fetch(
            """
            SELECT b.command, bool_or(t.machine_id = $2) AS here
            FROM blocks b
            JOIN terminals t ON t.id = b.terminal_id
            JOIN machines m ON m.id = t.machine_id
            WHERE t.owner_id = $1 AND m.project_id = $3
              AND b.command IS NOT NULL AND b.command <> ''
              AND b.expires_at > now()
            GROUP BY b.command
            ORDER BY bool_or(t.machine_id = $2) DESC, max(b.started_at) DESC
            LIMIT $4
            """, self.owner.id, owned["machine_id"], owned["project_id"],
            HISTORY_MAX)
        return [{"command": r["command"], "here": r["here"]} for r in rows]

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
            # What came since, then the mark that it is all old news: the
            # page's terminal must not answer questions programs asked in
            # it, as on a replay. A flood during the await above may have
            # pushed that out of what is kept: then a replay instead.
            try:
                new = t.events_after(self.sent)
            except LookupError:
                await self._replay()
            else:
                if new:
                    await self._events(new)
                await self._json({"op": "replayed", "ch": self.channel,
                                  "seq": self.sent})
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
        # The replay may begin inside a command, after its start marker: the
        # feed needs to know which, to put its output under it.
        await self._json({"op": "reset", "ch": self.channel, "seq": base,
                          "running": t.block_at(base)})
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
