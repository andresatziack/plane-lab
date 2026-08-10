# FEAT-003: Real-time Allowance Updates and UI Polish

## Status: completed

## Description

Ensure allowance indicators auto-refresh after service log CRUD operations, fix border
styling in overrun state to follow the design system, enhance alert styling with
severity-based colors, and improve progress bar visibility.

## Steps

1. Store: After each CRUD action (create, update, remove), call serviceAllowance.fetchAllowance
2. Component: Remove conditional border styling in overrun state, use standard border-subtle
3. Component: Add severity-based alert styling (red for negative balance, amber for warnings)
4. Component: Increase progress bar height from h-1.5 to h-2

## Acceptance Criteria

- Allowance indicators update automatically after create/update/delete service logs
- Border styling always uses border-subtle bg-surface-2 regardless of overrun state
- Alerts have distinct visual styling: red for negative balance, amber for high consumption
- Progress bar is h-2 for better visibility

## Findings

- TypeScript compiles cleanly with zero errors after building internal packages
- Backend tests cannot be run in this environment due to missing Python dependencies (redis module)
  but no backend files were changed
- pnpm 11.3.0 requires Node.js 22+ due to node:sqlite dependency
