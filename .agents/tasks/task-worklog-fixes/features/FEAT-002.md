# FEAT-002: Allowance Update, Delete, and Reconciliation on Creation

## Status: completed

## Description
Add PATCH and DELETE methods to the allowance endpoint, implement reconciliation logic
when a new allowance is created (existing logs debiting the pool should move to the
allowance), and provide frontend update/delete modals.

## Steps
1. Backend: Add PATCH method to IssueServiceAllowanceEndpoint (update reference/notes) - DONE
2. Backend: Add DELETE method to IssueServiceAllowanceEndpoint (reverse debits, soft-delete, re-apply to pool) - DONE
3. Backend: Add reconciliation logic in credit_allowance when a new allowance is created - DONE
4. Backend: Add update serializer (ServiceIssueAllowanceUpdateSerializer) - DONE
5. Frontend: Add updateAllowance and deleteAllowance to service and store - DONE
6. Frontend: Create edit-service-allowance-modal component - DONE
7. Frontend: Create delete-service-allowance-modal component - DONE
8. Frontend: Wire modals into the section component - DONE
9. Types: Add update payload type (IServiceIssueAllowanceUpdatePayload) - DONE

## Acceptance Criteria
- PATCH allows updating reference and notes on an open allowance
- DELETE reverses all debits, soft-deletes, and re-applies logs to the contract pool
- Creating a new allowance moves existing pool debits to it
- Frontend provides edit and delete modals accessible to workspace admins

## Findings
- No database/Django runtime available in the sandbox, so tests could not be executed
- Python syntax verified via ast.parse on all modified files
- Node/pnpm not available for TypeScript type checking, but code follows existing patterns exactly
- The reconciliation on creation uses the existing reverse_debit + apply_debit pipeline, ensuring consistency
- The delete_allowance function runs re-application outside the main transaction to avoid
  holding the lock during potentially slow contract resolution
