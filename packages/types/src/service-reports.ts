/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TServiceRevenueOrigin } from "./service-pricing";

/**
 * Which column decided the month a quantity belongs to. Decision D47.
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
 * The selection a number was computed from. Decision D49.
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
 * D50) -- absent, not zero, because a zero is a number the caller was not allowed to have.
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
  /** Presentation only, decision D52. Never derive a total from it. */
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
  contracted_hours: string;
  carried_hours: string;
  consumed_hours: string;
  granted_hours: string;
  balance_hours: string;
  discarded_by_cap_hours: string;
  overage_hours: string;
  overage_settlement: string;
  parcels: { origin_competence: string; hours: string }[];
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
  credited_hours: string;
  consumed_hours: string;
  balance_hours: string;
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
 * `revenue_series` is absent for a non-Admin, per D50. Absent rather than empty: an empty
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
    internal_work: { hours: string; entries: number };
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
