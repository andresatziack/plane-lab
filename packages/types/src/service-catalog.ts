/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Where logged time goes. This is the heart of the debit hierarchy rule (R6).
 *
 * - `debit_pool` draws from the client's contracted hour pool
 * - `bill_amount` produces an invoice amount
 * - `non_billable` produces neither
 */
export type TServiceBillingRoute = "debit_pool" | "bill_amount" | "non_billable";

/** Fields shared by every work log catalogue option. */
export interface IServiceCatalogOption {
  readonly id: string;
  readonly workspace_id: string;
  name: string;
  description: string;
  /**
   * Display order only. Reordering writes a midpoint between two neighbours on the
   * moved row, which is the house pattern for drag and drop.
   *
   * Not the classification priority that the calendar and windows phase introduces:
   * those are different concerns, and merging them would let a cosmetic drag change
   * how hours are classified, and therefore an invoice.
   */
  sequence: number;
  is_active: boolean;
  is_default: boolean;
  external_source: string | null;
  external_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly created_by: string | null;
  readonly updated_by: string | null;
}

/**
 * When work happened, and what that costs relative to business hours.
 *
 * SECURITY: `multiplier` must never reach a client. Rule R11 lets a client see the
 * hour type *label* and the resulting equivalent hours, not the factor. That is why
 * the catalogue endpoints are closed to guests and why the client portal reads hour
 * type names through the work log's own client-facing payload.
 */
export interface IServiceHourType extends IServiceCatalogOption {
  /**
   * A decimal, carried as a string on purpose.
   *
   * The API serialises it as a string and it must stay one here: parsing it into a
   * JavaScript number would reintroduce binary floating point into the one value
   * that every hour and every amount in the system is multiplied by. Format it for
   * display, compare it as a string, and never do arithmetic with it in the browser.
   *
   * Scale is fixed at two decimal places by section 4b of the master context.
   */
  multiplier: string;
  color: string;
  /**
   * Which hour type wins when two classification windows both cover the same instant.
   *
   * Lower wins. The seed uses 10 for the holiday type, 20 for after hours and 30 for
   * business hours, leaving room to insert a custom type between two of them without a
   * deploy. Rows created before the calendar phase were back-filled to 1000, which loses
   * every contest against a seeded type -- deliberately, since an unranked type should
   * not silently outrank a configured one.
   *
   * This is not `sequence`. `sequence` is drag-and-drop display order and changing it
   * must never change how an hour is classified.
   */
  priority: number;
}

/** Where the logged time goes: a contracted pool, an invoice, or nowhere. */
export interface IServiceBillingType extends IServiceCatalogOption {
  billing_route: TServiceBillingRoute;
}

/** The kinds of entity the configuration audit trail can describe. */
export type TServiceConfigEntity =
  | "service_hour_type"
  | "service_billing_type"
  | "service_holiday"
  | "service_classification_window";

/**
 * What happened to the configuration row.
 *
 * For the catalogues, the financial event is `updated` -- a multiplier changing. For
 * holidays and classification windows the relationship inverts: `created` and `deleted`
 * are the financial events, because registering a holiday moves every work log that day
 * onto a different multiplier. A consumer that renders only `updated` would hide exactly
 * the entries that matter most in those two tables.
 */
export type TServiceConfigVerb = "created" | "updated" | "deleted";

/** The actor of an audit entry, expanded so the reader does not need a second request. */
export interface IServiceConfigActivityActor {
  readonly id: string;
  readonly display_name: string;
  readonly first_name: string;
  readonly last_name: string;
  readonly email: string;
  readonly avatar_url: string | null;
}

/**
 * One field change to configuration that affects money.
 *
 * Append-only: there is no create, update or delete endpoint. Records changes to
 * tracked fields only -- creation and deletion are already answered by the entity's
 * own created_by, created_at and deleted_at.
 */
export interface IServiceConfigActivity {
  readonly id: string;
  readonly workspace_id: string;
  readonly entity_name: TServiceConfigEntity;
  readonly entity_identifier: string;
  readonly verb: TServiceConfigVerb;
  /** Null when `verb` is `created` or `deleted`: those describe the whole row. */
  readonly field_name: string | null;
  /**
   * For `updated`, the value before the change. For `deleted`, a human-readable summary
   * of the row that was removed. Null for `created`.
   */
  readonly old_value: string | null;
  /**
   * For `updated`, the value after the change. For `created`, a human-readable summary of
   * the row that appeared. Null for `deleted`.
   */
  readonly new_value: string | null;
  readonly actor: string | null;
  readonly actor_detail: IServiceConfigActivityActor | null;
  readonly created_at: string;
}

/** Filters accepted by the audit trail endpoint. */
export interface IServiceConfigActivityFilters {
  entity_name?: TServiceConfigEntity;
  entity_identifier?: string;
  /** A bare `YYYY-MM-DD` is expanded server side to cover the whole day. */
  created_at__gte?: string;
  created_at__lte?: string;
  per_page?: number;
  cursor?: string;
}

/** Cursor-paginated response shape used by the audit trail endpoint. */
export interface IServiceConfigActivityResponse {
  results: IServiceConfigActivity[];
  next_cursor?: string;
  prev_cursor?: string;
  next_page_results?: boolean;
  prev_page_results?: boolean;
  count?: number;
  total_pages?: number;
}
