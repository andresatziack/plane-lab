#!/usr/bin/env bash
# The frontend checks: i18n key parity, typecheck and lint.
#
# Usage:
#   ./run-web-checks.sh                 # i18n + types + lint
#   ./run-web-checks.sh i18n
#   ./run-web-checks.sh types
#   ./run-web-checks.sh lint path/one path/two   # paths default to the whole web app
#   REBUILD=1 ./run-web-checks.sh types          # after editing packages/*/src
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)

# pnpm is not on PATH in these sandboxes. nvm's node has corepack, which provides it.
# The repo pins node 22.18.0 in .mise.toml; a newer nvm node is fine for these checks
# (they are tsc, oxlint and a tsx script).
NODE_DIR=$(ls -d /root/.nvm/versions/node/* 2>/dev/null | tail -1)
[ -n "$NODE_DIR" ] && export PATH="$NODE_DIR/bin:$PATH"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

cd "$REPO_ROOT"

corepack enable pnpm >/dev/null 2>&1

if [ ! -d node_modules ]; then
    log "installing dependencies (slow, first run only)"
    pnpm install --frozen-lockfile 2>&1 | tail -15 || {
        echo "pnpm install failed" >&2
        exit 1
    }
fi

TARGET=${1:-all}
shift 2>/dev/null || true

if [ "$TARGET" = "all" ] || [ "$TARGET" = "i18n" ]; then
    log "i18n key parity across every locale"
    pnpm dlx tsx packages/i18n/scripts/sync-check.ts --ci 2>&1 | tail -25
fi

if [ "$TARGET" = "all" ] || [ "$TARGET" = "types" ]; then
    # The workspace packages have to be built first. `check:types` resolves @plane/types,
    # @plane/ui and friends through their build output, so without this every import of
    # them reports TS2307 -- including in files the current change never touched. The CI
    # workflow does the same: its check-types job `needs: build`.
    #
    # REBUILD=1 forces it: after editing packages/types/src a stale dist/ makes the new
    # exports look missing, which reads as an error in your own file.
    if [ ! -d packages/types/dist ] || [ "${REBUILD:-0}" = "1" ]; then
        log "building workspace packages (slow)"
        pnpm turbo run build --filter='./packages/*' 2>&1 | tail -12
    fi

    log "typecheck: web (react-router typegen + tsc)"
    pnpm --filter web run check:types 2>&1 | tail -40
fi

if [ "$TARGET" = "all" ] || [ "$TARGET" = "lint" ]; then
    # Paths are arguments because linting the whole monorepo surfaces pre-existing
    # warnings that are not yours, and the husky pre-commit hook fails on ANY oxlint
    # warning in a staged file. Lint what you touched, before you stage it.
    PATHS=("$@")
    [ ${#PATHS[@]} -eq 0 ] && PATHS=(apps/web)

    log "lint (oxlint): ${PATHS[*]}"
    npx oxlint --config .oxlintrc.json "${PATHS[@]}" 2>&1 | tail -40
fi
