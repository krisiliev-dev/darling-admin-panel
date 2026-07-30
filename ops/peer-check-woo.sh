#!/usr/bin/env bash
# Panel box → Woo box health check (the mirror of peer-check.sh on the Woo side).
#
# Each box watches the other, so a box that dies cannot be the one reporting it. This covers the LIKELY
# failure (one box down) but NOT the correlated one — a Contabo account or DC problem takes both, and
# neither would be alive to complain. That still needs a monitor outside both; owner action.
#
# 🚨 WE ASSERT THE BODY, NOT THE STATUS. nginx can be perfectly alive while the health generator behind
# it has been failing for a week, serving a stale file with a cheerful 200. The status code proves the
# web server answered; only `"ok":true` proves anything about the box. (Both agents were bitten by the
# status-only version of this trap on the same day, in opposite directions.)
#
# Alerts on the 3rd CONSECUTIVE liveness failure — one blip is a hiccup, three at 5-minute spacing is an
# outage. STALENESS alerts fire immediately instead: a stale backup is stale on every run, so there is
# no flapping to damp, and waiting 15 minutes to say so buys nothing.
set -uo pipefail
URL="${PEER_URL:-https://staging.darlinghome.bg/peer-health}"
STATE=/home/darling/.peer-woo-failures
LOG=/home/darling/peer-check-woo.log
ISSUE=/home/darling/ops-issue.sh
THRESHOLD=3
MAX_AGE_H=36          # their backup/restore crons run daily; 36h = a run was missed, not just late

ts() { date -u +%FT%TZ; }
num() {   # extract a numeric JSON field; empty if absent
  printf '%s' "$1" | grep -oE "\"$2\"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?" | grep -oE '\-?[0-9]+(\.[0-9]+)?$' | head -1
}

# --- staleness assertions on a healthy body (also the --check-body self-test entry point) -------
check_ages() {
  local body="$1" mech age
  mech=$(printf '%s' "$body" | grep -o '"proves_mechanism_only"[[:space:]]*:[[:space:]]*true' || true)
  # a restore test that compares an empty DB to an empty DB proves the MECHANISM, not the data. Carry
  # that into the alert, so a green history is never later read as "months of verified restores".
  local caveat=""
  [ -n "$mech" ] && caveat=" ⚠ Тестът засега проверява само механизма (няма данни в базата)."

  for f in restore_test_age_h backup_age_h offsite_age_h; do
    age=$(num "$body" "$f")
    if [ -z "$age" ]; then
      "$ISSUE" "woo_${f%_age_h}_stale" warning \
        "Липсва поле $f в /peer-health на магазина — не може да се провери давността.$caveat" || true
      continue
    fi
    if [ "${age%.*}" -gt "$MAX_AGE_H" ] 2>/dev/null; then
      case "$f" in
        restore_test_age_h) what="Тестовото възстановяване на базата";;
        backup_age_h)       what="Резервното копие";;
        *)                  what="Извънното копие (R2)";;
      esac
      "$ISSUE" "woo_${f%_age_h}_stale" error \
        "$what на магазина е отпреди ${age%.*} часа (праг ${MAX_AGE_H}ч) — вероятно кронът не се е изпълнил.$caveat" \
        "{\"field\":\"$f\",\"age_h\":$age,\"threshold_h\":$MAX_AGE_H}" || true
    else
      "$ISSUE" --resolve "woo_${f%_age_h}_stale" || true
    fi
  done
}

if [ "${1:-}" = "--check-body" ]; then     # self-test: run the assertions against a supplied body
  check_ages "${2:?json}"; exit 0
fi

fails=$(cat "$STATE" 2>/dev/null || echo 0)
# NB `|| code=000`, NOT `|| echo 000` inside the substitution: on a DNS/TLS failure curl BOTH prints
# "000" and exits non-zero, so the echo form concatenated them into "000000" — matching neither the
# "000" test nor the 5xx test, and the check reported OK for a host that does not exist.
body=$(curl -s --max-time 15 -w '\n%{http_code}' "$URL" 2>/dev/null) || body=$'\n000'
code=$(printf '%s' "$body" | tail -1)
body=$(printf '%s' "$body" | sed '$d')

healthy=0
case "$code" in
  ''|000|5*) healthy=0 ;;
  2*)        printf '%s' "$body" | grep -qE '"ok"[[:space:]]*:[[:space:]]*true' && healthy=1 ;;
  *)         healthy=0 ;;
esac

if [ "$healthy" = "0" ]; then
  fails=$((fails + 1)); echo "$fails" > "$STATE"
  echo "$(ts) FAIL (http=$code ok=no) consecutive=$fails" >> "$LOG"
  if [ "$fails" -eq "$THRESHOLD" ]; then
    "$ISSUE" woo_box_down error \
      "Магазинът (staging.darlinghome.bg) не отговаря коректно вече $((THRESHOLD * 5)) минути (код $code). Провери втория сървър." \
      "{\"url\":\"$URL\",\"code\":\"$code\",\"consecutive\":$fails}" || true
  fi
else
  if [ "$fails" -ge "$THRESHOLD" ]; then
    echo "$(ts) RECOVERED (http=$code) after $fails failures" >> "$LOG"
    "$ISSUE" --resolve woo_box_down || true
  fi
  echo 0 > "$STATE"
  echo "$(ts) OK (http=$code)" >> "$LOG"
  check_ages "$body"
fi
tail -n 4000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
