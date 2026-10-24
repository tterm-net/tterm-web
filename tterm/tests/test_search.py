"""Search over everything (0.1.7), the core's part: commands of every
terminal of one's own, ended ones too, what they printed, and notes, whole;
nothing of anyone else's."""
import json
import urllib.parse
import uuid
import zlib

from ..core import search
from .harness import request, sign_in_as

CHANGE = {"x-tterm": "1", "content-type": "application/json"}


def test_output_becomes_text_to_search():
    plain = search.plain
    assert plain(b"\x1b[1;31mERROR\x1b[0m disk full\r\n") == "ERROR disk full"
    assert plain(b"\x1b]0;denis@web: ~\x07hello\n") == "hello"
    assert plain(b"\x1b]8;;https://x.example\x1b\\link\x1b]8;;\x1b\\\n") == "link"
    # A progress bar ends as its last state; a backspace takes a character.
    assert plain(b"Downloading  10%\rDownloading  55%\rDownloading 100%\n") == \
        "Downloading 100%"
    assert plain(b"abx\bc\n") == "abc"
    # Lines end as terminals end them, CR LF: plain lines, not drawn over.
    assert plain(b"one\r\ntwo\r\n") == "one\ntwo"
    assert plain(b"Downloading  10%\rDownloading 100%\r\ndone\r\n") == \
        "Downloading 100%\ndone"
    assert plain(b"\x1b(Bplain\x1b=\x1b>\x07 text\x00\n") == "plain text"
    assert plain("café ✓\n".encode()) == "café ✓"
    assert plain(b"\xff\xfe broken\n") == "�� broken"
    long = "\n".join(f"line {n:05d}" for n in range(5000)).encode()
    text = plain(long)
    assert text.startswith("line 00000\n") and text.endswith("\nline 04999")
    assert "\n…\n" in text and len(text) <= search.TEXT_HEAD + search.TEXT_TAIL + 3
    assert all(len(line) == 10 for line in text.split("\n") if line != "…"), \
        "whole lines at the cut"
    assert search.text_of(zlib.compress(b"\x1b[32mok\x1b[0m\n")) == "ok"
    assert search.text_of(b"not zlib") == "" and search.text_of(None) == ""


def test_queries_are_words():
    assert search.words("  Certbot  certbot RENEW ") == ["certbot", "renew"]
    assert search.words("") == []
    assert search.words("cert\x00bot\x07 x") == ["cert", "bot", "x"], "no NUL for the database"
    assert len(search.words(" ".join(f"w{n}" for n in range(20)))) == search.WORDS_MAX
    assert search._like("100%_a\\b") == "%100\\%\\_a\\\\b%"


async def _machine(pool, project, name: str, kind: str = "server") -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO machines (project_id, kind, name) VALUES ($1, $2, $3) "
        "RETURNING id", project, kind, name)


async def _terminal(pool, machine, owner, name: str, ended: bool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO terminals (machine_id, owner_id, name, home, closed_at) "
        "VALUES ($1, $2, $3, '/home/denis', CASE WHEN $4 THEN now() END) "
        "RETURNING id", machine, owner, name, ended)


async def _block(pool, terminal, seq: int, command: str, output: bytes = b"", *,
                 code: int = 0, minutes_ago: int = 0, with_text: bool = True,
                 days: int = 30, owner=None) -> None:
    await pool.execute(
        """
        INSERT INTO blocks (terminal_id, kind, seq_start, seq_end, command,
                            started_by, started_at, finished_at, exit_code,
                            cwd, branch, output, output_text, expires_at)
        VALUES ($1, 'command', $2::bigint, $2::bigint + 1, $3, $9,
                now() - make_interval(mins => $4),
                now() - make_interval(mins => $4), $5, '/home/denis/shop',
                'main', $6, $7, now() + make_interval(days => $8))
        """, terminal, seq, command, minutes_ago, code, zlib.compress(output),
        search.plain(output) if with_text else None, days, owner)


async def _find(port: int, q: str, near: str = "", cookie: str | None = None) -> dict:
    query = urllib.parse.urlencode({"q": q, **({"near": near} if near else {})})
    r = await request(port, "GET", f"/api/search?{query}",
                      headers={"cookie": cookie} if cookie is not None else None)
    assert r.status == 200, r.text
    return r.json()


async def _note(port: int, project: str, title: str, blocks: list[dict]) -> dict:
    async def call(method, path, body):
        r = await request(port, method, path, body=json.dumps(body).encode(),
                          headers=CHANGE)
        assert r.status in (200, 201), r.text
        return r.json()
    folder = await call("POST", "/api/folders", {"projectId": project,
                                                 "name": f"F {title}"})
    note = await call("POST", "/api/notes", {"folderId": folder["id"]})
    return await call("PUT", f"/api/notes/{note['id']}",
                      {"baseVersion": note["version"], "title": title,
                       "blocks": blocks})


async def test_search_finds_commands_output_and_notes(h):
    async with h.core() as core:
        pool, port, me = core.pool, core.page_port, core.owner.id
        project = await pool.fetchval(
            "SELECT id FROM projects WHERE owner_id = $1", me)
        web = await _machine(pool, project, "web")
        db_ = await _machine(pool, project, "db")
        deploy = await _terminal(pool, web, me, "deploy", ended=True)
        live = await _terminal(pool, web, me, "1", ended=False)
        other = await _terminal(pool, db_, me, "2", ended=True)
        await _block(pool, deploy, 1, "sudo certbot renew --dry-run",
                     b"\x1b[32mCongratulations\x1b[0m, all renewals succeeded\n",
                     minutes_ago=90)
        await _block(pool, live, 3, "sudo certbot renew --dry-run",
                     b"Congratulations, all renewals succeeded\n", minutes_ago=5)
        await _block(pool, other, 5, "certbot --version",
                     b"bash: certbot: command not found\n", code=127,
                     minutes_ago=30)
        await _block(pool, deploy, 7, "cat /etc/nginx/sites-enabled/shop",
                     b"cat: /etc/nginx/sites-enabled/shop: Permission denied\n",
                     code=1, minutes_ago=60)
        await _block(pool, deploy, 9, "ls build", b"1000 files\n100% done\na_b\naxb\n",
                     minutes_ago=50)
        await _block(pool, deploy, 11, "certbot certificates", b"old\n",
                     minutes_ago=20, days=-1)
        state = (await request(port, "GET", "/api/state")).json()
        await _note(port, state["projects"][0]["id"], "Certificates", [
            {"id": "a", "type": "p", "text": "Let's Encrypt, renewed by certbot on web-01"},
            {"id": "b", "type": "todo", "text": "Check the expiry dates"},
            {"id": "c", "type": "code", "text": "sudo certbot renew"}])
        await _note(port, state["projects"][0]["id"], "Shopping", [
            {"id": "x", "type": "p", "text": "Milk and bread"}])
        await _note(port, state["projects"][0]["id"], "Windows", [
            {"id": "w", "type": "code", "text": 'dir C:\\Users\\denis "My Documents"'}])

        found = await _find(port, "certbot")
        commands = found["commands"]
        assert [c["command"] for c in commands] == \
            ["sudo certbot renew --dry-run", "certbot --version"], \
            "the latest run first, once per command and machine; expired gone"
        renew, version = commands
        assert renew["runs"] == 2 and not renew["ended"] and \
            renew["terminal"] == "P/web", renew
        assert renew["lines"] == ["Congratulations, all renewals succeeded"]
        assert version["exitCode"] == 127 and version["ended"] and \
            version["terminal"] == "P/db · 2" and version["seq"] == 5
        assert version["cwd"] == "/home/denis/shop" and version["home"] == "/home/denis"
        assert version["branch"] == "main" and version["machine"] == "db"
        assert [o["line"] for o in found["output"]] == ["bash: certbot: command not found"]
        assert found["output"][0]["hit"] == 0
        note = found["notes"][0]
        assert len(found["notes"]) == 1 and note["title"] == "Certificates"
        assert note["blockId"] == "a" and note["hit"] == 0 and not note["inTitle"]
        assert note["lines"][1] == {"text": "Check the expiry dates", "type": "todo",
                                    "done": False}

        found = await _find(port, "permission DENIED")
        assert found["commands"] == [] and len(found["output"]) == 1
        out = found["output"][0]
        assert out["line"] == "cat: /etc/nginx/sites-enabled/shop: Permission denied"
        assert out["terminal"] == "P/web · deploy" and out["exitCode"] == 1
        assert out["terminalId"] == str(deploy) and out["seq"] == 7

        # Words in any order, inside other words.
        found = await _find(port, "renew sudo")
        assert [c["command"] for c in found["commands"]] == ["sudo certbot renew --dry-run"]
        assert (await _find(port, "cert dry"))["commands"][0]["runs"] == 2
        # % and _ are themselves.
        assert [o["line"] for o in (await _find(port, "100%"))["output"]] == ["100% done"]
        assert [o["line"] for o in (await _find(port, "a_b"))["output"]] == ["a_b"]
        # A note is found by what is written in it, not by its JSON.
        assert (await _find(port, "type"))["notes"] == []
        assert (await _find(port, "milk"))["notes"][0]["title"] == "Shopping"
        assert (await _find(port, "todo"))["notes"] == [], "a checklist item is not the word"
        for q in ("c:\\users", '"my documents"'):
            assert [n["title"] for n in (await _find(port, q))["notes"]] == ["Windows"], q
        # A NUL in the query is a space, not an error.
        assert [c["command"] for c in (await _find(port, "\x00certbot"))["commands"]][:1] == \
            ["sudo certbot renew --dry-run"]
        found = await _find(port, "certificates expiry")
        assert found["notes"][0]["blockId"] == "b" and found["notes"][0]["inTitle"] is False
        found = await _find(port, "certificates")
        assert found["notes"][0]["inTitle"] and found["notes"][0]["blockId"] is None
        assert found["commands"] == [], "an expired block is not found"

        # Nobody else finds any of it, nor Denis theirs.
        anna = await sign_in_as(core, "anna@example.com")
        for q in ("certbot", "permission", "milk"):
            got = await _find(port, q, cookie=anna)
            assert got["commands"] == got["output"] == got["notes"] == [], q
        anna_id = await pool.fetchval("SELECT id FROM users WHERE email = 'anna@example.com'")
        hers = await pool.fetchval("SELECT id FROM projects WHERE owner_id = $1", anna_id)
        lab = await _machine(pool, hers, "lab")
        await _block(pool, await _terminal(pool, lab, anna_id, "1", ended=True), 1,
                     "certbot --secret-of-anna", b"", owner=anna_id)
        assert "certbot --secret-of-anna" not in \
            [c["command"] for c in (await _find(port, "certbot"))["commands"]]
        assert (await _find(port, "secret", cookie=anna))["commands"][0]["command"] == \
            "certbot --secret-of-anna"

        r = await request(port, "GET", "/api/search?q=certbot", headers={"cookie": ""})
        assert r.status == 401, r.text


async def test_nothing_typed_brings_the_commands_run_most_here(h):
    async with h.core() as core:
        pool, port, me = core.pool, core.page_port, core.owner.id
        project = await pool.fetchval(
            "SELECT id FROM projects WHERE owner_id = $1", me)
        web = await _machine(pool, project, "web")
        elsewhere = await _machine(pool, project, "db")
        t = await _terminal(pool, web, me, "1", ended=True)
        seq = 0
        for command, times in (("git pull", 3), ("npm ci", 1), ("ls", 2)):
            for n in range(times):
                seq += 2
                await _block(pool, t, seq, command, minutes_ago=seq)
        await _block(pool, await _terminal(pool, elsewhere, me, "1", ended=True),
                     1, "psql", minutes_ago=0)
        found = await _find(port, "", near=str(web))
        assert found["words"] == [] and found["commands"] == []
        assert [(c["command"], c["runs"]) for c in found["recent"]] == \
            [("git pull", 3), ("ls", 2), ("npm ci", 1)]
        assert (await _find(port, ""))["recent"] == [], "no machine, nothing"
        assert (await _find(port, "", near="not-a-uuid"))["recent"] == []


async def test_earlier_blocks_get_their_text(h):
    async with h.core() as core:
        pool, port, me = core.pool, core.page_port, core.owner.id
        # The core's own pass at start, over before these blocks come.
        await core.terminals._filler
        project = await pool.fetchval(
            "SELECT id FROM projects WHERE owner_id = $1", me)
        t = await _terminal(pool, await _machine(pool, project, "web"), me, "1",
                            ended=True)
        for n in range(5):
            await _block(pool, t, n * 2, f"make step{n}",
                         f"\x1b[1mbuilt target{n}\x1b[0m\n".encode(),
                         with_text=False)
        await _block(pool, t, 99, "old", b"expired output\n", with_text=False,
                     days=-1)
        assert (await _find(port, "target3"))["output"] == []
        assert await search.fill_texts(pool, pause=0) == 5
        assert await search.fill_texts(pool, pause=0) == 0, "once"
        found = await _find(port, "target3")
        assert [o["line"] for o in found["output"]] == ["built target3"]
        assert await pool.fetchval(
            "SELECT output_text FROM blocks WHERE command = 'old'") is None
