# `tools/agent-sandbox` — running the suite when Docker is not available

**The supported way to run the API tests is Docker**: `docker-compose-test.yml` at the
repo root, documented in [`apps/api/tests/RUNNING_TESTS.md`](../../apps/api/tests/RUNNING_TESTS.md).
Use that first.

These scripts are the fallback for **agent sandboxes where the container runtime is
broken**. They exist because the same discoveries were otherwise made from scratch every
session, at a cost of hours each time. Nothing here is used by CI, and nothing here
changes how the application runs.

## Scripts

| Script                   | What it does                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------ |
| `run-api-tests.sh`       | Whole flow: services, virtualenv, pytest. Forwards any pytest arguments              |
| `start-test-services.sh` | Native Postgres and Redis. Idempotent                                                |
| `make-migration.sh`      | `manage.py` with the same environment. The **only** way to exercise a migration here |
| `run-web-checks.sh`      | i18n parity, typecheck, oxlint                                                       |
| `api-env.sh`             | Sourced by the two above. Every variable is commented with what breaks without it    |

State (virtualenv, Postgres data) goes in `../.plane-agent-sandbox`, **outside the repo**,
so a test run never dirties `git status`. Override with `AGENT_SANDBOX_STATE`.

## The four traps, in the order they will bite

### 1. `--reuse-db` plus `--nomigrations` — the expensive one

`pytest.ini` sets both. The schema is built from the models **once**, then the test
database is reused forever and never brought back in step. Change a model and the next
run fails with `column ... does not exist` on every test touching that table — dozens of
errors that look like a catastrophic regression and are only a stale database.

```bash
RECREATE_DB=1 ./run-api-tests.sh      # after ANY model change
```

### 2. The suite cannot validate a migration

Same two flags: the suite never runs a migration, so a broken one passes unnoticed.
Migrations need `make-migration.sh` against a throwaway database. The pattern used for
every migration in the worklog series:

```bash
POSTGRES_DB=migcheck ./make-migration.sh migrate          # apply the whole chain
POSTGRES_DB=migcheck ./make-migration.sh migrate db 0127   # reverse one step
POSTGRES_DB=migcheck ./make-migration.sh migrate          # re-apply
```

Assert the **data**, not just that it ran: rows seeded, columns dropped on reverse,
back-fill correct. And assert the reverse actually removed things — a reverse that
silently does nothing looks identical to one that works.

Do it in **one shell invocation** where possible: background processes are reaped when
the spawning shell returns, so a Postgres started in one command is gone in the next.
That is why `run-api-tests.sh` starts the services itself every time.

### 3. Ports 5432 and 6379 are unreachable

They return `EHOSTUNREACH` no matter what is listening — intercepted by a network
policy. Postgres runs on **5433** and Redis on **6380**. Verified by binding a plain
socket on each.

Containers were tried and do not work: rootless podman cannot publish ports (netavark
`setns: IO error`, then `conmon exited prematurely`), and with `--network=host` the
containers report `Up` while `docker exec` denies they are running.

### 4. Frontend gotchas

- The husky pre-commit hook runs lint-staged, needs node on `PATH`, and fails on **any**
  oxlint warning in a staged file — including one that was already there. Lint your paths
  before staging.
- After editing `packages/*/src`, use `REBUILD=1 ./run-web-checks.sh types`. A stale
  `dist/` makes new exports look missing.
- `ruff check` is what CI would run for the API. `ruff format` is **not** — dozens of
  pre-existing files fail it, so reformatting produces unrelated churn. Do not.
- The propel `Button` has no `neutral-primary` variant. Use `secondary`.

## Reverting a deliberate sabotage

Verifying that a test actually fails when the thing it protects is broken means breaking
that thing on purpose. Revert with **git**:

```bash
git checkout -- path/to/file
```

Never keep a backup in `/tmp`: it does not persist between shell invocations, so the
restore fails and the `&&` chain aborts _before_ the verification prints — leaving the
file sabotaged while the output still says success. This happened. Never chain a
verification after a restore with `&&`; run it as a separate command.
