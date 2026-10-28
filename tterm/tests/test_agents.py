"""Computers through the agent (0.0.6), their terminals through a dropped
connection (0.0.7), and their own keys and removal (0.0.8): the real
tterm-agent, run from its own repository next to this one, against a core
on free ports, and agents spoken by hand for what a real one does rarely.

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
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import websockets

from ..core import accounts, agents, machines, terminals
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


def _agent_is_python() -> bool:
    """agent.py, or the agent from tterm-app: a program of its own."""
    return AGENT.suffix == ".py"


def _agent_version() -> str:
    if not _agent_is_python():
        return subprocess.run([str(AGENT), "--version"], capture_output=True,
                              text=True, check=True).stdout.strip()
    found = re.search(r'^__version__ = "([^"]+)"', AGENT.read_text(), re.M)
    assert found, "the agent says its version"
    return found.group(1)


def _at_least(major: int, minor: int) -> bool:
    return tuple(int(n) for n in _agent_version().split(".")[:2]) >= (major, minor)


def _keeps_shells() -> bool:
    """Whether the agent next door keeps its shells through a dropped
    connection (0.12.0 and newer)."""
    return _at_least(0, 12)


class Agent:
    """A real agent process, with what it logged."""

    def __init__(self, hub: str, token: str, home: Path, login: str,
                 name: str = "test-mac", key: str = "") -> None:
        # The key the installer would make (agent 0.13.0 and newer).
        env = {"HOME": str(home), "SHELL": login, "PATH": os.environ["PATH"],
               "USER": os.environ.get("USER", "tterm"),
               "LANG": "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8",
               "PYTHONUNBUFFERED": "1"}
        if _agent_is_python():
            args = (hub, token, name, key) if key else (hub, token, name)
            code = ("import asyncio, sys; sys.path.insert(0, %r); import agent; "
                    "asyncio.run(agent.main_loop(*%r))" % (str(AGENT.parent), args))
            self.args = [sys.executable, "-c", code]
        else:
            # The agent from tterm-app (0.14.0 and newer). In a container run
            # as root, TTERM_AGENT is its build for checks, which does not
            # refuse root (agent/examples/agent_for_tests.rs).
            self.args = [str(AGENT), "--hub", hub, "--token", token,
                         "--name", name] + (["--key", key] if key else [])
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

    async def kill(self) -> None:
        """Gone at once, saying nothing, its shells with it."""
        assert self.proc is not None
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        await self.proc.wait()
        if self._reader:
            await asyncio.wait_for(self._reader, 2)

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
    """Add a machine, Computer: the install and its token, as the installer
    and the agent started by hand both have it."""
    state = (await request(core.page_port, "GET", "/api/state")).json()
    r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                      body=json.dumps({"projectId": state["projects"][0]["id"],
                                       "kind": "computer"}).encode())
    assert r.status == 201, r.text
    # TTERM_TOKEN=TOKEN python3 agent.py --hub …
    token = r.json()["byHand"].split()[0].split("=", 1)[1]
    assert r.json()["command"] == \
        f"curl -fsSL https://connect.test/c/{token} | sh", r.json()
    return r.json()["id"], token


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
    assert old["kept"] == []
    kept = agents.parse_hello(json.dumps({
        "t": "hello", "token": "x" * 32, "web": 2,
        "kept": [3, 3, -1, 0, 2 ** 32, "7", True, 1.5, None, 9]
                + list(range(100, 200))}))
    assert kept is not None and \
        kept["kept"] == [3, 9] + list(range(100, 154)), \
        "channel numbers only, each once, and only so many looked at"
    assert agents.parse_hello(json.dumps({"t": "hello", "token": "x" * 32,
                                          "kept": "1,2"}))["kept"] == []
    # The computer's own key, from the installer (0.0.8): one like the
    # tokens tTerm Web makes, or the hello is not one.
    assert old["key"] is None
    keyed = agents.parse_hello(json.dumps({"t": "hello", "token": "x" * 32,
                                           "key": "k" * 43, "web": 2}))
    assert keyed is not None and keyed["key"] == "k" * 43
    for bad in ("k" * 31, "k" * 65, "k" * 40 + "/", 5, ""):
        assert agents.parse_hello(json.dumps({"t": "hello", "token": "x" * 32,
                                              "key": bad})) is None, bad


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
            version = _agent_version()
            assert listed and listed["online"] and \
                listed["agent"]["version"] == version, listed
            assert listed["passport"].get("agent") == version
            assert listed["passport"].get("os"), "the passport came through"
            machine_id = uuid.UUID(machine["id"])

            # A terminal on it, as on a server: markers, blocks, its size.
            t = await core.terminals.open(core.owner, machine_id, 100, 30)
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
            a = await core.terminals.open(core.owner, machine_id, 80, 24)
            b = await core.terminals.open(core.owner, machine_id, 80, 24)
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

            # The agent is stopped: its terminals end and say why, at once
            # (0.11.0 only drops the connection); the computer is offline.
            await first.stop()
            await _until(lambda: b.state == "ended", what="the agent's end")
            if _keeps_shells():
                assert b.reason == ("The agent on test-mac was stopped: this "
                                    "terminal has ended."), b.reason
            else:
                assert "was lost" in (b.reason or ""), b.reason
            state = (await request(core.page_port, "GET", "/api/state")).json()
            assert _computer(state)["online"] is False
            c = await core.terminals.open(core.owner, machine_id, 80, 24)
            await _until(lambda: c.state == "ended", what="offline")
            assert "not connected right now" in (c.reason or ""), c.reason

            # Back with the same token: the same machine, no new install.
            second = await Agent(_hub(core), token, home, login).start()
            await _until(lambda: core.agents.online(machine_id), 15,
                         what="the agent back")
            d = await core.terminals.open(core.owner, machine_id, 80, 24)
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


class Cut:
    """The way from the agent to the core, as a network is: cut, both ends
    see their connection end with no goodbye, as when a laptop's Wi-Fi
    drops or it falls asleep. New connections go through again at once."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.pairs: list[tuple[asyncio.StreamWriter, asyncio.StreamWriter]] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._pair, "127.0.0.1", 0)
        return f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/agent"

    async def _pair(self, reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1",
                                                                 self.port)
        except OSError:
            writer.transport.abort()
            return
        self.pairs.append((writer, up_writer))
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    def cut(self) -> None:
        for down, up in self.pairs:
            down.transport.abort()
            up.transport.abort()
        self.pairs.clear()

    async def stop(self) -> None:
        self.cut()
        if self.server is not None:
            self.server.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (OSError, ConnectionError):
        pass
    finally:
        writer.transport.abort()


async def test_a_terminal_waits_for_its_agent(h):
    """The real agent (0.12.0) loses its connection in the middle of a
    command: the terminal waits, away, and goes on where it was when the
    agent is back, the output whole and nothing twice. An agent that comes
    back restarted no longer has the shell: then the terminal says so."""
    if not _agent_here():
        return
    if not _keeps_shells():
        print(f"  · tterm-agent {_agent_version()} does not keep its shells: "
              "skipped")
        return
    login, _ = _login_shell()
    home = _home()
    first = second = None
    try:
        async with h.core() as core:
            cut = Cut(core.connect_port)
            hub = await cut.start()
            try:
                install_id, token = await _computer_install(core)
                first = await Agent(hub, token, home, login).start()
                status = await _wait_status(core, install_id, "connected", first)
                machine_id = uuid.UUID(status["machine"]["id"])
                t = await core.terminals.open(core.owner, machine_id, 80, 24)
                await _until(lambda: _prompts(t) >= 1, what="the first prompt")
                n = _prompts(t)
                t.write(b"for i in $(seq 1 60); do echo L$i; sleep 0.05; done\n")
                await _until(lambda: b"L10\r\n" in _screen(t), what="the first lines")

                cut.cut()
                await _until(lambda: t.away, what="the terminal away")
                assert t.state == "open" and not core.agents.online(machine_id)
                assert "asleep or offline" in (t.run("echo x") or "")
                t.resize(100, 33)
                # The agent comes back by itself, and the terminal with it.
                await _until(lambda: not t.away, 20, what="the agent back")
                await _until(lambda: _prompts(t) > n, 20, what="the loop's end")
                # Each line as it shows: what follows the last \r in it.
                lines = [ln.rsplit(b"\r", 1)[-1] for ln in _screen(t).split(b"\r\n")]
                lines = [ln for ln in lines if re.fullmatch(rb"L\d+", ln)]
                assert lines == [f"L{i}".encode() for i in range(1, 61)], lines
                t.write(b"stty size\n")
                await _until(lambda: _prompts(t) > n + 1, what="stty")
                assert b"33 100" in _screen(t), "the size it got while away"
                assert b"keeping 1 terminal(s)" in first.log
                assert b"took terminal" in first.log, first.log.decode()[-600:]

                # Killed while away: its shells went with it. A new one has
                # none of them, and the terminal ends saying so.
                u = await core.terminals.open(core.owner, machine_id, 80, 24)
                await _until(lambda: _prompts(u) >= 1, what="another prompt")
                await first.kill()
                await _until(lambda: t.away and u.away, what="both away")
                second = await Agent(hub, token, home, login).start()
                await _until(lambda: t.state == "ended" and u.state == "ended",
                             20, what="both ended")
                assert u.reason == ("test-mac came back without this terminal: "
                                    "its agent was restarted, or the computer "
                                    "was awake and offline for over an hour."), \
                    u.reason
                v = await core.terminals.open(core.owner, machine_id, 80, 24)
                await _until(lambda: _prompts(v) >= 1, what="a prompt again")
                await v.close()
            finally:
                await cut.stop()
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
        assert "forget" not in reply, "but not to forget a key it may have"
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
        t = await core.terminals.open(core.owner, machine_id, 80, 24)
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
                 address: str | None = None, **extra):
    """A connection that says hello as the agent would; it and the answer.
    The address is the one the tunnel would say it came from; `extra`
    changes the hello (web 2 and the shells it kept, say)."""
    headers = {"cf-connecting-ip": address} if address else None
    ws = await websockets.connect(hub, proxy=None, max_size=None,
                                  additional_headers=headers)
    await ws.send(json.dumps({"t": "hello", "token": token, "name": name,
                              "user": "u", "os": "Darwin 24.1.0",
                              "agent": "0.11.0", "shell": "zsh",
                              "channels": 1, "web": 1, **extra}))
    return ws, json.loads(await asyncio.wait_for(ws.recv(), 10))


class FakeMac:
    """An agent spoken by hand on one connection, past its hello: it answers
    what the core asks, keeps what it is sent, and prints what it is told.
    It has no shell scripts, so its terminals start without markers."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.got: list[dict] = []
        self.keys: dict[int, bytearray] = {}
        self.task = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        with contextlib.suppress(websockets.ConnectionClosed):
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    ch = int.from_bytes(raw[1:5], "big")
                    self.keys.setdefault(ch, bytearray()).extend(raw[5:])
                    continue
                msg = json.loads(raw)
                if msg.get("t") == "run":
                    await self.ws.send(json.dumps({"t": "ran", "id": msg["id"],
                                                   "code": 1, "out": "",
                                                   "err": ""}))
                elif msg.get("t") == "ping":
                    await self.ws.send(json.dumps({"t": "pong"}))
                self.got.append(msg)

    def sent(self, kind: str) -> list[dict]:
        return [m for m in self.got if m.get("t") == kind]

    async def say(self, **message) -> None:
        await self.ws.send(json.dumps(message))

    async def prints(self, ch: int, data: bytes) -> None:
        await self.ws.send(bytes([agents.OUTPUT]) + ch.to_bytes(4, "big") + data)

    async def opened(self, core, machine_id: uuid.UUID):
        """A new terminal on it, open; it and its channel."""
        t = await core.terminals.open(core.owner, machine_id, 80, 24)
        known = {m["ch"] for m in self.sent("open")}
        await _until(lambda: t.state == "open", what="the terminal open")
        ch = next(m["ch"] for m in self.sent("open") if m["ch"] not in known)
        return t, ch

    async def gone(self) -> None:
        await self.ws.close()
        with contextlib.suppress(BaseException):
            await self.task


async def _fake_mac(core, **hello) -> tuple[FakeMac, uuid.UUID, str]:
    """A computer joined by an agent spoken by hand: it, the machine, and
    its key, to come back with."""
    install_id, token = await _computer_install(core)
    ws, reply = await _hello(_hub(core), token, **hello)
    assert reply["t"] == "welcome", reply
    await _answer_passport(ws)
    status = await _until_status(core, install_id, "connected")
    return FakeMac(ws), uuid.UUID(status["machine"]["id"]), token


async def _back(core, token: str, **hello) -> FakeMac:
    ws, reply = await _hello(_hub(core), token, **hello)
    assert reply["t"] == "welcome", reply
    return FakeMac(ws)


CAME_BACK_WITHOUT = ("fake-mac came back without this terminal: its agent was "
                     "restarted, or the computer was awake and offline for "
                     "over an hour.")


async def test_terminals_wait_for_their_agent(h):
    """An agent that keeps its shells (web 2) goes away: its terminals stay
    open, away, take no keys, and go on from the byte they had when it is
    back; one closed meanwhile, and one nobody knows, are closed there."""
    async with h.core() as core:
        mac, machine_id, token = await _fake_mac(core, web=2, agent="0.12.0")
        t, ch = await mac.opened(core, machine_id)
        u, cu = await mac.opened(core, machine_id)
        assert ch != cu and 0 < ch < 2 ** 31 and 0 < cu < 2 ** 31
        await mac.prints(ch, b"one\r\n")
        await _until(lambda: b"one" in _screen(t), what="output")
        assert t.describe()["away"] is False and t.describe()["awaySince"] is None

        await mac.gone()
        await _until(lambda: t.away and u.away, what="both away")
        seen = t.describe()
        assert seen["state"] == "open" and seen["away"] is True, seen
        assert abs(seen["awaySince"] / 1000 - time.time()) < 10, seen
        assert not core.agents.online(machine_id)
        assert "fake-mac is asleep or offline" in (t.run("echo x") or "")
        try:
            await t.dirs()
            raise AssertionError("no side scripts while away")
        except terminals.NotOpen as exc:
            assert "asleep or offline" in str(exc)
        t.write(b"typed while away")
        t.resize(90, 30)
        # Ended from the page while away: it no longer waits.
        r = await request(core.page_port, "DELETE", f"/api/terminals/{u.id}")
        assert r.status == 403 and u.away, "a change needs the page's header"
        r = await request(core.page_port, "DELETE", f"/api/terminals/{u.id}",
                          headers=CHANGE)
        assert r.status == 200 and r.json()["state"] == "ended", r.text
        assert u.state == "ended" and u.reason == "Closed." and not u.away
        r = await request(core.page_port, "DELETE", f"/api/terminals/{u.id}",
                          headers=CHANGE)
        assert r.status == 200, "an ended one stays in memory a while"

        # Back with both shells and one more nobody knows.
        mac = await _back(core, token, web=2, kept=[ch, cu, 77])
        await _until(lambda: not t.away, what="t back")
        await _until(lambda: len(mac.sent("close")) == 2, what="the closes")
        assert mac.sent("resume") == [{"t": "resume", "ch": ch, "from": 5}]
        assert {"t": "size", "ch": ch, "cols": 90, "rows": 30} in mac.sent("size")
        assert sorted(m["ch"] for m in mac.sent("close")) == sorted([cu, 77])
        assert core.agents.online(machine_id)
        await mac.prints(ch, b"two\r\n")
        t.write(b"k")
        await _until(lambda: b"two" in _screen(t), what="more output")
        await _until(lambda: mac.keys.get(ch) == bytearray(b"k"),
                     what="keys again, and none typed while away")
        assert _screen(t) == b"one\r\ntwo\r\n", _screen(t)
        assert "asleep" not in (t.run("echo x") or ""), "no longer away"

        # Output the agent no longer kept: the count goes on after it. A
        # frame with nothing in it is not the end, and an absurd gap is not
        # believed.
        await mac.say(t="gap", ch=ch, lost=1000)
        await mac.ws.send(bytes([agents.OUTPUT]) + ch.to_bytes(4, "big"))
        await mac.say(t="gap", ch=ch, lost=10 ** 4000)
        await mac.prints(ch, b"three\r\n")
        await _until(lambda: b"three" in _screen(t), what="after the gap")
        assert t.state == "open" and _screen(t) == b"one\r\ntwo\r\nthree\r\n"
        await mac.gone()
        await _until(lambda: t.away, what="away again")
        mac = await _back(core, token, web=2, kept=[ch])
        await _until(lambda: mac.sent("resume"), what="the second resume")
        assert mac.sent("resume")[0]["from"] == 5 + 5 + 1000 + 7

        # It had it, then not: ended, saying so.
        await mac.say(t="exit", ch=ch, code=None, gone=True)
        await _until(lambda: t.state == "ended", what="gone")
        assert t.reason == CAME_BACK_WITHOUT, t.reason
        await mac.gone()


async def test_terminals_that_wait_in_vain(h):
    async with h.core() as core:
        mac, machine_id, token = await _fake_mac(core, web=2)

        # Away longer than terminals wait (made short here).
        away_max, agents.AWAY_MAX = agents.AWAY_MAX, 0.5
        try:
            t, _ = await mac.opened(core, machine_id)
            await mac.gone()
            await _until(lambda: t.away, what="away")
            await _until(lambda: t.state == "ended", 5, what="the wait's end")
        finally:
            agents.AWAY_MAX = away_max
        assert t.reason == ("fake-mac did not come back within 12 hours: this "
                            "terminal has ended."), t.reason
        assert not t.away and core.agents.parked(machine_id) == set()

        # Back, but restarted: its shells are gone.
        mac = await _back(core, token, web=2)
        t, ch = await mac.opened(core, machine_id)
        await mac.gone()
        await _until(lambda: t.away, what="away")
        mac = await _back(core, token, web=2, kept=[])
        await _until(lambda: t.state == "ended", what="the end")
        assert t.reason == CAME_BACK_WITHOUT, t.reason
        assert mac.sent("resume") == [] and mac.sent("close") == []

        # Stopped on purpose: nothing waits, and the terminal says so.
        t, _ = await mac.opened(core, machine_id)
        await mac.say(t="bye")
        await _until(lambda: t.state == "ended", what="bye")
        assert t.reason == ("The agent on fake-mac was stopped: this terminal "
                            "has ended."), t.reason
        assert not t.away and core.agents.parked(machine_id) == set()
        await mac.gone()

        # Back as an agent that does not keep shells: what waited ends, and
        # the shells it says it kept are not its to take back.
        mac = await _back(core, token, web=2)
        t, ch = await mac.opened(core, machine_id)
        await mac.gone()
        await _until(lambda: t.away, what="away")
        mac = await _back(core, token, web=1, kept=[ch])
        await _until(lambda: t.state == "ended", what="the end")
        assert t.reason == CAME_BACK_WITHOUT, t.reason
        assert mac.sent("resume") == [] and mac.sent("close") == []

        # Its own terminals end with its connection, as before 0.0.7.
        t, _ = await mac.opened(core, machine_id)
        await mac.gone()
        await _until(lambda: t.state == "ended", what="the end")
        assert t.reason == ("The connection to fake-mac was lost: the agent "
                            "disconnected"), t.reason
        assert core.agents.parked(machine_id) == set()

        # tTerm Web stopping ends whatever waits.
        mac = await _back(core, token, web=2)
        t, _ = await mac.opened(core, machine_id)
        await mac.gone()
        await _until(lambda: t.away, what="away")
    assert t.state == "ended" and t.reason == "tTerm Web stopped.", t.reason


async def test_channel_numbers_are_not_used_twice():
    """Numbers outlive a connection: a new one is never one the agent kept
    or one a terminal waits with."""
    async def nothing(*_):
        pass

    link = agents.AgentLink(nothing, nothing, {"kept": [5], "web": 2})
    registry = agents.Agents()
    link.machine_id = uuid.uuid4()
    link.agents = registry
    registry.park(link.machine_id, [agents.AgentChannel(link, 6, 80, 24)])
    assert registry.parked(link.machine_id) == {6}
    picks = iter([5, 6, 5, 7])
    real = agents.random.randrange
    agents.random.randrange = lambda *_: next(picks)
    try:
        channel = await link.open("sh", 80, 24, True)
    finally:
        agents.random.randrange = real
    assert channel.ch == 7
    link.lost("done", wait=False)
    registry.close_all("done")
    assert registry.parked(link.machine_id) == set()


async def _next(ws, kind: str) -> dict:
    while True:
        raw = await asyncio.wait_for(ws.recv(), 10)
        if isinstance(raw, str) and json.loads(raw).get("t") == kind:
            return json.loads(raw)


async def _answer_passport(ws, output: str = PASSPORT_OUTPUT) -> None:
    run = await _next(ws, "run")
    await ws.send(json.dumps({
        "t": "ran", "id": run["id"], "code": 0, "err": "",
        "out": base64.b64encode(output.encode()).decode()}))


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
        # The log names a computer that goes offline as tTerm Web does, not
        # by what its agent calls itself.
        said: list[str] = []
        catch = logging.Handler(logging.INFO)
        catch.emit = lambda record: said.append(record.getMessage())  # type: ignore[method-assign]
        agents_log = logging.getLogger("tterm.agents")
        level = agents_log.level
        agents_log.setLevel(logging.INFO)
        agents_log.addHandler(catch)
        try:
            await a.close()
            await b.close()
            await _until(lambda: "agent offline: MacBook-Pro-2" in said, 5,
                         f"the second one offline, said {said}")
        finally:
            agents_log.removeHandler(catch)
            agents_log.setLevel(level)
        # A removed computer's agent is told to stop.
        await core.pool.execute(
            "UPDATE machines SET removed_at = now() WHERE name = 'MacBook-Pro'")
        ws, reply = await _hello(hub, one, "MacBook-Pro")
        assert reply["t"] == "error" and reply["revoked"] is True and \
            "removed" in reply["error"], reply
        await ws.close()


async def test_a_computer_keeps_its_own_key(h):
    """From 0.0.8 the installer makes the computer a key of its own; the
    token from Add a machine only lets it join, once. Run again with a new
    command, it keeps the key: the computer stays the machine it was, and
    the new command's dialog says it is connected."""
    async with h.core() as core:
        hub = _hub(core)
        key = machines.new_token("computer")
        mac, machine_id, token = await _fake_mac(core, web=2, agent="0.13.0",
                                                 key=key)
        assert await core.pool.fetchval(
            "SELECT agent_token_hash FROM machines WHERE id = $1",
            machine_id) == machines.token_hash(key), "known by its key"
        await mac.gone()
        await _until(lambda: not core.agents.online(machine_id), what="offline")
        # The page says when it was last here.
        state = (await request(core.page_port, "GET", "/api/state")).json()
        seen = _computer(state)["lastSeen"]
        assert seen and abs(seen / 1000 - time.time()) < 10, state

        # The token is no key: an agent with it alone is not this computer.
        ws, reply = await _hello(hub, token, web=2)
        assert reply["t"] == "error" and reply["revoked"] is True, reply
        await ws.close()

        # Back with its key, the same agent: in, and its passport, read
        # just now, is not read again.
        mac = await _back(core, token, web=2, agent="0.13.0", key=key)
        await _until(lambda: core.agents.online(machine_id), what="online")
        await asyncio.sleep(0.3)
        assert mac.sent("run") == [], mac.got
        await mac.gone()

        # The installer run again: a new command, the same key, a newer
        # agent. The same machine, and the dialog hears it.
        install_id, second = await _computer_install(core)
        ws, reply = await _hello(hub, second, web=2, agent="0.13.1", key=key)
        assert reply["t"] == "welcome", reply
        status = await _until_status(core, install_id, "connected")
        assert status["machine"]["id"] == str(machine_id), status
        # Its passport is read again: tools come and go, sudo too.
        await _answer_passport(ws, PASSPORT_OUTPUT.replace("sudo=yes",
                                                           "sudo=password"))

        async def read_again() -> bool:
            pp = json.loads(await core.pool.fetchval(
                "SELECT passport FROM machines WHERE id = $1", machine_id))
            return pp.get("sudo_password") is True and pp["agent"] == "0.13.1"

        end = time.monotonic() + 10
        while not await read_again():
            assert time.monotonic() < end, "the passport was not read again"
            await asyncio.sleep(0.05)
        assert await core.pool.fetchval(
            "SELECT count(*) FROM machines WHERE kind = 'computer'") == 1
        journal = [r["kind"] for r in await core.pool.fetch(
            "SELECT kind FROM journal WHERE kind LIKE 'machine.%' ORDER BY id")]
        assert journal == ["machine.added", "machine.agent_installed"], journal
        r = await request(core.connect_port, "GET", f"/c/{second}")
        assert "already used" in r.text and "exit 1" in r.text, r.text
        await ws.close()


async def test_a_computer_is_removed(h):
    """Remove machine (0.0.8): the agent hears it and stops, the computer's
    terminals end and say why, those waiting for it too, and its key no
    longer works."""
    async with h.core() as core:
        hub = _hub(core)
        key = machines.new_token("computer")
        mac, machine_id, token = await _fake_mac(core, web=2, agent="0.13.0",
                                                 key=key)
        t, _ = await mac.opened(core, machine_id)
        r = await request(core.page_port, "DELETE", f"/api/machines/{machine_id}")
        assert r.status == 403 and core.agents.online(machine_id), \
            "a change needs the page's header"
        r = await request(core.page_port, "DELETE", f"/api/machines/{machine_id}",
                          headers=CHANGE)
        assert r.status == 200, r.text
        assert r.json() == {"id": str(machine_id), "name": "fake-mac",
                            "kind": "computer"}, r.json()
        await _until(lambda: t.state == "ended", what="its terminal's end")
        assert t.reason == "fake-mac was removed from tTerm Web.", t.reason
        await asyncio.wait_for(mac.task, 10)
        assert mac.sent("error") == [{"t": "error", "error": agents.REMOVED,
                                      "revoked": True, "forget": True}], mac.got
        assert not core.agents.online(machine_id)
        state = (await request(core.page_port, "GET", "/api/state")).json()
        assert _computer(state) is None and state["terminals"] == [], state
        r = await request(core.page_port, "DELETE", f"/api/machines/{machine_id}",
                          headers=CHANGE)
        assert r.status == 404, "removed once"
        # The terminal's record and the journal stay.
        assert await core.pool.fetchval(
            "SELECT end_reason FROM terminals WHERE id = $1", t.id) == t.reason
        details = await core.pool.fetchval(
            "SELECT details FROM journal WHERE kind = 'machine.removed'")
        assert json.loads(details) == {"name": "fake-mac", "kind": "computer"}
        # Its key works no more: an agent back with it stops for good, and
        # forgets it.
        ws, reply = await _hello(hub, token, web=2, agent="0.13.0", key=key)
        assert reply["t"] == "error" and reply["revoked"] is True and \
            "removed" in reply["error"] and reply.get("forget") is True, reply
        await ws.close()
        # Nor with a new command: the installer run again before the agent
        # heard of the removal kept the key. The dialog says what to do.
        install_id, again = await _computer_install(core)
        ws, reply = await _hello(hub, again, web=2, agent="0.13.0", key=key)
        assert reply["t"] == "error" and reply["revoked"] is True and \
            reply["error"] == agents.KEY_RETIRED and reply["forget"] is True, reply
        await ws.close()
        status = await _status(core, install_id)
        assert status["state"] == "failed" and \
            status["error"] == agents.KEY_RETIRED, status
        # A new key with the same command: in, a new machine.
        ws, reply = await _hello(hub, again, web=2, agent="0.13.0",
                                 key=machines.new_token("computer"))
        assert reply["t"] == "welcome", reply
        await _answer_passport(ws)
        status = await _until_status(core, install_id, "connected")
        assert status["machine"]["id"] != str(machine_id)
        await ws.close()

        # A computer that is away: what waits for it ends as well.
        mac, other_id, _ = await _fake_mac(core, web=2, agent="0.13.0",
                                           key=machines.new_token("computer"),
                                           name="fake-mac")
        u, _ = await mac.opened(core, other_id)
        await mac.gone()
        await _until(lambda: u.away, what="away")
        r = await request(core.page_port, "DELETE", f"/api/machines/{other_id}",
                          headers=CHANGE)
        assert r.status == 200, r.text
        await _until(lambda: u.state == "ended", what="the wait's end")
        assert u.reason == f"{u.machine.name} was removed from tTerm Web." \
            and not u.away, u.reason
        assert core.agents.parked(other_id) == set()


async def test_a_computer_is_in_one_project(h):
    """The installer run on a computer that is in another project already:
    the new command's dialog says where it is, rather than wait."""
    async with h.core() as core:
        key = machines.new_token("computer")
        mac, machine_id, _ = await _fake_mac(core, web=2, agent="0.13.0", key=key)
        await mac.gone()
        async with core.pool.acquire() as conn:
            other = await accounts.create_project(conn, core.owner.id, "Work", "W")
        r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                          body=json.dumps({"projectId": str(other),
                                           "kind": "computer"}).encode())
        token = r.json()["byHand"].split()[0].split("=", 1)[1]
        mac = await _back(core, token, web=2, agent="0.13.0", key=key)
        status = await _until_status(core, r.json()["id"], "failed")
        assert status["error"] == (
            "This computer is in tTerm Web already, in another project. Remove "
            "it there first, or run ~/.tterm-web/uninstall.sh on it and then "
            "this command."), status
        assert core.agents.online(machine_id), "it stays where it was"
        await mac.gone()


async def test_removed_while_it_says_hello(h):
    """Removed between the agent's hello and its being attached: Remove
    machine did not find it connected, so it hears it right after."""
    async with h.core() as core:
        key = machines.new_token("computer")
        mac, machine_id, token = await _fake_mac(core, web=2, agent="0.13.0",
                                                 key=key)
        await mac.gone()
        await _until(lambda: not core.agents.online(machine_id), what="offline")
        known = await agents._known(core.pool, key)
        await core.pool.execute(
            "UPDATE machines SET removed_at = now() WHERE id = $1", machine_id)
        real = agents._known

        async def as_it_was(pool, token):
            return known

        agents._known = as_it_was
        try:
            ws, reply = await _hello(_hub(core), token, web=2, agent="0.13.0",
                                     key=key)
        finally:
            agents._known = real
        assert reply["t"] == "welcome", reply
        assert await _next(ws, "error") == {"t": "error", "error": agents.REMOVED,
                                            "revoked": True, "forget": True}
        await asyncio.wait_for(ws.wait_closed(), 10)
        await _until(lambda: not core.agents.online(machine_id), what="gone")


async def test_the_agent_with_a_key_of_its_own(h):
    """The real agent (0.13.0) as the installer starts it, with a key of its
    own. Removed in tTerm Web, it hears so, ends its shells and stops."""
    if not _agent_here():
        return
    if not _at_least(0, 13):
        print(f"  · tterm-agent {_agent_version()} brings no key of its own: "
              "skipped")
        return
    login, _ = _login_shell()
    home = _home()
    agent = None
    try:
        async with h.core() as core:
            install_id, token = await _computer_install(core)
            key = machines.new_token("computer")
            agent = await Agent(_hub(core), token, home, login, key=key).start()
            status = await _wait_status(core, install_id, "connected", agent)
            machine_id = uuid.UUID(status["machine"]["id"])
            assert await core.pool.fetchval(
                "SELECT agent_token_hash FROM machines WHERE id = $1",
                machine_id) == machines.token_hash(key)
            t = await core.terminals.open(core.owner, machine_id, 80, 24)
            await _until(lambda: _prompts(t) >= 1, what="the first prompt")
            r = await request(core.page_port, "DELETE",
                              f"/api/machines/{machine_id}", headers=CHANGE)
            assert r.status == 200, r.text
            assert agent.proc is not None
            await asyncio.wait_for(agent.proc.wait(), 15)
            await asyncio.wait_for(agent._reader, 2)  # type: ignore[arg-type]
            assert agent.proc.returncode == 0, "stopped, not failed: no restart"
            assert b"access revoked: This computer was removed" in agent.log, \
                agent.log.decode()[-600:]
            await _until(lambda: t.state == "ended", what="the end")
            assert t.reason == "test-mac was removed from tTerm Web.", t.reason
    finally:
        if agent is not None:
            await agent.stop()
        shutil.rmtree(home, ignore_errors=True)


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
