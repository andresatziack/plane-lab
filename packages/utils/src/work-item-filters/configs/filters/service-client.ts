/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import type { IServiceClient, TFilterProperty, TSupportedOperators } from "@plane/types";
import { EQUALITY_OPERATOR, COLLECTION_OPERATOR } from "@plane/types";
// local imports
import type { IFilterIconConfig, TCreateFilterConfig, TCreateFilterConfigParams } from "../../../rich-filters";
import { createFilterConfig, createOperatorConfigEntry, getMultiSelectConfig } from "../../../rich-filters";

// ------------ Service client filter ------------

/**
 * Service client filter specific params
 */
export type TCreateServiceClientFilterParams = TCreateFilterConfigParams &
  IFilterIconConfig<IServiceClient> & {
    serviceClients: IServiceClient[];
  };

/**
 * Helper to get the service client multi select config
 */
export const getServiceClientMultiSelectConfig = (
  params: TCreateServiceClientFilterParams,
  singleValueOperator: TSupportedOperators
) =>
  getMultiSelectConfig<IServiceClient, string, IServiceClient>(
    {
      items: params.serviceClients,
      getId: (serviceClient) => serviceClient.id,
      // Prefer the trade name: it is what people call the company day to day.
      getLabel: (serviceClient) => serviceClient.trade_name || serviceClient.name,
      getValue: (serviceClient) => serviceClient.id,
      getIconData: (serviceClient) => serviceClient,
    },
    {
      singleValueOperator,
      ...params,
    },
    {
      ...params,
    }
  );

/**
 * Get the service client filter config.
 *
 * The client of a work item is derived from its project, so this filter resolves
 * server side through a foreign key hop rather than a column on the work item.
 *
 * @template P - The filter property
 * @param key - The filter key to use
 * @returns A function that takes parameters and returns the service client filter config
 */
export const getServiceClientFilterConfig =
  <P extends TFilterProperty>(key: P): TCreateFilterConfig<P, TCreateServiceClientFilterParams> =>
  (params: TCreateServiceClientFilterParams) =>
    createFilterConfig<P>({
      id: key,
      label: "Clients",
      ...params,
      icon: params.filterIcon,
      supportedOperatorConfigsMap: new Map([
        createOperatorConfigEntry(COLLECTION_OPERATOR.IN, params, (updatedParams) =>
          getServiceClientMultiSelectConfig(updatedParams, EQUALITY_OPERATOR.EXACT)
        ),
      ]),
    });
