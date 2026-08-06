/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type { IServiceIssueAllowanceCreditPayload, IServiceIssueAllowanceResponse } from "@plane/types";
// services
import { APIService } from "@/services/api.service";

export class ServiceIssueAllowanceService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * The allowance is addressed through its work item, not by its own id.
   *
   * There is exactly one allowance per work item, so the work item *is* the identifier.
   * A collection route would invite a second one, which the schema refuses anyway.
   */
  private basePath(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/service-allowance`;
  }

  /**
   * The allowance that pays for work on this work item, its own or an inherited one.
   *
   * `allowance: null` means rule R6 falls through to the contract pool. That is an
   * answer, not an error.
   */
  async fetchAllowance(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<IServiceIssueAllowanceResponse> {
    return this.get(`${this.basePath(workspaceSlug, projectId, issueId)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Credit hours, creating the allowance on the first credit.
   *
   * The same call for the first credit and for every later one, because they are the
   * same operation: an aditivo de escopo is another credit on the same allowance, never
   * a second allowance.
   *
   * Credits **this** work item, never an inherited ancestor's allowance -- the server
   * enforces that. To top up a parent project, credit the parent.
   */
  async creditAllowance(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceIssueAllowanceCreditPayload
  ): Promise<IServiceIssueAllowanceResponse> {
    return this.post(`${this.basePath(workspaceSlug, projectId, issueId)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
