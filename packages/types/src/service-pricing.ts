/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Which of the two pricing formulas produced a value.
 *
 * `base_multiplier` multiplied the client's base rate by the work log's **equivalent**
 * hours. `absolute_override` multiplied an absolute rate by its **logged** hours, because
 * an absolute rate replaces `base * multiplier` and therefore already embeds the
 * multiplier. Never re-derive a value in the browser from either.
 */
export type TServiceRateBasis = "base_multiplier" | "absolute_override";

/**
 * Why a work log that chose the contract pool was billed in reais instead. Decision D33.
 *
 * All four mean **commercial pendency with a deadline**, which is what distinguishes them
 * from a client whose model is avulso -- that client's work logs carry no deviation at all.
 * The consolidation shows the difference because one is a business model and the other is
 * somebody's overdue renewal.
 */
export type TServiceRouteDeviation =
  | "no_contract_for_client"
  | "contract_suspended"
  | "contract_ended"
  | "contract_expired";

/**
 * Why a work log that should carry money carries zero.
 *
 * Two classes that must never be shown together as one list:
 * - **registration pendency** (`no_price_sheet_for_client`, `no_price_sheet_in_force`) is
 *   real money nobody can invoice yet, and somebody must act;
 * - **internal work** (`internal_project_no_client`) is a project with no client, which is
 *   not billable to anyone. Nobody must act, so it is excluded from revenue rather than
 *   listed as a zeroed pendency -- a panel full of "failures" that are not failures is a
 *   panel operators learn to ignore.
 */
export type TServicePricingFailure =
  | "internal_project_no_client"
  | "no_price_sheet_for_client"
  | "no_price_sheet_in_force";

/** The four things a client can be invoiced for. Decision D36. */
export type TServiceRevenueOrigin =
  | "standalone_log"
  | "out_of_scope_log"
  | "contract_overage"
  | "allowance_overage";

/**
 * An absolute rate that replaces `base * multiplier` for one hour type, within one vigency.
 *
 * Belongs to a **price sheet, not to a client**: every vigency is a complete, self-contained
 * price sheet, so a readjustment does not leave an absolute override behind silently
 * un-readjusted.
 */
export interface IServiceClientHourTypeRate {
  readonly id: string;
  readonly hour_type: string;
  readonly hour_type_name: string | null;
  /** Monetary, as a decimal string. Do not parse it to do arithmetic. */
  readonly absolute_rate: string;
}

/**
 * One dated price sheet of a client: the base hour rate in force from a date.
 *
 * There is deliberately **no end date**. The sheet in force on a date is the one with the
 * greatest `starts_on` that is not after it, so a gap and an overlap are both
 * unrepresentable rather than handled. `starts_on` is always the first of a month, enforced
 * in the database.
 */
export interface IServiceClientPrice {
  readonly price_id: string;
  readonly starts_on: string;
  /** The single number a new client needs. Every hour type's rate is derived from it. */
  readonly base_hour_rate: string;
  readonly notes: string;
  readonly overrides: IServiceClientHourTypeRate[];
}

/** One row of the derived table: what an hour of this type actually costs. */
export interface IServiceEffectiveRate {
  readonly hour_type_id: string;
  readonly hour_type_name: string;
  readonly multiplier: string;
  /** Per **logged** hour, so the rows are comparable to each other. */
  readonly rate: string;
  readonly basis: TServiceRateBasis;
  readonly is_overridden: boolean;
}

export interface IServiceClientPriceList {
  readonly prices: IServiceClientPrice[];
  /** Which vigency applies on the date being viewed. Null when none does. */
  readonly in_force_price_id: string | null;
  readonly on_date: string;
  readonly effective_table: IServiceEffectiveRate[];
}

export interface IServiceEffectiveRateTable {
  readonly on_date: string;
  readonly effective_table: IServiceEffectiveRate[];
  readonly base_hour_rate: string | null;
  readonly in_force_since: string | null;
  readonly hour_types: number;
}

/** What billing an overage would cost, read before the irreversible write. */
export interface IServiceOveragePreview {
  readonly overage_hours: string;
  readonly overage_hour_rate: string;
  readonly amount: string;
  readonly rate_source: "contract" | "allowance" | "client_base_rate";
  /**
   * Always `false`. Stated by the API rather than assumed by the client, because billing an
   * overage cannot be undone and the confirmation has to say so.
   */
  readonly is_reversible: boolean;
}

export interface IServiceRevenueBucket {
  readonly hours: string;
  readonly amount: string;
  readonly amount_display: string;
  readonly entries: number;
}

export interface IServiceCommercialPendency {
  readonly reason: TServiceRouteDeviation;
  readonly hours: string;
  readonly amount: string;
  readonly amount_display: string;
  readonly entries: number;
}

/** Carries no amount, on purpose: there is none until a price is registered. */
export interface IServiceRegistrationPendency {
  readonly reason: TServicePricingFailure;
  readonly hours: string;
  readonly entries: number;
}

export interface IServiceConsolidatedClient {
  readonly service_client_id: string;
  readonly service_client_name: string;
  readonly origins: Record<TServiceRevenueOrigin, IServiceRevenueBucket>;
  readonly total_amount: string;
  readonly total_amount_display: string;
  readonly commercial_pendencies: IServiceCommercialPendency[];
  readonly registration_pendencies: IServiceRegistrationPendency[];
}

export interface IServiceBillingConsolidation {
  readonly competence: string;
  readonly clients: IServiceConsolidatedClient[];
  readonly total_amount: string;
  readonly total_amount_display: string;
  /** Reported, never invoiced. See `TServicePricingFailure`. */
  readonly internal_work: { readonly hours: string; readonly entries: number };
}

export interface IServiceLogExportRequest {
  provider?: "csv" | "xlsx" | "json";
  year?: number;
  month?: number;
  service_client_id?: string | null;
  project?: string[];
}

export interface IServiceLogExportHistory {
  readonly id: string;
  readonly created_at: string;
  readonly type: string;
  readonly provider: string;
  readonly status: "queued" | "processing" | "completed" | "failed";
  /** Why it failed, which the issue export's own serializer does not expose. */
  readonly reason: string;
  readonly url: string | null;
  readonly filters: Record<string, string> | null;
  readonly token: string;
}
