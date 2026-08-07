/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { Download } from "lucide-react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { CustomSearchSelect } from "@plane/ui";
import type { TReportFilterState } from "./use-report-filters";
import { competenceRange } from "./use-report-filters";

/**
 * The global filters of section 4, plus the export.
 *
 * **The export sends the current descriptor**, which is the whole of acceptance criterion 9:
 * the CSV is not "a similar query" but literally the selection on screen, because the server
 * accepts a `ServiceLogFilterSet` in `filters` and the export task consumes the same one. Before
 * that existed, the screen could filter by things the export could not express and the two
 * legitimately disagreed.
 */

const WINDOWS = [
  { value: "3", label: "Últimos 3 meses" },
  { value: "6", label: "Últimos 6 meses" },
  { value: "12", label: "Últimos 12 meses" },
  { value: "24", label: "Últimos 24 meses" },
];

const NON_BILLABLE_SCOPES = [
  { value: "", label: "Tudo" },
  { value: "debit_pool,bill_amount", label: "Só faturável" },
  { value: "non_billable", label: "Só não faturável" },
];

type Props = {
  filters: TReportFilterState;
  setFilter: (key: Exclude<keyof TReportFilterState, "competence_basis">, value: string | undefined) => void;
  setFilters: React.Dispatch<React.SetStateAction<TReportFilterState>>;
  clients?: { id: string; name: string }[];
  onExport?: () => void;
  isExporting?: boolean;
};

export const ReportToolbar = React.memo(function ReportToolbar(props: Props) {
  const { filters, setFilter, setFilters, clients, onExport, isExporting } = props;
  const { t } = useTranslation();

  const windowOptions = WINDOWS.map((option) => ({
    value: option.value,
    query: option.label,
    content: <span className="truncate">{option.label}</span>,
  }));

  const scopeOptions = NON_BILLABLE_SCOPES.map((option) => ({
    value: option.value,
    query: option.label,
    content: <span className="truncate">{option.label}</span>,
  }));

  const clientOptions = (clients ?? []).map((client) => ({
    value: client.id,
    query: client.name,
    content: <span className="truncate">{client.name}</span>,
  }));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <CustomSearchSelect
        value={[]}
        onChange={(monthsBack: string) => {
          // Replaces both ends at once: setting them one at a time would momentarily produce an
          // inverted range, which the server refuses with COMPETENCE_RANGE_IS_INVERTED.
          const range = competenceRange(Number(monthsBack) - 1);
          setFilters((current) => ({ ...current, ...range }));
        }}
        options={windowOptions}
        label={
          <span className="px-1">
            {filters.competence_from
              ? `${filters.competence_from} → ${filters.competence_to}`
              : t("service_reports.toolbar.window")}
          </span>
        }
      />

      {clientOptions.length > 0 ? (
        <CustomSearchSelect
          value={filters.service_client_ids ? filters.service_client_ids.split(",") : []}
          onChange={(ids: string[]) => setFilter("service_client_ids", ids.join(","))}
          options={clientOptions}
          multiple
          label={<span className="px-1">{t("service_reports.toolbar.clients")}</span>}
        />
      ) : null}

      <CustomSearchSelect
        value={filters.settled_routes ? [filters.settled_routes] : [""]}
        onChange={(routes: string) => setFilter("settled_routes", routes)}
        options={scopeOptions}
        label={<span className="px-1">{t("service_reports.toolbar.scope")}</span>}
      />

      {onExport ? (
        <Button
          variant="secondary"
          size="sm"
          prependIcon={<Download className="h-3.5 w-3.5" />}
          onClick={onExport}
          loading={isExporting}
        >
          {t("service_reports.toolbar.export")}
        </Button>
      ) : null}
    </div>
  );
});
