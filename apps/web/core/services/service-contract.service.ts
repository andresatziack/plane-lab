/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
// services
import { APIService } from "@/services/api.service";

/**
 * Contracts, their competency periods, and the alerts of section 9.
 *
 * Only the reads and the dismissal the dashboards need. Contract CRUD lives in the workspace
 * settings screens and is not routed through here, because a report that could edit a contract
 * would be a report that can change what it is reporting on.
 */
export class ServiceContractService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * Acknowledge an alert for one competency period. Section 9, decision B2.
   *
   * The balance at this moment is recorded server side, so the alert **comes back** if things
   * get materially worse -- dismissing a negative balance at -5h must not silence it at -200h.
   * An alert that fires once, on the cheapest day, is not a guardrail.
   *
   * Open to Members as well as Admins, because section 9 puts the panel in front of technicians.
   */
  async dismissAlert(
    workspaceSlug: string,
    periodId: string,
    alertCode: string
  ): Promise<{ alert_code: string; balance_at_dismissal: string; created: boolean; alerts: unknown[] }> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-contract-periods/${periodId}/dismiss-alert/`, {
      alert_code: alertCode,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** A contract's pool month by month, with each carried parcel's origin competency. */
  async fetchPeriods(workspaceSlug: string, contractId: string): Promise<unknown> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-contracts/${contractId}/periods/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
