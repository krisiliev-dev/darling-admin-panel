# Server-side ops scripts (panel box)

These live on the panel VPS at `/home/darling/` and run from cron. They are **copied here so they are
reviewable and version-controlled** — the box is the thing that runs them, this directory is the record.
After editing here, copy to the box (`scp ops/<f> darling@<host>:/home/darling/<f>`) — there is no
automatic deployment for these, deliberately: a broken monitor should not be one `git push` away.

| script | cron | what it does |
|---|---|---|
| `backup-darling.sh` | `15 3 * * *` | admin.db snapshot (30d local) + offsite copy to Cloudflare R2 |
| `peer-check-woo.sh` | `*/5 * * * *` | panel box → Woo box health via `/peer-health`; the Woo box runs the mirror image |
| `ops-issue.sh` | — | shared helper: records/resolves an issue in the panel's Проблеми list |

## Why these write to admin.db directly instead of calling the API
An ops alert must work precisely when the panel is unhealthy. Writing to SQLite needs no auth, no
network and no running container, so a check can still report that the thing it monitors is down.
`ops-issue.sh` dedupes on `(source='ops', kind)` exactly like the panel's own recorder, so a repeated
failure bumps `count` instead of spamming a row every five minutes, and `--resolve` auto-closes on
recovery.

## Assert the BODY, never just the status
`peer-check-woo.sh` requires HTTP 200 **and** `"ok":true` in the body. A status code proves the web
server answered; it says nothing about what is behind it. nginx will happily serve a week-old health
file with a cheerful 200, and an SPA returns 200 for paths that do not exist — which is how the Woo
side's first version of this check passed against a bogus URL. It also asserts freshness per field
(`restore_test_age_h`, `backup_age_h`, `offsite_age_h`, all >36h = a daily cron was missed) and carries
`proves_mechanism_only` into the alert text, so a restore test that currently compares an empty DB to an
empty DB is never later read as months of verified restores.

Staleness alerts fire immediately rather than after 3 strikes: a stale backup is stale on every run, so
there is no flapping to damp and waiting 15 minutes to say so buys nothing.

Self-testable without touching the other box: `peer-check-woo.sh --check-body '<json>'` runs only the
freshness assertions against a supplied body.

## Two failure-detection rules these encode
1. **Loud, never silent.** The R2 upload used to end in `2>/dev/null || true`: a failure produced no
   stderr and no non-zero exit, so the script reported success while off-site backups quietly stopped.
   You would have found out when you needed a restore.
2. **Alert on the 3rd consecutive failure, not the 1st.** One blip is a network hiccup; three in a row
   at five-minute spacing is an outage. Recovery is announced, and every run is logged either way.

## Known limit, stated plainly
Cross-box checks cover the LIKELY failure (one box down). They cannot cover the correlated one — a
Contabo account or datacentre problem takes both, and neither would be alive to complain. That needs a
monitor outside both boxes; owner action.

## Testing them
Both were verified by actually breaking them, which is the only way these bugs surface. Doing that
found a false-green in the peer check itself: on a DNS failure `curl` both prints `000` and exits
non-zero, so `$(curl … || echo 000)` concatenated into `000000`, matching neither the `000` nor the
`5xx` test — the check reported OK for a host that does not exist. Fixed with `|| code=000`.

**Both agents hit that identical bug independently, in separate codebases, on the same afternoon.** It
is not carelessness: `$(curl -w '%{http_code}' || echo 000)` reads as obviously correct and fails OPEN.
Neither of us found it by reading. A third variant of the same shape turned up the same day —
`str.replace()` silently no-opping when its pattern is absent while the script reported success. The
family resemblance is what matters: **`|| true`, `|| echo`, and an unasserted `replace()` are the same
bug in three languages.** Test the result, never the code.
