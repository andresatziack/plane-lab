/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TServiceRevenueOrigin } from "./service-pricing";

/**
 * Which column decided the month a quantity belongs to. Decision D48.
 *
 * **These are not interchangeable and the difference is not cosmetic.** Ad-hoc revenue
 * belongs to the month of `worked_on` -- the service date (R7). Contract consumption belongs
 * to the competency of the **debited period**, which differs whenever D31 clamps a work log
 * dated before the contract's vigency onto the first period: that log's `worked_on` month has
 * no period at all.
 *
 * So a consumption chart grouped by the service date would file retroactive hours in a month
 * the contract never had. The server chooses the basis and sends it back inside every bucket's
 * `filters`, so a drill-down replays the same one -- never assemble a descriptor here by hand.
 */
export type TServiceCompetenceBasis = "worked_on" | "debited_period";

/**
 * The selection a number was computed from. Decision D50.
 *
 * Query-parameter shaped because that is what it is: the server emits this inside every
 * bucket, the drill-down endpoint accepts it verbatim, and the CSV export persists it. **Send
 * it back unchanged.** Editing a descriptor between reading a total and asking for its rows is
 * exactly the divergence it exists to prevent -- if a narrower selection is wanted, ask the
 * server for a narrower report and use the descriptor it returns.
 */
export type TServiceReportFilters = {
  competence_basis: TServiceCompetenceBasis;
  competence_from?: string;
  competence_to?: string;
  service_client_ids?: string;
  project_ids?: string;
  issue_ids?: string;
  author_ids?: string;
  hour_type_ids?: string;
  billing_type_ids?: string;
  settled_routes?: string;
  route_deviation_reasons?: string;
  pricing_failure_reasons?: string;
  debited_period_ids?: string;
  debited_allowance_ids?: string;
  revenue_origins?: string;
  without_pool_origin?: string;
  without_service_client?: string;
};

/** Where a bucket that has no work log list is explained instead. Decision D56. */
export type TServiceReportDrillDown = {
  kind: "contract_period_statement" | "allowance_statement";
  competence: string;
};

/**
 * One reportable number.
 *
 * **Exactly one of `filters` and `drill_down` is present**, and which one is the whole of
 * D56. A bucket with `filters` can be clicked through to the work logs behind it. A bucket
 * with `drill_down` cannot, because there is no list of work logs that sums to it -- a billed
 * overage is the settlement of a deficit, and the logs that produced that deficit debited the
 * pool and carry no amount at all. Render the second kind as a link to the statement, never as
 * a list.
 *
 * Every hour figure arrives as a **string**, not a number: these are decimals with four
 * places and `JSON.parse` would turn them into floats. Use `*_display` for rendering and only
 * parse when a chart needs an axis value.
 *
 * `amount` and `amount_display` are **absent** for anyone who is not a workspace Admin (R11,
 * D51) -- absent, not zero, because a zero is a number the caller was not allowed to have.
 * Branch on presence, never on `=== 0`.
 */
export type TServiceReportBucket = {
  entries: number;
  filters?: TServiceReportFilters;
  drill_down?: TServiceReportDrillDown;

  logged_hours?: string;
  logged_hours_display?: string;
  equivalent_hours?: string;
  equivalent_hours_display?: string;
  debited_hours?: string;
  debited_hours_display?: string;

  amount?: string;
  amount_display?: string;
};

/** A bucket on a monthly series. `competence` is `"YYYY-MM"`. */
export type TServiceSeriesPoint = TServiceReportBucket & {
  competence: string;
};

/** A slice of a distribution. The `*_id` is null for the internal-work slice of a per-client cut. */
export type TServiceDistributionSlice = TServiceReportBucket & {
  hour_type_id?: string | null;
  hour_type_name?: string | null;
  billing_type_id?: string | null;
  billing_type_name?: string | null;
  author_id?: string | null;
  author_name?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  service_client_id?: string | null;
  service_client_name?: string | null;
};

/** The insight cards above a dashboard. */
export type TServiceReportTotals = TServiceReportBucket & {
  issues: number;
  filters: TServiceReportFilters;
  average_equivalent_hours_per_issue?: string;
  average_equivalent_hours_per_issue_display?: string;
  /** Presentation only, decision D53. Never derive a total from it. */
  average_amount_per_issue?: string;
  average_amount_per_issue_display?: string;
};

/** One competency of the revenue chart, split by the four origins of D36. */
export type TServiceRevenuePoint = {
  competence: string;
  origins: Record<TServiceRevenueOrigin, TServiceReportBucket>;
  total_amount?: string;
  total_amount_display?: string;
};

/** One competency of a contract's pool, with each carried parcel's origin. */
export type TServiceContractStatementRow = {
  period_id: string;
  competence: string;
  status: string;
  /**
   * Every hour quantity comes as a pair: the decimal string to compute and compare with, and
   * a `_display` twin already rendered by R3's formatter (`"10.0000"` / `"10h"`).
   *
   * **Render the twin, compute with the raw.** Rendering the raw is how a client came to see
   * "10.0000" where the contract says ten hours.
   */
  contracted_hours: string;
  contracted_hours_display: string;
  carried_hours: string;
  carried_hours_display: string;
  consumed_hours: string;
  consumed_hours_display: string;
  granted_hours: string;
  granted_hours_display: string;
  balance_hours: string;
  balance_hours_display: string;
  discarded_by_cap_hours: string;
  discarded_by_cap_hours_display: string;
  overage_hours: string;
  overage_hours_display: string;
  overage_settlement: string;
  parcels: { origin_competence: string; hours: string; hours_display: string }[];
};

export type TServiceReportContract = {
  contract_id: string;
  code: string;
  name: string;
  service_client_id: string;
  service_client_name: string;
  status: string;
  statement: TServiceContractStatementRow[];
};

/** One credit ever made to an allowance. The history section 3b had no screen for. */
export type TServiceAllowanceCredit = {
  entry_id: string;
  hours: string;
  hours_display: string;
  created_at: string;
  actor_id: string | null;
  actor_display_name: string | null;
  /** Set only when the hours were converted from a contract's remaining balance. */
  origin_competence: string | null;
  notes: string;
};

export type TServiceReportAllowance = {
  id: string;
  issue: string;
  issue_name: string;
  reference: string;
  /** Raw to compute with, `_display` to render. See `TServiceContractStatementRow`. */
  credited_hours: string;
  credited_hours_display: string;
  consumed_hours: string;
  consumed_hours_display: string;
  balance_hours: string;
  balance_hours_display: string;
  consumed_pct: string | null;
  status: string;
  /** Absent for anyone who is not a workspace Admin. */
  overage_hour_rate?: string;
  credits: TServiceAllowanceCredit[];
};

/**
 * Consumption, for a client under contract or for one billed ad-hoc.
 *
 * `shape` says which the server returned, and it is decided by the **data** -- whether the
 * client holds a contract covering the window -- not by anything the browser asked for. Do not
 * try to derive it here.
 *
 * `revenue_series` is absent for a non-Admin, per D51. Absent rather than empty: an empty
 * series is indistinguishable from a month with no revenue, and a technician would have no way
 * to know they were not being shown it.
 */
export type TServiceConsumptionReport = {
  shape: "contract" | "standalone";
  totals: TServiceReportTotals;
  consumption_series: TServiceSeriesPoint[];
  distributions: {
    hour_type: TServiceDistributionSlice[];
    billing_type: TServiceDistributionSlice[];
  };
  /** R5: never one number. Always broken down by billing type. */
  non_billable: TServiceDistributionSlice[];
  allowances: {
    active: TServiceReportAllowance[];
    /** The revised D35: the grace period ran out and a human has to decide. */
    pending_closure: TServiceReportAllowance[];
  };
  contracts?: TServiceReportContract[];
  revenue_series?: TServiceRevenuePoint[];
};

/**
 * A contract as the portal sends it: the same statement, without the Cliente's identity.
 *
 * The Admin payload names the `service_client` on every contract because that dashboard spans
 * several of them. The portal's spans exactly one -- the Cliente of the active project (D67) --
 * so repeating the name on each contract would be telling the client who they are.
 */
export type TServicePortalContract = Omit<TServiceReportContract, "service_client_id" | "service_client_name">;

/**
 * What the client's own user sees about their consumption. D55, D63, D67.
 *
 * **Deliberately not `TServiceConsumptionReport`**, though the two are close enough to tempt a
 * reuse. Three differences, and each of them would be a lie in the other direction:
 *
 * - `contracts[]` carries no `service_client_id` / `service_client_name`, which that type
 *   declares as required.
 * - There is no `revenue_series` and no `amount` anywhere, ever. `ReportViewer.guest()` does not
 *   compute money, so the keys are **absent** rather than zero (R11). An optional `amount` on
 *   this type would invite a component to render a currency card that can only ever be empty.
 * - `logged_hours` is absent for the same reason, and this one is the sharpest: the client
 *   legitimately sees `equivalent_hours`, so the two together give up the multiplier, which
 *   R11(c) forbids by name. `TServiceReportBucket` still declares it optional, so read it as
 *   "never present here" -- a chart on this payload must not offer it as a series.
 *
 * The window is scoped to one project server-side and cannot be widened by asking.
 */
export type TServicePortalReport = {
  shape: "contract" | "standalone";
  totals: TServiceReportTotals;
  consumption_series: TServiceSeriesPoint[];
  distributions: {
    hour_type: TServiceDistributionSlice[];
    billing_type: TServiceDistributionSlice[];
  };
  /** R5: never one number, for the client least of all. Always broken down by billing type. */
  non_billable: TServiceDistributionSlice[];
  allowances: {
    active: TServiceReportAllowance[];
    pending_closure: TServiceReportAllowance[];
  };
  /** Present only when `shape` is `"contract"`. One entry per contract, never merged. */
  contracts?: TServicePortalContract[];
};

export type TServiceOperationalReport = {
  totals: TServiceReportTotals;
  series: TServiceSeriesPoint[];
  distributions: {
    author: TServiceDistributionSlice[];
    project: TServiceDistributionSlice[];
    service_client: TServiceDistributionSlice[];
    hour_type: TServiceDistributionSlice[];
  };
  non_billable: TServiceDistributionSlice[];
};

/**
 * Which extreme of consumption an alert is about, or that it is an action item.
 *
 * `pending_action` is a fourth severity rather than a contract alert, and the difference
 * matters for rendering: a contract alert says "this client's agreement has a problem" and
 * wants attention, while `pending_action` says "there is a decision waiting on you" and wants
 * a button.
 */
export type TServiceAlertSeverity = "high_consumption" | "low_consumption" | "contract" | "pending_action";

export type TServiceAlert = {
  code: string;
  severity: TServiceAlertSeverity;
  [detail: string]: unknown;
};

export type TServicePeriodAlertEntry = {
  contract_id: string;
  contract_code: string;
  contract_name: string;
  service_client_id: string;
  service_client_name: string;
  period_id: string;
  competence: string;
  granted_hours: string;
  consumed_hours: string;
  balance_hours: string;
  /** Rendered twin of `balance_hours`, which is what the alert row prints. */
  balance_hours_display: string;
  alerts: TServiceAlert[];
  has_high_consumption: boolean;
  has_low_consumption: boolean;
};

export type TServiceAllowanceAlertEntry = {
  allowance_id: string;
  issue_id: string;
  issue_name: string;
  project_id: string;
  project_name: string;
  reference: string;
  credited_hours: string;
  consumed_hours: string;
  balance_hours: string;
  /** Rendered twin of `balance_hours`, which is what the alert row prints. */
  balance_hours_display: string;
  alerts: TServiceAlert[];
};

/**
 * Everything that needs somebody to act, from one request.
 *
 * Three lists rather than one, keyed by what they are about: merging them would mean giving
 * every entry a nullable contract, which is the shape that makes a caller guess.
 */
export type TServiceAttentionReport = {
  periods: TServicePeriodAlertEntry[];
  allowances: TServiceAllowanceAlertEntry[];
  clients_without_a_default_contract: string[];
  count: number;
};

/** The billing report. **Admin only** -- every number in it is money. */
export type TServiceBillingReport = {
  consolidation: {
    competence: string;
    clients: unknown[];
    total_amount: string;
    total_amount_display: string;
    internal_work: { hours: string; hours_display: string; entries: number };
  };
  revenue_series: TServiceRevenuePoint[];
  totals: TServiceReportTotals;
};

/** A page of the drill-down. `extra_stats.totals` lets a screen show the sum without paging. */
export type TServiceReportLogsPage = {
  results: unknown[];
  total_results: number;
  total_pages: number;
  next_page_results: boolean;
  extra_stats: { totals: TServiceReportTotals };
};

/**
 * One work item in the client's ticket table. Phase 10, item 7.
 *
 * A `TServiceReportBucket` -- so it carries `entries`, `filters` and the hour pair with its
 * `_display` twin -- plus the identity of the ticket. It intersects `TServiceReportBucket`
 * rather than restating those fields, which is what keeps the drill-down descriptor honest: the
 * row is a bucket of the same descriptor grouped one dimension further.
 *
 * **No `amount`, and unlike the rest of the portal that is not only about the client.** The
 * server never computes it here for any role, because a per-issue sum is not a figure D58
 * licenses: a ticket half absorbed by an allowance and half billed has no single amount that
 * corresponds to an invoice line. The client's money is on the work log rows of one ticket.
 *
 * `logged_hours` is absent for the reason the portal payload's docstring gives, and it is
 * absent by construction on the server -- never computed, so no flag restores it.
 */
export type TServicePortalIssueRow = TServiceReportBucket & {
  issue_id: string;
  /** With `project_identifier`, the readable key a client can read and paste: `MRB-3`. */
  sequence_id: number;
  name: string;
  project_identifier: string;
  state_name: string | null;
  state_group: string | null;
  /** ISO date of the most recent work log on this ticket. The table's sort key. */
  last_worked_on: string | null;
};

/**
 * A page of the client's ticket table.
 *
 * Carries `next_cursor` / `prev_cursor`, which `TServiceReportLogsPage` omits because the modal
 * that consumes it shows one page and never advances. This table does, so the cursors are part
 * of its contract.
 *
 * `extra_stats.totals` is computed from the **same** descriptor as the rows, so a filtered
 * table's header reports the filtered total and not the window's.
 */
export type TServicePortalIssuesPage = {
  results: TServicePortalIssueRow[];
  count: number;
  total_count: number;
  total_results: number;
  total_pages: number;
  next_cursor: string;
  prev_cursor: string;
  next_page_results: boolean;
  prev_page_results: boolean;
  extra_stats: { totals: TServiceReportTotals };
};
