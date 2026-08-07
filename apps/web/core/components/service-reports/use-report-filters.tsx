/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo, useState } from "react";
import type { TServiceReportFilters } from "@plane/types";

/**
 * The global filters of section 4, held as the descriptor the server understands.
 *
 * **Deliberately local state and not a MobX store.** These dashboards are read only: there is
 * no mutable domain state for a store to own, and every consumer of the filters is inside one
 * route. The master context says MobX for state; the nuance the worklog series settled is MobX
 * for **mutable domain state** and SWR plus a service class for reads, which is what every
 * `service_*` screen since Phase 1 has done.
 *
 * The shape is `TServiceReportFilters` rather than a friendlier object, on purpose: the server
 * emits exactly this inside every bucket, so keeping one vocabulary end to end means a filter
 * chosen in the toolbar and a filter that came back inside a chart are the same kind of thing.
 */

/** The competency of `date`, as the descriptor spells it. */
export const toCompetence = (date: Date): string =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;

/** `monthsBack` whole months before this one, inclusive of it. */
export const competenceRange = (monthsBack: number): { competence_from: string; competence_to: string } => {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth() - monthsBack, 1);

  return { competence_from: toCompetence(from), competence_to: toCompetence(now) };
};

export type TReportFilterState = Partial<TServiceReportFilters> & {
  include_group?: string;
};

/**
 * The order filter keys appear in an SWR cache key.
 *
 * A **declared** order rather than a sort, for two reasons. `localeCompare` is
 * locale-sensitive, so the same filter state could produce different cache keys for a Turkish
 * user and an English one -- which would be a cache miss nobody could reproduce. And a fixed
 * list makes the key stable when a field is added, instead of silently reshuffling every
 * existing key.
 */
const KEY_ORDER: (keyof TReportFilterState)[] = [
  "competence_basis",
  "competence_from",
  "competence_to",
  "service_client_ids",
  "project_ids",
  "issue_ids",
  "author_ids",
  "hour_type_ids",
  "billing_type_ids",
  "settled_routes",
  "route_deviation_reasons",
  "pricing_failure_reasons",
  "debited_period_ids",
  "debited_allowance_ids",
  "revenue_origins",
  "without_pool_origin",
  "without_service_client",
  "include_group",
];

export const useReportFilters = (initial?: TReportFilterState) => {
  const [filters, setFilters] = useState<TReportFilterState>(
    initial ?? { competence_basis: "worked_on", ...competenceRange(11) }
  );

  /**
   * Replace one key. Empty values are removed rather than sent blank, matching `to_params()`.
   *
   * `competence_basis` is excluded from the key type on purpose: it is not a user choice. The
   * server picks the basis -- D48 makes it a property of what is being measured, not a
   * preference -- and a toolbar that could set it would let somebody group contract consumption
   * by service date, which is the bug acceptance criterion 7 exists to catch.
   */
  const setFilter = useCallback(
    (key: Exclude<keyof TReportFilterState, "competence_basis">, value: string | undefined) => {
      setFilters((current) => {
        const next = { ...current };

        if (value === undefined || value === "") delete next[key];
        else next[key] = value;

        return next;
      });
    },
    []
  );

  /** A stable string for the SWR key. See `KEY_ORDER` for why it is not sorted. */
  const key = useMemo(
    () =>
      KEY_ORDER.filter((name) => filters[name] !== undefined && filters[name] !== "")
        .map((name) => `${name}=${filters[name]}`)
        .join("&"),
    [filters]
  );

  return { filters, setFilters, setFilter, key };
};
