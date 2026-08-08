/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** Whether an allowance still accepts movement. */
export type TServiceIssueAllowanceStatus = "open" | "closed";

/**
 * A pool of hours credited to one work item, isolated from the client's contract.
 *
 * Rule R6's first level: when a work item -- or an ancestor of it -- has an allowance,
 * every `debit_pool` work log on it consumes the allowance and **never** the client's
 * monthly support pool.
 *
 * Every hour quantity is a **string**, not a number, and deliberately so, for the same
 * reason `IServiceLog` gives: the API serialises them as decimal strings and parsing
 * them here would put binary floating point into the values an invoice is built from.
 * Do not do arithmetic on them in the browser. `balance_hours` and `consumed_pct` are
 * computed server side precisely so nothing here has to.
 *
 * SECURITY, rule R11: this shape is for technicians and admins. GUEST is refused by the
 * endpoint outright; the client's own view is the portal phase, with its own payload.
 */
export interface IServiceIssueAllowance {
  readonly id: string;
  readonly issue: string;
  readonly issue_name: string | null;
  readonly project: string;

  /** The commercial reference of the project this allowance pays for. */
  readonly reference: string;
  readonly notes: string;

  readonly credited_hours: string;
  /** The same figure rendered by R3's formatter: `"50.0000"` becomes `"50h"`. Render this one. */
  readonly credited_hours_display: string;
  readonly consumed_hours: string;
  readonly consumed_hours_display: string;
  /** Credited minus consumed. **Negative is a legitimate state** -- see section 3. */
  readonly balance_hours: string;
  readonly balance_hours_display: string;
  /** Null before any credit exists: zero of zero is not zero. */
  readonly consumed_pct: string | null;

  /** A deficit billed at close, in hours. The conversion to money is a later phase. */
  readonly overage_hours: string;
  /** A surplus written off at close. It never moves to the contract pool. */
  readonly expired_hours: string;

  readonly status: TServiceIssueAllowanceStatus;
  readonly closed_at: string | null;
  readonly closed_by: string | null;

  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * The four figures section 4 of the brief puts on the work item, plus the inheritance.
 *
 * `is_inherited` matters more than it looks: a technician seeing 40h on a sub-task has
 * to know those hours belong to the parent project, or the first thing they will do is
 * ask for a second allowance on the sub-task -- which is the one shape the schema
 * refuses.
 */
export interface IServiceIssueAllowanceSummary {
  readonly allowance_id: string;
  readonly issue_id: string;
  readonly reference: string;
  readonly notes: string;
  readonly status: TServiceIssueAllowanceStatus;
  /**
   * Each quantity twice: the decimal to compute with, and the `_display` twin R3's formatter
   * already rendered. The panel renders the twin; `balance_hours` stays the one that answers
   * "is this overrun", because `"−2h".startsWith("-")` is a string test on a formatted value
   * and the raw decimal is the honest place for it.
   */
  readonly credited_hours: string;
  readonly credited_hours_display: string;
  readonly consumed_hours: string;
  readonly consumed_hours_display: string;
  readonly balance_hours: string;
  readonly balance_hours_display: string;
  readonly consumed_pct: string | null;
  readonly overage_hours: string;
  readonly overage_hours_display: string;
  readonly expired_hours: string;
  readonly expired_hours_display: string;
  readonly closed_at: string | null;
  readonly is_inherited: boolean;
  readonly inherited_from_issue_id: string | null;
}

/** An allowance alert. Never a blocker -- section 3 is explicit that overrun does not block. */
export interface IServiceIssueAllowanceAlert {
  readonly code: string;
  readonly severity: string;
  readonly balance_hours?: string;
  readonly credited_hours?: string;
  readonly consumed_hours?: string;
  readonly consumed_pct?: string;
  readonly threshold_pct?: string;
}

/** One credit, as the history shows it. */
export interface IServiceIssueAllowanceCredit {
  readonly entry_id: string;
  readonly hours: string;
  readonly created_at: string;
  readonly actor_id: string | null;
  readonly actor_display_name: string | null;
  /** Set when the hours came from a contract's remaining balance. */
  readonly origin_competence: string | null;
  readonly notes: string;
}

/**
 * The response of the work item allowance endpoint.
 *
 * `allowance` is `null` when the work item has no allowance of its own and none
 * inherited. That is a legitimate answer, not an error: it means rule R6 falls through
 * to the contract pool.
 */
export interface IServiceIssueAllowanceResponse {
  readonly allowance: IServiceIssueAllowance | null;
  readonly summary?: IServiceIssueAllowanceSummary;
  readonly alerts?: IServiceIssueAllowanceAlert[];
  readonly credits?: IServiceIssueAllowanceCredit[];
}

/** What the credit form submits. Hours as a string, so the browser never rounds them. */
export interface IServiceIssueAllowanceCreditPayload {
  hours: string;
  reference?: string;
  notes?: string;
}
