#!/usr/bin/env bash
# Record an operational anomaly straight into the panel's Проблеми list (processing_issue).
#
# WHY from a shell script and not via the panel's API: these checks must work precisely when the panel
# is unhealthy — an ops alert that needs the thing it monitors to be alive is not an alert. Writing to
# admin.db directly needs no auth, no network, and no running container.
#
# Deduped by (source, kind) like the panel's own recorder: a repeated failure bumps `count` and
# `last_seen` rather than spamming a new row every 5 minutes.
#   usage: ops-issue.sh <kind> <severity> <summary> [detail-json]
#          ops-issue.sh --resolve <kind>          # auto-close when the condition clears
set -euo pipefail
DB=/home/darling/product-review/admin-panel/backend/admin.db
# admin.db is root-owned (created by the container, which runs as root) — sudo, and keep the WAL/journal
# files root-owned too so the container is never blocked by a stray darling-owned sidecar file.
q() { sudo sqlite3 "$DB" "$@"; }
esc() { printf '%s' "$1" | sed "s/'/''/g"; }

if [ "${1:-}" = "--resolve" ]; then
  kind=$(esc "${2:?kind}")
  q "UPDATE processing_issue SET resolved=1, resolved_at=CURRENT_TIMESTAMP, resolved_by='автоматично'
     WHERE resolved=0 AND source='ops' AND kind='$kind';"
  exit 0
fi

kind=$(esc "${1:?kind}"); sev=$(esc "${2:?severity}"); summary=$(esc "${3:?summary}")
detail=$(esc "${4:-{\}}")
existing=$(q "SELECT id FROM processing_issue WHERE resolved=0 AND source='ops' AND kind='$kind' LIMIT 1;")
if [ -n "$existing" ]; then
  q "UPDATE processing_issue SET count=count+1, last_seen=CURRENT_TIMESTAMP,
       summary='$summary', detail='$detail', severity='$sev' WHERE id=$existing;"
else
  q "INSERT INTO processing_issue (source, topic, order_ref, severity, kind, summary, detail)
     VALUES ('ops', 'ops', '', '$sev', '$kind', '$summary', '$detail');"
fi
