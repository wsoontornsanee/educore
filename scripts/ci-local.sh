#!/usr/bin/env bash
# Local CI: the same four gates as .github/workflows/ci.yml, run on this machine so GitHub Actions
# minutes are not spent. With --publish, the result is posted as the `ci/local` commit status on
# the pushed HEAD, so `gh pr checks <N>` and branch protection see it like any other check.
#
#   scripts/ci-local.sh              run every gate
#   scripts/ci-local.sh --publish    run every gate, then post ci/local for HEAD (must be pushed)
#   scripts/ci-local.sh checks mysql run only the named gates (checks, mobile, sqlite, mysql)
#   scripts/ci-local.sh --all        ignore the shortcuts below and run every gate for real
#
# Shortcuts (a gate is reported as skipped/cached, never silently dropped):
#   - a gate whose inputs are untouched relative to origin/main is skipped (docs-only change: no
#     Python gates; Python-only change: no mobile gate);
#   - on a clean tree, a gate that already passed on identical inputs is not re-run (results live
#     in the shared .git dir, so sibling worktrees and retries after a later gate failed benefit);
#   - the first failing gate stops the run, and everything runs at low CPU priority.
#
# MySQL gate: uses EDUCORE_DB_HOST/PORT/USER/PASSWORD (defaults: 127.0.0.1:3306, root, no password);
# Django creates and drops its own per-checkout test database.
set -uo pipefail

cd "$(dirname "$0")/.."

PUBLISH=0
ALL=0
GATES=()
for arg in "$@"; do
  case "$arg" in
    --publish) PUBLISH=1 ;;
    --all) ALL=1 ;;
    checks|sqlite|mysql|mobile) GATES+=("$arg") ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
[ ${#GATES[@]} -eq 0 ] && GATES=(checks mobile sqlite mysql)

if [ "$PUBLISH" = 1 ]; then
  # The status vouches for a commit, so it must describe exactly the code that was tested.
  [ -z "$(git status --porcelain)" ] || { echo "working tree is dirty; commit or stash before --publish" >&2; exit 2; }
  [ ${#GATES[@]} -eq 4 ] || { echo "--publish needs every gate; drop the gate names" >&2; exit 2; }
  SHA=$(git rev-parse HEAD)
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  git fetch -q origin "$BRANCH" 2>/dev/null || true
  [ "$(git rev-parse "origin/$BRANCH" 2>/dev/null)" = "$SHA" ] \
    || { echo "HEAD is not pushed to origin/$BRANCH; push first so the status lands on the PR head" >&2; exit 2; }
fi

renice -n 10 -p $$ >/dev/null 2>&1 || true

if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
export EDUCORE_DB_HOST="${EDUCORE_DB_HOST:-127.0.0.1}"
export EDUCORE_DB_PORT="${EDUCORE_DB_PORT:-3306}"
export EDUCORE_DB_USER="${EDUCORE_DB_USER:-root}"
export EDUCORE_DB_PASSWORD="${EDUCORE_DB_PASSWORD:-}"
export EDUCORE_DB_NAME="${EDUCORE_DB_NAME:-educore}"
# One test database per checkout: sibling worktrees may run this concurrently against one MySQL.
export EDUCORE_TEST_DB_NAME="${EDUCORE_TEST_DB_NAME:-test_educore_ci_$(basename "$PWD" | tr -c 'A-Za-z0-9\n' '_' | cut -c1-40)}"
# weasyprint loads pango/gobject by soname; Homebrew keeps them outside the default search path.
[ -d /opt/homebrew/lib ] && export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"

gate_checks() {
  export EDUCORE_USE_SQLITE=1
  $PY manage.py check || return 1
  # Catches a model change without a migration and duplicate migration numbers.
  $PY manage.py makemigrations --check --dry-run || return 1
  # Never run makemessages (it fuzzy-clobbers translations). Compare catalogs, not bytes:
  # msgfmt output varies by version.
  local tmp lang po
  tmp=$(mktemp -d)
  for lang in en id; do
    po=locale/$lang/LC_MESSAGES/django.po
    msgfmt --check -o "$tmp/$lang.mo" "$po" || return 1
    $PY - "$lang" "$tmp" <<'PY' || return 1
import gettext, sys
lang, tmp = sys.argv[1], sys.argv[2]
def catalog(path):
    with open(path, "rb") as f:
        return gettext.GNUTranslations(f)._catalog
if catalog(f"{tmp}/{lang}.mo") != catalog(f"locale/{lang}/LC_MESSAGES/django.mo"):
    sys.exit(f"locale/{lang}/LC_MESSAGES/django.mo is stale: run compilemessages -l {lang}")
PY
  done
  rm -rf "$tmp"
}

gate_sqlite() {
  export EDUCORE_USE_SQLITE=1
  # The PDF renderers silently fall back to HTML when weasyprint's native libraries are missing.
  $PY -c "import weasyprint; assert weasyprint.HTML(string='<p>x</p>').write_pdf().startswith(b'%PDF-')" || return 1
  $PY -m pytest -q --create-db
}

gate_mysql() {
  unset EDUCORE_USE_SQLITE
  local out
  out=$(mktemp)
  # models.W036 = a conditional unique constraint MySQL silently skips, so SQLite tests pass while
  # production is unprotected. It is only reported with --database. Only this warning is fatal:
  # mysql.W003 on core.StoredFile.key predates CI.
  $PY manage.py check --database default 2>&1 | tee "$out"
  if [ "${PIPESTATUS[0]}" -ne 0 ] || grep -q "models.W036" "$out"; then
    echo "a unique constraint with a condition is not created on MySQL (use a NULL marker, see TASK-071)" >&2
    rm -f "$out"; return 1
  fi
  rm -f "$out"
  $PY -m pytest -q --create-db apps/wallet apps/finance apps/identity
}

gate_mobile() {
  # npm ci wipes node_modules; skip it while the lockfile is the one already installed.
  local lock
  lock=$(git hash-object mobile/package-lock.json)
  (
    cd mobile
    if [ "$(cat node_modules/.ci-lock 2>/dev/null)" != "$lock" ]; then
      npm ci --silent && echo "$lock" > node_modules/.ci-lock || exit 1
    fi
    npm run typecheck && npm test
  )
}

BASE=$(git merge-base HEAD origin/main 2>/dev/null || true)
CHANGED=$([ -n "$BASE" ] && git diff --name-only "$BASE" || true)

# Does this change touch anything the gate reads? Unknown base means yes.
gate_relevant() {
  [ "$ALL" = 1 ] || [ -z "$BASE" ] && return 0
  case "$1" in
    mobile) grep -q '^mobile/' <<<"$CHANGED" ;;
    *) grep -qvE '^(mobile/|docs/|spec/|memory/|\.github/|[^/]+\.md$)' <<<"$CHANGED" ;;
  esac
}

# Fingerprint of the tracked files a gate reads; empty when the tree is dirty (no caching then).
gate_key() {
  [ -z "$(git status --porcelain)" ] || return 0
  if [ "$1" = mobile ]; then git ls-files -s -- mobile; else git ls-files -s -- . ':!mobile'; fi | git hash-object --stdin
}

PASSED_DIR="$(git rev-parse --git-common-dir)/ci-local-pass"
mkdir -p "$PASSED_DIR"
FAILED=()
NOTES=()
for gate in "${GATES[@]}"; do
  echo; echo "=== ci-local: $gate ==="
  if ! gate_relevant "$gate"; then
    echo "=== $gate: SKIPPED (nothing it reads changed vs origin/main) ==="; NOTES+=("$gate skipped"); continue
  fi
  key=$(gate_key "$gate")
  stamp="$PASSED_DIR/${gate}-${key}"
  if [ "$ALL" != 1 ] && [ -n "$key" ] && [ -e "$stamp" ]; then
    echo "=== $gate: CACHED (passed earlier on identical files) ==="; NOTES+=("$gate cached"); continue
  fi
  start=$SECONDS
  if "gate_$gate"; then
    echo "=== $gate: PASS ($((SECONDS - start))s) ==="
    [ -n "$key" ] && touch "$stamp"
  else
    echo "=== $gate: FAIL ($((SECONDS - start))s) ===" >&2
    FAILED+=("$gate"); break
  fi
done

echo
if [ ${#FAILED[@]} -eq 0 ]; then STATE=success; DESC="All gates passed locally: ${GATES[*]}${NOTES:+ (${NOTES[*]})}"
else STATE=failure; DESC="Failed locally: ${FAILED[*]}"; fi
echo "ci-local: $STATE ($DESC)"

if [ "$PUBLISH" = 1 ]; then
  gh api "repos/{owner}/{repo}/statuses/$SHA" -f state="$STATE" -f context="ci/local" \
    -f description="$DESC" >/dev/null && echo "posted ci/local=$STATE on ${SHA:0:9}"
fi
[ "$STATE" = success ]
