"""Connecting servers: tokens, the installer, registration over SSH."""
import asyncio
import json
import os
import re
import socket
import subprocess
import tempfile
import uuid
from types import SimpleNamespace

import asyncssh

from ..api.connect import client_ip
from ..core import machines, passport, ssh
from .harness import PASSPORT_OUTPUT, request, ssh_server

CHANGE = {"x-tterm": "1", "content-type": "application/json"}


def test_tokens_and_commands():
    token = machines.new_token()
    assert machines.TOKEN_RE.match(token), token
    assert machines.new_token() != token
    assert len(machines.token_hash(token)) == 32
    assert machines.command("https://connect-dev.tterm.net", "abcDEF123_-x") == \
        "curl -sSL https://connect-dev.tterm.net/s/abcDEF123_-x | sudo sh"
    key = machines.new_token("computer")
    assert len(key) == 32 and machines.TOKEN_RE.match(key), key
    # One in sixty-four would start with "-" and read as an option.
    drawn = [machines.new_token(kind) for kind in ("server", "computer")
             for _ in range(500)]
    assert not [t for t in drawn if t.startswith("-")]
    assert machines.hub_url("https://connect-dev.tterm.net") == \
        "wss://connect-dev.tterm.net/agent"
    assert machines.hub_url("http://127.0.0.1:8091") == "ws://127.0.0.1:8091/agent"
    # The key in the environment, out of the list of processes.
    assert machines.agent_command("https://c.test", key) == \
        f"TTERM_TOKEN={key} python3 agent.py --hub wss://c.test/agent"
    # From 0.0.8 a computer's command is an installer, run as its person.
    assert machines.computer_command("https://c.test", key) == \
        f"curl -fsSL https://c.test/c/{key} | sh"
    assert machines.KEY_RE.match(key) and not machines.KEY_RE.match(key[:31])


def test_a_removed_server_gets_its_line_back():
    """The command shown after a server is removed takes out tTerm Web's
    line in tterm's authorized_keys and nothing else: not the bot's, not a
    line of another tTerm Web."""
    command = machines.server_cleanup("ca-20261012-ffcd")
    assert command == ("sudo sed -i '/ tterm-web ca-20261012-ffcd$/d' "
                       "~tterm/.ssh/authorized_keys"), command
    assert machines.server_cleanup(None) is None
    assert machines.server_cleanup("ca'; reboot; '") is None, \
        "nothing odd goes into a command a person runs"
    lines = ["cert-authority ssh-ed25519 AAAAbot tterm-bot",
             "cert-authority ssh-ed25519 AAAAweb tterm-web ca-20261012-ffcd",
             "cert-authority ssh-ed25519 AAAAnew tterm-web ca-20261012-ffcd2",
             "ssh-ed25519 AAAAperson denis@mac"]
    with tempfile.NamedTemporaryFile("w", suffix=".keys", delete=False) as f:
        f.write("\n".join(lines) + "\n")
    try:
        # The script of the command, without -i: BSD sed (a Mac running the
        # checks) reads -i differently; the servers have GNU sed.
        script = command.split("'")[1]
        out = subprocess.run(["sed", script, f.name], capture_output=True,
                             text=True, check=True).stdout
    finally:
        os.unlink(f.name)
    assert out.splitlines() == [lines[0], lines[2], lines[3]], out


def test_machine_names():
    assert machines.machine_name("web-01.example.com") == "web-01"
    assert machines.machine_name("My Box!") == "My-Box"
    assert machines.machine_name("...") == "server"
    assert machines.free_name("web", {"db"}) == "web"
    assert machines.free_name("web", {"WEB", "web-2"}) == "web-3"


def test_installer_is_filled_in_and_safe():
    ca = asyncssh.generate_private_key("ssh-ed25519", comment="tterm-web ca-x")
    public = ca.export_public_key().decode().strip()
    script = machines.render_installer(token="abcDEF123_-x", ca_key=public,
                                       ca_id="ca-x",
                                       connect_url="https://connect.test")
    assert "{{" not in script
    assert 'TOKEN="abcDEF123_-x"' in script
    assert 'API_URL="https://connect.test"' in script
    key = " ".join(public.split()[:2])
    assert f'CA_KEY="{key}"' in script, "the CA key without its comment"
    assert "sshd_config" in script and "set -eu" in script
    try:
        machines.render_installer(token="$(reboot)abc", ca_key=public,
                                  ca_id="ca-x", connect_url="https://x")
    except ValueError:
        pass
    else:
        raise AssertionError("a token that is not a token was put in a script")
    refusal = machines.refusal_script("It's expired")
    assert refusal.startswith("#!/bin/sh") and "exit 1" in refusal
    assert "It's" not in refusal, "quotes cannot break out of the message"


def test_host_keys_are_cleaned():
    key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    bare = " ".join(key.split()[:2])
    assert machines.clean_host_keys([key + " root@web", "garbage", "", bare]) \
        == [bare], "valid keys only, without comments, once each"


async def test_a_command_stuck_mid_check_can_be_used_again(h):
    async with h.core() as core:
        _, token = await _install(core)
        await core.pool.execute(
            "UPDATE install_tokens SET state = 'connecting'")
        try:
            await machines.claim(core.pool, token)
        except machines.EnrollRefused as exc:
            assert exc.status == 409 and "right now" in str(exc)
        else:
            raise AssertionError("claimed a command in use")
        await core.pool.execute(
            "UPDATE install_tokens SET updated_at = now() - interval '3 minutes'")
        row = await machines.claim(core.pool, token)
        assert row is not None, "an attempt that died does not block it"


def test_passport_is_read_from_the_script_output():
    pp = passport.parse(PASSPORT_OUTPUT)
    assert pp["os"] == "Ubuntu 24.04.1 LTS" and pp["arch"] == "x86_64"
    assert pp["cpus"] == 1 and pp["memory_kb"] == 1003456
    assert pp["sudo"] is True and pp["systemd"] is True
    assert pp["shells"] == ["bash"] and pp["package_manager"] == "apt-get"
    assert pp["commands"] == ["apt", "git", "ls", "systemctl"]
    assert pp["sudo_password"] is False
    # sudo that asks for a password (a person's own Mac) is not no sudo.
    asks = passport.parse(PASSPORT_OUTPUT.replace("sudo=yes", "sudo=password"))
    assert asks["sudo"] is False and asks["sudo_password"] is True
    none = passport.parse(PASSPORT_OUTPUT.replace("sudo=yes", "sudo=no"))
    assert none["sudo"] is False and none["sudo_password"] is False
    summary = passport.summary(pp)
    assert "commands" not in summary and summary["commands_count"] == 4
    assert passport.parse("") == {"shells": [], "commands": []}


def test_known_hosts_lines_as_openssh_writes_them():
    key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    assert ssh.known_hosts("203.0.113.10", 22, [key]) == \
        f"203.0.113.10 {key}\n".encode()
    assert ssh.known_hosts("203.0.113.10", 2222, [key]) == \
        f"[203.0.113.10]:2222 {key}\n".encode()
    try:
        ssh.known_hosts("203.0.113.10", 22, ["ssh-ed25519 garbage"])
    except ssh.HostKeysUnusable:
        pass
    else:
        raise AssertionError("pinned nothing and called it a key")
    assert ssh.key_id("Denis Gerasimenko", "2fe3b3c7-aaaa", "web-01") == \
        "Denis Gerasimenko via tTerm Web (2fe3b3c7) to web-01"


def test_client_address_comes_from_the_local_proxy_only():
    def req(peer, headers):
        return SimpleNamespace(client=SimpleNamespace(host=peer),
                               headers=headers)
    tunnel = {"cf-connecting-ip": "203.0.113.10"}
    assert client_ip(req("127.0.0.1", tunnel)) == "203.0.113.10"
    assert client_ip(req("127.0.0.1", {"x-forwarded-for": "198.51.100.7, 10.0.0.1"})) \
        == "198.51.100.7"
    # From anyone else the header could be invented.
    assert client_ip(req("198.51.100.9", tunnel)) == "198.51.100.9"
    assert client_ip(req("127.0.0.1", {"cf-connecting-ip": "not-an-ip"})) \
        == "127.0.0.1"


async def _install(core, project_id=None) -> tuple[str, str]:
    """A command from the page API; returns (install id, token)."""
    if project_id is None:
        state = (await request(core.page_port, "GET", "/api/state")).json()
        project_id = state["projects"][0]["id"]
    r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                      body=json.dumps({"projectId": project_id}).encode())
    assert r.status == 201, (r.status, r.text)
    body = r.json()
    assert body["command"].startswith("curl -sSL https://connect.test/s/")
    return body["id"], body["command"].split("/s/")[1].split()[0]


def _enroll_body(token, port, keys, hostname="tterm-lab-01"):
    return json.dumps({"token": token, "hostname": hostname,
                       "os": "Ubuntu 24.04.1 LTS", "ssh_port": port,
                       "ssh_user": "tterm", "host_keys": keys}).encode()


async def test_installer_is_served_for_a_live_command_only(h):
    async with h.core() as core:
        _, token = await _install(core)
        r = await request(core.connect_port, "GET", f"/s/{token}",
                          host="connect.test")
        assert r.status == 200 and f'TOKEN="{token}"' in r.text
        assert r.headers["content-type"].startswith("text/x-shellscript")
        assert r.headers["cache-control"] == "no-store"
        ca = (await request(core.connect_port, "GET", "/ca.pub")).text
        assert ca.split()[1] in r.text, "the installer trusts the active CA"
        assert ca.strip().endswith(core.active_ca["id"])
        r = await request(core.connect_port, "GET", "/s/AAAAAAAAAAAA")
        assert "not valid" in r.text and "exit 1" in r.text
        await core.pool.execute(
            "UPDATE install_tokens SET expires_at = now() - interval '1 s'")
        r = await request(core.connect_port, "GET", f"/s/{token}")
        assert "expired" in r.text and "exit 1" in r.text


async def test_a_server_connects_over_ssh(h):
    async with h.core() as core:
        install_id, token = await _install(core)
        status = (await request(core.page_port, "GET",
                                f"/api/installs/{install_id}")).json()
        assert status["state"] == "waiting" and status["machine"] is None
        async with ssh_server(core.active_ca["public_key"]) as (port, host_key, sign_ins):
            keys = [host_key.export_public_key().decode().strip()]
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, keys))
            assert r.status == 200, (r.status, r.text)
            assert r.text == "tterm-lab-01 is connected. Go back to tTerm Web."
            assert sign_ins == ["tterm"], sign_ins
        status = (await request(core.page_port, "GET",
                                f"/api/installs/{install_id}")).json()
        assert status["state"] == "connected"
        assert status["machine"]["name"] == "tterm-lab-01"
        assert status["machine"]["label"] == "P/tterm-lab-01"
        assert status["machine"]["host"] == "127.0.0.1"
        state = (await request(core.page_port, "GET", "/api/state")).json()
        machine = state["projects"][0]["machines"][0]
        assert machine["os"] == "Ubuntu 24.04.1 LTS"
        assert machine["passport"]["commands_count"] == 4
        assert "commands" not in machine["passport"]
        details = (await request(core.page_port, "GET",
                                 f"/api/machines/{machine['id']}")).json()
        assert details["passport"]["package_manager"] == "apt-get"
        kinds = [r["kind"] for r in await core.pool.fetch(
            "SELECT kind FROM journal ORDER BY id")]
        assert kinds[-1] == "machine.added", kinds
        # Used once.
        r = await request(core.connect_port, "POST", "/enroll",
                          headers={"content-type": "application/json"},
                          body=_enroll_body(token, port, keys))
        assert r.status == 409 and "already used" in r.text


async def test_a_changed_host_key_is_refused(h):
    async with h.core() as core:
        install_id, token = await _install(core)
        async with ssh_server(core.active_ca["public_key"]) as (port, _, sign_ins):
            impostor = asyncssh.generate_private_key("ssh-ed25519")
            keys = [impostor.export_public_key().decode().strip()]
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, keys))
            assert r.status == 502 and "different host key" in r.text, r.text
            assert sign_ins == [], "nothing was run on that server"
        status = (await request(core.page_port, "GET",
                                f"/api/installs/{install_id}")).json()
        assert status["state"] == "failed" and "different host key" in status["error"]
        assert await core.pool.fetchval("SELECT count(*) FROM machines") == 0


async def test_failure_leaves_the_command_usable(h):
    async with h.core() as core:
        install_id, token = await _install(core)
        dead = h_free_port()
        key = asyncssh.generate_private_key("ssh-ed25519")
        keys = [key.export_public_key().decode().strip()]
        r = await request(core.connect_port, "POST", "/enroll",
                          headers={"content-type": "application/json"},
                          body=_enroll_body(token, dead, keys))
        assert r.status == 502 and "connection refused" in r.text, r.text
        async with ssh_server(core.active_ca["public_key"], host_key=key) as (port, _, _s):
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, keys))
            assert r.status == 200, (r.status, r.text)


async def test_the_same_server_twice_is_one_machine(h):
    async with h.core() as core:
        key = asyncssh.generate_private_key("ssh-ed25519")
        keys = [key.export_public_key().decode().strip()]
        async with ssh_server(core.active_ca["public_key"], host_key=key) as (port, _, _s):
            for _ in range(2):
                _, token = await _install(core)
                r = await request(core.connect_port, "POST", "/enroll",
                                  headers={"content-type": "application/json"},
                                  body=_enroll_body(token, port, keys))
                assert r.status == 200, r.text
            # A different server with the same hostname gets its own name.
            _, token = await _install(core)
        other = asyncssh.generate_private_key("ssh-ed25519")
        async with ssh_server(core.active_ca["public_key"], host_key=other) as (port2, _, _s):
            r = await request(
                core.connect_port, "POST", "/enroll",
                headers={"content-type": "application/json"},
                body=_enroll_body(token, port2,
                                  [other.export_public_key().decode().strip()]))
            assert r.status == 200 and r.text.startswith("tterm-lab-01-2 "), r.text
        names = sorted(r["name"] for r in await core.pool.fetch(
            "SELECT name FROM machines"))
        assert names == ["tterm-lab-01", "tterm-lab-01-2"], names
        kinds = [r["kind"] for r in await core.pool.fetch(
            "SELECT kind FROM journal WHERE kind LIKE 'machine.%' ORDER BY id")]
        assert kinds == ["machine.added", "machine.reconnected", "machine.added"]


async def test_enroll_without_signer_says_so(h):
    async with h.core(with_signer=False) as core:
        _, token = await _install(core)
        r = await request(core.connect_port, "GET", f"/s/{token}")
        assert "cannot sign" in r.text and "exit 1" in r.text
        r = await request(core.connect_port, "POST", "/enroll",
                          headers={"content-type": "application/json"},
                          body=_enroll_body(token, 22, ["ssh-ed25519 AAAA"]))
        assert r.status == 503 and "cannot sign" in r.text


async def test_install_api_guards(h):
    async with h.core() as core:
        state = (await request(core.page_port, "GET", "/api/state")).json()
        body = json.dumps({"projectId": state["projects"][0]["id"]}).encode()
        r = await request(core.page_port, "POST", "/api/installs", body=body,
                          headers={"content-type": "application/json"})
        assert r.status == 403, "without the page's header"
        r = await request(core.page_port, "POST", "/api/installs",
                          headers=CHANGE,
                          body=json.dumps({"projectId": str(uuid.uuid4())}).encode())
        assert r.status == 403, "a project that is not ours"
        r = await request(core.page_port, "GET", f"/api/installs/{uuid.uuid4()}")
        assert r.status == 404
        r = await request(core.page_port, "GET", f"/api/machines/{uuid.uuid4()}")
        assert r.status == 404
        r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                          body=json.dumps({"projectId": state["projects"][0]["id"],
                                           "kind": "phone"}).encode())
        assert r.status == 422, "a machine is a server or a computer"
        r = await request(core.page_port, "POST", "/api/installs", headers=CHANGE,
                          body=json.dumps({"projectId": state["projects"][0]["id"],
                                           "kind": "computer"}).encode())
        assert r.status == 201, r.text
        command = r.json()["command"]
        m = re.fullmatch(r"curl -fsSL https://connect\.test/c/"
                         r"([A-Za-z0-9_-]{32}) \| sh", command)
        assert m, f"a computer gets the installer, with a long token: {command}"
        token = m.group(1)
        assert r.json()["byHand"] == (f"TTERM_TOKEN={token} python3 agent.py "
                                      "--hub wss://connect.test/agent"), r.json()
        # The installer, with the token and where the agent connects.
        r = await request(core.connect_port, "GET", f"/c/{token}")
        assert r.status == 200 and "{{" not in r.text, r.text[:300]
        assert f'TOKEN="{token}"' in r.text
        assert 'HUB="${TTERM_HUB:-wss://connect.test/agent}"' in r.text
        assert machines.AGENT_SOURCE in r.text
        r = await request(core.connect_port, "GET", f"/s/{token}")
        assert "exit 1" in r.text and "for a computer" in r.text, \
            "a computer's token does not install a server"
        _, server = await _install(core)
        r = await request(core.connect_port, "GET", f"/c/{server}")
        assert "exit 1" in r.text and "for a server" in r.text, \
            "nor a server's an agent"
        r = await request(core.connect_port, "GET", "/c/no-such-token-here")
        assert "exit 1" in r.text and "not valid" in r.text, r.text
        r = await request(core.connect_port, "GET", "/c/$(reboot)")
        assert "exit 1" in r.text and "not valid" in r.text and \
            "reboot" not in r.text, r.text


async def test_cas_are_remembered_and_guarded(h):
    async with h.pool() as pool:
        ca = {"id": "ca-test", "public_key": "ssh-ed25519 AAAAone tterm-web"}
        await machines.remember_cas(pool, [ca])
        await machines.remember_cas(pool, [ca])
        assert await pool.fetchval("SELECT count(*) FROM cas") == 1
        try:
            await machines.remember_cas(pool, [{**ca, "public_key": "ssh-ed25519 AAAAtwo"}])
        except RuntimeError as exc:
            assert "different key" in str(exc)
        else:
            raise AssertionError("a CA's key was swapped silently")


def h_free_port() -> int:
    """A port nothing listens on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_installer_script_shell_syntax_is_valid(h):
    """sh -n reads the whole installer without running it, the computer's
    one too."""
    ca = asyncssh.generate_private_key("ssh-ed25519")
    script = machines.render_installer(
        token="abcDEF123_-x", ca_key=ca.export_public_key().decode(),
        ca_id="ca-x", connect_url="https://connect.test")
    computer = machines.render_computer_installer(
        token="abcDEF123_-xabcDEF123_-xabcDEF12", connect_url="https://c.test")
    for text in (script, computer):
        proc = await asyncio.create_subprocess_exec(
            "sh", "-n", stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate(text.encode())
        assert proc.returncode == 0, err.decode()
    assert re.search(r"usermod -p '\*'", script), "no locked account"
    assert "{{" not in computer and "set -eu" in computer
    try:
        machines.render_computer_installer(token="$(reboot)abcdefghijklmn",
                                           connect_url="https://x")
    except ValueError:
        pass
    else:
        raise AssertionError("a token that is not a token was put in a script")
