/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  TServiceAttentionReport,
  TServiceBillingReport,
  TServiceConsumptionReport,
  TServiceOperationalReport,
  TServicePortalIssuesPage,
  TServicePortalReport,
  TServiceReportFilters,
  TServiceReportLogsPage,
  TServiceTimesheetReport,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

/**
 * Consumption dashboards, billing reports, and the one drill-down.
 *
 * **Read only. Nothing here writes anything**, which is a property of the phase rather than
 * of this file: the only write the dashboards offer is dismissing an alert, and that lives on
 * the alert endpoints it belongs to.
 *
 * **Four of the five endpoints admit Members**, and the role decides the *shape* of the reply
 * rather than whether it arrives: a Member's payload has no monetary keys at all, because the
 * server never computes them (R11, D51). Do not treat a missing `amount` as an error or as
 * zero -- branch on its presence. Only `fetchBilling` is Admin-only, and a 403 from it is the
 * expected answer for a Member.
 *
 * **A `TServiceReportFilters` is passed back verbatim**, never assembled here. It arrives
 * inside every bucket the server sends, and the whole guarantee of D50 is that the number and
 * the list come from the same descriptor -- rebuilding one in the browser is how they start to
 * disagree.
 */
export class ServiceReportsService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * What a client has consumed. Sections 1, 1b and 1c.
   *
   * Returns `shape: "contract"` or `"standalone"` depending on whether the client holds a
   * contract covering the window. That is decided server side from the data, so a caller never
   * has to know which dashboard to ask for.
   *
   * `include_group` adds the client's children -- listed per contract, never summed into one
   * pool, because a client overrunning behind a healthy sibling is exactly what section 1c
   * exists to surface.
   */
  async fetchConsumption(
    workspaceSlug: string,
    filters: Partial<TServiceReportFilters> & { include_group?: string }
  ): Promise<TServiceConsumptionReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/consumption/`, { params: filters })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * What the client's own user sees about their consumption. Section 3c. D63, D67.
   *
   * **`projectId` is required, and it is the whole tenancy contract of this call.** The report
   * is the report *of one Cliente*, because Phase 8 says the contracts are independent and the
   * dashboards dedicated: Marcel, a GUEST of both Marubeni and Terlogs, must get Marubeni's
   * numbers inside Marubeni and never one payload holding both. The server refuses a call with
   * no project (`PORTAL_REPORT_REQUIRES_A_PROJECT`) or with more than one
   * (`PORTAL_REPORT_ACCEPTS_ONE_PROJECT`), so the consolidated payload cannot be requested even
   * by mistake -- which is why this signature takes the project separately from the filters
   * rather than trusting a caller to have put it in `project_ids`.
   *
   * A project the caller does not belong to answers **200 with an empty payload**, not 403: the
   * project route itself already refuses them, and the empty payload carries the full key set so
   * there is no special case here.
   *
   * Admins and Members reach this route too and receive **exactly** the client's projection --
   * no money, no `logged_hours`. That is deliberate (D63): it makes the portal auditable from
   * the inside. Never render it as an internal dashboard.
   */
  async fetchPortal(
    workspaceSlug: string,
    projectId: string,
    filters: Partial<TServiceReportFilters> = {}
  ): Promise<TServicePortalReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/portal/`, {
      params: { ...filters, project_ids: projectId },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Who worked on what. Section 3. Hours by technician, project, client and hour type. */
  async fetchOperational(
    workspaceSlug: string,
    filters: Partial<TServiceReportFilters>
  ): Promise<TServiceOperationalReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/operational/`, { params: filters })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Everything that needs somebody to act. Section 3b.
   *
   * Contract periods, work item allowances and configuration faults in one reply, so a badge
   * does not have to add three requests up and get the count wrong.
   */
  async fetchAttention(
    workspaceSlug: string,
    params: { contract_id?: string; project_id?: string } = {}
  ): Promise<TServiceAttentionReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/attention/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * What to invoice. Section 2. **Workspace ADMIN only.**
   *
   * A competency is required: "everything ever billed" is not a document anybody issues, and
   * defaulting to the current month would silently answer a different question.
   */
  async fetchBilling(workspaceSlug: string, filters: Partial<TServiceReportFilters>): Promise<TServiceBillingReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/billing/`, { params: filters })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * **The drill-down.** Acceptance criterion 8.
   *
   * Takes a bucket's own `filters` and returns the work logs behind it. One endpoint for every
   * chart, because the list and the number have to come from the same query -- two
   * implementations of "the rows behind this bucket" is how a list stops matching the total
   * above it.
   *
   * Pass the descriptor **exactly as it arrived**. `extra_stats.totals` carries the sum so a
   * modal can show it without paging through everything.
   */
  async fetchLogs(
    workspaceSlug: string,
    filters: TServiceReportFilters,
    pagination: { cursor?: string; per_page?: number } = {}
  ): Promise<TServiceReportLogsPage> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/logs/`, {
      params: { ...filters, ...pagination },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The **chamados** behind the client's numbers, paginated. Phase 10, item 7.
   *
   * The portal's own list route. Not `fetchLogs`: that one is ADMIN and MEMBER only, which is
   * why the portal had no drill-down at all until this existed -- a client clicking through
   * would have got a 403.
   *
   * `projectId` is separate from `filters` for the same reason as `fetchPortal`: the tenancy is
   * the caller's contract, not a filter they compose, and the server refuses a request naming no
   * project or more than one. A project the caller does not belong to answers 200 with an empty
   * page, which is a real paginator envelope and not a special case.
   *
   * The rows carry **no money for anybody**, including an Admin auditing the route. See
   * `TServicePortalIssueRow`.
   */
  async fetchPortalIssues(
    workspaceSlug: string,
    projectId: string,
    filters: Partial<TServiceReportFilters> = {},
    pagination: { cursor?: string; per_page?: number } = {}
  ): Promise<TServicePortalIssuesPage> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/portal/issues/`, {
      params: { ...filters, ...pagination, project_ids: projectId },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Timesheet grid: hours by technician and day within a date range.
   *
   * Returns rows (one per author) with per-day cells, column totals, and a grand total.
   */
  async fetchTimesheet(
    workspaceSlug: string,
    params: { worked_on_from: string; worked_on_to: string; author_ids?: string; project_ids?: string }
  ): Promise<TServiceTimesheetReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-reports/timesheet/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
