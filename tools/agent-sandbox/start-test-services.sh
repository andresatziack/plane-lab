#!/usr/bin/env bash
# Brings up the Postgres and Redis that the apps/api test suite needs, natively.
#
# Use this only when the documented Docker path does not work. The supported way to
# run the suite is `docker-compose-test.yml` at the repo root -- see
# `apps/api/tests/RUNNING_TESTS.md`. This script exists for agent sandboxes where the
# container runtime is broken, and it was built by discovering the two facts below the
# hard way. They cost hours; that is why they are written down.
#
# 1. Native packages, not containers. Rootless podman in these sandboxes cannot
#    publish ports: netavark fails with "setns: IO error" and the container dies with
#    "conmon exited prematurely". With --network=host the containers report Up while
#    `docker exec` insists they are not running. Both were tried.
#
# 2. Non-default ports. Connecting to 127.0.0.1:5432 or :6379 returns EHOSTUNREACH
#    ("No route to host") no matter what is listening -- those ports are intercepted
#    by a network policy, while 5433 and 6380 accept loopback connections normally.
#    Verified by binding a plain Python socket on each port.
#
# 3. Background processes are reaped when the shell invocation that spawned them
#    returns. Postgres does not survive between commands, which is why run-api-tests.sh
#    calls this script itself on every run rather than expecting a running service.
#    Each run therefore inherits a stale postmaster.pid and socket lock, which are
#    cleared below -- but only after confirming nothing is actually listening.
#
# Idempotent: an already-listening service is left alone.
set -uo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# State lives OUTSIDE the repo so a test run never dirties `git status`. Override with
# AGENT_SANDBOX_STATE if the parent directory is not writable.
STATE_DIR=${AGENT_SANDBOX_STATE:-$(dirname "$REPO_ROOT")/.plane-agent-sandbox}

PG_VERSION=15                 # matches docker-compose; Django 5.2 needs >= 13
# The Amazon Linux RPM puts the binaries straight in /usr/bin, not in the
# /usr/pgsql-<v>/bin that the PGDG packages use.
PG_BIN=${PG_BIN:-/usr/bin}
PGDATA=$STATE_DIR/pgdata
PG_SOCKET_DIR=$PGDATA/sockets
PG_LOG=$PGDATA/server.log
PG_PORT=${PG_PORT:-5433}
REDIS_PORT=${REDIS_PORT:-6380}
REDIS_LOG=$STATE_DIR/redis.log

mkdir -p "$STATE_DIR"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

port_open() {
    timeout 2 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$1" 2>/dev/null
}

# --------------------------------------------------------------------------
# Postgres
# --------------------------------------------------------------------------

if port_open "$PG_PORT"; then
    log "postgres already listening on $PG_PORT"
else
    if [ ! -x "$PG_BIN/initdb" ]; then
        log "installing postgresql${PG_VERSION}-server"
        dnf install -y -q "postgresql${PG_VERSION}-server" >/dev/null || {
            echo "failed to install postgres" >&2
            exit 1
        }
    fi

    # Postgres refuses to run as root, so everything below runs as `postgres`, the
    # account the RPM creates.
    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        log "initialising cluster at $PGDATA"
        rm -rf "$PGDATA"
        mkdir -p "$PGDATA"
        chown postgres:postgres "$PGDATA"
        chmod 700 "$PGDATA"
        su postgres -c "$PG_BIN/initdb -D $PGDATA -U postgres --auth-local=trust --auth-host=trust" >/dev/null || {
            echo "initdb failed" >&2
            exit 1
        }
    fi

    # Own socket directory instead of /tmp: an earlier container attempt left a
    # root-owned /tmp/.s.PGSQL.5432.lock behind, and postgres refuses to start when it
    # cannot create its lock file there.
    mkdir -p "$PG_SOCKET_DIR"
    chown postgres:postgres "$PG_SOCKET_DIR"

    if [ -f "$PGDATA/postmaster.pid" ] || compgen -G "$PG_SOCKET_DIR/.s.PGSQL.*" >/dev/null; then
        log "clearing stale postgres lock files"
        rm -f "$PGDATA/postmaster.pid" "$PG_SOCKET_DIR"/.s.PGSQL.*
    fi

    log "starting postgres on $PG_PORT"
    su postgres -c "$PG_BIN/pg_ctl -D $PGDATA -l $PG_LOG -o '-p $PG_PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=$PG_SOCKET_DIR' -w start" >/dev/null || {
        echo "pg_ctl start failed" >&2
        tail -20 "$PG_LOG" 2>/dev/null >&2
        exit 1
    }

    for _ in $(seq 1 60); do
        port_open "$PG_PORT" && break
        sleep 1
    done

    # The role and database the API settings expect. SUPERUSER because pytest-django
    # creates and drops test_<NAME>, and because the models rely on postgres extensions
    # (pgcrypto / uuid-ossp) that only a superuser can enable.
    su postgres -c "$PG_BIN/psql -h 127.0.0.1 -p $PG_PORT -qc \"CREATE ROLE plane LOGIN SUPERUSER CREATEDB PASSWORD 'plane';\"" >/dev/null 2>&1
    su postgres -c "$PG_BIN/createdb -h 127.0.0.1 -p $PG_PORT -O plane plane" >/dev/null 2>&1
fi

if ! port_open "$PG_PORT"; then
    echo "postgres is not listening on $PG_PORT" >&2
    tail -20 "$PG_LOG" 2>/dev/null >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------

if port_open "$REDIS_PORT"; then
    log "redis already listening on $REDIS_PORT"
else
    if ! command -v redis6-server >/dev/null 2>&1 && ! command -v redis-server >/dev/null 2>&1; then
        log "installing redis6"
        dnf install -y -q redis6 >/dev/null || {
            echo "failed to install redis" >&2
            exit 1
        }
    fi

    REDIS_BIN=$(command -v redis6-server || command -v redis-server)

    log "starting redis on $REDIS_PORT"
    # No persistence: this is a throwaway cache for a test run, and RDB snapshots would
    # only add disk churn and a failure mode.
    "$REDIS_BIN" --port "$REDIS_PORT" --bind 127.0.0.1 --daemonize yes \
        --save '' --appendonly no --logfile "$REDIS_LOG" || {
        echo "redis failed to start" >&2
        exit 1
    }

    for _ in $(seq 1 30); do
        port_open "$REDIS_PORT" && break
        sleep 1
    done
fi

if ! port_open "$REDIS_PORT"; then
    echo "redis is not listening on $REDIS_PORT" >&2
    tail -20 "$REDIS_LOG" 2>/dev/null >&2
    exit 1
fi

log "services ready (postgres :$PG_PORT, redis :$REDIS_PORT)"
