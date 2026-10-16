"""How much tTerm Web carries, written down once an hour: the numbers to look
at before the plans' limits or the server change. Counts only, no one's data;
kept a year. deploy/stats.sh shows them on the server.
"""
from __future__ import annotations

import os

import asyncpg

#: How long the counts are kept.
KEEP_DAYS = 365


def core_memory() -> int:
    """The memory this process takes now, resident, in bytes; 0 where the
    system does not say (only Linux does, in /proc)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            resident = int(f.read().split()[1])
    except (OSError, ValueError, IndexError):
        return 0
    return resident * os.sysconf("SC_PAGE_SIZE")


async def take(pool: asyncpg.Pool, *, computers_online: int,
               terminals_peak: int, terminals_top: int) -> None:
    """Writes down the counts of now; what the core alone knows (agents and
    terminals in its memory) comes from the caller. Counts older than a
    year go."""
    await pool.execute(
        """
        INSERT INTO usage_stats (users, active_day, signups_day, codes_day,
                                 machines, machines_top, computers_online,
                                 terminals_peak, terminals_top, commands_day,
                                 database_bytes, core_bytes)
        SELECT
            (SELECT count(*) FROM users),
            (SELECT count(DISTINCT user_id) FROM sessions
             WHERE last_seen_at > now() - interval '1 day'),
            (SELECT count(*) FROM users
             WHERE created_at > now() - interval '1 day'),
            (SELECT count(*) FROM login_codes
             WHERE created_at > now() - interval '1 day'),
            (SELECT count(*) FROM machines WHERE removed_at IS NULL),
            (SELECT COALESCE(max(n), 0) FROM (
                SELECT count(*) AS n FROM machines m
                JOIN projects p ON p.id = m.project_id
                WHERE m.removed_at IS NULL GROUP BY p.owner_id) per_person),
            $1, $2, $3,
            (SELECT count(*) FROM blocks
             WHERE started_at > now() - interval '1 day'),
            pg_database_size(current_database()),
            $4
        ON CONFLICT (taken_at) DO NOTHING
        """, computers_online, terminals_peak, terminals_top, core_memory())
    await pool.execute(
        "DELETE FROM usage_stats WHERE taken_at < now() - make_interval(days => $1)",
        KEEP_DAYS)
