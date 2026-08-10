# FEAT-002: Allowance Update, Delete, and Reconciliation on Creation

## Status: in_progress

## Description
Add PATCH and DELETE methods to the allowance endpoint, implement reconciliation logic
when a new allowance is created (existing logs debiting the pool should move to the
allowance), and provide frontend update/delete modals.

## Steps
1. Backend: Add PATCH method to IssueServiceAllowanceEndpoint (update reference/notes)
2. Backend: Add DELETE method to IssueServiceAllowanceEndpoint (reverse debits, soft-delete, re-apply to pool)
3. Backend: Add reconciliation logic in credit_allowance when a new allowance is created
4. Backend: Add update serializer
5. Frontend: Add updateAllowance and deleteAllowance to service and store
6. Frontend: Create edit-service-allowance-modal component
7. Frontend: Create delete-service-allowance-modal component
8. Frontend: Wire modals into the section component
9. Types: Add update payload type

## Acceptance Criteria
- PATCH allows updating reference and notes on an open allowance
- DELETE reverses all debits, soft-deletes, and re-applies logs to the contract pool
- Creating a new allowance moves existing pool debits to it
- Frontend provides edit and delete modals accessible to workspace admins
