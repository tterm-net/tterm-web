#!/bin/sh
# How much tTerm Web carries: the plans' limits, the counts the core writes
# down every hour (a line a day, the last two weeks, the most of each day),
# and who has the most machines and terminals now. As root on the server:
#
#   sh /opt/tterm-web/deploy/stats.sh
#
# The numbers to look at before a limit changes; how to change one is in
# deploy/README.md, "Plans and their limits".
set -eu

[ "$(id -u)" = 0 ] || { echo "Run it as root." >&2; exit 1; }

q() { (cd / && runuser -u postgres -- psql -X -q -P pager=off -d tterm_web -c "$1"); }

echo "== The plans and their limits"
q "SELECT id, name, limits FROM plans ORDER BY id"

echo "== A day a line, the last 14 days"
q "SELECT taken_at::date AS day,
          max(users) AS people,
          max(active_day) AS active,
          max(signups_day) AS new,
          max(codes_day) AS codes,
          max(machines) AS machines,
          max(machines_top) AS \"most machines\",
          max(computers_online) AS \"computers on\",
          max(terminals_peak) AS terminals,
          max(terminals_top) AS \"most terminals\",
          max(commands_day) AS commands,
          pg_size_pretty(max(database_bytes)) AS database,
          pg_size_pretty(max(core_bytes)) AS \"core memory\"
   FROM usage_stats
   WHERE taken_at > now() - interval '14 days'
   GROUP BY 1 ORDER BY 1 DESC"

echo "== The most machines, now"
q "SELECT u.email,
          count(*) AS machines,
          count(*) FILTER (WHERE m.kind = 'server') AS servers,
          count(*) FILTER (WHERE m.kind = 'computer') AS computers
   FROM machines m
   JOIN projects p ON p.id = m.project_id
   JOIN users u ON u.id = p.owner_id
   WHERE m.removed_at IS NULL
   GROUP BY u.email ORDER BY machines DESC, u.email LIMIT 10"

echo "== The most terminals open, now"
q "SELECT u.email, count(*) AS terminals
   FROM terminals t JOIN users u ON u.id = t.owner_id
   WHERE t.closed_at IS NULL
   GROUP BY u.email ORDER BY terminals DESC, u.email LIMIT 10"
