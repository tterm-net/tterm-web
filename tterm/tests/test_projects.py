"""Projects of one's own (0.1.1): made, renamed, colored and deleted, with
machines moving between them, terminals named, and the page's API for it."""
import asyncio
import json
import shutil
import uuid

import asyncpg

from ..core import accounts, auth, db, machines, notes, projects, terminals
from .harness import page_socket, request, shell_server, sign_in_as
from .test_terminals import _ca_public, _home, _login_shell, _machine, _until

CHANGE = {"x-tterm": "1", "content-type": "application/json"}
ME = "denis@example.com"


async def _owner(pool):
    me = await accounts.ensure_local_owner(pool, "Denis Gerasimenko", ME)
    project = (await accounts.projects_of(pool, me.id))[0]
    return me, uuid.UUID(project["id"])


async def _person(pool, email, name):
    async with pool.acquire() as conn, conn.transaction():
        return await auth._make_account(conn, email, name)


async def _server(pool, project_id, name, keys=("ssh-ed25519 AAAA one",)):
    return await pool.fetchval(
        "INSERT INTO machines (project_id, kind, name, host, port, login, "
        "host_keys) VALUES ($1, 'server', $2, '203.0.113.5', 22, 'tterm', $3) "
        "RETURNING id", project_id, name, list(keys))


async def refused(call, kind, words=""):
    try:
        await call
    except kind as exc:
        assert words.lower() in str(exc).lower(), str(exc)
        return
    raise AssertionError(f"not refused with {kind.__name__}")


def test_codes_and_colors_are_checked():
    assert projects.clean_code(" cx ") == "CX"
    assert projects.clean_code("ab12") == "AB12"
    assert projects.clean_code("дл") == "ДЛ", "a code from a Cyrillic name"
    for bad in ("", "  ", "ABCDE", "A-B", "a_b", 42, None):
        try:
            projects.clean_code(bad)
        except notes.Invalid:
            continue
        raise AssertionError(f"taken: {bad!r}")
    assert projects.clean_name("  Client\tX ") == "Client X"
    # What does not print is not kept: U+202E would turn the name around.
    assert projects.clean_name("Cli\u202eent\u200b X\ufeff") == "Client X"
    assert accounts.one_line("a\x85b\u2028c\u00a0d\x00e") == "a b c d e"
    assert terminals.clean_name(" deploy\u200b\u2066 web ") == "deploy web"
    for bad in ("", " ", "x" * 81, 5):
        try:
            projects.clean_name(bad)
        except notes.Invalid:
            continue
        raise AssertionError(f"taken: {bad!r}")
    for color in projects.COLORS:
        assert projects.clean_color(color) == color
    for bad in ("red", "", None, "Violet"):
        try:
            projects.clean_color(bad)
        except notes.Invalid:
            continue
        raise AssertionError(f"taken: {bad!r}")


async def test_projects_made_changed_deleted(h):
    async with h.pool() as pool:
        me, personal = await _owner(pool)
        listed = await accounts.projects_of(pool, me.id)
        assert [(p["name"], p["code"], p["color"], p["personal"], p["own"],
                 p["alone"]) for p in listed] == \
            [("Personal", "P", "violet", True, True, True)]
        # A new one takes a code from its name and the next free color.
        cx = await projects.create(pool, me.id, " Client  X ")
        assert (cx["name"], cx["code"], cx["color"], cx["role"]) == \
            ("Client X", "CX", "sky", "owner")
        assert cx["own"] and cx["alone"] and not cx["personal"]
        # The same name again: the code gets a number.
        cx2 = await projects.create(pool, me.id, "Client X")
        assert cx2["code"] == "CX2" and cx2["color"] == "teal"
        # Its code and color as asked, and a code someone has.
        lab = await projects.create(pool, me.id, "Home lab", "hl", "lime")
        assert (lab["code"], lab["color"]) == ("HL", "lime")
        await refused(projects.create(pool, me.id, "Other", "cx"), notes.Taken,
                      "code CX")
        await refused(projects.create(pool, me.id, "Other", "x", "red"),
                      notes.Invalid)
        await refused(projects.create(pool, me.id, " "), notes.Invalid)
        assert [p["code"] for p in await accounts.projects_of(pool, me.id)] == \
            ["P", "CX", "CX2", "HL"]

        # Personal is renamed like any other; a code taken is refused.
        changed = await projects.edit(pool, me.id, personal, name="Home",
                                      code="h", color="pink")
        assert changed == {"id": str(personal), "name": "Home", "code": "H",
                           "color": "pink"}
        await refused(projects.edit(pool, me.id, personal, code="hl"),
                      notes.Taken, "code HL")
        same = await projects.edit(pool, me.id, personal, code="H")
        assert same["code"] == "H", "its own code again is fine"
        kinds = [(r["kind"], json.loads(r["details"])) for r in await pool.fetch(
            "SELECT kind, details FROM journal WHERE project_id = $1 "
            "AND kind LIKE 'project.%' ORDER BY id", personal)]
        assert kinds[-1] == ("project.changed", {
            "name": {"from": "Personal", "to": "Home"},
            "code": {"from": "P", "to": "H"},
            "color": {"from": "violet", "to": "pink"}}), kinds
        assert len(kinds) == 2, "an edit that changes nothing writes nothing"
        listed = await accounts.projects_of(pool, me.id)
        assert listed[0]["name"] == "Home" and listed[0]["personal"]

        # Deleting: Personal stays; one with machines waits for them to go.
        await refused(projects.delete(pool, me.id, personal), notes.Refused,
                      "it stays")
        cx_id = uuid.UUID(cx["id"])
        await _server(pool, cx_id, "web-01")
        await _server(pool, cx_id, "db-01", ("ssh-ed25519 AAAA two",))
        await refused(projects.delete(pool, me.id, cx_id), notes.Refused,
                      "db-01 and web-01")
        await pool.execute("UPDATE machines SET removed_at = now() "
                           "WHERE project_id = $1", cx_id)
        folder = await notes.create_folder(pool, me.id, cx_id, "Runbooks")
        note = await notes.create_note(pool, me.id, uuid.UUID(folder["id"]), "x")
        await notes.create_folder(pool, me.id, cx_id, "Empty")
        token = await pool.fetchval(
            "INSERT INTO install_tokens (project_id, kind, token_hash, "
            "created_by, expires_at) VALUES ($1, 'server', '\\x6831', $2, "
            "now() + interval '10 minutes') RETURNING id", cx_id, me.id)
        gone = await projects.delete(pool, me.id, cx_id)
        assert gone == {"id": str(cx_id), "name": "Client X", "folders": 2,
                        "notes": 1}
        assert [p["code"] for p in await accounts.projects_of(pool, me.id)] == \
            ["H", "CX2", "HL"]
        assert await pool.fetchval("SELECT count(*) FROM notes WHERE id = $1",
                                   uuid.UUID(note["id"])) == 0
        assert await pool.fetchval("SELECT expires_at <= now() FROM "
                                   "install_tokens WHERE id = $1", token)
        # Its notes, its page and its commands are gone with it.
        await refused(notes.get_note(pool, me.id, uuid.UUID(note["id"])),
                      notes.NotFound)
        await refused(notes.create_folder(pool, me.id, cx_id, "Late"),
                      notes.NotFound)
        await refused(projects.edit(pool, me.id, cx_id, name="Back"),
                      notes.NotFound)
        await refused(projects.delete(pool, me.id, cx_id), notes.NotFound)
        # Its code is free again, and the journal kept its lines.
        again = await projects.create(pool, me.id, "Client X")
        assert again["code"] == "CX"
        assert await pool.fetchval(
            "SELECT count(*) FROM journal WHERE project_id = $1", cx_id) >= 3


async def _paused(pool, sql, *args):
    """A transaction that holds a lock until it is let go."""
    conn = await pool.acquire()
    held = conn.transaction()
    await held.start()
    await conn.execute(sql, *args)

    async def let_go(commit=False, before=None):
        try:
            if before:
                await conn.execute(*before)
            await (held.commit() if commit else held.rollback())
        finally:
            await pool.release(conn)
    return conn, let_go


async def test_notes_change_while_their_project_goes(h):
    """Deleting a project while its notes change. A change that makes
    something in it waits, then finds it gone; the rest goes on. Neither
    ever waits for the other in turn. The deletion is held up halfway, by a
    folder of the project kept locked, so that the change comes mid-way."""
    async with h.pool() as pool:
        me, _ = await _owner(pool)
        who = me.id
        changes = {
            # The change, and whether it must wait for the deletion.
            "new folder": (lambda f: notes.create_folder(pool, who, f["p"], "C"),
                           True),
            "new note": (lambda f: notes.create_note(pool, who, f["a"], "Late"),
                         True),
            "folder renamed": (lambda f: notes.rename_folder(pool, who, f["a"],
                                                             "C"), True),
            "note moved": (lambda f: notes.move_note(pool, who, f["n"], f["b"]),
                           True),
            "folder deleted": (lambda f: notes.delete_folder(pool, who, f["a"]),
                               False),
            "note deleted": (lambda f: notes.delete_note(pool, who, f["n"]),
                             False),
            "note saved": (lambda f: notes.save_note(pool, who, f["n"], 1, "y",
                                                     []), False),
        }
        for number, (what, (change, waits)) in enumerate(changes.items(), 1):
            project = await projects.create(pool, who, f"Gone {number}")
            f = {"p": uuid.UUID(project["id"])}
            for key, name in (("a", "A"), ("b", "B"), ("z", "Z")):
                folder = await notes.create_folder(pool, who, f["p"], name)
                f[key] = uuid.UUID(folder["id"])
            f["n"] = uuid.UUID((await notes.create_note(pool, who, f["a"]))["id"])
            _, let_go = await _paused(
                pool, "SELECT 1 FROM note_folders WHERE id = $1 FOR UPDATE",
                f["z"])
            try:
                deleting = asyncio.create_task(projects.delete(pool, who, f["p"]))
                await asyncio.sleep(0.3)
                assert not deleting.done(), f"{what}: the deletion went through"
                late = asyncio.create_task(change(f))
                await asyncio.sleep(0.3)
                if waits:
                    assert not late.done(), f"{what}: did not wait"
            finally:
                await let_go()
            gone = await asyncio.wait_for(deleting, 5)
            assert gone["name"] == f"Gone {number}", what
            try:
                await asyncio.wait_for(late, 5)
            except notes.NotFound:
                pass
            else:
                assert not waits, f"{what}: done in a deleted project"
            assert await pool.fetchval(
                "SELECT count(*) FROM note_folders WHERE project_id = $1",
                f["p"]) == 0, what


async def test_a_machine_moves_while_something_else_happens(h):
    """A move while the machine is removed, while it moves elsewhere, and
    while the same server comes into the target: one of them has its way,
    nothing waits for the other in turn."""
    async with h.pool() as pool:
        me, personal = await _owner(pool)
        lab = uuid.UUID((await projects.create(pool, me.id, "Lab"))["id"])
        ops = uuid.UUID((await projects.create(pool, me.id, "Ops"))["id"])

        # Removed meanwhile. The removal is held up once it has the
        # machine, by the lock it takes next for a server's line.
        web = await _server(pool, personal, "web-01")
        await pool.execute("INSERT INTO cas (id, public_key) VALUES "
                           "('ca-1', 'ssh-ed25519 AAAA ca')")
        await pool.execute("UPDATE machines SET ca_id = 'ca-1' WHERE id = $1",
                           web)
        _, let_go = await _paused(
            pool, "SELECT pg_advisory_xact_lock(hashtext($1))", "server-ca:ca-1")
        try:
            removing = asyncio.create_task(machines.remove(pool, me.id, web))
            await asyncio.sleep(0.3)
            assert not removing.done(), "the removal went through"
            moving = asyncio.create_task(
                projects.move_machine(pool, me.id, web, lab))
            await asyncio.sleep(0.3)
            assert not moving.done(), "the move did not wait for the removal"
        finally:
            await let_go()
        assert await asyncio.wait_for(removing, 5) is not None
        await refused(asyncio.wait_for(moving, 5), notes.NotFound,
                      "no such machine")
        assert await pool.fetchval(
            "SELECT count(*) FROM journal WHERE machine_id = $1 "
            "AND kind = 'machine.moved'", web) == 0

        # Moved elsewhere meanwhile. A server coming into Lab holds up the
        # move there; the move to Ops goes first.
        api = await _server(pool, personal, "api", ("ssh-ed25519 AAAA api",))
        _, let_go = await _paused(
            pool, "SELECT pg_advisory_xact_lock(hashtext($1))", str(lab))
        try:
            to_lab = asyncio.create_task(
                projects.move_machine(pool, me.id, api, lab))
            await asyncio.sleep(0.3)
            assert not to_lab.done(), "the move did not wait for Lab"
            to_ops = await asyncio.wait_for(
                projects.move_machine(pool, me.id, api, ops), 5)
            assert to_ops["from"] == "Personal"
        finally:
            await let_go()
        await refused(asyncio.wait_for(to_lab, 5), notes.Refused, "just moved")
        assert await pool.fetchval(
            "SELECT project_id FROM machines WHERE id = $1", api) == ops

        # The same server comes into Lab meanwhile, as its registration
        # would: not there twice.
        db_01 = await _server(pool, personal, "db-01", ("ssh-ed25519 AAAA db",))
        conn, let_go = await _paused(
            pool, "SELECT pg_advisory_xact_lock(hashtext($1))", str(lab))
        try:
            moving = asyncio.create_task(
                projects.move_machine(pool, me.id, db_01, lab))
            await asyncio.sleep(0.3)
            assert not moving.done(), "the move did not wait for Lab"
        finally:
            await let_go(commit=True, before=(
                "INSERT INTO machines (project_id, kind, name, host, port, "
                "login, host_keys) VALUES ($1, 'server', 'db', '203.0.113.6', "
                "22, 'tterm', $2)", lab, ["ssh-ed25519 AAAA db"]))
        await refused(asyncio.wait_for(moving, 5), notes.Refused,
                      "has this server already, as db")


async def test_a_note_moves_while_its_projects_change(h):
    """A note moved to another project (0.1.2) while the project it goes to,
    or the one it leaves, is deleted: the move waits, then finds it gone,
    and the note is where it was or gone with its project. Two notes moved
    the opposite ways at once both get there: neither move waits for the
    other in turn."""
    async with h.pool() as pool:
        me, personal = await _owner(pool)
        who = me.id
        home = uuid.UUID((await notes.create_folder(pool, who, personal,
                                                    "Home"))["id"])
        for side in ("to", "from"):
            project = uuid.UUID((await projects.create(pool, who,
                                                       f"Gone {side}"))["id"])
            there = uuid.UUID((await notes.create_folder(pool, who, project,
                                                         "There"))["id"])
            z = uuid.UUID((await notes.create_folder(pool, who, project,
                                                     "Z"))["id"])
            if side == "to":
                nid = uuid.UUID((await notes.create_note(pool, who, home))["id"])
                where, words = there, "no such folder"
            else:
                nid = uuid.UUID((await notes.create_note(pool, who, there))["id"])
                where, words = home, "no such note"
            # The deletion is held up halfway, by a folder of the project
            # kept locked, so that the move comes mid-way.
            _, let_go = await _paused(
                pool, "SELECT 1 FROM note_folders WHERE id = $1 FOR UPDATE", z)
            try:
                deleting = asyncio.create_task(projects.delete(pool, who, project))
                await asyncio.sleep(0.3)
                assert not deleting.done(), f"{side}: the deletion went through"
                moving = asyncio.create_task(notes.move_note(pool, who, nid, where))
                await asyncio.sleep(0.3)
                assert not moving.done(), f"{side}: the move did not wait"
            finally:
                await let_go()
            await asyncio.wait_for(deleting, 5)
            await refused(asyncio.wait_for(moving, 5), notes.NotFound, words)
            folder = await pool.fetchval("SELECT folder_id FROM notes WHERE id = $1",
                                         nid)
            assert folder == (home if side == "to" else None), side

        # Opposite ways at once, while the project first in the order is
        # held: both moves wait for it, and then go one after the other.
        # Each taking its own project first, they would lock each other out.
        a = uuid.UUID((await projects.create(pool, who, "A side"))["id"])
        b = uuid.UUID((await projects.create(pool, who, "B side"))["id"])
        low, high = sorted((a, b))
        f_low = uuid.UUID((await notes.create_folder(pool, who, low, "Low"))["id"])
        f_high = uuid.UUID((await notes.create_folder(pool, who, high,
                                                      "High"))["id"])
        n_low = uuid.UUID((await notes.create_note(pool, who, f_low, "One"))["id"])
        n_high = uuid.UUID((await notes.create_note(pool, who, f_high,
                                                    "Two"))["id"])
        _, let_go = await _paused(
            pool, "SELECT 1 FROM projects WHERE id = $1 FOR UPDATE", low)
        try:
            one = asyncio.create_task(notes.move_note(pool, who, n_low, f_high))
            await asyncio.sleep(0.3)
            two = asyncio.create_task(notes.move_note(pool, who, n_high, f_low))
            await asyncio.sleep(0.3)
            assert not one.done() and not two.done(), "they did not wait"
        finally:
            await let_go()
        assert (await asyncio.wait_for(one, 5))["projectId"] == str(high)
        assert (await asyncio.wait_for(two, 5))["projectId"] == str(low)

        # A tick, or a save, while the note moves: done where it is now, not
        # taken for a note that is gone. The move is held up once it has the
        # note, by the folder it goes to kept locked.
        for change in ("tick", "save"):
            nid = uuid.UUID((await notes.create_note(pool, who, f_low,
                                                     "Moving"))["id"])
            await notes.save_note(pool, who, nid, 1, "Moving", [
                {"id": "a", "type": "todo", "text": "Item"}])
            _, let_go = await _paused(
                pool, "SELECT 1 FROM note_folders WHERE id = $1 FOR UPDATE",
                f_high)
            try:
                moving = asyncio.create_task(notes.move_note(pool, who, nid, f_high))
                await asyncio.sleep(0.3)
                assert not moving.done(), f"{change}: the move went through"
                late = asyncio.create_task(
                    notes.tick(pool, who, nid, "a", True) if change == "tick"
                    else notes.save_note(pool, who, nid, 2, "Moved", [
                        {"id": "a", "type": "todo", "text": "Item"}]))
                await asyncio.sleep(0.3)
                assert not late.done(), f"{change}: did not wait for the move"
            finally:
                await let_go()
            assert (await asyncio.wait_for(moving, 5))["projectId"] == str(high)
            done = await asyncio.wait_for(late, 5)
            assert (done["projectId"], done["folderId"]) == (str(high), str(f_high)), \
                change
            assert done["version"] == 3, change

        # Deleted while it moves: the deletion waits, then finds it moved,
        # and says so; its line would go to the journal of the other project.
        nid = uuid.UUID((await notes.create_note(pool, who, f_low, "Gone?"))["id"])
        _, let_go = await _paused(
            pool, "SELECT 1 FROM note_folders WHERE id = $1 FOR UPDATE", f_high)
        try:
            moving = asyncio.create_task(notes.move_note(pool, who, nid, f_high))
            await asyncio.sleep(0.3)
            deleting = asyncio.create_task(notes.delete_note(pool, who, nid))
            await asyncio.sleep(0.3)
            assert not deleting.done(), "the deletion did not wait"
        finally:
            await let_go()
        assert (await asyncio.wait_for(moving, 5))["projectId"] == str(high)
        await refused(asyncio.wait_for(deleting, 5), notes.Refused, "just moved")
        assert await pool.fetchval("SELECT count(*) FROM notes WHERE id = $1",
                                   nid) == 1

        # Someone else's project or note named: nothing of theirs is held,
        # and a note they cannot see is not there, whatever else is named.
        kira = await _person(pool, "kira@example.com", "Kira Novak")
        _, let_go = await _paused(
            pool, "SELECT 1 FROM projects WHERE id = $1 FOR UPDATE", low)
        try:
            theirs = uuid.UUID((await notes.create_note(
                pool, kira.id, uuid.UUID((await notes.create_folder(
                    pool, kira.id, next(uuid.UUID(x["id"]) for x in
                                        await accounts.projects_of(pool, kira.id)),
                    "Kira's"))["id"])))["id"])
            await refused(asyncio.wait_for(notes.move_note(
                pool, kira.id, theirs, project_id=low), 2), notes.NotFound,
                "no such project")
            await refused(asyncio.wait_for(notes.move_note(
                pool, kira.id, n_high, project_id=uuid.uuid4()), 2),
                notes.NotFound, "no such note")
            for call in (notes.delete_note(pool, kira.id, n_high),
                         notes.create_note(pool, kira.id, f_low),
                         notes.delete_folder(pool, kira.id, f_low),
                         notes.rename_folder(pool, kira.id, f_low, "Mine")):
                await refused(asyncio.wait_for(call, 2), notes.NotFound)
        finally:
            await let_go()
        # Many at once, both ways: taken in turn by each, the two projects
        # would lock each other out now and then.
        many_low = [uuid.UUID((await notes.create_note(pool, who, f_low))["id"])
                    for _ in range(12)]
        many_high = [uuid.UUID((await notes.create_note(pool, who, f_high))["id"])
                     for _ in range(12)]
        done = await asyncio.wait_for(asyncio.gather(
            *(notes.move_note(pool, who, x, f_high) for x in many_low),
            *(notes.move_note(pool, who, x, f_low) for x in many_high),
            return_exceptions=True), 30)
        failed = [repr(x) for x in done if isinstance(x, BaseException)]
        assert not failed, failed


async def test_projects_made_in_a_day(h):
    """Made and deleted in a loop, projects would pile up: a day has a
    limit too, deleted ones counted."""
    async with h.pool() as pool:
        me, _ = await _owner(pool)
        was = projects.MOST_A_DAY
        projects.MOST_A_DAY = 3
        try:
            for name in ("One", "Two"):
                made = await projects.create(pool, me.id, name)
                await projects.delete(pool, me.id, uuid.UUID(made["id"]))
            # Personal, made with the account today, and the two.
            await refused(projects.create(pool, me.id, "Three"), notes.Refused,
                          "in the last day")
            await pool.execute(
                "UPDATE projects SET created_at = created_at - interval "
                "'25 hours' WHERE owner_id = $1", me.id)
            assert (await projects.create(pool, me.id, "Three"))["code"] == "T"
        finally:
            projects.MOST_A_DAY = was


async def test_who_may_change_a_project(h):
    async with h.pool() as pool:
        me, personal = await _owner(pool)
        team = uuid.UUID((await projects.create(pool, me.id, "Team"))["id"])
        oleg = await _person(pool, "oleg@example.com", "Oleg Petrov")
        sam = await _person(pool, "sam@example.com", "Sam Lee")
        eve = await _person(pool, "eve@example.com", "Eve Stranger")
        for person, role in ((oleg, "admin"), (sam, "member")):
            await pool.execute(
                "INSERT INTO project_members (project_id, user_id, role) "
                "VALUES ($1, $2, $3)", team, person.id, role)
        assert not (await accounts.projects_of(pool, me.id))[1]["alone"]
        theirs = await accounts.projects_of(pool, oleg.id)
        assert [(p["name"], p["own"], p["personal"]) for p in theirs] == \
            [("Personal", True, True), ("Team", False, False)]
        # An admin changes the name; a member and a stranger do not.
        await projects.edit(pool, oleg.id, team, name="The team")
        await refused(projects.edit(pool, sam.id, team, name="Mine"),
                      notes.NotAllowed)
        await refused(projects.edit(pool, eve.id, team, name="Mine"),
                      notes.NotFound)
        # Only the owner deletes it.
        await refused(projects.delete(pool, oleg.id, team), notes.NotAllowed)
        await refused(projects.delete(pool, eve.id, team), notes.NotFound)
        # Codes are each owner's own: Oleg may have a TH too.
        await projects.create(pool, oleg.id, "The hub", "TH")
        await projects.edit(pool, me.id, team, code="TH")
        # Oleg, changing Denis's project, hears whose code it is.
        await refused(projects.edit(pool, oleg.id, team, code="P"), notes.Taken,
                      "Its owner has a project with the code P")
        await refused(projects.edit(pool, me.id, team, code="P"), notes.Taken,
                      "You have a project with the code P")
        # A limit on how many.
        was = projects.MOST_PROJECTS
        projects.MOST_PROJECTS = 3
        try:
            await projects.create(pool, me.id, "Third")
            await refused(projects.create(pool, me.id, "Fourth"), notes.Refused,
                          "the most there may be")
        finally:
            projects.MOST_PROJECTS = was
        await projects.delete(pool, me.id, team)
        assert [p["name"] for p in await accounts.projects_of(pool, oleg.id)] \
            == ["Personal", "The hub"], "nobody is in a deleted project"


async def test_colors_go_round(h):
    async with h.pool() as pool:
        me, _ = await _owner(pool)
        made = [await projects.create(pool, me.id, f"Project {n}")
                for n in range(9)]
        # Personal took violet; the others take the rest in turn, then the
        # one fewest have.
        assert [p["color"] for p in made[:7]] == list(projects.COLORS[1:])
        assert made[7]["color"] == "violet" and made[8]["color"] == "sky"
        await projects.set_tint(pool, me.id, "block")
        assert await projects.tint_of(pool, me.id) == "block"
        await refused(projects.set_tint(pool, me.id, "wash"), notes.Invalid)
        assert await projects.tint_of(pool, me.id) == "block"


async def test_machines_move_between_own_projects(h):
    async with h.pool() as pool:
        me, personal = await _owner(pool)
        lab = uuid.UUID((await projects.create(pool, me.id, "Lab"))["id"])
        web = await _server(pool, personal, "web-01")
        moved = await projects.move_machine(pool, me.id, web, lab)
        assert (moved["name"], moved["projectId"], moved["code"], moved["from"]) \
            == ("web-01", str(lab), "L", "Personal")
        listed = await accounts.projects_of(pool, me.id)
        assert [[m["name"] for m in p["machines"]] for p in listed] == \
            [[], ["web-01"]]
        kinds = [r["project_id"] for r in await pool.fetch(
            "SELECT project_id FROM journal WHERE kind = 'machine.moved' "
            "AND machine_id = $1 ORDER BY id", web)]
        assert kinds == [personal, lab], "a line in each project"
        await refused(projects.move_machine(pool, me.id, web, lab), notes.Invalid,
                      "in Lab already")
        # A name taken there gets a number.
        other = await _server(pool, personal, "web-01",
                              ("ssh-ed25519 AAAA other",))
        moved = await projects.move_machine(pool, me.id, other, lab)
        assert moved["name"] != "web-01" and moved["name"].startswith("web-01")
        # The same server there already: refused.
        twin = await _server(pool, personal, "copy", ("ssh-ed25519 AAAA one",))
        await refused(projects.move_machine(pool, me.id, twin, lab),
                      notes.Refused, "has this server already, as web-01")
        # Only between one's own projects.
        oleg = await _person(pool, "oleg@example.com", "Oleg Petrov")
        olegs = uuid.UUID((await accounts.projects_of(pool, oleg.id))[0]["id"])
        await refused(projects.move_machine(pool, me.id, twin, olegs),
                      notes.NotFound, "no such project")
        await refused(projects.move_machine(pool, oleg.id, twin, olegs),
                      notes.NotFound, "no such machine")
        await pool.execute(
            "INSERT INTO project_members (project_id, user_id, role) "
            "VALUES ($1, $2, 'admin')", olegs, me.id)
        await refused(projects.move_machine(pool, me.id, twin, olegs),
                      notes.NotAllowed, "projects you own")
        # Not into a deleted project, and not one that was removed.
        gone = uuid.UUID((await projects.create(pool, me.id, "Gone"))["id"])
        await projects.delete(pool, me.id, gone)
        await refused(projects.move_machine(pool, me.id, twin, gone),
                      notes.NotFound, "no such project")
        await pool.execute("UPDATE machines SET removed_at = now() WHERE id = $1",
                           twin)
        await refused(projects.move_machine(pool, me.id, twin, lab),
                      notes.NotFound, "no such machine")


async def test_older_projects_get_colors_in_turn(h):
    """009 on a database from before it: each owner's projects, oldest
    first, take the colors one after another."""
    url = await h.empty_database()
    pool = await db.connect(url, max_size=2)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE schema_migrations (version text PRIMARY KEY, "
                "applied_at timestamptz NOT NULL DEFAULT now())")
            for path in sorted(db.MIGRATIONS.glob("*.sql")):
                if path.stem >= "009":
                    break
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (version) "
                                   "VALUES ($1)", path.stem)
            a = await conn.fetchval("INSERT INTO users (name) VALUES ('A') "
                                    "RETURNING id")
            b = await conn.fetchval("INSERT INTO users (name) VALUES ('B') "
                                    "RETURNING id")
            for owner, code, minutes in ((a, "P", 3), (a, "X", 2), (b, "P", 2),
                                         (a, "Y", 1)):
                await conn.execute(
                    "INSERT INTO projects (owner_id, name, code, created_at) "
                    "VALUES ($1, $2, $2, now() - make_interval(mins => $3))",
                    owner, code, minutes)
        applied = await db.migrate(pool)
        # 009 first, and whatever came after it (010 from 0.1.6).
        assert applied[0] == "009_projects", applied
        rows = await pool.fetch("SELECT owner_id, code, color FROM projects "
                                "ORDER BY created_at")
        colors = {(r["owner_id"], r["code"]): r["color"] for r in rows}
        assert colors == {(a, "P"): "violet", (a, "X"): "sky", (a, "Y"): "teal",
                          (b, "P"): "violet"}, colors
        assert await pool.fetchval(
            "SELECT project_tint FROM users WHERE id = $1", a) == "badge"
        # A deleted project's code is free, a live one's is not.
        await pool.execute("UPDATE projects SET deleted_at = now() "
                           "WHERE owner_id = $1 AND code = 'X'", a)
        await pool.execute("INSERT INTO projects (owner_id, name, code) "
                           "VALUES ($1, 'X again', 'X')", a)
        try:
            await pool.execute("INSERT INTO projects (owner_id, name, code) "
                               "VALUES ($1, 'Y again', 'Y')", a)
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("two live projects with one code")
    finally:
        await pool.close()


async def test_the_page_works_with_projects(h):
    async with h.core() as core:
        port = core.page_port

        async def call(method, path, body=None, headers=CHANGE):
            data = json.dumps(body).encode() if body is not None else None
            return await request(port, method, path, body=data, headers=headers)

        state = (await call("GET", "/api/state", headers={})).json()
        assert state["me"]["projectTint"] == "badge"
        personal = state["projects"][0]
        assert (personal["color"], personal["own"], personal["alone"]) == \
            ("violet", True, True)
        r = await call("POST", "/api/projects", {"name": "Client X"},
                       headers={"content-type": "application/json"})
        assert r.status == 403, "a change without the page's header"
        r = await call("POST", "/api/projects", {"name": "Client X"})
        assert r.status == 201, r.text
        cx = r.json()
        assert (cx["code"], cx["color"], cx["machines"], cx["folders"]) == \
            ("CX", "sky", [], [])
        r = await call("POST", "/api/projects", {"name": "Other", "code": "cx"})
        assert r.status == 409, r.text
        r = await call("POST", "/api/projects", {"name": "Other", "color": "red"})
        assert r.status == 422, r.text
        r = await call("PATCH", f"/api/projects/{personal['id']}",
                       {"name": "Home", "color": "teal"})
        assert r.status == 200 and r.json()["name"] == "Home", r.text
        r = await call("PATCH", f"/api/projects/{personal['id']}", {"code": "CX"})
        assert r.status == 409, r.text
        r = await call("PATCH", "/api/me", {"projectTint": "block"})
        assert r.status == 204, r.text
        r = await call("PATCH", "/api/me", {"projectTint": "loud"})
        assert r.status == 422, r.text
        # Nothing of a change is saved when a part of it will not do.
        r = await call("PATCH", "/api/me", {"theme": "dark", "projectTint": "x"})
        assert r.status == 422, r.text
        state = (await call("GET", "/api/state", headers={})).json()
        assert state["me"]["projectTint"] == "block"
        assert state["me"]["theme"] == "system"
        assert [(p["name"], p["code"], p["color"]) for p in state["projects"]] \
            == [("Home", "P", "teal"), ("Client X", "CX", "sky")]
        # A machine moves; a project with one is not deleted.
        machine = await core.pool.fetchval(
            "INSERT INTO machines (project_id, kind, name) VALUES ($1, "
            "'computer', 'mac') RETURNING id", uuid.UUID(personal["id"]))
        r = await call("POST", f"/api/machines/{machine}/move",
                       {"projectId": cx["id"]})
        assert r.status == 200 and r.json()["code"] == "CX", r.text
        r = await call("DELETE", f"/api/projects/{cx['id']}")
        assert r.status == 422 and "mac" in r.json()["error"], r.text
        r = await call("POST", f"/api/machines/{machine}/move",
                       {"projectId": personal["id"]})
        assert r.status == 200, r.text
        # Someone else sees none of it, and changes none of it.
        other = await sign_in_as(core, "anna@example.com")
        for method, path, body in (
                ("PATCH", f"/api/projects/{cx['id']}", {"name": "Mine"}),
                ("DELETE", f"/api/projects/{cx['id']}", None),
                ("POST", f"/api/machines/{machine}/move",
                 {"projectId": cx["id"]})):
            r = await call(method, path, body, headers={**CHANGE, "cookie": other})
            assert r.status == 404, (method, path, r.status, r.text)
        r = await call("DELETE", f"/api/projects/{cx['id']}")
        assert r.status == 200 and r.json()["name"] == "Client X", r.text
        r = await call("DELETE", f"/api/projects/{personal['id']}")
        assert r.status == 422, r.text
        state = (await call("GET", "/api/state", headers={})).json()
        assert [p["name"] for p in state["projects"]] == ["Home"]


async def test_a_terminal_is_named_and_follows_its_machine(h):
    """A name for a terminal (0.1.1), and its label as its machine moves to
    another project and that project's code changes: in the core, in the
    API, and on the socket that watches it."""
    login, _ = _login_shell()
    home = _home()
    try:
        async with h.core() as core, \
                shell_server(await _ca_public(core), home, login) \
                as (port, host_key):
            machine_id = await _machine(core, port, host_key)
            t = await core.terminals.open(core.owner, machine_id, 100, 30)
            assert t.label == "P/lab"
            await _until(lambda: t.state == "open", what="the terminal")

            async def call(method, path, body=None, headers=CHANGE):
                data = json.dumps(body).encode() if body is not None else None
                return await request(core.page_port, method, path, body=data,
                                     headers=headers)

            async with page_socket(core.page_port) as ws:
                await ws.send(json.dumps({"op": "attach", "ch": 1,
                                          "id": str(t.id), "after": None,
                                          "cols": 100, "rows": 30}))
                labels = []

                async def hear(label):
                    while True:
                        message = await ws.recv()
                        if isinstance(message, bytes):
                            continue
                        data = json.loads(message)
                        if data.get("op") in ("attached", "state"):
                            labels.append(data["terminal"]["label"])
                            if labels[-1] == label:
                                return

                async def heard(label):
                    try:
                        await asyncio.wait_for(hear(label), 10)
                    except TimeoutError:
                        raise AssertionError(f"never heard {label!r}: "
                                             f"{labels}") from None

                await heard("P/lab")
                r = await call("PATCH", f"/api/terminals/{t.id}",
                               {"name": "  deploy\u200b\tweb\u202e  "})
                assert r.status == 200, r.text
                assert (r.json()["name"], r.json()["label"]) == \
                    ("deploy web", "P/lab · deploy web")
                await heard("P/lab · deploy web")
                assert await core.pool.fetchval(
                    "SELECT name FROM terminals WHERE id = $1", t.id) == \
                    "deploy web"
                for bad in ("", "   ", "x" * 41, "2", " 12 "):
                    r = await call("PATCH", f"/api/terminals/{t.id}",
                                   {"name": bad})
                    assert r.status == 422, (bad, r.text)
                # The machine moves to another project, whose code changes.
                lab = (await call("POST", "/api/projects",
                                  {"name": "Lab"})).json()
                r = await call("POST", f"/api/machines/{machine_id}/move",
                               {"projectId": lab["id"]})
                assert r.status == 200, r.text
                await heard("L/lab · deploy web")
                r = await call("PATCH", f"/api/projects/{lab['id']}",
                               {"code": "LB"})
                assert r.status == 200, r.text
                await heard("LB/lab · deploy web")
                assert t.label == "LB/lab · deploy web"
            # Someone else's terminal is not there for them; an ended one
            # is not renamed.
            other = await sign_in_as(core, "anna@example.com")
            r = await call("PATCH", f"/api/terminals/{t.id}", {"name": "x"},
                           headers={**CHANGE, "cookie": other})
            assert r.status == 404, r.text
            await t.close()
            await _until(lambda: t.state == "ended", what="the end")
            r = await call("PATCH", f"/api/terminals/{t.id}", {"name": "late"})
            assert r.status == 409, r.text
            ended = (await call("GET", f"/api/terminals/{t.id}", headers={})).json()
            assert ended["label"] == "LB/lab · deploy web", ended
    finally:
        shutil.rmtree(home, ignore_errors=True)
