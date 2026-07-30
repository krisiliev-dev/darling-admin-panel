#!/usr/bin/env bash
# Daily backup of the panel-only DB (admin.db): local snapshot (30d) + offsite copy to Cloudflare R2.
#
# 2026-07-30: the R2 upload used to end in `2>/dev/null || true` — an rclone failure produced no stderr
# and no non-zero exit, so the script reported success while the off-site copy quietly stopped. You would
# have found out when you needed a restore. Failures are now LOUD, recorded in the panel's Проблеми, and
# the local snapshot is kept regardless. Same rule as everywhere else here: watch the STALENESS, and
# never let absence of alarm read as success.
set -euo pipefail
OUT=/home/darling/backups; STAMP=$(date +%F)
SNAP="${OUT}/admin.db.${STAMP}"
ISSUE=/home/darling/ops-issue.sh

# --- local snapshot, fail-closed on an empty or corrupt dump ------------------------------
sqlite3 /home/darling/product-review/admin-panel/backend/admin.db ".backup ${SNAP}"
if [ ! -s "$SNAP" ]; then
  echo "[backup] FATAL: snapshot is empty — NOT replacing yesterday's backup" >&2
  "$ISSUE" backup_failed error "Резервното копие на admin.db е ПРАЗНО — не е направено копие днес." || true
  exit 1
fi
if ! sqlite3 "$SNAP" 'PRAGMA integrity_check;' | grep -qx 'ok'; then
  echo "[backup] FATAL: snapshot failed integrity_check — keeping it for inspection, not uploading" >&2
  "$ISSUE" backup_failed error "Резервното копие на admin.db е ПОВРЕДЕНО (integrity_check се провали)." || true
  exit 1
fi
gzip -f "$SNAP"
find "$OUT" -name 'admin.db.*.gz' -mtime +30 -delete

# --- offsite → R2 ------------------------------------------------------------------------
if /home/darling/bin/rclone copy "$OUT" r2:darling-admin-panel-backups/ --include "admin.db.*.gz"; then
  rm -f /home/darling/.r2-upload-failed
  "$ISSUE" --resolve backup_offsite_stale || true
else
  echo "[backup] WARN: R2 upload FAILED — local backup kept, the OFF-SITE copy is now STALE" >&2
  date -u +%FT%TZ > /home/darling/.r2-upload-failed
  "$ISSUE" backup_offsite_stale error \
    "Копието към Cloudflare R2 се провали — локалното копие е налице, но ИЗВЪННИТЕ копия остаряват. Провери rclone/ключовете." || true
fi
