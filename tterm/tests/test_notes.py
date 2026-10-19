"""Notes in folders (0.1.0): what a note may hold, merging two devices'
saves, ticks, who may do what, and the page's API for them."""
import json
import uuid

from ..core import accounts, auth, notes
from .harness import request, sign_in_as

CHANGE = {"x-tterm": "1", "content-type": "application/json"}
ME = "denis@example.com"


def p(block_id, text, kind="p", **more):
    return {"id": block_id, "type": kind, "text": text, **more}


# ------------------------------------------------------------- cleaning

def test_folder_names():
    assert notes.clean_name("  Runbooks ") == "Runbooks"
    assert notes.clean_name("Release\n2.4\t notes") == "Release 2.4 notes"
    assert notes.clean_name("") is None
    assert notes.clean_name("   ") is None
    assert notes.clean_name("x" * 81) is None
    assert notes.clean_name("x" * 80) == "x" * 80
    assert notes.clean_name(42) is None


def test_titles_are_kept_as_typed():
    # A space at the end stays: the page is still typing (its caret would
    # jump if the saved copy lost it). Control characters become spaces.
    assert notes.clean_title("Release ") == "Release "
    assert notes.clean_title("  two  spaces ") == "  two  spaces "
    assert notes.clean_title("a\tb\nc\x07d") == "a b c d"
    assert notes.clean_title(None) == ""
    for bad in (5, "x" * (notes.TITLE_MAX + 1), "broken \ud800"):
        try:
            notes.clean_title(bad)
        except notes.Invalid:
            continue
        raise AssertionError(f"taken: {bad!r}")


def test_blocks_are_checked_and_tidied():
    blocks = notes.clean_blocks([
        p("a", "one\r\ntwo"),
        p("b", "Heading\nwith a break", "h"),
        p("c", "item", "todo", done=True, by="someone", at=1),
        p("d", "x", "hr"),
        p("e", "bell\x07 and tab\t", "code"),
    ])
    assert blocks == [
        {"id": "a", "type": "p", "text": "one\ntwo"},
        {"id": "b", "type": "h", "text": "Heading with a break"},
        {"id": "c", "type": "todo", "text": "item", "done": True},
        {"id": "d", "type": "hr", "text": ""},
        {"id": "e", "type": "code", "text": "bell and tab\t"},
    ], blocks
    for bad in ([p("a", "x"), p("a", "y")],            # the same id twice
                [p("a b", "x")], [p("", "x")],          # ids are simple
                [{"id": "a", "type": "script", "text": "x"}],
                [{"id": "a", "type": "p", "text": 5}],
                [p("a", "x" * (notes.TEXT_MAX + 1))],
                [p("a", "broken \ud800")],             # not storable
                "not a list", [["p", "x"]]):
        try:
            notes.clean_blocks(bad)
        except notes.Invalid:
            continue
        raise AssertionError(f"taken: {bad!r}")
    many = [p(f"b{i}", "") for i in range(notes.BLOCKS_MAX + 1)]
    try:
        notes.clean_blocks(many)
    except notes.Invalid:
        pass
    else:
        raise AssertionError("too many blocks taken")
    big = [p(f"b{i}", "я" * 19_000) for i in range(20)]
    try:
        notes.clean_blocks(big)
    except notes.Invalid:
        pass
    else:
        raise AssertionError("a note over BODY_MAX taken")


def test_summary_counts_ticks_and_takes_the_first_text():
    blocks = [p("h", "Release", "h"), p("a", "", "p"),
              p("b", "Freeze merges", "todo", done=True),
              p("c", "Deploy", "todo", done=False),
              p("d", "First words\nsecond line")]
    assert notes.summary(blocks) == (2, 1, "First words")
    assert notes.summary([]) == (0, 0, "")


# ---------------------------------------------------------------- merge

BASE = [p("a", "one"), p("b", "two"), p("c", "three")]


def test_merge_keeps_both_sides_changes_to_different_blocks():
    mine = [p("a", "one!"), p("b", "two"), p("c", "three"), p("n", "mine new")]
    theirs = [p("a", "one"), p("b", "two?"), p("t", "theirs new"),
              p("c", "three")]
    title, blocks = notes.merge("T", BASE, "T", mine, "T", theirs)
    assert title == "T"
    assert [(b["id"], b["text"]) for b in blocks] == [
        ("a", "one!"), ("b", "two?"), ("t", "theirs new"), ("c", "three"),
        ("n", "mine new")], blocks


def test_merge_puts_a_new_block_after_the_one_it_followed():
    mine = [p("a", "one"), p("x", "after one"), p("b", "two"), p("c", "three")]
    theirs = [p("z", "on top"), p("a", "one"), p("b", "two"), p("c", "three")]
    _, blocks = notes.merge("T", BASE, "T", mine, "T", theirs)
    assert [b["id"] for b in blocks] == ["z", "a", "x", "b", "c"], blocks
    mine = [p("x", "first"), *BASE]
    _, blocks = notes.merge("T", BASE, "T", mine, "T", BASE)
    assert [b["id"] for b in blocks] == ["x", "a", "b", "c"], blocks


def test_merge_deletions():
    mine = [p("a", "one"), p("c", "three")]                  # b deleted here
    theirs = [p("a", "one"), p("b", "two"), p("c", "three!")]
    _, blocks = notes.merge("T", BASE, "T", mine, "T", theirs)
    assert [(b["id"], b["text"]) for b in blocks] == [("a", "one"),
                                                      ("c", "three!")]
    # Deleted on both sides: simply gone.
    _, blocks = notes.merge("T", BASE, "T", mine, "T", mine)
    assert [b["id"] for b in blocks] == ["a", "c"]


def test_merge_clashes():
    def clash(mine, theirs, mine_title="T", theirs_title="T"):
        try:
            notes.merge("T", BASE, mine_title, mine, theirs_title, theirs)
        except notes._Clash:
            return True
        return False
    # The same block, two different ways.
    assert clash([p("a", "one!"), *BASE[1:]], [p("a", "one?"), *BASE[1:]])
    # Deleted here, changed there.
    assert clash(BASE[1:], [p("a", "one?"), *BASE[1:]])
    # A block's kind changed one way, its text the other.
    assert clash([p("a", "one", "h"), *BASE[1:]], [p("a", "one?"), *BASE[1:]])
    # The title changed both ways.
    assert clash(BASE, BASE, "Mine", "Theirs")
    # The same change on both sides is no clash.
    assert not clash([p("a", "same"), *BASE[1:]], [p("a", "same"), *BASE[1:]])
    title, _ = notes.merge("T", BASE, "Mine", BASE, "T", BASE)
    assert title == "Mine"
    title, _ = notes.merge("T", BASE, "T", BASE, "Theirs", BASE)
    assert title == "Theirs"


def test_a_tick_is_never_a_clash():
    base = [p("a", "item", "todo", done=False)]
    theirs = [p("a", "item", "todo", done=True, by="x", at=1)]
    mine = [p("a", "item, reworded", "todo", done=False)]
    _, blocks = notes.merge("T", base, "T", mine, "T", theirs)
    assert blocks[0]["text"] == "item, reworded"


# -------------------------------------------------------------- database

async def _owner(pool):
    me = await accounts.ensure_local_owner(pool, "Denis Gerasimenko", ME)
    project = (await accounts.projects_of(pool, me.id))[0]
    return me, uuid.UUID(project["id"])


async def _person(pool, email, name):
    async with pool.acquire() as conn, conn.transaction():
        return await auth._make_account(conn, email, name)


async def _join(pool, project_id, person, role):
    await pool.execute(
        "INSERT INTO project_members (project_id, user_id, role) "
        "VALUES ($1, $2, $3)", project_id, person.id, role)


async def test_folders_made_renamed_deleted(h):
    async with h.pool() as pool:
        me, project = await _owner(pool)
        runbooks = await notes.create_folder(pool, me.id, project, " Runbooks ")
        assert runbooks["name"] == "Runbooks"
        assert runbooks["createdBy"]["initials"] == "DG"
        for name in ("runbooks", "RUNBOOKS "):
            try:
                await notes.create_folder(pool, me.id, project, name)
            except notes.Taken as exc:
                assert "Runbooks" in str(exc) or "runbooks" in str(exc).lower()
            else:
                raise AssertionError(f"a second folder named {name!r}")
        team = await notes.create_folder(pool, me.id, project, "Team")
        renamed = await notes.rename_folder(pool, me.id, uuid.UUID(team["id"]),
                                            "People and access")
        assert renamed["name"] == "People and access"
        # Its own name in another case is fine.
        await notes.rename_folder(pool, me.id, uuid.UUID(team["id"]),
                                  "people and access")
        try:
            await notes.rename_folder(pool, me.id, uuid.UUID(team["id"]),
                                      "RUNBOOKS")
        except notes.Taken:
            pass
        else:
            raise AssertionError("renamed onto another folder's name")
        listed = (await notes.folders_of(pool, [project]))[str(project)]
        assert [f["name"] for f in listed] == ["people and access", "Runbooks"]
        gone = await notes.delete_folder(pool, me.id, uuid.UUID(team["id"]))
        assert gone["notes"] == 0
        kinds = [r["kind"] for r in await pool.fetch(
            "SELECT kind FROM journal WHERE kind LIKE 'folder.%' ORDER BY id")]
        assert kinds == ["folder.created", "folder.created", "folder.deleted"]


async def test_notes_saved_merged_ticked(h):
    async with h.pool() as pool:
        me, project = await _owner(pool)
        folder = await notes.create_folder(pool, me.id, project, "Release")
        note = await notes.create_note(pool, me.id, uuid.UUID(folder["id"]),
                                       "Checklist")
        nid = uuid.UUID(note["id"])
        assert (note["version"], note["title"], note["blocks"]) == (1, "Checklist", [])
        assert note["canDelete"] and note["canWrite"]
        v2 = await notes.save_note(pool, me.id, nid, 1, "Checklist", [
            p("a", "Freeze merges", "todo"), p("b", "Deploy", "todo"),
            p("c", "If it goes wrong:")])
        assert v2["version"] == 2
        # The same save again changes nothing and keeps the version.
        same = await notes.save_note(pool, me.id, nid, 2, "Checklist",
                                     v2["blocks"])
        assert same["version"] == 2
        # A tick: who and when from the core.
        ticked = await notes.tick(pool, me.id, nid, "a", True)
        item = ticked["blocks"][0]
        assert ticked["version"] == 3 and item["done"] is True
        assert item["by"] == str(me.id) and isinstance(item["at"], int)
        assert ticked["people"][str(me.id)]["initials"] == "DG"
        # An item that is not there, or not a checklist item: refused, but
        # not as a note that is gone.
        for block_id in ("zz", "c"):
            try:
                await notes.tick(pool, me.id, nid, block_id, True)
            except notes.Invalid:
                pass
            else:
                raise AssertionError(f"ticked {block_id}")
        # Another device saves from version 2: its text change stays, the
        # tick stays, and the page cannot take the tick off by saving.
        merged = await notes.save_note(pool, me.id, nid, 2, "Checklist", [
            p("a", "Freeze merges to main", "todo", done=False),
            p("b", "Deploy", "todo", done=True), p("c", "If it goes wrong:")])
        assert merged["version"] == 4
        a, b = merged["blocks"][0], merged["blocks"][1]
        assert (a["text"], a["done"], a["by"]) == ("Freeze merges to main",
                                                    True, str(me.id))
        assert b["done"] is False, "ticks come only through tick()"
        row = await pool.fetchrow(
            "SELECT todo_total, todo_done, excerpt FROM notes WHERE id = $1", nid)
        assert tuple(row) == (2, 1, "If it goes wrong:")
        # The same block changed two ways: a conflict, with the note as now.
        try:
            await notes.save_note(pool, me.id, nid, 3, "Checklist", [
                p("a", "Freeze everything", "todo"), p("b", "Deploy", "todo"),
                p("c", "If it goes wrong:")])
        except notes.Conflict as exc:
            assert exc.note["version"] == 4
        else:
            raise AssertionError("no conflict")
        # The page merges on its own, keeping its text where both changed,
        # and saves that from the version the conflict named.
        resolved = await notes.save_note(pool, me.id, nid, 4, "Checklist", [
            p("a", "Freeze everything", "todo"), p("b", "Deploy", "todo"),
            p("c", "If it goes wrong:")])
        assert resolved["version"] == 5
        assert resolved["blocks"][0]["text"] == "Freeze everything"
        assert resolved["blocks"][0]["done"] is True, "the tick stays the core's"
        # A new item may come ticked: by whoever saved it.
        more = await notes.save_note(pool, me.id, nid, 5, "Checklist", [
            *resolved["blocks"], p("d", "Tell the client", "todo", done=True)])
        assert more["blocks"][-1]["by"] == str(me.id)
        # A version from the future, or one long gone, cannot be merged.
        for base in (99, 0):
            try:
                await notes.save_note(pool, me.id, nid, base, "x", [])
            except notes.Conflict:
                pass
            else:
                raise AssertionError(f"merged from version {base}")


async def test_old_versions_are_dropped(h):
    async with h.pool() as pool:
        me, project = await _owner(pool)
        folder = await notes.create_folder(pool, me.id, project, "F")
        note = await notes.create_note(pool, me.id, uuid.UUID(folder["id"]))
        nid = uuid.UUID(note["id"])
        version = 1
        for i in range(notes.KEEP_VERSIONS + 10):
            version = (await notes.save_note(pool, me.id, nid, version, "",
                                             [p("a", str(i))]))["version"]
        kept = await pool.fetchval(
            "SELECT count(*) FROM note_versions WHERE note_id = $1", nid)
        assert kept == notes.KEEP_VERSIONS, kept


async def test_moving_and_deleting(h):
    async with h.pool() as pool:
        me, project = await _owner(pool)
        a = await notes.create_folder(pool, me.id, project, "A")
        b = await notes.create_folder(pool, me.id, project, "B")
        note = await notes.create_note(pool, me.id, uuid.UUID(a["id"]), "N")
        moved = await notes.move_note(pool, me.id, uuid.UUID(note["id"]),
                                      uuid.UUID(b["id"]))
        assert moved["folderId"] == b["id"] and moved["folder"] == "B"
        # Not into another person's project.
        other = await _person(pool, "kira@example.com", "Kira Novak")
        their_project = uuid.UUID((await accounts.projects_of(pool, other.id))[0]["id"])
        theirs = await notes.create_folder(pool, other.id, their_project, "Kira's")
        try:
            await notes.move_note(pool, me.id, uuid.UUID(note["id"]),
                                  uuid.UUID(theirs["id"]))
        except notes.Invalid:
            pass
        else:
            raise AssertionError("moved into someone else's project")
        try:
            await notes.move_note(pool, me.id, uuid.UUID(note["id"]), uuid.uuid4())
        except notes.NotFound:
            pass
        else:
            raise AssertionError("moved into a folder that is not there")
        await notes.create_note(pool, me.id, uuid.UUID(b["id"]), "Second")
        # The lists: the newest first, and who wrote and edited each.
        listed = (await notes.folders_of(pool, [project]))[str(project)]
        in_b = next(f for f in listed if f["name"] == "B")["notes"]
        assert [n["title"] for n in in_b] == ["Second", "N"]
        assert in_b[0]["createdBy"]["name"] == "Denis Gerasimenko"
        assert in_b[0]["editedBy"]["initials"] == "DG"
        assert in_b[1]["version"] == 1, "the version, to tell copies apart"
        gone = await notes.delete_note(pool, me.id, uuid.UUID(note["id"]))
        assert gone["title"] == "N"
        folder_gone = await notes.delete_folder(pool, me.id, uuid.UUID(b["id"]))
        assert folder_gone["notes"] == 1
        assert await pool.fetchval("SELECT count(*) FROM notes") == 0
        assert await pool.fetchval("SELECT count(*) FROM note_versions") == 0
        details = await pool.fetchval(
            "SELECT details FROM journal WHERE kind = 'folder.deleted'")
        assert json.loads(details)["notes"] == 1


async def test_who_may_do_what(h):
    async with h.pool() as pool:
        me, project = await _owner(pool)
        sam = await _person(pool, "sam@example.com", "Sam Lee")
        oleg = await _person(pool, "oleg@example.com", "Oleg Petrov")
        watcher = await _person(pool, "vera@example.com", "Vera Watch")
        stranger = await _person(pool, "eve@example.com", "Eve Stranger")
        await _join(pool, project, sam, "member")
        await _join(pool, project, oleg, "admin")
        await _join(pool, project, watcher, "observer")
        mine = await notes.create_folder(pool, me.id, project, "Runbooks")
        folder_id = uuid.UUID(mine["id"])
        note = await notes.create_note(pool, me.id, folder_id, "Deploy")
        nid = uuid.UUID(note["id"])

        async def refused(call, kind):
            try:
                await call
            except kind:
                return
            raise AssertionError(f"not refused with {kind.__name__}")

        # Someone outside the project does not even see it.
        await refused(notes.get_note(pool, stranger.id, nid), notes.NotFound)
        await refused(notes.save_note(pool, stranger.id, nid, 1, "x", []),
                      notes.NotFound)
        await refused(notes.create_folder(pool, stranger.id, project, "X"),
                      notes.NotFound)
        await refused(notes.delete_folder(pool, stranger.id, folder_id),
                      notes.NotFound)
        # An observer reads only.
        seen = await notes.get_note(pool, watcher.id, nid)
        assert seen["title"] == "Deploy" and not seen["canWrite"]
        await refused(notes.save_note(pool, watcher.id, nid, 1, "x", []),
                      notes.NotAllowed)
        await refused(notes.tick(pool, watcher.id, nid, "a", True),
                      notes.NotAllowed)
        await refused(notes.create_folder(pool, watcher.id, project, "X"),
                      notes.NotAllowed)
        # A member writes, but deletes only their own note and their own
        # empty folder.
        seen = await notes.get_note(pool, sam.id, nid)
        assert seen["canWrite"] and not seen["canDelete"]
        await notes.save_note(pool, sam.id, nid, 1, "Deploy", [p("a", "by Sam")])
        await refused(notes.delete_note(pool, sam.id, nid), notes.NotAllowed)
        await refused(notes.delete_folder(pool, sam.id, folder_id),
                      notes.NotAllowed)
        sams = await notes.create_folder(pool, sam.id, project, "Sam's")
        sams_note = await notes.create_note(pool, sam.id, uuid.UUID(sams["id"]))
        await refused(notes.delete_folder(pool, sam.id, uuid.UUID(sams["id"])),
                      notes.NotAllowed)
        # Made an observer, Sam reads his own note but deletes it no more.
        await pool.execute("UPDATE project_members SET role = 'observer' "
                           "WHERE user_id = $1 AND project_id = $2", sam.id, project)
        assert not (await notes.get_note(pool, sam.id,
                                         uuid.UUID(sams_note["id"])))["canDelete"]
        await refused(notes.delete_note(pool, sam.id, uuid.UUID(sams_note["id"])),
                      notes.NotAllowed)
        await pool.execute("UPDATE project_members SET role = 'member' "
                           "WHERE user_id = $1 AND project_id = $2", sam.id, project)
        await notes.delete_note(pool, sam.id, uuid.UUID(sams_note["id"]))
        await notes.delete_folder(pool, sam.id, uuid.UUID(sams["id"]))
        # An admin deletes anyone's.
        assert (await notes.get_note(pool, oleg.id, nid))["canDelete"]
        await notes.delete_note(pool, oleg.id, nid)
        await notes.delete_folder(pool, oleg.id, folder_id)
        # A membership that ended counts as none.
        await pool.execute("UPDATE project_members SET until = now() - "
                           "interval '1 minute' WHERE user_id = $1", sam.id)
        await refused(notes.create_folder(pool, sam.id, project, "Late"),
                      notes.NotFound)


# ------------------------------------------------------------------- API

async def test_the_page_works_with_notes(h):
    async with h.core() as core:
        port = core.page_port
        state = (await request(port, "GET", "/api/state")).json()
        project = state["projects"][0]
        assert project["folders"] == []

        async def call(method, path, body=None, headers=CHANGE):
            data = json.dumps(body).encode() if body is not None else None
            return await request(port, method, path, body=data, headers=headers)

        r = await call("POST", "/api/folders", {"projectId": project["id"],
                                                "name": "Runbooks"},
                       headers={"content-type": "application/json"})
        assert r.status == 403, "a change without the page's header"
        r = await call("POST", "/api/folders", {"projectId": project["id"],
                                                "name": "Runbooks"})
        assert r.status == 201, r.text
        folder = r.json()
        r = await call("POST", "/api/folders", {"projectId": project["id"],
                                                "name": "runbooks"})
        assert r.status == 409, r.text
        r = await call("POST", "/api/notes", {"folderId": folder["id"]})
        assert r.status == 201, r.text
        note = r.json()
        r = await call("PUT", f"/api/notes/{note['id']}", {
            "baseVersion": 1, "title": "Deploy web-01",
            "blocks": [p("a", "git pull", "code"), p("b", "Check it", "todo")]})
        assert r.status == 200, r.text
        assert r.json()["version"] == 2
        r = await call("POST", f"/api/notes/{note['id']}/tick",
                       {"blockId": "b", "done": True})
        assert r.status == 200 and r.json()["blocks"][1]["done"] is True, r.text
        r = await call("PUT", f"/api/notes/{note['id']}", {
            "baseVersion": 2, "title": "Deploy web-01",
            "blocks": [p("a", "git pull --rebase", "code"),
                       p("b", "Check it", "todo")]})
        assert r.status == 200 and r.json()["version"] == 4, r.text
        r = await call("PUT", f"/api/notes/{note['id']}", {
            "baseVersion": 2, "title": "Deploy web-01",
            "blocks": [p("a", "git fetch", "code"), p("b", "Check it", "todo")]})
        assert r.status == 409 and r.json()["conflict"] is True, r.text
        assert r.json()["note"]["blocks"][0]["text"] == "git pull --rebase"
        for base in (None, 0, 2 ** 31, True, "4"):
            body = {"title": "x"} if base is None else {"title": "x", "baseVersion": base}
            r = await call("PUT", f"/api/notes/{note['id']}", body)
            assert r.status == 422, (base, r.text)
        # An item that is not there: refused, and not as a note that is gone.
        r = await call("POST", f"/api/notes/{note['id']}/tick",
                       {"blockId": "zz", "done": True})
        assert r.status == 422, r.text
        state = (await request(port, "GET", "/api/state")).json()
        listed = state["projects"][0]["folders"][0]
        assert listed["name"] == "Runbooks"
        assert [(n["title"], n["total"], n["done"], n["excerpt"])
                for n in listed["notes"]] == [("Deploy web-01", 1, 1,
                                               "git pull --rebase")]
        # Too big to read at all.
        huge = json.dumps({"baseVersion": 4, "title": "",
                           "blocks": [p("a", "x" * 1_100_000)]}).encode()
        r = await request(port, "PUT", f"/api/notes/{note['id']}", body=huge,
                          headers=CHANGE)
        assert r.status == 413, r.status
        # Another person's session sees none of it.
        other = await sign_in_as(core, "anna@example.com")
        r = await request(port, "GET", f"/api/notes/{note['id']}",
                          headers={"cookie": other})
        assert r.status == 404, r.status
        r = await request(port, "DELETE", f"/api/folders/{folder['id']}",
                          headers={**CHANGE, "cookie": other})
        assert r.status == 404, r.status
        theirs = (await request(port, "GET", "/api/state",
                                headers={"cookie": other})).json()
        assert all(p_["folders"] == [] for p_ in theirs["projects"])
        # The owner deletes the note, then the folder.
        r = await call("DELETE", f"/api/notes/{note['id']}")
        assert r.status == 200 and r.json()["title"] == "Deploy web-01", r.text
        r = await call("GET", f"/api/notes/{note['id']}", headers={})
        assert r.status == 404
        r = await call("DELETE", f"/api/folders/{folder['id']}")
        assert r.status == 200 and r.json()["notes"] == 0, r.text
