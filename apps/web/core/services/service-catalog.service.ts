/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  IServiceBillingType,
  IServiceConfigActivityFilters,
  IServiceConfigActivityResponse,
  IServiceHourType,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

export class ServiceCatalogService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  // ---------------------------------------------------------------- hour types

  /**
   * List the hour type catalogue of a workspace.
   * @param onlyActive when true, inactive options are excluded. The work log form
   * wants that; the admin panel does not, because it has to be able to reactivate
   * them.
   */
  async fetchHourTypes(workspaceSlug: string, onlyActive = false): Promise<IServiceHourType[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-hour-types/`, {
      params: onlyActive ? { only_active: "true" } : {},
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createHourType(workspaceSlug: string, data: Partial<IServiceHourType>): Promise<IServiceHourType> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-hour-types/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateHourType(
    workspaceSlug: string,
    hourTypeId: string,
    data: Partial<IServiceHourType>
  ): Promise<IServiceHourType> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-hour-types/${hourTypeId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteHourType(workspaceSlug: string, hourTypeId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/service-hour-types/${hourTypeId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Promote an hour type to be the catalogue default.
   *
   * A dedicated action rather than a PATCH of `is_default`: promoting swaps two rows,
   * and a partial unique index rejects a second default.
   */
  async markHourTypeDefault(workspaceSlug: string, hourTypeId: string): Promise<IServiceHourType> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-hour-types/${hourTypeId}/mark-default/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------------- billing types

  async fetchBillingTypes(workspaceSlug: string, onlyActive = false): Promise<IServiceBillingType[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-billing-types/`, {
      params: onlyActive ? { only_active: "true" } : {},
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createBillingType(workspaceSlug: string, data: Partial<IServiceBillingType>): Promise<IServiceBillingType> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-billing-types/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateBillingType(
    workspaceSlug: string,
    billingTypeId: string,
    data: Partial<IServiceBillingType>
  ): Promise<IServiceBillingType> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-billing-types/${billingTypeId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteBillingType(workspaceSlug: string, billingTypeId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/service-billing-types/${billingTypeId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async markBillingTypeDefault(workspaceSlug: string, billingTypeId: string): Promise<IServiceBillingType> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-billing-types/${billingTypeId}/mark-default/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // -------------------------------------------------------------- audit trail

  /**
   * Read the configuration audit trail. Workspace admin only, and read only.
   *
   * The trail holds multiplier history and, from the pricing phase on, price
   * history. There is no write method here because there is no write endpoint.
   */
  async fetchConfigActivities(
    workspaceSlug: string,
    filters: IServiceConfigActivityFilters = {}
  ): Promise<IServiceConfigActivityResponse> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-config-activities/`, { params: filters })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
