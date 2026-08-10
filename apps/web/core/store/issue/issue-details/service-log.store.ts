/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set } from "lodash-es";
import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// plane imports
import type { IServiceLog, IServiceLogPayload, IServiceLogPreview, IServiceLogTotals } from "@plane/types";
// services
import { ServiceLogService } from "@/services/service-log.service";
// local imports
import type { IIssueDetail } from "./root.store";

/** Zeros at the persisted scale, so an empty work item renders like a populated one. */
const EMPTY_TOTALS: IServiceLogTotals = {
  logged_hours: "0.0000",
  equivalent_hours: "0.0000",
  debited_hours: "0.0000",
  logged_hours_display: "0h",
  equivalent_hours_display: "0h",
  debited_hours_display: "0h",
};

/** A batch and the segments it produced, which is the unit the UI edits and deletes. */
export type TServiceLogBatch = {
  batchId: string;
  segments: IServiceLog[];
};

export interface IServiceLogStoreActions {
  fetchServiceLogs: (workspaceSlug: string, projectId: string, issueId: string) => Promise<IServiceLog[]>;
  previewServiceLog: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceLogPayload
  ) => Promise<IServiceLogPreview>;
  createServiceLog: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceLogPayload
  ) => Promise<IServiceLog[]>;
  updateServiceLogBatch: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    batchId: string,
    data: IServiceLogPayload
  ) => Promise<IServiceLog[]>;
  removeServiceLogBatch: (workspaceSlug: string, projectId: string, issueId: string, batchId: string) => Promise<void>;
}

export interface IServiceLogStore extends IServiceLogStoreActions {
  // observables. null means "not fetched yet", which is distinct from "none".
  serviceLogsByIssue: Record<string, string[] | undefined>;
  serviceLogMap: Record<string, IServiceLog>;
  totalsByIssue: Record<string, IServiceLogTotals>;
  // helpers
  getServiceLogsByIssueId: (issueId: string) => IServiceLog[] | undefined;
  getServiceLogBatchesByIssueId: (issueId: string) => TServiceLogBatch[];
  getTotalsByIssueId: (issueId: string) => IServiceLogTotals;
  getServiceLogById: (serviceLogId: string) => IServiceLog | undefined;
  getServiceLogCountByIssueId: (issueId: string) => number;
}

/**
 * The work logs of a work item.
 *
 * Modelled on the issue link store, because a work log is the same kind of thing: a
 * sub-entity of a work item, fetched with it and shown in its own section.
 *
 * Two differences worth knowing:
 *
 * 1. **The totals come from the server, never from summing here.** Section 4b of the
 *    master context requires every total to be a sum of persisted values, and the hour
 *    quantities are decimal strings precisely so the browser cannot do float
 *    arithmetic on money. Every write response carries fresh totals, which is also why
 *    the panel never has to re-fetch to stay consistent.
 * 2. **Writes are not optimistic.** The server decides the rounding (R2), the
 *    multiplier snapshot (R4) and how many segments an interval becomes (R10) -- none
 *    of which the browser can predict. An optimistic row would show a number that is
 *    about to change, on a screen whose entire job is being trustworthy about hours.
 */
export class ServiceLogStore implements IServiceLogStore {
  // observables
  serviceLogsByIssue: Record<string, string[] | undefined> = {};
  serviceLogMap: Record<string, IServiceLog> = {};
  totalsByIssue: Record<string, IServiceLogTotals> = {};
  // root store
  rootIssueDetailStore: IIssueDetail;
  // services
  serviceLogService;

  constructor(rootStore: IIssueDetail) {
    makeObservable(this, {
      // observables
      serviceLogsByIssue: observable,
      serviceLogMap: observable,
      totalsByIssue: observable,
      // actions
      fetchServiceLogs: action,
      previewServiceLog: action,
      createServiceLog: action,
      updateServiceLogBatch: action,
      removeServiceLogBatch: action,
    });

    this.rootIssueDetailStore = rootStore;
    this.serviceLogService = new ServiceLogService();
  }

  // ------------------------------------------------------------------- helpers

  getServiceLogById = computedFn((serviceLogId: string) => this.serviceLogMap[serviceLogId] ?? undefined);

  getServiceLogsByIssueId = computedFn((issueId: string) => {
    const ids = this.serviceLogsByIssue[issueId];
    if (!ids) return undefined;
    return ids.map((id) => this.serviceLogMap[id]).filter(Boolean);
  });

  getServiceLogCountByIssueId = computedFn((issueId: string) => this.serviceLogsByIssue[issueId]?.length ?? 0);

  /**
   * The work logs grouped into the batches they were submitted as.
   *
   * The list renders batches, not rows, because a split entry is one thing the
   * technician did and has to be edited and deleted as one. Server order is preserved
   * -- it is already newest service date first, and segments of a batch are adjacent
   * and ordered by their position on the timeline.
   */
  getServiceLogBatchesByIssueId = computedFn((issueId: string): TServiceLogBatch[] => {
    const serviceLogs = this.getServiceLogsByIssueId(issueId);
    if (!serviceLogs) return [];

    const batches: TServiceLogBatch[] = [];
    const indexByBatchId = new Map<string, number>();

    serviceLogs.forEach((serviceLog) => {
      const existing = indexByBatchId.get(serviceLog.batch_id);
      if (existing !== undefined) {
        batches[existing].segments.push(serviceLog);
        return;
      }
      indexByBatchId.set(serviceLog.batch_id, batches.length);
      batches.push({ batchId: serviceLog.batch_id, segments: [serviceLog] });
    });

    batches.forEach((batch) => batch.segments.sort((a, b) => a.segment_index - b.segment_index));

    return batches;
  });

  getTotalsByIssueId = computedFn((issueId: string) => this.totalsByIssue[issueId] ?? EMPTY_TOTALS);

  // ------------------------------------------------------------------- actions

  private replaceIssueLogs(issueId: string, serviceLogs: IServiceLog[], totals: IServiceLogTotals) {
    runInAction(() => {
      // Drop the previous ids for this issue before re-indexing. An edit can reduce
      // the number of segments, and keeping stale ids would leave rows on screen that
      // no longer exist on the server.
      (this.serviceLogsByIssue[issueId] ?? []).forEach((id) => delete this.serviceLogMap[id]);

      serviceLogs.forEach((serviceLog) => set(this.serviceLogMap, [serviceLog.id], serviceLog));
      set(
        this.serviceLogsByIssue,
        [issueId],
        serviceLogs.map((serviceLog) => serviceLog.id)
      );
      set(this.totalsByIssue, [issueId], totals);
    });
  }

  fetchServiceLogs = async (workspaceSlug: string, projectId: string, issueId: string) => {
    const response = await this.serviceLogService.fetchServiceLogs(workspaceSlug, projectId, issueId);
    this.replaceIssueLogs(issueId, response.service_logs, response.totals);
    return response.service_logs;
  };

  /** Never writes to the store: a preview is not state, it is an answer to a question. */
  previewServiceLog = async (workspaceSlug: string, projectId: string, issueId: string, data: IServiceLogPayload) =>
    this.serviceLogService.previewServiceLog(workspaceSlug, projectId, issueId, data);

  createServiceLog = async (workspaceSlug: string, projectId: string, issueId: string, data: IServiceLogPayload) => {
    const response = await this.serviceLogService.createServiceLog(workspaceSlug, projectId, issueId, data);

    // Re-read rather than appending the response rows. A create can produce several
    // segments and the list is ordered by service date, so a backdated entry does not
    // belong at the end -- and the server's ordering is the one that is correct.
    await this.fetchServiceLogs(workspaceSlug, projectId, issueId);

    // The audit trail (R8) writes an IssueActivity row, so the activity feed on the
    // same screen is now stale.
    this.rootIssueDetailStore.activity.fetchActivities(workspaceSlug, projectId, issueId);

    // The allowance balance changes with every debit, so re-fetch it so the indicator
    // stays current without requiring a page reload.
    this.rootIssueDetailStore.serviceAllowance.fetchAllowance(workspaceSlug, projectId, issueId).catch(() => undefined);

    return response.service_logs;
  };

  updateServiceLogBatch = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    batchId: string,
    data: IServiceLogPayload
  ) => {
    const response = await this.serviceLogService.updateServiceLogBatch(
      workspaceSlug,
      projectId,
      issueId,
      batchId,
      data
    );

    await this.fetchServiceLogs(workspaceSlug, projectId, issueId);
    this.rootIssueDetailStore.activity.fetchActivities(workspaceSlug, projectId, issueId);

    // Re-fetch allowance so the indicator reflects the updated debit.
    this.rootIssueDetailStore.serviceAllowance.fetchAllowance(workspaceSlug, projectId, issueId).catch(() => undefined);

    return response.service_logs;
  };

  removeServiceLogBatch = async (workspaceSlug: string, projectId: string, issueId: string, batchId: string) => {
    const response = await this.serviceLogService.deleteServiceLogBatch(workspaceSlug, projectId, issueId, batchId);

    runInAction(() => {
      const remaining = (this.serviceLogsByIssue[issueId] ?? []).filter(
        (id) => this.serviceLogMap[id]?.batch_id !== batchId
      );
      (this.serviceLogsByIssue[issueId] ?? [])
        .filter((id) => this.serviceLogMap[id]?.batch_id === batchId)
        .forEach((id) => delete this.serviceLogMap[id]);
      set(this.serviceLogsByIssue, [issueId], remaining);
      // Totals come back from the delete response, so the panel cannot briefly show a
      // stale total next to a removed row.
      set(this.totalsByIssue, [issueId], response.totals);
    });

    this.rootIssueDetailStore.activity.fetchActivities(workspaceSlug, projectId, issueId);

    // Re-fetch allowance so the indicator reflects the reversed debit.
    this.rootIssueDetailStore.serviceAllowance.fetchAllowance(workspaceSlug, projectId, issueId).catch(() => undefined);
  };
}
