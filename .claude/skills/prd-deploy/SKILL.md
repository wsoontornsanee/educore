---
name: PRD Deploy
description: Deploy EduCore's main branch to the production server (educore.makan.live), isolated alongside another app on a shared, memory-constrained droplet. Use when the user asks to deploy, ship, push to production/PRD, or sync a merged PR to the live server.
---

# PRD Deploy

Deploys EduCore's `main` branch to the shared PRD droplet. The app lives in
its own isolated directory there — **never** touch the sibling app
(`exchange-python-service-prd`) or its files/crontab lines.

## Prerequisites (verify before starting)

1. **SSH key.** Not in this repo. As of this skill's writing it lives at
   `/Users/natsoon/Documents/GIT/exchange-python-service/skills/prd-server/id_rsa`
   on the operator's machine (a sibling project's deploy key, reused here).
   If that path doesn't exist in your environment, **stop and ask the user**
   for the correct key path or for a deploy key to be issued — do not guess
   or search broadly for private keys.
2. **Server:** `root@188.166.212.206` (DigitalOcean droplet, hostname
   `01-novawallet-api`, Ubuntu 24.04, 1 vCPU / 1.9GB RAM — tight, shared with
   `exchange-python-service-prd`. Check `free -h` if anything seems slow).
3. **App location on server:** `/root/myproject/educore-prd/`
   - `app/` — git checkout (`git@github.com:wsoontornsanee/educore.git`, deploy key at `app/../deploy_key`)
   - `venv/` — Python 3.12 virtualenv
   - `run/` — gunicorn socket + pidfile
   - `logs/` — `cron.log`, `gunicorn-access.log`, `gunicorn-error.log`
   - `run_manage.sh` — cron wrapper (sets `DJANGO_SETTINGS_MODULE`, `EDUCORE_CRON_HOST=1`, `EDUCORE_BACKUP_DIR`)
   - `backups/` — encrypted DB backups (`backup_database` cron output)
   - `.env` — production secrets (DB creds, `DJANGO_SECRET_KEY`, GCS creds, Fernet keys — see below)
4. **DB:** managed DigitalOcean MySQL 8, credentials already in `.env` on the server. Don't touch schema outside `migrate`.
5. **URL:** `https://educore.makan.live` — nginx vhost + certbot cert already provisioned.

## Deploy steps

Run every server command over SSH with the key from step 1:
```bash
KEY=<path-to-deploy-key>
ssh -i "$KEY" root@188.166.212.206 "<command>"
```

### 1. Check the server checkout is clean
```bash
ssh -i "$KEY" root@188.166.212.206 "cd /root/myproject/educore-prd/app && git status --short"
```
Expect only `frontend/collected_static/`, `gunicorn_conf.py`, `gunicorn_hooks.py` as untracked (deploy artifacts, not in git). Anything else — stop and investigate before pulling; don't silently overwrite unknown state.

### 2. Fast-forward to latest main
```bash
ssh -i "$KEY" root@188.166.212.206 "cd /root/myproject/educore-prd/app && GIT_SSH_COMMAND='ssh -i /root/myproject/educore-prd/deploy_key -o IdentitiesOnly=yes' git fetch origin main 2>&1 | tail -3 && git merge --ff-only origin/main && git log --oneline -1"
```
Note the file list in the merge output — check it for:
- **New/changed `*/crypto.py` files** referencing a new `EDUCORE_*_FERNET_KEY` setting → see "New Fernet keys" below.
- **New migrations** → step 4 handles them, but note if any touch a table you'd want to sanity-check after.
- **New dependency** in `requirements.txt` → `pip install -r requirements.txt` may be needed (check `git diff` on that file specifically; the venv doesn't auto-update). A `weasyprint` bump or a fresh host also needs the smoke check in "System packages" below.
- **Changes to `deploy/crontab`** → see "Syncing cron" below; this file is NOT auto-applied by pulling, the live crontab is separate.

### 2a. New Fernet keys (if a `*/crypto.py` diff references one)
Each of these apps has its own `EDUCORE_<DOMAIN>_FERNET_KEY` env var (biometric, counselling, clinic, partner, calendar — pattern keeps expanding). If a new one appears and isn't already in `.env`:
```bash
ssh -i "$KEY" root@188.166.212.206 "/root/myproject/educore-prd/venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```
Then append `EDUCORE_<NAME>_FERNET_KEY=<generated>` to `.env` on the server. **Check whether the relevant table already has encrypted rows before setting/rotating a key** — rotating an in-use key makes existing ciphertext permanently unreadable. If rows exist, this needs a real key-rotation plan, not a quiet swap; stop and ask.

Since PR #154, an explicitly-set-but-invalid Fernet key now raises `RuntimeError` at first use (fail-hard) instead of silently falling back — so a key you set must be genuine `Fernet.generate_key()` output, not an arbitrary passphrase.

### 3. Deploy checks
```bash
ssh -i "$KEY" root@188.166.212.206 "cd /root/myproject/educore-prd/app && DJANGO_SETTINGS_MODULE=educore.settings.production /root/myproject/educore-prd/venv/bin/python manage.py check --database default"
```
Use `--database default`, not bare `check` — Django only runs DB-dependent checks (like MySQL's `W036` partial-unique-constraint warning) when a database is explicitly named.

### 4. Migrate + collectstatic
```bash
ssh -i "$KEY" root@188.166.212.206 "cd /root/myproject/educore-prd/app && DJANGO_SETTINGS_MODULE=educore.settings.production /root/myproject/educore-prd/venv/bin/python manage.py migrate --noinput"
ssh -i "$KEY" root@188.166.212.206 "cd /root/myproject/educore-prd/app && DJANGO_SETTINGS_MODULE=educore.settings.production /root/myproject/educore-prd/venv/bin/python manage.py collectstatic --noinput"
```
`DJANGO_SETTINGS_MODULE` must be passed explicitly on every `manage.py` call — `.env`'s copy of it is inert (see "Gotchas" below).

### 5. Restart and smoke test
```bash
ssh -i "$KEY" root@188.166.212.206 "systemctl restart gunicorn-educore"
sleep 3
curl -s -o /dev/null -w 'HTTPS %{http_code}\n' https://educore.makan.live/admin/login/
```
Expect `200`. A `502`/`000` immediately after restart is usually just the worker still booting — wait 2-3s and retry once before treating it as a real failure.

### 6. Verify the actual feature, not just that the server is up
A 200 on `/admin/login/` proves gunicorn is alive, nothing about what you just shipped. For anything behind a new code path (a new endpoint, a cron command, a config-driven behavior change), exercise it for real against PRD — call the endpoint, run the management command directly, check `journalctl -u gunicorn-educore` or `logs/cron.log` for the actual output. This session caught two real bugs this way that `check`/`migrate`/a green smoke test all missed:
- a missing `django_cache` table (DRF throttling raised 500 until `manage.py createcachetable` was run — nothing had used the cache before)
- rate limiting silently not working behind Cloudflare (nginx's `X-Forwarded-For` shifts per request; fixed by keying on `CF-Connecting-IP` instead)

## System packages (native libraries the venv cannot provide)

`pip install -r requirements.txt` does not install `weasyprint`'s native libraries. Without them
the import fails and every PDF path **silently falls back to printable HTML** (QR decal sheets,
payment receipts, merchant settlement statements, report cards, foundation dashboard export;
each logs `weasyprint unavailable, falling back to HTML ...` at WARNING). The droplet is Ubuntu 24.04:

```bash
apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
```

(`weasyprint>=62` renders through Pango/HarfBuzz and Pillow; it no longer needs cairo or gdk-pixbuf.)

Smoke check on a new or rebuilt host, and after any `weasyprint` bump in `requirements.txt`:

```bash
ssh -i "$KEY" root@188.166.212.206 "/root/myproject/educore-prd/venv/bin/python -c \"import weasyprint; print(weasyprint.__version__, weasyprint.HTML(string='x').write_pdf()[:4])\""
```

Expect a version and `b'%PDF'`. An `OSError`/`ImportError` about `libpango`/`libgobject` means the packages above are missing.
To find out whether production has been silently degraded, grep for the fallback:
`journalctl -u gunicorn-educore | grep "weasyprint unavailable"`.

## Provisioning a rebuilt host

Everything outside the repo that a fresh droplet needs, beyond `requirements.txt`: the apt packages under "System packages" (weasyprint native libraries), the `.env` and deploy key described under Prerequisites, the nginx vhost/certbot cert, the `gunicorn-educore` systemd unit and the crontab (see "Syncing cron"). There is no Dockerfile or provisioning script; this skill is the record.

## Syncing cron (only when `deploy/crontab` changed)

The live root crontab is **not** derived from `deploy/crontab` automatically — it was hand-translated once and must be re-synced manually when the source file changes.

1. Back up first: `crontab -l > /root/myproject/educore-prd/crontab_backup_$(date +%Y%m%d%H%M%S).txt`
2. Rebuild the EduCore block from `deploy/crontab`, translating `python /app/manage.py` → `/root/myproject/educore-prd/run_manage.sh`, and preserving each line's `sleep N;` stagger offset (every job in `deploy/crontab` already carries a unique 3-second-spaced offset — copy it verbatim, don't regenerate). This matters because several jobs share a trigger minute (e.g. everything hourly fires together at `:00`); the offsets keep them from all starting in the same second on a 1-vCPU box.
3. Splice: keep everything above the `## EduCore PRD` marker in the current live crontab untouched (that's the other app's jobs), replace everything from that marker down with the rebuilt block.
4. Install with `crontab <file>`, then verify: `crontab -l | grep -c run_manage.sh` should match the line count in `deploy/crontab`, and `crontab -l | grep -c '<a distinctive string from the other app>'` should be unchanged from before.
5. Wait for one real cron minute to pass, then check `logs/cron.log` for errors.

## Gotchas (things that bit us this session)

- **`.env`'s `DJANGO_SETTINGS_MODULE` line does nothing.** `manage.py` calls `os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'educore.settings.local')` before `.env` is ever loaded (which happens inside `settings/base.py`, i.e. after the settings module is already chosen). Always pass it explicitly as a real env var on every manage.py invocation and in the gunicorn/cron wrapper — never rely on `.env` alone.
- **`DatabaseCache` needs `createcachetable`, `migrate` doesn't create it.** If anything starts using Django's cache framework (throttling, `CACHES['default']`) for the first time, run `manage.py createcachetable` once or every request 500s with `Table 'educore.django_cache' doesn't exist`.
- **Behind Cloudflare + nginx, trust `CF-Connecting-IP`, not raw `X-Forwarded-For`.** nginx's `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for` appends its own upstream peer (Cloudflare's anycast edge, which varies per request) as the last hop, so anything keying on the whole XFF string (DRF's default throttle `get_ident()` included) gets a different identity almost every request.
- **Never `git reset --hard` while you have uncommitted local edits you want to keep**, even on your own laptop's clone — `git checkout main && git reset --hard origin/main` silently discards them. Commit to a feature branch (or at minimum `git stash`) before touching `main`.
- **The droplet is memory-tight (1.9GB, shared).** Don't add a second WSGI worker, don't add more per-minute cron jobs than necessary, and glance at `free -h` after a deploy that adds anything long-running.
- **A stale `.git/index.lock`** can appear if a previous command was interrupted. Before removing it, confirm no real `git` process is running (`ps aux | grep git`) — don't blindly `rm` it if something might genuinely be mid-operation.

## Standing SOP reminder

Per `AGENTS.md`: never deploy or merge without explicit user instruction. This skill assumes the user has already said "deploy" / "merge and deploy" / equivalent for this specific change — it is not blanket standing authorization to deploy anything, anytime. After deploying, update the relevant Notion task (Astra Educore DB) to `Done` with the PR link and what was verified.
