/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  IServiceClassificationWindow,
  IServiceCoverageReport,
  IServiceHoliday,
  IServiceHolidayCalendar,
  IServiceHolidayImportResult,
  TServiceDayScope,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

export class ServiceCalendarService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  // ------------------------------------------------------------------ holidays

  async fetchHolidays(workspaceSlug: string, onlyActive = false): Promise<IServiceHoliday[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-holidays/`, {
      params: onlyActive ? { only_active: "true" } : {},
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Every holiday *observed* in a year, with annual recurrence expanded to real dates.
   *
   * A separate call from the list because the stored rows are a poor answer to "what does
   * next year look like": a recurring holiday is stored once and observed always.
   */
  async fetchCalendar(workspaceSlug: string, year: number): Promise<IServiceHolidayCalendar> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-holidays/calendar/`, { params: { year } })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createHoliday(workspaceSlug: string, data: Partial<IServiceHoliday>): Promise<IServiceHoliday> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-holidays/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateHoliday(
    workspaceSlug: string,
    holidayId: string,
    data: Partial<IServiceHoliday>
  ): Promise<IServiceHoliday> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-holidays/${holidayId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteHoliday(workspaceSlug: string, holidayId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/service-holidays/${holidayId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Import holidays from CSV text.
   *
   * All or nothing: a rejected import reports every bad row at once and stores none of
   * them, so a partial state never has to be reasoned about.
   */
  async importHolidays(workspaceSlug: string, csvContent: string): Promise<IServiceHolidayImportResult> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-holidays/bulk-import/`, {
      csv_content: csvContent,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------- classification windows

  async fetchWindows(
    workspaceSlug: string,
    filters: { only_active?: boolean; hour_type_id?: string; day_scope?: TServiceDayScope } = {}
  ): Promise<IServiceClassificationWindow[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-classification-windows/`, {
      params: {
        ...(filters.only_active ? { only_active: "true" } : {}),
        ...(filters.hour_type_id ? { hour_type_id: filters.hour_type_id } : {}),
        ...(filters.day_scope ? { day_scope: filters.day_scope } : {}),
      },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The health of the classification configuration: which day scopes and which ranges are
   * uncovered, plus any pair of windows the engine could not decide between.
   *
   * Read only. Coverage is derived from the windows and is never set.
   */
  async fetchCoverage(workspaceSlug: string): Promise<IServiceCoverageReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-classification-windows/coverage/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createWindow(
    workspaceSlug: string,
    data: Partial<IServiceClassificationWindow>
  ): Promise<IServiceClassificationWindow> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-classification-windows/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateWindow(
    workspaceSlug: string,
    windowId: string,
    data: Partial<IServiceClassificationWindow>
  ): Promise<IServiceClassificationWindow> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-classification-windows/${windowId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteWindow(workspaceSlug: string, windowId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/service-classification-windows/${windowId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
