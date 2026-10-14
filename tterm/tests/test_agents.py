"""Computers through the agent (0.0.6): the real tterm-agent, run from its
own repository next to this one, against a core on free ports.

The agent starts real shells here, as it would on a laptop: bash, or zsh on
a Mac, in a throwaway home. Its usual entry refuses root, which a check run
in a container may be; the checks start its connection loop directly
instead, the same code without that one check.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

import websockets

from ..core import agents, machines, terminals
from .harness import PASSPORT_OUTPUT, request
from .test_terminals import _home, _login_shell, _prompts, _screen, _until

CHANGE = {"x-tterm": "1", "content-type": "application/json"}

AGENT = Path(os.environ.get("TTERM_AGENT") or
             Path(__file__).resolve().parents[3] / "tterm-agent" / "agent.py")


def _agent_here() -> bool:
    if AGENT.is_file():
        return True
    print(f"  · tterm-agent not found at {AGENT}: the agent checks are skipped")
    return False


class Agent:
    """A real agent process, with what it logged."""

    def __init__(self, hub: str, token: str, home: Path, login: str,
                 name: str = "test-mac") -> None:
        code = ("import asyncio, sys; sys.path.insert(0, %r); import agent; "
                "asyncio.run(agent.main_loop(%r, %r, %r))"
                % (str(AGENT.parent), hub, token, name))
        env = {"HOME": str(home), "SHELL": login, "PATH": os.environ["PATH"],
               "USER": os.environ.get("USER", "tterm"),
               "LANG": "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8",
               "PYTHONUNBUFFERED": "1"}
        self.args = [sys.executable, "-c", code]
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self.log = b""
        self._reader: asyncio.Task | None = None

    async def start(self) -> "Agent":
        self.proc = await asyncio.create_subprocess_exec(
            *self.args, env=self.env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        self._reader = asyncio.create_task(self._read())
        return self

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            self.log += line

    async def stop(self) -> None:
        """Ctrl+C, as a person stops it; the shells end with it."""
        if self.proc is None or self.proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGINT)
        try:
            await asyncio.wait_for(self.proc.wait(), 5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.proc.pid, signal.SIGKILL)
            await self.proc.wait()
        if self._reader:
            await asyncio.wait_for(self._reader, 2)


async def _computer_install(core) -> tuple[str, str]:
    state = (await request(core.page_port, "GET", "/api/state")).json()
    r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                      body=json.dumps({"projectId": state["projects"][0]["id"],
                                       "kind": "computer"}).encode())
    assert r.status == 201, r.text
    # TTERM_TOKEN=KEY python3 agent.py --hub …
    return r.json()["id"], r.json()["command"].split()[0].split("=", 1)[1]


def _hub(core) -> str:
    return f"ws://127.0.0.1:{core.connect_port}/agent"


async def _status(core, install_id: str) -> dict:
    return (await request(core.page_port, "GET",
                          f"/api/installs/{install_id}")).json()


async def _wait_status(core, install_id: str, state: str, agent: Agent,
                       timeout: float = 20) -> dict:
    end = time.monotonic() + timeout
    while True:
        status = await _status(core, install_id)
        if status["state"] == state:
            return status
        assert time.monotonic() < end, \
            f"install {status}, agent said {agent.log.decode()[-400:]!r}"
        await asyncio.sleep(0.1)


def _computer(state: dict) -> dict | None:
    for project in state["projects"]:
        for m in project["machines"]:
            if m["kind"] == "computer":
                return m
    return None


def test_the_hello_is_read_and_cleaned():
    assert agents.parse_hello("nope") is None
    assert agents.parse_hello('{"t": "hello"}') is None, "no token"
    hello = agents.parse_hello(json.dumps({
        "t": "hello", "token": "x" * 32, "name": "Mac\x1b[31m" + "n" * 100,
        "agent": "0.11.0", "web": 1, "os": 5}))
    assert hello is not None
    assert hello["name"] == "Mac[31m" + "n" * 57, "printable, and not too long"
    assert hello["web"] == 1 and hello["os"] == "" and hello["agent"] == "0.11.0"
    old = agents.parse_hello(json.dumps({"t": "hello", "token": "x" * 32,
                                         "channels": 1}))
    assert old is not None and old["web"] == 0, "the bot's agent says no web"


async def test_a_computer_joins_and_its_terminals_work(h):
    if not _agent_here():
        return
    login, full = _login_shell()
    home = _home()
    (home / "src").mkdir()
    first = second = None
    try:
        async with h.core() as core:
            install_id, token = await _computer_install(core)
            assert len(token) == 32
            first = await Agent(_hub(core), token, home, login).start()
            status = await _wait_status(core, install_id, "connected", first)
            machine = status["machine"]
            assert machine["name"] == "test-mac" and machine["host"] is None
            state = (await request(core.page_port, "GET", "/api/state")).json()
            listed = _computer(state)
            assert listed and listed["online"] and \
                listed["agent"]["version"] == "0.11.0", listed
            assert listed["passport"].get("agent") == "0.11.0"
            assert listed["passport"].get("os"), "the passport came through"
            machine_id = uuid.UUID(machine["id"])

            # A terminal on it, as on a server: markers, blocks, its size.
            t = await core.terminals.open(machine_id, 100, 30)
            await _until(lambda: _prompts(t) >= 1, what="the first prompt")
            assert t.markers and t.blocks == full
            assert t._key.encode() not in _screen(t), "the key never shows"
            t.write(b"stty size; printf '\\320\\266-%s\\n' ok\n")
            await _until(lambda: _prompts(t) >= 2, what="stty")
            assert b"30 100" in _screen(t) and "ж-ok".encode() in _screen(t)
            t.resize(120, 40)
            await asyncio.sleep(0.2)
            t.write(b"stty size\n")
            await _until(lambda: _prompts(t) >= 3, what="the new size")
            assert b"40 120" in _screen(t)
            # Tab and the lists go through the agent too.
            got = await t.complete("ls s", 4)
            assert "src/" in [i["text"] for i in got["items"]], got
            assert "src" in (await t.dirs())["dirs"]
            if full:
                assert t.run("echo from-the-line") is None
                await _until(lambda: _prompts(t) >= 4, what="run")
                assert b"from-the-line" in _screen(t)
            # exit: the shell's own end, with its code.
            t.write(b"exit 7\n")
            await _until(lambda: t.state == "ended", what="exit")
            assert t.reason == "The shell ended with code 7.", t.reason

            # Two at once, each its own shell; one closed, the other goes on.
            a = await core.terminals.open(machine_id, 80, 24)
            b = await core.terminals.open(machine_id, 80, 24)
            await _until(lambda: _prompts(a) >= 1 and _prompts(b) >= 1,
                         what="two prompts")
            a.write(b"cd src\n")
            await _until(lambda: _prompts(a) >= 2, what="cd")
            await a.close()
            assert a.state == "ended" and a.reason == "Closed."
            b.write(b"pwd\n")
            await _until(lambda: _prompts(b) >= 2, what="pwd")
            assert os.path.realpath(b.cwd) == os.path.realpath(home), \
                "b stayed where it was"

            # The agent goes: its terminals end and say why; it is offline.
            await first.stop()
            await _until(lambda: b.state == "ended", what="the agent's end")
            assert "was lost" in (b.reason or ""), b.reason
            state = (await request(core.page_port, "GET", "/api/state")).json()
            assert _computer(state)["online"] is False
            c = await core.terminals.open(machine_id, 80, 24)
            await _until(lambda: c.state == "ended", what="offline")
            assert "not connected right now" in (c.reason or ""), c.reason

            # Back with the same token: the same machine, no new install.
            second = await Agent(_hub(core), token, home, login).start()
            await _until(lambda: core.agents.online(machine_id), 15,
                         what="the agent back")
            d = await core.terminals.open(machine_id, 80, 24)
            await _until(lambda: _prompts(d) >= 1, what="a prompt again")
            count = await core.pool.fetchval(
                "SELECT count(*) FROM machines WHERE kind = 'computer'")
            assert count == 1
            await d.close()
            journal = [r["kind"] for r in await core.pool.fetch(
                "SELECT kind FROM journal ORDER BY id")]
            assert "machine.added" in journal
    finally:
        for agent in (first, second):
            if agent is not None:
                await agent.stop()
        shutil.rmtree(home, ignore_errors=True)


async def test_tokens_and_agents_that_do_not_fit(h):
    async with h.core() as core:
        hub = _hub(core)

        async def hello(**extra) -> dict:
            async with websockets.connect(hub, proxy=None) as ws:
                await ws.send(json.dumps({"t": "hello", "name": "x", **extra}))
                return json.loads(await asyncio.wait_for(ws.recv(), 10))

        reply = await hello(token="t" * 32, channels=1)
        assert reply["t"] == "error" and "too old" in reply["error"] and \
            reply["revoked"] is True, "knocking again will not make it newer"
        reply = await hello(token="t" * 32, web=1)
        assert reply["t"] == "error" and reply["revoked"] is True, \
            "an unknown token tells the agent to stop"
        # A server's command, given to an agent.
        state = (await request(core.page_port, "GET", "/api/state")).json()
        r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                          body=json.dumps({"projectId": state["projects"][0]["id"],
                                           "kind": "server"}).encode())
        server_token = r.json()["command"].split("/s/")[1].split()[0]
        reply = await hello(token=server_token, web=1)
        assert reply["t"] == "error" and "for a server" in reply["error"] \
            and reply["revoked"] is True, reply
        status = await _status(core, r.json()["id"])
        assert status["state"] == "waiting" and status["error"] is None, \
            "the server's own command is left as it was"

        # A computer that is not connected cannot open a terminal.
        project = state["projects"][0]["id"]
        machine_id = await core.pool.fetchval(
            "INSERT INTO machines (project_id, kind, name) VALUES ($1, "
            "'computer', 'sleepy') RETURNING id", uuid.UUID(project))
        t = await core.terminals.open(machine_id, 80, 24)
        await _until(lambda: t.state == "ended", what="the refusal")
        assert "sleepy is not connected right now" in (t.reason or "")
        assert isinstance(t.machine, terminals.Machine) and \
            t.machine.kind == "computer"

    if not _agent_here():
        return
    # The real agent with a token that was never issued stops for good.
    async with h.core() as core:
        home = _home()
        try:
            agent = await Agent(_hub(core), machines.new_token("computer"),
                                home, "/bin/sh").start()
            assert agent.proc is not None
            await asyncio.wait_for(agent.proc.wait(), 15)
            await asyncio.wait_for(agent._reader, 2)  # type: ignore[arg-type]
            assert b"access revoked" in agent.log, agent.log
        finally:
            shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------- agents spoken by hand

async def _hello(hub: str, token: str, name: str = "fake-mac",
                 address: str | None = None):
    """A connection that says hello as the agent would; it and the answer.
    The address is the one the tunnel would say it came from."""
    headers = {"cf-connecting-ip": address} if address else None
    ws = await websockets.connect(hub, proxy=None, max_size=None,
                                  additional_headers=headers)
    await ws.send(json.dumps({"t": "hello", "token": token, "name": name,
                              "user": "u", "os": "Darwin 24.1.0",
                              "agent": "0.11.0", "shell": "zsh",
                              "channels": 1, "web": 1}))
    return ws, json.loads(await asyncio.wait_for(ws.recv(), 10))


async def _next(ws, kind: str) -> dict:
    while True:
        raw = await asyncio.wait_for(ws.recv(), 10)
        if isinstance(raw, str) and json.loads(raw).get("t") == kind:
            return json.loads(raw)


async def _answer_passport(ws) -> None:
    run = await _next(ws, "run")
    await ws.send(json.dumps({
        "t": "ran", "id": run["id"], "code": 0, "err": "",
        "out": base64.b64encode(PASSPORT_OUTPUT.encode()).decode()}))


async def _answer_pings(ws) -> None:
    """What a live agent does: answers the core's ping."""
    async for raw in ws:
        if isinstance(raw, str) and json.loads(raw).get("t") == "ping":
            await ws.send(json.dumps({"t": "pong"}))


async def _until_status(core, install_id: str, state: str,
                        timeout: float = 10) -> dict:
    end = time.monotonic() + timeout
    while True:
        status = await _status(core, install_id)
        if status["state"] == state:
            return status
        assert time.monotonic() < end, f"install {status}, not {state}"
        await asyncio.sleep(0.05)


async def test_an_agent_that_leaves_while_joining(h):
    """The agent goes while its passport is read: nothing is added, and the
    same command works when it comes back, at once."""
    async with h.core() as core:
        install_id, token = await _computer_install(core)
        ws, reply = await _hello(_hub(core), token)
        assert reply["t"] == "welcome", reply
        await _next(ws, "run")
        await ws.close()
        status = await _until_status(core, install_id, "failed", 3)
        assert "disconnected" in status["error"], status
        assert await core.pool.fetchval(
            "SELECT count(*) FROM machines WHERE kind = 'computer'") == 0
        ws, reply = await _hello(_hub(core), token)
        assert reply["t"] == "welcome", reply
        await _answer_passport(ws)
        status = await _until_status(core, install_id, "connected")
        assert status["machine"]["os"] == "Ubuntu 24.04.1 LTS", status
        await ws.close()


async def test_one_key_in_two_places(h):
    """A second agent with a computer's key does not take it over while
    the first answers; once the first is silent, it does, and the first
    connection is closed."""
    async with h.core() as core:
        hub = _hub(core)
        install_id, token = await _computer_install(core)
        a, reply = await _hello(hub, token, "laptop-a")
        await _answer_passport(a)
        status = await _until_status(core, install_id, "connected")
        machine_id = uuid.UUID(status["machine"]["id"])
        first = core.agents.get(machine_id)
        assert first is not None
        answering = asyncio.create_task(_answer_pings(a))
        b, reply = await _hello(hub, token, "laptop-b")
        assert reply["t"] == "error" and "already connected" in reply["error"] \
            and reply["revoked"] is False, reply
        assert core.agents.get(machine_id) is first
        await b.close()

        answering.cancel()
        started = time.monotonic()
        c, reply = await _hello(hub, token, "laptop-a")
        assert reply["t"] == "welcome", reply
        assert time.monotonic() - started >= agents.PROBE_TIMEOUT - 0.3
        await _until(lambda: core.agents.get(machine_id) not in (None, first),
                     what="the new connection")
        assert first.gone == "the agent connected again"
        await asyncio.wait_for(a.wait_closed(), 10)
        assert await core.pool.fetchval(
            "SELECT count(*) FROM machines WHERE kind = 'computer'") == 1
        await c.close()


async def test_computers_removed_or_alike(h):
    async with h.core() as core:
        hub = _hub(core)
        # Two computers of the same name are two machines: a name is
        # whatever a computer calls itself.
        one_id, one = await _computer_install(core)
        a, _ = await _hello(hub, one, "MacBook-Pro")
        await _answer_passport(a)
        await _until_status(core, one_id, "connected")
        two_id, two = await _computer_install(core)
        b, _ = await _hello(hub, two, "MacBook-Pro")
        await _answer_passport(b)
        status = await _until_status(core, two_id, "connected")
        assert status["machine"]["name"] == "MacBook-Pro-2", status
        names = sorted(r["name"] for r in await core.pool.fetch(
            "SELECT name FROM machines WHERE kind = 'computer'"))
        assert names == ["MacBook-Pro", "MacBook-Pro-2"], names
        await a.close()
        await b.close()
        # A removed computer's agent is told to stop.
        await core.pool.execute(
            "UPDATE machines SET removed_at = now() WHERE name = 'MacBook-Pro'")
        ws, reply = await _hello(hub, one, "MacBook-Pro")
        assert reply["t"] == "error" and reply["revoked"] is True and \
            "removed" in reply["error"], reply
        await ws.close()


async def test_strangers_are_kept_short(h):
    """/agent is open to the internet: what a stranger can make it do is
    bounded, and it cannot keep the agents out."""
    async with h.core() as core:
        hub = _hub(core)

        async def idle(address: str):
            return await websockets.connect(
                hub, proxy=None, additional_headers={"cf-connecting-ip": address})

        async def turned_away(address: str) -> bool:
            try:
                extra = await idle(address)
            except websockets.exceptions.InvalidStatus:
                return True
            await extra.close()
            return False

        # Only so many from one address may wait to say hello, an IPv6 one
        # counted by its /64; the next is not even accepted. (They may wait
        # longer than usual here, so that none goes on its own meanwhile.)
        hello_timeout, agents.HELLO_TIMEOUT = agents.HELLO_TIMEOUT, 60
        waiting = [await idle("10.0.0.1")
                   for _ in range(agents.PENDING_PER_ADDRESS)]
        try:
            await _until(lambda: core.agents.pending == len(waiting),
                         what="them waiting")
            assert await turned_away("10.0.0.1"), "one too many from one address"
            waiting += [await idle(f"2001:db8:1:2::{n}")
                        for n in range(1, agents.PENDING_PER_ADDRESS + 1)]
            assert await turned_away("2001:db8:1:2::ff"), "a /64 is one address"
            # Past the limit in all, the one that waited longest goes and
            # the newcomer is heard.
            n = 2
            while len(waiting) < agents.PENDING_MAX:
                waiting.append(await idle(f"10.0.{n // 200}.{n % 200 + 2}"))
                n += 1
            await _until(lambda: core.agents.pending == agents.PENDING_MAX,
                         what="all of them waiting")
            ws, reply = await _hello(hub, "t" * 32, address="10.9.9.9")
            assert reply["t"] == "error" and "not in tTerm Web" in reply["error"], \
                "the newcomer is heard"
            await ws.close()
            await asyncio.wait_for(waiting[0].wait_closed(), 5)
            assert waiting[0].close_code == 1013, waiting[0].close_code
            # Only so many are let go in a while; then the door again.
            waiting.append(await idle("10.8.0.1"))
            await _until(lambda: core.agents.pending == agents.PENDING_MAX,
                         what="full again")
            for i in range(agents.EVICTIONS - 1):
                waiting.append(await idle(f"10.8.1.{i + 1}"))
            assert await turned_away("10.8.2.1"), "past the evictions allowed"
            assert core.agents.pending == agents.PENDING_MAX
        finally:
            agents.HELLO_TIMEOUT = hello_timeout
            for ws in waiting:
                await ws.close()
        await _until(lambda: core.agents.pending == 0, what="them gone")

        # A hello far too long is not read; deep nesting breaks nothing.
        ws, reply = await _hello(hub, "t" * 32, "n" * 20000)
        assert reply["t"] == "error" and "not understood" in reply["error"]
        await ws.close()
        async with websockets.connect(hub, proxy=None, max_size=None) as ws:
            await ws.send("[" * 100000 + "]" * 100000)
            reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert "not understood" in reply["error"]

        # A connected agent that sends nonsense stays connected.
        install_id, token = await _computer_install(core)
        a, reply = await _hello(hub, token)
        await _answer_passport(a)
        status = await _until_status(core, install_id, "connected")
        link = core.agents.get(uuid.UUID(status["machine"]["id"]))
        assert link is not None
        for nonsense in ("[" * 100000 + "]" * 100000, "{]", "[1]",
                         json.dumps({"t": "ran", "id": [1], "code": 0}),
                         json.dumps({"t": "ran", "id": True}),
                         json.dumps({"t": "exit", "ch": {"a": 1}}),
                         json.dumps({"t": "exit", "ch": 5, "code": 1e300})):
            await a.send(nonsense)
        await a.send(json.dumps({"t": "hb"}))
        await _next(a, "hb")
        assert not link.gone and core.agents.get(link.machine_id) is link
        await a.close()

        # An address whose unknown tokens keep failing waits, without a
        # look. The computers it already has still come in, and other
        # addresses are not touched.
        for n in range(agents.STRIKES - 2):
            ws, reply = await _hello(hub, f"unknown-token-{n:04d}-abcdefghijk")
            assert reply["revoked"] is True and "not in tTerm Web" in reply["error"]
            await ws.close()
        ws, reply = await _hello(hub, "unknown-token-last-abcdefghijk")
        assert "Too many failed attempts" in reply["error"] and \
            reply["revoked"] is False, reply
        await ws.close()
        ws, reply = await _hello(hub, token)
        assert reply["t"] == "welcome", f"a known computer still comes in: {reply}"
        await ws.close()
        ws, reply = await _hello(hub, "unknown-token-last-abcdefghijk",
                                 address="10.1.2.3")
        assert "not in tTerm Web" in reply["error"], reply
        await ws.close()


async def test_what_waits_for_an_agent_is_bounded():
    # An agent that reads: the largest paste the page's socket takes goes
    # through whole, in pieces the agent's own limit is far above.
    got: list[bytes] = []

    async def send(item) -> None:
        got.append(item)

    async def close() -> None:
        pass

    link = agents.AgentLink(send, close, {"name": "fast"})
    paste = b"p" * (16 * 1024 * 1024)
    link.send_frame(agents.INPUT, 3, paste)
    await _until(lambda: sum(len(g) - 5 for g in got) == len(paste),
                 what="the whole paste")
    assert not link.gone
    assert max(len(g) for g in got) == agents.INPUT_PIECE + 5
    assert all(g[:5] == bytes([agents.INPUT]) + (3).to_bytes(4, "big")
               for g in got)
    sent = len(got)
    link.send_frame(agents.INPUT, 3, b"")
    await asyncio.sleep(0.02)
    assert len(got) == sent, "nothing to send for nothing typed"
    link.lost("done")

    # One that stopped reading is dropped before it fills the core.
    closed: list[int] = []
    stuck = asyncio.Event()

    async def stuck_send(item) -> None:
        await stuck.wait()

    async def stuck_close() -> None:
        closed.append(1)

    slow = agents.AgentLink(stuck_send, stuck_close, {"name": "slow"})
    for _ in range(100):
        slow.take_json({"t": "hb"})
    assert slow._outbox.qsize() == 1, "one answer to its signs of life waits"
    for _ in range(3):
        slow.send_frame(agents.INPUT, 1, paste)
        if slow.gone:
            break
    assert slow.gone and "stopped taking" in slow.gone, slow.gone
    assert slow._outbox.qsize() <= 1 and slow._queued == 0
    await asyncio.sleep(0.05)
    assert closed == [1], "and its connection is closed"
    stuck.set()
