"""People and projects.

Everyone signs in (from 0.0.9, see auth.py) and gets their first project,
Personal, with the code P. On the laptop the local owner of stage 1 stays:
the person this laptop belongs to, whose machines and history are there;
TTERM_OWNER_EMAIL gives them the address they sign in with.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Collection
from dataclasses import dataclass

import asyncpg

#: Held while the local owner is created, so that two processes starting at
#: once do not create two of them.
OWNER_LOCK = 0x7474_0002

THEMES = ("system", "light", "dark")

#: How long journal entries that belong to no project are kept.
PERSONAL_JOURNAL_DAYS = 365

_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class Person:
    id: uuid.UUID
    name: str
    theme: str
    plan: str
    email: str | None = None

    @property
    def first_name(self) -> str:
        return first_name(self.name)

    @property
    def initials(self) -> str:
        return initials(self.name)


def first_name(name: str) -> str:
    words = name.split()
    return words[0] if words else name


def initials(name: str) -> str:
    """Two letters for the avatar, as people expect: Denis Gerasimenko → DG.

    First and last word, so that a middle name does not push the surname out.
    A single word gives a single letter.
    """
    words = _WORD.findall(name)
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[-1][0]).upper()


def valid_code(code: str) -> bool:
    """One to four letters or digits."""
    return 1 <= len(code) <= 4 and all(ch.isalnum() and ch != "_" for ch in code)


def suggest_code(name: str, taken: Collection[str] = ()) -> str:
    """The code offered when a project is created: initials of its name.

    Client X → CX, Lisbon Bikes → LB, Personal → P. If the owner already has
    that code, a digit is added: CX2, CX3. The person can always change it.
    """
    words = _WORD.findall(name)
    base = "".join(w[0] for w in words[:4]).upper() if words else "P"
    if base not in taken:
        return base
    stem = base[:3]
    for digit in range(2, 10):
        candidate = f"{stem}{digit}"
        if candidate not in taken:
            return candidate
    for number in range(10, 1000):
        candidate = f"{base[0]}{number}"
        if candidate not in taken:
            return candidate
    raise ValueError("no free project code")


async def ensure_local_owner(pool: asyncpg.Pool, name: str,
                             email: str) -> Person:
    """The person this laptop belongs to, created on the first start, with
    the address they sign in with.

    Creates them together with the Personal project, code P, named as the
    settings say. Later starts find them and refresh only the address, if it
    changed in the settings: from 0.0.10 the name is the person's to change,
    on the account page, and a start does not take it back. An account that
    signed in with that address before the laptop's owner had it is left
    alone: the address stays theirs.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", OWNER_LOCK)
        row = await conn.fetchrow(
            "SELECT id, name, theme, plan_id, email FROM users "
            "WHERE is_local_owner")
        taken = await conn.fetchval(
            "SELECT id FROM users WHERE lower(email) = $1 AND NOT is_local_owner",
            email)
        if taken is not None:
            raise ValueError(f"{email} already belongs to another account")
        if row is None:
            user_id = await conn.fetchval(
                "INSERT INTO users (name, email, is_local_owner) "
                "VALUES ($1, $2, true) RETURNING id", name, email)
            await journal(conn, "user.created", user_id=user_id,
                          details={"name": name, "local_owner": True})
            await create_project(conn, user_id, "Personal", "P")
            row_theme, row_plan = "system", "free"
        else:
            user_id, row_theme, row_plan = row["id"], row["theme"], row["plan_id"]
            name = row["name"]
            if (row["email"] or "") != email:
                await conn.execute("UPDATE users SET email = $2 WHERE id = $1",
                                   user_id, email)
        # The address they sign in with: this one, and no other.
        await conn.execute(
            "DELETE FROM sign_in_methods WHERE user_id = $1 AND kind = 'email' "
            "AND subject <> $2", user_id, email)
        await conn.execute(
            "INSERT INTO sign_in_methods (user_id, kind, subject) "
            "VALUES ($1, 'email', $2) ON CONFLICT DO NOTHING", user_id, email)
        return Person(user_id, name, row_theme, row_plan, email)


async def local_owner(pool: asyncpg.Pool) -> Person | None:
    """The person this laptop belongs to, if there is one."""
    row = await pool.fetchrow(
        "SELECT id, name, theme, plan_id, email FROM users WHERE is_local_owner")
    if row is None:
        return None
    return Person(row["id"], row["name"], row["theme"], row["plan_id"],
                  row["email"])


async def create_project(conn: asyncpg.Connection, owner_id: uuid.UUID,
                         name: str, code: str) -> uuid.UUID:
    code = code.upper()
    if not valid_code(code):
        raise ValueError(f"project code must be 1 to 4 letters or digits: {code!r}")
    project_id = await conn.fetchval(
        "INSERT INTO projects (owner_id, name, code) VALUES ($1, $2, $3) "
        "RETURNING id", owner_id, name, code)
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id, role) "
        "VALUES ($1, $2, 'owner')", project_id, owner_id)
    await journal(conn, "project.created", project_id=project_id,
                  user_id=owner_id, details={"name": name, "code": code})
    return project_id


async def journal(conn: asyncpg.Connection, kind: str, *,
                  project_id: uuid.UUID | None = None,
                  user_id: uuid.UUID | None = None,
                  machine_id: uuid.UUID | None = None,
                  terminal_id: uuid.UUID | None = None,
                  details: dict | None = None) -> None:
    """Appends to the journal, kept as long as the project says."""
    await conn.execute(
        """
        INSERT INTO journal (project_id, user_id, kind, machine_id, terminal_id,
                             details, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb,
                now() + make_interval(days => COALESCE(
                    (SELECT journal_days FROM projects WHERE id = $1), $7)))
        """,
        project_id, user_id, kind, machine_id, terminal_id,
        _json(details or {}), PERSONAL_JOURNAL_DAYS)


async def set_theme(pool: asyncpg.Pool, user_id: uuid.UUID, theme: str) -> None:
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {', '.join(THEMES)}")
    await pool.execute("UPDATE users SET theme = $2 WHERE id = $1", user_id, theme)


async def set_name(pool: asyncpg.Pool, user_id: uuid.UUID, name: str) -> None:
    """The person's name, as they changed it on the account page (0.0.10).
    Cleaned by the caller (auth.clean_name). The journal notes that it
    changed, not what it was."""
    async with pool.acquire() as conn, conn.transaction():
        changed = await conn.fetchval(
            "UPDATE users SET name = $2 WHERE id = $1 AND name <> $2 "
            "RETURNING id", user_id, name)
        if changed is not None:
            await journal(conn, "user.renamed", user_id=user_id)


async def plan_of(pool: asyncpg.Pool, user_id: uuid.UUID) -> dict:
    """The person's plan for the account page: its name, its limits, and
    the machines of their own projects that count against "machines". The
    open terminals are the core's to count (Terminals.live)."""
    row = await pool.fetchrow(
        """
        SELECT p.id, p.name, p.limits,
               (SELECT count(*) FROM machines m
                JOIN projects pr ON pr.id = m.project_id
                WHERE pr.owner_id = u.id AND m.removed_at IS NULL) AS machines
        FROM users u JOIN plans p ON p.id = u.plan_id WHERE u.id = $1
        """, user_id)
    if row is None:
        raise LookupError(f"no such person: {user_id}")
    limits = json.loads(row["limits"]) if isinstance(row["limits"], str) \
        else dict(row["limits"] or {})

    def most(key: str) -> int | None:
        value = limits.get(key)
        # 50, or 50.0 and 1e2 as JSON may write them (as in limit()).
        return None if value is None else int(float(value))

    return {"id": row["id"], "name": row["name"],
            "limits": {"machines": most("machines"),
                       "terminals": most("terminals")},
            "machines": row["machines"]}


async def limit(pool: asyncpg.Pool | asyncpg.Connection, user_id: uuid.UUID,
                key: str) -> tuple[str, int | None]:
    """The person's plan by name, and the most of `key` it allows
    ("machines", "terminals"); None when the plan sets no limit for it.
    Plans live in the database: a limit changes there, with no release, and
    the database takes only whole numbers under known names (006)."""
    row = await pool.fetchrow(
        "SELECT p.name, p.limits ->> $2 AS most FROM users u "
        "JOIN plans p ON p.id = u.plan_id WHERE u.id = $1", user_id, key)
    if row is None:
        return "", None
    if row["most"] is None:
        return row["name"], None
    # 50, or 50.0 and 1e2 as JSON may write them.
    return row["name"], int(float(row["most"]))


async def person(pool: asyncpg.Pool, user_id: uuid.UUID) -> Person:
    row = await pool.fetchrow(
        "SELECT id, name, theme, plan_id, email FROM users WHERE id = $1",
        user_id)
    if row is None:
        raise LookupError(f"no such person: {user_id}")
    return Person(row["id"], row["name"], row["theme"], row["plan_id"],
                  row["email"])


async def projects_of(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[dict]:
    """The person's projects with their machines, as the sidebar shows them.

    Their own projects first, the oldest first, so Personal leads.
    """
    rows = await pool.fetch(
        """
        SELECT p.id, p.name, p.code, m.role, p.owner_id = $1 AS own
        FROM projects p
        JOIN project_members m ON m.project_id = p.id AND m.user_id = $1
        WHERE m.until IS NULL OR m.until > now()
        ORDER BY (p.owner_id = $1) DESC, p.created_at, p.name
        """, user_id)
    machines = await pool.fetch(
        """
        SELECT id, project_id, kind, name, host, last_seen_at,
               passport - 'commands' AS passport,
               jsonb_array_length(COALESCE(passport->'commands', '[]')) AS commands
        FROM machines
        WHERE removed_at IS NULL AND project_id = ANY($1::uuid[])
        ORDER BY lower(name)
        """, [r["id"] for r in rows])
    by_project: dict[uuid.UUID, list[dict]] = {}
    for m in machines:
        pp = json.loads(m["passport"]) if m["passport"] else {}
        pp["commands_count"] = m["commands"]
        seen = m["last_seen_at"]
        by_project.setdefault(m["project_id"], []).append({
            "id": str(m["id"]), "kind": m["kind"], "name": m["name"],
            "host": m["host"], "os": pp.get("os"), "passport": pp,
            # When its agent was last connected, for a computer: "last seen
            # 14 minutes ago" says more than offline alone.
            "lastSeen": int(seen.timestamp() * 1000) if seen else None})
    return [{
        "id": str(r["id"]),
        "name": r["name"],
        "code": r["code"],
        "role": r["role"],
        "machines": by_project.get(r["id"], []),
    } for r in rows]


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
