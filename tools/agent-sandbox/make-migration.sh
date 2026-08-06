#!/usr/bin/env bash
# Runs manage.py against the native sandbox services, with the same environment
# run-api-tests.sh uses.
#
# This is the ONLY way to exercise a migration here: pytest.ini sets --nomigrations, so
# the suite builds its schema from the models and a broken migration passes unnoticed.
# Every migration in the worklog series was validated with a throwaway database driven
# by this script -- apply the whole chain, assert the data, reverse, assert it is gone,
# re-apply. See README.md for the pattern.
#
# Usage:
#   ./make-migration.sh makemigrations db --name service_contract
#   ./make-migration.sh makemigrations --check --dry-run
#   ./make-migration.sh sqlmigrate db 0128_service_contract
#   ./make-migration.sh migrate
#   POSTGRES_DB=migcheck ./make-migration.sh migrate db 0127   # reverse, on a scratch db
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
API_DIR=$REPO_ROOT/apps/api
STATE_DIR=${AGENT_SANDBOX_STATE:-$(dirname "$REPO_ROOT")/.plane-agent-sandbox}
VENV_DIR=$STATE_DIR/venv-api

export PG_PORT=${PG_PORT:-5433}
export REDIS_PORT=${REDIS_PORT:-6380}

"$HERE/start-test-services.sh" >/dev/null

# shellcheck source=./api-env.sh
source "$HERE/api-env.sh"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "virtualenv missing at $VENV_DIR -- run ./run-api-tests.sh once first" >&2
    exit 1
fi

cd "$API_DIR"
exec "$VENV_DIR/bin/python" manage.py "$@"
