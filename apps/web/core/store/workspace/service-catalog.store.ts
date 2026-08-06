/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { orderBy, set } from "lodash-es";
// mobx
import { action, computed, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type {
  IServiceBillingType,
  IServiceCatalogOption,
  IServiceClassificationWindow,
  IServiceConfigActivity,
  IServiceConfigActivityFilters,
  IServiceCoverageReport,
  IServiceHoliday,
  IServiceHolidayCalendar,
  IServiceHolidayImportResult,
  IServiceHourType,
} from "@plane/types";
// services
import { ServiceCalendarService } from "@/services/service-calendar.service";
import { ServiceCatalogService } from "@/services/service-catalog.service";
// store
import type { CoreRootStore } from "../root.store";

/**
 * Gap left between two neighbouring options when appending, matching the backend's
 * SEQUENCE_STEP. Reordering halves the gap between two rows, so a large step is what
 * keeps midpoints usable for far longer than any catalogue will ever need.
 */
const SEQUENCE_STEP = 15000;

export type TServiceCatalogReorderEdge = "reorder-above" | "reorder-below";

/**
 * Sequence value that puts `destinationId`'s neighbour position within reach.
 *
 * Mirrors the widget manager's approach: compute a midpoint on the client and PATCH
 * only the row that moved, instead of renumbering the whole catalogue.
 */
const computeReorderSequence = <T extends IServiceCatalogOption>(
  sortedOptions: T[],
  destinationId: string,
  edge: TServiceCatalogReorderEdge
): number | undefined => {
  const destinationIndex = sortedOptions.findIndex((option) => option.id === destinationId);
  if (destinationIndex === -1) return undefined;

  const destination = sortedOptions[destinationIndex];

  if (edge === "reorder-above") {
    const previous = sortedOptions[destinationIndex - 1];
    return previous ? (previous.sequence + destination.sequence) / 2 : destination.sequence - SEQUENCE_STEP;
  }

  const next = sortedOptions[destinationIndex + 1];
  return next ? (destination.sequence + next.sequence) / 2 : destination.sequence + SEQUENCE_STEP;
};

export interface IServiceCatalogStore {
  // observables. null means "not fetched yet", which is distinct from "empty".
  hourTypes: Record<string, IServiceHourType> | null;
  billingTypes: Record<string, IServiceBillingType> | null;
  configActivities: IServiceConfigActivity[] | null;
  holidays: Record<string, IServiceHoliday> | null;
  classificationWindows: Record<string, IServiceClassificationWindow> | null;
  coverage: IServiceCoverageReport | null;
  // computed
  hourTypeIds: string[];
  billingTypeIds: string[];
  activeHourTypes: IServiceHourType[];
  activeBillingTypes: IServiceBillingType[];
  defaultHourType: IServiceHourType | null;
  defaultBillingType: IServiceBillingType | null;
  // computed actions
  getHourTypeById: (id: string | null | undefined) => IServiceHourType | null;
  getBillingTypeById: (id: string | null | undefined) => IServiceBillingType | null;
  getHourTypeNameById: (id: string | null | undefined) => string | undefined;
  getBillingTypeNameById: (id: string | null | undefined) => string | undefined;
  resolveDefaultBillingTypeId: (clientDefaultBillingTypeId: string | null | undefined) => string | undefined;
  // fetch actions
  fetchHourTypes: (workspaceSlug: string) => Promise<IServiceHourType[]>;
  fetchBillingTypes: (workspaceSlug: string) => Promise<IServiceBillingType[]>;
  fetchConfigActivities: (
    workspaceSlug: string,
    filters?: IServiceConfigActivityFilters
  ) => Promise<IServiceConfigActivity[]>;
  // hour type crud
  createHourType: (workspaceSlug: string, data: Partial<IServiceHourType>) => Promise<IServiceHourType>;
  updateHourType: (workspaceSlug: string, id: string, data: Partial<IServiceHourType>) => Promise<IServiceHourType>;
  removeHourType: (workspaceSlug: string, id: string) => Promise<void>;
  markHourTypeDefault: (workspaceSlug: string, id: string) => Promise<void>;
  reorderHourType: (
    workspaceSlug: string,
    id: string,
    destinationId: string,
    edge: TServiceCatalogReorderEdge
  ) => Promise<void>;
  // billing type crud
  createBillingType: (workspaceSlug: string, data: Partial<IServiceBillingType>) => Promise<IServiceBillingType>;
  updateBillingType: (
    workspaceSlug: string,
    id: string,
    data: Partial<IServiceBillingType>
  ) => Promise<IServiceBillingType>;
  removeBillingType: (workspaceSlug: string, id: string) => Promise<void>;
  markBillingTypeDefault: (workspaceSlug: string, id: string) => Promise<void>;
  reorderBillingType: (
    workspaceSlug: string,
    id: string,
    destinationId: string,
    edge: TServiceCatalogReorderEdge
  ) => Promise<void>;
  // computed, calendar
  holidayIds: string[];
  windowIds: string[];
  windowsByDayScope: Record<string, IServiceClassificationWindow[]>;
  // holiday crud
  fetchHolidays: (workspaceSlug: string) => Promise<IServiceHoliday[]>;
  fetchHolidayCalendar: (workspaceSlug: string, year: number) => Promise<IServiceHolidayCalendar>;
  createHoliday: (workspaceSlug: string, data: Partial<IServiceHoliday>) => Promise<IServiceHoliday>;
  updateHoliday: (workspaceSlug: string, id: string, data: Partial<IServiceHoliday>) => Promise<IServiceHoliday>;
  removeHoliday: (workspaceSlug: string, id: string) => Promise<void>;
  importHolidays: (workspaceSlug: string, csvContent: string) => Promise<IServiceHolidayImportResult>;
  // classification window crud
  fetchClassificationWindows: (workspaceSlug: string) => Promise<IServiceClassificationWindow[]>;
  fetchCoverage: (workspaceSlug: string) => Promise<IServiceCoverageReport>;
  createClassificationWindow: (
    workspaceSlug: string,
    data: Partial<IServiceClassificationWindow>
  ) => Promise<IServiceClassificationWindow>;
  updateClassificationWindow: (
    workspaceSlug: string,
    id: string,
    data: Partial<IServiceClassificationWindow>
  ) => Promise<IServiceClassificationWindow>;
  removeClassificationWindow: (workspaceSlug: string, id: string) => Promise<void>;
}

/**
 * The work log catalogues of a workspace, plus the configuration audit trail.
 *
 * One store for both catalogues rather than two: they are configured in the same
 * admin panel, fetched together, and the calendar and windows phase adds holidays
 * and classification windows to the same panel. Splitting now would mean four stores
 * for one screen.
 */
export class ServiceCatalogStore implements IServiceCatalogStore {
  hourTypes: Record<string, IServiceHourType> | null = null;
  billingTypes: Record<string, IServiceBillingType> | null = null;
  configActivities: IServiceConfigActivity[] | null = null;
  holidays: Record<string, IServiceHoliday> | null = null;
  classificationWindows: Record<string, IServiceClassificationWindow> | null = null;
  coverage: IServiceCoverageReport | null = null;
  // services
  serviceCalendarService;
  serviceCatalogService;
  // root store
  rootStore;

  constructor(_rootStore: CoreRootStore) {
    makeObservable(this, {
      // observables
      hourTypes: observable,
      billingTypes: observable,
      configActivities: observable,
      holidays: observable,
      classificationWindows: observable,
      coverage: observable,
      // computed
      hourTypeIds: computed,
      holidayIds: computed,
      windowIds: computed,
      windowsByDayScope: computed,
      billingTypeIds: computed,
      activeHourTypes: computed,
      activeBillingTypes: computed,
      defaultHourType: computed,
      defaultBillingType: computed,
      // fetch actions
      fetchHourTypes: action,
      fetchBillingTypes: action,
      fetchConfigActivities: action,
      // hour type crud
      createHourType: action,
      updateHourType: action,
      removeHourType: action,
      markHourTypeDefault: action,
      reorderHourType: action,
      // billing type crud
      createBillingType: action,
      updateBillingType: action,
      removeBillingType: action,
      markBillingTypeDefault: action,
      reorderBillingType: action,
      // holiday crud
      fetchHolidays: action,
      fetchHolidayCalendar: action,
      createHoliday: action,
      updateHoliday: action,
      removeHoliday: action,
      importHolidays: action,
      // classification window crud
      fetchClassificationWindows: action,
      fetchCoverage: action,
      createClassificationWindow: action,
      updateClassificationWindow: action,
      removeClassificationWindow: action,
    });

    this.serviceCalendarService = new ServiceCalendarService();
    this.serviceCatalogService = new ServiceCatalogService();
    this.rootStore = _rootStore;
  }

  // ------------------------------------------------------------------ computed

  /** Ordered by `sequence`, which is the order the admin dragged them into. */
  get hourTypeIds() {
    return orderBy(Object.values(this.hourTypes ?? {}), "sequence", "asc").map((option) => option.id);
  }

  get billingTypeIds() {
    return orderBy(Object.values(this.billingTypes ?? {}), "sequence", "asc").map((option) => option.id);
  }

  /** What the work log form should offer. Inactive options stay out. */
  get activeHourTypes() {
    return orderBy(
      Object.values(this.hourTypes ?? {}).filter((option) => option.is_active),
      "sequence",
      "asc"
    );
  }

  get activeBillingTypes() {
    return orderBy(
      Object.values(this.billingTypes ?? {}).filter((option) => option.is_active),
      "sequence",
      "asc"
    );
  }

  get defaultHourType() {
    return Object.values(this.hourTypes ?? {}).find((option) => option.is_default && option.is_active) ?? null;
  }

  get defaultBillingType() {
    return Object.values(this.billingTypes ?? {}).find((option) => option.is_default && option.is_active) ?? null;
  }

  // ---------------------------------------------------------- computed actions

  getHourTypeById = computedFn((id: string | null | undefined) => (id ? (this.hourTypes?.[id] ?? null) : null));

  getBillingTypeById = computedFn((id: string | null | undefined) => (id ? (this.billingTypes?.[id] ?? null) : null));

  /**
   * Display name for an hour type id.
   *
   * Resolves inactive options too. A work log recorded against an option that was
   * later deactivated has to keep showing the name it was recorded against.
   */
  getHourTypeNameById = computedFn((id: string | null | undefined) => (id ? this.hourTypes?.[id]?.name : undefined));

  getBillingTypeNameById = computedFn((id: string | null | undefined) =>
    id ? this.billingTypes?.[id]?.name : undefined
  );

  /**
   * The billing type a work log form should pre-select, given the client's default.
   *
   * The browser half of decision D7, and it mirrors `resolve_default_billing_type` on
   * the server deliberately: catalogue default, overridden by the client's own, with
   * an inactive client default falling back rather than pre-selecting something the
   * dropdown hides.
   *
   * The third level of the chain -- what the technician actually picks -- is the form
   * state and is not this function's business.
   */
  resolveDefaultBillingTypeId = computedFn((clientDefaultBillingTypeId: string | null | undefined) => {
    const clientDefault = clientDefaultBillingTypeId ? this.billingTypes?.[clientDefaultBillingTypeId] : undefined;
    if (clientDefault?.is_active) return clientDefault.id;
    return this.defaultBillingType?.id;
  });

  // ------------------------------------------------------------ fetch actions

  fetchHourTypes = async (workspaceSlug: string) => {
    const response = await this.serviceCatalogService.fetchHourTypes(workspaceSlug);
    runInAction(() => {
      const optionMap: Record<string, IServiceHourType> = {};
      response.forEach((option) => {
        if (option?.id) optionMap[option.id] = option;
      });
      this.hourTypes = optionMap;
    });
    return response;
  };

  fetchBillingTypes = async (workspaceSlug: string) => {
    const response = await this.serviceCatalogService.fetchBillingTypes(workspaceSlug);
    runInAction(() => {
      const optionMap: Record<string, IServiceBillingType> = {};
      response.forEach((option) => {
        if (option?.id) optionMap[option.id] = option;
      });
      this.billingTypes = optionMap;
    });
    return response;
  };

  fetchConfigActivities = async (workspaceSlug: string, filters: IServiceConfigActivityFilters = {}) => {
    const response = await this.serviceCatalogService.fetchConfigActivities(workspaceSlug, filters);
    const results = response?.results ?? [];
    runInAction(() => {
      this.configActivities = results;
    });
    return results;
  };

  // ------------------------------------------------------------- hour type crud

  createHourType = async (workspaceSlug: string, data: Partial<IServiceHourType>) => {
    const response = await this.serviceCatalogService.createHourType(workspaceSlug, data);
    runInAction(() => {
      if (!this.hourTypes) this.hourTypes = {};
      set(this.hourTypes, [response.id], response);
      // Creating the first option of an empty catalogue makes it the default, which
      // the server decides. Re-read so no stale is_default is shown.
      if (response.is_default) this.#clearOtherDefaults("hourTypes", response.id);
    });
    return response;
  };

  updateHourType = async (workspaceSlug: string, id: string, data: Partial<IServiceHourType>) => {
    const original = this.hourTypes?.[id];
    try {
      runInAction(() => {
        if (this.hourTypes?.[id]) set(this.hourTypes, [id], { ...this.hourTypes[id], ...data });
      });
      const response = await this.serviceCatalogService.updateHourType(workspaceSlug, id, data);
      runInAction(() => {
        if (!this.hourTypes) this.hourTypes = {};
        set(this.hourTypes, [id], response);
      });
      return response;
    } catch (error) {
      // Roll the optimistic write back. The server refuses several edits the UI
      // cannot fully predict -- unsetting or deactivating the default, a duplicate
      // name -- and the screen must never keep a value that was rejected.
      runInAction(() => {
        if (original && this.hourTypes) set(this.hourTypes, [id], original);
      });
      throw error;
    }
  };

  removeHourType = async (workspaceSlug: string, id: string) => {
    await this.serviceCatalogService.deleteHourType(workspaceSlug, id);
    runInAction(() => {
      if (this.hourTypes) delete this.hourTypes[id];
    });
  };

  markHourTypeDefault = async (workspaceSlug: string, id: string) => {
    const response = await this.serviceCatalogService.markHourTypeDefault(workspaceSlug, id);
    runInAction(() => {
      if (!this.hourTypes) this.hourTypes = {};
      set(this.hourTypes, [id], response);
      this.#clearOtherDefaults("hourTypes", id);
    });
  };

  reorderHourType = async (
    workspaceSlug: string,
    id: string,
    destinationId: string,
    edge: TServiceCatalogReorderEdge
  ) => {
    const sortedOptions = orderBy(Object.values(this.hourTypes ?? {}), "sequence", "asc");
    const sequence = computeReorderSequence(sortedOptions, destinationId, edge);
    if (sequence === undefined) return;
    await this.updateHourType(workspaceSlug, id, { sequence });
  };

  // ---------------------------------------------------------- billing type crud

  createBillingType = async (workspaceSlug: string, data: Partial<IServiceBillingType>) => {
    const response = await this.serviceCatalogService.createBillingType(workspaceSlug, data);
    runInAction(() => {
      if (!this.billingTypes) this.billingTypes = {};
      set(this.billingTypes, [response.id], response);
      if (response.is_default) this.#clearOtherDefaults("billingTypes", response.id);
    });
    return response;
  };

  updateBillingType = async (workspaceSlug: string, id: string, data: Partial<IServiceBillingType>) => {
    const original = this.billingTypes?.[id];
    try {
      runInAction(() => {
        if (this.billingTypes?.[id]) set(this.billingTypes, [id], { ...this.billingTypes[id], ...data });
      });
      const response = await this.serviceCatalogService.updateBillingType(workspaceSlug, id, data);
      runInAction(() => {
        if (!this.billingTypes) this.billingTypes = {};
        set(this.billingTypes, [id], response);
      });
      return response;
    } catch (error) {
      runInAction(() => {
        if (original && this.billingTypes) set(this.billingTypes, [id], original);
      });
      throw error;
    }
  };

  removeBillingType = async (workspaceSlug: string, id: string) => {
    await this.serviceCatalogService.deleteBillingType(workspaceSlug, id);
    runInAction(() => {
      if (this.billingTypes) delete this.billingTypes[id];
    });
  };

  markBillingTypeDefault = async (workspaceSlug: string, id: string) => {
    const response = await this.serviceCatalogService.markBillingTypeDefault(workspaceSlug, id);
    runInAction(() => {
      if (!this.billingTypes) this.billingTypes = {};
      set(this.billingTypes, [id], response);
      this.#clearOtherDefaults("billingTypes", id);
    });
  };

  reorderBillingType = async (
    workspaceSlug: string,
    id: string,
    destinationId: string,
    edge: TServiceCatalogReorderEdge
  ) => {
    const sortedOptions = orderBy(Object.values(this.billingTypes ?? {}), "sequence", "asc");
    const sequence = computeReorderSequence(sortedOptions, destinationId, edge);
    if (sequence === undefined) return;
    await this.updateBillingType(workspaceSlug, id, { sequence });
  };

  // ------------------------------------------------ computed, holidays and windows

  /** Ordered by date, which is how a calendar reads. */
  get holidayIds() {
    return orderBy(Object.values(this.holidays ?? {}), ["date", "name"], ["asc", "asc"]).map((holiday) => holiday.id);
  }

  get windowIds() {
    return orderBy(Object.values(this.classificationWindows ?? {}), ["day_scope", "start_time"], ["asc", "asc"]).map(
      (window) => window.id
    );
  }

  /**
   * Windows grouped by the kind of day they apply to.
   *
   * The panel edits the week one day at a time, because that is how an admin thinks about
   * it -- and because coverage is a per-day-scope question, so grouping here is what lets a
   * gap be shown next to the day that has it.
   */
  get windowsByDayScope() {
    const grouped: Record<string, IServiceClassificationWindow[]> = {};

    Object.values(this.classificationWindows ?? {}).forEach((window) => {
      if (!grouped[window.day_scope]) grouped[window.day_scope] = [];
      grouped[window.day_scope].push(window);
    });

    Object.keys(grouped).forEach((scope) => {
      grouped[scope] = orderBy(grouped[scope], "start_time", "asc");
    });

    return grouped;
  }

  // --------------------------------------------------------------- holiday crud

  fetchHolidays = async (workspaceSlug: string) => {
    const response = await this.serviceCalendarService.fetchHolidays(workspaceSlug);
    runInAction(() => {
      const holidayMap: Record<string, IServiceHoliday> = {};
      response.forEach((holiday) => {
        if (holiday?.id) holidayMap[holiday.id] = holiday;
      });
      this.holidays = holidayMap;
    });
    return response;
  };

  /** Never stored: an annual view is a question about a year, not state about the workspace. */
  fetchHolidayCalendar = async (workspaceSlug: string, year: number) =>
    this.serviceCalendarService.fetchCalendar(workspaceSlug, year);

  createHoliday = async (workspaceSlug: string, data: Partial<IServiceHoliday>) => {
    const response = await this.serviceCalendarService.createHoliday(workspaceSlug, data);
    runInAction(() => {
      if (!this.holidays) this.holidays = {};
      set(this.holidays, [response.id], response);
    });
    return response;
  };

  updateHoliday = async (workspaceSlug: string, id: string, data: Partial<IServiceHoliday>) => {
    const original = this.holidays?.[id];
    try {
      runInAction(() => {
        if (this.holidays?.[id]) set(this.holidays, [id], { ...this.holidays[id], ...data });
      });
      const response = await this.serviceCalendarService.updateHoliday(workspaceSlug, id, data);
      runInAction(() => {
        if (!this.holidays) this.holidays = {};
        set(this.holidays, [id], response);
      });
      return response;
    } catch (error) {
      // Roll the optimistic write back. The server refuses edits the UI cannot predict --
      // a recurrence collision in either direction -- and the screen must never keep a
      // value that was rejected.
      runInAction(() => {
        if (original && this.holidays) set(this.holidays, [id], original);
      });
      throw error;
    }
  };

  removeHoliday = async (workspaceSlug: string, id: string) => {
    await this.serviceCalendarService.deleteHoliday(workspaceSlug, id);
    runInAction(() => {
      if (this.holidays) delete this.holidays[id];
    });
  };

  /**
   * Import from CSV, then re-read.
   *
   * Re-read rather than merged: the import is all-or-nothing and can create many rows, and
   * the list is ordered by date, so appending would put an imported January after a
   * December that was already there.
   */
  importHolidays = async (workspaceSlug: string, csvContent: string) => {
    const response = await this.serviceCalendarService.importHolidays(workspaceSlug, csvContent);
    await this.fetchHolidays(workspaceSlug);
    return response;
  };

  // ---------------------------------------------- classification window crud

  fetchClassificationWindows = async (workspaceSlug: string) => {
    const response = await this.serviceCalendarService.fetchWindows(workspaceSlug);
    runInAction(() => {
      const windowMap: Record<string, IServiceClassificationWindow> = {};
      response.forEach((window) => {
        if (window?.id) windowMap[window.id] = window;
      });
      this.classificationWindows = windowMap;
    });
    return response;
  };

  fetchCoverage = async (workspaceSlug: string) => {
    const response = await this.serviceCalendarService.fetchCoverage(workspaceSlug);
    runInAction(() => {
      this.coverage = response;
    });
    return response;
  };

  /**
   * Every window write re-reads the coverage report afterwards.
   *
   * The server validates the whole *set* on each write and rolls the write back when it
   * would break the week -- so the health indicator is stale the moment anything changes,
   * whether the change succeeded or was refused. Re-reading is cheaper than trying to
   * predict the new report in the browser, and it cannot disagree with the server.
   */
  createClassificationWindow = async (workspaceSlug: string, data: Partial<IServiceClassificationWindow>) => {
    const response = await this.serviceCalendarService.createWindow(workspaceSlug, data);
    runInAction(() => {
      if (!this.classificationWindows) this.classificationWindows = {};
      set(this.classificationWindows, [response.id], response);
    });
    await this.fetchCoverage(workspaceSlug);
    return response;
  };

  updateClassificationWindow = async (
    workspaceSlug: string,
    id: string,
    data: Partial<IServiceClassificationWindow>
  ) => {
    // Not optimistic, unlike the catalogue edits. A window edit can be refused by a
    // set-level rule the browser does not evaluate (coverage, or an overlap at the same
    // priority), and showing the new borders before the server accepts them would show a
    // configuration that does not exist.
    const response = await this.serviceCalendarService.updateWindow(workspaceSlug, id, data);
    runInAction(() => {
      if (!this.classificationWindows) this.classificationWindows = {};
      set(this.classificationWindows, [id], response);
    });
    await this.fetchCoverage(workspaceSlug);
    return response;
  };

  removeClassificationWindow = async (workspaceSlug: string, id: string) => {
    await this.serviceCalendarService.deleteWindow(workspaceSlug, id);
    runInAction(() => {
      if (this.classificationWindows) delete this.classificationWindows[id];
    });
    await this.fetchCoverage(workspaceSlug);
  };

  // -------------------------------------------------------------------- private

  /**
   * Keep exactly one option flagged as default in the local map.
   *
   * The server swapped them in one transaction; without this the panel would briefly
   * show two defaults, which is the one thing the invariant promises cannot happen.
   */
  #clearOtherDefaults(collection: "hourTypes" | "billingTypes", keepId: string) {
    const options = this[collection];
    if (!options) return;
    Object.values(options).forEach((option) => {
      if (option.id !== keepId && option.is_default) set(options, [option.id, "is_default"], false);
    });
  }
}
