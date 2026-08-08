/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { IUserLite } from "./users";

/**
 * Who, on the client's side, asked for this work. Decision D62.
 *
 * **Its own record and not a column on the work item**, because it answers a question the work
 * item's own fields cannot: a ticket opened by a technician on the phone has `created_by` set to
 * the technician, and the person who actually asked is nowhere. Naming them is also what grants
 * them visibility -- `client_may_reach_issue` reads `created_by` OR this attribution -- so
 * setting it is a permission-bearing act, not a label.
 *
 * The requester is always an active **GUEST** of the work item's project. The server refuses
 * anyone else with `NOT_A_CLIENT_USER_OF_THIS_PROJECT`, and that is a 400 rather than a 403: the
 * caller is entitled, the target is ineligible.
 */
export interface IServiceIssueRequester {
  readonly id: string;
  readonly issue: string;
  /** The requester's user id. */
  readonly requester: string;
  readonly requester_detail: IUserLite;
  readonly created_at: string;
  /** The technician who recorded the attribution, which is rarely the requester. */
  readonly created_by: string;
}

/**
 * What the endpoint answers.
 *
 * **`{ requester: null }` is the common case, not an error.** Most work items have no
 * attribution: either the client opened the ticket themselves, in which case `created_by`
 * already grants them visibility, or nobody has recorded who asked. The server answers 200 with
 * this shape rather than 404, so a missing attribution needs no error handling.
 */
export type TServiceIssueRequesterResponse = IServiceIssueRequester | { requester: null };

export interface IServiceIssueRequesterPayload {
  requester_id: string;
}
