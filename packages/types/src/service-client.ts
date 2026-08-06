/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * A client company that service desk work is delivered to and billed for.
 *
 * Named "service client" rather than "customer" on purpose: Plane ships an
 * unrelated Customers feature, and sharing the name would collide on route and
 * meaning.
 */
export interface IServiceClient {
  readonly id: string;
  readonly workspace_id: string;
  name: string;
  trade_name: string;
  /** Normalized CNPJ, 14 characters, no mask. Null when not provided. */
  tax_id: string | null;
  is_active: boolean;
  /**
   * The billing type pre-selected for work logged against this client's projects.
   *
   * Replaces the two-value `default_billing_mode` enum this interface carried before
   * the billing type catalogue existed: keeping both would have been two sources of
   * truth for one decision, and a foreign key also expresses defaults the enum could
   * not, such as a client whose standard route is Cortesia.
   *
   * Null means "use the catalogue default". Resolution is catalogue default, then
   * this, then whatever the technician picks on the individual work log.
   */
  default_billing_type: string | null;
  /** Corporate parent. Records a shareholding relationship and carries no behaviour. */
  parent: string | null;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  notes: string;
  /** Number of live projects linked to this client. Read only, computed by the API. */
  readonly project_count: number;
  readonly created_at: string;
  readonly updated_at: string;
  readonly created_by: string | null;
  readonly updated_by: string | null;
}

/** Minimal shape returned when a client is embedded in another payload. */
export interface IServiceClientLite {
  readonly id: string;
  name: string;
  trade_name: string;
  is_active: boolean;
}

/** A project as listed by the client admin screens. */
export interface IServiceClientProject {
  readonly id: string;
  name: string;
  identifier: string;
  logo_props: unknown;
  service_client: string | null;
  service_client_detail: IServiceClientLite | null;
  guest_view_all_features: boolean;
  is_time_tracking_enabled: boolean;
}

/**
 * Payload for bulk assigning projects to a client.
 *
 * The two feature flags are optional and are only applied when sent. Turning on
 * guest_view_all_features must be an explicit choice by the admin, never a
 * silent side effect of linking a project.
 */
export interface IServiceClientProjectAssignment {
  project_ids: string[];
  guest_view_all_features?: boolean;
  is_time_tracking_enabled?: boolean;
}

export interface IServiceClientProjectAssignmentResponse {
  updated_project_count: number;
  projects: IServiceClientProject[];
}
