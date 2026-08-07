/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// The pricing phase's vocabulary, imported rather than restated so that a work log and the
// consolidation cannot disagree about what a deviation reason is.
import type { TServicePricingFailure, TServiceRateBasis, TServiceRouteDeviation } from "./service-pricing";

/**
 * How the technician expressed the time. Rule R9.
 *
 * Only `interval` establishes *when* the work happened, which is why only interval
 * entries may be split into segments by the classification engine (D13).
 */
export type TServiceLogEntryMode = "duration" | "interval";

/** How the record got here. Decision D12. */
export type TServiceLogSource = "manual" | "imported" | "api";

/**
 * A work log, as the technician and the admin see it.
 *
 * SECURITY, rule R11: this shape carries `raw_duration_minutes`, `logged_hours` and
 * `applied_multiplier`, none of which a client may see. The client portal phase reads
 * a different payload from a different endpoint; do not reuse this interface there.
 *
 * Every hour quantity is a **string**, not a number, and deliberately so. The API
 * serialises them as decimal strings and they must stay strings in the browser:
 * parsing them would put binary floating point into the values an invoice is built
 * from. Format them for display, sum them on the server, and never do arithmetic on
 * them here. The `*_display` fields exist so the browser never needs to.
 */
export interface IServiceLog {
  readonly id: string;
  readonly issue: string;
  readonly project_id: string;
  readonly workspace_id: string;

  /** Who did the work, which is not always who recorded it (R8). */
  readonly author: string;
  readonly author_detail: {
    readonly id: string;
    readonly display_name: string;
    readonly first_name: string;
    readonly last_name: string;
    readonly avatar_url: string | null;
  } | null;

  /** The date the work happened -- drives billing competency (R7), not created_at. */
  worked_on: string;
  description: string;
  entry_mode: TServiceLogEntryMode;
  /** `HH:MM:SS`, both null in duration mode. */
  start_time: string | null;
  end_time: string | null;
  readonly source: TServiceLogSource;

  // The four quantities of section 4. Distinct on purpose; conflating them is the
  // most likely error in this feature.
  /** What was entered, before rounding. Never shown to a client. */
  readonly raw_duration_minutes: number;
  /** After R2 rounding. The official chronological time. Never shown to a client. */
  readonly logged_hours: string;
  /** After the multiplier. The only hour quantity a client sees. */
  readonly equivalent_hours: string;
  /** Equivalent hours, or "0.0000" when the billing route does not charge. */
  readonly debited_hours: string;

  // R4 snapshots. A later catalogue edit must never change these.
  readonly applied_multiplier: string;
  /** The route of the billing type that was **chosen**. */
  readonly applied_billing_route: string;

  // --- the money, Phase 6 -------------------------------------------------------------
  //
  // Every field below is **optional because it is absent for a non-Admin**, not because it
  // is sometimes null. R11 keeps the value in reais away from technicians and R11(b) makes
  // that a serializer's job: the keys are removed from the payload. A client that renders
  // `amount ?? "R$ 0,00"` would invent a price where it was told nothing.

  /** The route that was actually **applied**. Differs from the chosen one only under D33. */
  readonly settled_billing_route?: string;
  /** Why the two differ: a commercial pendency, with a deadline attached. */
  readonly route_deviation_reason?: TServiceRouteDeviation | null;
  readonly applied_hour_rate?: string | null;
  readonly applied_rate_basis?: TServiceRateBasis | null;
  readonly amount?: string;
  /** Formatted pt-BR. **Null when the row carries no value at all**, never "R$ 0,00". */
  readonly amount_display?: string | null;
  /** Why a row that should carry money carries zero. */
  readonly pricing_failure_reason?: TServicePricingFailure | null;

  hour_type: string;
  /** Resolved through `all_objects`, so a retired option still renders (Phase 2, #3). */
  readonly hour_type_name: string | null;
  readonly hour_type_color: string | null;
  billing_type: string;
  readonly billing_type_name: string | null;

  /** What the classification engine proposed. Null until that engine exists. */
  readonly suggested_hour_type: string | null;
  readonly suggested_hour_type_name: string | null;
  readonly is_hour_type_overridden: boolean;
  /** e.g. "Feriado: Natal". Snapshotted text, not a rule reference. */
  readonly classification_reason: string;

  /** Groups the segments one submission produced. Always set; a single entry is a batch of one. */
  readonly batch_id: string;
  readonly segment_index: number;

  /** From the snapshotted route, so it drives the "Garantia" / "Cortesia" badge. */
  readonly is_billable: boolean;

  // pt-BR renderings, computed server side so there is one implementation.
  readonly raw_duration_display: string;
  readonly logged_hours_display: string;
  readonly equivalent_hours_display: string;

  readonly created_at: string;
  readonly updated_at: string;
  readonly created_by: string | null;
  readonly updated_by: string | null;
}

/**
 * The three totals of a work item. Section 6 of the phase brief.
 *
 * Three numbers, never one. They differ, and the labels must make clear how:
 * `logged` is chronological time including non-billable work, `equivalent` has the
 * multiplier applied, `debited` excludes non-billable routes.
 */
export interface IServiceLogTotals {
  readonly logged_hours: string;
  readonly equivalent_hours: string;
  readonly debited_hours: string;
  readonly logged_hours_display: string;
  readonly equivalent_hours_display: string;
  readonly debited_hours_display: string;

  /**
   * The billed value of the work item, Phase 6. **Optional because it is genuinely absent**
   * for anyone who is not a workspace Admin -- R11(b) makes the restriction a serializer's
   * job, so the key is not sent rather than sent as null. Render it when present; never
   * substitute a zero for its absence.
   */
  readonly amount?: string;
  readonly amount_display?: string;
}

/** What the form submits. The duration is free text; the server parses it (R1). */
export interface IServiceLogPayload {
  worked_on: string;
  description: string;
  entry_mode: TServiceLogEntryMode;
  /** Free text, duration mode only: "1h 15min", "1,5h", "90min". */
  duration?: string | null;
  /** `HH:MM`, interval mode only. */
  start_time?: string | null;
  end_time?: string | null;
  hour_type_id?: string | null;
  billing_type_id: string;
  /** Delegation is a later phase; the API refuses anyone but the caller for now. */
  author_id?: string | null;
}

/** A non-blocking advisory returned alongside a successful write. */
export interface IServiceLogWarning {
  readonly code: string;
  readonly raw_duration_minutes?: number;
}

/**
 * What a submission *would* create, computed without persisting anything.
 *
 * Produced by the same server code as the save, so the preview cannot promise one
 * thing and store another. Section 3 requires the preview to be shown before saving
 * whenever the entry will be split.
 */
export interface IServiceLogPreview {
  readonly segments: IServiceLog[];
  readonly raw_duration_minutes: number;
  /** Acceptance criterion 2: the form has to say that rounding happened. */
  readonly was_rounded: boolean;
  readonly totals: {
    readonly logged_hours: string;
    readonly equivalent_hours: string;
    readonly debited_hours: string;
  };
  readonly warning: IServiceLogWarning | null;
}

/** Response shape of the list endpoint and of both write endpoints. */
export interface IServiceLogResponse {
  readonly service_logs: IServiceLog[];
  readonly totals: IServiceLogTotals;
  readonly batch_id?: string;
  readonly warning?: IServiceLogWarning | null;
}
