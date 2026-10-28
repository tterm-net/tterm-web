"""Connecting servers: tokens, the installer, registration over SSH."""
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncssh

from ..api.connect import client_ip
from ..core import machines, passport, ssh, trust
from .harness import PASSPORT_OUTPUT, old_line, request, sign_in_as, ssh_server

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


def _sed(command: str, lines: list[str]) -> list[str]:
    """What a cleanup command leaves of these lines."""
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
    return out.splitlines()


def test_a_removed_server_gets_its_line_back():
    """The command shown when tTerm Web could not take a removed server's
    line out itself takes out that line and nothing else: not the line of
    the same server in another project, not the bot's, not a line of
    another tTerm Web."""
    mine, theirs = trust.new_principal(), trust.new_principal()
    web = "ssh-ed25519 AAAAweb"
    lines = ["cert-authority ssh-ed25519 AAAAbot tterm-bot",
             trust.line(mine, web, "ca-20261012-ffcd"),
             trust.line(theirs, web, "ca-20261012-ffcd"),
             "cert-authority ssh-ed25519 AAAAweb tterm-web ca-20261012-ffcd",
             "cert-authority ssh-ed25519 AAAAnew tterm-web ca-20261012-ffcd2",
             "ssh-ed25519 AAAAperson denis@mac"]
    command = machines.server_cleanup(mine)
    assert command == (f"sudo sed -i '/^cert-authority,principals=\"{mine}\" /d' "
                       "~tterm/.ssh/authorized_keys"), command
    assert _sed(command, lines) == [lines[0]] + lines[2:]
    # A server still with the line from before 0.0.12: that line alone.
    command = machines.server_cleanup(None, "ca-20261012-ffcd", own_line=False)
    assert command == ("sudo sed -i '/^cert-authority [^,]* tterm-web "
                       "ca-20261012-ffcd$/d' ~tterm/.ssh/authorized_keys"), command
    assert _sed(command, lines) == lines[:3] + lines[4:]
    assert machines.server_cleanup(None) is None
    for odd in ("m-x'; reboot; '", f"{mine}\n"):
        assert machines.server_cleanup(odd) is None, \
            "nothing odd goes into a command a person runs"
    assert machines.server_cleanup(None, "ca'; reboot; '", own_line=False) is None


def test_machine_names():
    assert machines.machine_name("web-01.example.com") == "web-01"
    assert machines.machine_name("My Box!") == "My-Box"
    assert machines.machine_name("...") == "server"
    assert machines.free_name("web", {"db"}) == "web"
    assert machines.free_name("web", {"WEB", "web-2"}) == "web-3"


def test_installer_is_filled_in_and_safe():
    ca = asyncssh.generate_private_key("ssh-ed25519", comment="tterm-web ca-x")
    public = ca.export_public_key().decode().strip()
    principal = trust.new_principal()
    script = machines.render_installer(token="abcDEF123_-x", ca_key=public,
                                       ca_id="ca-x", principal=principal,
                                       connect_url="https://connect.test")
    assert "{{" not in script
    assert 'TOKEN="abcDEF123_-x"' in script
    assert 'API_URL="https://connect.test"' in script
    key = " ".join(public.split()[:2])
    assert (f"LINE='cert-authority,principals=\"{principal}\" {key} "
            "tterm-web ca-x'") in script, "the CA for this server, no comment"
    assert f'PRINCIPAL="{principal}"' in script
    assert "sshd_config" in script and "set -eu" in script
    for bad in ({"token": "$(reboot)abc"}, {"principal": "m-x' $(reboot) '"},
                {"principal": "tterm"}, {"ca_id": "ca-x' $(reboot)"}):
        try:
            machines.render_installer(**{
                "token": "abcDEF123_-x", "ca_key": public, "ca_id": "ca-x",
                "principal": principal, "connect_url": "https://x", **bad})
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} was put in a script")
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


async def _installed(core, token: str, home: Path) -> str:
    """What the installer served for the command does to authorized_keys,
    on a server whose tterm lives in `home`: its line, unless a line for
    its principal is there already. The principal."""
    script = (await request(core.connect_port, "GET", f"/s/{token}")).text
    principal = re.search(r'^PRINCIPAL="(m-[0-9a-f-]{36})"$', script, re.M)[1]
    line = re.search(r"^LINE='([^']+)'$", script, re.M)[1]
    keys = home / ".ssh" / "authorized_keys"
    keys.parent.mkdir(parents=True, exist_ok=True)
    text = keys.read_text() if keys.exists() else ""
    if f'principals="{principal}"' not in text:
        keys.write_text(text + line + "\n")
    return principal


def _home() -> Path:
    """A server's ~tterm, with nothing in authorized_keys yet."""
    home = Path(tempfile.mkdtemp(prefix="ttsrv"))
    (home / ".ssh").mkdir()
    (home / ".ssh" / "authorized_keys").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPerson denis@mac\n")
    return home


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
        # The line takes only certificates made out to the command's own
        # principal, the same each time the script is fetched.
        principal = re.search(r'PRINCIPAL="(m-[0-9a-f-]{36})"', r.text)[1]
        assert f"LINE='cert-authority,principals=\"{principal}\" " in r.text
        again = (await request(core.connect_port, "GET", f"/s/{token}")).text
        assert f'PRINCIPAL="{principal}"' in again
        _, other = await _install(core)
        assert principal not in (await request(core.connect_port, "GET",
                                               f"/s/{other}")).text
        r = await request(core.connect_port, "GET", "/s/AAAAAAAAAAAA")
        assert "not valid" in r.text and "exit 1" in r.text
        await core.pool.execute(
            "UPDATE install_tokens SET expires_at = now() - interval '1 s'")
        r = await request(core.connect_port, "GET", f"/s/{token}")
        assert "expired" in r.text and "exit 1" in r.text


async def test_a_server_connects_over_ssh(h):
    home = _home()
    async with h.core() as core:
        install_id, token = await _install(core)
        status = (await request(core.page_port, "GET",
                                f"/api/installs/{install_id}")).json()
        assert status["state"] == "waiting" and status["machine"] is None
        principal = await _installed(core, token, home)
        async with ssh_server(core.active_ca["public_key"], home=home) as (
                port, host_key, sign_ins):
            keys = [host_key.export_public_key().decode().strip()]
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, keys))
            assert r.status == 200, (r.status, r.text)
            assert r.text == "tterm-lab-01 is connected. Go back to tTerm Web."
            # The passport, and the lines put in order.
            assert sign_ins == ["tterm", "tterm"], sign_ins
        row = await core.pool.fetchrow("SELECT principal, own_line FROM machines")
        assert (row["principal"], row["own_line"]) == (principal, True), row
        assert (home / ".ssh" / "authorized_keys").read_text().count(
            "cert-authority") == 1
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
    shutil.rmtree(home)


async def test_a_server_takes_only_certificates_made_for_it(h):
    """The security audit of 18.10: someone with an account and a way to
    post from a server's address, a user of that server without sudo, say,
    or a computer behind the same address, gets a command of their own and
    posts back that server's port and host keys, which anyone there can
    read. tTerm Web signs in for that command's principal, which only its
    installer, run with sudo, puts on a server: the server refuses, and
    nothing is added. So it did before 0.0.12 too, as far as a server's line
    from back then goes, which takes the user tterm and no principal."""
    async with h.core() as core:
        home = _home()
        _, token = await _install(core)
        await _installed(core, token, home)
        async with ssh_server(core.active_ca["public_key"], home=home) as (
                port, host_key, _s):
            keys = [host_key.export_public_key().decode().strip()]
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, keys))
            assert r.status == 200, r.text
            anna = await sign_in_as(core, "anna@example.com")
            state = (await request(core.page_port, "GET", "/api/state",
                                   headers={"cookie": anna})).json()
            r = await request(core.page_port, "POST", "/api/installs",
                              headers={**CHANGE, "cookie": anna},
                              body=json.dumps({"projectId": state["projects"][0]["id"]}).encode())
            assert r.status == 201, r.text
            hers = r.json()["command"].split("/s/")[1].split()[0]
            before = (home / ".ssh" / "authorized_keys").read_text()
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(hers, port, keys))
            assert r.status == 502 and "did not accept our certificate" in r.text, \
                (r.status, r.text)
        assert (home / ".ssh" / "authorized_keys").read_text() == before
        old = Path(tempfile.mkdtemp(prefix="ttsrv"))
        old_line(old, core.active_ca["public_key"])
        async with ssh_server(core.active_ca["public_key"], home=old) as (
                port, host_key, sign_ins):
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(hers, port, [
                                  host_key.export_public_key().decode().strip()]))
            assert r.status == 502 and "did not accept" in r.text, r.text
            assert sign_ins == []
        owners = [r["owner_id"] for r in await core.pool.fetch(
            "SELECT p.owner_id FROM machines m JOIN projects p "
            "ON p.id = m.project_id")]
        assert owners == [core.owner.id], "nothing of Anna's"
        shutil.rmtree(home)
        shutil.rmtree(old)


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


async def test_a_check_puts_in_order_only_the_server_it_signed_in_to(h):
    """While checking a fresh server, tTerm Web gives the same server in
    other projects a line of its own, if it still had the old one. Which
    machines are this server goes by the host key the server proved, not by
    what its installer said: a key of someone else's server put in the
    post changes nothing of theirs."""
    async with h.core() as core:
        cas = await core.signer.keys()
        ca = next(c for c in cas if c["active"])
        anna = await sign_in_as(core, "anna@example.com")
        project = await core.pool.fetchval(
            "SELECT p.id FROM projects p JOIN users u ON u.id = p.owner_id "
            "WHERE u.email = 'anna@example.com'")
        theirs = asyncssh.generate_private_key("ssh-ed25519")
        their_key = theirs.export_public_key().decode().strip()
        victim = await core.pool.fetchval(
            "INSERT INTO machines (project_id, kind, name, host, port, login, "
            "host_keys, ca_id, principal) VALUES ($1, 'server', 'db', "
            "'192.0.2.7', 22, 'tterm', $2, $3, $4) RETURNING id",
            project, [their_key], ca["id"], trust.new_principal())
        assert anna
        home = _home()
        _, token = await _install(core)
        await _installed(core, token, home)
        old_line(home, ca["public_key"])
        async with ssh_server(ca["public_key"], home=home) as (port, host_key, _s):
            mine = host_key.export_public_key().decode().strip()
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, [mine, their_key]))
            assert r.status == 200, r.text
        row = await core.pool.fetchrow(
            "SELECT principal, own_line, host FROM machines WHERE id = $1", victim)
        assert row["own_line"] is False and row["host"] == "192.0.2.7", row
        text = (home / ".ssh" / "authorized_keys").read_text()
        assert row["principal"] not in text, text
        assert "cert-authority ssh-" not in text, "the old line is out"
        shutil.rmtree(home)


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
        home = _home()
        await _installed(core, token, home)
        async with ssh_server(core.active_ca["public_key"], host_key=key,
                              home=home) as (port, _, _s):
            r = await request(core.connect_port, "POST", "/enroll",
                              headers={"content-type": "application/json"},
                              body=_enroll_body(token, port, keys))
            assert r.status == 200, (r.status, r.text)
        shutil.rmtree(home)


async def test_the_same_server_twice_is_one_machine(h):
    async with h.core() as core:
        key = asyncssh.generate_private_key("ssh-ed25519")
        keys = [key.export_public_key().decode().strip()]
        home = _home()
        async with ssh_server(core.active_ca["public_key"], host_key=key,
                              home=home) as (port, _, _s):
            principals = []
            for _ in range(2):
                _, token = await _install(core)
                principals.append(await _installed(core, token, home))
                r = await request(core.connect_port, "POST", "/enroll",
                                  headers={"content-type": "application/json"},
                                  body=_enroll_body(token, port, keys))
                assert r.status == 200, r.text
            # Connected again: the line of the second command, and the
            # first one's is gone.
            text = (home / ".ssh" / "authorized_keys").read_text()
            assert principals[1] in text and principals[0] not in text, text
            assert "denis@mac" in text, "the person's own key stays"
            assert await core.pool.fetchval(
                "SELECT principal FROM machines") == principals[1]
            # A different server with the same hostname gets its own name.
            _, token = await _install(core)
        other = asyncssh.generate_private_key("ssh-ed25519")
        second = _home()
        await _installed(core, token, second)
        async with ssh_server(core.active_ca["public_key"], host_key=other,
                              home=second) as (port2, _, _s):
            r = await request(
                core.connect_port, "POST", "/enroll",
                headers={"content-type": "application/json"},
                body=_enroll_body(token, port2,
                                  [other.export_public_key().decode().strip()]))
            assert r.status == 200 and r.text.startswith("tterm-lab-01-2 "), r.text
        shutil.rmtree(home)
        shutil.rmtree(second)
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
        # Retired in the signer, marked so here, once.
        await machines.remember_cas(pool, [{**ca, "retired": True}])
        first = await pool.fetchval("SELECT retired_at FROM cas")
        await machines.remember_cas(pool, [{**ca, "retired": True}])
        assert first is not None and \
            await pool.fetchval("SELECT retired_at FROM cas") == first
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
        ca_id="ca-x", principal=trust.new_principal(),
        connect_url="https://connect.test")
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


# ------------------------------------------------ the computer's installer

def _fake_agent(version: str) -> str:
    """The agent as its release has it, for the installer: it says its
    version; started, it says what the real one says when it is in, and
    runs until stopped, as itself (the installer knows it by what it runs)."""
    return f"""#!/bin/sh
case "$1" in --version) echo {version}; exit 0 ;; esac
echo "$@" > "$(dirname "$0")/started-with"
echo "[tterm-agent] version {version}, hub wss://c.test/agent"
echo "[tterm-agent] connected as «test»"
# Until stopped, or its folder is gone with the check's.
while [ -d "$(dirname "$0")" ]; do sleep 1; done
"""


#: Stand-ins for what the installer reaches outside its home: the release
#: on GitHub, the system's name and the user's number, the services, which
#: start and stop the agent as launchd and systemd would. Nothing of the
#: real computer is touched, the owner's Mac included.
STUBS = {
    "uname": r"""#!/bin/sh
here=$(dirname "$0")
case "$1" in -m) cat "$here/arch" ;; *) cat "$here/os" ;; esac
""",
    "id": r"""#!/bin/sh
here=$(dirname "$0")
case "$1" in -u) cat "$here/uid" ;; -un) echo someone ;; *) exit 1 ;; esac
""",
    "curl": r"""#!/bin/sh
here=$(dirname "$0")
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in -o) out="$2"; shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac
done
echo "curl $url" >> "$here/asked"
cp "$here/release-file" "$out"
""",
    "systemctl": r"""#!/bin/sh
here=$(dirname "$0")
echo "systemctl $*" >> "$here/asked"
[ "$(cat "$here/systemd")" = on ] || exit 1
unit="$HOME/.config/systemd/user/tterm-web-agent.service"
case "$*" in
    "--user enable --now tterm-web-agent.service")
        run=$(sed -n 's/^ExecStart=//p' "$unit")
        log=$(sed -n 's/^StandardOutput=append://p' "$unit")
        eval "set -- $run"
        "$@" >> "$log" 2>&1 </dev/null &
        echo $! > "$here/running" ;;
    "--user stop tterm-web-agent.service")
        if [ -f "$here/running" ]; then kill "$(cat "$here/running")" 2>/dev/null; rm -f "$here/running"; fi ;;
esac
exit 0
""",
    "launchctl": r"""#!/bin/sh
here=$(dirname "$0")
echo "launchctl $*" >> "$here/asked"
case "$1" in
    load)
        [ ! -f "$here/fail-load" ] || exit 1
        log=$(sed -n 's/.*<key>StandardOutPath<\/key><string>\(.*\)<\/string>.*/\1/p' "$2")
        sed -n '/<key>ProgramArguments<\/key>/,/<\/array>/s/.*<string>\(.*\)<\/string>.*/\1/p' "$2" > "$here/args"
        set --
        while IFS= read -r a; do set -- "$@" "$a"; done < "$here/args"
        "$@" >> "$log" 2>&1 </dev/null &
        echo $! > "$here/running" ;;
    unload|remove)
        [ -f "$here/running" ] || exit 1
        kill "$(cat "$here/running")" 2>/dev/null; rm -f "$here/running" ;;
esac
""",
    "loginctl": "#!/bin/sh\nexit 1\n",
    # A Python new enough for agent.py, whatever this computer has: the
    # checks stop before it would be used.
    "python3": r"""#!/bin/sh
case "$1" in -c) echo 313 ;; --version) echo "Python 3.13.0" ;; *) exit 1 ;; esac
""",
}

_TOKEN = "abcDEF123_-xabcDEF123_-xabcDEF12"


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Computer:
    """A computer of the checks': a home of its own, the stand-ins first on
    PATH, the file the release would give."""

    def __init__(self, root: Path, name: str, system: str = "Linux", arch: str = "x86_64",
                 systemd: bool = True) -> None:
        self.root = root
        self.home = root / name
        self.home.mkdir()
        self.stubs = root / f"{name}-stubs"
        self.stubs.mkdir()
        for stub, text in STUBS.items():
            (self.stubs / stub).write_text(text)
            (self.stubs / stub).chmod(0o755)
        self.put("os", system)
        self.put("arch", arch)
        self.put("systemd", "on" if systemd else "off")
        self.put("uid", "1000")
        (self.stubs / "asked").write_text("")
        self.release = self.stubs / "release-file"
        self.dir = self.home / ".tterm-web"
        self.others: list[int] = []

    def put(self, name: str, value: str) -> None:
        (self.stubs / name).write_text(value + "\n")

    def asked(self) -> list[str]:
        return (self.stubs / "asked").read_text().splitlines()

    def running(self) -> int | None:
        """The agent a stand-in service, or start.sh, started."""
        for pid in self._pids():
            return pid
        return None

    def _pids(self) -> list[int]:
        found = []
        for path in (self.stubs / "running", self.dir / "agent.pid"):
            if path.is_file() and path.read_text().strip().isdigit():
                found.append(int(path.read_text().strip()))
        return found

    async def install(self, script: str, env: dict | None = None) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "sh", "-s", cwd=str(self.root), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={"HOME": str(self.home), "PATH": f"{self.stubs}:/usr/bin:/bin", "LANG": "C",
                 **(env or {})})
        out, _ = await asyncio.wait_for(proc.communicate(script.encode()), 40)
        await asyncio.sleep(0.2)
        return proc.returncode, out.decode()

    def settings(self) -> dict:
        lines = (self.dir / "env").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines)

    def stop(self) -> None:
        for pid in [*self._pids(), *self.others]:
            _end(pid)


def _alive(pid: int | None) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Ended, and not yet reaped by its parent, the system.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ")[-1][:1] != "Z"
    except OSError:
        return True


def _end(pid: int | None) -> None:
    if pid and pid > 1:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass


def _released(connect_url: str = "https://c.test") -> tuple[str, str, dict]:
    """The installer as tTerm Web serves it, its agent's version and the
    checksums written in it."""
    script = machines.render_computer_installer(token=_TOKEN, connect_url=connect_url)
    version = re.search(r'^AGENT_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$', script, re.M).group(1)
    sums = dict(re.findall(r'^SUM_([A-Z0-9_]+)="([0-9a-f]{64})"$', script, re.M))
    assert set(sums) == {"MACOS", "LINUX_X86_64", "LINUX_ARM64", "PY"}, sums
    assert re.search(r'^FILES="https://github\.com/tterm-net/tterm-app/releases/download/'
                     r'agent-v\$AGENT_VERSION"$', script, re.M), "from tTerm's releases"
    return script, version, sums


def _checked_against(script: str, sums: dict, which: str, file: Path) -> str:
    """The installer with the checksum of `file` in place of the release's:
    its own checks, on a file at hand."""
    changed = script.replace(f'SUM_{which}="{sums[which]}"', f'SUM_{which}="{_sha256(file)}"')
    assert changed != script
    return changed


async def test_the_computer_installer_on_linux():
    """The computer's installer on Linux with systemd (0.1.12), run for real
    with stand-ins: the agent's file from the release, checked against the
    checksum written in the installer and asked its version before the
    running agent stops; one that is not it does not go in, and the running
    agent goes on; run again, the same key, and what the agent's own
    updates left goes; TTERM_UPDATE=off stays and is said; over agent.py
    under systemd, the old agent stops, the unit runs the new one, Python's
    environment goes; and a session that cannot reach the user's systemd
    changes nothing."""
    script, version, sums = _released()
    root = Path(tempfile.mkdtemp(prefix="tterm-install-"))
    boxes: list[_Computer] = []
    try:
        mac = _Computer(root, "linux")
        boxes.append(mac)
        mac.release.write_text(_fake_agent(version))
        run = _checked_against(script, sums, "LINUX_X86_64", mac.release)

        code, out = await mac.install(run)
        assert code == 0 and "Done: the computer is connected" in out, out
        assert f"Agent: {version}, it updates itself" in out and "systemd --user" in out, out
        assert mac.asked()[0] == (f"curl https://github.com/tterm-net/tterm-app/releases/"
                                  f"download/agent-v{version}/tterm-agent-linux-x86_64"), mac.asked()
        unit = (mac.home / ".config/systemd/user/tterm-web-agent.service").read_text()
        assert f'ExecStart="{mac.dir}/tterm-agent" --env-file "{mac.dir}/env"' in unit, unit
        first_agent = mac.running()
        assert _alive(first_agent)
        assert (mac.dir / "started-with").read_text().split() == ["--env-file", str(mac.dir / "env")]
        first = mac.settings()
        assert first["TTERM_HUB"] == "wss://c.test/agent" and first["TTERM_TOKEN"] == _TOKEN
        assert machines.KEY_RE.match(first["TTERM_KEY"]) and len(first["TTERM_KEY"]) == 43, first
        assert "TTERM_UPDATE" not in first
        assert (mac.dir / "env").stat().st_mode & 0o777 == 0o600
        assert mac.dir.stat().st_mode & 0o777 == 0o700

        # Not the release's file: refused, and the running agent goes on.
        mac.release.write_text(_fake_agent("6.6.6"))
        before = {p.name: p.read_bytes() for p in mac.dir.iterdir() if p.is_file()}
        code, out = await mac.install(run)
        assert code == 1 and "its checksum differs. Nothing was changed." in out, out
        code, out = await mac.install(_checked_against(script, sums, "LINUX_X86_64", mac.release))
        assert code == 1 and f"says it is 6.6.6, not {version}." in out and \
            "Nothing was changed." in out, out
        assert {p.name: p.read_bytes() for p in mac.dir.iterdir() if p.is_file()} == before
        assert _alive(first_agent) and mac.running() == first_agent
        mac.release.write_text(_fake_agent(version))

        # Again, with what the agent's own updates leave behind, and off.
        for left in ("tterm-agent.old", "tterm-agent.trial", "tterm-agent.skip",
                     ".tterm-agent.4242.new", ".tterm-agent.trial.4242.tmp"):
            (mac.dir / left).write_text("left")
        code, out = await mac.install(run, {"TTERM_UPDATE": "off"})
        assert code == 0 and "updating it" in out, out
        assert f"Agent: {version}, it does not update itself (TTERM_UPDATE=off)" in out, out
        assert not _alive(first_agent), "the agent before stopped"
        assert _alive(mac.running())
        again = mac.settings()
        assert again["TTERM_KEY"] == first["TTERM_KEY"], "the same computer"
        assert again["TTERM_UPDATE"] == "off"
        assert sorted(p.name for p in mac.dir.iterdir() if "tterm-agent" in p.name) == \
            ["tterm-agent", "tterm-agent.skip"], "a version not taken stays not taken"
        code, out = await mac.install(run)
        assert code == 0 and mac.settings()["TTERM_UPDATE"] == "off", out
        code, out = await mac.install(run, {"TTERM_UPDATE": "on"})
        assert code == 0 and "it updates itself" in out and mac.settings()["TTERM_UPDATE"] == "on", out

        # Over agent.py under systemd: it stops, the unit runs the new one.
        old = _Computer(root, "old")
        boxes.append(old)
        old.release.write_text(_fake_agent(version))
        (old.dir / "venv" / "bin").mkdir(parents=True)
        (old.dir / "agent.py").write_text('__version__ = "0.13.0"\n')
        (old.dir / "env").write_text(
            f"TTERM_HUB=wss://c.test/agent\nTTERM_TOKEN=used\nTTERM_KEY={'k' * 43}\n")
        unit_path = old.home / ".config/systemd/user/tterm-web-agent.service"
        unit_path.parent.mkdir(parents=True)
        unit_path.write_text(f"[Service]\nExecStart={old.dir}/venv/bin/python {old.dir}/agent.py "
                             f"--env-file {old.dir}/env\n")
        before_agent = subprocess.Popen(["sleep", "60"])
        old.put("running", str(before_agent.pid))

        # From a session that cannot reach the user's systemd: nothing changes.
        old.put("systemd", "off")
        code, out = await old.install(_checked_against(script, sums, "LINUX_X86_64", old.release))
        assert code == 1 and "this session cannot" in out, out
        assert before_agent.poll() is None, "the agent goes on"
        assert (old.dir / "venv").is_dir() and (old.dir / "agent.py").is_file()
        assert "agent.py" in unit_path.read_text()

        old.put("systemd", "on")
        code, out = await old.install(_checked_against(script, sums, "LINUX_X86_64", old.release))
        assert code == 0 and "Done" in out, out
        assert before_agent.wait(5) is not None, "the agent before stopped"
        assert f'ExecStart="{old.dir}/tterm-agent"' in unit_path.read_text()
        assert not (old.dir / "venv").exists() and not (old.dir / "agent.py").exists()
        assert old.settings()["TTERM_KEY"] == "k" * 43
        assert _alive(old.running())
        assert not any("launchctl" in a for b in boxes for a in b.asked())
    finally:
        for box in boxes:
            box.stop()
        shutil.rmtree(root, ignore_errors=True)


async def test_the_computer_installer_on_a_mac():
    """On a Mac (0.1.12): the file for macOS, its checksum and version; the
    launchd job runs it with the settings file; over agent.py's job, the
    old agent stops, the job runs the new one, Python's environment goes.
    launchd is a stand-in, under a label of the checks' own."""
    script, version, sums = _released()
    script = script.replace('LABEL="net.tterm.web-agent"', 'LABEL="net.tterm.web-agent.checks"')
    root = Path(tempfile.mkdtemp(prefix="tterm-install-"))
    boxes: list[_Computer] = []
    try:
        import plistlib
        mac = _Computer(root, "mac", system="Darwin", arch="arm64")
        boxes.append(mac)
        mac.release.write_text(_fake_agent(version))
        run = _checked_against(script, sums, "MACOS", mac.release)
        plist = mac.home / "Library/LaunchAgents/net.tterm.web-agent.checks.plist"

        # Over agent.py's job: it stops, the job runs the new one.
        (mac.dir / "venv" / "bin").mkdir(parents=True)
        (mac.dir / "agent.py").write_text('__version__ = "0.13.0"\n')
        (mac.dir / "env").write_text(
            f"TTERM_HUB=wss://c.test/agent\nTTERM_TOKEN=used\nTTERM_KEY={'m' * 43}\n")
        before_agent = subprocess.Popen(["sleep", "60"])
        mac.put("running", str(before_agent.pid))
        # launchd will not take the job: the agent before is still all there.
        mac.put("fail-load", "yes")
        code, out = await mac.install(run)
        assert code == 1 and "Could not set up autostart" in out, out
        assert (mac.dir / "venv").is_dir() and (mac.dir / "agent.py").is_file()
        (mac.stubs / "fail-load").unlink()
        before_agent.wait(5)
        before_agent = subprocess.Popen(["sleep", "60"])
        mac.put("running", str(before_agent.pid))
        code, out = await mac.install(run)
        assert code == 0 and "Done: the computer is connected" in out, out
        assert "System: macos, arm64" in out and "launchd" in out, out
        assert before_agent.wait(5) is not None, "the agent before stopped"
        job = plistlib.loads(plist.read_bytes())
        assert job["Label"] == "net.tterm.web-agent.checks"
        assert job["ProgramArguments"] == [f"{mac.dir}/tterm-agent", "--env-file", f"{mac.dir}/env"], job
        assert job["KeepAlive"] == {"SuccessfulExit": False} and job["RunAtLoad"] is True
        assert not (mac.dir / "venv").exists() and not (mac.dir / "agent.py").exists()
        assert mac.settings()["TTERM_KEY"] == "m" * 43
        assert _alive(mac.running())
        asked = mac.asked()
        assert asked[0] == (f"curl https://github.com/tterm-net/tterm-app/releases/download/"
                            f"agent-v{version}/tterm-agent-macos"), asked
        assert f"launchctl load {plist}" in asked, asked
        assert not any(a.startswith("systemctl") for a in asked), asked
    finally:
        for box in boxes:
            box.stop()
        shutil.rmtree(root, ignore_errors=True)


async def test_the_computer_installer_without_a_service_manager():
    """Without user services (WSL, a container; Linux only, as start.sh is
    used only there): the agent starts in the background, agent.pid; run
    again, the agent of agent.pid stops, and a number of agent.pid now
    another program's is left alone; a removed computer gets a new key; the
    agent before for a Linux the new one has no file for is checked too;
    not as root, nor from a terminal of the agent it replaces."""
    import sys
    script, version, sums = _released()
    root = Path(tempfile.mkdtemp(prefix="tterm-install-"))
    boxes: list[_Computer] = []
    try:
        box = _Computer(root, "plain", systemd=False)
        boxes.append(box)
        box.release.write_text(_fake_agent(version))
        run = _checked_against(script, sums, "LINUX_X86_64", box.release)
        if sys.platform != "darwin":
            code, out = await box.install(run)
            assert code == 0 and "Done" in out and "No autostart on this system" in out, out
            first_agent = box.running()
            assert _alive(first_agent)
            code, out = await box.install(run)
            assert code == 0, out
            assert not _alive(first_agent), "the agent of agent.pid stopped"
            # After a restart agent.pid may name another program: left alone.
            _end(box.running())
            other = subprocess.Popen(["sleep", "60"])
            box.others.append(other.pid)
            (box.dir / "agent.pid").write_text(f"{other.pid}\n")
            code, out = await box.install(run)
            assert code == 0 and other.poll() is None, out

            # Removed in tTerm Web, the agent forgot the key: a new one.
            key = box.settings()["TTERM_KEY"]
            (box.dir / "env").write_text("TTERM_HUB=wss://c.test/agent\nTTERM_TOKEN=used\n"
                                         "TTERM_REMOVED=1\n")
            code, out = await box.install(run)
            fresh = box.settings()
            assert code == 0 and machines.KEY_RE.match(fresh["TTERM_KEY"]), out
            assert fresh["TTERM_KEY"] != key and "TTERM_REMOVED" not in fresh

        # A Linux the new agent has no file for: agent.py, checked too.
        box.put("arch", "riscv64")
        box.release.write_text('__version__ = "0.13.0"\nprint("not the released one")\n')
        code, out = await box.install(script)
        assert code == 1 and "its checksum differs. Nothing was changed." in out, out
        box.put("arch", "x86_64")

        # Not as root; not from a terminal of the agent it would stop.
        box.put("uid", "0")
        code, out = await box.install(run)
        assert code == 1 and "Do not run this as root" in out, out
        box.put("uid", "1000")
        (box.dir).mkdir(exist_ok=True)
        (box.dir / "tterm-agent").write_text(_fake_agent(version))
        code, out = await box.install(run, {"TTERM_AGENT_DIR": str(box.dir)})
        assert code == 1 and "This terminal runs through the agent" in out, out
    finally:
        for box in boxes:
            box.stop()
        shutil.rmtree(root, ignore_errors=True)
