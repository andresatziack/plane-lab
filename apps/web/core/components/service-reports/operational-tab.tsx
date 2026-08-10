/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import type { TServiceReportFilters } from "@plane/types";
import { Loader } from "@plane/ui";
import { ServiceClientService } from "@/services/service-client.service";
import { ServicePricingService } from "@/services/service-pricing.service";
import { ServiceReportsService } from "@/services/service-reports.service";
import { DistributionTable, HoursSeriesChart } from "./charts";
import { DrillDownModal } from "./drill-down-modal";
import { ReportInsightCard } from "./report-insight-card";
import { ReportToolbar } from "./report-toolbar";
import { useReportFilters } from "./use-report-filters";

const reports = new ServiceReportsService();
const clientsService = new ServiceClientService();
const pricing = new ServicePricingService();

/**
 * Who worked on what. Section 3.
 *
 * The team's view rather than the invoice's: hours by technician, project, client and hour type.
 * Open to Members without reservation -- a technician who cannot see the distribution cannot
 * self-organise, and a coordinator balancing load needs it more than an Admin does.
 *
 * Every table row drills down, which is where criterion 8 lands for this tab. The money column
 * appears only where the payload carries it.
 */
export const OperationalTab = React.memo(function OperationalTab() {
  const { t } = useTranslation();
  const params = useParams();
  const workspaceSlug = params.workspaceSlug?.toString();

  const { filters, setFilter, setFilters, key } = useReportFilters();
  const [drillDown, setDrillDown] = useState<{ title: string; filters: TServiceReportFilters } | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const { data: clients } = useSWR(
    workspaceSlug ? `service-clients-${workspaceSlug}` : null,
    workspaceSlug ? () => clientsService.fetchServiceClients(workspaceSlug) : null
  );

  const { data, isLoading } = useSWR(
    workspaceSlug ? `service-report-operational-${workspaceSlug}-${key}` : null,
    workspaceSlug ? () => reports.fetchOperational(workspaceSlug, filters) : null
  );

  const handleExport = async () => {
    if (!workspaceSlug) return;

    setIsExporting(true);
    try {
      await pricing.requestExport(workspaceSlug, { provider: "csv", filters } as never);
    } finally {
      setIsExporting(false);
    }
  };

  if (isLoading) {
    return (
      <Loader className="space-y-4 p-4">
        <Loader.Item height="80px" />
        <Loader.Item height="240px" />
      </Loader>
    );
  }

  if (!data) return null;

  const totals = data.totals;

  const DIMENSIONS = [
    { key: "author" as const, nameKey: "author_name" as const, label: t("service_reports.distribution.author") },
    { key: "project" as const, nameKey: "project_name" as const, label: t("service_reports.distribution.project") },
    {
      key: "service_client" as const,
      nameKey: "service_client_name" as const,
      label: t("service_reports.distribution.service_client"),
    },
    {
      key: "hour_type" as const,
      nameKey: "hour_type_name" as const,
      label: t("service_reports.distribution.hour_type"),
    },
  ];

  return (
    <div className="flex flex-col gap-6 p-4">
      <ReportToolbar
        filters={filters}
        setFilter={setFilter}
        setFilters={setFilters}
        clients={clients?.map((client) => ({ id: client.id, name: client.name }))}
        onExport={handleExport}
        isExporting={isExporting}
      />

      <div className="grid grid-cols-2 gap-6 rounded-lg border border-subtle p-4 md:grid-cols-4">
        <ReportInsightCard
          label={t("service_reports.totals.logged_hours")}
          value={totals.logged_hours_display}
          onDrillDown={() => setDrillDown({ title: t("service_reports.totals.logged_hours"), filters: totals.filters })}
        />
        <ReportInsightCard
          label={t("service_reports.totals.equivalent_hours")}
          value={totals.equivalent_hours_display}
        />
        <ReportInsightCard label={t("service_reports.totals.issues")} value={String(totals.issues)} />
        <ReportInsightCard
          label={t("service_reports.totals.average_hours_per_issue")}
          value={totals.average_equivalent_hours_per_issue_display ?? "—"}
        />
      </div>

      <section className="rounded-lg border border-subtle p-4">
        <h3 className="mb-3 text-14 font-medium text-primary">{t("service_reports.operational.series")}</h3>
        <HoursSeriesChart
          data={data.series}
          keys={[
            { key: "logged_hours", label: t("service_reports.series.logged") },
            { key: "equivalent_hours", label: t("service_reports.series.equivalent") },
          ]}
        />
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        {DIMENSIONS.map((dimension) => (
          <section key={dimension.key} className="rounded-lg border border-subtle p-4">
            <h3 className="mb-3 text-14 font-medium text-primary">{dimension.label}</h3>
            <DistributionTable
              slices={data.distributions[dimension.key]}
              nameKey={dimension.nameKey}
              nameLabel={dimension.label}
              onRowClick={(slice) =>
                slice.filters
                  ? setDrillDown({
                      title: (slice[dimension.nameKey] as string | null) ?? dimension.label,
                      filters: slice.filters,
                    })
                  : undefined
              }
            />
          </section>
        ))}
      </div>

      <section className="rounded-lg border border-subtle p-4">
        <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.non_billable.title")}</h3>
        <p className="mb-3 text-12 text-tertiary">{t("service_reports.non_billable.hint")}</p>
        <DistributionTable
          slices={data.non_billable}
          nameKey="billing_type_name"
          nameLabel={t("service_reports.distribution.billing_type")}
          onRowClick={(slice) =>
            slice.filters ? setDrillDown({ title: slice.billing_type_name ?? "", filters: slice.filters }) : undefined
          }
        />
      </section>

      {drillDown ? (
        <DrillDownModal isOpen onClose={() => setDrillDown(null)} title={drillDown.title} filters={drillDown.filters} />
      ) : null}
    </div>
  );
});
