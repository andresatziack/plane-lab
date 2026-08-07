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

  /**
   * Acknowledge an alert for one allowance, so it stops being noise. Decision D53.
   *
   * **The route Phase 5 could not offer.** `ServiceAlertDismissal.period` was a mandatory
   * foreign key until migration 0132 gave it the D29 nullable pair, so there was nowhere to
   * record the acknowledgement of an allowance alert -- it was named as debt owed by Phase 9
   * rather than hidden in a comment.
   *
   * Open to Members as well as Admins, matching the period endpoint: section 9 puts the panel
   * in front of technicians, and an alert nobody present can quiet is an alert everybody learns
   * to ignore.
   *
   * **Dismissing `ALLOWANCE_PENDING_CLOSURE` forfeits nothing.** It silences the reminder while
   * the negotiation the grace period exists for is still happening. Decision B2's band brings
   * it back if the balance moves materially, and the hours are only ever written off by the
   * close endpoint, which is a separate and deliberate act.
   */
  async dismissAlert(
    workspaceSlug: string,
    allowanceId: string,
    alertCode: string
  ): Promise<{ alert_code: string; balance_at_dismissal: string; created: boolean; alerts: unknown[] }> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-issue-allowances/${allowanceId}/dismiss-alert/`, {
      alert_code: alertCode,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
