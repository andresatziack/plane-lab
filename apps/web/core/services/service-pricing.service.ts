/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  IServiceBillingConsolidation,
  IServiceClientHourTypeRate,
  IServiceClientPrice,
  IServiceClientPriceList,
  IServiceEffectiveRateTable,
  IServiceLogExportHistory,
  IServiceLogExportRequest,
  IServiceOveragePreview,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

/**
 * Price sheets, the effective rate table, and the billing consolidation.
 *
 * **Every endpoint behind this service is workspace ADMIN.** R11 puts the value in reais out
 * of a technician's reach, and a price sheet is that value before it has been applied. A 403
 * from any of these is the expected answer for a Member, not an error to work around.
 */
export class ServicePricingService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * Every vigency of a client, plus which one is in force on `onDate` and the table it
   * produces.
   *
   * `onDate` defaults to today server side. Passing it is how the settings screen previews a
   * future readjustment without the client having to work out which sheet applies.
   */
  async fetchPrices(
    workspaceSlug: string,
    serviceClientId: string,
    onDate?: string
  ): Promise<IServiceClientPriceList> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/prices/`, {
      params: onDate ? { on_date: onDate } : {},
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Register a new vigency. **This is what an annual readjustment is** -- it does not edit the
   * current sheet, so history stays intact and a retroactive work log still prices at the rate
   * that applied on its service date.
   */
  async createPrice(
    workspaceSlug: string,
    serviceClientId: string,
    data: { starts_on: string; base_hour_rate: string; notes?: string }
  ): Promise<IServiceClientPrice> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/prices/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Correct a sheet registered wrong. Editing does not reprice work logs already priced by it. */
  async updatePrice(
    workspaceSlug: string,
    serviceClientId: string,
    priceId: string,
    data: Partial<{ starts_on: string; base_hour_rate: string; notes: string }>
  ): Promise<IServiceClientPrice> {
    return this.patch(
      `/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/prices/${priceId}/`,
      data
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Remove a vigency that should never have existed.
   *
   * Refused with `PRICE_VIGENCY_ALREADY_PRICED_WORK_LOGS` once the vigency has priced work --
   * those rows carry a rate snapshot, and deleting the sheet would leave nobody able to look
   * up where the number came from when a client disputes an invoice.
   */
  async deletePrice(workspaceSlug: string, serviceClientId: string, priceId: string): Promise<void> {
    return this.delete(
      `/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/prices/${priceId}/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createHourTypeRate(
    workspaceSlug: string,
    priceId: string,
    data: { hour_type: string; absolute_rate: string }
  ): Promise<IServiceClientHourTypeRate> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-client-prices/${priceId}/hour-type-rates/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateHourTypeRate(
    workspaceSlug: string,
    priceId: string,
    rateId: string,
    data: { absolute_rate: string }
  ): Promise<IServiceClientHourTypeRate> {
    return this.patch(
      `/api/workspaces/${workspaceSlug}/service-client-prices/${priceId}/hour-type-rates/${rateId}/`,
      data
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Remove an override, restoring `base * multiplier` for that hour type. */
  async deleteHourTypeRate(workspaceSlug: string, priceId: string, rateId: string): Promise<void> {
    return this.delete(
      `/api/workspaces/${workspaceSlug}/service-client-prices/${priceId}/hour-type-rates/${rateId}/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** The rate that actually applies to each hour type. Derived server side, never cached here. */
  async fetchEffectiveRates(
    workspaceSlug: string,
    serviceClientId: string,
    onDate?: string
  ): Promise<IServiceEffectiveRateTable> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-clients/${serviceClientId}/effective-rates/`, {
      params: onDate ? { on_date: onDate } : {},
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchConsolidation(
    workspaceSlug: string,
    params: { year: number; month: number; service_client_id?: string }
  ): Promise<IServiceBillingConsolidation> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-billing-consolidation/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * What billing a period's overage would cost, **before** doing it.
   *
   * Read this and show it in the confirmation. Billing an overage cannot be undone, and an
   * irreversible mistake has to be deliberate rather than careless.
   */
  async fetchPeriodOveragePreview(workspaceSlug: string, periodId: string): Promise<IServiceOveragePreview> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-contract-periods/${periodId}/overage-preview/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** The allowance counterpart. `overage_preview` is null when there is nothing to bill. */
  async fetchAllowanceOveragePreview(
    workspaceSlug: string,
    allowanceId: string
  ): Promise<{ overage_preview: IServiceOveragePreview | null }> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-issue-allowances/${allowanceId}/close/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Set or clear an allowance's own overage rate.
   *
   * `null` clears it and restores the fallback to the client's base rate. An allowance is a
   * separately negotiated sale, so the support price is usually the wrong price for it.
   */
  async setAllowanceOverageRate(
    workspaceSlug: string,
    allowanceId: string,
    overageHourRate: string | null
  ): Promise<{ allowance: unknown }> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-issue-allowances/${allowanceId}/overage-rate/`, {
      overage_hour_rate: overageHourRate,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Queue a work log export. Uses the existing export pipeline, so it inherits its history. */
  async requestExport(
    workspaceSlug: string,
    data: IServiceLogExportRequest
  ): Promise<{ message: string; token: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-log-exports/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchExportHistory(workspaceSlug: string): Promise<IServiceLogExportHistory[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-log-exports/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
