#!/usr/bin/env bash
# Runs the apps/api pytest suite natively, for agent sandboxes where Docker is broken.
#
# The supported path is `docker-compose-test.yml` -- see `apps/api/tests/RUNNING_TESTS.md`.
# Use this only when that does not work; `tools/agent-sandbox/README.md` explains when.
#
# Self-bootstrapping, because sandboxes are not shared between sessions: it brings up
# Postgres and Redis, creates a Python 3.12 virtualenv with the API requirements, then
# runs pytest. Re-running is cheap -- every step is skipped when already in place.
#
# Usage:
#   ./run-api-tests.sh                      # whole suite
#   ./run-api-tests.sh -m unit              # any pytest args are forwarded
#   ./run-api-tests.sh plane/tests/unit/utils/test_service_log.py
#   RECREATE_DB=1 ./run-api-tests.sh        # REQUIRED after any model change
#   REBUILD_VENV=1 ./run-api-tests.sh       # force a fresh virtualenv
#
# ======================================================================
# READ THIS BEFORE DEBUGGING A SUDDEN WALL OF ERRORS
# ======================================================================
#
# pytest.ini sets BOTH --reuse-db and --nomigrations. Together they mean the schema is
# built from the models, but only ONCE -- the test database is then reused across runs
# and never brought back in step with the models. Add a field and the next run fails
# with "column ... does not exist" on every test that touches the table, which looks
# like a catastrophic regression and is only a stale database.
#
# Pass RECREATE_DB=1 after changing any model.
#
# The same two flags mean the suite CANNOT validate a migration: it never runs one.
# Migration work needs make-migration.sh and a hand-written check script.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
API_DIR=$REPO_ROOT/apps/api
STATE_DIR=${AGENT_SANDBOX_STATE:-$(dirname "$REPO_ROOT")/.plane-agent-sandbox}
VENV_DIR=$STATE_DIR/venv-api
PY_VERSION=${PY_VERSION:-3.12.13}   # Dockerfile.api pins 3.12; Django 5.2 needs >= 3.10

# Non-default ports: 5432 and 6379 are unreachable on loopback in these sandboxes
# (EHOSTUNREACH regardless of what is listening). See start-test-services.sh.
export PG_PORT=${PG_PORT:-5433}
export REDIS_PORT=${REDIS_PORT:-6380}

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

"$HERE/start-test-services.sh"

# --------------------------------------------------------------------------
# Virtualenv
# --------------------------------------------------------------------------

if [ "${REBUILD_VENV:-0}" = "1" ]; then
    rm -rf "$VENV_DIR"
fi

if [ ! -x "$VENV_DIR/bin/pytest" ]; then
    log "creating virtualenv ($PY_VERSION)"

    PYENV_ROOT=${PYENV_ROOT:-/root/.pyenv}
    PYTHON_BIN="$PYENV_ROOT/versions/$PY_VERSION/bin/python"

    if [ ! -x "$PYTHON_BIN" ]; then
        PYTHON_BIN=$(command -v python3.12 || command -v python3 || true)
    fi

    if [ ! -x "$PYTHON_BIN" ]; then
        echo "no usable python found (wanted $PY_VERSION under $PYENV_ROOT/versions)" >&2
        echo "available: $(ls "$PYENV_ROOT/versions" 2>/dev/null | tr '\n' ' ')" >&2
        exit 1
    fi

    "$PYTHON_BIN" -m venv "$VENV_DIR"

    log "installing requirements (a few minutes the first time)"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip wheel

    # base.txt pins psycopg, psycopg-binary and psycopg-c. psycopg-c ships no wheel and
    # its source build needs the CPython headers, which are not in these images -- so it
    # is dropped. psycopg-binary provides the same C speedups as a prebuilt wheel, and
    # the driver behaviour Django sees is identical.
    REQ_FILE=$(mktemp)
    trap 'rm -f "$REQ_FILE"' EXIT
    {
        grep -v '^psycopg-c==' "$API_DIR/requirements/base.txt"
        grep -v '^-r ' "$API_DIR/requirements/test.txt"
    } > "$REQ_FILE"

    "$VENV_DIR/bin/pip" install --quiet -r "$REQ_FILE"
else
    log "virtualenv already provisioned"
fi

# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

# shellcheck source=./api-env.sh
source "$HERE/api-env.sh"

PYTEST_EXTRA=()
if [ "${RECREATE_DB:-0}" = "1" ]; then
    log "dropping the test database"
    # Dropped here rather than left to --create-db alone. pytest-django tries to drop it
    # too, but reports "database already exists" and carries on when anything still holds
    # a connection -- which would silently keep the stale schema that RECREATE_DB exists
    # to get rid of. Terminating the backends first makes it deterministic.
    su postgres -c "/usr/bin/psql -h 127.0.0.1 -p $PG_PORT -U postgres -qc \
        \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'test_${POSTGRES_DB}';\"" \
        >/dev/null 2>&1
    su postgres -c "/usr/bin/psql -h 127.0.0.1 -p $PG_PORT -U postgres -qc \
        \"DROP DATABASE IF EXISTS test_${POSTGRES_DB};\"" >/dev/null 2>&1
    PYTEST_EXTRA+=(--create-db)
fi

log "running pytest"
cd "$API_DIR"
exec "$VENV_DIR/bin/pytest" "${PYTEST_EXTRA[@]+"${PYTEST_EXTRA[@]}"}" "$@"
