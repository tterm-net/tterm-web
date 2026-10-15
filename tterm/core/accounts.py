"""People and projects.

Stage 1 has no sign-in. The page opens only on the owner's laptop, and
whatever it does is done as one person, the local owner, created on the first
start together with their first project, Personal, with the code P.
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


async def ensure_local_owner(pool: asyncpg.Pool, name: str) -> Person:
    """The person this laptop belongs to, created on the first start.

    Creates them together with the Personal project, code P. Later starts
    find them and only refresh the name, if it changed in the settings.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", OWNER_LOCK)
        row = await conn.fetchrow(
            "SELECT id, name, theme, plan_id FROM users WHERE is_local_owner")
        if row is None:
            user_id = await conn.fetchval(
                "INSERT INTO users (name, is_local_owner) VALUES ($1, true) "
                "RETURNING id", name)
            await journal(conn, "user.created", user_id=user_id,
                          details={"name": name, "local_owner": True})
            await create_project(conn, user_id, "Personal", "P")
            return Person(user_id, name, "system", "free")
        if row["name"] != name:
            await conn.execute("UPDATE users SET name = $2 WHERE id = $1",
                               row["id"], name)
        return Person(row["id"], name, row["theme"], row["plan_id"])


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


async def person(pool: asyncpg.Pool, user_id: uuid.UUID) -> Person:
    row = await pool.fetchrow(
        "SELECT id, name, theme, plan_id FROM users WHERE id = $1", user_id)
    if row is None:
        raise LookupError(f"no such person: {user_id}")
    return Person(row["id"], row["name"], row["theme"], row["plan_id"])


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
