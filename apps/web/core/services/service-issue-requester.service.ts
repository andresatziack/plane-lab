/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type { IServiceIssueRequesterPayload, TServiceIssueRequesterResponse } from "@plane/types";
// services
import { APIService } from "@/services/api.service";

/**
 * Who asked for this work, on the client's side. Decision D62.
 *
 * **All three verbs are ADMIN and MEMBER only, GUEST included in neither.** A client must not
 * name another client's user as the requester, and the asymmetry is the point: the attribution
 * grants portal visibility, so handing the client the pen would let them widen their own reach.
 */
export class ServiceIssueRequesterService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * Addressed through the work item, because there is exactly one requester per work item.
   *
   * The model is a `OneToOneField`, so the work item *is* the identifier and a collection route
   * would invite a second attribution the schema refuses.
   */
  private basePath(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/service-requester`;
  }

  /** `{ requester: null }` when nobody was named. An answer, not an error. */
  async fetchRequester(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<TServiceIssueRequesterResponse> {
    return this.get(`${this.basePath(workspaceSlug, projectId, issueId)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Name the requester, or replace whoever was named.
   *
   * **Idempotent, and there is no separate update call**: the server does `update_or_create`, so
   * changing the requester is this same POST and does not need a DELETE first.
   *
   * It also subscribes the requester to the work item in the same transaction, so notification is
   * Plane's own rather than a second mechanism.
   *
   * Refuses a target who is not an active GUEST of this project with **400**
   * `NOT_A_CLIENT_USER_OF_THIS_PROJECT`. Callers should still offer only guests -- see
   * `IssueServiceRequesterProperty` -- but the server is the authority.
   */
  async setRequester(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceIssueRequesterPayload
  ): Promise<TServiceIssueRequesterResponse> {
    return this.post(`${this.basePath(workspaceSlug, projectId, issueId)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Remove the attribution. 204 even when there was none.
   *
   * **The subscription is deliberately left behind.** Only the visibility grant goes away: a
   * requester who has been following the conversation should not silently stop receiving it
   * because somebody corrected the attribution.
   */
  async removeRequester(workspaceSlug: string, projectId: string, issueId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug, projectId, issueId)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
