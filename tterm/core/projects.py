"""Projects of one's own (from 0.1.1): made, changed, deleted, and machines
moved from one to another.

Anyone signed in makes a project, and owns it. Until people come into
projects, in a later step, each has only its owner in it. Personal, the
first project, made with the account, is renamed like any other but stays:
it is where machines go when nothing else is said. A project with machines
in it is not deleted: they are moved or removed first, so that nothing
loses its access to a server unasked. Its notes go with it; the project
itself stays in the database, with nobody in it, so that the journal and
the history of its old machines keep what they point to.

Each project has a color, one of eight, which marks it in the sidebar and
in tabs for everyone in it; a new one gets a color none of the owner's
other projects has, while there is one. Whether the sidebar shows only the
badge in that color or the whole block of the project is each person's own
choice (`set_tint`).
"""
from __future__ import annotations

import uuid

import asyncpg

from . import accounts, machines, notes
from .notes import Invalid, NotAllowed, NotFound, Refused, Taken

#: In the order a new project takes them.
COLORS = accounts.COLORS
#: How much of a project's color the sidebar shows: the badge, or the block.
TINTS = ("badge", "block")
#: Projects of one's own at once, at most: more is a mistake, not a need.
MOST_PROJECTS = 50
#: Made in a day, deleted ones too: making and deleting them in a loop
#: would leave the database ever more rows.
MOST_A_DAY = 50
MANAGERS = ("owner", "admin")

__all__ = ["COLORS", "TINTS", "Refused", "create", "edit", "delete",
           "move_machine", "set_tint", "tint_of"]


def clean_name(name: object) -> str:
    """A project's name: one line, 1 to 80 characters, as a folder's."""
    cleaned = notes.clean_name(name)
    if cleaned is None:
        raise Invalid("Name the project: up to 80 characters.")
    return cleaned


def clean_code(code: object) -> str:
    """The code that marks it in tabs: 1 to 4 letters or digits."""
    if not isinstance(code, str):
        raise Invalid("The code is 1 to 4 letters or digits.")
    code = code.strip().upper()
    if not accounts.valid_code(code):
        raise Invalid("The code is 1 to 4 letters or digits.")
    return code


def clean_color(color: object) -> str:
    if color not in COLORS:
        raise Invalid(f"The color is one of {', '.join(COLORS)}.")
    return str(color)


async def _codes(conn: asyncpg.Connection, owner_id: uuid.UUID) -> set[str]:
    return {r["code"] for r in await conn.fetch(
        "SELECT code FROM projects WHERE owner_id = $1 AND deleted_at IS NULL",
        owner_id)}


async def _lock_owner(conn: asyncpg.Connection, owner_id: uuid.UUID) -> None:
    """One change to a person's projects at a time: their count and their
    codes hold."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                       f"projects-of:{owner_id}")


async def _project(conn: asyncpg.Connection, project_id: uuid.UUID,
                   user_id: uuid.UUID) -> tuple[asyncpg.Record, str]:
    """The project, locked, with the person's role in it; NotFound when it
    is not theirs to see.

    Locked FOR NO KEY UPDATE: a line in the journal, or anything else that
    only points to the project, goes on meanwhile, so that what holds one
    of its machines or notes and then writes such a line never waits for
    this in turn. What must wait takes the row itself: a machine coming in
    (machines._make_room), a new folder or note (notes._hold)."""
    row = await conn.fetchrow(
        """
        SELECT p.id, p.owner_id, p.name, p.code, p.color, pm.role,
               p.id = (SELECT id FROM projects WHERE owner_id = p.owner_id
                       ORDER BY created_at, id LIMIT 1) AS personal
        FROM projects p
        JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = $2
         AND (pm.until IS NULL OR pm.until > now())
        WHERE p.id = $1 AND p.deleted_at IS NULL
        FOR NO KEY UPDATE OF p
        """, project_id, user_id)
    if row is None:
        raise NotFound("No such project.")
    return row, row["role"]


def _taken(code: str, own: bool = True) -> Taken:
    """Codes differ among one owner's projects: an admin changing another's
    project hears it of its owner."""
    whose = "You have" if own else "Its owner has"
    return Taken(f"{whose} a project with the code {code} already: pick "
                 "another.")


async def create(pool: asyncpg.Pool, user_id: uuid.UUID, name: object,
                 code: object = None, color: object = None) -> dict:
    """A new project of the person's own, as the sidebar lists it. Without a
    code, one made from the name; without a color, a free one."""
    name = clean_name(name)
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, user_id)
        count = await conn.fetchval(
            "SELECT count(*) FROM projects WHERE owner_id = $1 "
            "AND deleted_at IS NULL", user_id)
        if count >= MOST_PROJECTS:
            raise Refused(f"You have {count} projects, the most there may be. "
                          "Delete one to make another.")
        if await conn.fetchval(
                "SELECT count(*) FROM projects WHERE owner_id = $1 "
                "AND created_at > now() - interval '1 day'",
                user_id) >= MOST_A_DAY:
            raise Refused(f"You made {MOST_A_DAY} projects in the last day, "
                          "the most there may be. Make more tomorrow.")
        taken = await _codes(conn, user_id)
        if code is None or (isinstance(code, str) and not code.strip()):
            code = accounts.suggest_code(name, taken)
        code = clean_code(code)
        if code in taken:
            raise _taken(code)
        color = clean_color(color) if color is not None else \
            await accounts.free_color(conn, user_id)
        project_id = await accounts.create_project(conn, user_id, name, code,
                                                   color)
    return {"id": str(project_id), "name": name, "code": code, "color": color,
            "role": "owner", "personal": False, "own": True, "alone": True,
            "machines": [], "folders": []}


async def edit(pool: asyncpg.Pool, user_id: uuid.UUID, project_id: uuid.UUID,
               *, name: object = None, code: object = None,
               color: object = None) -> dict:
    """The name, the code or the color, any of them, changed by the owner or
    an admin. The journal notes what changed."""
    async with pool.acquire() as conn, conn.transaction():
        row, role = await _project(conn, project_id, user_id)
        if role not in MANAGERS:
            raise NotAllowed("The owner and admins change a project's name, "
                             "code and color.")
        await _lock_owner(conn, row["owner_id"])
        new = {"name": row["name"], "code": row["code"], "color": row["color"]}
        if name is not None:
            new["name"] = clean_name(name)
        if code is not None:
            new["code"] = clean_code(code)
            if new["code"] != row["code"] and \
                    new["code"] in await _codes(conn, row["owner_id"]):
                raise _taken(new["code"], own=row["owner_id"] == user_id)
        if color is not None:
            new["color"] = clean_color(color)
        changed = {k: v for k, v in new.items() if v != row[k]}
        if changed:
            await conn.execute(
                "UPDATE projects SET name = $2, code = $3, color = $4 "
                "WHERE id = $1", project_id, new["name"], new["code"],
                new["color"])
            await accounts.journal(
                conn, "project.changed", project_id=project_id,
                user_id=user_id,
                details={k: {"from": row[k], "to": v} for k, v in changed.items()})
    return {"id": str(project_id), **new}


async def delete(pool: asyncpg.Pool, user_id: uuid.UUID,
                 project_id: uuid.UUID) -> dict:
    """Deletes a project of the person's own with no machines in it, and its
    folders with their notes. Personal stays."""
    async with pool.acquire() as conn, conn.transaction():
        row, role = await _project(conn, project_id, user_id)
        if row["owner_id"] != user_id:
            raise NotAllowed("Only its owner deletes a project.")
        if row["personal"]:
            raise Refused(f"{row['name']} is your first project, where machines "
                          "go unless you say otherwise: it stays. Rename it if "
                          "you like.")
        # Machines come in under this project's row: with it locked, none
        # can come in while it goes (machines._make_room).
        names = [r["name"] for r in await conn.fetch(
            "SELECT name FROM machines WHERE project_id = $1 "
            "AND removed_at IS NULL ORDER BY lower(name)", project_id)]
        if names:
            raise Refused("Move or remove its machines first: "
                          f"{_listed(names)}.")
        folders = await conn.fetchval(
            "SELECT count(*) FROM note_folders WHERE project_id = $1",
            project_id)
        note_count = await conn.fetchval(
            "SELECT count(*) FROM notes n JOIN note_folders f "
            "ON f.id = n.folder_id WHERE f.project_id = $1", project_id)
        await conn.execute("DELETE FROM note_folders WHERE project_id = $1",
                           project_id)
        # Commands for it that were not used yet work no more.
        await conn.execute(
            "UPDATE install_tokens SET expires_at = now() WHERE project_id = $1 "
            "AND expires_at > now() AND used_at IS NULL", project_id)
        await conn.execute("DELETE FROM project_members WHERE project_id = $1",
                           project_id)
        await conn.execute(
            "UPDATE projects SET deleted_at = now() WHERE id = $1", project_id)
        await accounts.journal(conn, "project.deleted", project_id=project_id,
                               user_id=user_id,
                               details={"name": row["name"], "code": row["code"],
                                        "folders": folders, "notes": note_count})
    return {"id": str(project_id), "name": row["name"], "folders": folders,
            "notes": note_count}


async def move_machine(pool: asyncpg.Pool, user_id: uuid.UUID,
                       machine_id: uuid.UUID, project_id: uuid.UUID) -> dict:
    """Moves a machine to another project of the person's own. Its terminals
    go on running, nothing changes on the machine itself: a server keeps its
    line, a computer its key. Where the name is taken, it gets a number."""
    async with pool.acquire() as conn, conn.transaction():
        # Where it is now, to know which projects to lock; looked at again
        # once they are.
        was = await conn.fetchval(
            "SELECT project_id FROM machines WHERE id = $1 "
            "AND removed_at IS NULL", machine_id)
        if was is None:
            raise NotFound("No such machine.")
        # Machines coming into the target wait, and this for them: taken
        # first, as registrations take it (machines._register_server), so
        # that a server installed there meanwhile is one machine, not two.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           str(project_id))
        # Both projects locked, always in the same order.
        rows = {}
        for pid in sorted({was, project_id}):
            try:
                rows[pid] = await _project(conn, pid, user_id)
            except NotFound:
                if pid == was:
                    raise NotFound("No such machine.") from None
                raise NotFound("No such project.") from None
        source, _ = rows[was]
        target, _ = rows[project_id]
        # The machine last: a removal or another move that came first has
        # its way.
        machine = await conn.fetchrow(
            "SELECT id, name, kind, project_id, host_keys FROM machines "
            "WHERE id = $1 AND removed_at IS NULL FOR NO KEY UPDATE",
            machine_id)
        if machine is None:
            raise NotFound("No such machine.")
        if machine["project_id"] != was:
            raise Refused(f"{machine['name']} was just moved from another tab "
                          "or device. Look again.")
        if source["owner_id"] != user_id or target["owner_id"] != user_id:
            raise NotAllowed("A machine moves between projects you own.")
        if source["id"] == target["id"]:
            raise Invalid(f"{machine['name']} is in {target['name']} already.")
        if machine["kind"] == "server":
            twin = await conn.fetchval(
                "SELECT name FROM machines WHERE project_id = $1 "
                "AND removed_at IS NULL AND host_keys && $2::text[]",
                project_id, machine["host_keys"])
            if twin is not None:
                raise Refused(f"{target['name']} has this server already, "
                              f"as {twin}.")
        taken = {r["name"] for r in await conn.fetch(
            "SELECT name FROM machines WHERE project_id = $1 "
            "AND removed_at IS NULL", project_id)}
        name = machines.free_name(machine["name"], taken)
        await conn.execute(
            "UPDATE machines SET project_id = $2, name = $3 WHERE id = $1",
            machine_id, project_id, name)
        details = {"name": name, "kind": machine["kind"],
                   "from": str(source["id"]), "to": str(project_id),
                   "fromName": source["name"], "toName": target["name"]}
        if name != machine["name"]:
            details["was"] = machine["name"]
        for pid in (source["id"], project_id):
            await accounts.journal(conn, "machine.moved", project_id=pid,
                                   user_id=user_id, machine_id=machine_id,
                                   details=details)
    return {"id": str(machine_id), "name": name, "kind": machine["kind"],
            "projectId": str(project_id), "project": target["name"],
            "code": target["code"], "from": source["name"]}


async def set_tint(pool: asyncpg.Pool, user_id: uuid.UUID, tint: object) -> None:
    if tint not in TINTS:
        raise Invalid(f"The tint is one of {', '.join(TINTS)}.")
    await pool.execute("UPDATE users SET project_tint = $2 WHERE id = $1",
                       user_id, tint)


async def tint_of(pool: asyncpg.Pool, user_id: uuid.UUID) -> str:
    return await pool.fetchval(
        "SELECT project_tint FROM users WHERE id = $1", user_id) or "badge"


def _listed(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) > 5:
        names = names[:4] + [f"{len(names) - 4} more"]
    return ", ".join(names[:-1]) + " and " + names[-1]
