/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set } from "lodash-es";
import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// plane imports
import type {
  IServiceIssueAllowanceAlert,
  IServiceIssueAllowanceCreditPayload,
  IServiceIssueAllowanceResponse,
  IServiceIssueAllowanceSummary,
} from "@plane/types";
// services
import { ServiceIssueAllowanceService } from "@/services/service-issue-allowance.service";
// local imports
import type { IIssueDetail } from "./root.store";

export interface IServiceAllowanceStoreActions {
  fetchAllowance: (
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ) => Promise<IServiceIssueAllowanceResponse>;
  creditAllowance: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceIssueAllowanceCreditPayload
  ) => Promise<IServiceIssueAllowanceResponse>;
}

export interface IServiceAllowanceStore extends IServiceAllowanceStoreActions {
  /**
   * Keyed by work item id. **`undefined` means "not fetched yet" and `null` means "this
   * work item has no allowance"**, and the difference is the whole reason the two are
   * kept apart: rendering the credit call-to-action for a work item whose allowance has
   * simply not loaded yet would flash a wrong answer at the technician.
   */
  summaryByIssue: Record<string, IServiceIssueAllowanceSummary | null | undefined>;
  alertsByIssue: Record<string, IServiceIssueAllowanceAlert[]>;
  // helpers
  getSummaryByIssueId: (issueId: string) => IServiceIssueAllowanceSummary | null | undefined;
  getAlertsByIssueId: (issueId: string) => IServiceIssueAllowanceAlert[];
  hasLoadedByIssueId: (issueId: string) => boolean;
}

/**
 * The hour allowance of a work item. Section 4 of the allowance phase brief.
 *
 * Two properties carried over from the work log store for the same reasons, and both
 * matter more here because this screen is about money:
 *
 * 1. **Every figure comes from the server, never from arithmetic here.** The hour
 *    quantities are decimal strings so that the browser cannot add them as floats, and
 *    `balance_hours` and `consumed_pct` arrive computed. Section 4b of the master
 *    context requires every total to be a sum of persisted values.
 * 2. **Writes are not optimistic.** A credit can create the allowance, and the server
 *    decides the resulting balance -- and on a sub-task the credit lands on a *different*
 *    work item than the one being displayed. An optimistic number would be a guess on
 *    the one screen whose job is being trustworthy about hours.
 *
 * Only the summary and the alerts are stored. The credit history is deliberately not
 * kept: it is Phase 9's screen, and holding it here would mean maintaining a list nothing
 * renders.
 */
export class ServiceAllowanceStore implements IServiceAllowanceStore {
  // observables
  summaryByIssue: Record<string, IServiceIssueAllowanceSummary | null | undefined> = {};
  alertsByIssue: Record<string, IServiceIssueAllowanceAlert[]> = {};
  // root store
  rootIssueDetailStore: IIssueDetail;
  // services
  serviceIssueAllowanceService;

  constructor(rootStore: IIssueDetail) {
    makeObservable(this, {
      // observables
      summaryByIssue: observable,
      alertsByIssue: observable,
      // actions
      fetchAllowance: action,
      creditAllowance: action,
    });

    this.rootIssueDetailStore = rootStore;
    this.serviceIssueAllowanceService = new ServiceIssueAllowanceService();
  }

  // ------------------------------------------------------------------- helpers

  getSummaryByIssueId = computedFn((issueId: string) => this.summaryByIssue[issueId]);

  getAlertsByIssueId = computedFn((issueId: string) => this.alertsByIssue[issueId] ?? []);

  /** Distinguishes "no allowance" from "not loaded", which render differently. */
  hasLoadedByIssueId = computedFn((issueId: string) => this.summaryByIssue[issueId] !== undefined);

  // ------------------------------------------------------------------- actions

  private storeResponse(issueId: string, response: IServiceIssueAllowanceResponse) {
    runInAction(() => {
      set(this.summaryByIssue, [issueId], response.allowance ? (response.summary ?? null) : null);
      set(this.alertsByIssue, [issueId], response.alerts ?? []);
    });
  }

  fetchAllowance = async (workspaceSlug: string, projectId: string, issueId: string) => {
    const response = await this.serviceIssueAllowanceService.fetchAllowance(workspaceSlug, projectId, issueId);
    this.storeResponse(issueId, response);
    return response;
  };

  creditAllowance = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceIssueAllowanceCreditPayload
  ) => {
    const response = await this.serviceIssueAllowanceService.creditAllowance(workspaceSlug, projectId, issueId, data);

    // Re-read rather than storing the write response directly. The credit always lands
    // on `issueId`, but what this screen *displays* is whatever rule R6 resolves for it,
    // which on a sub-task can be an ancestor's allowance. Re-reading asks the same
    // question the indicator answers instead of assuming the two coincide.
    await this.fetchAllowance(workspaceSlug, projectId, issueId);

    return response;
  };
}
