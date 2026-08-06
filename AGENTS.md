# Agent Development Guide

## Commands

- `pnpm dev` - Start all dev servers (web:3000, admin:3001)
- `pnpm build` - Build all packages and apps
- `pnpm check` - Run all checks (format, lint, types)
- `pnpm check:lint` - OxLint across all packages
- `pnpm check:types` - TypeScript type checking
- `pnpm fix` - Auto-fix format and lint issues
- `pnpm turbo run <command> --filter=<package>` - Target specific package/app
- `pnpm --filter=@plane/ui storybook` - Start Storybook on port 6006

## Code Style

- **Imports**: Use `workspace:*` for internal packages, `catalog:` for external deps
- **TypeScript**: Strict mode enabled, all files must be typed
- **Formatting**: oxfmt, run `pnpm fix:format`
- **Linting**: OxLint with shared `.oxlintrc.json` config
- **Naming**: camelCase for variables/functions, PascalCase for components/types
- **Error Handling**: Use try-catch with proper error types, log errors appropriately
- **State Management**: MobX stores in `packages/shared-state`, reactive patterns
- **Testing**: All features require unit tests, use existing test framework per package
- **Components**: Build in `@plane/ui` with Storybook for isolated development

## Work log feature (fork-specific)

This fork adds a service-desk work log and billing feature. If you are asked to work on
it, start at [`docs/worklog/PROXIMA-SESSAO.md`](./docs/worklog/PROXIMA-SESSAO.md) — it
names the current phase and everything to read. Conventions that apply to every phase are
in [`docs/worklog/CONVENCOES-DE-TRABALHO.md`](./docs/worklog/CONVENCOES-DE-TRABALHO.md).

## Backend tests (Docker)

The Django/pytest suite for `apps/api` runs in an isolated stack defined by `docker-compose-test.yml` at the repo root.

Prereq (once): `./setup.sh` — generates `apps/api/.env` from `.env.example`.

- Full suite: `docker compose -f docker-compose-test.yml up --build --abort-on-container-exit --exit-code-from api-tests`
- Subset: `docker compose -f docker-compose-test.yml run --rm api-tests pytest -m unit`
- Teardown: `docker compose -f docker-compose-test.yml down -v`

See `apps/api/tests/RUNNING_TESTS.md` for the full walkthrough and troubleshooting; see `apps/api/tests/TESTING_GUIDE.md` for test conventions and fixtures.

**If the container runtime does not work** (rootless podman in agent sandboxes cannot publish ports), use the native fallback in [`tools/agent-sandbox/`](./tools/agent-sandbox/README.md). Its README also documents the trap that costs the most time here: `pytest.ini` sets both `--reuse-db` and `--nomigrations`, so the test database is built from the models **once** and reused — any model change then produces a wall of `column ... does not exist` that looks catastrophic and is only a stale database. Pass `RECREATE_DB=1`.
