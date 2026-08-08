/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { IUserLite } from "./users";

/**
 * The three work log grants of Phase 7. Decision D45.
 *
 * Three independent booleans and not a role, because they answer three unrelated questions and a
 * ladder would force an order the business does not have: a coordinator who logs on behalf of the
 * team need not be able to edit what the team logged.
 *
 * `can_reassign_author` is deliberately the narrowest. On its own it does nothing -- the server
 * scopes it to work logs the caller may already edit, so it cannot smuggle in a slice of
 * `can_manage_others`.
 */
export type TServiceMemberCapabilityKey = "can_manage_others" | "can_delegate" | "can_reassign_author";

/**
 * A grant row as the workspace list returns it.
 *
 * **Only members holding at least one grant appear in that list.** Revocation keeps the row with
 * every flag false so the audit trail stays attached, and the server filters those out -- so
 * absence from the list means "holds nothing", never "was never considered".
 *
 * An **Admin holds all three implicitly and appears here nowhere**. Resolution is `is_admin OR
 * flag`, with no back-fill, so rendering an Admin from this list would show three switches off
 * for somebody who can do all three.
 */
export interface IServiceMemberPermission {
  readonly id: string;
  readonly workspace: string;
  /** The user id. */
  readonly member: string;
  readonly member_detail: IUserLite;
  readonly can_manage_others: boolean;
  readonly can_delegate: boolean;
  readonly can_reassign_author: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * The caller's own resolved capabilities, from `service-member-permissions/me/`.
 *
 * **Resolved, not stored**: an Admin gets all three `true` without a row existing. This is what a
 * work log form should ask before offering the delegation field, rather than inferring it from a
 * role.
 */
export interface IServiceMemberCapabilities {
  readonly is_admin: boolean;
  readonly can_manage_others: boolean;
  readonly can_delegate: boolean;
  readonly can_reassign_author: boolean;
}

/** Only the flags being changed need to be sent; the server touches nothing else. */
export type TServiceMemberPermissionPayload = Partial<Record<TServiceMemberCapabilityKey, boolean>>;

/** What PATCH answers: the flags as they now stand, not the full row. */
export interface IServiceMemberPermissionUpdateResponse {
  readonly member: string;
  readonly can_manage_others: boolean;
  readonly can_delegate: boolean;
  readonly can_reassign_author: boolean;
}
