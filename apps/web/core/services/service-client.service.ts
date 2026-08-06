/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  IServiceClient,
  IServiceClientLite,
  IServiceClientProject,
  IServiceClientProjectAssignment,
  IServiceClientProjectAssignmentResponse,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

export class ServiceClientService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * List the client companies of a workspace.
   * @param onlyActive when true, inactive clients are excluded, which is what
   * pickers want and the admin list does not.
   */
  async fetchServiceClients(workspaceSlug: string, onlyActive = false): Promise<IServiceClient[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-clients/`, {
      params: onlyActive ? { only_active: "true" } : {},
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchServiceClientDetails(workspaceSlug: string, serviceClientId: string): Promise<IServiceClient> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createServiceClient(workspaceSlug: string, data: Partial<IServiceClient>): Promise<IServiceClient> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-clients/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateServiceClient(
    workspaceSlug: string,
    serviceClientId: string,
    data: Partial<IServiceClient>
  ): Promise<IServiceClient> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteServiceClient(workspaceSlug: string, serviceClientId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The clients the requesting user belongs to, derived from project membership.
   * There is no user-to-client association table by design.
   */
  async fetchUserServiceClients(workspaceSlug: string): Promise<IServiceClientLite[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-clients/me/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchServiceClientProjects(workspaceSlug: string, serviceClientId: string): Promise<IServiceClientProject[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/projects/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Bulk assign projects to a client, used to attribute pre-existing projects. */
  async assignProjects(
    workspaceSlug: string,
    serviceClientId: string,
    data: IServiceClientProjectAssignment
  ): Promise<IServiceClientProjectAssignmentResponse> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/assign-projects/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
