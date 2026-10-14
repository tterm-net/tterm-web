"""Computers: the agent that dials in (from 0.0.6, with agent 0.11.0).

A laptop cannot be reached from outside: it has no stable address, it sits
behind NAT and it sleeps. So the direction is reversed: the agent opens a
WebSocket to the machines' side, /agent, and keeps it open. Every terminal
on that computer goes through that one connection.

The agent stays a pipe: it starts what it is asked to in a pseudo-terminal
and passes the bytes both ways, and it runs the small sh scripts it is
sent. The markers, the blocks and everything else live here, as for a
server, so the agent rarely needs a new version.

How a computer joins. Add a machine, then Computer, gives a command with a
token that works for ten minutes. The agent, started with it, says hello;
the core takes the token, reads the computer's passport through the agent,
and from then on the same token is that computer's key: only its hash is
kept, with the machine, and the agent signs in with it every time.

The protocol, all of it on the one WebSocket (web 1):

    agent → core  text   hello {token, name, user, os, agent, shell, web}
    core → agent  text   welcome {name}, or error {error, revoked}
    core → agent  text   open {ch, cmd, cols, rows, echo}, size {ch, cols,
                         rows}, close {ch}, run {id, script, timeout}, hb,
                         ping
    core → agent  bytes  2, the channel in 4 bytes, keys
    agent → core  bytes  1, the channel in 4 bytes, output
    agent → core  text   exit {ch, code, error}, ran {id, code, out, err},
                         hb, pong

The Telegram bot speaks an older form of it to the same agent; the agent
tells the two apart by what it is sent.

/agent is open to the internet, so what a stranger can make it do is
bounded: a hello is small and comes at once, only so many connections from
one address may wait to say it, and when too many wait in all the oldest
goes; an address whose hellos keep failing waits, except for computers it
already has; a message is never larger than a script's answer.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import datetime as dt
import ipaddress
import itertools
import json
import logging
import time
import uuid
from typing import Awaitable, Callable

import asyncpg
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import machines, passport
from .transport import Channel, Link, LinkError

log = logging.getLogger("tterm.agents")

#: Binary frames: a byte of kind, four of channel, then the data.
OUTPUT, INPUT = 1, 2
#: The agent says hello as soon as it is connected; a connection that does
#: not is dropped.
HELLO_TIMEOUT = 5
#: A hello is a few hundred bytes; anything much longer is not one and is
#: not even parsed.
HELLO_MAX = 8192
#: The largest message an agent may send at all, set on the listener: a
#: script's answer, up to 2 MiB in base64, with room to spare.
FRAME_MAX = 4 * 1024 * 1024
#: Connections that have not said who they are yet: at most this many from
#: one address (an IPv6 one by its /64), the next is not accepted; and this
#: many in all, past which the one that has waited longest is let go. A real
#: agent says hello at once, so it is never the one that has waited longest,
#: and a stranger holding connections open cannot keep agents out.
PENDING_PER_ADDRESS = 4
PENDING_MAX = 64
#: How many waiting connections may be let go that way in EVICT_WINDOW
#: seconds. A closed connection's socket stays open a while for the other
#: side's goodbye; past this, new ones are refused at the door again, which
#: frees theirs at once.
EVICTIONS = 32
EVICT_WINDOW = 10.0
#: Failed hellos one address may make in STRIKE_WINDOW seconds before it
#: is turned away without a look.
STRIKES = 20
STRIKE_WINDOW = 600.0
#: How long a connected agent has to answer when another one comes with
#: its key.
PROBE_TIMEOUT = 3.0
#: What may wait to be sent to one agent. One that stops reading is
#: dropped rather than let fill the core's memory. Twice the largest paste
#: the page's socket takes in one message (uvicorn's 16 MiB), so a paste
#: alone never counts as an agent that stopped reading.
OUTBOX_MAX = 32 * 1024 * 1024
#: Keys go to the agent in pieces of at most this, each its own frame.
INPUT_PIECE = 64 * 1024
#: The oldest agent protocol tTerm Web works with.
WEB_PROTOCOL = 1
#: Reading the passport of a computer that just joined, at most.
PASSPORT_TIMEOUT = 20
#: Fields of the hello kept, and how long each may be.
HELLO_TEXT = {"name": 64, "user": 64, "os": 200, "agent": 32, "shell": 64}
#: The answer to the agent's sign of life.
HB = json.dumps({"t": "hb"}, separators=(",", ":"))

TOO_OLD = ("This agent is too old for tTerm Web: it needs version 0.11.0 or "
           "newer, from github.com/tterm-net/tterm-agent.")
NOT_KNOWN = ("This computer is not in tTerm Web, or it was removed. Add it "
             "again: Add a machine, then Computer.")
FOR_A_SERVER = ("This token is for a server: its command runs on the server. "
                "For this computer, choose Computer under Add a machine.")
TWICE = ("This computer is already connected to tTerm Web: its key is in use "
         "by another agent. Stop that one first.")
TOO_MANY = ("Too many failed attempts from this address. Try again in a few "
            "minutes.")
LEFT_WHILE_JOINING = ("The agent disconnected while the computer was being "
                      "added. Run the command again.")


class AgentChannel(Channel):
    """A terminal's shell on the computer, as the terminal sees it."""

    def __init__(self, link: "AgentLink", ch: int) -> None:
        self.link = link
        self.ch = ch
        self._out: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._exit: asyncio.Future = asyncio.get_running_loop().create_future()
        self._lost: str | None = None
        self.closed = False

    @property
    def over(self) -> bool:
        return self.closed or self._exit.done()

    def write(self, data: bytes) -> None:
        if not self.over:
            self.link.send_frame(INPUT, self.ch, data)

    async def read(self) -> bytes:
        item = await self._out.get()
        if item is None:
            self._out.put_nowait(None)          # every later read ends too
            if self._lost:
                raise ConnectionError(self._lost)
            return b""
        return item

    def resize(self, cols: int, rows: int) -> None:
        if not self.over:
            self.link.send_json({"t": "size", "ch": self.ch, "cols": cols,
                                 "rows": rows})

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if not self._exit.done():
            self.link.send_json({"t": "close", "ch": self.ch})
            self._exit.set_result(None)
        self.link.forget(self.ch)
        self._out.put_nowait(None)

    async def exit_code(self) -> int | None:
        try:
            return await asyncio.wait_for(asyncio.shield(self._exit), 2)
        except asyncio.TimeoutError:
            return None

    # -------------------------------------------------- from the link

    def output(self, data: bytes) -> None:
        if not self.closed:
            self._out.put_nowait(data)

    def ended(self, code: int | None) -> None:
        if not self._exit.done():
            self._exit.set_result(code)
        self._out.put_nowait(None)

    def lost(self, reason: str) -> None:
        self._lost = reason
        if not self._exit.done():
            self._exit.set_result(None)
        self._out.put_nowait(None)


Sender = Callable[[str | bytes], Awaitable[None]]
Closer = Callable[[], Awaitable[None]]


class AgentLink(Link):
    """One connected agent: one computer, all its terminals.

    What goes to the agent goes through one queue and one task, so that it
    leaves in the order it was given: keys are typed in order, a resize does
    not overtake the keys before it.
    """

    def __init__(self, send: Sender, close: Closer, hello: dict) -> None:
        self.machine_id: uuid.UUID | None = None
        self.name = hello.get("name") or "computer"
        self.user = hello.get("user") or ""
        self.os = hello.get("os") or ""
        self.version = hello.get("agent") or ""
        self.shell = hello.get("shell") or ""
        self.connected_at = time.time()
        #: Why the connection is gone, once it is.
        self.gone: str | None = None
        self._send = send
        self._close = close
        self._closing: asyncio.Task | None = None
        self._outbox: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self._queued = 0
        self._hb_waiting = False
        self._heard = asyncio.Event()
        self._channels: dict[int, AgentChannel] = {}
        self._scripts: dict[int, asyncio.Future] = {}
        self._numbers = itertools.count(1)
        self._sender = asyncio.create_task(self._send_all())

    # ----------------------------------------------------------- out

    def _put(self, item: str | bytes) -> None:
        if self.gone:
            return
        self._queued += len(item)
        if self._queued > OUTBOX_MAX:
            self.lost("the agent stopped taking what tTerm Web sends it")
            self.hang_up()
            return
        self._outbox.put_nowait(item)

    def send_json(self, message: dict) -> None:
        self._put(json.dumps(message, separators=(",", ":")))

    def send_frame(self, kind: int, ch: int, data: bytes) -> None:
        head = bytes([kind]) + ch.to_bytes(4, "big")
        for start in range(0, len(data), INPUT_PIECE):
            self._put(head + data[start:start + INPUT_PIECE])

    async def _send_all(self) -> None:
        while True:
            item = await self._outbox.get()
            if item is None or self.gone:
                return
            self._queued -= len(item)
            if item == HB:
                self._hb_waiting = False
            try:
                await self._send(item)
            except Exception as exc:        # noqa: BLE001 - the link is gone
                self.lost(f"the connection to the agent broke ({type(exc).__name__})")
                return

    def hang_up(self) -> None:
        """Closes the connection from this side: for a link that is over
        while its socket may not know it yet."""
        if self._closing is None:
            self._closing = asyncio.create_task(self._hang_up())

    async def _hang_up(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._close(), 5)

    async def answers(self, timeout: float) -> bool:
        """Whether the agent at the other end still answers: asked with a
        ping, anything from it within `timeout` will do."""
        if self.gone:
            return False
        self._heard.clear()
        self.send_json({"t": "ping"})
        try:
            await asyncio.wait_for(self._heard.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return not self.gone

    # ------------------------------------------------------ Link

    async def script(self, text: str, timeout: float) -> tuple[int | None, bytes, bytes]:
        if self.gone:
            raise LinkError(self.gone)
        number = next(self._numbers)
        answer = asyncio.get_running_loop().create_future()
        self._scripts[number] = answer
        self.send_json({"t": "run", "id": number, "script": text,
                        "timeout": timeout})
        try:
            return await asyncio.wait_for(answer, timeout + 5)
        except asyncio.TimeoutError:
            raise LinkError("the computer did not answer in time") from None
        finally:
            self._scripts.pop(number, None)

    async def open(self, command: str, cols: int, rows: int,
                   echo: bool) -> Channel:
        if self.gone:
            raise ConnectionError(self.gone)
        ch = next(self._numbers)
        channel = AgentChannel(self, ch)
        self._channels[ch] = channel
        self.send_json({"t": "open", "ch": ch, "cmd": command, "cols": cols,
                        "rows": rows, "echo": echo})
        return channel

    def release(self) -> None:
        """Nothing to do: the connection is the computer's, not the
        terminal's. Its channel was closed on its own."""

    def forget(self, ch: int) -> None:
        self._channels.pop(ch, None)

    # ------------------------------------------------------------ in

    def heard(self) -> None:
        """Anything at all came from the agent."""
        self._heard.set()

    def take_bytes(self, frame: bytes) -> None:
        if len(frame) < 5 or frame[0] != OUTPUT:
            return
        channel = self._channels.get(int.from_bytes(frame[1:5], "big"))
        if channel is not None:
            channel.output(frame[5:])

    def take_json(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "exit":
            ch = message.get("ch")
            channel = self._channels.pop(ch, None) if _whole(ch) else None
            if channel is not None:
                code = message.get("code")
                error = message.get("error")
                if isinstance(error, str) and error:
                    # The shell never started: the terminal says why.
                    channel.lost(f"the agent could not start the shell "
                                 f"({error[:200]})")
                else:
                    channel.ended(code if _whole(code) else None)
        elif kind == "ran":
            number = message.get("id")
            answer = self._scripts.get(number) if _whole(number) else None
            if answer is not None and not answer.done():
                code = message.get("code")
                answer.set_result((code if _whole(code) else None,
                                   _b64(message.get("out")),
                                   _b64(message.get("err"))))
        elif kind in ("hb", "pong"):
            # Answering is a must: the agent takes a long silence for a dead
            # link. One answer waiting to go is enough, however many come.
            if not self._hb_waiting:
                self._hb_waiting = True
                self._put(HB)

    def lost(self, reason: str) -> None:
        """The connection is gone: every terminal on it ends, every script
        asked fails, nothing more is sent."""
        if self.gone:
            return
        self.gone = reason
        for channel in list(self._channels.values()):
            channel.lost(reason)
        self._channels.clear()
        for answer in self._scripts.values():
            if not answer.done():
                answer.set_exception(LinkError(reason))
        while not self._outbox.empty():
            self._outbox.get_nowait()
        self._queued = 0
        self._outbox.put_nowait(None)
        self._heard.set()


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _b64(value) -> bytes:
    if not isinstance(value, str):
        return b""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return b""


class Agents:
    """The computers whose agent is connected right now."""

    def __init__(self) -> None:
        self._links: dict[uuid.UUID, AgentLink] = {}
        #: Connections that have not said who they are yet, oldest first,
        #: each with its address.
        self._waiting: dict[object, tuple[str, WebSocket]] = {}
        self._letting_go: set[asyncio.Task] = set()
        self._let_go_at: list[float] = []
        #: When each address last failed its hellos, the latest few.
        self._strikes: dict[str, list[float]] = {}

    def attach(self, link: AgentLink) -> None:
        assert link.machine_id is not None
        old = self._links.get(link.machine_id)
        self._links[link.machine_id] = link
        if old is not None and old is not link:
            # Two agents with one key came at the same moment: the later
            # one stays, the other hears it is over.
            old.lost("the agent connected again")
            old.hang_up()

    def detach(self, link: AgentLink) -> bool:
        """Forgets a link that went away, but only if it is still current:
        an agent that came back at once has a newer one by now."""
        if link.machine_id is not None and self._links.get(link.machine_id) is link:
            del self._links[link.machine_id]
            return True
        return False

    def get(self, machine_id: uuid.UUID) -> AgentLink | None:
        link = self._links.get(machine_id)
        return link if link is not None and not link.gone else None

    def online(self, machine_id: uuid.UUID | str) -> bool:
        try:
            key = machine_id if isinstance(machine_id, uuid.UUID) \
                else uuid.UUID(machine_id)
        except ValueError:
            return False
        return self.get(key) is not None

    def describe(self, machine_id: uuid.UUID | str) -> dict | None:
        """What the page shows of a connected agent."""
        try:
            key = machine_id if isinstance(machine_id, uuid.UUID) \
                else uuid.UUID(machine_id)
        except ValueError:
            return None
        link = self.get(key)
        if link is None:
            return None
        return {"version": link.version, "since": int(link.connected_at * 1000)}

    def close_all(self, reason: str) -> None:
        for link in list(self._links.values()):
            link.lost(reason)
            link.hang_up()
        self._links.clear()

    @property
    def pending(self) -> int:
        return len(self._waiting)

    def may_wait(self, address: str) -> bool:
        if sum(1 for a, _ in self._waiting.values()
               if a == address) >= PENDING_PER_ADDRESS:
            return False
        if len(self._waiting) < PENDING_MAX:
            return True
        cutoff = time.monotonic() - EVICT_WINDOW
        self._let_go_at = [t for t in self._let_go_at if t >= cutoff]
        return len(self._let_go_at) < EVICTIONS

    def waits(self, address: str, ws: WebSocket) -> object:
        """A connection that has yet to say hello; when too many wait, the
        one that has waited longest is let go."""
        while len(self._waiting) >= PENDING_MAX:
            oldest = next(iter(self._waiting))
            _, stale = self._waiting.pop(oldest)
            self._let_go_at.append(time.monotonic())
            task = asyncio.create_task(_let_go(stale))
            self._letting_go.add(task)
            task.add_done_callback(self._letting_go.discard)
        key = object()
        self._waiting[key] = (address, ws)
        return key

    def waited(self, key: object) -> None:
        self._waiting.pop(key, None)

    def struck_out(self, address: str) -> bool:
        """Whether this address failed too often lately to be looked at."""
        times = self._strikes.get(address)
        if not times:
            return False
        if times[-1] < time.monotonic() - STRIKE_WINDOW:
            del self._strikes[address]
            return False
        recent = [t for t in times if t >= time.monotonic() - STRIKE_WINDOW]
        return len(recent) >= STRIKES

    def strike(self, address: str) -> None:
        now = time.monotonic()
        if address not in self._strikes and len(self._strikes) >= 4096:
            # Forget the quiet ones first, everyone if that is not enough.
            for key in [k for k, v in self._strikes.items()
                        if v[-1] < now - STRIKE_WINDOW]:
                del self._strikes[key]
            if len(self._strikes) >= 4096:
                self._strikes.clear()
        times = self._strikes.setdefault(address, [])
        times.append(now)
        del times[:-STRIKES]


# -------------------------------------------------------- the connection

def address_key(address: str) -> str:
    """Who to count a connection against: its address, or for IPv6 its /64,
    which one person can have whole."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return address


async def _let_go(ws: WebSocket) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(ws.close(code=1013), 5)


def parse_hello(raw: str) -> dict | None:
    """The agent's first message, cleaned; None if it is not one."""
    if len(raw) > HELLO_MAX:
        return None
    try:
        message = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(message, dict) or message.get("t") != "hello":
        return None
    token = message.get("token")
    if not isinstance(token, str) or not token:
        return None
    hello: dict = {"token": token}
    for key, limit in HELLO_TEXT.items():
        value = message.get(key)
        hello[key] = "".join(ch for ch in value[:limit * 2]
                             if ch.isprintable())[:limit] \
            if isinstance(value, str) else ""
    web = message.get("web")
    hello["web"] = web if _whole(web) else 0
    return hello


async def session(ws: WebSocket, pool: asyncpg.Pool, agents: Agents,
                  address: str = "") -> None:
    """One agent's connection, from its hello until it goes."""
    address = address_key(address)
    if not agents.may_wait(address):
        # Not even accepted. A real agent tries again in a moment.
        with contextlib.suppress(Exception):
            await ws.close(code=1013)
        return
    await ws.accept()
    waiting = agents.waits(address, ws)
    try:
        met = await _meet(ws, pool, agents, address, waiting)
    finally:
        agents.waited(waiting)
    if met is None:
        return
    hello, known, claimed = met

    async def send(item: str | bytes) -> None:
        if isinstance(item, bytes):
            await ws.send_bytes(item)
        else:
            await ws.send_text(item)

    async def close() -> None:
        await ws.close()

    link = AgentLink(send, close, hello)
    link.send_json({"t": "welcome",
                    "name": known["name"] if known else link.name})
    reader = asyncio.create_task(_read(ws, link))
    try:
        if known is not None:
            link.machine_id = known["id"]
            agents.attach(link)
            await _seen(pool, known["id"], link)
            log.info("agent online: %s/%s (agent %s)", known["code"],
                     known["name"], link.version or "?")
        else:
            assert claimed is not None
            try:
                machine_id, name = await _join(pool, claimed, link)
            except Exception as exc:        # noqa: BLE001 - said to both sides
                if link.gone:
                    log.info("%s left while joining: %s", link.name, link.gone)
                    await machines.fail_install(pool, claimed["id"],
                                                LEFT_WHILE_JOINING)
                    return
                log.exception("a computer could not join")
                await machines.fail_install(
                    pool, claimed["id"], f"The computer could not be added: {exc}")
                link.send_json({"t": "error", "error":
                                "tTerm Web could not add this computer."})
                await asyncio.sleep(0.2)
                with contextlib.suppress(Exception):
                    await ws.close()
                return
            link.machine_id = machine_id
            agents.attach(link)
        await reader
    finally:
        reader.cancel()
        link.lost("the agent disconnected")
        if agents.detach(link):
            log.info("agent offline: %s", link.name)


async def _meet(ws: WebSocket, pool: asyncpg.Pool, agents: Agents,
                address: str, waiting: object) -> tuple[
                    dict, asyncpg.Record | None, asyncpg.Record | None] | None:
    """Reads the hello and decides: a computer we know, one that joins now
    with its token, or neither, and then the agent hears why."""
    try:
        first = await asyncio.wait_for(ws.receive(), HELLO_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        with contextlib.suppress(Exception):
            await ws.close()
        return None
    # It said something: from here it is not waiting, and is not the one
    # let go when too many wait, even while its key is looked up.
    agents.waited(waiting)
    if first.get("type") == "websocket.disconnect":
        return None
    text = first.get("text")
    hello = parse_hello(text) if isinstance(text, str) else None
    if hello is None:
        agents.strike(address)
        await _refuse(ws, "The agent's hello was not understood.")
        return None
    if hello["web"] < WEB_PROTOCOL:
        # Knocking again will not make it newer.
        await _refuse(ws, TOO_OLD, revoked=True)
        return None

    known = await _known(pool, hello["token"])
    if known is not None:
        current = agents.get(known["id"])
        if current is not None:
            # The same key again. Either this agent came back before we
            # noticed its old connection die, or the key is in two places
            # at once; the old connection answering tells which. Without
            # the question the two would take the computer from each
            # other every time one of them reconnected.
            if await current.answers(PROBE_TIMEOUT):
                await _refuse(ws, TWICE)
                return None
            current.lost("the agent connected again")
            current.hang_up()
        return hello, known, None

    # A token no computer has as its key. An address that keeps sending
    # those is not looked up any more for a while; the computers it already
    # has, behind the same office or carrier address, still come in above.
    if agents.struck_out(address):
        await _refuse(ws, TOO_MANY)
        return None
    problem = await _install_problem(pool, hello["token"])
    if problem is not None:
        agents.strike(address)
        await _refuse(ws, *problem)
        return None
    try:
        claimed = await machines.claim(pool, hello["token"])
    except machines.EnrollRefused as refused:
        # A token that never was, or has expired, will not start working:
        # the agent is told to stop rather than to knock forever. One in
        # use right now may work in a minute.
        if refused.status != 409:
            agents.strike(address)
        await _refuse(ws, str(refused) if refused.status != 404 else NOT_KNOWN,
                      revoked=refused.status in (404, 410))
        return None
    return hello, None, claimed


async def _read(ws: WebSocket, link: AgentLink) -> None:
    """Everything the agent sends, until it goes; then the link is over at
    once, and with it whatever waits on it."""
    try:
        while True:
            try:
                message = await ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return
            if message.get("type") == "websocket.disconnect":
                return
            link.heard()
            data = message.get("bytes")
            if data is not None:
                link.take_bytes(data)
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                parsed = json.loads(text)
            except (ValueError, RecursionError):
                continue
            if isinstance(parsed, dict):
                link.take_json(parsed)
    finally:
        link.lost("the agent disconnected")


async def _refuse(ws: WebSocket, error: str, revoked: bool = False) -> None:
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps({"t": "error", "error": error,
                                       "revoked": revoked}))
        await ws.close()


async def _known(pool: asyncpg.Pool, token: str) -> asyncpg.Record | None:
    if not machines.TOKEN_RE.match(token):
        return None
    return await pool.fetchrow(
        """
        SELECT m.id, m.name, p.code
        FROM machines m JOIN projects p ON p.id = m.project_id
        WHERE m.agent_token_hash = $1 AND m.kind = 'computer'
          AND m.removed_at IS NULL
        """, machines.token_hash(token))


async def _install_problem(pool: asyncpg.Pool,
                           token: str) -> tuple[str, bool] | None:
    """For a token that is no computer's key: why it cannot join, and
    whether the agent is to stop for good; None if it may try."""
    if not machines.TOKEN_RE.match(token):
        return NOT_KNOWN, True
    row = await pool.fetchrow(
        "SELECT kind, state FROM install_tokens WHERE token_hash = $1",
        machines.token_hash(token))
    if row is None:
        return NOT_KNOWN, True
    if row["kind"] != "computer":
        # A server's command, given to an agent: it stays as it was, for
        # the server it was made for.
        return FOR_A_SERVER, True
    if row["state"] == "connected":
        # It became a computer's key, and that computer was removed.
        return NOT_KNOWN, True
    return None


async def _seen(pool: asyncpg.Pool, machine_id: uuid.UUID,
                link: AgentLink) -> None:
    with contextlib.suppress(Exception):
        await pool.execute(
            "UPDATE machines SET last_seen_at = now(), passport = passport || "
            "jsonb_build_object('agent', $2::text) WHERE id = $1",
            machine_id, link.version)


async def _join(pool: asyncpg.Pool, claimed: asyncpg.Record,
                link: AgentLink) -> tuple[uuid.UUID, str]:
    """Reads the passport through the agent and registers the computer.

    An agent that goes meanwhile is not added: its command works again
    when it comes back. One that is there but could not answer is added
    with what its hello said."""
    try:
        code, out, _ = await link.script(passport.SCRIPT, PASSPORT_TIMEOUT)
        details = passport.parse(out.decode("utf-8", "replace"))
    except LinkError as exc:
        if link.gone:
            raise
        log.info("could not read the passport of %s: %s", link.name, exc)
        details = {"shells": [], "commands": []}
    if link.gone:
        raise LinkError(link.gone)
    details["checked_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    details["agent"] = link.version
    if link.os and "os" not in details:
        details["os"] = link.os
    if link.user:
        details["user"] = link.user
    return await machines.add_computer(pool, claimed, link.name, details)
