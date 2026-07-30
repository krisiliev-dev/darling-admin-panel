#!/usr/bin/env bash
# Panel box → Woo box liveness check (the mirror of peer-check.sh on the Woo side).
#
# Each box watches the other, so a box that dies cannot be the one reporting it. This covers the LIKELY
# failure (one box down) but NOT the correlated one — a Contabo account or DC problem takes both, and
# neither would be alive to complain. That still needs a monitor outside both; owner action.
#
# 401 IS THE HEALTHY ANSWER: staging is behind basic auth, so a 401 proves nginx + TLS are alive and
# answering. We treat "any HTTP response that isn't 5xx" as alive rather than pinning exactly 401 —
# if the owner removes basic auth later, a healthy 200 must not start paging people at 3am.
#
# Alerts on the 3rd CONSECUTIVE failure, not the first: one blip is a network hiccup, three in a row at
# 5-minute spacing is an outage. Announces recovery, and logs on every run either way.
set -uo pipefail
URL="https://staging.darlinghome.bg/"
STATE=/home/darling/.peer-woo-failures
LOG=/home/darling/peer-check-woo.log
ISSUE=/home/darling/ops-issue.sh
THRESHOLD=3

ts() { date -u +%FT%TZ; }
fails=$(cat "$STATE" 2>/dev/null || echo 0)
# NB `|| code=000`, NOT `|| echo 000` inside the substitution: on a DNS/TLS failure curl BOTH prints
# "000" and exits non-zero, so the echo form concatenated them into "000000" — which matched neither the
# "000" test nor the 5xx test, and the check reported OK for a host that does not exist. My own monitor
# had a false-green in it; found by actually breaking it, which is the only way these ever show up.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$URL" 2>/dev/null) || code=000

# no response at all (DNS, refused, timeout, TLS) or the server answered but is broken (5xx)
case "$code" in
  ''|000|5*) unhealthy=1 ;;
  *)         unhealthy=0 ;;
esac
if [ "$unhealthy" = "1" ]; then
  fails=$((fails + 1)); echo "$fails" > "$STATE"
  echo "$(ts) FAIL ($code) consecutive=$fails" >> "$LOG"
  if [ "$fails" -eq "$THRESHOLD" ]; then
    "$ISSUE" woo_box_down error \
      "Магазинът (staging.darlinghome.bg) не отговаря вече $((THRESHOLD * 5)) минути (код $code). Провери втория сървър." \
      "{\"url\":\"$URL\",\"code\":\"$code\",\"consecutive\":$fails}" || true
  fi
else
  if [ "$fails" -ge "$THRESHOLD" ]; then
    echo "$(ts) RECOVERED ($code) after $fails failures" >> "$LOG"
    "$ISSUE" --resolve woo_box_down || true
  fi
  echo 0 > "$STATE"
  echo "$(ts) OK ($code)" >> "$LOG"
fi
# keep the log from growing without bound (~2 weeks at 5-minute spacing)
tail -n 4000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
