# CLAUDE — Rapid Session Startup & Quick Reference

## Quick Session Startup
1. Inspect `SOUL.md` & `USER.md`.
2. Inspect `memory/00_CORE.md` (permanent architectural constraints).
3. Inspect `memory/01_PROJECT.md` (current step, active tasks, blockers).
4. Review `AGENTS.md` for engineering protocols and red lines.

---

## Key Development Commands

### Environment
```bash
# Activate virtual environment (Windows PowerShell)
.venv\Scripts\Activate.ps1
```

### Database & Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### Running Tests
```bash
python manage.py test apps.core
# or with pytest
pytest
```

### CI (runs locally, not on GitHub Actions)
```bash
scripts/ci-local.sh --publish   # all gates, then posts the `ci/local` commit status for the pushed HEAD
scripts/ci-local.sh checks      # or run single gates: checks | sqlite | mysql | mobile
```

### Background Tasks & Cron Verification
```bash
# Run task queue drainer
python manage.py drain_tasks --limit 200

# Run a specific scheduled job
python manage.py <job_command>
```

---

## Core Rules Reminder
- **Single monolith, MySQL 8 only, cron background execution.**
- **No Redis / Celery / brokers.**
- **3-layer tenancy (`TenantModel`, `TenantManager`, cross-tenant 404 test).**
- **Money: `core.fields.MoneyField` (`DECIMAL(18,2)`), double-entry ledger, round-half-up.**
- **`id-ID` first.**
- **PR lifecycle: merge automatically once local CI (`ci/local` status) passes (`--admin` only if the required review is the sole blocker); deploy only on explicit instruction (`AGENTS.md` §4 stage 5).**
