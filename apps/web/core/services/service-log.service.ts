/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  IServiceLogClientResponse,
  IServiceLogPayload,
  IServiceLogPreview,
  IServiceLogResponse,
  IServiceLogTotals,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

export class ServiceLogService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private basePath(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/service-logs`;
  }

  /** Every work log of a work item, newest service date first, plus the three totals. */
  async fetchServiceLogs(workspaceSlug: string, projectId: string, issueId: string): Promise<IServiceLogResponse> {
    return this.get(`${this.basePath(workspaceSlug, projectId, issueId)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The same work logs as the *client* is allowed to see them. R11, D58, D65.
   *
   * Its own endpoint and its own return type rather than a flag on `fetchServiceLogs`, for the
   * reason the server gives for having a second serializer: the fields R11 withholds should be
   * unreachable, not conditionally absent. A flag would leave one code path that returns either
   * shape, and the caller that forgets which is the leak.
   */
  async fetchClientServiceLogs(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<IServiceLogClientResponse> {
    return this.get(`${this.basePath(workspaceSlug, projectId, issueId)}/client/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchTotals(workspaceSlug: string, projectId: string, issueId: string): Promise<IServiceLogTotals> {
    return this.get(`${this.basePath(workspaceSlug, projectId, issueId)}/totals/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * What a submission would create, without creating it.
   *
   * Drives the real-time conversion display R1 asks for, the rounding notice of
   * acceptance criterion 2, and the split preview of section 3. Computed by the same
   * server code as the save, which is the point -- a preview built from separate logic
   * can disagree with what gets stored.
   */
  async previewServiceLog(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceLogPayload
  ): Promise<IServiceLogPreview> {
    return this.post(`${this.basePath(workspaceSlug, projectId, issueId)}/preview/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createServiceLog(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: IServiceLogPayload
  ): Promise<IServiceLogResponse> {
    return this.post(`${this.basePath(workspaceSlug, projectId, issueId)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Edit a whole entry.
   *
   * The batch, not a single row: an edit can change how many segments the entry has,
   * and section 3 requires the segments of one submission to be edited together.
   */
  async updateServiceLogBatch(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    batchId: string,
    data: IServiceLogPayload
  ): Promise<IServiceLogResponse> {
    return this.patch(`${this.basePath(workspaceSlug, projectId, issueId)}/batches/${batchId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Delete an entry and every segment of it, transactionally. */
  async deleteServiceLogBatch(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    batchId: string
  ): Promise<{ totals: IServiceLogTotals }> {
    return this.delete(`${this.basePath(workspaceSlug, projectId, issueId)}/batches/${batchId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
