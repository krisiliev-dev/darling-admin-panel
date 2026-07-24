# Дарлинг 7 Admin panel — production deployment runbook

> **⚠️ RUNTIME IS NOW DOCKER.** The app runs as a `docker compose` stack
> (`admin-panel/docker-compose.yml`: a `panel` backend container + a `caddy` reverse-proxy with
> automatic HTTPS). The **systemd + bare-metal venv + nginx + certbot** sections below (§3a venv,
> §5 systemd, §6 nginx, §7 certbot) are **superseded** by that stack. Everything else here still
> applies: §1 provision, §2 OS hardening + firewall, §4 the secret **contents** of `~/.hermes/.env`,
> §8 backups, §9 nekorekten IP-lock, §10 go-live. Docker how-to lives alongside the compose file.

Target: **one Ubuntu 24.04 LTS VPS** hosting the FastAPI backend, the built React/Vite
frontend (static, behind Caddy), and the Barsy⇄Shopify webhook receivers.

Provider is host-agnostic. Either works (both EU, GDPR-fine, DE/FI datacentres):

| Provider | Plan | Price | Specs |
|----------|------|-------|-------|
| Hetzner | CX33 | ~€8–9/mo | 4 vCPU / 8 GB / 80 GB, Falkenstein/Nuremberg (DE) or Helsinki (FI) |
| Contabo | Cloud VPS 10 | €4.50/mo | 3 vCPU / 8 GB / 75 GB NVMe, DE |

Currently **pre-launch (0 orders)**. `WEBHOOKS_LIVE` stays `0` until the checklist in
[`WEBHOOKS_LAUNCH.md`](./WEBHOOKS_LAUNCH.md) is complete — this runbook stands up the host;
that doc flips the store live.

Conventions used below (substitute your real values):

- `<DOMAIN>` — the panel hostname, e.g. `panel.darling7.com`
- `<STATIC_IP>` — the VPS's dedicated static IPv4
- `<ADMIN_IP>` — the IP **you** administer from (for SSH allow-listing)
- deploy user: `darling`  ·  install root: `/opt/darling`

> **Deployment footprint — read first.** The panel does **not** live in `admin-panel/`
> alone. `admin-panel/darling_core/__init__.py` resolves the real business code at
> `../../agent-pipeline`, and `admin-panel/backend/app/core.py` imports `darling_core`,
> `shopify_orders`, and `barsy_shopify_connector` from there. **You must deploy the whole
> `product-review/` parent tree with `admin-panel/` and `agent-pipeline/` kept as
> siblings**, or `import darling_core` fails at startup. The panel also *reads*
> `agent-pipeline/pipeline.db` (the enriched catalog / system-of-record snapshot) on the
> `/api/health` probe.

---

## 1. Provision the VPS

- Create an **Ubuntu 24.04 LTS** instance, 2–4 vCPU / 8 GB RAM / 80 GB disk.
- **Assign a dedicated static IPv4** and record it as `<STATIC_IP>`. This is **mandatory**,
  not cosmetic:
  1. **nekorekten API key is IP-allowlisted.** At launch the key is locked to this host's
     egress IP (see §9 and `WEBHOOKS_LAUNCH.md` step 2a). A dynamic/changing IP breaks the
     risky-customer lookup and forces re-allowlisting.
  2. **Shopify and Barsy need a stable webhook URL.** The receivers are registered against
     `https://<DOMAIN>/webhooks/...`; `<DOMAIN>` must resolve to a fixed IP so deliveries
     don't blackhole after a reboot/re-image.
- Point DNS: create an **A record** `<DOMAIN> → <STATIC_IP>` before requesting TLS (§7).

---

## 2. OS preparation

SSH in as root once, then create a non-root sudo user and harden.

```bash
# --- as root ---
adduser darling
usermod -aG sudo darling
rsync --archive --chown=darling:darling ~/.ssh /home/darling   # copy your SSH key over
```

```bash
# --- reconnect as darling, then: ---
sudo timedatectl set-timezone Europe/Sofia
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y python3-venv python3-dev build-essential nginx git curl ufw \
  unattended-upgrades

# Node 20 LTS (for the frontend build)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# unattended security upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # answer "Yes"
```

Firewall — **8770 is never publicly exposed**; only nginx reaches it on localhost:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <ADMIN_IP> to any port 22 proto tcp   # SSH only from you
sudo ufw allow 443/tcp                                     # HTTPS
sudo ufw allow 80/tcp                                      # HTTP (certbot + redirect)
sudo ufw enable
sudo ufw status verbose
```

> Do **not** open 8770. The uvicorn service binds to `127.0.0.1` (§5); nginx is the only
> thing that talks to it. Exposing 8770 would let clients hit the app directly and forge
> `X-Forwarded-For` to defeat the auth rate-limiter.

---

## 3. App deploy

Copy the **whole `product-review/` tree** to the host, preserving the sibling layout. Use
`git clone` if it's in a repo, otherwise `rsync` from the workstation.

```bash
sudo mkdir -p /opt/darling && sudo chown darling:darling /opt/darling
# Option A — git:
#   git clone <repo> /opt/darling/product-review
# Option B — rsync from your machine (run locally), excluding junk & secrets:
#   rsync -av --exclude '__pycache__' --exclude 'node_modules' --exclude '.env' \
#     --exclude 'frontend/dist' ./product-review/ darling@<STATIC_IP>:/opt/darling/product-review/
```

Confirm the two siblings exist:

```bash
ls -d /opt/darling/product-review/admin-panel /opt/darling/product-review/agent-pipeline
```

### 3a. Python venv + backend deps

There is **no `requirements.txt` in the repo** — create one (pin versions after first
`pip freeze`). The backend needs only these (the `agent-pipeline` core modules use the Python
stdlib — `urllib`, `sqlite3` — so they add nothing):

```bash
cat > /opt/darling/product-review/admin-panel/backend/requirements.txt <<'EOF'
fastapi
uvicorn[standard]
fastapi-users[sqlalchemy]
sqlalchemy
aiosqlite
pydantic
pydantic-settings
python-multipart
EOF
```

`python-multipart` is required — the login endpoint (`/api/auth/login`) and CSV/file
uploads post form data. `fastapi-users[sqlalchemy]` pulls in the async SQLAlchemy user store
and password hashing.

```bash
cd /opt/darling/product-review/admin-panel/backend
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
# once it runs, freeze exact versions so deploys are reproducible:
./.venv/bin/pip freeze > requirements.lock.txt
```

**Confirm `darling_core` imports** before wiring systemd (this proves the sibling path +
`pipeline.db` resolve):

```bash
cd /opt/darling/product-review/admin-panel/backend
./.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from app.core import health; print(health())"
# expect a dict with core_root=.../agent-pipeline, db=ok, db_products=<n>, couriers=[...]
```

### 3b. Frontend build

The frontend is built once to static files and served by nginx (no Node process runs in
prod). `frontend/src/lib/api.ts` calls the API at **same-origin `/api`** — no build-time API
URL to set; nginx routes `/api` to the backend (§6).

```bash
cd /opt/darling/product-review/admin-panel
npm --prefix frontend ci
npm --prefix frontend run build     # tsc -b && vite build  ->  frontend/dist/
ls frontend/dist/index.html         # sanity check
```

---

## 4. Secrets / environment

Secrets live in **`~/.hermes/.env`** (i.e. `/home/darling/.hermes/.env`) — the shared secret
file that both the panel (`backend/app/config.py` reads it by absolute path) **and** the
`agent-pipeline` modules (`barsy_api.py`, `shopify_api.py`, `courier.py` read
`~/.hermes/.env` directly) load. Using one file keeps both halves in agreement. **Never
commit it.** A local `admin-panel/backend/.env` may override individual keys if needed
(later file wins in pydantic).

```bash
mkdir -p /home/darling/.hermes
umask 077
cat > /home/darling/.hermes/.env <<'EOF'
# ── Auth ────────────────────────────────────────────────────────────────
ADMIN_JWT_SECRET=<openssl rand -hex 32>     # stable JWT secret; blank = sessions die on restart
# COOKIE_SECURE unset -> defaults True (HTTPS-only session cookie). Do NOT set 0 in prod.

# ── Reverse-proxy trust (rate limiter) ──────────────────────────────────
TRUST_PROXY=1                               # nginx terminates TLS and appends the real client IP

# ── CORS: the real panel origin only ────────────────────────────────────
CORS_ORIGINS=https://<DOMAIN>

# ── Barsy⇄Shopify webhook receivers ─────────────────────────────────────
SHOPIFY_WEBHOOK_SECRET=<shopify app webhook signing secret>
BARSY_WEBHOOK_TOKEN=<openssl rand -hex 32>  # embedded in the registered Barsy URL ?token=
WEBHOOKS_LIVE=0                             # STAYS 0 until WEBHOOKS_LAUNCH.md is done
ORDER_INGEST_MODE=clientorder              # 'clientorder' = staff review first (safer)

# ── nekorekten risky-customer lookup ────────────────────────────────────
NEKOREKTEN_API_KEY=<key>                    # see §9 — IP-lock + rotate at launch

# ── Barsy ERP (system of record) ────────────────────────────────────────
BARSY_HOST=<host>
BARSY_USER=<user>
BARSY_PASS=<pass>

# ── Shopify Admin API ───────────────────────────────────────────────────
SHOPIFY_STORE=<shop>.myshopify.com
SHOPIFY_CLIENT_ID=<id>
SHOPIFY_CLIENT_SECRET=<secret>
SHOPIFY_API_VERSION=<version>

# ── Couriers (set the ones you use) ─────────────────────────────────────
SPEEDY_USER=<user>
SPEEDY_PASS=<pass>
ECONT_USER=<user>
ECONT_PASS=<pass>
ECONT_SENDER_PHONE=<phone>                  # Econt needs a sender profile — see WEBHOOKS_LAUNCH.md
EOF
chmod 600 /home/darling/.hermes/.env
```

Env-var names above are the real ones from `backend/app/config.py` and the `agent-pipeline`
loaders — do not rename them. `webhooks_live` false means every verified webhook logs a
dry-run **plan** and writes nothing (the master safety gate).

---

## 5. systemd service (uvicorn)

Create `/etc/systemd/system/darling-panel.service`. **`--no-proxy-headers` is critical.**

```ini
[Unit]
Description=Darling 7 admin panel (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=darling
Group=darling
Environment=HOME=/home/darling
WorkingDirectory=/opt/darling/product-review/admin-panel/backend
EnvironmentFile=/home/darling/.hermes/.env
ExecStart=/opt/darling/product-review/admin-panel/backend/.venv/bin/uvicorn \
    app.main:app \
    --host 127.0.0.1 --port 8770 \
    --no-proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Why `--no-proxy-headers` (do **not** remove it): uvicorn's own `ProxyHeadersMiddleware`
would rewrite `request.client` from `X-Forwarded-For` for localhost peers **before the app
sees it**, silently defeating the panel's proxy-trust gate. With `--no-proxy-headers`,
uvicorn never touches `request.client`, and the app's `TRUST_PROXY` flag (set in §4) is the
single control over whether XFF is trusted. nginx is the **only** thing allowed to set the
real client IP (§6) — that's what makes the auth rate-limiter unspoofable. Never use
`--reload` here (see §11 gotcha). Bind to `127.0.0.1` only.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now darling-panel
sudo systemctl status darling-panel --no-pager
curl -s http://127.0.0.1:8770/api/health   # local probe should return the health JSON
```

---

## 6. nginx reverse proxy

Create `/etc/nginx/sites-available/darling`:

```nginx
server {
    listen 80;
    server_name <DOMAIN>;
    # certbot inserts the HTTP->HTTPS redirect + cert lines here in §7.

    root /opt/darling/product-review/admin-panel/frontend/dist;
    index index.html;

    # API -> backend
    location /api/ {
        proxy_pass http://127.0.0.1:8770;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;  # REAL client IP, last hop
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Webhook receivers -> backend (Shopify HMAC / Barsy token)
    location /webhooks/ {
        proxy_pass http://127.0.0.1:8770;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Deliver the raw body UNALTERED — Shopify HMAC-SHA256 is computed over the exact bytes.
        proxy_request_buffering on;
        proxy_set_header Content-Type $content_type;
    }

    # SPA: serve static assets, fall back to index.html for client-side routes
    location / {
        try_files $uri /index.html;
    }
}
```

`$proxy_add_x_forwarded_for` appends the true `$remote_addr` as the last XFF hop, which is
exactly what the app's `TRUST_PROXY=1` gate expects — so the rate-limiter keys on the real
client IP. Do **not** add rewrites, sub_filter, or body transforms on `/webhooks/`; the HMAC
check needs the byte-for-byte original payload.

```bash
sudo ln -s /etc/nginx/sites-available/darling /etc/nginx/sites-enabled/darling
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

---

## 7. TLS (Let's Encrypt)

DNS `<DOMAIN> → <STATIC_IP>` must already resolve.

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <DOMAIN> --redirect --agree-tos -m anthropic@vkriss.com --no-eff-email
# certbot edits the server block to listen 443 with the cert and adds the 80->443 redirect.
systemctl list-timers | grep certbot     # auto-renew timer is installed by the package
sudo certbot renew --dry-run             # verify renewal works
```

After this, `COOKIE_SECURE` (default true) is satisfied — session cookies flow only over
HTTPS.

---

## 8. Backups

Barsy is the operational system-of-record, **but panel-local data exists ONLY on this host**:
the audit log, staff accounts, COD settlements, pricing rules, and order outcomes live in
`admin-panel/backend/admin.db`; the enriched catalog snapshot the panel reads lives in
`agent-pipeline/pipeline.db`. Both are SQLite. Back them up daily to somewhere off-box.

```bash
sudo tee /usr/local/bin/darling-backup.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%F)
OUT=/opt/darling/backups/$STAMP
mkdir -p "$OUT"
# .backup is safe on a live SQLite file (consistent snapshot, no locking issues)
sqlite3 /opt/darling/product-review/admin-panel/backend/admin.db ".backup '$OUT/admin.db'"
sqlite3 /opt/darling/product-review/agent-pipeline/pipeline.db   ".backup '$OUT/pipeline.db'"
tar -czf "$OUT.tar.gz" -C /opt/darling/backups "$STAMP" && rm -rf "$OUT"
find /opt/darling/backups -name '*.tar.gz' -mtime +30 -delete   # keep 30 days
# TODO: ship "$OUT.tar.gz" offsite — rclone to object storage, scp, or restic.
EOF
sudo chmod +x /usr/local/bin/darling-backup.sh
sudo apt-get install -y sqlite3
# daily at 03:15
echo '15 3 * * * darling /usr/local/bin/darling-backup.sh' | sudo tee /etc/cron.d/darling-backup
```

Also enable the **provider's snapshot feature** (Hetzner/Contabo both offer it) as a
whole-disk fallback. If write-concurrency ever becomes a bottleneck (multiple staff settling
COD simultaneously), migrate `admin.db` to **Postgres** — SQLite is fine at current volume
but serialises writes.

---

## 9. nekorekten IP-lock + key rotation (at launch)

Cross-ref [`WEBHOOKS_LAUNCH.md`](./WEBHOOKS_LAUNCH.md) **step 2a**. Before go-live:

1. In the nekorekten dashboard, change the API-key IP allowlist from `0.0.0.0/0` (dev/open)
   to **`<STATIC_IP>/32`** — this host's static egress IP.
2. **Rotate the key** (the dev key was exposed in a chat transcript). Put the new value in
   `NEKOREKTEN_API_KEY` (§4) and `sudo systemctl restart darling-panel`.

A leaked key locked to one IP is useless elsewhere; an open key is not — do both.

---

## 10. Go-live

Webhook registration and flipping the store live are **not** duplicated here — follow
[`WEBHOOKS_LAUNCH.md`](./WEBHOOKS_LAUNCH.md) in order (Phase 2 real-delivery smoke test, then
launch steps 1–8: secrets, nekorekten lock, fiscal POS, stock load, webhook registration,
loop guard, nightly reconcile, then `WEBHOOKS_LIVE=1`). This host **is** the "always-on
public-HTTPS host" that doc's step 1 refers to — no ngrok needed.

Host smoke test (run after §7, before touching webhooks):

```bash
curl -s https://<DOMAIN>/api/health            # -> {"db":"ok", "db_products":..., "couriers":[...]}
```

- [ ] `https://<DOMAIN>/` loads the SPA (login screen).
- [ ] First-run owner setup / login succeeds; session cookie is `Secure`.
- [ ] A data page renders (e.g. dashboard / catalog) — proves `pipeline.db` reads work.
- [ ] `curl https://<DOMAIN>/webhooks/shopify/orders` with a bad signature returns **401**
      (receiver is reachable and verifying) — then proceed to `WEBHOOKS_LAUNCH.md` Phase 2.
- [ ] Confirm `WEBHOOKS_LIVE=0` until every `WEBHOOKS_LAUNCH.md` step is checked off.

---

## 11. Operations

**Logs**

```bash
journalctl -u darling-panel -f            # live backend logs
journalctl -u darling-panel --since today
sudo tail -f /var/log/nginx/error.log     # proxy / TLS issues
```

**Restart / stop**

```bash
sudo systemctl restart darling-panel
sudo systemctl reload nginx               # after editing the nginx block
```

**Deploy an update**

```bash
cd /opt/darling/product-review
git pull                                  # (or rsync the new tree)

# backend deps (only if requirements changed)
cd admin-panel/backend
./.venv/bin/pip install -r requirements.txt

# frontend rebuild
cd /opt/darling/product-review/admin-panel
npm --prefix frontend ci
npm --prefix frontend run build

# restart the API; nginx picks up the new frontend/dist immediately
sudo systemctl restart darling-panel
curl -s https://<DOMAIN>/api/health
```

**`--reload` orphan gotcha — do NOT use `--reload` in production.** uvicorn's reloader spawns
a child worker; on restart/crash the child can be **orphaned**, leaving a stale process still
bound to `8770` so the new one can't start (or old code keeps serving). The systemd unit (§5)
runs a single process **without** `--reload` precisely to avoid this. If you ever see
"address already in use" on 8770:

```bash
sudo systemctl stop darling-panel
sudo ss -ltnp 'sport = :8770'             # find any lingering pid
sudo pkill -f 'uvicorn app.main:app' || true
sudo systemctl start darling-panel
```
