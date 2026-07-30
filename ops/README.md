# Server-side ops scripts (panel box)

These live on the panel VPS at `/home/darling/` and run from cron. They are **copied here so they are
reviewable and version-controlled** — the box is the thing that runs them, this directory is the record.
After editing here, copy to the box (`scp ops/<f> darling@<host>:/home/darling/<f>`) — there is no
automatic deployment for these, deliberately: a broken monitor should not be one `git push` away.

| script | cron | what it does |
|---|---|---|
| `backup-darling.sh` | `15 3 * * *` | admin.db snapshot (30d local) + offsite copy to Cloudflare R2 |
| `peer-check-woo.sh` | `*/5 * * * *` | panel box → Woo box liveness; the Woo box runs the mirror image |
| `ops-issue.sh` | — | shared helper: records/resolves an issue in the panel's Проблеми list |

## Why these write to admin.db directly instead of calling the API
An ops alert must work precisely when the panel is unhealthy. Writing to SQLite needs no auth, no
network and no running container, so a check can still report that the thing it monitors is down.
`ops-issue.sh` dedupes on `(source='ops', kind)` exactly like the panel's own recorder, so a repeated
failure bumps `count` instead of spamming a row every five minutes, and `--resolve` auto-closes on
recovery.

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
