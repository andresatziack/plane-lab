/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set } from "lodash-es";
// mobx
import { action, observable, makeObservable, computed, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type { IServiceClient, IServiceClientProject, IServiceClientProjectAssignment } from "@plane/types";
// services
import { ServiceClientService } from "@/services/service-client.service";
// store
import type { CoreRootStore } from "../root.store";

export interface IServiceClientStore {
  // observables
  serviceClients: Record<string, IServiceClient> | null;
  // computed
  serviceClientIds: string[];
  activeServiceClients: IServiceClient[];
  // computed actions
  getServiceClientById: (serviceClientId: string | null | undefined) => IServiceClient | null;
  getServiceClientNameById: (serviceClientId: string | null | undefined) => string | undefined;
  // fetch actions
  fetchServiceClients: (workspaceSlug: string) => Promise<IServiceClient[]>;
  fetchServiceClientProjects: (workspaceSlug: string, serviceClientId: string) => Promise<IServiceClientProject[]>;
  // crud actions
  createServiceClient: (workspaceSlug: string, data: Partial<IServiceClient>) => Promise<IServiceClient>;
  updateServiceClient: (
    workspaceSlug: string,
    serviceClientId: string,
    data: Partial<IServiceClient>
  ) => Promise<IServiceClient>;
  removeServiceClient: (workspaceSlug: string, serviceClientId: string) => Promise<void>;
  assignProjects: (
    workspaceSlug: string,
    serviceClientId: string,
    data: IServiceClientProjectAssignment
  ) => Promise<void>;
}

export class ServiceClientStore implements IServiceClientStore {
  // observables
  // null means "not fetched yet", which components use to show a loader rather
  // than an empty state.
  serviceClients: Record<string, IServiceClient> | null = null;
  // services
  serviceClientService;
  // root store
  rootStore;

  constructor(_rootStore: CoreRootStore) {
    makeObservable(this, {
      // observables
      serviceClients: observable,
      // computed
      serviceClientIds: computed,
      activeServiceClients: computed,
      // fetch actions
      fetchServiceClients: action,
      fetchServiceClientProjects: action,
      // CRUD actions
      createServiceClient: action,
      updateServiceClient: action,
      removeServiceClient: action,
      assignProjects: action,
    });

    // services
    this.serviceClientService = new ServiceClientService();
    // root store
    this.rootStore = _rootStore;
  }

  /** Client ids sorted by name, which is the order the admin list renders. */
  get serviceClientIds() {
    return (
      Object.values(this.serviceClients ?? {})
        // toSorted is not available in this app's TS lib target, and Object.values
        // already returns a fresh array, so sorting in place mutates nothing shared.
        // oxlint-disable-next-line unicorn/no-array-sort
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((serviceClient) => serviceClient.id)
    );
  }

  /** Only active clients: what pickers and filters should offer. */
  get activeServiceClients() {
    return (
      Object.values(this.serviceClients ?? {})
        .filter((serviceClient) => serviceClient.is_active)
        // oxlint-disable-next-line unicorn/no-array-sort
        .sort((a, b) => a.name.localeCompare(b.name))
    );
  }

  getServiceClientById = computedFn((serviceClientId: string | null | undefined) => {
    if (!serviceClientId) return null;
    return this.serviceClients?.[serviceClientId] ?? null;
  });

  /**
   * Display name for a client id.
   *
   * This is what renders the derived client on a work item: the item carries only
   * the id, and the name is resolved here from the already fetched workspace
   * clients, so no extra request is made per work item.
   */
  getServiceClientNameById = computedFn((serviceClientId: string | null | undefined) => {
    if (!serviceClientId) return undefined;
    const serviceClient = this.serviceClients?.[serviceClientId];
    if (!serviceClient) return undefined;
    return serviceClient.trade_name || serviceClient.name;
  });

  fetchServiceClients = async (workspaceSlug: string) => {
    const response = await this.serviceClientService.fetchServiceClients(workspaceSlug);
    runInAction(() => {
      const serviceClientMap: Record<string, IServiceClient> = {};
      response.forEach((serviceClient) => {
        if (serviceClient?.id) serviceClientMap[serviceClient.id] = serviceClient;
      });
      this.serviceClients = serviceClientMap;
    });
    return response;
  };

  fetchServiceClientProjects = async (workspaceSlug: string, serviceClientId: string) =>
    await this.serviceClientService.fetchServiceClientProjects(workspaceSlug, serviceClientId);

  createServiceClient = async (workspaceSlug: string, data: Partial<IServiceClient>) => {
    const response = await this.serviceClientService.createServiceClient(workspaceSlug, data);
    runInAction(() => {
      if (!this.serviceClients) this.serviceClients = {};
      set(this.serviceClients, [response.id], response);
    });
    return response;
  };

  updateServiceClient = async (workspaceSlug: string, serviceClientId: string, data: Partial<IServiceClient>) => {
    const original = this.serviceClients?.[serviceClientId];
    try {
      runInAction(() => {
        if (this.serviceClients?.[serviceClientId]) {
          set(this.serviceClients, [serviceClientId], { ...this.serviceClients[serviceClientId], ...data });
        }
      });
      const response = await this.serviceClientService.updateServiceClient(workspaceSlug, serviceClientId, data);
      runInAction(() => {
        if (!this.serviceClients) this.serviceClients = {};
        set(this.serviceClients, [serviceClientId], response);
      });
      return response;
    } catch (error) {
      // Roll the optimistic write back so the screen never shows a value the
      // server rejected, for instance an invalid CNPJ or a duplicate name.
      runInAction(() => {
        if (original && this.serviceClients) set(this.serviceClients, [serviceClientId], original);
      });
      throw error;
    }
  };

  removeServiceClient = async (workspaceSlug: string, serviceClientId: string) => {
    await this.serviceClientService.deleteServiceClient(workspaceSlug, serviceClientId);
    runInAction(() => {
      if (this.serviceClients) delete this.serviceClients[serviceClientId];
    });
  };

  assignProjects = async (
    workspaceSlug: string,
    serviceClientId: string,
    data: IServiceClientProjectAssignment
  ): Promise<void> => {
    const response = await this.serviceClientService.assignProjects(workspaceSlug, serviceClientId, data);
    // The client's project_count changed, and the project store now holds stale
    // service_client values for the projects that were just reassigned.
    await this.fetchServiceClients(workspaceSlug);
    runInAction(() => {
      response.projects.forEach((project) => {
        const projectDetails = this.rootStore.projectRoot.project.projectMap?.[project.id];
        if (projectDetails) {
          set(this.rootStore.projectRoot.project.projectMap, [project.id], {
            ...projectDetails,
            service_client: project.service_client,
            guest_view_all_features: project.guest_view_all_features,
          });
        }
      });
    });
  };
}
